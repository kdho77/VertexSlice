#!/usr/bin/env python3
"""run_gates.py — grade one ApexLight candidate and its ApexCore pair.

Exit 0 only when every gate passes. A missing file or a missing slice time
fails closed. This does not slice, merge, or deploy.

  python3 validation/apexlight_hollow_gates/run_gates.py \
      --light-base LIVE_LIGHT.gcode --light-cand CAND_LIGHT.gcode \
      --core-base LIVE_CORE.gcode --core-cand CAND_CORE.gcode \
      --slice-seconds 176.059 --json /tmp/grade.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ahg_common as C
import core_identity
import filament_delta
import g1_thin_bond
import g2_thick_bond
import hollow_tool
import slice_time
import surface_lock


def grade(light_base, light_cand, core_base, core_cand, slice_seconds, model="orthocad"):
    rows = [
        hollow_tool.evaluate(light_cand),
        g1_thin_bond.evaluate(light_cand, light_base, model),
        g2_thick_bond.evaluate(light_cand, light_base, model),
        surface_lock.evaluate(light_cand, light_base),
        filament_delta.evaluate(light_cand, light_base, model),
        slice_time.evaluate(slice_seconds),
        core_identity.evaluate(core_cand, core_base),
    ]
    rows = [C.round_report(r) for r in rows]
    failed = [r["gate"] for r in rows if r["result"] != "PASS"]
    return {
        "result": "FAIL" if failed else "PASS",
        "failed_gates": failed,
        "model": model,
        "gates": rows,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--light-base", required=True)
    ap.add_argument("--light-cand", required=True)
    ap.add_argument("--core-base", required=True)
    ap.add_argument("--core-cand", required=True)
    ap.add_argument("--slice-seconds", type=float, default=None)
    ap.add_argument("--model", default="orthocad")
    ap.add_argument("--json")
    a = ap.parse_args()
    rep = grade(a.light_base, a.light_cand, a.core_base, a.core_cand, a.slice_seconds, a.model)
    if a.json:
        json.dump(rep, open(a.json, "w"), indent=1)
        open(a.json, "a").write("\n")
    brief = []
    for g in rep["gates"]:
        brief.append({"gate": g["gate"], "result": g["result"], "failures": g["failures"]})
    print(json.dumps({"result": rep["result"], "gates": brief}, indent=1))
    sys.exit(0 if rep["result"] == "PASS" else 1)


if __name__ == "__main__":
    main()
