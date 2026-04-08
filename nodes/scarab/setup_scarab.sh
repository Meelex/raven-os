#!/bin/bash
# ============================================================
# setup_scarab.sh
# Run on Scarab (Pi Zero 2) after first boot.
# Sets up WireGuard VPN client, e-ink display, and self-healing.
# ============================================================

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

log()  { echo -e "${GREEN}[✓]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
err()  { echo -e "${RED}[✗]${NC} $1"; exit 1; }
info() { echo -e "${CYAN}[→]${NC} $1"; }

echo ""
echo "================================================"
echo "  Raven OS — Scarab Setup"
echo "  Travel router for fiancée"
echo "================================================"
echo ""

# ── Verify running as correct user ────────────────────────────
if [ "$EUID" -eq 0 ]; then
    err "Do not run as root. Run as your normal scarab user."
fi

SCARAB_USER=$(whoami)
HOME_DIR=$(eval echo ~$SCARAB_USER)
SCARAB_DIR="$HOME_DIR/scarab"
mkdir -p "$SCARAB_DIR"

log "Running as: $SCARAB_USER"

# ── System update ──────────────────────────────────────────────
info "Updating system..."
sudo apt-get update -qq
sudo apt-get install -y -qq \
    wireguard \
    python3 \
    python3-pip \
    python3-venv \
    git \
    iptables \
    curl \
    net-tools \
    hostapd \
    dnsmasq

log "System packages installed"

# ── Python venv ────────────────────────────────────────────────
info "Creating Python environment..."
python3 -m venv "$SCARAB_DIR/venv"
source "$SCARAB_DIR/venv/bin/activate"
pip install --quiet pillow flask requests
log "Python environment ready"

# ── WireGuard client config ────────────────────────────────────
info "Setting up WireGuard..."
sudo mkdir -p /etc/wireguard

# Write Scarab's private key
sudo bash -c 'cat > /etc/wireguard/scarab_private.key << "KEYEOF"
vYB4R6lC6UfKoAX6lecPITfLY5uVX9jQGAlvcZ8qqgY=
KEYEOF'
sudo chmod 600 /etc/wireguard/scarab_private.key

# Write WireGuard client config
sudo bash -c 'cat > /etc/wireguard/wg0.conf << "WGEOF"
[Interface]
PrivateKey = vYB4R6lC6UfKoAX6lecPITfLY5uVX9jQGAlvcZ8qqgY=
Address = 10.0.0.2/24
DNS = 8.8.8.8

[Peer]
# Duat — home base
PublicKey = EmKaNVYNtB68gF6AR3tgVZAyAmR/0qPa1PYX3YUQSUE=
Endpoint = ravenos.myddns.me:51820
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 25
WGEOF'
sudo chmod 600 /etc/wireguard/wg0.conf

log "WireGuard config written"

# ── Enable IP forwarding ───────────────────────────────────────
info "Enabling IP forwarding..."
echo "net.ipv4.ip_forward=1" | sudo tee -a /etc/sysctl.conf > /dev/null
sudo sysctl -p > /dev/null
log "IP forwarding enabled"

# ── Self-healing script ────────────────────────────────────────
info "Writing self-healing monitor..."
cat > "$SCARAB_DIR/tunnel_monitor.sh" << 'MONITOR'
#!/bin/bash
# Scarab tunnel monitor — checks Duat every 60 seconds
# Brings tunnel down if unreachable, back up when Duat returns

DUAT_TUNNEL_IP="10.0.0.1"
FAIL_COUNT=0
MAX_FAILS=3
TUNNEL_UP=false

log() { echo "[$(date '+%H:%M:%S')] $1" >> /home/scarab/scarab/tunnel.log; }

while true; do
    if ping -c 1 -W 3 "$DUAT_TUNNEL_IP" > /dev/null 2>&1; then
        FAIL_COUNT=0
        if [ "$TUNNEL_UP" = false ]; then
            log "Duat reachable — tunnel restored"
            TUNNEL_UP=true
        fi
    else
        FAIL_COUNT=$((FAIL_COUNT + 1))
        log "Duat unreachable (attempt $FAIL_COUNT/$MAX_FAILS)"

        if [ "$FAIL_COUNT" -ge "$MAX_FAILS" ] && [ "$TUNNEL_UP" = true ]; then
            log "Tunnel down — falling back to direct connection"
            sudo wg-quick down wg0 2>/dev/null || true
            TUNNEL_UP=false
            FAIL_COUNT=0
        fi

        if [ "$TUNNEL_UP" = false ]; then
            # Try to reconnect
            if ping -c 1 -W 5 ravenos.myddns.me > /dev/null 2>&1; then
                log "Duat reachable via DNS — reconnecting tunnel"
                sudo wg-quick up wg0 2>/dev/null && TUNNEL_UP=true && log "Tunnel restored"
            fi
        fi
    fi
    sleep 60
done
MONITOR

chmod +x "$SCARAB_DIR/tunnel_monitor.sh"
log "Self-healing monitor written"

# ── Display script ─────────────────────────────────────────────
info "Writing e-ink display script..."
cat > "$SCARAB_DIR/scarab_display.py" << 'DISPLAY'
#!/usr/bin/env python3
"""
scarab_display.py - Raven OS
E-ink display for Scarab Pi Zero 2 (Waveshare 2.13" V4, no touch)
Shows tunnel status, connection info, data stats.
"""

import time
import subprocess
import os
import sys
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.expanduser("~/TP_lib"))
try:
    from TP_lib import epd2in13_V4
    HAS_DISPLAY = True
