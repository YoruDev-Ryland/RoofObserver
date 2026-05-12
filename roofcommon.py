from __future__ import annotations

import json
import logging
import re
import sqlite3
import sys
import traceback
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DAILY_SEPARATOR = "_______________________"
ROOF_STATUS_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}[AP]M) (\w+) Roof Status: (\w+)$"
)
NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")


def runtime_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def default_config_path() -> Path:
    return runtime_dir() / "config.json"


def resolve_path(raw_path: str, config_dir: Path) -> Path:
    path = Path(raw_path).expanduser()
    if path.is_absolute():
        return path
    return (config_dir / path).resolve()


def load_config(config_path: str | Path | None = None) -> dict[str, Any]:
    path = Path(config_path or default_config_path()).expanduser().resolve()
    data = json.loads(path.read_text(encoding="utf-8"))

    share_root = str(data["share_root"])
    if not share_root:
        raise ValueError("share_root must not be empty")

    share_root_path = Path(share_root)
    if not share_root.startswith("\\\\") and not share_root_path.is_absolute():
        raise ValueError("share_root must be a UNC path or an absolute local path")

    if re.match(r"^[A-Za-z]:\\", share_root):
        logging.warning(
            "share_root=%s looks like a drive-letter path; use a UNC path for Windows service deployment",
            share_root,
        )

    source_timezone = data.get("source_timezone", "America/Chicago")
    try:
        ZoneInfo(source_timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(
            f"Time zone data for '{source_timezone}' is unavailable. Install the tzdata package or rebuild the Windows executable with tzdata bundled."
        ) from exc

    config_dir = path.parent
    db_path = resolve_path(data["db_path"], config_dir)
    log_path = resolve_path(data["log_path"], config_dir)

    roof_dir = share_root_path / data["roof_subdir"]
    weather_dir = share_root_path / data["weather_subdir"]

    return {
        **data,
        "config_path": path,
        "config_dir": config_dir,
        "share_root_path": share_root_path,
        "roof_dir": roof_dir,
        "weather_dir": weather_dir,
        "db_path": db_path,
        "log_path": log_path,
        "source_timezone": source_timezone,
        "poll_interval_seconds": int(data.get("poll_interval_seconds", 30)),
        "api_port": int(data.get("api_port", 5000)),
        "api_host": data.get("api_host", "127.0.0.1"),
    }


def setup_logging(log_path: str | Path, *, name: str | None = None) -> None:
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(logging.INFO)

    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=5)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    if name:
        logging.getLogger(name).setLevel(logging.INFO)


def bootstrap_log_path(service_name: str) -> Path:
    return runtime_dir() / f"{service_name}-startup.log"


def log_bootstrap_exception(service_name: str, exc: BaseException) -> Path:
    path = bootstrap_log_path(service_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        f"{timestamp} fatal startup error in {service_name}",
        "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        "",
    ]
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
    return path


def utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def normalize_source_ts(raw_ts: str, source_timezone: str, fmt: str) -> str:
    local_dt = datetime.strptime(raw_ts, fmt).replace(tzinfo=ZoneInfo(source_timezone))
    return local_dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def extract_number(value: str | None) -> float | None:
    if not value:
        return None
    match = NUMBER_RE.search(value)
    if not match:
        return None
    return float(match.group(0))


SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS roof_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        logged_at TEXT NOT NULL,
        building TEXT NOT NULL,
        source_file_ts TEXT NOT NULL,
        source_tz TEXT,
        source_ts_utc TEXT NOT NULL,
        status TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS weather_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        logged_at TEXT NOT NULL,
        source_file_ts TEXT NOT NULL,
        source_ts_utc TEXT NOT NULL,
        sky_temp_f REAL,
        ambient_temp_f REAL,
        wind_speed REAL,
        humidity_pct REAL,
        dew_point_f REAL,
        rain_flag INTEGER,
        wet_flag INTEGER,
        cloud_cond INTEGER,
        wind_cond INTEGER,
        rain_cond INTEGER,
        day_cond INTEGER,
        raw_line TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS daily_weather (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        logged_at TEXT NOT NULL,
        source_record_ts TEXT NOT NULL,
        source_ts_utc TEXT NOT NULL,
        ambient_temp_f REAL,
        sky_temp_f REAL,
        dew_point_f REAL,
        wind_speed_mph REAL,
        humidity_pct REAL,
        sky_condition TEXT,
        dampness TEXT,
        brightness_pct REAL,
        barometer_inhg REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_daily_ts ON daily_weather(source_record_ts)",
    "CREATE INDEX IF NOT EXISTS idx_roof_events_building_ts ON roof_events(building, source_ts_utc DESC)",
    "CREATE INDEX IF NOT EXISTS idx_roof_events_status_ts ON roof_events(status, source_ts_utc DESC)",
    "CREATE INDEX IF NOT EXISTS idx_weather_snapshots_ts ON weather_snapshots(source_ts_utc DESC)",
    "CREATE INDEX IF NOT EXISTS idx_daily_weather_ts ON daily_weather(source_ts_utc DESC)",
]


def init_db(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    for statement in SCHEMA_STATEMENTS:
        conn.execute(statement)
    conn.commit()
    return conn


def get_meta(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    if row is None:
        return default
    return row[0]


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO meta(key, value) VALUES(?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )


def sqlite_readonly_uri(db_path: str | Path) -> str:
    return f"{Path(db_path).resolve().as_uri()}?mode=ro"