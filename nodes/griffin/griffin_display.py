#!/usr/bin/env python3
"""
griffin_display.py - Raven OS
E-ink display for Scarab Pi Zero 2 (Waveshare 2.13" V4, no touch)
Clean professional layout with Watchdog heartbeat.

Modes:
  - Standby: no iPhone connected -> USB tether instructions
  - Setup:   iPhone connected, WireGuard configuring
  - Active:  tunnel established -- success + live timestamp
"""

import time
import subprocess
import os
import sys
import socket
import threading
from urllib.request import urlopen, Request as _Req
import json as _json

sys.path.insert(0, os.path.expanduser("~"))

try:
    from TP_lib import epd2in13_V4
    from PIL import Image, ImageDraw
    HAS_DISPLAY = True
except ImportError:
    from PIL import Image, ImageDraw
    HAS_DISPLAY = False
    print("[!] Display not available -- running headless")

# ── Config ─────────────────────────────────────────────────────
DUAT_TUNNEL_IP       = "172.16.0.1"
LEGIOM_IP            = "192.168.1.6"
WATCHDOG_PORT        = 7744
HEARTBEAT_INTERVAL   = 30
DISPLAY_REFRESH      = 5
_bound_char    = ""
_bound_check_t = 0
_start_time        = __import__("time").time()         # set in main() for uptime display
ACTIVITY_INTERVAL  = 15          # seconds between activity changes
BT_NOTIFY_SECONDS    = 30  # show BT screen this long on connect events

def _bt_state():
    try:
        import json as _j
        with open('/tmp/bt_pan_state.json') as _f:
            return _j.load(_f)
    except Exception:
        return {}

IDLE_ACTIVITIES = [
    ("SCOUTING",   "Scanning the perimeter..."),
    ("PATROLLING", "Running sector sweep..."),
    ("STANDBY",    "Awaiting operator orders"),
    ("MONITORING", "Watching the network..."),
    ("MAPPING",    "Charting local routes..."),
    ("ANALYZING",  "Processing threat data..."),
    ("RESTING",    "Conserving power..."),
    ("LISTENING",  "Passive monitoring mode"),
]
BOUND_ACTIVITIES = [
    ("TRADE ROUTE", "Securing gold reserves"),
    ("SCOUTING",    "Clearing path ahead..."),
    ("SUPPORT",     "+20% gold bonus active"),
    ("ESCORT",      "Shadowing hero..."),
    ("GUARDING",    "Maintaining the perimeter"),
]

# Canvas is landscape: 250 wide x 122 tall
IDLE_ACTIVITIES = [
    ("SCOUTING",   "Scanning the perimeter..."),
    ("PATROLLING", "Running sector sweep..."),
    ("STANDBY",    "Awaiting operator orders"),
    ("MONITORING", "Watching the network..."),
    ("MAPPING",    "Charting local routes..."),
    ("ANALYZING",  "Processing threat data..."),
    ("RESTING",    "Conserving power..."),
    ("LISTENING",  "Passive monitoring mode"),
]
BOUND_ACTIVITIES = [
    ("TRADE ROUTE", "Securing gold reserves"),
    ("SCOUTING",    "Clearing path ahead..."),
    ("SUPPORT",     "+20% gold bonus active"),
    ("ESCORT",      "Shadowing hero..."),
    ("GUARDING",    "Maintaining the perimeter"),
]

W = 250
H = 122

# ── State ──────────────────────────────────────────────────────
def get_tunnel_status():
    try:
        result = subprocess.check_output(
            ["sudo", "wg", "show", "wg0"],
            stderr=subprocess.DEVNULL,
            universal_newlines=True
        )
        if "latest handshake" in result:
            return "ACTIVE"
        elif "interface" in result:
            return "CONNECTING"
        return "DOWN"
    except Exception:
        return "DOWN"

def get_duat_status():
    try:
        result = subprocess.run(
            ["ping", "-c", "1", "-W", "2", DUAT_TUNNEL_IP],
            capture_output=True, timeout=5
        )
        return "ONLINE" if result.returncode == 0 else "OFFLINE"
    except Exception:
        return "OFFLINE"

