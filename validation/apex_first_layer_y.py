#!/usr/bin/env python3
"""First-layer gantry Y checks vs Cura Apex insole reference."""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CURA_REF = REPO / "validation/reference_gcode/apex_cura_insole_ref.gcode"

# Cura ref layer 0 extrusion Y range (gantry / belt nozzle height axis).
CURA_FIRST_LAYER_Y_MIN = 0.066
CURA_FIRST_LAYER_Y_TOL = 0.05


def _cura_first_layer_y_bounds(gcode_text: str) -> tuple[float | None, float | None]:
    lines = gcode_text.splitlines()
    in_l0 = False
    ys: list[float] = []
    y = 0.0
    for line in lines:
        if line.startswith(";LAYER:0"):
            in_l0 = True
            continue
        if in_l0 and line.startswith(";LAYER:1"):
            break
        if not in_l0:
            continue
        s = line.strip()
        if not s.startswith(("G0", "G1")):
            continue
        m = re.search(r"\bY(-?[\d.]+)", s)
        if m:
            y = float(m.group(1))
        if "E" in s and re.search(r"\bE(-?[\d.]+)", s) and float(re.search(r"\bE(-?[\d.]+)", s).group(1)) > 0:
            ys.append(y)
    if not ys:
        return None, None
    return min(ys), max(ys)


def _orca_first_layer_y_bounds(gcode_text: str, max_layers: int = 12) -> tuple[float | None, float | None]:
    """Min/max gantry Y over the first ``max_layers`` model layers (Cura layer-0 analogue)."""
    lines = gcode_text.splitlines()
    started = False
    layer_idx = 0
    ys: list[float] = []
    y = 0.0
    role: str | None = None

    for line in lines:
        if "M204 S3000" in line or "PRINT_START" in line:
            started = True
        if not started:
            continue
        if ";LAYER_CHANGE" in line:
            layer_idx += 1
            if layer_idx > max_layers:
                break
            role = None
            continue
        if ";TYPE:" in line:
            role = line.split(";TYPE:", 1)[1].strip()
        if role in ("Custom",):
            continue
        s = line.strip()
        if not s.startswith(("G0", "G1")):
            continue
        m = re.search(r"\bY(-?[\d.]+)", s)
        if m:
            y = float(m.group(1))
        if "E" in s and re.search(r"\bE(-?[\d.]+)", s) and float(re.search(r"\bE(-?[\d.]+)", s).group(1)) > 0:
            ys.append(y)

    if not ys:
        return None, None
    return min(ys), max(ys)


def first_layer_extrusion_y_bounds(gcode_text: str) -> tuple[float | None, float | None, float | None]:
    if ";LAYER:0" in gcode_text:
        y_min, y_max = _cura_first_layer_y_bounds(gcode_text)
        return None, y_min, y_max
    y_min, y_max = _orca_first_layer_y_bounds(gcode_text)
    return None, y_min, y_max


def assert_first_layer_y_near_cura(
    gcode_text: str,
    *,
    ref_y_min: float = CURA_FIRST_LAYER_Y_MIN,
    tol_mm: float = CURA_FIRST_LAYER_Y_TOL,
) -> list[str]:
    _, y_min, _ = first_layer_extrusion_y_bounds(gcode_text)
    if y_min is None:
        return ["could not measure first-layer Y min"]
    if abs(y_min - ref_y_min) > tol_mm:
        return [f"first-layer Y min {y_min:.3f} mm vs Cura ref {ref_y_min:.3f} mm (tol ±{tol_mm})"]
    return []


def cura_ref_first_layer_y_min(path: Path | None = None) -> float:
    path = path or CURA_REF
    _, y_min, _ = first_layer_extrusion_y_bounds(path.read_text(encoding="utf-8", errors="replace"))
    return y_min if y_min is not None else CURA_FIRST_LAYER_Y_MIN
