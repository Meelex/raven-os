#!/usr/bin/env python3
"""
Ladder Slasher — Duat Server v2
Shared-world dungeon crawl with generated room maps.
Run:   python3 app.py
Access: http://192.168.12.231:5000
"""

import sqlite3, json, time, os, random, math, subprocess, threading
from flask import Flask, request, jsonify, send_file, g

app = Flask(__name__)
DB_PATH = os.path.join(os.path.dirname(__file__), 'ladder_slasher.db')

# ═══════════════════════════════════════════════════════
# DATABASE
# ═══════════════════════════════════════════════════════

def get_db():
    db = getattr(g, '_db', None)
    if db is None:
        db = g._db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
    return db

@app.teardown_appcontext
def close_db(e):
    db = getattr(g, '_db', None)
    if db: db.close()

def init_db():
    with app.app_context():
        db = get_db()
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            pin TEXT NOT NULL,
            created_at INTEGER DEFAULT (strftime('%s','now'))
        );
        CREATE TABLE IF NOT EXISTS characters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            class TEXT NOT NULL,
            hardcore INTEGER DEFAULT 0,
            alive INTEGER DEFAULT 1,
            level INTEGER DEFAULT 1,
            ladder_level INTEGER DEFAULT 1,
            floor INTEGER DEFAULT 1,
            xp INTEGER DEFAULT 0,
            xp_next INTEGER DEFAULT 100,
            gold INTEGER DEFAULT 15,
            hp INTEGER DEFAULT 0,
            max_hp INTEGER DEFAULT 0,
            mp INTEGER DEFAULT 0,
            max_mp INTEGER DEFAULT 0,
            stat_points INTEGER DEFAULT 4,
            kills INTEGER DEFAULT 0,
            deepest_floor INTEGER DEFAULT 1,
            stat_str INTEGER DEFAULT 0,
            stat_dex INTEGER DEFAULT 0,
            stat_vit INTEGER DEFAULT 0,
            stat_int INTEGER DEFAULT 0,
            equipment TEXT DEFAULT '{}',
            inventory TEXT DEFAULT '[]',
            hp_pots INTEGER DEFAULT 2,
            mp_pots INTEGER DEFAULT 1,
            created_at INTEGER DEFAULT (strftime('%s','now')),
            updated_at INTEGER DEFAULT (strftime('%s','now')),
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS ladder (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            char_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            char_name TEXT NOT NULL,
            class TEXT NOT NULL,
            hardcore INTEGER DEFAULT 0,
            level INTEGER DEFAULT 1,
            deepest_floor INTEGER DEFAULT 1,
            kills INTEGER DEFAULT 0,
            alive INTEGER DEFAULT 1,
            recorded_at INTEGER DEFAULT (strftime('%s','now')),
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            host_user_id INTEGER NOT NULL,
            host_char_id INTEGER NOT NULL,
            guest_user_id INTEGER,
            guest_char_id INTEGER,
            state TEXT DEFAULT 'waiting',
            dungeon_state TEXT DEFAULT '{}',
            created_at INTEGER DEFAULT (strftime('%s','now')),
            updated_at INTEGER DEFAULT (strftime('%s','now'))
        );
        CREATE TABLE IF NOT EXISTS chat (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            message TEXT NOT NULL,
            ts INTEGER DEFAULT (strftime('%s','now'))
        );
        CREATE TABLE IF NOT EXISTS pets (
            id TEXT PRIMARY KEY,
            device_ip TEXT NOT NULL,
            display_name TEXT NOT NULL,
            class_name TEXT NOT NULL,
            icon TEXT NOT NULL,
            claim_pin TEXT NOT NULL DEFAULT '0000',
            level INTEGER DEFAULT 1,
            xp INTEGER DEFAULT 0,
            xp_next INTEGER DEFAULT 100,
            floor INTEGER DEFAULT 1,
            hp INTEGER DEFAULT 40,
            max_hp INTEGER DEFAULT 40,
            atk INTEGER DEFAULT 7,
            def_val INTEGER DEFAULT 3,
            online INTEGER DEFAULT 0,
            last_seen INTEGER DEFAULT 0,
            offline_since INTEGER DEFAULT 0,
            pending_delivery TEXT DEFAULT '[]'
        );
        CREATE TABLE IF NOT EXISTS pet_bindings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pet_id TEXT NOT NULL,
            char_id INTEGER NOT NULL,
            bound_at INTEGER DEFAULT (strftime('%s','now')),
            last_claimed INTEGER DEFAULT 0,
            UNIQUE(pet_id, char_id),
            FOREIGN KEY(char_id) REFERENCES characters(id)
        );
        CREATE TABLE IF NOT EXISTS raids (
            id TEXT PRIMARY KEY,
            host_user_id INTEGER NOT NULL,
            boss_id TEXT NOT NULL,
            state TEXT DEFAULT 'lobby',
            roster TEXT DEFAULT '[]',
            boss_state TEXT DEFAULT '{}',
            event_log TEXT DEFAULT '[]',
            loot_rolls TEXT DEFAULT '{}',
            created_at INTEGER DEFAULT (strftime('%s','now')),
            updated_at INTEGER DEFAULT (strftime('%s','now'))
        );
        """)
        db.commit()

def row_to_dict(row):
    return dict(row) if row else None

def update_ladder(db, char):
    ex = db.execute('SELECT id FROM ladder WHERE char_id=?', (char['id'],)).fetchone()
    if ex:
        db.execute("""UPDATE ladder SET level=?,deepest_floor=?,kills=?,alive=?,
                      recorded_at=strftime('%s','now') WHERE char_id=?""",
                   (char['level'],char['deepest_floor'],char['kills'],char['alive'],char['id']))
    else:
        db.execute("""INSERT INTO ladder(user_id,char_id,username,char_name,class,hardcore,level,deepest_floor,kills,alive)
                      VALUES(?,?,?,?,?,?,?,?,?,?)""",
                   (char['user_id'],char['id'],char['username'],char['name'],char['class'],
                    char['hardcore'],char['level'],char['deepest_floor'],char['kills'],char['alive']))

# ═══════════════════════════════════════════════════════
# DUNGEON GENERATOR
# ═══════════════════════════════════════════════════════

GRID = 5

MONSTER_POOL = [
    {'name':'Restless Shade',    'icon':'👻','tier':1,'hp':18,'atk':4,'def':1,'xp':12,'gold':[1,5]},
    {'name':'Bone Servant',      'icon':'💀','tier':1,'hp':25,'atk':5,'def':2,'xp':15,'gold':[1,6]},
    {'name':'Desert Jackal',     'icon':'🐺','tier':1,'hp':12,'atk':6,'def':0,'xp':10,'gold':[0,3]},
    {'name':'Serpent of Set',    'icon':'🐍','tier':1,'hp':15,'atk':4,'def':1,'xp':8, 'gold':[0,4]},
    {'name':'Ammit Spawn',       'icon':'🦛','tier':2,'hp':28,'atk':7,'def':3,'xp':20,'gold':[2,8]},
    {'name':'Mummy Warden',      'icon':'🧟','tier':2,'hp':40,'atk':9,'def':4,'xp':28,'gold':[3,12]},
    {'name':'Scorpion Man',      'icon':'🦂','tier':2,'hp':32,'atk':11,'def':2,'xp':25,'gold':[4,14]},
    {'name':'Shadow Priest',     'icon':'🧙','tier':2,'hp':30,'atk':10,'def':3,'xp':22,'gold':[3,10]},
    {'name':'Flame of Sekhmet',  'icon':'🔥','tier':3,'hp':55,'atk':14,'def':5,'xp':40,'gold':[5,18]},
    {"name":"Sobek's Herald",    'icon':'🐊','tier':3,'hp':65,'atk':12,'def':8,'xp':45,'gold':[6,20]},
    {"name":"Devourer's Eye",    'icon':'👁','tier':3,'hp':48,'atk':16,'def':4,'xp':48,'gold':[8,22]},
    {'name':'Warden of Duat',    'icon':'⚖','tier':4,'hp':70,'atk':20,'def':8,'xp':70,'gold':[12,30]},
    {'name':'Demon of the Gate', 'icon':'😈','tier':4,'hp':90,'atk':22,'def':10,'xp':80,'gold':[15,35]},
    {'name':'Apep Serpent',      'icon':'🐉','tier':4,'hp':80,'atk':24,'def':6,'xp':88,'gold':[14,32]},
    {'name':'Fallen Aspect of Ra','icon':'☀','tier':5,'hp':140,'atk':28,'def':15,'xp':120,'gold':[25,60]},
    {'name':'Corrupted Osiris',  'icon':'💚','tier':5,'hp':160,'atk':24,'def':18,'xp':130,'gold':[28,65]},
    {'name':'The Unnamed God',   'icon':'🌑','tier':5,'hp':120,'atk':32,'def':12,'xp':140,'gold':[30,70]},
]

LOOT_POOL = [
    # ── Normal Weapons ──
    {'name':'Short Sword',  'icon':'🗡️','type':'weapon','rarity':'normal','stats':{'atk':5},'slot':'weapon'},
    {'name':'Falchion',     'icon':'🗡️','type':'weapon','rarity':'normal','stats':{'atk':7},'slot':'weapon'},
    {'name':'Cudgel',       'icon':'🪓','type':'weapon','rarity':'normal','stats':{'atk':6,'str':1},'slot':'weapon'},
    {'name':'Battle Axe',   'icon':'🪓','type':'weapon','rarity':'normal','stats':{'atk':8},'slot':'weapon'},
    {'name':'Longbow',      'icon':'🏹','type':'weapon','rarity':'normal','stats':{'atk':6,'dex':2},'slot':'weapon'},
    {'name':'Hunting Bow',  'icon':'🏹','type':'weapon','rarity':'normal','stats':{'atk':5,'dex':1},'slot':'weapon'},
    # ── Magic Weapons ──
    {'name':'Flaming Sword','icon':'🗡️','type':'weapon','rarity':'magic','stats':{'atk':10,'str':2},'slot':'weapon'},
    {'name':'Searing Blade','icon':'🗡️','type':'weapon','rarity':'magic','stats':{'atk':11,'str':2},'slot':'weapon'},
    {'name':'Phase Dagger', 'icon':'🗡️','type':'weapon','rarity':'magic','stats':{'atk':9,'dex':4},'slot':'weapon'},
    {'name':'Staff of Ruin','icon':'🪄','type':'weapon','rarity':'magic','stats':{'atk':7,'int':4},'slot':'weapon'},
    # ── Rare Weapons ──
    {'name':'Vampiric Blade','icon':'🗡️','type':'weapon','rarity':'rare','stats':{'atk':14,'vit':3},'slot':'weapon'},
    {'name':'Voidblade',     'icon':'🗡️','type':'weapon','rarity':'rare','stats':{'atk':17,'int':3},'slot':'weapon'},
    {'name':'Titan Maul',    'icon':'🪓','type':'weapon','rarity':'rare','stats':{'atk':22,'str':6},'slot':'weapon'},
    {'name':'Chaos Axe',     'icon':'🪓','type':'weapon','rarity':'rare','stats':{'atk':18,'str':4},'slot':'weapon'},
    # ── Normal Armor ──
    {'name':'Leather Armor','icon':'🦺','type':'armor','rarity':'normal','stats':{'def':5},'slot':'armor'},
    {'name':'Brigandine',   'icon':'🦺','type':'armor','rarity':'normal','stats':{'def':6},'slot':'armor'},
    {'name':'Chain Mail',   'icon':'🛡️','type':'armor','rarity':'normal','stats':{'def':8},'slot':'armor'},
    # ── Magic Armor ──
    {'name':'Plate of the Fallen','icon':'🛡️','type':'armor','rarity':'magic','stats':{'def':12,'vit':3},'slot':'armor'},
    {'name':'Shadow Leathers',    'icon':'🦺','type':'armor','rarity':'magic','stats':{'def':8,'dex':4},'slot':'armor'},
    {'name':'Woven Chainmail',    'icon':'🛡️','type':'armor','rarity':'magic','stats':{'def':10,'dex':3},'slot':'armor'},
    {'name':'Crimson Plate',      'icon':'🛡️','type':'armor','rarity':'magic','stats':{'def':13,'str':3},'slot':'armor'},
    # ── Rare Armor ──
    {'name':'Runeplate',        'icon':'🛡️','type':'armor','rarity':'rare','stats':{'def':18,'str':2,'vit':4},'slot':'armor'},
    {'name':'Ironhide Cuirass', 'icon':'🛡️','type':'armor','rarity':'rare','stats':{'def':22,'str':3,'vit':5},'slot':'armor'},
    {'name':'Shadowweave',      'icon':'🦺','type':'armor','rarity':'rare','stats':{'def':14,'dex':6,'int':3},'slot':'armor'},
    # ── Normal Rings & Amulets ──
    {'name':'Iron Band',        'icon':'💍','type':'ring',  'rarity':'normal','stats':{'str':2},'slot':'ring'},
    # ── Magic Rings & Amulets ──
    {'name':'Ring of Fury',     'icon':'💍','type':'ring',  'rarity':'magic','stats':{'str':3,'atk':2},'slot':'ring'},
    {'name':"Warder's Ring",    'icon':'💍','type':'ring',  'rarity':'magic','stats':{'def':2,'vit':3},'slot':'ring'},
    {'name':'Mana Stone',       'icon':'💎','type':'amulet','rarity':'magic','stats':{'int':4,'mp':10},'slot':'amulet'},
    {'name':'Mystic Pendant',   'icon':'📿','type':'amulet','rarity':'magic','stats':{'int':5,'mp':8},'slot':'amulet'},
    # ── Rare Rings & Amulets ──
    {'name':'Skull Ring',       'icon':'💍','type':'ring',  'rarity':'rare','stats':{'str':3,'dex':3},'slot':'ring'},
    {'name':'Band of Wrath',    'icon':'💍','type':'ring',  'rarity':'rare','stats':{'str':5,'atk':4},'slot':'ring'},
    {'name':'Blood Pendant',    'icon':'📿','type':'amulet','rarity':'rare','stats':{'vit':5,'hp':20},'slot':'amulet'},
    {'name':'Soul Choker',      'icon':'📿','type':'amulet','rarity':'rare','stats':{'vit':6,'int':4,'hp':15},'slot':'amulet'},
    # ── Legendary — Egyptian-themed, unique, powerful ──
    {'name':'Eye of Horus',        'icon':'👁️','type':'weapon','rarity':'legendary','stats':{'atk':22,'int':6,'lifesteal':5},'slot':'weapon'},
    {'name':'Spear of Anubis',     'icon':'🗡️','type':'weapon','rarity':'legendary','stats':{'atk':24,'dex':8,'str':4},'slot':'weapon'},
    {'name':'Flail of Sekhmet',    'icon':'🪓','type':'weapon','rarity':'legendary','stats':{'atk':28,'str':6},'slot':'weapon'},
    {'name':'Bow of Neith',        'icon':'🏹','type':'weapon','rarity':'legendary','stats':{'atk':18,'dex':12},'slot':'weapon'},
    {'name':'Shroud of Osiris',    'icon':'🛡️','type':'armor', 'rarity':'legendary','stats':{'def':28,'vit':10,'hp':30},'slot':'armor'},
    {'name':"Scales of Ma'at",     'icon':'⚖️','type':'armor', 'rarity':'legendary','stats':{'def':20,'vit':6,'int':6},'slot':'armor'},
    {'name':'Ring of the Pharaoh', 'icon':'💍','type':'ring',  'rarity':'legendary','stats':{'str':6,'dex':6,'vit':4},'slot':'ring'},
    {'name':'Amulet of Thoth',     'icon':'📿','type':'amulet','rarity':'legendary','stats':{'int':10,'mp':20,'atk':4},'slot':'amulet'},
]

# ═══════════════════════════════════════════════════════
# RAID BOSSES — 10-player encounters with mechanics
# ═══════════════════════════════════════════════════════

RAID_BOSSES = {
    'warden': {
        'id':'warden','name':'Warden of the Gates','icon':'⚖️',
        'lore':'The ancient keeper who judges all souls at the threshold of Duat.',
        'difficulty':3,'hp_per_player':700,'atk':58,'def':16,
        'aoe_interval':12,'mark_interval':8,
        'phases':[
            {'id':1,'pct':1.00,'name':'Gate Stance','desc':'The Warden surveys his domain.'},
            {'id':2,'pct':0.70,'name':'Judgment','desc':'The Warden marks souls for punishment.','trigger':'mark_of_doom'},
            {'id':3,'pct':0.40,'name':'Shadow Split','desc':'Shadow guardians tear free from the Warden.','trigger':'shadow_adds'},
            {'id':4,'pct':0.20,'name':'Final Judgment','desc':'The Warden channels all power. Attacks intensify.','trigger':'frenzy'},
        ],
        'loot_bonus':0.15,
    },
    'apophis': {
        'id':'apophis','name':'Apophis the Devourer','icon':'🐍',
        'lore':'The primordial chaos serpent who hungers to unmake all of creation.',
        'difficulty':4,'hp_per_player':1000,'atk':72,'def':20,
        'aoe_interval':10,'mark_interval':0,
        'phases':[
            {'id':1,'pct':1.00,'name':'Coiling','desc':'Apophis circles, testing the party.'},
            {'id':2,'pct':0.65,'name':'Venom Surge','desc':'Venom floods the chamber — all heroes are being poisoned.','trigger':'venom_pool'},
            {'id':3,'pct':0.40,'name':'Devour','desc':'Apophis consumes a hero whole. Free them before they are digested.','trigger':'devour'},
            {'id':4,'pct':0.20,'name':'Chaos Unbound','desc':'Apophis wraps himself in chaotic energy — only overwhelming force can pierce it.','trigger':'chaos_shield'},
        ],
        'loot_bonus':0.25,
    },
    'pharaoh': {
        'id':'pharaoh','name':'The Pharaoh Ascendant','icon':'👑',
        'lore':'A god-king who stole divine fire and now bars the gates of eternity to all mortals.',
        'difficulty':5,'hp_per_player':1400,'atk':88,'def':26,
        'aoe_interval':10,'mark_interval':0,
        'phases':[
            {'id':1,'pct':1.00,'name':'Divine Reign','desc':'The Pharaoh radiates stolen godlike power.'},
            {'id':2,'pct':0.75,'name':'Plague of the Gods','desc':'Three souls are condemned with divine plague. Cleanse before it spreads.','trigger':'plague_mark'},
            {'id':3,'pct':0.50,'name':'Wrath and Ruin','desc':'Guardians descend. A reckoning is invoked — burn them down fast.','trigger':'adds_dps_check'},
            {'id':4,'pct':0.25,'name':'Rebirth','desc':'The Pharaoh channels stolen divine life. Stop the Rebirth or face annihilation.','trigger':'rebirth'},
        ],
        'loot_bonus':0.40,
    },
}

def _raid_aoe_interval(bd, n):
    return max(3, int(bd['aoe_interval'] * math.sqrt(max(1,n)) / math.sqrt(10)))

def _raid_mark_interval(bd, n):
    base = bd.get('mark_interval', 0)
    if not base: return 0
    return max(3, int(base * math.sqrt(max(1,n)) / math.sqrt(10)))

def _get_raid_phase(hp_pct, phases):
    """Return the most-advanced phase whose threshold has been crossed."""
    active = phases[0]
    for ph in phases:
        if hp_pct <= ph['pct'] + 0.001:
            active = ph
    return active

def _make_boss_state(boss_id, n_players):
    bd = RAID_BOSSES[boss_id]
    max_hp = bd['hp_per_player'] * max(1, n_players)
    return {
        'id': boss_id, 'name': bd['name'], 'icon': bd['icon'],
        'hp': max_hp, 'max_hp': max_hp,
        'atk': bd['atk'], 'def': bd['def'],
        'phase': 1, 'phase_name': bd['phases'][0]['name'],
        'immune': False,
        'adds': [],
        'attack_count': 0,
        'phase_announced': {1: True},
        'aoe_next': _raid_aoe_interval(bd, n_players),
        'mark_next': _raid_mark_interval(bd, n_players) or 999999,
        'venom_stacks': 0,
        'devour': None,
        'chaos_shield': None,
        'dps_check': None,
        'rebirth_fired': False,
        'rebirth_check': None,
        'active_mechanic': None,
    }

def _roster_entry(uid, char_id, username, char_name, cls, class_icon, hp, max_hp):
    return {
        'uid': str(uid), 'char_id': char_id,
        'username': username, 'char_name': char_name,
        'class': cls, 'class_icon': class_icon,
        'hp': hp, 'max_hp': max_hp,
        'ready': False, 'dead': False,
        'marked': 0, 'plagued': 0,
    }

def _get_raid_data(db, rid):
    row = db.execute('SELECT * FROM raids WHERE id=?', (rid,)).fetchone()
    if not row: return None
    row = dict(row)
    row['roster']     = json.loads(row['roster'])
    row['boss_state'] = json.loads(row['boss_state'])
    row['event_log']  = json.loads(row['event_log'])
    row['loot_rolls'] = json.loads(row['loot_rolls'])
    return row

def _save_raid_data(db, rid, raid):
    db.execute("""UPDATE raids SET state=?,roster=?,boss_state=?,event_log=?,loot_rolls=?,
                  updated_at=strftime('%s','now') WHERE id=?""",
               (raid['state'], json.dumps(raid['roster']), json.dumps(raid['boss_state']),
                json.dumps(raid['event_log']), json.dumps(raid['loot_rolls']), rid))
    db.commit()

def _raid_log(raid, msg, mtype=''):
    raid['event_log'].append({'msg': msg, 'type': mtype, 'ts': int(time.time())})
    if len(raid['event_log']) > 120:
        raid['event_log'] = raid['event_log'][-80:]

def _resolve_mechanic_triggers(boss, roster, bd, n, log_fn):
    """Check if any mechanics should fire based on attack_count. Returns list of fired mechanic events."""
    fired = []
    atk = boss['attack_count']
    hp_pct = boss['hp'] / boss['max_hp'] if boss['max_hp'] else 0

    # Phase transition check
    cur_phase = _get_raid_phase(hp_pct, bd['phases'])
    old_phase_id = boss.get('phase', 1)
    if cur_phase['id'] != old_phase_id and cur_phase['id'] not in boss.get('phase_announced', {}):
        boss['phase'] = cur_phase['id']
        boss['phase_name'] = cur_phase['name']
        boss.setdefault('phase_announced', {})[cur_phase['id']] = True
        trigger = cur_phase.get('trigger')
        log_fn(f"⚡ PHASE {cur_phase['id']}: {cur_phase['name']} — {cur_phase['desc']}", 'phase')
        fired.append({'type': 'phase', 'phase': cur_phase['id'], 'name': cur_phase['name'], 'trigger': trigger})

        # Apply trigger
        if trigger == 'frenzy':
            boss['aoe_next'] = max(3, boss['aoe_next'] // 2)
            log_fn('💢 FRENZY — AoE attacks now twice as frequent!', 'mechanic')
            boss['active_mechanic'] = 'frenzy'
            fired.append({'type': 'frenzy'})

        elif trigger == 'shadow_adds':
            boss['immune'] = True
            n_adds = 2 if bd['id'] != 'pharaoh' else 3
            for i in range(n_adds):
                add_hp = max(200, int(boss['max_hp'] * 0.08))
                boss['adds'].append({
                    'id': i+1, 'name': 'Shadow Warden' if bd['id']=='warden' else 'Royal Guardian',
                    'icon': '👥', 'hp': add_hp, 'max_hp': add_hp,
                    'atk': max(10, int(boss['atk'] * 0.55)),
                })
            boss['active_mechanic'] = 'shadow_adds'
            log_fn(f"⚠ SHADOW ADDS — {n_adds} guardians spawn! Boss is IMMUNE until they fall!", 'mechanic')
            fired.append({'type': 'shadow_adds', 'n': n_adds})

        elif trigger == 'venom_pool':
            boss['active_mechanic'] = 'venom_pool'
            log_fn('☠ VENOM SURGE — Poison floods the chamber! Kill fast — each hit you take adds venom stacks.', 'mechanic')
            fired.append({'type': 'venom_pool'})

        elif trigger == 'devour':
            alive = [p for p in roster if not p['dead']]
            if alive:
                target = random.choice(alive)
                boss['devour'] = {
                    'uid': target['uid'],
                    'username': target['username'],
                    'free_hp_needed': max(200, int(boss['max_hp'] * 0.04 * math.sqrt(n))),
                    'hp_done': 0,
                    'attacks_left': 6,
                }
                boss['active_mechanic'] = 'devour'
                log_fn(f"🌀 DEVOUR — {target['username']} is being devoured! Deal {boss['devour']['free_hp_needed']} combined damage to free them!", 'mechanic')
                fired.append({'type': 'devour', 'target': target['username'], 'uid': target['uid']})

        elif trigger == 'chaos_shield':
            needed = max(300, int(boss['max_hp'] * 0.05 * math.sqrt(n)))
            boss['chaos_shield'] = {'needed': needed, 'done': 0, 'attacks_left': int(2.5 * n)}
            boss['immune'] = True
            boss['active_mechanic'] = 'chaos_shield'
            log_fn(f"⚡ CHAOS SHIELD — Boss is immune! Collectively deal {needed} damage to break it.", 'mechanic')
            fired.append({'type': 'chaos_shield', 'needed': needed})

        elif trigger == 'plague_mark':
            alive = [p for p in roster if not p['dead'] and not p.get('plagued')]
            targets = random.sample(alive, min(3, len(alive)))
            for t in targets:
                t['plagued'] = 4
            names = ', '.join(t['username'] for t in targets)
            boss['active_mechanic'] = 'plague_mark'
            log_fn(f"🦠 PLAGUE MARK — {names} are plagued! They must Cleanse or the plague spreads!", 'mechanic')
            fired.append({'type': 'plague_mark', 'targets': [t['uid'] for t in targets]})

        elif trigger == 'adds_dps_check':
            boss['immune'] = False  # ensure boss is hittable
            n_adds = 3
            for i in range(n_adds):
                add_hp = max(300, int(boss['max_hp'] * 0.10))
                boss['adds'].append({
                    'id': i+1, 'name': 'Royal Guardian',
                    'icon': '🗡️', 'hp': add_hp, 'max_hp': add_hp,
                    'atk': max(15, int(boss['atk'] * 0.6)),
                })
            dps_needed = int(boss['max_hp'] * 0.25)
            attacks_allowed = int(3.5 * n)
            boss['dps_check'] = {'needed': dps_needed, 'done': 0, 'attacks_left': attacks_allowed}
            boss['active_mechanic'] = 'adds_dps_check'
            log_fn(f"⏱ DPS CHECK — Kill guardians and deal {dps_needed} damage in {attacks_allowed} attacks or face divine punishment!", 'mechanic')
            fired.append({'type': 'adds_dps_check', 'needed': dps_needed})

        elif trigger == 'rebirth':
            if not boss.get('rebirth_fired'):
                boss['rebirth_fired'] = True
                heal_to = int(boss['max_hp'] * 0.60)
                boss['hp'] = max(boss['hp'], heal_to)
                boss['rebirth_check'] = {'attacks_left': int(3 * n)}
                boss['active_mechanic'] = 'rebirth'
                log_fn(f"💚 REBIRTH — The Pharaoh heals to {boss['hp']} HP! Burn him down in {boss['rebirth_check']['attacks_left']} attacks or face annihilation!", 'mechanic')
                fired.append({'type': 'rebirth', 'attacks_left': boss['rebirth_check']['attacks_left']})

    # Periodic AoE (bone_shatter / venom tick)
    if atk >= boss['aoe_next']:
        boss['aoe_next'] = atk + _raid_aoe_interval(bd, n)
        is_venom = boss.get('venom_stacks', 0) > 0
        aoe_base = int(boss['atk'] * 0.30)
        venom_bonus = boss.get('venom_stacks', 0) * 12
        aoe_dmg = aoe_base + venom_bonus
        total_hit = 0
        for p in roster:
            if not p['dead']:
                hit = max(5, aoe_dmg + random.randint(-8, 8))
                p['hp'] = max(0, p['hp'] - hit)
                if p['hp'] <= 0 and not p['dead']:
                    p['dead'] = True
                    log_fn(f"💀 {p['username']} falls to the AoE!", 'death')
                total_hit += hit
        if is_venom:
            boss['venom_stacks'] = min(10, boss.get('venom_stacks', 0) + 1)
            log_fn(f"☠ VENOM TICK (×{boss['venom_stacks']}) — {aoe_dmg} dmg to all! Stacks growing!", 'mechanic')
        else:
            log_fn(f"🔥 BONE SHATTER — {aoe_dmg} damage hits the entire raid!", 'mechanic')
        fired.append({'type': 'aoe', 'dmg': aoe_dmg, 'venom': is_venom})

    # Periodic mark (Warden only)
    mark_interval = _raid_mark_interval(bd, n)
    if mark_interval and boss.get('phase', 1) >= 2 and atk >= boss.get('mark_next', 999999):
        boss['mark_next'] = atk + mark_interval
        alive_unmarked = [p for p in roster if not p['dead'] and not p.get('marked')]
        if alive_unmarked:
            target = random.choice(alive_unmarked)
            target['marked'] = 5
            boss['active_mechanic'] = 'mark_of_doom'
            log_fn(f"☠ MARK OF DOOM — {target['username']} is marked! They take double damage for 5 hits.", 'mechanic')
            fired.append({'type': 'mark_of_doom', 'target': target['username'], 'uid': target['uid']})

    return fired

def _check_wipe(roster):
    return all(p['dead'] for p in roster)

def _raid_make_loot(floor, boss_id):
    bd = RAID_BOSSES.get(boss_id, {})
    bonus = bd.get('loot_bonus', 0.15)
    rr = random.random()
    if   rr < 0.05 + bonus:         rarity = 'legendary'
    elif rr < 0.20 + bonus * 1.5:   rarity = 'rare'
    elif rr < 0.50:                  rarity = 'magic'
    else:                            rarity = 'normal'
    pool = [i for i in LOOT_POOL if i['rarity'] == rarity] or LOOT_POOL
    item = dict(random.choice(pool))
    fl = max(1, floor)
    fl_bonus = max(0, int(math.log(fl + 1, 2)))
    mult = {'normal':1.2,'magic':2.0,'rare':3.0,'legendary':4.5}[rarity]
    item['stats'] = {k: max(1, int((v + fl_bonus) * mult)) for k, v in item.get('stats', {}).items()}
    item['id'] = f"raid_{int(time.time())}_{random.randint(1000,9999)}"
    item['from_raid'] = True
    return item

# ═══════════════════════════════════════════════════════
# PET SYSTEM — Raven & Scarab auto-play companions
# ═══════════════════════════════════════════════════════

PET_DEFS = {
    'raven':  {'device_ip':'192.168.1.3','display_name':'Raven', 'class_name':'Warden', 'icon':'🦅',
               'base_hp':40,'base_atk':7,'base_def':3},
    'scarab': {'device_ip':'192.168.1.2','display_name':'Scarab','class_name':'Courier','icon':'🪲',
               'base_hp':35,'base_atk':8,'base_def':2},
}

def _ensure_pets(db):
    """Seed pet rows if they don't exist, assign stable PINs."""
    # Stable PINs derived from pet id — same every time, no random rotation
    PINS = {'raven': '7743', 'scarab': '5151'}
    for pid, p in PET_DEFS.items():
        exists = db.execute('SELECT id FROM pets WHERE id=?',(pid,)).fetchone()
        if not exists:
            db.execute("""INSERT INTO pets(id,device_ip,display_name,class_name,icon,claim_pin,
                                          hp,max_hp,atk,def_val,offline_since)
                          VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                       (pid,p['device_ip'],p['display_name'],p['class_name'],p['icon'],
                        PINS.get(pid,'1234'),
                        p['base_hp'],p['base_hp'],p['base_atk'],p['base_def'],int(time.time())))
        else:
            # Ensure claim_pin column exists on old DBs (migration safety)
            try:
                db.execute('UPDATE pets SET claim_pin=? WHERE id=? AND claim_pin=?',
                           (PINS.get(pid,'1234'), pid, '0000'))
            except Exception:
                pass
            # Migration: add unbind lockout columns if missing
            for col, defval in [('unbind_attempts','INTEGER DEFAULT 0'),
                                 ('unbind_locked_until','INTEGER DEFAULT 0')]:
                try:
                    db.execute(f'ALTER TABLE pets ADD COLUMN {col} {defval}')
                except Exception:
                    pass
    db.commit()

def _ping(ip):
    try:
        r = subprocess.run(['ping','-c','1','-W','1',ip],capture_output=True,timeout=3)
        return r.returncode == 0
    except Exception:
        return False

def _make_pet_loot(pet_floor, offline_hours):
    """Generate one loot item. quality scales 0→1 over 24h offline. Never legendary."""
    quality = min(1.0, offline_hours / 24.0)
    rr = random.random()
    if   rr < 0.06 + quality * 0.30: rarity = 'rare'
    elif rr < 0.28 + quality * 0.22: rarity = 'magic'
    else:                             rarity = 'normal'
    pool = [i for i in LOOT_POOL if i['rarity'] == rarity]
    if not pool: pool = LOOT_POOL
    item = dict(random.choice(pool))
    item['stats'] = {k: max(1, round(v*(1+(pet_floor-1)*0.07))) for k,v in item.get('stats',{}).items()}
    item['id'] = f"pet_{int(time.time())}_{random.randint(1000,9999)}"
    item['from_pet'] = True
    item['generated_at'] = int(time.time())
    return item

def _pet_combat_tick(db, pet_id):
    """Advance pet one room: fight a monster, gain XP, maybe gain loot."""
    pet = db.execute('SELECT * FROM pets WHERE id=?',(pet_id,)).fetchone()
    if not pet: return
    pet = dict(pet)

    floor   = pet['floor']
    hp      = pet['hp']
    max_hp  = pet['max_hp']
    atk     = pet['atk']
    def_    = pet['def_val']
    level   = pet['level']
    xp      = pet['xp']
    xp_next = pet['xp_next']

    mon = make_monster(floor)
    mon_hp = mon['hp']

    # Simplified combat
    rounds = 0
    while mon_hp > 0 and hp > 0 and rounds < 40:
        pet_dmg = max(1, atk - mon['def'] + random.randint(-2,3))
        mon_hp  = max(0, mon_hp - pet_dmg)
        if mon_hp <= 0: break
        mon_dmg = max(1, mon['atk'] - def_ + random.randint(-1,2))
        hp      = max(0, hp - mon_dmg)
        rounds += 1

    if hp <= 0:
        # Pet retreats — respawn at 60% HP, no progress this tick
        db.execute('UPDATE pets SET hp=? WHERE id=?', (int(max_hp*0.6), pet_id))
        db.commit(); return

    # Win: gain XP, recover some HP, maybe advance floor, maybe loot
    xp += mon['xp']
    hp  = min(max_hp, hp + int(max_hp*0.18))  # post-fight regen

    # Level up
    while xp >= xp_next and level < 30:
        xp      -= xp_next
        level   += 1
        xp_next  = int(100*(1.4**(level-1)))
        max_hp   = int(max_hp * 1.04)
        atk     += 1
        if level % 3 == 0: def_ += 1
    hp = min(max_hp, hp)

    # Floor progression (~20% chance per tick win)
    if random.random() < 0.20:
        floor += 1

    # Accumulate loot (55% chance, max 10 items queued)
    pending = json.loads(pet.get('pending_delivery') or '[]')
    if len(pending) < 10 and random.random() < 0.55:
        pending.append(_make_pet_loot(floor, 8))  # baseline "8h quality" during auto-play

    db.execute("""UPDATE pets SET level=?,xp=?,xp_next=?,floor=?,hp=?,max_hp=?,atk=?,def_val=?,
                  pending_delivery=? WHERE id=?""",
               (level,xp,xp_next,floor,hp,max_hp,atk,def_,json.dumps(pending),pet_id))
    db.commit()

def _pet_loop():
    """Background thread: ping devices, update online status, run auto-play tick every 5 min."""
    import sqlite3 as _sq
    tick_interval = 300  # seconds between full ticks
    last_tick = {}

    while True:
        try:
            db = _sq.connect(DB_PATH)
            db.row_factory = _sq.Row
            db.execute("PRAGMA journal_mode=WAL")

            _ensure_pets(db)
            now = int(time.time())

            for pid, p in PET_DEFS.items():
                pet = db.execute('SELECT * FROM pets WHERE id=?',(pid,)).fetchone()
                if not pet: continue
                pet = dict(pet)

                was_online = bool(pet['online'])
                is_online  = _ping(p['device_ip'])

                if is_online and not was_online:
                    # Device came back — generate comeback loot based on offline duration
                    offline_secs  = now - pet['offline_since'] if pet['offline_since'] else 0
                    offline_hours = min(24, offline_secs / 3600)
                    n_items = max(1, min(3, int(offline_hours / 8) + 1))
                    pending = json.loads(pet.get('pending_delivery') or '[]')
                    for _ in range(n_items):
                        if len(pending) < 10:
                            pending.append(_make_pet_loot(pet['floor'], offline_hours))
                    db.execute('UPDATE pets SET online=1,last_seen=?,pending_delivery=? WHERE id=?',
                               (now,json.dumps(pending),pid))
                elif not is_online and was_online:
                    db.execute('UPDATE pets SET online=0,offline_since=? WHERE id=?',(now,pid))
                elif is_online:
                    db.execute('UPDATE pets SET last_seen=? WHERE id=?',(now,pid))

                db.commit()

                # Run combat tick every tick_interval seconds (only when online)
                if is_online:
                    if now - last_tick.get(pid,0) >= tick_interval:
                        last_tick[pid] = now
                        _pet_combat_tick(db, pid)

            db.close()
        except Exception as e:
            print(f"[PET LOOP ERROR] {e}")

        time.sleep(60)  # check every minute

def make_monster(floor, is_boss=False, is_elite=False):
    """
    Balance philosophy: party-game feel, not hardcore grind.
    - HP scales logarithmically so floor 1-5 is easy, 6-15 moderate, 15+ genuinely hard
    - Damage scales more slowly than HP so fights feel beatable
    - Boss gets a rage phase at 50% HP (handled client-side via is_boss flag + max_hp)
    - Always deal at least 1 damage (no zero-floor walls)
    """
    tier_max = min(5, math.ceil(floor / 5))
    pool = [m for m in MONSTER_POOL if m['tier'] <= tier_max]
    boss_pool = [m for m in MONSTER_POOL if m['tier'] == tier_max]
    base = random.choice(boss_pool if is_boss else pool)

    mult = 2.0 if is_boss else 1.25 if is_elite else 1.0

    # Logarithmic HP scaling — tuned for faster fights
    hp_scale = 1.1 + math.log(floor + 1, 2) * 0.6
    # Damage scales moderately — fights feel beatable but not trivial
    atk_scale = 1.0 + math.log(floor + 1, 2) * 0.45
    def_scale  = 1.0 + math.log(floor + 1, 2) * 0.28

    hp  = max(8,  int(base['hp']  * mult * hp_scale))
    atk = max(2,  int(base['atk'] * mult * atk_scale))
    dfn = max(0,  int(base['def'] * mult * def_scale))
    xp  = max(5,  int(base['xp']  * mult * hp_scale))  # xp tracks HP scale
    gold_lo = max(1, int(base['gold'][0] * (1 + floor * 0.4)))
    gold_hi = max(gold_lo + 1, int(base['gold'][1] * (1 + floor * 0.4)))

    return {
        'name': ('Champion ' if is_boss else 'Elite ' if is_elite else '') + base['name'],
        'icon': base['icon'],
        'hp': hp, 'max_hp': hp,
        'atk': atk, 'def': dfn,
        'xp': xp,
        'gold': [gold_lo, gold_hi],
        'level': floor + random.randint(-1, 2),
        'is_boss': is_boss, 'is_elite': is_elite,
        # Boss rage threshold: below 50% HP, atk increases 40%
        'rage_threshold': hp // 2 if is_boss else 0,
        'rage_atk': int(atk * 1.4) if is_boss else atk,
    }

def make_loot(floor, guaranteed=False, force_rare=False):
    """
    Loot balance:
    - Normal: baseline stats, always usable
    - Magic: 1.6x stat values, one bonus stat
    - Rare: 2.4x stat values, two bonus stats, guaranteed on boss
    Rarity is always visibly meaningful — no overlap between tiers.
    """
    if not guaranteed and not force_rare and random.random() < 0.15:
        return None

    rr = random.random()
    if force_rare:
        # Boss kill: legendary chance scales 5%→25% over first 10 floors
        if rr < min(0.25, 0.05 + floor * 0.02):
            rarity = 'legendary'
        else:
            rarity = 'rare'
    elif rr < min(0.04, 0.005 + floor * 0.003):
        rarity = 'legendary'
    elif rr < 0.06 + floor * 0.006:
        rarity = 'rare'
    elif rr < 0.28 + floor * 0.012:
        rarity = 'magic'
    else:
        rarity = 'normal'

    pool = [i for i in LOOT_POOL if i['rarity'] == rarity] or [i for i in LOOT_POOL if i['rarity'] == 'rare']
    item = dict(random.choice(pool))

    # Scale base stats by floor depth (gentle logarithmic)
    floor_bonus = max(0, int(math.log(floor + 1, 2)))
    base_stats = {k: v + floor_bonus for k, v in item['stats'].items()}

    # Rarity multiplier — ensures tiers are visibly distinct
    mult = {'normal': 1.0, 'magic': 1.6, 'rare': 2.4, 'legendary': 3.5}[rarity]
    scaled = {k: max(1, int(v * mult)) for k, v in base_stats.items()}

    # Bonus stats by rarity tier
    bonus_stats = ['str','dex','vit','int','atk','def']
    if rarity == 'magic':
        bonus_key = random.choice(bonus_stats)
        scaled[bonus_key] = scaled.get(bonus_key, 0) + 1 + floor_bonus
    elif rarity == 'rare':
        for _ in range(2):
            bonus_key = random.choice(bonus_stats)
            scaled[bonus_key] = scaled.get(bonus_key, 0) + 2 + floor_bonus
    elif rarity == 'legendary':
        for _ in range(3):
            bonus_key = random.choice(bonus_stats)
            scaled[bonus_key] = scaled.get(bonus_key, 0) + 3 + floor_bonus

    item['stats'] = scaled
    return item

def generate_dungeon(floor):
    size = GRID
    visited = [[False]*size for _ in range(size)]
    exits = {(r,c): set() for r in range(size) for c in range(size)}
    OPP = {'N':'S','S':'N','E':'W','W':'E'}

    def carve(r, c):
        visited[r][c] = True
        dirs = [('N',-1,0),('S',1,0),('E',0,1),('W',0,-1)]
        random.shuffle(dirs)
        for d, dr, dc in dirs:
            nr, nc = r+dr, c+dc
            if 0 <= nr < size and 0 <= nc < size and not visited[nr][nc]:
                exits[(r,c)].add(d)
                exits[(nr,nc)].add(OPP[d])
                carve(nr, nc)

    carve(0, 0)
    # Add loops
    for _ in range(size // 2):
        r, c = random.randint(0,size-1), random.randint(0,size-1)
        for d, dr, dc in [('E',0,1),('S',1,0)]:
            nr, nc = r+dr, c+dc
            if 0 <= nr < size and 0 <= nc < size:
                exits[(r,c)].add(d)
                exits[(nr,nc)].add(OPP[d])

    all_coords = [(r,c) for r in range(size) for c in range(size)]
    def mdist(a,b): return abs(a[0]-b[0])+abs(a[1]-b[1])

    boss_coord  = max(all_coords, key=lambda p: mdist(p,(0,0)))
    stairs_coord = max([p for p in all_coords if p != boss_coord and p != (0,0)],
                       key=lambda p: mdist(p,(0,0)))
    remaining = [p for p in all_coords if p not in [(0,0), boss_coord, stairs_coord]]
    random.shuffle(remaining)

    total = len(remaining)
    n_enemy    = int(total * 0.45)
    n_loot     = int(total * 0.12)
    n_trap     = int(total * 0.10)
    n_shrine   = int(total * 0.08)
    n_roulette = max(1, int(total * 0.07))
    n_merchant = 1 if floor > 1 else 0
    n_special  = n_enemy + n_loot + n_trap + n_shrine + n_roulette + n_merchant
    type_list  = (['enemy']*n_enemy + ['loot']*n_loot + ['trap']*n_trap +
                  ['shrine']*n_shrine + ['roulette']*n_roulette +
                  ['merchant']*n_merchant + ['empty']*max(0,total-n_special))
    random.shuffle(type_list)

    rooms = {}

    for i, (r,c) in enumerate(remaining):
        rtype = type_list[i] if i < len(type_list) else 'empty'
        room = {'type':rtype, 'exits':list(exits[(r,c)]),
                'cleared':False, 'fog':True, 'monster':None, 'loot':None, 'loot_taken':{}}
        if rtype == 'enemy':
            room['monster'] = make_monster(floor, is_elite=random.random()<0.2)
            room['loot']    = make_loot(floor)
        elif rtype == 'loot':
            room['loot']    = make_loot(floor, guaranteed=True)
        elif rtype == 'trap':
            room['loot']    = make_loot(floor, guaranteed=True)
            room['disarmed'] = False
            room['sprung']   = False
        elif rtype == 'shrine':
            room['offerings'] = [
                {'type':'hp','name':'Offering of Flesh',  'icon':'🩸','cost':15+floor*5,'used':False},
                {'type':'mp','name':'Offering of Spirit', 'icon':'💫','cost':12+floor*4,'used':False},
            ]
        elif rtype == 'roulette':
            room['spun'] = False
        elif rtype == 'merchant':
            room['shop'] = [
                {'name':'Rejuvenation Potion','icon':'⚗️','cost':15+floor*4,'effect':'rejuv'},
            ]
        rooms[f"{r},{c}"] = room

    rooms['0,0'] = {'type':'start','exits':list(exits[(0,0)]),
                    'cleared':True,'fog':False,'monster':None,'loot':None,'loot_taken':{}}

    br,bc = boss_coord
    boss_mon = make_monster(floor, is_boss=True)
    rooms[f"{br},{bc}"] = {
        'type':'boss','exits':list(exits[boss_coord]),
        'cleared':False,'fog':True,
        'monster':boss_mon,'loot':make_loot(floor,guaranteed=True),'loot_taken':{}
    }

    sr,sc = stairs_coord
    rooms[f"{sr},{sc}"] = {
        'type':'stairs','exits':list(exits[stairs_coord]),
        'cleared':False,'fog':True,'locked':True,
        'monster':None,'loot':None,'loot_taken':{}
    }

    return {
        'grid_size': size, 'floor': floor,
        'boss_coord': f"{br},{bc}", 'stairs_coord': f"{sr},{sc}", 'start_coord': '0,0',
        'boss_dead': False, 'stairs_open': False,
        'rooms': rooms, 'players': {},
        'event_log': [],
        'generated_at': int(time.time()),
    }

# ═══════════════════════════════════════════════════════
# AUTH
# ═══════════════════════════════════════════════════════

@app.route('/api/register', methods=['POST'])
def register():
    d = request.get_json()
    username = (d.get('username') or '').strip()
    pin = str(d.get('pin') or '').strip()
    if not username or not (2 <= len(username) <= 20):
        return jsonify({'error':'Username must be 2-20 characters'}), 400
    if not pin or len(pin) != 4 or not pin.isdigit():
        return jsonify({'error':'PIN must be 4 digits'}), 400
    db = get_db()
    try:
        cur = db.execute('INSERT INTO users(username,pin) VALUES(?,?)',(username,pin))
        db.commit()
        return jsonify({'user_id':cur.lastrowid,'username':username})
    except sqlite3.IntegrityError:
        return jsonify({'error':'Username taken'}), 409

@app.route('/api/login', methods=['POST'])
def login():
    d = request.get_json()
    username = (d.get('username') or '').strip()
    pin = str(d.get('pin') or '').strip()
    db = get_db()
    u = row_to_dict(db.execute('SELECT * FROM users WHERE username=? AND pin=?',(username,pin)).fetchone())
    if not u: return jsonify({'error':'Invalid credentials'}), 401
    return jsonify({'user_id':u['id'],'username':u['username']})

@app.route('/api/users', methods=['GET'])
def list_users():
    db = get_db()
    return jsonify([row_to_dict(r) for r in
        db.execute('SELECT id,username FROM users ORDER BY username').fetchall()])

# ═══════════════════════════════════════════════════════
# CHARACTERS
# ═══════════════════════════════════════════════════════

CLASS_BASES = {
    'fighter':   {'str':8,'dex':6,'vit':8,'int':4, 'hp_m':1.1,'mp_m':0.9},
    'barbarian': {'str':12,'dex':4,'vit':10,'int':2,'hp_m':1.3,'mp_m':0.6},
    'rogue':     {'str':5,'dex':12,'vit':5,'int':4, 'hp_m':0.9,'mp_m':0.9},
    'mage':      {'str':3,'dex':5,'vit':4,'int':14, 'hp_m':0.7,'mp_m':1.6},
    'ranger':    {'str':5,'dex':10,'vit':6,'int':6, 'hp_m':0.95,'mp_m':1.1},
    'samurai':   {'str':10,'dex':10,'vit':6,'int':4,'hp_m':1.0,'mp_m':1.0},
    'cleric':    {'str':4,'dex':5,'vit':8,'int':12, 'hp_m':0.85,'mp_m':1.4},
    'paladin':   {'str':9,'dex':5,'vit':10,'int':5, 'hp_m':1.2, 'mp_m':1.0},
}
CLASS_ICONS = {'fighter':'🗡️','barbarian':'🪓','rogue':'🗡️','mage':'🔮','ranger':'🏹','samurai':'⚔️',
               'cleric':'✨','paladin':'⚜️'}
START_WEAPONS = {
    'mage':    {'name':'Staff of Ruin', 'icon':'🪄','type':'weapon','rarity':'normal','stats':{'atk':7,'int':4},'slot':'weapon'},
    'ranger':  {'name':'Short Bow',     'icon':'🏹','type':'weapon','rarity':'normal','stats':{'atk':5,'dex':2},'slot':'weapon'},
    'cleric':  {'name':'Divine Staff',  'icon':'🌟','type':'weapon','rarity':'normal','stats':{'atk':5,'int':4},'slot':'weapon'},
    'paladin': {'name':'Holy Mace',     'icon':'⚖️','type':'weapon','rarity':'normal','stats':{'atk':7,'vit':2},'slot':'weapon'},
}
DEFAULT_WEAPON = {'name':'Short Sword','icon':'🗡️','type':'weapon','rarity':'normal','stats':{'atk':5},'slot':'weapon'}
START_ARMOR    = {'name':'Tattered Cloth','icon':'👕','type':'armor','rarity':'normal','stats':{'def':2},'slot':'armor'}

@app.route('/api/characters', methods=['GET'])
def get_characters():
    uid = request.args.get('user_id')
    if not uid: return jsonify({'error':'user_id required'}), 400
    db = get_db()
    chars = [row_to_dict(r) for r in
             db.execute('SELECT * FROM characters WHERE user_id=? ORDER BY updated_at DESC',(uid,)).fetchall()]
    for c in chars:
        c['equipment'] = json.loads(c['equipment'])
        c['inventory']  = json.loads(c['inventory'])
    return jsonify(chars)

@app.route('/api/characters', methods=['POST'])
def create_character():
    d = request.get_json()
    uid = d.get('user_id')
    if not uid: return jsonify({'error':'user_id required'}), 400
    db = get_db()
    u = row_to_dict(db.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone())
    if not u: return jsonify({'error':'User not found'}), 404
    if db.execute('SELECT COUNT(*) FROM characters WHERE user_id=?',(uid,)).fetchone()[0] >= 6:
        return jsonify({'error':'Max 6 characters'}), 400
    cls = d.get('class','fighter')
    b   = CLASS_BASES.get(cls, CLASS_BASES['fighter'])
    hc  = 1 if d.get('hardcore') else 0
    cname = (d.get('char_name') or u['username']).strip()[:20]
    max_hp = int((b['vit']*8+10)*b['hp_m'])
    max_mp = int((b['int']*5+5)*b['mp_m'])
    eq = {'weapon':START_WEAPONS.get(cls,DEFAULT_WEAPON),'armor':START_ARMOR,'ring':None,'amulet':None}
    cur = db.execute("""INSERT INTO characters
        (user_id,name,class,hardcore,hp,max_hp,mp,max_mp,
         stat_str,stat_dex,stat_vit,stat_int,equipment,inventory)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (uid,cname,cls,hc,max_hp,max_hp,max_mp,max_mp,
         b['str'],b['dex'],b['vit'],b['int'],json.dumps(eq),json.dumps([])))
    db.commit()
    char = row_to_dict(db.execute('SELECT * FROM characters WHERE id=?',(cur.lastrowid,)).fetchone())
    char['equipment'] = json.loads(char['equipment'])
    char['inventory']  = json.loads(char['inventory'])
    char['username']   = u['username']
    update_ladder(db, char)
    db.commit()
    return jsonify(char), 201

