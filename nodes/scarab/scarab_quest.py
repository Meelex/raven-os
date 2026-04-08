#!/usr/bin/env python3
"""
scarab_quest.py -- Raven OS / Scarab
Display-only dungeon renderer for Scarab's Waveshare 2.13" V4 e-ink.
250x122 pixels, 1-bit, NO TOUCH -- everything is display-only.

Polls /quest/state from Duat every 5s and re-renders.
Called by scarab_display.py when realm=True.

Public API:
    from scarab_quest import poll_quest_state, draw_scarab_dungeon, DUNGEON_REFRESH
"""

import json
import time
from urllib.request import urlopen, Request
from urllib.error   import URLError

# ── Config ────────────────────────────────────────────────────────────────────
DUAT_IP        = "192.168.1.5"
QUEST_PORT     = 6178
QUEST_URL      = "http://" + DUAT_IP + ":" + str(QUEST_PORT) + "/quest/state"
DUNGEON_REFRESH = 5   # seconds between polls

# ── Canvas dimensions ─────────────────────────────────────────────────────────
W = 250
H = 122

# ── Cached state from last poll ───────────────────────────────────────────────
_state = None
_last_poll = 0.0
_anim_fr   = 0        # animation frame counter, incremented each render

# ── Acts ──────────────────────────────────────────────────────────────────────
_ACTS = [
    "DUAT WASTES",
    "SANDS OF SET",
    "THOTH TEMPLES",
    "HALL OF RA",
    "VOID OF ISFET",
]
_ACT_ROMAN = ["I", "II", "III", "IV", "V"]

# ── State polling ─────────────────────────────────────────────────────────────
def poll_quest_state():
    """Fetch /quest/state from Duat. Returns parsed dict or None on failure."""
    try:
        req = Request(QUEST_URL, headers={"User-Agent": "ScarabOS/1.0"})
        with urlopen(req, timeout=4) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None

def get_state(force=False):
    """Return cached state, refreshing if stale or forced."""
    global _state, _last_poll
    now = time.time()
    if force or (now - _last_poll) >= DUNGEON_REFRESH:
        fresh = poll_quest_state()
        if fresh is not None:
            _state = fresh
        _last_poll = now
    return _state

def is_in_realm():
    """Quick check: returns True if Duat reports realm=True."""
    s = get_state()
    return bool(s and s.get("realm", False))

# ── Sprite library (adapted from quest_companion.py) ─────────────────────────
def _sp_scarab(d, x, y, fr=0):
    """Scarab beetle sprite ~28x22px."""
    d.ellipse([x+0,  y+6,  x+14, y+22], fill=0)
    d.ellipse([x+14, y+6,  x+28, y+22], fill=0)
    d.line([(x+14, y+6), (x+14, y+22)],  fill=255, width=1)
    d.ellipse([x+8,  y+0,  x+20, y+10], fill=0)
    d.line([(x+10, y+0),  (x+7,  y-4)],  fill=0, width=2)
    d.line([(x+18, y+0),  (x+21, y-4)],  fill=0, width=2)
    d.ellipse([x+9,  y+2,  x+12, y+5],  fill=255)
    d.ellipse([x+16, y+2,  x+19, y+5],  fill=255)
    ly = y+10 if fr % 2 == 0 else y+12
    for i in range(3):
        d.line([(x+2,  y+9+i*4), (x-5,  ly+i*3)], fill=0, width=1)
        d.line([(x+26, y+9+i*4), (x+33, ly+i*3)], fill=0, width=1)

def _sp_raven(d, x, y, fr=0):
    """Raven character sprite ~34x30px."""
    d.ellipse([x+4,  y+8,  x+24, y+20], fill=0)
    d.ellipse([x+17, y+2,  x+28, y+12], fill=0)
    d.polygon([(x+27,y+5), (x+34,y+7), (x+27,y+9)], fill=0)
    wy = y+3 if fr % 2 == 0 else y+15
    d.polygon([(x+6, y+12), (x+0,  wy), (x+18, y+10)], fill=0)
    d.polygon([(x+4, y+13), (x+4,  y+19), (x-3, y+16)], fill=0)
    d.ellipse([x+20, y+4,  x+23, y+7],  fill=255)
    d.line([(x+10, y+20), (x+8,  y+26)], fill=0, width=2)
    d.line([(x+18, y+20), (x+20, y+26)], fill=0, width=2)
    d.line([(x+8,  y+26), (x+5,  y+28)], fill=0, width=1)
    d.line([(x+8,  y+26), (x+11, y+28)], fill=0, width=1)
    d.line([(x+20, y+26), (x+17, y+28)], fill=0, width=1)
    d.line([(x+20, y+26), (x+23, y+28)], fill=0, width=1)

