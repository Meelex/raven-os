#!/usr/bin/env python3
"""
griffin_companion.py -- Raven OS
Egyptian dungeon crawler for the Waveshare 2.13" e-ink display.
250x122 pixels, 1-bit (0=black, 255=white), landscape.

Solo mode:  state is local.
Realm mode: state lives on Duat (/quest/*). Griffin joins when tunneled.
            Raven renders both characters. Either can act.

Integration API (same as dungeon_game.py):
    from griffin_companion import dg, dg_reset, draw_game, handle_left, handle_right, auto_tick
"""
import random as _rng
import time   as _time
import json, ssl
from urllib.request import urlopen, Request
from urllib.error   import URLError

# ── Config ────────────────────────────────────────────────────────────────────
DUAT_IP          = "192.168.1.5"
DUAT_UNLOCK_PORT = 6176
PLAYER_NAME  = "GRIFFIN"
PARTNER_NAME = "RAVEN"
PLAYER_CLASS = "GUARDIAN"

# ── Acts & enemies ────────────────────────────────────────────────────────────
_ACTS = [
    "DUAT WASTES",
    "SANDS OF SET",
    "THOTH TEMPLES",
    "HALL OF RA",
    "VOID OF ISFET",
]
_ACT_ROMAN = ["I", "II", "III", "IV", "V"]

# (name, base_hp, base_atk, xp, gold)
_ENEMIES = [
    [("SHADE",  12,2,8,3),  ("JACKAL",  10,4,12,4), ("MUMMY",    18,3,15,5)],
    [("SCORPION",16,5,20,7),("WRAITH",  14,6,22,8), ("SET SLAVE",20,5,18,6)],
    [("GUARDIAN",25,6,30,10),("SCRIBE", 18,8,28,9), ("T.GHOST",  20,7,25,8)],
    [("SUN WARR",28,9,40,14),("FLAME DJN",24,10,38,13),("SOL.HAWK",22,11,35,12)],
    [("VOID SHDE",35,12,55,20),("ENDLESS",30,14,50,18),("C.SPAWN",32,13,52,19)],
]

_BOSSES = [
    {"name":"AMMIT",  "hp":80,  "atk":12,"xp":150,"gold":40, "special":"devour",
     "flavor":"Scales ready to weigh!"},
    {"name":"SET",    "hp":120, "atk":15,"xp":250,"gold":65, "special":"chaos",
     "flavor":"Chaos takes hold!"},
    {"name":"APEP",   "hp":160, "atk":18,"xp":380,"gold":90, "special":"coil",
     "flavor":"Serpent coils around you!"},
    {"name":"SEKHMET","hp":200, "atk":22,"xp":520,"gold":120,"special":"plague",
     "flavor":"Plague upon all!"},
    {"name":"ISFET",  "hp":999, "atk":25,"xp":0,  "gold":200,"special":"endless",
     "flavor":"The Void stirs..."},
]

# ── Character classes ─────────────────────────────────────────────────────────
_CLASS_BONUS = {
    "SCOUT":    {"hp":0,   "atk":0,  "crit":0.20},
    "ROGUE":    {"hp":-5,  "atk":1,  "crit":0.18},
    "HOST":     {"hp":5,   "atk":-1, "crit":0.10},
    "WARRIOR":  {"hp":10,  "atk":2,  "crit":0.10},
    "GUARDIAN": {"hp":15,  "atk":0,  "crit":0.12},
    "GRIFFIN":  {"hp":12,  "atk":1,  "crit":0.16},  # balanced — tough but sharp-eyed
}

_EXPLORE_FLAVOR = [
    "Sand shifts beneath you.",
    "A torch flickers.",
    "Ancient whispers...",
    "The sands breathe.",
    "Shadows stir ahead.",
    "Hot wind from below.",
    "Something watches you.",
    "Hieroglyphs glow faintly.",
    "Jackals howl in the dark.",
    "The air smells of resin.",
    "A distant bell tolls.",
    "Canopic jars rattle.",
    "Wings echo in the dark.",
    "The griffin's eye gleams.",
]

