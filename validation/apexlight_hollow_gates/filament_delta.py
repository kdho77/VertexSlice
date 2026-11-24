#!/usr/bin/env python3
"""filament_delta.py — #46 filament tolerance plus frozen G9 outline.

Filament may increase only by sparse infill or solid added into sections that
were hollow on live (thickness >= 3 mm, air > 1 mm, no non-wall material).
No extrusion may sit more than 0.02 mm outside the live printed outer outline.
That outline test is frozen G9. The increase itself is not a failure.

The gate quotes `; filament used [mm]` and `; filament used [g]` for live and
the candidate. A decrease, a removed extrusion, or plastic that is not infill
or solid inside a previously hollow column fails.
"""
from __future__ import annotations

import math
import re

import ahg_common as C
import hollow_tool

FILL_TYPES = {"Sparse infill", "Solid infill", "Internal solid infill"}
MM_RE = re.compile(r"^; filament used \[mm\] = (.+)$", re.M)
G_RE = re.compile(r"^; filament used \[g\] = (.+)$", re.M)


def _footer(path):
    text = open(path, errors="replace").read()
    mm = MM_RE.search(text)
    grams = G_RE.search(text)
    return (None if mm is None else mm.group(1).strip(),
            None if grams is None else grams.group(1).strip())


def _keys(parsed):
    keys = []
    for d in parsed["events"]:
        if d["kind"] != "ext" or d.get("leader") or d["layer"] < 1:
            continue
        keys.append((
            d["layer"], d.get("type"),
            round(d["x0"], 4), round(d["y0"], 4), round(d["x1"], 4), round(d["y1"], 4),
            round(d["e"], 5),
        ))
    return keys


def _inside_hollow(d, hollow_by_bin):
    x0, y0, x1, y1 = d["x0"], d["y0"], d["x1"], d["y1"]
    length = math.hypot(x1 - x0, y1 - y0)
    n = max(1, int(math.ceil(length / hollow_tool.COLUMN_MM)))
    for i in range(n):
        t = (i + 0.5) / n
        x = x0 + (x1 - x0) * t
        y = y0 + (y1 - y0) * t
        b = int(math.floor(x / hollow_tool.COLUMN_MM))
        gap = hollow_by_bin.get((d["layer"], b))
        if gap is None or not (gap[0] - 0.05 <= y <= gap[1] + 0.05):
            return False
    return True


def evaluate(cand_path, base_path, model="orthocad"):
    cand, base = C.parse(cand_path), C.parse(base_path)
    base_mm, base_g = _footer(base_path)
    cand_mm, cand_g = _footer(cand_path)
    fails = []
    outside = C.extrusion_outside(cand, base, model)
    if outside:
        fails.append(
            "%d forefoot samples lie more than %.2f mm outside the live outer outline"
            % (len(outside), C.OUTLINE_TOL)
        )
    from collections import Counter
    cb, cc = Counter(_keys(base)), Counter(_keys(cand))
    removed = sum((cb - cc).values())
    added_keys = list((cc - cb).elements())
    if removed:
        fails.append("%d live extrusions are missing; filament may only be added" % removed)
    hollow = hollow_tool.analyze(base)
    by_bin = {(r["layer"], r["bin"]): (r["air_lo"], r["air_hi"]) for r in hollow["columns"]}
    # Recover the original moves for added keys. Match rounded endpoints.
    added_moves = []
    seen = Counter()
    want = Counter(added_keys)
    for d in cand["events"]:
        if d["kind"] != "ext" or d.get("leader") or d["layer"] < 1:
            continue
        key = (
            d["layer"], d.get("type"),
            round(d["x0"], 4), round(d["y0"], 4), round(d["x1"], 4), round(d["y1"], 4),
            round(d["e"], 5),
        )
        if seen[key] < want[key]:
            seen[key] += 1
            added_moves.append(d)
    bad_type = 0
    outside_hollow = 0
    for d in added_moves:
        if d.get("type") not in FILL_TYPES:
            bad_type += 1
            continue
        if not _inside_hollow(d, by_bin):
            outside_hollow += 1
    if bad_type:
        fails.append("%d added extrusions are not sparse infill or solid" % bad_type)
    if outside_hollow:
        fails.append("%d added infill/solid extrusions are outside a previously hollow >=3 mm section" % outside_hollow)
    try:
        base_mm_f = float(base_mm) if base_mm is not None else None
        cand_mm_f = float(cand_mm) if cand_mm is not None else None
    except ValueError:
        base_mm_f = cand_mm_f = None
    if base_mm_f is None or cand_mm_f is None:
        fails.append("filament used [mm] footer missing — fail closed")
    elif cand_mm_f + 0.05 < base_mm_f:
        fails.append("filament decreased from %s mm to %s mm; only added fill is allowed" % (base_mm, cand_mm))
    if base_g is None or cand_g is None:
        fails.append("filament used [g] footer missing — fail closed")
    return {
        "gate": "F filament_delta",
        "live_filament_mm": base_mm,
        "live_filament_g": base_g,
        "cand_filament_mm": cand_mm,
        "cand_filament_g": cand_g,
        "increase_mm": None if base_mm_f is None or cand_mm_f is None else cand_mm_f - base_mm_f,
        "removed_extrusions": removed,
        "added_extrusions": len(added_moves),
        "added_bad_type": bad_type,
        "added_outside_hollow": outside_hollow,
        "outside_outline_samples": len(outside),
        "outline_tol_mm": C.OUTLINE_TOL,
        "result": "FAIL" if fails else "PASS",
        "failures": fails,
    }
