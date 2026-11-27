#!/usr/bin/env python3
"""hollow_tool.py — 0.25 mm forefoot-hollow tool. Frozen with this folder.

A column is 0.25 mm in X. Skin beads use the same envelope as frozen G1:
shallow segments only (|dx|/length >= 0.25), width

    w = (E/length) * filament_area / 0.46 + 0.46 * (1 - pi/4)

Outer thickness is the distance between the outer edges of the two skin
envelopes. Air is the open gap between those envelopes. A column is hollow
when the thickness is >= 3 mm, the air is > 1 mm, and no non-skin extrusion
occupies that air gap.

Non-skin plastic (sparse infill, solid, inner wall, gap fill) is swept along
its path. The bead is w wide. A round cap of radius w/2 closes the ends, which
is the physical line, not a rectangle around the whole move. ApexCore's live
forefoot reads 0 on this test. ApexLight's does not.

Windows. 0.46 mm material: layers <= 165. 0.42 mm material also uses the
physical-equivalent window round(165 * 0.46 / layer_height), which is 181 at
0.42 mm. Both counts are reported. The pass line is 0 in every window that
applies. Total hollow and max air are reported and are not the pass line.
"""
from __future__ import annotations

import math

import ahg_common as C

COLUMN_MM = 0.25
SECTION_MM = 3.0
AIR_MM = 1.0
SAMPLE_MM = 0.2
FOREFOOT_046 = 165
HEIGHT_046 = 0.46


def physical_last(height_mm):
    h = HEIGHT_046 if height_mm is None else height_mm
    if h <= 1e-6:
        h = HEIGHT_046
    return int(round(FOREFOOT_046 * HEIGHT_046 / h))


def _columns(parsed):
    area = parsed["area"]
    skins = {}
    cover = {}
    for d in parsed["events"]:
        if d["kind"] != "ext" or d.get("leader"):
            continue
        layer = d["layer"]
        if layer < 1:
            continue
        x0, y0, x1, y1, e = d["x0"], d["y0"], d["x1"], d["y1"], d["e"]
        dx, dy = x1 - x0, y1 - y0
        length = math.hypot(dx, dy)
        if length < 1e-6 or e <= 1e-9:
            continue
        cos = abs(dx) / length
        sin = abs(dy) / length
        width = C.bead_width(e, length, area)
        if d.get("type") in C.SKIN_TYPES:
            if cos < C.COS_MIN:
                continue
            yhalf = (width * 0.5) / cos
            xa, xb = (x0, x1) if x0 <= x1 else (x1, x0)
            b0 = int(math.floor(xa / COLUMN_MM))
            b1 = int(math.floor(xb / COLUMN_MM))
            for b in range(b0, b1 + 1):
                lo = max(xa, b * COLUMN_MM)
                hi = min(xb, (b + 1) * COLUMN_MM)
                if hi - lo <= 1e-9:
                    continue
                xc = (lo + hi) * 0.5
                if abs(dx) > 1e-12:
                    t = (xc - x0) / dx
                    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
                else:
                    t = 0.5
                yc = y0 + t * dy
                skins.setdefault((layer, b), []).append((yc - yhalf, yc + yhalf, yc))
            continue
        yhalf = width * 0.5
        xrad = yhalf * sin
        n = max(1, int(math.ceil(length / SAMPLE_MM)))
        for i in range(n + 1):
            t = i / n
            x = x0 + dx * t
            y = y0 + dy * t
            along = min(t, 1.0 - t) * length
            rad = yhalf if along <= yhalf else xrad
            b0 = int(math.floor((x - rad) / COLUMN_MM))
            b1 = int(math.floor((x + rad) / COLUMN_MM))
            for b in range(b0, b1 + 1):
                blo = b * COLUMN_MM
                bhi = blo + COLUMN_MM
                if x + rad < blo or x - rad > bhi:
                    continue
                cover.setdefault((layer, b), []).append((y - yhalf, y + yhalf))
    rows = []
    for key, beads in skins.items():
        pair = C._skin_pair([(a, c, yc, "skin") for a, c, yc in beads])
        if pair is None or pair[4]:
            continue
        thickness, air = C.section_of(pair)
        if thickness < SECTION_MM or air <= AIR_MM:
            continue
        phigh, dlow = pair[1], pair[2]
        occupied = False
        for ya, yb in cover.get(key, ()):
            if min(yb, dlow) - max(ya, phigh) > 0.0:
                occupied = True
                break
        if occupied:
            continue
        layer, b = key
        rows.append({
            "layer": layer,
            "bin": b,
            "x_mm": (b + 0.5) * COLUMN_MM,
            "thickness_mm": thickness,
            "air_mm": air,
            "air_lo": phigh,
            "air_hi": dlow,
        })
    return rows


def analyze(parsed):
    rows = _columns(parsed)
    height = parsed["bead_height"]
    last = physical_last(height)
    def count(pred):
        return sum(1 for r in rows if pred(r["layer"]))
    airs = [r["air_mm"] for r in rows]
    return {
        "column_mm": COLUMN_MM,
        "section_min_mm": SECTION_MM,
        "air_min_mm": AIR_MM,
        "bead_height_mm": height,
        "forefoot_last_046": FOREFOOT_046,
        "physical_last": last,
        "forefoot_165": count(lambda layer: layer <= FOREFOOT_046),
        "physical_count": count(lambda layer: layer <= last),
        "above_165": count(lambda layer: layer > FOREFOOT_046),
        "total": len(rows),
        "max_air_mm": max(airs) if airs else 0.0,
        "columns": rows,
    }


def evaluate(path):
    parsed = C.parse(path)
    rep = analyze(parsed)
    fails = []
    if rep["forefoot_165"] != 0:
        fails.append(
            "forefoot hollow %d on layers <= %d (0.46 mm window); 0 required"
            % (rep["forefoot_165"], FOREFOOT_046)
        )
    if rep["physical_last"] != FOREFOOT_046 and rep["physical_count"] != 0:
        fails.append(
            "hollow %d on layers <= %d (physical window at bead height %s mm); 0 required"
            % (rep["physical_count"], rep["physical_last"], rep["bead_height_mm"])
        )
    if rep["bead_height_mm"] is None:
        fails.append("no ;HEIGHT in 0.2-0.55 mm — fail closed, the layer window is unknown")
    out = {k: v for k, v in rep.items() if k != "columns"}
    out["gate"] = "H hollow"
    out["path"] = path
    out["result"] = "FAIL" if fails else "PASS"
    out["failures"] = fails
    return out