@app.route('/api/characters/<int:cid>', methods=['PUT'])
def save_character(cid):
    d = request.get_json()
    uid = d.get('user_id')
    db = get_db()
    if not row_to_dict(db.execute('SELECT id FROM characters WHERE id=? AND user_id=?',(cid,uid)).fetchone()):
        return jsonify({'error':'Not found'}), 404
    fields = ['level','ladder_level','floor','xp','xp_next','gold','hp','max_hp','mp','max_mp',
              'stat_points','kills','deepest_floor','stat_str','stat_dex','stat_vit','stat_int',
              'hp_pots','mp_pots','alive']
    updates = {f: d[f] for f in fields if f in d}
    if 'equipment' in d: updates['equipment'] = json.dumps(d['equipment'])
    if 'inventory'  in d: updates['inventory']  = json.dumps(d['inventory'])
    if updates:
        sc = ', '.join(f'{k}=?' for k in updates) + ", updated_at=strftime('%s','now')"
        db.execute(f'UPDATE characters SET {sc} WHERE id=?', list(updates.values())+[cid])
        db.commit()
    updated = row_to_dict(db.execute('SELECT * FROM characters WHERE id=?',(cid,)).fetchone())
    updated['equipment'] = json.loads(updated['equipment'])
    updated['inventory']  = json.loads(updated['inventory'])
    u = row_to_dict(db.execute('SELECT username FROM users WHERE id=?',(uid,)).fetchone())
    updated['username'] = u['username'] if u else '?'
    update_ladder(db, updated)
    db.commit()
    return jsonify(updated)