# ── Game state ────────────────────────────────────────────────────────────────
dg = {
    "player": {
        "name":    PLAYER_NAME,
        "cls":     PLAYER_CLASS,
        "hp":      25, "max_hp": 25,
        "level":   1,  "xp": 0, "xp_next": 50,
        "gold":    0,  "atk": 5, "kills": 0,
        "alive":   True,
        "coiled":  False,
    },
    "sub":         "EXPLORE",
    "area":        0,
    "steps":       0,
    "next_enc":    5,
    "anim":        0,
    "auto_t":      0.0,
    "log":         ["Griffin enters the Wastes.", "Tap MOVE or wait..."],
    "enemy":       None,
    "loot":        "",
    "lvlup_msg":   "",
    "realm":       False,
    "scarab":      None,   # co-op partner state (keyed as "scarab" for compat)
    "boss_pending":False,
    "area_kills":  0,
}

# ── Network helpers ───────────────────────────────────────────────────────────
def _ssl_ctx():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

def _base():
    return "https://" + DUAT_IP + ":" + str(DUAT_UNLOCK_PORT)

def _get(path, timeout=4):
    try:
        with urlopen(Request(_base() + path), timeout=timeout, context=_ssl_ctx()) as r:
            return json.loads(r.read())
    except Exception:
        return None

def _post(path, data, timeout=5):
    try:
        body = json.dumps(data).encode()
        req  = Request(_base() + path, data=body,
                       headers={"Content-Type": "application/json"})
        with urlopen(req, timeout=timeout, context=_ssl_ctx()) as r:
            return json.loads(r.read())
    except Exception:
        return None

# ── Realm sync ────────────────────────────────────────────────────────────────
def _sync_from_duat(remote):
    """Pull full shared state from Duat into local dg."""
    if not remote:
        return
    me = remote.get("players", {}).get(PLAYER_NAME)
    if not me:
        return

    dg["sub"]         = remote.get("sub",       dg["sub"])
    dg["area"]        = remote.get("area",       dg["area"])
    dg["steps"]       = remote.get("steps",      dg["steps"])
    dg["next_enc"]    = remote.get("next_enc",   dg["next_enc"])
    dg["enemy"]       = remote.get("enemy")
    dg["log"]         = remote.get("log",        dg["log"])
    dg["anim"]        = remote.get("anim",       dg["anim"])
    dg["area_kills"]  = remote.get("act_kills",  dg["area_kills"])
    dg["boss_pending"]= remote.get("boss_pending", dg["boss_pending"])

    p = dg["player"]
    for k in ("hp", "max_hp", "level", "xp", "xp_next",
              "gold", "atk", "kills", "alive", "cls"):
        if k in me:
            p[k] = me[k]
    p["name"] = PLAYER_NAME

    # Co-op partner
    players = remote.get("players", {})
    partner = players.get(PARTNER_NAME)
    dg["scarab"] = partner   # kept as "scarab" key for internal compat
    dg["realm"]  = True

def mp_join():
    """Register with Duat, sending current character stats."""
    p = dg["player"]
    char = {
        "cls":     p["cls"],
        "hp":      p["hp"],
        "max_hp":  p["max_hp"],
        "level":   p["level"],
        "xp":      p["xp"],
        "xp_next": p["xp_next"],
        "gold":    p["gold"],
        "atk":     p["atk"],
        "kills":   p["kills"],
    }
    r = _post("/quest/join", {"player": PLAYER_NAME, "character": char})
    if r:
        _sync_from_duat(r.get("state"))

def mp_leave():
    _post("/quest/leave", {"player": PLAYER_NAME})
    dg["realm"] = False
    dg["scarab"] = None

def mp_action(action):
    r = _post("/quest/action", {"player": PLAYER_NAME, "action": action})
    if r:
        _sync_from_duat(r.get("state"))

def mp_poll():
    r = _get("/quest/state")
    _sync_from_duat(r)

# ── Solo game logic ───────────────────────────────────────────────────────────
def _new_player():
    bonus    = _CLASS_BONUS.get(PLAYER_CLASS, _CLASS_BONUS["GUARDIAN"])
    base_hp  = 25 + bonus["hp"]
    base_atk = 5  + bonus["atk"]
    return {
        "name":    PLAYER_NAME,
        "cls":     PLAYER_CLASS,
        "hp":      base_hp, "max_hp": base_hp,
        "level":   1, "xp": 0, "xp_next": 50,
        "gold":    0, "atk": base_atk, "kills": 0,
        "alive":   True,
        "coiled":  False,
    }

