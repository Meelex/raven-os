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
    # ── Tier 1: Floors 1-5 (Tomb Entrance) ──
    {'name':'Tomb Scarab',         'icon':'🪲','tier':1,'hp':14,'atk':3,'def':0,'xp':8, 'gold':[0,3]},
    {'name':'Desert Rat',          'icon':'🐀','tier':1,'hp':10,'atk':4,'def':0,'xp':6, 'gold':[0,2]},
    {'name':'Sand Viper',          'icon':'🐍','tier':1,'hp':16,'atk':5,'def':1,'xp':10,'gold':[0,4],'status_proc':{'effect':'poison','chance':0.20}},
    {'name':'Jackal Pup',          'icon':'🐺','tier':1,'hp':12,'atk':4,'def':0,'xp':8, 'gold':[0,3]},
    {'name':'Grave Robber',        'icon':'💀','tier':1,'hp':20,'atk':5,'def':1,'xp':12,'gold':[1,5]},
    {'name':'Clay Golem',          'icon':'🗿','tier':1,'hp':28,'atk':4,'def':3,'xp':14,'gold':[1,4]},
    {'name':'Dust Wraith',         'icon':'👻','tier':1,'hp':15,'atk':5,'def':0,'xp':10,'gold':[0,3]},
    {'name':'Scorpion Swarm',      'icon':'🦂','tier':1,'hp':18,'atk':6,'def':0,'xp':12,'gold':[1,4],'status_proc':{'effect':'poison','chance':0.15}},
    # ── Tier 2: Floors 6-10 (Catacombs) ──
    {'name':'Mummified Priest',    'icon':'🧟','tier':2,'hp':35,'atk':8,'def':3,'xp':22,'gold':[2,9]},
    {'name':'Jackal Warrior',      'icon':'🐺','tier':2,'hp':30,'atk':9,'def':2,'xp':20,'gold':[2,8]},
    {'name':'Cobra Guardian',      'icon':'🐍','tier':2,'hp':28,'atk':10,'def':2,'xp':22,'gold':[2,10],'status_proc':{'effect':'poison','chance':0.25}},
    {'name':'Sandstone Sentinel',  'icon':'🗿','tier':2,'hp':45,'atk':7,'def':6,'xp':25,'gold':[3,11]},
    {'name':'Cursed Scribe',       'icon':'🧙','tier':2,'hp':25,'atk':9,'def':2,'xp':18,'gold':[2,8],'status_proc':{'effect':'curse','chance':0.20}},
    {'name':'Canopic Horror',      'icon':'🫙','tier':2,'hp':38,'atk':10,'def':3,'xp':26,'gold':[3,12]},
    {'name':'Bone Stalker',        'icon':'💀','tier':2,'hp':32,'atk':11,'def':2,'xp':24,'gold':[3,11]},
    {'name':'Tomb Spider',         'icon':'🕷️','tier':2,'hp':22,'atk':11,'def':1,'xp':20,'gold':[2,9],'status_proc':{'effect':'bleed','chance':0.18}},
    # ── Tier 3: Floors 11-15 (Underworld Gates) ──
    {'name':'Ammit Spawn',         'icon':'🦛','tier':3,'hp':55,'atk':13,'def':5,'xp':38,'gold':[5,18]},
    {'name':'Serpopard',           'icon':'🐆','tier':3,'hp':48,'atk':15,'def':4,'xp':40,'gold':[5,20]},
    {'name':'Deshret Demon',       'icon':'😈','tier':3,'hp':50,'atk':14,'def':5,'xp':42,'gold':[6,20],'status_proc':{'effect':'burn','chance':0.22}},
    {'name':'Soul Eater',          'icon':'👁','tier':3,'hp':44,'atk':16,'def':3,'xp':44,'gold':[7,22],'status_proc':{'effect':'curse','chance':0.25}},
    {'name':'Obsidian Golem',      'icon':'🗿','tier':3,'hp':70,'atk':10,'def':12,'xp':40,'gold':[6,18]},
    {'name':'Nile Croc Spirit',    'icon':'🐊','tier':3,'hp':60,'atk':13,'def':7,'xp':44,'gold':[6,22]},
    {'name':'Shadow Jackal',       'icon':'🐺','tier':3,'hp':45,'atk':17,'def':3,'xp':46,'gold':[7,22]},
    {'name':'Plague Locust',       'icon':'🦗','tier':3,'hp':38,'atk':14,'def':2,'xp':38,'gold':[5,18],'status_proc':{'effect':'poison','chance':0.30}},
    # ── Tier 4: Floors 16-20 (Duat Depths) ──
    {'name':'Apep Serpent',        'icon':'🐉','tier':4,'hp':80,'atk':22,'def':6,'xp':72,'gold':[12,30],'status_proc':{'effect':'poison','chance':0.30}},
    {'name':'Set Beast',           'icon':'🐗','tier':4,'hp':90,'atk':20,'def':8,'xp':70,'gold':[12,32]},
    {'name':'War Sphinx',          'icon':'🦁','tier':4,'hp':100,'atk':18,'def':12,'xp':76,'gold':[14,34]},
    {'name':'Nephthys Shade',      'icon':'🌑','tier':4,'hp':72,'atk':24,'def':5,'xp':78,'gold':[13,32],'status_proc':{'effect':'curse','chance':0.28}},
    {'name':'Scarab Colossus',     'icon':'🪲','tier':4,'hp':110,'atk':16,'def':14,'xp':72,'gold':[14,30]},
    {'name':'Chaos Elemental',     'icon':'🌀','tier':4,'hp':78,'atk':26,'def':4,'xp':82,'gold':[15,36],'status_proc':{'effect':'burn','chance':0.30}},
    {'name':'Blood Pharaoh',       'icon':'🧛','tier':4,'hp':85,'atk':22,'def':8,'xp':80,'gold':[14,35]},
    {'name':'Abyssal Scorpion',    'icon':'🦂','tier':4,'hp':75,'atk':24,'def':6,'xp':78,'gold':[13,32],'status_proc':{'effect':'bleed','chance':0.25}},
    # ── Tier 5: Floors 21-25 (Divine Realm) ──
    {'name':'Son of Apophis',      'icon':'🐍','tier':5,'hp':130,'atk':28,'def':12,'xp':110,'gold':[22,55],'status_proc':{'effect':'poison','chance':0.35}},
    {"name":"Ra's Fury",           'icon':'☀️','tier':5,'hp':120,'atk':32,'def':10,'xp':118,'gold':[24,58],'status_proc':{'effect':'burn','chance':0.35}},
    {'name':'Sekhmet Avatar',      'icon':'🦁','tier':5,'hp':150,'atk':26,'def':15,'xp':120,'gold':[25,62]},
    {"name":"Thoth's Riddle",      'icon':'📜','tier':5,'hp':110,'atk':28,'def':12,'xp':112,'gold':[22,56],'status_proc':{'effect':'frostbite','chance':0.28}},
    {'name':'Anubis Judge',        'icon':'⚖️','tier':5,'hp':140,'atk':24,'def':18,'xp':122,'gold':[26,62]},
    {'name':'Sobek Champion',      'icon':'🐊','tier':5,'hp':160,'atk':22,'def':16,'xp':118,'gold':[24,60]},
    {'name':'Horus Sentinel',      'icon':'🦅','tier':5,'hp':130,'atk':30,'def':12,'xp':124,'gold':[25,64]},
    {'name':'Osiris Revenant',     'icon':'💚','tier':5,'hp':145,'atk':26,'def':16,'xp':126,'gold':[26,65]},
    # ── Tier 6: Floors 26+ (Infinite Abyss — Mythic variants) ──
    {'name':'Mythic Asp of Chaos', 'icon':'🐍','tier':6,'hp':200,'atk':38,'def':16,'xp':180,'gold':[35,80],'status_proc':{'effect':'poison','chance':0.40}},
    {'name':'Abyssal Ra',          'icon':'☀️','tier':6,'hp':180,'atk':44,'def':14,'xp':190,'gold':[38,88],'status_proc':{'effect':'burn','chance':0.40}},
    {'name':'Void Osiris',         'icon':'💚','tier':6,'hp':220,'atk':36,'def':20,'xp':200,'gold':[40,90]},
    {'name':'Void Sphinx',         'icon':'🦁','tier':6,'hp':240,'atk':32,'def':24,'xp':195,'gold':[38,85]},
    {'name':'Eternal Wraith',      'icon':'👻','tier':6,'hp':175,'atk':46,'def':12,'xp':195,'gold':[38,86],'status_proc':{'effect':'curse','chance':0.35}},
    {'name':'Chaos God Fragment',  'icon':'🌀','tier':6,'hp':185,'atk':48,'def':14,'xp':205,'gold':[42,92],'status_proc':{'effect':'burn','chance':0.45}},
    {'name':'Duat Titan',          'icon':'🗿','tier':6,'hp':280,'atk':28,'def':30,'xp':200,'gold':[40,88]},
    {'name':'The Unnamed God',     'icon':'🌑','tier':6,'hp':190,'atk':50,'def':16,'xp':210,'gold':[45,100],'status_proc':{'effect':'curse','chance':0.40}},
]

