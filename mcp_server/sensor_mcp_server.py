"""
mcp_server/sensor_mcp_server.py
--------------------------------
BioT Sensor MCP Server.

A Model Context Protocol server that exposes sensor database tools to the
LLM agent over the Streamable HTTP transport (JSON-RPC 2.0, POST /mcp).

This process runs independently of the FastAPI main process.  It creates its
own DbContext (read-only — never calls initialize()) and SensorRepository.
WAL mode on the shared SQLite file ensures reads here never block the
subscriber's writes in the main process.

Tools exposed:
    get_latest_sensor_data(sensor)      — most recent row for one sensor
    get_value_for_axis(sensor, axis)    — most recent value for one axis
    get_sensor_history(sensor, minutes) — time-windowed rows + stats
    get_db_schema()                     — CREATE TABLE statements
    get_event_log(limit)                — ereignis_data entries
    get_row_count(table)                — per-table counts + latest timestamp
    execute_query(sql)                  — read-only SELECT (mode=ro enforced)

Run standalone (for testing):
    uv run python -m mcp_server.sensor_mcp_server

Run via main.py:
    Started automatically as a subprocess when the FastAPI server starts.
"""

import time

from mcp.server.fastmcp import FastMCP

from app.logger import get_logger
from config.settings import settings
from database.db_context import DbContext
from database.sensor_repository import SensorRepository

log = get_logger(__name__)

# ── Database (read-only access from this subprocess) ──────────────────────────
# DbContext is instantiated without initialize() — this process only reads.
# execute_read() opens fresh mode=ro connections per query.

_db_context = DbContext(settings.sqlite_db_path)
_repo       = SensorRepository(_db_context)

# ── MCP Server instance ───────────────────────────────────────────────────────

mcp = FastMCP(
    name="BioT Sensor Server",
    instructions=(
        "Provides read-only access to live IoT sensor data from an ESP8266 "
        "connected via MQTT. Tables: accel_data, gyro_data, magnet_data, "
        "ereignis_data. All timestamps are Unix milliseconds."
    ),
)

# ── Sensor → table mapping ────────────────────────────────────────────────────

_SENSOR_TABLE = {
    "accel":          "accel_data",
    "accelerometer":  "accel_data",
    "bewegung":       "accel_data",
    "beschleunigung": "accel_data",
    "gyro":           "gyro_data",
    "gyroscope":      "gyro_data",
    "gyroskop":       "gyro_data",
    "magnet":         "magnet_data",
    "magnetometer":   "magnet_data",
    "hall":           "magnet_data",
    "magnetfeld":     "magnet_data",
    "ereignis":       "ereignis_data",
    "events":         "ereignis_data",
    "event":          "ereignis_data",
}

_AXIS_COLUMNS = {
    "accel_data":  {"x": "accelX",  "y": "accelY",  "z": "accelZ"},
    "gyro_data":   {"x": "gyroX",   "y": "gyroY",   "z": "gyroZ"},
    "magnet_data": {"x": "magnetX", "y": "magnetY", "z": "magnetZ"},
}


# ── Internal helpers ──────────────────────────────────────────────────────────

def _resolve_table(sensor: str) -> str:
    table = _SENSOR_TABLE.get(sensor.lower().strip())
    if not table:
        valid = ", ".join(sorted(set(_SENSOR_TABLE.keys())))
        raise ValueError(f"Unknown sensor: {sensor!r}. Valid values: {valid}")
    return table


def _rows_to_text(rows: list[dict], overflow: bool) -> str:
    if not rows:
        return "No data found. The table may be empty."
    col_names = list(rows[0].keys())
    header = " | ".join(col_names)
    sep    = "-" * len(header)
    lines  = [header, sep] + [
        " | ".join(str(row[c]) for c in col_names) for row in rows
    ]
    if overflow:
        lines.append(f"... (showing first {settings.mcp_max_rows} rows only)")
    return "\n".join(lines)


def _format_age(ts_ms: int) -> str:
    age_s = (time.time() * 1000 - ts_ms) / 1000
    if age_s < 120:
        return f"{age_s:.0f} seconds ago"
    return f"{age_s / 60:.1f} minutes ago"


# ── MCP Tools ─────────────────────────────────────────────────────────────────

