#!/usr/bin/env python3
"""Cura-matched gantry Y trim for belt OrthoCAD insoles (BeltTransform y_mach_offset)."""
from __future__ import annotations

import math
import struct
from pathlib import Path


def stl_min_y_plus_z_mm(stl_path: Path) -> float:
    data = stl_path.read_bytes()
    if data[:5] == b"solid":
        raise ValueError(f"ASCII STL not supported: {stl_path}")
    n = struct.unpack_from("<I", data, 80)[0]
    best = float("inf")
    for i in range(n):
        vals = struct.unpack_from("<12f", data, 84 + i * 50)
        for j in range(3, 12, 3):
            y, z = vals[j + 1], vals[j + 2]
            best = min(best, y + z)
    return best


def cura_matched_y_mach_offset_mm(
    stl_path: Path,
    *,
    initial_layer_print_height_mm: float = 0.42,
    belt_angle_deg: float = 45.0,
) -> float:
    """
    Offset applied to belt inverse Y_mach so first-layer extrusion matches Cura ref (~0.066 mm).

    Derived from Z_virt_min on the bed-placed mesh vs the first slice plane (initial_layer / cos(angle)).
    """
    z_virt_min = stl_min_y_plus_z_mm(stl_path)
    layer0_virt = initial_layer_print_height_mm / math.cos(math.radians(belt_angle_deg))
    return -(z_virt_min - layer0_virt) / math.sqrt(2.0)


def write_belt_transform_ini(
    ini_path: Path,
    stl_path: Path,
    *,
    initial_layer_print_height_mm: float = 0.42,
    belt_angle_deg: float = 45.0,
) -> float:
    offset = cura_matched_y_mach_offset_mm(
        stl_path,
        initial_layer_print_height_mm=initial_layer_print_height_mm,
        belt_angle_deg=belt_angle_deg,
    )
    ini_path.write_text(f"y_mach_offset = {offset:.6f}\n", encoding="utf-8")
    return offset
