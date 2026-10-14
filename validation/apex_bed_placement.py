#!/usr/bin/env python3
"""Belt placement targets for Apex V2 / IdeaFormer IR3 V2 (Cura-validated insole layout)."""
from __future__ import annotations

import re
from pathlib import Path

# IdeaFormer IR3 V2 / Vertex Apex V2 machine preset: 250 mm across-belt (X), belt run in gcode Z.
APEX_PRINTABLE_X_MIN_MM = 0.0
APEX_PRINTABLE_X_MAX_MM = 250.0
APEX_BED_X_GEOMETRIC_CENTER_MM = 125.0

# Cura apex_cura_insole_ref.gcode places the validation insole at X≈94–186 (center ≈140).
CURA_REF_EXTRUSION_X_CENTER_MM = 140.0
APEX_INSOLE_X_CENTER_MM = CURA_REF_EXTRUSION_X_CENTER_MM

DEFAULT_X_MARGIN_MM = 2.0


def extrusion_xyz_bounds(gcode_text: str) -> dict[str, float]:
    """
    Machine XYZ bounds over extrusion moves (G0/G1 with positive E), tracking modal coords.
    Matches Kendon viewer / Cura ref measurement style.
    """
    x = y = z = 0.0
    xs: list[float] = []
    ys: list[float] = []
    zs: list[float] = []
    for line in gcode_text.splitlines():
        s = line.strip()
        if not s.startswith(("G0", "G1")):
            continue
        em = re.search(r"\bE(-?[\d.]+)", s)
        if em and float(em.group(1)) > 0:
            xs.append(x)
            ys.append(y)
            zs.append(z)
        for axis, var in (("X", "x"), ("Y", "y"), ("Z", "z")):
            m = re.search(rf"\b{axis}(-?[\d.]+)", s)
            if m:
                val = float(m.group(1))
                if var == "x":
                    x = val
                elif var == "y":
                    y = val
                else:
                    z = val
    if not xs:
        return {}
    return {
        "x_min": min(xs),
        "x_max": max(xs),
        "y_min": min(ys),
        "y_max": max(ys),
        "z_min": min(zs),
        "z_max": max(zs),
        "x_span": max(xs) - min(xs),
        "y_span": max(ys) - min(ys),
        "z_span": max(zs) - min(zs),
        "x_center": (min(xs) + max(xs)) / 2.0,
    }


def extrusion_bounds_from_path(path: Path) -> dict[str, float]:
    return extrusion_xyz_bounds(path.read_text(encoding="utf-8", errors="replace"))


def assert_apex_insole_extrusion_layout(
    bounds: dict[str, float],
    *,
    width_mm: float,
    length_along_belt_mm: float,
    x_center_mm: float = APEX_INSOLE_X_CENTER_MM,
    x_margin_mm: float = DEFAULT_X_MARGIN_MM,
    width_tol_mm: float = 2.0,
    center_tol_mm: float = 2.0,
    length_tol_frac: float = 0.06,
) -> list[str]:
    """Return failure messages; empty if layout matches Cura-style belt conventions."""
    fails: list[str] = []
    if not bounds:
        return ["no extrusion moves in gcode"]

    x_span = bounds["x_span"]
    if abs(x_span - width_mm) > width_tol_mm:
        fails.append(f"extrusion X span {x_span:.2f} mm vs insole width {width_mm:.2f} mm (tol {width_tol_mm})")

    xc = bounds["x_center"]
    if abs(xc - x_center_mm) > center_tol_mm:
        fails.append(f"extrusion X center {xc:.2f} mm vs ref {x_center_mm:.2f} mm (tol {center_tol_mm})")

    if bounds["x_min"] < APEX_PRINTABLE_X_MIN_MM + x_margin_mm - 0.05:
        fails.append(f"extrusion X min {bounds['x_min']:.2f} below printable margin")
    if bounds["x_max"] > APEX_PRINTABLE_X_MAX_MM - x_margin_mm + 0.05:
        fails.append(f"extrusion X max {bounds['x_max']:.2f} above printable margin")

    z_span = bounds["z_span"]
    if length_along_belt_mm > 0:
        rel = abs(z_span - length_along_belt_mm) / length_along_belt_mm
        if rel > length_tol_frac:
            fails.append(
                f"extrusion Z span {z_span:.2f} mm vs belt length {length_along_belt_mm:.2f} mm "
                f"(rel {rel:.3f} > {length_tol_frac})"
            )
    return fails