LOOT_POOL = [
    # ── Common Weapons ──
    {'name':'Short Sword',  'icon':'🗡️','type':'weapon','rarity':'common','stats':{'atk':5},'slot':'weapon'},
    {'name':'Falchion',     'icon':'🗡️','type':'weapon','rarity':'common','stats':{'atk':7},'slot':'weapon'},
    {'name':'Cudgel',       'icon':'🪓','type':'weapon','rarity':'common','stats':{'atk':6,'str':1},'slot':'weapon'},
    {'name':'Battle Axe',   'icon':'🪓','type':'weapon','rarity':'common','stats':{'atk':8},'slot':'weapon'},
    {'name':'Longbow',      'icon':'🏹','type':'weapon','rarity':'common','stats':{'atk':6,'dex':2},'slot':'weapon'},
    {'name':'Hunting Bow',  'icon':'🏹','type':'weapon','rarity':'common','stats':{'atk':5,'dex':1},'slot':'weapon'},
    # ── Uncommon Weapons ──
    {'name':'Flaming Sword','icon':'🗡️','type':'weapon','rarity':'uncommon','stats':{'atk':10,'str':2},'slot':'weapon'},
    {'name':'Searing Blade','icon':'🗡️','type':'weapon','rarity':'uncommon','stats':{'atk':11,'str':2},'slot':'weapon'},
    {'name':'Phase Dagger', 'icon':'🗡️','type':'weapon','rarity':'uncommon','stats':{'atk':9,'dex':4},'slot':'weapon'},
    {'name':'Staff of Ruin','icon':'🪄','type':'weapon','rarity':'uncommon','stats':{'atk':7,'int':4},'slot':'weapon'},
    # ── Rare Weapons ──
    {'name':'Vampiric Blade','icon':'🗡️','type':'weapon','rarity':'rare','stats':{'atk':14,'vit':3},'slot':'weapon'},
    {'name':'Voidblade',     'icon':'🗡️','type':'weapon','rarity':'rare','stats':{'atk':17,'int':3},'slot':'weapon'},
    {'name':'Titan Maul',    'icon':'🪓','type':'weapon','rarity':'rare','stats':{'atk':22,'str':6},'slot':'weapon'},
    {'name':'Chaos Axe',     'icon':'🪓','type':'weapon','rarity':'rare','stats':{'atk':18,'str':4},'slot':'weapon'},
    # ── Common Armor ──
    {'name':'Leather Armor','icon':'🦺','type':'armor','rarity':'common','stats':{'def':5},'slot':'armor'},
    {'name':'Brigandine',   'icon':'🦺','type':'armor','rarity':'common','stats':{'def':6},'slot':'armor'},
    {'name':'Chain Mail',   'icon':'🛡️','type':'armor','rarity':'common','stats':{'def':8},'slot':'armor'},
    # ── Uncommon Armor ──
    {'name':'Plate of the Fallen','icon':'🛡️','type':'armor','rarity':'uncommon','stats':{'def':12,'vit':3},'slot':'armor'},
    {'name':'Shadow Leathers',    'icon':'🦺','type':'armor','rarity':'uncommon','stats':{'def':8,'dex':4},'slot':'armor'},
    {'name':'Woven Chainmail',    'icon':'🛡️','type':'armor','rarity':'uncommon','stats':{'def':10,'dex':3},'slot':'armor'},
    {'name':'Crimson Plate',      'icon':'🛡️','type':'armor','rarity':'uncommon','stats':{'def':13,'str':3},'slot':'armor'},
    # ── Rare Armor ──
    {'name':'Runeplate',        'icon':'🛡️','type':'armor','rarity':'rare','stats':{'def':18,'str':2,'vit':4},'slot':'armor'},
    {'name':'Ironhide Cuirass', 'icon':'🛡️','type':'armor','rarity':'rare','stats':{'def':22,'str':3,'vit':5},'slot':'armor'},
    {'name':'Shadowweave',      'icon':'🦺','type':'armor','rarity':'rare','stats':{'def':14,'dex':6,'int':3},'slot':'armor'},
    # ── Common Helmets ──
    {'name':'Linen Headdress',  'icon':'⛑️','type':'helmet','rarity':'common','stats':{'def':3},'slot':'helmet'},
    {'name':'Leather Cap',      'icon':'⛑️','type':'helmet','rarity':'common','stats':{'def':4,'vit':1},'slot':'helmet'},
    # ── Uncommon Helmets ──
    {'name':'Bronze Helm',   'icon':'⛑️','type':'helmet','rarity':'uncommon','stats':{'def':7,'str':2},'slot':'helmet'},
    {'name':'Iron Helm',     'icon':'⛑️','type':'helmet','rarity':'uncommon','stats':{'def':8,'vit':2},'slot':'helmet'},
    {'name':'Mage Hood',     'icon':'⛑️','type':'helmet','rarity':'uncommon','stats':{'def':5,'int':4},'slot':'helmet'},
    # ── Rare Helmets ──
    {'name':'Runic Helm',    'icon':'⛑️','type':'helmet','rarity':'rare','stats':{'def':14,'str':3,'vit':4},'slot':'helmet'},
    {'name':'Shadow Hood',   'icon':'⛑️','type':'helmet','rarity':'rare','stats':{'def':10,'dex':6,'int':3},'slot':'helmet'},
    # ── Common Belts ──
    {'name':'Rope Belt',     'icon':'🪢','type':'belt','rarity':'common','stats':{'vit':2},'slot':'belt'},
    {'name':'Leather Strap', 'icon':'🪢','type':'belt','rarity':'common','stats':{'def':2,'str':1},'slot':'belt'},
    # ── Uncommon Belts ──
    {'name':'Studded Belt',  'icon':'🪢','type':'belt','rarity':'uncommon','stats':{'def':4,'str':3},'slot':'belt'},
    {'name':'Warrior Belt',  'icon':'🪢','type':'belt','rarity':'uncommon','stats':{'vit':4,'str':3},'slot':'belt'},
    {'name':'Arcane Sash',   'icon':'🪢','type':'belt','rarity':'uncommon','stats':{'int':4,'mp':8},'slot':'belt'},
    # ── Rare Belts ──
    {'name':'Battle Sash',   'icon':'🪢','type':'belt','rarity':'rare','stats':{'def':8,'str':4,'vit':5},'slot':'belt'},
    {'name':'Mage Sash',     'icon':'🪢','type':'belt','rarity':'rare','stats':{'int':6,'mp':15},'slot':'belt'},
    # ── Common Boots ──
    {'name':'Sandals',       'icon':'👢','type':'boots','rarity':'common','stats':{'dex':2},'slot':'boots'},
    {'name':'Leather Boots', 'icon':'👢','type':'boots','rarity':'common','stats':{'def':3,'dex':1},'slot':'boots'},
    # ── Uncommon Boots ──
    {'name':'Swift Boots',   'icon':'👢','type':'boots','rarity':'uncommon','stats':{'dex':5,'def':3},'slot':'boots'},
    {'name':'Iron Greaves',  'icon':'👢','type':'boots','rarity':'uncommon','stats':{'def':7,'vit':3},'slot':'boots'},
    {'name':'Scout Boots',   'icon':'👢','type':'boots','rarity':'uncommon','stats':{'dex':5,'int':3},'slot':'boots'},
    # ── Rare Boots ──
    {'name':'Shadow Boots',  'icon':'👢','type':'boots','rarity':'rare','stats':{'dex':8,'def':6,'int':2},'slot':'boots'},
    {'name':'Titan Greaves', 'icon':'👢','type':'boots','rarity':'rare','stats':{'def':12,'vit':5,'str':3},'slot':'boots'},
    # ── Common Gloves ──
    {'name':'Cloth Wraps',   'icon':'🧤','type':'gloves','rarity':'common','stats':{'str':2},'slot':'gloves'},
    {'name':'Leather Gloves','icon':'🧤','type':'gloves','rarity':'common','stats':{'str':2,'dex':1},'slot':'gloves'},
    # ── Uncommon Gloves ──
    {'name':'Fighter Gloves','icon':'🥊','type':'gloves','rarity':'uncommon','stats':{'str':4,'atk':2},'slot':'gloves'},
    {'name':'Archer Gloves', 'icon':'🧤','type':'gloves','rarity':'uncommon','stats':{'dex':5,'atk':2},'slot':'gloves'},
    {'name':'Mage Wraps',    'icon':'🧤','type':'gloves','rarity':'uncommon','stats':{'int':4,'atk':2},'slot':'gloves'},
    # ── Rare Gloves ──
    {'name':'Iron Gauntlets',   'icon':'🥊','type':'gloves','rarity':'rare','stats':{'str':6,'atk':5,'vit':2},'slot':'gloves'},
    {'name':'Shadow Gauntlets', 'icon':'🧤','type':'gloves','rarity':'rare','stats':{'dex':7,'int':3,'atk':4},'slot':'gloves'},
    # ── Common Rings & Amulets ──
    {'name':'Iron Band',        'icon':'💍','type':'ring',  'rarity':'common','stats':{'str':2},'slot':'ring'},
    # ── Uncommon Rings & Amulets ──
    {'name':'Ring of Fury',     'icon':'💍','type':'ring',  'rarity':'uncommon','stats':{'str':3,'atk':2},'slot':'ring'},
    {'name':"Warder's Ring",    'icon':'💍','type':'ring',  'rarity':'uncommon','stats':{'def':2,'vit':3},'slot':'ring'},
    {'name':'Mana Stone',       'icon':'💎','type':'amulet','rarity':'uncommon','stats':{'int':4,'mp':10},'slot':'amulet'},
    {'name':'Mystic Pendant',   'icon':'📿','type':'amulet','rarity':'uncommon','stats':{'int':5,'mp':8},'slot':'amulet'},
    # ── Rare Rings & Amulets ──
    {'name':'Skull Ring',       'icon':'💍','type':'ring',  'rarity':'rare','stats':{'str':3,'dex':3},'slot':'ring'},
    {'name':'Band of Wrath',    'icon':'💍','type':'ring',  'rarity':'rare','stats':{'str':5,'atk':4},'slot':'ring'},
    {'name':'Blood Pendant',    'icon':'📿','type':'amulet','rarity':'rare','stats':{'vit':5,'hp':20},'slot':'amulet'},
    {'name':'Soul Choker',      'icon':'📿','type':'amulet','rarity':'rare','stats':{'vit':6,'int':4,'hp':15},'slot':'amulet'},
    # ── Legendary — 64 class-specific fixed-stat items ──
    # Medjay (fighter)
    {'name':"Khopesh of the Pharaoh's Guard",'icon':'🗡️','type':'weapon','slot':'weapon','rarity':'legendary','class_affinity':'fighter','stats':{'atk':32,'str':8,'vit':4},'level_req':15,'special_effect':"Counter-Strike proc rate +15%; deals 55% dmg back (up from 40%).",'flavor':"Forged in the furnaces of Karnak, carried by those who never retreated."},
    {'name':'Plate of the Eternal Sentinel', 'icon':'🛡️','type':'armor', 'slot':'armor', 'rarity':'legendary','class_affinity':'fighter','stats':{'def':30,'vit':12,'hp':40},'level_req':15,'special_effect':"When Counter-Strike procs, also heal 5% max HP.",'flavor':"Worn by the last guard of Heliopolis, who fought until dawn broke."},
    {'name':'Crown of the Medjay Commander', 'icon':'⛑️','type':'helmet','slot':'helmet','rarity':'legendary','class_affinity':'fighter','stats':{'def':12,'str':6,'vit':6},'level_req':15,'special_effect':"+5% damage for each hit taken in current combat (max 25%).",'flavor':"The Commander's crown never fell — even when he did."},
    {'name':'Seal of the Desert Warden',     'icon':'💍','type':'ring',  'slot':'ring',  'rarity':'legendary','class_affinity':'fighter','stats':{'str':8,'vit':6,'atk':4},'level_req':15,'special_effect':"Reflects 8% of all damage taken back to the attacker.",'flavor':"Three rings were made. Only one survived the sack of Thebes."},
    {'name':'Girdle of Unyielding Iron',     'icon':'🪢','type':'belt',  'slot':'belt',  'rarity':'legendary','class_affinity':'fighter','stats':{'vit':8,'str':4,'def':8},'level_req':15,'special_effect':"+15% max HP. Cannot be reduced below 1 HP once per floor.",'flavor':"Hammered from the iron of a fallen meteor — untouched by time."},
    {'name':'Sandals of the Endless March',  'icon':'👢','type':'boots', 'slot':'boots', 'rarity':'legendary','class_affinity':'fighter','stats':{'str':4,'dex':4,'vit':4},'level_req':15,'special_effect':"First attack each combat deals +25% bonus damage.",'flavor':"These sandals have crossed the Red Land a thousand times."},
    {'name':'Gauntlets of the Iron Fist',    'icon':'🧤','type':'gloves','slot':'gloves','rarity':'legendary','class_affinity':'fighter','stats':{'str':8,'atk':6},'level_req':15,'special_effect':"+15% chance to stun on basic attack.",'flavor':"No shield survives their impact. No enemy survives twice."},
    {'name':'Cartouche of the Warring King', 'icon':'📿','type':'amulet','slot':'amulet','rarity':'legendary','class_affinity':'fighter','stats':{'str':6,'vit':8,'hp':20},'level_req':15,'special_effect':"Counter-Strike proc rate doubled (50%); deals 60% dmg back.",'flavor':"The king's name, inscribed in gold, speaks of endless war."},
    # Kushite (barbarian)
    {'name':'Axe of Bloodied Sands',         'icon':'🪓','type':'weapon','slot':'weapon','rarity':'legendary','class_affinity':'barbarian','stats':{'atk':36,'str':10},'level_req':15,'special_effect':"Bloodlust stacks build twice as fast. Max 8 stacks.",'flavor':"Soaked in sand and gore from a hundred battles at the edge of the world."},
    {'name':'Hide of the War Elephant',      'icon':'🛡️','type':'armor', 'slot':'armor', 'rarity':'legendary','class_affinity':'barbarian','stats':{'def':26,'vit':14,'str':4},'level_req':15,'special_effect':"Deal bonus damage equal to 6% of your max HP on each hit.",'flavor':"Stripped from the last great elephant of the Napatan kings."},
    {'name':'War Mask of Kush',              'icon':'⛑️','type':'helmet','slot':'helmet','rarity':'legendary','class_affinity':'barbarian','stats':{'str':8,'vit':6},'level_req':15,'special_effect':"Bloodlust stacks are never lost between combat rooms.",'flavor':"Those who saw this mask knew they would not see dawn."},
    {'name':'Ring of the Berserker King',    'icon':'💍','type':'ring',  'slot':'ring',  'rarity':'legendary','class_affinity':'barbarian','stats':{'str':10,'atk':6},'level_req':15,'special_effect':"At max Bloodlust stacks, all attacks deal +20% bonus damage.",'flavor':"The ring of a warlord whose army swept from Kush to the Delta."},
    {'name':'Girdle of the War God',         'icon':'🪢','type':'belt',  'slot':'belt',  'rarity':'legendary','class_affinity':'barbarian','stats':{'str':8,'vit':8,'atk':4},'level_req':15,'special_effect':"Primal Rage no longer costs HP. Cleave hits 3 times.",'flavor':"Worn by Apedemak's chosen. War follows in its shadow."},
    {'name':'Stompers of the Warchief',      'icon':'👢','type':'boots', 'slot':'boots', 'rarity':'legendary','class_affinity':'barbarian','stats':{'str':6,'vit':6},'level_req':15,'special_effect':"+20% damage on the first attack of each combat round.",'flavor':"The ground trembles with each step. Enemies tremble more."},
    {'name':'Crushing Fists of Aapep',       'icon':'🧤','type':'gloves','slot':'gloves','rarity':'legendary','class_affinity':'barbarian','stats':{'str':10,'atk':8},'level_req':15,'special_effect':"Basic attacks have 20% chance to stagger (enemy ATK -25% next turn).",'flavor':"Named for the chaos serpent — chaos is all they leave behind."},
    {'name':"Totem of Khnum's Fury",         'icon':'📿','type':'amulet','slot':'amulet','rarity':'legendary','class_affinity':'barbarian','stats':{'str':8,'vit':6,'hp':25},'level_req':15,'special_effect':"All combat skills cost 25% less MP. Berserker's Howl ATK bonus +60%.",'flavor':"Carved by the hands of Khnum himself. The hands tell only war."},
    # Shaduf (rogue)
    {'name':'Fangs of Apophis',              'icon':'🗡️','type':'weapon','slot':'weapon','rarity':'legendary','class_affinity':'rogue','stats':{'atk':28,'dex':14},'level_req':15,'special_effect':"Hemorrhage deals 8% HP/turn (up from 5%). Applies 2 stacks on hit.",'flavor':"Twin blades shaped like the teeth of the destroyer serpent."},
    {'name':'Wrappings of the Shadow Dancer','icon':'🦺','type':'armor', 'slot':'armor', 'rarity':'legendary','class_affinity':'rogue','stats':{'def':16,'dex':10,'str':3},'level_req':15,'special_effect':"+10% dodge chance. Dodge also triggers a counter for 80% DEX damage.",'flavor':"Silence made solid. Movement made art."},
    {'name':'Mask of the Serpent Thief',     'icon':'⛑️','type':'helmet','slot':'helmet','rarity':'legendary','class_affinity':'rogue','stats':{'dex':10,'int':4},'level_req':15,'special_effect':"Shadow Step dodge lasts 2 turns. Counter damage increased to 130%.",'flavor':"Stolen from the treasury of a god. The thief was never caught."},
    {'name':'Coil of the Asp',               'icon':'💍','type':'ring',  'slot':'ring',  'rarity':'legendary','class_affinity':'rogue','stats':{'dex':10,'atk':6},'level_req':15,'special_effect':"Guaranteed critical hit against any poisoned enemy.",'flavor':"The asp that bit the queen? This ring holds its shed skin."},
    {'name':'Sash of Swiftness',             'icon':'🪢','type':'belt',  'slot':'belt',  'rarity':'legendary','class_affinity':'rogue','stats':{'dex':8,'str':4},'level_req':15,'special_effect':"Each consecutive hit adds +8% bonus damage (max 6 stacks per combat).",'flavor':"Move fast. Strike faster. Leave nothing but footprints."},
    {'name':'Shadow-Step Wrappings',         'icon':'👢','type':'boots', 'slot':'boots', 'rarity':'legendary','class_affinity':'rogue','stats':{'dex':12},'level_req':15,'special_effect':"Always acts first in combat. First hit of each room crits.",'flavor':"Those who wore these were said to step between moments."},
    {'name':'Claws of the Night Asp',        'icon':'🧤','type':'gloves','slot':'gloves','rarity':'legendary','class_affinity':'rogue','stats':{'dex':10,'atk':6},'level_req':15,'special_effect':"Basic attacks apply 1 Hemorrhage stack. Stacks up to 4.",'flavor':"One scratch ends empires. The Night Asp knew patience."},
    {'name':'Eye of the Stalker',            'icon':'📿','type':'amulet','slot':'amulet','rarity':'legendary','class_affinity':'rogue','stats':{'dex':8,'int':4,'mp':15},'level_req':15,'special_effect':"Death Mark activates at 50% HP (up from 40%). Damage 3.5x.",'flavor':"It watches. It waits. It never misses."},
    # Sem Priest (mage)
    {'name':'Staff of a Thousand Truths',    'icon':'🪄','type':'weapon','slot':'weapon','rarity':'legendary','class_affinity':'mage','stats':{'atk':24,'int':16},'level_req':15,'special_effect':"Wrath of Ra MP cost -30%. All spells deal +15% damage.",'flavor':"Each knot in this staff contains one truth of the universe."},
    {'name':'Robes of the Final Mystery',    'icon':'🦺','type':'armor', 'slot':'armor', 'rarity':'legendary','class_affinity':'mage','stats':{'def':14,'int':12,'mp':30},'level_req':15,'special_effect':"Arcane Body: ignore 75% enemy armor (up from 50%). +20% spell power.",'flavor':"Written on the inside: the true name of the void."},
    {'name':'Circlet of Thoth',              'icon':'⛑️','type':'helmet','slot':'helmet','rarity':'legendary','class_affinity':'mage','stats':{'int':14,'mp':20},'level_req':15,'special_effect':"Soul Drain returns double MP. INT scaling +10% on all skills.",'flavor':"Thoth placed it on the brow of his chosen scribe."},
    {'name':'Eye of Thoth',                  'icon':'💍','type':'ring',  'slot':'ring',  'rarity':'legendary','class_affinity':'mage','stats':{'int':12,'mp':20},'level_req':15,'special_effect':"All INT-scaled attacks deal +20% bonus damage. +10% INT from all sources.",'flavor':"The all-seeing eye, reduced to a gemstone. Still watching."},
    {'name':'Girdle of the Celestial Scribe','icon':'🪢','type':'belt',  'slot':'belt',  'rarity':'legendary','class_affinity':'mage','stats':{'int':10,'mp':18},'level_req':15,'special_effect':"Frost Chains slow 3 turns (up from 2). Spells cost 1 MP less per level.",'flavor':"Around the waist of heaven's secretary. The scrolls never fall."},
    {'name':'Sandals of the Starwalker',     'icon':'👢','type':'boots', 'slot':'boots', 'rarity':'legendary','class_affinity':'mage','stats':{'int':8,'dex':4,'mp':12},'level_req':15,'special_effect':"When MP > 50%, act first in combat. Spell damage +10% while acting first.",'flavor':"They tread the sky-bridge between Duat and the stars."},
    {'name':'Hands of Heka',                 'icon':'🧤','type':'gloves','slot':'gloves','rarity':'legendary','class_affinity':'mage','stats':{'int':12,'atk':4},'level_req':15,'special_effect':"Sekhmet's Wrath damages all enemies. Arcane Body armor bypass raised to 80%.",'flavor':"These hands wrote the first spell. They never stopped casting."},
    {'name':'Medallion of the Divine Word',  'icon':'📿','type':'amulet','slot':'amulet','rarity':'legendary','class_affinity':'mage','stats':{'int':14,'mp':25},'level_req':15,'special_effect':"MP regenerates 10% of max per floor. All spells deal +20% bonus damage.",'flavor':"The word that created the world hangs from this chain."},
    # Nubian Archer (ranger)
    {'name':'Bow of Neith Reborn',           'icon':'🏹','type':'weapon','slot':'weapon','rarity':'legendary','class_affinity':'ranger','stats':{'atk':30,'dex':14},'level_req':15,'special_effect':"Rain of Arrows fires 5 times (up from 3). Eagle Eye cooldown -2.",'flavor':"Restrung from the sinew of a divine animal. Never misses."},
    {'name':'Hide of the Swift Cheetah',     'icon':'🦺','type':'armor', 'slot':'armor', 'rarity':'legendary','class_affinity':'ranger','stats':{'def':18,'dex':10,'str':4},'level_req':15,'special_effect':"+12% dodge. Critical hit damage multiplier +0.5x.",'flavor':"Stripped from Mafdet's companion. Speed inherited."},
    {'name':'Feathered Crown of Neith',      'icon':'⛑️','type':'helmet','slot':'helmet','rarity':'legendary','class_affinity':'ranger','stats':{'dex':10,'int':4},'level_req':15,'special_effect':"Arrow Storm fires 1 extra arrow. Keen Eye critical damage +40%.",'flavor':"The Mother of Gods blessed the archer who wore this. Then blessed them again."},
    {'name':'Band of the Desert Wind',       'icon':'💍','type':'ring',  'slot':'ring',  'rarity':'legendary','class_affinity':'ranger','stats':{'dex':12,'atk':4},'level_req':15,'special_effect':"First attack each combat always crits. Crit damage multiplier +50%.",'flavor':"The desert wind cannot be caught. This ring ensures neither can its wearer."},
    {'name':'Quiver Sash of the Eternal Hunt','icon':'🪢','type':'belt', 'slot':'belt',  'rarity':'legendary','class_affinity':'ranger','stats':{'dex':8,'str':4},'level_req':15,'special_effect':"Carry 2 extra skill charges per combat. Piercing Shot ignores 80% armor.",'flavor':"An endless quiver for the hunter who never stops."},
    {'name':'Boots of the Horizon Runner',   'icon':'👢','type':'boots', 'slot':'boots', 'rarity':'legendary','class_affinity':'ranger','stats':{'dex':12,'vit':4},'level_req':15,'special_effect':"Always acts first. First hit each combat deals +30% bonus damage.",'flavor':"The horizon always retreats. These boots always catch it."},
    {'name':'Falcon Grip Bracers',           'icon':'🧤','type':'gloves','slot':'gloves','rarity':'legendary','class_affinity':'ranger','stats':{'dex':10,'atk':6},'level_req':15,'special_effect':"Eagle Eye cooldown reduced to 3. Piercing Shot DEX scaling x2.5.",'flavor':"Horus lent his grip to the archer who once matched his aim."},
    {'name':"Pendant of the Hunter's Moon",  'icon':'📿','type':'amulet','slot':'amulet','rarity':'legendary','class_affinity':'ranger','stats':{'dex':10,'int':6,'mp':18},'level_req':15,'special_effect':"Piercing Shot ignores 80% armor (up from 50%). Crits restore 5 MP.",'flavor':"The moon sees every hunt. This pendant remembers them all."},
    # Shardana (samurai)
    {'name':'Twin Khopesh of the Sea Peoples','icon':'⚔️','type':'weapon','slot':'weapon','rarity':'legendary','class_affinity':'samurai','stats':{'atk':34,'str':8,'dex':8},'level_req':15,'special_effect':"Dual Onslaught strikes 3 times. Death Blow threshold raised to 40% HP.",'flavor':"One blade to start the battle. The other to end the world."},
    {'name':'Corselet of the Sea Warrior',   'icon':'🛡️','type':'armor', 'slot':'armor', 'rarity':'legendary','class_affinity':'samurai','stats':{'def':22,'str':6,'dex':6},'level_req':15,'special_effect':"Death Blow activates at 40% HP (up from 30%). Damage 2.5x.",'flavor':"Dragged across three seas and a desert. Still unblemished."},
    {'name':'Plumed Helm of the Guard',      'icon':'⛑️','type':'helmet','slot':'helmet','rarity':'legendary','class_affinity':'samurai','stats':{'str':6,'dex':8},'level_req':15,'special_effect':"Critical hits deal 2.5x (up from 2x). +10% crit chance.",'flavor':"The plume has soaked in the blood of kings. It stands proud still."},
    {'name':'Signet of the Sea-King',        'icon':'💍','type':'ring',  'slot':'ring',  'rarity':'legendary','class_affinity':'samurai','stats':{'str':8,'dex':8,'atk':4},'level_req':15,'special_effect':"Void Cut deals 35% enemy HP (up from 25%). Crits reduce Void Cut CD by 1.",'flavor':"The sea-king ruled for a day. The ring rules forever."},
    {'name':'War Sash of the Shardana',      'icon':'🪢','type':'belt',  'slot':'belt',  'rarity':'legendary','class_affinity':'samurai','stats':{'str':8,'dex':6,'atk':4},'level_req':15,'special_effect':"Blade Flash cooldown: 0. Iaijutsu cooldown reduced by 2.",'flavor':"Cinched before a thousand duels. Loosened after none."},
    {'name':'Sea-Striders of the Elite',     'icon':'👢','type':'boots', 'slot':'boots', 'rarity':'legendary','class_affinity':'samurai','stats':{'str':4,'dex':10},'level_req':15,'special_effect':"Dual Onslaught + Blade Flash combo: next attack +30% damage.",'flavor':"They crossed the Great Green without rest. They can cross anything."},
    {'name':'Iron Fists of the Sea King',    'icon':'🧤','type':'gloves','slot':'gloves','rarity':'legendary','class_affinity':'samurai','stats':{'str':8,'dex':8},'level_req':15,'special_effect':"Critical hits deal 2.5x damage. Dual Onslaught gains 1 extra hit.",'flavor':"The sea king crushed iron with his bare hands. These remember."},
    {'name':'Medallion of the Undying Warrior','icon':'📿','type':'amulet','slot':'amulet','rarity':'legendary','class_affinity':'samurai','stats':{'str':6,'dex':6,'vit':6,'hp':20},'level_req':15,'special_effect':"Survive one lethal blow per floor at 1 HP. Triggers Death Blow on surviving.",'flavor':"He fell three times. He rose four. The medallion never shattered."},
    # Heka (cleric)
    {'name':'Sistrum of Divine Healing',     'icon':'🌟','type':'weapon','slot':'weapon','rarity':'legendary','class_affinity':'cleric','stats':{'atk':22,'int':14,'vit':4},'level_req':15,'special_effect':"Mend Wounds heals 40% HP (up from 30%). Healing skills have 0 cooldown.",'flavor':"The rattle of this sistrum calls gods to the healer's side."},
    {'name':'Vestments of the Holy Flame',   'icon':'🦺','type':'armor', 'slot':'armor', 'rarity':'legendary','class_affinity':'cleric','stats':{'def':18,'int':10,'vit':8},'level_req':15,'special_effect':"Divine Light bonus +35% (up from 20%). Purifying Flame heals 20% HP.",'flavor':"The cloth glows faintly. It never needs washing."},
    {'name':'Headband of the Heka Priest',   'icon':'⛑️','type':'helmet','slot':'helmet','rarity':'legendary','class_affinity':'cleric','stats':{'int':12,'vit':6},'level_req':15,'special_effect':"Breath of Osiris shield lasts 5 hits (up from 3). +15% healing power.",'flavor':"Inscribed with every prayer ever whispered in Heliopolis."},
    {'name':'Ankh of Osiris',                'icon':'💍','type':'ring',  'slot':'ring',  'rarity':'legendary','class_affinity':'cleric','stats':{'int':10,'vit':8,'hp':25},'level_req':15,'special_effect':"Searing Light also heals 5% HP. Holy damage ignores 50% armor.",'flavor':"The key of life. It opens every door — even the last one."},
    {'name':'Girdle of the Sacred Flame',    'icon':'🪢','type':'belt',  'slot':'belt',  'rarity':'legendary','class_affinity':'cleric','stats':{'int':8,'vit':8,'hp':20},'level_req':15,'special_effect':"All healing in combat +30%. MP regenerates 8% per combat round.",'flavor':"Blessed at the altar of Ra. The warmth never fades."},
    {'name':'Sandals of the Sacred Threshold','icon':'👢','type':'boots','slot':'boots', 'rarity':'legendary','class_affinity':'cleric','stats':{'int':6,'vit':6,'dex':4},'level_req':15,'special_effect':"When HP falls below 30%, auto-cast Mend Wounds once per combat.",'flavor':"Each step crosses a threshold between life and the beyond."},
    {'name':'Gloves of the Healing Touch',   'icon':'🧤','type':'gloves','slot':'gloves','rarity':'legendary','class_affinity':'cleric','stats':{'int':10,'vit':4},'level_req':15,'special_effect':"Healing skills cost 30% less MP. Mend Wounds +10% HP per cast.",'flavor':"Touch with these, and the wound remembers it was whole."},
    {'name':"Amulet of Ra's Grace",          'icon':'📿','type':'amulet','slot':'amulet','rarity':'legendary','class_affinity':'cleric','stats':{'int':12,'vit':6,'mp':20},'level_req':15,'special_effect':"Searing Light restores 8% HP. Purifying Flame AoE damage +30%.",'flavor':"Ra pressed this amulet into the healer's hand. 'They will need it,' he said."},
    # Tjaty (paladin)
    {'name':'Scepter of Divine Retribution', 'icon':'⚖️','type':'weapon','slot':'weapon','rarity':'legendary','class_affinity':'paladin','stats':{'atk':28,'str':8,'int':6},'level_req':15,'special_effect':"Judgment heals 75% of damage dealt (up from 50%). +20% holy damage.",'flavor':"The scepter of pharaoh's justice. It has never known mercy."},
    {'name':'Plate of the Golden Throne',    'icon':'🛡️','type':'armor', 'slot':'armor', 'rarity':'legendary','class_affinity':'paladin','stats':{'def':32,'str':6,'vit':10},'level_req':15,'special_effect':"Bulwark damage reduction 25% (up from 15%). Heal shields +20% of heal.",'flavor':"Cast from the gold of the throne itself. The king still sits in it."},
    {'name':'Helmet of the Sacred Order',    'icon':'⛑️','type':'helmet','slot':'helmet','rarity':'legendary','class_affinity':'paladin','stats':{'str':6,'vit':8,'int':4},'level_req':15,'special_effect':"Holy Shield blocks 60% damage (up from 40%). After Holy Shield: next atk +60%.",'flavor':"Sacred to the order of Ma'at. Only the worthy can lift it."},
    {'name':"Ring of Pharaoh's Grace",       'icon':'💍','type':'ring',  'slot':'ring',  'rarity':'legendary','class_affinity':'paladin','stats':{'str':6,'vit':8,'int':4},'level_req':15,'special_effect':"All healing effects also restore 5% MP. Smite has 20% chance to stun.",'flavor':"The pharaoh's mercy — for the few who deserved it."},
    {'name':'Girdle of Holy Might',          'icon':'🪢','type':'belt',  'slot':'belt',  'rarity':'legendary','class_affinity':'paladin','stats':{'str':8,'vit':8,'def':6},'level_req':15,'special_effect':"Lay on Hands shield = 25% of heal (up from 10%). +2 heals per combat.",'flavor':"The girdle of the heavenly judiciary. It holds the scales in place."},
    {'name':'Sabatons of the Holy Knight',   'icon':'👢','type':'boots', 'slot':'boots', 'rarity':'legendary','class_affinity':'paladin','stats':{'str':6,'vit':6,'dex':4},'level_req':15,'special_effect':"After Holy Shield activation, next attack deals +70% damage.",'flavor':"The last knight of the sacred order wore these. He never lost."},
    {'name':'Gauntlets of Divine Wrath',     'icon':'🧤','type':'gloves','slot':'gloves','rarity':'legendary','class_affinity':'paladin','stats':{'str':8,'int':4,'atk':4},'level_req':15,'special_effect':"Smite has 20% stun chance. Judgment deals +30% damage on stunned enemies.",'flavor':"To be struck by these is to feel the wrath of heaven."},
    {"name":"Talisman of Ma'at's Balance",   'icon':'📿','type':'amulet','slot':'amulet','rarity':'legendary','class_affinity':'paladin','stats':{'str':6,'vit':8,'int':6,'hp':20},'level_req':15,'special_effect':"Bulwark shield cap 40% of max HP. All skills cost 15% less MP.",'flavor':"Balance. Truth. Judgment. These are not ideals — they are this amulet."},
]

