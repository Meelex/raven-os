#pragma once
#include <Arduino.h>
#include "companion.h"

// Returns true if WiFi connected and Duat reachable
bool duat_connect_wifi();
void duat_disconnect_wifi();
bool duat_is_online();

// Fetch companion stats from Duat — returns true on success
bool duat_fetch_companion(const char* comp_id, Companion* out);

// Push a battle result to Duat — returns true on success
bool duat_push_battle(const char* device_id, const BattleRecord* rec);

// Flush all unsynced battle records from a queue
void duat_flush_queue(const char* device_id,
                      BattleRecord* queue, int queue_len);
