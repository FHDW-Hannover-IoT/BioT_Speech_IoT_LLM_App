"""
mcp_server/sensor_mcp_server.py
--------------------------------
BioT Sensor MCP Server.

A proper Model Context Protocol server that exposes sensor database tools
to the LLM agent over the Streamable HTTP transport.

Why Streamable HTTP over stdio:
    - Runs as a persistent network service on port 8002
    - Can be reached remotely when the app is deployed publicly
    - The agent connects via HTTP — same pattern works locally and in the cloud
    - stdio would only work as a subprocess of the agent, making remote deployment
      impossible without a full rearchitecture

Tools exposed (covering all use cases from LLM_USE_CASES.md):
    get_latest_sensor_data(sensor)      — UC-1.1  latest reading for one sensor
    get_sensor_history(sensor, minutes) — UC-1.2/3 readings over a time window
    get_db_schema()                     — UC-1.5  inspect table structure
    get_event_log(limit)                — UC-2.3  ereignis_data entries
    get_row_count(table)                — UC-1.4  check data availability
    execute_query(sql)                  — UC-1.x  raw SELECT for complex queries

Run standalone (for testing):
    uv run python -m mcp_server.sensor_mcp_server

Run via main.py:
    Started automatically as a subprocess when the FastAPI server starts.
"""

import sqlite3
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from app.logger import get_logger
from config.settings import settings

log = get_logger(__name__)

# ── MCP Server instance ───────────────────────────────────────────────────────

mcp = FastMCP(
    name="BioT Sensor Server",
    instructions=(
        "Provides read-only access to live IoT sensor data from an ESP8266 "
        "connected via MQTT. Tables: accel_data, gyro_data, magnet_data, "
        "ereignis_data. All timestamps are Unix milliseconds."
    ),
)

# ── Shared DB path (injected from settings) ───────────────────────────────────

_DB_PATH: Path = settings.sqlite_db_path
_MAX_ROWS = 50

# ── Sensor → table mapping ────────────────────────────────────────────────────

_SENSOR_TABLE = {
    # Accelerometer / Bewegung
    "accel":          "accel_data",
    "accelerometer":  "accel_data",
    "bewegung":       "accel_data",
    "beschleunigung": "accel_data",
    # Gyroscope
    "gyro":           "gyro_data",
    "gyroscope":      "gyro_data",
    "gyroskop":       "gyro_data",
    # Magnetometer / Hall
    "magnet":         "magnet_data",
    "magnetometer":   "magnet_data",
    "hall":           "magnet_data",
    "magnetfeld":     "magnet_data",
    # Events
    "ereignis":       "ereignis_data",
    "events":         "ereignis_data",
    "event":          "ereignis_data",
}

_VALID_TABLES = {"accel_data", "gyro_data", "magnet_data", "ereignis_data"}


# ── Internal helpers ──────────────────────────────────────────────────────────

