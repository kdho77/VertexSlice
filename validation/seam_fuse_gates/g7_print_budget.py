#!/usr/bin/env python3
"""g7_print_budget.py - FROZEN gate G7 (PM, 2026-11-20, rev 2). Do not edit; checked by SHA256SUMS.
Kendon 2026-11-19: slicer fixes must not meaningfully increase print time versus live.
usage: g7_print_budget.py CANDIDATE.gcode LIVE.gcode PRE29.gcode [--json out.json]      (ApexCore, same STL)
Time = sfg_common.toolpath_seconds() on the FINAL G-code of all three files (one model for all; slicer footers are not
comparable: #29 rewrites LIVE's footer from the toolpath, PRE-29 keeps the slicer's pre-post-process figure, and
post-process feedrate changes never reach a slicer footer). Filament = sfg_common.net_e_mm() (net E fed).
PASS iff time <= LIVE x 1.02 AND <= PRE-29 x 1.02, and filament <= PRE-29 x 1.02.
Reports minutes and filament (mm, and footer grams) for LIVE, PRE-29 and CANDIDATE. If PRE-29 itself is > 2 % slower
than LIVE, the candidate cannot pass by construction; the gate then fails and that is reported (no tuning).
"""
import argparse, json, os, sys, hashlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sfg_common as C

PCT = 2.0


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("cand"); ap.add_argument("live"); ap.add_argument("pre29"); ap.add_argument("--json")
    a = ap.parse_args()
    T = {k: C.toolpath_seconds(f) for k, f in (("cand", a.cand), ("live", a.live), ("pre29", a.pre29))}
    E = {k: C.net_e_mm(f) for k, f in (("cand", a.cand), ("pre29", a.pre29), ("live", a.live))}
    F = {k: C.footer(f) for k, f in (("cand", a.cand), ("live", a.live), ("pre29", a.pre29))}
    fails = []
    if T["cand"] > T["live"] * (1 + PCT / 100):
        fails.append("time %.2f min > LIVE %.2f min +%.0f%% (%+.2f%%)" % (T["cand"] / 60, T["live"] / 60, PCT, 100 * (T["cand"] / T["live"] - 1)))
    if T["cand"] > T["pre29"] * (1 + PCT / 100):
        fails.append("time %.2f min > PRE-29 %.2f min +%.0f%% (%+.2f%%)" % (T["cand"] / 60, T["pre29"] / 60, PCT, 100 * (T["cand"] / T["pre29"] - 1)))
    if E["cand"] > E["pre29"] * (1 + PCT / 100):
        fails.append("filament %.1f mm > PRE-29 %.1f mm +%.0f%%" % (E["cand"], E["pre29"], PCT))
    if T["pre29"] > T["live"] * (1 + PCT / 100):
        fails.append("note: PRE-29 itself is %.2f%% slower than LIVE" % (100 * (T["pre29"] / T["live"] - 1)))
    rows = {k.upper() if k != "cand" else "CANDIDATE": {"minutes": round(T[k] / 60, 2), "filament_mm": round(E[k], 1),
            "footer_time": F[k]["time"], "footer_grams": F[k]["filament_g"]} for k in ("live", "pre29", "cand")}
    rep = {"gate": "G7 print_budget", "sha256": hashlib.sha256(open(__file__, "rb").read()).hexdigest(),
           "files": {"cand": a.cand, "live": a.live, "pre29": a.pre29}, "table": rows,
           "time_vs_live_pct": round(100 * (T["cand"] / T["live"] - 1), 3),
           "time_vs_pre29_pct": round(100 * (T["cand"] / T["pre29"] - 1), 3),
           "filament_vs_pre29_pct": round(100 * (E["cand"] / E["pre29"] - 1), 3),
           "result": "PASS" if not fails else "FAIL", "failures": fails}
    if a.json:
        json.dump(rep, open(a.json, "w"), indent=1)
    print(json.dumps({k: rep[k] for k in ("gate", "result", "failures")}), file=sys.stderr)
    print(json.dumps(rep, indent=1))
    sys.exit(0 if not fails else 1)


if __name__ == "__main__":
    main()
