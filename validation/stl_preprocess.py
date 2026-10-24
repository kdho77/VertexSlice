#!/usr/bin/env python3
"""Rigid STL transforms for belt placement (rotate + translate only, never scale)."""
from __future__ import annotations

import struct
import tempfile
from pathlib import Path

from apex_bed_placement import APEX_INSOLE_X_CENTER_MM, APEX_PRINTABLE_X_MAX_MM
from apex_insole_tunables import DEFAULT_PRINT_DIRECTION, orthocad_base_rotation_z_deg, validate_print_direction


def _read_triangles(path: Path) -> list[tuple[tuple[float, float, float], ...]]:
    data = path.read_bytes()
    if data[:5] == b"solid":
        raise ValueError(f"ASCII STL not supported: {path}")
    n = struct.unpack_from("<I", data, 80)[0]
    tris: list[tuple[tuple[float, float, float], ...]] = []
    for i in range(n):
        vals = struct.unpack_from("<12f", data, 84 + i * 50)
        verts = tuple((vals[j], vals[j + 1], vals[j + 2]) for j in range(3, 12, 3))
        tris.append(verts)
    return tris


def _write_triangles(path: Path, tris: list[tuple[tuple[float, float, float], ...]]) -> None:
    with path.open("wb") as f:
        header = b"vertexslice rigid preprocess" + b"\0" * (80 - 28)
        f.write(header[:80])
        f.write(struct.pack("<I", len(tris)))
        for verts in tris:
            f.write(struct.pack("<12f", 0.0, 0.0, 1.0, *verts[0], *verts[1], *verts[2]))
            f.write(struct.pack("<H", 0))


def _rotate_z(x: float, y: float, degrees: int) -> tuple[float, float]:
    import math

    rad = math.radians(degrees)
    c, s = math.cos(rad), math.sin(rad)
    return x * c - y * s, x * s + y * c


def prepare_belt_stl(
    src: Path,
    rotate_z_deg: int,
    bed_width_x_mm: float = APEX_PRINTABLE_X_MAX_MM,
    x_center_mm: float = APEX_INSOLE_X_CENTER_MM,
) -> tuple[Path, dict]:
    """
    Orient for Apex belt slicing (model frame before Orca belt trafo):
    - width across belt → model +X
    - length / toe-first advance → model +Y (maps to gcode Z along belt)
    - thickness → model +Z

    Applies Z rotation, places Y/Z min at 0, centers X at ``x_center_mm`` (Cura ref ≈140 on 250 mm bed).
    Raises ValueError if across-belt width exceeds ``bed_width_x_mm``.
    """
    tris = _read_triangles(src)
    rotated: list[tuple[tuple[float, float, float], ...]] = []
    xs: list[float] = []
    ys: list[float] = []
    zs: list[float] = []
    for tri in tris:
        new_tri = []
        for x, y, z in tri:
            xr, yr = _rotate_z(x, y, rotate_z_deg)
            new_tri.append((xr, yr, z))
            xs.append(xr)
            ys.append(yr)
            zs.append(z)
        rotated.append(tuple(new_tri))

    min_x, min_y, min_z = min(xs), min(ys), min(zs)
    max_x, max_y, max_z = max(xs), max(ys), max(zs)
    width = max_x - min_x
    length = max_y - min_y
    if width > bed_width_x_mm + 0.05:
        raise ValueError(
            f"insole width {width:.2f} mm exceeds belt width {bed_width_x_mm} mm after rotation {rotate_z_deg}°"
        )

    x_shift = x_center_mm - (width / 2.0) - min_x
    shifted: list[tuple[tuple[float, float, float], ...]] = []
    xs2: list[float] = []
    ys2: list[float] = []
    zs2: list[float] = []
    for tri in rotated:
        st = tuple((x + x_shift, y - min_y, z - min_z) for x, y, z in tri)
        shifted.append(st)
        for x, y, z in st:
            xs2.append(x)
            ys2.append(y)
            zs2.append(z)

    x_extent = max(xs2) - min(xs2)
    y_extent = max(ys2) - min(ys2)

    out = Path(tempfile.mkdtemp(prefix="belt_stl_")) / f"{src.stem}_belt{rotate_z_deg}.stl"
    _write_triangles(out, shifted)
    meta = {
        "preprocessed_stl": str(out),
        "rotation_z_deg": int(rotate_z_deg),
        "oriented_x_across_belt_mm": round(x_extent, 3),
        "oriented_y_along_belt_mm": round(y_extent, 3),
        "oriented_z_mm": round(max(zs2) - min(zs2), 3),
        "bed_width_x_mm": bed_width_x_mm,
        "x_center_mm": x_center_mm,
        "model_x_shift_mm": round(x_shift, 3),
    }
    return out, meta


def prepare_orthocad_insole_stl(
    src: Path,
    bed_width_x_mm: float = APEX_PRINTABLE_X_MAX_MM,
    x_center_mm: float = APEX_INSOLE_X_CENTER_MM,
    print_direction: str = DEFAULT_PRINT_DIRECTION,
) -> tuple[Path, dict]:
    """OrthoCAD scan: align length with +Y; toe_first applies +180° Z (default)."""
    direction = validate_print_direction(print_direction)
    deg = orthocad_base_rotation_z_deg(direction)
    out, meta = prepare_belt_stl(src, deg, bed_width_x_mm=bed_width_x_mm, x_center_mm=x_center_mm)
    meta["print_direction"] = direction
    meta["rotation_z_deg"] = deg
    return out, meta