def get_data_stats():
    try:
        result = subprocess.check_output(
            ["sudo", "wg", "show", "wg0", "transfer"],
            stderr=subprocess.DEVNULL,
            universal_newlines=True
        ).strip()
        if result:
            parts = result.split()
            if len(parts) >= 2:
                rx = int(parts[0]) / (1024 * 1024)
                tx = int(parts[1]) / (1024 * 1024)
                return "U:{:.1f}  D:{:.1f} MB".format(tx, rx)
    except Exception:
        pass
    return "U:0.0  D:0.0 MB"

def get_iphone_connected():
    """Check if iPhone is connected via USB tethering or WiFi hotspot.
    Returns 'usb', 'wifi', or False."""
    # USB tether: usb0 has an inet address
    try:
        r = subprocess.check_output("ip addr show usb0 2>/dev/null",
                                    shell=True, universal_newlines=True)
        if "inet " in r:
            return "usb"
    except Exception:
        pass
    # WiFi hotspot: wlan0 is on iPhone's fixed hotspot subnet 172.20.10.x
    try:
        r = subprocess.check_output("ip addr show wlan0 2>/dev/null",
                                    shell=True, universal_newlines=True)
        if "172.20.10." in r:
            return "wifi"
    except Exception:
        pass
    return False

# ── Heartbeat to Watchdog ──────────────────────────────────────
def heartbeat_loop():
    """Send UDP heartbeat to Legiom Watchdog every 30 seconds."""
    while True:
        try:
            tunnel = get_tunnel_status()
            duat   = get_duat_status()
            msg    = "GRIFFIN|Griffin|griffin|ALIVE|{}|{}".format(tunnel, duat)
            sock   = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.sendto(msg.encode(), (LEGIOM_IP, WATCHDOG_PORT))
            sock.close()
        except Exception:
            pass
        time.sleep(HEARTBEAT_INTERVAL)

hb_thread = threading.Thread(target=heartbeat_loop, daemon=True)
hb_thread.start()

# ── Drawing helpers ────────────────────────────────────────────
def draw_griffin_small(draw, cx, cy, s=0.45):
    """Egyptian griffin: falcon head, lion body, eagle wings."""
    c = 0
    def i(v): return int(v * s)
    def w(v): return max(1, int(v * s))

    # Lion body
    draw.ellipse([cx-i(16), cy-i(6), cx+i(16), cy+i(16)], fill=c)

    # Neck (connecting body to head, offset right)
    draw.polygon([
        (cx+i(4),  cy-i(6)),
        (cx+i(13), cy-i(6)),
        (cx+i(15), cy-i(17)),
        (cx+i(3),  cy-i(17)),
    ], fill=c)

    # Falcon head
    draw.ellipse([cx+i(2), cy-i(26), cx+i(20), cy-i(11)], fill=c)

    # Hooked beak
    draw.polygon([
        (cx+i(18), cy-i(22)),
        (cx+i(26), cy-i(18)),
        (cx+i(19), cy-i(14)),
    ], fill=c)

    # Eye (white circle)
    draw.ellipse([cx+i(12), cy-i(24), cx+i(17), cy-i(19)], fill=255)

    # Eagle wings spread
    draw.polygon([
        (cx-i(6),  cy-i(4)),
        (cx-i(30), cy-i(20)),
        (cx-i(26), cy-i(4)),
        (cx-i(14), cy+i(2)),
    ], fill=c)
    # Wing feather detail line
    draw.line([cx-i(26), cy-i(4), cx-i(30), cy-i(20)], fill=255, width=w(1))

    # Front legs (lion paws)
    draw.line([cx-i(8),  cy+i(16), cx-i(10), cy+i(28)], fill=c, width=w(2))
    draw.line([cx+i(2),  cy+i(16), cx+i(4),  cy+i(28)], fill=c, width=w(2))
    # Front paws
    draw.line([cx-i(10), cy+i(28), cx-i(14), cy+i(31)], fill=c, width=w(1))
    draw.line([cx-i(10), cy+i(28), cx-i(7),  cy+i(31)], fill=c, width=w(1))
    draw.line([cx+i(4),  cy+i(28), cx+i(1),  cy+i(31)], fill=c, width=w(1))
    draw.line([cx+i(4),  cy+i(28), cx+i(7),  cy+i(31)], fill=c, width=w(1))

    # Hind legs
    draw.line([cx-i(14), cy+i(12), cx-i(16), cy+i(26)], fill=c, width=w(2))
    draw.line([cx+i(12), cy+i(12), cx+i(14), cy+i(26)], fill=c, width=w(2))

    # Lion tail (arcs up behind body)
    draw.arc([cx-i(32), cy-i(6), cx-i(16), cy+i(14)],
             start=210, end=340, fill=c, width=w(2))
    # Tail tuft
    draw.polygon([
        (cx-i(32), cy-i(6)),
        (cx-i(36), cy-i(11)),
        (cx-i(27), cy-i(10)),
    ], fill=c)

