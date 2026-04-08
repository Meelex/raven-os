# Legiom — Windows Watchdog

The Windows workstation component. Runs the Watchdog GUI that monitors the Downloads folder, hashes new files, queries the Duat malware database, and sends lock/unlock requests.

---

## Hardware

Any Windows 10/11 machine with Python 3.10+ and OpenSSH server.

The reference build:
- CPU: Intel Core i9
- GPU: NVIDIA RTX 5070
- RAM: 16GB
- OS: Windows 11

---

## Files

| File | Purpose |
|------|---------|
| `watchdog_windows.py` | Main Watchdog GUI application |
| `raven_ring_watchdog_tab.html` | Ring biometrics tab (embedded in Watchdog GUI) |

---

## Python Dependencies

```powershell
pip install tkinter pillow requests watchdog pywin32
```

Or run from the provided virtual environment.

---

## Configuration

Config file: `%APPDATA%\RavenOS\watchdog_config.json`

| Setting | Default |
|---------|---------|
| Downloads path | `C:\Users\<user>\Downloads` |
| Duat IP | `192.168.1.5` |
| Hash DB port | `6174` |
| Unlock port | `6176` |
| Raven IP | `192.168.1.3` |
| Raven port | `6175` |
| Heartbeat port | `7743` |
| Hostname | `Legiom` |
| Windows user | your username |

The config is created automatically on first run. Edit it to match your network.

---

## Building the Executable

```powershell
pip install pyinstaller
pyinstaller watchdog_windows.py --onefile --noconsole --icon=raven.ico --name=RavenWatchdog
```

The compiled `RavenWatchdog.exe` goes in `dist/`. Double-click to run — no Python install needed.

---

## OpenSSH Server Setup

Duat and Raven need to SSH into this machine. Install OpenSSH Server:

```powershell
# Run as Administrator
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Set-Service -Name sshd -StartupType Automatic
Start-Service sshd
```

**Admin account SSH keys** — Windows OpenSSH ignores per-user `authorized_keys` for Administrator accounts. Use the system-wide file:

```
C:\ProgramData\ssh\administrators_authorized_keys
```

Set permissions (run as Administrator):
```powershell
icacls "C:\ProgramData\ssh\administrators_authorized_keys" /inheritance:r
icacls "C:\ProgramData\ssh\administrators_authorized_keys" /grant "NT AUTHORITY\SYSTEM:F"
icacls "C:\ProgramData\ssh\administrators_authorized_keys" /grant "BUILTIN\Administrators:F"
```

Edit `C:\ProgramData\ssh\sshd_config` — verify this line exists and is uncommented:
```
AuthorizedKeysFile __PROGRAMDATA__/ssh/administrators_authorized_keys
```

Restart sshd after any sshd_config change:
```powershell
Restart-Service sshd
```

**Firewall scope** — the SSH firewall rule defaults to Private network only. Set your home WiFi to Private once:
```powershell
Set-NetConnectionProfile -Name "YourWiFiName" -NetworkCategory Private
```

---

## Watchdog GUI — Tabs

| Tab | Contents |
|-----|----------|
| WATCHDOG | File scan status, recent verdicts, active alerts |
| UNLOCK QUEUE | Files awaiting unlock/deny decision |
| LAN INTEL | Devices on the network |
| RING | Ring biometrics live feed (HR, SpO2, steps, confidence) |
| SLASHER | Ladder Slasher leaderboard (auto-refreshes every 15s) |

---

## Heartbeat

The Watchdog sends a UDP heartbeat to Raven every 30 seconds:

```
WATCHDOG|Legiom|YourUser|ALIVE
```

Sent to Raven at `192.168.1.3:7743`. If Raven stops receiving heartbeats, it can flag the Watchdog as offline.

---

## File Lock / Unlock

When the Hash DB returns a MALICIOUS verdict:

1. Watchdog sends `POST http://192.168.1.5:6176/lock` with file path and hash
2. Duat SSHes into Legiom and runs: `icacls "<filepath>" /deny <user>:(RX)`
3. File is now read/execute-denied at the OS level
4. Alert goes to Raven — user taps UNLOCK or DENY on the e-ink display
5. Unlock decision sent to Duat → Duat SSHes back → `icacls "<filepath>" /grant <user>:(F)`

The Watchdog GUI shows file status (LOCKED / UNLOCKED / DENIED) in real time.