def _sp_shade(d, x, y, fr=0):
    d.ellipse([x+4, y+2,  x+18, y+14], fill=0)
    d.ellipse([x+6, y+5,  x+9,  y+9],  fill=255)
    d.ellipse([x+13,y+5,  x+16, y+9],  fill=255)
    wy = 3 if fr % 2 == 0 else -3
    for wx in range(0, 24, 6):
        d.arc([x+wx, y+14, x+wx+6, y+20+wy], 0, 180, fill=0, width=2)

def _sp_jackal(d, x, y, fr=0):
    d.ellipse([x+6, y+4,  x+20, y+14], fill=0)
    d.polygon([(x+8, y+4), (x+6,  y-2), (x+10, y+3)], fill=0)
    d.polygon([(x+14,y+4), (x+18, y-2), (x+14, y+3)], fill=0)
    d.ellipse([x+7, y+7,  x+10, y+10], fill=255)
    d.ellipse([x+14,y+7,  x+17, y+10], fill=255)
    d.polygon([(x+2,y+14),(x+22,y+14),(x+20,y+28),(x+4,y+28)], fill=0)
    ay = y+18 if fr % 2 == 0 else y+16
    d.line([(x+4, y+18), (x-2, ay)],  fill=0, width=2)
    d.line([(x+20,y+18), (x+26,ay)],  fill=0, width=2)
    d.line([(x+8, y+28), (x+6, y+38)], fill=0, width=2)
    d.line([(x+16,y+28), (x+18,y+38)], fill=0, width=2)

def _sp_mummy(d, x, y, fr=0):
    d.ellipse([x+4, y+0,  x+18, y+14], fill=0)
    d.ellipse([x+6, y+4,  x+10, y+8],  fill=255)
    d.ellipse([x+12,y+4,  x+16, y+8],  fill=255)
    d.rectangle([x+2,y+14,x+20,y+38], fill=0)
    for wy in range(y+16, y+36, 5):
        d.line([(x+2, wy), (x+20, wy)], fill=255, width=1)
    ay = y+20 if fr % 2 == 0 else y+18
    d.line([(x+2, y+18), (x-5, ay)],  fill=0, width=3)
    d.line([(x+20,y+18), (x+27,ay)],  fill=0, width=3)
    d.line([(x+7, y+38), (x+5, y+48)], fill=0, width=3)
    d.line([(x+15,y+38), (x+17,y+48)], fill=0, width=3)

def _sp_scorpion(d, x, y, fr=0):
    d.ellipse([x+4, y+6,  x+20, y+18], fill=0)
    d.ellipse([x+8, y+2,  x+16, y+10], fill=0)
    d.ellipse([x+9, y+4,  x+12, y+7],  fill=255)
    d.ellipse([x+13,y+4,  x+16, y+7],  fill=255)
    d.arc([x+16,y+0, x+28,y+12], 270, 90,  fill=0, width=3)
    d.arc([x+22,y-4, x+30,y+4],  90,  270, fill=0, width=3)
    d.polygon([(x+26,y-4),(x+30,y-6),(x+28,y+0)], fill=0)
    for i in range(4):
        cy = y+10 if fr % 2 == 0 else y+12
        d.line([(x+4,  y+9+i*2), (x-4, cy+i*2)], fill=0, width=1)
        d.line([(x+20, y+9+i*2), (x+28,cy+i*2)], fill=0, width=1)

