#!/usr/bin/env python3
"""
Scarab Pet Dungeon Agent
Headless dungeon AI for Scarab (Pi Zero 2, no display).
Runs on 192.168.1.2 — reports to Gates of Darkness server at 192.168.1.5:5000
"""

import json
import math
import os
import random
import socket
import subprocess
import time
from urllib.request import Request, urlopen

# ── Identity check ────────────────────────────────────────────────────────────
_hostname = socket.gethostname().lower()
if 'scarab' not in _hostname:
    print(f"[scarab_agent] WARNING: hostname '{_hostname}' does not contain 'scarab'.")
    print("[scarab_agent] This agent is intended for the Scarab device. Continuing anyway.")

# ── Server config ─────────────────────────────────────────────────────────────
GATES_IP   = "192.168.1.5"
GATES_PORT = 5000
PET_ID     = "scarab"

HERO_POLL_INTERVAL    = 60      # seconds between active_heroes checks
SYNC_INTERVAL_IDLE    = 150     # ~2.5 min between syncs when no heroes
SYNC_INTERVAL_ACTIVE  = 90     # ~1.5 min between syncs when heroes present

FIGHT_SLEEP_IDLE      = (8, 12)
FIGHT_SLEEP_COMPANION = (4, 6)

TEMP_PATH = "/sys/class/thermal/thermal_zone0/temp"

# ── Egyptian weapon name pool ─────────────────────────────────────────────────
EGYPTIAN_WEAPONS = [
    "Khopesh",
    "Sickle Sword",
    "Kopesh Blade",
    "Mace of Amun",
    "Spear of Horus",
    "Axe of Sekhmet",
    "Serpent Dagger",
    "Staff of Thoth",
    "Bow of Neith",
    "Flail of Osiris",
    "Scepter of Ra",
    "Blade of Anubis",
    "Lotus Sword",
    "Crook of Pharaoh",
]