# Mark all legendaries as non-tradeable
for _itm in LOOT_POOL:
    if _itm['rarity'] == 'legendary':
        _itm['tradeable'] = False

# ── RAID LOOT POOL — 64 items (8 classes × 8 slots), only drop from raid bosses ──
RAID_LOOT_POOL = [
    # ── Medjay (fighter) ──
    {'name':'Khopesh of Undying Iron',       'icon':'⚔️','type':'weapon','slot':'weapon','rarity':'raid','tradeable':False,'class_affinity':'fighter','stats':{'atk':55,'str':18,'vit':10},'level_req':15,'proc':{'effect':'bleed','chance':0.25},'special_effect':"Bleed on hit (25%). Counter-Strike now always triggers when hit. Deals 75% back.",'flavor':"Tempered in the blood of a thousand armies. It does not stop."},
    {'name':'Sentinel Plate of the Sun God', 'icon':'🛡️','type':'armor', 'slot':'armor', 'rarity':'raid','tradeable':False,'class_affinity':'fighter','stats':{'def':55,'vit':22,'hp':80,'str':8},'level_req':15,'special_effect':"Counter-Strike procs restore 8% max HP. Immune to the first debuff per floor.",'flavor':"Ra's light is woven into the metal. It does not bend."},
    {'name':'Crown of the Eternal Warden',   'icon':'⛑️','type':'helmet','slot':'helmet','rarity':'raid','tradeable':False,'class_affinity':'fighter','stats':{'def':22,'str':12,'vit':14},'level_req':15,'special_effect':"+8% damage per hit taken (max 40%). Stack survives between rooms.",'flavor':"The Eternal Warden never removed his crown. Enemies learned why."},
    {'name':'Seal of the Deathless Guard',   'icon':'💍','type':'ring',  'slot':'ring',  'rarity':'raid','tradeable':False,'class_affinity':'fighter','stats':{'str':14,'vit':12,'atk':10},'level_req':15,'special_effect':"Reflects 18% of all damage. Counter-Strike chance raised to 45%.",'flavor':"Three seals were made. Two returned. This one did not need to."},
    {'name':'Girdle of the Iron Wall',       'icon':'🪢','type':'belt',  'slot':'belt',  'rarity':'raid','tradeable':False,'class_affinity':'fighter','stats':{'vit':18,'str':10,'def':18},'level_req':15,'special_effect':"+25% max HP. Survive two lethal blows per floor at 1 HP (not once — twice).",'flavor':"Fashioned from the walls of Heliopolis. Those walls never fell."},
    {'name':'March-Boots of Ra',             'icon':'👢','type':'boots', 'slot':'boots', 'rarity':'raid','tradeable':False,'class_affinity':'fighter','stats':{'str':10,'dex':10,'vit':10},'level_req':15,'special_effect':"First 3 attacks each combat deal +35% bonus damage. Acts first against non-bosses.",'flavor':"These crossed the desert in three days. The enemy was not ready."},
    {'name':'Gauntlets of the War God',      'icon':'🧤','type':'gloves','slot':'gloves','rarity':'raid','tradeable':False,'class_affinity':'fighter','stats':{'str':16,'atk':14},'level_req':15,'special_effect':"+30% stun chance on basic attack. Stunned enemies take +20% more damage.",'flavor':"Montu forged these for the only warrior he respected."},
    {'name':'Cartouche of the Deathless King','icon':'📿','type':'amulet','slot':'amulet','rarity':'raid','tradeable':False,'class_affinity':'fighter','stats':{'str':14,'vit':16,'hp':45},'level_req':15,'special_effect':"Counter-Strike guaranteed at below 40% HP. Deals 100% back.",'flavor':"The name inscribed cannot be erased. The king cannot be killed."},
    # ── Kushite (barbarian) ──
    {'name':'Axe of the Last Warchief',      'icon':'🪓','type':'weapon','slot':'weapon','rarity':'raid','tradeable':False,'class_affinity':'barbarian','stats':{'atk':60,'str':20},'level_req':15,'proc':{'effect':'bleed','chance':0.30},'special_effect':"Bleed on hit (30%). Bloodlust stacks build 3x as fast. Max 12 stacks.",'flavor':"Every warchief before him died. He refused to."},
    {'name':'Hide of the War Titan',         'icon':'🛡️','type':'armor', 'slot':'armor', 'rarity':'raid','tradeable':False,'class_affinity':'barbarian','stats':{'def':45,'vit':24,'str':10},'level_req':15,'special_effect':"Deal bonus damage equal to 12% of max HP per hit. Bloodlust never expires.",'flavor':"The titan fell. Its hide did not."},
    {'name':'Death Mask of the Conqueror',   'icon':'⛑️','type':'helmet','slot':'helmet','rarity':'raid','tradeable':False,'class_affinity':'barbarian','stats':{'str':16,'vit':14},'level_req':15,'special_effect':"At max Bloodlust, all attacks are guaranteed crits. Stacks permanent per floor.",'flavor':"No enemy who saw this mask survived to describe it."},
    {'name':"Ring of Khnum's Wrath",         'icon':'💍','type':'ring',  'slot':'ring',  'rarity':'raid','tradeable':False,'class_affinity':'barbarian','stats':{'str':18,'atk':12},'level_req':15,'special_effect':"At max Bloodlust: +50% damage. Cleave strikes every enemy simultaneously.",'flavor':"Khnum shaped the ring from river clay mixed with the blood of gods."},
    {'name':'Girdle of the Primal God',      'icon':'🪢','type':'belt',  'slot':'belt',  'rarity':'raid','tradeable':False,'class_affinity':'barbarian','stats':{'str':16,'vit':16,'atk':10},'level_req':15,'special_effect':"Primal Rage costs no HP. Rampage hits all enemies. Cleave strikes 5 times.",'flavor':"Apedemak himself wore this. His enemies wore nothing afterward."},
    {'name':'Stompers of the God of War',    'icon':'👢','type':'boots', 'slot':'boots', 'rarity':'raid','tradeable':False,'class_affinity':'barbarian','stats':{'str':14,'vit':14},'level_req':15,'special_effect':"+40% damage on the first attack each round. Berserker's Howl duration +2 turns.",'flavor':"Each footfall shook the foundations of Kush. The god of war approves."},
    {'name':'Crushing Fists of Chaos',       'icon':'🧤','type':'gloves','slot':'gloves','rarity':'raid','tradeable':False,'class_affinity':'barbarian','stats':{'str':18,'atk':14},'level_req':15,'special_effect':"40% chance to stagger on basic attack (enemy ATK -40% next turn). Stagger stacks.",'flavor':"Chaos itself takes the form of these fists when they strike."},
    {'name':'Totem of the Storm God',        'icon':'📿','type':'amulet','slot':'amulet','rarity':'raid','tradeable':False,'class_affinity':'barbarian','stats':{'str':16,'vit':12,'hp':50},'level_req':15,'special_effect':"All combat skills cost 0 MP. Berserker's Howl ATK bonus +100%. Never fades.",'flavor':"The storm god gave this to one warrior. The world has not recovered."},
    # ── Shaduf (rogue) ──
    {'name':'Viper Twins of the Void',       'icon':'🗡️','type':'weapon','slot':'weapon','rarity':'raid','tradeable':False,'class_affinity':'rogue','stats':{'atk':48,'dex':24},'level_req':15,'proc':{'effect':'poison','chance':0.35},'special_effect':"Poison on hit (35%). Hemorrhage deals 12% HP/turn. Applies 3 stacks on hit.",'flavor':"Two blades, one shadow. Neither misses."},
    {'name':'Wrappings of the Void Dancer',  'icon':'🦺','type':'armor', 'slot':'armor', 'rarity':'raid','tradeable':False,'class_affinity':'rogue','stats':{'def':28,'dex':18,'str':8},'level_req':15,'special_effect':"+22% dodge. Dodge triggers a counter for 120% DEX damage. Always crits.",'flavor':"The Void Dancer wore these to her final performance. The audience did not applaud."},
    {'name':'Mask of the Unseen',            'icon':'⛑️','type':'helmet','slot':'helmet','rarity':'raid','tradeable':False,'class_affinity':'rogue','stats':{'dex':18,'int':10},'level_req':15,'special_effect':"Shadow Step lasts 3 turns. Counter damage 180%. Hemorrhage spreads on kill.",'flavor':"No one has seen the face beneath this mask. No one alive."},
    {'name':'Coil of the Void Asp',          'icon':'💍','type':'ring',  'slot':'ring',  'rarity':'raid','tradeable':False,'class_affinity':'rogue','stats':{'dex':18,'atk':12},'level_req':15,'special_effect':"Guaranteed crit against poisoned or bleeding enemies. Ambush crits deal 3x.",'flavor':"The void asp coils. The strike is inevitable."},
    {'name':'Sash of Endless Speed',         'icon':'🪢','type':'belt',  'slot':'belt',  'rarity':'raid','tradeable':False,'class_affinity':'rogue','stats':{'dex':16,'str':10},'level_req':15,'special_effect':"Each consecutive hit adds +15% damage (no cap). Consecutive hits never reset on dodge.",'flavor':"The fastest thing in the desert is this sash. The second fastest is regret."},
    {'name':'Shadow-Step Void Wrappings',    'icon':'👢','type':'boots', 'slot':'boots', 'rarity':'raid','tradeable':False,'class_affinity':'rogue','stats':{'dex':22},'level_req':15,'special_effect':"Always acts first. All hits in first 3 turns are guaranteed crits. +30% move speed.",'flavor':"Step between heartbeats. Strike between breaths."},
    {'name':'Claws of the Void Asp',         'icon':'🧤','type':'gloves','slot':'gloves','rarity':'raid','tradeable':False,'class_affinity':'rogue','stats':{'dex':18,'atk':12},'level_req':15,'special_effect':"Basic attacks apply 2 Hemorrhage stacks. Stacks up to 8. Each stack +3% damage.",'flavor':"The Void Asp leaves no witnesses. The claws leave no survivors."},
    {'name':'Eye of the Void Stalker',       'icon':'📿','type':'amulet','slot':'amulet','rarity':'raid','tradeable':False,'class_affinity':'rogue','stats':{'dex':16,'int':8,'mp':30},'level_req':15,'special_effect':"Death Mark activates at 60% HP. Damage 5x. Resets on kill.",'flavor':"It sees through walls, through time, through flesh. It never blinks."},
    # ── Sem Priest (mage) ──
    {'name':'Staff of the First Word',       'icon':'🪄','type':'weapon','slot':'weapon','rarity':'raid','tradeable':False,'class_affinity':'mage','stats':{'atk':42,'int':28},'level_req':15,'proc':{'effect':'frostbite','chance':0.30},'special_effect':"Frostbite on hit (30%). Wrath of Ra costs 0 MP. All spells deal +30% damage.",'flavor':"The first word created the world. This staff speaks it again."},
    {'name':'Robes of the Void Mystery',     'icon':'🦺','type':'armor', 'slot':'armor', 'rarity':'raid','tradeable':False,'class_affinity':'mage','stats':{'def':24,'int':22,'mp':60},'level_req':15,'special_effect':"Arcane Body bypasses 100% armor. +40% spell power. Immune to silence.",'flavor':"The mystery inside these robes is the nature of nothing."},
    {'name':'Circlet of Thoth Ascendant',    'icon':'⛑️','type':'helmet','slot':'helmet','rarity':'raid','tradeable':False,'class_affinity':'mage','stats':{'int':24,'mp':40},'level_req':15,'special_effect':"Soul Drain returns triple MP. All INT scaling +25%. MP regenerates 5% per turn.",'flavor':"Thoth ascended. He left this behind as a reminder of what he left."},
    {'name':'Eye of the Infinite',           'icon':'💍','type':'ring',  'slot':'ring',  'rarity':'raid','tradeable':False,'class_affinity':'mage','stats':{'int':22,'mp':40},'level_req':15,'special_effect':"All INT attacks deal +40% damage. +25% INT from all sources.",'flavor':"The infinite eye sees every possible outcome. It chooses the worst one for enemies."},
    {'name':'Girdle of the Stars',           'icon':'🪢','type':'belt',  'slot':'belt',  'rarity':'raid','tradeable':False,'class_affinity':'mage','stats':{'int':20,'mp':36},'level_req':15,'special_effect':"Frost Chains slow 5 turns. Spells cost no MP while above 70% MP.",'flavor':"The stars are inscribed here in the true celestial order. They grant their power."},
    {'name':'Sandals of the Void Walker',    'icon':'👢','type':'boots', 'slot':'boots', 'rarity':'raid','tradeable':False,'class_affinity':'mage','stats':{'int':16,'dex':10,'mp':24},'level_req':15,'special_effect':"Always acts first. Spell damage +25% while acting first. MP restored on kill.",'flavor':"The void is not empty. These sandals know every corner of it."},
    {'name':'Hands of the First Scribe',     'icon':'🧤','type':'gloves','slot':'gloves','rarity':'raid','tradeable':False,'class_affinity':'mage','stats':{'int':22,'atk':8},'level_req':15,'special_effect':"Sekhmet's Wrath damages all enemies twice. Wrath of Ra guaranteed crit.",'flavor':"These wrote the universe into existence. Still writing."},
    {'name':'Medallion of the Infinite Word', 'icon':'📿','type':'amulet','slot':'amulet','rarity':'raid','tradeable':False,'class_affinity':'mage','stats':{'int':26,'mp':50},'level_req':15,'special_effect':"MP regenerates 20% of max per floor. All spells deal +40% bonus damage.",'flavor':"The word that created the world — and the power to say it again."},
    # ── Nubian Archer (ranger) ──
    {'name':'Bow of Neith Herself',          'icon':'🏹','type':'weapon','slot':'weapon','rarity':'raid','tradeable':False,'class_affinity':'ranger','stats':{'atk':52,'dex':24},'level_req':15,'proc':{'effect':'bleed','chance':0.25},'special_effect':"Bleed on hit (25%). Rain of Arrows fires 8 times. Eagle Eye crit damage 4x.",'flavor':"Neith reclaimed this bow after her chosen archer fell. She carries it still."},
    {'name':'Hide of the Divine Cheetah',    'icon':'🦺','type':'armor', 'slot':'armor', 'rarity':'raid','tradeable':False,'class_affinity':'ranger','stats':{'def':32,'dex':18,'str':10},'level_req':15,'special_effect':"+25% dodge. Critical hit damage multiplier +1.0x. On crit, reset Keen Eye.",'flavor':"Mafdet herself shed this hide. The speed transferred."},
    {'name':'Feathered Crown of the Sky God','icon':'⛑️','type':'helmet','slot':'helmet','rarity':'raid','tradeable':False,'class_affinity':'ranger','stats':{'dex':20,'int':8},'level_req':15,'special_effect':"Arrow Storm fires 3 extra arrows. Keen Eye crit damage +80%. Crit chain on kill.",'flavor':"Horus blessed this crown with the clarity of divine sight. See everything. Hit everything."},
    {'name':'Band of the Eternal Wind',      'icon':'💍','type':'ring',  'slot':'ring',  'rarity':'raid','tradeable':False,'class_affinity':'ranger','stats':{'dex':22,'atk':10},'level_req':15,'special_effect':"First 2 attacks each combat always crit. Crit damage multiplier +100%.",'flavor':"The wind cannot be bound. This ring ensures neither can its bearer."},
    {'name':'Quiver of the Endless Hunt',    'icon':'🪢','type':'belt',  'slot':'belt',  'rarity':'raid','tradeable':False,'class_affinity':'ranger','stats':{'dex':16,'str':10},'level_req':15,'special_effect':"Carry unlimited skill charges per combat. Piercing Shot ignores 100% armor.",'flavor':"The hunt never ends. The quiver never empties."},
    {'name':'Boots of the Sky Runner',       'icon':'👢','type':'boots', 'slot':'boots', 'rarity':'raid','tradeable':False,'class_affinity':'ranger','stats':{'dex':22,'vit':10},'level_req':15,'special_effect':"Always acts first. First 2 hits deal +50% damage. +20% dodge.",'flavor':"The sky itself is a surface to run on. These boots proved it."},
    {'name':'Falcon Grip of Horus',          'icon':'🧤','type':'gloves','slot':'gloves','rarity':'raid','tradeable':False,'class_affinity':'ranger','stats':{'dex':20,'atk':12},'level_req':15,'special_effect':"Eagle Eye cooldown 1. Piercing Shot DEX scaling x4. All shots pierce armor.",'flavor':"Horus pressed these into the archer's hands. 'You're ready,' he said."},
    {'name':"Pendant of the Eternal Moon",   'icon':'📿','type':'amulet','slot':'amulet','rarity':'raid','tradeable':False,'class_affinity':'ranger','stats':{'dex':20,'int':10,'mp':36},'level_req':15,'special_effect':"Piercing Shot ignores 100% armor. Crits restore 10 MP. Eagle Eye cooldown 0.",'flavor':"The moon watches every hunt. This pendant is the moon's eye."},
    # ── Shardana (samurai) ──
    {'name':'Twin Void Blades of the Sea',   'icon':'⚔️','type':'weapon','slot':'weapon','rarity':'raid','tradeable':False,'class_affinity':'samurai','stats':{'atk':58,'str':16,'dex':16},'level_req':15,'proc':{'effect':'bleed','chance':0.35},'special_effect':"Bleed on hit (35%). Dual Onslaught strikes 5 times. Death Blow at 50% HP, 3x damage.",'flavor':"One blade for justice. The other for everything else."},
    {'name':'Corselet of the Undying Sea',   'icon':'🛡️','type':'armor', 'slot':'armor', 'rarity':'raid','tradeable':False,'class_affinity':'samurai','stats':{'def':40,'str':14,'dex':14},'level_req':15,'special_effect':"Death Blow at 50% HP, 3.5x damage. Survive one lethal hit per room at 1 HP.",'flavor':"Crossed every sea. Survived every battle. Refuses to stop."},
    {'name':'Plumed Helm of the Undying',    'icon':'⛑️','type':'helmet','slot':'helmet','rarity':'raid','tradeable':False,'class_affinity':'samurai','stats':{'str':14,'dex':16},'level_req':15,'special_effect':"Critical hits deal 3.5x. +20% crit chance. On crit, reset Blade Flash cooldown.",'flavor':"Worn by those who fell and rose. The plume has never touched the ground."},
    {'name':'Signet of the Undying King',    'icon':'💍','type':'ring',  'slot':'ring',  'rarity':'raid','tradeable':False,'class_affinity':'samurai','stats':{'str':16,'dex':16,'atk':10},'level_req':15,'special_effect':"Void Cut deals 50% enemy HP. Crits reduce ALL cooldowns by 1.",'flavor':"The undying king wears this still. You found a copy. Perhaps he allowed it."},
    {'name':'War Sash of the Void Warrior',  'icon':'🪢','type':'belt',  'slot':'belt',  'rarity':'raid','tradeable':False,'class_affinity':'samurai','stats':{'str':16,'dex':14,'atk':10},'level_req':15,'special_effect':"Blade Flash cooldown: 0 always. Iaijutsu cooldown 0. Chain them infinitely.",'flavor':"The void warrior's sash holds nothing but potential. Infinite potential."},
    {'name':'Sea-Striders of the Void',      'icon':'👢','type':'boots', 'slot':'boots', 'rarity':'raid','tradeable':False,'class_affinity':'samurai','stats':{'str':10,'dex':20},'level_req':15,'special_effect':"Dual Onslaught + Blade Flash combo: +60% damage on next attack. Stacks.",'flavor':"The void has no floor. These boots do not care."},
    {'name':'Iron Fists of the Void King',   'icon':'🧤','type':'gloves','slot':'gloves','rarity':'raid','tradeable':False,'class_affinity':'samurai','stats':{'str':16,'dex':16},'level_req':15,'special_effect':"Critical hits deal 3.5x. Dual Onslaught +2 extra hits. Death Blow guaranteed crit.",'flavor':"The void king crushed stars with these. Enemies are easier."},
    {'name':'Medallion of the Eternal Blade','icon':'📿','type':'amulet','slot':'amulet','rarity':'raid','tradeable':False,'class_affinity':'samurai','stats':{'str':14,'dex':14,'vit':12,'hp':40},'level_req':15,'special_effect':"Survive two lethal blows per floor at 1 HP. Both trigger Death Blow.",'flavor':"The blade is eternal. The warrior who wields it must become the same."},
    # ── Heka (cleric) ──
    {'name':'Sistrum of the Undying Light',  'icon':'🌟','type':'weapon','slot':'weapon','rarity':'raid','tradeable':False,'class_affinity':'cleric','stats':{'atk':38,'int':24,'vit':10},'level_req':15,'proc':{'effect':'curse','chance':0.25},'special_effect':"Curse on hit (25%). Mend Wounds heals 55% HP. All healing skills have no cooldown.",'flavor':"The sound of this sistrum reaches across the boundary of death."},
    {'name':'Vestments of the Eternal Flame','icon':'🦺','type':'armor', 'slot':'armor', 'rarity':'raid','tradeable':False,'class_affinity':'cleric','stats':{'def':30,'int':18,'vit':16},'level_req':15,'special_effect':"Purifying Flame heals 35% HP. All healing +50%. Spells ignore 100% armor.",'flavor':"The cloth absorbs all light, then gives it back threefold."},
    {'name':'Headband of Ra Ascendant',      'icon':'⛑️','type':'helmet','slot':'helmet','rarity':'raid','tradeable':False,'class_affinity':'cleric','stats':{'int':22,'vit':12},'level_req':15,'special_effect':"Breath of Osiris shield lasts 8 hits. +30% healing power. Auto-heal 5% HP/turn.",'flavor':"Ra's crown, left behind when he ascended. Still warm with divine light."},
    {'name':'Ankh of Eternal Life',          'icon':'💍','type':'ring',  'slot':'ring',  'rarity':'raid','tradeable':False,'class_affinity':'cleric','stats':{'int':18,'vit':16,'hp':50},'level_req':15,'special_effect':"Searing Light heals 10% HP + ignores 100% armor. Revive from 0 HP once per floor.",'flavor':"The key to eternal life. The gods kept it hidden for good reason."},
    {'name':'Girdle of the Eternal Flame',   'icon':'🪢','type':'belt',  'slot':'belt',  'rarity':'raid','tradeable':False,'class_affinity':'cleric','stats':{'int':16,'vit':16,'hp':40},'level_req':15,'special_effect':"All healing in combat +60%. MP regenerates 15% per combat round.",'flavor':"The eternal flame does not burn out. Neither does its wearer."},
    {'name':'Sandals of the Divine Threshold','icon':'👢','type':'boots','slot':'boots', 'rarity':'raid','tradeable':False,'class_affinity':'cleric','stats':{'int':12,'vit':14,'dex':10},'level_req':15,'special_effect':"When HP < 40%, auto-cast Mend Wounds every turn. No cooldown.",'flavor':"Each step is a prayer. The divine threshold moves with the wearer."},
    {'name':"Gloves of Ra's Light",          'icon':'🧤','type':'gloves','slot':'gloves','rarity':'raid','tradeable':False,'class_affinity':'cleric','stats':{'int':20,'vit':10},'level_req':15,'special_effect':"Healing skills cost 0 MP. Mend Wounds +20% HP per cast. Cleanse all debuffs on heal.",'flavor':"Touch with these, and Ra's light flows through."},
    {'name':"Amulet of Ra's Eternal Grace",  'icon':'📿','type':'amulet','slot':'amulet','rarity':'raid','tradeable':False,'class_affinity':'cleric','stats':{'int':22,'vit':12,'mp':40},'level_req':15,'special_effect':"Searing Light restores 15% HP. Purifying Flame AoE deals +60% and heals all.",'flavor':"Ra pressed this amulet into the last healer's hand. 'The world needs you,' he said."},
    # ── Tjaty (paladin) ──
    {'name':'Scepter of Divine Annihilation','icon':'⚖️','type':'weapon','slot':'weapon','rarity':'raid','tradeable':False,'class_affinity':'paladin','stats':{'atk':48,'str':16,'int':14},'level_req':15,'proc':{'effect':'curse','chance':0.30},'special_effect':"Curse on hit (30%). Judgment heals 100% of damage dealt. +40% holy damage.",'flavor':"Judgment is not a decision. It is an event. This scepter makes it so."},
    {'name':'Plate of the God-King',         'icon':'🛡️','type':'armor', 'slot':'armor', 'rarity':'raid','tradeable':False,'class_affinity':'paladin','stats':{'def':56,'str':14,'vit':20},'level_req':15,'special_effect':"Bulwark damage reduction 40%. Heal shields +40% of heal. Immune to one-shots.",'flavor':"The god-king's plate. The god is gone. The plate remains."},
    {'name':'Helmet of the Divine Order',    'icon':'⛑️','type':'helmet','slot':'helmet','rarity':'raid','tradeable':False,'class_affinity':'paladin','stats':{'str':12,'vit':16,'int':10},'level_req':15,'special_effect':"Holy Shield blocks 80% damage (8 hits). After Holy Shield: next atk +100%.",'flavor':"The sacred order's last helmet. Sacred to the moment of final justice."},
    {"name":"Ring of the God-King's Grace",  'icon':'💍','type':'ring',  'slot':'ring',  'rarity':'raid','tradeable':False,'class_affinity':'paladin','stats':{'str':12,'vit':16,'int':10},'level_req':15,'special_effect':"All healing restores 10% MP. Smite 40% stun chance. Stunned enemies take +40% more.",'flavor':"The god-king's mercy was rare. When given, it was absolute."},
    {'name':'Girdle of Divine Might',        'icon':'🪢','type':'belt',  'slot':'belt',  'rarity':'raid','tradeable':False,'class_affinity':'paladin','stats':{'str':16,'vit':16,'def':14},'level_req':15,'special_effect':"Lay on Hands shield = 50% of heal. Unlimited heals per combat.",'flavor':"The divine girdle of the god-king's judiciary. The scales are permanently balanced."},
    {'name':'Sabatons of the God-Knight',    'icon':'👢','type':'boots', 'slot':'boots', 'rarity':'raid','tradeable':False,'class_affinity':'paladin','stats':{'str':14,'vit':14,'dex':10},'level_req':15,'special_effect':"After Holy Shield, next 3 attacks deal +100% damage each.",'flavor':"The god-knight won every battle. These boots remember every step."},
    {'name':"Gauntlets of Heaven's Wrath",   'icon':'🧤','type':'gloves','slot':'gloves','rarity':'raid','tradeable':False,'class_affinity':'paladin','stats':{'str':16,'int':10,'atk':10},'level_req':15,'special_effect':"Smite 40% stun. Judgment +60% on stunned. On kill, heal 20% max HP.",'flavor':"Heaven's wrath made solid. There is no defense."},
    {"name":"Talisman of Absolute Balance",  'icon':'📿','type':'amulet','slot':'amulet','rarity':'raid','tradeable':False,'class_affinity':'paladin','stats':{'str':14,'vit':16,'int':14,'hp':40},'level_req':15,'special_effect':"Bulwark cap 60% of max HP. All skills free. Survive one lethal hit per floor.",'flavor':"Absolute balance. The scales do not tip. The warrior does not fall."},
]

