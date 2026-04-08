# System Architecture

Full architecture reference for the Raven OS network.

---

## Network Topology

```
Internet
    │
    └── Hetzner VPS (YOUR_VPS_IP:443) — WireGuard relay
            │ WireGuard tunnel
            └── Duat (Pi 5, 192.168.1.5)
                    │
                    └── LAN 192.168.1.0/24
                            ├── Legiom (Windows, 192.168.1.6)
                            ├── Raven  (Pi Zero 2, 192.168.1.3)
                            ├── Scarab (Pi Zero 2, 192.168.1.2)
                            └── Griffin (Pi Zero 2, 192.168.1.8)
```

The Hetzner VPS exists specifically to relay WireGuard traffic — T-Mobile CGNAT blocks inbound connections to Duat. External devices (phones, Scarab on a foreign network) tunnel through the VPS to reach Duat on the LAN.

---

## Legiom (Windows Workstation)

```
watchdog_windows.py
    ├── FileSystemWatcher — monitors C:\Users\adilt\Downloads
    ├── SHA256 hasher — hashes every new file
    ├── Hash DB client — queries Duat:6174
    ├── Lock client — sends lock requests to Duat:6176
    ├── Heartbeat sender — UDP packets to Raven:7743 every 30s
    ├── Slasher tab — polls game leaderboard from Duat:5000
    └── Ring tab — polls ring biometrics from Duat:7744
```

Config stored at: `%APPDATA%\RavenOS\watchdog_config.json`

---

## Duat (Pi 5 — Home Base)

```
Duat (192.168.1.5)
    │
    ├── Hash DB service (port 6174)          [raven_hashdb.py]
    │       ├── SQLite: /home/duat/hashes.db
    │       ├── MalwareBazaar daily feed (anonymous)
    │       ├── CIRCL hashlookup (live fallback for misses)
    │       └── Anonymous submission of unknown hashes back to MalwareBazaar
    │
    ├── Unlock service (port 6176)           [duat_unlock.py]
    │       ├── Receives lock request from Watchdog
    │       ├── SSH as YourUser@Legiom → icacls /deny YourUser:(RX)
    │       ├── Forwards alert to Raven:6175
    │       └── Receives unlock/deny decision from Raven → SSH → icacls /grant YourUser:(F)
    │
    ├── Ring service (port 7744)             [raven_ring.py]
    │       ├── BLE connection to COLMI R02 ring
    │       ├── Biometrics: HR, SpO2, steps, battery
    │       ├── SQLite: /home/raven/raven_ring.db
    │       ├── Rolling baseline + confidence score
    │       ├── Gesture detection (Iron House editing context only)
    │       └── REST API: /status /biometrics /baseline /gesture
    │
    ├── Display (optional)                   [duat_display.py]
    │       └── MHS 3.5" LCD — color dashboard via /dev/fb0 (PIL + numpy)
    │
    └── Game server (port 5000)              [app.py / ladder_slasher_server.py]
            ├── Flask HTTP server
            ├── SQLite: /opt/raven-slasher/ladder_slasher.db
            ├── Serves ladder_slasher.html
            └── REST API — full game state, co-op sessions, chat, leaderboard
```

---

## Raven (Pi Zero 2 — Portable E-Ink Deck)

```
raven_deck.py (main OS loop)
    │
    ├── E-ink display driver (Waveshare 2.13" V4)
    │       └── Waveshare EPD HAT over SPI + GPIO
    │
    ├── Touch input (gt1151 capacitive controller)
    │       ├── Left half tap = cycle/action
    │       └── Right half tap = select/back
    │
    ├── LAN scanner
    │       ├── Ping sweep (socket-based)
    │       └── Port scan (nmap)
    │
    ├── Bluetooth scanner (hcitool lescan)
    ├── WiFi AP scanner (iwlist)
    │
    ├── Remote scan (SSH to YourUser@Legiom)
    │       └── Lists Downloads folder contents
    │
    ├── Quarantine engine (quarantine.py)
    │
    ├── Heartbeat listener (UDP 7743)
    │       └── Watches for WATCHDOG|Legiom|YourUser|ALIVE every 30s
    │
    └── Flask unlock API (port 6175)
            ├── POST /alert — receives lock alerts from Duat
            ├── GET /queue — returns pending unlock decisions
            └── POST /decision — Raven POSTs UNLOCK or DENY back to Duat
```

**Menu structure:**
```
MAIN
└── MENU
    ├── 1. LAN PING SWEEP
    ├── 2. VIEW LAN INTEL
    ├── 3. AUTO-AUDIT LAN
    ├── 4. BLUETOOTH
    ├── 5. WIFI APs
    ├── 6. SYS HEALTH
    ├── 7. VIEW LOGS
    ├── 8. REMOTE SCAN PC
    ├── 9. UNLOCK QUEUE
    ├── 10. SHUT DOWN
    └── <- BACK TO MAIN
```