# ── Dungeon state ─────────────────────────────────────────────────────────────
state = {
    "floor":       1,
    "level":       1,
    "xp":          0,
    "xp_next":     100,
    "hp":          40,
    "max_hp":      40,
    "atk":         7,
    "def_val":     3,
    "kills":       0,
    "companion_mode": False,
    "accumulated_xp":   0,
    "accumulated_gold": 0,
    "last_sync":   0.0,
    "last_hero_poll": 0.0,
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _log(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

def _gd_base():
    return f"http://{GATES_IP}:{GATES_PORT}"

def _gd_post(path, data):
    try:
        body = json.dumps(data).encode()
        req  = Request(
            _gd_base() + path,
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urlopen(req, timeout=6) as r:
            return json.loads(r.read())
    except Exception as e:
        _log(f"POST {path} failed: {e}")
        return None

def _gd_get(path):
    try:
        with urlopen(Request(_gd_base() + path), timeout=6) as r:
            return json.loads(r.read())
    except Exception as e:
        _log(f"GET {path} failed: {e}")
        return None

def _cpu_temp():
    """Read CPU temperature in Celsius (returns float or None)."""
    try:
        with open(TEMP_PATH) as f:
            return int(f.read().strip()) / 1000.0
    except Exception:
        return None

def _client_count():
    """Count LAN clients via arp -n (lines containing 'ether')."""
    try:
        out = subprocess.check_output(["arp", "-n"], text=True, timeout=5)
        return sum(1 for line in out.splitlines() if "ether" in line)
    except Exception:
        return 0

def _post_heartbeat():
    """Post CPU temp and client count to /api/pets/heartbeat."""
    temp    = _cpu_temp()
    clients = _client_count()
    _gd_post("/api/pets/heartbeat", {
        "pet_id":      PET_ID,
        "cpu_temp":    temp,
        "clients":     clients,
    })


# ── Level-up logic ────────────────────────────────────────────────────────────

def _apply_xp(xp_gained):
    """Apply XP to state, level up as needed. Returns True if leveled up."""
    leveled = False
    state["xp"] += xp_gained
    while state["xp"] >= state["xp_next"] and state["level"] < 30:
        state["xp"]     -= state["xp_next"]
        state["level"]  += 1
        state["xp_next"] = int(100 * (1.4 ** (state["level"] - 1)))
        state["max_hp"]  = int(state["max_hp"] * 1.04)
        state["atk"]    += 1
        if state["level"] % 3 == 0:
            state["def_val"] += 1
        leveled = True
    state["hp"] = min(state["max_hp"], state["hp"])
    return leveled


# ── Monster stats ─────────────────────────────────────────────────────────────

def _make_monster(floor):
    return {
        "hp":  10 + floor * 8,
        "atk":  3 + floor * 2,
        "def":  1 + floor // 3,
    }


# ── Fight one monster ─────────────────────────────────────────────────────────

def _fight():
    """
    Simulate one fight. Returns (won, xp_gained, gold_gained, loot_name).
    loot_name is '' if no drop.
    """
    floor  = state["floor"]
    mon    = _make_monster(floor)
    mon_hp = mon["hp"]
    hp     = state["hp"]

    pet_atk = 6 + state["level"]
    pet_def = 2 + state["level"] // 4

    rounds = 0
    while mon_hp > 0 and hp > 0 and rounds < 50:
        pet_dmg = max(1, pet_atk - mon["def"] + random.randint(-2, 3))
        mon_hp  = max(0, mon_hp - pet_dmg)
        if mon_hp <= 0:
            break
        mon_dmg = max(1, mon["atk"] - pet_def + random.randint(-1, 2))
        hp      = max(0, hp - mon_dmg)
        rounds += 1

    state["hp"] = hp

    if hp <= 0:
        # Death — respawn at same floor with 60% HP
        _log(f"[DEATH] Floor {floor} — respawning at 60% HP")
        state["hp"] = max(1, int(state["max_hp"] * 0.6))
        return False, 0, 0, ""

    # Win
    state["kills"] += 1
    xp_gained   = 10 + floor * 4 + random.randint(0, 5)
    gold_gained  = random.randint(1, 3 + floor)
    loot_name    = ""

    # Regen some HP post-fight
    state["hp"] = min(state["max_hp"], state["hp"] + int(state["max_hp"] * 0.15))

    # Floor advance
    if random.random() < 0.15:
        state["floor"] += 1
        _log(f"[FLOOR] Advanced to floor {state['floor']}")

    # Item drop
    if random.random() < 0.35:
        loot_name = random.choice(EGYPTIAN_WEAPONS)

    return True, xp_gained, gold_gained, loot_name


# ── Sync to Gates server ──────────────────────────────────────────────────────

def _sync(xp_gained=0, gold_gained=0, loot_name="", loot_icon=""):
    """Push accumulated state to Gates of Darkness."""
    payload = {
        "pet_id":      PET_ID,
        "level":       state["level"],
        "area":        state["floor"],
        "hp":          state["hp"],
        "max_hp":      state["max_hp"],
        "xp_gained":   xp_gained,
        "gold_gained": gold_gained,
        "loot_name":   loot_name,
        "loot_icon":   loot_icon or "⚔",
    }
    res = _gd_post("/api/pets/play/sync", payload)
    state["last_sync"] = time.time()
    state["accumulated_xp"]   = 0
    state["accumulated_gold"] = 0

    if res:
        companion = res.get("companion", "")
        was_active = state["companion_mode"]
        state["companion_mode"] = bool(companion)
        if state["companion_mode"] and not was_active:
            _log(f"[COMPANION] Heroes online: {companion} — switching to active mode")
        elif not state["companion_mode"] and was_active:
            _log("[COMPANION] No heroes online — returning to idle mode")

def _poll_heroes():
    """Poll active_heroes endpoint to update companion mode."""
    state["last_hero_poll"] = time.time()
    heroes = _gd_get("/api/pets/active_heroes")
    if heroes is None:
        return
    was_active = state["companion_mode"]
    if heroes:
        names = ", ".join(h.get("char_name", "?") for h in heroes)
        state["companion_mode"] = True
        if not was_active:
            _log(f"[HERO POLL] Active heroes: {names}")
    else:
        state["companion_mode"] = False
        if was_active:
            _log("[HERO POLL] No active heroes")


# ── Main loop ─────────────────────────────────────────────────────────────────

def main():
    _log(f"Scarab Agent starting. hostname={socket.gethostname()}, "
         f"pet_id={PET_ID}, server={GATES_IP}:{GATES_PORT}")

    last_heartbeat = 0.0

    while True:
        try:
            now = time.time()

            # Heartbeat every 5 minutes
            if now - last_heartbeat >= 300:
                _post_heartbeat()
                last_heartbeat = now

            # Hero poll
            if now - state["last_hero_poll"] >= HERO_POLL_INTERVAL:
                _poll_heroes()

            # Run one fight
            companion_mode = state["companion_mode"]
            won, xpg, gg, loot = _fight()

            if won:
                state["accumulated_xp"]   += xpg
                state["accumulated_gold"] += gg
                _apply_xp(xpg)

                loot_icon = ""
                if loot:
                    loot_icon = "⚔"
                    _log(f"[LOOT] {loot} (floor {state['floor']}, level {state['level']})")

                # Check if it's time to sync
                sync_interval = SYNC_INTERVAL_ACTIVE if companion_mode else SYNC_INTERVAL_IDLE
                if (now - state["last_sync"] >= sync_interval) or loot:
                    _log(
                        f"[SYNC] floor={state['floor']} level={state['level']} "
                        f"hp={state['hp']}/{state['max_hp']} "
                        f"xp_acc={state['accumulated_xp']} gold_acc={state['accumulated_gold']}"
                        + (f" loot={loot}" if loot else "")
                    )
                    _sync(
                        xp_gained=state["accumulated_xp"],
                        gold_gained=state["accumulated_gold"],
                        loot_name=loot,
                        loot_icon=loot_icon,
                    )
            else:
                # Died — force a sync so server knows current HP
                _sync()

            # Sleep between fights
            sleep_range = FIGHT_SLEEP_COMPANION if companion_mode else FIGHT_SLEEP_IDLE
            sleep_secs  = random.uniform(*sleep_range)
            time.sleep(sleep_secs)

        except KeyboardInterrupt:
            _log("Shutting down.")
            break
        except Exception as e:
            _log(f"[ERROR] Unhandled exception: {e}")
            time.sleep(15)


if __name__ == "__main__":
    main()