# ── UNIQUE ITEM TEMPLATES — named items with scaled random stats ──
UNIQUE_ITEM_TEMPLATES = {
    'weapon': [
        {'name':"Scarab's Fang",      'icon':'🗡️','base_stats':{'atk':12,'dex':4},  'special':"Critical hit chance +10%. Crits deal extra DEX damage."},
        {'name':"Blade of the Dusk",  'icon':'🗡️','base_stats':{'atk':10,'str':3},  'special':"Life steal: heal 8% of damage dealt on each hit."},
        {'name':"Noon Fury Blade",    'icon':'🗡️','base_stats':{'atk':11,'int':5},  'special':"INT bonus applies to all physical attacks (x0.3 scaling)."},
        {'name':"Staff of Set's Gale",'icon':'🪄','base_stats':{'atk':8,'int':7},   'special':"Restore 5 MP on every kill."},
        {'name':"Fangs of Sekhmet",   'icon':'🗡️','base_stats':{'atk':13,'str':4},  'special':"Basic attacks apply burn: 3 damage/turn for 3 turns."},
        {'name':"Hymn of the Archer", 'icon':'🏹','base_stats':{'atk':9,'dex':6},   'special':"DEX bonus applies to all damage (x0.3 scaling)."},
    ],
    'armor': [
        {'name':"Khnum's Scales",          'icon':'🛡️','base_stats':{'def':10,'vit':4},'special':"Reduce all damage taken by 5%."},
        {'name':"Corselet of the Falcon",  'icon':'🦺','base_stats':{'def':7,'dex':5}, 'special':"+6% dodge chance."},
        {'name':"Robes of Midnight",       'icon':'🦺','base_stats':{'def':6,'int':6}, 'special':"Take 15% less magic damage. MP regenerates 2% per round."},
        {'name':"Delta Padded Linen",      'icon':'🦺','base_stats':{'def':8,'vit':5}, 'special':"Regenerate 3% HP at end of each combat round."},
        {'name':"Sobek's Lacquered Hide",  'icon':'🛡️','base_stats':{'def':12,'str':3},'special':"Thorns: deal 15% of received damage back to attacker."},
    ],
    'helmet': [
        {'name':"Jackal Crown",       'icon':'⛑️','base_stats':{'def':6,'vit':4},        'special':"20% chance to resist instant-death effects."},
        {'name':"Mask of Anubis",     'icon':'⛑️','base_stats':{'def':5,'int':6},        'special':"Spell damage +12%."},
        {'name':"Horned Cap of Amun", 'icon':'⛑️','base_stats':{'def':5,'str':5},        'special':"+10% STR for all damage calculations."},
        {'name':"Moon God Circlet",   'icon':'⛑️','base_stats':{'def':4,'int':5,'mp':8}, 'special':"MP regenerates 5% faster between rooms."},
        {'name':"War Cap of Thutmose",'icon':'⛑️','base_stats':{'def':7,'str':4},        'special':"+8% critical hit chance."},
    ],
    'ring': [
        {'name':"Loop of the Scarab", 'icon':'💍','base_stats':{'dex':4,'str':2},  'special':"+20% gold from all sources."},
        {'name':"Coil of the Lotus",  'icon':'💍','base_stats':{'vit':5,'hp':12},  'special':"+15 max HP. Potions restore +10% more HP."},
        {'name':"Ring of the Nile",   'icon':'💍','base_stats':{'int':4,'mp':10},  'special':"Restore 3 MP after each combat round."},
        {'name':"Band of Two Lands",  'icon':'💍','base_stats':{'str':4,'vit':4},  'special':"+5% damage when above 70% HP."},
        {'name':"Cartouche of Amun",  'icon':'💍','base_stats':{'int':5,'mp':8},   'special':"Spell MP cost reduced by 8%."},
    ],
    'belt': [
        {'name':"River God Sash",          'icon':'🪢','base_stats':{'vit':5,'hp':15},'special':"+15 max HP. HP potions restore +10% more."},
        {'name':"Warrior Girdle of Montu", 'icon':'🪢','base_stats':{'str':5,'atk':4},'special':"STR-based attacks deal +10% bonus damage."},
        {'name':"Girdle of Isis",          'icon':'🪢','base_stats':{'int':5,'mp':12},'special':"INT-based spells cost 8% less MP."},
        {'name':"Serpent Clasp Belt",      'icon':'🪢','base_stats':{'dex':5,'def':3},'special':"+5% dodge. On dodge, next attack is a guaranteed crit."},
        {'name':"Red Land Linen Wrap",     'icon':'🪢','base_stats':{'def':5,'vit':4},'special':"Take 10% less damage from enemies above floor 5."},
    ],
    'boots': [
        {'name':"Desert Runner Sandals",   'icon':'👢','base_stats':{'dex':6},          'special':"Acts first in combat if DEX exceeds enemy level."},
        {'name':"War Boots of Montu",      'icon':'👢','base_stats':{'str':4,'atk':4},  'special':"First attack each combat deals +15% damage."},
        {'name':"Blessed Sandals of Nut",  'icon':'👢','base_stats':{'int':4,'mp':10},  'special':"Regenerate 3% MP per room entered."},
        {'name':"Striders of the Nile",    'icon':'👢','base_stats':{'vit':5,'hp':12},  'special':"Restore 5% HP when entering a new room."},
        {'name':"Shadow Steps of Set",     'icon':'👢','base_stats':{'dex':6,'int':3},  'special':"+8% crit chance. Crits also apply 1 poison tick."},
    ],
    'gloves': [
        {'name':"Iron Wraps of Horus",     'icon':'🧤','base_stats':{'str':5,'atk':4},  'special':"STR attacks deal +12% damage."},
        {'name':"Fingers of Thoth",        'icon':'🧤','base_stats':{'int':6,'mp':8},   'special':"INT spells have +10% damage."},
        {'name':"Hands of Sobek",          'icon':'🧤','base_stats':{'dex':5,'atk':3},  'special':"+6% crit chance."},
        {'name':"Vitality Wraps of Isis",  'icon':'🧤','base_stats':{'vit':5,'hp':15},  'special':"Take 8% less damage. +10 max HP."},
        {'name':"Golden Hawk Gauntlets",   'icon':'🥊','base_stats':{'str':6,'atk':5},  'special':"Deal +10% bonus damage against bosses and elites."},
    ],
    'amulet': [
        {'name':"Crook Amulet",           'icon':'📿','base_stats':{'int':5,'mp':12},  'special':"Spell power +15%."},
        {'name':"Flail Pendant",          'icon':'📿','base_stats':{'str':5,'atk':4},  'special':"STR-based attacks deal +15% damage."},
        {'name':"Lotus Flower Amulet",    'icon':'📿','base_stats':{'int':4,'mp':14},  'special':"MP regenerates 5 per combat round."},
        {'name':"Scarab of Life",         'icon':'📿','base_stats':{'vit':6,'hp':18},  'special':"HP potions restore +20% more. +20 max HP."},
        {'name':"Eye of the Desert",      'icon':'📿','base_stats':{'dex':5,'int':4},  'special':"+8% crit chance. Crits deal 2.2x damage (up from 2x)."},
    ],
}

