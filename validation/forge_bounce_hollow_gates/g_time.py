#!/usr/bin/env python3
"""g_time.py — FROZEN gate. Slice wall-clock is not worse than 260 s.

The pass line is the server gateway cap. It is not the G-code footer estimate.
A missing measurement fails closed.

usage: g_time.py --seconds 7.218 [--json out.json]
exit 0 PASS, 1 FAIL
"""
import argparse, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fbh_common as C


def evaluate(seconds):
    fails = []
    if seconds is None:
        fails.append("slice time was not measured — fail closed")
    elif float(seconds) > C.SLICE_TIME_CAP_S + 1e-9:
        fails.append("slice time %.3f s is worse than %.0f s" % (float(seconds), C.SLICE_TIME_CAP_S))
    return {
        "gate": "slice_time",
        "seconds": None if seconds is None else float(seconds),
        "cap_s": C.SLICE_TIME_CAP_S,
        "result": "FAIL" if fails else "PASS",
        "failures": fails,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, required=True)
    ap.add_argument("--json")
    a = ap.parse_args()
    rep = C.round_report(evaluate(a.seconds))
    if a.json:
        json.dump(rep, open(a.json, "w"), indent=1)
        open(a.json, "a").write("\n")
    print(json.dumps(rep))
    sys.exit(0 if rep["result"] == "PASS" else 1)


if __name__ == "__main__":
    main()
