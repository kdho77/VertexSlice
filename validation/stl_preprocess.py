#!/usr/bin/env python3
"""Rigid STL transforms for belt placement (rotate + translate only, never scale)."""
from __future__ import annotations

import struct
import tempfile
from pathlib import Path


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
            # normal placeholder
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
    bed_width_x_mm: float = 250.0,
) -> tuple[Path, dict]:
    """
    Rotate about Z (toe-first along +Y belt) and translate so min corner sits at origin.
    Raises ValueError if across-belt width exceeds bed_width_x_mm.
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
    shifted: list[tuple[tuple[float, float, float], ...]] = []
    xs2: list[float] = []
    ys2: list[float] = []
    zs2: list[float] = []
    for tri in rotated:
        st = tuple((x - min_x, y - min_y, z - min_z) for x, y, z in tri)
        shifted.append(st)
        for x, y, z in st:
            xs2.append(x)
            ys2.append(y)
            zs2.append(z)

    x_extent = max(xs2) - min(xs2)
    y_extent = max(ys2) - min(ys2)
    if x_extent > bed_width_x_mm + 0.05:
        raise ValueError(
            f"insole width {x_extent:.2f} mm exceeds belt width {bed_width_x_mm} mm after rotation {rotate_z_deg}°"
        )

    out = Path(tempfile.mkdtemp(prefix="belt_stl_")) / f"{src.stem}_belt{rotate_z_deg}.stl"
    _write_triangles(out, shifted)
    meta = {
        "preprocessed_stl": str(out),
        "rotation_z_deg": int(rotate_z_deg),
        "oriented_x_across_belt_mm": round(x_extent, 3),
        "oriented_y_along_belt_mm": round(y_extent, 3),
        "oriented_z_mm": round(max(zs2) - min(zs2), 3),
        "bed_width_x_mm": bed_width_x_mm,
    }
    return out, meta
