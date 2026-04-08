#!/usr/bin/env python3
"""
duat_unlock.py - Raven OS
Receives lock/unlock decisions, executes via Horus SSH.
"""

import os, json, time, subprocess, threading, logging
from datetime import datetime
from pathlib import Path
from flask import Flask, request, jsonify, send_file

BASE_DIR = Path.home() / ".raven"
LOG_FILE = BASE_DIR / "duat_unlock.log"
BASE_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()])
log = logging.getLogger("duat-unlock")

def load_config():
    cfg = {}
    cf = BASE_DIR / "config"
    if cf.exists():
        for line in cf.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k,v = line.split("=",1)
                cfg[k.strip()] = v.strip()
    return cfg

cfg = load_config()
WINDOWS_IP   = cfg.get("WINDOWS_IP","")
WINDOWS_USER = cfg.get("WINDOWS_USER","Horus")
SSH_KEY      = cfg.get("SSH_KEY", str(Path.home()/".ssh/duat_horus_key"))
RAVEN_IP     = cfg.get("RAVEN_IP","")
RAVEN_PORT   = cfg.get("RAVEN_PORT","6175")
PORT         = 6176

state = {"pending": [], "history": []}

def ssh_lock(filepath):
    if not WINDOWS_IP: return False, "WINDOWS_IP not set"
    win = filepath.replace("/","\\")
    cmd = ["ssh","-i",SSH_KEY,"-o","StrictHostKeyChecking=no",
           "-o","BatchMode=yes","-o","ConnectTimeout=10",
           f"{WINDOWS_USER}@{WINDOWS_IP}",
           f'icacls "{win}" /deny {WINDOWS_USER}:RX /T']
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return r.returncode==0, r.stderr.strip() or "locked"
    except Exception as e:
        return False, str(e)

def ssh_unlock(filepath):
    if not WINDOWS_IP: return False, "WINDOWS_IP not set"
    win = filepath.replace("/","\\")
    cmd = ["ssh","-i",SSH_KEY,"-o","StrictHostKeyChecking=no",
           "-o","BatchMode=yes","-o","ConnectTimeout=10",
           f"{WINDOWS_USER}@{WINDOWS_IP}",
           f'icacls "{win}" /grant {WINDOWS_USER}:F /T']
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return r.returncode==0, r.stderr.strip() or "unlocked"
    except Exception as e:
        return False, str(e)

def forward_to_raven(alert):
    if not RAVEN_IP: return
    try:
        import urllib.request as ur
        body = json.dumps(alert).encode()
        req = ur.Request(f"http://{RAVEN_IP}:{RAVEN_PORT}/alert",
            data=body, headers={"Content-Type":"application/json"})
        ur.urlopen(req, timeout=8)
        log.info(f"Alert forwarded to Raven: {alert['filename']}")
    except Exception as e:
        log.warning(f"Could not reach Raven: {e}")

app = Flask(__name__)

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status":"ok","device":"duat",
                    "windows_ip":WINDOWS_IP,"raven_ip":RAVEN_IP,
                    "pending":len(state["pending"])})

@app.route("/lock", methods=["POST"])
def lock_file():
    data = request.json
    if not data or "filepath" not in data:
        return jsonify({"error":"missing filepath"}), 400
    filepath = data["filepath"]
    filename = data.get("filename", Path(filepath).name)
    alert = {"filename":filename,"filepath":filepath,
             "verdict":data.get("verdict","unknown"),
             "threat_name":data.get("threat_name",""),
             "hash":data.get("hash",""),
             "locked_at":datetime.now().isoformat(),"status":"pending"}
    ok, msg = ssh_lock(filepath)
    alert["lock_status"] = "locked" if ok else f"failed:{msg}"
    state["pending"].append(alert)
    log.info(f"Lock {'ok' if ok else 'failed'}: {filename}")
    threading.Thread(target=forward_to_raven, args=(alert,), daemon=True).start()
    return jsonify({"status":"locked" if ok else "lock_failed","forwarded_to_raven":bool(RAVEN_IP)})

