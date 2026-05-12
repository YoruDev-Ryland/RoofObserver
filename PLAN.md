# RoofObserver — Implementation Plan

## Overview

Two cooperating Python processes run as Windows services on a Windows 11 telescope PC:

1. **Poller** (`roofobserver.py`) — watches roof and weather files on the mapped network drive and writes changes to a local SQLite database.
2. **API server** (`roofapi.py`) — a lightweight Flask REST API that serves all database tables as JSON, accessible to other machines on the Tailscale VPN.

A GitHub Actions workflow builds both scripts into a single Windows installer exe (`RoofObserverSetup.exe`) on every version tag push. The installer is uploaded as a GitHub Release artifact so it can be downloaded from the repo page and run directly on the telescope PC.

---

## Source File Formats (verified from live samples)

### `Z:\roof\building-N\RoofStatusFile.txt`
Single-line, overwritten in place. One active entry per building.
```
2026-05-12 04:32:07AM CST Roof Status: CLOSED
```
Fields: `date`, `time`, `timezone`, `Roof Status:`, `status`

### `Z:\weather\weatherdata.txt`
Single-line, overwritten in place. Live weather snapshot from the station.
```
2026-05-12 12:04:31.00 F M 37     76.8  77      0      51  57.2   000 0 0 00020 046154.50314 1 1 1 3 0 0
```
Space-delimited positional fields (Boltwood/Clarity-style):  
`date time units_temp units_wind sky_temp ambient_temp sensor_temp wind_speed humidity dew_point rain_flag wet_flag cloud_cond wind_cond rain_cond day_cond roof_close_requested alert`

### `Z:\weather\daily.txt`
Append-only multi-record log. New block appended approximately every 10 minutes. Records are separated by `_______________________`.
```
Time: 5/12/2026 12:10:11 AM

Ambient Temp.:	65° F
Sky Temperature:	30.6° F
Dew Point: 	59.7° F
Wind Speed:	0 mph
Humidity:		85%
Sky Condition:	Clear
Dampness:	Dry
Brightness:	0%
Barometer:	28.52 in Hg
```

---

## Technology Stack

| Concern | Choice | Reason |
|---|---|---|
| Language | Python 3.11+ | Cross-platform, easy to maintain |
| Database | SQLite (via stdlib `sqlite3`) | File-based, zero server, durable, queryable |
| API framework | Flask + Waitress | Flask is minimal; Waitress is a production-grade pure-Python WSGI server (no C compiler needed on Windows) |
| Service host | NSSM (Non-Sucking Service Manager) | Wraps any executable as a Windows service; no code changes needed |
| Config | `config.json` (stdlib `json`) | Human-editable, AI-parseable |
| Scheduling | `time.sleep` poll loop | Reliable on network drives; `watchdog` is unreliable over SMB |
| Packaging | PyInstaller | Bundles Python + scripts into standalone Windows exes; no Python install needed on scope PC |
| Installer | Inno Setup 6 | Creates a single-file `RoofObserverSetup.exe`; pre-installed on GitHub Actions `windows-latest` |
| CI/CD | GitHub Actions | Builds and publishes the installer on every `v*` tag push |

**Pip installs required** (run once on the telescope PC — OR skip entirely if using the installer):
```
pip install flask waitress
```

---

## Configuration File (`config.json`)

```json
{
  "share_root": "Z:\\",
  "roof_subdir": "roof",
  "weather_subdir": "weather",
  "db_path": "C:\\RoofObserver\\roofobserver.db",
  "poll_interval_seconds": 30,
  "log_path": "C:\\RoofObserver\\roofobserver.log",
  "api_host": "0.0.0.0",
  "api_port": 5000
}
```