def _get_conn() -> sqlite3.Connection:
    """Open a read-only connection to the sensor database."""
    if not _DB_PATH.exists():
        raise FileNotFoundError(
            f"Sensor database not found at {_DB_PATH}. "
            "Start the MQTT subscriber and let the Android app collect data first."
        )
    conn = sqlite3.connect(f"file:{_DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _rows_to_text(rows: list, col_names: list[str], overflow: bool) -> str:
    """Format query results as a readable table string."""
    if not rows:
        return "No data found. The table may be empty."
    header = " | ".join(col_names)
    sep = "-" * len(header)
    lines = [header, sep] + [" | ".join(str(v) for v in row) for row in rows]
    if overflow:
        lines.append(f"... (showing first {_MAX_ROWS} rows only)")
    return "\n".join(lines)


def _resolve_table(sensor: str) -> str:
    """Resolve a human-readable sensor name to a table name."""
    table = _SENSOR_TABLE.get(sensor.lower().strip())
    if not table:
        valid = ", ".join(sorted(set(_SENSOR_TABLE.keys())))
        raise ValueError(
            f"Unknown sensor: {sensor!r}. Valid values: {valid}"
        )
    return table


# ── MCP Tools ─────────────────────────────────────────────────────────────────

@mcp.tool()
def get_latest_sensor_data(sensor: str) -> str:
    """
    Get the single most recent reading for a sensor.

    Use this for questions like:
        "What is the current accelerometer value?"
        "Was ist der aktuelle Gyro-Wert?"
        "Show me the latest magnet reading"

    Args:
        sensor: Sensor name. Accepts: accel, gyro, magnet, bewegung,
                beschleunigung, gyroskop, magnetfeld, hall (German and English).

    Returns:
        The most recent row from the sensor table with a human-readable timestamp.
    """
    log.info("MCP tool: get_latest_sensor_data(sensor=%r)", sensor)
    try:
        table = _resolve_table(sensor)
        conn = _get_conn()
        cursor = conn.execute(
            f"SELECT * FROM {table} ORDER BY timestamp DESC LIMIT 1"
        )
        rows = cursor.fetchall()
        col_names = [d[0] for d in cursor.description]
        conn.close()

        if not rows:
            return f"No data in {table} yet. Is the MQTT subscriber running?"

        # Format timestamp as relative time for readability
        row = dict(rows[0])
        ts_ms = row.get("timestamp", 0)
        age_s = (time.time() * 1000 - ts_ms) / 1000
        age_str = (
            f"{age_s:.0f} seconds ago" if age_s < 120
            else f"{age_s/60:.1f} minutes ago"
        )

        result = _rows_to_text(rows, col_names, False)
        return f"{result}\n\nRecorded: {age_str}"

    except (FileNotFoundError, ValueError) as exc:
        log.warning("get_latest_sensor_data error: %s", exc)
        return str(exc)
    except sqlite3.Error as exc:
        log.error("DB error in get_latest_sensor_data: %s", exc, exc_info=True)
        return f"Database error: {exc}"


@mcp.tool()
def get_sensor_history(sensor: str, minutes: int = 10) -> str:
    """
    Get sensor readings from the last N minutes.

    Use this for questions like:
        "Show me the last 10 minutes of gyro data"
        "Zeige die Gyro-Daten der letzten Stunde"
        "What was the average acceleration this morning?"
        "Were there any spikes in the accelerometer data?"

    Returns up to 50 rows ordered newest first, plus a summary
    (count, min, max, average per axis).

    Args:
        sensor:  Sensor name (accel, gyro, magnet — German/English).
        minutes: How many minutes back to fetch. Default 10.

    Returns:
        Tabular data plus a statistical summary (count, min, max, avg per axis).
    """
    log.info("MCP tool: get_sensor_history(sensor=%r, minutes=%d)", sensor, minutes)
    try:
        table = _resolve_table(sensor)
        if table == "ereignis_data":
            return "For event history use get_event_log() instead."

        since_ms = int((time.time() - minutes * 60) * 1000)

        conn = _get_conn()

        # Fetch rows
        cursor = conn.execute(
            f"SELECT * FROM {table} WHERE timestamp >= ? ORDER BY timestamp DESC LIMIT ?",
            (since_ms, _MAX_ROWS + 1),
        )
        rows = cursor.fetchall()
        col_names = [d[0] for d in cursor.description]
        overflow = len(rows) > _MAX_ROWS
        display_rows = rows[:_MAX_ROWS]

        # Compute summary statistics per numeric column
        numeric_cols = [c for c in col_names if c not in ("id", "timestamp")]
        summary_lines = [f"\nSummary for last {minutes} min ({len(display_rows)} rows):"]

        for col in numeric_cols:
            stats = conn.execute(
                f"SELECT COUNT(*), MIN({col}), MAX({col}), AVG({col}) "
                f"FROM {table} WHERE timestamp >= ?",
                (since_ms,),
            ).fetchone()
            if stats and stats[0]:
                summary_lines.append(
                    f"  {col}: count={stats[0]}, min={stats[1]:.3f}, "
                    f"max={stats[2]:.3f}, avg={stats[3]:.3f}"
                )

        conn.close()

        data_text = _rows_to_text(display_rows, col_names, overflow)
        return data_text + "\n".join(summary_lines)

    except (FileNotFoundError, ValueError) as exc:
        log.warning("get_sensor_history error: %s", exc)
        return str(exc)
    except sqlite3.Error as exc:
        log.error("DB error in get_sensor_history: %s", exc, exc_info=True)
        return f"Database error: {exc}"


@mcp.tool()
def get_db_schema() -> str:
    """
    Return the CREATE TABLE statements for all tables in the sensor database.

    Use this when you need to know which columns a table has before
    writing a query, or when the user asks what data is stored.

    Use this for questions like:
        "What tables are in the database?"
        "Welche Daten werden gespeichert?"
        "What columns does the gyro table have?"

    Returns:
        CREATE TABLE statements for all tables, plus a plain-language description.
    """
    log.info("MCP tool: get_db_schema()")
    try:
        conn = _get_conn()
        cursor = conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = cursor.fetchall()
        conn.close()

        if not tables:
            return (
                "The database exists but contains no tables yet. "
                "Ensure the MQTT subscriber has been running and sensor data is flowing."
            )

        schema_lines = []
        descriptions = {
            "accel_data":    "MPU-6050 accelerometer — accelX/Y/Z in g-force",
            "gyro_data":     "MPU-6050 gyroscope — gyroX/Y/Z in degrees/second",
            "magnet_data":   "A3144 hall effect sensor — magnetX/Y/Z",
            "ereignis_data": "Threshold events — sensorType (ACCEL/GYRO/MAGNET), value, axis",
        }

        for name, sql in tables:
            if sql:
                desc = descriptions.get(name, "")
                schema_lines.append(f"-- {name}: {desc}\n{sql}\n")

        log.info("Schema returned for %d tables", len(tables))
        return "\n".join(schema_lines)

    except FileNotFoundError as exc:
        log.warning("get_db_schema: %s", exc)
        return str(exc)
    except sqlite3.Error as exc:
        log.error("DB error in get_db_schema: %s", exc, exc_info=True)
        return f"Database error: {exc}"


@mcp.tool()
def get_event_log(limit: int = 10) -> str:
    """
    Return recent threshold events from the ereignis_data table.

    Use this for questions like:
        "How many events were triggered today?"
        "Zeige die letzten 5 Ereignisse"
        "Were there any ACCEL events recently?"
        "Show me all gyro threshold crossings"

    Args:
        limit: Maximum number of events to return (default 10, max 50).

    Returns:
        Recent events with sensorType, value, axis, and human-readable timestamps.
    """
    log.info("MCP tool: get_event_log(limit=%d)", limit)
    limit = min(limit, _MAX_ROWS)

    try:
        conn = _get_conn()
        cursor = conn.execute(
            "SELECT * FROM ereignis_data ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        )
        rows = cursor.fetchall()
        col_names = [d[0] for d in cursor.description]

        # Also get today's count for context
        today_start_ms = int(
            (time.time() - (time.time() % 86400)) * 1000
        )
        today_count = conn.execute(
            "SELECT COUNT(*) FROM ereignis_data WHERE timestamp >= ?",
            (today_start_ms,),
        ).fetchone()[0]

        conn.close()

        result = _rows_to_text(rows, col_names, False)
        return f"{result}\n\nEvents today: {today_count}"

    except FileNotFoundError as exc:
        log.warning("get_event_log: %s", exc)
        return str(exc)
    except sqlite3.Error as exc:
        log.error("DB error in get_event_log: %s", exc, exc_info=True)
        return f"Database error: {exc}"


@mcp.tool()
def get_row_count(table: str = "all") -> str:
    """
    Return row counts to check data availability.

    Use this for questions like:
        "Is there any data in the database?"
        "How many accelerometer readings have been recorded?"
        "Wie viele Einträge gibt es?"
        "Gibt es Gyro-Daten?"

    Args:
        table: Table name (accel_data, gyro_data, magnet_data, ereignis_data)
               or "all" to get counts for every table.

    Returns:
        Row count(s) and timestamp of the most recent entry per table.
    """
    log.info("MCP tool: get_row_count(table=%r)", table)
    try:
        conn = _get_conn()
        tables_to_check = (
            list(_VALID_TABLES) if table.lower() == "all"
            else [table.lower().strip()]
        )

        for t in tables_to_check:
            if t not in _VALID_TABLES:
                conn.close()
                return f"Unknown table: {t!r}. Valid: {', '.join(sorted(_VALID_TABLES))}"

        lines = []
        for t in sorted(tables_to_check):
            count = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            latest = conn.execute(
                f"SELECT MAX(timestamp) FROM {t}"
            ).fetchone()[0]

            if latest:
                age_s = (time.time() * 1000 - latest) / 1000
                age_str = (
                    f"{age_s:.0f}s ago" if age_s < 120
                    else f"{age_s/60:.1f}min ago"
                )
                lines.append(f"{t}: {count} rows, latest {age_str}")
            else:
                lines.append(f"{t}: 0 rows (empty)")

        conn.close()
        return "\n".join(lines)

    except FileNotFoundError as exc:
        log.warning("get_row_count: %s", exc)
        return str(exc)
    except sqlite3.Error as exc:
        log.error("DB error in get_row_count: %s", exc, exc_info=True)
        return f"Database error: {exc}"


@mcp.tool()
def execute_query(sql: str) -> str:
    """
    Execute a raw read-only SQL SELECT query against the sensor database.

    Use this for complex queries not covered by the other tools, such as:
        - Custom time range filters
        - Aggregations (AVG, MIN, MAX, COUNT with WHERE clauses)
        - JOINs across tables
        - Trend analysis across many rows

    Only SELECT statements are permitted. Any other statement is rejected.

    Args:
        sql: A valid SQLite SELECT statement.

    Returns:
        Query results as a formatted table, or an error message.
    """
    log.info("MCP tool: execute_query(sql=%r)", sql[:100])

    if not sql.strip().upper().startswith("SELECT"):
        log.warning("Blocked non-SELECT query: %s", sql[:80])
        return f"Only SELECT queries are permitted. Received: {sql[:80]!r}"

    try:
        conn = _get_conn()
        cursor = conn.execute(sql)
        rows = cursor.fetchmany(_MAX_ROWS + 1)
        col_names = [d[0] for d in cursor.description]
        overflow = len(rows) > _MAX_ROWS
        conn.close()

        log.info("execute_query returned %d rows", min(len(rows), _MAX_ROWS))
        return _rows_to_text(rows[:_MAX_ROWS], col_names, overflow)

    except FileNotFoundError as exc:
        log.warning("execute_query: %s", exc)
        return str(exc)
    except sqlite3.OperationalError as exc:
        log.warning("execute_query SQL error: %s", exc)
        return f"SQL error: {exc}"
    except sqlite3.Error as exc:
        log.error("DB error in execute_query: %s", exc, exc_info=True)
        return f"Database error: {exc}"


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = settings.mcp_server_port
    log.info("Starting BioT Sensor MCP Server on port %d", port)
    print(f"BioT Sensor MCP Server starting on http://0.0.0.0:{port}/mcp")
    mcp.run(transport="streamable-http", host="0.0.0.0", port=port, path="/mcp")