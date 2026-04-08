#include "dungeon.h"
#include <Preferences.h>

DungeonState dungeon = {};

// ── Enemy tables ─────────────────────────────────────────────
struct EnemyDef { const char* name; int hp; int atk; int xp; int gold; const char* special; };

static const EnemyDef ENEMIES[DUNGEON_AREA_COUNT][4] = {
    { // Area 0 — DUAT WASTES
        {"Jackal Scout",  12, 3, 10, 5,  ""},
        {"Shadow Scarab", 10, 4, 12, 6,  ""},
        {"Bone Walker",   15, 3, 14, 7,  ""},
        {"Desert Asp",    9,  5, 13, 6,  ""},
    },
    { // Area 1 — FIELDS OF AARU
        {"River Leech",   18, 5, 22, 10, ""},
        {"Croc Spawn",    22, 6, 25, 12, ""},
        {"Marsh Wraith",  16, 7, 28, 11, ""},
        {"Scarab Swarm",  20, 5, 24, 10, ""},
    },
    { // Area 2 — VALLEY OF KINGS
        {"Mummy Warrior", 30, 8, 40, 18, ""},
        {"Sand Wraith",   25, 10,44, 20, ""},
        {"Tomb Raider",   28, 9, 42, 19, ""},
        {"Stone Cobra",   32, 8, 45, 18, ""},
    },
    { // Area 3 — TEMPLE OF SET
        {"Chaos Priest",  40, 12, 65, 28, "chaos"},
        {"Plague Bearer", 38, 11, 60, 26, "plague"},
        {"Stone Golem",   50, 10, 70, 30, ""},
        {"SET Cultist",   42, 13, 68, 29, "chaos"},
    },
    { // Area 4 — ISFET'S REALM
        {"Void Spawn",    55, 14, 90, 40, ""},
        {"Soul Eater",    50, 16, 95, 42, "devour"},
        {"Endless Shade", 60, 15, 92, 41, ""},
        {"ISFET Shard",   65, 14, 100,45, "coil"},
    },
};

struct BossDef { const char* name; int hp; int atk; int xp; int gold; const char* special; const char* flavor; };
static const BossDef BOSSES[DUNGEON_AREA_COUNT] = {
    {"SOBEK",    40,  10, 60,  25, "devour", "Crocodile god rises!"},
    {"AMMIT",    65,  13, 100, 40, "devour", "Heart-devourer appears!"},
    {"APEP",     90,  15, 150, 60, "coil",   "Serpent of chaos coils!"},
    {"SET",      120, 18, 220, 80, "chaos",  "Lord of storms descends!"},
    {"SEKHMET",  160, 22, 330,110, "plague", "Goddess of plague rises!"},
};

// ── Exploration flavour ───────────────────────────────────────
static const char* FLAVOR[] = {
    "Dust and silence...", "A cold wind passes...", "The sands shift...",
    "Hieroglyphs glow...", "A distant howl...",     "Nothing but shadows...",
};
static const int FLAVOR_LEN = 6;

static uint32_t _rng_state = 12345;
static int _rng_range(int lo, int hi) {
    _rng_state ^= _rng_state << 13;
    _rng_state ^= _rng_state >> 17;
    _rng_state ^= _rng_state << 5;
    return lo + (int)(_rng_state % (uint32_t)(hi - lo + 1));
}
static float _rng_f() { return (_rng_range(0, 9999)) / 10000.0f; }

// ── Log helpers ───────────────────────────────────────────────
static void dlog(const char* l0, const char* l1 = "") {
    strlcpy(dungeon.log[0], l0, 40);
    strlcpy(dungeon.log[1], l1, 40);
    dungeon.event_ready = true;
}

// ── Enemy spawn ───────────────────────────────────────────────
static void spawn_enemy(int area, float scale, bool boss) {
    DungeonEnemy& e = dungeon.enemy;
    memset(&e, 0, sizeof(e));
    if (boss) {
        const BossDef& b = BOSSES[area];
        strlcpy(e.name,    b.name,    sizeof(e.name));
        strlcpy(e.special, b.special, sizeof(e.special));
        e.hp     = e.max_hp = max(1, (int)(b.hp  * scale));
        e.atk    = max(1, (int)(b.atk * scale));
        e.xp     = b.xp;
        e.gold   = _rng_range(b.gold, b.gold * 2);
        e.is_boss = true;
        dungeon.sub = DSUB_COMBAT;
        dlog(b.flavor, "BOSS BATTLE!");
    } else {
        int idx = _rng_range(0, 3);
        const EnemyDef& d = ENEMIES[area][idx];
        strlcpy(e.name,    d.name,    sizeof(e.name));
        strlcpy(e.special, d.special, sizeof(e.special));
        e.hp     = e.max_hp = max(1, (int)(d.hp  * scale));
        e.atk    = max(1, (int)(d.atk * scale));
        e.xp     = d.xp;
        e.gold   = _rng_range(d.gold, d.gold * 2);
        e.is_boss = false;
        char msg[40];
        snprintf(msg, sizeof(msg), "%s APPEARS!", e.name);
        dungeon.sub = DSUB_COMBAT;
        dlog(msg, "Face the enemy!");
    }
    dungeon.enemy_active = true;
    dungeon.coiled = false;
}

