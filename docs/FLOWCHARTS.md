# System Flowcharts

Visual reference for the key flows in Raven OS. Rendered automatically on GitHub.

---

## File Threat Detection and Lock Flow

```mermaid
flowchart TD
    A[File downloaded\nto Downloads folder] --> B[Watchdog detects new file\nFileSystemWatcher]
    B --> C[Compute SHA256 hash]
    C --> D{Query Duat\nHash DB :6174}
    D -->|CLEAN| E[Log entry\nNo action]
    D -->|UNKNOWN| F[Log entry\nSubmit hash anonymously\nto MalwareBazaar]
    D -->|MALICIOUS| G[POST lock request\nto Duat :6176]
    G --> H[Duat SSHes into PC\nicacls /deny user:RX]
    H --> I[File locked at OS level]
    I --> J[Duat forwards alert\nto Raven :6175]
    J --> K[Raven e-ink\nTHREAT DETECTED screen]
    K --> L{User taps\nleft on Raven}
    L --> M[Alert dismissed\nFile enters UNLOCK QUEUE\nSEEN flag set]
```

---

## Physical Unlock / Deny Flow

```mermaid
flowchart TD
    A[File in UNLOCK QUEUE\non Raven e-ink] --> B[Navigate:\nMENU → UNLOCK QUEUE]
    B --> C[Select file\nright tap]
    C --> D[UNLOCK CONFIRM screen]
    D --> E{User decision}
    E -->|Left tap\nUNLOCK| F[Raven POSTs UNLOCK\nto Duat :6176]
    E -->|Right tap\nDENY| G[Raven POSTs DENY\nto Duat :6176]
    F --> H[Duat SSHes into PC\nicacls /grant user:F]
    H --> I[File accessible again]
    G --> J[File remains locked\nLogged as DENIED]
```

---

## Hash DB Lookup Flow

```mermaid
flowchart TD
    A[Receive hash query\nfrom Watchdog] --> B{Check local\nSQLite cache}
    B -->|Cache hit| C[Return cached verdict\nCLEAN / MALICIOUS]
    B -->|Cache miss| D{Query MalwareBazaar\ndaily feed loaded?}
    D -->|Yes - check feed| E{Hash in\nMalwareBazaar?}
    E -->|Yes| F[Return MALICIOUS\nCache result]
    E -->|No| G[Query CIRCL\nhashlookup live API]
    G --> H{CIRCL result}
    H -->|Known malware| F
    H -->|Not found| I[Return UNKNOWN\nQueue anonymous submission]
    D -->|Feed not loaded| G
```

---

## WireGuard Connection Flow

```mermaid
flowchart TD
    A[Client device\non foreign network] --> B[WireGuard client\nconnects to VPS :443]
    B --> C[Hetzner VPS\nYOUR_VPS_IP]
    C --> D{Is destination\non home LAN?}
    D -->|Yes 192.168.1.x| E[Route through\npersistent Duat tunnel]
    E --> F[Duat Pi 5\n192.168.1.5]
    F --> G[LAN destination\nRaven / Scarab / Legiom]
    D -->|No - internet traffic| H[Forward to internet\nvia VPS NAT]
```

---

## Raven Boot and Display Loop

```mermaid
flowchart TD
    A[Power on\nRaven Pi Zero] --> B[systemd starts raven.service]
    B --> C[raven_deck.py initializes]
    C --> D[Init e-ink display\nWaveshare 2.13 V4]
    D --> E[Start Flask API\nport 6175]
    E --> F[Start UDP heartbeat\nlistener port 7743]
    F --> G[Show MAIN screen]
    G --> H{Touch input}
    H -->|Left tap on MAIN| I[Inject test\nunlock alert]
    H -->|Right tap on MAIN| J[Show MENU]
    J --> K{Menu selection}
    K -->|LAN PING SWEEP| L[nmap ping scan\nShow results on e-ink]
    K -->|VIEW LAN INTEL| M[Scroll IPs\nPort scan on select]
    K -->|AUTO-AUDIT LAN| N[Sequential deep scan\nLog to raven_intel.txt]
    K -->|BLUETOOTH| O[hcitool lescan\nCount BLE devices]
    K -->|WIFI APs| P[iwlist scan\nCount APs]
    K -->|SYS HEALTH| Q[CPU temp / RAM / IP\nLive display]
    K -->|VIEW LOGS| R[Read raven_intel.txt\nScroll on e-ink]
    K -->|REMOTE SCAN PC| S[SSH YourUser@Legiom\nList Downloads folder]
    K -->|UNLOCK QUEUE| T[Show pending\nlock alerts]
    K -->|SHUT DOWN| U[Export data\nDisplay OFFLINE\nHalt kernel]
    T --> V{Select file}
    V --> W[UNLOCK CONFIRM\nscreen]
    W -->|Left tap UNLOCK| X[POST to Duat :6176\naction: UNLOCK]
    W -->|Right tap DENY| Y[POST to Duat :6176\naction: DENY]
```

