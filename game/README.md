# Ladder Slasher — Game Server

LAN-hosted dungeon crawler. Players connect from any browser on the local network. No app install required.

---

## Files

| File | Purpose |
|------|---------|
| `app.py` | Flask backend — game server, dungeon generation, character persistence |
| `ladder_slasher.html` | Full game client — served by Flask, runs in browser |

---

## Quick Start

```bash
# On Duat (Pi 5)
pip3 install flask --break-system-packages

sudo mkdir -p /opt/raven-slasher
sudo cp app.py ladder_slasher.html /opt/raven-slasher/

python3 /opt/raven-slasher/app.py
```

Open `http://192.168.1.5:5000` on any device on the LAN.

The SQLite database (`ladder_slasher.db`) is auto-created in `/opt/raven-slasher/` on first run.

---

## Systemd Service

```bash
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
sudo systemctl daemon-reload
sudo systemctl enable --now raven-slasher
```

---

## Deploy Update from Legiom

```bash
scp app.py ladder_slasher.html duat@192.168.1.5:/opt/raven-slasher/
ssh duat@192.168.1.5 "sudo systemctl restart raven-slasher"
```

---

## Game Overview

See [docs/LADDER_SLASHER.md](../../docs/LADDER_SLASHER.md) for full game design documentation — classes, skills, room types, co-op, leaderboard, and backlog.

---

## Flask Dependency Only

Flask is the only non-stdlib dependency. SQLite3 is built into Python.

```bash
python3 -c "import flask; print(flask.__version__)"
```
