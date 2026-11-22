#!/usr/bin/env python3
"""run_gates.py — run frozen forefoot Cura-copy gates G1-G9.

Exit 0 only when every gate PASSes. A missing measurement fails closed.

usage:
  run_gates.py --core orthocad:BASE.gcode:CAND.gcode
               [--core insole:BASE.gcode:CAND.gcode]
               [--pair ApexLight:BASE.gcode:CAND.gcode ...]
               [--strict-materials] [--json out.json]
"""
import argparse, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fcg_common as C
import g1_thin_bond, g2_thick_bond, g3_outer_edge, g4_seams
import g5_outside_scope, g6_flow, g7_time, g8_crossings, g9_filament_outline


def _triple(spec):
    parts = spec.split(":")
    if len(parts) != 3:
        raise SystemExit("expected a:b:c, got %r" % spec)
    return parts


def evaluate(cores, pairs, strict_materials):
    rows = []
    # Geometry gates run on every ApexCore STL. G5, and the per-file flow/time/crossings, follow.
    for model, base, cand in cores:
        for mod in (g1_thin_bond, g2_thick_bond, g3_outer_edge, g4_seams, g9_filament_outline):
            rows.append(mod.evaluate(cand, base, model))
        rows.append(g6_flow.evaluate(cand, model))
        rows.append(g7_time.evaluate(cand, base))
        rows.append(g8_crossings.evaluate(cand, base))
    rows.append(g5_outside_scope.evaluate(cores, pairs, strict_materials))
    fails = [r for r in rows if r["result"] != "PASS"]
    return {
        "gates": rows,
        "result": "FAIL" if fails else "PASS",
        "fail_count": len(fails),
        "pass_count": len(rows) - len(fails),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--core", action="append", default=[])
    ap.add_argument("--pair", action="append", default=[])
    ap.add_argument("--strict-materials", action="store_true")
    ap.add_argument("--json")
    a = ap.parse_args()
    cores = []
    for spec in a.core:
        model, base, cand = _triple(spec)
        if model not in C.FOREFOOT_LAST:
            raise SystemExit("unknown model %r" % model)
        cores.append((model, base, cand))
    if not cores:
        raise SystemExit("pass at least one --core model:base.gcode:cand.gcode")
    pairs = [_triple(spec) for spec in a.pair]
    rep = C.round_report(evaluate(cores, pairs, a.strict_materials))
    if a.json:
        json.dump(rep, open(a.json, "w"), indent=1)
        open(a.json, "a").write("\n")
    for row in rep["gates"]:
        name = row["gate"]
        extra = row.get("model", "")
        print("%-22s %-10s %s" % (name, extra, row["result"]))
        for failure in row["failures"]:
            print("  %s" % failure)
    print("OVERALL %s  (%d pass, %d fail)" % (rep["result"], rep["pass_count"], rep["fail_count"]))
    sys.exit(0 if rep["result"] == "PASS" else 1)


if __name__ == "__main__":
    main()
