#pragma once
#include <Arduino.h>
#include "companion.h"

enum BleState {
    BLE_IDLE,
    BLE_ADVERTISING,   // waiting for challenger
    BLE_SCANNING,      // looking for opponents
    BLE_CONNECTING,    // challenger connecting to host
    BLE_EXCHANGING,    // stats + seed exchange in progress
    BLE_SIMULATING,    // running local sim
    BLE_DONE,          // result available
    BLE_ERROR,
};

struct BleOpponent {
    char     name[20];
    char     comp_id[20];
    int      hp;
    int      max_hp;
    int      attack;
    int      defense;
    char     address[20];
};

// ── Scan results ──────────────────────────────────────────────
#define MAX_OPPONENTS 4

extern BleOpponent ble_found[MAX_OPPONENTS];
extern int         ble_found_count;
extern BleState    ble_state;
extern BattleResult ble_result;
extern BleOpponent  ble_opponent;   // the one we fought

// ── API ───────────────────────────────────────────────────────
void ble_init(const Companion* my_companion);
void ble_start_advertising();     // I am ready to fight — be the host
void ble_start_scanning();        // I want to fight — find hosts
void ble_challenge(int idx);      // challenge found[idx]
void ble_cancel();                // cancel current BLE op
void ble_tick();                  // call from loop()
