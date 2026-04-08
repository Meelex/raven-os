#!/usr/bin/env python3
"""
watchdog_windows.py - Raven OS
Monitors Downloads folder on Windows.
Checks hashes against Duat (Pi 5) Hash DB.
Sends lock requests to Duat on threats.
Displays live connection status for Duat and Raven.

Requirements:
    pip install watchdog requests

Run:
    python watchdog_windows.py
"""

import os
import sys
import json
import hashlib
import threading
import time
import subprocess
import tkinter as tk
from tkinter import scrolledtext
from datetime import datetime
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    HAS_WATCHDOG = True
except ImportError:
    HAS_WATCHDOG = False

try:
    import process_monitor as _pm
    HAS_PROCMON = True
except ImportError:
    HAS_PROCMON = False

try:
    import requests
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

# ── Config ─────────────────────────────────────────────────────
CONFIG_DIR  = Path(os.environ.get("APPDATA", "C:/Users")) / "RavenOS"
CONFIG_FILE = CONFIG_DIR / "watchdog_config.json"
LOG_FILE    = CONFIG_DIR / "watchdog.log"
CONFIG_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_CONFIG = {
    "downloads_path":   str(Path.home() / "Downloads"),
    "duat_ip":          "",
    "duat_port":        "6174",
    "duat_unlock_port": "6176",
    "raven_ip":         "192.168.1.3",
    "raven_port":       "6175",
    "raven_heartbeat_port": "7743",
    "hashdb_enabled":   False,
    "lock_on_threat":   True,
    "scan_on_start":    True,
}

# ── Registry ──────────────────────────────────────────────────
REGISTRY_FILE = CONFIG_DIR / "registry.json"

NODES = [
    {"id": "raven",  "name": "RAVEN",  "role": "Portable Device",      "shape": "◆", "color": "#00ff88"},
    {"id": "duat",   "name": "DUAT",   "role": "Home Base",             "shape": "⬡", "color": "#00ff88"},
    {"id": "legiom", "name": "LEGIOM", "role": "Primary Workstation",   "shape": "■", "color": "#ff3344"},
    {"id": "scarab", "name": "SCARAB", "role": "Travel Router",         "shape": "⬟", "color": "#444466"},
    {"id": "anubis", "name": "ANUBIS", "role": "Guardian Node",         "shape": "△", "color": "#444466"},
]

