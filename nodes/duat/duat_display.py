#!/usr/bin/env python3
"""
duat_display.py — Raven OS
Color console for Duat's MHS 3.5" LCD (480x320 RGB565, PIL → /dev/fb0).
No SDL/pygame — pure PIL + numpy.
"""

import os, sys, time, json, ssl, socket, threading, subprocess, collections, random
import struct, glob, platform
from datetime import datetime
from urllib.request import urlopen, Request
import numpy as np
from PIL import Image, ImageDraw, ImageFont

# ── Screen ─────────────────────────────────────────────────────────────────────
FB   = "/dev/fb0"
W, H = 480, 320

# ── Layout ─────────────────────────────────────────────────────────────────────
HDR_H  = 40      # header bar
VDIV   = 218     # vertical divider x (left | right)
MY0    = 43      # main content top y
MY1    = 258     # main content bottom y
LOG_Y0 = 261     # event log top
LOG_Y1 = 302     # event log bottom
FTR_Y  = 303     # footer top

# ── Palette ────────────────────────────────────────────────────────────────────
BG      = (  8,   8,  15)
BG2     = ( 13,  13,  28)
BG3     = ( 18,  18,  38)
GREEN   = (  0, 255, 136)
GREEN2  = (  0, 190,  95)
GREEN3  = (  0, 120,  60)
AMBER   = (255, 170,   0)
RED     = (255,  55,  70)
CYAN    = (  0, 200, 255)
WHITE   = (210, 215, 245)
DIM     = ( 50,  50,  95)
DIMMER  = ( 22,  22,  50)
MUTED   = ( 85,  85, 130)
PENDING = ( 90,  65, 160)   # Anubis not-yet-deployed purple

# ── Network config ─────────────────────────────────────────────────────────────
HASHDB_PORT = 6174
UNLOCK_PORT = 6176
RAVEN_IP    = "192.168.1.3"
RAVEN_PORT  = 6175
LEGIOM_IP   = "192.168.1.6"
DUAT_IP     = "192.168.1.5"
SCARAB_IP   = "172.16.0.2"   # WireGuard tunnel IP
RING_PORT   = 7744

REFRESH_SVC  = 5    # seconds between API polls
PING_LEGIOM  = 15   # seconds between Legiom pings

# ── View state (cycled by touchscreen tap) ────────────────────────────────────
# Cycle order: network → dungeon → ring → ringinfo → network
_VIEWS     = ["network", "dungeon", "ring", "ringinfo"]
_view      = "network"
_view_lock = threading.Lock()

# ── Event log ──────────────────────────────────────────────────────────────────
_events: collections.deque = collections.deque(maxlen=60)
_state_lock = threading.Lock()