- `api_host` `0.0.0.0` makes the API reachable on all interfaces including Tailscale. Change to `127.0.0.1` to restrict to localhost only.
- `api_port` default is `5000`. Ensure Windows Firewall allows inbound TCP on this port (or rely on Tailscale's overlay which bypasses the host firewall for VPN peers).

- `share_root` is the only field that needs to change if the drive letter or path changes.
- Building directories under `roof_subdir` are discovered dynamically (any subdirectory containing `RoofStatusFile.txt`).

---

## Database Schema

### Table: `roof_events`
Stores a row every time a building's roof status changes (or at startup).

```sql
CREATE TABLE IF NOT EXISTS roof_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    logged_at    TEXT NOT NULL,          -- ISO-8601 UTC timestamp when row was inserted
    building     TEXT NOT NULL,          -- e.g. "building-2"
    file_ts      TEXT NOT NULL,          -- timestamp string parsed from the file
    timezone     TEXT,                   -- e.g. "CST"
    status       TEXT NOT NULL           -- "OPEN" or "CLOSED"
);
```

### Table: `weather_snapshots`
Stores a row every time `weatherdata.txt` content changes.

```sql
CREATE TABLE IF NOT EXISTS weather_snapshots (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    logged_at      TEXT NOT NULL,
    file_ts        TEXT NOT NULL,        -- datetime from field 0+1 of the file
    sky_temp_f     REAL,
    ambient_temp_f REAL,
    wind_speed     REAL,
    humidity_pct   REAL,
    dew_point_f    REAL,
    rain_flag      INTEGER,
    wet_flag       INTEGER,
    cloud_cond     INTEGER,
    wind_cond      INTEGER,
    rain_cond      INTEGER,
    day_cond       INTEGER,
    raw_line       TEXT                  -- full raw line for future re-parsing
);
```

### Table: `daily_weather`
Stores each parsed block from `daily.txt`. Tracks file byte offset to avoid re-inserting old records.

```sql
CREATE TABLE IF NOT EXISTS daily_weather (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    logged_at       TEXT NOT NULL,
    record_ts       TEXT NOT NULL,       -- "Time:" field from the block
    ambient_temp_f  REAL,
    sky_temp_f      REAL,
    dew_point_f     REAL,
    wind_speed_mph  REAL,
    humidity_pct    REAL,
    sky_condition   TEXT,
    dampness        TEXT,
    brightness_pct  REAL,
    barometer_inhg  REAL
);
```

### Table: `meta`
Key-value store for service state (last file positions, etc.).

```sql
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
```
Key examples: `daily_txt_offset`, `weatherdata_last_line`.

---

## Project Structure

### Repo (GitHub)
```
RoofObserver/
    roofobserver.py          ← poller service script
    roofapi.py               ← API server script
    config.json              ← default config template
    installer.iss            ← Inno Setup script
    .github/
        workflows/
            build.yml        ← GitHub Actions CI/CD
    SFROShare/               ← local dev sample data (gitignored)
```

### Installed on scope PC (written by installer)
```
C:\RoofObserver\
    roofobserver.exe         ← poller service executable
    roofapi.exe              ← API server executable
    nssm.exe                 ← service manager (bundled by installer)
    config.json              ← only written if not already present
    roofobserver.db          ← created automatically on first run
    roofobserver.log         ← poller log
    roofapi.log              ← API server log
```

---

## Implementation Steps

Each step is discrete and verifiable.

### Step 1 — Project scaffold
1. Create directory `C:\RoofObserver\` on the telescope PC.
2. Create `config.json` with the fields shown above, setting paths appropriately.
3. Verify Python 3.11+ is installed: `python --version`.

### Step 2 — Write `roofobserver.py`

The script is a single file with these sections in order:

**2a. Imports and constants**  
`sqlite3`, `json`, `os`, `re`, `time`, `datetime`, `logging`, `pathlib`, `sys`

**2b. `load_config(path)`**  
Reads `config.json`. Resolves `share_root`, builds full paths for roof dir, weather dir, db, log. Returns a config dict.

**2c. `init_db(db_path)`**  
Opens SQLite connection, runs `CREATE TABLE IF NOT EXISTS` for all four tables, calls `conn.commit()`. Returns connection.

**2d. `parse_roof_file(text)` → dict or None**  
Regex: `r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}[AP]M) (\w+) Roof Status: (\w+)'`  
Returns `{file_ts, timezone, status}` or `None` if parse fails.

