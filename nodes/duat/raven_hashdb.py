#!/usr/bin/env python3
"""
raven_hashdb.py - Raven OS Hash Intelligence Service
Runs on Duat (Pi 5). Pulls threat feeds, serves lookup API.
"""

import os, csv, json, time, hashlib, logging, sqlite3, zipfile, threading, io, ssl
from datetime import datetime
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.request import urlopen, Request
from urllib.error import URLError

BASE_DIR  = Path.home() / ".raven" / "hashdb"
DB_PATH   = BASE_DIR / "hashes.db"
LOG_PATH  = BASE_DIR / "hashdb.log"
HOST      = "0.0.0.0"
PORT      = 6174
UPDATE_HOURS = 24
BASE_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler()])
log = logging.getLogger("raven-hashdb")

FEEDS = [
    {"name": "MalwareBazaar Recent", "url": "https://bazaar.abuse.ch/export/csv/recent/", "type": "bazaar_raw_csv"},
    {"name": "URLhaus Payloads", "url": "https://urlhaus.abuse.ch/downloads/payloads/", "type": "skip"},
]
CIRCL_API = "https://hashlookup.circl.lu/lookup"
BAZAAR_SUBMIT = "https://mb-api.abuse.ch/api/v1/"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS hashes (
        hash TEXT PRIMARY KEY, hash_type TEXT NOT NULL,
        threat_name TEXT, source TEXT, first_seen TEXT,
        added_at TEXT DEFAULT (datetime('now')))""")
    c.execute("""CREATE TABLE IF NOT EXISTS unknown_flags (
        hash TEXT PRIMARY KEY, filename TEXT,
        flagged_at TEXT DEFAULT (datetime('now')),
        submitted INTEGER DEFAULT 0, submit_result TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS feed_stats (
        feed_name TEXT PRIMARY KEY, last_update TEXT,
        hash_count INTEGER DEFAULT 0, status TEXT)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_hash ON hashes(hash)")
    conn.commit(); conn.close()
    log.info(f"Database ready at {DB_PATH}")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def db_stats():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) as total FROM hashes")
    total = c.fetchone()["total"]
    c.execute("SELECT COUNT(*) as u FROM unknown_flags WHERE submitted=0")
    unknown = c.fetchone()["u"]
    conn.close()
    return {"total_hashes": total, "unknown_pending": unknown}

def fetch_url(url, timeout=120):
    req = Request(url, headers={"User-Agent": "RavenOS-Duat/1.0"})
    with urlopen(req, timeout=timeout) as r:
        return r.read()

def parse_malwarebazaar_csv(data, source):
    hashes = []
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            csv_name = [n for n in z.namelist() if n.endswith(".csv")][0]
            with z.open(csv_name) as f:
                reader = csv.DictReader(
                    line.decode("utf-8", errors="replace")
                    for line in f if not line.startswith(b"#"))
                for row in reader:
                    sha256 = row.get("sha256_hash","").strip().lower()
                    name   = row.get("signature", row.get("file_name","unknown"))
                    seen   = row.get("first_seen","")
                    if sha256 and len(sha256)==64:
                        hashes.append((sha256,"sha256",name,source,seen))
    except Exception as e:
        log.error(f"Parse error: {e}")
    return hashes

def parse_bazaar_raw_csv(data, source):
    """Parse MalwareBazaar raw CSV.
    Format: "date", "sha256", "md5", "sha1", "reporter", "filename", ...
    Lines starting with # are comments.
    """
    import csv, io
    hashes = []
    try:
        text = data.decode("utf-8", errors="replace")
        reader = csv.reader(
            (l for l in text.splitlines() if l and not l.startswith("#")),
            skipinitialspace=True
        )
        for row in reader:
            if len(row) < 3:
                continue
            seen   = row[0].strip()
            sha256 = row[1].strip().lower()
            md5    = row[2].strip().lower()
            name   = row[5].strip() if len(row) > 5 else "unknown"
            if sha256 and len(sha256) == 64:
                hashes.append((sha256, "sha256", name, source, seen))
            if md5 and len(md5) == 32:
                hashes.append((md5, "md5", name, source, seen))
    except Exception as e:
        log.error(f"Bazaar parse error: {e}")
    return hashes

def parse_bazaar_raw_csv(data, source):
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
    return hashes

