#!/usr/bin/env python3
"""Post-process belt G-code when CoolingBuffer markers are missing (per-layer min time)."""
from __future__ import annotations

import math
import re
from typing import List, Tuple

BELT_I_YY = math.sqrt(2.0)


def machine_xy_length(dx: float, dy: float) -> float:
    dym = dy * BELT_I_YY
    return math.hypot(dx, dym)


def _parse_axes(line: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for ax in "XYZEF":
        m = re.search(rf"\b{ax}(-?[\d.]+)", line)
        if m:
            out[ax] = float(m.group(1))
    return out


def apply_belt_layer_slowdown(
    gcode: str,
    *,
    slow_down_layer_time: float = 8.0,
    slow_down_min_speed: float = 10.0,
    heel_layer_start: int | None = None,
    heel_min_layer_time: float | None = None,
) -> str:
    if ";_EXTRUDE_SET_SPEED" in gcode:
        return gcode

    lines = gcode.splitlines(keepends=True)
    layer_spans: List[Tuple[int, int]] = []
    i = 0
    while i < len(lines):
        if lines[i].startswith(";LAYER_CHANGE"):
            start = i
            i += 1
            while i < len(lines) and not lines[i].startswith(";LAYER_CHANGE"):
                i += 1
            layer_spans.append((start, i))
        else:
            i += 1

    out = list(lines)
    pos = [0.0, 0.0, 0.0]
    cur_f = 0.0

    for layer_no, (start, end) in enumerate(layer_spans, start=0):
        layer_target = slow_down_layer_time
        if heel_layer_start is not None and heel_min_layer_time is not None and layer_no + 1 >= heel_layer_start:
            layer_target = max(slow_down_layer_time, heel_min_layer_time)
        moves: List[Tuple[int, float, float]] = []
        t = 0.0
        pos_l = pos[:]
        f_l = cur_f
        for j in range(start, end):
            raw = out[j]
            s = raw.strip()
            if not s.startswith(("G0", "G1", "G2", "G3")):
                continue
            vals = _parse_axes(s)
            if "F" in vals:
                f_l = vals["F"] / 60.0
            if "E" not in vals:
                for ax, k in zip("XYZ", range(3)):
                    if ax in vals:
                        pos_l[k] = vals[ax]
                continue
            de = vals["E"]
            if de <= 0:
                continue
            npos = pos_l[:]
            for ax, k in zip("XYZ", range(3)):
                if ax in vals:
                    npos[k] = vals[ax]
            dx, dy, dz = npos[0] - pos_l[0], npos[1] - pos_l[1], npos[2] - pos_l[2]
            seg_len = machine_xy_length(dx, dy) if (dx * dx + dy * dy) > 0 else abs(dz)
            if seg_len > 0 and f_l > 0:
                moves.append((j, seg_len, f_l))
                t += seg_len / f_l
            pos_l = npos

        if t >= layer_target or not moves:
            pos = pos_l
            cur_f = f_l
            continue

        scale = min(1.0, layer_target / max(t, 1e-9))
        min_f = slow_down_min_speed
        for j, seg_len, f_mm_s in moves:
            new_f = max(min_f, f_mm_s * scale)
            line = out[j]
            if re.search(r"\bF[\d.]+", line):
                line = re.sub(r"\bF[\d.]+", f"F{int(round(new_f * 60))}", line, count=1)
            else:
                line = line.rstrip("\n") + f" F{int(round(new_f * 60))}\n"
            out[j] = line
        pos = pos_l

    return "".join(out)
