# Troubleshooting

Common issues and how to fix them.

---

## Raven

### E-ink display stays blank after boot

```bash
# Check if SPI is enabled
ls /dev/spidev*         # should show /dev/spidev0.0

# Check Raven service
sudo journalctl -u raven -n 50

# Check GPIO — display needs root or gpio group
sudo python3 raven_deck.py   # run manually as root to test
```

Most common cause: SPI not enabled. Run `sudo raspi-config` → Interface Options → SPI → Enable, then reboot.

### Touch input not registering

The gt1151 controller communicates over I2C. Check I2C is enabled:
```bash
sudo raspi-config   # Interface Options → I2C → Enable
i2cdetect -y 1      # should show device at address 0x14 or 0x5d
```

Also verify the HAT is fully seated on the GPIO header.

### "No module named Pillow / spidev / gpiod"

You're running outside the virtual environment:
```bash
source ~/raven_env/bin/activate
python3 raven_deck.py
```

Or update the systemd service to point to the venv Python explicitly:
```
ExecStart=/home/raven/raven_env/bin/python -u /home/raven/raven_deck.py
```

### Raven can't SSH into PC for remote scan

```bash
# Test manually from Raven
ssh -i ~/.ssh/pc_access_key -v YourUser@192.168.1.6 "dir C:\Users\YourUser\Downloads"
```

Common causes:
- Public key not in `C:\ProgramData\ssh\administrators_authorized_keys` on the PC
- Wrong permissions on `administrators_authorized_keys` (see NETWORK.md)
- PC WiFi set to Public network profile — SSH firewall rule won't fire. Fix: `Set-NetConnectionProfile`
- OpenSSH service not running on PC: `Get-Service sshd`

---

## Duat

### Hash DB returns 500 / won't start

```bash
sudo journalctl -u duat-hashdb -n 100
```

Common causes:
- MalwareBazaar CSV format changed — run `bash fix_hashdb_parser.sh`
- SQLite DB permissions: `ls -la /home/duat/hashes.db`
- Port 6174 already in use: `sudo ss -tlnp | grep 6174`

### Unlock service can't SSH into PC

```bash
# Test from Duat
ssh -i ~/.ssh/duat_horus_key -v YourUser@192.168.1.6 "echo ok"
```

Same checklist as Raven SSH above. The key file on Duat is `~/.ssh/duat_horus_key`.

### icacls command fails (file lock/unlock doesn't work)

```bash
# Test the exact command Duat runs
ssh -i ~/.ssh/duat_horus_key YourUser@192.168.1.6 "icacls C:\Users\YourUser\Downloads\test.txt /deny YourUser:(RX)"
```

If the SSH works but icacls fails:
- Confirm you're using the correct Windows username
- The file path must exist
- Run `icacls` without arguments on the PC to confirm it's available

### Ring service won't connect to ring

```bash
sudo journalctl -u raven-ring -n 50

# Check BLE adapter
hciconfig
bluetoothctl
> scan on   # look for YOUR_RING_NAME
```

Common causes:
- Ring is out of range or needs charging (below ~10% battery BLE becomes unreliable)
- BLE address changed (COLMI R02 occasionally rotates MAC) — run `bluetoothctl scan on` to find current address and update `raven_ring.py`
- Another device has the ring connected — disconnect from the COLMI app first

### Game server (port 5000) returns 502 / not responding

```bash
sudo systemctl status raven-slasher
sudo journalctl -u raven-slasher -n 50

# Check port
sudo ss -tlnp | grep 5000

# Test directly
cd /opt/raven-slasher && python3 app.py
```

---

## Legiom (Windows Watchdog)

### Watchdog shows "Duat unreachable"

```powershell
# Test from PowerShell
Invoke-WebRequest http://192.168.1.5:6174/health
curl http://192.168.1.5:6174/health
```

- Confirm Duat is on and the service is running: `ssh duat@192.168.1.5 "sudo systemctl status duat-hashdb"`
- Check Windows Firewall isn't blocking outbound on port 6174 (unlikely but possible in corporate environments)