// ── Exploration tick ──────────────────────────────────────────
static void solo_move(Companion* c) {
    _rng_state ^= (uint32_t)millis();   // mix entropy each step

    dungeon.steps++;
    float scale = 1.0f + (c->level - 1) * 0.18f;

    // Healing shrine (7% chance)
    if (_rng_f() < 0.07f) {
        int h = _rng_range(6, 14);
        c->hp = min(c->max_hp, c->hp + h);
        char msg[40];
        snprintf(msg, sizeof(msg), "Shrine! +%d HP", h);
        dlog("Found a healing shrine!", msg);
        return;
    }

    // Boss spawn (need enough kills + 20% chance per step)
    if (dungeon.area_kills >= DUNGEON_BOSS_KILLS && _rng_f() < 0.20f) {
        spawn_enemy(dungeon.area, scale, true);
        dungeon.area_kills = 0;
        return;
    }

    // Normal encounter
    if (dungeon.steps >= dungeon.next_enc) {
        spawn_enemy(dungeon.area, scale, false);
        dungeon.steps    = 0;
        dungeon.next_enc = _rng_range(DUNGEON_ENC_MIN, DUNGEON_ENC_MAX);
        return;
    }

    // Exploring
    const char* fl = FLAVOR[_rng_range(0, FLAVOR_LEN - 1)];
    char step[40];
    snprintf(step, sizeof(step), "Step %d/%d  %s",
             dungeon.steps, dungeon.next_enc, DUNGEON_AREAS[dungeon.area]);
    dlog(fl, step);
}

// ── Level up helper ───────────────────────────────────────────
static bool apply_xp(Companion* c, int xp) {
    c->xp += xp;
    bool leveled = false;
    while (c->xp >= c->xp_to_next) {
        c->xp        -= c->xp_to_next;
        c->level++;
        c->xp_to_next = (int)(c->xp_to_next * 1.6f);
        c->max_hp    += 10;
        c->hp         = c->max_hp;
        c->attack    += 2;
        leveled = true;

        // Advance area every 5 levels
        int new_area = min((c->level - 1) / 5, DUNGEON_AREA_COUNT - 1);
        if (new_area > dungeon.area) {
            dungeon.area      = new_area;
            dungeon.area_kills = 0;
        }
    }
    return leveled;
}

// ── Combat tick ───────────────────────────────────────────────
static void solo_attack(Companion* c) {
    DungeonEnemy& e = dungeon.enemy;
    char l0[40] = "", l1[40] = "";

    // Player attack (skip if coiled)
    if (dungeon.coiled) {
        dungeon.coiled = false;
        strlcpy(l0, "Coiled! Attack skipped.", sizeof(l0));
    } else {
        int dmg  = max(1, c->attack - (e.atk / 3) + _rng_range(-1, 3));
        bool crit = _rng_f() < 0.15f;
        if (crit) dmg = (int)(dmg * 2.2f);
        e.hp -= dmg;
        snprintf(l0, sizeof(l0), "%sHit %s for %d [%d/%d]",
                 crit ? "CRIT! " : "", e.name, dmg,
                 max(0, e.hp), e.max_hp);
    }

    // Enemy dead?
    if (e.hp <= 0) {
        c->wins++;
        dungeon.area_kills++;
        dungeon.gold += e.gold;
        bool lvled = apply_xp(c, e.xp);

        snprintf(dungeon.loot_msg, sizeof(dungeon.loot_msg),
                 "+%dXP  +%dGold  %s", e.xp, e.gold,
                 lvled ? "(LEVEL UP!)" : "");
        dlog(e.is_boss ? "BOSS DEFEATED!" : (l0[0] ? l0 : "Enemy slain!"),
             dungeon.loot_msg);
        dungeon.sub          = lvled ? DSUB_LEVELUP : DSUB_LOOT;
        dungeon.enemy_active = false;
        dungeon_save(c);
        return;
    }

    // Enemy counter-attack
    const char* sp = e.special;
    if (strcmp(sp, "devour") == 0 && _rng_f() < 0.30f) {
        int heal = max(1, e.atk / 2);
        e.hp = min(e.max_hp, e.hp + heal);
        snprintf(l1, sizeof(l1), "%s devours! +%dhp", e.name, heal);
    } else if (strcmp(sp, "chaos") == 0 && _rng_f() < 0.25f) {
        c->attack = max(1, c->attack - 1);
        snprintf(l1, sizeof(l1), "Chaos! -1 ATK (%d)", c->attack);
    } else if (strcmp(sp, "coil") == 0 && _rng_f() < 0.35f) {
        dungeon.coiled = true;
        snprintf(l1, sizeof(l1), "%s coils you! Next skip.", e.name);
    } else if (strcmp(sp, "plague") == 0 && _rng_f() < 0.20f) {
        c->max_hp = max(5, c->max_hp - 2);
        c->hp     = min(c->hp, c->max_hp);
        snprintf(l1, sizeof(l1), "Plague! -2 maxHP (%d)", c->max_hp);
    } else {
        int edmg = max(1, e.atk + _rng_range(-1, 2));
        c->hp -= edmg;
        snprintf(l1, sizeof(l1), "%s hits %d  HP:%d", e.name, edmg, c->hp);
    }

    if (l1[0] == '\0') {
        int edmg = max(1, e.atk + _rng_range(-1, 2));
        c->hp -= edmg;
        snprintf(l1, sizeof(l1), "%s hits %d  HP:%d", e.name, edmg, c->hp);
    }

    dlog(l0, l1);

    // Player dead?
    if (c->hp <= 0) {
        c->hp = 0;
        c->losses++;
        dungeon.sub          = DSUB_DEAD;
        dungeon.enemy_active = false;
        dungeon.dead_at      = millis();
        dlog("FALLEN IN THE WASTES", "Respawning in 30s...");
        dungeon_save(c);
    }
}

