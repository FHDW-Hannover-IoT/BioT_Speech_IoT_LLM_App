"""
database/db_context.py
----------------------
DbContext — owns the single persistent SQLite write connection.

Design
------
- One persistent write connection (WAL journal mode) shared across all
  threads in the main process (MQTT subscriber + FastAPI handlers).
- threading.Lock() serialises all writes — the subscriber thread and any
  API handler that writes must not race.
- Read connections are opened fresh per-query using the read-only URI
  (file:...?mode=ro).  WAL ensures reads never block concurrent writes.
- All write operations use the connection as a context manager:
    with self._write_conn: ...
  which begins a transaction, commits on clean exit, and rolls back on
  any exception — true ACID behaviour.

The MCP server runs as a separate subprocess and instantiates its own
DbContext.  It only calls execute_read() and never calls initialize(),
so no write connection is opened in that process.
"""

import sqlite3
import threading
from pathlib import Path
from typing import Any, Optional

from app.logger import get_logger

log = get_logger(__name__)

# ── Schema ────────────────────────────────────────────────────────────────────
# Mirrors the Android Room database exactly so the LLM's SQL queries work
# identically against both databases.

_DDL: list[str] = [
    # ── Raw sensor tables (1-second resolution) ──────────────────────────────
    # UNIQUE on timestamp prevents duplicate rows when seeded data and live MQTT
    # data overlap at the same second. INSERT OR IGNORE drops the duplicate silently.
    """CREATE TABLE IF NOT EXISTS accel_data (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp INTEGER NOT NULL UNIQUE,
        accelX    REAL    NOT NULL,
        accelY    REAL    NOT NULL,
        accelZ    REAL    NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS gyro_data (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp INTEGER NOT NULL UNIQUE,
        gyroX     REAL    NOT NULL,
        gyroY     REAL    NOT NULL,
        gyroZ     REAL    NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS magnet_data (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp INTEGER NOT NULL UNIQUE,
        magnetX   REAL    NOT NULL,
        magnetY   REAL    NOT NULL,
        magnetZ   REAL    NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS ereignis_data (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp  INTEGER NOT NULL,
        sensorType TEXT    NOT NULL,
        value      REAL    NOT NULL,
        axis       TEXT    NOT NULL
    )""",
    # ── 1-minute rollup tables ────────────────────────────────────────────────
    # Each row is the AVG of all raw rows in a 60-second bucket.
    # timestamp = (raw_ts / 60000) * 60000 — always the bucket's start millisecond.
    # PRIMARY KEY on timestamp acts as the upsert key for INSERT OR REPLACE recomputation.
    # Cleared and recomputed on every server startup so they're always consistent with raw data.
    """CREATE TABLE IF NOT EXISTS accel_data_1min (
        timestamp INTEGER PRIMARY KEY,
        accelX    REAL NOT NULL,
        accelY    REAL NOT NULL,
        accelZ    REAL NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS gyro_data_1min (
        timestamp INTEGER PRIMARY KEY,
        gyroX     REAL NOT NULL,
        gyroY     REAL NOT NULL,
        gyroZ     REAL NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS magnet_data_1min (
        timestamp INTEGER PRIMARY KEY,
        magnetX   REAL NOT NULL,
        magnetY   REAL NOT NULL,
        magnetZ   REAL NOT NULL
    )""",
    # ── 1-hour rollup tables ──────────────────────────────────────────────────
    # Each row is the AVG of all raw rows in a 3600-second bucket.
    # Used for 24h and 1-week views — at most 168 rows for a full week.
    """CREATE TABLE IF NOT EXISTS accel_data_1hour (
        timestamp INTEGER PRIMARY KEY,
        accelX    REAL NOT NULL,
        accelY    REAL NOT NULL,
        accelZ    REAL NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS gyro_data_1hour (
        timestamp INTEGER PRIMARY KEY,
        gyroX     REAL NOT NULL,
        gyroY     REAL NOT NULL,
        gyroZ     REAL NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS magnet_data_1hour (
        timestamp INTEGER PRIMARY KEY,
        magnetX   REAL NOT NULL,
        magnetY   REAL NOT NULL,
        magnetZ   REAL NOT NULL
    )""",
]

_INDICES: list[str] = [
    "CREATE INDEX IF NOT EXISTS idx_accel_ts        ON accel_data(timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_gyro_ts         ON gyro_data(timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_magnet_ts       ON magnet_data(timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_ereignis_ts     ON ereignis_data(timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_accel_1min_ts   ON accel_data_1min(timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_gyro_1min_ts    ON gyro_data_1min(timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_magnet_1min_ts  ON magnet_data_1min(timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_accel_1hour_ts  ON accel_data_1hour(timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_gyro_1hour_ts   ON gyro_data_1hour(timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_magnet_1hour_ts ON magnet_data_1hour(timestamp)",
]


class DbContext:
    """
    Manages the persistent SQLite write connection and schema.

    Args:
        db_path: Absolute path to the SQLite database file.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._write_conn: Optional[sqlite3.Connection] = None
        self._lock = threading.Lock()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def initialize(self) -> None:
        """
        Open the persistent write connection, enable WAL mode, create schema.
        Call once at server startup before any reads or writes.
        """
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

        self._write_conn = sqlite3.connect(
            str(self._db_path),
            check_same_thread=False,
        )
        self._write_conn.row_factory = sqlite3.Row

        # WAL: concurrent reads (MCP subprocess) and writes (subscriber) proceed
        # without blocking each other.
        self._write_conn.execute("PRAGMA journal_mode=WAL")
        self._write_conn.execute("PRAGMA foreign_keys=ON")
        # NORMAL sync is safe with WAL and avoids fsync on every commit.
        self._write_conn.execute("PRAGMA synchronous=NORMAL")

        for stmt in _DDL:
            self._write_conn.execute(stmt)
        for stmt in _INDICES:
            self._write_conn.execute(stmt)
        self._write_conn.commit()

        log.info("DbContext initialised (WAL, schema ready): %s", self._db_path)

    def close(self) -> None:
        """Close the persistent write connection. Call on server shutdown."""
        if self._write_conn:
            self._write_conn.close()
            self._write_conn = None
            log.info("DbContext closed")

    # ── Writes ────────────────────────────────────────────────────────────────

    def execute_write(self, sql: str, params: tuple = ()) -> int:
        """
        Execute one write statement inside a transaction.
        Commits on success, rolls back on any exception.
        Returns the number of rows affected (cursor.rowcount).
        """
        with self._lock:
            with self._write_conn:
                cur = self._write_conn.execute(sql, params)
                return cur.rowcount

    def execute_write_many(self, sql: str, param_list: list[tuple]) -> None:
        """Execute multiple writes in a single atomic transaction."""
        if not param_list:
            return
        with self._lock:
            with self._write_conn:
                self._write_conn.executemany(sql, param_list)

    # ── Reads ─────────────────────────────────────────────────────────────────

    def execute_read(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        """
        Execute a read-only SELECT on a fresh mode=ro connection.
        WAL ensures this never blocks concurrent writes.

        Returns a list of dicts (column name → value).
        Raises FileNotFoundError if the database file does not exist yet.
        """
        if not self._db_path.exists():
            raise FileNotFoundError(
                f"Sensor database not found at {self._db_path}. "
                "Start the server and let MQTT data flow first."
            )
        conn = sqlite3.connect(f"file:{self._db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.execute(sql, params)
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()
