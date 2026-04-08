# Setup Guide

Deploy in this order. Each node depends on Duat being up first.

---

## Prerequisites

- Raspberry Pi Imager installed on your PC
- A router where you can set DHCP reservations
- SSH client (built into Windows 10/11, macOS, Linux)
- Your LAN subnet (this guide uses `192.168.1.x` — adjust to match yours)

---

## Step 1 — Reserve Static IPs

In your router admin panel, create DHCP reservations for each Pi using its MAC address.
The MAC address is printed on the Pi or visible in your router's connected devices list.

| Node | Assign this IP |
|------|---------------|
| Duat (Pi 5) | 192.168.1.5 |
| Raven (Pi Zero 2) | 192.168.1.3 |
| Scarab (Pi Zero 2) | 192.168.1.2 |
| Griffin (Pi Zero 2) | 192.168.1.8 |

---

## Step 2 — Flash and Boot Duat

Duat must be up first — everything else talks to it.

**Flash Raspberry Pi OS Bookworm (64-bit, full or lite) to your storage medium.**

In Raspberry Pi Imager before flashing:
- Enable SSH
- Set username: `duat`
- Set hostname: `duat`
- Configure your WiFi SSID and password

Boot Duat, then SSH in:
```bash
ssh duat@192.168.1.5
```

**Install dependencies:**
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3-pip python3-venv sqlite3 curl

pip3 install flask --break-system-packages
```

**Deploy core services:**
```bash
mkdir -p /home/duat/duat
# Copy from your PC:
# scp raven_hashdb.py duat_unlock.py duat@192.168.1.5:/home/duat/duat/
```

**Deploy and enable Hash DB (port 6174):**
```bash
sudo nano /etc/systemd/system/duat-hashdb.service
```
```ini
[Unit]
Description=Raven Hash DB
After=network.target

[Service]
User=duat
WorkingDirectory=/home/duat/duat
ExecStart=python3 /home/duat/duat/raven_hashdb.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now duat-hashdb
curl http://192.168.1.5:6174/health   # should return {"status": "ok"}
```

**Deploy and enable Unlock Service (port 6176):**
```bash
sudo nano /etc/systemd/system/duat-unlock.service
```
```ini
[Unit]
Description=Raven Unlock Service
After=network.target

[Service]
User=duat
WorkingDirectory=/home/duat/duat
ExecStart=python3 /home/duat/duat/duat_unlock.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl enable --now duat-unlock
curl http://192.168.1.5:6176/health
```

**Generate Duat's SSH key for locking files on your PC:**
```bash
ssh-keygen -t ed25519 -C "duat@duat" -f ~/.ssh/duat_horus_key
cat ~/.ssh/duat_horus_key.pub   # copy this — needed in Step 4
```

---

## Step 3 — Deploy Ladder Slasher (optional but fun)

```bash
sudo mkdir -p /opt/raven-slasher
# scp game/app.py game/ladder_slasher.html duat@192.168.1.5:/opt/raven-slasher/

sudo nano /etc/systemd/system/raven-slasher.service
```
```ini
[Unit]
Description=Ladder Slasher Game Server
After=network.target