@mcp.tool()
def get_latest_sensor_data(sensor: str) -> str:
    """
    Get the single most recent reading for a sensor.

    Use for: "What is the current accelerometer value?",
    "Was ist der aktuelle Gyro-Wert?", "Show me the latest reading".

    Args:
        sensor: Sensor name (accel, gyro, magnet — German or English).
    """
    log.info("MCP tool: get_latest_sensor_data(sensor=%r)", sensor)
    try:
        table = _resolve_table(sensor)
        row   = _repo.get_latest(table)

        if not row:
            return f"No data in {table} yet. Is the MQTT subscriber running?"

        ts_ms  = row.get("timestamp", 0)
        lines  = [" | ".join(str(v) for v in row.values())]
        header = " | ".join(row.keys())
        result = f"{header}\n{'-' * len(header)}\n{lines[0]}"
        return f"{result}\n\nRecorded: {_format_age(ts_ms)}"

    except (FileNotFoundError, ValueError) as exc:
        log.warning("get_latest_sensor_data error: %s", exc)
        return str(exc)
    except Exception as exc:
        log.error("DB error in get_latest_sensor_data: %s", exc, exc_info=True)
        return f"Database error: {exc}"


@mcp.tool()
def get_value_for_axis(sensor: str, axis: str) -> str:
    """
    Get the latest value for a single axis (X, Y, or Z) of a sensor.

    Implements the 'Tell value (sensor) (axis)' command, e.g.
    "Tell me the gyro X value", "Sage mir die Beschleunigung Z".

    Args:
        sensor: Sensor name (accel, gyro, magnet — German or English).
        axis:   Single axis: x, y, or z (case-insensitive).
    """
    log.info("MCP tool: get_value_for_axis(sensor=%r, axis=%r)", sensor, axis)
    try:
        table = _resolve_table(sensor)
        if table == "ereignis_data":
            return "ereignis_data is per-event. Use get_event_log instead."

        axis_key = axis.lower().strip()
        axis_map = _AXIS_COLUMNS.get(table)
        if not axis_map or axis_key not in axis_map:
            return f"Unknown axis {axis!r} for {sensor!r}. Valid: x, y, z."

        column = axis_map[axis_key]
        rows   = _db_context.execute_read(
            f"SELECT timestamp, {column} FROM {table} ORDER BY timestamp DESC LIMIT 1"
        )

        if not rows:
            return f"No data in {table} yet."

        ts_ms = rows[0]["timestamp"]
        value = rows[0][column]
        return (
            f"{sensor.capitalize()} axis {axis_key.upper()} = {value:.3f} "
            f"({_format_age(ts_ms)})"
        )

    except (FileNotFoundError, ValueError) as exc:
        log.warning("get_value_for_axis error: %s", exc)
        return str(exc)
    except Exception as exc:
        log.error("DB error in get_value_for_axis: %s", exc, exc_info=True)
        return f"Database error: {exc}"


@mcp.tool()
def get_sensor_history(sensor: str, minutes: int = 10) -> str:
    """
    Get sensor readings from the last N minutes with a statistical summary.

    Use for: anomaly detection, trend analysis, "Show last N minutes of data",
    "Were there any spikes?", "Zeige die Gyro-Daten der letzten Stunde".

    Args:
        sensor:  Sensor name (accel, gyro, magnet — German or English).
        minutes: How many minutes back to fetch. Default 10.
    """
    log.info("MCP tool: get_sensor_history(sensor=%r, minutes=%d)", sensor, minutes)
    try:
        table    = _resolve_table(sensor)
        if table == "ereignis_data":
            return "For event history use get_event_log() instead."

        since_ms = int((time.time() - minutes * 60) * 1000)
        limit    = settings.mcp_max_rows

        rows     = _repo.get_history(table, since_ms, limit + 1)
        overflow = len(rows) > limit
        display  = rows[:limit]

        numeric_cols = [c for c in (display[0].keys() if display else [])
                        if c not in ("id", "timestamp")]
        stats = _repo.get_stats(table, since_ms, numeric_cols)

        summary_lines = [f"\nSummary for last {minutes} min ({len(display)} rows):"]
        for col, s in stats.items():
            summary_lines.append(
                f"  {col}: count={s['count']}, min={s['min']:.3f}, "
                f"max={s['max']:.3f}, avg={s['avg']:.3f}"
            )

        return _rows_to_text(display, overflow) + "\n".join(summary_lines)

    except (FileNotFoundError, ValueError) as exc:
        log.warning("get_sensor_history error: %s", exc)
        return str(exc)
    except Exception as exc:
        log.error("DB error in get_sensor_history: %s", exc, exc_info=True)
        return f"Database error: {exc}"