### Watchdog doesn't detect new files

- Confirm the monitored path in `watchdog_config.json` matches your actual Downloads path
- Run as Administrator if Downloads folder has restricted permissions
- Check `%APPDATA%\RavenOS\watchdog_config.json` exists — delete it to regenerate defaults

### SSH from Duat/Raven into PC is refused

```powershell
# Check OpenSSH is running
Get-Service sshd

# Check firewall rule profile
Get-NetFirewallRule -Name "OpenSSH-Server-In-TCP" | Get-NetFirewallProfile

# Set network to Private
Get-NetConnectionProfile
Set-NetConnectionProfile -Name "YourWiFiName" -NetworkCategory Private
```

Check Event Viewer → Windows Logs → Security for failed auth events (event ID 4625).

### "Permissions for authorized_keys are too open"

OpenSSH rejects the key silently if permissions are wrong. The `administrators_authorized_keys` file must be owned by SYSTEM and Administrators only:
```powershell
icacls "C:\ProgramData\ssh\administrators_authorized_keys" /inheritance:r
icacls "C:\ProgramData\ssh\administrators_authorized_keys" /grant "NT AUTHORITY\SYSTEM:F"
icacls "C:\ProgramData\ssh\administrators_authorized_keys" /grant "BUILTIN\Administrators:F"
```
No other users or groups should appear in the ACL.

---

## WireGuard

### Client can't reach Duat through VPN

```bash
# On client
sudo wg show              # check handshake timestamp — should be recent

# On VPS
sudo wg show wg0 latest-handshakes   # check all peer handshakes

# DNS check
nslookup yourdomain.duckdns.org      # must resolve to VPS IP
```

If handshake is old (>3 minutes):
- Client's keepalive should reconnect automatically — wait 30s
- If stuck: `sudo wg-quick down wg0 && sudo wg-quick up wg0`
- Check VPS firewall allows UDP port 443 inbound

### iPhone/Scarab won't connect to hotspot

- Enable "Maximize Compatibility" in iPhone Settings → Personal Hotspot
- Pi Zero 2 W is 2.4 GHz only — this forces the hotspot to broadcast 2.4 GHz

### Scarab loses VPN tunnel after network change

This is expected — WireGuard needs a few keepalive cycles to detect the new network and rekey. Wait ~60 seconds. If it doesn't recover:
```bash
ssh scarab@192.168.1.2    # if on LAN
sudo wg-quick down wg0 && sudo wg-quick up wg0
```

---

## ESP32 Companion

### Device not recognized as COM port

Install the CH343 driver: https://www.wch-ic.com/downloads/CH343SER_EXE.html

Replug after installing. Use a data-capable USB cable (not charge-only).

### Flash fails / upload timeout

1. Check the correct COM port in PlatformIO
2. Hold the BOOT button on the ESP32-S3 while clicking Upload, release after upload starts
3. Reduce upload speed in `platformio.ini`: `upload_speed = 460800`

### BLE battle won't pair

Both devices must be within ~5 meters. Walls and interference reduce range significantly on ESP32-S3. If pairing fails:
1. Power cycle both devices
2. Try again — BLE scan on one device, wait for the other to appear

---

## General Diagnostics

```bash
# Quick health check — run from any LAN device
curl -s http://192.168.1.5:6174/health | python3 -m json.tool
curl -s http://192.168.1.5:6176/health | python3 -m json.tool
curl -s http://192.168.1.5:7744/status | python3 -m json.tool
curl -s http://192.168.1.5:5000/api/online | python3 -m json.tool

# Ping all nodes
for ip in 192.168.1.2 192.168.1.3 192.168.1.5 192.168.1.6 192.168.1.8; do
    ping -c 1 -W 1 $ip > /dev/null && echo "$ip UP" || echo "$ip DOWN"
done

# Check all systemd services on Duat
ssh duat@192.168.1.5 "sudo systemctl status duat-hashdb duat-unlock raven-ring raven-slasher"
```