@app.route('/api/characters/<int:cid>/delete', methods=['POST'])
def delete_character(cid):
    d = request.get_json()
    uid = d.get('user_id')
    db = get_db()
    if not db.execute('SELECT id FROM characters WHERE id=? AND user_id=?',(cid,uid)).fetchone():
        return jsonify({'error':'Not found'}), 404
    db.execute('DELETE FROM characters WHERE id=?',(cid,))
    db.execute('DELETE FROM ladder WHERE char_id=?',(cid,))
    db.commit()
    return jsonify({'ok':True})

# ═══════════════════════════════════════════════════════
# LADDER
# ═══════════════════════════════════════════════════════

@app.route('/api/ladder', methods=['GET'])
def get_ladder():
    db = get_db()
    sc = [row_to_dict(r) for r in db.execute("""SELECT * FROM ladder WHERE hardcore=0
        ORDER BY deepest_floor DESC,level DESC,kills DESC LIMIT 30""").fetchall()]
    hc = [row_to_dict(r) for r in db.execute("""SELECT * FROM ladder WHERE hardcore=1
        ORDER BY deepest_floor DESC,level DESC,kills DESC LIMIT 30""").fetchall()]
    kills = [row_to_dict(r) for r in db.execute("""SELECT * FROM ladder
        ORDER BY kills DESC,deepest_floor DESC,level DESC LIMIT 30""").fetchall()]
    return jsonify({'softcore':sc,'hardcore':hc,'kills':kills})

