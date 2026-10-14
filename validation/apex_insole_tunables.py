#!/usr/bin/env python3
"""User-modifiable Apex insole settings (defaults + API validation ranges)."""
from __future__ import annotations

DEFAULT_WALL_LOOPS = 1
WALL_LOOPS_MIN = 1
WALL_LOOPS_MAX = 6
INFILL_DENSITY_PCT_MIN = 5
INFILL_DENSITY_PCT_MAX = 100


def validate_wall_loops(value: int) -> int:
    v = int(value)
    if v < WALL_LOOPS_MIN or v > WALL_LOOPS_MAX:
        raise ValueError(f"wall_loops must be {WALL_LOOPS_MIN}–{WALL_LOOPS_MAX}")
    return v


def validate_infill_density_percent(value: float | int) -> int:
    v = int(round(float(value)))
    if v < INFILL_DENSITY_PCT_MIN or v > INFILL_DENSITY_PCT_MAX:
        raise ValueError(f"infill_density must be {INFILL_DENSITY_PCT_MIN}–{INFILL_DENSITY_PCT_MAX}%")
    return v


def format_infill_density(percent: int) -> str:
    return f"{validate_infill_density_percent(percent)}%"