except ImportError:
    HAS_DISPLAY = False
    print("[!] Display not available — running headless")

# ── State ──────────────────────────────────────────────────────
def get_tunnel_status():
    """Check if WireGuard tunnel is up."""
    try:
        result = subprocess.check_output(
            ["sudo", "wg", "show", "wg0"],
            stderr=subprocess.DEVNULL,
            universal_newlines=True
        )
        return "active" if "latest handshake" in result else "connecting"
    except Exception:
        return "down"

def get_duat_status():
    """Ping Duat tunnel IP."""
    try:
        result = subprocess.run(
            ["ping", "-c", "1", "-W", "2", "10.0.0.1"],
            capture_output=True, timeout=5
        )
        return "online" if result.returncode == 0 else "offline"
    except Exception:
        return "offline"

def get_connected_devices():
    """Count devices connected to Scarab hotspot."""
    try:
        result = subprocess.check_output(
            "cat /proc/net/arp | grep -v IP | wc -l",
            shell=True, universal_newlines=True
        )
        return int(result.strip())
    except Exception:
        return 0

def get_data_stats():
    """Get WireGuard transfer stats."""
    try:
        result = subprocess.check_output(
            ["sudo", "wg", "show", "wg0", "transfer"],
            stderr=subprocess.DEVNULL,
            universal_newlines=True
        ).strip()
        if result:
            parts = result.split()
            if len(parts) >= 2:
                rx = int(parts[0]) / (1024*1024)
                tx = int(parts[1]) / (1024*1024)
                return f"↑{tx:.1f}MB ↓{rx:.1f}MB"
    except Exception:
        pass
    return "↑0.0MB ↓0.0MB"

def get_signal():
    """Get WiFi signal strength."""
    try:
        result = subprocess.check_output(
            "iwconfig usb0 2>/dev/null | grep -i quality | awk '{print $2}' | cut -d= -f2",
            shell=True, universal_newlines=True
        ).strip()
        return result or "N/A"
    except Exception:
        return "N/A"

# ── Scarab beetle drawing ──────────────────────────────────────
def draw_scarab(draw, cx, cy, s=1.0, color=0):
    """Draw scarab beetle at center position with scale."""
    # Body
    draw.ellipse([cx-int(18*s), cy-int(2*s), cx+int(18*s), cy+int(40*s)], fill=color)
    # Head
    draw.ellipse([cx-int(10*s), cy-int(16*s), cx+int(10*s), cy+int(2*s)], fill=color)
    # Wing divide
    draw.line([cx, cy, cx, cy+int(38*s)], fill=255, width=max(1,int(1.5*s)))
    # Antennae
    draw.line([cx-int(4*s), cy-int(14*s), cx-int(16*s), cy-int(22*s)], fill=color, width=max(1,int(2*s)))
    draw.line([cx+int(4*s), cy-int(14*s), cx+int(16*s), cy-int(22*s)], fill=color, width=max(1,int(2*s)))
    draw.ellipse([cx-int(18*s)-2, cy-int(24*s)-2, cx-int(14*s)+2, cy-int(20*s)+2], fill=color)
    draw.ellipse([cx+int(14*s)-2, cy-int(24*s)-2, cx+int(18*s)+2, cy-int(20*s)+2], fill=color)
    # Left legs
    draw.line([cx-int(16*s), cy+int(8*s), cx-int(28*s), cy+int(2*s)], fill=color, width=max(1,int(2*s)))
    draw.line([cx-int(17*s), cy+int(18*s), cx-int(32*s), cy+int(16*s)], fill=color, width=max(1,int(2*s)))
    draw.line([cx-int(15*s), cy+int(28*s), cx-int(26*s), cy+int(36*s)], fill=color, width=max(1,int(2*s)))
    # Right legs
    draw.line([cx+int(16*s), cy+int(8*s), cx+int(28*s), cy+int(2*s)], fill=color, width=max(1,int(2*s)))
    draw.line([cx+int(17*s), cy+int(18*s), cx+int(32*s), cy+int(16*s)], fill=color, width=max(1,int(2*s)))
    draw.line([cx+int(15*s), cy+int(28*s), cx+int(26*s), cy+int(36*s)], fill=color, width=max(1,int(2*s)))

