#!/usr/bin/env python3
"""A7 time. ApexCore toolpath time (sfg_common.toolpath_seconds) and filament
(sfg_common.net_e_mm) stay within +0.5 % of BASE on both STLs.

usage: a7_time.py BASE_DIR CAND_DIR [--json out.json]
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scg_common as S
import sfg_common as C

S.pin_sfg()


def stats(path: str) -> dict:
    return {
        "time_s": C.toolpath_seconds(path),
        "filament_mm": C.net_e_mm(path),
        "footer": C.footer(path),
    }


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
        cand = stats(S.gcode(args.cand_dir, name))
        base = stats(S.gcode(args.base_dir, name))
        t_pct = 100.0 * (cand["time_s"] / base["time_s"] - 1.0) if base["time_s"] else None
        f_pct = 100.0 * (cand["filament_mm"] / base["filament_mm"] - 1.0) if base["filament_mm"] else None
        measured[key] = {
            "cand_time_s": cand["time_s"], "base_time_s": base["time_s"], "time_pct": t_pct,
            "cand_filament_mm": cand["filament_mm"], "base_filament_mm": base["filament_mm"],
            "filament_pct": f_pct, "cand_footer": cand["footer"], "base_footer": base["footer"],
        }
        if base["time_s"] <= 0 or base["filament_mm"] <= 0:
            fails.append("%s: BASE time or filament is 0" % key)
            continue
        if cand["time_s"] > base["time_s"] * (1.0 + S.TIME_FRAC):
            fails.append("%s: time %.1f s > BASE %.1f s +0.5%% (%+.3f%%)" % (
                key, cand["time_s"], base["time_s"], t_pct))
        if cand["filament_mm"] > base["filament_mm"] * (1.0 + S.FILAMENT_FRAC):
            fails.append("%s: filament %.2f mm > BASE %.2f mm +0.5%% (%+.3f%%)" % (
                key, cand["filament_mm"], base["filament_mm"], f_pct))
        parts.append(("%s_time_pct" % key, t_pct))
        parts.append(("%s_filament_pct" % key, f_pct))
        parts.append(("%s_time_s" % key, cand["time_s"]))
        parts.append(("%s_base_time_s" % key, base["time_s"]))
    status = "FAIL" if fails else "PASS"
    rep = {"gate": "A7 time", "result": status, "failures": fails, "measured": measured,
           "frac": S.TIME_FRAC}
    S.emit(status, "A7", parts, rep, args.json)


if __name__ == "__main__":
    main()
