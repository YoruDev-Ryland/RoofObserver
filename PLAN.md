# RoofObserver — Implementation Plan

## Overview

Two cooperating Python processes run as Windows services on a Windows 11 telescope PC:

1. **Poller** (`roofobserver.py`) — watches roof and weather files on the mapped network drive and writes changes to a local SQLite database.
2. **API server** (`roofapi.py`) — a lightweight Flask REST API that serves all database tables as JSON, accessible to other machines on the Tailscale VPN.

A GitHub Actions workflow builds both scripts into a single Windows installer exe (`RoofObserverSetup.exe`) on every version tag push. The installer is the primary deployment path. It is uploaded as a GitHub Release artifact so it can be downloaded from the repo page and run directly on the telescope PC.

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

**Primary deployment**: download and run `RoofObserverSetup.exe` from the GitHub Releases page on the scope PC.
**Fallback/dev deployment only**: install Python and run the scripts directly.

**Pip installs required** (fallback/dev path only):
```
pip install flask waitress
```

---

## Configuration File (`config.json`)

```json
{
  "share_root": "\\\\YOUR-SHARE-HOST\\SFROShare",
  "roof_subdir": "roof",
  "weather_subdir": "weather",
  "source_timezone": "America/Chicago",
  "db_path": "C:\\RoofObserver\\roofobserver.db",
  "poll_interval_seconds": 30,
  "log_path": "C:\\RoofObserver\\roofobserver.log",
  "api_host": "0.0.0.0",
  "api_port": 5000
}
```

