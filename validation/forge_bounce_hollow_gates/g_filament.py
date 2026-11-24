#!/usr/bin/env python3
"""g_filament.py — FROZEN gate. #46 filament tolerance and frozen G9 outline.

Filament may increase only by sparse infill or solid added into a previously
hollow >= 3 mm forefoot section. No extrusion more than 0.02 mm outside the
live printed outer outline (frozen G9). Quote live vs candidate filament
used [mm] and [g] from the G-code footer.

usage: g_filament.py CANDIDATE.gcode --base LIVE.gcode [--json out.json]
exit 0 PASS, 1 FAIL
"""
import argparse, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fbh_common as C


def _hollow_index(parsed):
    """(layer, x_bin) -> (air_lo, air_hi) for hollow forefoot columns."""
    out = {}
    for s in C.hollow_samples(parsed):
        if s["layer"] < 1 or s["layer"] > C.equivalent_last(C.file_layer_height(parsed)):
            continue
        out[(s["layer"], s["x_bin"])] = s
    return out


def _in_hollow(d, index):
    """True when the added bead's midpoint sits in a hollow column's air, or
    overlaps that air by up to 0.5 mm (the bead has to touch both skins)."""
    import math
    xa, xb = (d["x0"], d["x1"]) if d["x0"] <= d["x1"] else (d["x1"], d["x0"])
    b0 = int(math.floor(xa / C.TOOL_MM))
    b1 = int(math.floor(xb / C.TOOL_MM))
    y = (d["y0"] + d["y1"]) * 0.5
    for b in range(b0 - 1, b1 + 2):
        hit = index.get((d["layer"], b))
        if hit is None:
            continue
        if (hit["air_lo"] - 0.5) <= y <= (hit["air_hi"] + 0.5):
            return True
    return False


def evaluate(cand_path, base_path):
    cand, base = C.parse(cand_path), C.parse(base_path)
    live_f = C.footer_filament(base_path)
    cand_f = C.footer_filament(cand_path)
    from collections import Counter
    base_keys = Counter(C.segment_key(d) for d in base["events"] if d["kind"] == "ext" and not d.get("leader"))
    added = []
    bad_type = []
    outside_hollow = []
    hollow = _hollow_index(base)
    equiv = C.equivalent_last(C.file_layer_height(base))
    for d in cand["events"]:
        if d["kind"] != "ext" or d.get("leader"):
            continue
        key = C.segment_key(d)
        if base_keys[key] > 0:
            base_keys[key] -= 1
            continue
        added.append(d)
        typ = d.get("type") or ""
        if typ not in C.ADDED_FILL_TYPES:
            bad_type.append({"layer": d["layer"], "type": typ, "line": d["line"]})
        elif not (1 <= d["layer"] <= equiv and _in_hollow(d, hollow)):
            outside_hollow.append({"layer": d["layer"], "type": typ, "x": round(d["x1"], 3), "y": round(d["y1"], 3)})
    # Only beads that are not already in the live file can fall outside the
    # outline. Scoring the live overhangs against themselves flags samples on
    # Forge and would reject an unchanged file.
    outside = []
    if added:
        slim = dict(cand)
        slim["events"] = added
        outside = C.extrusion_outside(slim, base, "orthocad")
    fails = []
    if live_f["mm"] is None or cand_f["mm"] is None:
        fails.append("filament used [mm] footer missing — fail closed")
    if live_f["g"] is None or cand_f["g"] is None:
        fails.append("filament used [g] footer missing — fail closed")
    if outside:
        fails.append("%d forefoot bead samples lie more than %.2f mm outside the live outer outline"
                     % (len(outside), C.OUTLINE_TOL))
    if bad_type:
        fails.append("%d added extrusions are not sparse infill or solid" % len(bad_type))
    if outside_hollow:
        fails.append("%d added forefoot extrusions are outside a previously hollow >=3 mm section" % len(outside_hollow))
    increase_mm = None
    if live_f["mm"] is not None and cand_f["mm"] is not None:
        increase_mm = cand_f["mm"] - live_f["mm"]
    return {
        "gate": "filament",
        "live_mm": live_f["mm"],
        "live_g": live_f["g"],
        "cand_mm": cand_f["mm"],
        "cand_g": cand_f["g"],
        "increase_mm": increase_mm,
        "increase_g": None if live_f["g"] is None or cand_f["g"] is None else cand_f["g"] - live_f["g"],
        "outside_samples": len(outside),
        "outline_tol_mm": C.OUTLINE_TOL,
        "added_extrusions": len(added),
        "added_bad_type": len(bad_type),
        "added_outside_hollow": len(outside_hollow),
        "examples_outside": outside[:6],
        "examples_bad_type": bad_type[:6],
        "examples_outside_hollow": outside_hollow[:6],
        "result": "FAIL" if fails else "PASS",
        "failures": fails,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cand")
    ap.add_argument("--base", required=True)
    ap.add_argument("--json")
    a = ap.parse_args()
    rep = C.round_report(evaluate(a.cand, a.base))
    if a.json:
        json.dump(rep, open(a.json, "w"), indent=1)
        open(a.json, "a").write("\n")
    print(json.dumps({k: rep[k] for k in ("gate", "result", "live_mm", "cand_mm", "live_g", "cand_g", "increase_mm", "failures")}))
    sys.exit(0 if rep["result"] == "PASS" else 1)


if __name__ == "__main__":
    main()