def _sp_wraith(d, x, y, fr=0):
    d.ellipse([x+5, y+0,  x+17, y+10], fill=0)
    d.ellipse([x+7, y+3,  x+10, y+6],  fill=255)
    d.ellipse([x+12,y+3,  x+15, y+6],  fill=255)
    d.polygon([(x+0,y+10),(x+22,y+10),(x+26,y+35),(x-4,y+35)], fill=0)
    wy = 4 if fr % 2 == 0 else -4
    for wx in range(0, 30, 6):
        d.arc([x-4+wx, y+35, x-4+wx+6, y+41+wy], 0, 180, fill=0, width=2)
    ay = y+16 if fr % 2 == 0 else y+14
    d.line([(x+0, y+14), (x-8, ay)],   fill=0, width=2)
    d.line([(x+22,y+14), (x+30,ay)],   fill=0, width=2)

def _sp_guardian(d, x, y, fr=0):
    d.ellipse([x+4, y+0,  x+18, y+12], fill=0)
    d.ellipse([x+6, y+4,  x+9,  y+7],  fill=255)
    d.ellipse([x+13,y+4,  x+16, y+7],  fill=255)
    d.arc([x+2, y-4, x+20, y+8], 180, 360, fill=0, width=3)
    d.rectangle([x+4,y+12,x+18,y+34], fill=0)
    d.ellipse([x-6,y+12,x+4, y+28], fill=0)
    d.ellipse([x-4,y+14,x+2, y+26], fill=255)
    ay = y+20 if fr % 2 == 0 else y+18
    d.line([(x+18,y+16),(x+26,ay)], fill=0, width=3)
    d.line([(x+8, y+34),(x+6, y+44)], fill=0, width=3)
    d.line([(x+14,y+34),(x+16,y+44)], fill=0, width=3)

def _sp_scribe(d, x, y, fr=0):
    d.ellipse([x+5, y+0,  x+17, y+12], fill=0)
    d.ellipse([x+7, y+4,  x+10, y+7],  fill=255)
    d.ellipse([x+12,y+4,  x+15, y+7],  fill=255)
    d.polygon([(x+2,y+12),(x+20,y+12),(x+18,y+36),(x+4,y+36)], fill=0)
    d.line([(x+20,y+8),(x+20,y+38)],  fill=0, width=2)
    d.ellipse([x+17,y+5, x+23,y+11],  fill=0)
    ay = y+18 if fr % 2 == 0 else y+16
    d.line([(x+2,y+16),(x-4,ay)],  fill=0, width=2)
    d.line([(x+8, y+36),(x+6, y+46)], fill=0, width=3)
    d.line([(x+14,y+36),(x+16,y+46)], fill=0, width=3)

def _sp_hawk(d, x, y, fr=0):
    d.ellipse([x+4, y+4,  x+20, y+16], fill=0)
    d.polygon([(x+20,y+6),(x+30,y+4),(x+22,y+10)], fill=0)
    d.ellipse([x+15,y+7,  x+18, y+10], fill=255)
    wy = y+2 if fr % 2 == 0 else y+8
    d.polygon([(x+4, y+10),(x-4,wy),(x+10,y+14)], fill=0)
    d.polygon([(x+20,y+10),(x+28,wy),(x+14,y+14)], fill=0)
    d.rectangle([x+6,y+16,x+18,y+36], fill=0)
    d.line([(x+8, y+36),(x+6, y+46)], fill=0, width=3)
    d.line([(x+14,y+36),(x+16,y+46)], fill=0, width=3)

def _sp_void(d, x, y, fr=0):
    sz = 2 if fr % 2 == 0 else 0
    d.ellipse([x+2-sz, y+4-sz,  x+20+sz, y+20+sz], fill=0)
    d.ellipse([x+5, y+7,  x+9,  y+11], fill=255)
    d.ellipse([x+13,y+7,  x+17, y+11], fill=255)
    for i in range(4):
        tx = x + i * 6
        ty = y + 16 + (2 if fr % 2 == 0 else -2)
        d.line([(tx, y+18), (tx-2, ty+14)], fill=0, width=2)
        d.line([(tx+2,y+6), (tx+6, y-4)],  fill=0, width=1)