# ── Screens ────────────────────────────────────────────────────
def render_boot(draw, tick):
    draw.rectangle([0, 0, 250, 122], fill=255)
    draw.rectangle([0, 0, 250, 122], outline=0, width=3)
    draw_scarab(draw, 125, 30, s=1.05)
    draw.text((78, 88), "SCARAB", fill=0)
    draw.text((68, 102), "INITIALIZING...", fill=0)
    if tick % 2 == 0:
        draw.rectangle([80, 113, 170, 116], fill=0)

def render_idle(draw):
    draw.rectangle([0, 0, 250, 122], fill=255)
    draw.rectangle([0, 0, 250, 122], outline=0, width=2)
    draw.rectangle([0, 0, 250, 18], fill=0)
    draw.text((8, 4), "SCARAB", fill=255)
    draw.text((172, 4), "STANDBY", fill=255)
    draw_scarab(draw, 125, 32, s=0.95)
    draw.text((52, 90), "NO TUNNEL ACTIVE", fill=0)
    draw.line([20, 97, 230, 97], fill=0, width=1)
    draw.text((56, 106), "PLUG IN TO ACTIVATE", fill=0)

def render_active(draw, duat_status, devices, data_stats, tick):
    draw.rectangle([0, 0, 250, 122], fill=255)
    draw.rectangle([0, 0, 250, 122], outline=0, width=2)
    draw.rectangle([0, 0, 250, 18], fill=0)
    draw.text((8, 4), "SCARAB", fill=255)
    # Pulsing dot
    r = 5 if tick % 2 == 0 else 4
    draw.ellipse([210-r, 9-r, 210+r, 9+r], fill=255)
    draw.text((220, 4), "ON", fill=255)
    # Small scarab top right
    draw_scarab(draw, 218, 22, s=0.45)
    # Status
    draw.text((10, 26), "TUNNEL", fill=0)
    draw.ellipse([80, 22, 86, 28], fill=0)
    draw.text((92, 26), "ACTIVE", fill=0)
    draw.text((10, 40), "DUAT", fill=0)
    draw.ellipse([80, 36, 86, 42], fill=0 if duat_status == "online" else 255, outline=0)
    draw.text((92, 40), duat_status.upper(), fill=0)
    draw.line([10, 50, 185, 50], fill=0, width=1)
    draw.text((10, 58), "DEVICE", fill=0)
    draw.text((80, 58), f"{devices} connected", fill=0)
    draw.text((10, 71), "DATA", fill=0)
    draw.text((80, 71), data_stats, fill=0)
    draw.line([10, 79, 185, 79], fill=0, width=1)
    draw.text((10, 91), "ravenos.myddns.me", fill=0)
    draw.text((10, 105), "🔒 ENCRYPTED", fill=0)

def render_fallback(draw, tick):
    draw.rectangle([0, 0, 250, 122], fill=255)
    draw.rectangle([0, 0, 250, 122], outline=0, width=2)
    draw.rectangle([0, 0, 250, 18], fill=0)
    draw.text((8, 4), "SCARAB", fill=255)
    draw.text((148, 4), "FALLBACK", fill=255)
    # Faded scarab with X
    draw_scarab(draw, 125, 30, s=0.85, color=0)
    draw.line([93, 16, 157, 66], fill=0, width=2)
    draw.line([157, 16, 93, 66], fill=0, width=2)
    draw.line([10, 74, 240, 74], fill=0, width=1)
    draw.text((42, 82), "DUAT UNREACHABLE", fill=0)
    draw.text((28, 95), "DIRECT CONNECTION ACTIVE", fill=0)
    if tick % 2 == 0:
        draw.text((72, 108), "RETRYING ● ● ●", fill=0)
    else:
        draw.text((88, 108), "RETRYING", fill=0)