@app.route('/api/stats', methods=['GET'])
def get_stats():
    db = get_db()
    rows = db.execute("""
        SELECT u.username,
               COUNT(c.id) as char_count,
               COALESCE(SUM(c.kills), 0) as total_kills,
               MAX(c.deepest_floor) as best_floor,
               MAX(c.level) as best_level,
               SUM(CASE WHEN c.alive=1 THEN 1 ELSE 0 END) as alive_count
        FROM users u
        JOIN characters c ON c.user_id = u.id
        GROUP BY u.id, u.username
        ORDER BY best_floor DESC, total_kills DESC
    """).fetchall()
    return jsonify([row_to_dict(r) for r in rows])

# ═══════════════════════════════════════════════════════
# SESSIONS & DUNGEON
# ═══════════════════════════════════════════════════════

def gen_sid():
    return ''.join(str(random.randint(0,9)) for _ in range(6))

def get_ds(db, sid):
    row = db.execute('SELECT dungeon_state FROM sessions WHERE id=?',(sid,)).fetchone()
    return json.loads(row['dungeon_state']) if row else None

def save_ds(db, sid, state):
    db.execute("UPDATE sessions SET dungeon_state=?,updated_at=strftime('%s','now') WHERE id=?",
               (json.dumps(state), sid))
    db.commit()

