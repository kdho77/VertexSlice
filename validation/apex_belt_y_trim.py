#!/usr/bin/env python3
"""Cura-matched gantry Y trim for belt OrthoCAD insoles (BeltTransform y_mach_offset)."""
from __future__ import annotations

import math
import struct
from pathlib import Path

# Apex V2 first layer is 0.2 mm (materials will not stick at 0.42).
APEX_INITIAL_LAYER_PRINT_HEIGHT_MM = 0.2
# Cura Apex insole ref first-layer extrusion Y min is 0.066 mm.
TARGET_FIRST_LAYER_Y_MM = 0.07
FIRST_LAYER_Y_MIN_LO_MM = 0.03
FIRST_LAYER_Y_MIN_HI_MM = 0.12


def _read_triangles(stl_path: Path) -> list[list[tuple[float, float, float]]]:
    data = stl_path.read_bytes()
    if data[:5] == b"solid":
        raise ValueError(f"ASCII STL not supported: {stl_path}")
    n = struct.unpack_from("<I", data, 80)[0]
    tris: list[list[tuple[float, float, float]]] = []
    for i in range(n):
        vals = struct.unpack_from("<12f", data, 84 + i * 50)
        tris.append([(vals[j], vals[j + 1], vals[j + 2]) for j in range(3, 12, 3)])
    return tris


def stl_aabb_mm(stl_path: Path) -> tuple[float, float, float, float, float, float]:
    tris = _read_triangles(stl_path)
    xs = [p[0] for tri in tris for p in tri]
    ys = [p[1] for tri in tris for p in tri]
    zs = [p[2] for tri in tris for p in tri]
    return min(xs), min(ys), min(zs), max(xs), max(ys), max(zs)


def stl_min_y_plus_z_mm(stl_path: Path) -> float:
    tris = _read_triangles(stl_path)
    return min(p[1] + p[2] for tri in tris for p in tri)


def _plane_hits_yz(tris: list[list[tuple[float, float, float]]], plane_sum: float) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    for tri in tris:
        for i in range(3):
            a, b = tri[i], tri[(i + 1) % 3]
            sa, sb = a[1] + a[2], b[1] + b[2]
            if abs(sa - plane_sum) < 1e-6:
                pts.append((a[1], a[2]))
            if (sa - plane_sum) * (sb - plane_sum) < 0.0:
                t = (plane_sum - sa) / (sb - sa)
                pts.append((a[1] + t * (b[1] - a[1]), a[2] + t * (b[2] - a[2])))
    return pts


def first_slice_gantry_y_mm(
    stl_path: Path,
    *,
    initial_layer_print_height_mm: float = APEX_INITIAL_LAYER_PRINT_HEIGHT_MM,
    belt_angle_deg: float = 45.0,
    layer_height_mm: float = 0.42,
    max_planes: int = 8,
) -> float:
    """
    Min gantry Y (no y_mach_offset) of the first nonempty virtual slice.

    After bed place, Y_virt = Z_model and Y_mach = √2 × Y_virt. The first slice
    plane is Z_virt = layer0_virt = initial_layer / cos(angle), matching
    PrintObject belt layer spacing. Measured per mesh / print direction.
    """
    tris = _read_triangles(stl_path)
    z_virt_min = min(p[1] + p[2] for tri in tris for p in tri)
    layer0_virt = initial_layer_print_height_mm / math.cos(math.radians(belt_angle_deg))
    later_virt = layer_height_mm / math.cos(math.radians(belt_angle_deg))
    for i in range(max_planes):
        plane = z_virt_min + layer0_virt + i * later_virt
        pts = _plane_hits_yz(tris, plane)
        if pts:
            return math.sqrt(2.0) * min(z for _y, z in pts)
    return 0.0


def cpp_runtime_y_mach_offset_mm(
    stl_path: Path,
    *,
    initial_layer_print_height_mm: float = APEX_INITIAL_LAYER_PRINT_HEIGHT_MM,
    belt_angle_deg: float = 45.0,
) -> float:
    """
    Mirror GCode.cpp runtime y_trim: AABB after bed-place + belt forward.

    For a Y/Z-min-placed mesh the AABB keel is 0, so this equals +initial_layer.
    INI y_mach_offset stacks with this runtime term.
    """
    _xmin, ymin, zmin, _xmax, ymax, zmax = stl_aabb_mm(stl_path)
    corners = (
        (ymin - ymin, zmin - zmin),
        (ymin - ymin, zmax - zmin),
        (ymax - ymin, zmin - zmin),
        (ymax - ymin, zmax - zmin),
    )
    # Forward: Z_virt = Y + Z
    z_virt_min = min(y + z for y, z in corners)
    layer0_virt = initial_layer_print_height_mm / math.cos(math.radians(belt_angle_deg))
    return -(z_virt_min - layer0_virt) / math.sqrt(2.0)


def cura_matched_y_mach_offset_mm(
    stl_path: Path,
    *,
    initial_layer_print_height_mm: float = APEX_INITIAL_LAYER_PRINT_HEIGHT_MM,
    belt_angle_deg: float = 45.0,
    target_y_mm: float = TARGET_FIRST_LAYER_Y_MM,
) -> float:
    """
    Offset written to belt_transform.ini so first-layer extrusion Y ≈ target.

    Measured per placed mesh (toe_first vs heel_first differ). Compensates the
    C++ runtime AABB trim so the stacked offset lands at ``target_y_mm`` (~0.07).
    """
    y0 = first_slice_gantry_y_mm(
        stl_path,
        initial_layer_print_height_mm=initial_layer_print_height_mm,
        belt_angle_deg=belt_angle_deg,
    )
    runtime = cpp_runtime_y_mach_offset_mm(
        stl_path,
        initial_layer_print_height_mm=initial_layer_print_height_mm,
        belt_angle_deg=belt_angle_deg,
    )
    return target_y_mm - y0 - runtime


def predicted_first_layer_y_mm(
    stl_path: Path,
    *,
    initial_layer_print_height_mm: float = APEX_INITIAL_LAYER_PRINT_HEIGHT_MM,
    belt_angle_deg: float = 45.0,
    y_mach_offset_mm: float | None = None,
) -> float:
    y0 = first_slice_gantry_y_mm(
        stl_path,
        initial_layer_print_height_mm=initial_layer_print_height_mm,
        belt_angle_deg=belt_angle_deg,
    )
    runtime = cpp_runtime_y_mach_offset_mm(
        stl_path,
        initial_layer_print_height_mm=initial_layer_print_height_mm,
        belt_angle_deg=belt_angle_deg,
    )
    if y_mach_offset_mm is None:
        y_mach_offset_mm = cura_matched_y_mach_offset_mm(
            stl_path,
            initial_layer_print_height_mm=initial_layer_print_height_mm,
            belt_angle_deg=belt_angle_deg,
        )
    return y0 + runtime + y_mach_offset_mm


def write_belt_transform_ini(
    ini_path: Path,
    stl_path: Path,
    *,
    initial_layer_print_height_mm: float = APEX_INITIAL_LAYER_PRINT_HEIGHT_MM,
    belt_angle_deg: float = 45.0,
) -> float:
    offset = cura_matched_y_mach_offset_mm(
        stl_path,
        initial_layer_print_height_mm=initial_layer_print_height_mm,
        belt_angle_deg=belt_angle_deg,
    )
    ini_path.write_text(f"y_mach_offset = {offset:.6f}\n", encoding="utf-8")
    return offset
