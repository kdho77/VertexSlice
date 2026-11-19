#!/usr/bin/env python3
"""Reject the over-extrusion that close_thin_skins left in the thin toe.

A gap or solid bead wider than 1.5 nozzle diameters, an out-and-back retrace
of that bead, or an extrusion at travel feed is a failure. Walls are not
scored: the locked wall width is 1.0 mm and this gate is the skin-fill limit.
A file with no gap or solid extrusion has nothing to report.
"""
from __future__ import annotations

import math
import re

# Rounded-rectangle width at the locked 0.46 mm layer, 1.75 mm filament.
# width = E * area / length / height + height * (1 - pi/4).
_LAYER_H = 0.46
_AREA = math.pi * (1.75 / 2.0) ** 2
_ADJ = _LAYER_H * (1.0 - 0.25 * math.pi)
NOZZLE_MM = 1.0
MAX_BEAD_MM = 1.5 * NOZZLE_MM
# Travel on this machine is F12720. Extrusion feeds are the wall 1560.
_TRAVEL_FEED = 6000.0
_MIN_RETRACE_MM = 0.5
_WORD = re.compile(r"([XYZEF])([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)")

# The features close_thin_skins rewrote. A wider wall is a different change.
_SOLID = {
    "Gap infill",
    "Internal solid infill",
    "Top surface",
    "Bottom surface",
}


def _words(line: str) -> dict[str, str]:
    return dict(_WORD.findall(line.split(";", 1)[0]))


def _width_from_e(e_mm: float, length: float) -> float:
    if length <= 1e-6:
        return 0.0
    return e_mm * _AREA / length / _LAYER_H + _ADJ


def _key(x0: float, y0: float, x1: float, y1: float) -> tuple[int, int, int, int]:
    return (round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2))


def violations(text: str, *, nozzle_mm: float = NOZZLE_MM) -> list[str]:
    """Human-readable failures. Empty when the file has no offending bead."""
    limit = 1.5 * nozzle_mm
    found: list[str] = []
    typ = ""
    width: float | None = None
    feed: float | None = None
    x = y = None
    layer = 0
    seen: set[tuple] = set()

    for index, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if line.startswith(";LAYER_CHANGE"):
            layer += 1
            seen.clear()
            continue
        if line.startswith(";TYPE:"):
            typ = line[6:].strip()
            width = None
            continue
        if line.startswith(";WIDTH:"):
            try:
                width = float(line.split(":", 1)[1].strip())
            except ValueError:
                width = None
            continue
        if not line.startswith(("G0", "G1", "G00", "G01")):
            continue
        w = _words(line)
        if "F" in w:
            feed = float(w["F"])
        nx = float(w["X"]) if "X" in w else x
        ny = float(w["Y"]) if "Y" in w else y
        extruding = (
            "E" in w
            and float(w["E"]) > 0
            and x is not None
            and y is not None
            and ("X" in w or "Y" in w)
        )
        if extruding and nx is not None and ny is not None and typ in _SOLID:
            length = math.hypot(nx - x, ny - y)
            if feed is not None and feed >= _TRAVEL_FEED:
                found.append(f"line {index}: {typ} extrudes at F{feed:.0f}")
            flow_w = _width_from_e(float(w["E"]), length) if length > 0.05 else 0.0
            wide = None
            if width is not None and width > limit + 1e-6:
                wide = width
            if flow_w > limit + 1e-6:
                wide = flow_w if wide is None else max(wide, flow_w)
            if wide is not None:
                found.append(f"line {index}: {typ} bead {wide:.2f} mm is wider than {limit:.2f} mm")
            if length >= _MIN_RETRACE_MM:
                forward = (typ, _key(x, y, nx, ny))
                back = (typ, _key(nx, ny, x, y))
                if (layer, back) in seen:
                    found.append(
                        f"line {index}: {typ} retraces {x:.2f},{y:.2f} -> {nx:.2f},{ny:.2f}"
                    )
                seen.add((layer, forward))
        if "X" in w and nx is not None:
            x = nx
        if "Y" in w and ny is not None:
            y = ny
    return found
