"""
seeding/generators.py
---------------------
Pure functions that generate fake-but-plausible sensor rows for a time window.

Each function returns a list of (timestamp_ms, x, y, z) tuples at 1-second
intervals (1,000 ms step), ready for SensorRepository.bulk_insert_*.

Data shapes:
  accel  — Z near 9.81 m/s² (gravity), X/Y near 0, slow drift + small noise
  gyro   — ±0.5 rad/s, slow oscillation + small noise
  magnet — 25-50 µT per axis, slow drift + small noise
"""

import math
import random

_STEP_MS = 1_000


def generate_accel_rows(
    start_ms: int, end_ms: int, rng: random.Random
) -> list[tuple[int, float, float, float]]:
    rows: list[tuple[int, float, float, float]] = []
    t = start_ms
    i = 0
    while t < end_ms:
        phase = i / 600.0  # one full cycle per ~10 minutes
        x = 0.05 * math.sin(phase * 2 * math.pi) + rng.gauss(0, 0.02)
        y = 0.04 * math.sin(phase * 2 * math.pi + 1.0) + rng.gauss(0, 0.02)
        z = 9.81 + 0.08 * math.sin(phase * 2 * math.pi + 2.0) + rng.gauss(0, 0.03)
        rows.append((t, round(x, 4), round(y, 4), round(z, 4)))
        t += _STEP_MS
        i += 1
    return rows


def generate_gyro_rows(
    start_ms: int, end_ms: int, rng: random.Random
) -> list[tuple[int, float, float, float]]:
    rows: list[tuple[int, float, float, float]] = []
    t = start_ms
    i = 0
    while t < end_ms:
        phase = i / 900.0  # one full cycle per 15 minutes
        x = 0.5 * math.sin(phase * 2 * math.pi) + rng.gauss(0, 0.01)
        y = 0.4 * math.sin(phase * 2 * math.pi + 1.2) + rng.gauss(0, 0.01)
        z = 0.3 * math.sin(phase * 2 * math.pi + 2.4) + rng.gauss(0, 0.01)
        rows.append((t, round(x, 5), round(y, 5), round(z, 5)))
        t += _STEP_MS
        i += 1
    return rows


def generate_magnet_rows(
    start_ms: int, end_ms: int, rng: random.Random
) -> list[tuple[int, float, float, float]]:
    rows: list[tuple[int, float, float, float]] = []
    t = start_ms
    i = 0
    while t < end_ms:
        phase = i / 1200.0  # one full cycle per 20 minutes
        x = 28.0 + 4.0 * math.sin(phase * 2 * math.pi) + rng.gauss(0, 0.5)
        y = -12.0 + 3.0 * math.sin(phase * 2 * math.pi + 1.5) + rng.gauss(0, 0.5)
        z = 42.0 + 5.0 * math.sin(phase * 2 * math.pi + 3.0) + rng.gauss(0, 0.5)
        rows.append((t, round(x, 3), round(y, 3), round(z, 3)))
        t += _STEP_MS
        i += 1
    return rows
