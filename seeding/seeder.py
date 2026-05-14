"""
seeding/seeder.py
-----------------
DatabaseSeeder — inserts fake 24h sensor rows on startup and removes them on
shutdown without touching any rows written by the live MQTT subscriber.

Cleanup strategy: capture MAX(id) per table before and after bulk-insert.
On unseed(), DELETE WHERE id > before AND id <= after. MQTT rows have
IDs above after_id so they can never be touched.
"""

import random
import time

from app.logger import get_logger
from database.sensor_repository import SensorRepository
from seeding.generators import generate_accel_rows, generate_gyro_rows, generate_magnet_rows

log = get_logger(__name__)

_STEP_MS = 1_000


class DatabaseSeeder:
    """
    Populates the sensor DB with fake historical data on server startup and
    cleans it up on shutdown.

    Args:
        repository: The application-scoped SensorRepository.
        hours: How many hours of history to generate (default 24).
        rng_seed: Seed for the random number generator (deterministic output).
    """

    def __init__(
        self,
        repository: SensorRepository,
        hours: int = 24,
        rng_seed: int = 42,
    ) -> None:
        self._repo = repository
        self._hours = hours
        self._rng = random.Random(rng_seed)
        self._ranges: dict[str, tuple[int, int]] = {}

    def seed(self) -> None:
        """Generate and insert fake rows for all three sensor tables."""
        now_ms = int(time.time() * 1000)
        start_ms = now_ms - self._hours * 3_600_000

        _TABLES = [
            ("accel_data",  generate_accel_rows,  self._repo.bulk_insert_accel),
            ("gyro_data",   generate_gyro_rows,   self._repo.bulk_insert_gyro),
            ("magnet_data", generate_magnet_rows, self._repo.bulk_insert_magnet),
        ]

        for table, gen_fn, bulk_fn in _TABLES:
            before_id = self._repo.get_max_id(table)
            rows = gen_fn(start_ms, now_ms, self._rng)
            bulk_fn(rows)
            after_id = self._repo.get_max_id(table)
            self._ranges[table] = (before_id, after_id)
            log.info(
                "Seeded %s: %d rows (id %d–%d)",
                table,
                after_id - before_id,
                before_id + 1,
                after_id,
            )

    def unseed(self) -> None:
        """Delete all rows that were inserted by seed(). Safe to call multiple times."""
        if not self._ranges:
            return
        for table, (before_id, after_id) in self._ranges.items():
            if after_id <= before_id:
                continue
            deleted = self._repo.delete_id_range(table, before_id, after_id)
            log.info(
                "Unseeded %s: %d rows removed (id %d–%d)",
                table,
                deleted,
                before_id + 1,
                after_id,
            )
        self._ranges.clear()