**2e. `poll_roofs(conn, roof_dir)`**  
- Walk `roof_dir` for subdirectories that contain `RoofStatusFile.txt`.
- For each building, read the file, parse it.
- Check the last known status from `meta` table (`key = "roof_{building}_last"`).
- If status changed (or no prior record), insert a row into `roof_events` and update `meta`.

**2f. `parse_weatherdata_line(line)` → dict or None**  
Split on whitespace. Field indices (0-based):  
`0=date, 1=time, 2=temp_units, 3=wind_units, 4=sky_temp, 5=ambient_temp, 6=sensor_temp, 7=wind_speed, 8=humidity, 9=dew_point, 10=rain_flag, 11=wet_flag, 12=cloud_cond, 13=wind_cond, 14=rain_cond, 15=day_cond`  
Cast numeric fields to float/int. Return dict.

**2g. `poll_weatherdata(conn, weather_dir)`**  
- Read `weatherdata.txt` as a single stripped line.
- Compare to `meta` key `weatherdata_last_line`.
- If changed, parse and insert into `weather_snapshots`, update `meta`.

**2h. `poll_daily(conn, weather_dir)`**  
- Open `daily.txt` in binary mode; seek to `meta` key `daily_txt_offset` (default 0).
- Read new bytes to EOF; update offset in `meta`.
- Decode and split on `_______________________`.
- For each non-empty block, parse labeled fields using `str.splitlines()` and `str.split(':',1)` stripping.
- Extract numeric values with regex `r'[\d.]+' ` from value strings.
- Insert into `daily_weather` if `record_ts` not already present (use `INSERT OR IGNORE` with a UNIQUE index on `record_ts`).

> Add `CREATE UNIQUE INDEX IF NOT EXISTS uq_daily_ts ON daily_weather(record_ts);` to `init_db`.

**2i. `main()`**  
```python
config = load_config("config.json")
setup_logging(config["log_path"])   # rotating file + stdout
conn = init_db(config["db_path"])
interval = config["poll_interval_seconds"]
logging.info("RoofObserver started")
while True:
    try:
        poll_roofs(conn, config["roof_dir"])
        poll_weatherdata(conn, config["weather_dir"])
        poll_daily(conn, config["weather_dir"])
        conn.commit()
    except Exception as e:
        logging.exception("Poll cycle error: %s", e)
    time.sleep(interval)
```

**2j. Entry point**  
```python
if __name__ == "__main__":
    main()
```

### Step 3 — Write `roofapi.py`

A second single-file script. It opens the database read-only and serves all data as JSON via Flask + Waitress.

**3a. Imports**  
`flask`, `sqlite3`, `json`, `logging`, `pathlib`

**3b. `load_config(path)`**  
Same helper as in `roofobserver.py` (copy it). Reads `config.json`.

**3c. Flask app setup**  
```python
app = Flask(__name__)
```
Open the database in read-only mode using the URI: `sqlite:///path/to/db?mode=ro` (via `uri=True` in `sqlite3.connect`). Store the connection at module level.

**3d. Helper `query(sql, params=())`**  
Executes a query and returns a list of dicts using `cursor.description` to map column names.

**3e. Endpoints**

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Health check — returns `{"status": "ok", "db": "<path>"}` |
| `GET` | `/roofs` | List all distinct building names in `roof_events` |
| `GET` | `/roofs/events` | All roof events. Query params: `building`, `status`, `since` (ISO timestamp), `limit` (default 500) |
| `GET` | `/roofs/events/<building>` | Events for one building. Same query params as above minus `building` |
| `GET` | `/weather/snapshots` | Rows from `weather_snapshots`. Query params: `since`, `limit` (default 500) |
| `GET` | `/weather/daily` | Rows from `daily_weather`. Query params: `since`, `limit` (default 500) |
| `GET` | `/weather/latest` | Single most recent row from both `weather_snapshots` and `daily_weather` combined into one JSON object |

