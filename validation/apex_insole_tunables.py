#!/usr/bin/env python3
"""User-modifiable Apex insole settings (defaults + API validation ranges)."""
from __future__ import annotations

DEFAULT_WALL_LOOPS = 1
WALL_LOOPS_MIN = 1
WALL_LOOPS_MAX = 6
INFILL_DENSITY_PCT_MIN = 5
INFILL_DENSITY_PCT_MAX = 100

PRINT_DIRECTION_TOE_FIRST = "toe_first"
PRINT_DIRECTION_HEEL_FIRST = "heel_first"
DEFAULT_PRINT_DIRECTION = PRINT_DIRECTION_TOE_FIRST
PRINT_DIRECTION_CHOICES = (PRINT_DIRECTION_TOE_FIRST, PRINT_DIRECTION_HEEL_FIRST)


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


def validate_print_direction(value: str | None) -> str:
    if value is None or str(value).strip() == "":
        return DEFAULT_PRINT_DIRECTION
    v = str(value).strip().lower()
    if v not in PRINT_DIRECTION_CHOICES:
        raise ValueError(f"print_direction must be one of: {', '.join(PRINT_DIRECTION_CHOICES)}")
    return v


def orthocad_base_rotation_z_deg(print_direction: str) -> int:
    """90° aligns scan length with belt +Y; toe_first adds 180° (Kendon default)."""
    deg = 90
    if validate_print_direction(print_direction) == PRINT_DIRECTION_TOE_FIRST:
        deg += 180
    return deg
