#!/usr/bin/env python3
"""g6_flow.py — FROZEN gate G6. Do not edit; checked by SHA256SUMS.

G6 Flow. Every forefoot extruding move of length >= 0.05 mm has volumetric
flow Q = (E/length) * filament_area * (feed/60) <= 17.1 mm^3/s, the BASE
outer-wall peak, and none over the 35 mm^3/s hard cap (filament_max_volumetric_speed,
accepted for feasibility testing). Travels are not speed-capped: BASE travel
feed is 212 mm/s. Inner-wall moves on BASE itself exceed 17.1, so an unchanged
file fails this gate.

usage: g6_flow.py CANDIDATE.gcode [--model orthocad|insole] [--json out.json]
exit 0 PASS, 1 FAIL
"""
import argparse, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fcg_common as C


def evaluate(cand_path, model):
    parsed = C.parse(cand_path)
    last = C.FOREFOOT_LAST[model]
    area = parsed["area"]
    qs = []
    over_cap = over_hard = 0
    worst = None
    for d in parsed["events"]:
        if d["kind"] != "ext" or d.get("leader"):
            continue
        if d["layer"] < 1 or d["layer"] > last:
            continue
        L = C.seglen(d)
        if L < C.FLOW_MIN_LEN or d["e"] <= 1e-9 or d["f"] <= 0:
            continue
        q = (d["e"] / L) * area * (d["f"] / 60.0)
        qs.append(q)
        if worst is None or q > worst[0]:
            worst = (q, d["layer"], d["line"], d.get("type"), d["f"] / 60.0)
        if q > C.FLOW_CAP + 1e-4:
            over_cap += 1
        if q > C.FLOW_HARD + 1e-4:
            over_hard += 1
    fails = []
    if not qs:
        fails.append("no forefoot extrusions measured — fail closed")
    if over_cap:
        fails.append("%d forefoot moves exceed %.1f mm^3/s (max %.3f)" % (over_cap, C.FLOW_CAP, max(qs)))
    if over_hard:
        fails.append("%d forefoot moves exceed the %.1f mm^3/s hard cap" % (over_hard, C.FLOW_HARD))
    return {
        "gate": "G6 flow",
        "model": model,
        "moves": len(qs),
        "max_mm3_s": max(qs) if qs else None,
        "p95_mm3_s": C.percentile(qs, 0.95),
        "median_mm3_s": C.percentile(qs, 0.5),
        "over_17_1": over_cap,
        "over_35": over_hard,
        "cap_mm3_s": C.FLOW_CAP,
        "hard_cap_mm3_s": C.FLOW_HARD,
        "worst": None if worst is None else {
            "q": worst[0], "layer": worst[1], "line": worst[2], "type": worst[3], "speed_mm_s": worst[4],
        },
        "result": "FAIL" if fails else "PASS",
        "failures": fails,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cand")
    ap.add_argument("--model", choices=sorted(C.FOREFOOT_LAST))
    ap.add_argument("--json")
    a = ap.parse_args()
    model = C.detect_model(a.cand, a.model)
    rep = C.round_report(evaluate(a.cand, model))
    if a.json:
        json.dump(rep, open(a.json, "w"), indent=1)
        open(a.json, "a").write("\n")
    print(json.dumps({k: rep[k] for k in ("gate", "result", "failures", "max_mm3_s", "over_17_1", "over_35")}))
    sys.exit(0 if rep["result"] == "PASS" else 1)


if __name__ == "__main__":
    main()
