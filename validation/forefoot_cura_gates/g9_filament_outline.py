#!/usr/bin/env python3
"""g9_filament_outline.py — FROZEN gate G9. Do not edit; checked by SHA256SUMS.

G9 Filament. The forefoot filament increase (candidate minus BASE, extruding E
on layers 1..forefoot) is reported. It is not itself a fail: copying Cura adds
plastic inside the outline. Any forefoot bead that sticks more than 0.02 mm
past the BASE printed outer edge fails. Nothing is extruded outside that outline.

usage: g9_filament_outline.py CANDIDATE.gcode --base BASE.gcode [--model orthocad|insole] [--json out.json]
exit 0 PASS, 1 FAIL
"""
import argparse, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fcg_common as C


def evaluate(cand_path, base_path, model):
    cand, base = C.parse(cand_path), C.parse(base_path)
    fc, fb = C.forefoot_filament_mm(cand, model), C.forefoot_filament_mm(base, model)
    outside = C.extrusion_outside(cand, base, model)
    fails = []
    if fb <= 0:
        fails.append("BASE forefoot filament is 0 — fail closed")
    if outside:
        fails.append("%d forefoot bead samples lie outside the BASE outer outline (showing %d)"
                     % (len(outside), len(outside)))
    return {
        "gate": "G9 filament_outline",
        "model": model,
        "cand_forefoot_mm": fc,
        "base_forefoot_mm": fb,
        "increase_mm": fc - fb,
        "increase_pct": (100.0 * (fc / fb - 1.0)) if fb else None,
        "outside_samples": outside,
        "outline_tol_mm": C.OUTLINE_TOL,
        "result": "FAIL" if fails else "PASS",
        "failures": fails,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cand")
    ap.add_argument("--base", required=True)
    ap.add_argument("--model", choices=sorted(C.FOREFOOT_LAST))
    ap.add_argument("--json")
    a = ap.parse_args()
    model = C.detect_model(a.cand, a.model)
    rep = C.round_report(evaluate(a.cand, a.base, model))
    if a.json:
        json.dump(rep, open(a.json, "w"), indent=1)
        open(a.json, "a").write("\n")
    print(json.dumps({k: rep[k] for k in ("gate", "result", "failures", "increase_mm", "increase_pct")}))
    sys.exit(0 if rep["result"] == "PASS" else 1)


if __name__ == "__main__":
    main()
