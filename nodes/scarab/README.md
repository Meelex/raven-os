# Scarab Node

Pi Zero 2 W acting as a travel router and WireGuard VPN client. Scarab tunnels all traffic back to the home network through a VPS relay. It also runs a headless dungeon game agent.

---

## Hardware

- Raspberry Pi Zero 2 W
- MicroSD 16GB+
- USB power bank or phone charger
- Case optional (often runs open for heat dissipation)

---

## OS Setup

```bash
# Flash Raspberry Pi OS Bookworm (64-bit Lite, headless)
# Pre-configure SSH and WiFi in Raspberry Pi Imager

ssh scarab@192.168.1.2

sudo apt update
sudo apt install -y wireguard python3-pip

pip3 install requests --break-system-packages
```

---

## WireGuard Setup

```bash
# Run the setup script
bash setup_scarab.sh
```

Or manually:

```bash
# Copy WireGuard client config
sudo cp scarab.conf /etc/wireguard/wg0.conf
sudo chmod 600 /etc/wireguard/wg0.conf

# Enable on boot
sudo systemctl enable --now wg-quick@wg0

# Verify tunnel is up
sudo wg show
```

See [docs/NETWORK.md](../../docs/NETWORK.md) for WireGuard config format and VPS relay setup.

**iPhone hotspot note:** Pi Zero 2 W only supports 2.4 GHz WiFi. On iPhone, enable Settings → Personal Hotspot → "Maximize Compatibility" to force 2.4 GHz.

---

## Files

| File | Purpose |
|------|---------|
| `scarab_agent.py` | Headless dungeon AI — auto-plays in Ladder Slasher on Duat |
| `scarab_dungeon.py` | Dungeon game logic for Scarab's display (if display is attached) |
| `scarab_quest.py` | Quest system integration |
| `setup_scarab.sh` | Full Scarab setup script |

---

## Dungeon Agent

Scarab runs an autonomous dungeon agent that connects to the game server on Duat and plays the dungeon crawler:

```bash
# Run manually
python3 scarab_agent.py

# Or as a systemd service
sudo nano /etc/systemd/system/scarab-agent.service
```

```ini
[Unit]
Description=Scarab Dungeon Agent
After=network.target wg-quick@wg0.service

[Service]
User=scarab
WorkingDirectory=/home/scarab
ExecStart=python3 /home/scarab/scarab_agent.py
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
```

The agent uses Scarab's identity (`scarab` in `socket.gethostname()`). It will warn and continue if the hostname check fails.

---

## Self-Healing Behavior

Scarab detects Duat going offline and falls back gracefully — the user connected through Scarab never notices. When Duat returns, Scarab reconnects automatically via persistent WireGuard keepalives.

---

## Network Identity

| Property | Value |
|----------|-------|
| LAN IP | 192.168.1.2 |
| WireGuard tunnel IP | 172.16.0.2 |
| Hostname | scarab |
