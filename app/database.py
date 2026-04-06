"""
app/database.py
---------------
Database access layer for the BioT sensor SQLite database.

Responsibilities:
- Provide a single entry point for all database queries.
- Enforce read-only access (SELECT only) to prevent accidental writes.
- Format query results into readable plain-text for the LLM agent.
- Handle all SQLite errors gracefully without crashing the server.

The database schema mirrors the Android Room database:
    accel_data   — accelerometer readings (timestamp, accelX, accelY, accelZ)
    gyro_data    — gyroscope readings    (timestamp, gyroX,  gyroY,  gyroZ)
    magnet_data  — magnetometer readings (timestamp, magnetX, magnetY, magnetZ)
    ereignis_data — sensor events        (timestamp, sensorType, value, axis)
"""

import sqlite3
from pathlib import Path


# Maximum number of rows returned per query to prevent oversized LLM context windows
_MAX_ROWS = 50


def query(db_path: Path, sql: str) -> str:
    """
    Execute a read-only SQL SELECT query against the sensor database and
    return the results formatted as a human-readable plain-text table.

    Args:
        db_path: Absolute path to the SQLite database file.
        sql:     A SQL SELECT statement. Any other statement type is rejected.

    Returns:
        A formatted string containing column headers and rows, or an
        informational message if the database is empty or missing.

    Security:
        Only SELECT statements are permitted. Any attempt to pass INSERT,
        UPDATE, DELETE, DROP, etc. is rejected before execution.
    """
    # ── Guard: database file must exist ──────────────────────────────────────
    if not db_path.exists():
        return (
            "The sensor database file does not exist yet. "
            "Start the Android app and let it collect some sensor data first."
        )

    # ── Guard: only SELECT statements allowed ─────────────────────────────────
    normalized = sql.strip().upper()
    if not normalized.startswith("SELECT"):
        return (
            "Only SELECT queries are permitted. "
            f"Received: {sql[:80]!r}"
        )

    try:
        # Use row_factory so column names are accessible
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute(sql)
        rows = cursor.fetchmany(_MAX_ROWS + 1)  # fetch one extra to detect overflow
        col_names = [desc[0] for desc in cursor.description]
        conn.close()

        if not rows:
            return "Query returned no results. The table may be empty."

        overflow = len(rows) > _MAX_ROWS
        display_rows = rows[:_MAX_ROWS]

        # ── Format as a plain-text table ──────────────────────────────────────
        header = " | ".join(col_names)
        separator = "-" * len(header)
        data_lines = [
            " | ".join(str(value) for value in row)
            for row in display_rows
        ]

        lines = [header, separator, *data_lines]

        if overflow:
            lines.append(f"... (showing first {_MAX_ROWS} rows only)")

        return "\n".join(lines)

    except sqlite3.OperationalError as exc:
        # Common cause: table doesn't exist yet (app hasn't written data)
        return f"Database query failed: {exc}"
    except sqlite3.Error as exc:
        return f"Unexpected database error: {exc}"


def get_schema(db_path: Path) -> str:
    """
    Return the CREATE TABLE statements for all tables in the database.
    Useful for letting the agent understand what data is available before
    constructing queries.

    Args:
        db_path: Absolute path to the SQLite database file.

    Returns:
        A string listing all table schemas, or a message if empty/missing.
    """
    if not db_path.exists():
        return "Database file not found."

    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = cursor.fetchall()
        conn.close()

        if not tables:
            return "The database exists but contains no tables yet."

        return "\n\n".join(
            f"-- {name}\n{sql}" for name, sql in tables if sql
        )

    except sqlite3.Error as exc:
        return f"Could not read schema: {exc}"