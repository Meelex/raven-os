#pragma once
#include "companion.h"
#include <Arduino.h>
#include <Preferences.h>

// ── Timing ───────────────────────────────────────────────────
#define DUNGEON_TICK_MS    4000UL   // 4s per action (explore step or combat round)
#define DUNGEON_REGEN_MS  20000UL   // regen 1 HP every 20s while exploring
#define DUNGEON_DEAD_MS   30000UL   // 30s dead before respawn

// ── Encounter spacing ────────────────────────────────────────
#define DUNGEON_ENC_MIN   4         // min steps before encounter
#define DUNGEON_ENC_MAX   9         // max steps before encounter
#define DUNGEON_BOSS_KILLS 8        // area kills needed before boss can spawn

// ── Log ──────────────────────────────────────────────────────
#define DLOG_LEN  2                 // two display lines of combat log

// ── State machine ────────────────────────────────────────────
enum DungeonSub {
    DSUB_EXPLORE,
    DSUB_COMBAT,
    DSUB_LOOT,
    DSUB_LEVELUP,
    DSUB_DEAD,
};

// ── Enemy ────────────────────────────────────────────────────
struct DungeonEnemy {
    char name[16];
    int  hp;
    int  max_hp;
    int  atk;
    int  xp;
    int  gold;
    char special[12];   // "" | "devour" | "chaos" | "coil" | "plague"
    bool is_boss;
    bool coiled;        // APEP coil debuff on player
};

// ── Area names ───────────────────────────────────────────────
#define DUNGEON_AREA_COUNT 5
static const char* DUNGEON_AREAS[DUNGEON_AREA_COUNT] = {
    "DUAT WASTES", "FIELDS OF AARU", "VALLEY OF KINGS",
    "TEMPLE OF SET", "ISFET'S REALM"
};

// ── State ────────────────────────────────────────────────────
struct DungeonState {
    DungeonSub sub;
    bool     active;
    int      area;          // 0-4
    int      steps;         // steps since last encounter
    int      next_enc;      // steps threshold for next encounter
    int      area_kills;    // kills in current area
    int      gold;          // accumulated gold

    DungeonEnemy enemy;
    bool     enemy_active;

    uint32_t next_tick;     // millis() when next action fires
    uint32_t last_regen;
    uint32_t dead_at;       // millis() when player died

    char     log[DLOG_LEN][40];  // two-line display log
    char     loot_msg[40];       // loot/levelup reward text
    bool     event_ready;        // main loop should redraw screen
    bool     coiled;             // player is coiled (APEP skip-turn)
};

extern DungeonState dungeon;

void dungeon_init(Companion* c);
void dungeon_tick(Companion* c);    // call every loop()
void dungeon_save(const Companion* c);
void dungeon_load(Companion* c);