# ── Main loop ──────────────────────────────────────────────────
def main():
    if HAS_DISPLAY:
        epd = epd2in13_V4.EPD()
        epd.init(epd.FULL_UPDATE)
        epd.Clear(0xFF)
        canvas = Image.new('1', (epd.height, epd.width), 255)
        draw = ImageDraw.Draw(canvas)
        epd.displayPartBaseImage(epd.getbuffer(canvas))
        epd.init(epd.PART_UPDATE)
    else:
        canvas = Image.new('1', (250, 122), 255)
        draw = ImageDraw.Draw(canvas)

    print("[*] Scarab display starting...")
    tick = 0

    # Boot screen for 3 seconds
    for _ in range(6):
        draw.rectangle([0, 0, 250, 122], fill=255)
        render_boot(draw, tick)
        if HAS_DISPLAY:
            epd.displayPartial(epd.getbuffer(canvas))
        tick += 1
        time.sleep(0.5)

    print("[*] Entering main loop")

    while True:
        tunnel = get_tunnel_status()
        duat   = get_duat_status()

        draw.rectangle([0, 0, 250, 122], fill=255)

        if tunnel == "down":
            render_fallback(draw, tick)
        elif tunnel in ("active", "connecting"):
            devices = get_connected_devices()
            stats   = get_data_stats()
            render_active(draw, duat, devices, stats, tick)
        else:
            render_idle(draw)

        if HAS_DISPLAY:
            epd.displayPartial(epd.getbuffer(canvas))

        tick += 1
        time.sleep(5)

if __name__ == "__main__":
    main()
DISPLAY

chmod +x "$SCARAB_DIR/scarab_display.py"
log "Display script written"

# ── Systemd services ───────────────────────────────────────────
info "Writing systemd services..."
VENV_PYTHON="$SCARAB_DIR/venv/bin/python3"

sudo tee /etc/systemd/system/scarab-wireguard.service > /dev/null << SVCEOF
[Unit]
Description=Scarab WireGuard VPN
After=network.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/bin/wg-quick up wg0
ExecStop=/usr/bin/wg-quick down wg0

[Install]
WantedBy=multi-user.target
SVCEOF

sudo tee /etc/systemd/system/scarab-monitor.service > /dev/null << SVCEOF
[Unit]
Description=Scarab Tunnel Self-Healing Monitor
After=scarab-wireguard.service

[Service]
Type=simple
User=$SCARAB_USER
ExecStart=/bin/bash $SCARAB_DIR/tunnel_monitor.sh
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
SVCEOF

sudo tee /etc/systemd/system/scarab-display.service > /dev/null << SVCEOF
[Unit]
Description=Scarab E-Ink Display
After=network.target

[Service]
Type=simple
User=$SCARAB_USER
WorkingDirectory=$SCARAB_DIR
ExecStart=$VENV_PYTHON $SCARAB_DIR/scarab_display.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SVCEOF

sudo systemctl daemon-reload
sudo systemctl enable scarab-wireguard scarab-monitor scarab-display
log "Systemd services enabled"

# ── Start WireGuard ────────────────────────────────────────────
info "Starting WireGuard tunnel..."
sudo wg-quick up wg0 2>/dev/null && log "WireGuard tunnel up" || warn "Tunnel start failed — may need router port forward active"

# ── Install TP_lib for display ─────────────────────────────────
info "Cloning TP_lib display drivers..."
if [ ! -d "$HOME_DIR/TP_lib" ]; then
    git clone https://github.com/waveshare/e-Paper.git /tmp/e-paper-repo 2>/dev/null || true
    if [ -d "/tmp/e-paper-repo/RaspberryPi_JetsonNano/python/lib/waveshare_epd" ]; then
        cp -r /tmp/e-paper-repo/RaspberryPi_JetsonNano/python/lib/waveshare_epd "$HOME_DIR/TP_lib"
        log "Display drivers installed"
    else
        warn "Could not clone drivers — copy TP_lib from Raven manually"
    fi
    rm -rf /tmp/e-paper-repo
else
    log "TP_lib already present"
fi

# ── Verify ────────────────────────────────────────────────────
echo ""
echo "================================================"
echo "  Verifying..."
echo "================================================"

sudo wg show wg0 && log "WireGuard: running" || warn "WireGuard: not running"

echo ""
echo "================================================"
echo "  SCARAB SETUP COMPLETE"
echo "================================================"
echo ""
log "WireGuard tunnel: ravenos.myddns.me:51820"
log "Tunnel IP: 10.0.0.2"
log "Duat: 10.0.0.1"
log "Self-healing monitor: active"
log "Display: scarab_display.py"
echo ""
warn "Next: SCP TP_lib from Raven if display drivers failed"
warn "      scp -r raven@192.168.1.5:~/TP_lib ~/TP_lib"
echo ""
echo "================================================"