All list endpoints return `{"count": N, "results": [...]}`.  
All timestamps in responses are the raw strings stored in the DB.  
All endpoints return `Content-Type: application/json`.

**3f. Query param handling pattern (for each list endpoint)**  
```python
building = request.args.get("building")
since    = request.args.get("since")
limit    = int(request.args.get("limit", 500))

where, params = [], []
if building: where.append("building = ?");  params.append(building)
if since:    where.append("logged_at >= ?"); params.append(since)
clause = ("WHERE " + " AND ".join(where)) if where else ""
params.append(limit)
rows = query(f"SELECT * FROM roof_events {clause} ORDER BY logged_at DESC LIMIT ?", params)
```

**3g. `main()`**  
```python
config = load_config("config.json")
setup_logging(config["log_path"].replace("roofobserver", "roofapi"))
from waitress import serve
logging.info("RoofAPI starting on %s:%s", config["api_host"], config["api_port"])
serve(app, host=config["api_host"], port=config["api_port"])
```

**3h. Entry point**  
```python
if __name__ == "__main__":
    main()
```

### Step 4 — Test locally (dev machine or telescope PC)

1. Point `config.json` `share_root` at the local `SFROShare/` snapshot folder for testing.
2. Run `python roofobserver.py` in one terminal — confirm no errors in log and rows appear in the DB.
3. Run `python roofapi.py` in a second terminal — confirm it starts and logs the bind address.
4. Use `sqlite3 roofobserver.db "SELECT * FROM roof_events LIMIT 10;"` to verify DB rows.
5. Hit `http://localhost:5000/` — should return `{"status": "ok"}`.
6. Hit `http://localhost:5000/roofs/events` — should return roof event rows as JSON.
7. Hit `http://localhost:5000/weather/latest` — should return the latest weather snapshot.
8. Change a `RoofStatusFile.txt` manually; confirm a new row appears in `/roofs/events` within one poll interval.
9. Append a new block to `daily.txt`; confirm it appears in `/weather/daily` without duplicates.

### Step 5 — Install both scripts as Windows services with NSSM

1. Download NSSM from https://nssm.cc/download — place `nssm.exe` in `C:\RoofObserver\`.
2. Run `pip install flask waitress` on the telescope PC if not already done.
3. Open an elevated PowerShell prompt.
4. Install the poller service:
   ```
   C:\RoofObserver\nssm.exe install RoofObserver "C:\Python311\python.exe" "C:\RoofObserver\roofobserver.py"
   C:\RoofObserver\nssm.exe set RoofObserver AppDirectory "C:\RoofObserver"
   C:\RoofObserver\nssm.exe set RoofObserver AppStdout "C:\RoofObserver\roofobserver.log"
   C:\RoofObserver\nssm.exe set RoofObserver AppStderr "C:\RoofObserver\roofobserver.log"
   C:\RoofObserver\nssm.exe set RoofObserver Start SERVICE_AUTO_START
   C:\RoofObserver\nssm.exe start RoofObserver
   ```
5. Install the API service:
   ```
   C:\RoofObserver\nssm.exe install RoofAPI "C:\Python311\python.exe" "C:\RoofObserver\roofapi.py"
   C:\RoofObserver\nssm.exe set RoofAPI AppDirectory "C:\RoofObserver"
   C:\RoofObserver\nssm.exe set RoofAPI AppStdout "C:\RoofObserver\roofapi.log"
   C:\RoofObserver\nssm.exe set RoofAPI AppStderr "C:\RoofObserver\roofapi.log"
   C:\RoofObserver\nssm.exe set RoofAPI Start SERVICE_AUTO_START
   C:\RoofObserver\nssm.exe start RoofAPI
   ```
6. Confirm both services: `sc query RoofObserver` and `sc query RoofAPI` — both should show `STATE: RUNNING`.
7. From another machine on the Tailscale network, curl the Tailscale IP of the telescope PC:
   ```
   curl http://<tailscale-ip>:5000/
   curl http://<tailscale-ip>:5000/roofs/events?limit=10
   ```

