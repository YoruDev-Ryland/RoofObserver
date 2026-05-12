from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
import time
from pathlib import Path

from roofcommon import (
    DAILY_SEPARATOR,
    ROOF_STATUS_RE,
    extract_number,
    get_meta,
    init_db,
    load_config,
    log_bootstrap_exception,
    normalize_source_ts,
    set_meta,
    setup_logging,
    update_source_availability,
    utc_now_text,
)


def parse_roof_file(text: str, source_timezone: str) -> dict[str, str] | None:
    match = ROOF_STATUS_RE.match(text.strip())
    if not match:
        return None

    source_file_ts, source_tz, status = match.groups()
    return {
        "source_file_ts": source_file_ts,
        "source_tz": source_tz,
        "source_ts_utc": normalize_source_ts(source_file_ts, source_timezone, "%Y-%m-%d %I:%M:%S%p"),
        "status": status.upper(),
    }


def poll_roofs(conn: sqlite3.Connection, roof_dir: Path, source_timezone: str) -> int:
    try:
        if not roof_dir.exists():
            update_source_availability(
                conn,
                source_name="roof_dir",
                path=roof_dir,
                is_available=False,
                detail="roof directory does not exist",
            )
            logging.warning("roof directory does not exist: %s", roof_dir)
            return 0

        building_dirs = sorted(path for path in roof_dir.iterdir() if path.is_dir())
    except OSError as exc:
        update_source_availability(
            conn,
            source_name="roof_dir",
            path=roof_dir,
            is_available=False,
            detail=str(exc),
        )
        raise

    update_source_availability(conn, source_name="roof_dir", path=roof_dir, is_available=True)

    inserted = 0
    for building_dir in building_dirs:
        building = building_dir.name
        roof_file = building_dir / "RoofStatusFile.txt"
        if not roof_file.exists():
            update_source_availability(
                conn,
                source_name=f"roof_file:{building}",
                path=roof_file,
                is_available=False,
                detail="RoofStatusFile.txt does not exist",
            )
            continue

        try:
            roof_text = roof_file.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            update_source_availability(
                conn,
                source_name=f"roof_file:{building}",
                path=roof_file,
                is_available=False,
                detail=str(exc),
            )
            logging.warning("could not read roof file %s: %s", roof_file, exc)
            continue

        update_source_availability(
            conn,
            source_name=f"roof_file:{building}",
            path=roof_file,
            is_available=True,
        )

        payload = parse_roof_file(roof_text, source_timezone)
        if payload is None:
            logging.warning("could not parse roof file: %s", roof_file)
            continue

        last_status = get_meta(conn, f"roof_{building}_last")
        if last_status == payload["status"]:
            continue

        conn.execute(
            """
            INSERT INTO roof_events(logged_at, building, source_file_ts, source_tz, source_ts_utc, status)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (
                utc_now_text(),
                building,
                payload["source_file_ts"],
                payload["source_tz"],
                payload["source_ts_utc"],
                payload["status"],
            ),
        )
        set_meta(conn, f"roof_{building}_last", payload["status"])
        inserted += 1

    return inserted


def parse_weatherdata_line(line: str, source_timezone: str) -> dict[str, object] | None:
    parts = line.split()
    if len(parts) < 21:
        return None

    raw_ts = f"{parts[0]} {parts[1]}"
    condition_tail = parts[-6:]
    return {
        "source_file_ts": raw_ts,
        "source_ts_utc": normalize_source_ts(raw_ts, source_timezone, "%Y-%m-%d %H:%M:%S.%f"),
        "sky_temp_f": float(parts[4]),
        "ambient_temp_f": float(parts[5]),
        "wind_speed": float(parts[7]),
        "humidity_pct": float(parts[8]),
        "dew_point_f": float(parts[9]),
        "rain_flag": int(parts[10]),
        "wet_flag": int(parts[11]),
        "cloud_cond": int(condition_tail[0]),
        "wind_cond": int(condition_tail[1]),
        "rain_cond": int(condition_tail[2]),
        "day_cond": int(condition_tail[3]),
        "raw_line": line,
    }


def poll_weatherdata(conn: sqlite3.Connection, weather_dir: Path, source_timezone: str) -> int:
    weather_file = weather_dir / "weatherdata.txt"
    try:
        if not weather_file.exists():
            update_source_availability(
                conn,
                source_name="weatherdata_file",
                path=weather_file,
                is_available=False,
                detail="weatherdata file does not exist",
            )
            logging.warning("weatherdata file does not exist: %s", weather_file)
            return 0

        line = weather_file.read_text(encoding="utf-8", errors="replace").strip()
    except OSError as exc:
        update_source_availability(
            conn,
            source_name="weatherdata_file",
            path=weather_file,
            is_available=False,
            detail=str(exc),
        )
        raise

    update_source_availability(conn, source_name="weatherdata_file", path=weather_file, is_available=True)
    if not line:
        return 0

    if line == get_meta(conn, "weatherdata_last_line"):
        return 0

    payload = parse_weatherdata_line(line, source_timezone)
    if payload is None:
        logging.warning("could not parse weatherdata line")
        return 0

    conn.execute(
        """
        INSERT INTO weather_snapshots(
            logged_at, source_file_ts, source_ts_utc, sky_temp_f, ambient_temp_f,
            wind_speed, humidity_pct, dew_point_f, rain_flag, wet_flag, cloud_cond,
            wind_cond, rain_cond, day_cond, raw_line
        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            utc_now_text(),
            payload["source_file_ts"],
            payload["source_ts_utc"],
            payload["sky_temp_f"],
            payload["ambient_temp_f"],
            payload["wind_speed"],
            payload["humidity_pct"],
            payload["dew_point_f"],
            payload["rain_flag"],
            payload["wet_flag"],
            payload["cloud_cond"],
            payload["wind_cond"],
            payload["rain_cond"],
            payload["day_cond"],
            payload["raw_line"],
        ),
    )
    set_meta(conn, "weatherdata_last_line", line)
    return 1


