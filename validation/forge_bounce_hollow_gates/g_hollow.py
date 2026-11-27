#!/usr/bin/env python3
"""g_hollow.py — FROZEN gate. Forefoot hollow is 0 on the 0.25 mm tool.

A 0.25 mm column is hollow when the outer-envelope section is >= 3 mm, no
non-wall bead overlaps the gap, and the enclosed air is > 1 mm. Report the
count on layers 1..165 and on the physical-equivalent window
ceil(165 * 0.46 / layer_height). Both must be 0. Also report the count over
every layer and the max air in each window.

usage: g_hollow.py CANDIDATE.gcode [--json out.json]
exit 0 PASS, 1 FAIL
"""
import argparse, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fbh_common as C


def evaluate(path):
    parsed = C.parse(path)
    height = C.file_layer_height(parsed)
    equiv = C.equivalent_last(height)
    samples = C.hollow_samples(parsed)
    n165, air165 = C.hollow_window(samples, C.FOREFOOT_LAYER_CAP)
    neq, aireq = C.hollow_window(samples, equiv)
    nall, airall = C.hollow_window(samples, 10 ** 9)
    above = [s for s in samples if s["layer"] > C.FOREFOOT_LAYER_CAP]
    fails = []
    if n165 != 0:
        fails.append("forefoot hollow L<=165 is %d; 0 required" % n165)
    if neq != 0:
        fails.append("forefoot hollow L<=%d (physical equivalent) is %d; 0 required" % (equiv, neq))
    return {
        "gate": "hollow",
        "tool_mm": C.TOOL_MM,
        "section_min_mm": C.HOLLOW_SECTION_MIN,
        "air_min_mm": C.HOLLOW_AIR_MIN,
        "layer_height_mm": height,
        "window_165": C.FOREFOOT_LAYER_CAP,
        "window_equivalent": equiv,
        "hollow_l165": n165,
        "max_air_l165_mm": air165,
        "hollow_equivalent": neq,
        "max_air_equivalent_mm": aireq,
        "hollow_total": nall,
        "hollow_after_165": len(above),
        "max_air_total_mm": airall,
        "result": "FAIL" if fails else "PASS",
        "failures": fails,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cand")
    ap.add_argument("--json")
    a = ap.parse_args()
    rep = C.round_report(evaluate(a.cand))
    if a.json:
        json.dump(rep, open(a.json, "w"), indent=1)
        open(a.json, "a").write("\n")
    print(json.dumps({k: rep[k] for k in ("gate", "result", "hollow_l165", "hollow_equivalent", "hollow_total", "max_air_total_mm", "failures")}))
    sys.exit(0 if rep["result"] == "PASS" else 1)


if __name__ == "__main__":
    main()