- `share_root` should be a UNC path, not a mapped drive letter. Windows services should not depend on per-user drive mappings like `Z:`.
- `source_timezone` is the observatory timezone used to normalize source timestamps into UTC for querying.
- `api_host` `0.0.0.0` makes the API reachable on all interfaces including Tailscale. Change to `127.0.0.1` to restrict to localhost only.
- `api_port` default is `5000`. Ensure Windows Firewall allows inbound TCP on this port (or rely on Tailscale's overlay which bypasses the host firewall for VPN peers).

- `share_root` is the main field that needs to change if the network path changes.
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
    source_file_ts TEXT NOT NULL,      -- original timestamp string from the file
    source_tz      TEXT,               -- e.g. "CST"
    source_ts_utc  TEXT NOT NULL,      -- normalized ISO-8601 UTC timestamp used for API filters
    status       TEXT NOT NULL           -- "OPEN" or "CLOSED"
);
```

### Table: `weather_snapshots`
Stores a row every time `weatherdata.txt` content changes.

```sql
CREATE TABLE IF NOT EXISTS weather_snapshots (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    logged_at      TEXT NOT NULL,
    source_file_ts TEXT NOT NULL,      -- datetime from field 0+1 of the file
    source_ts_utc  TEXT NOT NULL,      -- normalized ISO-8601 UTC timestamp used for API filters
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
    source_record_ts TEXT NOT NULL,    -- raw "Time:" field from the block
    source_ts_utc    TEXT NOT NULL,    -- normalized ISO-8601 UTC timestamp used for API filters
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
  roofcommon.py           ← shared config/db/logging helpers
    roofobserver.py          ← poller service script
    roofapi.py               ← API server script
    config.json              ← default config template
  requirements.txt         ← runtime dependencies for local/dev runs
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
1. Keep the repo on the dev machine; do not hand-copy scripts to the scope PC.
2. Set `config.json` to the correct UNC share path and observatory timezone.
3. Use the installer for real deployment on the scope PC. Keep direct Python execution only for local development and debugging.

### Step 2 — Write `roofobserver.py`

The poller uses a small shared helper module, `roofcommon.py`, for config loading, SQLite schema setup, timestamp normalization, and logging. `roofobserver.py` stays focused on file polling and inserts.

The script is a single file with these sections in order:

**2a. Imports and constants**
`sqlite3`, `json`, `os`, `re`, `time`, `datetime`, `logging`, `pathlib`, `sys`

**2b. `load_config(path)`**
Reads `config.json`. Resolves `share_root`, builds full paths for roof dir, weather dir, db, log. Validates that `share_root` is a UNC path. Returns a config dict.

**2c. `init_db(db_path)`**
Opens SQLite connection, enables WAL mode, runs `CREATE TABLE IF NOT EXISTS` for all four tables, calls `conn.commit()`. Returns connection.

**2d. `normalize_source_ts(raw_ts, source_timezone)` → str**
Parses each source timestamp format, attaches `source_timezone`, converts to UTC, and returns ISO-8601 UTC text. This is the timestamp used for API `since` filters.

**2e. `parse_roof_file(text)` → dict or None**
Regex: `r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}[AP]M) (\w+) Roof Status: (\w+)'`  
Returns `{source_file_ts, source_tz, source_ts_utc, status}` or `None` if parse fails.

**2f. `poll_roofs(conn, roof_dir)`**
- Walk `roof_dir` for subdirectories that contain `RoofStatusFile.txt`.
- For each building, read the file, parse it.
- Check the last known status from `meta` table (`key = "roof_{building}_last"`).
- If status changed (or no prior record), insert a row into `roof_events` and update `meta`.

**2g. `parse_weatherdata_line(line)` → dict or None**
Split on whitespace. Field indices (0-based):  
`0=date, 1=time, 2=temp_units, 3=wind_units, 4=sky_temp, 5=ambient_temp, 6=sensor_temp, 7=wind_speed, 8=humidity, 9=dew_point, 10=rain_flag, 11=wet_flag, 12=cloud_cond, 13=wind_cond, 14=rain_cond, 15=day_cond`  
Cast numeric fields to float/int. Also derive `source_file_ts` and `source_ts_utc`. Return dict.

**2h. `poll_weatherdata(conn, weather_dir)`**
- Read `weatherdata.txt` as a single stripped line.
- Compare to `meta` key `weatherdata_last_line`.
- If changed, parse and insert into `weather_snapshots`, update `meta`.

**2i. `poll_daily(conn, weather_dir)`**
- Open `daily.txt` in binary mode; seek to `meta` key `daily_txt_offset` (default 0).
- Read new bytes to EOF; update offset in `meta`.
- Decode and split on `_______________________`.
- For each non-empty block, parse labeled fields using `str.splitlines()` and `str.split(':',1)` stripping.
- Extract numeric values with regex `r'[\d.]+' ` from value strings.
- Normalize the `Time:` field into `source_ts_utc`.
- Insert into `daily_weather` if `source_record_ts` not already present (use `INSERT OR IGNORE` with a UNIQUE index on `source_record_ts`).

> Add `CREATE UNIQUE INDEX IF NOT EXISTS uq_daily_ts ON daily_weather(source_record_ts);` to `init_db`.

**2j. `main()`**
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

**2k. Entry point**
```python
if __name__ == "__main__":
    main()
```

### Step 3 — Write `roofapi.py`

A second single-file script. It opens SQLite in read-only mode per request and serves all data as JSON via Flask + Waitress.

`roofapi.py` imports shared config/logging/path helpers from `roofcommon.py`.

**3a. Imports**
`flask`, `sqlite3`, `json`, `logging`, `pathlib`

**3b. `load_config(path)`**
Same helper as in `roofobserver.py` (copy it). Reads `config.json`.

**3c. Flask app setup**
```python
app = Flask(__name__)
```
Use a helper that opens the database in read-only mode using `file:path/to/db?mode=ro` with `uri=True`. Do not keep a module-level shared SQLite connection.

**3d. Helpers `get_db()` and `query(sql, params=())`**
`get_db()` opens a fresh read-only SQLite connection per request. `query()` executes a query, returns a list of dicts using `cursor.description`, and closes the connection immediately after use.

**3e. Endpoints**

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Health check — returns `{"status": "ok", "db": "<path>"}` |
| `GET` | `/roofs` | List all distinct building names in `roof_events` |
| `GET` | `/roofs/events` | All roof events. Query params: `building`, `status`, `since` (UTC ISO timestamp against `source_ts_utc`), `limit` (default 500) |
| `GET` | `/roofs/events/<building>` | Events for one building. Same query params as above minus `building` |
| `GET` | `/weather/snapshots` | Rows from `weather_snapshots`. Query params: `since`, `limit` (default 500) |
| `GET` | `/weather/daily` | Rows from `daily_weather`. Query params: `since`, `limit` (default 500) |
| `GET` | `/weather/latest` | Single most recent row from both `weather_snapshots` and `daily_weather` combined into one JSON object |

All list endpoints return `{"count": N, "results": [...]}`.
Responses should include both the raw source timestamp fields and `source_ts_utc`.
All endpoints return `Content-Type: application/json`.

**3f. Query param handling pattern (for each list endpoint)**  
```python
building = request.args.get("building")
since    = request.args.get("since")
limit    = int(request.args.get("limit", 500))

where, params = [], []
if building: where.append("building = ?");  params.append(building)
if since:    where.append("source_ts_utc >= ?"); params.append(since)
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
6. Hit `http://localhost:5000/roofs/events` — should return roof event rows as JSON including `source_ts_utc`.
7. Hit `http://localhost:5000/weather/latest` — should return the latest weather snapshot.
8. Call `/roofs/events?since=<utc-iso-ts>` and confirm filtering works correctly.
9. Change a `RoofStatusFile.txt` manually; confirm a new row appears in `/roofs/events` within one poll interval.
10. Append a new block to `daily.txt`; confirm it appears in `/weather/daily` without duplicates.

### Step 5 — Install on the scope PC via the installer (primary path)

1. On the dev machine, push a version tag:
  ```
  git tag v1.0.0
  git push origin v1.0.0
  ```
2. Wait for GitHub Actions to finish and publish `RoofObserverSetup.exe` to the GitHub Release.
3. On the scope PC, download `RoofObserverSetup.exe` from the repo Releases page.
4. Run the installer as Administrator.
5. Edit `C:\RoofObserver\config.json` so `share_root` is the correct UNC path and verify `source_timezone`.
6. Confirm both services are installed and started: `sc query RoofObserver` and `sc query RoofAPI`.
7. From another machine on the Tailscale network, curl the Tailscale IP of the telescope PC:
  ```
  curl http://<tailscale-ip>:5000/
  curl http://<tailscale-ip>:5000/roofs/events?limit=10
  ```

> The installer stops and recreates both services during upgrades so exe replacements are reliable and upgrades stay idempotent.

### Step 6 — Manual install fallback (Python + NSSM)

1. Clone the repo or copy the full source tree to `C:\RoofObserver\` so `roofobserver.py`, `roofapi.py`, `roofcommon.py`, `config.json`, and `requirements.txt` are all present.
2. Install NSSM on the telescope PC. Any reliable method is fine; `choco install nssm -y` is the simplest if Chocolatey is available.
3. Run `python -m pip install -r requirements.txt` on the telescope PC if not already done.
4. Open an elevated PowerShell prompt.
5. Install the poller service:
  ```
  C:\RoofObserver\nssm.exe install RoofObserver "C:\Python311\python.exe" "C:\RoofObserver\roofobserver.py"
  C:\RoofObserver\nssm.exe set RoofObserver AppDirectory "C:\RoofObserver"
  C:\RoofObserver\nssm.exe set RoofObserver AppStdout "C:\RoofObserver\roofobserver.log"
  C:\RoofObserver\nssm.exe set RoofObserver AppStderr "C:\RoofObserver\roofobserver.log"
  C:\RoofObserver\nssm.exe set RoofObserver Start SERVICE_AUTO_START
  C:\RoofObserver\nssm.exe start RoofObserver
  ```
6. Install the API service:
  ```
  C:\RoofObserver\nssm.exe install RoofAPI "C:\Python311\python.exe" "C:\RoofObserver\roofapi.py"
  C:\RoofObserver\nssm.exe set RoofAPI AppDirectory "C:\RoofObserver"
  C:\RoofObserver\nssm.exe set RoofAPI AppStdout "C:\RoofObserver\roofapi.log"
  C:\RoofObserver\nssm.exe set RoofAPI AppStderr "C:\RoofObserver\roofapi.log"
  C:\RoofObserver\nssm.exe set RoofAPI Start SERVICE_AUTO_START
  C:\RoofObserver\nssm.exe start RoofAPI
  ```
7. Configure the services to restart after failures:
  ```
  C:\RoofObserver\nssm.exe set RoofObserver AppExit Default Restart
  C:\RoofObserver\nssm.exe set RoofObserver AppRestartDelay 5000
  C:\RoofObserver\nssm.exe set RoofAPI AppExit Default Restart
  C:\RoofObserver\nssm.exe set RoofAPI AppRestartDelay 5000
  ```
8. Confirm both services: `sc query RoofObserver` and `sc query RoofAPI` — both should show `STATE: RUNNING`.
9. From another machine on the Tailscale network, curl the Tailscale IP of the telescope PC:
  ```
  curl http://<tailscale-ip>:5000/
  curl http://<tailscale-ip>:5000/roofs/events?limit=10
  ```

> **Network path requirement**: Use a UNC path in `share_root`. Do not rely on a mapped drive letter like `Z:` for the service account.

> **Service account**: If the network share requires authentication, run the services under a Windows account that has permission to the UNC share.

> **Windows Firewall**: Tailscale traffic typically bypasses the Windows Firewall for VPN peers. If access from the web server is blocked, add an inbound rule: `netsh advfirewall firewall add rule name="RoofAPI" dir=in action=allow protocol=TCP localport=5000`.

### Step 7 — GitHub Actions: build and publish installer

This step is done from the dev machine, not the scope PC. The workflow is already committed to the repo; it now fires automatically on every push to `main`, on `v*` tag pushes, and by manual dispatch.

**Files to create in the repo:**

**`.github/workflows/build.yml`** — triggers on pushes to `main`, on `v*` tag pushes, and via manual dispatch:
```yaml
name: Build Installer
on:
  push:
    branches: [main]
    tags: ['v*']
  workflow_dispatch:
    inputs:
      tag:
        description: Existing version tag to build manually
        required: true
        type: string
permissions:
  contents: write
jobs:
  build:
    runs-on: windows-latest
    steps:
      - name: Resolve release mode
        # branch push -> rolling prerelease "edge"
        # tag push / manual tag -> numbered release "v*"
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - name: Install Python deps
        run: pip install -r requirements.txt pyinstaller
      - name: Install NSSM
        run: |
          choco install nssm --no-progress -y
          $nssmPath = Join-Path $env:ChocolateyInstall 'bin\nssm.exe'
          Copy-Item $nssmPath .\nssm.exe
        shell: pwsh
      - name: Update rolling edge tag
        if: github.ref_type == 'branch'
        run: |
          git tag -f edge "$GITHUB_SHA"
          git push origin refs/tags/edge --force
        shell: bash
      - name: Build executables
        run: |
          pyinstaller --onefile --name roofobserver roofobserver.py
          pyinstaller --onefile --name roofapi roofapi.py
      - name: Build installer
        run: iscc installer.iss
      - name: Upload to GitHub Release
        uses: softprops/action-gh-release@v2
        with:
          tag_name: edge-or-v-tag
          prerelease: true-for-edge-false-for-v-tag
          overwrite_files: true
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
PrivilegesRequired=admin

[Files]
Source: "dist\roofobserver.exe"; DestDir: "{app}"
Source: "dist\roofapi.exe";     DestDir: "{app}"
Source: "nssm.exe";              DestDir: "{app}"
Source: "config.json";           DestDir: "{app}"; Flags: onlyifdoesntexist

[Code]
; Before file replacement, stop and remove existing services if present.
; After file copy, reinstall services, set auto-restart, and start both.
```

**Build on every push:**
```bash
git push origin main
```
Every push to `main` automatically builds the installer and updates a rolling prerelease tagged `edge`.

**To create a numbered release:**
```bash
./release.sh
```
The helper script is optional now. Use it only when you want a numbered `v*` release. For normal development builds, just push to `main` and the workflow updates the rolling `edge` prerelease automatically. In both cases, the installer is attached directly to the GitHub Release and no Actions artifacts are stored.

### Step 8 — Ongoing maintenance / future changes

- To publish the latest dev build: push to `main`; GitHub Actions updates the rolling `edge` prerelease automatically.
- To publish a numbered release: use `./release.sh` to create and push a new `v*` tag.
- To change the share path: edit `share_root` in `config.json` at `C:\RoofObserver\` to the correct UNC path, restart only the poller (`nssm restart RoofObserver`).
- To change the source timezone: edit `source_timezone` and restart the poller so future ingested rows normalize correctly.
- To change the API port or bind address: edit `api_host`/`api_port` in `config.json`, restart the API service (`nssm restart RoofAPI`).
- To add new buildings: no changes needed — discovery is dynamic.
- To query data directly: any SQLite browser (e.g., DB Browser for SQLite) or the `sqlite3` CLI.

---

## Out of Scope (keep it simple)

- No web UI or dashboard — the API serves raw JSON; rendering is left to the consuming web server.
- No authentication on the API — Tailscale provides network-level access control; do not expose port 5000 to the public internet.
- No data retention pruning — the DB will grow slowly (roof changes are infrequent; daily weather is ~144 rows/day).
- No alerting or notifications.

---

## Current Implementation Status

- Implemented files: `roofcommon.py`, `roofobserver.py`, `roofapi.py`, and `requirements.txt`.
- Local ingestion validation completed against the `SFROShare` snapshot: `roof_events=12`, `weather_snapshots=1`, `daily_weather=71` on the first one-shot poll.
- Local API validation completed against the generated SQLite DB using Flask's test client: `/`, `/roofs`, `/roofs/events`, and `/weather/latest` returned successful JSON responses.
- Automatic CI release added: every push to `main` now builds the installer and updates a rolling prerelease tagged `edge`.
- Release helper retained as optional tooling: `release.sh` creates and pushes a numbered `v*` tag when you want a formal versioned release.
- Remaining real-world deployment steps are Windows-specific: build the installer from GitHub Actions, install on the scope PC, set the final UNC `share_root`, and verify NSSM services plus network API access over Tailscale.