def load_registry():
    if REGISTRY_FILE.exists():
        try:
            with open(REGISTRY_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_registry(reg):
    with open(REGISTRY_FILE, "w") as f:
        json.dump(reg, f, indent=2)

# ── Heartbeat ──────────────────────────────────────────────────────
import socket
import webbrowser
import getpass

HOSTNAME = "Legiom"
USERNAME = getpass.getuser()

def heartbeat_loop(cfg_ref):
    """Send UDP heartbeat to Raven every 30 seconds so it shows ONLINE."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    while True:
        raven_ip = cfg_ref.get("raven_ip", "")
        port     = int(cfg_ref.get("raven_heartbeat_port", 7743))
        if raven_ip:
            try:
                msg = f"WATCHDOG|{HOSTNAME}|{USERNAME}|ALIVE"
                sock.sendto(msg.encode(), (raven_ip, port))
            except Exception:
                pass
        time.sleep(30)

SUSPICIOUS_EXTS = {
    ".exe", ".bat", ".cmd", ".ps1", ".vbs", ".js",
    ".msi", ".dll", ".scr", ".pif", ".com", ".jar",
    ".hta", ".wsf", ".reg", ".inf"
}

# ── Raven unlock listener (port 6177) ─────────────────────────
WATCHDOG_UNLOCK_PORT = 6177
_app_ref = None  # set after app init

class UnlockHandler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def send_json(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            locked = {}
            if _app_ref:
                locked = {fp: info["name"] for fp, info in _app_ref.locked_files.items()}
            self.send_json(200, {"status": "ok", "device": "watchdog",
                                 "locked_files": locked})
        else:
            self.send_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path in ("/unlock", "/unlock_decision"):
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length))
            filepath = data.get("filepath", "")
            if not filepath or not _app_ref:
                self.send_json(400, {"error": "missing filepath or app not ready"})
                return
            # Run unlock in background thread
            threading.Thread(
                target=_app_ref.local_unlock,
                args=(filepath,), daemon=True
            ).start()
            self.send_json(200, {"status": "unlock_requested", "filepath": filepath})
        else:
            self.send_json(404, {"error": "not found"})

def start_unlock_listener():
    server = HTTPServer(("0.0.0.0", WATCHDOG_UNLOCK_PORT), UnlockHandler)
    server.serve_forever()

# ── Local lock / unlock (icacls) ───────────────────────────────
def icacls_lock(filepath):
    result = subprocess.run(
        ["icacls", filepath, "/deny", f"{USERNAME}:RX"],
        capture_output=True, text=True, timeout=15
    )
    return result.returncode == 0, result.stderr.strip()

def icacls_unlock(filepath):
    subprocess.run(
        ["icacls", filepath, "/remove:d", USERNAME],
        capture_output=True, text=True, timeout=15
    )
    result = subprocess.run(
        ["icacls", filepath, "/grant", f"{USERNAME}:F"],
        capture_output=True, text=True, timeout=15
    )
    return result.returncode == 0, result.stderr.strip()

# ── Config persistence ─────────────────────────────────────────
def load_config():
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE) as f:
                saved = json.load(f)
            cfg = DEFAULT_CONFIG.copy()
            cfg.update(saved)
            return cfg
        except Exception:
            pass
    return DEFAULT_CONFIG.copy()

def save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)

# ── Hashing ────────────────────────────────────────────────────
def hash_file(path, chunk=65536):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while True:
                block = f.read(chunk)
                if not block:
                    break
                h.update(block)
        return h.hexdigest()
    except (PermissionError, FileNotFoundError, OSError):
        return None

# ── Network helpers ────────────────────────────────────────────
def _get(url, timeout=6):
    try:
        if HAS_REQUESTS:
            r = requests.get(url, timeout=timeout, verify=False)
            return r.json(), None
        else:
            import ssl as _ssl
            from urllib.request import urlopen, Request
            ctx = _ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = _ssl.CERT_NONE
            with urlopen(Request(url), timeout=timeout, context=ctx) as resp:
                return json.loads(resp.read()), None
    except Exception as e:
        return None, str(e)

def _post(url, data, timeout=8):
    try:
        if HAS_REQUESTS:
            r = requests.post(url, json=data, timeout=timeout, verify=False)
            return r.json(), None
        else:
            import ssl as _ssl
            from urllib.request import urlopen, Request
            ctx = _ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = _ssl.CERT_NONE
            body = json.dumps(data).encode()
            req = Request(url, data=body,
                          headers={"Content-Type": "application/json"})
            with urlopen(req, timeout=timeout, context=ctx) as resp:
                return json.loads(resp.read()), None
    except Exception as e:
        return None, str(e)

# ── Device clients ─────────────────────────────────────────────
def check_duat(cfg):
    ip = cfg.get("duat_ip", "")
    if not ip:
        return False, "not configured"
    data, err = _get(f"https://{ip}:{cfg['duat_port']}/health")
    if data and data.get("status") == "ok":
        total = data.get("db_stats", {}).get("total_hashes", 0)
        return True, f"{total:,} hashes"
    return False, err or "offline"

def check_raven(cfg):
    ip = cfg.get("raven_ip", "")
    if not ip:
        return False, "not configured"
    data, err = _get(f"http://{ip}:{cfg['raven_port']}/health", timeout=4)
    if data and data.get("status") == "ok":
        pending = data.get("pending_alerts", 0)
        return True, f"{pending} pending"
    return False, err or "offline"

def query_hashdb(cfg, file_hash, filename):
    ip = cfg.get("duat_ip", "")
    if not ip or not cfg.get("hashdb_enabled"):
        return {"verdict": "hashdb_disabled"}
    data, err = _get(
        f"https://{ip}:{cfg['duat_port']}/lookup/{file_hash}?filename={filename}"
    )
    return data if data else {"verdict": "hashdb_unavailable", "error": err}

def send_lock_request(cfg, filepath, filename, verdict, threat, file_hash):
    ip = cfg.get("duat_ip", "")
    if not ip or not cfg.get("lock_on_threat"):
        return False, "disabled"
    data, err = _post(
        f"https://{ip}:{cfg['duat_unlock_port']}/lock",
        {"filepath": filepath, "filename": filename,
         "verdict": verdict, "threat_name": threat, "hash": file_hash}
    )
    if data:
        return True, data.get("status", "sent")
    return False, err or "no response"

# ── File event handler ─────────────────────────────────────────
class DownloadsHandler(FileSystemEventHandler):
    def __init__(self, app):
        self.app = app

    def on_created(self, event):
        if not event.is_directory:
            threading.Thread(target=self.app.process_file,
                             args=(event.src_path,), daemon=True).start()

    def on_moved(self, event):
        if not event.is_directory:
            threading.Thread(target=self.app.process_file,
                             args=(event.dest_path,), daemon=True).start()

    def on_deleted(self, event):
        if not event.is_directory:
            self.app.log(f"✗  Removed: {Path(event.src_path).name}", "muted")

# ── Main App ───────────────────────────────────────────────────
class RavenWatchdog:
    def __init__(self, root):
        self.root = root
        self.root.title("Raven OS — Watchdog")
        self.root.geometry("860x640")
        self.root.configure(bg="#08080f")
        self.root.resizable(True, True)
        self.root.minsize(640, 480)

        self.cfg      = load_config()
        self.observer = None
        self.running  = False
        self.paused   = False

        self.file_count    = 0
        self.threat_count  = 0
        self.unknown_count = 0
        self.locked_count  = 0
        self.locked_files  = {}  # filepath -> {name, reason}

        self.flagged_processes = []  # list of anomaly dicts from process_monitor

        self.duat_ok    = False
        self.raven_ok   = False
        self.scarab_ok  = False
        self.scarab_last = 0
        self.scarab_tunnel = 'UNKNOWN'
        self.scarab_duat   = 'UNKNOWN'
        self.scarab_up     = '0.0'
        self.scarab_down   = '0.0'
        self.registry = load_registry()
        self.selected_node = "legiom"

        # OPS tab state
        self.ops_health          = {}   # check_id -> {ok, detail, ts}
        self.ops_health_widgets  = {}   # check_id -> {dot, status, time}
        self.ops_ledger_widgets  = {}   # nid -> {dot, status, time}
        self.ops_last_good       = {}

        self._build_ui()
        self._check_deps()
        self._start_status_loop()
        self._start_slasher_loop()

        # Register global ref for unlock listener
        global _app_ref
        _app_ref = self

        # Start Raven unlock listener
        threading.Thread(target=start_unlock_listener, daemon=True).start()

        # Start heartbeat — sends ALIVE to Raven every 30s
        threading.Thread(target=heartbeat_loop, args=(self.cfg,), daemon=True).start()

        # Start Scarab heartbeat listener
        threading.Thread(target=self._scarab_listener, daemon=True).start()

        # Start process monitor (60-second periodic scan)
        if HAS_PROCMON:
            self._proc_monitor = _pm.ProcessMonitorThread(
                raven_ip=self.cfg.get("raven_ip", "192.168.1.3"),
                raven_port=int(self.cfg.get("raven_port", 6175)),
                interval=60,
                log_fn=self.log,
                after_fn=self.root.after,
                result_callback=self._on_process_flags,
            )
            self._proc_monitor.start()
        else:
            self.log("⚠  process_monitor not available — pip install psutil", "warn")

        if self.cfg.get("scan_on_start"):
            self.root.after(800, self.start_watching)

    # ── Dep check ──────────────────────────────────────────────
    def _check_deps(self):
        if not HAS_WATCHDOG:
            self.log("⚠  'watchdog' not installed — using polling fallback", "warn")
            self.log("   pip install watchdog", "warn")
        if not HAS_REQUESTS:
            self.log("⚠  'requests' not installed", "warn")
            self.log("   pip install requests", "warn")

    # ── UI builder ─────────────────────────────────────────────
    def _build_ui(self):
        tab_bar = tk.Frame(self.root, bg="#0b0b16")
        tab_bar.pack(fill=tk.X)

        self.tab_btns   = {}
        self.tab_frames = {}

        for name in ["MONITOR", "SLASHER", "REGISTRY", "OPS", "SETTINGS"]:
            btn = tk.Button(
                tab_bar, text=name,
                font=("Courier", 10, "bold"),
                bg="#0b0b16", fg="#333355",
                activebackground="#161628",
                relief=tk.FLAT, padx=22, pady=10,
                cursor="hand2",
                command=lambda n=name: self.show_tab(n)
            )
            btn.pack(side=tk.LEFT)
            self.tab_btns[name] = btn
            self.tab_frames[name] = tk.Frame(self.root, bg="#08080f")

        self._build_monitor()
        self._build_slasher()
        self._build_registry()
        self._build_ops()
        self._build_settings()
        # Accent line below tab bar
        self._tab_accent = tk.Frame(self.root, bg="#00ff88", height=1)
        self._tab_accent.pack(fill=tk.X)
        self.show_tab("MONITOR")

    def show_tab(self, name):
        for n, f in self.tab_frames.items():
            f.pack_forget()
            self.tab_btns[n].config(fg="#222244", bg="#0b0b16")
        self.tab_frames[name].pack(fill=tk.BOTH, expand=True)
        self.tab_btns[name].config(fg="#00ff88", bg="#0d0d1c")

    def _build_monitor(self):
        frame = self.tab_frames["MONITOR"]

        # Header
        hdr = tk.Frame(frame, bg="#0d0d1c", pady=10)
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text="👁  RAVEN WATCHDOG",
                 font=("Courier", 15, "bold"),
                 fg="#00ff88", bg="#0d0d1c").pack(side=tk.LEFT, padx=18)
        self.status_label = tk.Label(hdr, text="● IDLE",
                                      font=("Courier", 10),
                                      fg="#333355", bg="#0d0d1c")
        self.status_label.pack(side=tk.RIGHT, padx=18)

        # Connection bar
        conn = tk.Frame(frame, bg="#060612", pady=7)
        conn.pack(fill=tk.X)
        tk.Label(conn, text="CONNECTIONS",
                 font=("Courier", 8), fg="#1a1a33",
                 bg="#060612", padx=12).pack(side=tk.LEFT)

        duat_f = tk.Frame(conn, bg="#060612", padx=10)
        duat_f.pack(side=tk.LEFT)
        tk.Label(duat_f, text="DUAT", font=("Courier", 8),
                 fg="#333355", bg="#060612").pack(side=tk.LEFT, padx=(0,4))
        self.duat_dot = tk.Label(duat_f, text="●", font=("Courier", 10),
                                  fg="#1a1a33", bg="#060612")
        self.duat_dot.pack(side=tk.LEFT)
        self.duat_detail = tk.Label(duat_f, text="—",
                                     font=("Courier", 9),
                                     fg="#333355", bg="#060612")
        self.duat_detail.pack(side=tk.LEFT, padx=(4,0))

        raven_f = tk.Frame(conn, bg="#060612", padx=14)
        raven_f.pack(side=tk.LEFT)
        tk.Label(raven_f, text="RAVEN", font=("Courier", 8),
                 fg="#333355", bg="#060612").pack(side=tk.LEFT, padx=(0,4))
        self.raven_dot = tk.Label(raven_f, text="●", font=("Courier", 10),
                                   fg="#1a1a33", bg="#060612")
        self.raven_dot.pack(side=tk.LEFT)
        self.raven_detail = tk.Label(raven_f, text="—",
                                      font=("Courier", 9),
                                      fg="#333355", bg="#060612")
        self.raven_detail.pack(side=tk.LEFT, padx=(4,0))

        scarab_f = tk.Frame(conn, bg="#060612", padx=14)
        scarab_f.pack(side=tk.LEFT)
        tk.Label(scarab_f, text="SCARAB", font=("Courier", 8),
                 fg="#333355", bg="#060612").pack(side=tk.LEFT, padx=(0,4))
        self.scarab_dot = tk.Label(scarab_f, text="●", font=("Courier", 10),
                                   fg="#1a1a33", bg="#060612")
        self.scarab_dot.pack(side=tk.LEFT)
        self.scarab_detail = tk.Label(scarab_f, text="—",
                                      font=("Courier", 9),
                                      fg="#333355", bg="#060612")
        self.scarab_detail.pack(side=tk.LEFT, padx=(4,0))

        tk.Button(conn, text="⟳", font=("Courier", 11),
                  bg="#060612", fg="#333355",
                  activebackground="#111122",
                  relief=tk.FLAT, padx=8, cursor="hand2",
                  command=self._recheck_connections
                  ).pack(side=tk.RIGHT, padx=10)

        # Stats
        stats = tk.Frame(frame, bg="#0c0c1a", pady=8)
        stats.pack(fill=tk.X)
        self.stat_files   = self._stat(stats, "FILES",   "0")
        self.stat_threats = self._stat(stats, "THREATS", "0")
        self.stat_unknown = self._stat(stats, "UNKNOWN", "0")
        self.stat_locked  = self._stat(stats, "LOCKED",  "0")
        self.stat_watch   = self._stat(stats, "WATCHING",self._short_path())

        # Log
        lw = tk.Frame(frame, bg="#08080f", padx=12, pady=6)
        lw.pack(fill=tk.BOTH, expand=True)
        tk.Label(lw, text="ACTIVITY LOG", font=("Courier", 8),
                 fg="#1a1a33", bg="#08080f").pack(anchor=tk.W)

        self.log_box = scrolledtext.ScrolledText(
            lw, font=("Courier", 10),
            bg="#040410", fg="#00cc66",
            insertbackground="#00ff88",
            relief=tk.FLAT, borderwidth=0,
            wrap=tk.WORD, state=tk.DISABLED
        )
        self.log_box.pack(fill=tk.BOTH, expand=True, pady=(4,0))

        for tag, color in [
            ("warn",    "#ffaa00"), ("threat",  "#ff3344"),
            ("info",    "#00ff88"), ("muted",   "#1e1e3a"),
            ("new",     "#0088ff"), ("unknown", "#ff8800"),
            ("clean",   "#228844"), ("locked",  "#ff3344"),
        ]:
            self.log_box.tag_config(tag, foreground=color)

        # Locked files panel
        lhdr = tk.Frame(frame, bg="#0e0e1a", pady=4)
        lhdr.pack(fill=tk.X)
        tk.Label(lhdr, text="🔒 LOCKED FILES",
                 font=("Courier", 9, "bold"),
                 fg="#ff3344", bg="#0e0e1a", padx=12).pack(side=tk.LEFT)
        tk.Label(lhdr, text=f"  (Raven can unlock via port {WATCHDOG_UNLOCK_PORT})",
                 font=("Courier", 8), fg="#333355", bg="#0e0e1a").pack(side=tk.LEFT)

        self.locked_panel_inner = tk.Frame(frame, bg="#08080f")
        self.locked_panel_inner.pack(fill=tk.X)
        tk.Label(self.locked_panel_inner, text="No locked files.",
                 font=("Courier", 9), fg="#333355", bg="#08080f"
                 ).pack(anchor=tk.W, padx=8, pady=3)

        # Process anomaly panel
        phdr = tk.Frame(frame, bg="#0e0e1a", pady=4)
        phdr.pack(fill=tk.X)
        tk.Label(phdr, text="⚠ PROCESS ANOMALIES",
                 font=("Courier", 9, "bold"),
                 fg="#ffaa00", bg="#0e0e1a", padx=12).pack(side=tk.LEFT)
        tk.Button(phdr, text="⟳ SCAN",
                  font=("Courier", 8), bg="#0e0e1a", fg="#555577",
                  activebackground="#161628", relief=tk.FLAT,
                  padx=6, cursor="hand2",
                  command=lambda: threading.Thread(
                      target=self._run_process_scan, daemon=True).start()
                  ).pack(side=tk.RIGHT, padx=8)

        self.proc_panel_inner = tk.Frame(frame, bg="#08080f")
        self.proc_panel_inner.pack(fill=tk.X)
        tk.Label(self.proc_panel_inner, text="No anomalies detected.",
                 font=("Courier", 9), fg="#333355", bg="#08080f"
                 ).pack(anchor=tk.W, padx=8, pady=3)

        # Controls
        ctrl = tk.Frame(frame, bg="#0d0d1c", pady=10)
        ctrl.pack(fill=tk.X)

        self.btn_start = tk.Button(
            ctrl, text="▶  START",
            font=("Courier", 10, "bold"),
            bg="#0a2a1a", fg="#00ff88",
            relief=tk.FLAT, padx=20, pady=8, cursor="hand2",
            command=self.start_watching
        )
        self.btn_start.pack(side=tk.LEFT, padx=(18,6))

        self.btn_pause = tk.Button(
            ctrl, text="⏸  PAUSE",
            font=("Courier", 10, "bold"),
            bg="#161628", fg="#333355",
            relief=tk.FLAT, padx=20, pady=8,
            state=tk.DISABLED, cursor="hand2",
            command=self.toggle_pause
        )
        self.btn_pause.pack(side=tk.LEFT, padx=6)

        self.btn_scan = tk.Button(
            ctrl, text="⟳  SCAN NOW",
            font=("Courier", 10, "bold"),
            bg="#161628", fg="#0088ff",
            relief=tk.FLAT, padx=20, pady=8, cursor="hand2",
            command=lambda: threading.Thread(
                target=self.scan_existing, daemon=True).start()
        )
        self.btn_scan.pack(side=tk.LEFT, padx=6)

        self.btn_stop = tk.Button(
            ctrl, text="■  STOP",
            font=("Courier", 10, "bold"),
            bg="#161628", fg="#ff3344",
            relief=tk.FLAT, padx=20, pady=8,
            state=tk.DISABLED, cursor="hand2",
            command=self.stop_watching
        )
        self.btn_stop.pack(side=tk.RIGHT, padx=18)

    # ── SLASHER tab ────────────────────────────────────────────────────────────
    def _build_slasher(self):
        frame = self.tab_frames["SLASHER"]
        frame.configure(bg="#08080f")

        BG    = "#08080f"
        BG2   = "#0d0d1c"
        BG3   = "#111122"
        GREEN = "#00ff88"
        AMBER = "#ffaa00"
        RED   = "#ff3344"
        MUTED = "#555588"
        WHITE = "#d2d4f5"
        CYAN  = "#00c8ff"
        PURPLE= "#8844dd"
        GOLD  = "#d4822a"

        # ── Header ───────────────────────────────────────────────
        hdr = tk.Frame(frame, bg=BG2, pady=10)
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text="⚔  GATES OF DARKNESS",
                 font=("Courier", 14, "bold"), bg=BG2, fg=GOLD).pack(side=tk.LEFT, padx=16)
        tk.Button(
            hdr, text="🎮  PLAY", font=("Courier", 9, "bold"), bg="#14082a", fg="#aa66ff",
            activebackground="#1e0c3a", relief=tk.FLAT, padx=10, pady=4, cursor="hand2",
            command=lambda: webbrowser.open("http://192.168.1.5:5000")
        ).pack(side=tk.LEFT, padx=12)
        right_hdr = tk.Frame(hdr, bg=BG2)
        right_hdr.pack(side=tk.RIGHT, padx=14)
        self.slasher_last_lbl = tk.Label(right_hdr, text="",
                 font=("Courier", 8), bg=BG2, fg=MUTED)
        self.slasher_last_lbl.pack(side=tk.LEFT, padx=(0, 8))
        self.slasher_refresh_btn = tk.Button(
            right_hdr, text="⟳", font=("Courier", 10), bg=BG2, fg="#333355",
            activebackground="#161628", relief=tk.FLAT, padx=6, cursor="hand2",
            command=lambda: threading.Thread(
                target=self._refresh_slasher, daemon=True).start()
        )
        self.slasher_refresh_btn.pack(side=tk.LEFT)

        # ── Column headers ────────────────────────────────────────
        col_hdr = tk.Frame(frame, bg=BG3, pady=4)
        col_hdr.pack(fill=tk.X)
        headers = [
            ("#",       3,  MUTED),
            ("PLAYER",  10, MUTED),
            ("LV",      4,  MUTED),
            ("FLOOR",   6,  MUTED),
            ("KILLS",   6,  MUTED),
            ("CLASS",   8,  MUTED),
            ("NAME",    0,  MUTED),
        ]
        for txt, w, fg in headers:
            kw = {"width": w} if w else {}
            tk.Label(col_hdr, text=txt, font=("Courier", 7, "bold"),
                     bg=BG3, fg=fg, anchor="w", **kw).pack(side=tk.LEFT, padx=(8 if txt=="#" else 2, 0))

        tk.Frame(frame, bg="#1a1a30", height=1).pack(fill=tk.X)

        # ── Scrollable player list ────────────────────────────────
        self.slasher_frame = tk.Frame(frame, bg=BG)
        self.slasher_frame.pack(fill=tk.BOTH, expand=True, padx=0, pady=0)
        tk.Label(self.slasher_frame, text="  Connecting...",
                 font=("Courier", 9), bg=BG, fg=MUTED).pack(anchor="w", pady=8, padx=14)

        # ── Footer ────────────────────────────────────────────────
        tk.Frame(frame, bg="#1a1a30", height=1).pack(fill=tk.X, side=tk.BOTTOM)
        self.slasher_count_lbl = tk.Label(frame, text="",
                 font=("Courier", 8), bg=BG3, fg=MUTED, pady=4)
        self.slasher_count_lbl.pack(fill=tk.X, side=tk.BOTTOM)

        # Store colors
        self._dg_colors = {
            "BG": BG, "BG2": BG2, "BG3": BG3,
            "GREEN": GREEN, "AMBER": AMBER, "RED": RED,
            "MUTED": MUTED, "WHITE": WHITE, "CYAN": CYAN, "PURPLE": PURPLE,
            "GOLD": GOLD,
        }


    def _build_registry(self):
        frame = self.tab_frames["REGISTRY"]
        frame.configure(bg="#08080f")

        # Header
        hdr = tk.Frame(frame, bg="#0d0d1c", pady=10)
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text="◈  NETWORK KEY REGISTRY",
                 font=("Courier", 14, "bold"),
                 fg="#00ff88", bg="#0d0d1c").pack(side=tk.LEFT, padx=18)

        # Trusted/Pending counts
        self.reg_trusted_lbl = tk.Label(hdr, text="0  TRUSTED",
                                         font=("Courier", 10, "bold"),
                                         fg="#00ff88", bg="#0d0d1c")
        self.reg_trusted_lbl.pack(side=tk.RIGHT, padx=8)
        self.reg_pending_lbl = tk.Label(hdr, text="0  PENDING",
                                         font=("Courier", 10),
                                         fg="#ffaa00", bg="#0d0d1c")
        self.reg_pending_lbl.pack(side=tk.RIGHT, padx=8)

        # Node selector bar
        node_bar = tk.Frame(frame, bg="#060612", pady=8)
        node_bar.pack(fill=tk.X)
        self.node_btns = {}
        for node in NODES:
            nid = node["id"]
            reg = self.registry.get(nid, {})
            registered = bool(reg.get("pubkey"))
            status_color = "#00ff88" if registered else "#333355"
            status_text  = "REGISTERED" if registered else "UNREGISTERED"

            col = tk.Frame(node_bar, bg="#060612", padx=12)
            col.pack(side=tk.LEFT)

            shape_lbl = tk.Label(col, text=node["shape"],
                                  font=("Courier", 18),
                                  fg=node["color"] if registered else "#333355",
                                  bg="#060612", cursor="hand2")
            shape_lbl.pack()

            name_lbl = tk.Label(col, text=node["name"],
                                 font=("Courier", 9, "bold"),
                                 fg="#aaaacc", bg="#060612", cursor="hand2")
            name_lbl.pack()

            status_lbl = tk.Label(col, text=status_text,
                                   font=("Courier", 7),
                                   fg=status_color, bg="#060612")
            status_lbl.pack()

            self.node_btns[nid] = {
                "frame": col, "shape": shape_lbl,
                "name": name_lbl, "status": status_lbl
            }

            for w in [col, shape_lbl, name_lbl, status_lbl]:
                w.bind("<Button-1>", lambda e, n=nid: self._select_node(n))

        # Detail panel
        detail = tk.Frame(frame, bg="#08080f")
        detail.pack(fill=tk.BOTH, expand=True, padx=16, pady=8)

        # Left — connection config
        left = tk.Frame(detail, bg="#08080f")
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8))

        tk.Label(left, text="CONNECTION CONFIG",
                 font=("Courier", 8), fg="#333355", bg="#08080f"
                 ).pack(anchor=tk.W, pady=(0, 4))

        # Node name + role
        name_row = tk.Frame(left, bg="#08080f")
        name_row.pack(fill=tk.X, pady=(0, 8))
        self.reg_node_icon = tk.Label(name_row, text="■",
                                       font=("Courier", 22),
                                       fg="#ff3344", bg="#08080f")
        self.reg_node_icon.pack(side=tk.LEFT, padx=(0, 8))
        name_col = tk.Frame(name_row, bg="#08080f")
        name_col.pack(side=tk.LEFT)
        self.reg_node_name = tk.Label(name_col, text="LEGIOM",
                                       font=("Courier", 16, "bold"),
                                       fg="#ffffff", bg="#08080f")
        self.reg_node_name.pack(anchor=tk.W)
        self.reg_node_role = tk.Label(name_col, text="Primary Workstation",
                                       font=("Courier", 9),
                                       fg="#555577", bg="#08080f")
        self.reg_node_role.pack(anchor=tk.W)

        # IP field
        tk.Label(left, text="IP / HOSTNAME",
                 font=("Courier", 8), fg="#555577", bg="#08080f"
                 ).pack(anchor=tk.W, pady=(8,2))
        self.reg_ip_entry = tk.Entry(left, font=("Courier", 12),
                                      bg="#0e0e1e", fg="#d0d0f0",
                                      insertbackground="#00ff88",
                                      relief=tk.FLAT, bd=6)
        self.reg_ip_entry.pack(fill=tk.X)

        # Port field
        port_row = tk.Frame(left, bg="#08080f")
        port_row.pack(fill=tk.X, pady=(6, 0))
        tk.Label(port_row, text="PORT",
                 font=("Courier", 8), fg="#555577", bg="#08080f",
                 width=6, anchor=tk.W).pack(side=tk.LEFT)
        self.reg_port_entry = tk.Entry(port_row, font=("Courier", 12),
                                        bg="#0e0e1e", fg="#d0d0f0",
                                        insertbackground="#00ff88",
                                        relief=tk.FLAT, bd=6, width=8)
        self.reg_port_entry.insert(0, "22")
        self.reg_port_entry.pack(side=tk.LEFT)

        # User field
        tk.Label(left, text="SSH USER",
                 font=("Courier", 8), fg="#555577", bg="#08080f"
                 ).pack(anchor=tk.W, pady=(8, 2))
        self.reg_user_entry = tk.Entry(left, font=("Courier", 12),
                                        bg="#0e0e1e", fg="#d0d0f0",
                                        insertbackground="#00ff88",
                                        relief=tk.FLAT, bd=6)
        self.reg_user_entry.pack(fill=tk.X)

        # Password field
        tk.Label(left, text="PASSWORD  (first time only)",
                 font=("Courier", 8), fg="#555577", bg="#08080f"
                 ).pack(anchor=tk.W, pady=(8, 2))
        self.reg_pass_entry = tk.Entry(left, font=("Courier", 12),
                                        bg="#0e0e1e", fg="#d0d0f0",
                                        insertbackground="#00ff88",
                                        relief=tk.FLAT, bd=6, show="●")
        self.reg_pass_entry.pack(fill=tk.X)

        # Register / Revoke buttons
        btn_row = tk.Frame(left, bg="#08080f")
        btn_row.pack(fill=tk.X, pady=(12, 0))

        self.reg_btn = tk.Button(btn_row, text="REGISTER NODE",
                                  font=("Courier", 10, "bold"),
                                  bg="#0a2a1a", fg="#00ff88",
                                  activebackground="#0a3a2a",
                                  relief=tk.FLAT, padx=16, pady=8,
                                  cursor="hand2",
                                  command=self._register_node)
        self.reg_btn.pack(side=tk.LEFT)

        self.revoke_btn = tk.Button(btn_row, text="REVOKE KEY",
                                     font=("Courier", 10, "bold"),
                                     bg="#2a0a0a", fg="#ff3344",
                                     activebackground="#3a0a0a",
                                     relief=tk.FLAT, padx=16, pady=8,
                                     cursor="hand2",
                                     command=self._revoke_node)
        self.revoke_btn.pack(side=tk.LEFT, padx=(8, 0))

        self.rotate_btn = tk.Button(btn_row, text="ROTATE KEYS",
                                     font=("Courier", 10, "bold"),
                                     bg="#1a1a0a", fg="#ffaa00",
                                     activebackground="#2a2a0a",
                                     relief=tk.FLAT, padx=16, pady=8,
                                     cursor="hand2",
                                     command=self._rotate_keys)
        self.rotate_btn.pack(side=tk.LEFT, padx=(8, 0))

        self.rotate_wg_btn = tk.Button(btn_row, text="ROTATE WG KEYS",
                                        font=("Courier", 10, "bold"),
                                        bg="#0a1a2a", fg="#00aaff",
                                        activebackground="#0a2a3a",
                                        relief=tk.FLAT, padx=16, pady=8,
                                        cursor="hand2",
                                        command=self._rotate_wg_keys)
        self.rotate_wg_btn.pack(side=tk.LEFT, padx=(8, 0))

        self.reg_status_lbl = tk.Label(left, text="",
                                        font=("Courier", 9),
                                        fg="#444466", bg="#08080f")
        self.reg_status_lbl.pack(anchor=tk.W, pady=(6, 0))

        # Right — public key + activity log
        right = tk.Frame(detail, bg="#08080f", width=320)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        right.pack_propagate(False)

        tk.Label(right, text="PUBLIC KEY REGISTRY",
                 font=("Courier", 8), fg="#333355", bg="#08080f"
                 ).pack(anchor=tk.W, pady=(0, 4))

        self.reg_key_box = tk.Text(right, font=("Courier", 8),
                                    bg="#0a0a1a", fg="#ff3344",
                                    relief=tk.FLAT, bd=6,
                                    wrap=tk.WORD, height=5,
                                    cursor="hand2",
                                    state=tk.DISABLED)
        self.reg_key_box.pack(fill=tk.X)
        self.reg_key_box.bind("<Button-1>", self._copy_pubkey)

        tk.Label(right, text="click to copy",
                 font=("Courier", 7), fg="#333355", bg="#08080f"
                 ).pack(anchor=tk.W)

        self.reg_last_lbl = tk.Label(right, text="",
                                      font=("Courier", 8),
                                      fg="#333355", bg="#08080f")
        self.reg_last_lbl.pack(anchor=tk.W, pady=(4, 8))

        tk.Label(right, text="ACTIVITY LOG",
                 font=("Courier", 8), fg="#333355", bg="#08080f"
                 ).pack(anchor=tk.W, pady=(0, 4))

        from tkinter import scrolledtext as st
        self.reg_log_box = st.ScrolledText(right, font=("Courier", 9),
                                            bg="#050510", fg="#00cc66",
                                            relief=tk.FLAT, bd=0,
                                            wrap=tk.WORD, height=6,
                                            state=tk.DISABLED)
        self.reg_log_box.pack(fill=tk.BOTH, expand=True)
        self.reg_log_box.tag_config("ok",   foreground="#00ff88")
        self.reg_log_box.tag_config("err",  foreground="#ff3344")
        self.reg_log_box.tag_config("info", foreground="#00cc66")

        # Select legiom by default
        self._select_node("legiom")

    def _select_node(self, nid):
        self.selected_node = nid
        node = next((n for n in NODES if n["id"] == nid), None)
        if not node:
            return

        # Update node bar highlight
        for n, widgets in self.node_btns.items():
            is_sel = n == nid
            widgets["frame"].config(
                bg="#111122" if is_sel else "#060612",
                highlightbackground="#00ff88" if is_sel else "#060612",
                highlightthickness=1 if is_sel else 0
            )

        reg = self.registry.get(nid, {})
        registered = bool(reg.get("pubkey"))

        # Update detail panel
        self.reg_node_icon.config(text=node["shape"],
                                   fg=node["color"] if registered else "#333355")
        self.reg_node_name.config(text=node["name"])
        self.reg_node_role.config(text=node["role"])

        # Fill fields from registry
        self.reg_ip_entry.delete(0, tk.END)
        self.reg_ip_entry.insert(0, reg.get("ip", ""))
        self.reg_port_entry.delete(0, tk.END)
        self.reg_port_entry.insert(0, reg.get("port", "22"))
        self.reg_user_entry.delete(0, tk.END)
        self.reg_user_entry.insert(0, reg.get("user", ""))
        self.reg_pass_entry.delete(0, tk.END)

        # Public key display
        self.reg_key_box.config(state=tk.NORMAL)
        self.reg_key_box.delete("1.0", tk.END)
        if registered:
            self.reg_key_box.insert(tk.END, reg.get("pubkey", ""))
            ts = reg.get("registered_at", "")
            self.reg_last_lbl.config(text=f"Last registered: {ts}")
        else:
            self.reg_key_box.insert(tk.END, "No key registered.")
            self.reg_last_lbl.config(text="")
        self.reg_key_box.config(state=tk.DISABLED)

        # Button states
        self.revoke_btn.config(
            state=tk.NORMAL if registered else tk.DISABLED,
            fg="#ff3344" if registered else "#333355"
        )
        self.rotate_btn.config(
            state=tk.NORMAL if registered else tk.DISABLED,
            fg="#ffaa00" if registered else "#333355"
        )

        self.reg_status_lbl.config(
            text="● REGISTERED" if registered else "○ UNREGISTERED",
            fg="#00ff88" if registered else "#444466"
        )

        self._update_registry_counts()

    def _reg_log(self, msg, tag="info"):
        ts = datetime.now().strftime("%H:%M:%S")
        self.reg_log_box.config(state=tk.NORMAL)
        self.reg_log_box.insert(tk.END, f"{ts}  {msg}\n", tag)
        self.reg_log_box.see(tk.END)
        self.reg_log_box.config(state=tk.DISABLED)

    def _copy_pubkey(self, event=None):
        reg = self.registry.get(self.selected_node, {})
        key = reg.get("pubkey", "")
        if key:
            self.root.clipboard_clear()
            self.root.clipboard_append(key)
            self._reg_log("Public key copied to clipboard", "ok")

    def _update_registry_counts(self):
        trusted = sum(1 for n in NODES if self.registry.get(n["id"], {}).get("pubkey"))
        pending = len(NODES) - trusted
        self.reg_trusted_lbl.config(text=f"{trusted}  TRUSTED")
        self.reg_pending_lbl.config(text=f"{pending}  PENDING",
                                     fg="#ffaa00" if pending > 0 else "#00ff88")

        # Refresh node bar status labels
        for node in NODES:
            nid = node["id"]
            reg = self.registry.get(nid, {})
            registered = bool(reg.get("pubkey"))
            if nid in self.node_btns:
                self.node_btns[nid]["status"].config(
                    text="REGISTERED" if registered else "UNREGISTERED",
                    fg="#00ff88" if registered else "#333355"
                )
                self.node_btns[nid]["shape"].config(
                    fg=node["color"] if registered else "#333355"
                )

    def _register_node(self):
        nid      = self.selected_node
        ip       = self.reg_ip_entry.get().strip()
        port     = self.reg_port_entry.get().strip() or "22"
        user     = self.reg_user_entry.get().strip()
        password = self.reg_pass_entry.get().strip()

        if not ip or not user:
            self.reg_status_lbl.config(text="⚠  Enter IP and SSH user", fg="#ffaa00")
            return

        if not password:
            # Try keyless if already registered
            reg = self.registry.get(nid, {})
            if not reg.get("pubkey"):
                self.reg_status_lbl.config(text="⚠  Enter password for first registration", fg="#ffaa00")
                return

        self._reg_log(f"Initiating connection to {user}@{ip}:{port}...")
        self.reg_btn.config(state=tk.DISABLED, text="CONNECTING...")
        self.root.update()

        threading.Thread(
            target=self._do_register,
            args=(nid, ip, port, user, password),
            daemon=True
        ).start()

    def _do_register(self, nid, ip, port, user, password=""):
        try:
            import paramiko
            HAS_PARAMIKO = True
        except ImportError:
            HAS_PARAMIKO = False

        local_key_path    = Path.home() / ".ssh" / "id_ed25519"
        local_key_pub     = Path.home() / ".ssh" / "id_ed25519.pub"
        local_auth_keys   = Path.home() / ".ssh" / "authorized_keys"

        try:
            # ── Connect ────────────────────────────────────────
            self.root.after(0, lambda: self._reg_log("SSH handshake complete", "ok"))

            if HAS_PARAMIKO and password:
                client = paramiko.SSHClient()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                client.connect(ip, port=int(port), username=user,
                               password=password, timeout=15,
                               allow_agent=False, look_for_keys=False)

                def run(cmd):
                    _, stdout, stderr = client.exec_command(cmd, timeout=20)
                    out = stdout.read().decode("utf-8", errors="replace").strip()
                    stdout.channel.recv_exit_status()
                    return out

            else:
                # Fall back to subprocess SSH with existing keys
                import subprocess as sp
                def run(cmd):
                    r = sp.run(
                        ["ssh", "-o", "StrictHostKeyChecking=no",
                         "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                         "-p", port, f"{user}@{ip}", cmd],
                        capture_output=True, text=True, timeout=25
                    )
                    return r.stdout.strip()
                client = None

            # ── Ensure local key exists ────────────────────────
            if not local_key_path.exists():
                import subprocess as sp
                sp.run(["ssh-keygen", "-t", "ed25519", "-f", str(local_key_path),
                        "-N", "", "-C", f"{os.getenv('USERNAME', 'user')}@legiom"], capture_output=True)
                self.root.after(0, lambda: self._reg_log("Generated local key", "ok"))

            local_pubkey = local_key_pub.read_text().strip()

            # ── Get/generate remote key ────────────────────────
            self.root.after(0, lambda: self._reg_log("Checking for existing keypair at ~/.ssh/id_ed25519..."))
            remote_out = run("cat ~/.ssh/id_ed25519.pub 2>/dev/null || echo NO_KEY")

            if "NO_KEY" in remote_out or not remote_out.startswith("ssh-"):
                self.root.after(0, lambda: self._reg_log("No existing keypair — generating...", "info"))
                run('mkdir -p ~/.ssh && chmod 700 ~/.ssh')
                run(f'ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N "" -C "{user}@{nid}"')
                remote_out = run('cat ~/.ssh/id_ed25519.pub')
                # Find the actual key line
                pubkey = ""
                for line in remote_out.strip().split("\n"):
                    if line.startswith("ssh-"):
                        pubkey = line.strip()
                        break
            else:
                # Find the actual key line from existing output
                pubkey = ""
                for line in remote_out.strip().split("\n"):
                    if line.startswith("ssh-"):
                        pubkey = line.strip()
                        break
                self.root.after(0, lambda: self._reg_log("Existing keypair found - retrieving", "ok"))

            if not pubkey.startswith("ssh-"):
                self.root.after(0, lambda: self._reg_log("Failed to get remote public key", "err"))
                self.root.after(0, lambda: self.reg_btn.config(state=tk.NORMAL, text="REGISTER NODE"))
                if client: client.close()
                return

            # ── Exchange keys ──────────────────────────────────
            # Add local pubkey to remote authorized_keys
            check = run(f"grep -qF '{local_pubkey}' ~/.ssh/authorized_keys 2>/dev/null && echo FOUND")
            if "FOUND" not in check:
                run(f"mkdir -p ~/.ssh && chmod 700 ~/.ssh && "
                    f"echo '{local_pubkey}' >> ~/.ssh/authorized_keys && "
                    f"chmod 600 ~/.ssh/authorized_keys")
                self.root.after(0, lambda: self._reg_log("Key added to remote authorized_keys", "ok"))
            else:
                self.root.after(0, lambda: self._reg_log("Key already on remote", "ok"))

            # Add remote pubkey to local authorized_keys
            local_auth_keys.parent.mkdir(exist_ok=True)
            existing = local_auth_keys.read_text() if local_auth_keys.exists() else ""
            if pubkey.split()[1] not in existing:
                with open(local_auth_keys, "a") as f:
                    f.write(f"{pubkey}\n")
                self.root.after(0, lambda: self._reg_log("Remote key added to local authorized_keys", "ok"))
            else:
                self.root.after(0, lambda: self._reg_log("Remote key already in local authorized_keys", "ok"))

            if client: client.close()

            # ── Save registry ──────────────────────────────────
            self.registry[nid] = {
                "ip": ip, "port": port, "user": user,
                "pubkey": pubkey,
                "registered_at": datetime.now().strftime("%m/%d/%Y, %I:%M:%S %p")
            }
            save_registry(self.registry)

            self.root.after(0, lambda pk=pubkey: [
                self._reg_log(f"Key registered: {pk[:32]}...", "ok"),
                self._select_node(nid),
                self.reg_btn.config(state=tk.NORMAL, text="REGISTER NODE"),
                self.reg_status_lbl.config(text="● REGISTERED", fg="#00ff88"),
                self._update_registry_counts()
            ])

        except Exception as e:
            self.root.after(0, lambda err=str(e): [
                self._reg_log(f"Error: {err[:60]}", "err"),
                self.reg_btn.config(state=tk.NORMAL, text="REGISTER NODE")
            ])

    def _revoke_node(self):
        nid = self.selected_node
        reg = self.registry.get(nid, {})
        if not reg.get("pubkey"):
            return

        self._reg_log(f"Revoking key for {nid.upper()}...", "err")

        # Remove from registry
        self.registry[nid] = {
            "ip": reg.get("ip", ""),
            "port": reg.get("port", "22"),
            "user": reg.get("user", ""),
            "pubkey": "",
            "registered_at": ""
        }
        save_registry(self.registry)

        # Remove from local authorized_keys if it was there
        auth_keys = Path.home() / ".ssh" / "authorized_keys"
        pubkey = reg.get("pubkey", "")
        if auth_keys.exists() and pubkey:
            try:
                lines = auth_keys.read_text().splitlines()
                lines = [l for l in lines if pubkey.split()[1] not in l]
                auth_keys.write_text("\n".join(lines) + "\n")
                self._reg_log("Key removed from local authorized_keys", "ok")
            except Exception as e:
                self._reg_log(f"Could not update authorized_keys: {e}", "err")

        self._select_node(nid)
        self._update_registry_counts()
        self._reg_log(f"{nid.upper()} marked unregistered", "ok")

    def _rotate_keys(self):
        nid  = self.selected_node
        reg  = self.registry.get(nid, {})
        if not reg.get("pubkey"):
            self.reg_status_lbl.config(text="⚠  Node not registered", fg="#ffaa00")
            return

        ip   = self.reg_ip_entry.get().strip() or reg.get("ip", "")
        port = self.reg_port_entry.get().strip() or reg.get("port", "22")
        user = self.reg_user_entry.get().strip() or reg.get("user", "")

        if not ip or not user:
            self.reg_status_lbl.config(text="⚠  Missing IP or user", fg="#ffaa00")
            return

        self._reg_log(f"Starting key rotation for {nid.upper()}...", "info")
        self.rotate_btn.config(state=tk.DISABLED, text="ROTATING...")
        self.root.update()

        threading.Thread(
            target=self._do_rotate,
            args=(nid, ip, port, user, reg.get("pubkey", "")),
            daemon=True
        ).start()

    def _do_rotate(self, nid, ip, port, user, old_pubkey):
        local_key_path  = Path.home() / ".ssh" / "id_ed25519"
        local_key_pub   = Path.home() / ".ssh" / "id_ed25519.pub"
        local_auth_keys = Path.home() / ".ssh" / "authorized_keys"

        try:
            import subprocess as sp

            def run_ssh(cmd):
                r = sp.run(
                    ["ssh", "-i", str(local_key_path),
                     "-o", "StrictHostKeyChecking=no",
                     "-o", "BatchMode=yes",
                     "-o", "ConnectTimeout=10",
                     "-p", port, f"{user}@{ip}", cmd],
                    capture_output=True, text=True, timeout=30
                )
                return r.stdout.strip()

            local_pubkey = local_key_pub.read_text().strip() if local_key_pub.exists() else ""

            # ── Step 1: Generate new key on remote ────────────
            self.root.after(0, lambda: self._reg_log("Generating new keypair on remote...", "info"))
            run_ssh('mv ~/.ssh/id_ed25519 ~/.ssh/id_ed25519.old 2>/dev/null || true')
            run_ssh('mv ~/.ssh/id_ed25519.pub ~/.ssh/id_ed25519.pub.old 2>/dev/null || true')
            run_ssh(f'ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N "" -C "{user}@{nid}-rotated"')
            new_pubkey_raw = run_ssh('cat ~/.ssh/id_ed25519.pub')

            new_pubkey = ""
            for line in new_pubkey_raw.split("\n"):
                if line.startswith("ssh-"):
                    new_pubkey = line.strip()
                    break

            if not new_pubkey.startswith("ssh-"):
                self.root.after(0, lambda: self._reg_log("Failed to generate new key — rolling back", "err"))
                run_ssh('mv ~/.ssh/id_ed25519.old ~/.ssh/id_ed25519 2>/dev/null || true')
                run_ssh('mv ~/.ssh/id_ed25519.pub.old ~/.ssh/id_ed25519.pub 2>/dev/null || true')
                self.root.after(0, lambda: self.rotate_btn.config(state=tk.NORMAL, text="ROTATE KEYS"))
                return

            self.root.after(0, lambda pk=new_pubkey: self._reg_log(f"New key generated: {pk[:32]}...", "ok"))

            # ── Step 2: Add new remote pubkey to local authorized_keys ──
            self.root.after(0, lambda: self._reg_log("Adding new key to local authorized_keys...", "info"))
            existing = local_auth_keys.read_text() if local_auth_keys.exists() else ""
            if new_pubkey.split()[1] not in existing:
                with open(local_auth_keys, "a") as f:
                    f.write(f"{new_pubkey}\n")

            # ── Step 3: Add local pubkey to remote authorized_keys ──
            if local_pubkey:
                check = run_ssh(f"grep -qF '{local_pubkey}' ~/.ssh/authorized_keys 2>/dev/null && echo FOUND")
                if "FOUND" not in check:
                    run_ssh(f"echo '{local_pubkey}' >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys")

            # ── Step 4: Verify new key works ──────────────────
            self.root.after(0, lambda: self._reg_log("Verifying new key connection...", "info"))
            verify = sp.run(
                ["ssh", "-i", str(local_key_path),
                 "-o", "StrictHostKeyChecking=no",
                 "-o", "BatchMode=yes",
                 "-o", "ConnectTimeout=10",
                 "-p", port, f"{user}@{ip}", "echo VERIFIED"],
                capture_output=True, text=True, timeout=20
            )

            if "VERIFIED" not in verify.stdout:
                self.root.after(0, lambda: self._reg_log("Verification failed — rolling back to old key", "err"))
                run_ssh('mv ~/.ssh/id_ed25519.old ~/.ssh/id_ed25519 2>/dev/null || true')
                run_ssh('mv ~/.ssh/id_ed25519.pub.old ~/.ssh/id_ed25519.pub 2>/dev/null || true')
                # Remove new key from local authorized_keys
                if local_auth_keys.exists() and new_pubkey:
                    lines = local_auth_keys.read_text().splitlines()
                    lines = [l for l in lines if new_pubkey.split()[1] not in l]
                    local_auth_keys.write_text("\n".join(lines) + "\n")
                self.root.after(0, lambda: self.rotate_btn.config(state=tk.NORMAL, text="ROTATE KEYS"))
                return

            self.root.after(0, lambda: self._reg_log("New key verified ✓", "ok"))

            # ── Step 5: Remove OLD key from remote authorized_keys ──
            self.root.after(0, lambda: self._reg_log("Removing old key from remote...", "info"))
            if old_pubkey:
                try:
                    old_key_part = old_pubkey.split()[1]
                    run_ssh(f"sed -i '/{old_key_part}/d' ~/.ssh/authorized_keys")
                except Exception:
                    pass

            # ── Step 6: Remove OLD key from local authorized_keys ──
            if old_pubkey and local_auth_keys.exists():
                try:
                    lines = local_auth_keys.read_text().splitlines()
                    lines = [l for l in lines if old_pubkey.split()[1] not in l]
                    local_auth_keys.write_text("\n".join(lines) + "\n")
                    self.root.after(0, lambda: self._reg_log("Old key removed from local authorized_keys", "ok"))
                except Exception:
                    pass

            # ── Step 7: Clean up old key files on remote ──────
            run_ssh('rm -f ~/.ssh/id_ed25519.old ~/.ssh/id_ed25519.pub.old')

            # ── Step 8: Save new key to registry ──────────────
            self.registry[nid] = {
                "ip": ip, "port": port, "user": user,
                "pubkey": new_pubkey,
                "registered_at": datetime.now().strftime("%m/%d/%Y, %I:%M:%S %p")
            }
            save_registry(self.registry)

            self.root.after(0, lambda pk=new_pubkey: [
                self._reg_log(f"Key rotation complete ✓ — {pk[:24]}...", "ok"),
                self._select_node(nid),
                self.rotate_btn.config(state=tk.NORMAL, text="ROTATE KEYS"),
                self.reg_status_lbl.config(text="● ROTATED & VERIFIED", fg="#00ff88")
            ])

        except Exception as e:
            self.root.after(0, lambda err=str(e): [
                self._reg_log(f"Rotation error: {err[:60]}", "err"),
                self.rotate_btn.config(state=tk.NORMAL, text="ROTATE KEYS")
            ])

    # ── WireGuard Key Rotation ──────────────────────────────────
    def _rotate_wg_keys(self):
        from tkinter import messagebox
        if not messagebox.askyesno(
            "ROTATE WG KEYS",
            "Rotate WireGuard keys on VPS, Duat, and Scarab.\n\n"
            "Old keys remain active until new ones are verified.\n\n"
            "All three nodes must be reachable via SSH.\n\nContinue?"
        ):
            return
        self._reg_log("=== WireGuard Key Rotation Started ===", "info")
        self.rotate_wg_btn.config(state=tk.DISABLED, text="ROTATING WG...")
        self.root.update()
        threading.Thread(target=self._do_rotate_wg, daemon=True).start()

    def _do_rotate_wg(self):
        import subprocess as sp
        local_key = str(Path.home() / ".ssh" / "id_ed25519")

        def ssh(ip, port, user, cmd, timeout=30):
            r = sp.run(
                ["ssh", "-i", local_key,
                 "-o", "StrictHostKeyChecking=no",
                 "-o", "BatchMode=yes",
                 "-o", "ConnectTimeout=10",
                 "-p", str(port), f"{user}@{ip}", cmd],
                capture_output=True, text=True, timeout=timeout
            )
            return r.stdout.strip(), r.returncode

        def log(msg, tag="info"):
            self.root.after(0, lambda m=msg, t=tag: self._reg_log(m, t))

        vps_reg    = self.registry.get("vps", {})
        duat_reg   = self.registry.get("duat", {})
        scarab_reg = self.registry.get("scarab", {})

        nodes = {
            "vps": {
                "ip":   vps_reg.get("ip", ""),
                "port": vps_reg.get("port", "22"),
                "user": vps_reg.get("user", "root"),
            },
            "duat": {
                "ip":   duat_reg.get("ip") or self.cfg.get("duat_ip", "192.168.1.5"),
                "port": duat_reg.get("port", "22"),
                "user": duat_reg.get("user", "duat"),
            },
            "scarab": {
                "ip":   scarab_reg.get("ip", "192.168.1.2"),
                "port": scarab_reg.get("port", "22"),
                "user": scarab_reg.get("user", "scarab"),
            },
        }

        try:
            new_pubkeys = {}

            # Phase 1: Generate new keypairs and update PrivateKey on each node
            for name, n in nodes.items():
                log(f"[{name.upper()}] Generating new WireGuard keypair...", "info")
                script = (
                    "OLD_PRIV=$(sudo grep -oP 'PrivateKey = \\K.*' /etc/wireguard/wg0.conf | head -1); "
                    "NEW_PRIV=$(wg genkey); "
                    "NEW_PUB=$(echo \"$NEW_PRIV\" | wg pubkey); "
                    "sudo cp /etc/wireguard/wg0.conf /etc/wireguard/wg0.conf.bak; "
                    "sudo sed -i \"s|PrivateKey = $OLD_PRIV|PrivateKey = $NEW_PRIV|\" /etc/wireguard/wg0.conf; "
                    "echo \"PUB:$NEW_PUB\""
                )
                out, rc = ssh(n["ip"], n["port"], n["user"], script)
                new_pub = ""
                for line in out.split("\n"):
                    if line.startswith("PUB:"):
                        new_pub = line[4:].strip()
                        break

                if not new_pub or len(new_pub) < 40:
                    log(f"[{name.upper()}] Failed to generate new key — rolling back all nodes!", "err")
                    for rn in nodes.values():
                        ssh(rn["ip"], rn["port"], rn["user"],
                            "sudo cp /etc/wireguard/wg0.conf.bak /etc/wireguard/wg0.conf 2>/dev/null || true")
                    self.root.after(0, lambda: self.rotate_wg_btn.config(state=tk.NORMAL, text="ROTATE WG KEYS"))
                    return

                new_pubkeys[name] = new_pub
                log(f"[{name.upper()}] New WG pubkey: {new_pub[:20]}...", "ok")

            # Phase 2: Read current configs and update peer public keys cross-node
            log("Updating peer public keys across all nodes...", "info")

            # Read VPS config to find old Duat and Scarab peer keys
            vps_conf, _ = ssh(nodes["vps"]["ip"], nodes["vps"]["port"], nodes["vps"]["user"],
                              "sudo cat /etc/wireguard/wg0.conf")

            # Update Duat peer on VPS
            old_duat_pub = self._parse_peer_pubkey(vps_conf, "172.16.0.1")
            if old_duat_pub and "duat" in new_pubkeys:
                new_duat_pub = new_pubkeys["duat"]
                ssh(nodes["vps"]["ip"], nodes["vps"]["port"], nodes["vps"]["user"],
                    f"sudo sed -i 's|PublicKey = {old_duat_pub}|PublicKey = {new_duat_pub}|' /etc/wireguard/wg0.conf")
                log("[VPS] Updated Duat peer key", "ok")
            else:
                log("[VPS] Could not find Duat peer key to update", "err")

            # Update Scarab peer on VPS
            old_scarab_pub = self._parse_peer_pubkey(vps_conf, "172.16.0.2")
            if old_scarab_pub and "scarab" in new_pubkeys:
                new_scarab_pub = new_pubkeys["scarab"]
                ssh(nodes["vps"]["ip"], nodes["vps"]["port"], nodes["vps"]["user"],
                    f"sudo sed -i 's|PublicKey = {old_scarab_pub}|PublicKey = {new_scarab_pub}|' /etc/wireguard/wg0.conf")
                log("[VPS] Updated Scarab peer key", "ok")
            else:
                log("[VPS] Could not find Scarab peer key to update", "err")

            # Update VPS peer on Duat
            duat_conf, _ = ssh(nodes["duat"]["ip"], nodes["duat"]["port"], nodes["duat"]["user"],
                               "sudo cat /etc/wireguard/wg0.conf")
            old_vps_pub_duat = self._parse_peer_pubkey_first(duat_conf)
            if old_vps_pub_duat and "vps" in new_pubkeys:
                new_vps_pub = new_pubkeys["vps"]
                ssh(nodes["duat"]["ip"], nodes["duat"]["port"], nodes["duat"]["user"],
                    f"sudo sed -i 's|PublicKey = {old_vps_pub_duat}|PublicKey = {new_vps_pub}|' /etc/wireguard/wg0.conf")
                log("[DUAT] Updated VPS peer key", "ok")
            else:
                log("[DUAT] Could not find VPS peer key to update", "err")

            # Update VPS peer on Scarab
            scarab_conf, _ = ssh(nodes["scarab"]["ip"], nodes["scarab"]["port"], nodes["scarab"]["user"],
                                 "sudo cat /etc/wireguard/wg0.conf")
            old_vps_pub_scarab = self._parse_peer_pubkey_first(scarab_conf)
            if old_vps_pub_scarab and "vps" in new_pubkeys:
                new_vps_pub = new_pubkeys["vps"]
                ssh(nodes["scarab"]["ip"], nodes["scarab"]["port"], nodes["scarab"]["user"],
                    f"sudo sed -i 's|PublicKey = {old_vps_pub_scarab}|PublicKey = {new_vps_pub}|' /etc/wireguard/wg0.conf")
                log("[SCARAB] Updated VPS peer key", "ok")
            else:
                log("[SCARAB] Could not find VPS peer key to update", "err")

            # Phase 3: Restart WireGuard on all nodes
            log("Restarting WireGuard on all nodes...", "info")
            for name, n in nodes.items():
                _, rc = ssh(n["ip"], n["port"], n["user"],
                            "sudo wg-quick down wg0 2>/dev/null; sleep 1; sudo wg-quick up wg0",
                            timeout=45)
                log(f"[{name.upper()}] WireGuard restarted {'✓' if rc == 0 else '— check manually'}", "ok" if rc == 0 else "err")

            # Phase 4: Verify tunnel
            log("Verifying tunnel (waiting 5s for handshake)...", "info")
            time.sleep(5)
            out, _ = ssh(nodes["vps"]["ip"], nodes["vps"]["port"], nodes["vps"]["user"],
                         "ping -c 2 -W 3 172.16.0.1 > /dev/null 2>&1 && echo TUNNEL_OK || echo TUNNEL_FAIL")
            if "TUNNEL_OK" in out:
                log("VPS → Duat tunnel: VERIFIED ✓", "ok")
            else:
                log("VPS → Duat tunnel: not verified — check configs manually", "err")

            # Phase 5: Save new WG pubkeys to registry, delete exposed key file
            for name, pub in new_pubkeys.items():
                entry = self.registry.setdefault(name, {})
                entry["wg_pubkey"]    = pub
                entry["wg_rotated_at"] = datetime.now().strftime("%m/%d/%Y, %I:%M:%S %p")
            save_registry(self.registry)

            exposed = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop" / "duatwireguard.txt"
            if exposed.exists():
                exposed.unlink()
                log("Deleted exposed duatwireguard.txt from Desktop ✓", "ok")

            self.root.after(0, lambda: [
                self._reg_log("=== WireGuard rotation complete ===", "ok"),
                self.rotate_wg_btn.config(state=tk.NORMAL, text="ROTATE WG KEYS"),
                self.reg_status_lbl.config(text="● WG KEYS ROTATED", fg="#00ff88"),
            ])

        except Exception as e:
            self.root.after(0, lambda err=str(e): [
                self._reg_log(f"WG rotation error: {err[:80]}", "err"),
                self.rotate_wg_btn.config(state=tk.NORMAL, text="ROTATE WG KEYS"),
            ])

    def _parse_peer_pubkey(self, conf_text, allowed_ip_hint):
        """Find the PublicKey for a peer whose AllowedIPs contains allowed_ip_hint."""
        current_pub = None
        for line in conf_text.split("\n"):
            line = line.strip()
            if line.startswith("PublicKey"):
                current_pub = line.split("=", 1)[1].strip()
            elif line.startswith("AllowedIPs") and allowed_ip_hint in line:
                return current_pub
        return None

    def _parse_peer_pubkey_first(self, conf_text):
        """Return the PublicKey of the first [Peer] block (single-peer configs)."""
        in_peer = False
        for line in conf_text.split("\n"):
            line = line.strip()
            if line == "[Peer]":
                in_peer = True
            elif in_peer and line.startswith("PublicKey"):
                return line.split("=", 1)[1].strip()
        return None

    # ── OPS tab ────────────────────────────────────────────────────────────────
    def _build_ops(self):
        frame = self.tab_frames["OPS"]
        frame.configure(bg="#08080f")

        BG    = "#08080f"
        BG2   = "#0d0d1c"
        BG3   = "#111122"
        GREEN = "#00ff88"
        AMBER = "#ffaa00"
        RED   = "#ff3344"
        MUTED = "#333355"
        TEXT  = "#d0d0f0"

        # ── Header ───────────────────────────────────────────────
        hdr = tk.Frame(frame, bg=BG2, pady=10)
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text="⬡  OPS CONSOLE",
                 font=("Courier", 14, "bold"),
                 fg=GREEN, bg=BG2).pack(side=tk.LEFT, padx=18)

        self.ops_scan_lbl = tk.Label(hdr, text="",
                 font=("Courier", 8), bg=BG2, fg=MUTED)
        self.ops_scan_lbl.pack(side=tk.RIGHT, padx=8)

        self.ops_scan_btn = tk.Button(
            hdr, text="▶  QUICK SCAN",
            font=("Courier", 9, "bold"),
            bg="#0a2a1a", fg=GREEN,
            activebackground="#0a3a2a",
            relief=tk.FLAT, padx=12, pady=4, cursor="hand2",
            command=self._ops_quick_scan
        )
        self.ops_scan_btn.pack(side=tk.RIGHT, padx=(0, 4))

        # ── Scrollable content area ───────────────────────────────
        content = tk.Frame(frame, bg=BG)
        content.pack(fill=tk.BOTH, expand=True, padx=16, pady=8)

        # ── Section Health Grid ───────────────────────────────────
        tk.Label(content, text="SUBSYSTEM HEALTH",
                 font=("Courier", 8), fg="#222244", bg=BG,
                 pady=4).pack(anchor=tk.W)

        grid_outer = tk.Frame(content, bg=BG3)
        grid_outer.pack(fill=tk.X)

        # Column header row
        col_hdr = tk.Frame(grid_outer, bg="#0a0a18", pady=3)
        col_hdr.pack(fill=tk.X)
        for txt, w, anchor in [
            ("SUBSYSTEM",   24, "w"),
            ("STATUS",       9, "w"),
            ("LAST CHECK / DETAIL", 0, "w"),
        ]:
            kw = {"width": w} if w else {}
            tk.Label(col_hdr, text=txt,
                     font=("Courier", 7, "bold"),
                     fg=MUTED, bg="#0a0a18", anchor=anchor,
                     **kw).pack(side=tk.LEFT, padx=(10 if txt == "SUBSYSTEM" else 4, 0))

        tk.Frame(grid_outer, bg="#1a1a30", height=1).pack(fill=tk.X)

        HEALTH_CHECKS = [
            ("wireguard",   "WireGuard Tunnel"),
            ("ddns",        "DDNS"),
            ("heartbeat",   "Heartbeat System"),
            ("ssh_auth",    "SSH Key Auth"),
            ("pihole",      "Pi-hole DNS"),
            ("watchdog",    "Watchdog GUI"),
            ("game_server", "Duat Game Server"),
            ("remote_ctrl", "Remote Control Chain"),
        ]

        for i, (check_id, check_name) in enumerate(HEALTH_CHECKS):
            row_bg = BG if i % 2 == 0 else "#09090f"
            row = tk.Frame(grid_outer, bg=row_bg, pady=4)
            row.pack(fill=tk.X)

            tk.Label(row, text=check_name,
                     font=("Courier", 9), fg=TEXT, bg=row_bg,
                     width=24, anchor="w").pack(side=tk.LEFT, padx=(10, 0))

            dot = tk.Label(row, text="●", font=("Courier", 10),
                           fg=AMBER, bg=row_bg)
            dot.pack(side=tk.LEFT, padx=(4, 2))

            status_lbl = tk.Label(row, text="UNKNOWN",
                                   font=("Courier", 8), fg=AMBER, bg=row_bg,
                                   width=8, anchor="w")
            status_lbl.pack(side=tk.LEFT, padx=(0, 4))

            time_lbl = tk.Label(row, text="—",
                                 font=("Courier", 8), fg="#333355", bg=row_bg,
                                 anchor="w")
            time_lbl.pack(side=tk.LEFT, padx=(4, 0), fill=tk.X, expand=True)

            tk.Button(
                row, text="CHECK",
                font=("Courier", 7),
                bg="#111122", fg="#555577",
                activebackground="#1a1a33",
                relief=tk.FLAT, padx=6, pady=1, cursor="hand2",
                command=lambda cid=check_id: threading.Thread(
                    target=self._ops_run_check, args=(cid,), daemon=True).start()
            ).pack(side=tk.RIGHT, padx=(0, 8))

            self.ops_health_widgets[check_id] = {
                "dot": dot, "status": status_lbl, "time": time_lbl
            }

        tk.Frame(content, bg="#1a1a30", height=1).pack(fill=tk.X, pady=(8, 0))

        # ── Ledger Sync ───────────────────────────────────────────
        ledger_hdr = tk.Frame(content, bg=BG2, pady=6)
        ledger_hdr.pack(fill=tk.X, pady=(8, 0))
        tk.Label(ledger_hdr, text="LEDGER SYNC  —  RAVEN_OS_MASTER.md",
                 font=("Courier", 8), fg="#222244", bg=BG2,
                 padx=4).pack(side=tk.LEFT)

        self.ops_push_btn = tk.Button(
            ledger_hdr, text="PUSH TO ALL",
            font=("Courier", 8, "bold"),
            bg="#0a1a2a", fg="#00aaff",
            activebackground="#0a2a3a",
            relief=tk.FLAT, padx=10, pady=3, cursor="hand2",
            command=lambda: threading.Thread(
                target=self._ops_push_master, daemon=True).start()
        )
        self.ops_push_btn.pack(side=tk.RIGHT, padx=8)

        ledger_frame = tk.Frame(content, bg=BG3)
        ledger_frame.pack(fill=tk.X)

        for i, nid in enumerate(["duat", "raven", "scarab"]):
            row_bg = BG if i % 2 == 0 else "#09090f"
            row = tk.Frame(ledger_frame, bg=row_bg, pady=4)
            row.pack(fill=tk.X)

            tk.Label(row, text=nid.upper(),
                     font=("Courier", 9, "bold"), fg=TEXT, bg=row_bg,
                     width=10, anchor="w").pack(side=tk.LEFT, padx=(10, 0))

            dot = tk.Label(row, text="●", font=("Courier", 10),
                           fg=AMBER, bg=row_bg)
            dot.pack(side=tk.LEFT, padx=(4, 2))

            status_lbl = tk.Label(row, text="UNKNOWN",
                                   font=("Courier", 8), fg=AMBER, bg=row_bg,
                                   width=12, anchor="w")
            status_lbl.pack(side=tk.LEFT, padx=(0, 4))

            time_lbl = tk.Label(row, text="—",
                                 font=("Courier", 8), fg="#333355", bg=row_bg,
                                 anchor="w")
            time_lbl.pack(side=tk.LEFT, padx=(4, 0), fill=tk.X, expand=True)

            self.ops_ledger_widgets[nid] = {
                "dot": dot, "status": status_lbl, "time": time_lbl
            }

        tk.Frame(content, bg="#1a1a30", height=1).pack(fill=tk.X, pady=(8, 0))

        # ── Last Known Good State ─────────────────────────────────
        lkg_hdr = tk.Frame(content, bg=BG2, pady=6)
        lkg_hdr.pack(fill=tk.X, pady=(8, 0))
        tk.Label(lkg_hdr, text="LAST KNOWN GOOD STATE",
                 font=("Courier", 8), fg="#222244", bg=BG2,
                 padx=4).pack(side=tk.LEFT)

        self.ops_mark_btn = tk.Button(
            lkg_hdr, text="MARK GOOD",
            font=("Courier", 8, "bold"),
            bg="#0a2a1a", fg=GREEN,
            activebackground="#0a3a2a",
            relief=tk.FLAT, padx=10, pady=3, cursor="hand2",
            command=self._ops_mark_good
        )
        self.ops_mark_btn.pack(side=tk.RIGHT, padx=8)

        lkg_panel = tk.Frame(content, bg=BG3, pady=8)
        lkg_panel.pack(fill=tk.X)

        self.ops_lkg_lbl = tk.Label(
            lkg_panel, text="No saved state.",
            font=("Courier", 9), fg="#333355", bg=BG3,
            justify=tk.LEFT, anchor="w"
        )
        self.ops_lkg_lbl.pack(anchor=tk.W, padx=10)

        # Load any saved good state on startup
        self._ops_load_last_good()

    # ── OPS helpers ────────────────────────────────────────────────────────────
    def _ops_load_last_good(self):
        lkg_file = CONFIG_DIR / "ops_last_good.json"
        if lkg_file.exists():
            try:
                with open(lkg_file) as f:
                    self.ops_last_good = json.load(f)
                self._ops_refresh_lkg_panel()
            except Exception:
                pass

    def _ops_refresh_lkg_panel(self):
        lkg = self.ops_last_good
        if not lkg:
            self.ops_lkg_lbl.config(text="No saved state.", fg="#333355")
            return
        ts     = lkg.get("timestamp", "unknown")
        wg     = lkg.get("wireguard_iface", "—")
        nodes  = ", ".join(lkg.get("nodes_online", [])) or "none"
        tunnel = lkg.get("tunnel_status", "—")
        text   = (f"  Saved:    {ts}\n"
                  f"  WG iface: {wg}\n"
                  f"  Online:   {nodes}\n"
                  f"  Tunnel:   {tunnel}")
        self.ops_lkg_lbl.config(text=text, fg="#d0d0f0")

    def _ops_mark_good(self):
        nodes_online = [cid for cid, s in self.ops_health.items() if s.get("ok")]
        wg_state     = self.ops_health.get("wireguard", {})
        self.ops_last_good = {
            "timestamp":       datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "wireguard_iface": wg_state.get("detail", "—"),
            "nodes_online":    nodes_online,
            "tunnel_status":   "UP" if wg_state.get("ok") else "UNKNOWN",
        }
        lkg_file = CONFIG_DIR / "ops_last_good.json"
        try:
            with open(lkg_file, "w") as f:
                json.dump(self.ops_last_good, f, indent=2)
        except Exception:
            pass
        self.root.after(0, self._ops_refresh_lkg_panel)

    def _ops_quick_scan(self):
        self.ops_scan_btn.config(state=tk.DISABLED, text="SCANNING...")
        self.ops_scan_lbl.config(text="")
        AMBER = "#ffaa00"
        for w in self.ops_health_widgets.values():
            w["dot"].config(fg=AMBER)
            w["status"].config(text="CHECKING", fg=AMBER)

        def run_all():
            threads = [
                threading.Thread(
                    target=self._ops_run_check, args=(cid,), daemon=True)
                for cid in self.ops_health_widgets
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=20)
            ts = datetime.now().strftime("%H:%M:%S")
            self.root.after(0, lambda: [
                self.ops_scan_btn.config(state=tk.NORMAL, text="▶  QUICK SCAN"),
                self.ops_scan_lbl.config(text=f"done {ts}"),
            ])

        threading.Thread(target=run_all, daemon=True).start()

    def _ops_run_check(self, check_id):
        import subprocess as sp
        local_key = str(Path.home() / ".ssh" / "id_ed25519")
        duat_reg  = self.registry.get("duat", {})
        duat_ip   = duat_reg.get("ip") or self.cfg.get("duat_ip", "192.168.1.5")
        duat_port = str(duat_reg.get("port", "22"))
        duat_user = duat_reg.get("user", "duat")

        def ssh_duat(cmd, timeout=15):
            r = sp.run(
                ["ssh", "-i", local_key,
                 "-o", "StrictHostKeyChecking=no",
                 "-o", "BatchMode=yes",
                 "-o", "ConnectTimeout=10",
                 "-p", duat_port, f"{duat_user}@{duat_ip}", cmd],
                capture_output=True, text=True, timeout=timeout
            )
            return r.stdout.strip(), r.returncode

        ok     = False
        detail = ""
        try:
            if check_id == "wireguard":
                out, _ = ssh_duat("sudo wg show interfaces 2>/dev/null")
                ok     = bool(out.strip())
                detail = out.strip() or "no interface"

            elif check_id == "ddns":
                out, _ = ssh_duat("crontab -l 2>/dev/null | grep no-ip || echo MISSING")
                ok     = "MISSING" not in out and bool(out.strip())
                detail = "entry found" if ok else "missing"

            elif check_id == "heartbeat":
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.settimeout(3)
                try:
                    s.sendto(b"PING", (duat_ip, 7744))
                    s.recvfrom(128)
                    ok     = True
                    detail = "responding"
                except Exception:
                    ok     = False
                    detail = "no response"
                finally:
                    s.close()

            elif check_id == "ssh_auth":
                results = []
                for nid in ["duat", "raven", "scarab"]:
                    reg   = self.registry.get(nid, {})
                    nip   = reg.get("ip", "")
                    nport = str(reg.get("port", "22"))
                    nuser = reg.get("user", "")
                    if not nip or not nuser:
                        continue
                    r = sp.run(
                        ["ssh", "-i", local_key,
                         "-o", "BatchMode=yes",
                         "-o", "ConnectTimeout=5",
                         "-p", nport, f"{nuser}@{nip}", "echo OK"],
                        capture_output=True, text=True, timeout=12
                    )
                    results.append(f"{nid}:{'✓' if 'OK' in r.stdout else '✗'}")
                ok     = any("✓" in r for r in results)
                detail = " ".join(results) if results else "no nodes configured"

            elif check_id == "pihole":
                out, _ = ssh_duat("pihole status 2>/dev/null || echo MISSING")
                ok     = "Enabled" in out or "enabled" in out
                detail = "enabled" if ok else "disabled/missing"

            elif check_id == "watchdog":
                ok     = True
                detail = "running"

            elif check_id == "game_server":
                ip       = self.cfg.get("duat_ip", "") or duat_ip
                data, err = _get(f"http://{ip}:5000/api/ladder", timeout=5)
                ok       = data is not None
                detail   = "online" if ok else (err or "offline")[:24]

            elif check_id == "remote_ctrl":
                ok     = None   # amber / manual
                detail = "manual check required"

        except Exception as e:
            ok     = False
            detail = str(e)[:30]

        ts = datetime.now().strftime("%H:%M:%S")
        self.ops_health[check_id] = {"ok": ok, "detail": detail, "ts": ts}

        if ok is None:
            fg, status_txt = "#ffaa00", "MANUAL"
        elif ok:
            fg, status_txt = "#00ff88", "PASS"
        else:
            fg, status_txt = "#ff3344", "FAIL"

        def update_ui():
            w = self.ops_health_widgets.get(check_id)
            if w:
                w["dot"].config(fg=fg)
                w["status"].config(text=status_txt, fg=fg)
                w["time"].config(text=f"{ts}  {detail[:26]}")
        self.root.after(0, update_ui)

    def _ops_check_ledger(self, nid):
        import subprocess as sp
        import hashlib as hl

        local_key = str(Path.home() / ".ssh" / "id_ed25519")
        node_remote_paths = {
            "duat":   "/home/duat/RAVEN_OS_MASTER.md",
            "raven":  "/home/raven/RAVEN_OS_MASTER.md",
            "scarab": "/home/scarab/RAVEN_OS_MASTER.md",
        }
        default_ips  = {"duat": self.cfg.get("duat_ip", "192.168.1.5"),
                        "raven": self.cfg.get("raven_ip", "192.168.1.3"),
                        "scarab": "192.168.1.2"}
        default_users = {"duat": "duat", "raven": "raven", "scarab": "scarab"}

        reg   = self.registry.get(nid, {})
        ip    = reg.get("ip", "") or default_ips.get(nid, "")
        port  = str(reg.get("port", "22"))
        user  = reg.get("user", "") or default_users.get(nid, "")

        if not ip or not user:
            self._ops_update_ledger(nid, "UNREACHABLE", "not configured", "")
            return

        # Compute local MD5
        local_master = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop" / "RAVEN_OS_MASTER.md"
        local_hash   = ""
        if local_master.exists():
            try:
                local_hash = hl.md5(local_master.read_bytes()).hexdigest()
            except Exception:
                pass

        remote_path = node_remote_paths.get(nid, f"/home/{user}/RAVEN_OS_MASTER.md")
        try:
            r = sp.run(
                ["ssh", "-i", local_key,
                 "-o", "StrictHostKeyChecking=no",
                 "-o", "BatchMode=yes",
                 "-o", "ConnectTimeout=10",
                 "-p", port, f"{user}@{ip}",
                 f"md5sum {remote_path} 2>/dev/null || echo MISSING"],
                capture_output=True, text=True, timeout=20
            )
            out = r.stdout.strip()
            ts  = datetime.now().strftime("%H:%M:%S")

            if not out or "MISSING" in out:
                self._ops_update_ledger(nid, "STALE", "file missing on node", ts)
            else:
                remote_hash = out.split()[0]
                if not local_hash:
                    self._ops_update_ledger(nid, "UNKNOWN", "local file missing", ts)
                elif remote_hash == local_hash:
                    self._ops_update_ledger(nid, "VERIFIED", "checksums match", ts)
                else:
                    r2 = sp.run(
                        ["ssh", "-i", local_key,
                         "-o", "StrictHostKeyChecking=no",
                         "-o", "BatchMode=yes",
                         "-o", "ConnectTimeout=10",
                         "-p", port, f"{user}@{ip}",
                         f"stat -c '%y' {remote_path} 2>/dev/null | cut -c1-16"],
                        capture_output=True, text=True, timeout=15
                    )
                    mod = r2.stdout.strip() or "unknown date"
                    self._ops_update_ledger(nid, "STALE", f"mismatch · {mod}", ts)
        except Exception as e:
            self._ops_update_ledger(nid, "UNREACHABLE", str(e)[:28], "")

    def _ops_update_ledger(self, nid, status, detail, ts):
        color_map = {
            "VERIFIED":    "#00ff88",
            "STALE":       "#ff3344",
            "UNREACHABLE": "#ffaa00",
            "UNKNOWN":     "#ffaa00",
        }
        fg = color_map.get(status, "#ffaa00")

        def update():
            w = self.ops_ledger_widgets.get(nid)
            if w:
                w["dot"].config(fg=fg)
                w["status"].config(text=status, fg=fg)
                w["time"].config(
                    text=(f"{ts}  {detail[:30]}" if ts else detail[:30]),
                    fg="#555577"
                )
        self.root.after(0, update)

    def _ops_push_master(self):
        import subprocess as sp
        local_master = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop" / "RAVEN_OS_MASTER.md"
        if not local_master.exists():
            self.root.after(0, lambda: self.ops_push_btn.config(
                state=tk.NORMAL, text="PUSH TO ALL"))
            return

        self.root.after(0, lambda: self.ops_push_btn.config(
            state=tk.DISABLED, text="PUSHING..."))

        local_key = str(Path.home() / ".ssh" / "id_ed25519")
        node_remote_paths = {
            "duat":   "/home/duat/RAVEN_OS_MASTER.md",
            "raven":  "/home/raven/RAVEN_OS_MASTER.md",
            "scarab": "/home/scarab/RAVEN_OS_MASTER.md",
        }
        default_ips   = {"duat": self.cfg.get("duat_ip", "192.168.1.5"),
                         "raven": self.cfg.get("raven_ip", "192.168.1.3"),
                         "scarab": "192.168.1.2"}
        default_users = {"duat": "duat", "raven": "raven", "scarab": "scarab"}

        for nid, remote_path in node_remote_paths.items():
            reg  = self.registry.get(nid, {})
            ip   = reg.get("ip", "") or default_ips.get(nid, "")
            port = str(reg.get("port", "22"))
            user = reg.get("user", "") or default_users.get(nid, "")
            if not ip or not user:
                self._ops_update_ledger(nid, "UNREACHABLE", "not configured", "")
                continue
            try:
                sp.run(
                    ["scp",
                     "-i", local_key,
                     "-o", "StrictHostKeyChecking=no",
                     "-o", "BatchMode=yes",
                     "-o", "ConnectTimeout=10",
                     "-P", port,
                     str(local_master),
                     f"{user}@{ip}:{remote_path}"],
                    capture_output=True, text=True, timeout=30
                )
            except Exception:
                pass
            threading.Thread(
                target=self._ops_check_ledger, args=(nid,), daemon=True).start()

        self.root.after(0, lambda: self.ops_push_btn.config(
            state=tk.NORMAL, text="PUSH TO ALL"))

    def _settings_build_registry_section(self):
        pass  # placeholder

    def _build_settings(self):
        frame = self.tab_frames["SETTINGS"]

        tk.Label(frame, text="SETTINGS",
                 font=("Courier", 14, "bold"),
                 fg="#00ff88", bg="#08080f", pady=18).pack()

        wrap = tk.Frame(frame, bg="#08080f", padx=44)
        wrap.pack(fill=tk.BOTH, expand=True)

        self._section(wrap, "DOWNLOADS FOLDER")
        self.entry_path = self._entry(wrap, self.cfg["downloads_path"])

        self._section(wrap, "DUAT  (Pi 5 — home base)")
        self.entry_duat_ip    = self._labeled_entry(wrap, "IP Address",   self.cfg.get("duat_ip",""))
        self.entry_duat_port  = self._labeled_entry(wrap, "Hash DB Port", self.cfg.get("duat_port","6174"), w=10)
        self.entry_duat_uport = self._labeled_entry(wrap, "Unlock Port",  self.cfg.get("duat_unlock_port","6176"), w=10)

        self.hashdb_var = tk.BooleanVar(value=self.cfg.get("hashdb_enabled", False))
        self.lock_var   = tk.BooleanVar(value=self.cfg.get("lock_on_threat", True))
        self._checkbox(wrap, "Enable hash DB lookups", self.hashdb_var)
        self._checkbox(wrap, "Auto-lock threats via Duat", self.lock_var)

        self._section(wrap, "RAVEN  (Pi Zero 2 — portable)")
        self.entry_raven_ip   = self._labeled_entry(wrap, "IP Address", self.cfg.get("raven_ip",""))
        self.entry_raven_port = self._labeled_entry(wrap, "Port",       self.cfg.get("raven_port","6175"), w=10)

        self._section(wrap, "OPTIONS")
        self.scan_start_var = tk.BooleanVar(value=self.cfg.get("scan_on_start", True))
        self._checkbox(wrap, "Start watching on launch", self.scan_start_var)

        btn_row = tk.Frame(wrap, bg="#08080f")
        btn_row.pack(fill=tk.X, pady=(18,0))

        tk.Button(btn_row, text="⟳  TEST CONNECTIONS",
                  font=("Courier", 10, "bold"),
                  bg="#0a1a2e", fg="#0088ff",
                  relief=tk.FLAT, padx=16, pady=8, cursor="hand2",
                  command=self._test_from_settings
                  ).pack(side=tk.LEFT)

        tk.Button(btn_row, text="💾  SAVE",
                  font=("Courier", 10, "bold"),
                  bg="#00ff88", fg="#060a08",
                  activebackground="#00cc66",
                  relief=tk.FLAT, padx=24, pady=8, cursor="hand2",
                  command=self.save_settings
                  ).pack(side=tk.LEFT, padx=12)

        self.settings_msg = tk.Label(wrap, text="",
                                      font=("Courier", 9),
                                      fg="#444466", bg="#08080f")
        self.settings_msg.pack(anchor=tk.W, pady=(8,0))

    # ── Settings helpers ───────────────────────────────────────
    def _section(self, p, t):
        tk.Label(p, text=t, font=("Courier", 9),
                 fg="#222244", bg="#08080f", pady=10).pack(anchor=tk.W)

    def _entry(self, p, val):
        e = tk.Entry(p, font=("Courier", 11),
                     bg="#0e0e1e", fg="#d0d0f0",
                     insertbackground="#00ff88",
                     relief=tk.FLAT, bd=6)
        e.insert(0, val)
        e.pack(fill=tk.X, pady=(0,4))
        return e

    def _labeled_entry(self, p, label, val, w=None):
        row = tk.Frame(p, bg="#08080f")
        row.pack(fill=tk.X, pady=(0,4))
        tk.Label(row, text=label, font=("Courier", 10),
                 fg="#555577", bg="#08080f",
                 width=16, anchor=tk.W).pack(side=tk.LEFT)
        kw = dict(font=("Courier", 11), bg="#0e0e1e", fg="#d0d0f0",
                  insertbackground="#00ff88", relief=tk.FLAT, bd=6)
        if w:
            kw["width"] = w
        e = tk.Entry(row, **kw)
        e.insert(0, val)
        e.pack(side=tk.LEFT, fill=tk.X, expand=not w)
        return e

    def _checkbox(self, p, label, var):
        tk.Checkbutton(p, text=label, variable=var,
                       font=("Courier", 10), fg="#777799",
                       bg="#08080f", selectcolor="#0e0e1e",
                       activebackground="#08080f",
                       activeforeground="#00ff88").pack(anchor=tk.W)

    def _stat(self, p, label, val):
        f = tk.Frame(p, bg="#0c0c1a", padx=16)
        f.pack(side=tk.LEFT)
        tk.Label(f, text=label, font=("Courier", 8),
                 fg="#222244", bg="#0c0c1a").pack()
        v = tk.Label(f, text=val, font=("Courier", 12, "bold"),
                     fg="#00ff88", bg="#0c0c1a")
        v.pack()
        return v

    def _short_path(self):
        p = self.cfg.get("downloads_path", "")
        return ("..." + p[-18:]) if len(p) > 21 else p

    # ── Connection status loop ─────────────────────────────────
    def _start_status_loop(self):
        threading.Thread(target=self._refresh_connections, daemon=True).start()
        def loop():
            while True:
                time.sleep(30)
                self._refresh_connections()
        threading.Thread(target=loop, daemon=True).start()

    def _refresh_connections(self):
        duat_ok,  duat_msg  = check_duat(self.cfg)
        raven_ok, raven_msg = check_raven(self.cfg)
        self.duat_ok  = duat_ok
        self.raven_ok = raven_ok
        self.root.after(0, lambda: self._paint_dot(
            self.duat_dot, self.duat_detail,
            duat_ok, duat_msg, self.cfg.get("duat_ip","")
        ))
        self.root.after(0, lambda: self._paint_dot(
            self.raven_dot, self.raven_detail,
            raven_ok, raven_msg, self.cfg.get("raven_ip","")
        ))

    def _start_slasher_loop(self):
        """Refresh Slasher data every 15 seconds independently."""
        def loop():
            while True:
                self._refresh_slasher()
                time.sleep(15)
        threading.Thread(target=loop, daemon=True).start()

    def _refresh_slasher(self):
        """Fetch Slasher ladder from Duat game server (port 5000)."""
        ip = self.cfg.get("duat_ip", "")
        if not ip:
            self.root.after(0, lambda: self._refresh_slasher_ui(None))
            return
        raw, _ = _get(f"http://{ip}:5000/api/ladder")
        # API returns {"kills": [...], "hardcore": [...]} or a flat list
        if isinstance(raw, dict):
            combined = raw.get("kills", []) + raw.get("hardcore", [])
        elif isinstance(raw, list):
            combined = raw
        else:
            combined = None  # None signals server offline
        self.root.after(0, lambda d=combined: self._refresh_slasher_ui(d))

    def _refresh_slasher_ui(self, data):
        """Rebuild the Slasher leaderboard with deduplicated, sorted ladder data."""
        C = self._dg_colors
        now_str = datetime.now().strftime("%H:%M:%S")

        for w in self.slasher_frame.winfo_children():
            w.destroy()

        if data is None:
            self.slasher_last_lbl.config(text=f"offline · {now_str}")
            self.slasher_count_lbl.config(text="  server unreachable")
            tk.Label(self.slasher_frame, text="  Server offline — check Duat :5000",
                     font=("Courier", 9), bg=C["BG"], fg=C["MUTED"]).pack(anchor="w", pady=8, padx=14)
            return

        self.slasher_last_lbl.config(text=f"updated {now_str}")

        if not data:
            self.slasher_count_lbl.config(text="  no characters on record")
            tk.Label(self.slasher_frame, text="  No characters on record yet.",
                     font=("Courier", 9), bg=C["BG"], fg=C["MUTED"]).pack(anchor="w", pady=8, padx=14)
            return

        # Deduplicate by char_id (keep highest floor/kills entry per char)
        seen = {}
        for entry in data:
            cid = entry.get("char_id") or entry.get("id") or id(entry)
            existing = seen.get(cid)
            if existing is None:
                seen[cid] = entry
            else:
                ef = entry.get("deepest_floor", entry.get("floor", 0))
                xf = existing.get("deepest_floor", existing.get("floor", 0))
                if ef > xf or (ef == xf and entry.get("kills", 0) > existing.get("kills", 0)):
                    seen[cid] = entry
        deduped = list(seen.values())

        # Sort: alive first, then deepest floor DESC, then kills DESC
        deduped.sort(key=lambda e: (
            0 if e.get("alive", 1) else 1,
            -e.get("deepest_floor", e.get("floor", 0)),
            -e.get("kills", 0),
        ))

        CLS_ICONS = {
            "WARRIOR": "■", "ROGUE": "◉", "MAGE": "◈",
            "CLERIC": "○", "RANGER": "◆",
        }
        RANK_BADGES = {1: ("★", C["GOLD"]), 2: ("◆", C["AMBER"]), 3: ("◉", C["CYAN"])}

        alive_count = sum(1 for e in deduped if e.get("alive", 1))
        self.slasher_count_lbl.config(
            text=f"  {len(deduped)} characters  ·  {alive_count} alive")

        for rank, entry in enumerate(deduped[:12], start=1):
            name      = entry.get("username", "?")
            char_name = entry.get("char_name", "")
            level     = entry.get("level", 1)
            floor     = entry.get("deepest_floor", entry.get("floor", 0))
            kills     = entry.get("kills", 0)
            cls_name  = (entry.get("char_class") or entry.get("class") or "?").upper()
            alive     = entry.get("alive", 1)

            row_bg = "#0a0a18" if alive else "#100808"
            row = tk.Frame(self.slasher_frame, bg=row_bg, pady=3)
            row.pack(fill=tk.X, pady=1, padx=2)

            # Rank badge
            badge_txt, badge_col = RANK_BADGES.get(rank, (str(rank), C["MUTED"]))
            tk.Label(row, text=badge_txt, font=("Courier", 8, "bold"),
                     bg=row_bg, fg=badge_col, width=2, anchor="e").pack(side=tk.LEFT, padx=(6, 4))

            # Player name
            name_col = C["WHITE"] if alive else "#554444"
            icon = CLS_ICONS.get(cls_name, "·")
            tk.Label(row, text=f"{icon} {name[:9]:<9}",
                     font=("Courier", 9, "bold"), bg=row_bg, fg=name_col,
                     width=12, anchor="w").pack(side=tk.LEFT, padx=(0, 2))

            # Level
            tk.Label(row, text=f"{level:<3}",
                     font=("Courier", 8), bg=row_bg, fg=C["CYAN"],
                     width=4, anchor="w").pack(side=tk.LEFT)

            # Floor
            floor_col = C["GOLD"] if floor >= 10 else (C["AMBER"] if floor >= 5 else C["MUTED"])
            tk.Label(row, text=f"F{floor:<3}",
                     font=("Courier", 8), bg=row_bg, fg=floor_col,
                     width=6, anchor="w").pack(side=tk.LEFT)

            # Kills
            tk.Label(row, text=f"⚔{kills:<4}",
                     font=("Courier", 8), bg=row_bg, fg="#9966aa",
                     width=7, anchor="w").pack(side=tk.LEFT)

            # Class
            tk.Label(row, text=f"{cls_name[:7]:<7}",
                     font=("Courier", 8), bg=row_bg, fg=C["MUTED"],
                     width=8, anchor="w").pack(side=tk.LEFT)

            # Char name
            if char_name:
                tk.Label(row, text=char_name[:14],
                         font=("Courier", 8), bg=row_bg, fg="#666688",
                         anchor="w").pack(side=tk.LEFT, padx=(2, 0))

    def _paint_dot(self, dot, detail, ok, msg, ip):
        if not ip:
            dot.config(fg="#1a1a33")
            detail.config(text="not configured", fg="#1a1a33")
        elif ok:
            dot.config(fg="#00ff88")
            detail.config(text=msg, fg="#00ff88")
        else:
            dot.config(fg="#ff3344")
            detail.config(text=msg or "offline", fg="#444455")

    def _scarab_listener(self):
        """Listen for UDP heartbeats from Scarab on port 7743."""
        import socket as _socket
        sock = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        sock.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", 7744))  # separate port for Scarab
        sock.settimeout(5)
        while True:
            try:
                data, addr = sock.recvfrom(1024)
                msg = data.decode().strip()
                parts = msg.split("|")
                # Format: SCARAB|Scarab|scarab|ALIVE|TUNNEL|DUAT
                if len(parts) >= 4 and parts[0] == "SCARAB" and parts[3] == "ALIVE":
                    self.scarab_ok     = True
                    self.scarab_last   = time.time()
                    self.scarab_tunnel = parts[4] if len(parts) > 4 else "UNKNOWN"
                    self.scarab_duat   = parts[5] if len(parts) > 5 else "UNKNOWN"
                    ago = "now"
                    detail = f"tunnel:{self.scarab_tunnel.lower()} duat:{self.scarab_duat.lower()}"
                    self.root.after(0, lambda d=detail: self._paint_dot(
                        self.scarab_dot, self.scarab_detail, True, d, "scarab"
                    ))
            except Exception:
                # Check timeout
                if self.scarab_last > 0 and (time.time() - self.scarab_last) > 90:
                    self.scarab_ok = False
                    self.root.after(0, lambda: self._paint_dot(
                        self.scarab_dot, self.scarab_detail, False, "offline", "scarab"
                    ))

    def _recheck_connections(self):
        self.duat_dot.config(fg="#ffaa00")
        self.raven_dot.config(fg="#ffaa00")
        self.scarab_dot.config(fg="#ffaa00")
        threading.Thread(target=self._refresh_connections, daemon=True).start()

    def _test_from_settings(self):
        test = self.cfg.copy()
        test["duat_ip"]    = self.entry_duat_ip.get().strip()
        test["duat_port"]  = self.entry_duat_port.get().strip()
        test["raven_ip"]   = self.entry_raven_ip.get().strip()
        test["raven_port"] = self.entry_raven_port.get().strip()
        self.settings_msg.config(text="Testing...", fg="#ffaa00")
        self.root.update()
        dok,  dmsg  = check_duat(test)
        rok,  rmsg  = check_raven(test)
        lines = [
            f"Duat:  {'✓ ' + dmsg if dok else '✗ ' + (dmsg or 'offline')}",
            f"Raven: {'✓ ' + rmsg if rok else '✗ ' + (rmsg or 'offline')}",
        ]
        self.settings_msg.config(
            text="\n".join(lines),
            fg="#00ff88" if (dok or rok) else "#ff3344"
        )

    def save_settings(self):
        self.cfg.update({
            "downloads_path":   self.entry_path.get().strip(),
            "duat_ip":          self.entry_duat_ip.get().strip(),
            "duat_port":        self.entry_duat_port.get().strip(),
            "duat_unlock_port": self.entry_duat_uport.get().strip(),
            "raven_ip":         self.entry_raven_ip.get().strip(),
            "raven_port":       self.entry_raven_port.get().strip(),
            "hashdb_enabled":   self.hashdb_var.get(),
            "lock_on_threat":   self.lock_var.get(),
            "scan_on_start":    self.scan_start_var.get(),
        })
        save_config(self.cfg)
        self.stat_watch.config(text=self._short_path())
        self.settings_msg.config(text="✓  Saved", fg="#00ff88")
        self.log("✓  Settings saved", "info")
        threading.Thread(target=self._refresh_connections, daemon=True).start()
        self.show_tab("MONITOR")

    # ── Logging ────────────────────────────────────────────────
    def log(self, msg, tag="info"):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_box.config(state=tk.NORMAL)
        self.log_box.insert(tk.END, f"[{ts}] {msg}\n", tag)
        self.log_box.see(tk.END)
        self.log_box.config(state=tk.DISABLED)
        try:
            with open(LOG_FILE, "a") as f:
                f.write(f"[{ts}] {msg}\n")
        except Exception:
            pass

    def set_status(self, text, color="#00ff88"):
        self.status_label.config(text=f"● {text}", fg=color)

    # ── File processing ────────────────────────────────────────
    def process_file(self, filepath):
        path = Path(filepath)
        if not path.exists() or not path.is_file():
            return

        name    = path.name
        ext     = path.suffix.lower()
        ext_sus = ext in SUSPICIOUS_EXTS

        time.sleep(0.4)

        self.file_count += 1
        self.root.after(0, lambda: self.stat_files.config(text=str(self.file_count)))

        if self.cfg.get("hashdb_enabled") and self.duat_ok:
            self.root.after(0, lambda n=name: self.log(f"↓  {n} — hashing...", "new"))
            h = hash_file(filepath)

            if h:
                result  = query_hashdb(self.cfg, h, name)
                verdict = result.get("verdict", "unknown")

                if verdict == "malicious":
                    threat = result.get("threat_name", "unknown")
                    self.threat_count += 1
                    self.locked_count += 1
                    self.root.after(0, lambda n=name, t=threat, fh=h: [
                        self.log(f"🚨 MALICIOUS: {n}  [{t}]  ({fh[:16]}...)", "threat"),
                        self.stat_threats.config(text=str(self.threat_count), fg="#ff3344"),
                        self.stat_locked.config(text=str(self.locked_count),  fg="#ff3344"),
                    ])
                    threading.Thread(
                        target=self._do_lock,
                        args=(filepath, name, verdict, threat, h),
                        daemon=True
                    ).start()

                elif verdict == "known_clean":
                    self.root.after(0, lambda n=name, fh=h:
                        self.log(f"✓  Clean: {n}  ({fh[:16]}...)", "clean"))

                else:
                    self.unknown_count += 1
                    self.root.after(0, lambda n=name, fh=h: [
                        self.log(f"?  Unknown: {n}  ({fh[:16]}...)  flagged", "unknown"),
                        self.stat_unknown.config(text=str(self.unknown_count), fg="#ff8800"),
                    ])

                if ext_sus and verdict != "malicious":
                    self.root.after(0, lambda n=name:
                        self.log(f"⚠  Suspicious extension: {n}", "warn"))
            else:
                self.root.after(0, lambda n=name:
                    self.log(f"↓  {n}  (locked/empty — could not hash)", "muted"))
                if ext_sus:
                    self.root.after(0, lambda n=name:
                        self.log(f"⚠  Suspicious extension: {n}", "warn"))
        else:
            if ext_sus:
                self.threat_count += 1
                self.root.after(0, lambda n=name: [
                    self.log(f"⚠  Suspicious extension: {n}", "warn"),
                    self.stat_threats.config(text=str(self.threat_count), fg="#ff3344"),
                ])
            else:
                self.root.after(0, lambda n=name: self.log(f"↓  {n}", "new"))

    def _do_lock(self, filepath, name, verdict, threat, h):
        # Lock locally first — no SSH needed
        reason = f"{verdict}: {threat[:20]}" if threat else verdict
        self.local_lock(filepath, name, reason=reason)

        # Notify Duat → forwards to Raven for e-ink alert
        if self.cfg.get("duat_ip") and self.duat_ok:
            ok, msg = send_lock_request(self.cfg, filepath, name, verdict, threat, h)
            if ok:
                self.root.after(0, lambda n=name:
                    self.log(f"   Raven notified via Duat: {n}", "muted"))

    # ── Local lock / unlock (wired to icacls) ──────────────────
    def local_lock(self, filepath, name, reason="threat"):
        ok, err = icacls_lock(filepath)
        if ok:
            self.locked_files[filepath] = {"name": name, "reason": reason}
            self.root.after(0, lambda n=name: [
                self.log(f"🔒 Locked: {n}", "locked"),
                self._refresh_locked_panel(),
            ])
        else:
            self.root.after(0, lambda n=name, e=err:
                self.log(f"⚠  Lock failed: {n} — {e}", "warn"))

    def local_unlock(self, filepath):
        name = self.locked_files.get(filepath, {}).get("name", Path(filepath).name)
        ok, err = icacls_unlock(filepath)
        if ok:
            self.locked_files.pop(filepath, None)
            self.root.after(0, lambda n=name: [
                self.log(f"🔓 Unlocked: {n}", "info"),
                self._refresh_locked_panel(),
            ])
        else:
            self.root.after(0, lambda n=name, e=err:
                self.log(f"⚠  Unlock failed: {n} — {e}", "warn"))

    def _refresh_locked_panel(self):
        for w in self.locked_panel_inner.winfo_children():
            w.destroy()
        if not self.locked_files:
            tk.Label(self.locked_panel_inner, text="No locked files.",
                     font=("Courier", 9), fg="#333355", bg="#08080f"
                     ).pack(anchor=tk.W, padx=8, pady=3)
        else:
            for fp, info in list(self.locked_files.items()):
                row = tk.Frame(self.locked_panel_inner, bg="#08080f")
                row.pack(fill=tk.X, padx=8, pady=1)
                tk.Label(row, text=f"🔒 {info['name']}",
                         font=("Courier", 9), fg="#ff3344", bg="#08080f"
                         ).pack(side=tk.LEFT)
                tk.Label(row, text=f"  {info['reason']}",
                         font=("Courier", 8), fg="#444455", bg="#08080f"
                         ).pack(side=tk.LEFT)

    # ── Process monitor integration ─────────────────────────────
    def _on_process_flags(self, flagged: list):
        """Callback fired on main thread when process scan finds anomalies."""
        self.flagged_processes = flagged
        self._refresh_process_panel()
        for entry in flagged:
            self.log(
                f"⚠  PROCESS: {entry['name']} — {entry['reason']}", "warn"
            )

    def _refresh_process_panel(self):
        for w in self.proc_panel_inner.winfo_children():
            w.destroy()
        if not self.flagged_processes:
            tk.Label(self.proc_panel_inner, text="No anomalies detected.",
                     font=("Courier", 9), fg="#333355", bg="#08080f"
                     ).pack(anchor=tk.W, padx=8, pady=3)
        else:
            for entry in self.flagged_processes:
                row = tk.Frame(self.proc_panel_inner, bg="#08080f")
                row.pack(fill=tk.X, padx=8, pady=1)
                tk.Label(row, text=f"⚠ {entry['name']}",
                         font=("Courier", 9, "bold"),
                         fg="#ffaa00", bg="#08080f").pack(side=tk.LEFT)
                tk.Label(row,
                         text=f"  {entry['reason'][:80]}",
                         font=("Courier", 8), fg="#555577",
                         bg="#08080f").pack(side=tk.LEFT)

    def _run_process_scan(self):
        """Manual one-shot process scan triggered by the SCAN button."""
        if not HAS_PROCMON:
            self.root.after(0, lambda: self.log(
                "⚠  process_monitor module not available", "warn"))
            return
        flagged = _pm.run_scan(
            raven_ip=self.cfg.get("raven_ip", "192.168.1.3"),
            raven_port=int(self.cfg.get("raven_port", 6175)),
            log_fn=self.log,
        )
        self.root.after(0, lambda f=flagged: self._on_process_flags(f))

    # ── Watcher ────────────────────────────────────────────────
    def start_watching(self):
        path = self.cfg.get("downloads_path", "")
        if not path or not Path(path).exists():
            self.log(f"⚠  Path not found: {path} — check Settings", "warn")
            return
        if self.running:
            return

        if HAS_WATCHDOG:
            handler = DownloadsHandler(self)
            self.observer = Observer()
            self.observer.schedule(handler, path, recursive=False)
            self.observer.start()
        else:
            threading.Thread(target=self._poll_loop, daemon=True).start()

        self.running = True
        self.paused  = False
        self.set_status("WATCHING", "#00ff88")
        self.btn_start.config(state=tk.DISABLED)
        self.btn_pause.config(state=tk.NORMAL, fg="#00ff88")
        self.btn_stop.config(state=tk.NORMAL)
        self.log(f"✓  Watching: {path}", "info")
        self.log(f"   Duat:  {'online' if self.duat_ok else 'offline'}", "info")
        self.log(f"   Raven: {'online' if self.raven_ok else 'offline'}", "info")
        self.log("─" * 55, "muted")
        threading.Thread(target=self.scan_existing, daemon=True).start()

    def _poll_loop(self):
        p = Path(self.cfg.get("downloads_path", ""))
        known = set(p.iterdir()) if p.exists() else set()
        while self.running:
            if not self.paused and p.exists():
                cur = set(p.iterdir())
                for f in (cur - known):
                    if f.is_file():
                        threading.Thread(target=self.process_file,
                                         args=(str(f),), daemon=True).start()
                for f in (known - cur):
                    self.root.after(0, lambda n=f.name:
                        self.log(f"✗  Removed: {n}", "muted"))
                known = cur
            time.sleep(5)

    def scan_existing(self):
        p = Path(self.cfg.get("downloads_path", ""))
        if not p.exists():
            return
        files = [f for f in p.iterdir() if f.is_file()]
        if not files:
            self.root.after(0, lambda: self.log("Downloads is empty.", "muted"))
            return
        self.root.after(0, lambda c=len(files):
            self.log(f"Scanning {c} existing files...", "info"))
        for f in files:
            self.process_file(str(f))
        self.root.after(0, lambda: self.log("Scan complete.", "info"))

    def toggle_pause(self):
        self.paused = not self.paused
        if self.paused:
            self.btn_pause.config(text="▶  RESUME")
            self.set_status("PAUSED", "#ffaa00")
            self.log("Paused.", "warn")
            if self.observer:
                self.observer.stop()
        else:
            self.btn_pause.config(text="⏸  PAUSE")
            self.set_status("WATCHING", "#00ff88")
            self.log("Resumed.", "info")
            if HAS_WATCHDOG:
                path = self.cfg.get("downloads_path", "")
                handler = DownloadsHandler(self)
                self.observer = Observer()
                self.observer.schedule(handler, path, recursive=False)
                self.observer.start()

    def stop_watching(self):
        self.running = False
        if self.observer:
            self.observer.stop()
            self.observer.join()
            self.observer = None
        self.set_status("STOPPED", "#ff3344")
        self.log("Stopped.", "warn")
        self.btn_start.config(state=tk.NORMAL)
        self.btn_pause.config(state=tk.DISABLED, text="⏸  PAUSE", fg="#333355")
        self.btn_stop.config(state=tk.DISABLED)

    def on_close(self):
        self.stop_watching()
        self.root.destroy()


# ── Entry ──────────────────────────────────────────────────────
if __name__ == "__main__":
    root = tk.Tk()
    app = RavenWatchdog(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()
