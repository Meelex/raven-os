#pragma once
#include <Arduino.h>
#include <Preferences.h>

// ── Companion data (mirrors Duat PET_DEFS) ───────────────────
struct Companion {
    char   id[20];           // e.g. "raven", "griffin"
    char   name[20];         // display name
    char   class_name[20];   // e.g. "Sentinel", "Guardian"
    int    level;
    int    hp;
    int    max_hp;
    int    attack;
    int    defense;
    int    xp;
    int    xp_to_next;
    int    wins;
    int    losses;
    bool   loaded;           // false = using defaults, not synced yet
};

// Default companions (used before first Duat sync)
inline void companion_default_raven(Companion* c) {
    strlcpy(c->id,         "raven",    sizeof(c->id));
    strlcpy(c->name,       "RAVEN",    sizeof(c->name));
    strlcpy(c->class_name, "Scout",    sizeof(c->class_name));
    c->level     = 1;
    c->hp        = 30;
    c->max_hp    = 30;
    c->attack    = 6;
    c->defense   = 3;
    c->xp        = 0;
    c->xp_to_next= 100;
    c->wins      = 0;
    c->losses    = 0;
    c->loaded    = false;
}

inline void companion_default_scarab(Companion* c) {
    strlcpy(c->id,         "scarab",   sizeof(c->id));
    strlcpy(c->name,       "SCARAB",   sizeof(c->name));
    strlcpy(c->class_name, "Courier",  sizeof(c->class_name));
    c->level     = 1;
    c->hp        = 28;
    c->max_hp    = 28;
    c->attack    = 5;
    c->defense   = 5;
    c->xp        = 0;
    c->xp_to_next= 100;
    c->wins      = 0;
    c->losses    = 0;
    c->loaded    = false;
}

inline void companion_default_griffin(Companion* c) {
    strlcpy(c->id,         "griffin",  sizeof(c->id));
    strlcpy(c->name,       "GRIFFIN",  sizeof(c->name));
    strlcpy(c->class_name, "Sentinel", sizeof(c->class_name));
    c->level     = 1;
    c->hp        = 37;
    c->max_hp    = 37;
    c->attack    = 7;
    c->defense   = 4;
    c->xp        = 0;
    c->xp_to_next= 100;
    c->wins      = 0;
    c->losses    = 0;
    c->loaded    = false;
}

// ── Device identity ──────────────────────────────────────────
struct DeviceConfig {
    char device_id[20];    // e.g. "companion_1"
    char comp_id[20];      // e.g. "raven" or "griffin"
};

#ifndef DEVICE_ID_DEFAULT
#define DEVICE_ID_DEFAULT "companion_1"
#endif
#ifndef COMP_ID_DEFAULT
#define COMP_ID_DEFAULT "raven"
#endif

inline void device_config_load(DeviceConfig* cfg) {
    Preferences prefs;
    prefs.begin("companion", true);
    String did = prefs.getString("device_id", DEVICE_ID_DEFAULT);
    String cid = prefs.getString("comp_id",   COMP_ID_DEFAULT);
    prefs.end();
    strlcpy(cfg->device_id, did.c_str(), sizeof(cfg->device_id));
    strlcpy(cfg->comp_id,   cid.c_str(), sizeof(cfg->comp_id));
}

inline void device_config_save(DeviceConfig* cfg) {
    Preferences prefs;
    prefs.begin("companion", false);
    prefs.putString("device_id", cfg->device_id);
    prefs.putString("comp_id",   cfg->comp_id);
    prefs.end();
}

// ── Battle record (local queue for Duat sync) ─────────────────
#define MAX_PENDING_BATTLES 10

struct BattleRecord {
    char     opponent_name[20];
    bool     won;
    int      rounds;
    int      hp_remaining;
    uint32_t timestamp;
    bool     synced;
};

// ── Battle result ─────────────────────────────────────────────
enum BattleWinner { BATTLE_ATTACKER, BATTLE_DEFENDER, BATTLE_DRAW };

struct BattleResult {
    BattleWinner winner;
    int          rounds;
    int          attacker_hp;
    int          defender_hp;
};

inline BattleResult simulate_battle(const Companion* a, const Companion* b, uint32_t seed) {
    srand(seed);
    int aHP = a->hp;
    int bHP = b->hp;
    int round = 0;
    while (aHP > 0 && bHP > 0 && round < 100) {
        int dmg = max(1, a->attack - b->defense + (rand() % 5));
        bHP -= dmg;
        if (bHP <= 0) break;
        dmg = max(1, b->attack - a->defense + (rand() % 5));
        aHP -= dmg;
        round++;
    }
    BattleResult r;
    r.rounds       = round;
    r.attacker_hp  = max(0, aHP);
    r.defender_hp  = max(0, bHP);
    r.winner       = (aHP > 0) ? BATTLE_ATTACKER : (bHP > 0) ? BATTLE_DEFENDER : BATTLE_DRAW;
    return r;
}