def _sp_boss(d, x, y, fr=0):
    for bx, by in [(0,-8),(6,-11),(12,-8)]:
        d.polygon([(x+bx,y+4),(x+bx+4,y+4),(x+bx+2,y+by)], fill=0)
    d.ellipse([x+2, y+4,  x+28, y+20], fill=0)
    d.ellipse([x+5, y+8,  x+9,  y+13], fill=255)
    d.ellipse([x+21,y+8,  x+25, y+13], fill=255)
    d.arc([x+10,y+14, x+20,y+19], 0, 180, fill=255, width=2)
    d.polygon([(x+0,y+20),(x+30,y+20),(x+32,y+40),(x-2,y+40)], fill=0)
    cw = x-10 if fr % 2 == 0 else x-12
    d.polygon([(x+0, y+22),(cw,    y+35),(x+0, y+38)], fill=0)
    d.polygon([(x+30,y+22),(x+42,  y+35),(x+30,y+38)], fill=0)
    d.line([(x+10,y+40),(x+8, y+54)], fill=0, width=4)
    d.line([(x+20,y+40),(x+22,y+54)], fill=0, width=4)

def _sp_ammit(d, x, y, fr=0):
    d.polygon([(x+0,y+8),(x+22,y+8),(x+22,y+14),(x+0,y+14)], fill=0)
    d.polygon([(x+0,y+6),(x+22,y+6),(x+22,y+8),(x+0,y+8)],   fill=255)
    d.ellipse([x+16,y+4, x+20,y+8], fill=255)
    for tx in range(2, 20, 4):
        d.rectangle([x+tx,y+8, x+tx+2,y+11], fill=255)
    d.polygon([(x+4,y+14),(x+24,y+14),(x+26,y+34),(x+2,y+34)], fill=0)
    ay = y+20 if fr % 2 == 0 else y+18
    d.line([(x+4, y+20),(x-4,ay)],   fill=0, width=3)
    d.line([(x+24,y+20),(x+32,ay)],  fill=0, width=3)
    d.line([(x+8, y+34),(x+5, y+44)], fill=0, width=3)
    d.line([(x+18,y+34),(x+21,y+44)], fill=0, width=3)

def _sp_isfet(d, x, y, fr=0):
    sz = 3 if fr % 2 == 0 else 0
    d.ellipse([x-sz, y+2-sz, x+32+sz, y+26+sz], fill=0)
    d.ellipse([x+3, y+6,  x+9,  y+12], fill=255)
    d.ellipse([x+23,y+6,  x+29, y+12], fill=255)
    d.ellipse([x+5, y+8,  x+7,  y+10], fill=0)
    d.ellipse([x+25,y+8,  x+27, y+10], fill=0)
    d.arc([x+10,y+16, x+22,y+22], 0, 180, fill=255, width=2)
    for i in range(5):
        tx = x + i * 7
        oy = 4 if i % 2 == 0 else 0
        d.line([(tx, y+24),(tx-3, y+42+oy)], fill=0, width=2)
    for i in range(4):
        tx = x + i * 8
        d.line([(tx, y+4), (tx-4, y-8)], fill=0, width=2)

# Enemy sprite dispatch
_SP = {
    "SHADE":     _sp_shade,
    "JACKAL":    _sp_jackal,
    "MUMMY":     _sp_mummy,
    "SCORPION":  _sp_scorpion,
    "WRAITH":    _sp_wraith,
    "SET SLAVE": _sp_shade,
    "GUARDIAN":  _sp_guardian,
    "SCRIBE":    _sp_scribe,
    "T.GHOST":   _sp_wraith,
    "SUN WARR":  _sp_hawk,
    "FLAME DJN": _sp_guardian,
    "SOL.HAWK":  _sp_hawk,
    "VOID SHDE": _sp_void,
    "ENDLESS":   _sp_void,
    "C.SPAWN":   _sp_void,
    "AMMIT":     _sp_ammit,
    "SET":       _sp_boss,
    "APEP":      _sp_boss,
    "SEKHMET":   _sp_boss,
    "ISFET":     _sp_isfet,
}

# ── Drawing helpers ───────────────────────────────────────────────────────────
def _bar(d, x, y, w, h, val, maxv):
    """Filled HP/XP bar. White background, black fill."""
    d.rectangle([x, y, x+w, y+h], outline=0, fill=255)
    if maxv > 0 and val > 0:
        filled = int(w * max(0, min(val, maxv)) / maxv)
        if filled > 0:
            d.rectangle([x, y, x+filled, y+h], fill=0)

def _roman(n):
    return _ACT_ROMAN[min(n, 4)]

