#!/usr/bin/env python3
"""g3_seam_joint.py - FROZEN gate G3 (PM, 2026-11-20, rev 2). Do not edit; checked by SHA256SUMS.
Kendon 2026-11-20 7:49 PM MT (t2446u): "I don't care about the fixed X position, but I do think that we need to do the
full prime and print the first segment slowly. I don't know that I agree about stopping .15 mm short seems that would not
work as well as butting".
usage: g3_seam_joint.py CANDIDATE_APEXCORE.gcode --ref PRE29_APEXCORE.gcode [--json out.json]   exit 0 PASS, 1 FAIL
Closed Outer/Overhang perimeter loops (sfg_common.perimeter_chains) are matched to PRE-29's by (layer, order in layer);
the loop count per layer must equal PRE-29's (J0). On EVERY matched loop:
  J1 butt joint: the loop ends within 0.02 mm of its own start and does not extrude past it (no overlap; no stop-short).
  J2 full prime: over the moves between the previous extrusion and the loop's first extruding move, total prime equals
     total retract within 0.01 mm; any retract there is one of the retract amounts PRE-29 uses before its loops (0.5 mm;
     retract length unchanged). Whether a given loop retracts may differ from PRE-29, because main's crossing-free
     inside seam travel (brief step 2d) is kept.
  J3 slow first segment: the loop's FIRST extruding move runs at F <= 1200 mm/min (20 mm/s, Cura's "Belt wall speed");
     every later move of the loop has PRE-29's feedrate for that move (+/- 0.5 mm/min) and the loop has PRE-29's
     segment count, so nothing else is slowed or split.
  J4 dry wipe unchanged: the non-extruding moves between ;WIPE_START and ;WIPE_END after the loop (PRE-29's 0.7 mm
     glide) equal PRE-29's: same count, end points within 0.002 mm, same feedrates (+/- 0.5).
"""
import argparse, json, os, sys, hashlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sfg_common as C

BUTT_TOL = 0.02
PR_TOL = 0.01
SLOW_F = 1200.0
F_TOL = 0.5
XY_TOL = 0.002


def loops(fn):
    ev = C.parse(fn)
    out = {}
    for ch in C.perimeter_chains(ev):
        if not ch["closed"]:
            continue
        i = ch["seg_idx"][0] - 1
        rt = pr = 0.0
        while i >= 0 and ev[i]["kind"] != "ext":
            if ev[i]["kind"] == "retract":
                rt += -ev[i]["e"]
            elif ev[i]["kind"] == "prime":
                pr += ev[i]["e"]
            i -= 1
        wipe = []
        j = ch["seg_idx"][-1] + 1
        inw = False
        while j < len(ev) and ev[j]["kind"] not in ("ext", "layer"):
            d = ev[j]
            if d["kind"] == "comment" and d["raw"].startswith(";WIPE_START"):
                inw = True
            elif d["kind"] == "comment" and d["raw"].startswith(";WIPE_END"):
                break
            elif inw and d["kind"] == "travel":
                wipe.append((d["x1"], d["y1"], d["f"]))
            j += 1
        sx, sy = ch["start"]
        ex, ey = ch["end"]
        last = ch["segs"][-1]
        # extruded past the start: the closing move reaches the start before its own end
        past = 0.0
        d0, t = C.seg_point_dist(sx, sy, last["x0"], last["y0"], last["x1"], last["y1"])
        if d0 <= BUTT_TOL and t < 1.0:
            past = (1 - t) * C.seglen(last)
        out.setdefault(ch["layer"], []).append({
            "layer": ch["layer"], "end_gap": ((ex - sx) ** 2 + (ey - sy) ** 2) ** 0.5, "past": past,
            "ret_seg": ch["ret_seg"], "nseg": len(ch["segs"]), "f": [s["f"] for s in ch["segs"]],
            "first_len": C.seglen(ch["segs"][0]), "retract": rt, "prime": pr, "wipe": wipe})
    return out


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("cand"); ap.add_argument("--ref", required=True); ap.add_argument("--json")
    a = ap.parse_args()
    C_, P_ = loops(a.cand), loops(a.ref)
    bad = {k: [] for k in ("J0", "J1", "J2", "J3", "J4")}
    rset = sorted({round(l["retract"], 3) for v in P_.values() for l in v if l["retract"] > 1e-6})
    n = 0
    for lay in sorted(set(C_) | set(P_)):
        cl, pl = C_.get(lay, []), P_.get(lay, [])
        if len(cl) != len(pl):
            bad["J0"].append({"layer": lay, "cand_loops": len(cl), "pre29_loops": len(pl)})
        for c, p in zip(cl, pl):
            n += 1
            if c["end_gap"] > BUTT_TOL or c["past"] > BUTT_TOL or c["ret_seg"] != c["nseg"] - 1:
                bad["J1"].append({"layer": lay, "end_gap_mm": round(c["end_gap"], 4), "extruded_past_start_mm": round(c["past"], 4)})
            if abs(c["prime"] - c["retract"]) > PR_TOL or (c["retract"] > 1e-6 and not any(abs(c["retract"] - r) <= PR_TOL for r in rset)):
                bad["J2"].append({"layer": lay, "retract": round(c["retract"], 4), "prime": round(c["prime"], 4), "pre29_retract_amounts": rset})
            why = []
            if c["f"][0] > SLOW_F + F_TOL:
                why.append("first move F%.0f > 1200" % c["f"][0])
            if c["nseg"] != p["nseg"]:
                why.append("segments %d != PRE-29 %d" % (c["nseg"], p["nseg"]))
            else:
                k = next((k for k in range(1, c["nseg"]) if abs(c["f"][k] - p["f"][k]) > F_TOL), None)
                if k is not None:
                    why.append("move %d F%.0f != PRE-29 F%.0f" % (k, c["f"][k], p["f"][k]))
            if why:
                bad["J3"].append({"layer": lay, "why": why})
            wc, wp = c["wipe"], p["wipe"]
            if len(wc) != len(wp) or any(abs(u[0] - v[0]) > XY_TOL or abs(u[1] - v[1]) > XY_TOL or abs(u[2] - v[2]) > F_TOL for u, v in zip(wc, wp)):
                bad["J4"].append({"layer": lay, "cand_wipe": wc[:3], "pre29_wipe": wp[:3]})
    fails = ["%s: %d fail (of %d matched loops)" % (k, len(v), n) for k, v in bad.items() if v]
    if n == 0:
        fails.append("no closed perimeter loops matched")
    fl = sorted(l["first_len"] for v in C_.values() for l in v)
    rep = {"gate": "G3 seam_joint", "sha256": hashlib.sha256(open(__file__, "rb").read()).hexdigest(),
           "cand_file": a.cand, "ref_file": a.ref, "matched_loops": n, "pre29_retract_amounts": rset,
           "first_segment_mm": {"min": round(fl[0], 3), "median": round(fl[len(fl) // 2], 3), "max": round(fl[-1], 3)} if fl else None,
           "counts": {k: len(v) for k, v in bad.items()}, "examples": {k: v[:6] for k, v in bad.items()},
           "result": "PASS" if not fails else "FAIL", "failures": fails}
    if a.json:
        json.dump(rep, open(a.json, "w"), indent=1)
    print(json.dumps({k: rep[k] for k in ("gate", "result", "failures")}), file=sys.stderr)
    print(json.dumps(rep, indent=1))
    sys.exit(0 if not fails else 1)


if __name__ == "__main__":
    main()