@mcp.tool()
def get_db_schema() -> str:
    """
    Return CREATE TABLE statements for all tables in the sensor database.

    Use for: "What tables are in the database?",
    "Welche Daten werden gespeichert?", "What columns does gyro have?"
    """
    log.info("MCP tool: get_db_schema()")
    try:
        tables = _repo.get_schema()
        if not tables:
            return (
                "The database exists but contains no tables yet. "
                "Ensure the MQTT subscriber has been running."
            )

        descriptions = {
            "accel_data":    "MPU-6050 accelerometer — accelX/Y/Z in g-force",
            "gyro_data":     "MPU-6050 gyroscope — gyroX/Y/Z in degrees/second",
            "magnet_data":   "A3144 hall effect sensor — magnetX/Y/Z",
            "ereignis_data": "Threshold events — sensorType, value, axis",
        }

        lines = []
        for row in tables:
            name, sql = row["name"], row["sql"]
            if sql:
                lines.append(f"-- {name}: {descriptions.get(name, '')}\n{sql}\n")

        log.info("Schema returned for %d tables", len(tables))
        return "\n".join(lines)

    except FileNotFoundError as exc:
        log.warning("get_db_schema: %s", exc)
        return str(exc)
    except Exception as exc:
        log.error("DB error in get_db_schema: %s", exc, exc_info=True)
        return f"Database error: {exc}"


@mcp.tool()
def get_event_log(limit: int = 10) -> str:
    """
    Return recent threshold events from the ereignis_data table.

    Use for: "Show notifications", "Zeige die letzten Ereignisse",
    "Were there any ACCEL events recently?", SHOW_NOTIFICATIONS commands.

    Args:
        limit: Maximum number of events to return (default 10, capped at mcp_max_rows).
    """
    log.info("MCP tool: get_event_log(limit=%d)", limit)
    limit = min(limit, settings.mcp_max_rows)

    try:
        rows = _repo.get_history("ereignis_data", 0, limit)

        today_start_ms = int((time.time() - (time.time() % 86400)) * 1000)
        today_count    = _repo.get_count_since("ereignis_data", today_start_ms)

        result = _rows_to_text(rows, False)
        return f"{result}\n\nEvents today: {today_count}"

    except FileNotFoundError as exc:
        log.warning("get_event_log: %s", exc)
        return str(exc)
    except Exception as exc:
        log.error("DB error in get_event_log: %s", exc, exc_info=True)
        return f"Database error: {exc}"


@mcp.tool()
def get_row_count(table: str = "all") -> str:
    """
    Return row counts and latest timestamp per table to check data availability.

    Use for: "Is there any data?", "How many accel readings?",
    "Wie viele Eintraege gibt es?", "Gibt es Gyro-Daten?"

    Args:
        table: Table name or "all" for all tables.
    """
    log.info("MCP tool: get_row_count(table=%r)", table)
    try:
        counts = _repo.get_row_counts()
        if table.lower() != "all":
            t = table.lower().strip()
            if t not in counts:
                return f"Unknown table: {t!r}. Valid: {', '.join(sorted(counts))}"
            counts = {t: counts[t]}

        lines = []
        for t, info in sorted(counts.items()):
            cnt    = info["count"]
            latest = info["latest"]
            if latest:
                lines.append(f"{t}: {cnt} rows, latest {_format_age(latest)}")
            else:
                lines.append(f"{t}: 0 rows (empty)")

        return "\n".join(lines)

    except FileNotFoundError as exc:
        log.warning("get_row_count: %s", exc)
        return str(exc)
    except Exception as exc:
        log.error("DB error in get_row_count: %s", exc, exc_info=True)
        return f"Database error: {exc}"


@mcp.tool()
def execute_query(sql: str) -> str:
    """
    Execute a raw read-only SQL SELECT against the sensor database.

    The connection is opened in SQLite read-only mode (file:...?mode=ro) so
    any write statement is rejected by SQLite itself — not just by a string check.

    Use for: custom time ranges, aggregations, JOINs, trend analysis.

    Args:
        sql: A valid SQLite SELECT statement.
    """
    log.info("MCP tool: execute_query(sql=%r)", sql[:100])

    # Belt-and-suspenders guard — the mode=ro connection already blocks writes
    if not sql.strip().upper().startswith("SELECT"):
        log.warning("Blocked non-SELECT query: %s", sql[:80])
        return f"Only SELECT queries are permitted. Received: {sql[:80]!r}"

    try:
        limit = settings.mcp_max_rows
        rows  = _repo.execute_select(sql, limit + 1)
        overflow = len(rows) > limit
        log.info("execute_query returned %d rows", min(len(rows), limit))
        return _rows_to_text(rows[:limit], overflow)

    except FileNotFoundError as exc:
        log.warning("execute_query: %s", exc)
        return str(exc)
    except Exception as exc:
        log.warning("execute_query error: %s", exc)
        return f"Query error: {exc}"


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    port = settings.mcp_server_port
    log.info("Starting BioT Sensor MCP Server on port %d", port)
    print(f"BioT Sensor MCP Server starting on http://0.0.0.0:{port}/mcp")

    starlette_app = mcp.streamable_http_app()
    uvicorn.run(starlette_app, host="0.0.0.0", port=port)
