#!/bin/bash
# setup_bt_pan.sh — One-time Raven BT PAN setup
# Run as root on Raven: sudo bash setup_bt_pan.sh
set -e

echo "[bt_pan] Installing python3-dbus python3-gi..."
apt-get install -y python3-dbus python3-gi > /dev/null

echo "[bt_pan] Loading bnep module..."
modprobe bnep
grep -qxF bnep /etc/modules || echo bnep >> /etc/modules

echo "[bt_pan] Enabling bluetoothd --compat (required for NAP server)..."
mkdir -p /etc/systemd/system/bluetooth.service.d
cat > /etc/systemd/system/bluetooth.service.d/raven.conf << 'EOF'
[Service]
ExecStart=
ExecStart=/usr/libexec/bluetooth/bluetoothd --compat --noplugin=sap
EOF
systemctl daemon-reload
systemctl restart bluetooth
sleep 2

echo "[bt_pan] Creating /etc/raven config dir..."
mkdir -p /etc/raven

# Only create config if it doesn't exist (preserve user's phone_mac)
if [ ! -f /etc/raven/bt_config.json ]; then
    cat > /etc/raven/bt_config.json << 'EOF'
{
  "phone_mac":  "XX:XX:XX:XX:XX:XX",
  "phone_name": "My Phone",
  "bt_name":    "RAVEN-OS",
  "socks_port": 1080
}
EOF
    echo ""
    echo "  *** Set your phone's BT MAC in /etc/raven/bt_config.json ***"
    echo "  Find it with: bluetoothctl scan on  (then bluetoothctl devices)"
    echo ""
fi

echo "[bt_pan] Telling NetworkManager to ignore pan0 and bnep* ..."
mkdir -p /etc/NetworkManager/conf.d
cat > /etc/NetworkManager/conf.d/bt-pan.conf << 'EOF'
[keyfile]
unmanaged-devices=interface-name:pan0,interface-name:bnep*
EOF
systemctl reload NetworkManager 2>/dev/null || true

echo "[bt_pan] Installing bt_pan.py..."
cp /tmp/bt_pan.py /home/raven/bt_pan.py
chown raven:raven /home/raven/bt_pan.py
chmod +x /home/raven/bt_pan.py

echo "[bt_pan] Installing systemd service..."
cat > /etc/systemd/system/raven-btpan.service << 'EOF'
[Unit]
Description=Raven OS BT PAN Gateway
After=bluetooth.service network-online.target
Wants=bluetooth.service

[Service]
Type=simple
User=root
ExecStart=/usr/bin/python3 /home/raven/bt_pan.py
Restart=on-failure
RestartSec=15
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable raven-btpan
systemctl start raven-btpan

echo ""
echo "[bt_pan] Done."
echo ""
echo "  Raven BT name: RAVEN-OS (always discoverable)"
echo "  At home  → phone connects to RAVEN-OS → routed to home network"
echo "  Away     → Raven tethers to phone → WG up → SOCKS5 on :1080"
echo ""
echo "  Phone pairing: go to Bluetooth settings, tap RAVEN-OS, confirm PIN"
echo "  For PANU mode: enable BT tethering on your phone first"
echo ""
echo "  Status:  sudo journalctl -u raven-btpan -f"
echo "  Config:  /etc/raven/bt_config.json"
