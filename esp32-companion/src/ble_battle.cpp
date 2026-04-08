#include "ble_battle.h"
#include "config.h"
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEClient.h>
#include <BLEScan.h>
#include <BLEAdvertisedDevice.h>
#include <ArduinoJson.h>

// ── Globals ───────────────────────────────────────────────────
BleOpponent  ble_found[MAX_OPPONENTS];
int          ble_found_count = 0;
BleState     ble_state       = BLE_IDLE;
BattleResult ble_result;
BleOpponent  ble_opponent;

static const Companion* _my_companion = nullptr;

// ── BLE objects ───────────────────────────────────────────────
static BLEServer*         _server      = nullptr;
static BLEService*        _service     = nullptr;
static BLECharacteristic* _char_stats  = nullptr;
static BLECharacteristic* _char_ctrl   = nullptr;
static BLECharacteristic* _char_result = nullptr;
static BLEScan*           _scan        = nullptr;
static BLEClient*         _client      = nullptr;
static bool               _connected   = false;

// ── Helpers ───────────────────────────────────────────────────
static String companion_to_json(const Companion* c) {
    JsonDocument doc;
    doc["name"]    = c->name;
    doc["comp_id"] = c->id;
    doc["hp"]      = c->hp;
    doc["max_hp"]  = c->max_hp;
    doc["atk"]     = c->attack;
    doc["def"]     = c->defense;
    String out;
    serializeJson(doc, out);
    return out;
}

static bool json_to_opponent(const String& json, BleOpponent* opp) {
    JsonDocument doc;
    if (deserializeJson(doc, json) != DeserializationError::Ok) return false;
    strlcpy(opp->name,    doc["name"]    | "Unknown", sizeof(opp->name));
    strlcpy(opp->comp_id, doc["comp_id"] | "raven",   sizeof(opp->comp_id));
    opp->hp      = doc["hp"]      | 30;
    opp->max_hp  = doc["max_hp"]  | 30;
    opp->attack  = doc["atk"]     | 6;
    opp->defense = doc["def"]     | 3;
    return true;
}

// ── Server callbacks ──────────────────────────────────────────
class ServerCallbacks : public BLEServerCallbacks {
    void onConnect(BLEServer* s) override    { _connected = true; }
    void onDisconnect(BLEServer* s) override { _connected = false; }
};

class CtrlCallback : public BLECharacteristicCallbacks {
    void onWrite(BLECharacteristic* c) override {
        // Challenger wrote a seed to CONTROL → run simulation
        String val = c->getValue().c_str();
        uint32_t seed = (uint32_t)strtoul(val.c_str(), nullptr, 10);

        // Read opponent stats from STATS characteristic
        String stats_json = _char_stats->getValue().c_str();
        // We ARE the host; challenger wrote seed, their stats were written to STATS
        BleOpponent opp;
        json_to_opponent(stats_json, &opp);
        strlcpy(ble_opponent.name,    opp.name,    sizeof(ble_opponent.name));
        strlcpy(ble_opponent.comp_id, opp.comp_id, sizeof(ble_opponent.comp_id));
        ble_opponent.hp      = opp.hp;
        ble_opponent.max_hp  = opp.max_hp;
        ble_opponent.attack  = opp.attack;
        ble_opponent.defense = opp.defense;

        Companion opp_c;
        opp_c.hp      = opp.hp;
        opp_c.max_hp  = opp.max_hp;
        opp_c.attack  = opp.attack;
        opp_c.defense = opp.defense;

        ble_result = simulate_battle(_my_companion, &opp_c, seed);

        // Write result back
        char res_str[64];
        snprintf(res_str, sizeof(res_str), "%d,%d,%d,%d",
                 (int)ble_result.winner, ble_result.rounds,
                 ble_result.attacker_hp, ble_result.defender_hp);
        _char_result->setValue(res_str);
        _char_result->notify();

        ble_state = BLE_DONE;
    }
};

// ── Scan callback ─────────────────────────────────────────────
class ScanCallbacks : public BLEAdvertisedDeviceCallbacks {
    void onResult(BLEAdvertisedDevice dev) override {
        if (!dev.haveServiceUUID()) return;
        if (!dev.isAdvertisingService(BLEUUID(BATTLE_SERVICE_UUID))) return;
        if (ble_found_count >= MAX_OPPONENTS) return;

        BleOpponent& opp = ble_found[ble_found_count];
        strlcpy(opp.name,    dev.getName().c_str(), sizeof(opp.name));
        strlcpy(opp.address, dev.getAddress().toString().c_str(), sizeof(opp.address));
        if (strlen(opp.name) == 0) strlcpy(opp.name, "Unknown", sizeof(opp.name));
        ble_found_count++;
    }
};