def generate_unique(floor):
    """Generate a named unique item. Stats scale with floor — same name can drop
    at different power levels. Level req = floor x1.5."""
    slot = random.choice(list(UNIQUE_ITEM_TEMPLATES.keys()))
    template = random.choice(UNIQUE_ITEM_TEMPLATES[slot])
    level_req = max(1, int(floor * 1.5))
    scale = 1 + level_req * 0.08
    scaled = {}
    for k, v in template['base_stats'].items():
        base = v * scale
        variance = base * 0.15
        scaled[k] = max(1, int(base + random.uniform(-variance, variance)))
    return {
        'name': template['name'],
        'icon': template['icon'],
        'type': slot,
        'slot': slot,
        'rarity': 'unique',
        'stats': scaled,
        'special_effect': template['special'],
        'level_req': level_req,
        'flavor': 'A named item of legend. Power grows with those who seek it.',
        'id': f"uniq_{int(time.time())}_{random.randint(1000,9999)}",
    }

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
    """Loot from raid boss kills. Raid tier is the highest possible drop."""
    bd = RAID_BOSSES.get(boss_id, {})
    bonus = bd.get('loot_bonus', 0.15)
    rr = random.random()
    # Raid tier: guaranteed for hardest bosses, high chance for others
    raid_chance = {'warden': 0.35, 'apophis': 0.55, 'pharaoh': 0.80}.get(boss_id, 0.40)
    if rr < raid_chance:
        rarity = 'raid'
    elif rr < raid_chance + 0.15 + bonus:
        rarity = 'legendary'
    elif rr < raid_chance + 0.25 + bonus:
        rarity = 'unique'
    elif rr < raid_chance + 0.50:
        rarity = 'rare'
    elif rr < raid_chance + 0.75:
        rarity = 'uncommon'
    else:
        rarity = 'common'

    if rarity == 'raid':
        pool = RAID_LOOT_POOL or LOOT_POOL
        item = dict(random.choice(pool))
        item['id'] = f"raid_{int(time.time())}_{random.randint(1000,9999)}"
        item['from_raid'] = True
        return item

    if rarity == 'unique':
        item = generate_unique(floor)
        item['id'] = f"raid_{int(time.time())}_{random.randint(1000,9999)}"
        item['from_raid'] = True
        return item

    if rarity == 'legendary':
        pool = [i for i in LOOT_POOL if i['rarity'] == 'legendary']
        if pool:
            item = dict(random.choice(pool))
            item['id'] = f"raid_{int(time.time())}_{random.randint(1000,9999)}"
            item['from_raid'] = True
            return item
        rarity = 'rare'

    pool = [i for i in LOOT_POOL if i['rarity'] == rarity] or LOOT_POOL
    item = dict(random.choice(pool))
    fl = max(1, floor)
    fl_bonus = max(0, int(math.log(fl + 1, 2)))
    mult = {'common':1.2,'uncommon':2.0,'rare':3.0}.get(rarity, 1.2)
    item['stats'] = {k: max(1, int((v + fl_bonus) * mult)) for k, v in item.get('stats', {}).items()}
    item['level_req'] = max(1, floor)
    item['id'] = f"raid_{int(time.time())}_{random.randint(1000,9999)}"
    item['from_raid'] = True
    item.setdefault('tradeable', True)
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
    if   rr < 0.01 + quality * 0.10: rarity = 'unique'
    elif rr < 0.06 + quality * 0.30: rarity = 'rare'
    elif rr < 0.28 + quality * 0.22: rarity = 'uncommon'
    else:                             rarity = 'common'
    if rarity == 'unique':
        item = generate_unique(pet_floor)
        item['from_pet'] = True
        item['generated_at'] = int(time.time())
        return item
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
    tier_max = min(6, max(1, math.ceil(floor / 5)))
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

    mon = {
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
        'status_effects': [],
    }
    if base.get('status_proc'):
        mon['status_proc'] = base['status_proc']
    return mon