def update_feed(feed):
    log.info(f"Updating: {feed['name']}")
    try:
        data = fetch_url(feed["url"])
        if feed["type"] == "skip":
            return
        elif feed["type"] == "malwarebazaar_csv_zip":
            hashes = parse_malwarebazaar_csv(data, feed["name"])
        elif feed["type"] == "bazaar_raw_csv":
            hashes = parse_bazaar_raw_csv(data, feed["name"])
        elif feed["type"] == "urlhaus_csv":
            hashes = parse_urlhaus_csv(data, feed["name"])
        else:
            return
        if not hashes:
            return
        conn = get_db()
        conn.executemany("""INSERT OR REPLACE INTO hashes
            (hash,hash_type,threat_name,source,first_seen) VALUES (?,?,?,?,?)""", hashes)
        conn.execute("""INSERT OR REPLACE INTO feed_stats
            (feed_name,last_update,hash_count,status) VALUES (?,datetime('now'),?,'ok')""",
            (feed["name"], len(hashes)))
        conn.commit(); conn.close()
        log.info(f"Imported {len(hashes):,} hashes from {feed['name']}")
    except Exception as e:
        log.error(f"Feed update failed {feed['name']}: {e}")

def update_all_feeds():
    log.info("Feed update cycle starting...")
    for feed in FEEDS:
        update_feed(feed)
        time.sleep(5)
    log.info(f"Done. DB: {db_stats()['total_hashes']:,} hashes")

def feed_loop():
    while True:
        update_all_feeds()
        time.sleep(UPDATE_HOURS * 3600)

def lookup_local(h):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM hashes WHERE hash=?", (h,))
    row = c.fetchone(); conn.close()
    if row:
        return {"found":True,"verdict":"malicious","threat_name":row["threat_name"],
                "source":row["source"],"first_seen":row["first_seen"]}
    return None

def lookup_circl(h):
    if len(h)==32: ep=f"{CIRCL_API}/md5/{h}"
    elif len(h)==40: ep=f"{CIRCL_API}/sha1/{h}"
    elif len(h)==64: ep=f"{CIRCL_API}/sha256/{h}"
    else: return None
    try:
        with urlopen(Request(ep, headers={"User-Agent":"RavenOS-Duat/1.0"}), timeout=10) as r:
            data = json.loads(r.read())
            if "message" in data and "not found" in data["message"].lower():
                return {"found":False,"verdict":"unknown","source":"circl"}
            return {"found":True,"verdict":"known_clean",
                    "threat_name":data.get("FileName","unknown"),"source":"circl"}
    except Exception:
        return None

def flag_unknown(h, filename):
    conn = get_db()
    conn.execute("INSERT OR IGNORE INTO unknown_flags (hash,filename) VALUES (?,?)", (h,filename))
    conn.commit(); conn.close()

def lookup_hash(h, filename="unknown"):
    h = h.strip().lower()
    local = lookup_local(h)
    if local: return local
    circl = lookup_circl(h)
    if circl and circl.get("verdict") == "known_clean": return circl
    flag_unknown(h, filename)
    return {"found":False,"verdict":"unknown","hash":h,"filename":filename,
            "message":"Not found. Flagged for review."}

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def send_json(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self.send_json(200, {"status":"ok","device":"duat","db_stats":db_stats()})
        elif self.path.startswith("/lookup/"):
            h = self.path.split("/lookup/")[-1].split("?")[0].strip()
            filename = "unknown"
            if "?" in self.path:
                for p in self.path.split("?")[1].split("&"):
                    if p.startswith("filename="): filename = p.split("=",1)[1]
            result = lookup_hash(h, filename)
            log.info(f"Lookup: {h[:16]}... → {result['verdict']}")
            self.send_json(200, result)
        elif self.path == "/stats":
            conn = get_db()
            feeds = [dict(r) for r in conn.execute("SELECT * FROM feed_stats").fetchall()]
            conn.close()
            self.send_json(200, {"db": db_stats(), "feeds": feeds})
        else:
            self.send_json(404, {"error":"not found"})

if __name__ == "__main__":
    log.info("Duat Hash DB Service starting...")
    init_db()
    threading.Thread(target=feed_loop, daemon=True).start()
    server = HTTPServer((HOST, PORT), Handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain("/home/duat/duat/certs/duat.crt", "/home/duat/duat/certs/duat.key")
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    log.info(f"Listening on port {PORT} (HTTPS)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Stopped.")
