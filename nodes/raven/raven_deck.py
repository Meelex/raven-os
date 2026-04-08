import time
import threading
import subprocess
import os
import ftplib
import hashlib
import socket
import json
from PIL import Image, ImageDraw
from TP_lib import epd2in13_V4
from TP_lib import gt1151

try:
    from flask import Flask, request, jsonify
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False
    print("[!] Flask not installed. Run: pip install flask --break-system-packages")

# ── Global State ───────────────────────────────────────────────
ui_page    = "MAIN"
menu_index = 0
intel_index = 0
unlock_index = 0
active_unlock = None

menu_options = [
    "1. LAN PING SWEEP",
    "2. VIEW LAN INTEL",
    "3. AUTO-AUDIT LAN",
    "4. BLUETOOTH",
    "5. WIFI APs",
    "6. SYS HEALTH",
    "7. VIEW LOGS",
    "8. REMOTE SCAN PC",
    "9. UNLOCK QUEUE",
    "10. SHUT DOWN",
    "<- BACK TO MAIN"
]

# ── Vulnerability Dictionary ───────────────────────────────────
VULN_ADVISORY = {
    "21/tcp":    "FTP: Unencrypted. Use SFTP.",
    "23/tcp":    "TELNET: Cleartext. Use SSH.",
    "80/tcp":    "HTTP: Unencrypted. Use HTTPS.",
    "445/tcp":   "SMB: Win Share. Disable SMBv1.",
    "3389/tcp":  "RDP: Remote Access. Use VPN.",
    "5900/tcp":  "VNC: Remote Desktop. Needs Auth.",
    "554/tcp":   "RTSP: Unencrypted IP Camera.",
    "1883/tcp":  "MQTT: Unencrypted IoT Broker.",
    "8080/tcp":  "ALT-HTTP: Check for Webcams/Routers.",
    "3306/tcp":  "MYSQL: DB Exposed to LAN.",
    "5432/tcp":  "POSTGRES: DB Exposed to LAN.",
    "9000/tcp":  "PORTAINER: Docker UI. Check Auth.",
    "27017/tcp": "MONGO: DB Open. Check Auth."
}

# ── Config ─────────────────────────────────────────────────────
QUARANTINE_DIR = "/home/raven/quarantine_zone"
LEDGER_FILE    = "/home/raven/hash_ledger.txt"
os.makedirs(QUARANTINE_DIR, exist_ok=True)

HEARTBEAT_PORT       = 7743
HEARTBEAT_TIMEOUT    = 90
PC_SSH_KEY           = "/home/raven/.ssh/id_ed25519"
THREAT_EXTENSIONS    = (".bat", ".exe", ".ps1", ".vbs", ".cmd")
THREAT_DISPLAY_SECONDS = 3

DUAT_IP              = "192.168.12.231"
DUAT_PORT            = 6176
LEGIOM_IP            = "192.168.12.85"
WATCHDOG_UNLOCK_PORT = 6177
UNLOCK_API_PORT      = 6175

# ── State Variables ────────────────────────────────────────────
watchdog_status   = "UNKNOWN"
watchdog_last_seen = 0
watchdog_pc_name  = ""
pc_ip             = ""

remote_scan_results = []
remote_scan_count   = 0

threat_active  = False
threat_text    = ""
threat_timer   = 0
threats_caught = 0
last_threat_name = "NONE"

current_status = "SYSTEM STANDBY"
target_subnet  = "UNKNOWN"
active_target_ip = ""
target_count   = 0
total_vulns    = 0
audit_progress = ""
bt_count       = 0
wifi_count     = 0

nmap_results  = []
port_results  = []
log_history   = []

can_audit_ftp = False
audit_results = ""

is_scanning = False
flag_t      = 1
uptime_tick = 0

unlock_queue  = []

# ── Seen Threats ───────────────────────────────────────────────
SEEN_FILE = "/home/raven/.raven/seen_threats.json"
seen_threats = set()

def load_seen_threats():
    global seen_threats
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE) as f:
                seen_threats = set(json.load(f))
        except Exception:
            seen_threats = set()

def save_seen_threat(filename):
    seen_threats.add(filename)
    os.makedirs(os.path.dirname(SEEN_FILE), exist_ok=True)
    try:
        with open(SEEN_FILE, "w") as f:
            json.dump(list(seen_threats), f)
    except Exception:
        pass

load_seen_threats()

# ── Hardware Init ──────────────────────────────────────────────
print("Booting Hardware...")
try:
    epd    = epd2in13_V4.EPD()
    gt     = gt1151.GT1151()
    GT_Dev = gt1151.GT_Development()
    GT_Old = gt1151.GT_Development()

    epd.init(epd.FULL_UPDATE)
    gt.GT_Init()
    epd.Clear(0xFF)

    canvas = Image.new('1', (epd.height, epd.width), 255)
    draw   = ImageDraw.Draw(canvas)

    epd.displayPartBaseImage(epd.getbuffer(canvas))
    epd.init(epd.PART_UPDATE)
except Exception as e:
    print(f"Hardware initialization failed: {e}")
    exit()

