#!/usr/bin/env python3
"""g1_seam_bottom.py - FROZEN gate G1 (PM, 2026-11-20). Do not edit; checked by SHA256SUMS.
Kendon 2026-11-20 11:38 AM MT: "The seams have always been on the bottom it's been a permanent rule since the beginning".
usage: g1_seam_bottom.py CANDIDATE_APEXCORE.gcode --ref PRE29_APEXCORE.gcode [--json out.json]     exit 0 PASS, 1 FAIL
Rules (physical belt frame, bottom = the loop's own min-Y edge, see sfg_common.py):
  S  every CLOSED Outer/Overhang perimeter loop (chain returning to its start) starts AND ends on its bottom edge:
     distance from the point to the loop's bottom edge (sfg_common.bottom_stretch: the stretch of the loop path within
     BOT_TOL 0.25 mm of the loop's min Y that holds its lowest point) <= 0.15 mm.
  LC every layer change (end point of the first Z-changing move of each layer that prints a perimeter) is on the bottom:
     distance to the bottom edge of the layer's lowest perimeter loop <= 0.55 mm (0.15 mm + the 0.4 mm belt-seam lift).
     Layers that hold at least one CLOSED loop: 0 off-bottom allowed. Layers whose only perimeters are OPEN fragments
     (the last toe-tip sections, cut across the top of the part): off-bottom count must not exceed PRE-29's (LC-open).
  OE OPEN perimeter chains (toe-tip fragments; geometry the slicer leaves open): number of off-bottom start/end points
     must not exceed PRE-29's (--ref), so no new off-bottom ends are introduced. Reported in full.
"""
import argparse, json, os, sys, hashlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sfg_common as C

S_TOL = 0.15
LC_TOL = 0.55


def measure(fn):
    ev = C.parse(fn)
    chains = C.perimeter_chains(ev)
    by_layer = {}
    for ch in chains:
        by_layer.setdefault(ch["layer"], []).append(ch)
    s_bad, oe_bad, lc_bad, lc_open_bad = [], [], [], []
    n_closed = n_open = n_lc = 0
    for ch in chains:
        if ch["closed"]:
            n_closed += 1
            for nm, (px, py) in (("start", ch["start"]), ("end", ch["end"])):
                if not C.on_bottom(ch, px, py, S_TOL):
                    s_bad.append({"layer": ch["layer"], "which": nm, "x": round(px, 3), "y": round(py, 3),
                                  "dist_to_bottom": round(C.dist_to_bottom(ch, px, py), 3),
                                  "loop_miny": round(ch["miny"], 3), "bottom_edge": C.bottom_edge(ch)})
        else:
            n_open += 1
            for nm, (px, py) in (("start", ch["start"]), ("end", ch["end"])):
                if not C.on_bottom(ch, px, py, S_TOL):
                    oe_bad.append({"layer": ch["layer"], "which": nm, "x": round(px, 3), "y": round(py, 3)})
    seen = set()
    for d in ev:
        if d["kind"] == "travel" and d["zmove"] and d["layer"] not in seen and d["layer"] in by_layer:
            seen.add(d["layer"])
            n_lc += 1
            lch = by_layer[d["layer"]]
            low = min(lch, key=lambda c: c["miny"])
            dist = C.dist_to_bottom(low, d["x1"], d["y1"])
            if dist > LC_TOL:
                (lc_bad if any(c["closed"] for c in lch) else lc_open_bad).append({"layer": d["layer"], "x": round(d["x1"], 3), "y": round(d["y1"], 3), "z": round(d["z1"], 3),
                               "dist_to_bottom": round(dist, 3), "layer_miny": round(low["miny"], 3), "bottom_edge": C.bottom_edge(low),
                               "line": d["line"]})
    return {"closed_loops": n_closed, "closed_off_bottom": len(s_bad), "open_chains": n_open,
            "open_ends_off_bottom": len(oe_bad), "layer_changes": n_lc, "layer_changes_off_bottom": len(lc_bad),
            "open_only_layer_changes_off_bottom": len(lc_open_bad), "examples_lc_open": lc_open_bad[:15],
            "examples_closed": s_bad[:15], "examples_open": oe_bad[:15], "examples_lc": lc_bad[:15]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cand"); ap.add_argument("--ref", required=True); ap.add_argument("--json")
    a = ap.parse_args()
    c = measure(a.cand); r = measure(a.ref)
    fails = []
    if c["closed_loops"] == 0:
        fails.append("no closed perimeter loops found")
    if c["closed_off_bottom"]:
        fails.append("S: %d closed-loop start/end points off the bottom edge (0 required)" % c["closed_off_bottom"])
    if c["layer_changes_off_bottom"]:
        fails.append("LC: %d of %d layer changes off the bottom (0 required)" % (c["layer_changes_off_bottom"], c["layer_changes"]))
    if c["open_only_layer_changes_off_bottom"] > r["open_only_layer_changes_off_bottom"]:
        fails.append("LC-open: %d open-fragment-only layer changes off the bottom > PRE-29 %d" % (
            c["open_only_layer_changes_off_bottom"], r["open_only_layer_changes_off_bottom"]))
    if c["open_ends_off_bottom"] > r["open_ends_off_bottom"]:
        fails.append("OE: %d open-chain ends off the bottom > PRE-29 %d" % (c["open_ends_off_bottom"], r["open_ends_off_bottom"]))
    rep = {"gate": "G1 seam_bottom", "sha256": hashlib.sha256(open(__file__, "rb").read()).hexdigest(),
           "cand_file": a.cand, "ref_file": a.ref, "cand": c, "pre29": r,
           "result": "PASS" if not fails else "FAIL", "failures": fails}
    if a.json:
        json.dump(rep, open(a.json, "w"), indent=1)
    print(json.dumps({k: rep[k] for k in ("gate", "result", "failures")}), file=sys.stderr)
    print(json.dumps(rep, indent=1))
    sys.exit(0 if not fails else 1)


if __name__ == "__main__":
    main()
