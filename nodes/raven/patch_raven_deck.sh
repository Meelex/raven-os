#!/bin/bash
# ============================================================
# patch_raven_deck.sh
# Run on Raven (Pi Zero 2).
# Patches raven_deck.py with:
#   1. Tap to dismiss threat interrupt instantly
#   2. Seen flag — already-shown threats skip interrupt on reboot
#   3. Test unlock trigger from MAIN screen (hold both sides)
# ============================================================

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

log()  { echo -e "${GREEN}[✓]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
err()  { echo -e "${RED}[✗]${NC} $1"; exit 1; }
info() { echo -e "${CYAN}[→]${NC} $1"; }

DECK="$HOME/raven_deck.py"

echo ""
echo "================================================"
echo "  Raven OS — Deck Patch"
echo "  Tap-to-dismiss + Seen flag + Unlock test"
echo "================================================"
echo ""

[ -f "$DECK" ] || err "raven_deck.py not found at $DECK"

info "Backing up..."
cp "$DECK" "${DECK}.bak"
log "Backup saved to ${DECK}.bak"

info "Applying patch..."

python3 - << 'PYEOF'
import sys

with open("/home/raven/raven_deck.py", "r") as f:
    content = f.read()

# ── 1. Add seen_threats set and test_unlock flag to globals ────
old_globals = """# --- UNLOCK QUEUE ---
DUAT_IP   = "192.168.12.231"   # Update to Duat's IP when Pi 5 is set up
DUAT_PORT = 6176
UNLOCK_API_PORT = 6175

unlock_queue = []        # list of alert dicts waiting for decision
unlock_index = 0         # current selection in UNLOCK_QUEUE page
active_unlock = None     # alert currently shown in UNLOCK_CONFIRM"""

new_globals = """# --- UNLOCK QUEUE ---
DUAT_IP   = "192.168.12.231"
DUAT_PORT = 6176
UNLOCK_API_PORT = 6175

unlock_queue = []        # list of alert dicts waiting for decision
unlock_index = 0         # current selection in UNLOCK_QUEUE page
active_unlock = None     # alert currently shown in UNLOCK_CONFIRM

# --- SEEN THREATS ---
# Threats shown before reboot are marked seen so they skip the
# 30s interrupt and just show a summary count on MAIN instead.
SEEN_FILE = "/home/raven/.raven/seen_threats.json"
seen_threats = set()

def load_seen_threats():
    global seen_threats
    import json, os
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE) as f:
                seen_threats = set(json.load(f))
        except Exception:
            seen_threats = set()

def save_seen_threat(filename):
    import json, os
    seen_threats.add(filename)
    os.makedirs(os.path.dirname(SEEN_FILE), exist_ok=True)
    try:
        with open(SEEN_FILE, "w") as f:
            json.dump(list(seen_threats), f)
    except Exception:
        pass

load_seen_threats()"""

content = content.replace(old_globals, new_globals, 1)

# ── 2. Update trigger_threat_alert to check seen flag ─────────
old_trigger = """def trigger_threat_alert(filename, file_hash):
    \"\"\"Directly activate the threat display from the quarantine thread.\"\"\"
    global threat_active, threat_text, threat_timer, threats_caught, last_threat_name

    # If a threat is already being displayed, wait for it to clear
    while threat_active:
        time.sleep(1)

    threat_text = f"THREAT: {filename}\\nHASH: {file_hash[:12]}"
    threats_caught += 1
    last_threat_name = filename
    threat_timer = time.time()
    threat_active = True"""

new_trigger = """def trigger_threat_alert(filename, file_hash):
    \"\"\"Directly activate the threat display from the quarantine thread.\"\"\"
    global threat_active, threat_text, threat_timer, threats_caught, last_threat_name

    threats_caught += 1
    last_threat_name = filename

    # Already seen before reboot — skip interrupt, just count it
    if filename in seen_threats:
        print(f"[*] Seen threat skipped (already shown): {filename}")
        return

    # If a threat is already being displayed, wait for it to clear
    while threat_active:
        time.sleep(1)

    threat_text = f"THREAT: {filename}\\nHASH: {file_hash[:12]}"
    threat_timer = time.time()
    threat_active = True
    save_seen_threat(filename)"""

content = content.replace(old_trigger, new_trigger, 1)

# ── 3. Update Flask alert receiver to check seen flag ─────────
old_flask_alert = """        unlock_queue.append(alert)

        # Trigger threat interrupt on display
        fname = alert["filename"]
        verdict = alert["verdict"].upper()
        threat_text = f"FILE LOCKED\\n{fname[:22]}\\n{verdict}"
        threats_caught += 1
        last_threat_name = fname
        threat_timer = time.time()
        threat_active = True"""

new_flask_alert = """        unlock_queue.append(alert)

        # Trigger threat interrupt on display (skip if already seen)
        fname = alert["filename"]
        verdict = alert["verdict"].upper()
        threats_caught += 1
        last_threat_name = fname

        if fname not in seen_threats:
            threat_text = f"FILE LOCKED\\n{fname[:22]}\\n{verdict}"
            threat_timer = time.time()
            threat_active = True
            save_seen_threat(fname)
        else:
            print(f"[*] Seen alert skipped: {fname}")"""

content = content.replace(old_flask_alert, new_flask_alert, 1)

# ── 4. Add tap-to-dismiss to threat interrupt block ───────────
old_interrupt = """        if threat_active:
            # Check if the 30-second display has expired
            if time.time() - threat_timer >= THREAT_DISPLAY_SECONDS:
                clear_threat_signal()
            else:
                # Render the threat alert screen (overrides everything)
                draw.rectangle((0, 0, 250, 122), fill=0)  # Black out screen
                draw.text((5, 2), "!!! THREAT DETECTED !!!", fill=255)
                # Split threat_text into lines for display
                threat_lines = threat_text.split("\\n")
                for i, line in enumerate(threat_lines[:4]):
                    draw.text((10, 30 + (i * 18)), line[:28], fill=255)

                # Countdown timer
                remaining = int(THREAT_DISPLAY_SECONDS - (time.time() - threat_timer))
                draw.text((10, 105), f"Auto-clear in {remaining}s", fill=255)

                epd.displayPartial(epd.getbuffer(canvas))
                time.sleep(1)  # Update once per second during threat
                continue  # Skip all normal input/rendering"""

new_interrupt = """        if threat_active:
            # Check if the 30-second display has expired
            if time.time() - threat_timer >= THREAT_DISPLAY_SECONDS:
                clear_threat_signal()
            else:
                # Check for tap-to-dismiss
                gt.GT_Scan(GT_Dev, GT_Old)
                if GT_Dev.TouchpointFlag:
                    GT_Dev.TouchpointFlag = 0
                    current_time = time.time()
                    if current_time - last_tap_time > 0.4:
                        last_tap_time = current_time
                        clear_threat_signal()
                        continue

                # Render the threat alert screen (overrides everything)
                draw.rectangle((0, 0, 250, 122), fill=0)  # Black out screen
                draw.text((5, 2), "!!! THREAT DETECTED !!!", fill=255)
                # Split threat_text into lines for display
                threat_lines = threat_text.split("\\n")
                for i, line in enumerate(threat_lines[:4]):
                    draw.text((10, 30 + (i * 18)), line[:28], fill=255)

                # Tap to dismiss hint + countdown
                remaining = int(THREAT_DISPLAY_SECONDS - (time.time() - threat_timer))
                draw.text((10, 97), "[ TAP ANYWHERE TO DISMISS ]", fill=255)
                draw.text((10, 110), f"Auto-clear in {remaining}s", fill=255)

                epd.displayPartial(epd.getbuffer(canvas))
                time.sleep(0.1)  # Faster loop so tap feels responsive
                continue  # Skip all normal input/rendering"""

content = content.replace(old_interrupt, new_interrupt, 1)

# ── 5. Add seen threat count to MAIN screen display ───────────
old_main_display = """                # Watchdog heartbeat status
                wd_display = watchdog_status
                if watchdog_status == "ONLINE" and watchdog_last_seen > 0:
                    ago = int(time.time() - watchdog_last_seen)
                    wd_display = f"ONLINE ({ago}s ago)"
                draw.text((10, 77), f"WATCHDOG: {wd_display}", fill=0)"""

new_main_display = """                # Watchdog heartbeat status
                wd_display = watchdog_status
                if watchdog_status == "ONLINE" and watchdog_last_seen > 0:
                    ago = int(time.time() - watchdog_last_seen)
                    wd_display = f"ONLINE ({ago}s ago)"
                draw.text((10, 77), f"WATCHDOG: {wd_display}", fill=0)

                # Seen threats summary (skipped on reboot)
                if len(seen_threats) > 0:
                    draw.text((10, 90), f"SEEN: {len(seen_threats)} prior threats", fill=0)"""

content = content.replace(old_main_display, new_main_display, 1)

# ── 6. Add test unlock trigger to MAIN touch handler ──────────
# Left tap on MAIN triggers a test unlock alert
old_main_touch = """            if ui_page == "MAIN":
                if is_right_tap:
                    ui_page = "MENU"
                    menu_index = 0"""

new_main_touch = """            if ui_page == "MAIN":
                if is_right_tap:
                    ui_page = "MENU"
                    menu_index = 0
                elif is_left_tap:
                    # Left tap on MAIN = inject test unlock alert
                    # Use this to verify Raven → Duat → Horus chain
                    test_alert = {
                        "filename":    "raven_unlock_test.txt",
                        "filepath":    "C:/Users/YourUser/Downloads/raven_unlock_test.txt",
                        "verdict":     "test",
                        "threat_name": "Manual chain test",
                        "hash":        "0000000000000000",
                    }
                    unlock_queue.append(test_alert)
                    threat_text = "UNLOCK TEST\\nraven_unlock_test.txt\\nTEST ALERT"
                    threat_timer = time.time()
                    threat_active = True
                    current_status = "TEST: Unlock alert queued"
                    print("[*] Test unlock alert injected")\n"""

content = content.replace(old_main_touch, new_main_touch, 1)

with open("/home/raven/raven_deck.py", "w") as f:
    f.write(content)

print("Patch applied.")
PYEOF

if [ $? -ne 0 ]; then
    warn "Patch failed — restoring backup"
    cp "${DECK}.bak" "$DECK"
    err "Restore complete."
fi

# ── Verify syntax ──────────────────────────────────────────────
info "Verifying syntax..."
python3 -c "import ast; ast.parse(open('$DECK').read()); print('Syntax OK')"
if [ $? -ne 0 ]; then
    warn "Syntax error — restoring backup"
    cp "${DECK}.bak" "$DECK"
    err "Restore complete."
fi

log "Patch applied successfully"

echo ""
echo "================================================"
echo "  PATCH COMPLETE"
echo "================================================"
echo ""
log "Tap to dismiss: any touch clears threat interrupt"
log "Seen flag: prior threats skip interrupt on reboot"
log "Test unlock: left tap on MAIN injects test alert"
echo ""
warn "Restart raven_deck.py to apply changes:"
echo "  sudo pkill -f raven_deck.py"
echo "  python3 ~/raven_deck.py"
echo "================================================"
echo ""
