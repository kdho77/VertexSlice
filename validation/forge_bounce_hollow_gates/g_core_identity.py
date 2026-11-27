#!/usr/bin/env python3
"""g_core_identity.py — FROZEN gate. ApexCore G-code is byte-identical to live.

The OrcaSlicer stamp line is the only exempt byte range. Any other difference
fails, including a one-byte edit that is not the stamp.

usage: g_core_identity.py CANDIDATE.gcode --base LIVE.gcode [--json out.json]
exit 0 PASS, 1 FAIL
"""
import argparse, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fbh_common as C


def evaluate(cand_path, base_path):
    cand_raw = open(cand_path, "rb").read()
    base_raw = open(base_path, "rb").read()
    cand, cand_stamps = C.strip_stamp(cand_raw)
    base, base_stamps = C.strip_stamp(base_raw)
    fails = []
    if cand_stamps != 1 or base_stamps != 1:
        fails.append("expected exactly one OrcaSlicer stamp line (cand %d, live %d)" % (cand_stamps, base_stamps))
    if cand != base:
        # First differing line, for the grade. Do not print the whole file.
        cl, bl = cand.split(b"\n"), base.split(b"\n")
        line = None
        for i, (a, b) in enumerate(zip(cl, bl)):
            if a != b:
                line = i + 1
                break
        if line is None:
            line = min(len(cl), len(bl)) + 1
        fails.append("ApexCore differs from live at line %d after the stamp strip (%d vs %d bytes)"
                     % (line, len(cand), len(base)))
    return {
        "gate": "core_identity",
        "cand_bytes": len(cand_raw),
        "live_bytes": len(base_raw),
        "stamp_lines_cand": cand_stamps,
        "stamp_lines_live": base_stamps,
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
    print(json.dumps({k: rep[k] for k in ("gate", "result", "cand_bytes", "live_bytes", "failures")}))
    sys.exit(0 if rep["result"] == "PASS" else 1)


if __name__ == "__main__":
    main()