def log_ev(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    _events.appendleft(f"{ts}  {msg}")
    print(f"[{ts}] {msg}", flush=True)

# ── HTTP helpers ───────────────────────────────────────────────────────────────
def _ssl():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode    = ssl.CERT_NONE
    return ctx

def _get(url, timeout=4):
    try:
        with urlopen(Request(url), timeout=timeout, context=_ssl()) as r:
            return json.loads(r.read())
    except Exception:
        return None

def _get_plain(url, timeout=4):
    """HTTP (no TLS) — for services not behind self-signed cert (e.g. Raven Flask)."""
    try:
        with urlopen(Request(url), timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None

def _tcp_open(ip, port, timeout=2):
    """True if TCP port is reachable — use instead of ICMP ping (Windows blocks ICMP)."""
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except Exception:
        return False

# ── Ring client introspection (run once at startup) ──────────────────────────
def _inspect_ring_client():
    """Return (version_str, methods_list) for colmi_r02_client."""
    lines = []
    version = "not installed"
    try:
        import importlib.metadata
        version = importlib.metadata.version("colmi-r02-client")
    except Exception:
        pass
    try:
        from colmi_r02_client.client import R02Client
        methods = [m for m in dir(R02Client)
                   if not m.startswith("__")]
        lines = methods
    except ImportError:
        lines = ["colmi_r02_client not found"]
    except Exception as e:
        lines = [f"import error: {e}"]
    return version, lines

_ring_client_version, _ring_client_methods = _inspect_ring_client()

# ── Global state ───────────────────────────────────────────────────────────────
state: dict = {
    "hashdb":         None,
    "unlock":         None,
    "pending":        None,
    "raven":          None,
    "dungeon":        None,
    "legiom_online":  False,
    "scarab_online":  False,
    "ring_status":    None,   # /status response
    "ring_bio":       None,   # /biometrics/latest response
    "ring_baseline":  None,   # /baseline response
    "ring_gesture":   None,   # /gesture/latest response
}
_prev: dict = {}

def fetch_all():
    global _prev
    with _state_lock:
        _prev = {k: v for k, v in state.items()}
    state["hashdb"]       = _get(f"https://127.0.0.1:{HASHDB_PORT}/health")
    state["unlock"]       = _get(f"https://127.0.0.1:{UNLOCK_PORT}/health")
    state["pending"]      = _get(f"https://127.0.0.1:{UNLOCK_PORT}/pending")
    state["raven"]        = _get_plain(f"http://{RAVEN_IP}:{RAVEN_PORT}/health")
    state["dungeon"]      = _get(f"https://127.0.0.1:{UNLOCK_PORT}/quest/state")
    state["ring_status"]  = _get_plain(f"http://127.0.0.1:{RING_PORT}/status")
    state["ring_bio"]     = _get_plain(f"http://127.0.0.1:{RING_PORT}/biometrics/latest")
    state["ring_baseline"]= _get_plain(f"http://127.0.0.1:{RING_PORT}/baseline")
    state["ring_gesture"] = _get_plain(f"http://127.0.0.1:{RING_PORT}/gesture/latest")

# ── Duat dungeon presence ──────────────────────────────────────────────────────
# Duat joins as HOST (heals, low-chance interference).
# Legiom joins as WARRIOR when it's online and a party is active.

_DUAT_PLAYER    = "DUAT"
_LEGIOM_PLAYER  = "LEGIOM"
_DUAT_IN_GAME   = False
_LEGIOM_IN_GAME = False
_DUAT_LAST_ACT  = 0.0
_LEGIOM_LAST_ACT = 0.0
_INTERFERE_CHANCE = 0.12   # 12% — Duat acts in combat
_EXPLORE_CHANCE   = 0.06   # 6%  — Duat nudges exploration

def _manage_player(name, cls, in_game_flag, last_act_ref,
                   sub, players, interfere_c, explore_c):
    """Shared join/leave/act logic for an auto-player. Returns (in_game, last_act)."""
    already  = name in players
    in_game  = in_game_flag
    last_act = last_act_ref

    if sub == "IDLE":
        if already:
            _post_duat("/quest/leave", {"player": name})
            log_ev(f"{name} left the dungeon")
        return False, last_act

    if not already:
        resp = _post_duat("/quest/join", {"player": name, "character": {"cls": cls}})
        if resp:
            log_ev(f"{name} joined as {cls}")
            return True, last_act
        return in_game, last_act

    now = time.time()
    if now - last_act < REFRESH_SVC:
        return True, last_act

    action = None
    if sub == "COMBAT" and random.random() < interfere_c:
        action = "attack"
    elif sub == "EXPLORE" and random.random() < explore_c:
        action = "move"
    elif sub in ("LOOT", "DEAD"):
        action = "continue"

    if action:
        resp = _post_duat("/quest/action", {"player": name, "action": action})
        if resp:
            last_act = now
            for line in ((resp.get("state") or {}).get("log") or [])[:1]:
                if line and name in line:
                    log_ev(f"Dungeon: {line}")

    return True, last_act

def duat_dungeon_tick():
    """Called each fetch cycle. Manages Duat (HOST) and Legiom (WARRIOR) in the dungeon."""
    global _DUAT_IN_GAME, _DUAT_LAST_ACT, _LEGIOM_IN_GAME, _LEGIOM_LAST_ACT
    dg = state.get("dungeon")
    if not dg:
        return
    sub     = dg.get("sub", "IDLE")
    players = dg.get("players") or {}

    # Duat — always participates when party active
    _DUAT_IN_GAME, _DUAT_LAST_ACT = _manage_player(
        _DUAT_PLAYER, "HOST", _DUAT_IN_GAME, _DUAT_LAST_ACT,
        sub, players, _INTERFERE_CHANCE, _EXPLORE_CHANCE
    )

    # Legiom — participates only when online
    legiom_online = state.get("legiom_online", False)
    if legiom_online:
        _LEGIOM_IN_GAME, _LEGIOM_LAST_ACT = _manage_player(
            _LEGIOM_PLAYER, "WARRIOR", _LEGIOM_IN_GAME, _LEGIOM_LAST_ACT,
            sub, players,
            interfere_c=0.20,   # Warriors fight more aggressively
            explore_c=0.08
        )
    elif _LEGIOM_PLAYER in players:
        # Legiom went offline — remove from party
        _post_duat("/quest/leave", {"player": _LEGIOM_PLAYER})
        _LEGIOM_IN_GAME = False
        log_ev("Legiom disconnected from dungeon")

def _post_duat(path, data):
    """POST to local unlock service (quest endpoints)."""
    import urllib.request, urllib.error
    try:
        body = json.dumps(data).encode()
        req  = urllib.request.Request(
            f"https://127.0.0.1:{UNLOCK_PORT}{path}",
            data=body, method="POST",
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, context=_ssl(), timeout=4) as r:
            return json.loads(r.read())
    except Exception:
        return None

def _ok(d, key="status"):
    return bool(d and d.get(key) == "ok")

def detect_transitions():
    # Hash DB
    if _ok(state["hashdb"]) and not _ok(_prev.get("hashdb")):
        total = (state["hashdb"] or {}).get("db_stats", {}).get("total_hashes", 0)
        log_ev(f"Hash DB online — {total:,} hashes loaded")
    elif not _ok(state["hashdb"]) and _ok(_prev.get("hashdb")):
        log_ev("Hash DB offline!")

    # Raven
    if _ok(state["raven"]) and not _ok(_prev.get("raven")):
        log_ev("Raven online")
    elif not _ok(state["raven"]) and _ok(_prev.get("raven")):
        log_ev("Raven offline")

    # Ring
    ring_now  = bool(state.get("ring_status"))
    ring_prev = bool(_prev.get("ring_status"))
    if ring_now and not ring_prev:
        batt = (state["ring_status"] or {}).get("battery", "?")
        log_ev(f"Ring online — battery {batt}%")
    elif not ring_now and ring_prev:
        log_ev("Ring offline")

    # Unlock queue
    pend  = state.get("pending") or {}
    ppend = _prev.get("pending") or {}
    items  = pend.get("pending", [])
    pitems = ppend.get("pending", [])
    if len(items) > len(pitems):
        name = items[-1].get("filename", "?")
        log_ev(f"LOCKED: {name}")
    elif len(items) < len(pitems) and pitems:
        log_ev("File unlocked / queue cleared")

    # Dungeon state changes
    dg  = state.get("dungeon") or {}
    pdg = _prev.get("dungeon") or {}
    if dg.get("sub") != pdg.get("sub") and dg.get("sub"):
        sub = dg["sub"]
        e   = dg.get("enemy") or {}
        if sub == "COMBAT":
            log_ev(f"Combat: {e.get('name','?')} [{e.get('hp','?')}/{e.get('max_hp','?')}hp]")
        elif sub == "LOOT":
            log_ev("Enemy slain — loot shared")
        elif sub == "DEAD":
            log_ev("PARTY WIPED!")
        elif sub == "EXPLORE":
            if pdg.get("sub") == "IDLE":
                log_ev("Dungeon party formed")
    # Player join/leave
    new_pl = set((dg.get("players") or {})) - set((pdg.get("players") or {}))
    for p in new_pl:
        log_ev(f"Dungeon: {p} joined")
    left_pl = set((pdg.get("players") or {})) - set((dg.get("players") or {}))
    for p in left_pl:
        log_ev(f"Dungeon: {p} left")

# ── Legiom ping (background thread) ───────────────────────────────────────────
def _ping_legiom_loop():
    while True:
        now  = _tcp_open(LEGIOM_IP, 22)   # SSH port — Windows blocks ICMP
        prev = state.get("legiom_online", False)
        state["legiom_online"] = now
        if now and not prev:
            log_ev("Legiom online")
        elif not now and prev:
            log_ev("Legiom offline")
        time.sleep(PING_LEGIOM)

def _ping_scarab_loop():
    while True:
        r    = subprocess.run(["ping", "-c", "1", "-W", "2", SCARAB_IP],
                              capture_output=True)
        now  = r.returncode == 0
        prev = state.get("scarab_online", False)
        state["scarab_online"] = now
        if now and not prev:
            log_ev("Scarab tunnel up")
        elif not now and prev:
            log_ev("Scarab tunnel down")
        time.sleep(PING_LEGIOM)

# ── Touchscreen listener ───────────────────────────────────────────────────────
def _find_touch_dev():
    """Return the /dev/input/eventN path for the touchscreen.
    Searches by sysfs device name first (ADS7846 / anything with 'touch').
    Falls back to first readable event device if no named match found.
    """
    candidates = sorted(glob.glob("/dev/input/event*"))
    # First pass: match by name
    for path in candidates:
        try:
            idx = path.split("event")[-1]
            with open(f"/sys/class/input/event{idx}/device/name") as nf:
                name = nf.read().strip().lower()
            if "touch" in name or "ads7846" in name:
                return path
        except Exception:
            continue
    # Fallback: first readable device
    for path in candidates:
        try:
            with open(path, "rb"):
                pass
            return path
        except Exception:
            continue
    return None

def _launch_ring_service():
    """Start raven-ring systemd service in the background."""
    try:
        subprocess.Popen(
            ["sudo", "systemctl", "start", "raven-ring"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        log_ev("Ring service starting...")
    except Exception as e:
        log_ev(f"Ring launch failed: {e}")

def _touch_loop():
    """Background thread: reads raw input events, cycles view on finger-down.
    Cycle order: network → dungeon → ring → network ...
    Special case: first tap on ring view when service offline → start the service.
    Debounced to 600ms so ADS7846 continuous events don't fire multiple times.
    """
    global _view
    EV_KEY    = 1
    BTN_TOUCH = 0x14a
    if platform.architecture()[0] == "32bit":
        fmt, sz = "IIHHi", 16   # Pi Zero (32-bit kernel)
    else:
        fmt, sz = "qqHHi", 24   # Pi 5 (64-bit kernel)

    dev = _find_touch_dev()
    if not dev:
        log_ev("Touch: no input device found")
        return
    log_ev(f"Touch listener: {dev}")

    last_tap = 0.0   # debounce timestamp
    DEBOUNCE = 0.6   # seconds

    try:
        with open(dev, "rb") as f:
            while True:
                data = f.read(sz)
                if len(data) < sz:
                    continue
                fields = struct.unpack(fmt, data)
                ev_type, ev_code, ev_value = fields[2], fields[3], fields[4]
                if ev_type != EV_KEY or ev_code != BTN_TOUCH or ev_value != 1:
                    continue
                now = time.time()
                if now - last_tap < DEBOUNCE:
                    continue   # ignore held/repeated events
                last_tap = now

                with _view_lock:
                    current = _view
                idx = _VIEWS.index(current) if current in _VIEWS else 0
                nxt = _VIEWS[(idx + 1) % len(_VIEWS)]

                # If leaving to ring view and service is offline, start it
                if nxt == "ring" and not state.get("ring_status"):
                    threading.Thread(target=_launch_ring_service, daemon=True).start()

                with _view_lock:
                    _view = nxt
                log_ev(f"View → {nxt.upper()}")
    except Exception as e:
        log_ev(f"Touch error: {e}")

# ── Framebuffer write ──────────────────────────────────────────────────────────
def to_fb(img: Image.Image) -> bytes:
    a  = np.array(img, dtype=np.uint16)
    r  = (a[:, :, 0] >> 3).astype(np.uint16)
    g  = (a[:, :, 1] >> 2).astype(np.uint16)
    b  = (a[:, :, 2] >> 3).astype(np.uint16)
    return ((r << 11) | (g << 5) | b).tobytes()

def flush(img: Image.Image):
    with open(FB, "wb") as f:
        f.write(to_fb(img))

# ── Fonts ──────────────────────────────────────────────────────────────────────
def load_fonts():
    B = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
    N = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
    L = "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf"
    b = B if os.path.exists(B) else (N if os.path.exists(N) else None)
    n = N if os.path.exists(N) else (L if os.path.exists(L) else None)
    def F(path, sz):
        return ImageFont.truetype(path, sz) if path else ImageFont.load_default()
    return {
        "title": F(b, 18),
        "head":  F(b, 11),
        "body":  F(n, 11),
        "small": F(n, 10),
        "tiny":  F(n,  9),
        "clock": F(b, 15),
    }

# ── Drawing primitives ─────────────────────────────────────────────────────────
def put(draw, x, y, s, font, color=WHITE):
    draw.text((x, y), str(s), font=font, fill=color)

def hline(draw, y, x0=0, x1=W, c=DIM):
    draw.line([(x0, y), (x1, y)], fill=c)

def vline(draw, x, y0=MY0, y1=MY1, c=DIM):
    draw.line([(x, y0), (x, y1)], fill=c)

def dot(draw, x, y, ok=True, pending=False, r=4):
    col = PENDING if pending else (GREEN if ok else RED)
    draw.ellipse([x-r, y-r, x+r, y+r],     fill=col)
    draw.ellipse([x-r+2, y-r+2, x+r-2, y+r-2], fill=BG)
    draw.ellipse([x-r+3, y-r+3, x+r-3, y+r-3], fill=col)

def hp_bar(draw, x, y, hp, maxhp, w=88, h=7, col=GREEN2):
    pct    = max(0.0, min(1.0, hp / max(maxhp, 1)))
    filled = int(w * pct)
    col_b  = RED if pct < 0.25 else (AMBER if pct < 0.55 else col)
    draw.rectangle([x, y, x+w, y+h], fill=DIMMER)
    if filled:
        draw.rectangle([x, y, x+filled, y+h], fill=col_b)
    draw.rectangle([x, y, x+w, y+h], outline=DIM)

def xp_bar(draw, x, y, xp, xp_next, w=88, h=3):
    pct    = max(0.0, min(1.0, xp / max(xp_next, 1)))
    filled = int(w * pct)
    draw.rectangle([x, y, x+w, y+h], fill=DIMMER)
    if filled:
        draw.rectangle([x, y, x+filled, y+h], fill=CYAN)

def section_head(draw, x, y, label, F):
    """Draw a ▶ LABEL section header."""
    put(draw, x, y, f"▶ {label}", F["head"], GREEN2)
    return y + 15

# ── Network render (default view) ─────────────────────────────────────────────
def render_network(st: dict, F: dict, tick: int) -> Image.Image:
    img  = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    hdb      = st.get("hashdb")
    unl      = st.get("unlock")
    raven    = st.get("raven")
    pend     = st.get("pending")
    dg       = st.get("dungeon")
    hdb_ok   = _ok(hdb)
    unl_ok   = _ok(unl)
    raven_ok = _ok(raven)
    pcount   = (pend or {}).get("count", 0)
    legiom   = st.get("legiom_online", False)

    # ── HEADER ─────────────────────────────────────────────────────────────────
    draw.rectangle([0, 0, W, HDR_H], fill=BG2)
    put(draw, 10, 11, "⬡  DUAT", F["title"], GREEN)
    put(draw, 113, 15, "//  RAVEN OS  //  HOME BASE", F["body"], MUTED)
    ts = datetime.now().strftime("%H:%M:%S")
    tw = F["clock"].getlength(ts)
    put(draw, W - int(tw) - 10, 13, ts, F["clock"], GREEN2)
    hline(draw, HDR_H, c=GREEN3)

    # ── LEFT PANEL — NODE ROSTER ───────────────────────────────────────────────
    LX = 8
    ly = MY0 + 1
    ly = section_head(draw, LX, ly, "NODE ROSTER", F)

    scarab_online     = st.get("scarab_online", False)
    scarab_in_dungeon = bool(dg and "SCARAB" in (dg.get("players") or {}))

    def node_row(icon, name, ip_str, ok, pending=False, badge=None, badge_c=GREEN2):
        nonlocal ly
        nc  = PENDING if pending else (WHITE  if ok else MUTED)
        ipc = PENDING if pending else (MUTED  if ok else DIMMER)
        sc  = PENDING if pending else (GREEN2 if ok else RED)
        lbl = "PENDING"  if pending else ("ONLINE" if ok else "OFFLINE")

        dot(draw, LX + 5, ly + 6, ok, pending=pending)
        put(draw, LX + 14, ly, f"{icon} {name}", F["body"], nc)
        put(draw, LX + 130, ly, lbl, F["tiny"], sc)
        ly += 12
        put(draw, LX + 14, ly, ip_str, F["tiny"], ipc)
        if badge:
            put(draw, LX + 130, ly, badge, F["tiny"], badge_c)
        ly += 12
        hline(draw, ly + 1, LX, VDIV - 4, DIMMER)
        ly += 5

    # DUAT (self — always online)
    node_row("◈", "DUAT",   DUAT_IP,   True,  badge="HOST",   badge_c=MUTED)
    # LEGIOM
    node_row("■", "LEGIOM", LEGIOM_IP, legiom, badge="MAIN PC" if legiom else None, badge_c=MUTED)
    # RAVEN
    ral = (raven or {}).get("pending_alerts", 0)
    node_row("◆", "RAVEN",  RAVEN_IP,  raven_ok,
             badge=f"{ral} alert{'s' if ral!=1 else ''}" if ral else None, badge_c=AMBER)
    # SCARAB
    scarab_badge = "DUNGEON" if scarab_in_dungeon else ("TUNNEL" if scarab_online else None)
    node_row("◉", "SCARAB", SCARAB_IP, scarab_online,
             badge=scarab_badge, badge_c=GREEN2)
    # ANUBIS — future
    node_row("○", "ANUBIS", "Awaiting deploy", False, pending=True)

    # ── VERTICAL DIVIDER ───────────────────────────────────────────────────────
    vline(draw, VDIV, MY0, MY1, c=DIM)

    # ── RIGHT PANEL ────────────────────────────────────────────────────────────
    RX = VDIV + 8
    ry = MY0 + 1

    # Services ──────────────────────────────────────────────────────────────────
    ry = section_head(draw, RX, ry, "SERVICES", F)

    # Hash DB
    dot(draw, RX + 5, ry + 6, hdb_ok)
    put(draw, RX + 14, ry, "HASH DB", F["body"], WHITE if hdb_ok else MUTED)
    put(draw, RX + 73, ry, f":{HASHDB_PORT}", F["small"], DIM)
    if hdb_ok:
        total = (hdb or {}).get("db_stats", {}).get("total_hashes", 0)
        unk   = (hdb or {}).get("db_stats", {}).get("unknown_pending", 0)
        put(draw, RX + 108, ry, f"{total:,}", F["small"], GREEN2)
        if unk:
            put(draw, RX + 175, ry, f"+{unk}?", F["small"], AMBER)
    else:
        put(draw, RX + 108, ry, "offline", F["small"], RED)
    ry += 14

    # Unlock service
    dot(draw, RX + 5, ry + 6, unl_ok)
    put(draw, RX + 14, ry, "UNLOCK ", F["body"], WHITE if unl_ok else MUTED)
    put(draw, RX + 73, ry, f":{UNLOCK_PORT}", F["small"], DIM)
    if unl_ok:
        qc = RED if pcount else GREEN2
        put(draw, RX + 108, ry, f"{pcount} pending", F["small"], qc)
    else:
        put(draw, RX + 108, ry, "offline", F["small"], RED)
    ry += 16

    hline(draw, ry, VDIV + 4, W, DIM)
    ry += 6

    # Dungeon ───────────────────────────────────────────────────────────────────
    ry = section_head(draw, RX, ry, "DUNGEON", F)

    if not dg or dg.get("sub") == "IDLE":
        put(draw, RX + 10, ry, "No active party.", F["body"], DIMMER)
        ry += 14
    else:
        sub      = dg.get("sub", "?")
        area_idx = dg.get("area", 0)
        players  = dg.get("players") or {}
        enemy    = dg.get("enemy") or {}
        dg_log   = dg.get("log") or []
        AREAS    = ["DUAT WASTES","SANDS OF SET","THOTH TEMPLES","HALL OF RA","VOID OF ISFET"]
        area_nm  = AREAS[min(area_idx, 4)]

        sub_c = {
            "EXPLORE": GREEN2, "COMBAT": RED, "LOOT": AMBER, "DEAD": MUTED
        }.get(sub, WHITE)
        put(draw, RX + 10, ry, sub, F["body"], sub_c)
        put(draw, RX + 68, ry, f"// {area_nm}", F["tiny"], MUTED)
        ry += 14

        # Player rows
        for pname, pd in list(players.items())[:3]:
            alive  = pd.get("alive", True)
            hp, mhp = pd.get("hp", 0), pd.get("max_hp", 25)
            xp, xpn = pd.get("xp", 0), pd.get("xp_next", 50)
            lvl    = pd.get("level", 1)
            gold   = pd.get("gold", 0)
            nc     = WHITE if alive else MUTED

            put(draw, RX + 10, ry, f"{pname[:7]}", F["small"], nc)
            put(draw, RX + 65, ry, f"L{lvl}", F["tiny"], CYAN)
            put(draw, RX + 82, ry, f"{gold}g", F["tiny"], AMBER)

            bx = RX + 108
            hp_bar(draw, bx, ry + 2, hp, mhp, w=88, h=7)
            put(draw, bx + 92, ry + 1, f"{hp}/{mhp}", F["tiny"], MUTED)
            ry += 11
            xp_bar(draw, bx, ry, xp, xpn, w=88, h=3)
            ry += 6

        # Enemy bar (combat)
        if sub == "COMBAT" and enemy:
            put(draw, RX + 10, ry, f"▸ {enemy.get('name','?')[:12]}", F["small"], RED)
            bx = RX + 108
            hp_bar(draw, bx, ry + 2, enemy.get("hp", 0), enemy.get("max_hp", 1),
                   w=88, h=7, col=RED)
            put(draw, bx + 92, ry + 1,
                f"{enemy.get('hp',0)}/{enemy.get('max_hp',1)}", F["tiny"], RED)
            ry += 14

        # Latest log line
        latest = next((l for l in dg_log if l), "")
        if latest:
            put(draw, RX + 10, ry, f"\"{latest[:38]}\"", F["tiny"], MUTED)
            ry += 13

    # Unlock queue (if non-empty) ────────────────────────────────────────────────
    if pcount:
        hline(draw, ry + 2, VDIV + 4, W, DIM)
        ry += 7
        ry = section_head(draw, RX, ry, "UNLOCK QUEUE", F)
        items = (pend or {}).get("pending", [])
        for item in items[:3]:
            put(draw, RX + 10, ry, f"🔒 {item.get('filename','?')[:32]}", F["tiny"], RED)
            ry += 12

    # ── EVENT LOG ──────────────────────────────────────────────────────────────
    draw.rectangle([0, LOG_Y0, W, LOG_Y1], fill=BG2)
    hline(draw, LOG_Y0, c=DIM)
    put(draw, 6, LOG_Y0 + 3, "//", F["head"], GREEN3)
    evs = list(_events)
    for i, ev in enumerate(evs[:3]):
        ey  = LOG_Y0 + 3 + i * 13
        col = WHITE if i == 0 else MUTED
        put(draw, 26, ey, ev[:76], F["tiny"], col)

    # ── FOOTER ──────────────────────────────────────────────────────────────────
    draw.rectangle([0, FTR_Y, W, H], fill=BG2)
    hline(draw, FTR_Y, c=DIMMER)
    date_s = datetime.now().strftime("%Y-%m-%d")
    host   = socket.gethostname().upper()
    put(draw, 8, FTR_Y + 4,
        f"RAVEN OS  //  {host}  //  {DUAT_IP}  //  {date_s}", F["tiny"], DIM)
    put(draw, W - 105, FTR_Y + 4, "[TAP=DUNGEON]", F["tiny"], MUTED)
    # Heartbeat blink
    bc = GREEN3 if tick % 2 == 0 else DIMMER
    draw.ellipse([W-13, FTR_Y+5, W-5, FTR_Y+13], fill=bc)

    return img


# ── Dungeon full-screen render ─────────────────────────────────────────────────
AREAS = ["DUAT WASTES", "SANDS OF SET", "THOTH TEMPLES", "HALL OF RA", "VOID OF ISFET"]

def render_dungeon(st: dict, F: dict, tick: int) -> Image.Image:
    img  = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    dg = st.get("dungeon") or {}

    # ── HEADER ─────────────────────────────────────────────────────────────────
    draw.rectangle([0, 0, W, HDR_H], fill=BG2)
    put(draw, 10, 11, "⬡  DUNGEON", F["title"], AMBER)
    put(draw, 120, 15, "//  QUEST COMPANION", F["body"], MUTED)
    ts = datetime.now().strftime("%H:%M:%S")
    tw = F["clock"].getlength(ts)
    put(draw, W - int(tw) - 10, 13, ts, F["clock"], GREEN2)
    hline(draw, HDR_H, c=GREEN3)

    MID = 230   # vertical divider x

    # ── LEFT PANEL — PARTY ─────────────────────────────────────────────────────
    LX = 8
    ly = MY0 + 1
    ly = section_head(draw, LX, ly, "PARTY", F)

    sub      = dg.get("sub", "IDLE")
    area_idx = dg.get("area", 0)
    players  = dg.get("players") or {}
    enemy    = dg.get("enemy") or {}
    dg_log   = dg.get("log") or []
    act_kills = dg.get("act_kills", 0)
    boss_pend = dg.get("boss_pending", False)
    isfet_pw  = dg.get("isfet_power", 0)

    area_nm  = AREAS[min(area_idx, 4)]

    sub_c = {"EXPLORE": GREEN2, "COMBAT": RED, "LOOT": AMBER, "DEAD": MUTED}.get(sub, WHITE)

    if sub == "IDLE" or not players:
        put(draw, LX + 10, ly, "No active party", F["body"], DIMMER)
        put(draw, LX + 10, ly + 16, "Waiting for adventurers...", F["tiny"], DIMMER)
    else:
        # Zone/state line
        put(draw, LX + 4, ly, sub, F["head"], sub_c)
        put(draw, LX + 60, ly, f"// {area_nm}", F["tiny"], MUTED)
        put(draw, LX + 4, ly + 12, f"Kills: {act_kills}", F["tiny"], MUTED)
        if boss_pend:
            put(draw, LX + 60, ly + 12, "BOSS!", F["tiny"], RED)
        ly += 26

        # Player rows — wider HP bars to use full left panel
        BAR_W = MID - LX - 60
        for pname, pd in list(players.items())[:4]:
            alive   = pd.get("alive", True)
            hp, mhp = pd.get("hp", 0), pd.get("max_hp", 25)
            xp, xpn = pd.get("xp", 0), pd.get("xp_next", 50)
            lvl     = pd.get("level", 1)
            cls     = pd.get("cls", "?")
            nc      = WHITE if alive else MUTED

            put(draw, LX + 4, ly, f"{pname[:6]}", F["small"], nc)
            put(draw, LX + 48, ly, f"L{lvl}", F["tiny"], CYAN)
            put(draw, LX + 64, ly, f"{cls[:4]}", F["tiny"], MUTED)

            bx = LX + 4
            hp_bar(draw, bx, ly + 12, hp, mhp, w=BAR_W, h=8)
            put(draw, bx + BAR_W + 4, ly + 12, f"{hp}/{mhp}", F["tiny"], MUTED)
            xp_bar(draw, bx, ly + 22, xp, xpn, w=BAR_W, h=3)
            ly += 30
            hline(draw, ly, LX, MID - 4, DIMMER)
            ly += 4

    # ── VERTICAL DIVIDER ───────────────────────────────────────────────────────
    vline(draw, MID, MY0, LOG_Y0 - 2, c=DIM)

    # ── RIGHT PANEL — ENEMY + LOG ──────────────────────────────────────────────
    RX = MID + 8
    ry = MY0 + 1
    ry = section_head(draw, RX, ry, "ENEMY", F)

    if sub == "COMBAT" and enemy:
        ename  = enemy.get("name", "?")
        ehp    = enemy.get("hp", 0)
        emhp   = enemy.get("max_hp", 1)
        is_boss = enemy.get("boss", False)

        boss_label = " [BOSS]" if is_boss else ""
        ecol = RED if not is_boss else AMBER
        put(draw, RX + 4, ry, f"{ename[:16]}{boss_label}", F["body"], ecol)
        ry += 14
        hp_bar(draw, RX + 4, ry, ehp, emhp, w=W - RX - 12, h=10, col=RED)
        put(draw, RX + 4, ry + 13, f"{ehp} / {emhp} HP", F["tiny"], RED)
        ry += 26

        if is_boss:
            # Special indicators
            spc = enemy.get("special", "")
            if spc:
                put(draw, RX + 4, ry, f"SPECIAL: {spc.upper()}", F["tiny"], AMBER)
                ry += 13
    else:
        put(draw, RX + 4, ry, sub if sub != "IDLE" else "None", F["body"], DIMMER)
        ry += 16

    # Isfet power tracker
    if isfet_pw > 0:
        hline(draw, ry, MID + 4, W, DIMMER)
        ry += 4
        put(draw, RX + 4, ry, f"ISFET POWER: {isfet_pw}", F["small"], RED)
        ry += 13

    # Battle log (right panel bottom)
    hline(draw, ry + 2, MID + 4, W, DIM)
    ry += 6
    section_head(draw, RX, ry, "LOG", F)
    ry += 15
    for i, line in enumerate(dg_log[:6]):
        if not line:
            continue
        col = WHITE if i == 0 else (MUTED if i < 3 else DIMMER)
        put(draw, RX + 4, ry, line[:30], F["tiny"], col)
        ry += 12
        if ry >= LOG_Y0 - 4:
            break

    # ── EVENT LOG STRIP ────────────────────────────────────────────────────────
    draw.rectangle([0, LOG_Y0, W, LOG_Y1], fill=BG2)
    hline(draw, LOG_Y0, c=DIM)
    put(draw, 6, LOG_Y0 + 3, "//", F["head"], GREEN3)
    evs = list(_events)
    for i, ev in enumerate(evs[:3]):
        ey  = LOG_Y0 + 3 + i * 13
        col = WHITE if i == 0 else MUTED
        put(draw, 26, ey, ev[:76], F["tiny"], col)

    # ── FOOTER ──────────────────────────────────────────────────────────────────
    draw.rectangle([0, FTR_Y, W, H], fill=BG2)
    hline(draw, FTR_Y, c=DIMMER)
    date_s = datetime.now().strftime("%Y-%m-%d")
    put(draw, 8, FTR_Y + 4,
        f"RAVEN OS  //  DUAT  //  QUEST COMPANION  //  {date_s}", F["tiny"], DIM)
    put(draw, W - 96, FTR_Y + 4, "[TAP=NETWORK]", F["tiny"], MUTED)
    bc = GREEN3 if tick % 2 == 0 else DIMMER
    draw.ellipse([W-13, FTR_Y+5, W-5, FTR_Y+13], fill=bc)

    return img


# ── Ring view ─────────────────────────────────────────────────────────────────
def render_ring(st: dict, F: dict, tick: int) -> Image.Image:
    img  = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    rs   = st.get("ring_status")   or {}
    bio  = st.get("ring_bio")      or {}
    base = st.get("ring_baseline") or {}
    gest = st.get("ring_gesture")  or {}

    svc_running = bool(st.get("ring_status"))
    ring_conn   = rs.get("connected", False)

    # ── HEADER ─────────────────────────────────────────────────────────────────
    draw.rectangle([0, 0, W, HDR_H], fill=BG2)
    put(draw, 10, 11, "◇  RING", F["title"], CYAN)
    put(draw, 100, 15, "//  R02  //  RING", F["body"], MUTED)
    ts = datetime.now().strftime("%H:%M:%S")
    tw = F["clock"].getlength(ts)
    put(draw, W - int(tw) - 10, 13, ts, F["clock"], GREEN2)
    hline(draw, HDR_H, c=GREEN3)

    # ── SERVICE / RING STATUS ──────────────────────────────────────────────────
    LX = 8
    y  = MY0 + 2

    # Service dot + label
    svc_col = GREEN  if svc_running else RED
    svc_lbl = "RUNNING" if svc_running else "STOPPED"
    dot(draw, LX + 5, y + 6, svc_running)
    put(draw, LX + 14, y,    "RING SERVICE", F["body"], WHITE if svc_running else MUTED)
    put(draw, LX + 110, y,   svc_lbl, F["tiny"], svc_col)
    put(draw, LX + 14, y+12, f"127.0.0.1:{RING_PORT}", F["tiny"], DIM)
    y += 26

    if not svc_running:
        hline(draw, y, 0, W, DIM)
        y += 10
        put(draw, LX, y, "Ring service is not running.", F["body"], MUTED)
        y += 16
        put(draw, LX, y, "[TAP TO START]", F["head"], AMBER)
        # Footer
        draw.rectangle([0, FTR_Y, W, H], fill=BG2)
        hline(draw, FTR_Y, c=DIMMER)
        put(draw, 8, FTR_Y + 4, "RAVEN OS  //  DUAT  //  RING", F["tiny"], DIM)
        put(draw, W - 120, FTR_Y + 4, "[TAP=START RING]", F["tiny"], AMBER)
        bc = GREEN3 if tick % 2 == 0 else DIMMER
        draw.ellipse([W-13, FTR_Y+5, W-5, FTR_Y+13], fill=bc)
        return img

    # BLE ring connection status
    ble_col = GREEN if ring_conn else AMBER
    ble_lbl = "BLE CONNECTED" if ring_conn else "BLE SEARCHING"
    dot(draw, LX + 5, y + 6, ring_conn)
    put(draw, LX + 14, y, ble_lbl, F["body"], ble_col)
    batt = bio.get("battery")
    if batt is not None:
        put(draw, LX + 130, y, f"{batt}%", F["small"], GREEN2 if batt > 30 else AMBER)
    y += 20
    hline(draw, y, 0, W, DIM)
    y += 6

    # ── LEFT PANEL: BIOMETRICS ─────────────────────────────────────────────────
    VDIV2 = 220
    y = section_head(draw, LX, y, "BIOMETRICS", F)

    def bio_row(label, value, unit, col=WHITE):
        nonlocal y
        put(draw, LX + 4, y, label, F["small"], MUTED)
        put(draw, LX + 60, y, str(value) if value is not None else "--", F["body"], col)
        put(draw, LX + 100, y, unit, F["tiny"], DIM)
        y += 16

    hr   = bio.get("heart_rate")
    spo2 = bio.get("spo2")
    steps = bio.get("steps")

    # Colour HR by rough zone (resting=green, elevated=amber, high=red)
    hr_col = (RED if hr and hr > 100 else (AMBER if hr and hr > 80 else GREEN))
    bio_row("HEART RATE", hr,    "BPM", hr_col)
    bio_row("SPO2",       spo2,  "%",   GREEN if (spo2 and spo2 >= 95) else AMBER)
    bio_row("STEPS",      steps, "today")

    # Battery bar
    put(draw, LX + 4, y, "BATTERY", F["small"], MUTED)
    if batt is not None:
        bar_w = 88
        fill  = int(bar_w * batt / 100)
        bar_col = RED if batt <= 15 else (AMBER if batt <= 30 else GREEN2)
        draw.rectangle([LX + 60, y + 2, LX + 60 + bar_w, y + 10], fill=DIMMER)
        if fill:
            draw.rectangle([LX + 60, y + 2, LX + 60 + fill, y + 10], fill=bar_col)
        draw.rectangle([LX + 60, y + 2, LX + 60 + bar_w, y + 10], outline=DIM)
        put(draw, LX + 155, y, f"{batt}%", F["tiny"], bar_col)
    else:
        put(draw, LX + 60, y, "--", F["body"], MUTED)
    y += 16

    hline(draw, y + 2, LX, VDIV2 - 4, DIMMER)
    y += 8

    # Confidence bar
    y = section_head(draw, LX, y, "CONFIDENCE", F)
    conf  = base.get("confidence", 0.0) or 0.0
    bar_w = VDIV2 - LX - 16
    fill  = int(bar_w * conf)
    conf_col = GREEN if conf >= 0.9 else (GREEN2 if conf >= 0.5 else AMBER)
    draw.rectangle([LX + 4, y + 2, LX + 4 + bar_w, y + 10], fill=DIMMER)
    if fill:
        draw.rectangle([LX + 4, y + 2, LX + 4 + fill, y + 10], fill=conf_col)
    draw.rectangle([LX + 4, y + 2, LX + 4 + bar_w, y + 10], outline=DIM)
    put(draw, LX + 4, y + 14, f"{conf:.4f}  ({conf*100:.1f}%)", F["tiny"], conf_col)
    put(draw, LX + 4, y + 25, "personal readiness", F["tiny"], DIM)
    y += 38

    # Baseline stats (compact)
    n = base.get("sample_count", 0)
    hr_m = base.get("hr_mean")
    hr_s = base.get("hr_std")
    if hr_m:
        put(draw, LX + 4, y,
            f"baseline  HR {hr_m:.0f}±{hr_s:.1f}  n={n}", F["tiny"], DIM)
        y += 12

    # ── VERTICAL DIVIDER ───────────────────────────────────────────────────────
    vline(draw, VDIV2, MY0 + 30, MY1, c=DIM)

    # ── RIGHT PANEL: GESTURE ───────────────────────────────────────────────────
    RX = VDIV2 + 8
    ry = MY0 + 30
    ry = section_head(draw, RX, ry, "GESTURE", F)

    gmode = gest.get("mode") or rs.get("gesture_mode", "off")
    mode_col = GREEN if gmode == "editing" else MUTED
    put(draw, RX + 4, ry, "MODE", F["small"], MUTED)
    put(draw, RX + 50, ry, gmode.upper(), F["body"], mode_col)
    ry += 18

    gname = gest.get("gesture")
    gts   = gest.get("ts")
    if gname:
        put(draw, RX + 4, ry, gname, F["body"], AMBER)
        ry += 14
        if gts:
            from datetime import datetime as _dt
            ts_str = _dt.fromtimestamp(gts).strftime("%H:%M:%S")
            put(draw, RX + 4, ry, ts_str, F["tiny"], DIM)
        ry += 14
    else:
        put(draw, RX + 4, ry, "--", F["body"], DIMMER)
        ry += 18

    hline(draw, ry + 2, VDIV2 + 4, W, DIMMER)
    ry += 8

    # BLE address
    ry = section_head(draw, RX, ry, "HARDWARE", F)
    put(draw, RX + 4, ry,     "XX:XX:XX:XX:XX:XX", F["tiny"], DIM)
    put(draw, RX + 4, ry + 11, "right hand", F["tiny"], DIMMER)
    ry += 26

    # Last seen
    last_seen = rs.get("last_seen")
    if last_seen:
        age = time.time() - last_seen
        if age < 60:
            age_s = f"{int(age)}s ago"
        else:
            age_s = f"{int(age/60)}m ago"
        put(draw, RX + 4, ry, f"last seen {age_s}", F["tiny"], DIM)

    # ── EVENT LOG ──────────────────────────────────────────────────────────────
    draw.rectangle([0, LOG_Y0, W, LOG_Y1], fill=BG2)
    hline(draw, LOG_Y0, c=DIM)
    put(draw, 6, LOG_Y0 + 3, "//", F["head"], GREEN3)
    evs = list(_events)
    for i, ev in enumerate(evs[:3]):
        ey  = LOG_Y0 + 3 + i * 13
        col = WHITE if i == 0 else MUTED
        put(draw, 26, ey, ev[:76], F["tiny"], col)

    # ── FOOTER ─────────────────────────────────────────────────────────────────
    draw.rectangle([0, FTR_Y, W, H], fill=BG2)
    hline(draw, FTR_Y, c=DIMMER)
    date_s = datetime.now().strftime("%Y-%m-%d")
    put(draw, 8, FTR_Y + 4,
        f"RAVEN OS  //  DUAT  //  RING  //  {date_s}", F["tiny"], DIM)
    put(draw, W - 105, FTR_Y + 4, "[TAP=NETWORK]", F["tiny"], MUTED)
    bc = GREEN3 if tick % 2 == 0 else DIMMER
    draw.ellipse([W-13, FTR_Y+5, W-5, FTR_Y+13], fill=bc)

    return img


# ── Ring client info view ─────────────────────────────────────────────────────
def render_ringinfo(F: dict, tick: int) -> Image.Image:
    """Shows colmi_r02_client version and available R02Client methods.
    Read this screen to tell Claude what methods are available."""
    img  = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    # Header
    draw.rectangle([0, 0, W, HDR_H], fill=BG2)
    put(draw, 10, 11, "◇  RING CLIENT INFO", F["title"], CYAN)
    ts = datetime.now().strftime("%H:%M:%S")
    tw = F["clock"].getlength(ts)
    put(draw, W - int(tw) - 10, 13, ts, F["clock"], GREEN2)
    hline(draw, HDR_H, c=GREEN3)

    y = MY0 + 4

    # Package version
    put(draw, 8, y, "PACKAGE", F["head"], GREEN2)
    put(draw, 90, y, "colmi-r02-client", F["body"], WHITE)
    y += 15
    put(draw, 8, y, "VERSION", F["head"], GREEN2)
    ver_col = GREEN if _ring_client_version != "not installed" else RED
    put(draw, 90, y, _ring_client_version, F["body"], ver_col)
    y += 18
    hline(draw, y, 0, W, DIM)
    y += 6

    # Methods list — two columns
    put(draw, 8, y, "R02Client METHODS", F["head"], GREEN2)
    y += 14

    methods = _ring_client_methods
    if not methods or methods[0].startswith("colmi") or methods[0].startswith("import"):
        put(draw, 8, y, methods[0] if methods else "none", F["body"], RED)
    else:
        # Split into two columns
        col_w   = W // 2 - 8
        max_rows = (LOG_Y0 - y) // 11
        left  = methods[:max_rows]
        right = methods[max_rows:max_rows * 2]
        for i, m in enumerate(left):
            put(draw, 8,       y + i * 11, m[:26], F["tiny"], WHITE)
        for i, m in enumerate(right):
            put(draw, W // 2,  y + i * 11, m[:26], F["tiny"], MUTED)

    # Event log strip
    draw.rectangle([0, LOG_Y0, W, LOG_Y1], fill=BG2)
    hline(draw, LOG_Y0, c=DIM)
    put(draw, 6, LOG_Y0 + 3, "//", F["head"], GREEN3)
    for i, ev in enumerate(list(_events)[:3]):
        put(draw, 26, LOG_Y0 + 3 + i * 13, ev[:76],
            F["tiny"], WHITE if i == 0 else MUTED)

    # Footer
    draw.rectangle([0, FTR_Y, W, H], fill=BG2)
    hline(draw, FTR_Y, c=DIMMER)
    put(draw, 8, FTR_Y + 4, "RAVEN OS  //  DUAT  //  RING INFO", F["tiny"], DIM)
    put(draw, W - 113, FTR_Y + 4, "[TAP=NETWORK]", F["tiny"], MUTED)
    bc = GREEN3 if tick % 2 == 0 else DIMMER
    draw.ellipse([W-13, FTR_Y+5, W-5, FTR_Y+13], fill=bc)

    return img


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    print("Duat Display — color console starting", flush=True)
    F    = load_fonts()
    tick = 0
    last = 0.0

    # Background threads
    threading.Thread(target=_ping_legiom_loop, daemon=True).start()
    threading.Thread(target=_ping_scarab_loop, daemon=True).start()
    threading.Thread(target=_touch_loop,       daemon=True).start()
    log_ev("Duat display online")

    while True:
        now = time.time()
        if now - last >= REFRESH_SVC:
            fetch_all()
            detect_transitions()
            duat_dungeon_tick()
            last = now
        try:
            with _state_lock:
                snap = dict(state)
            with _view_lock:
                v = _view
            if v == "dungeon":
                img = render_dungeon(snap, F, tick)
            elif v == "ring":
                img = render_ring(snap, F, tick)
            elif v == "ringinfo":
                img = render_ringinfo(F, tick)
            else:
                img = render_network(snap, F, tick)
            flush(img)
        except Exception as e:
            print(f"render error: {e}", flush=True)
        tick += 1
        time.sleep(1)


if __name__ == "__main__":
    main()