def dg_reset():
    dg["player"]     = _new_player()
    dg["sub"]        = "EXPLORE"
    dg["area"]       = 0
    dg["steps"]      = 0
    dg["next_enc"]   = _rng.randint(4, 7)
    dg["anim"]       = 0
    dg["auto_t"]     = 0.0
    dg["log"]        = ["NEW QUEST", "Duat Wastes awaits..."]
    dg["enemy"]      = None
    dg["loot"]       = ""
    dg["lvlup_msg"]  = ""
    dg["realm"]      = False
    dg["scarab"]     = None
    dg["boss_pending"] = False
    dg["area_kills"] = 0
    mp_join()  # always register with Duat for display + co-op

def _spawn_solo(boss=False):
    p   = dg["player"]
    idx = min(dg["area"], 4)
    sc  = 1 + (p["level"] - 1) * 0.2

    if boss:
        b = _BOSSES[idx]
        dg["enemy"] = {
            "name":    b["name"],
            "hp":      max(1, int(b["hp"] * sc)),
            "max_hp":  max(1, int(b["hp"] * sc)),
            "atk":     max(1, int(b["atk"] * sc)),
            "xp":      b["xp"],
            "gold":    _rng.randint(b["gold"], b["gold"] * 2),
            "special": b["special"],
            "is_boss": True,
        }
        dg["sub"]  = "BOSS"
        dg["log"]  = [b["name"] + " RISES!", b["flavor"]]
        dg["boss_pending"] = False
    else:
        nm, bhp, batk, xp, gld = _rng.choice(_ENEMIES[idx])
        dg["enemy"] = {
            "name":    nm,
            "hp":      max(1, int(bhp * sc)),
            "max_hp":  max(1, int(bhp * sc)),
            "atk":     max(1, int(batk * sc)),
            "xp":      xp,
            "gold":    _rng.randint(gld, gld * 2),
            "special": None,
            "is_boss": False,
        }
        dg["sub"] = "COMBAT"
        dg["log"] = [nm + " APPEARS!", "Face the enemy!"]

def _solo_move():
    p = dg["player"]
    dg["steps"] += 1
    dg["anim"]   = (dg["anim"] + 1) % 4

    if _rng.random() < 0.07:
        h = _rng.randint(8, 15)
        p["hp"] = min(p["max_hp"], p["hp"] + h)
        dg["log"] = ["Found a healing shrine!", "  Restored +" + str(h) + " HP"]
        return

    if dg["area_kills"] >= 8 and _rng.random() < 0.20:
        _spawn_solo(boss=True)
        return

    if dg["steps"] >= dg["next_enc"]:
        _spawn_solo(boss=False)
    else:
        dg["log"] = [_rng.choice(_EXPLORE_FLAVOR),
                     "  Step " + str(dg["steps"]) + "/" + str(dg["next_enc"])]

def _crit_chance():
    return _CLASS_BONUS.get(dg["player"]["cls"], _CLASS_BONUS["GUARDIAN"])["crit"]

