# Raven OS

**Developed by Aaron Diltz**

A sovereign home security and infrastructure network built on Raspberry Pi hardware.
All data stays local. No third-party cloud. No signups. No API keys.

![Duat home base dashboard](assets/duat/duat_main_screen.jpg)

| Portable nodes — all three Pi Zero 2 W + ESP32-S3 companions | Pi Zero 2 W + e-paper HAT |
|---|---|
| ![All nodes](assets/hardware/all_nodes_portable.jpg) | ![Pi Zero e-paper](assets/hardware/pi_zero_epaper_nodes.jpg) |

---

## What It Is

Raven OS is a multi-node home network built around consent-based security design. It monitors your Downloads folder for malware, physically locks and unlocks files via a handheld e-ink device, tracks biometrics from a smart ring, and hosts a local dungeon crawler game — all on hardware you own, on a network you control.

---

## The Nodes

| Node | Hardware | Role |
|------|----------|------|
| **Raven** | Pi Zero 2 W | Portable e-ink security deck — network recon, file unlock UI |
| **Duat** | Pi 5 | Home base — malware hash DB, file lock/unlock service, ring biometrics, game server |
| **Scarab** | Pi Zero 2 W | Travel router — WireGuard VPN client, dungeon game pet |
| **Griffin** | Pi Zero 2 W | Companion node — e-ink display, dungeon game agent |
| **Legiom** | Windows PC | Main workstation — Watchdog GUI that monitors Downloads and triggers locks |
| **ESP32 Companion** | Waveshare ESP32-S3 1.54" ePaper | Portable companion devices — BLE battle, dungeon sync |

---

## How the Security System Works

```
File lands in Downloads (Windows PC)
    → Watchdog hashes it (SHA256)
    → Queries Duat Hash DB (port 6174) — MalwareBazaar feed + CIRCL fallback
    → MALICIOUS verdict
    → Watchdog sends lock request to Duat (port 6176)
    → Duat SSH into PC → icacls /deny — file locked at OS level
    → Duat forwards alert to Raven (port 6175)
    → Raven e-ink shows THREAT DETECTED
    → Physical tap to dismiss → file goes to UNLOCK QUEUE
    → Navigate Menu → UNLOCK QUEUE → select file
    → Tap UNLOCK or DENY on Raven e-ink display
    → Raven POSTs decision to Duat
    → Duat SSH into PC → icacls /grant — file accessible again
```

Physical confirmation is required to unlock. There is no remote software bypass.

---

## Repository Structure

```
raven-os/
├── LICENSE
├── README.md                  ← you are here
├── docs/
│   ├── ARCHITECTURE.md        ← full system architecture and data flows
│   ├── HARDWARE.md            ← bill of materials for every node
│   ├── NETWORK.md             ← LAN setup, WireGuard VPN, SSH architecture
│   └── LADDER_SLASHER.md      ← game design, classes, rooms, co-op
├── nodes/
│   ├── raven/                 ← Pi Zero 2 — portable e-ink security deck
│   ├── duat/                  ← Pi 5 — home base server
│   ├── scarab/                ← Pi Zero 2 — travel router / VPN
│   └── griffin/               ← Pi Zero 2 — companion node
├── legiom/                    ← Windows workstation (Watchdog GUI)
├── game/                      ← Ladder Slasher dungeon crawler
│   ├── app.py                 ← Flask game server (run on Duat)
│   └── ladder_slasher.html    ← Full game client (served by Flask)
└── esp32-companion/           ← ESP32-S3 ePaper companion device firmware
    ├── platformio.ini
    └── src/
```

---

## Key Design Principles

**Sovereign infrastructure.** All data stays local. Community threat feeds (MalwareBazaar) are consumed anonymously. Unknown hashes are submitted back anonymously — contributing without identifying.

**Consent-based design.** Physical confirmation required for every security decision. No automated unlocks. No remote bypasses.

**Security by default.** Authentication and input sanitization built in from the start, not added later.

**Self-healing network.** Nodes detect each other going offline and fall back gracefully. The user never notices.

---

## Quick Start

See the README in each subdirectory for node-specific setup instructions.

- **Start here:** [docs/HARDWARE.md](docs/HARDWARE.md) — buy list and hardware assembly
- **Then:** [docs/NETWORK.md](docs/NETWORK.md) — LAN config and SSH keys
- **Then deploy in order:** Duat → Raven → Legiom Watchdog → Scarab/Griffin as desired

---

## Adapting for Your Setup

The source code contains hardcoded values from the reference deployment (username `YourUser`, LAN IPs `192.168.1.x`, paths like `C:\Users\adilt\Downloads`). Search-replace these for your environment:

| Find | Replace with |
|------|-------------|
| `YourUser` | your Windows username |
| `192.168.1.5` | your Duat IP |
| `192.168.1.3` | your Raven IP |
| `192.168.1.6` | your Legiom/PC IP |

The Watchdog config file (`%APPDATA%\RavenOS\watchdog_config.json`) handles most of these at runtime — the hardcoded values are fallbacks.

---

## License

MIT — see [LICENSE](LICENSE). Use it, modify it, build on it.
