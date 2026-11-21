#!/usr/bin/env python3
"""g5_crossings.py - FROZEN gate G5 (PM, 2026-11-20). Do not edit; checked by SHA256SUMS.
Kendon 2026-11-20 12:17 PM MT: "...DO NOT DARE BREAK THE DO NOT CROSS THE TOP SURFACE RULE".
usage: g5_crossings.py GCODE [--baseline LIVE_SAME_PROFILE.gcode] [--json out.json]     exit 0 PASS, 1 FAIL
Counts travels that cross a wall with ctc_frozen.py (a byte-for-byte copy of main b7e90bd1's
scripts/g2b_heelmod/check_travel_wall_crossings.py, sha256 5641f7ec...; frozen here so the PR cannot change the checker).
  ApexCore (no --baseline): perimeter_crossings, surface_crossings and seam_block_crossings must all be 0.
  ApexLight (--baseline = LIVE ApexLight on the same STL): each count <= LIVE's. ApexLight must be byte-identical to LIVE
  (G6), and LIVE ApexLight insole_sample already has 1 perimeter/seam_block crossing (measured 2026-11-20), so an
  absolute 0 is impossible there without changing ApexLight; the bar is "no new crossing".
"""
import argparse, json, os, sys, hashlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ctc_frozen

KEYS = ("perimeter_crossings", "surface_crossings", "seam_block_crossings")


def counts(fn):
    r = ctc_frozen.analyze(open(fn, errors="replace").read())
    return {k: r["totals"][k] for k in KEYS}, r.get("samples", [])[:10]


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("gcode"); ap.add_argument("--baseline"); ap.add_argument("--json")
    a = ap.parse_args()
    c, samp = counts(a.gcode)
    b = counts(a.baseline)[0] if a.baseline else None
    fails = []
    for k in KEYS:
        lim = b[k] if b else 0
        if c[k] > lim:
            fails.append("%s %d > %d (%s)" % (k, c[k], lim, "LIVE baseline" if b else "0 required"))
    rep = {"gate": "G5 crossings", "sha256": hashlib.sha256(open(__file__, "rb").read()).hexdigest(),
           "ctc_frozen_sha256": hashlib.sha256(open(ctc_frozen.__file__, "rb").read()).hexdigest(),
           "file": a.gcode, "baseline_file": a.baseline, "counts": c, "baseline": b, "samples": samp,
           "result": "PASS" if not fails else "FAIL", "failures": fails}
    if a.json:
        json.dump(rep, open(a.json, "w"), indent=1)
    print(json.dumps({k: rep[k] for k in ("gate", "result", "failures")}), file=sys.stderr)
    print(json.dumps(rep, indent=1))
    sys.exit(0 if not fails else 1)


if __name__ == "__main__":
    main()
