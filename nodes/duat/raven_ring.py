#!/usr/bin/env python3
"""
raven_ring.py — Raven Ring Framework
Runs on Duat (Pi 5). Connects to a COLMI R02 smart ring over BLE.
Collects biometrics, detects gestures (Iron House editing context),
and serves a REST API on port 7744.

Install:
    pip install colmi-r02-client --break-system-packages

Run:
    python3 raven_ring.py                  # normal daemon
    python3 raven_ring.py --test-gestures  # gesture calibration mode

API endpoints (port 7744):
    GET  /status
    GET  /biometrics/latest
    GET  /biometrics/history?hours=24
    GET  /baseline
    GET  /gesture/latest
    POST /gesture/mode   {"mode": "editing"|"off"}
"""

import asyncio
import sys
import time
import math
import sqlite3
import threading
import json
import logging
import argparse
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

try:
    from colmi_r02_client.client import Client as RingClient
    from colmi_r02_client import steps as ring_steps
except ImportError:
    print("ERROR: colmi-r02-client not installed.")
    print("Run: pip install colmi-r02-client --break-system-packages")
    sys.exit(1)

# ── Config ───────────────────────────────────────────────────────────────────
RING_ADDRESS        = "XX:XX:XX:XX:XX:XX"  # Set to your COLMI R02 BLE address
DB_PATH             = "/home/raven/raven_ring.db"
API_HOST            = "0.0.0.0"
API_PORT            = 7744
POLL_INTERVAL_S     = 30     # realtime HR/SpO2 each take a few seconds to collect
BASELINE_INTERVAL_S = 3600   # save baseline snapshot every hour

# Gesture detection thresholds — tune these with --test-gestures
# Watch the raw values, then adjust constants at the top here.
TAP_G           = 1.2    # g-force spike to count as one tap
TAP_MAX_MS      = 150    # a tap spike longer than this is not a tap
HOLD_3S         = 3.0    # hold duration for EDIT_RIGHT_5
HOLD_5S         = 5.0    # hold duration for EDIT_LEFT_10
SWIPE_G         = 1.5    # X-axis g-force for swipe
SWIPE_MAX_MS    = 500    # swipe spike must resolve within this
TAP_WINDOW_MS   = 700    # collect multiple taps within this window
STILL_THRESHOLD = 0.3    # total g below this = "holding still"

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("raven_ring")


# ── Shared state (written by BLE loop, read by API) ──────────────────────────
class RingState:
    def __init__(self):
        self._lock           = threading.Lock()
        self.connected       = False
        self.last_seen       = None
        self.heart_rate      = None
        self.spo2            = None
        self.steps           = None
        self.battery         = None
        self.latest_gesture  = None
        self.latest_gesture_ts = None
        self.gesture_mode    = "off"   # "off" or "editing"
        self.baseline        = {}
        self.confidence      = 0.0

    def update_biometrics(self, hr=None, spo2=None, steps=None, battery=None):
        with self._lock:
            self.connected = True
            self.last_seen = time.time()
            if hr      is not None and hr      > 0: self.heart_rate = hr
            if spo2    is not None and spo2    > 0: self.spo2       = spo2
            if steps   is not None:                  self.steps      = steps
            if battery is not None:                  self.battery    = battery

    def record_gesture(self, name, context):
        with self._lock:
            self.latest_gesture    = name
            self.latest_gesture_ts = time.time()

    def set_disconnected(self):
        with self._lock:
            self.connected = False

    def snapshot(self):
        with self._lock:
            return {
                "connected":         self.connected,
                "last_seen":         self.last_seen,
                "heart_rate":        self.heart_rate,
                "spo2":              self.spo2,
                "steps":             self.steps,
                "battery":           self.battery,
                "latest_gesture":    self.latest_gesture,
                "latest_gesture_ts": self.latest_gesture_ts,
                "gesture_mode":      self.gesture_mode,
                "baseline":          dict(self.baseline),
                "confidence":        self.confidence,
            }


state = RingState()


