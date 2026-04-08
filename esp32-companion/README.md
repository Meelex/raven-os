# ESP32 Companion Device

Portable handheld companion devices built on the Waveshare ESP32-S3 1.54" e-Paper AIoT Development Board. Two units — one for each companion node (Raven, Griffin, and/or Scarab).

---

## Hardware

| Component | Part |
|-----------|------|
| Board | Waveshare ESP32-S3 1.54" e-Paper AIoT Development Board |
| Display | 200×200 px e-paper, black/white, built-in |
| Wireless | ESP32-S3: WiFi + BLE onboard |
| Input | 2 physical side buttons |
| Power | USB-C or LiPo battery |

Waveshare product page: https://www.waveshare.com/wiki/ESP32-S3-ePaper-1.54

---

## Features

- Companion sprite display with stats and dungeon state
- Cyclable menu via physical side buttons
- **BLE peer-to-peer battle** — two devices can battle locally with no server required
- **Duat sync** — battle results sync back to Duat over WiFi when available

---

## Build Targets

Three firmware targets defined in `platformio.ini`:

| Target | Identity | Companion ID |
|--------|----------|-------------|
| `raven` | Raven's companion | `companion_1` |
| `scarab` | Scarab's companion | `companion_2` |
| `griffin` | Griffin's companion | `companion_3` |

Flash with: `pio run -e raven --target upload`

---

## Setup

### 1. Install PlatformIO

1. Install VS Code
2. Extensions (Ctrl+Shift+X) → search "PlatformIO IDE" → Install
3. Reload VS Code when prompted
4. Wait for PlatformIO to finish installing

### 2. USB Driver

Plug the board in via USB data cable. Check Device Manager → "Ports (COM & LPT)".

If it shows as "Unknown Device", install the CH343 driver:
- https://www.wch-ic.com/downloads/CH343SER_EXE.html

### 3. Configure Secrets

Copy `src/secrets.h.example` to `src/secrets.h` and fill in:

```cpp
// src/secrets.h
#define WIFI_SSID     "YourNetworkName"
#define WIFI_PASSWORD "YourPassword"
#define DUAT_IP       "192.168.1.5"
#define DUAT_PORT     5000
```

`secrets.h` is not committed to the repository.

### 4. Configure Device

Edit `src/config.h` to set your Duat IP (or set it in `secrets.h`). The device ID and companion ID are set by build flags in `platformio.ini` — no manual editing needed.

### 5. Flash

```bash
# Open PlatformIO terminal
# Flash as Raven's companion
pio run -e raven --target upload

# Flash as Griffin's companion
pio run -e griffin --target upload

# Flash as Scarab's companion
pio run -e scarab --target upload

# Monitor serial output
pio device monitor
```

---

## Source Files

| File | Purpose |
|------|---------|
| `main.cpp` | Main loop — init, button handling, display update cycle |
| `config.h` | Network config, display dimensions, timing constants |
| `companion.h` | Companion identity struct, stats, level |
| `display.cpp/.h` | E-paper rendering — sprites, stats, menus |
| `buttons.cpp/.h` | Physical button debounce and event handling |
| `dungeon.cpp/.h` | Local dungeon state — rooms, combat, loot |
| `ble_battle.cpp/.h` | BLE peer-to-peer battle protocol |
| `duat_sync.cpp/.h` | WiFi sync of battle results to Duat REST API |
| `epaper_driver_bsp.cpp/.h` | Low-level e-paper driver (Waveshare BSP) |
| `secrets.h` | WiFi credentials (not committed — create locally) |

---

## Dependencies (auto-installed by PlatformIO)

```
adafruit/Adafruit GFX Library
adafruit/Adafruit BusIO
adafruit/Adafruit NeoPixel
bblanchon/ArduinoJson
```

---

## BLE Battle Protocol

Two companion devices can battle peer-to-peer over BLE without any server:

1. Player A holds button to scan for nearby companions
2. Player A selects Player B's device from the list
3. Both devices display battle UI
4. Combat is resolved locally on both devices simultaneously
5. Result is displayed on both screens
6. Next time either device has WiFi, result is synced to Duat

Battle results are stored locally if WiFi is unavailable and synced on next connection.