# ── Touch IRQ Thread ───────────────────────────────────────────
def pthread_irq():
    while flag_t == 1:
        if gt.digital_read(gt.INT) == 0:
            GT_Dev.Touch = 1
        else:
            GT_Dev.Touch = 0
        time.sleep(0.01)

t = threading.Thread(target=pthread_irq, daemon=True)
t.start()

# ── Quarantine Engine ──────────────────────────────────────────
def get_sha256(filepath):
    sha256_hash = hashlib.sha256()
    with open(filepath, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()

def trigger_threat_alert(filename, file_hash):
    global threat_active, threat_text, threat_timer, threats_caught, last_threat_name
    threats_caught += 1
    last_threat_name = filename

    if filename in seen_threats:
        print(f"[*] Seen threat skipped: {filename}")
        return

    while threat_active:
        time.sleep(1)

    threat_text  = f"THREAT: {filename}\nHASH: {file_hash[:12]}"
    threat_timer = time.time()
    threat_active = True
    save_seen_threat(filename)

    try:
        with open("/home/raven/raven_intel.txt", "a") as f:
            f.write(f"[{time.ctime()}] THREAT INTERCEPTED: {filename} | {file_hash}\n")
    except Exception:
        pass
    try:
        with open(LEDGER_FILE, "a") as ledger:
            ledger.write(f"[{time.ctime()}] {filename} | {file_hash}\n")
    except Exception:
        pass

    print(f"[!] QUARANTINE HIT #{threats_caught}: {filename}")

def quarantine_engine():
    processed_files = set()
    print("[*] Quarantine engine online...")
    while flag_t == 1:
        try:
            for filename in os.listdir(QUARANTINE_DIR):
                if any(filename.lower().endswith(ext) for ext in THREAT_EXTENSIONS) and filename not in processed_files:
                    filepath = os.path.join(QUARANTINE_DIR, filename)
                    time.sleep(1)
                    file_hash = get_sha256(filepath)
                    trigger_threat_alert(filename, file_hash)
                    processed_files.add(filename)
        except Exception as e:
            print(f"[!] Quarantine scan error: {e}")
        time.sleep(2)

qt = threading.Thread(target=quarantine_engine, daemon=True)
qt.start()

def clear_threat_signal():
    global threat_active, threat_text, threat_timer
    threat_active = False
    threat_text   = ""
    threat_timer  = 0

# ── Heartbeat Listener ─────────────────────────────────────────
def heartbeat_listener():
    global watchdog_status, watchdog_last_seen, watchdog_pc_name, pc_ip
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", HEARTBEAT_PORT))
    sock.settimeout(5)
    print(f"[*] Heartbeat listener online (UDP {HEARTBEAT_PORT})...")
    while flag_t == 1:
        try:
            data, addr = sock.recvfrom(1024)
            message = data.decode().strip()
            parts = message.split("|")
            if len(parts) >= 4 and parts[0] == "WATCHDOG" and parts[3] == "ALIVE":
                watchdog_pc_name   = parts[1]
                watchdog_last_seen = time.time()
                watchdog_status    = "ONLINE"
                pc_ip = addr[0]
        except socket.timeout:
            pass
        except Exception as e:
            print(f"[!] Heartbeat error: {e}")
        if watchdog_last_seen > 0 and (time.time() - watchdog_last_seen) > HEARTBEAT_TIMEOUT:
            watchdog_status = "OFFLINE"

hb = threading.Thread(target=heartbeat_listener, daemon=True)
hb.start()

# ── Flask Unlock API ───────────────────────────────────────────
def start_unlock_api():
    if not HAS_FLASK:
        return
    app = Flask(__name__)

    @app.route("/health", methods=["GET"])
    def health():
        return jsonify({
            "status": "ok",
            "device": "raven",
            "pending_alerts": len(unlock_queue)
        })

    @app.route("/alert", methods=["POST"])
    def receive_alert():
        global threat_active, threat_text, threat_timer, threats_caught, last_threat_name
        data = request.json
        if not data or "filename" not in data:
            return jsonify({"error": "missing filename"}), 400

        alert = {
            "filename":    data.get("filename", "unknown"),
            "filepath":    data.get("filepath", ""),
            "verdict":     data.get("verdict", "unknown"),
            "threat_name": data.get("threat_name", ""),
            "hash":        data.get("hash", ""),
        }
        unlock_queue.append(alert)

        fname   = alert["filename"]
        verdict = alert["verdict"].upper()
        threats_caught += 1
        last_threat_name = fname

        if fname not in seen_threats:
            threat_text  = f"FILE LOCKED\n{fname[:22]}\n{verdict}"
            threat_timer = time.time()
            threat_active = True
            save_seen_threat(fname)
        else:
            print(f"[*] Seen alert skipped: {fname}")

        try:
            with open("/home/raven/raven_intel.txt", "a") as f:
                f.write(f"[{time.ctime()}] LOCK ALERT: {fname} [{verdict}]\n")
        except Exception:
            pass

        return jsonify({"status": "queued", "pending": len(unlock_queue)})

    import logging
    log = logging.getLogger("werkzeug")
    log.setLevel(logging.ERROR)
    app.run(host="0.0.0.0", port=UNLOCK_API_PORT, debug=False, use_reloader=False)

if HAS_FLASK:
    flask_thread = threading.Thread(target=start_unlock_api, daemon=True)
    flask_thread.start()
    print(f"[*] Unlock API on port {UNLOCK_API_PORT}")

# ── Unlock Decision ────────────────────────────────────────────
def send_unlock_decision(action, alert):
    global current_status
    target_url = f"http://{LEGIOM_IP}:{WATCHDOG_UNLOCK_PORT}/unlock_decision"
    payload = json.dumps({
        "action":     action,
        "filename":   alert["filename"],
        "filepath":   alert.get("filepath", ""),
        "decided_by": "raven",
        "timestamp":  time.ctime()
    }).encode()

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(8)
        sock.connect((LEGIOM_IP, WATCHDOG_UNLOCK_PORT))
        http_req = (
            f"POST /unlock_decision HTTP/1.1\r\n"
            f"Host: {LEGIOM_IP}:{WATCHDOG_UNLOCK_PORT}\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(payload)}\r\n"
            f"Connection: close\r\n\r\n"
        ).encode() + payload
        sock.sendall(http_req)
        sock.close()
        current_status = f"{action.upper()}: {alert['filename'][:16]}"
        print(f"[*] Sent {action} to Watchdog for {alert['filename']}")
    except Exception as e:
        current_status = "WATCHDOG UNREACHABLE"
        print(f"[!] Could not reach Watchdog: {e}")

    try:
        with open("/home/raven/raven_intel.txt", "a") as f:
            f.write(f"[{time.ctime()}] UNLOCK DECISION: {action} for {alert['filename']}\n")
    except Exception:
        pass

# ── Remote Scan ────────────────────────────────────────────────
def get_pc_ssh_user():
    return os.environ.get("RAVEN_PC_USER", "user")

def run_remote_scan():
    global current_status, is_scanning, remote_scan_results, remote_scan_count
    is_scanning = True
    remote_scan_results = []
    remote_scan_count   = 0

    target_ip = pc_ip if pc_ip else LEGIOM_IP

    if not target_ip:
        current_status = "NO PC DETECTED"
        is_scanning = False
        return

    if watchdog_status != "ONLINE":
        current_status = "PC WATCHDOG OFFLINE"
        is_scanning = False
        return

    if not os.path.exists(PC_SSH_KEY):
        current_status = "NO SSH KEY FOUND"
        is_scanning = False
        return

    current_status = f"REMOTE SCAN: {target_ip}"
    pc_user = get_pc_ssh_user()

    ext_patterns = " ".join([f"*{ext}" for ext in THREAT_EXTENSIONS])
    remote_cmd = f'dir /b "%USERPROFILE%\\Downloads\\"{ext_patterns} 2>nul'

    try:
        ssh_cmd = [
            "ssh", "-i", PC_SSH_KEY,
            "-o", "StrictHostKeyChecking=no",
            "-o", "ConnectTimeout=10",
            f"{pc_user}@{target_ip}",
            "cmd", "/c", remote_cmd
        ]
        result = subprocess.check_output(ssh_cmd, universal_newlines=True, timeout=30)
        found_files = [f.strip() for f in result.strip().split("\n") if f.strip()]
        remote_scan_count = len(found_files)

        if remote_scan_count == 0:
            remote_scan_results = ["CLEAN: No threats found."]
            current_status = "REMOTE: ALL CLEAR"
        else:
            for filename in found_files:
                remote_scan_results.append(f"[!] {filename}")
                try:
                    scp_cmd = [
                        "scp", "-i", PC_SSH_KEY,
                        "-o", "StrictHostKeyChecking=no",
                        f'{pc_user}@{target_ip}:"%USERPROFILE%/Downloads/{filename}"',
                        f"{QUARANTINE_DIR}/{filename}"
                    ]
                    subprocess.run(scp_cmd, check=True, timeout=30,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                except Exception:
                    remote_scan_results.append("  -> PULL FAILED")

            current_status = f"REMOTE: {remote_scan_count} THREATS"
            with open("/home/raven/raven_intel.txt", "a") as f:
                f.write(f"[{time.ctime()}] REMOTE SCAN: {remote_scan_count} threats on {target_ip}\n")

    except subprocess.TimeoutExpired:
        current_status = "REMOTE: TIMEOUT"
        remote_scan_results = ["SSH connection timed out."]
    except subprocess.CalledProcessError:
        remote_scan_results = ["CLEAN: No threats found."]
        current_status = "REMOTE: ALL CLEAR"
    except Exception as e:
        current_status = "REMOTE: SSH FAILED"
        remote_scan_results = [f"Error: {str(e)[:28]}"]

    is_scanning = False

# ── Recon Engines ──────────────────────────────────────────────
def get_local_subnet():
    global current_status, target_subnet
    current_status = "DETECTING NETWORK..."
    try:
        route_info = subprocess.check_output(
            "ip route | grep -v default | grep src | head -n 1 | awk '{print $1}'",
            shell=True, universal_newlines=True
        ).strip()
        target_subnet = route_info if route_info else "192.168.1.0/24"
        current_status = "NETWORK DETECTED"
    except Exception:
        target_subnet = "192.168.1.0/24"
        current_status = "DETECT FAILED"

def run_nmap_scan():
    global current_status, target_count, is_scanning, nmap_results
    is_scanning   = True
    current_status = "PING SWEEPING..."
    nmap_results  = []
    try:
        result = subprocess.check_output(['nmap', '-sn', target_subnet], universal_newlines=True)
        for line in result.split('\n'):
            if "Nmap scan report for" in line:
                nmap_results.append(line.replace("Nmap scan report for ", "").strip())
        target_count = len(nmap_results)
        if target_count > 0:
            nmap_results.append("<- BACK TO MENU")
        current_status = "SWEEP COMPLETE"
    except Exception:
        current_status = "SWEEP FAILED"
    is_scanning = False

def run_port_scan():
    global current_status, is_scanning, port_results, can_audit_ftp, audit_results
    is_scanning   = True
    can_audit_ftp = False
    audit_results = ""
    current_status = f"SCAN: {active_target_ip}"
    port_results  = []
    try:
        result = subprocess.check_output(['nmap', '-F', active_target_ip], universal_newlines=True)
        for line in result.split('\n'):
            if "/tcp" in line and "open" in line:
                raw_port = line.split()[0]
                if raw_port == "21/tcp":
                    can_audit_ftp = True
                if raw_port in VULN_ADVISORY:
                    port_results.append(f"[!] {VULN_ADVISORY[raw_port]}")
                else:
                    port_results.append(f"+ {' '.join(line.split())}")
        if len(port_results) == 0:
            port_results.append("NO OPEN PORTS FOUND")
        current_status = "PORT SCAN COMPLETE"
    except Exception:
        port_results.append("SCAN FAILED")
        current_status = "PORT SCAN ERROR"
    is_scanning = False

def run_full_audit():
    global current_status, is_scanning, total_vulns, audit_progress
    is_scanning  = True
    total_vulns  = 0
    current_status = "AUDITING LAN..."
    if len(nmap_results) == 0:
        current_status = "ERROR: SWEEP LAN FIRST"
        is_scanning = False
        return
    for i, target in enumerate(nmap_results):
        if target == "<- BACK TO MENU":
            continue
        ip = target.split("(")[1].replace(")", "") if "(" in target else target
        audit_progress = f"[{i+1}/{target_count}] {ip}"
        try:
            result = subprocess.check_output(['nmap', '-F', ip], universal_newlines=True)
            for line in result.split('\n'):
                if "/tcp" in line and "open" in line:
                    raw_port = line.split()[0]
                    if raw_port in VULN_ADVISORY:
                        total_vulns += 1
                        with open("/home/raven/raven_intel.txt", "a") as f:
                            f.write(f"[!] VULN FOUND: {raw_port} on {ip}\n")
        except Exception:
            pass
    audit_progress = ""
    current_status = "AUDIT COMPLETE"
    is_scanning = False

def audit_ftp():
    global current_status, audit_results
    current_status = "EXPLOITING FTP..."
    try:
        ftp = ftplib.FTP(active_target_ip, timeout=5)
        ftp.login('anonymous', 'anonymous@example.com')
        files = ftp.nlst()
        ftp.quit()
        if files:
            audit_results = f"PWNED: {len(files)} files exposed."
            with open("/home/raven/raven_intel.txt", "a") as f:
                f.write(f"\n[!] VULN PROVEN: Anonymous FTP on {active_target_ip}\n")
                f.write(f"    Exposed Files: {', '.join(files[:5])}\n")
        else:
            audit_results = "PWNED: Login worked, folder empty."
    except Exception:
        audit_results = "SECURE: Auth Required."
    current_status = "EXPLOIT COMPLETE"

def run_bt_scan():
    global current_status, bt_count, is_scanning
    is_scanning = True
    current_status = "SCANNING BLUETOOTH..."
    try:
        subprocess.run(["bluetoothctl", "--timeout", "5", "scan", "on"], capture_output=True)
        result = subprocess.check_output("bluetoothctl devices | wc -l", shell=True, universal_newlines=True)
        bt_count = int(result.strip())
        current_status = "BT SCAN COMPLETE"
    except Exception:
        current_status = "BT SCAN FAILED"
    is_scanning = False

def run_wifi_scan():
    global current_status, wifi_count, is_scanning
    is_scanning = True
    current_status = "SCANNING AIRSPACE..."
    try:
        result = subprocess.check_output(
            "nmcli -t -f BSSID dev wifi | grep -v '^$' | wc -l",
            shell=True, universal_newlines=True
        )
        wifi_count = int(result.strip())
        current_status = "WIFI SCAN COMPLETE"
    except Exception:
        current_status = "WIFI SCAN FAILED"
    is_scanning = False

def export_data():
    try:
        timestamp = time.strftime("%Y-%m-%d %H:%M")
        with open("/home/raven/raven_intel.txt", "a") as f:
            f.write(f"\n--- SESSION: {timestamp} ---\n")
            f.write(f"SUBNET: {target_subnet} | HOSTS: {target_count} | VULNS: {total_vulns}\n")
            for ip in nmap_results:
                if ip != "<- BACK TO MENU":
                    f.write(f"  - {ip}\n")
            f.write(f"WIFI APs: {wifi_count} | BT DEVICES: {bt_count}\n")
            f.write(f"THREATS INTERCEPTED: {threats_caught} | LAST: {last_threat_name}\n")
            f.write(f"WATCHDOG: {watchdog_status} | PC: {pc_ip or 'N/A'}\n")
    except Exception:
        pass

def load_logs():
    global log_history
    log_history = []
    if os.path.exists("/home/raven/raven_intel.txt"):
        try:
            with open("/home/raven/raven_intel.txt", "r") as f:
                lines = f.readlines()
            for line in lines:
                if "--- SESSION:" in line:
                    log_history.append(line.replace("---", "").strip())
        except Exception:
            log_history.append("ERROR READING LOGS")
    else:
        log_history.append("NO LOGS FOUND")
    log_history = log_history[::-1]

def get_sys_health():
    try:
        temp = subprocess.check_output(
            "cat /sys/class/thermal/thermal_zone0/temp", shell=True
        ).decode().strip()
        temp_c = int(temp) / 1000.0
        mem = subprocess.check_output(
            "free -m | awk 'NR==2{printf \"%s/%sMB\", $3,$2}'", shell=True
        ).decode().strip()
        ip = subprocess.check_output(
            "hostname -I | awk '{print $1}'", shell=True
        ).decode().strip()
        return f"{temp_c:.1f}C", mem, ip
    except Exception:
        return "N/A", "N/A", "N/A"

def trigger_shutdown():
    global flag_t
    export_data()
    draw.rectangle((0, 0, 250, 122), fill=255)
    draw.rectangle((0, 0, 250, 20), fill=0)
    draw.text((5, 2), "RAVEN OS | OFFLINE", fill=255)
    draw.text((10, 40), "SYSTEM HALTED SAFELY.", fill=0)
    draw.text((10, 60), "All intel saved to SD Card.", fill=0)
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    draw.text((10, 90), f"PWR OFF: {timestamp}", fill=0)
    epd.init(epd.FULL_UPDATE)
    epd.display(epd.getbuffer(canvas))
    time.sleep(2)
    epd.sleep()
    flag_t = 0
    print("\n[!] Halting OS...")
    subprocess.run(["sudo", "shutdown", "-h", "now"])
    exit()

# ── Main OS Loop ───────────────────────────────────────────────
try:
    print("\nRaven OS Online. Standing by.")
    last_ui_update = time.time()
    last_tap_time  = 0

    while True:
        # ── Threat Interrupt ──────────────────────────────────
        if threat_active:
            if time.time() - threat_timer >= THREAT_DISPLAY_SECONDS:
                clear_threat_signal()
            else:
                # Check for tap to dismiss
                gt.GT_Scan(GT_Dev, GT_Old)
                if GT_Dev.TouchpointFlag:
                    GT_Dev.TouchpointFlag = 0
                    now = time.time()
                    if now - last_tap_time > 0.4:
                        last_tap_time = now
                        clear_threat_signal()
                        continue

                draw.rectangle((0, 0, 250, 122), fill=0)
                draw.text((5, 2), "!!! THREAT DETECTED !!!", fill=255)
                threat_lines = threat_text.split("\n")
                for i, line in enumerate(threat_lines[:4]):
                    draw.text((10, 28 + (i * 18)), line[:28], fill=255)
                draw.text((10, 97), "[ TAP TO DISMISS ]", fill=255)
                remaining = int(THREAT_DISPLAY_SECONDS - (time.time() - threat_timer))
                draw.text((10, 110), f"Auto-clear in {remaining}s", fill=255)
                epd.displayPartial(epd.getbuffer(canvas))
                time.sleep(0.1)
                continue

        # ── Touch Input ───────────────────────────────────────
        gt.GT_Scan(GT_Dev, GT_Old)
        if (GT_Old.X[0] == GT_Dev.X[0] and
            GT_Old.Y[0] == GT_Dev.Y[0] and
            GT_Old.S[0] == GT_Dev.S[0]):
            pass
        elif GT_Dev.TouchpointFlag:
            GT_Dev.TouchpointFlag = 0

            current_time = time.time()
            if current_time - last_tap_time < 0.4:
                continue
            last_tap_time = current_time

            raw_y       = GT_Dev.Y[0]
            is_left_tap  = raw_y >= 125
            is_right_tap = raw_y < 125

            # ── Hitboxes ──────────────────────────────────────
            if ui_page == "MAIN":
                if is_right_tap:
                    ui_page    = "MENU"
                    menu_index = 0
                elif is_left_tap:
                    # Test unlock alert
                    test_alert = {
                        "filename":    "raven_unlock_test.txt",
                        "filepath":    "C:/Users/YourUser/Downloads/raven_unlock_test.txt",
                        "verdict":     "test",
                        "threat_name": "Manual chain test",
                        "hash":        "0000000000000000",
                    }
                    unlock_queue.append(test_alert)
                    threat_text  = "UNLOCK TEST\nraven_unlock_test.txt\nTEST ALERT"
                    threat_timer = time.time()
                    threat_active = True
                    current_status = "TEST: Unlock alert queued"

            elif ui_page == "MENU":
                if is_left_tap:
                    menu_index = (menu_index + 1) % len(menu_options)
                elif is_right_tap:
                    if menu_index == 0:
                        ui_page = "TOOL_NMAP"
                    elif menu_index == 1:
                        ui_page     = "NMAP_RESULTS"
                        intel_index = 0
                    elif menu_index == 2:
                        ui_page = "TOOL_AUDIT"
                    elif menu_index == 3:
                        ui_page = "TOOL_BT"
                    elif menu_index == 4:
                        ui_page = "TOOL_WIFI"
                    elif menu_index == 5:
                        ui_page = "TOOL_SYS"
                    elif menu_index == 6:
                        load_logs()
                        ui_page = "VIEW_LOGS"
                    elif menu_index == 7:
                        ui_page = "TOOL_REMOTE"
                    elif menu_index == 8:
                        unlock_index = 0
                        ui_page = "UNLOCK_QUEUE"
                    elif menu_index == 9:
                        trigger_shutdown()
                    elif menu_index == 10:
                        ui_page = "MAIN"

            elif ui_page == "TOOL_NMAP":
                if is_left_tap:
                    if target_subnet == "UNKNOWN":
                        threading.Thread(target=get_local_subnet, daemon=True).start()
                    elif not is_scanning:
                        threading.Thread(target=run_nmap_scan, daemon=True).start()
                        ui_page = "MAIN"
                elif is_right_tap:
                    ui_page = "MENU"

            elif ui_page == "TOOL_AUDIT":
                if is_left_tap and not is_scanning:
                    threading.Thread(target=run_full_audit, daemon=True).start()
                    ui_page = "MAIN"
                elif is_right_tap:
                    ui_page = "MENU"

            elif ui_page == "NMAP_RESULTS":
                if is_left_tap:
                    if len(nmap_results) > 0:
                        intel_index = (intel_index + 1) % len(nmap_results)
                elif is_right_tap:
                    if len(nmap_results) > 0 and not is_scanning:
                        raw_target = nmap_results[intel_index]
                        if raw_target == "<- BACK TO MENU":
                            ui_page = "MENU"
                        else:
                            global active_target_ip
                            active_target_ip = raw_target.split("(")[1].replace(")", "") if "(" in raw_target else raw_target
                            threading.Thread(target=run_port_scan, daemon=True).start()
                            ui_page = "TARGET_DETAILS"
                    else:
                        ui_page = "MENU"

            elif ui_page == "TARGET_DETAILS":
                if is_left_tap and can_audit_ftp and not audit_results:
                    threading.Thread(target=audit_ftp, daemon=True).start()
                elif is_right_tap:
                    ui_page = "NMAP_RESULTS"

            elif ui_page in ["TOOL_BT", "TOOL_WIFI", "TOOL_SYS", "VIEW_LOGS"]:
                if is_left_tap and not is_scanning:
                    if ui_page == "TOOL_BT":
                        threading.Thread(target=run_bt_scan, daemon=True).start()
                    elif ui_page == "TOOL_WIFI":
                        threading.Thread(target=run_wifi_scan, daemon=True).start()
                    ui_page = "MAIN"
                elif is_right_tap:
                    ui_page = "MENU"

            elif ui_page == "TOOL_REMOTE":
                if is_left_tap and not is_scanning:
                    threading.Thread(target=run_remote_scan, daemon=True).start()
                    ui_page = "REMOTE_RESULTS"
                elif is_right_tap:
                    ui_page = "MENU"

            elif ui_page == "REMOTE_RESULTS":
                if is_right_tap:
                    ui_page = "MENU"

            elif ui_page == "UNLOCK_QUEUE":
                if is_left_tap:
                    if len(unlock_queue) > 0:
                        unlock_index = (unlock_index + 1) % len(unlock_queue)
                elif is_right_tap:
                    if len(unlock_queue) > 0:
                        active_unlock = unlock_queue[unlock_index]
                        ui_page = "UNLOCK_CONFIRM"
                    else:
                        ui_page = "MENU"

            elif ui_page == "UNLOCK_CONFIRM":
                if active_unlock:
                    if is_left_tap:
                        threading.Thread(
                            target=send_unlock_decision,
                            args=("unlock", active_unlock),
                            daemon=True
                        ).start()
                        if active_unlock in unlock_queue:
                            unlock_queue.remove(active_unlock)
                        active_unlock = None
                        ui_page = "UNLOCK_QUEUE"
                    elif is_right_tap:
                        threading.Thread(
                            target=send_unlock_decision,
                            args=("deny", active_unlock),
                            daemon=True
                        ).start()
                        if active_unlock in unlock_queue:
                            unlock_queue.remove(active_unlock)
                        active_unlock = None
                        ui_page = "UNLOCK_QUEUE"

            last_ui_update = 0

        # ── Rendering ─────────────────────────────────────────
        current_time = time.time()
        if current_time - last_ui_update >= 2.0:

            draw.rectangle((0, 0, 250, 122), fill=255)
            draw.rectangle((0, 0, 250, 20), fill=0)
            draw.text((5, 2), f"RAVEN OS | {ui_page}", fill=255)

            if ui_page == "MAIN":
                draw.text((10, 25), f"STATE: {current_status}", fill=0)
                draw.text((10, 38), f"TARGETS: {target_count} | VULNS: {total_vulns}", fill=0)
                draw.text((10, 51), f"WIFI: {wifi_count} | BT: {bt_count}", fill=0)
                draw.text((10, 64), f"THREATS: {threats_caught} | {last_threat_name[:16]}", fill=0)

                wd_display = watchdog_status
                if watchdog_status == "ONLINE" and watchdog_last_seen > 0:
                    ago = int(time.time() - watchdog_last_seen)
                    wd_display = f"ONLINE ({ago}s ago)"
                draw.text((10, 77), f"WATCHDOG: {wd_display}", fill=0)

                if len(seen_threats) > 0:
                    draw.text((10, 90), f"SEEN: {len(seen_threats)} prior threats", fill=0)

                if is_scanning:
                    if audit_progress:
                        draw.text((10, 90), f"> {audit_progress[:25]}", fill=0)
                    elif uptime_tick % 2 == 0:
                        draw.text((10, 90), ">>> SCAN IN PROGRESS <<<", fill=0)

                draw.rectangle((130, 100, 240, 120), outline=0, fill=255)
                draw.text((160, 103), "[ MENU ]", fill=0)

            elif ui_page == "MENU":
                draw.text((10, 25), "SELECT MODULE:", fill=0)
                start_m_idx  = max(0, menu_index - 3)
                page_m_items = menu_options[start_m_idx:start_m_idx+4]
                for i, option in enumerate(page_m_items):
                    actual_idx = start_m_idx + i
                    prefix = " > " if actual_idx == menu_index else "   "
                    draw.text((10, 40 + (i * 13)), f"{prefix}{option}", fill=0)
                draw.rectangle((10, 100, 120, 120), outline=0, fill=255)
                draw.text((30, 103), "[ CYCLE ]", fill=0)
                draw.rectangle((130, 100, 240, 120), outline=0, fill=255)
                draw.text((155, 103), "[ SELECT ]", fill=0)

            elif ui_page == "TOOL_NMAP":
                draw.text((10, 25), "MODULE: LAN SWEEP", fill=0)
                draw.text((10, 45), f"Target: {target_subnet}", fill=0)
                draw.text((10, 60), "Finds all active IP addresses.", fill=0)
                draw.rectangle((10, 100, 120, 120), outline=0, fill=255)
                button_text = "[ DETECT ]" if target_subnet == "UNKNOWN" else "[ SWEEP ]"
                draw.text((30, 103), button_text, fill=0)
                draw.rectangle((130, 100, 240, 120), outline=0, fill=255)
                draw.text((160, 103), "[ BACK ]", fill=0)

            elif ui_page == "TOOL_AUDIT":
                draw.text((10, 25), "MODULE: AUTO-AUDIT", fill=0)
                draw.text((10, 45), "Deep scans all swept targets", fill=0)
                draw.text((10, 60), "to tally total vulnerabilities.", fill=0)
                draw.rectangle((10, 100, 120, 120), outline=0, fill=255)
                draw.text((30, 103), "[ START ]", fill=0)
                draw.rectangle((130, 100, 240, 120), outline=0, fill=255)
                draw.text((160, 103), "[ BACK ]", fill=0)

            elif ui_page == "NMAP_RESULTS":
                draw.text((10, 25), "SELECT TARGET FOR PORT SCAN:", fill=0)
                if len(nmap_results) == 0:
                    draw.text((10, 50), "No data. Run LAN sweep first.", fill=0)
                else:
                    start_idx  = max(0, intel_index - 3)
                    page_items = nmap_results[start_idx:start_idx+4]
                    for i, item in enumerate(page_items):
                        actual_idx = start_idx + i
                        prefix = " > " if actual_idx == intel_index else "   "
                        draw.text((5, 42 + (i*14)), f"{prefix}{item[:26]}", fill=0)
                draw.rectangle((10, 100, 120, 120), outline=0, fill=255)
                draw.text((30, 103), "[ CYCLE ]", fill=0)
                draw.rectangle((130, 100, 240, 120), outline=0, fill=255)
                draw.text((150, 103), "[ DEEP SCAN ]", fill=0)

            elif ui_page == "TARGET_DETAILS":
                draw.text((10, 25), f"TARGET: {active_target_ip[-18:]}", fill=0)
                if is_scanning:
                    if uptime_tick % 2 == 0:
                        draw.text((10, 50), ">>> PROBING PORTS <<<", fill=0)
                else:
                    for i, port in enumerate(port_results[:3]):
                        draw.text((5, 42 + (i*14)), f"{port[:30]}", fill=0)
                    if audit_results:
                        draw.text((5, 84), f"-> {audit_results[:30]}", fill=0)
                if can_audit_ftp and not audit_results:
                    draw.rectangle((10, 100, 120, 120), outline=0, fill=255)
                    draw.text((25, 103), "[ AUDIT FTP ]", fill=0)
                draw.rectangle((130, 100, 240, 120), outline=0, fill=255)
                draw.text((160, 103), "[ BACK ]", fill=0)

            elif ui_page in ["TOOL_BT", "TOOL_WIFI", "TOOL_SYS", "VIEW_LOGS"]:
                if ui_page == "TOOL_BT":
                    draw.text((10, 25), "MODULE: BLUETOOTH", fill=0)
                elif ui_page == "TOOL_WIFI":
                    draw.text((10, 25), "MODULE: WIFI AIRSPACE", fill=0)
                elif ui_page == "TOOL_SYS":
                    temp, mem, ip = get_sys_health()
                    draw.text((10, 25), "MODULE: SYSTEM HEALTH", fill=0)
                    draw.text((10, 45), f"CPU Temp: {temp}", fill=0)
                    draw.text((10, 60), f"RAM Use:  {mem}", fill=0)
                    draw.text((10, 75), f"Local IP: {ip}", fill=0)
                elif ui_page == "VIEW_LOGS":
                    draw.text((10, 25), "PAST SESSION HISTORY:", fill=0)
                    for i, log in enumerate(log_history[:4]):
                        draw.text((10, 42 + (i*14)), f"> {log[:28]}", fill=0)
                if ui_page in ["TOOL_BT", "TOOL_WIFI"]:
                    draw.rectangle((10, 100, 120, 120), outline=0, fill=255)
                    draw.text((30, 103), "[ SCAN ]", fill=0)
                draw.rectangle((130, 100, 240, 120), outline=0, fill=255)
                draw.text((160, 103), "[ BACK ]", fill=0)

            elif ui_page == "TOOL_REMOTE":
                draw.text((10, 25), "MODULE: REMOTE SCAN PC", fill=0)
                draw.text((10, 42), f"Watchdog: {watchdog_status}", fill=0)
                target_display = pc_ip if pc_ip else LEGIOM_IP
                draw.text((10, 56), f"PC: {target_display}", fill=0)
                draw.text((10, 72), "Scans Downloads for threats", fill=0)
                draw.rectangle((10, 100, 120, 120), outline=0, fill=255)
                draw.text((30, 103), "[ SCAN ]", fill=0)
                draw.rectangle((130, 100, 240, 120), outline=0, fill=255)
                draw.text((160, 103), "[ BACK ]", fill=0)

            elif ui_page == "REMOTE_RESULTS":
                target_display = pc_ip if pc_ip else LEGIOM_IP
                draw.text((10, 25), f"REMOTE SCAN: {target_display}", fill=0)
                if is_scanning:
                    if uptime_tick % 2 == 0:
                        draw.text((10, 50), ">>> SCANNING PC <<<", fill=0)
                else:
                    if len(remote_scan_results) == 0:
                        draw.text((10, 45), "No results yet.", fill=0)
                    else:
                        for i, result in enumerate(remote_scan_results[:4]):
                            draw.text((5, 42 + (i*14)), f"{result[:30]}", fill=0)
                draw.rectangle((130, 100, 240, 120), outline=0, fill=255)
                draw.text((160, 103), "[ BACK ]", fill=0)

            elif ui_page == "UNLOCK_QUEUE":
                draw.text((10, 25), f"UNLOCK QUEUE ({len(unlock_queue)} pending):", fill=0)
                if len(unlock_queue) == 0:
                    draw.text((10, 55), "No locked files.", fill=0)
                    draw.text((10, 70), "All clear.", fill=0)
                else:
                    start_u = max(0, unlock_index - 2)
                    for i, alert in enumerate(unlock_queue[start_u:start_u+3]):
                        actual = start_u + i
                        prefix = " > " if actual == unlock_index else "   "
                        fname   = alert["filename"][:22]
                        verdict = alert["verdict"][:8].upper()
                        draw.text((5, 38 + (i*16)), f"{prefix}{fname}", fill=0)
                        draw.text((15, 50 + (i*16)), f"  [{verdict}]", fill=0)
                draw.rectangle((10, 100, 120, 120), outline=0, fill=255)
                draw.text((30, 103), "[ CYCLE ]", fill=0)
                draw.rectangle((130, 100, 240, 120), outline=0, fill=255)
                draw.text((145, 103), "[ SELECT ]", fill=0)

            elif ui_page == "UNLOCK_CONFIRM":
                if active_unlock:
                    fname   = active_unlock["filename"]
                    verdict = active_unlock["verdict"].upper()
                    threat  = active_unlock.get("threat_name", "")
                    fhash   = active_unlock.get("hash", "")
                    draw.rectangle((0, 0, 250, 20), fill=0)
                    draw.text((5, 2), "LOCKED FILE - DECIDE:", fill=255)
                    draw.text((5, 25), f"{fname[:28]}", fill=0)
                    draw.text((5, 39), f"Status: {verdict}", fill=0)
                    if threat:
                        draw.text((5, 52), f"{threat[:28]}", fill=0)
                    if fhash:
                        draw.text((5, 65), f"{fhash[:24]}...", fill=0)
                    draw.rectangle((2, 100, 118, 120), outline=0, fill=255)
                    draw.text((22, 103), "[ UNLOCK ]", fill=0)
                    draw.rectangle((132, 100, 248, 120), fill=0)
                    draw.text((162, 103), "[ DENY ]", fill=255)

            epd.displayPartial(epd.getbuffer(canvas))
            uptime_tick   += 1
            last_ui_update = current_time

        time.sleep(0.1)

except KeyboardInterrupt:
    print("\nShutting down Raven...")
    flag_t = 0
    epd.sleep()
    time.sleep(1)
    epd.Dev_exit()
    exit()