# ── Database ─────────────────────────────────────────────────────────────────
def init_db():
    con = sqlite3.connect(DB_PATH)
    con.executescript("""
        CREATE TABLE IF NOT EXISTS biometrics (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          REAL NOT NULL,
            heart_rate  INTEGER,
            spo2        INTEGER,
            steps       INTEGER,
            battery     INTEGER
        );
        CREATE TABLE IF NOT EXISTS gestures (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          REAL NOT NULL,
            gesture     TEXT NOT NULL,
            context     TEXT
        );
        CREATE TABLE IF NOT EXISTS baseline (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            computed_at  REAL NOT NULL,
            hr_mean      REAL,
            hr_std       REAL,
            hr_min       INTEGER,
            hr_max       INTEGER,
            spo2_mean    REAL,
            spo2_std     REAL,
            sample_count INTEGER,
            confidence   REAL
        );
    """)
    con.commit()
    con.close()
    log.info("Database ready at %s", DB_PATH)


def db_insert_biometric(ts, hr, spo2, steps, battery):
    try:
        con = sqlite3.connect(DB_PATH)
        con.execute(
            "INSERT INTO biometrics (ts, heart_rate, spo2, steps, battery) VALUES (?,?,?,?,?)",
            (ts, hr, spo2, steps, battery)
        )
        con.commit()
        con.close()
    except Exception as e:
        log.error("DB write biometric: %s", e)


def db_insert_gesture(ts, name, context):
    try:
        con = sqlite3.connect(DB_PATH)
        con.execute(
            "INSERT INTO gestures (ts, gesture, context) VALUES (?,?,?)",
            (ts, name, context)
        )
        con.commit()
        con.close()
    except Exception as e:
        log.error("DB write gesture: %s", e)