@app.route('/api/sessions', methods=['POST'])
def create_session():
    d = request.get_json()
    uid, cid = d.get('user_id'), d.get('char_id')
    if not uid or not cid: return jsonify({'error':'user_id and char_id required'}), 400
    db = get_db()
    db.execute("DELETE FROM sessions WHERE host_user_id=? AND state='waiting'",(uid,))
    sid = gen_sid()
    while db.execute('SELECT id FROM sessions WHERE id=?',(sid,)).fetchone():
        sid = gen_sid()
    char = row_to_dict(db.execute('SELECT * FROM characters WHERE id=?',(cid,)).fetchone())
    u    = row_to_dict(db.execute('SELECT username FROM users WHERE id=?',(uid,)).fetchone())
    dungeon = generate_dungeon(char.get('floor',1) if char else 1)
    dungeon['players'][str(uid)] = {
        'pos':'0,0','username':u['username'] if u else '?',
        'class_icon':CLASS_ICONS.get(char['class'],'⚔️') if char else '⚔️',
        'hp':char['hp'] if char else 50,'max_hp':char['max_hp'] if char else 50,
        'last_seen':int(time.time())
    }
    db.execute("INSERT INTO sessions(id,host_user_id,host_char_id,dungeon_state) VALUES(?,?,?,?)",
               (sid,uid,cid,json.dumps(dungeon)))
    db.commit()
    return jsonify({'session_id':sid})

@app.route('/api/sessions/open', methods=['GET'])
def list_open_sessions():
    db = get_db()
    cutoff = int(time.time()) - 180
    rows = db.execute("""SELECT s.id,s.host_user_id,u.username as host_name,s.state,s.created_at
        FROM sessions s JOIN users u ON s.host_user_id=u.id
        WHERE s.state='waiting' AND s.updated_at>? ORDER BY s.created_at DESC""",(cutoff,)).fetchall()
    return jsonify([row_to_dict(r) for r in rows])

@app.route('/api/sessions/<sid>/join', methods=['POST'])
def join_session(sid):
    d = request.get_json()
    uid, cid = d.get('user_id'), d.get('char_id')
    db = get_db()
    sess = row_to_dict(db.execute('SELECT * FROM sessions WHERE id=?',(sid,)).fetchone())
    if not sess: return jsonify({'error':'Session not found'}), 404
    if sess['state'] not in ('waiting','active'): return jsonify({'error':'Session closed'}), 409
    if str(sess['host_user_id']) == str(uid): return jsonify({'error':'Cannot join own session'}), 400
    char = row_to_dict(db.execute('SELECT * FROM characters WHERE id=?',(cid,)).fetchone())
    u    = row_to_dict(db.execute('SELECT username FROM users WHERE id=?',(uid,)).fetchone())
    state = get_ds(db, sid)
    state['players'][str(uid)] = {
        'pos':'0,0','username':u['username'] if u else '?',
        'class_icon':CLASS_ICONS.get(char['class'],'⚔️') if char else '⚔️',
        'hp':char['hp'] if char else 50,'max_hp':char['max_hp'] if char else 50,
        'last_seen':int(time.time())
    }
    uname = u['username'] if u else '?'
    state.setdefault('event_log',[]).append(
        {'type':'join','msg':f"⚔ {uname} has entered the dungeon!",'ts':int(time.time())})
    db.execute("""UPDATE sessions SET guest_user_id=?,guest_char_id=?,state='active',
                  dungeon_state=?,updated_at=strftime('%s','now') WHERE id=?""",
               (uid,cid,json.dumps(state),sid))
    db.commit()
    return jsonify({'ok':True,'session_id':sid})

@app.route('/api/sessions/<sid>', methods=['GET'])
def get_session(sid):
    db = get_db()
    sess = row_to_dict(db.execute('SELECT * FROM sessions WHERE id=?',(sid,)).fetchone())
    if not sess: return jsonify({'error':'Not found'}), 404
    sess['dungeon_state'] = json.loads(sess['dungeon_state'])
    return jsonify(sess)

@app.route('/api/sessions/<sid>/move', methods=['POST'])
def move_player(sid):
    d = request.get_json()
    uid = str(d.get('user_id'))
    direction = d.get('direction')
    db = get_db()
    state = get_ds(db, sid)
    if not state: return jsonify({'error':'Not found'}), 404
    p = state['players'].get(uid)
    if not p: return jsonify({'error':'Not in session'}), 403

    pos = p['pos']
    r, c = map(int, pos.split(','))
    room = state['rooms'].get(pos, {})

    if (not room.get('cleared') and room.get('type') == 'enemy'
            and room.get('monster') and room['monster']['hp'] > 0):
        return jsonify({'error':'Defeat the enemy first!', 'blocked':True}), 409

    delta = {'N':(-1,0),'S':(1,0),'E':(0,1),'W':(0,-1)}.get(direction)
    if not delta: return jsonify({'error':'Invalid direction'}), 400
    nr, nc = r+delta[0], c+delta[1]
    if not (0 <= nr < GRID and 0 <= nc < GRID):
        return jsonify({'error':'Wall'}), 400
    if direction not in room.get('exits',[]):
        return jsonify({'error':'No exit that way'}), 400

    new_pos = f"{nr},{nc}"
    new_room = state['rooms'].get(new_pos, {})
    new_room['fog'] = False
    p['pos'] = new_pos
    p['last_seen'] = int(time.time())
    if 'hp' in d: p['hp'] = max(0, int(d['hp']))
    if 'max_hp' in d: p['max_hp'] = max(1, int(d['max_hp']))

    if new_room.get('type') == 'boss' and not new_room.get('cleared'):
        state.setdefault('event_log',[]).append(
            {'type':'move','msg':f"⚠ {p['username']} has entered the BOSS CHAMBER!",'ts':int(time.time())})
    elif new_room.get('type') == 'stairs' and state.get('stairs_open'):
        state.setdefault('event_log',[]).append(
            {'type':'move','msg':f"🪜 {p['username']} has reached the stairs!",'ts':int(time.time())})

    state['rooms'][new_pos] = new_room
    save_ds(db, sid, state)
    return jsonify({'ok':True,'new_pos':new_pos,'room':new_room})

@app.route('/api/sessions/<sid>/sync', methods=['POST'])
def sync_session_hp(sid):
    """Lightweight endpoint — update a player's HP in session state (potion, heal, etc.)."""
    d = request.get_json()
    uid = str(d.get('user_id'))
    db = get_db()
    state = get_ds(db, sid)
    if not state: return jsonify({'error':'Not found'}), 404
    p = state['players'].get(uid)
    if not p: return jsonify({'error':'Not in session'}), 403
    if 'hp' in d: p['hp'] = max(0, int(d['hp']))
    if 'max_hp' in d: p['max_hp'] = max(1, int(d['max_hp']))
    p['last_seen'] = int(time.time())
    save_ds(db, sid, state)
    return jsonify({'ok': True})

@app.route('/api/sessions/<sid>/attack', methods=['POST'])
def attack_room(sid):
    d = request.get_json()
    uid = str(d.get('user_id'))
    player_atk  = int(d.get('player_atk', 5))
    player_def  = int(d.get('player_def', 2))
    player_dex  = int(d.get('player_dex', 6))
    player_maxhp = int(d.get('player_max_hp', 50))
    player_curhp = int(d.get('player_current_hp', 50))
    username    = d.get('username','???')
    is_skill    = bool(d.get('is_skill'))
    skill_name  = d.get('skill_name','Ability')
    skill_mult  = float(d.get('skill_dmg_mult', 1.0))
    is_heal     = bool(d.get('is_heal'))
    heal_pct    = float(d.get('heal_pct', 0))
    heal_target = str(d.get('heal_target_uid', uid))

    db = get_db()
    state = get_ds(db, sid)
    if not state: return jsonify({'error':'Not found'}), 404
    p = state['players'].get(uid)
    if not p: return jsonify({'error':'Not in session'}), 403

    pos  = p['pos']
    room = state['rooms'].get(pos)

    # Co-op heal skill — no monster required
    if is_heal:
        result = {'heal_amount':0,'healed_uid':heal_target,'monster_dmg':0}
        heal_amount = int(player_maxhp * heal_pct)
        target_p = state['players'].get(heal_target)
        if target_p:
            target_p['hp'] = min(target_p.get('max_hp', player_maxhp),
                                 target_p.get('hp', player_maxhp) + heal_amount)
        result['heal_amount'] = heal_amount
        state.setdefault('event_log',[]).append({'type':'heal',
            'msg':f"💚 {username} uses {skill_name}: +{heal_amount}HP",'ts':int(time.time())})
        # Monster still retaliates if present
        if room and room.get('monster') and room['monster']['hp'] > 0:
            mon = room['monster']
            raw_def = max(0, int(player_def * 0.5))
            m_dmg = max(1, int(mon['atk'] * (100/(100+raw_def)) * (random.random()*0.3+0.85)))
            result['monster_dmg'] = m_dmg
            state['event_log'].append({'type':'combat',
                'msg':f"🩸 {mon['name']}: -{m_dmg} to {username} (while healing)",'ts':int(time.time())})
        p['hp'] = max(0, player_curhp - result.get('monster_dmg', 0))
        p['last_seen'] = int(time.time())
        if len(state['event_log']) > 100: state['event_log'] = state['event_log'][-60:]
        save_ds(db, sid, state)
        return jsonify(result)

    if not room or not room.get('monster') or room['monster']['hp'] <= 0:
        return jsonify({'error':'No monster here'}), 400

    mon = room['monster']
    result = {'player_dmg':0,'monster_dmg':0,'killed':False,'crit':False,'boss_dead':False}

    # Player hits monster
    crit = random.random() < min(0.4, 0.05 + player_dex*0.01)
    dmg  = max(1, int(player_atk * (random.random()*0.4+0.8)))
    if is_skill: dmg = int(dmg * skill_mult)
    if crit:     dmg = int(dmg * 1.8)
    mon['hp'] = max(0, mon['hp'] - dmg)
    result['player_dmg'] = dmg
    result['crit'] = crit

    label = f"{username} {'uses '+skill_name if is_skill else 'attacks'}!{' ⚡CRIT!' if crit else ''}"
    log_msg = f"{'⚡' if crit else '⚔'} {username}: -{dmg} to {mon['name']}"
    state.setdefault('event_log',[]).append({
        'type':'combat','msg':log_msg,'ts':int(time.time()),
        'uid':uid,'dmg':dmg,'crit':crit,
        'mon_hp':mon['hp'],'mon_max_hp':mon.get('max_hp',mon['hp']),
    })

    if mon['hp'] <= 0:
        result['killed'] = True
        result['xp_gain']   = mon['xp'] + int(mon['level']*2)
        result['gold_gain'] = mon['gold'][0] + random.randint(0, max(0, mon['gold'][1]-mon['gold'][0]))
        result['loot']      = room.get('loot')
        room['cleared'] = True
        room['monster'] = None
        state['event_log'].append({'type':'kill',
            'msg':f"☠ {mon['name']} slain by {username}! +{result['xp_gain']}XP +{result['gold_gain']}g",
            'ts':int(time.time())})
        if mon.get('is_boss'):
            result['boss_dead'] = True
            state['boss_dead']  = True
            state['stairs_open'] = True
            # Unlock stairs room
            sc = state.get('stairs_coord','')
            if sc in state['rooms']:
                state['rooms'][sc]['locked'] = False
            # Boss always drops Rare loot
            if not room.get('loot'):
                room['loot'] = make_loot(state['floor'], guaranteed=True, force_rare=True)
                result['loot'] = room['loot']
            state['event_log'].append({'type':'boss_dead',
                'msg':f"🏆 BOSS SLAIN by {username}! The stairs are open — descend when ready.",
                'ts':int(time.time())})
    else:
        # Check rage phase — boss attacks harder below 50% HP
        effective_atk = mon['atk']
        rage_thresh = mon.get('rage_threshold', 0)
        if rage_thresh and mon['hp'] <= rage_thresh:
            effective_atk = mon.get('rage_atk', mon['atk'])
            if not mon.get('rage_announced'):
                mon['rage_announced'] = True
                state['event_log'].append({'type':'boss_rage',
                    'msg':f"💢 {mon['name']} ENRAGES — attack power surges!",
                    'ts':int(time.time())})
                result['boss_raged'] = True

        raw_def = max(0, int(player_def * 0.5))
        # Use percentage-based damage formula: atk * (100 / (100 + def))
        # Guarantees minimum damage, no zero-floor walls
        m_dmg = max(1, int(effective_atk * (100 / (100 + raw_def)) * (random.random()*0.3+0.85)))
        result['monster_dmg'] = m_dmg
        state['event_log'].append({'type':'combat',
            'msg':f"🩸 {mon['name']}: -{m_dmg} to {username}",
            'ts':int(time.time())})

    p['hp']       = max(0, player_curhp - result.get('monster_dmg',0))
    p['max_hp']   = player_maxhp
    p['last_seen'] = int(time.time())

    if len(state['event_log']) > 100:
        state['event_log'] = state['event_log'][-60:]

    state['rooms'][pos] = room
    save_ds(db, sid, state)
    return jsonify(result)

@app.route('/api/sessions/<sid>/loot', methods=['POST'])
def take_loot(sid):
    d = request.get_json()
    uid = str(d.get('user_id'))
    db = get_db()
    state = get_ds(db, sid)
    if not state: return jsonify({'error':'Not found'}), 404
    p = state['players'].get(uid)
    if not p: return jsonify({'error':'Not in session'}), 403
    room = state['rooms'].get(p['pos'], {})
    if not room.get('loot'): return jsonify({'loot':None})
    if room.get('loot_taken',{}).get(uid): return jsonify({'loot':None,'already_taken':True})
    loot = room['loot']
    room.setdefault('loot_taken',{})[uid] = True
    state['event_log'].append({'type':'loot',
        'msg':f"🎁 {p['username']} found: {loot['icon']} {loot['name']}!",'ts':int(time.time())})
    state['rooms'][p['pos']] = room
    save_ds(db, sid, state)
    return jsonify({'loot':loot})

@app.route('/api/sessions/<sid>/buy', methods=['POST'])
def buy_item(sid):
    d = request.get_json()
    uid = str(d.get('user_id'))
    idx = int(d.get('item_idx', 0))
    db  = get_db()
    state = get_ds(db, sid)
    if not state: return jsonify({'error':'Not found'}), 404
    p = state['players'].get(uid)
    if not p: return jsonify({'error':'Not in session'}), 403
    room = state['rooms'].get(p['pos'], {})
    if room.get('type') != 'merchant': return jsonify({'error':'No merchant here'}), 400
    shop = room.get('shop', [])
    if idx >= len(shop): return jsonify({'error':'Invalid item'}), 400
    return jsonify({'item':shop[idx],'cost':shop[idx]['cost']})