def _maybe_add_lifesteal(item, rarity):
    """Randomly add life_steal and/or mana_steal to an item based on rarity."""
    ls_ranges = {
        'uncommon': (0, 3),
        'rare':     (2, 5),
        'unique':   (3, 7),
        'legendary':(4, 8),
        'raid':     (5,10),
    }
    ms_ranges = {
        'uncommon': (0, 2),
        'rare':     (1, 4),
        'unique':   (2, 5),
        'legendary':(3, 6),
        'raid':     (4, 8),
    }
    if rarity not in ls_ranges:
        return item

    # Chance to get lifesteal: uncommon 15%, rare 35%, unique 50%, legendary 70%, raid 90%
    ls_chance = {'uncommon':0.15,'rare':0.35,'unique':0.50,'legendary':0.70,'raid':0.90}.get(rarity,0)
    ms_chance = {'uncommon':0.08,'rare':0.20,'unique':0.35,'legendary':0.55,'raid':0.75}.get(rarity,0)

    stats = item.get('stats', {})
    if random.random() < ls_chance:
        lo, hi = ls_ranges[rarity]
        val = random.randint(lo, hi)
        if val > 0:
            stats['life_steal'] = val
    if random.random() < ms_chance:
        lo, hi = ms_ranges[rarity]
        val = random.randint(lo, hi)
        if val > 0:
            stats['mana_steal'] = val
    item['stats'] = stats
    return item

