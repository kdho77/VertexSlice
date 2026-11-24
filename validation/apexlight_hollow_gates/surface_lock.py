#!/usr/bin/env python3
"""surface_lock.py — outer walls, skins, seams, and layer changes vs live.

ApexLight's skins are the Outer wall and Overhang wall beads. Those paths,
the closed-loop seam starts, and the layer-change end points are identical to
live or the gate fails. Identical includes the extrusion length on the wall,
so a wider bead on the same centreline is a different path.

A seam or layer change that leaves the plantar face fails even when the count
of loops is unchanged. On live compared with itself this gate passes: the
placement is locked, not redrawn.
"""
from __future__ import annotations

from collections import Counter

import ahg_common as C

BOTTOM_SEAM = 0.15
BOTTOM_LAYER = 0.55


def _wall_keys(parsed):
    keys = []
    for d in parsed["events"]:
        if d["kind"] != "ext" or d.get("leader"):
            continue
        if d.get("type") not in C.SKIN_TYPES:
            continue
        if d["layer"] < 1:
            continue
        keys.append((
            d["layer"], d.get("type"),
            round(d["x0"], 4), round(d["y0"], 4), round(d["x1"], 4), round(d["y1"], 4),
            round(d["e"], 5),
        ))
    return keys


def _seams(parsed):
    out = []
    for ch in C.perimeter_chains(parsed["events"]):
        if ch["layer"] < 1 or not ch["closed"]:
            continue
        x, y = ch["start"]
        out.append({
            "layer": ch["layer"],
            "x": round(x, 4),
            "y": round(y, 4),
            "dist": C.dist_to_bottom(ch, x, y),
            "chain": ch,
        })
    return out


def _layer_changes(parsed):
    out = {}
    low = {}
    for ch in C.perimeter_chains(parsed["events"]):
        if ch["layer"] < 1:
            continue
        prev = low.get(ch["layer"])
        if prev is None or ch["miny"] < prev["miny"]:
            low[ch["layer"]] = ch
    for d in parsed["events"]:
        if d["kind"] == "travel" and d["zmove"] and d["layer"] >= 1 and d["layer"] not in out:
            ch = low.get(d["layer"])
            dist = None if ch is None else C.dist_to_bottom(ch, d["x1"], d["y1"])
            out[d["layer"]] = {
                "x": round(d["x1"], 4),
                "y": round(d["y1"], 4),
                "dist": dist,
                "line": d["line"],
            }
    return out


def _counter_diff(a, b, limit=8):
    ca, cb = Counter(a), Counter(b)
    missing = list((ca - cb).elements())
    extra = list((cb - ca).elements())
    return missing[:limit], extra[:limit], sum((ca - cb).values()), sum((cb - ca).values())


def evaluate(cand_path, base_path):
    cand, base = C.parse(cand_path), C.parse(base_path)
    fails = []
    bm, em, nm, ne = _counter_diff(_wall_keys(base), _wall_keys(cand))
    if nm or ne:
        fails.append(
            "outer-wall/skin paths differ from live: %d live segments missing, %d candidate segments added"
            % (nm, ne)
        )
    base_seams = _seams(base)
    cand_seams = _seams(cand)
    bkey = [(s["layer"], s["x"], s["y"]) for s in base_seams]
    ckey = [(s["layer"], s["x"], s["y"]) for s in cand_seams]
    base_seam_set = set(bkey)
    sm, se, snm, sne = _counter_diff(bkey, ckey)
    # A live start that is already where live put it stays. A new start off the
    # plantar face is the prohibited move. Identity still rejects a slide along
    # the bottom, because the point is no longer the live point.
    off = [s for s in cand_seams if s["dist"] > BOTTOM_SEAM + 1e-9 and (s["layer"], s["x"], s["y"]) not in base_seam_set]
    if snm or sne:
        fails.append("seam starts differ from live: %d missing, %d added" % (snm, sne))
    if off:
        fails.append("%d closed seam starts are off the plantar face (>%0.2f mm from that loop's bottom)"
                     % (len(off), BOTTOM_SEAM))
    base_lc = _layer_changes(base)
    cand_lc = _layer_changes(cand)
    lc_bad = []
    lc_off = []
    for layer in sorted(set(base_lc) | set(cand_lc)):
        b, c = base_lc.get(layer), cand_lc.get(layer)
        if b is None or c is None or b["x"] != c["x"] or b["y"] != c["y"]:
            lc_bad.append(layer)
        moved = b is None or c is None or b["x"] != c["x"] or b["y"] != c["y"]
        if c is not None and moved and c["dist"] is not None and c["dist"] > BOTTOM_LAYER + 1e-9:
            lc_off.append(layer)
    if lc_bad:
        fails.append("%d layer-change placements differ from live" % len(lc_bad))
    if lc_off:
        fails.append("%d layer changes are off the plantar face (>%0.2f mm from the bottom edge)"
                     % (len(lc_off), BOTTOM_LAYER))
    return {
        "gate": "S surface_lock",
        "wall_segments_base": len(_wall_keys(base)),
        "wall_segments_cand": len(_wall_keys(cand)),
        "walls_missing": nm,
        "walls_added": ne,
        "seams_base": len(base_seams),
        "seams_cand": len(cand_seams),
        "seams_missing": snm,
        "seams_added": sne,
        "seams_off_bottom": len(off),
        "layer_changes_moved": len(lc_bad),
        "layer_changes_off_bottom": len(lc_off),
        "examples_off_bottom": [{"layer": s["layer"], "x": s["x"], "y": s["y"], "dist": round(s["dist"], 3)} for s in off[:6]],
        "result": "FAIL" if fails else "PASS",
        "failures": fails,
    }