> **Network drive caveat**: If the Z: drive is not mapped at Windows service startup time, the poller will log an error and retry on the next poll cycle — no crash. Ensure the drive mapping is persistent (mapped with "Reconnect at sign-in" or via `net use` in a startup script). The API service is unaffected by the drive state.

> **Windows Firewall**: Tailscale traffic typically bypasses the Windows Firewall for VPN peers. If access from the web server is blocked, add an inbound rule: `netsh advfirewall firewall add rule name="RoofAPI" dir=in action=allow protocol=TCP localport=5000`.

### Step 6 — GitHub Actions: build and publish installer

This step is done from the dev machine, not the scope PC. The workflow is already committed to the repo; it fires automatically when a version tag is pushed.

**Files to create in the repo:**

**`.github/workflows/build.yml`** — triggers on `v*` tag push:
```yaml
name: Build Installer
on:
  push:
    tags: ['v*']
jobs:
  build:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - name: Install Python deps
        run: pip install flask waitress pyinstaller
      - name: Download NSSM
        run: |
          Invoke-WebRequest -Uri https://nssm.cc/release/nssm-2.24.zip -OutFile nssm.zip
          Expand-Archive nssm.zip -DestinationPath nssm_tmp
          Copy-Item nssm_tmp\nssm-2.24\win64\nssm.exe .\nssm.exe
        shell: pwsh
      - name: Build executables
        run: |
          pyinstaller --onefile --name roofobserver roofobserver.py
          pyinstaller --onefile --name roofapi roofapi.py
      - name: Build installer
        run: iscc installer.iss
      - name: Upload to GitHub Release
        uses: softprops/action-gh-release@v2
        with:
          files: Output/RoofObserverSetup.exe
```

**`installer.iss`** — Inno Setup 6 script:
```
[Setup]
AppName=RoofObserver
AppVersion=1.0
DefaultDirName={autopf}\RoofObserver
OutputDir=Output
OutputBaseFilename=RoofObserverSetup
Compression=lzma
SolidCompression=yes

[Files]
Source: "dist\roofobserver.exe"; DestDir: "{app}"
Source: "dist\roofapi.exe";     DestDir: "{app}"
Source: "nssm.exe";              DestDir: "{app}"
Source: "config.json";           DestDir: "{app}"; Flags: onlyifdoesntexist

[Run]
Filename: "{app}\nssm.exe"; Parameters: "install RoofObserver \"{app}\roofobserver.exe\""; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set RoofObserver AppDirectory \"{app}\""; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set RoofObserver Start SERVICE_AUTO_START"; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "install RoofAPI \"{app}\roofapi.exe\""; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set RoofAPI AppDirectory \"{app}\""; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set RoofAPI Start SERVICE_AUTO_START"; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "start RoofObserver"; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "start RoofAPI"; Flags: runhidden
```

**To release a new version:**
```bash
git tag v1.0.0
git push origin v1.0.0
```
Actions will build, create the GitHub Release, and attach `RoofObserverSetup.exe` automatically. Visit the repo Releases page on the scope PC and download the exe.

### Step 7 — Ongoing maintenance / future changes

- To release an update: commit changes, push a new `v*` tag; GitHub Actions builds and publishes the installer automatically.
- To change the share path: edit `share_root` in `config.json` at `C:\RoofObserver\`, restart only the poller (`nssm restart RoofObserver`).
- To change the API port or bind address: edit `api_host`/`api_port` in `config.json`, restart the API service (`nssm restart RoofAPI`).
- To add new buildings: no changes needed — discovery is dynamic.
- To query data directly: any SQLite browser (e.g., DB Browser for SQLite) or the `sqlite3` CLI.

---

## Out of Scope (keep it simple)

- No web UI or dashboard — the API serves raw JSON; rendering is left to the consuming web server.
- No authentication on the API — Tailscale provides network-level access control; do not expose port 5000 to the public internet.
- No data retention pruning — the DB will grow slowly (roof changes are infrequent; daily weather is ~144 rows/day).
- No alerting or notifications.
