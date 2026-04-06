"""
app/database.py
---------------
Database access layer for the BioT sensor SQLite database.
SELECT-only. All errors are caught and returned as strings to the agent.
"""

import sqlite3
from pathlib import Path

from app.logger import get_logger

log = get_logger(__name__)

_MAX_ROWS = 50


def query(db_path: Path, sql: str) -> str:
    """
    Execute a read-only SQL SELECT query and return results as plain text.

    Args:
        db_path: Absolute path to the SQLite database file.
        sql:     A SQL SELECT statement. Any other statement type is rejected.

    Returns:
        Formatted string of results, or an informational message if empty/missing.
    """
    if not db_path.exists():
        log.warning("Database file not found: %s", db_path)
        return (
            "The sensor database file does not exist yet. "
            "Start the Android app and let it collect some sensor data first."
        )

    normalized = sql.strip().upper()
    if not normalized.startswith("SELECT"):
        log.warning("Blocked non-SELECT query: %s", sql[:80])
        return f"Only SELECT queries are permitted. Received: {sql[:80]!r}"

    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        log.debug("Executing: %s", sql)
        cursor.execute(sql)
        rows = cursor.fetchmany(_MAX_ROWS + 1)
        col_names = [desc[0] for desc in cursor.description]
        conn.close()

        if not rows:
            log.info("Query returned no results: %s", sql[:80])
            return "Query returned no results. The table may be empty."

        overflow = len(rows) > _MAX_ROWS
        display_rows = rows[:_MAX_ROWS]

        header = " | ".join(col_names)
        separator = "-" * len(header)
        data_lines = [" | ".join(str(v) for v in row) for row in display_rows]
        lines = [header, separator, *data_lines]

        if overflow:
            lines.append(f"... (showing first {_MAX_ROWS} rows only)")

        log.info("Query returned %d rows (overflow=%s): %s", len(display_rows), overflow, sql[:80])
        return "\n".join(lines)

    except sqlite3.OperationalError as exc:
        log.warning("SQL OperationalError for query %r: %s", sql[:80], exc)
        return f"Database query failed: {exc}"
    except sqlite3.Error as exc:
        log.error("Unexpected SQLite error for query %r: %s", sql[:80], exc, exc_info=True)
        return f"Unexpected database error: {exc}"


def get_schema(db_path: Path) -> str:
    """
    Return CREATE TABLE statements for all tables in the database.
    """
    if not db_path.exists():
        log.warning("Schema requested but database not found: %s", db_path)
        return "Database file not found."

    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute("SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = cursor.fetchall()
        conn.close()

        if not tables:
            log.info("Schema requested but database has no tables")
            return "The database exists but contains no tables yet."

        log.info("Schema returned for %d tables", len(tables))
        return "\n\n".join(f"-- {name}\n{sql}" for name, sql in tables if sql)

    except sqlite3.Error as exc:
        log.error("Could not read schema: %s", exc, exc_info=True)
        return f"Could not read schema: {exc}"