---

## Heartbeat Monitoring Flow

```mermaid
sequenceDiagram
    participant W as Watchdog (Legiom)
    participant R as Raven

    loop Every 30 seconds
        W->>R: UDP :7743 "WATCHDOG|Legiom|YourUser|ALIVE"
        R->>R: Update last_seen timestamp
    end

    Note over W,R: If Watchdog crashes or PC sleeps...
    R->>R: Heartbeat timeout detected
    R->>R: Display WATCHDOG OFFLINE warning
```

---

## Full Security Chain — Sequence Diagram

```mermaid
sequenceDiagram
    participant PC as Legiom (Watchdog)
    participant D as Duat
    participant R as Raven
    participant U as User

    PC->>PC: File lands in Downloads
    PC->>PC: SHA256 hash computed
    PC->>D: GET /lookup?hash=abc123 :6174
    D->>D: Check SQLite + MalwareBazaar feed
    D-->>PC: {"verdict": "MALICIOUS"}
    PC->>D: POST /lock {file, hash} :6176
    D->>PC: SSH → icacls /deny user:RX
    D->>R: POST /alert {file, hash, verdict} :6175
    R->>R: Add to unlock queue
    R->>U: E-ink: THREAT DETECTED
    U->>R: Left tap — dismiss to queue
    U->>R: Navigate to UNLOCK QUEUE
    U->>R: Select file → UNLOCK CONFIRM
    U->>R: Left tap — UNLOCK
    R->>D: POST /decision {file, action: UNLOCK} :6176
    D->>PC: SSH → icacls /grant user:F
    D-->>R: {"status": "unlocked"}
    R->>R: Remove from queue
    R->>U: E-ink: Queue empty
```

---

## ESP32 Companion BLE Battle Flow

```mermaid
flowchart TD
    A[Player A holds button\nScan for nearby companions] --> B[BLE scan\nFind nearby ESP32 devices]
    B --> C[Select Player B\nfrom device list]
    C --> D[BLE pairing\nestablished]
    D --> E[Both devices show\nbattle UI]
    E --> F[Combat resolved\nlocally on both devices]
    F --> G[Result displayed\non both screens]
    G --> H{WiFi available?}
    H -->|Yes| I[duat_sync.cpp\nPOST result to Duat :5000]
    H -->|No| J[Store result locally\nSync on next WiFi connection]
    I --> K[Leaderboard updated\non game server]
```

---

## Ring Biometric Pipeline

```mermaid
flowchart TD
    A[COLMI R02 Ring\nBLE: XX:XX:XX:XX:XX:XX] -->|BLE connection| B[raven_ring.py\non Duat]
    B --> C[Poll biometrics\nHR / SpO2 / Steps / Battery]
    C --> D[Store in SQLite\nraven_ring.db]
    D --> E[Compute rolling baseline\n30-day window]
    E --> F[Generate confidence score\npersonal readiness indicator]
    F --> G{Context check}
    G -->|Iron House editing mode| H[Gesture detection active]
    G -->|Other contexts| I[Gesture detection disabled]
    H --> J[Recognize gesture patterns\nfrom BLE motion data]
    J --> K[Trigger Iron House\nediting actions]
    D --> L[REST API :7744\n/status /biometrics /baseline /gesture]
    L --> M[Watchdog Ring tab\nLive display on Legiom]
```