@app.route("/unlock_decision", methods=["POST"])
def unlock_decision():
    data = request.json or {}
    action     = data.get("action")
    filename   = data.get("filename","")
    filepath   = data.get("filepath","")
    decided_by = data.get("decided_by","unknown")
    if action not in ("unlock","deny"):
        return jsonify({"error":"action must be unlock or deny"}), 400
    log.info(f"Decision: {action} for {filename} by {decided_by}")
    result = {"filename":filename,"action":action,
              "decided_by":decided_by,"timestamp":datetime.now().isoformat()}
    if action == "unlock" and filepath:
        ok, msg = ssh_unlock(filepath)
        result["success"] = ok
        result["ssh_result"] = msg
    else:
        result["success"] = True
        result["ssh_result"] = "denied"
    state["pending"] = [a for a in state["pending"] if a["filename"] != filename]
    state["history"].append(result)
    if len(state["history"]) > 100:
        state["history"] = state["history"][-100:]
    return jsonify(result)

@app.route("/pending", methods=["GET"])
def pending():
    return jsonify({"pending":state["pending"],"count":len(state["pending"])})

@app.route("/history", methods=["GET"])
def history():
    return jsonify({"history":state["history"][-20:]})


# ── Quest Companion ────────────────────────────────────────────────────────────
import random as _qr

_ACTS = ["DUAT WASTES","SANDS OF SET","THOTH TEMPLES","HALL OF RA","VOID OF ISFET"]
_ACT_SHORT = ["DUAT","SET","THOTH","RA","ISFET"]

_QMOBS = [
    [("SHADE",12,2,8,3),("JACKAL",10,4,12,4),("MUMMY",18,3,15,5)],
    [("SCORPION",16,5,20,7),("WRAITH",14,6,22,8),("SET SLAVE",20,5,18,6)],
    [("GUARDIAN",25,6,30,10),("SCRIBE",18,8,28,9),("T.GHOST",20,7,25,8)],
    [("SUN WARR",28,9,40,14),("FLAME DJN",24,10,38,13),("SOL.HAWK",22,11,35,12)],
    [("VOID SHDE",35,12,55,20),("ENDLESS",30,14,50,18),("C.SPAWN",32,13,52,19)],
]
_BOSSES = [
    {"name":"AMMIT",  "hp":80, "atk":12,"xp":150,"gold":40, "special":"devour"},
    {"name":"SET",    "hp":120,"atk":15,"xp":250,"gold":65, "special":"chaos"},
    {"name":"APEP",   "hp":160,"atk":18,"xp":380,"gold":90, "special":"coil"},
    {"name":"SEKHMET","hp":200,"atk":22,"xp":520,"gold":120,"special":"plague"},
    {"name":"ISFET",  "hp":999,"atk":25,"xp":0,  "gold":200,"special":"endless"},
]
_CLASS_BONUS = {
    "SCOUT":   {"hp":0,  "atk":0,  "crit":0.20},
    "ROGUE":   {"hp":-5, "atk":1,  "crit":0.18},
    "HOST":    {"hp":5,  "atk":-1, "crit":0.10},
    "WARRIOR": {"hp":10, "atk":2,  "crit":0.10},
    "GUARDIAN":{"hp":15, "atk":0,  "crit":0.12},
}

_qg = {
    "players":     {},
    "sub":         "IDLE",
    "area":        0, "steps": 0, "next_enc": 5,
    "enemy":       None,
    "log":         ["Quest awaits.", "Heroes needed."],
    "anim":        0,
    "act_kills":   0,
    "boss_pending":False,
    "isfet_power": 0,  # increases with each Isfet retreat
}

def _q_new_p(name, char=None):
    cls   = (char or {}).get("cls", "SCOUT")
    bonus = _CLASS_BONUS.get(cls, _CLASS_BONUS["SCOUT"])
    base_hp  = 25 + bonus["hp"]
    base_atk = 5  + bonus["atk"]
    p = {
        "name":    name, "cls":    cls,
        "hp":      base_hp, "max_hp": base_hp,
        "level":   1, "xp": 0, "xp_next": 50,
        "gold":    0, "atk": base_atk, "kills": 0,
        "alive":   True, "fleeing": False, "coiled": False,
    }
    if char:
        # Restore character stats from client (realm sync)
        for k in ("level","xp","xp_next","gold","atk","kills","max_hp"):
            if k in char:
                p[k] = char[k]
        p["hp"] = min(char.get("hp", p["max_hp"]), p["max_hp"])
    return p

def _q_avg_lvl():
    ps = list(_qg["players"].values())
    return sum(p["level"] for p in ps) / max(len(ps), 1)

