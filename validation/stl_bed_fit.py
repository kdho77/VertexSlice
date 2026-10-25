#!/usr/bin/env python3
"""STL bounding-box checks for Vertex Apex V2 belt (X = across belt width; length → model +Y → gcode Z)."""
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


def stl_signed_volume(path: Path) -> float:
    """Signed volume of STL triangles (sign indicates winding)."""
    data = path.read_bytes()
    n = struct.unpack_from("<I", data, 80)[0]
    vol = 0.0
    for i in range(n):
        vals = struct.unpack_from("<12f", data, 84 + i * 50)
        x0, y0, z0 = vals[3], vals[4], vals[5]
        x1, y1, z1 = vals[6], vals[7], vals[8]
        x2, y2, z2 = vals[9], vals[10], vals[11]
        vol += (
            x0 * (y1 * z2 - y2 * z1)
            + x1 * (y2 * z0 - y0 * z2)
            + x2 * (y0 * z1 - y1 * z0)
        )
    return vol / 6.0


def assert_oriented_extents_match_source(
    src: Path, meta: dict, *, bed_width_x_mm: float = 250.0
) -> list[str]:
    """After belt prep, across/along extents must match bounds_after_z_rotation on the source."""
    fails: list[str] = []
    rot = int(meta.get("rotation_z_deg", 0))
    expected = bounds_after_z_rotation(src, rot, bed_width_x_mm=bed_width_x_mm)
    for key in ("oriented_x_across_belt_mm", "oriented_y_along_belt_mm"):
        got = float(meta.get(key, -1))
        exp = float(expected[key])
        if abs(got - exp) > 0.02:
            fails.append(f"{key} prep {got} vs z-rotation bounds {exp}")
    return fails


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