def make_loot(floor, guaranteed=False, force_rare=False):
    """
    Rarity tiers: common < uncommon < rare < unique (named/random) < legendary (fixed/class)
    - Boss kill: guaranteed rare+, ~15% unique, ~0.5% legendary
    - Regular: mostly common/uncommon, rare ~6%, unique ~2%, legendary ~0.1%
    """
    if not guaranteed and not force_rare and random.random() < 0.15:
        return None

    rr = random.random()
    if force_rare:
        # Boss kill
        if rr < 0.005:        rarity = 'legendary'
        elif rr < 0.155:      rarity = 'unique'
        else:                 rarity = 'rare'
    elif rr < 0.001 + floor * 0.0001:  rarity = 'legendary'
    elif rr < 0.02  + floor * 0.005:   rarity = 'unique'
    elif rr < 0.06  + floor * 0.006:   rarity = 'rare'
    elif rr < 0.28  + floor * 0.012:   rarity = 'uncommon'
    else:                               rarity = 'common'

    if rarity == 'unique':
        item = generate_unique(floor)
        item = _maybe_add_lifesteal(item, 'unique')
        item['tradeable'] = True
        return item

    if rarity == 'legendary':
        pool = [i for i in LOOT_POOL if i['rarity'] == 'legendary']
        if pool:
            item = dict(random.choice(pool))
            item['id'] = f"loot_{int(time.time())}_{random.randint(1000,9999)}"
            item = _maybe_add_lifesteal(item, 'legendary')
            item['tradeable'] = False
            return item
        rarity = 'rare'  # fallback

    pool = [i for i in LOOT_POOL if i['rarity'] == rarity] or [i for i in LOOT_POOL if i['rarity'] == 'rare']
    item = dict(random.choice(pool))

    floor_bonus = max(0, int(math.log(floor + 1, 2)))
    base_stats = {k: v + floor_bonus for k, v in item.get('stats', {}).items()}
    mult = {'common': 1.0, 'uncommon': 1.6, 'rare': 2.4}[rarity]
    scaled = {k: max(1, int(v * mult)) for k, v in base_stats.items()}

    bonus_stats = ['str','dex','vit','int','atk','def']
    if rarity == 'uncommon':
        bk = random.choice(bonus_stats)
        scaled[bk] = scaled.get(bk, 0) + 1 + floor_bonus
    elif rarity == 'rare':
        for _ in range(2):
            bk = random.choice(bonus_stats)
            scaled[bk] = scaled.get(bk, 0) + 2 + floor_bonus

    item['stats'] = scaled
    item['level_req'] = max(1, floor)
    item = _maybe_add_lifesteal(item, rarity)
    item['tradeable'] = True
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
    is_raid_floor = (floor >= 10 and floor % 5 == 0)
    boss_mon = make_monster(floor, is_boss=True)
    if is_raid_floor:
        # Raid boss: 3x HP & ATK, guaranteed raid-tier loot
        boss_mon['hp'] = boss_mon['max_hp'] = int(boss_mon['hp'] * 3)
        boss_mon['atk'] = int(boss_mon['atk'] * 2.5)
        boss_mon['rage_threshold'] = boss_mon['hp'] // 2
        boss_mon['rage_atk'] = int(boss_mon['atk'] * 1.5)
        boss_mon['xp'] = int(boss_mon['xp'] * 3)
        boss_mon['gold'] = [boss_mon['gold'][0]*3, boss_mon['gold'][1]*3]
        boss_mon['name'] = 'Raid Boss: ' + boss_mon['name']
        boss_mon['is_raid_boss'] = True
        boss_loot = _raid_make_loot(floor, 'warden' if floor < 20 else ('apophis' if floor < 30 else 'pharaoh'))
    else:
        boss_loot = make_loot(floor, guaranteed=True)
    rooms[f"{br},{bc}"] = {
        'type': 'raid_boss' if is_raid_floor else 'boss',
        'exits':list(exits[boss_coord]),
        'cleared':False,'fog':True,
        'monster':boss_mon,'loot':boss_loot,'loot_taken':{}
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
    eq = {'weapon':START_WEAPONS.get(cls,DEFAULT_WEAPON),'armor':START_ARMOR,'helmet':None,'ring':None,'belt':None,'boots':None,'gloves':None,'amulet':None}
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

# ── STATUS EFFECTS HELPERS ──────────────────────────────────────────────────
STATUS_EFFECT_DEFS = {
    'poison':   {'turns':3, 'icon':'🟢', 'label':'Poison',   'dmg_type':'pct_max', 'val':0.04},
    'burn':     {'turns':3, 'icon':'🔥', 'label':'Burn',     'dmg_type':'flat',    'val':8},
    'bleed':    {'turns':4, 'icon':'🩸', 'label':'Bleed',    'dmg_type':'str_pct', 'val':0.8},
    'frostbite':{'turns':3, 'icon':'🧊', 'label':'Frostbite','dmg_type':'flat',    'val':3,  'atk_reduce':0.20},
    'curse':    {'turns':4, 'icon':'💜', 'label':'Curse',    'dmg_type':'flat',    'val':2,  'dmg_reduce':0.15},
}

def _apply_status_effect(effects_list, effect_type, attacker_str=0, floor=1):
    """Add or refresh a status effect on the target."""
    defn = STATUS_EFFECT_DEFS.get(effect_type, {})
    if not defn:
        return effects_list
    # Calculate DoT value
    if defn['dmg_type'] == 'flat':
        dmg = int(defn['val'] + floor * 2) if effect_type == 'burn' else int(defn['val'])
    elif defn['dmg_type'] == 'str_pct':
        dmg = max(1, int(attacker_str * defn['val']))
    else:
        dmg = 0  # pct_max calculated at tick time
    # Refresh if already present
    for eff in effects_list:
        if eff['type'] == effect_type:
            eff['turns'] = defn['turns']
            if 'dmg' in eff:
                eff['dmg'] = max(eff['dmg'], dmg)
            return effects_list
    new_eff = {'type': effect_type, 'turns': defn['turns'], 'icon': defn['icon'], 'label': defn['label']}
    if effect_type in ('poison', 'burn', 'bleed'):
        new_eff['dmg'] = dmg
    if effect_type == 'frostbite':
        new_eff['atk_reduce'] = defn['atk_reduce']
    if effect_type == 'curse':
        new_eff['dmg_reduce'] = defn['dmg_reduce']
    effects_list.append(new_eff)
    return effects_list

def _tick_status_effects(effects_list, mon_max_hp):
    """Tick all effects on the monster. Returns (total_dot_dmg, updated_list, messages)."""
    total_dot = 0
    messages = []
    remaining = []
    for eff in effects_list:
        dot_dmg = 0
        defn = STATUS_EFFECT_DEFS.get(eff['type'], {})
        if defn.get('dmg_type') == 'pct_max':
            dot_dmg = max(1, int(mon_max_hp * defn['val']))
        elif eff.get('dmg', 0) > 0:
            dot_dmg = eff['dmg']
        if dot_dmg > 0:
            total_dot += dot_dmg
            messages.append(f"{eff['icon']} {eff['label']}: -{dot_dmg}")
        eff['turns'] -= 1
        if eff['turns'] > 0:
            remaining.append(eff)
    return total_dot, remaining, messages

def _get_active_debuffs(effects_list):
    """Return atk_reduce and dmg_reduce from active status effects."""
    atk_reduce = 0.0
    dmg_reduce = 0.0
    for eff in effects_list:
        if eff.get('atk_reduce'):
            atk_reduce = max(atk_reduce, eff['atk_reduce'])
        if eff.get('dmg_reduce'):
            dmg_reduce = max(dmg_reduce, eff['dmg_reduce'])
    return atk_reduce, dmg_reduce

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
    # Ensure monster has status_effects list
    if 'status_effects' not in mon:
        mon['status_effects'] = []

    result = {'player_dmg':0,'monster_dmg':0,'killed':False,'crit':False,'boss_dead':False,
              'life_stolen':0,'mana_stolen':0,'status_effects_applied':[],'dot_damage':0}

    # ── Tick status effects on monster (DoT fires BEFORE monster acts) ──
    dot_total = 0
    if mon['status_effects']:
        dot_total, mon['status_effects'], dot_msgs = _tick_status_effects(
            mon['status_effects'], mon.get('max_hp', mon['hp']))
        if dot_total > 0:
            mon['hp'] = max(0, mon['hp'] - dot_total)
            result['dot_damage'] = dot_total
            for m in dot_msgs:
                state.setdefault('event_log',[]).append({'type':'combat','msg':m,'ts':int(time.time())})

    # If DoT killed the monster, no further processing
    if mon['hp'] <= 0:
        result['killed'] = True
        result['xp_gain']   = mon['xp'] + int(mon['level']*2)
        result['gold_gain'] = mon['gold'][0] + random.randint(0, max(0, mon['gold'][1]-mon['gold'][0]))
        result['loot']      = room.get('loot')
        room['cleared'] = True
        room['monster'] = None
        state.setdefault('event_log',[]).append({'type':'kill',
            'msg':f"☠ {mon['name']} slain by DoT! +{result['xp_gain']}XP +{result['gold_gain']}g",
            'ts':int(time.time())})
        p['hp'] = player_curhp  # No monster_dmg this round (DoT kill)
        p['max_hp'] = player_maxhp
        p['last_seen'] = int(time.time())
        if len(state.get('event_log',[])) > 100:
            state['event_log'] = state['event_log'][-60:]
        state['rooms'][pos] = room
        save_ds(db, sid, state)
        return jsonify(result)

    # ── Player hits monster ──
    crit = random.random() < min(0.4, 0.05 + player_dex*0.01)
    dmg  = max(1, int(player_atk * (random.random()*0.4+0.8)))
    if is_skill: dmg = int(dmg * skill_mult)
    if crit:     dmg = int(dmg * 1.8)
    mon['hp'] = max(0, mon['hp'] - dmg)
    result['player_dmg'] = dmg
    result['crit'] = crit

    # ── Life steal / Mana steal ──
    # These come from equipped items — client sends player stats; server tracks via session player state
    # We apply them based on player_atk as proxy (actual equipment stats come from client-side damage calc)
    # For co-op: use fraction of damage dealt
    ls_pct = float(d.get('life_steal_pct', 0))   # client sends sum of life_steal stats as %
    ms_pct = float(d.get('mana_steal_pct', 0))
    if ls_pct > 0 and dmg > 0:
        life_gained = max(1, int(dmg * ls_pct / 100))
        result['life_stolen'] = life_gained
    if ms_pct > 0 and dmg > 0:
        mana_gained = max(1, int(dmg * ms_pct / 100))
        result['mana_stolen'] = mana_gained

    # ── Status effect proc from weapon ──
    weapon_proc = d.get('weapon_proc')  # {'effect':'poison','chance':0.15}
    if weapon_proc and isinstance(weapon_proc, dict) and mon['hp'] > 0:
        proc_effect  = weapon_proc.get('effect','poison')
        proc_chance  = float(weapon_proc.get('chance', 0))
        if random.random() < proc_chance:
            attacker_str = int(d.get('player_str', player_atk // 3))
            floor_num    = state.get('floor', 1)
            mon['status_effects'] = _apply_status_effect(
                mon['status_effects'], proc_effect, attacker_str, floor_num)
            result['status_effects_applied'].append(proc_effect)
            defn = STATUS_EFFECT_DEFS.get(proc_effect, {})
            state.setdefault('event_log',[]).append({'type':'combat',
                'msg':f"{defn.get('icon','⚡')} {proc_effect.title()} applied to {mon['name']}!",
                'ts':int(time.time())})

    log_msg = f"{'⚡' if crit else '⚔'} {username}: -{dmg} to {mon['name']}"
    state.setdefault('event_log',[]).append({
        'type':'combat','msg':log_msg,'ts':int(time.time()),
        'uid':uid,'dmg':dmg,'crit':crit,
        'mon_hp':mon['hp'],'mon_max_hp':mon.get('max_hp',mon['hp']),
    })
    result['mon_status_effects'] = mon['status_effects']

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

        # Apply debuffs from status effects
        atk_reduce, dmg_reduce = _get_active_debuffs(mon.get('status_effects', []))
        effective_atk = int(effective_atk * (1 - atk_reduce))

        raw_def = max(0, int(player_def * 0.5))
        # Use percentage-based damage formula: atk * (100 / (100 + def))
        # Guarantees minimum damage, no zero-floor walls
        m_dmg = max(1, int(effective_atk * (100 / (100 + raw_def)) * (random.random()*0.3+0.85)))
        if dmg_reduce > 0:
            m_dmg = max(1, int(m_dmg * (1 - dmg_reduce)))
        result['monster_dmg'] = m_dmg
        state['event_log'].append({'type':'combat',
            'msg':f"🩸 {mon['name']}: -{m_dmg} to {username}",
            'ts':int(time.time())})

        # ── Monster status proc on player ──
        mon_proc = mon.get('status_proc')
        if mon_proc and random.random() < mon_proc.get('chance', 0):
            effect = mon_proc['effect']
            p.setdefault('status_effects', [])
            p['status_effects'] = _apply_status_effect(
                p['status_effects'], effect, attacker_str=mon['atk']//4, floor=state.get('floor',1))
            defn = STATUS_EFFECT_DEFS.get(effect, {})
            result['player_proc_applied'] = effect
            state['event_log'].append({'type':'combat',
                'msg':f"{defn.get('icon','⚡')} {mon['name']} inflicts {defn.get('label',effect)} on {username}!",
                'ts':int(time.time())})

    # ── Tick player status effects (DoT on player from previous monster procs) ──
    p.setdefault('status_effects', [])
    if p['status_effects']:
        pdot, p['status_effects'], pdot_msgs = _tick_status_effects(
            p['status_effects'], player_maxhp)
        if pdot > 0:
            result['player_dot_damage'] = pdot
            player_curhp = max(0, player_curhp - pdot)
            for m in pdot_msgs:
                state['event_log'].append({'type':'combat',
                    'msg':f"[You] {m}",'ts':int(time.time())})
    result['player_status_effects'] = p.get('status_effects', [])

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
# PARTY TRADE SYSTEM
# ═══════════════════════════════════════════════════════

def _get_trade(state):
    """Return current trade dict from dungeon_state, or None."""
    return state.get('trade')

def _save_trade(state, trade):
    state['trade'] = trade

def _clear_trade(state):
    state.pop('trade', None)

def _role_for_uid(state, uid):
    """Return 'host' or 'guest' or None."""
    players = state.get('players', {})
    for pid, pdata in players.items():
        if str(pid) == str(uid):
            return pdata.get('role')
    return None

def _inventory_for_uid(state, uid):
    players = state.get('players', {})
    for pid, pdata in players.items():
        if str(pid) == str(uid):
            return pdata.get('inventory', [])
    return []

def _set_inventory_for_uid(state, uid, inv):
    players = state.get('players', {})
    for pid in list(players.keys()):
        if str(pid) == str(uid):
            players[pid]['inventory'] = inv
    state['players'] = players


@app.route('/api/sessions/<sid>/trade', methods=['GET'])
def get_trade(sid):
    db = get_db()
    state = get_ds(db, sid)
    if not state:
        return jsonify({'error': 'Not found'}), 404
    trade = _get_trade(state)
    return jsonify({'trade': trade})


@app.route('/api/sessions/<sid>/trade/offer', methods=['POST'])
def trade_offer(sid):
    d = request.get_json() or {}
    uid = str(d.get('user_id', ''))
    item_id = d.get('item_id')          # item id to offer (or None to clear offer)

    db = get_db()
    state = get_ds(db, sid)
    if not state:
        return jsonify({'error': 'Not found'}), 404

    role = _role_for_uid(state, uid)
    if not role:
        return jsonify({'error': 'Not in session'}), 403

    trade = _get_trade(state)

    # Start a new trade if none active
    if not trade:
        trade = {
            'active': True,
            'initiator': role,
            'host_offer': [],
            'guest_offer': [],
            'host_locked': False,
            'guest_locked': False,
        }

    if not trade.get('active'):
        return jsonify({'error': 'Trade not active'}), 409

    # If either side is already locked, don't allow changing offer
    offer_key = f'{role}_offer'
    locked_key = f'{role}_locked'
    if trade.get(locked_key):
        return jsonify({'error': 'Already locked in — cancel to change offer'}), 409

    inv = _inventory_for_uid(state, uid)

    if item_id is None:
        # Clear this player's offer
        trade[offer_key] = []
    else:
        # Find the item in inventory
        item = next((it for it in inv if it.get('id') == item_id), None)
        if not item:
            return jsonify({'error': 'Item not in inventory'}), 400
        rarity = item.get('rarity', 'common')
        tradeable = item.get('tradeable', True)
        if not tradeable or rarity in ('legendary', 'raid'):
            return jsonify({'error': 'Soulbound items cannot be traded'}), 400
        trade[offer_key] = [item]

    _save_trade(state, trade)
    save_ds(db, sid, state)
    return jsonify({'ok': True, 'trade': trade})


@app.route('/api/sessions/<sid>/trade/accept', methods=['POST'])
def trade_accept(sid):
    """Lock in your side of the trade. When both locked, execute the swap."""
    d = request.get_json() or {}
    uid = str(d.get('user_id', ''))

    db = get_db()
    state = get_ds(db, sid)
    if not state:
        return jsonify({'error': 'Not found'}), 404

    role = _role_for_uid(state, uid)
    if not role:
        return jsonify({'error': 'Not in session'}), 403

    trade = _get_trade(state)
    if not trade or not trade.get('active'):
        return jsonify({'error': 'No active trade'}), 409

    locked_key = f'{role}_locked'
    trade[locked_key] = True

    # Check if both sides locked
    if trade.get('host_locked') and trade.get('guest_locked'):
        # Determine partner uid
        players = state.get('players', {})
        partner_uid = None
        for pid, pdata in players.items():
            if str(pid) != str(uid):
                partner_uid = str(pid)
                break

        if partner_uid is None:
            _clear_trade(state)
            save_ds(db, sid, state)
            return jsonify({'error': 'Partner not found'}), 500

        my_role = role
        partner_role = 'guest' if my_role == 'host' else 'host'

        my_offer      = trade.get(f'{my_role}_offer', [])
        partner_offer = trade.get(f'{partner_role}_offer', [])

        # Get inventories
        my_inv      = _inventory_for_uid(state, uid)
        partner_inv = _inventory_for_uid(state, partner_uid)

        # Remove offered items from each inventory
        my_offer_ids      = {it['id'] for it in my_offer}
        partner_offer_ids = {it['id'] for it in partner_offer}

        my_inv_new      = [it for it in my_inv      if it['id'] not in my_offer_ids]
        partner_inv_new = [it for it in partner_inv if it['id'] not in partner_offer_ids]

        # Add received items to each inventory
        my_inv_new      += partner_offer
        partner_inv_new += my_offer

        _set_inventory_for_uid(state, uid, my_inv_new)
        _set_inventory_for_uid(state, partner_uid, partner_inv_new)

        # Log event
        state.setdefault('event_log', []).append({
            'type': 'trade',
            'msg': '⚖ Trade completed!',
            'ts': int(time.time()),
        })

        _clear_trade(state)
        save_ds(db, sid, state)
        return jsonify({'ok': True, 'completed': True, 'trade': None})

    _save_trade(state, trade)
    save_ds(db, sid, state)
    return jsonify({'ok': True, 'completed': False, 'trade': trade})


@app.route('/api/sessions/<sid>/trade/cancel', methods=['POST'])
def trade_cancel(sid):
    d = request.get_json() or {}
    uid = str(d.get('user_id', ''))

    db = get_db()
    state = get_ds(db, sid)
    if not state:
        return jsonify({'error': 'Not found'}), 404

    role = _role_for_uid(state, uid)
    if not role:
        return jsonify({'error': 'Not in session'}), 403

    state.setdefault('event_log', []).append({
        'type': 'trade',
        'msg': '⚖ Trade cancelled.',
        'ts': int(time.time()),
    })

    _clear_trade(state)
    save_ds(db, sid, state)
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
