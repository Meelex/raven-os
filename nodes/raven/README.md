# Raven Node

Pi Zero 2 W running the main Raven OS loop. This is the portable handheld security deck — network recon, malware alerts, and physical file unlock confirmation.

---

## Hardware

- Raspberry Pi Zero 2 W
- Waveshare 2.13" Touch E-Ink HAT (V4) — 250×122 px, capacitive touch
- USB power bank (10,000 mAh recommended)
- MicroSD 16GB+

The Waveshare HAT connects directly to the 40-pin GPIO header. No additional wiring.

---

## OS Setup

```bash
# Flash Raspberry Pi OS Bookworm (64-bit Lite, headless) to MicroSD
# Enable SSH and configure WiFi in Raspberry Pi Imager before flashing

# SSH in
ssh raven@192.168.1.3

# Install dependencies
sudo apt update
sudo apt install -y nmap bluez network-manager python3-venv

# Create Python environment
python3 -m venv ~/raven_env
source ~/raven_env/bin/activate
pip install Pillow spidev RPi.GPIO gpiod flask

# Enable SPI and I2C
sudo raspi-config
# → Interface Options → SPI → Enable
# → Interface Options → I2C → Enable
```

---

## SSH Keys

Raven needs a key to SSH into the Windows PC (Legiom) for remote Downloads scans:

```bash
# On Raven
ssh-keygen -t ed25519 -C "raven-os-remote-access" -f ~/.ssh/pc_access_key

# Copy the public key
cat ~/.ssh/pc_access_key.pub
```

Add the output to `C:\ProgramData\ssh\administrators_authorized_keys` on Legiom.

See [docs/NETWORK.md](../../docs/NETWORK.md) for Windows SSH setup details.

---

## Files

| File | Purpose |
|------|---------|
| `raven_deck.py` | Main OS loop — display, touch, menus, scanner, unlock queue |
| `quarantine.py` | Quarantine engine — monitors a directory, hashes files, signals threats |
| `watchdog_pi.py` | Pi-side watchdog (alternative to Windows watchdog for Pi-only setups) |
| `setup_bt_pan.sh` | Bluetooth PAN tethering setup |

---

## Deploy / Run

```bash
# Copy files to Raven
scp raven_deck.py raven@192.168.1.3:/home/raven/
scp quarantine.py raven@192.168.1.3:/home/raven/

# On Raven — run manually first to verify display works
source ~/raven_env/bin/activate
python3 raven_deck.py
```

---

## Systemd Service (Auto-Start)

```bash
sudo nano /etc/systemd/system/raven.service
```

```ini
[Unit]
Description=Raven OS
After=network.target

[Service]
User=root
WorkingDirectory=/home/raven
ExecStart=/home/raven/raven_env/bin/python -u /home/raven/raven_deck.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now raven
sudo journalctl -u raven -f
```

---

## Touch Interface

The e-ink glass is divided invisibly in half horizontally:
- **Left half** = cycle/action (scroll menus, trigger scans)
- **Right half** = select/back

Software debounce: 400ms. Firm, deliberate taps work best.

**MAIN screen left tap** injects a test unlock alert — useful for testing the full Duat → Raven → Duat chain without needing a real malicious file.

---

## Flask API (Port 6175)

Raven runs a small Flask server alongside the display loop. Duat posts alerts here; Raven posts decisions back to Duat.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/alert` | POST | Receive lock alert from Duat |
| `/queue` | GET | Return pending unlock decisions |
| `/decision` | POST | Post UNLOCK or DENY decision to Duat |

---

## Heartbeat (UDP 7743)

Raven listens for UDP packets from the Watchdog on Legiom. Format:
```
WATCHDOG|Legiom|YourUser|ALIVE
```
Packets arrive every 30 seconds. If the heartbeat stops, Raven can display a warning.

---

## Logs

All scan results and audit history write to:
```
/home/raven/raven_intel.txt
```

Readable via: **MENU → VIEW LOGS**

---

## Safe Shutdown

Always use **MENU → SHUT DOWN** before removing power. This exports RAM state to the SD card and prints an OFFLINE timestamp to the display. Wait for the green ACT LED to stop flashing before unplugging.
