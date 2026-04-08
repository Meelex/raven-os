#!/usr/bin/env python3
"""
watchdog.py - Raven OS
Monitors the Windows PC's Downloads folder via SSH.
Displays activity in a GUI with pause/stop controls.
"""

import os
import json
import subprocess
import threading
import time
import tkinter as tk
from tkinter import ttk, scrolledtext
from datetime import datetime
from pathlib import Path

# ── Config ────────────────────────────────────────────────────
CONFIG_FILE = Path.home() / ".raven" / "config"
LOG_FILE    = Path.home() / ".raven" / "watchdog.log"
SCAN_INTERVAL = 30  # seconds between scans

# Suspicious file extensions
SUSPICIOUS_EXTS = {
    ".exe", ".bat", ".cmd", ".ps1", ".vbs", ".js",
    ".msi", ".dll", ".scr", ".pif", ".com", ".jar",
    ".hta", ".wsf", ".reg", ".inf"
}

# ── Load config ───────────────────────────────────────────────
def load_config():
    config = {}
    if not CONFIG_FILE.exists():
        return None
    with open(CONFIG_FILE) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                config[k.strip()] = v.strip()
    return config

# ── SSH scan ──────────────────────────────────────────────────
def scan_downloads(config):
    """SSH into Windows as Horus and list Downloads folder contents."""
    cmd = [
        "ssh",
        "-i", config["SSH_KEY"],
        "-o", "StrictHostKeyChecking=no",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=10",
        f"{config['WINDOWS_USER']}@{config['WINDOWS_IP']}",
        f"dir \"{config['WATCHED_PATH']}\" /b /a:-d 2>&1"
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        if result.returncode != 0:
            return None, result.stderr.strip()
        files = [f.strip() for f in result.stdout.strip().splitlines() if f.strip()]
        return files, None
    except subprocess.TimeoutExpired:
        return None, "SSH connection timed out"
    except Exception as e:
        return None, str(e)

def classify_files(files):
    """Separate files into suspicious and clean."""
    suspicious = []
    clean = []
    for f in files:
        ext = Path(f).suffix.lower()
        if ext in SUSPICIOUS_EXTS:
            suspicious.append(f)
        else:
            clean.append(f)
    return suspicious, clean

# ── GUI ───────────────────────────────────────────────────────
class RavenWatchdog:
    def __init__(self, root):
        self.root = root
        self.root.title("Raven OS — Watchdog")
        self.root.geometry("720x520")
        self.root.configure(bg="#0a0a0f")
        self.root.resizable(True, True)

        self.config = load_config()
        self.running = False
        self.paused = False
        self.thread = None
        self.last_files = set()
        self.scan_count = 0

        self._build_ui()

        if not self.config:
            self.log("⚠  Config not found. Run setup_pi_raven.sh first.", "warn")
        else:
            self.log(f"✓  Connected to {self.config.get('WINDOWS_IP', 'unknown')} as {self.config.get('WINDOWS_USER', 'Horus')}")
            self.log(f"✓  Watching: {self.config.get('WATCHED_PATH', '')}")
            self.log(f"✓  Scan interval: {SCAN_INTERVAL}s")
            self.log("─" * 60)
            self.start_watchdog()

    def _build_ui(self):
        # ── Header ──
        header = tk.Frame(self.root, bg="#0e0e18", pady=12)
        header.pack(fill=tk.X)

        tk.Label(header, text="👁  RAVEN WATCHDOG",
                 font=("Courier", 16, "bold"),
                 fg="#00ff88", bg="#0e0e18").pack(side=tk.LEFT, padx=20)

        self.status_label = tk.Label(header, text="● STARTING",
                                      font=("Courier", 10),
                                      fg="#ffaa00", bg="#0e0e18")
        self.status_label.pack(side=tk.RIGHT, padx=20)

        # ── Stats bar ──
        stats = tk.Frame(self.root, bg="#111120", pady=6)
        stats.pack(fill=tk.X)

        self.stat_scans = self._stat_item(stats, "SCANS", "0")
        self.stat_files = self._stat_item(stats, "FILES", "0")
        self.stat_threats = self._stat_item(stats, "THREATS", "0")
        self.stat_last = self._stat_item(stats, "LAST SCAN", "never")

        # ── Log area ──
        log_frame = tk.Frame(self.root, bg="#0a0a0f", padx=10, pady=6)
        log_frame.pack(fill=tk.BOTH, expand=True)

        tk.Label(log_frame, text="ACTIVITY LOG",
                 font=("Courier", 9),
                 fg="#444466", bg="#0a0a0f").pack(anchor=tk.W)

        self.log_box = scrolledtext.ScrolledText(
            log_frame,
            font=("Courier", 10),
            bg="#060610",
            fg="#00dd77",
            insertbackground="#00ff88",
            relief=tk.FLAT,
            borderwidth=0,
            wrap=tk.WORD,
            state=tk.DISABLED
        )
        self.log_box.pack(fill=tk.BOTH, expand=True, pady=(4, 0))

        # Tag colors
        self.log_box.tag_config("warn",    foreground="#ffaa00")
        self.log_box.tag_config("threat",  foreground="#ff4455")
        self.log_box.tag_config("info",    foreground="#00ff88")
        self.log_box.tag_config("muted",   foreground="#444466")
        self.log_box.tag_config("new",     foreground="#00aaff")

        # ── Controls ──
        controls = tk.Frame(self.root, bg="#0e0e18", pady=10)
        controls.pack(fill=tk.X)

        self.btn_pause = tk.Button(
            controls, text="⏸  PAUSE",
            font=("Courier", 10, "bold"),
            bg="#1a1a2e", fg="#00ff88",
            activebackground="#0a3a2e",
            relief=tk.FLAT, padx=20, pady=8,
            cursor="hand2",
            command=self.toggle_pause
        )
        self.btn_pause.pack(side=tk.LEFT, padx=(20, 8))

        self.btn_scan = tk.Button(
            controls, text="⟳  SCAN NOW",
            font=("Courier", 10, "bold"),
            bg="#1a1a2e", fg="#0088ff",
            activebackground="#0a1a3e",
            relief=tk.FLAT, padx=20, pady=8,
            cursor="hand2",
            command=self.force_scan
        )
        self.btn_scan.pack(side=tk.LEFT, padx=8)

        self.btn_stop = tk.Button(
            controls, text="■  STOP",
            font=("Courier", 10, "bold"),
            bg="#1a1a2e", fg="#ff4455",
            activebackground="#3a0a0a",
            relief=tk.FLAT, padx=20, pady=8,
            cursor="hand2",
            command=self.stop_watchdog
        )
        self.btn_stop.pack(side=tk.RIGHT, padx=20)

        self.countdown_label = tk.Label(
            controls, text="",
            font=("Courier", 9),
            fg="#444466", bg="#0e0e18"
        )
        self.countdown_label.pack(side=tk.RIGHT, padx=8)

    def _stat_item(self, parent, label, value):
        frame = tk.Frame(parent, bg="#111120", padx=20)
        frame.pack(side=tk.LEFT)
        tk.Label(frame, text=label,
                 font=("Courier", 8),
                 fg="#444466", bg="#111120").pack()
        val = tk.Label(frame, text=value,
                       font=("Courier", 13, "bold"),
                       fg="#00ff88", bg="#111120")
        val.pack()
        return val

    def log(self, msg, tag="info"):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_box.config(state=tk.NORMAL)
        self.log_box.insert(tk.END, f"[{ts}] {msg}\n", tag)
        self.log_box.see(tk.END)
        self.log_box.config(state=tk.DISABLED)

        # Also write to log file
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a") as f:
            f.write(f"[{ts}] {msg}\n")

    def set_status(self, text, color="#00ff88"):
        self.status_label.config(text=f"● {text}", fg=color)

    def start_watchdog(self):
        self.running = True
        self.paused = False
        self.set_status("WATCHING", "#00ff88")
        self.thread = threading.Thread(target=self._watch_loop, daemon=True)
        self.thread.start()
        self._update_countdown()

    def _watch_loop(self):
        while self.running:
            if not self.paused:
                self._do_scan()
            # Wait with countdown
            for i in range(SCAN_INTERVAL):
                if not self.running:
                    return
                time.sleep(1)

    def _do_scan(self):
        if not self.config:
            return

        self.root.after(0, lambda: self.set_status("SCANNING...", "#ffaa00"))

        files, error = scan_downloads(self.config)

        if error:
            self.root.after(0, lambda: self.log(f"SSH Error: {error}", "warn"))
            self.root.after(0, lambda: self.set_status("ERROR", "#ff4455"))
            return

        self.scan_count += 1
        current_files = set(files) if files else set()
        new_files = current_files - self.last_files
        removed_files = self.last_files - current_files
        suspicious, clean = classify_files(list(current_files))

        # Update stats
        self.root.after(0, lambda: self.stat_scans.config(text=str(self.scan_count)))
        self.root.after(0, lambda: self.stat_files.config(text=str(len(current_files))))
        self.root.after(0, lambda: self.stat_threats.config(
            text=str(len(suspicious)),
            fg="#ff4455" if suspicious else "#00ff88"
        ))
        self.root.after(0, lambda: self.stat_last.config(
            text=datetime.now().strftime("%H:%M:%S")
        ))

        # Log new files
        for f in new_files:
            ext = Path(f).suffix.lower()
            if ext in SUSPICIOUS_EXTS:
                self.root.after(0, lambda fn=f: self.log(f"⚠  SUSPICIOUS: {fn}", "threat"))
            else:
                self.root.after(0, lambda fn=f: self.log(f"↓  New file: {fn}", "new"))

        # Log removed files
        for f in removed_files:
            self.root.after(0, lambda fn=f: self.log(f"✗  Removed: {fn}", "muted"))

        # Log threats summary
        if suspicious and self.scan_count == 1:
            for f in suspicious:
                self.root.after(0, lambda fn=f: self.log(f"⚠  SUSPICIOUS: {fn}", "threat"))

        if not new_files and not removed_files and self.scan_count > 1:
            self.root.after(0, lambda: self.log(
                f"No changes. {len(current_files)} files in Downloads.", "muted"
            ))

        self.last_files = current_files
        self.root.after(0, lambda: self.set_status("WATCHING", "#00ff88"))

    def force_scan(self):
        if not self.running:
            return
        self.log("Manual scan triggered...", "info")
        threading.Thread(target=self._do_scan, daemon=True).start()

    def toggle_pause(self):
        self.paused = not self.paused
        if self.paused:
            self.btn_pause.config(text="▶  RESUME")
            self.set_status("PAUSED", "#ffaa00")
            self.log("Watchdog paused.", "warn")
        else:
            self.btn_pause.config(text="⏸  PAUSE")
            self.set_status("WATCHING", "#00ff88")
            self.log("Watchdog resumed.", "info")

    def stop_watchdog(self):
        self.running = False
        self.set_status("STOPPED", "#ff4455")
        self.log("Watchdog stopped.", "warn")
        self.btn_pause.config(state=tk.DISABLED)
        self.btn_scan.config(state=tk.DISABLED)
        self.btn_stop.config(state=tk.DISABLED)

    def _update_countdown(self):
        if not self.running:
            return
        if self.paused:
            self.countdown_label.config(text="paused")
        else:
            elapsed = int(time.time()) % SCAN_INTERVAL
            remaining = SCAN_INTERVAL - elapsed
            self.countdown_label.config(text=f"next scan in {remaining}s")
        self.root.after(1000, self._update_countdown)


# ── Entry point ───────────────────────────────────────────────
if __name__ == "__main__":
    root = tk.Tk()
    app = RavenWatchdog(root)
    root.mainloop()