def _solo_attack():
    p = dg["player"]
    e = dg["enemy"]
    log = []

    if p.get("coiled"):
        p["coiled"] = False
        log.append("Coiled! Attack skipped.")
    else:
        dmg  = max(1, p["atk"] + _rng.randint(-2, 3))
        crit = _rng.random() < _crit_chance()
        if crit:
            dmg = int(dmg * 2.2)
        e["hp"] -= dmg
        dg["anim"] = (dg["anim"] + 1) % 4
        log.append(("CRIT! " if crit else "") +
                   "Hit " + str(dmg) +
                   " [" + str(e["hp"]) + "/" + str(e["max_hp"]) + "]")

    if e["hp"] <= 0:
        if e.get("special") == "endless":
            dg["sub"] = "RETREAT"
            dg["log"] = ["ISFET RETREATS!", "Void pushed back..."]
            dg["enemy"] = None
            return

        xpg = e["xp"]
        gg  = e["gold"]
        p["xp"]    += xpg
        p["gold"]  += gg
        p["kills"] += 1
        dg["area_kills"] = dg.get("area_kills", 0) + 1
        dg["loot"] = "+" + str(xpg) + " XP   +" + str(gg) + " Gold"
        log.append(e["name"] + " SLAIN!")

        lvled = False
        while p["xp"] >= p["xp_next"]:
            p["level"]   += 1
            p["xp"]      -= p["xp_next"]
            p["xp_next"]  = int(p["xp_next"] * 1.6)
            p["max_hp"]  += 10
            p["hp"]       = p["max_hp"]
            p["atk"]     += 2
            dg["lvlup_msg"] = "LEVEL " + str(p["level"]) + "! +10HP +2ATK"
            new_a = min((p["level"] - 1) // 5, len(_ACTS) - 1)
            if new_a > dg["area"]:
                dg["area"]      = new_a
                dg["area_kills"] = 0
                dg["lvlup_msg"] += "\n" + _ACTS[new_a] + "!"
            lvled = True

        dg["enemy"] = None
        dg["sub"]   = "LEVELUP" if lvled else "LOOT"
        dg["log"]   = log
        return

    # Enemy counter-attack
    sp = e.get("special")
    if sp == "devour":
        if _rng.random() < 0.30:
            heal = max(1, e["atk"] // 2)
            e["hp"] = min(e["max_hp"], e["hp"] + heal)
            log.append("AMMIT devours! +" + str(heal) + "hp")
    elif sp == "chaos":
        if _rng.random() < 0.25:
            p["atk"] = max(1, p["atk"] - 1)
            log.append("SET chaos! -1 ATK")
    elif sp == "coil":
        if _rng.random() < 0.35:
            p["coiled"] = True
            log.append("APEP coils you!")
    elif sp == "plague":
        if _rng.random() < 0.20:
            p["max_hp"] = max(5, p["max_hp"] - 2)
            p["hp"]     = min(p["hp"], p["max_hp"])
            log.append("SEKHMET plague! -2 maxHP")

    edmg = max(1, e["atk"] + _rng.randint(-1, 2))
    p["hp"] -= edmg
    log.append("Enemy " + str(edmg) + " dmg  HP:" + str(p["hp"]))
    dg["log"] = log

    if p["hp"] <= 0:
        p["hp"]    = 0
        p["alive"] = False
        dg["sub"]  = "DEAD"
        dg["log"].append("FALLEN IN THE WASTES")

def _solo_flee():
    p = dg["player"]
    if _rng.random() < 0.55:
        dg["sub"]   = "EXPLORE"
        dg["enemy"] = None
        dg["log"]   = ["Fled the battle!", "Griffin escapes..."]
    else:
        e    = dg["enemy"]
        edmg = max(1, e["atk"] + _rng.randint(0, 2))
        p["hp"] -= edmg
        dg["log"] = ["Flee failed!", "Hit for " + str(edmg) + "  HP:" + str(p["hp"])]
        if p["hp"] <= 0:
            p["hp"]    = 0
            p["alive"] = False
            dg["sub"]  = "DEAD"
            dg["log"].append("FALLEN IN THE WASTES")

# ── Sprites ───────────────────────────────────────────────────────────────────
def _sp_griffin(d, x, y, fr=0):
    """Egyptian griffin ~34x38px. Falcon head, lion body, eagle wings."""
    # Lion body
    d.ellipse([x+2, y+12, x+26, y+26], fill=0)
    # Neck connecting head to body
    d.polygon([(x+16, y+8), (x+24, y+8), (x+24, y+16), (x+16, y+16)], fill=0)
    # Falcon head
    d.ellipse([x+16, y+0, x+32, y+12], fill=0)
    # Hooked beak
    d.polygon([(x+30, y+4), (x+36, y+6), (x+31, y+10)], fill=0)
    # Eye (white with dark pupil)
    d.ellipse([x+24, y+3, x+28, y+7], fill=255)
    d.ellipse([x+25, y+4, x+27, y+6], fill=0)
    # Nemes headdress cloth (stripes falling from head)
    d.line([(x+18, y+10), (x+14, y+20)], fill=0, width=2)
    d.line([(x+20, y+10), (x+16, y+22)], fill=0, width=1)
    # Eagle wings (animated — flap up/down)
    wy = y+6 if fr % 2 == 0 else y+14
    d.polygon([(x+6,  y+14), (x+0,  wy),    (x+14, y+18)], fill=0)  # left wing
    d.polygon([(x+20, y+14), (x+28, wy),    (x+14, y+18)], fill=0)  # right wing
    # Wing feather detail
    if fr % 2 == 0:
        d.line([(x+3, wy+2), (x+8, y+16)],  fill=255, width=1)
        d.line([(x+25,wy+2), (x+20,y+16)],  fill=255, width=1)
    # Front legs (lion paws)
    d.line([(x+8,  y+26), (x+6,  y+34)], fill=0, width=2)
    d.line([(x+18, y+26), (x+20, y+34)], fill=0, width=2)
    # Hind legs
    d.line([(x+5,  y+24), (x+3,  y+34)], fill=0, width=2)
    d.line([(x+22, y+24), (x+24, y+34)], fill=0, width=2)
    # Paws (front)
    d.line([(x+6,  y+34), (x+3,  y+36)], fill=0, width=1)
    d.line([(x+6,  y+34), (x+8,  y+36)], fill=0, width=1)
    d.line([(x+20, y+34), (x+18, y+36)], fill=0, width=1)
    d.line([(x+20, y+34), (x+22, y+36)], fill=0, width=1)
    # Lion tail (arcs up and back)
    d.arc([x-4, y+14, x+8, y+24], 210, 350, fill=0, width=2)
    d.polygon([(x-4, y+14), (x-7, y+11), (x-2, y+11)], fill=0)  # tail tuft

def _sp_raven(d, x, y, fr=0):
    """Raven character sprite ~34x30px (partner)."""
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

_SP = {
    "SHADE":    _sp_shade,
    "JACKAL":   _sp_jackal,
    "MUMMY":    _sp_mummy,
    "SCORPION": _sp_scorpion,
    "WRAITH":   _sp_wraith,
    "SET SLAVE":_sp_shade,
    "GUARDIAN": _sp_guardian,
    "SCRIBE":   _sp_scribe,
    "T.GHOST":  _sp_wraith,
    "SUN WARR": _sp_hawk,
    "FLAME DJN":_sp_guardian,
    "SOL.HAWK": _sp_hawk,
    "VOID SHDE":_sp_void,
    "ENDLESS":  _sp_void,
    "C.SPAWN":  _sp_void,
    "AMMIT":    _sp_ammit,
    "SET":      _sp_boss,
    "APEP":     _sp_boss,
    "SEKHMET":  _sp_boss,
    "ISFET":    _sp_isfet,
}

# ── Drawing helpers ───────────────────────────────────────────────────────────
def _bar(d, x, y, w, h, val, maxv, fill_val=0):
    d.rectangle([x, y, x+w, y+h], outline=0, fill=255)
    if maxv > 0 and val > 0:
        d.rectangle([x, y, x + int(w * max(0, val) / maxv), y+h], fill=fill_val)

def _roman(n):
    return _ACT_ROMAN[min(n, 4)]

# ── Screen renderers ──────────────────────────────────────────────────────────
def draw_explore(d):
    p  = dg["player"]
    fr = dg["anim"]
    ar = _ACTS[min(dg["area"], 4)]
    sc = dg.get("scarab")
    rm = dg["realm"]

    d.rectangle([0, 0, 250, 17], fill=0)
    hdr = "ACT " + _roman(dg["area"]) + " // " + ar
    d.text((3, 2), hdr[:28], fill=255)
    if rm and sc:
        d.text((3, 2), "G:" + str(p["hp"]) + " R:" + str(sc.get("hp","?")), fill=255)
    else:
        d.text((170, 2), "HP:" + str(p["hp"]), fill=255)

    d.rectangle([0, 17, 250, 94], fill=255)
    d.polygon([(0, 17),  (70, 38),  (70, 76),  (0, 94)],     fill=0)
    d.polygon([(250,17), (180,38),  (180,76),  (250,94)],     fill=0)
    d.polygon([(0, 17),  (70, 38),  (180,38),  (250,17)],     fill=0)
    d.line([(70, 38),  (70, 76)],  fill=255, width=1)
    d.line([(180,38),  (180,76)],  fill=255, width=1)
    d.line([(70, 76),  (180,76)],  fill=0,   width=1)
    for tx in range(70, 181, 18):
        d.line([(tx, 76), (tx, 94)], fill=0, width=1)
    for tx in [72, 178]:
        d.rectangle([tx-1, 40, tx+1, 47], fill=255)
        if fr % 2 == 0:
            d.polygon([(tx-3, 40), (tx+3, 40), (tx, 34)],   fill=255)
        else:
            d.polygon([(tx-2, 40), (tx+4, 40), (tx+1, 33)], fill=255)

    rx = 100 + (fr % 2) * 4
    _sp_griffin(d, rx, 42, fr)   # Griffin is the player
    if rm and sc and sc.get("alive", True):
        sx = 130 + (fr % 2) * 3
        _sp_raven(d, sx, 48, fr)  # Raven is the co-op partner

    if not (rm and sc):
        d.text((88, 21), ar[:14], fill=255)

    _bar(d, 71, 77, 108, 4, p["xp"], p["xp_next"])

    d.text((88, 30),
           "Step " + str(dg["steps"]) + "/" + str(dg["next_enc"]) +
           (" CO-OP" if rm else ""),
           fill=255)

    d.rectangle([0, 94, 250, 107], fill=255)
    d.line([(0, 94), (250, 94)], fill=0)
    log = dg["log"]
    d.text((4, 95), (log[0] if log else "")[:34], fill=0)

    d.rectangle([0, 107, 250, 122], fill=255)
    d.line([(0, 107), (250, 107)], fill=0)
    d.rectangle([2,   109, 85,  121], fill=0)
    d.text((14, 111), "[ MOVE ]",  fill=255)
    d.rectangle([165, 109, 248, 121], outline=0, fill=255)
    d.text((177,111), "[ BACK ]",  fill=0)

def draw_combat(d):
    p  = dg["player"]
    e  = dg["enemy"]
    fr = dg["anim"]
    sc = dg.get("scarab")
    rm = dg["realm"]

    d.rectangle([0, 0, 250, 17], fill=0)
    if rm and sc:
        d.text((3, 2),
               "G:" + str(p["hp"]) + "hp  R:" + str(sc.get("hp","?")) + "hp",
               fill=255)
    else:
        d.text((3, 2),
               "Lv" + str(p["level"]) +
               "  K:" + str(p["kills"]) +
               "  G:" + str(p["gold"]),
               fill=255)
    if e:
        d.text((140, 2), e["name"][:10], fill=255)

    d.rectangle([0, 17, 250, 90], fill=255)
    d.line([(0, 80), (250, 80)], fill=0)
    for tx in range(0, 250, 16):
        d.rectangle([tx, 80, tx+14, 89], outline=0, fill=255)

    if rm and sc:
        d.line([(115, 17), (115, 80)], fill=0)
        shake = 1 if fr % 4 == 1 else 0
        _sp_griffin(d, 5+shake, 22, fr)
        if sc.get("alive", True):
            _sp_raven(d, 68+shake, 32, fr)
        else:
            d.text((68, 50), "DOWN", fill=0)
        d.text((4,  68), "G:" + str(p["hp"]),            fill=0)
        _bar(d, 4,  75, 50, 5, p["hp"], p["max_hp"])
        shp  = sc.get("hp", 0)
        smax = sc.get("max_hp", 25)
        d.text((62, 68), "R:" + str(shp),                fill=0)
        _bar(d, 62, 75, 50, 5, shp, smax)
    else:
        d.line([(122, 17), (122, 80)], fill=0)
        d.text((111, 45), "VS", fill=0)
        shake = 1 if fr % 4 == 1 else 0
        _sp_griffin(d, 14+shake, 22, fr)
        d.text((4,  68), "HP:" + str(p["hp"]),           fill=0)
        _bar(d, 4,  75, 55, 5, p["hp"], p["max_hp"])
        if p.get("coiled"):
            d.text((4, 57), "COILED!", fill=0)

    if e:
        sp = _SP.get(e["name"], _sp_shade)
        ex = 175 + (1 if fr % 4 == 2 else 0)
        sp(d, ex, 18, fr)
        d.text((130, 68), e["name"][:10], fill=0)
        _bar(d, 130, 75, 55, 5, e["hp"], e["max_hp"])

    d.rectangle([0, 90, 250, 107], fill=255)
    d.line([(0, 90), (250, 90)], fill=0)
    log = dg["log"]
    d.text((4, 91), (log[0] if log else "")[:30], fill=0)
    if len(log) > 1:
        d.text((4, 100), log[1][:30], fill=0)

    d.rectangle([0, 107, 250, 122], fill=255)
    d.line([(0, 107), (250, 107)], fill=0)
    d.rectangle([2,   109, 110, 121], fill=0)
    d.text((20, 111), "[ ATTACK ]",  fill=255)
    d.rectangle([140, 109, 248, 121], outline=0, fill=255)
    d.text((163,111), "[ FLEE ]",    fill=0)

def draw_boss(d):
    p  = dg["player"]
    e  = dg["enemy"]
    fr = dg["anim"]
    sc = dg.get("scarab")
    rm = dg["realm"]

    d.rectangle([0, 0, 250, 17], fill=0)
    if e:
        d.text((3, 2), "** BOSS: " + e["name"][:14] + " **", fill=255)
    d.rectangle([0, 17, 250, 90], fill=255)

    d.rectangle([0, 17, 3, 90], fill=0)
    d.rectangle([247,17, 250,90], fill=0)

    shake = 2 if fr % 2 == 0 else 0

    if rm and sc:
        _sp_griffin(d, 5+shake, 20, fr)
        if sc.get("alive", True):
            _sp_raven(d, 55+shake, 28, fr)
        d.text((4,  72), "G:" + str(p["hp"]),              fill=0)
        _bar(d, 4,  78, 45, 5, p["hp"], p["max_hp"])
        shp  = sc.get("hp", 0)
        smax = sc.get("max_hp", 25)
        d.text((55, 72), "R:" + str(shp),                  fill=0)
        _bar(d, 55, 78, 45, 5, shp, smax)
    else:
        _sp_griffin(d, 8+shake, 26, fr)
        d.text((4, 72), "HP:" + str(p["hp"]),              fill=0)
        _bar(d, 4, 78, 55, 5, p["hp"], p["max_hp"])
        if p.get("coiled"):
            d.text((4, 60), "COILED!", fill=0)

    if e:
        sp = _SP.get(e["name"], _sp_boss)
        sp(d, 155, 16, fr)
        _bar(d, 130, 78, 110, 6, e["hp"], e["max_hp"])
        d.text((130, 70), e["name"][:10] + " " + str(e["hp"]) + "hp", fill=0)

    d.rectangle([0, 90, 250, 107], fill=255)
    d.line([(0, 90), (250, 90)], fill=0)
    log = dg["log"]
    d.text((4, 91),  (log[0] if log else "")[:30], fill=0)
    if len(log) > 1:
        d.text((4, 100), log[1][:30], fill=0)

    d.rectangle([0, 107, 250, 122], fill=255)
    d.line([(0, 107), (250, 107)], fill=0)
    d.rectangle([2,   109, 110, 121], fill=0)
    d.text((20, 111), "[ ATTACK ]",  fill=255)
    d.rectangle([140, 109, 248, 121], outline=0, fill=255)
    d.text((163,111), "[ FLEE ]",    fill=0)

def draw_loot(d):
    p = dg["player"]
    d.rectangle([0, 0, 250, 122], fill=255)
    d.rectangle([0, 0, 250, 17],  fill=0)
    d.text((3, 2),
           "ENEMY SLAIN  Lv" + str(p["level"]) + "  K:" + str(p["kills"]),
           fill=255)
    d.rectangle([100, 28, 150, 65], outline=0, fill=255)
    d.rectangle([100, 28, 150, 40], fill=0)
    d.rectangle([120, 35, 130, 45], fill=255)
    # Griffin motif on chest
    d.ellipse([110, 34, 122, 44], outline=0, fill=255)  # wing arc
    d.polygon([(116,34),(121,28),(126,34)], fill=0)      # beak / talon
    d.text((50, 70), (dg["loot"] or "")[:30], fill=0)
    d.text((20, 82),
           "HP:" + str(p["hp"]) + "/" + str(p["max_hp"]) +
           "  XP:" + str(p["xp"]) + "/" + str(p["xp_next"]),
           fill=0)
    if dg["realm"]:
        d.text((20, 93), "REALM: loot shared!", fill=0)
    d.rectangle([0, 107, 250, 122], fill=255)
    d.line([(0, 107), (250, 107)], fill=0)
    d.rectangle([75, 109, 175, 121], fill=0)
    d.text((90, 111), "[ CONTINUE ]", fill=255)

def draw_levelup(d):
    p = dg["player"]
    d.rectangle([0, 0, 250, 122], fill=255)
    d.rectangle([0, 0, 250, 17],  fill=0)
    d.text((40, 2), "* LEVEL UP! *", fill=255)
    d.text((10, 25),
           PLAYER_NAME + " Lv " + str(p["level"]) + " - " + p.get("cls", PLAYER_CLASS),
           fill=0)
    for sx, sy in [(20,50),(230,50),(125,40),(50,70),(200,70)]:
        d.text((sx, sy), "*", fill=0)
    parts = (dg["lvlup_msg"] or "").split("\n")
    d.text((10, 80), parts[0][:30], fill=0)
    if len(parts) > 1:
        d.text((10, 92), parts[1][:30], fill=0)
    d.text((10,103), (dg["loot"] or "")[:30], fill=0)
    d.rectangle([75, 109, 175, 121], fill=0)
    d.text((88, 111), "[ CONTINUE ]", fill=255)

def draw_retreat(d):
    d.rectangle([0, 0, 250, 122], fill=255)
    d.rectangle([0, 0, 250, 17],  fill=0)
    d.text((30, 2), "** VOID RETREATS **", fill=255)
    _sp_isfet(d, 95, 30, 0)
    d.text((10, 80), "ISFET RETREATS!", fill=0)
    d.text((10, 92), "VOID PUSHED BACK", fill=0)
    d.rectangle([0, 107, 250, 122], fill=255)
    d.line([(0, 107), (250, 107)], fill=0)
    d.rectangle([75, 109, 175, 121], fill=0)
    d.text((90, 111), "[ CONTINUE ]", fill=255)

def draw_dead(d):
    p = dg["player"]
    d.rectangle([0, 0, 250, 122], fill=255)
    d.rectangle([0, 0, 250, 17],  fill=0)
    label = "PARTY WIPED!" if (dg["realm"] and dg.get("scarab")) else "FALLEN IN THE WASTES"
    d.text((30, 2), label, fill=255)
    d.rectangle([102, 30, 148, 80], fill=0)
    d.ellipse([102, 22, 148, 52],   fill=0)
    d.text((108, 36), "R.I.P.",      fill=255)
    d.text((108, 50), PLAYER_NAME,   fill=255)
    d.text((108, 63), "Lv" + str(p["level"]), fill=255)
    d.text((5, 84),  "Level " + str(p["level"]) + "  Kills: " + str(p["kills"]), fill=0)
    d.text((5, 95),  "Gold: "  + str(p["gold"])  + "  " + _ACTS[min(dg["area"],4)], fill=0)
    d.rectangle([0, 107, 250, 122], fill=255)
    d.line([(0, 107), (250, 107)], fill=0)
    d.rectangle([75, 109, 175, 121], fill=0)
    d.text((90, 111), "[ RESTART ]", fill=255)

def draw_game(d):
    """Main dispatch - call this from griffin_deck.py each frame."""
    sub = dg["sub"]
    if   sub == "EXPLORE": draw_explore(d)
    elif sub == "COMBAT":  draw_combat(d)
    elif sub == "BOSS":    draw_boss(d)
    elif sub == "LOOT":    draw_loot(d)
    elif sub == "LEVELUP": draw_levelup(d)
    elif sub == "RETREAT": draw_retreat(d)
    elif sub == "DEAD":    draw_dead(d)
    else:                  draw_explore(d)

# ── Input handlers ────────────────────────────────────────────────────────────
def handle_left():
    sub = dg["sub"]
    if sub == "EXPLORE":
        if dg["realm"]: mp_action("move")
        else:           _solo_move()
    elif sub in ("COMBAT", "BOSS"):
        if dg["realm"]: mp_action("attack")
        else:           _solo_attack()
    elif sub in ("LOOT", "LEVELUP", "RETREAT"):
        if dg["realm"]: mp_action("continue")
        else:
            dg["sub"]        = "EXPLORE"
            dg["boss_pending"] = False
    elif sub == "DEAD":
        if dg["realm"]: mp_action("continue")
        else:           dg_reset()

def handle_right():
    sub = dg["sub"]
    if sub in ("COMBAT", "BOSS"):
        if dg["realm"]: mp_action("flee")
        else:           _solo_flee()
    elif sub in ("LOOT", "LEVELUP", "RETREAT"):
        if dg["realm"]: mp_action("continue")
        else:
            dg["sub"]        = "EXPLORE"
            dg["boss_pending"] = False
    elif sub == "DEAD":
        if dg["realm"]: mp_action("continue")
        else:           dg_reset()

def auto_tick():
    """Auto-advances every 3.5s. Returns True if action taken."""
    now = _time.time()
    if now - dg["auto_t"] < 3.5:
        return False
    dg["auto_t"] = now
    sub = dg["sub"]
    if sub == "EXPLORE":
        if dg["realm"]: mp_action("move")
        else:           _solo_move()
        return True
    elif sub in ("COMBAT", "BOSS"):
        if dg["realm"]: mp_action("attack")
        else:           _solo_attack()
        return True
    elif sub in ("LOOT", "LEVELUP", "RETREAT"):
        if dg["realm"]: mp_action("continue")
        else:
            dg["sub"]        = "EXPLORE"
            dg["boss_pending"] = False
        return True
    elif sub == "DEAD":
        if now - dg["auto_t"] >= 1.5:
            if dg["realm"]: mp_action("continue")
            else:           dg_reset()
        return True
    return False

def mp_sync_tick():
    """Poll Duat for state updates (partner may have acted). Call each render frame."""
    if dg["realm"]:
        mp_poll()