def parse_daily_block(block: str, source_timezone: str) -> dict[str, object] | None:
    fields: dict[str, str] = {}
    for raw_line in block.splitlines():
        line = raw_line.strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        fields[key.strip()] = value.strip()

    source_record_ts = fields.get("Time")
    if not source_record_ts:
        return None

    return {
        "source_record_ts": source_record_ts,
        "source_ts_utc": normalize_source_ts(source_record_ts, source_timezone, "%m/%d/%Y %I:%M:%S %p"),
        "ambient_temp_f": extract_number(fields.get("Ambient Temp.")),
        "sky_temp_f": extract_number(fields.get("Sky Temperature")),
        "dew_point_f": extract_number(fields.get("Dew Point")),
        "wind_speed_mph": extract_number(fields.get("Wind Speed")),
        "humidity_pct": extract_number(fields.get("Humidity")),
        "sky_condition": fields.get("Sky Condition"),
        "dampness": fields.get("Dampness"),
        "brightness_pct": extract_number(fields.get("Brightness")),
        "barometer_inhg": extract_number(fields.get("Barometer")),
    }


def poll_daily(conn: sqlite3.Connection, weather_dir: Path, source_timezone: str) -> int:
    daily_file = weather_dir / "daily.txt"
    try:
        if not daily_file.exists():
            update_source_availability(
                conn,
                source_name="daily_file",
                path=daily_file,
                is_available=False,
                detail="daily file does not exist",
            )
            logging.warning("daily file does not exist: %s", daily_file)
            return 0

        offset = int(get_meta(conn, "daily_txt_offset", "0") or "0")
        remainder = get_meta(conn, "daily_txt_remainder", "") or ""
        file_size = daily_file.stat().st_size
        if file_size < offset:
            offset = 0
            remainder = ""

        with daily_file.open("rb") as handle:
            handle.seek(offset)
            chunk = handle.read()
            new_offset = handle.tell()
    except OSError as exc:
        update_source_availability(
            conn,
            source_name="daily_file",
            path=daily_file,
            is_available=False,
            detail=str(exc),
        )
        raise

    update_source_availability(conn, source_name="daily_file", path=daily_file, is_available=True)

    text = remainder + chunk.decode("utf-8", errors="replace")
    if not text:
        return 0

    blocks = text.split(DAILY_SEPARATOR)
    new_remainder = ""
    if text and not text.endswith(DAILY_SEPARATOR):
        new_remainder = blocks.pop()

    inserted = 0
    for block in blocks:
        payload = parse_daily_block(block, source_timezone)
        if payload is None:
            continue
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO daily_weather(
                logged_at, source_record_ts, source_ts_utc, ambient_temp_f, sky_temp_f,
                dew_point_f, wind_speed_mph, humidity_pct, sky_condition, dampness,
                brightness_pct, barometer_inhg
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                utc_now_text(),
                payload["source_record_ts"],
                payload["source_ts_utc"],
                payload["ambient_temp_f"],
                payload["sky_temp_f"],
                payload["dew_point_f"],
                payload["wind_speed_mph"],
                payload["humidity_pct"],
                payload["sky_condition"],
                payload["dampness"],
                payload["brightness_pct"],
                payload["barometer_inhg"],
            ),
        )
        if cursor.rowcount:
            inserted += 1

    set_meta(conn, "daily_txt_offset", str(new_offset))
    set_meta(conn, "daily_txt_remainder", new_remainder)
    return inserted


def run_poll_cycle(conn: sqlite3.Connection, config: dict[str, object]) -> dict[str, int]:
    source_timezone = str(config["source_timezone"])
    stats = {
        "roof_events": 0,
        "weather_snapshots": 0,
        "daily_weather": 0,
    }

    collectors = {
        "roof_events": lambda: poll_roofs(conn, Path(config["roof_dir"]), source_timezone),
        "weather_snapshots": lambda: poll_weatherdata(conn, Path(config["weather_dir"]), source_timezone),
        "daily_weather": lambda: poll_daily(conn, Path(config["weather_dir"]), source_timezone),
    }

    for name, collector in collectors.items():
        try:
            stats[name] = collector()
        except Exception:
            logging.exception("poll collector failed: %s", name)

    conn.commit()
    return stats


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Poll observatory roof and weather files into SQLite")
    parser.add_argument("--config", default=None, help="Path to config.json")
    parser.add_argument("--once", action="store_true", help="Run one poll cycle and exit")
    return parser.parse_args()


def main() -> int:
    try:
        args = parse_args()
        config = load_config(args.config)
        setup_logging(config["log_path"], name="roofobserver")
        conn = init_db(config["db_path"])

        logging.info("RoofObserver started")
        try:
            while True:
                try:
                    stats = run_poll_cycle(conn, config)
                    logging.info("poll cycle complete: %s", stats)
                except Exception:
                    logging.exception("poll cycle error")

                if args.once:
                    break
                time.sleep(int(config["poll_interval_seconds"]))
        finally:
            conn.close()

        return 0
    except Exception as exc:
        bootstrap_path = log_bootstrap_exception("roofobserver", exc)
        try:
            logging.exception("RoofObserver fatal startup error")
        except Exception:
            pass
        print(f"RoofObserver fatal startup error. See {bootstrap_path}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())