// ── Public API ────────────────────────────────────────────────
void ble_init(const Companion* my_companion) {
    _my_companion = my_companion;
    BLEDevice::init(BLE_DEVICE_NAME);
    ble_state = BLE_IDLE;
}

void ble_start_advertising() {
    ble_state = BLE_ADVERTISING;

    _server  = BLEDevice::createServer();
    _server->setCallbacks(new ServerCallbacks());

    _service     = _server->createService(BATTLE_SERVICE_UUID);
    _char_stats  = _service->createCharacteristic(BATTLE_CHAR_STATS,
                    BLECharacteristic::PROPERTY_READ | BLECharacteristic::PROPERTY_WRITE);
    _char_ctrl   = _service->createCharacteristic(BATTLE_CHAR_CONTROL,
                    BLECharacteristic::PROPERTY_WRITE);
    _char_result = _service->createCharacteristic(BATTLE_CHAR_RESULT,
                    BLECharacteristic::PROPERTY_READ | BLECharacteristic::PROPERTY_NOTIFY);

    _char_ctrl->setCallbacks(new CtrlCallback());

    // Publish our own stats
    String my_stats = companion_to_json(_my_companion);
    _char_stats->setValue(my_stats.c_str());

    _service->start();

    BLEAdvertising* adv = BLEDevice::getAdvertising();
    adv->addServiceUUID(BATTLE_SERVICE_UUID);
    adv->setScanResponse(true);
    adv->setMinPreferred(0x06);
    BLEDevice::startAdvertising();
}

void ble_start_scanning() {
    ble_state = BLE_SCANNING;
    ble_found_count = 0;

    _scan = BLEDevice::getScan();
    _scan->setAdvertisedDeviceCallbacks(new ScanCallbacks());
    _scan->setActiveScan(true);
    _scan->setInterval(100);
    _scan->setWindow(99);
    _scan->start(5, false);  // 5 second scan (synchronous)
    ble_state = BLE_IDLE;   // scan complete; caller checks ble_found_count
}

void ble_challenge(int idx) {
    if (idx < 0 || idx >= ble_found_count) return;
    ble_state = BLE_CONNECTING;

    BleOpponent& target = ble_found[idx];
    _client = BLEDevice::createClient();

    BLEAddress addr(target.address);
    if (!_client->connect(addr)) {
        ble_state = BLE_ERROR;
        return;
    }

    ble_state = BLE_EXCHANGING;

    BLERemoteService* svc = _client->getService(BATTLE_SERVICE_UUID);
    if (!svc) { _client->disconnect(); ble_state = BLE_ERROR; return; }

    // Read host stats
    BLERemoteCharacteristic* r_stats = svc->getCharacteristic(BATTLE_CHAR_STATS);
    if (!r_stats) { _client->disconnect(); ble_state = BLE_ERROR; return; }
    String host_stats = r_stats->readValue().c_str();
    json_to_opponent(host_stats, &ble_opponent);

    // Write our stats to host
    String my_stats = companion_to_json(_my_companion);
    r_stats->writeValue(my_stats.c_str());

    // Generate shared seed, write to CONTROL
    BLERemoteCharacteristic* r_ctrl = svc->getCharacteristic(BATTLE_CHAR_CONTROL);
    if (!r_ctrl) { _client->disconnect(); ble_state = BLE_ERROR; return; }

    uint32_t seed = (uint32_t)(millis() ^ esp_random());
    char seed_str[16];
    snprintf(seed_str, sizeof(seed_str), "%u", seed);
    r_ctrl->writeValue(seed_str);

    // Run our local simulation with same seed
    ble_state = BLE_SIMULATING;
    Companion opp_c;
    opp_c.hp      = ble_opponent.hp;
    opp_c.max_hp  = ble_opponent.max_hp;
    opp_c.attack  = ble_opponent.attack;
    opp_c.defense = ble_opponent.defense;

    ble_result = simulate_battle(_my_companion, &opp_c, seed);

    _client->disconnect();
    ble_state = BLE_DONE;
}

void ble_cancel() {
    if (_client)  { _client->disconnect(); }
    if (_scan)    { _scan->stop(); }
    if (_server)  { BLEDevice::stopAdvertising(); }
    ble_state       = BLE_IDLE;
    ble_found_count = 0;
    _connected      = false;
}

void ble_tick() {
    // Scan is synchronous — state is set to BLE_IDLE in ble_start_scanning()
    // This tick is a no-op but kept for future async use
}