[Service]
User=duat
WorkingDirectory=/opt/raven-slasher
ExecStart=python3 /opt/raven-slasher/app.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl enable --now raven-slasher
# Open http://192.168.1.5:5000 in any browser on the LAN
```

---

## Step 4 — Set Up Legiom (Windows PC)

**Install OpenSSH Server:**
```powershell
# Run as Administrator
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Set-Service -Name sshd -StartupType Automatic
Start-Service sshd
```

**Set home WiFi to Private (required for SSH firewall rule):**
```powershell
Set-NetConnectionProfile -Name "YourWiFiName" -NetworkCategory Private
```

**Add Duat's public key to the admin authorized_keys file:**
```
C:\ProgramData\ssh\administrators_authorized_keys
```
Append the contents of `~/.ssh/duat_horus_key.pub` from Duat to that file.

**Fix permissions on the authorized_keys file (OpenSSH enforces this):**
```powershell
icacls "C:\ProgramData\ssh\administrators_authorized_keys" /inheritance:r
icacls "C:\ProgramData\ssh\administrators_authorized_keys" /grant "NT AUTHORITY\SYSTEM:F"
icacls "C:\ProgramData\ssh\administrators_authorized_keys" /grant "BUILTIN\Administrators:F"
```

**Verify `sshd_config` has this line uncommented:**
```
AuthorizedKeysFile __PROGRAMDATA__/ssh/administrators_authorized_keys
```
File path: `C:\ProgramData\ssh\sshd_config`

**Test from Duat:**
```bash
ssh -i ~/.ssh/duat_horus_key YourWindowsUsername@192.168.1.6 "echo connected"
```

**Install and run the Watchdog:**
```bash
pip install tkinter pillow requests watchdog pywin32
python watchdog_windows.py
```
Or run `RavenWatchdog.exe` directly (no Python needed).

---

## Step 5 — Flash and Boot Raven

**Flash Raspberry Pi OS Bookworm (64-bit Lite, headless):**
- Enable SSH
- Username: `raven`
- Hostname: `raven`
- WiFi configured

```bash
ssh raven@192.168.1.3

sudo apt update
sudo apt install -y nmap bluez network-manager python3-venv

python3 -m venv ~/raven_env
source ~/raven_env/bin/activate
pip install Pillow spidev RPi.GPIO gpiod flask

sudo raspi-config
# Interface Options → SPI → Enable
# Interface Options → I2C → Enable
```

**Generate Raven's SSH key for scanning your PC:**
```bash
ssh-keygen -t ed25519 -C "raven-os-remote-access" -f ~/.ssh/pc_access_key
cat ~/.ssh/pc_access_key.pub   # add to C:\ProgramData\ssh\administrators_authorized_keys
```

**Deploy and start Raven OS:**
```bash
# scp nodes/raven/raven_deck.py raven@192.168.1.3:/home/raven/

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

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now raven
sudo journalctl -u raven -f
```

The e-ink display should come on. Left tap = cycle, right tap = select.

**Test the full alert chain:**
On Raven's MAIN screen, left-tap to inject a test unlock alert. The Duat unlock service should receive it and forward to Raven's unlock queue.

---

## Step 6 — Ring Service (optional)

Deploy on Duat. The COLMI R02 ring must be charged and nearby.

```bash
pip3 install colmi-r02-client --break-system-packages

# scp nodes/duat/raven_ring.py nodes/duat/raven_ring.service duat@192.168.1.5:/home/duat/

sudo cp /home/duat/raven_ring.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now raven-ring

# Verify
curl http://192.168.1.5:7744/status
```

Calibrate gestures before using Iron House integration:
```bash
python3 raven_ring.py --test-gestures
```

---

## Step 7 — Scarab and Griffin (optional)

Both follow the same pattern as Raven. Flash, SSH in, install deps, copy files, create systemd service.

- Scarab additionally needs WireGuard: `sudo apt install wireguard`
- Griffin uses the same Waveshare 2.13" HAT as Raven — same SPI/I2C setup

See the node-specific READMEs in `nodes/scarab/` and `nodes/griffin/`.

---

## Step 8 — ESP32 Companion Devices (optional)

1. Install VS Code + PlatformIO extension
2. Copy `esp32-companion/src/secrets.h.example` to `esp32-companion/src/secrets.h` and fill in your WiFi and Duat IP
3. Open the `esp32-companion/` folder in PlatformIO
4. Flash: `pio run -e raven --target upload`

---

## Verify Everything

```bash
# From any machine on the LAN
curl http://192.168.1.5:6174/health     # Hash DB
curl http://192.168.1.5:6176/health     # Unlock service
curl http://192.168.1.5:7744/status     # Ring (if deployed)
curl http://192.168.1.5:5000            # Game server (if deployed)

# SSH into Raven
ssh raven@192.168.1.3

# Check Raven OS service
ssh raven@192.168.1.3 "sudo systemctl status raven"
```

Drop a file into your Downloads folder and watch the chain run end to end.
