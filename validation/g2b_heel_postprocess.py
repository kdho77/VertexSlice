#!/usr/bin/env python3
"""G2b heelmod post-process: heel layer-time floor and outer-wall speed floor."""
from __future__ import annotations

import re

from validation.apex_belt_layer_slowdown import apply_belt_layer_slowdown

HEEL_LAYER_START = 293  # 1-based; matches heel coupon band


def enforce_heel_outer_floor(
    gcode: str,
    *,
    heel_layer_start: int = HEEL_LAYER_START,
    min_outer_mm_s: float = 10.0,
) -> str:
    lines = gcode.splitlines(keepends=True)
    layer_idx = -1
    feat = ""
    out: list[str] = []
    min_f = min_outer_mm_s * 60.0
    for line in lines:
        if line.startswith(";LAYER_CHANGE"):
            layer_idx += 1
        if line.startswith(";TYPE:"):
            feat = line.split(":", 1)[1].strip()
        s = line.strip()
        if (
            layer_idx + 1 >= heel_layer_start
            and s.startswith("G1")
            and " E" in s
            and feat in ("Outer wall", "Overhang wall")
        ):
            m = re.search(r"\bF([\d.]+)", s)
            f_val = float(m.group(1)) if m else None
            if f_val is None or f_val < min_f:
                body = re.sub(r"\sF[\d.]+", "", s.split(";", 1)[0])
                line = f"{body} F{min_f:g}\n"
        out.append(line)
    return "".join(out)


def apply_heel_flow_scale(
    gcode: str,
    *,
    heel_layer_start: int = HEEL_LAYER_START,
    outer_scale: float = 1.0,
    inner_scale: float = 1.0,
) -> str:
    """Scale extrusion on heel layers to approximate height-range flow modifiers."""
    if outer_scale == 1.0 and inner_scale == 1.0:
        return gcode
    lines = gcode.splitlines(keepends=True)
    layer_idx = -1
    feat = ""
    out: list[str] = []
    for line in lines:
        if line.startswith(";LAYER_CHANGE"):
            layer_idx += 1
        if line.startswith(";TYPE:"):
            feat = line.split(":", 1)[1].strip()
        if layer_idx + 1 < heel_layer_start:
            out.append(line)
            continue
        s = line.strip()
        if not s.startswith("G1") or " E" not in s:
            out.append(line)
            continue
        scale = 1.0
        if feat in ("Outer wall", "Overhang wall"):
            scale = outer_scale
        elif feat == "Inner wall":
            scale = inner_scale
        if scale == 1.0:
            out.append(line)
            continue
        m = re.search(r"\bE([\d.]+)", s)
        if not m:
            out.append(line)
            continue
        e = float(m.group(1)) * scale
        body = re.sub(r"\bE[\d.]+", f"E{e:.5f}", s.split(";", 1)[0])
        out.append(body + "\n")
    return "".join(out)


def apply_g2b_heel_guards(
    gcode: str,
    *,
    heel_layer_start: int = HEEL_LAYER_START,
    slow_down_layer_time: float = 4.0,
    slow_down_min_speed: float = 20.0,
    heel_min_layer_time: float = 8.0,
    min_outer_mm_s: float = 10.0,
) -> str:
    text = apply_belt_layer_slowdown(
        gcode,
        slow_down_layer_time=slow_down_layer_time,
        slow_down_min_speed=slow_down_min_speed,
        heel_layer_start=heel_layer_start,
        heel_min_layer_time=heel_min_layer_time,
    )
    return enforce_heel_outer_floor(
        text,
        heel_layer_start=heel_layer_start,
        min_outer_mm_s=min_outer_mm_s,
    )
