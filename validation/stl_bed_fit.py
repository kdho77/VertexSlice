#!/usr/bin/env python3
"""STL bounding-box checks for Vertex Apex V2 belt (X = across belt, Y = along belt)."""
from __future__ import annotations

import struct
from pathlib import Path


def stl_axis_bounds(path: Path) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float]]:
    data = path.read_bytes()
    if data[:5] == b"solid":
        raise ValueError(f"ASCII STL not supported: {path}")
    n = struct.unpack_from("<I", data, 80)[0]
    xs: list[float] = []
    ys: list[float] = []
    zs: list[float] = []
    for i in range(n):
        vals = struct.unpack_from("<12f", data, 84 + i * 50)
        for j in range(3, 12, 3):
            xs.append(vals[j])
            ys.append(vals[j + 1])
            zs.append(vals[j + 2])
    return (min(xs), max(xs)), (min(ys), max(ys)), (min(zs), max(zs))


def bounds_after_z_rotation(
    path: Path, degrees: int, bed_width_x_mm: float = 250.0
) -> dict:
    """
    Return axis extents after rotating part about Z (toe-first along +Y belt).
    Raises ValueError if the part exceeds bed width (X).
    """
    (x0, x1), (y0, y1), (z0, z1) = stl_axis_bounds(path)
    deg = int(degrees) % 360
    if deg == 0:
        x_extent, y_extent = x1 - x0, y1 - y0
    elif deg == 90:
        x_extent, y_extent = y1 - y0, x1 - x0
    elif deg == 180:
        x_extent, y_extent = x1 - x0, y1 - y0
    elif deg == 270:
        x_extent, y_extent = y1 - y0, x1 - x0
    else:
        raise ValueError(f"unsupported rotation {degrees} (use 0/90/180/270)")

    out = {
        "stl_path": str(path),
        "rotation_z_deg": deg,
        "stl_x_mm": round(x1 - x0, 3),
        "stl_y_mm": round(y1 - y0, 3),
        "stl_z_mm": round(z1 - z0, 3),
        "oriented_x_across_belt_mm": round(x_extent, 3),
        "oriented_y_along_belt_mm": round(y_extent, 3),
        "bed_width_x_mm": bed_width_x_mm,
    }
    if x_extent > bed_width_x_mm + 0.05:
        raise ValueError(
            f"part width {x_extent:.2f} mm exceeds Apex belt width {bed_width_x_mm} mm after Z rotation {deg}°"
        )
    return out