@app.route('/api/sessions/<sid>/roulette', methods=['POST'])
def roulette_spin(sid):
    d   = request.get_json()
    uid = str(d.get('user_id'))
    spin_type = d.get('spin_type')   # 'gold' or 'hp'
    db  = get_db()
    state = get_ds(db, sid)
    if not state: return jsonify({'error':'Not found'}), 404
    p = state['players'].get(uid)
    if not p: return jsonify({'error':'Not in session'}), 403
    room = state['rooms'].get(p['pos'], {})
    if room.get('type') != 'roulette': return jsonify({'error':'No roulette here'}), 400
    if room.get('spun'): return jsonify({'error':'Already spun'}), 400
    if spin_type not in ('gold','hp','skip'): return jsonify({'error':'Invalid spin_type'}), 400

    floor  = state.get('floor', 1)
    result = {'outcome': 'skip', 'reward': 0}

    if spin_type == 'skip':
        room['spun'] = True
        room['spin_result'] = {'type':'skip','icon':'🚶','name':'Passed','reward':0}
        state['rooms'][p['pos']] = room
        save_ds(db, sid, state)
        return jsonify(result)

    # Verify costs
    gold_cost = 10 + floor * 8
    hp_cost_pct = 0.12
    player_hp  = int(d.get('player_hp',  p.get('hp', 50)))
    player_maxhp = int(d.get('player_max_hp', p.get('max_hp', 50)))
    player_gold  = int(d.get('player_gold', 0))

    if spin_type == 'gold' and player_gold < gold_cost:
        return jsonify({'error':'Not enough gold'}), 400
    hp_cost = max(1, int(player_maxhp * hp_cost_pct))
    if spin_type == 'hp' and player_hp <= hp_cost:
        return jsonify({'error':'Not enough HP'}), 400

    # Weighted outcome table
    outcomes = [
        ('gold_windfall', 25), ('item_drop', 20), ('hp_restore', 15),
        ('mp_restore', 10),    ('dmg_buff',  10), ('curse_gold', 12),
        ('curse_hp',    8),
    ]
    # HP spin gives slightly better odds — shift 4% from curses to good outcomes
    if spin_type == 'hp':
        outcomes = [
            ('gold_windfall', 28), ('item_drop', 22), ('hp_restore', 16),
            ('mp_restore', 12),    ('dmg_buff',  10), ('curse_gold',  8),
            ('curse_hp',    4),
        ]
    total_w = sum(w for _,w in outcomes)
    r = random.random() * total_w
    outcome_type = outcomes[-1][0]
    for name, w in outcomes:
        r -= w
        if r <= 0:
            outcome_type = name
            break

    spin_result = {'type': outcome_type, 'cost_type': spin_type}
    reward = 0

    if outcome_type == 'gold_windfall':
        mult = random.uniform(2.5, 5.0)
        base = gold_cost if spin_type == 'gold' else int(player_maxhp * 0.20)
        reward = int(base * mult)
        spin_result.update({'icon':'💰','name':'Gold Windfall','reward':reward,
                            'desc':f'+{reward} gold!'})
    elif outcome_type == 'item_drop':
        loot = make_loot(floor, guaranteed=True)
        spin_result.update({'icon':'⚗️','name':'Cursed Offering','loot':loot,
                            'desc':'A relic of uncertain power.'})
    elif outcome_type == 'hp_restore':
        spin_result.update({'icon':'❤️','name':'Blessing of Life','desc':'All wounds healed.'})
    elif outcome_type == 'mp_restore':
        spin_result.update({'icon':'💙','name':'Font of Power','desc':'Full mana restored.'})
    elif outcome_type == 'dmg_buff':
        spin_result.update({'icon':'🔥','name':'Frenzied State','desc':'+20% dmg for 3 floors.'})
    elif outcome_type == 'curse_gold':
        penalty = gold_cost * 2
        spin_result.update({'icon':'💸','name':'Tax of the Dead','reward':-penalty,
                            'desc':f'Spirits take {penalty} gold.'})
    elif outcome_type == 'curse_hp':
        penalty_pct = 0.20
        penalty = max(1, int(player_maxhp * penalty_pct))
        spin_result.update({'icon':'💀','name':"Death's Touch",'reward':-penalty,
                            'desc':f'The void deals {penalty} damage.'})

    room['spun'] = True
    room['spin_result'] = spin_result
    state['rooms'][p['pos']] = room
    p['last_seen'] = int(time.time())

    state.setdefault('event_log',[]).append({'type':'roulette',
        'msg':f"🎲 {d.get('username','???')} spins the wheel: {spin_result.get('name','?')}!",
        'ts':int(time.time())})
    save_ds(db, sid, state)

    result['outcome'] = outcome_type
    result['spin_result'] = spin_result
    return jsonify(result)

@app.route('/api/sessions/<sid>/update_player', methods=['POST'])
def update_player(sid):
    d = request.get_json()
    uid = str(d.get('user_id'))
    db  = get_db()
    state = get_ds(db, sid)
    if not state: return jsonify({'error':'Not found'}), 404
    p = state['players'].get(uid)
    if not p: return jsonify({'error':'Not in session'}), 403
    if 'hp'     in d: p['hp']     = d['hp']
    if 'max_hp' in d: p['max_hp'] = d['max_hp']
    p['last_seen'] = int(time.time())
    save_ds(db, sid, state)
    return jsonify({'ok':True})

@app.route('/api/sessions/<sid>/new_floor', methods=['POST'])
def new_floor(sid):
    d  = request.get_json()
    fn = int(d.get('floor', 1))
    db = get_db()
    state = get_ds(db, sid)
    if not state: return jsonify({'error':'Not found'}), 404
    if not state.get('stairs_open'): return jsonify({'error':'Boss not dead yet'}), 409
    old_players = state.get('players', {})
    state = generate_dungeon(fn)
    for pid, pdata in old_players.items():
        pdata['pos'] = '0,0'
        pdata['last_seen'] = int(time.time())
        state['players'][pid] = pdata
    state['event_log'].append({'type':'floor','msg':f"🪜 The party descends to floor {fn}.",'ts':int(time.time())})
    save_ds(db, sid, state)
    return jsonify({'ok':True,'floor':fn})

@app.route('/api/sessions/<sid>/close', methods=['POST'])
def close_session(sid):
    db = get_db()
    db.execute("UPDATE sessions SET state='closed' WHERE id=?",(sid,))
    db.commit()
    return jsonify({'ok':True})

# ═══════════════════════════════════════════════════════
# PRESENCE & CHAT
# ═══════════════════════════════════════════════════════

@app.route('/api/online', methods=['GET'])
def get_online():
    db = get_db()
    cutoff = int(time.time()) - 600  # active in last 10 minutes
    rows = db.execute("""
        SELECT u.username, c.id as char_id, c.name as char_name, c.class,
               c.level, c.floor, c.hp, c.max_hp, c.alive,
               s.id as session_id, s.state as session_state
        FROM characters c
        JOIN users u ON c.user_id = u.id
        LEFT JOIN sessions s ON (s.host_char_id = c.id OR s.guest_char_id = c.id)
                             AND s.state = 'active'
                             AND s.updated_at > strftime('%s','now') - 180
        WHERE c.updated_at > ? AND c.alive = 1
        ORDER BY c.updated_at DESC
    """, (cutoff,)).fetchall()
    return jsonify([row_to_dict(r) for r in rows])

@app.route('/api/chat', methods=['GET'])
def get_chat():
    since = int(request.args.get('since', 0))
    db = get_db()
    rows = db.execute(
        "SELECT * FROM chat WHERE ts > ? ORDER BY ts ASC LIMIT 50", (since,)
    ).fetchall()
    return jsonify([row_to_dict(r) for r in rows])

@app.route('/api/chat', methods=['POST'])
def post_chat():
    d = request.get_json() or {}
    username = (d.get('username') or '?')[:20]
    message  = (d.get('message') or '').strip()[:200]
    if not message:
        return jsonify({'error': 'empty'}), 400
    db = get_db()
    db.execute("INSERT INTO chat(username,message) VALUES(?,?)", (username, message))
    db.commit()
    return jsonify({'ok': True})

# ═══════════════════════════════════════════════════════
# PET ROUTES
# ═══════════════════════════════════════════════════════

@app.route('/api/pets/bound', methods=['GET'])
def pets_bound():
    """Lightweight check: is this pet bound, and to whom?"""
    pet_id = request.args.get('pet_id')
    if not pet_id:
        return jsonify({'error': 'pet_id required'}), 400
    db = get_db()
    row = db.execute(
        'SELECT pb.char_id, c.name FROM pet_bindings pb '
        'JOIN characters c ON c.id=pb.char_id '
        'WHERE pb.pet_id=? LIMIT 1', (pet_id,)
    ).fetchone()
    if row:
        return jsonify({'bound': True, 'char_name': row['name']})
    return jsonify({'bound': False, 'char_name': ''})

@app.route('/api/pets/status', methods=['GET'])
def pets_status():
    db = get_db()
    _ensure_pets(db)
    result = {}
    for pid in PET_DEFS:
        pet = db.execute('SELECT * FROM pets WHERE id=?',(pid,)).fetchone()
        if not pet: continue
        pet = dict(pet)
        pending = json.loads(pet.get('pending_delivery') or '[]')
        result[pid] = {
            'id':           pid,
            'display_name': pet['display_name'],
            'class_name':   pet['class_name'],
            'icon':         pet['icon'],
            'online':       bool(pet['online']),
            'level':        pet['level'],
            'floor':        pet['floor'],
            'hp':           pet['hp'],
            'max_hp':       pet['max_hp'],
            'pending_count':len(pending),
            'last_seen':    pet['last_seen'],
        }
    return jsonify(result)

@app.route('/api/pets/claim', methods=['POST'])
def pets_claim():
    """Bind a character to a pet using the pet's PIN."""
    data    = request.get_json(force=True)
    char_id = data.get('char_id')
    pet_id  = data.get('pet_id')
    pin     = str(data.get('pin',''))
    if not all([char_id, pet_id, pin]):
        return jsonify({'error':'char_id, pet_id, pin required'}), 400

    db  = get_db()
    _ensure_pets(db)
    pet = db.execute('SELECT * FROM pets WHERE id=?',(pet_id,)).fetchone()
    if not pet:
        return jsonify({'error':'Unknown pet'}), 404
    if str(dict(pet)['claim_pin']) != pin:
        return jsonify({'error':'Invalid PIN'}), 403

    char = db.execute('SELECT id FROM characters WHERE id=?',(char_id,)).fetchone()
    if not char:
        return jsonify({'error':'Character not found'}), 404

    # Block if already bound to a different character
    existing = db.execute(
        'SELECT char_id FROM pet_bindings WHERE pet_id=? AND char_id!=?',
        (pet_id, char_id)
    ).fetchone()
    if existing:
        return jsonify({'error':'This companion is already bound to another hero'}), 403

    db.execute("""INSERT OR IGNORE INTO pet_bindings(pet_id,char_id)
                  VALUES(?,?)""", (pet_id, char_id))
    db.commit()
    return jsonify({'ok': True, 'pet_id': pet_id,
                    'display_name': dict(pet)['display_name']})

@app.route('/api/pets/unbind', methods=['POST'])
def pets_unbind():
    data    = request.get_json(force=True)
    char_id = data.get('char_id')
    pet_id  = data.get('pet_id')
    pin     = str(data.get('pin',''))
    if not all([char_id, pet_id, pin]):
        return jsonify({'error':'char_id, pet_id, pin required'}), 400
    db = get_db()
    _ensure_pets(db)
    pet = db.execute('SELECT * FROM pets WHERE id=?',(pet_id,)).fetchone()
    if not pet:
        return jsonify({'error':'Unknown pet'}), 404
    pet = dict(pet)

    # Check if this char is actually bound
    binding = db.execute('SELECT * FROM pet_bindings WHERE pet_id=? AND char_id=?',
                         (pet_id, char_id)).fetchone()
    if not binding:
        return jsonify({'error':'Not bound'}), 400

    # Lockout check
    now = int(time.time())
    locked_until = pet.get('unbind_locked_until', 0) or 0
    if now < locked_until:
        remaining = locked_until - now
        hours   = remaining // 3600
        minutes = (remaining % 3600) // 60
        return jsonify({'error': f'Unbind locked for {hours}h {minutes}m',
                        'locked_until': locked_until}), 403

    # PIN check
    if str(pet['claim_pin']) != pin:
        attempts = (pet.get('unbind_attempts', 0) or 0) + 1
        if attempts >= 5:
            lock_ts = now + 86400  # 24-hour lockout
            db.execute('UPDATE pets SET unbind_attempts=0, unbind_locked_until=? WHERE id=?',
                       (lock_ts, pet_id))
            db.commit()
            return jsonify({'error': 'Too many failed attempts. Unbind locked for 24 hours.',
                            'locked_until': lock_ts}), 403
        db.execute('UPDATE pets SET unbind_attempts=? WHERE id=?', (attempts, pet_id))
        db.commit()
        remaining_attempts = 5 - attempts
        return jsonify({'error': f'Wrong PIN. {remaining_attempts} attempt{"s" if remaining_attempts!=1 else ""} remaining.',
                        'attempts_remaining': remaining_attempts}), 403

    # Correct PIN — unbind and reset counters
    db.execute('DELETE FROM pet_bindings WHERE pet_id=? AND char_id=?', (pet_id, char_id))
    db.execute('UPDATE pets SET unbind_attempts=0, unbind_locked_until=0 WHERE id=?', (pet_id,))
    db.commit()
    return jsonify({'ok': True})

@app.route('/api/pets/bindings', methods=['GET'])
def pets_bindings():
    """Return which pets a character is bound to, with pending item counts."""
    char_id = request.args.get('char_id')
    if not char_id:
        return jsonify({'error':'char_id required'}), 400
    db = get_db()
    _ensure_pets(db)
    bindings = db.execute(
        'SELECT * FROM pet_bindings WHERE char_id=?', (char_id,)
    ).fetchall()
    result = {}
    # Build result for pets this character IS bound to
    for b in bindings:
        b = dict(b)
        pet = db.execute('SELECT * FROM pets WHERE id=?',(b['pet_id'],)).fetchone()
        if not pet: continue
        pet = dict(pet)
        pending = json.loads(pet.get('pending_delivery') or '[]')
        unclaimed = [i for i in pending if i.get('generated_at',0) > b['last_claimed']]
        result[b['pet_id']] = {
            'bound': True,
            'last_claimed': b['last_claimed'],
            'unclaimed_count': len(unclaimed),
            'unbind_locked_until': pet.get('unbind_locked_until', 0) or 0,
            'unbind_attempts': pet.get('unbind_attempts', 0) or 0,
        }
    # For unbound pets, report if claimed by someone else
    all_pets = db.execute('SELECT id FROM pets').fetchall()
    for row in all_pets:
        pid = row['id']
        if pid in result:
            continue
        other = db.execute(
            'SELECT char_id FROM pet_bindings WHERE pet_id=? AND char_id!=?',
            (pid, char_id)
        ).fetchone()
        result[pid] = {'bound': False, 'claimed_by_other': other is not None}
    return jsonify(result)