def _auto_badge(d):
    """Draw 'AUTO' badge at bottom-right — no-touch indicator."""
    d.rectangle([200, 109, 248, 121], outline=0, fill=0)
    d.text((207, 111), "AUTO", fill=255)

def _log_lines(d, log, y_start=91, max_lines=2, max_chars=30):
    """Draw up to max_lines log entries starting at y_start."""
    for i, line in enumerate(log[:max_lines]):
        d.text((4, y_start + i * 9), str(line)[:max_chars], fill=0)

# ── Screen renderers ─────────────────────────────────────────────────────────
def _draw_explore(d, s, fr):
    """EXPLORE: corridor walk, step dots, kill ticks, 2 log lines."""
    me   = s.get("players", {}).get("SCARAB", {})
    raven = s.get("players", {}).get("RAVEN")
    area  = s.get("area", 0)
    area_name = s.get("area_name") or _ACTS[min(area, 4)]
    steps  = s.get("steps", 0)
    next_e = s.get("next_enc", 5)
    kills  = s.get("area_kills", 0)
    log    = s.get("log", [])
    hp     = me.get("hp", 0)
    max_hp = me.get("max_hp", 20)
    level  = me.get("level", 1)

    # ── Header bar ──
    d.rectangle([0, 0, W, 17], fill=0)

    # Show both HP if Raven is also in realm
    if raven and raven.get("alive", True):
        r_hp = raven.get("hp", 0)
        hdr = "R:" + str(r_hp) + " S:" + str(hp) + "  " + area_name[:14]
    else:
        hdr = "ROGUE // " + area_name[:14]
        d.text((194, 2), "HP:" + str(hp), fill=255)
    d.text((3, 2), hdr[:32], fill=255)

    # ── Dungeon corridor ──
    d.rectangle([0, 17, W, 94], fill=255)
    # Perspective walls
    d.polygon([(0, 17),  (70, 38),  (70, 76),  (0, 94)],  fill=0)
    d.polygon([(W, 17),  (180,38),  (180,76),  (W, 94)],   fill=0)
    d.polygon([(0, 17),  (70, 38),  (180,38),  (W, 17)],   fill=0)
    # Floor seam
    d.line([(70, 76), (180, 76)], fill=0, width=1)
    # Floor tiles
    for tx in range(70, 181, 18):
        d.line([(tx, 76), (tx, 94)], fill=0, width=1)
    # Torches
    for tx in [72, 178]:
        d.rectangle([tx-1, 40, tx+1, 47], fill=255)
        if fr % 2 == 0:
            d.polygon([(tx-3, 40), (tx+3, 40), (tx, 34)],   fill=255)
        else:
            d.polygon([(tx-2, 40), (tx+4, 40), (tx+1, 33)], fill=255)

    # Scarab sprite walking in corridor
    sx = 105 + (fr % 2) * 4
    _sp_scarab(d, sx, 48, fr)

    # If Raven alive in realm, show them too (slightly ahead)
    if raven and raven.get("alive", True):
        rx = 88 + (fr % 2) * 3
        _sp_raven(d, rx, 44, fr)

    # ── XP bar under floor ──
    xp     = me.get("xp", 0)
    xp_next = me.get("xp_next", 50)
    _bar(d, 71, 77, 108, 4, xp, xp_next)

    # ── Step progress dots (top of corridor zone) ──
    dot_y = 20
    dot_x0 = 74
    for i in range(min(next_e, 10)):
        cx = dot_x0 + i * 10
        if i < steps:
            d.ellipse([cx, dot_y, cx+5, dot_y+5], fill=0)
        else:
            d.ellipse([cx, dot_y, cx+5, dot_y+5], outline=0, fill=255)

    # ── Kill ticks (bottom-right of corridor header) ──
    tick_x = 185
    tick_y = 20
    for i in range(min(kills, 8)):
        d.line([(tick_x + i*7, tick_y), (tick_x + i*7, tick_y+6)], fill=255, width=1)
        if (i + 1) % 5 == 0:
            d.line([(tick_x + (i-4)*7, tick_y+3), (tick_x + i*7, tick_y+3)], fill=255, width=1)

    # ── Log zone ──
    d.rectangle([0, 94, W, 107], fill=255)
    d.line([(0, 94), (W, 94)], fill=0)
    _log_lines(d, log, y_start=95, max_lines=1, max_chars=34)

    # ── Footer: AUTO badge + step count ──
    d.rectangle([0, 107, W, H], fill=255)
    d.line([(0, 107), (W, 107)], fill=0)
    d.text((4, 109), "Lv" + str(level) + "  Step:" + str(steps) + "/" + str(next_e), fill=0)
    _auto_badge(d)


