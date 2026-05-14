"""
database/sensor_repository.py
------------------------------
SensorRepository — typed read/write access to all sensor tables.

All writes go through DbContext.execute_write (ACID, thread-safe).
All reads use DbContext.execute_read (fresh read-only connections, WAL-safe).

Used by:
  - mcp_server/mqtt_subscriber.py   — writes live MQTT data
  - mcp_server/sensor_mcp_server.py — reads for LLM tool responses
  - app/main.py                     — reads for /data/* endpoints (Android sync)
"""

from typing import Any

from app.logger import get_logger
from database.db_context import DbContext

log = get_logger(__name__)

_VALID_TABLES = {"accel_data", "gyro_data", "magnet_data", "ereignis_data"}


class SensorRepository:
    """
    Typed access layer for the BioT sensor database.

    Args:
        db_context: The shared DbContext instance (application-scoped).
    """

    def __init__(self, db_context: DbContext) -> None:
        self._ctx = db_context

    # ── Writes ────────────────────────────────────────────────────────────────

    def insert_accel(self, timestamp: int, x: float, y: float, z: float) -> None:
        self._ctx.execute_write(
            "INSERT INTO accel_data (timestamp, accelX, accelY, accelZ) VALUES (?,?,?,?)",
            (timestamp, x, y, z),
        )
        log.debug("insert_accel ts=%d x=%.3f y=%.3f z=%.3f", timestamp, x, y, z)

    def insert_gyro(self, timestamp: int, x: float, y: float, z: float) -> None:
        self._ctx.execute_write(
            "INSERT INTO gyro_data (timestamp, gyroX, gyroY, gyroZ) VALUES (?,?,?,?)",
            (timestamp, x, y, z),
        )
        log.debug("insert_gyro ts=%d x=%.3f y=%.3f z=%.3f", timestamp, x, y, z)

    def insert_magnet(self, timestamp: int, x: float, y: float, z: float) -> None:
        self._ctx.execute_write(
            "INSERT INTO magnet_data (timestamp, magnetX, magnetY, magnetZ) VALUES (?,?,?,?)",
            (timestamp, x, y, z),
        )
        log.debug("insert_magnet ts=%d x=%.3f y=%.3f z=%.3f", timestamp, x, y, z)

    def insert_ereignis(
        self, timestamp: int, sensor_type: str, value: float, axis: str
    ) -> None:
        self._ctx.execute_write(
            "INSERT INTO ereignis_data (timestamp, sensorType, value, axis) VALUES (?,?,?,?)",
            (timestamp, sensor_type, value, axis),
        )
        log.debug(
            "insert_ereignis ts=%d type=%s value=%.3f axis=%s",
            timestamp,
            sensor_type,
            value,
            axis,
        )

    # ── Reads ─────────────────────────────────────────────────────────────────

    def get_latest(self, table: str) -> dict[str, Any] | None:
        """Return the most recent row from a sensor table, or None if empty."""
        _check_table(table)
        rows = self._ctx.execute_read(
            f"SELECT * FROM {table} ORDER BY timestamp DESC LIMIT 1"
        )
        return rows[0] if rows else None

    def get_history(
        self, table: str, since_ms: int, limit: int
    ) -> list[dict[str, Any]]:
        """Return up to `limit` rows from `table` newer than `since_ms`."""
        _check_table(table)
        return self._ctx.execute_read(
            f"SELECT * FROM {table} WHERE timestamp >= ? ORDER BY timestamp DESC LIMIT ?",
            (since_ms, limit),
        )

    def get_range(
        self, table: str, from_ms: int, to_ms: int, limit: int = 500
    ) -> list[dict[str, Any]]:
        """Return rows between two epoch-millisecond timestamps, oldest first."""
        _check_table(table)
        return self._ctx.execute_read(
            f"SELECT * FROM {table} "
            f"WHERE timestamp BETWEEN ? AND ? ORDER BY timestamp ASC LIMIT ?",
            (from_ms, to_ms, limit),
        )

    def get_stats(
        self, table: str, since_ms: int, numeric_cols: list[str]
    ) -> dict[str, dict[str, float]]:
        """Return count/min/max/avg for each column in a single query."""
        _check_table(table)
        if not numeric_cols:
            return {}
        agg_exprs = ", ".join(
            f"COUNT({c}) as cnt_{c}, MIN({c}) as mn_{c}, "
            f"MAX({c}) as mx_{c}, AVG({c}) as av_{c}"
            for c in numeric_cols
        )
        rows = self._ctx.execute_read(
            f"SELECT {agg_exprs} FROM {table} WHERE timestamp >= ?",
            (since_ms,),
        )
        if not rows:
            return {}
        row = rows[0]
        return {
            col: {
                "count": row[f"cnt_{col}"],
                "min": row[f"mn_{col}"],
                "max": row[f"mx_{col}"],
                "avg": row[f"av_{col}"],
            }
            for col in numeric_cols
            if row.get(f"cnt_{col}")
        }

    def get_row_counts(self) -> dict[str, dict[str, Any]]:
        """Return row count and latest timestamp for every sensor table."""
        result: dict[str, dict[str, Any]] = {}
        for table in sorted(_VALID_TABLES):
            rows = self._ctx.execute_read(
                f"SELECT COUNT(*) as cnt, MAX(timestamp) as latest FROM {table}"
            )
            if rows:
                result[table] = {
                    "count": rows[0]["cnt"],
                    "latest": rows[0]["latest"],
                }
        return result

    def get_schema(self) -> list[dict[str, str]]:
        """Return CREATE TABLE statements for all tables."""
        return self._ctx.execute_read(
            "SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name"
        )

    def execute_select(self, sql: str, limit: int = 50) -> list[dict[str, Any]]:
        """Execute a raw SELECT and return up to `limit` rows (LIMIT pushed into SQL)."""
        wrapped = f"SELECT * FROM ({sql.rstrip('; ')}) LIMIT {limit}"
        return self._ctx.execute_read(wrapped)

    def bulk_insert_accel(self, rows: list[tuple[int, float, float, float]]) -> None:
        self._ctx.execute_write_many(
            "INSERT INTO accel_data (timestamp, accelX, accelY, accelZ) VALUES (?,?,?,?)",
            rows,
        )

    def bulk_insert_gyro(self, rows: list[tuple[int, float, float, float]]) -> None:
        self._ctx.execute_write_many(
            "INSERT INTO gyro_data (timestamp, gyroX, gyroY, gyroZ) VALUES (?,?,?,?)",
            rows,
        )

    def bulk_insert_magnet(self, rows: list[tuple[int, float, float, float]]) -> None:
        self._ctx.execute_write_many(
            "INSERT INTO magnet_data (timestamp, magnetX, magnetY, magnetZ) VALUES (?,?,?,?)",
            rows,
        )

    def get_max_id(self, table: str) -> int:
        """Return the current MAX(id) for a table, or 0 if the table is empty."""
        _check_table(table)
        rows = self._ctx.execute_read(
            f"SELECT COALESCE(MAX(id), 0) AS m FROM {table}"
        )
        return rows[0]["m"]

    def delete_id_range(self, table: str, gt_id: int, le_id: int) -> int:
        """Delete rows where gt_id < id <= le_id. Returns the number of rows deleted."""
        _check_table(table)
        return self._ctx.execute_write(
            f"DELETE FROM {table} WHERE id > ? AND id <= ?",
            (gt_id, le_id),
        )

    def get_count_since(self, table: str, since_ms: int) -> int:
        """Return row count for rows with timestamp >= since_ms."""
        _check_table(table)
        rows = self._ctx.execute_read(
            f"SELECT COUNT(*) as cnt FROM {table} WHERE timestamp >= ?",
            (since_ms,),
        )
        return rows[0]["cnt"] if rows else 0


# ── Internal helpers ──────────────────────────────────────────────────────────


def _check_table(table: str) -> None:
    if table not in _VALID_TABLES:
        raise ValueError(
            f"Unknown table: {table!r}. Valid: {', '.join(sorted(_VALID_TABLES))}"
        )