def _q_spawn_mob(boss=False):
    idx = min(_qg["area"], 4)
    n   = len(_qg["players"])
    sc  = (1 + (_q_avg_lvl()-1) * 0.2) * (1 + (n-1) * 0.5)
    if boss:
        b           = dict(_BOSSES[idx])
        extra_power = _qg["isfet_power"] if idx == 4 else 0
        base_hp     = max(1, int(b["hp"] * sc)) + extra_power * 10
        base_atk    = max(1, int(b["atk"] * (1 + (_q_avg_lvl()-1) * 0.15))) + extra_power
        _qg["enemy"] = {
            "name":     b["name"],
            "hp":       base_hp,
            "max_hp":   base_hp,
            "atk":      base_atk,
            "_base_atk":base_atk,
            "xp":       b["xp"],
            "gold":     _qr.randint(b["gold"], b["gold"] * 2),
            "special":  b["special"],
            "is_boss":  True,
        }
        _qg["sub"]          = "BOSS"
        _qg["boss_pending"] = False
        _qg["log"]          = [b["name"] + " RISES!", "Face your fate!"]
    else:
        nm, bhp, batk, xp, gld = _qr.choice(_QMOBS[idx])
        _qg["enemy"] = {
            "name":    nm,
            "hp":      max(1, int(bhp * sc)),
            "max_hp":  max(1, int(bhp * sc)),
            "atk":     max(1, int(batk * (1 + (_q_avg_lvl()-1) * 0.2))),
            "xp":      xp,
            "gold":    _qr.randint(gld, gld * 2),
            "is_boss": False, "special": None,
        }
        _qg["sub"] = "COMBAT"
        _qg["log"] = [nm + " APPEARS!", "Together we fight!"]

_Q_FLAVOR = [
    "Sand shifts beneath you.", "A torch flickers.",
    "Ancient whispers...",      "The sands breathe.",
    "Shadows stir ahead.",      "Hot wind from below.",
    "Hieroglyphs glow faintly.","Jackals howl in the dark.",
    "The air smells of resin.", "Canopic jars rattle.",
]

def _q_move(name):
    p = _qg["players"][name]
    _qg["steps"] += 1
    _qg["anim"]   = (_qg["anim"] + 1) % 4
    # Rare healing shrine
    if _qr.random() < 0.07:
        h = _qr.randint(8, 15)
        p["hp"] = min(p["max_hp"], p["hp"] + h)
        _qg["log"] = [name + " found a shrine! +" + str(h) + "HP", ""]
        return
    # Boss trigger: act_kills >= 8 and 25% chance
    if _qg["act_kills"] >= 8 and _qr.random() < 0.25:
        _q_spawn_mob(boss=True)
        return
    if _qg["steps"] >= _qg["next_enc"]:
        _q_spawn_mob(boss=False)
    else:
        _qg["log"] = [_qr.choice(_Q_FLAVOR),
                      "Step " + str(_qg["steps"]) + "/" + str(_qg["next_enc"])]

