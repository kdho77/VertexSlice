#!/usr/bin/env python3
"""G1 seam on the bottom, the frozen fuse gate, scored with BASE as the reference
for the open-chain allowances. Closed-loop and closed-layer rules are absolute
(zero off the bottom), exactly as g1_seam_bottom.py.

usage: g1_bottom.py BASE_DIR CAND_DIR [--json out.json]
"""
import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scg_common as S

S.pin_sfg()


def run_one(cand: str, base: str) -> dict:
    script = os.path.join(S.sfg_dir(), "g1_seam_bottom.py")
    proc = subprocess.run([sys.executable, script, cand, "--ref", base], capture_output=True, text=True)
    if not proc.stdout.strip():
        raise SystemExit("g1_seam_bottom.py produced no JSON for %s: %s" % (cand, proc.stderr[-500:]))
    rep = json.loads(proc.stdout)
    rep["returncode"] = proc.returncode
    return rep


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("base_dir")
    ap.add_argument("cand_dir")
    ap.add_argument("--json")
    args = ap.parse_args()
    fails = []
    measured = {}
    parts = []
    for key, name in S.CORE.items():
        rep = run_one(S.gcode(args.cand_dir, name), S.gcode(args.base_dir, name))
        cand = rep["cand"]
        measured[key] = {
            "result": rep["result"], "failures": rep["failures"],
            "closed_loops": cand["closed_loops"],
            "closed_off_bottom": cand["closed_off_bottom"],
            "layer_changes_off_bottom": cand["layer_changes_off_bottom"],
            "open_ends_off_bottom": cand["open_ends_off_bottom"],
            "open_only_layer_changes_off_bottom": cand["open_only_layer_changes_off_bottom"],
            "examples_closed": cand.get("examples_closed", [])[:4],
            "examples_lc": cand.get("examples_lc", [])[:4],
        }
        if rep["result"] != "PASS":
            fails.append("%s: %s" % (key, "; ".join(rep["failures"])))
        parts.append(("%s_closed_off" % key, cand["closed_off_bottom"]))
        parts.append(("%s_lc_off" % key, cand["layer_changes_off_bottom"]))
        parts.append(("%s_open_off" % key, cand["open_ends_off_bottom"]))
    status = "FAIL" if fails else "PASS"
    out = {"gate": "G1 seam_bottom", "result": status, "failures": fails, "measured": measured}
    S.emit(status, "G1", parts, out, args.json)


if __name__ == "__main__":
    main()
