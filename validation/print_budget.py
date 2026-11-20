#!/usr/bin/env python3
"""print_budget.py - FROZEN gate (OrthoCAD PM, 2026-11-20). Do not edit; CI checks its sha256.

Kendon 2026-11-19: slicer fixes must not meaningfully increase print time versus live; Cura's method should print at the same speed.
usage: print_budget.py LIVE.gcode CANDIDATE.gcode [--time-pct P] [--json out.json]   (--time-pct: per-profile D2 value, passed by the frozen runner)
PASS only if: sparse_infill_density identical (same-infill comparison), footer estimated time <= live * (1 + TIME_BUDGET_PCT/100) (default +1.0%),
footer filament [g] <= live * (1 + FILAMENT_BUDGET_PCT/100) (+4.0%). Exit 0 PASS, 1 FAIL.
"""
import argparse, json, re, sys, hashlib

# >>> PM PARAMETERS (changed only by the PM before launch; the brief is rebuilt with the new sha) >>>
TIME_BUDGET_PCT = 2.0       # KENDON D2 (11:11 AM MT) ApexCore +1%, raised to +2% at 12:17 PM MT (D3 skin fix ~+1.7%); runner passes --time-pct per profile (ApexLight +10%)
FILAMENT_BUDGET_PCT = 4.0
# <<< PM PARAMETERS <<<


def info(fn):
    t = open(fn, errors="replace").read()
    m = re.search(r"^; estimated printing time \(normal mode\) = (.+)$", t, re.M)
    g = re.search(r"^; filament used \[g\] = ([\d.]+)", t, re.M)
    inf = re.search(r"^; sparse_infill_density = (\S+)", t, re.M)
    secs = None
    if m:
        secs = 0
        for v, u in re.findall(r"(\d+)([dhms])", m.group(1)):
            secs += int(v) * {"d": 86400, "h": 3600, "m": 60, "s": 1}[u]
    return {"time_s": secs, "time": m.group(1) if m else None, "filament_g": float(g.group(1)) if g else None,
            "infill": inf.group(1) if inf else None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("live")
    ap.add_argument("cand")
    ap.add_argument("--json")
    ap.add_argument("--time-pct", type=float)
    a = ap.parse_args()
    tp = TIME_BUDGET_PCT if a.time_pct is None else a.time_pct
    L, C = info(a.live), info(a.cand)
    fails = []
    if None in L.values() or None in C.values():
        fails.append("missing footer/infill field")
    else:
        if L["infill"] != C["infill"]:
            fails.append("infill %s != live %s (compare at same infill)" % (C["infill"], L["infill"]))
        if C["time_s"] > L["time_s"] * (1 + tp / 100.0):
            fails.append("time %s > live %s +%.1f%%" % (C["time"], L["time"], tp))
        if C["filament_g"] > L["filament_g"] * (1 + FILAMENT_BUDGET_PCT / 100.0):
            fails.append("filament %.2f g > live %.2f g +%.1f%%" % (C["filament_g"], L["filament_g"], FILAMENT_BUDGET_PCT))
    rep = {"result": "PASS" if not fails else "FAIL", "failures": fails, "live": L, "cand": C,
           "time_pct": (100.0 * (C["time_s"] / L["time_s"] - 1)) if L["time_s"] and C["time_s"] else None,
           "filament_pct": (100.0 * (C["filament_g"] / L["filament_g"] - 1)) if L["filament_g"] and C["filament_g"] else None,
           "time_budget_pct": tp, "print_budget_sha256": hashlib.sha256(open(__file__, "rb").read()).hexdigest()}
    if a.json:
        json.dump(rep, open(a.json, "w"), indent=1)
    print(json.dumps(rep, indent=1))
    sys.exit(0 if not fails else 1)


if __name__ == "__main__":
    main()