def _q_attack(name):
    e   = _qg["enemy"]
    p   = _qg["players"][name]
    log = []

    # HOST class: heal lowest-HP ally 2hp per round
    if p.get("cls") == "HOST" and len(_qg["players"]) > 1:
        alive = [pp for pp in _qg["players"].values() if pp["alive"]]
        if alive:
            target_heal = min(alive, key=lambda pp: pp["hp"])
            target_heal["hp"] = min(target_heal["max_hp"], target_heal["hp"] + 2)

    # Coiled: skip attack
    if p.get("coiled"):
        p["coiled"] = False
        log.append(name + " is coiled! Skipped.")
    else:
        cls     = p.get("cls", "SCOUT")
        crit_ch = _CLASS_BONUS.get(cls, _CLASS_BONUS["SCOUT"])["crit"]
        dmg     = max(1, p["atk"] + _qr.randint(-2, 3))
        crit    = _qr.random() < crit_ch
        if crit:
            dmg = int(dmg * 2.2)
        e["hp"] -= dmg
        log.append(("CRIT! " if crit else "") +
                   name[:6] + " hit " + str(dmg) +
                   " [" + str(e["hp"]) + "/" + str(e["max_hp"]) + "]")

    if e["hp"] <= 0:
        special = e.get("special")

        # Isfet endless: retreat instead of dying
        if special == "endless":
            _qg["isfet_power"] += 1
            _qg["sub"]          = "RETREAT"
            _qg["enemy"]        = None
            _qg["log"]          = ["ISFET RETREATS!", "Void pushed back..."]
            return

        xpg = e["xp"]
        gg  = e["gold"]
        n   = max(1, len([pp for pp in _qg["players"].values() if pp["alive"]]))
        for pp in _qg["players"].values():
            if pp["alive"]:
                pp["xp"]    += xpg
                pp["gold"]  += _qr.randint(max(1, gg // n), max(1, gg))
                pp["kills"] += 1
                # Level up
                while pp["xp"] >= pp["xp_next"]:
                    pp["level"]   += 1
                    pp["xp"]      -= pp["xp_next"]
                    pp["xp_next"]  = int(pp["xp_next"] * 1.6)
                    pp["max_hp"]  += 10
                    pp["hp"]       = pp["max_hp"]
                    pp["atk"]     += 2
        _qg["act_kills"] += 1
        # Area progression: every 5 avg levels
        new_a = min(int((_q_avg_lvl()-1) // 5), 4)
        if new_a > _qg["area"]:
            _qg["area"]      = new_a
            _qg["act_kills"] = 0
            log.append("Entering " + _ACTS[new_a] + "!")
        _qg["enemy"]    = None
        _qg["sub"]      = "LOOT"
        _qg["next_enc"] = _qr.randint(3, 8)
        _qg["steps"]    = 0
        log.append(e["name"] + " SLAIN! Loot shared!")
        _qg["log"] = log
        return

    # Enemy counterattack
    special = e.get("special")

    # Chaos: 20% chance enemy gains +3 atk (max +9 above base)
    if special == "chaos" and _qr.random() < 0.20:
        base = e.get("_base_atk", e["atk"])
        e["atk"] = min(e["atk"] + 3, base + 9)
        log.append("SET empowered! atk=" + str(e["atk"]))

    alive_players = [pn for pn, pp in _qg["players"].items() if pp["alive"]]
    if not alive_players:
        _qg["sub"] = "DEAD"
        _qg["log"] = ["All heroes fallen.", "PARTY WIPED!"]
        return

    # Plague: hits ALL alive players
    targets = alive_players if special == "plague" else [_qr.choice(alive_players)]

    for target_name in targets:
        tp = _qg["players"][target_name]
        ed = max(1, e["atk"] + _qr.randint(-1, 2))

        # Devour: if damage >= 50% target max_hp, stun target next turn
        if special == "devour" and ed >= tp["max_hp"] * 0.5:
            tp["coiled"] = True
            log.append("DEVOURED! " + target_name[:6] + " stunned!")

        # Coil: 25% chance target is coiled next turn
        if special == "coil" and _qr.random() < 0.25:
            tp["coiled"] = True
            log.append(target_name[:6] + " COILED!")

        tp["hp"] -= ed
        log.append(e["name"][:8] + " hits " + target_name[:6] +
                   " " + str(ed) +
                   " [" + str(tp["hp"]) + "/" + str(tp["max_hp"]) + "]")
        if tp["hp"] <= 0:
            tp["hp"]    = 0
            tp["alive"] = False
            log.append(target_name + " has fallen!")
            if all(not pp["alive"] for pp in _qg["players"].values()):
                _qg["sub"] = "DEAD"
                log.append("PARTY WIPED!")

    _qg["log"] = log

def _q_flee(name):
    e = _qg["enemy"]
    p = _qg["players"][name]
    if _qr.random() < 0.55:
        cost = max(1, e["atk"] // 2)
        p["hp"] -= cost
        p["fleeing"] = True
        fighting = [pn for pn, pp in _qg["players"].items()
                    if pp["alive"] and not pp.get("fleeing")]
        if not fighting:
            for pp in _qg["players"].values():
                pp["fleeing"] = False
            _qg["enemy"] = None
            _qg["sub"]   = "EXPLORE"
            _qg["log"]   = ["Party retreated!", "Regrouping..."]
        else:
            _qg["log"] = [name + " fled! (-" + str(cost) + "hp)",
                          "Others still fight!"]
        if p["hp"] <= 0:
            p["hp"]    = 0
            p["alive"] = False
            _qg["log"].append(name + " fell fleeing!")
            if all(not pp["alive"] for pp in _qg["players"].values()):
                _qg["sub"] = "DEAD"
                _qg["log"].append("PARTY WIPED!")
    else:
        ed = max(1, e["atk"] + _qr.randint(0, 3))
        p["hp"] -= ed
        _qg["log"] = [name + " can't flee!",
                      e["name"][:8] + " hits " + str(ed)]
        if p["hp"] <= 0:
            p["hp"]    = 0
            p["alive"] = False
            _qg["log"].append(name + " has fallen!")
            if all(not pp["alive"] for pp in _qg["players"].values()):
                _qg["sub"] = "DEAD"
                _qg["log"].append("PARTY WIPED!")


@app.route("/quest/state", methods=["GET"])
def quest_state():
    return jsonify(_qg)

@app.route("/quest/join", methods=["POST"])
def quest_join():
    data = request.get_json(force=True) or {}
    name = data.get("player", "UNKNOWN")
    char = data.get("character")
    if name not in _qg["players"]:
        _qg["players"][name] = _q_new_p(name, char)
        if _qg["sub"] == "IDLE":
            _qg.update({
                "sub":      "EXPLORE", "area":     0,
                "steps":    0,         "next_enc":  _qr.randint(4,7),
                "act_kills":0,         "boss_pending":False,
                "enemy":    None,
            })
            _qg["log"] = [name + " enters the quest!", _ACTS[0] + " awaits..."]
        else:
            prev = _qg["log"][0] if _qg["log"] else ""
            _qg["log"] = [name + " joins the realm!", prev]
    elif char:
        # Update stats for returning player
        p = _qg["players"][name]
        for k in ("level","xp","xp_next","gold","atk","kills","max_hp","cls"):
            if k in char:
                p[k] = char[k]
        p["hp"] = min(char.get("hp", p["max_hp"]), p["max_hp"])
    return jsonify({"status": "joined", "state": _qg})

@app.route("/quest/leave", methods=["POST"])
def quest_leave():
    data = request.get_json(force=True) or {}
    name = data.get("player", "UNKNOWN")
    _qg["players"].pop(name, None)
    if not _qg["players"]:
        _qg["sub"] = "IDLE"
        _qg["log"] = ["All heroes gone.", "The realm sleeps..."]
    else:
        _qg["log"] = [name + " left the realm.", ""]
    return jsonify({"status": "left", "state": _qg})

@app.route("/quest/action", methods=["POST"])
def quest_action():
    data   = request.get_json(force=True) or {}
    name   = data.get("player", "UNKNOWN")
    action = data.get("action", "")
    if name not in _qg["players"]:
        return jsonify({"error": "not in quest"}), 400
    _qg["anim"] = (_qg["anim"] + 1) % 4
    sub = _qg["sub"]
    if   action == "move"   and sub == "EXPLORE":         _q_move(name)
    elif action == "attack" and sub in ("COMBAT","BOSS"):  _q_attack(name)
    elif action == "flee"   and sub in ("COMBAT","BOSS"):  _q_flee(name)
    elif action == "continue":
        if sub in ("LOOT","DEAD","RETREAT"):
            if sub == "DEAD":
                for pp in _qg["players"].values():
                    cls   = pp.get("cls","SCOUT")
                    bonus = _CLASS_BONUS.get(cls, _CLASS_BONUS["SCOUT"])
                    pp["hp"]  = 25 + bonus["hp"]
                    pp["max_hp"] = 25 + bonus["hp"]
                    pp["alive"]  = True
                    pp["fleeing"]= False
                    pp["coiled"] = False
            _qg["sub"]         = "EXPLORE"
            _qg["steps"]       = 0
            _qg["next_enc"]    = _qr.randint(4, 7)
            _qg["boss_pending"]= False
            _qg["log"]         = ["Back to the wastes!", ""]
    return jsonify({"status": "ok", "state": _qg})


# ── Backward-compat dungeon aliases ───────────────────────────────────────────
@app.route("/dungeon/state", methods=["GET"])
def dungeon_state_compat():
    return quest_state()

@app.route("/dungeon/join", methods=["POST"])
def dungeon_join_compat():
    return quest_join()

@app.route("/dungeon/leave", methods=["POST"])
def dungeon_leave_compat():
    return quest_leave()

@app.route("/dungeon/action", methods=["POST"])
def dungeon_action_compat():
    return quest_action()


@app.route("/quest", methods=["GET"])
def quest_viewer():
    import os
    html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "quest.html")
    return send_file(html_path, mimetype="text/html")

if __name__ == "__main__":
    log.info(f"Duat Unlock Service | Windows: {WINDOWS_IP} | Raven: {RAVEN_IP} | Port: {PORT}")
    ssl_ctx = ("/home/duat/duat/certs/duat.crt", "/home/duat/duat/certs/duat.key")
    app.run(host="0.0.0.0", port=PORT, debug=False, ssl_context=ssl_ctx)
