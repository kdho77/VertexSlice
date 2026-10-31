#!/usr/bin/env python3
"""Toe-first belt advance checks for Apex insole G-code."""
from __future__ import annotations

import re


def first_extrusion_machine_y_mm(gcode_text: str) -> float | None:
    """Machine Y at the first positive-E move (belt nozzle height axis)."""
    y = 0.0
    for line in gcode_text.splitlines():
        s = line.strip()
        if not s.startswith(("G0", "G1")):
            continue
        m = re.search(r"\bY(-?[\d.]+)", s)
        if m:
            y = float(m.group(1))
        if "E" in s:
            em = re.search(r"\bE(-?[\d.]+)", s)
            if em and float(em.group(1)) > 0:
                return y
    return None


def early_belt_z_mm(gcode_text: str, max_layers: int = 8) -> list[float]:
    """First extrusion Z (belt length axis) per layer."""
    x = y = z = 0.0
    layer_z: list[float] = []
    pending = False
    for line in gcode_text.splitlines():
        if ";LAYER_CHANGE" in line or line.startswith(";LAYER:"):
            pending = True
            continue
        s = line.strip()
        if not s.startswith(("G0", "G1")):
            continue
        for ax, var in (("X", "x"), ("Y", "y"), ("Z", "z")):
            m = re.search(rf"\b{ax}(-?[\d.]+)", s)
            if m:
                if var == "x":
                    x = float(m.group(1))
                elif var == "y":
                    y = float(m.group(1))
                else:
                    z = float(m.group(1))
        if "E" in s:
            em = re.search(r"\bE(-?[\d.]+)", s)
            if em and float(em.group(1)) > 0 and pending:
                layer_z.append(z)
                pending = False
                if len(layer_z) >= max_layers:
                    break
    return layer_z


def assert_toe_first_gcode(gcode_text: str, *, max_first_y_mm: float = 5.0) -> list[str]:
    """
    Toe enters the belt first when early extrusion uses low machine Y (Cura ref style)
    and early belt-Z layers start near the toe end (low Z), not the heel (high Z).
    """
    fails: list[str] = []
    first_y = first_extrusion_machine_y_mm(gcode_text)
    if first_y is None:
        return ["no extrusion moves for toe-first check"]
    if first_y > max_first_y_mm:
        fails.append(f"first extrusion Y {first_y:.2f} mm > {max_first_y_mm} (heel-first?)")

    early = early_belt_z_mm(gcode_text)
    if len(early) < 3:
        fails.append("could not read early layer belt Z for toe-first check")
        return fails

    all_z: list[float] = []
    z = 0.0
    for line in gcode_text.splitlines():
        s = line.strip()
        if not s.startswith(("G0", "G1")):
            continue
        m = re.search(r"\bZ(-?[\d.]+)", s)
        if m:
            z = float(m.group(1))
        if "E" in s and re.search(r"\bE(-?[\d.]+)", s) and float(re.search(r"\bE(-?[\d.]+)", s).group(1)) > 0:
            all_z.append(z)
    if not all_z:
        return fails
    z_min, z_max = min(all_z), max(all_z)
    z_span = z_max - z_min
    if z_span < 20:
        return fails
    early_med = sorted(early)[len(early) // 2]
    # Toe-first: first layers should be in the lower third of belt travel.
    if early_med > z_min + 0.45 * z_span:
        fails.append(
            f"early belt Z median {early_med:.1f} mm not near toe (z_min={z_min:.1f}, z_max={z_max:.1f})"
        )
    return fails
