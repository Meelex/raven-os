#pragma once

// ── E-Paper SPI pins ─────────────────────────────────────────
#define EPD_DC_PIN    10
#define EPD_CS_PIN    11
#define EPD_SCK_PIN   12
#define EPD_MOSI_PIN  13
#define EPD_RST_PIN   9
#define EPD_BUSY_PIN  8
#define EPD_PWR_PIN   6

// ── Display size ─────────────────────────────────────────────
#define EPD_WIDTH   200
#define EPD_HEIGHT  200

// ── Buttons ──────────────────────────────────────────────────
#define BTN_A_PIN   0   // BOOT button — cycle forward / confirm
#define BTN_B_PIN   18  // PWR button  — cycle back / secondary / long=sleep

// Press durations (ms)
#define BTN_DEBOUNCE_MS  50
#define BTN_LONG_MS      600
#define BTN_SLEEP_MS     2000  // hold PWR 2s to sleep

// ── Hardware ─────────────────────────────────────────────────
#define LED_PIN      21   // WS2812B NeoPixel (ESP32-S3-Zero base board)
#define BATT_ADC_PIN 4    // ADC1_CH3 — battery voltage divider (÷2)
#define VBAT_EN_PIN  17   // enable battery ADC rail (active HIGH)

// ── Status bar ────────────────────────────────────────────────
#define STATUS_BAR_H 10  // height reserved at top of every screen

// ── Display colors (Adafruit GFX convention) ─────────────────
#define BLACK  0
#define WHITE  1

// ── BLE ──────────────────────────────────────────────────────
#define BATTLE_SERVICE_UUID    "4a657264-0001-0001-0001-000000000001"
#define BATTLE_CHAR_STATS      "4a657264-0001-0001-0001-000000000002"
#define BATTLE_CHAR_CONTROL    "4a657264-0001-0001-0001-000000000003"
#define BATTLE_CHAR_RESULT     "4a657264-0001-0001-0001-000000000004"
#define BLE_DEVICE_NAME        "RAVEN_BATTLE"

// ── Sleep / refresh ──────────────────────────────────────────
#define FULL_REFRESH_INTERVAL_MS  30000   // full EPD refresh every 30s to clear ghosting
#define DUAT_SYNC_INTERVAL_MS     60000   // background Duat sync every 60s

// ── Storage keys (Preferences) ───────────────────────────────
#define PREF_NS         "companion"
#define PREF_DEVICE_ID  "device_id"
#define PREF_COMP_ID    "comp_id"