def db_insert_baseline(snap):
    try:
        con = sqlite3.connect(DB_PATH)
        con.execute(
            """INSERT INTO baseline
               (computed_at, hr_mean, hr_std, hr_min, hr_max,
                spo2_mean, spo2_std, sample_count, confidence)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (snap["computed_at"], snap["hr_mean"], snap["hr_std"],
             snap["hr_min"],      snap["hr_max"],
             snap["spo2_mean"],   snap["spo2_std"],
             snap["sample_count"], snap["confidence"])
        )
        con.commit()
        con.close()
    except Exception as e:
        log.error("DB write baseline: %s", e)


def db_get_biometrics_last_n_hours(hours=24):
    since = time.time() - hours * 3600
    try:
        con = sqlite3.connect(DB_PATH)
        rows = con.execute(
            "SELECT ts, heart_rate, spo2, steps, battery "
            "FROM biometrics WHERE ts >= ? ORDER BY ts",
            (since,)
        ).fetchall()
        con.close()
        return [
            {"ts": r[0], "heart_rate": r[1], "spo2": r[2],
             "steps": r[3], "battery": r[4]}
            for r in rows
        ]
    except Exception as e:
        log.error("DB query biometrics: %s", e)
        return []


def db_get_all_biometrics_for_baseline():
    """Return all valid HR and SpO2 readings for baseline computation."""
    try:
        con = sqlite3.connect(DB_PATH)
        hr_rows   = con.execute(
            "SELECT heart_rate FROM biometrics WHERE heart_rate IS NOT NULL AND heart_rate > 0"
        ).fetchall()
        spo2_rows = con.execute(
            "SELECT spo2 FROM biometrics WHERE spo2 IS NOT NULL AND spo2 > 0"
        ).fetchall()
        bounds = con.execute(
            "SELECT MIN(ts), MAX(ts) FROM biometrics WHERE heart_rate > 0"
        ).fetchone()
        con.close()
        hours = 0.0
        if bounds and bounds[0] and bounds[1]:
            hours = (bounds[1] - bounds[0]) / 3600.0
        return [r[0] for r in hr_rows], [r[0] for r in spo2_rows], hours
    except Exception as e:
        log.error("DB query baseline data: %s", e)
        return [], [], 0.0


# ── Baseline computation ──────────────────────────────────────────────────────
def _mean_std(values):
    if not values:
        return None, None
    n    = len(values)
    mean = sum(values) / n
    std  = math.sqrt(sum((v - mean) ** 2 for v in values) / max(n - 1, 1))
    return round(mean, 2), round(std, 2)


def compute_confidence(hr_values, hours_collected):
    """
    Returns a confidence score 0.0–1.0.
    This is a personal readiness indicator — not a security gate.
    Target is 0.999 but that takes time to earn.

    Factors:
        40% — sample count (1000 samples = full contribution)
        40% — hours of data collected (72h = full contribution)
        20% — HR std dev stability (lower is better; normal range ~5–15 bpm)
    """
    if not hr_values:
        return 0.0

    sample_factor = min(len(hr_values) / 1000.0, 1.0)
    time_factor   = min(hours_collected / 72.0,   1.0)

    _, std = _mean_std(hr_values)
    if std is None or std == 0:
        stability_factor = 0.0
    else:
        # std of 5 bpm = perfect, 35 bpm = 0
        stability_factor = max(0.0, min(1.0, 1.0 - (std - 5.0) / 30.0))

    confidence = 0.4 * sample_factor + 0.4 * time_factor + 0.2 * stability_factor
    return round(confidence, 4)


def recompute_baseline():
    hr_vals, spo2_vals, hours = db_get_all_biometrics_for_baseline()
    if not hr_vals:
        return None

    hr_mean, hr_std       = _mean_std(hr_vals)
    spo2_mean, spo2_std   = _mean_std(spo2_vals) if spo2_vals else (None, None)
    confidence            = compute_confidence(hr_vals, hours)

    return {
        "computed_at":  time.time(),
        "hr_mean":      hr_mean,
        "hr_std":       hr_std,
        "hr_min":       min(hr_vals),
        "hr_max":       max(hr_vals),
        "spo2_mean":    spo2_mean,
        "spo2_std":     spo2_std,
        "sample_count": len(hr_vals),
        "confidence":   confidence,
    }


# ── Gesture detector ──────────────────────────────────────────────────────────
class GestureDetector:
    """
    Processes accelerometer samples and fires gesture events.

    Gesture vocabulary is ISOLATED by context.
    Iron House editing gestures only fire when mode == "editing".
    Future Raven OS gesture contexts will be defined separately and
    will look different — keep vocabularies isolated.

    Iron House editing gestures:
        1 tap              → EDIT_RIGHT_1     (move cursor right 1)
        2 taps             → EDIT_LEFT_1      (move cursor left 1)
        5 taps             → EDIT_CONFIRM
        Hold 3s (still)    → EDIT_RIGHT_5     (move cursor right 5)
        Hold 5s (still)    → EDIT_LEFT_10     (move cursor left 10)
        Wrist swipe right  → EDIT_SECTION_NEXT
        Wrist swipe left   → EDIT_SECTION_PREV

    Tune thresholds at the top of this file using --test-gestures.
    """

    def __init__(self, on_gesture, test_mode=False):
        self.on_gesture = on_gesture   # callback(gesture_name, raw_xyz_tuple)
        self.test_mode  = test_mode
        self._lock      = threading.Lock()

        # Tap state
        self._in_tap           = False   # currently inside a tap spike
        self._tap_count        = 0
        self._tap_window_start = None

        # Hold state
        self._hold_start = None
        self._hold_fired = False

        # Swipe state
        self._swipe_start = None
        self._swipe_axis  = None

    def feed(self, x_g, y_g, z_g, mode):
        """
        Call with each accelerometer reading (values in g-force).
        mode — current gesture context, e.g. "editing" or "off"
        """
        with self._lock:
            now   = time.time()
            total = math.sqrt(x_g**2 + y_g**2 + z_g**2)
            raw   = (x_g, y_g, z_g)

            # Iron House gestures are only active in editing mode
            if mode != "editing":
                return

            # ── Swipe (X-axis dominant, short burst) ─────────────────────────
            if abs(x_g) >= SWIPE_G:
                if self._swipe_start is None:
                    self._swipe_start = now
                    self._swipe_axis  = "right" if x_g > 0 else "left"
                elif (now - self._swipe_start) * 1000 > SWIPE_MAX_MS:
                    # Spike lasted too long — not a swipe
                    self._swipe_start = None
                    self._swipe_axis  = None
            else:
                if self._swipe_start is not None:
                    elapsed_ms = (now - self._swipe_start) * 1000
                    if elapsed_ms <= SWIPE_MAX_MS:
                        name = ("EDIT_SECTION_NEXT" if self._swipe_axis == "right"
                                else "EDIT_SECTION_PREV")
                        self._fire(name, raw)
                    self._swipe_start = None
                    self._swipe_axis  = None

            # ── Tap (sharp spike, short duration) ────────────────────────────
            if total >= TAP_G:
                if not self._in_tap:
                    self._in_tap = True
                    if self._tap_window_start is None:
                        self._tap_window_start = now
                        self._tap_count = 1
                    else:
                        elapsed = (now - self._tap_window_start) * 1000
                        if elapsed <= TAP_WINDOW_MS:
                            self._tap_count += 1
                        else:
                            # Old window expired — resolve it and start fresh
                            self._resolve_taps(raw)
                            self._tap_window_start = now
                            self._tap_count = 1
            else:
                if self._in_tap:
                    self._in_tap = False  # spike ended
                # Resolve taps if window has expired
                if self._tap_window_start is not None:
                    if (now - self._tap_window_start) * 1000 > TAP_WINDOW_MS:
                        self._resolve_taps(raw)

            # ── Hold (very still for N seconds) ──────────────────────────────
            if total < STILL_THRESHOLD:
                if self._hold_start is None:
                    self._hold_start = now
                    self._hold_fired = False
                else:
                    held = now - self._hold_start
                    if held >= HOLD_5S and not self._hold_fired:
                        self._fire("EDIT_LEFT_10", raw)
                        self._hold_fired = True
                    elif held >= HOLD_3S and not self._hold_fired:
                        self._fire("EDIT_RIGHT_5", raw)
                        self._hold_fired = True
            else:
                self._hold_start = None
                self._hold_fired = False

    def _resolve_taps(self, raw):
        count = self._tap_count
        self._tap_count        = 0
        self._tap_window_start = None

        mapping = {
            1: "EDIT_RIGHT_1",
            2: "EDIT_LEFT_1",
            5: "EDIT_CONFIRM",
        }
        if count in mapping:
            self._fire(mapping[count], raw)
        # Counts 3 and 4 have no mapping yet — silently ignored

    def _fire(self, name, raw):
        if self.test_mode:
            ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            print(f"\n[{ts}] GESTURE  {name:<22}  "
                  f"x={raw[0]:+.3f}g  y={raw[1]:+.3f}g  z={raw[2]:+.3f}g")
        self.on_gesture(name, raw)


# ── REST API ──────────────────────────────────────────────────────────────────
class RingAPIHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass  # suppress default per-request logging

    def _json(self, code, data):
        body = json.dumps(data, indent=2).encode()
        self.send_response(code)
        self.send_header("Content-Type",  "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        qs     = parse_qs(parsed.query)
        snap   = state.snapshot()

        if parsed.path == "/status":
            self._json(200, {
                "connected":    snap["connected"],
                "battery":      snap["battery"],
                "last_seen":    snap["last_seen"],
                "gesture_mode": snap["gesture_mode"],
            })

        elif parsed.path == "/biometrics/latest":
            self._json(200, {
                "ts":         snap["last_seen"],
                "heart_rate": snap["heart_rate"],
                "spo2":       snap["spo2"],
                "steps":      snap["steps"],
                "battery":    snap["battery"],
            })

        elif parsed.path == "/biometrics/history":
            hours = int(qs.get("hours", ["24"])[0])
            self._json(200, db_get_biometrics_last_n_hours(hours))

        elif parsed.path == "/baseline":
            b = snap["baseline"]
            self._json(200, {**b, "confidence": snap["confidence"]})

        elif parsed.path == "/gesture/latest":
            self._json(200, {
                "gesture": snap["latest_gesture"],
                "ts":      snap["latest_gesture_ts"],
                "mode":    snap["gesture_mode"],
            })

        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if urlparse(self.path).path == "/ingest":
            # Receive biometrics from raven_ring_bridge.py running on phone
            length = int(self.headers.get("Content-Length", 0))
            try:
                data    = json.loads(self.rfile.read(length))
                ts      = data.get("ts") or time.time()
                hr      = data.get("heart_rate")
                spo2    = data.get("spo2")
                steps   = data.get("steps")
                battery = data.get("battery")
                state.update_biometrics(hr=hr, spo2=spo2, steps=steps, battery=battery)
                db_insert_biometric(ts, hr, spo2, steps, battery)
                log.info("Ingest — HR=%s SpO2=%s Steps=%s Battery=%s", hr, spo2, steps, battery)
                self._json(200, {"status": "ok", "ts": ts})
            except Exception as e:
                self._json(400, {"error": str(e)})

        elif urlparse(self.path).path == "/gesture/mode":
            length = int(self.headers.get("Content-Length", 0))
            try:
                data = json.loads(self.rfile.read(length))
                mode = data.get("mode", "off")
                if mode not in ("editing", "off"):
                    self._json(400, {"error": "mode must be 'editing' or 'off'"})
                    return
                with state._lock:
                    state.gesture_mode = mode
                log.info("Gesture mode → %s", mode)
                self._json(200, {"mode": mode})
            except Exception as e:
                self._json(400, {"error": str(e)})
        else:
            self._json(404, {"error": "not found"})


def start_api_server():
    server = HTTPServer((API_HOST, API_PORT), RingAPIHandler)
    log.info("Ring API on %s:%d", API_HOST, API_PORT)
    server.serve_forever()


# ── BLE ring loop ─────────────────────────────────────────────────────────────
async def ring_loop(test_mode=False):
    """
    Connects to the COLMI R02 ring and polls biometrics every 30 seconds.
    Reconnects automatically if the connection drops.

    colmi_r02_client v0.1.0 API (Client class):
        get_realtime_heart_rate() → list[int] | None   (collects ~6 readings)
        get_realtime_spo2()       → list[int] | None
        get_battery()             → BatteryInfo(battery_level: int, charging: bool)
        get_heart_rate_log()      → HeartRateLog | NoData  (historical, today)
        get_device_info()         → dict(hw_version, fw_version)
        set_time(datetime)        → None
        blink_twice()             → None

    NOTE: This library version has no accelerometer access.
    Gesture detection via ring accelerometer is not possible with v0.1.0.
    The gesture framework remains in place — wire it up if a future version
    or alternative BLE approach exposes accelerometer data.

    Steps: the library has the packet defined but no Client method.
    We send GET_TODAY_STEPS_PACKET manually and read from the queue.
    """
    while True:
        log.info("Connecting to ring %s ...", RING_ADDRESS)
        try:
            async with RingClient(address=RING_ADDRESS) as client:
                log.info("Ring connected")
                state.connected = True
                last_baseline_save = time.time()
                poll_count = 0

                while True:
                    ts = time.time()

                    # ── Heart rate (realtime, collects up to 6 readings) ──────
                    hr_val = None
                    try:
                        readings = await client.get_realtime_heart_rate()
                        if readings:
                            # Filter zeros, take last valid reading
                            valid = [r for r in readings if r > 0]
                            hr_val = valid[-1] if valid else None
                    except Exception as e:
                        log.debug("HR read: %s", e)

                    # ── SpO2 (every other poll to avoid back-to-back timeouts) ─
                    spo2_val = None
                    if poll_count % 2 == 0:
                        try:
                            readings = await client.get_realtime_spo2()
                            if readings:
                                valid = [r for r in readings if r > 0]
                                spo2_val = valid[-1] if valid else None
                        except Exception as e:
                            log.debug("SpO2 read: %s", e)

                    # ── Battery ───────────────────────────────────────────────
                    battery_val = None
                    try:
                        info = await client.get_battery()
                        battery_val = info.battery_level
                        if test_mode:
                            print(f"  charging={info.charging}", end="")
                    except Exception as e:
                        log.debug("Battery read: %s", e)

                    # ── Steps (manual packet — parser holds state in COMMAND_HANDLERS) ──
                    steps_val = None
                    try:
                        from colmi_r02_client.client import COMMAND_HANDLERS
                        parser = COMMAND_HANDLERS[ring_steps.CMD_GET_STEP_SOMEDAY].__self__
                        parser.reset()
                        await client.send_packet(ring_steps.GET_TODAY_STEPS_PACKET)
                        await asyncio.sleep(3)   # wait for all step packets to arrive
                        if parser.details:
                            steps_val = sum(d.steps for d in parser.details)
                    except Exception as e:
                        log.debug("Steps read: %s", e)

                    state.update_biometrics(
                        hr=hr_val, spo2=spo2_val,
                        steps=steps_val, battery=battery_val
                    )
                    db_insert_biometric(ts, hr_val, spo2_val, steps_val, battery_val)

                    if test_mode:
                        print(f"\n  HR={hr_val}  SpO2={spo2_val}  "
                              f"Steps={steps_val}  Battery={battery_val}%  "
                              f"Confidence={state.confidence:.4f}")

                    # ── Hourly baseline snapshot ───────────────────────────────
                    if time.time() - last_baseline_save >= BASELINE_INTERVAL_S:
                        snap = recompute_baseline()
                        if snap:
                            db_insert_baseline(snap)
                            with state._lock:
                                state.baseline   = snap
                                state.confidence = snap["confidence"]
                            log.info(
                                "Baseline — HR %.1f±%.1f bpm  SpO2 %.1f%%  "
                                "n=%d  CONFIDENCE: %.4f (%.1f%%)",
                                snap["hr_mean"] or 0,   snap["hr_std"] or 0,
                                snap["spo2_mean"] or 0, snap["sample_count"],
                                snap["confidence"],     snap["confidence"] * 100,
                            )
                        last_baseline_save = time.time()

                    poll_count += 1
                    await asyncio.sleep(POLL_INTERVAL_S)

        except Exception as e:
            log.warning("Ring disconnected or error: %s — retry in 10s", e)
            state.set_disconnected()
            await asyncio.sleep(10)


# ── Gesture event handler ─────────────────────────────────────────────────────
def on_gesture(name, raw):
    ctx = state.gesture_mode
    ts  = time.time()
    state.record_gesture(name, ctx)
    db_insert_gesture(ts, name, ctx)
    log.info("Gesture: %s  (ctx=%s)", name, ctx)


# ── Baseline background refresh ───────────────────────────────────────────────
def baseline_refresh_loop():
    """
    Keeps state.confidence current between hourly DB saves.
    Runs every 5 minutes. Prints confidence score.
    """
    while True:
        time.sleep(300)
        snap = recompute_baseline()
        if snap:
            with state._lock:
                state.baseline   = snap
                state.confidence = snap["confidence"]
            log.info(
                "Confidence: %.4f (%.1f%%)  n=%d",
                snap["confidence"], snap["confidence"] * 100, snap["sample_count"]
            )


# ── Hook point for second ring (future) ──────────────────────────────────────
# To add a second ring, add another RingState and ring_loop here.
# The "hold hands" consent mechanic (two rings simultaneously present) will
# live in a separate module — raven_ring_consent.py or similar.
# Nothing to wire up yet. This comment is the hook.


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Raven Ring daemon")
    parser.add_argument(
        "--test-gestures", action="store_true",
        help="Print detected gestures and raw accel values in real time for calibration"
    )
    args = parser.parse_args()

    if args.test_gestures:
        print("=" * 60)
        print("  GESTURE TEST MODE — Iron House editing context")
        print("  Editing mode is ON. Ctrl+C to stop.")
        print("  Tap, hold, swipe — watch the raw values.")
        print("  Adjust TAP_G, SWIPE_G, etc. at the top of this file.")
        print("=" * 60)
        print()
        with state._lock:
            state.gesture_mode = "editing"

    init_db()

    # Load last known baseline
    snap = recompute_baseline()
    if snap:
        with state._lock:
            state.baseline   = snap
            state.confidence = snap["confidence"]
        log.info(
            "Loaded baseline — confidence: %.4f (%.1f%%)  n=%d",
            snap["confidence"], snap["confidence"] * 100, snap["sample_count"]
        )
    else:
        log.info("No baseline data yet — building from scratch")

    detector = GestureDetector(on_gesture, test_mode=args.test_gestures)

    # API server (skip in test mode — keep output clean)
    if not args.test_gestures:
        threading.Thread(target=start_api_server, daemon=True).start()

    # Baseline refresh
    threading.Thread(target=baseline_refresh_loop, daemon=True).start()

    # BLE loop — this runs forever
    try:
        asyncio.run(ring_loop(test_mode=args.test_gestures))
    except KeyboardInterrupt:
        log.info("Stopped.")


if __name__ == "__main__":
    main()
