#!/usr/bin/env python3
"""A9 crossings. ApexCore perimeter, surface, and seam-block crossings are 0,
counted by the pinned validation/seam_fuse_gates/ctc_frozen.py.

usage: a9_crossings.py BASE_DIR CAND_DIR [--json out.json]
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scg_common as S
import ctc_frozen

S.pin_sfg()

KEYS = ("perimeter_crossings", "surface_crossings", "seam_block_crossings")


def counts(path: str) -> dict:
    report = ctc_frozen.analyze(open(path, errors="replace").read())
    return {k: report["totals"][k] for k in KEYS}


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
        c = counts(S.gcode(args.cand_dir, name))
        measured[key] = c
        for metric in KEYS:
            parts.append(("%s_%s" % (key, metric), c[metric]))
            if c[metric] != 0:
                fails.append("%s %s %d != 0" % (key, metric, c[metric]))
    status = "FAIL" if fails else "PASS"
    rep = {"gate": "A9 crossings", "result": status, "failures": fails, "measured": measured,
           "ctc_frozen_sha256": S.SFG_SHA256["ctc_frozen.py"]}
    S.emit(status, "A9", parts, rep, args.json)


if __name__ == "__main__":
    main()
