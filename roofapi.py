from __future__ import annotations

import argparse
import logging
import socket
import sqlite3
import sys
from pathlib import Path

from flask import Flask, jsonify, request

from roofcommon import load_config, log_bootstrap_exception, setup_logging, sqlite_readonly_uri


def get_db(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(sqlite_readonly_uri(db_path), uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


def query(db_path: Path, sql: str, params: tuple[object, ...] = ()) -> list[dict[str, object]]:
    connection = get_db(db_path)
    try:
        rows = connection.execute(sql, params).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def parse_limit(default: int = 500, maximum: int = 5000) -> int:
    raw_limit = request.args.get("limit", str(default))
    limit = int(raw_limit)
    if limit < 1:
        raise ValueError("limit must be positive")
    return min(limit, maximum)


def build_where(clauses: list[str], params: list[object]) -> tuple[str, tuple[object, ...]]:
    if not clauses:
        return "", tuple(params)
    return f" WHERE {' AND '.join(clauses)}", tuple(params)


def ensure_port_available(host: str, port: int) -> None:
    bind_host = host if host not in {"0.0.0.0", "::"} else ""
    family = socket.AF_INET6 if ":" in host and host not in {"0.0.0.0", "::"} else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((bind_host, port))
        except OSError as exc:
            raise RuntimeError(f"API port {port} on host '{host}' is unavailable: {exc}") from exc


def create_app(config: dict[str, object]) -> Flask:
    app = Flask(__name__)
    db_path = Path(config["db_path"])

    @app.after_request
    def add_cors_headers(response):
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return response

    @app.errorhandler(ValueError)
    def handle_value_error(error: ValueError):
        return jsonify({"error": str(error)}), 400

    @app.errorhandler(sqlite3.Error)
    def handle_sqlite_error(error: sqlite3.Error):
        logging.exception("database error")
        return jsonify({"error": "database unavailable", "detail": str(error)}), 503

    @app.get("/")
    def health() -> tuple[object, int]:
        return jsonify({"status": "ok", "db": str(db_path), "db_exists": db_path.exists()}), 200

    @app.get("/roofs")
    def roofs() -> tuple[object, int]:
        results = query(
            db_path,
            "SELECT DISTINCT building FROM roof_events ORDER BY building ASC",
        )
        buildings = [row["building"] for row in results]
        return jsonify({"count": len(buildings), "results": buildings}), 200

    @app.get("/roofs/events")
    @app.get("/roofs/events/<building>")
    def roof_events(building: str | None = None) -> tuple[object, int]:
        limit = parse_limit()
        status = request.args.get("status")
        since = request.args.get("since")

        clauses: list[str] = []
        params: list[object] = []
        if building:
            clauses.append("building = ?")
            params.append(building)
        elif request.args.get("building"):
            clauses.append("building = ?")
            params.append(request.args["building"])
        if status:
            clauses.append("status = ?")
            params.append(status.upper())
        if since:
            clauses.append("source_ts_utc >= ?")
            params.append(since)

        where_clause, params_tuple = build_where(clauses, params)
        results = query(
            db_path,
            f"SELECT * FROM roof_events{where_clause} ORDER BY source_ts_utc DESC, id DESC LIMIT ?",
            params_tuple + (limit,),
        )
        return jsonify({"count": len(results), "results": results}), 200

    @app.get("/weather/snapshots")
    def weather_snapshots() -> tuple[object, int]:
        limit = parse_limit()
        since = request.args.get("since")

        clauses: list[str] = []
        params: list[object] = []
        if since:
            clauses.append("source_ts_utc >= ?")
            params.append(since)

        where_clause, params_tuple = build_where(clauses, params)
        results = query(
            db_path,
            f"SELECT * FROM weather_snapshots{where_clause} ORDER BY source_ts_utc DESC, id DESC LIMIT ?",
            params_tuple + (limit,),
        )
        return jsonify({"count": len(results), "results": results}), 200

    @app.get("/weather/daily")
    def daily_weather() -> tuple[object, int]:
        limit = parse_limit()
        since = request.args.get("since")

        clauses: list[str] = []
        params: list[object] = []
        if since:
            clauses.append("source_ts_utc >= ?")
            params.append(since)

        where_clause, params_tuple = build_where(clauses, params)
        results = query(
            db_path,
            f"SELECT * FROM daily_weather{where_clause} ORDER BY source_ts_utc DESC, id DESC LIMIT ?",
            params_tuple + (limit,),
        )
        return jsonify({"count": len(results), "results": results}), 200

    @app.get("/weather/latest")
    def latest_weather() -> tuple[object, int]:
        snapshot = query(
            db_path,
            "SELECT * FROM weather_snapshots ORDER BY source_ts_utc DESC, id DESC LIMIT 1",
        )
        daily = query(
            db_path,
            "SELECT * FROM daily_weather ORDER BY source_ts_utc DESC, id DESC LIMIT 1",
        )
        return jsonify(
            {
                "weather_snapshot": snapshot[0] if snapshot else None,
                "daily_weather": daily[0] if daily else None,
            }
        ), 200

    @app.get("/sources/availability")
    def source_availability() -> tuple[object, int]:
        limit = parse_limit(default=100, maximum=1000)
        source_name = request.args.get("source_name")
        status = request.args.get("status")
        since = request.args.get("since")

        clauses: list[str] = []
        params: list[object] = []
        if source_name:
            clauses.append("source_name = ?")
            params.append(source_name)
        if status:
            clauses.append("status = ?")
            params.append(status.lower())
        if since:
            clauses.append("logged_at >= ?")
            params.append(since)

        where_clause, params_tuple = build_where(clauses, params)
        results = query(
            db_path,
            f"SELECT * FROM source_availability_events{where_clause} ORDER BY logged_at DESC, id DESC LIMIT ?",
            params_tuple + (limit,),
        )
        return jsonify({"count": len(results), "results": results}), 200

    @app.get("/sources/availability/latest")
    def latest_source_availability() -> tuple[object, int]:
        results = query(
            db_path,
            """
            SELECT source_name, path, status, detail, logged_at, recovered_at
            FROM source_availability_events
            WHERE id IN (
                SELECT MAX(id)
                FROM source_availability_events
                GROUP BY source_name
            )
            ORDER BY source_name ASC
            """,
        )
        return jsonify({"count": len(results), "results": results}), 200

    return app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve roof and weather data from SQLite")
    parser.add_argument("--config", default=None, help="Path to config.json")
    return parser.parse_args()


def main() -> int:
    try:
        args = parse_args()
        config = load_config(args.config)
        api_log_path = str(config["log_path"]).replace("roofobserver", "roofapi")
        setup_logging(api_log_path, name="roofapi")
        app = create_app(config)

        host = str(config["api_host"])
        port = int(config["api_port"])
        ensure_port_available(host, port)
        logging.info("RoofAPI starting on %s:%s", host, port)

        try:
            from waitress import serve
        except ImportError:
            logging.warning("waitress is not installed; falling back to Flask development server")
            app.run(host=host, port=port)
            return 0

        serve(app, host=host, port=port)
        return 0
    except Exception as exc:
        bootstrap_path = log_bootstrap_exception("roofapi", exc)
        try:
            logging.exception("RoofAPI fatal startup error")
        except Exception:
            pass
        print(f"RoofAPI fatal startup error. See {bootstrap_path}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())