def _draw_combat(d, s, fr):
    """COMBAT: Scarab left vs enemy right, HP bars, 2 log lines, AUTO badge."""
    me    = s.get("players", {}).get("SCARAB", {})
    raven = s.get("players", {}).get("RAVEN")
    enemy = s.get("enemy")
    log   = s.get("log", [])
    hp    = me.get("hp", 0)
    max_hp = me.get("max_hp", 20)

    # ── Header ──
    d.rectangle([0, 0, W, 17], fill=0)
    if raven and raven.get("alive", True):
        r_hp = raven.get("hp", 0)
        d.text((3, 2), "R:" + str(r_hp) + "hp  S:" + str(hp) + "hp", fill=255)
    else:
        d.text((3, 2),
               "Lv" + str(me.get("level",1)) +
               "  K:" + str(me.get("kills",0)) +
               "  G:" + str(me.get("gold",0)),
               fill=255)
    if enemy:
        d.text((150, 2), enemy.get("name","")[:9], fill=255)

    # ── Combat arena ──
    d.rectangle([0, 17, W, 90], fill=255)
    d.line([(0, 80), (W, 80)], fill=0)
    # Floor tiles
    for tx in range(0, W, 16):
        d.rectangle([tx, 80, tx+14, 89], outline=0, fill=255)

    # ── Player side ──
    shake = 1 if fr % 4 == 1 else 0

    if raven and raven.get("alive", True):
        # Co-op: Raven top-left, Scarab below
        d.line([(115, 17), (115, 80)], fill=0)
        _sp_raven(d, 5 + shake, 22, fr)
        _sp_scarab(d, 60 + shake, 34, fr)
        d.text((4,  68), "R:" + str(raven.get("hp", 0)), fill=0)
        _bar(d, 4,  75, 50, 5, raven.get("hp", 0), raven.get("max_hp", 20))
        d.text((62, 68), "S:" + str(hp), fill=0)
        _bar(d, 62, 75, 50, 5, hp, max_hp)
    else:
        # Solo display
        d.line([(122, 17), (122, 80)], fill=0)
        d.text((109, 45), "VS", fill=0)
        _sp_scarab(d, 18 + shake, 28, fr)
        d.text((4, 68), "HP:" + str(hp), fill=0)
        _bar(d, 4, 75, 55, 7, hp, max_hp)

    # ── Enemy side ──
    if enemy:
        sp  = _SP.get(enemy.get("name", ""), _sp_shade)
        ex  = 170 + (1 if fr % 4 == 2 else 0)
        sp(d, ex, 18, fr)
        d.text((130, 68), enemy.get("name","")[:10], fill=0)
        _bar(d, 130, 75, 55, 7, enemy.get("hp", 0), enemy.get("max_hp", 1))

    # ── Log ──
    d.rectangle([0, 90, W, 107], fill=255)
    d.line([(0, 90), (W, 90)], fill=0)
    _log_lines(d, log, y_start=91, max_lines=2, max_chars=30)

    # ── Footer ──
    d.rectangle([0, 107, W, H], fill=255)
    d.line([(0, 107), (W, 107)], fill=0)
    d.text((4, 109), "COMBAT", fill=0)
    _auto_badge(d)


