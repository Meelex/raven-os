#!/usr/bin/env python3
"""
scarab_dungeon.py — Raven OS / Scarab node
Dungeon co-op auto-player for Scarab (Pi Zero 2 travel router).

Behaviour
─────────
• Monitors the WireGuard tunnel (wg0) for an active handshake.
• When tunnel is up   → POST /dungeon/join as "SCARAB" on Duat, then auto-play.
• While tunnel lives  → choose actions (move / attack / flee) every TICK seconds.
• When tunnel drops   → POST /dungeon/leave and wait for tunnel to return.
• Runs forever; designed to be managed by systemd (After=wg-quick@wg0.service).

Deploy
──────
  sudo cp scarab_dungeon.py /home/scarab/scarab_dungeon.py
  sudo cp scarab_dungeon.service /etc/systemd/system/
  sudo systemctl enable --now scarab_dungeon
"""

import time
import random
import subprocess
import sys

try:
    import requests
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except ImportError:
    subprocess.run([sys.executable, "-m", "pip", "install", "requests"], check=True)
    import requests
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ── Config ─────────────────────────────────────────────────────────────────────

DUAT_IP      = "192.168.1.5"
DUAT_PORT    = 6176
BASE_URL     = f"https://{DUAT_IP}:{DUAT_PORT}"
PLAYER       = "SCARAB"
WG_IFACE     = "wg0"

TICK         = 3.5          # seconds between auto-play actions
POLL_TUNNEL  = 10           # seconds between tunnel-down checks while playing
CHECK_IDLE   = 15           # seconds between checks when tunnel is down

# How long since last WireGuard handshake before we consider the tunnel dead.
# wg reports handshake age in seconds; >180s usually means the peer is gone.
HANDSHAKE_STALE_SEC = 180

# ── Logging ────────────────────────────────────────────────────────────────────

def log(msg):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] SCARAB ▶  {msg}", flush=True)

# ── WireGuard helpers ───────────────────────────────────────────────────────────

def _wg_latest_handshake(iface=WG_IFACE):
    """Return seconds since last handshake, or None if unavailable."""
    try:
        out = subprocess.check_output(
            ["wg", "show", iface, "latest-handshakes"],
            stderr=subprocess.DEVNULL, text=True
        ).strip()
        # Output: "<peer_pubkey>  <unix_timestamp>"
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                ts = int(parts[-1])
                if ts == 0:
                    return None          # never had a handshake
                age = int(time.time()) - ts
                return age
    except Exception:
        pass
    return None

def tunnel_is_up(iface=WG_IFACE):
    """True if the WireGuard interface exists and has a recent handshake."""
    age = _wg_latest_handshake(iface)
    if age is None:
        return False
    return age < HANDSHAKE_STALE_SEC

# ── Duat dungeon API ────────────────────────────────────────────────────────────

SESS = requests.Session()
SESS.verify = False          # self-signed cert

def _post(path, data=None):
    try:
        r = SESS.post(f"{BASE_URL}{path}", json=data or {}, timeout=6)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log(f"POST {path} failed: {e}")
        return None

def _get(path):
    try:
        r = SESS.get(f"{BASE_URL}{path}", timeout=6)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log(f"GET {path} failed: {e}")
        return None

def join():
    resp = _post("/dungeon/join", {"player": PLAYER})
    if resp:
        log("Joined dungeon.")
    return resp is not None

def leave():
    _post("/dungeon/leave", {"player": PLAYER})
    log("Left dungeon.")

def get_state():
    return _get("/dungeon/state")

def action(act):
    return _post("/dungeon/action", {"player": PLAYER, "action": act})

# ── Auto-play logic ─────────────────────────────────────────────────────────────

def choose_action(state):
    """Pick the best action for the current dungeon sub-state."""
    sub = state.get("sub", "IDLE")
    players = state.get("players", {})

    if sub == "EXPLORE":
        return "move"

    if sub == "COMBAT":
        me = players.get(PLAYER, {})
        hp     = me.get("hp", 1)
        max_hp = me.get("max_hp", 1)
        # Flee if badly hurt (below 25% HP) with 60% chance, otherwise attack
        if max_hp > 0 and hp / max_hp < 0.25 and random.random() < 0.60:
            return "flee"
        return "attack"

    if sub in ("LOOT", "DEAD"):
        return "continue"

    return None   # IDLE — nothing to do

def play_loop():
    """Main loop while tunnel is active. Returns when tunnel drops."""
    log("Tunnel up — joining dungeon.")
    if not join():
        log("Could not join dungeon — will retry.")
        return

    last_tunnel_check = time.time()

    while True:
        # Periodic tunnel health check
        now = time.time()
        if now - last_tunnel_check >= POLL_TUNNEL:
            if not tunnel_is_up():
                log("Tunnel dropped — leaving dungeon.")
                leave()
                return
            last_tunnel_check = now

        # Fetch state and act
        state = get_state()
        if state is None:
            log("Could not reach Duat — waiting...")
            time.sleep(TICK)
            continue

        act = choose_action(state)
        if act:
            resp = action(act)
            if resp:
                logs = resp.get("state", {}).get("log", [])
                for line in logs:
                    if line:
                        log(line)
        else:
            log(f"Sub={state.get('sub')} — waiting...")

        time.sleep(TICK)

# ── Entry point ─────────────────────────────────────────────────────────────────

def main():
    log("Scarab dungeon daemon starting.")
    log(f"Watching WireGuard interface: {WG_IFACE}")
    log(f"Duat endpoint: {BASE_URL}")

    in_dungeon = False

    while True:
        up = tunnel_is_up()

        if up and not in_dungeon:
            in_dungeon = True
            play_loop()           # blocks until tunnel drops
            in_dungeon = False
        elif not up:
            log(f"Tunnel down — waiting {CHECK_IDLE}s...")

        time.sleep(CHECK_IDLE)


if __name__ == "__main__":
    main()