def render_boot(draw, tick):
    draw.rectangle([0, 0, W, H], fill=255)
    draw.rectangle([0, 0, W, H], outline=0, width=2)
    draw_griffin_small(draw, W // 2, 44, s=0.90)
    draw.text((W//2 - 24, 82), "GRIFFIN", fill=0)
    draw.text((W//2 - 32, 96), "INITIALIZING...", fill=0)
    if tick % 2 == 0:
        draw.rectangle([85, 110, 165, 113], fill=0)


def _load_pet_state():
    try:
        import json as _j
        with open("/tmp/griffin_pet_state.json") as _f:
            d = _j.load(_f)
        if __import__("time").time() - d.get("updated_at", 0) < 60:
            return d
    except Exception:
        pass
    return None

def _draw_bar(draw, x, y, w, h, val, max_val, fill=0):
    draw.rectangle([x, y, x+w, y+h], outline=fill)
    if max_val > 0:
        filled = max(0, min(w-2, int((val / max_val) * (w-2))))
        if filled > 0:
            draw.rectangle([x+1, y+1, x+1+filled, y+h-1], fill=fill)

def render_farming(draw, ps, tick):
    companion = ps.get("companion", "")
    floor   = ps.get("floor", 1)
    level   = ps.get("level", 1)
    hp      = ps.get("hp", 0)
    max_hp  = ps.get("max_hp", 1)
    xp      = ps.get("xp", 0)
    xp_next = ps.get("xp_next", 100)
    kills   = ps.get("kills", 0)
    event   = ps.get("last_event", "")
    etype   = ps.get("event_type", "idle")
    elog    = ps.get("event_log", [])

    is_fighting = (etype == "combat")
    is_dead     = (etype == "death")

    draw.rectangle([0, 0, W, H], fill=255)

    # Header bar
    draw.rectangle([0, 0, W, 18], fill=0)
    if is_fighting and tick % 2 == 0:
        hdr = "FIGHTING!"
    elif companion:
        hdr = "COMPANION"
    else:
        hdr = "FARMING"
    draw.text((6, 4), "GRIFFIN", fill=255)
    draw.text((80, 4), hdr, fill=255)
    draw.text((W - 36, 4), "F" + str(floor), fill=255)

    # Scarab sprite
    cx, cy = 22, 50
    draw_griffin_small(draw, cx, cy, s=0.5)

    # Combat aura: pulsing ring when fighting
    if is_fighting:
        r = 19 if tick % 2 == 0 else 16
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=0, width=1)

    # Death X overlay
    if is_dead:
        draw.line([cx - 13, cy - 13, cx + 13, cy + 13], fill=0, width=2)
        draw.line([cx + 13, cy - 13, cx - 13, cy + 13], fill=0, width=2)

    # Stats block right of sprite
    draw.text((48, 21), "Lv" + str(level), fill=0)
    draw.text((80, 21), str(kills) + " kills", fill=0)

    # HP bar
    draw.text((48, 34), "HP", fill=0)
    _draw_bar(draw, 64, 36, 88, 7, hp, max_hp)
    draw.text((156, 34), str(hp) + "/" + str(max_hp), fill=0)

    # XP bar
    draw.text((48, 47), "XP", fill=0)
    _draw_bar(draw, 64, 49, 88, 7, xp, xp_next)
    draw.text((156, 47), str(xp) + "/" + str(xp_next), fill=0)

    # Companion name
    if companion:
        draw.text((48, 60), "WITH: " + companion[:16], fill=0)

    # Divider
    draw.line([6, 71, W - 6, 71], fill=0, width=1)

    # Event log: last 3 entries, newest at bottom
    PFXS = {"loot": "+ ", "death": "X ", "combat": "~ ", "win": "> ", "idle": "  "}
    if elog:
        entries = elog[-3:]
    elif event:
        entries = [{"text": event, "type": etype}]
    else:
        entries = []

    y = 74
    for i, entry in enumerate(entries):
        pfx  = PFXS.get(entry.get("type", ""), "  ")
        line = (pfx + entry.get("text", ""))[:36]
        # Newest line gets a blinking asterisk marker on even ticks
        if i == len(entries) - 1 and tick % 2 == 0:
            line = line[:34] + " *"
        draw.text((8, y), line, fill=0)
        y += 12

    # Pulsing dot bottom-right
    rd = 3 if tick % 2 == 0 else 2
    draw.ellipse([W - 10 - rd, H - 8 - rd, W - 10 + rd, H - 8 + rd], fill=0)


def _check_bound():
    global _bound_char, _bound_check_t
    try:
        with urlopen(_Req("http://192.168.1.5:5000/api/pets/bound?pet_id=griffin"), timeout=3) as r:
            data = _json.loads(r.read())
            _bound_char = data.get("char_name", "") if data.get("bound") else ""
    except Exception:
        pass
    _bound_check_t = __import__("time").time()



def render_idle(draw, tick):
    """Pwnagotchi-style companion idle display with rotating activity."""
    import time as _t
    now = _t.time()

    acts = BOUND_ACTIVITIES if _bound_char else IDLE_ACTIVITIES
    act_idx = int(now / ACTIVITY_INTERVAL) % len(acts)
    act_label, act_text = acts[act_idx]
    countdown = int(ACTIVITY_INTERVAL - (now % ACTIVITY_INTERVAL))

    uptime_s = int(now - _start_time) if _start_time > 0 else 0
    if uptime_s >= 3600:
        uptime_str = "{}h{}m".format(uptime_s // 3600, (uptime_s % 3600) // 60)
    else:
        uptime_str = "{}m{}s".format(uptime_s // 60, uptime_s % 60)

    draw.rectangle([0, 0, W, H], fill=255)

    # Header bar
    draw.rectangle([0, 0, W, 18], fill=0)
    draw.text((6, 4), "GRIFFIN", fill=255)
    draw.text((70, 4), act_label, fill=255)
    cd_str = "{}s".format(countdown)
    draw.text((W - 6 - len(cd_str) * 6, 4), cd_str, fill=255)

    # Large griffin graphic centered
    draw_griffin_small(draw, W // 2, 50, s=0.75)

    # Activity text
    draw.text((8, 78), "> " + act_text, fill=0)

    # Divider + bottom info
    draw.line([6, 90, W - 6, 90], fill=0, width=1)
    if _bound_char:
        draw.text((6, 93), "BOUND: " + _bound_char[:14], fill=0)
    else:
        draw.text((6, 93), "NO HERO LINKED", fill=0)
    draw.text((6, 104), "BIND/UNBIND: 3847", fill=0)
    draw.text((W - 70, 104), "UP " + uptime_str, fill=0)

    # Pulsing dot
    r = 3 if tick % 2 == 0 else 2
    draw.ellipse([W - 10 - r, H - 8 - r, W - 10 + r, H - 8 + r], fill=0)


def render_setup(draw, tick, method="usb"):
    """iPhone connected via USB or WiFi, WireGuard configuring."""
    draw.rectangle([0, 0, W, H], fill=255)
    draw.rectangle([0, 0, W, 18], fill=0)
    draw.text((6, 4), "GRIFFIN", fill=255)
    draw.text((124, 4), "CONFIGURING", fill=255)
    draw.line([8, 19, W - 8, 19], fill=0, width=1)

    draw_griffin_small(draw, W - 22, 30, s=0.40)

    draw.text((8, 25), "iPhone detected", fill=0)
    draw.text((8, 40), "Starting WireGuard...", fill=0)

    n_dots = (tick % 5) + 1
    dots = ". " * n_dots
    draw.text((8, 57), dots.strip(), fill=0)

    cx, cy = 18, 95
    r = 4 if tick % 2 == 0 else 3
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=0, width=2)
    link_label = "WIFI HOTSPOT OK" if method == "wifi" else "USB TETHER OK"
    draw.text((28, 88), link_label, fill=0)
    draw.text((28, 102), "AWAITING WG HANDSHAKE", fill=0)

def render_active(draw, duat, data, tick, tunnel="ACTIVE", started=0.0):
    draw.rectangle([0, 0, W, H], fill=255)
    # Header bar
    draw.rectangle([0, 0, W, 18], fill=0)
    draw.text((6, 4), "GRIFFIN", fill=255)
    # Pulsing dot
    r = 4 if tick % 2 == 0 else 3
    draw.ellipse([W-22-r, 9-r, W-22+r, 9+r], fill=255)
    draw.text((W-16, 4), "ON", fill=255)
    # Small griffin top right
    draw_griffin_small(draw, W-28, 24, s=0.42)
    draw.line([0, 19, W, 19], fill=0, width=1)

    col1 = 8    # label x
    col2 = 72   # dot x
    col3 = 84   # value x

    # TUNNEL row
    draw.text((col1, 26), "TUNNEL", fill=0)
    dot_fill = 0 if tunnel == "ACTIVE" else 255
    draw.ellipse([col2, 29, col2+6, 35], fill=dot_fill, outline=0)
    draw.text((col3, 26), tunnel, fill=0)
    if tunnel == "ACTIVE" and started > 0:
        since_str = "since " + time.strftime("%H:%M", time.localtime(started))
        draw.text((col3 + 50, 26), since_str, fill=0)

    # DUAT row
    draw.text((col1, 40), "DUAT", fill=0)
    dot_fill2 = 0 if duat == "ONLINE" else 255
    draw.ellipse([col2, 43, col2+6, 49], fill=dot_fill2, outline=0)
    draw.text((col3, 40), duat, fill=0)

    draw.line([col1, 54, 185, 54], fill=0, width=1)

    # DATA row
    draw.text((col1, 58), "DATA", fill=0)
    draw.text((col3, 58), data, fill=0)

    draw.line([col1, 71, 185, 71], fill=0, width=1)

    draw.text((col1, 75), "ravenos.myddns.me", fill=0)
    draw.text((col1, 88), "[ENCRYPTED]", fill=0)
    now_str = time.strftime("%H:%M:%S")
    draw.text((col1, 104), "updated " + now_str, fill=0)

    # Lock icon area
    draw.rectangle([W-20, 74, W-4, 90], outline=0, fill=255)
    draw.arc([W-17, 74, W-7, 82], start=0, end=180, fill=0, width=2)
    draw.rectangle([W-18, 79, W-6, 89], fill=0)

def render_fallback(draw, tick):
    draw.rectangle([0, 0, W, H], fill=255)
    draw.rectangle([0, 0, W, 18], fill=0)
    draw.text((6, 4), "GRIFFIN", fill=255)
    draw.text((148, 4), "FALLBACK", fill=255)
    draw_griffin_small(draw, W//2, 28, s=0.85)
    draw.line([W//2-28, 14, W//2+28, 62], fill=0, width=2)
    draw.line([W//2+28, 14, W//2-28, 62], fill=0, width=2)
    draw.line([8, 72, W-8, 72], fill=0, width=1)
    draw.text((W//2 - 50, 78), "DUAT UNREACHABLE", fill=0)
    draw.text((W//2 - 60, 92), "DIRECT CONNECTION ACTIVE", fill=0)
    retry = "RETRYING  . . ." if tick % 2 == 0 else "RETRYING"
    draw.text((W//2 - 28, 107), retry, fill=0)

def render_bt_notify(draw, bts, remaining):
    """BT connection event screen shown for BT_NOTIFY_SECONDS."""
    draw.rectangle([0, 0, W, H], fill=255)
    draw.rectangle([0, 0, W, 18], fill=0)
    draw.text((6, 4), 'BT LINK', fill=255)
    mode = bts.get('mode', '').upper()
    draw.text((68, 4), '[{}]'.format(mode), fill=255)
    draw.text((200, 4), '{}s'.format(remaining), fill=255)
    pin = bts.get('pin')
    if pin:
        draw.text((8, 26), 'PAIRING CODE:', fill=0)
        draw.text((8, 42), pin, fill=0)
        draw.text((8, 62), 'Enter on phone', fill=0)
    elif bts.get('connected'):
        ph  = bts.get('phone') or 'Phone'
        pip = bts.get('phone_ip') or ''
        oif = bts.get('out_if') or ''
        draw.text((8, 26), 'LINKED: {}'.format(ph[:16]), fill=0)
        if pip:
            draw.text((8, 42), 'IP: {}'.format(pip), fill=0)
        draw.text((8, 58), 'via {}'.format(oif), fill=0)
        sp = bts.get('socks_port')
        if sp:
            draw.text((8, 74), 'SOCKS5 :{}'.format(sp), fill=0)
            draw.text((8, 88), 'set proxy on phone', fill=0)
    else:
        draw.text((8, 26), 'Phone disconnected', fill=0)
        if mode == 'NAP':
            draw.text((8, 42), 'Connect RAVEN-OS', fill=0)
            draw.text((8, 56), 'in BT settings', fill=0)
    draw_griffin_small(draw, W - 22, H - 28, s=0.4)


def main():
    if HAS_DISPLAY:
        epd = epd2in13_V4.EPD()
        epd.init()
        epd.Clear(0xFF)
        canvas = Image.new('1', (epd.height, epd.width), 255)
        draw   = ImageDraw.Draw(canvas)
        epd.displayPartBaseImage(epd.getbuffer(canvas))
    else:
        canvas = Image.new('1', (W, H), 255)
        draw   = ImageDraw.Draw(canvas)

    global _start_time; _start_time = __import__('time').time()
    print("[*] Griffin display starting...")
    tick = 0

    # Boot screen 3 seconds
    for _ in range(6):
        draw.rectangle([0, 0, W, H], fill=255)
        render_boot(draw, tick)
        if HAS_DISPLAY:
            epd.displayPartial(epd.getbuffer(canvas))
        if tick % 6 == 0: _check_bound()
        tick += 1
        time.sleep(0.5)

    if HAS_DISPLAY:
        epd.init()

    print("[*] Entering main loop")

    _tunnel_started  = 0.0
    _prev_tunnel     = None
    _bt_notify_start = 0.0
    _bt_prev_conn    = None
    _bt_prev_pin     = None

    while True:
        # ── BT notify check ────────────────────────────────────────────────
        _bts      = _bt_state()
        _bt_conn  = _bts.get('connected', False)
        _bt_pin   = _bts.get('pin')
        if _bt_conn != _bt_prev_conn or _bt_pin != _bt_prev_pin:
            _bt_notify_start = time.time()
        _bt_prev_conn = _bt_conn
        _bt_prev_pin  = _bt_pin

        _bt_elapsed = time.time() - _bt_notify_start
        if _bt_notify_start > 0 and _bt_elapsed < BT_NOTIFY_SECONDS:
            remaining = int(BT_NOTIFY_SECONDS - _bt_elapsed)
            draw.rectangle([0, 0, W, H], fill=255)
            render_bt_notify(draw, _bts, remaining)
            if HAS_DISPLAY:
                epd.displayPartial(epd.getbuffer(canvas))
            time.sleep(1)
            continue

        iphone = get_iphone_connected()
        tunnel = get_tunnel_status()
        duat   = get_duat_status()
        data   = get_data_stats()

        # Track when tunnel first became active (for "since HH:MM" timestamp)
        if tunnel == "ACTIVE" and _prev_tunnel != "ACTIVE":
            _tunnel_started = time.time()
            print("[*] Tunnel ACTIVE at {}".format(time.strftime('%H:%M:%S')))
        _prev_tunnel = tunnel

        draw.rectangle([0, 0, W, H], fill=255)

        _ps = _load_pet_state()
        if _ps:
            render_farming(draw, _ps, tick)
        elif not iphone:
            render_idle(draw, tick)
        elif tunnel == "DOWN":
            # iPhone present but WireGuard still starting
            render_setup(draw, tick, method=iphone)
        elif tunnel in ("ACTIVE", "CONNECTING"):
            # Tunnel is up -- show status with connected-since timestamp
            render_active(draw, duat, data, tick, tunnel, _tunnel_started)
        else:
            render_idle(draw, tick)

        if HAS_DISPLAY:
            epd.displayPartial(epd.getbuffer(canvas))

        tick += 1
        time.sleep(DISPLAY_REFRESH)

if __name__ == "__main__":
    main()
