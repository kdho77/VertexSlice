#!/usr/bin/env python3
"""g7_time.py — FROZEN gate G7. Do not edit; checked by SHA256SUMS.

G7 Time. Total ApexCore print time, measured by the same toolpath clock as the
frozen seam-fuse budget (cruise on extrusions, trapezoid on travels), is
<= BASE + 2%. The slicer footer is reported and is not the pass line.

usage: g7_time.py CANDIDATE.gcode --base BASE.gcode [--json out.json]
exit 0 PASS, 1 FAIL
"""
import argparse, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fcg_common as C


def evaluate(cand_path, base_path):
    tc, tb = C.toolpath_seconds(cand_path), C.toolpath_seconds(base_path)
    fails = []
    if tb <= 0:
        fails.append("BASE toolpath time is 0 — fail closed")
    elif tc > tb * (1.0 + C.TIME_FRACTION) + 1e-6:
        fails.append("time %.1f s > BASE %.1f s + 2%% (%+.2f%%)" % (tc, tb, 100.0 * (tc / tb - 1.0)))
    return {
        "gate": "G7 time",
        "cand_s": tc,
        "base_s": tb,
        "delta_pct": (100.0 * (tc / tb - 1.0)) if tb else None,
        "limit_pct": C.TIME_FRACTION * 100.0,
        "cand_footer": C.footer_time(cand_path),
        "base_footer": C.footer_time(base_path),
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
    print(json.dumps({k: rep[k] for k in ("gate", "result", "failures", "cand_s", "base_s", "delta_pct")}))
    sys.exit(0 if rep["result"] == "PASS" else 1)


if __name__ == "__main__":
    main()
