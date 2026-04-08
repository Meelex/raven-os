import os
import time
import hashlib

# --- CONFIGURATION ---
QUARANTINE_DIR = "/home/raven/quarantine_zone"
LEDGER_FILE = "/home/raven/hash_ledger.txt"
SIGNAL_FILE = "/home/raven/THREAT_SIGNAL.txt"

os.makedirs(QUARANTINE_DIR, exist_ok=True)

def get_sha256(filepath):
    sha256_hash = hashlib.sha256()
    with open(filepath, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()

print("[*] RAVEN QUARANTINE ENGINE: MONITORING...")

processed_files = set()

while True:
    for filename in os.listdir(QUARANTINE_DIR):
        if filename.endswith(".bat") and filename not in processed_files:
            filepath = os.path.join(QUARANTINE_DIR, filename)
            time.sleep(1) # Wait for SCP to finish
            
            file_hash = get_sha256(filepath)
            
            # 1. Log to the permanent ledger
            with open(LEDGER_FILE, "a") as ledger:
                ledger.write(f"[{time.ctime()}] {filename} | {file_hash}\n")
            
            # 2. SIGNAL THE SCREEN (This is the magic part)
            with open(SIGNAL_FILE, "w") as f:
                f.write(f"THREAT: {filename}\nHASH: {file_hash[:12]}")
            
            print(f"[!] CAUGHT & SIGNALED: {filename}")
            processed_files.add(filename)
            
    time.sleep(2)