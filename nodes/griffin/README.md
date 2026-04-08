# Griffin Node

Pi Zero 2 W running an Egyptian griffin companion. E-ink display, dungeon game UI, and a headless game agent. Griffin is a companion to Raven — when both are online, both characters appear in the shared realm on Duat.

---

## Hardware

- Raspberry Pi Zero 2 W
- Waveshare 2.13" Touch E-Ink HAT (V4) — same model as Raven
- USB power bank
- MicroSD 16GB+

The HAT connects directly to the 40-pin GPIO header. Same wiring as Raven.

---

## OS Setup

```bash
# Flash Raspberry Pi OS Bookworm (64-bit Lite, headless)
ssh griffin@192.168.1.8

sudo apt update
sudo apt install -y python3-venv python3-pip

python3 -m venv ~/griffin_env
source ~/griffin_env/bin/activate
pip install Pillow spidev RPi.GPIO gpiod flask requests

# Enable SPI and I2C
sudo raspi-config
# → Interface Options → SPI → Enable
# → Interface Options → I2C → Enable
```

---

## Files

| File | Purpose |
|------|---------|
| `griffin_companion.py` | E-ink dungeon UI — displays companion sprite, dungeon state, handles touch |
| `griffin_display.py` | Waveshare 2.13" V4 display driver |
| `griffin_agent.py` | Headless dungeon agent — auto-plays in Ladder Slasher on Duat |

---

## Deploy

```bash
# Copy files to Griffin
scp griffin_companion.py griffin_display.py griffin_agent.py griffin@192.168.1.8:/home/griffin/griffin/
```

---

## Systemd Service

Griffin's agent runs on boot via systemd:

```bash
sudo nano /etc/systemd/system/griffin-agent.service
```

```ini
[Unit]
Description=Griffin Dungeon Agent
After=network.target

[Service]
User=griffin
WorkingDirectory=/home/griffin/griffin
ExecStart=/home/griffin/griffin_env/bin/python -u /home/griffin/griffin_agent.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now griffin-agent
sudo journalctl -u griffin-agent -f
```

---

## Dungeon Game Integration

Griffin participates in the shared dungeon realm hosted on Duat (port 5000). It operates in two modes:

**Solo mode:** Game state stored locally on Griffin. Plays offline.

**Realm mode:** Connects to Duat `/quest/*` API. Raven and Griffin appear as separate characters in the same dungeon. Either can act. State is server-authoritative.

---

## Network Identity

| Property | Value |
|----------|-------|
| LAN IP | 192.168.1.8 |
| Hostname | griffin |
| Claim PIN | 3847 |