// ── Public API ────────────────────────────────────────────────
void dungeon_init(Companion* c) {
    dungeon.active     = true;
    dungeon.sub        = DSUB_EXPLORE;
    if (dungeon.area < 0 || dungeon.area >= DUNGEON_AREA_COUNT) dungeon.area = 0;
    dungeon.steps      = 0;
    dungeon.next_enc   = _rng_range(DUNGEON_ENC_MIN, DUNGEON_ENC_MAX);
    dungeon.next_tick  = millis() + DUNGEON_TICK_MS;
    dungeon.last_regen = millis();
    dungeon.enemy_active = false;
    dungeon.coiled     = false;
    dungeon.event_ready = false;
    _rng_state ^= (uint32_t)millis() ^ (uint32_t)c->level;
    dlog("Dungeon crawler started!", DUNGEON_AREAS[dungeon.area]);
}

void dungeon_tick(Companion* c) {
    if (!dungeon.active) return;
    uint32_t now = millis();

    // HP regen while exploring
    if (dungeon.sub == DSUB_EXPLORE &&
        now - dungeon.last_regen >= DUNGEON_REGEN_MS) {
        dungeon.last_regen = now;
        if (c->hp < c->max_hp) { c->hp++; dungeon.event_ready = true; }
    }

    if (now < dungeon.next_tick) return;
    dungeon.next_tick = now + DUNGEON_TICK_MS;

    switch (dungeon.sub) {
    case DSUB_EXPLORE:
        solo_move(c);
        break;

    case DSUB_COMBAT:
        solo_attack(c);
        break;

    case DSUB_LOOT:
    case DSUB_LEVELUP:
        // Hold one tick to display the reward, then continue exploring
        dungeon.sub        = DSUB_EXPLORE;
        dungeon.enemy_active = false;
        dlog(DUNGEON_AREAS[dungeon.area], "Continuing...");
        break;

    case DSUB_DEAD:
        if (now - dungeon.dead_at >= DUNGEON_DEAD_MS) {
            // Respawn: restore base stats, keep level/XP
            c->hp     = c->max_hp / 2;
            dungeon.sub        = DSUB_EXPLORE;
            dungeon.enemy_active = false;
            dungeon.coiled     = false;
            dungeon.area_kills = 0;
            dungeon.steps      = 0;
            dungeon.next_enc   = _rng_range(DUNGEON_ENC_MIN, DUNGEON_ENC_MAX);
            dlog("Respawned!", DUNGEON_AREAS[dungeon.area]);
            dungeon_save(c);
        }
        break;
    }
}

void dungeon_save(const Companion* c) {
    Preferences p;
    p.begin("dungeon", false);
    p.putInt("area",    dungeon.area);
    p.putInt("gold",    dungeon.gold);
    p.putInt("level",   c->level);
    p.putInt("hp",      c->hp);
    p.putInt("max_hp",  c->max_hp);
    p.putInt("attack",  c->attack);
    p.putInt("defense", c->defense);
    p.putInt("xp",      c->xp);
    p.putInt("xp_next", c->xp_to_next);
    p.putInt("wins",    c->wins);
    p.putInt("losses",  c->losses);
    p.end();
}

void dungeon_load(Companion* c) {
    Preferences p;
    p.begin("dungeon", true);
    dungeon.area   = p.getInt("area",    0);
    dungeon.gold   = p.getInt("gold",    0);
    c->level       = p.getInt("level",   c->level);
    c->hp          = p.getInt("hp",      c->hp);
    c->max_hp      = p.getInt("max_hp",  c->max_hp);
    c->attack      = p.getInt("attack",  c->attack);
    c->defense     = p.getInt("defense", c->defense);
    c->xp          = p.getInt("xp",      c->xp);
    c->xp_to_next  = p.getInt("xp_next", c->xp_to_next);
    c->wins        = p.getInt("wins",    c->wins);
    c->losses      = p.getInt("losses",  c->losses);
    p.end();
}