def _draw_boss(d, s, fr):
    """BOSS: same as combat but dramatic header + corner decorations + wide boss HP bar."""
    me    = s.get("players", {}).get("SCARAB", {})
    raven = s.get("players", {}).get("RAVEN")
    enemy = s.get("enemy")
    log   = s.get("log", [])
    hp    = me.get("hp", 0)
    max_hp = me.get("max_hp", 20)

    # ── Boss header ──
    d.rectangle([0, 0, W, 17], fill=0)
    if enemy:
        d.text((3, 2), "** BOSS: " + enemy.get("name","")[:13] + " **", fill=255)
    else:
        d.text((3, 2), "** BOSS ENCOUNTER **", fill=255)

    d.rectangle([0, 17, W, 90], fill=255)

    # Corner decorations
    d.rectangle([0,   17, 3,  90], fill=0)
    d.rectangle([247, 17, W,  90], fill=0)
    # Corner glyphs
    for cx, cy in [(4, 18), (4, 82), (243, 18), (243, 82)]:
        d.polygon([(cx, cy), (cx+4, cy), (cx+2, cy+4)], fill=0)

    shake = 2 if fr % 2 == 0 else 0

    # ── Player(s) left ──
    if raven and raven.get("alive", True):
        _sp_raven(d, 5 + shake, 24, fr)
        _sp_scarab(d, 55 + shake, 34, fr)
        d.text((4,  72), "R:" + str(raven.get("hp", 0)), fill=0)
        _bar(d, 4,  78, 45, 5, raven.get("hp", 0), raven.get("max_hp", 20))
        d.text((55, 72), "S:" + str(hp), fill=0)
        _bar(d, 55, 78, 45, 5, hp, max_hp)
    else:
        _sp_scarab(d, 10 + shake, 30, fr)
        d.text((4, 72), "HP:" + str(hp), fill=0)
        _bar(d, 4, 78, 55, 5, hp, max_hp)

    # ── Boss right side — larger ──
    if enemy:
        sp = _SP.get(enemy.get("name",""), _sp_boss)
        sp(d, 155, 16, fr)
        e_hp  = enemy.get("hp", 0)
        e_max = enemy.get("max_hp", 1)
        d.text((110, 70), enemy.get("name","")[:10] + " " + str(e_hp) + "hp", fill=0)
        _bar(d, 110, 78, 130, 7, e_hp, e_max)

    # ── Log ──
    d.rectangle([0, 90, W, 107], fill=255)
    d.line([(0, 90), (W, 90)], fill=0)
    _log_lines(d, log, y_start=91, max_lines=2, max_chars=30)

    # ── Footer ──
    d.rectangle([0, 107, W, H], fill=255)
    d.line([(0, 107), (W, 107)], fill=0)
    d.text((4, 109), "BOSS FIGHT", fill=0)
    _auto_badge(d)


def _draw_loot(d, s, fr):
    """LOOT / LEVELUP: stats screen with XP bar."""
    me   = s.get("players", {}).get("SCARAB", {})
    log  = s.get("log", [])
    sub  = s.get("sub", "LOOT")
    hp    = me.get("hp", 0)
    max_hp = me.get("max_hp", 20)
    xp    = me.get("xp", 0)
    xp_next = me.get("xp_next", 50)
    level = me.get("level", 1)
    gold  = me.get("gold", 0)
    kills = me.get("kills", 0)

    d.rectangle([0, 0, W, H], fill=255)
    d.rectangle([0, 0, W, 17], fill=0)

    if sub == "LEVELUP":
        d.text((40, 2), "** LEVEL UP! **", fill=255)
        # Stars
        for sx, sy in [(10,25),(235,25),(125,22),(45,60),(205,60)]:
            d.text((sx, sy), "*", fill=0)
    else:
        d.text((3, 2),
               "ENEMY SLAIN  Lv" + str(level) + "  K:" + str(kills),
               fill=255)

    # Treasure chest
    d.rectangle([100, 26, 150, 58], outline=0, fill=255)
    d.rectangle([100, 26, 150, 38], fill=0)
    d.rectangle([120, 34, 130, 44], fill=255)
    d.ellipse([114, 34, 136, 48], outline=0, fill=255)

    # Stats
    d.text((10, 65), "HP:" + str(hp) + "/" + str(max_hp) +
           "  XP:" + str(xp) + "/" + str(xp_next), fill=0)
    _bar(d, 10, 75, 230, 5, xp, xp_next)
    d.text((10, 83), "Gold: " + str(gold), fill=0)

    # Log lines
    _log_lines(d, log, y_start=93, max_lines=2, max_chars=34)

    # Footer
    d.rectangle([0, 107, W, H], fill=255)
    d.line([(0, 107), (W, 107)], fill=0)
    d.text((4, 109), "LOOTING...", fill=0)
    _auto_badge(d)


