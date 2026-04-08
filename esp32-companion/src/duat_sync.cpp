#include "duat_sync.h"
#include "secrets.h"
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>

static bool _online = false;

bool duat_connect_wifi() {
    if (WiFi.status() == WL_CONNECTED) {
        _online = true;
        return true;
    }
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

    uint32_t start = millis();
    while (WiFi.status() != WL_CONNECTED) {
        delay(200);
        if (millis() - start > 12000) {
            _online = false;
            return false;
        }
    }
    _online = true;
    return true;
}

void duat_disconnect_wifi() {
    WiFi.disconnect(true);
    _online = false;
}

bool duat_is_online() {
    return _online && (WiFi.status() == WL_CONNECTED);
}

bool duat_fetch_companion(const char* comp_id, Companion* out) {
    if (!duat_is_online()) return false;

    char url[128];
    snprintf(url, sizeof(url), "http://%s:%d/api/companion/%s",
             DUAT_HOST, DUAT_PORT, comp_id);

    HTTPClient http;
    http.begin(url);
    http.setTimeout(5000);
    int code = http.GET();
    if (code != 200) {
        http.end();
        return false;
    }

    String body = http.getString();
    http.end();

    JsonDocument doc;
    DeserializationError err = deserializeJson(doc, body);
    if (err) return false;

    strlcpy(out->id,         comp_id,                     sizeof(out->id));
    strlcpy(out->name,       doc["name"] | comp_id,       sizeof(out->name));
    strlcpy(out->class_name, doc["class_name"] | "Scout", sizeof(out->class_name));
    out->level      = doc["level"]      | 1;
    out->hp         = doc["hp"]         | out->max_hp;
    out->max_hp     = doc["max_hp"]     | 30;
    out->attack     = doc["attack"]     | 6;
    out->defense    = doc["defense"]    | 3;
    out->xp         = doc["xp"]         | 0;
    out->xp_to_next = doc["xp_to_next"] | 100;
    out->wins       = doc["wins"]       | 0;
    out->losses     = doc["losses"]     | 0;
    out->loaded     = true;
    return true;
}

bool duat_push_battle(const char* device_id, const BattleRecord* rec) {
    if (!duat_is_online()) return false;

    char url[128];
    snprintf(url, sizeof(url), "http://%s:%d/api/battle/result", DUAT_HOST, DUAT_PORT);

    JsonDocument doc;
    doc["device_id"]     = device_id;
    doc["opponent_name"] = rec->opponent_name;
    doc["won"]           = rec->won;
    doc["rounds"]        = rec->rounds;
    doc["hp_remaining"]  = rec->hp_remaining;
    doc["timestamp"]     = rec->timestamp;

    String body;
    serializeJson(doc, body);

    HTTPClient http;
    http.begin(url);
    http.addHeader("Content-Type", "application/json");
    http.setTimeout(5000);
    int code = http.POST(body);
    http.end();
    return (code == 200 || code == 201);
}

void duat_flush_queue(const char* device_id,
                      BattleRecord* queue, int queue_len)
{
    for (int i = 0; i < queue_len; i++) {
        if (!queue[i].synced) {
            if (duat_push_battle(device_id, &queue[i])) {
                queue[i].synced = true;
            }
        }
    }
}