@app.route('/api/pets/deliver', methods=['POST'])
def pets_deliver():
    """Deliver unclaimed pet items to a bound character. Each character claims independently."""
    data    = request.get_json(force=True)
    char_id = data.get('char_id')
    if not char_id:
        return jsonify({'error':'char_id required'}), 400

    db   = get_db()
    char = db.execute('SELECT * FROM characters WHERE id=?',(char_id,)).fetchone()
    if not char:
        return jsonify({'error':'Character not found'}), 404

    inventory = json.loads(dict(char).get('inventory') or '[]')
    delivered = []
    now       = int(time.time())

    bindings = db.execute(
        'SELECT * FROM pet_bindings WHERE char_id=?', (char_id,)
    ).fetchall()

    for b in bindings:
        b = dict(b)
        pid = b['pet_id']
        pet = db.execute('SELECT * FROM pets WHERE id=?',(pid,)).fetchone()
        if not pet: continue
        pet = dict(pet)
        if not pet['online']: continue

        pending  = json.loads(pet.get('pending_delivery') or '[]')
        # Items this character hasn't seen yet
        unseen   = [i for i in pending if i.get('generated_at',0) > b['last_claimed']]
        slots    = 20 - len(inventory)
        to_give  = unseen[:min(3, slots)]

        for item in to_give:
            item = dict(item)
            item['pet_name'] = pet['display_name']
            delivered.append(item)
            inventory.append(item)

        if to_give:
            db.execute('UPDATE pet_bindings SET last_claimed=? WHERE pet_id=? AND char_id=?',
                       (now, pid, char_id))

    if delivered:
        db.execute("UPDATE characters SET inventory=?,updated_at=strftime('%s','now') WHERE id=?",
                   (json.dumps(inventory), char_id))
        db.commit()

    return jsonify({'delivered': delivered})

# ═══════════════════════════════════════════════════════
# PET COMPANION — HERO SYNC ENDPOINTS
# ═══════════════════════════════════════════════════════

_PET_USERNAMES = {"raven-warden", "scarab-courier"}

@app.route('/api/pets/heartbeat', methods=['POST'])
def pets_heartbeat():
    """Receive device stats from Raven/Scarab and store on pet row."""
    data   = request.get_json(force=True)
    pet_id = data.get('pet_id')
    if pet_id not in PET_DEFS:
        return jsonify({'error':'unknown pet'}), 400
    db = get_db()
    _ensure_pets(db)
    # Store device stats as JSON in pending state; update last_seen
    try:
        db.execute("""UPDATE pets SET last_seen=?, online=1,
                      cpu_temp=?, wifi_clients=? WHERE id=?""",
                   (int(time.time()),
                    float(data.get('cpu_temp', 0)),
                    int(data.get('wifi_clients', 0)),
                    pet_id))
        db.commit()
    except Exception:
        # cpu_temp / wifi_clients columns may not exist on old DB — add them
        try:
            db.execute("ALTER TABLE pets ADD COLUMN cpu_temp REAL DEFAULT 0")
            db.execute("ALTER TABLE pets ADD COLUMN wifi_clients INTEGER DEFAULT 0")
            db.commit()
            db.execute("UPDATE pets SET last_seen=?,online=1,cpu_temp=?,wifi_clients=? WHERE id=?",
                       (int(time.time()), float(data.get('cpu_temp',0)),
                        int(data.get('wifi_clients',0)), pet_id))
            db.commit()
        except Exception:
            pass
    return jsonify({'ok': True})

@app.route('/api/pets/active_heroes', methods=['GET'])
def pets_active_heroes():
    db  = get_db()
    cutoff = int(time.time()) - 300
    rows = db.execute(
        "SELECT c.*, u.username FROM characters c JOIN users u ON c.user_id=u.id "
        "WHERE c.updated_at > ? AND c.alive=1",
        (cutoff,)
    ).fetchall()
    heroes = []
    for r in rows:
        r = dict(r)
        if r['username'] in _PET_USERNAMES:
            continue
        heroes.append({
            'username':   r['username'],
            'char_name':  r['name'],
            'level':      r['level'],
            'floor':      r['floor'],
            'char_class': r['class'],
        })
    return jsonify(heroes)

@app.route('/api/pets/play/sync', methods=['POST'])
def pets_play_sync():
    db   = get_db()
    body = request.get_json(force=True) or {}

    pet_id     = body.get('pet_id', '')
    new_level  = int(body.get('level', 1))
    new_floor  = int(body.get('area', 1))
    new_hp     = int(body.get('hp', 0))
    new_max_hp = int(body.get('max_hp', 0))
    xp_gained  = int(body.get('xp_gained', 0))
    gold_gained= int(body.get('gold_gained', 0))
    loot_name  = body.get('loot_name', '')
    loot_icon  = body.get('loot_icon', '')

    pet = db.execute('SELECT * FROM pets WHERE id=?', (pet_id,)).fetchone()
    if not pet:
        return jsonify({'ok': False, 'error': 'pet not found'}), 404
    pet = dict(pet)

    level   = new_level
    floor   = new_floor
    hp      = new_hp if new_hp > 0 else pet['hp']
    max_hp  = new_max_hp if new_max_hp > 0 else pet['max_hp']
    atk     = pet['atk']
    def_    = pet['def_val']
    xp      = pet['xp']
    xp_next = pet['xp_next']

    if xp_gained > 0 or gold_gained > 0:
        xp += xp_gained
        while xp >= xp_next and level < 30:
            xp      -= xp_next
            level   += 1
            xp_next  = int(100 * (1.4 ** (level - 1)))
            max_hp   = int(max_hp * 1.04)
            atk     += 1
            if level % 3 == 0:
                def_ += 1
        hp = min(max_hp, hp)

    pending = json.loads(pet.get('pending_delivery') or '[]')
    if loot_name:
        if len(pending) < 10:
            pending.append({
                'id':           f"quest_{int(time.time())}_{random.randint(1000,9999)}",
                'name':         loot_name,
                'icon':         loot_icon or '⚔',
                'rarity':       'magic',
                'type':         'weapon',
                'slot':         'weapon',
                'stats':        {'atk': level * 2},
                'from_pet':     True,
                'generated_at': int(time.time()),
            })

    db.execute(
        "UPDATE pets SET level=?,xp=?,xp_next=?,floor=?,hp=?,max_hp=?,atk=?,def_val=?,"
        "pending_delivery=?,online=1,last_seen=strftime('%s','now') WHERE id=?",
        (level, xp, xp_next, floor, hp, max_hp, atk, def_, json.dumps(pending), pet_id)
    )
    db.commit()

    # Return active hero names for companion display
    cutoff = int(time.time()) - 300
    rows = db.execute(
        "SELECT c.name, u.username FROM characters c JOIN users u ON c.user_id=u.id "
        "WHERE c.updated_at > ? AND c.alive=1",
        (cutoff,)
    ).fetchall()
    companion_names = [dict(r)['name'] for r in rows
                       if dict(r)['username'] not in _PET_USERNAMES]
    companion_str = ', '.join(companion_names)

    return jsonify({'ok': True, 'companion': companion_str})

# ═══════════════════════════════════════════════════════
# RAIDS
# ═══════════════════════════════════════════════════════

@app.route('/api/raids', methods=['POST'])
def create_raid():
    d = request.get_json()
    uid, cid, boss_id = d.get('user_id'), d.get('char_id'), d.get('boss_id','warden')
    if boss_id not in RAID_BOSSES:
        return jsonify({'error': 'Unknown boss'}), 400
    db = get_db()
    char = row_to_dict(db.execute('SELECT * FROM characters WHERE id=?', (cid,)).fetchone())
    u    = row_to_dict(db.execute('SELECT username FROM users WHERE id=?', (uid,)).fetchone())
    if not char or not u: return jsonify({'error':'Character not found'}), 404
    # Close any existing open raids from this host
    db.execute("UPDATE raids SET state='closed' WHERE host_user_id=? AND state IN ('lobby','active')", (uid,))
    rid = ''.join(str(random.randint(0,9)) for _ in range(6))
    while db.execute('SELECT id FROM raids WHERE id=?', (rid,)).fetchone():
        rid = ''.join(str(random.randint(0,9)) for _ in range(6))
    entry = _roster_entry(uid, cid, u['username'], char['name'], char['class'],
                          CLASS_ICONS.get(char['class'],'⚔️'), char['hp'], char['max_hp'])
    entry['ready'] = False
    roster = [entry]
    db.execute("INSERT INTO raids(id,host_user_id,boss_id,roster,boss_state,event_log,loot_rolls) VALUES(?,?,?,?,?,?,?)",
               (rid, uid, boss_id, json.dumps(roster), json.dumps({}), json.dumps([]), json.dumps({})))
    db.commit()
    return jsonify({'raid_id': rid, 'boss_id': boss_id})

@app.route('/api/raids/open', methods=['GET'])
def list_open_raids():
    db = get_db()
    cutoff = int(time.time()) - 300
    rows = db.execute("""SELECT r.id,r.boss_id,r.host_user_id,r.roster,r.state,r.created_at,
                                u.username as host_name
                         FROM raids r JOIN users u ON r.host_user_id=u.id
                         WHERE r.state='lobby' AND r.updated_at>? ORDER BY r.created_at DESC""",
                      (cutoff,)).fetchall()
    out = []
    for row in rows:
        row = dict(row)
        roster = json.loads(row['roster'])
        out.append({'id': row['id'], 'boss_id': row['boss_id'], 'host_name': row['host_name'],
                    'player_count': len(roster), 'boss_name': RAID_BOSSES[row['boss_id']]['name'],
                    'boss_icon': RAID_BOSSES[row['boss_id']]['icon']})
    return jsonify(out)

@app.route('/api/raids/<rid>/join', methods=['POST'])
def join_raid(rid):
    d = request.get_json()
    uid, cid = d.get('user_id'), d.get('char_id')
    db = get_db()
    raid = _get_raid_data(db, rid)
    if not raid: return jsonify({'error': 'Raid not found'}), 404
    if raid['state'] != 'lobby': return jsonify({'error': 'Raid already started or closed'}), 409
    if len(raid['roster']) >= 10: return jsonify({'error': 'Raid is full (10/10)'}), 409
    if any(p['uid'] == str(uid) for p in raid['roster']):
        return jsonify({'ok': True, 'raid_id': rid})  # already in
    char = row_to_dict(db.execute('SELECT * FROM characters WHERE id=?', (cid,)).fetchone())
    u    = row_to_dict(db.execute('SELECT username FROM users WHERE id=?', (uid,)).fetchone())
    if not char or not u: return jsonify({'error': 'Not found'}), 404
    entry = _roster_entry(uid, cid, u['username'], char['name'], char['class'],
                          CLASS_ICONS.get(char['class'],'⚔️'), char['hp'], char['max_hp'])
    raid['roster'].append(entry)
    _raid_log(raid, f"⚔ {u['username']} has joined the raid!", 'join')
    _save_raid_data(db, rid, raid)
    return jsonify({'ok': True, 'raid_id': rid})

@app.route('/api/raids/<rid>', methods=['GET'])
def get_raid(rid):
    db = get_db()
    raid = _get_raid_data(db, rid)
    if not raid: return jsonify({'error': 'Not found'}), 404
    out = dict(raid)
    out['boss_def'] = {k: v for k, v in RAID_BOSSES.get(raid['boss_id'], {}).items() if k != 'phases'}
    out['boss_def']['phases'] = RAID_BOSSES.get(raid['boss_id'], {}).get('phases', [])
    return jsonify(out)

@app.route('/api/raids/<rid>/ready', methods=['POST'])
def raid_ready(rid):
    d = request.get_json()
    uid = str(d.get('user_id'))
    db = get_db()
    raid = _get_raid_data(db, rid)
    if not raid: return jsonify({'error': 'Not found'}), 404
    p = next((x for x in raid['roster'] if x['uid'] == uid), None)
    if not p: return jsonify({'error': 'Not in raid'}), 403
    p['ready'] = not p['ready']
    _save_raid_data(db, rid, raid)
    return jsonify({'ready': p['ready']})

@app.route('/api/raids/<rid>/start', methods=['POST'])
def start_raid(rid):
    d = request.get_json()
    uid = str(d.get('user_id'))
    db = get_db()
    raid = _get_raid_data(db, rid)
    if not raid: return jsonify({'error': 'Not found'}), 404
    if str(raid['host_user_id']) != uid: return jsonify({'error': 'Only host can start'}), 403
    if raid['state'] != 'lobby': return jsonify({'error': 'Already started'}), 409
    if len(raid['roster']) < 1: return jsonify({'error': 'Need at least 1 player'}), 400
    n = len(raid['roster'])
    raid['boss_state'] = _make_boss_state(raid['boss_id'], n)
    raid['state'] = 'active'
    bd = RAID_BOSSES[raid['boss_id']]
    _raid_log(raid, f"⚔ The raid begins! {n} heroes face {bd['name']} {bd['icon']}", 'start')
    _save_raid_data(db, rid, raid)
    return jsonify({'ok': True})