def _draw_dead(d, s, fr):
    """DEAD: tombstone with 'FALLEN -- RISING' message."""
    me   = s.get("players", {}).get("SCARAB", {})
    raven = s.get("players", {}).get("RAVEN")
    area  = s.get("area", 0)
    area_name = s.get("area_name") or _ACTS[min(area, 4)]
    level = me.get("level", 1)
    kills = me.get("kills", 0)
    gold  = me.get("gold", 0)
    both_dead = raven and not raven.get("alive", True) and not me.get("alive", True)

    d.rectangle([0, 0, W, H], fill=255)
    d.rectangle([0, 0, W, 17], fill=0)

    label = "PARTY WIPED!" if both_dead else "FALLEN IN THE WASTES"
    d.text((30, 2), label, fill=255)

    # Tombstone
    d.rectangle([102, 30, 148, 80], fill=0)
    d.ellipse([102, 22, 148, 52], fill=0)
    d.text((108, 36), "R.I.P.",      fill=255)
    d.text((108, 50), "SCARAB",      fill=255)
    d.text((108, 62), "Lv" + str(level), fill=255)

    # Stats
    d.text((5, 84), "Level " + str(level) + "  Kills: " + str(kills), fill=0)
    d.text((5, 93), "Gold: " + str(gold) + "  " + area_name[:16], fill=0)

    # Pulsing revival message
    msg = "FALLEN -- RISING" if fr % 2 == 0 else "FALLEN . . . . ."
    d.text((5, 103), msg, fill=0)

    # Footer
    d.rectangle([0, 107, W, H], fill=255)
    d.line([(0, 107), (W, 107)], fill=0)
    d.text((4, 109), "AUTO-REVIVE", fill=0)
    _auto_badge(d)


def _draw_offline(d, fr):
    """Shown when Duat is unreachable / quest state unavailable."""
    d.rectangle([0, 0, W, H], fill=255)
    d.rectangle([0, 0, W, 17], fill=0)
    d.text((3, 2), "ROGUE // DUNGEON", fill=255)

    # Draw scarab with question mark
    _sp_scarab(d, W//2 - 14, 30, fr)

    d.line([(10, 68), (W-10, 68)], fill=0, width=1)
    d.text((W//2 - 38, 74), "DUAT UNREACHABLE", fill=0)
    msg = "RETRYING..." if fr % 2 == 0 else "RETRYING . ."
    d.text((W//2 - 24, 86), msg, fill=0)

    d.rectangle([0, 107, W, H], fill=255)
    d.line([(0, 107), (W, 107)], fill=0)
    d.text((4, 109), "QUEST OFFLINE", fill=0)
    _auto_badge(d)


# ── Main dispatch ─────────────────────────────────────────────────────────────
def draw_scarab_dungeon(d):
    """
    Main entry point. Call this each render tick when in DUNGEON mode.
    Polls /quest/state, updates canvas via ImageDraw object d.
    Returns True if state was successfully fetched, False if Duat unreachable.
    """
    global _anim_fr
    _anim_fr = (_anim_fr + 1) % 8

    s = get_state()
    fr = _anim_fr

    # Clear canvas
    d.rectangle([0, 0, W, H], fill=255)

    if s is None:
        _draw_offline(d, fr)
        return False

    sub = s.get("sub", "EXPLORE")

    if sub == "EXPLORE":
        _draw_explore(d, s, fr)
    elif sub == "COMBAT":
        _draw_combat(d, s, fr)
    elif sub == "BOSS":
        _draw_boss(d, s, fr)
    elif sub in ("LOOT", "LEVELUP"):
        _draw_loot(d, s, fr)
    elif sub == "DEAD":
        _draw_dead(d, s, fr)
    else:
        # Fallback: IDLE / RETREAT / unknown — show explore
        _draw_explore(d, s, fr)

    return True


if __name__ == "__main__":
    # Quick syntax / import smoke test
    from PIL import Image, ImageDraw
    img  = Image.new("1", (W, H), 255)
    d    = ImageDraw.Draw(img)
    result = draw_scarab_dungeon(d)
    print("draw_scarab_dungeon OK (got state:", result, ")")
    print("scarab_quest import: ok")
