#!/bin/bash
# ============================================================
# fix_hashdb_parser.sh
# Run on Duat. Fixes MalwareBazaar CSV parser and restarts
# the hash DB service. No manual editing required.
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

HASHDB="$HOME/duat/raven_hashdb.py"

echo ""
echo "================================================"
echo "  Raven OS — Fix Hash DB Parser"
echo "================================================"
echo ""

[ -f "$HASHDB" ] || err "raven_hashdb.py not found at $HASHDB"

# ── Backup original ────────────────────────────────────────────
info "Backing up original..."
cp "$HASHDB" "${HASHDB}.bak"
log "Backup saved to ${HASHDB}.bak"

# ── Rewrite the file with fixed parser ────────────────────────
info "Applying fixed parser..."

python3 - << PYEOF
import re

with open("$HASHDB", "r") as f:
    content = f.read()

# Fix FEEDS list - both use raw CSV, URLhaus skipped for now (730MB)
old_feeds = '''FEEDS = [
    {"name": "MalwareBazaar Recent", "url": "https://bazaar.abuse.ch/export/csv/recent/", "type": "urlhaus_csv"},
    {"name": "URLhaus Payloads", "url": "https://urlhaus.abuse.ch/downloads/payloads/", "type": "urlhaus_csv"},
]'''

new_feeds = '''FEEDS = [
    {"name": "MalwareBazaar Recent", "url": "https://bazaar.abuse.ch/export/csv/recent/", "type": "bazaar_raw_csv"},
]
# URLhaus skipped — 730MB zip, needs separate parser (backlog)'''

content = content.replace(old_feeds, new_feeds)

# Fix parse_urlhaus_csv to handle positional (no-header) CSV
old_parser = '''def parse_urlhaus_csv(data, source):
    hashes = []
    try:
        text = data.decode("utf-8", errors="replace")
        reader = csv.DictReader(l for l in text.splitlines() if not l.startswith("#"))
        for row in reader:
            sha256 = row.get("sha256_hash","").strip().lower()
            seen   = row.get("firstseen","")
            if sha256 and len(sha256)==64:
                hashes.append((sha256,"sha256","urlhaus_payload",source,seen))
    except Exception as e:
        log.error(f"URLhaus parse error: {e}")
    return hashes'''

new_parser = '''def parse_bazaar_raw_csv(data, source):
    """Parse MalwareBazaar raw CSV (no header row, positional columns).
    Columns: date, sha256, md5, sha1, reporter, filename, filetype, ...
    """
    hashes = []
    try:
        text = data.decode("utf-8", errors="replace")
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # Strip surrounding quotes and split on ","
            parts = [p.strip().strip('"') for p in line.split('","')]
            if len(parts) < 2:
                continue
            seen   = parts[0] if len(parts) > 0 else ""
            sha256 = parts[1].strip().lower() if len(parts) > 1 else ""
            md5    = parts[2].strip().lower() if len(parts) > 2 else ""
            name   = parts[5].strip() if len(parts) > 5 else "unknown"
            if sha256 and len(sha256) == 64:
                hashes.append((sha256, "sha256", name, source, seen))
            if md5 and len(md5) == 32:
                hashes.append((md5, "md5", name, source, seen))
    except Exception as e:
        log.error(f"Bazaar parse error: {e}")
    return hashes

def parse_urlhaus_csv(data, source):
    hashes = []
    try:
        text = data.decode("utf-8", errors="replace")
        reader = csv.DictReader(l for l in text.splitlines() if not l.startswith("#"))
        for row in reader:
            sha256 = row.get("sha256_hash","").strip().lower()
            seen   = row.get("firstseen","")
            if sha256 and len(sha256)==64:
                hashes.append((sha256,"sha256","urlhaus_payload",source,seen))
    except Exception as e:
        log.error(f"URLhaus parse error: {e}")
    return hashes'''

content = content.replace(old_parser, new_parser)

# Fix update_feed to handle new type
old_update = '''        if feed["type"] == "malwarebazaar_csv_zip":
            hashes = parse_malwarebazaar_csv(data, feed["name"])
        elif feed["type"] == "urlhaus_csv":
            hashes = parse_urlhaus_csv(data, feed["name"])'''

new_update = '''        if feed["type"] == "bazaar_raw_csv":
            hashes = parse_bazaar_raw_csv(data, feed["name"])
        elif feed["type"] == "malwarebazaar_csv_zip":
            hashes = parse_malwarebazaar_csv(data, feed["name"])
        elif feed["type"] == "urlhaus_csv":
            hashes = parse_urlhaus_csv(data, feed["name"])'''

content = content.replace(old_update, new_update)

with open("$HASHDB", "w") as f:
    f.write(content)

print("Parser fix applied.")
PYEOF

if [ $? -ne 0 ]; then
    warn "Python edit failed — restoring backup"
    cp "${HASHDB}.bak" "$HASHDB"
    err "Restore complete. Check error above."
fi

log "Parser fixed"

# ── Verify syntax ──────────────────────────────────────────────
info "Verifying syntax..."
source "$HOME/duat/venv/bin/activate"
python3 -c "import ast; ast.parse(open('$HASHDB').read()); print('Syntax OK')"
if [ $? -ne 0 ]; then
    warn "Syntax error — restoring backup"
    cp "${HASHDB}.bak" "$HASHDB"
    err "Restore complete."
fi

# ── Restart service ────────────────────────────────────────────
info "Restarting duat-hashdb service..."
sudo systemctl restart duat-hashdb
sleep 15

# ── Check stats ────────────────────────────────────────────────
info "Checking hash count..."
STATS=$(curl -s http://localhost:6174/stats)
echo "$STATS"

TOTAL=$(echo "$STATS" | python3 -c "import sys,json; print(json.load(sys.stdin)['db']['total_hashes'])" 2>/dev/null)

echo ""
echo "================================================"
if [ "$TOTAL" -gt "0" ] 2>/dev/null; then
    log "Hash DB loaded: $TOTAL hashes"
    echo -e "  ${GREEN}✓ Parser fix successful.${NC}"
else
    warn "Still 0 hashes after 15s — feed may still be downloading"
    warn "Check with: sudo journalctl -u duat-hashdb -f"
fi
echo "================================================"
echo ""