@app.route('/api/raids/<rid>/attack', methods=['POST'])
def raid_attack(rid):
    d = request.get_json()
    uid          = str(d.get('user_id'))
    player_atk   = int(d.get('player_atk', 10))
    player_def   = int(d.get('player_def', 5))
    player_maxhp = int(d.get('player_max_hp', 100))
    player_curhp = int(d.get('player_current_hp', 100))
    player_dex   = int(d.get('player_dex', 6))
    is_skill     = bool(d.get('is_skill'))
    skill_mult   = float(d.get('skill_dmg_mult', 1.0))
    skill_name   = d.get('skill_name', 'Ability')
    username     = d.get('username', '?')
    action       = d.get('action', 'attack')  # 'attack'|'cleanse'|'potion'

    db = get_db()
    raid = _get_raid_data(db, rid)
    if not raid: return jsonify({'error': 'Raid not found'}), 404
    if raid['state'] != 'active': return jsonify({'error': 'Raid not active'}), 409

    roster = raid['roster']
    boss   = raid['boss_state']
    bd     = RAID_BOSSES[raid['boss_id']]
    n      = len(roster)

    me = next((p for p in roster if p['uid'] == uid), None)
    if not me: return jsonify({'error': 'Not in raid'}), 403
    if me['dead']: return jsonify({'error': 'You are dead'}), 409

    # Sync HP from client
    me['hp']     = min(player_curhp, player_maxhp)
    me['max_hp'] = player_maxhp

    result = {'player_dmg': 0, 'boss_dmg_to_me': 0, 'killed': False,
              'mechanics': [], 'phase_changed': False, 'wipe': False, 'victory': False}

    # Handle cleanse action
    if action == 'cleanse':
        if me.get('plagued', 0) > 0:
            me['plagued'] = 0
            _raid_log(raid, f"✨ {username} cleanses the plague!", 'system')
            result['cleansed'] = True
        _save_raid_data(db, rid, raid)
        return jsonify(result)

    # Handle potion action
    if action == 'potion':
        heal = int(me['max_hp'] * 0.35)
        me['hp'] = min(me['max_hp'], me['hp'] + heal)
        _raid_log(raid, f"⚗ {username} uses a Rejuvenation Potion! +{heal} HP", 'system')
        result['healed'] = heal
        _save_raid_data(db, rid, raid)
        return jsonify(result)

    # ── ATTACK ──

    # Boss immunity check
    if boss.get('immune') and boss.get('active_mechanic') not in ('mark_of_doom','frenzy'):
        # Still allow attack but damage goes to shield/devour check, not boss HP
        pass

    # Plague penalty — deal 50% dmg if plagued
    plague_mult = 0.5 if me.get('plagued', 0) > 0 else 1.0
    if me.get('plagued', 0) > 0:
        me['plagued'] -= 1
        if me['plagued'] <= 0:
            me['plagued'] = 0
        # Check spread: if plagued when attacking, spread to 1 random alive unplagued player
        alive_unplagued = [p for p in roster if not p['dead'] and not p.get('plagued') and p['uid'] != uid]
        if alive_unplagued and random.random() < 0.5:
            spread_to = random.choice(alive_unplagued)
            spread_to['plagued'] = 3
            _raid_log(raid, f"🦠 Plague spreads from {username} to {spread_to['username']}!", 'mechanic')
            result['plague_spread'] = spread_to['uid']

    # Compute damage
    crit = random.random() < min(0.35, 0.05 + player_dex * 0.01)
    raw  = max(1, int(player_atk * (random.random() * 0.4 + 0.8) * plague_mult))
    if is_skill: raw = int(raw * skill_mult)
    if crit:     raw = int(raw * 1.8)
    dmg = raw

    devour = boss.get('devour')
    chaos  = boss.get('chaos_shield')
    dps_ch = boss.get('dps_check')

    if boss.get('immune') and chaos:
        # Damage goes to chaos shield break pool
        chaos['done'] += dmg
        chaos['attacks_left'] -= 1
        if chaos['done'] >= chaos['needed']:
            boss['immune'] = False
            boss['chaos_shield'] = None
            boss['active_mechanic'] = None
            _raid_log(raid, f"💥 CHAOS SHIELD BROKEN by {username}! Boss vulnerable — deal double damage for 3 attacks!", 'mechanic')
            boss['vulnerability'] = 3  # next 3 attacks deal 2x
        elif chaos['attacks_left'] <= 0:
            boss['immune'] = False
            boss['chaos_shield'] = None
            boss['active_mechanic'] = None
            # Punishment: all take 30% max HP
            for p in roster:
                if not p['dead']:
                    pen = int(p['max_hp'] * 0.30)
                    p['hp'] = max(0, p['hp'] - pen)
                    if p['hp'] <= 0: p['dead'] = True
            _raid_log(raid, '💀 CHAOS SHIELD expired! All heroes take 30% max HP in punishment!', 'mechanic')
        result['player_dmg'] = 0  # no direct boss dmg
    elif boss.get('immune'):
        result['player_dmg'] = 0
    else:
        # Vulnerability bonus
        if boss.get('vulnerability', 0) > 0:
            dmg = int(dmg * 2)
            boss['vulnerability'] -= 1

        boss['hp'] = max(0, boss['hp'] - dmg)
        boss['attack_count'] = boss.get('attack_count', 0) + 1
        result['player_dmg'] = dmg

        label = f"{'⚡CRIT! ' if crit else ''}{'['+skill_name+'] ' if is_skill else ''}"
        _raid_log(raid, f"⚔ {username} {label}→ -{dmg} to {boss['name']}{' 🤢(plagued)' if plague_mult<1 else ''}", 'combat')

        # Devour free-HP tracking
        if devour and devour.get('hp_done', 0) < devour.get('free_hp_needed', 9999):
            devour['hp_done'] = devour.get('hp_done', 0) + dmg
            if devour['hp_done'] >= devour['free_hp_needed']:
                boss['devour'] = None
                boss['active_mechanic'] = None
                _raid_log(raid, f"✨ {devour['username']} is freed from Apophis! Combined damage broke the grip!", 'mechanic')

        # DPS check tracking
        if dps_ch:
            dps_ch['done'] += dmg
            dps_ch['attacks_left'] -= 1
            if dps_ch['done'] >= dps_ch['needed']:
                boss['dps_check'] = None
                if boss['active_mechanic'] == 'adds_dps_check':
                    boss['active_mechanic'] = None
                _raid_log(raid, '✅ DPS CHECK PASSED! Divine punishment averted.', 'mechanic')
            elif dps_ch['attacks_left'] <= 0:
                boss['dps_check'] = None
                # Punishment: all take 40% max HP
                for p in roster:
                    if not p['dead']:
                        pen = int(p['max_hp'] * 0.40)
                        p['hp'] = max(0, p['hp'] - pen)
                        if p['hp'] <= 0: p['dead'] = True
                _raid_log(raid, '💀 DPS CHECK FAILED! Divine wrath strikes all heroes for 40% HP!', 'mechanic')

        # Rebirth check tracking
        if boss.get('rebirth_check'):
            boss['rebirth_check']['attacks_left'] -= 1
            if boss['rebirth_check']['attacks_left'] <= 0:
                boss['rebirth_check'] = None
                if boss['hp'] > 0:
                    # annihilation
                    for p in roster:
                        if not p['dead']:
                            p['hp'] = max(0, int(p['hp'] * 0.10))
                            if p['hp'] <= 0: p['dead'] = True
                    _raid_log(raid, '💀 REBIRTH WINDOW EXPIRED! The Pharaoh annihilates the raid!', 'mechanic')
                    boss['active_mechanic'] = None

        # Check kill
        if boss['hp'] <= 0:
            boss['hp'] = 0
            raid['state'] = 'victory'
            # Generate loot for each alive player
            floor = max(p.get('char_id', 1) for p in roster)
            loot_rolls = {}
            for p in roster:
                if not p['dead']:
                    item = _raid_make_loot(int(boss['max_hp'] / max(1, len(roster)) / 100), raid['boss_id'])
                    loot_rolls[p['uid']] = item
            raid['loot_rolls'] = loot_rolls
            _raid_log(raid, f"🏆 {boss['name']} has been SLAIN! Victory! Loot awarded to survivors.", 'victory')
            result['victory'] = True

    # Fire mechanic triggers (only if boss still alive)
    if boss['hp'] > 0:
        fired = _resolve_mechanic_triggers(boss, roster, bd, n, lambda msg, t='': _raid_log(raid, msg, t))
        result['mechanics'] = fired

    # Boss retaliation against THIS player (if not immune, boss alive, and not victory)
    if not result['victory'] and boss['hp'] > 0 and not boss.get('immune'):
        effective_atk = boss['atk']
        if boss.get('phase', 1) >= 4:
            effective_atk = int(effective_atk * 1.35)
        # Mark doubles damage to marked player
        marked_mult = 2.0 if me.get('marked', 0) > 0 else 1.0
        if me.get('marked', 0) > 0:
            me['marked'] -= 1
        # Venom stacks add flat damage
        venom_bonus = boss.get('venom_stacks', 0) * 8
        base_boss_dmg = max(1, int(effective_atk * (100 / (100 + max(0, player_def // 2))) * (random.random() * 0.25 + 0.875)))
        boss_dmg = max(1, int((base_boss_dmg + venom_bonus) * marked_mult))
        me['hp'] = max(0, me['hp'] - boss_dmg)
        result['boss_dmg_to_me'] = boss_dmg
        if marked_mult > 1:
            _raid_log(raid, f"🩸 {boss['name']} JUDGMENT STRIKES {username}: -{boss_dmg} (MARKED!)", 'combat')
        else:
            _raid_log(raid, f"🩸 {boss['name']} retaliates on {username}: -{boss_dmg}", 'combat')

        # Devoured player still alive, takes extra tick dmg
        if devour and devour.get('uid') == uid:
            devour['attacks_left'] = devour.get('attacks_left', 0) - 1
            if devour.get('attacks_left', 0) <= 0:
                boss['devour'] = None
                boss['active_mechanic'] = None
                # Penalty to devoured player: 50% HP loss
                penalty = int(me['max_hp'] * 0.50)
                me['hp'] = max(1, me['hp'] - penalty)
                _raid_log(raid, f"😰 {username} was devoured too long — expelled at 50% HP!", 'mechanic')

    # Check death
    if me['hp'] <= 0 and not me['dead']:
        me['dead'] = True
        me['hp'] = 0
        _raid_log(raid, f"💀 {username} has fallen!", 'death')
        result['dead'] = True

    # Check wipe
    if _check_wipe(roster) and raid['state'] == 'active':
        raid['state'] = 'wiped'
        _raid_log(raid, f"💀 WIPE — All heroes have fallen before {boss['name']}!", 'wipe')
        result['wipe'] = True

    _save_raid_data(db, rid, raid)
    return jsonify(result)

@app.route('/api/raids/<rid>/attack_add', methods=['POST'])
def raid_attack_add(rid):
    d = request.get_json()
    uid        = str(d.get('user_id'))
    add_id     = int(d.get('add_id', 1))
    player_atk = int(d.get('player_atk', 10))
    player_def = int(d.get('player_def', 5))
    player_dex = int(d.get('player_dex', 6))
    player_maxhp = int(d.get('player_max_hp', 100))
    player_curhp = int(d.get('player_current_hp', 100))
    is_skill   = bool(d.get('is_skill'))
    skill_mult = float(d.get('skill_dmg_mult', 1.0))
    username   = d.get('username', '?')

    db  = get_db()
    raid = _get_raid_data(db, rid)
    if not raid: return jsonify({'error': 'Not found'}), 404
    if raid['state'] != 'active': return jsonify({'error': 'Not active'}), 409

    boss   = raid['boss_state']
    roster = raid['roster']
    me     = next((p for p in roster if p['uid'] == uid), None)
    if not me or me['dead']: return jsonify({'error': 'Cannot act'}), 403

    me['hp'] = min(player_curhp, player_maxhp)
    me['max_hp'] = player_maxhp

    add = next((a for a in boss.get('adds', []) if a['id'] == add_id), None)
    if not add: return jsonify({'error': 'Add not found'}), 404

    crit = random.random() < min(0.35, 0.05 + player_dex * 0.01)
    raw  = max(1, int(player_atk * (random.random() * 0.4 + 0.8)))
    if is_skill: raw = int(raw * skill_mult)
    if crit:     raw = int(raw * 1.8)

    add['hp'] = max(0, add['hp'] - raw)
    add_killed = add['hp'] <= 0
    _raid_log(raid, f"{'⚡CRIT! ' if crit else ''}⚔ {username} → -{raw} to {add['name']}{'  ☠ SLAIN!' if add_killed else ''}", 'combat')

    if add_killed:
        boss['adds'] = [a for a in boss['adds'] if a['id'] != add_id]
        if not boss['adds'] and boss.get('immune'):
            boss['immune'] = False
            boss['active_mechanic'] = None
            _raid_log(raid, '✅ All adds slain! Boss is vulnerable again!', 'mechanic')

    # DPS check counts add damage too
    if boss.get('dps_check'):
        boss['dps_check']['done'] += raw
        boss['dps_check']['attacks_left'] -= 1
        if boss['dps_check']['done'] >= boss['dps_check']['needed']:
            boss['dps_check'] = None
            _raid_log(raid, '✅ DPS CHECK PASSED!', 'mechanic')
        elif boss['dps_check']['attacks_left'] <= 0:
            boss['dps_check'] = None
            for p in roster:
                if not p['dead']:
                    p['hp'] = max(0, int(p['hp'] - p['max_hp'] * 0.40))
                    if p['hp'] <= 0: p['dead'] = True
            _raid_log(raid, '💀 DPS CHECK FAILED! All take 40% HP!', 'mechanic')

    # Add retaliates
    if not add_killed:
        add_dmg = max(1, int(add['atk'] * (random.random() * 0.3 + 0.85)))
        me['hp'] = max(0, me['hp'] - add_dmg)
        _raid_log(raid, f"🩸 {add['name']} strikes {username}: -{add_dmg}", 'combat')
        if me['hp'] <= 0 and not me['dead']:
            me['dead'] = True
            _raid_log(raid, f"💀 {username} falls!", 'death')

    result = {'add_dmg': raw, 'add_killed': add_killed, 'boss_dmg_to_me': 0 if add_killed else add_dmg if not add_killed else 0}
    result['boss_dmg_to_me'] = 0 if add_killed else add_dmg

    if _check_wipe(roster):
        raid['state'] = 'wiped'
        _raid_log(raid, f"💀 WIPE!", 'wipe')
        result['wipe'] = True

    _save_raid_data(db, rid, raid)
    return jsonify(result)

@app.route('/api/raids/<rid>/leave', methods=['POST'])
def leave_raid(rid):
    d  = request.get_json()
    uid = str(d.get('user_id'))
    db  = get_db()
    raid = _get_raid_data(db, rid)
    if not raid: return jsonify({'error': 'Not found'}), 404
    raid['roster'] = [p for p in raid['roster'] if p['uid'] != uid]
    if str(raid['host_user_id']) == uid or not raid['roster']:
        raid['state'] = 'closed'
    _save_raid_data(db, rid, raid)
    return jsonify({'ok': True})

# ═══════════════════════════════════════════════════════
# SERVE FRONTEND
# ═══════════════════════════════════════════════════════

@app.route('/')
def index():
    return send_file(os.path.join(os.path.dirname(__file__), 'ladder_slasher.html'))

if __name__ == '__main__':
    init_db()
    # Start pet companion background thread
    t = threading.Thread(target=_pet_loop, daemon=True, name='pet-loop')
    t.start()
    print("="*50)
    print("  Ladder Slasher — Duat Server v2")
    print("  http://192.168.1.5:5000")
    print("  Pet PINs — Raven 🦅: 7743  |  Scarab 🪲: 5151")
    print("="*50)
    app.run(host='0.0.0.0', port=5000, debug=False)