MAIN screen left tap injects a test unlock alert for chain testing.

---

## Scarab (Pi Zero 2 — Travel Router)

```
scarab_agent.py (headless dungeon AI)
    └── Polls Duat game server → autonomous dungeon exploration

WireGuard client
    └── Tunnels all traffic through Hetzner VPS → Duat LAN
```

When Scarab is on a foreign network (hotel WiFi, etc.) it automatically brings up the WireGuard tunnel. your phone can also hotspot-share the tunnel — devices connecting to the hotspot get VPN access without their own WireGuard install.

---

## Griffin (Pi Zero 2 — Companion Node)

```
griffin_companion.py + griffin_display.py
    ├── E-ink display (Waveshare 2.13" V4 — same as Raven)
    ├── Dungeon crawler UI on display
    └── griffin_agent.py (headless dungeon AI, systemd autostart)
```

Griffin runs as an autonomous dungeon participant. When Raven is also online, both characters appear in the shared realm on Duat.

---

## ESP32 Companion Device

```
esp32-companion/ (PlatformIO/Arduino firmware)
    ├── display.cpp — e-paper rendering (Waveshare 1.54" 200x200)
    ├── buttons.cpp — physical button handling (2 side buttons)
    ├── companion.h — companion identity + stats
    ├── dungeon.cpp — local dungeon state
    ├── ble_battle.cpp — BLE peer-to-peer battle protocol
    ├── duat_sync.cpp — WiFi sync of battle results to Duat
    ├── config.h — WiFi SSID, Duat IP, device config
    └── secrets.h — WiFi password (not committed)
```

Three build targets: `raven`, `griffin`, `scarab` — each device gets its own companion identity.

---

## File Lock / Unlock Flow (full detail)

```
1. File downloaded to C:\Users\adilt\Downloads
2. watchdog_windows.py detects new file via FileSystemWatcher
3. SHA256 hash computed
4. POST http://192.168.1.5:6174/lookup {hash: "..."}
5a. CLEAN → no action, log entry
5b. UNKNOWN → log entry, anonymous submission to MalwareBazaar
5c. MALICIOUS:
    6. POST http://192.168.1.5:6176/lock {file: "...", hash: "..."}
    7. Duat unlockd: SSH YourUser@192.168.1.6
       → icacls "C:\Users\YourUser\Downloads\<file>" /deny YourUser:(RX)
    8. POST http://192.168.1.3:6175/alert {file: "...", hash: "...", verdict: "MALICIOUS"}
    9. Raven e-ink: THREAT DETECTED screen
   10. User taps left → dismissed to unlock queue (SEEN flag set)
   11. User navigates: MENU → UNLOCK QUEUE → selects file
   12. UNLOCK CONFIRM screen appears
   13. Tap left (UNLOCK) or right (DENY)
   14. POST http://192.168.1.5:6176/decision {file: "...", action: "UNLOCK"|"DENY"}
   15a. UNLOCK: SSH → icacls ... /grant YourUser:(F)
   15b. DENY: file stays locked, logged
```

---

## SSH Architecture

| From | To | Key | Purpose |
|------|----|-----|---------|
| Raven | YourUser@Legiom | `/home/raven/.ssh/pc_access_key` | Remote Downloads scan |
| Duat | YourUser@Legiom | `/home/duat/.ssh/duat_horus_key` | Lock/unlock files via icacls |
| Admin | raven@Raven | standard SSH key | Deploy/manage Raven |
| Admin | duat@Duat | standard SSH key | Deploy/manage Duat |

**Legiom (Windows) SSH notes:**

`YourUser` is an Administrator account. Windows OpenSSH for admin accounts ignores per-user `authorized_keys`. Use the system-wide file instead:

```
C:\ProgramData\ssh\administrators_authorized_keys
```

Permissions must be: `NT AUTHORITY\SYSTEM:F` and `BUILTIN\Administrators:F` only.

`sshd_config` (`C:\ProgramData\ssh\sshd_config`) must contain:
```
AuthorizedKeysFile __PROGRAMDATA__/ssh/administrators_authorized_keys
```

The OpenSSH firewall rule is scoped to Private network profile only. Set your home WiFi to Private once:
```powershell
Set-NetConnectionProfile -Name "YourNetworkName" -NetworkCategory Private
```

---

## Port Reference

| Port | Protocol | Service | Host |
|------|----------|---------|------|
| 6174 | TCP | Hash DB API | Duat |
| 6175 | TCP | Raven Unlock API | Raven |
| 6176 | TCP | Duat Unlock Service | Duat |
| 7743 | UDP | Watchdog heartbeat listener | Raven |
| 7744 | TCP | Ring biometric API | Duat |
| 5000 | TCP | Game server (Ladder Slasher) | Duat |
| 22 | TCP | SSH | All nodes |
| 51820 | UDP | WireGuard | Duat / VPS |
