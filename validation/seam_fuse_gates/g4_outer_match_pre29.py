#!/usr/bin/env python3
"""g4_outer_match_pre29.py - FROZEN gate G4 (PM, 2026-11-20, rev 3). Do not edit; checked by SHA256SUMS.
Kendon 2026-11-20 9:48 AM MT: the outside surface must not change; 6:16 PM MT: "roll back to that and fix that split seam
but without changing the walls and everything like that". The only ApexCore wall-path change allowed vs LIVE is the
return to PRE-29's Arachne paths, so the candidate is compared with PRE-29.
usage: g4_outer_match_pre29.py CANDIDATE_APEXCORE.gcode PRE29_APEXCORE.gcode [--json out.json]   exit 0 PASS, 1 FAIL
  P  wall paths: per layer, every Outer/Overhang wall bead (and, as a second check, every Inner wall bead) is sampled
     every 0.05 mm; samples within 1.0 mm of any chain start (seam) of that type in that layer, in either file, are left
     out. Deviation = distance to the other file's beads of the same group in that layer, both directions.
     PASS: p99 deviation <= 0.02 mm AND total sampled length deviating > 0.05 mm <= 20 mm, for each group and direction.
  N  same layer count; per layer, the same number of perimeter chains (Outer+Overhang) and Inner wall chains, and the same
     set of extrusion types (no new type such as Gap infill).
  W  flow/width: median E per mm of each extrusion type within 1 % of PRE-29.
  S  speeds: the set of F values used by extruding moves of each type equals PRE-29's, leaving out ONLY moves of closed
     perimeter loops that lie in the first 10 mm or last 10 mm of the loop (Kendon t2446u / t2451u: those are slowed to
     <= 20 mm/s; G3 J3 checks them, the collinear 10 mm splits, and holds every other move to PRE-29's feedrate).
     Collinear splits do not change P (distance-based) or the chain counts in N.
  K  commands: the ordered sequences of fan (M106/M107), temperature (M104/M109/M140/M190), acceleration (M204) and
     flow/speed/PA (M220/M221/M900) commands are identical to PRE-29.
"""
import argparse, json, os, sys, hashlib, statistics
from collections import defaultdict, Counter
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sfg_common as C

STEP = 0.05
SEAM_EXCL = 1.0
P99_MAX = 0.02
DEV_MM = 0.05
DEV_LEN_MAX = 20.0
FLOW_REL = 0.01
GROUPS = {"perimeter": ("Outer wall", "Overhang wall"), "inner": ("Inner wall",)}
KCMDS = {106, 107, 104, 109, 140, 190, 204, 220, 221, 900}


def load(fn):
    ev = C.parse(fn)
    nlayers = max([d["layer"] for d in ev] + [0]) + 1
    segs = {g: defaultdict(list) for g in GROUPS}
    starts = {g: defaultdict(list) for g in GROUPS}
    chains = {g: Counter() for g in GROUPS}
    types = defaultdict(set)
    epm = defaultdict(list)
    fset = defaultdict(set)
    cmds = []
    prev_kind_ext_type = None
    first_moves = set()
    for c in C.perimeter_chains(ev):
        if not c["closed"]:
            continue
        L, acc = c["length"], 0.0
        for k, sg in zip(c["seg_idx"], c["segs"]):
            a0 = acc; acc += C.seglen(sg)
            if L < 20.0 or a0 < 10.0 + 0.02 or acc > L - 10.0 - 0.02:
                first_moves.add(k)
    for i, d in enumerate(ev):
        if d["kind"] == "cmd" and d["m"] in KCMDS:
            cmds.append(d["raw"])
        if d["kind"] != "ext":
            if d["kind"] in ("travel", "retract", "layer") or (d["kind"] == "type"):
                prev_kind_ext_type = None
            continue
        t = d["type"]
        types[d["layer"]].add(t)
        L = C.seglen(d)
        if L > 0.5:
            epm[t].append(d["e"] / L)
        if i not in first_moves:
            fset[t].add(round(d["f"], 1))
        for g, tt in GROUPS.items():
            if t in tt:
                segs[g][d["layer"]].append((d["x0"], d["y0"], d["x1"], d["y1"]))
                if prev_kind_ext_type is None or prev_kind_ext_type not in tt:
                    starts[g][d["layer"]].append((d["x0"], d["y0"]))
                    chains[g][d["layer"]] += 1
        prev_kind_ext_type = t
    return {"n": nlayers, "segs": segs, "starts": starts, "chains": chains, "types": types,
            "epm": {t: statistics.median(v) for t, v in epm.items() if v}, "fset": fset, "cmds": cmds}


def samples(seglist):
    pts = []
    for x0, y0, x1, y1 in seglist:
        L = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
        n = max(1, int(L / STEP))
        t = (np.arange(n) + 0.5) / n
        pts.append(np.stack([x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, np.full(n, L / n)], 1))
    return np.concatenate(pts) if pts else np.zeros((0, 3))


def dist_to_segs(P, seglist):
    if len(P) == 0:
        return np.zeros(0)
    if not seglist:
        return np.full(len(P), np.inf)
    S = np.array(seglist)
    A, B = S[:, :2], S[:, 2:]
    D = B - A
    L2 = np.maximum((D ** 2).sum(1), 1e-18)
    out = np.empty(len(P))
    for k in range(0, len(P), 2000):
        Q = P[k:k + 2000, None, :2]
        t = np.clip(((Q - A[None]) * D[None]).sum(2) / L2[None], 0, 1)
        proj = A[None] + t[..., None] * D[None]
        out[k:k + 2000] = np.sqrt(((Q - proj) ** 2).sum(2)).min(1)
    return out


def compare(a, b):
    res = {}
    for g in GROUPS:
        for dirn, (X, Y) in (("cand->pre29", (a, b)), ("pre29->cand", (b, a))):
            devs, wts, worst = [], [], defaultdict(float)
            for lay in range(max(a["n"], b["n"])):
                P = samples(X["segs"][g].get(lay, []))
                if len(P) == 0:
                    continue
                seams = X["starts"][g].get(lay, []) + Y["starts"][g].get(lay, [])
                if seams:
                    Sm = np.array(seams)
                    dmin = np.sqrt(((P[:, None, :2] - Sm[None]) ** 2).sum(2)).min(1)
                    P = P[dmin > SEAM_EXCL]
                dv = dist_to_segs(P, Y["segs"][g].get(lay, []))
                devs.append(dv); wts.append(P[:, 2])
                big = P[:, 2][dv > DEV_MM].sum()
                if big:
                    worst[lay] = big
            dv = np.concatenate(devs) if devs else np.zeros(0)
            w = np.concatenate(wts) if wts else np.zeros(0)
            finite = np.isfinite(dv)
            res[g + " " + dirn] = {"samples": int(len(dv)),
                                    "p99_mm": float(np.percentile(np.where(finite, dv, 1e9), 99)) if len(dv) else 0.0,
                                    "max_mm": float(dv[finite].max()) if finite.any() else None,
                                    "len_over_0.05_mm": round(float(w[dv > DEV_MM].sum()), 3),
                                    "worst_layers": sorted(((k, round(v, 2)) for k, v in worst.items()), key=lambda kv: -kv[1])[:10]}
    return res


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("cand"); ap.add_argument("pre29"); ap.add_argument("--json")
    a = ap.parse_args()
    A, B = load(a.cand), load(a.pre29)
    fails = []
    P = compare(A, B)
    for k, v in P.items():
        if v["p99_mm"] > P99_MAX:
            fails.append("P %s: p99 %.4f mm > %.2f" % (k, v["p99_mm"], P99_MAX))
        if v["len_over_0.05_mm"] > DEV_LEN_MAX:
            fails.append("P %s: %.2f mm of wall moved > %.2f mm (limit %.0f mm)" % (k, v["len_over_0.05_mm"], DEV_MM, DEV_LEN_MAX))
    if A["n"] != B["n"]:
        fails.append("N: layer count %d != PRE-29 %d" % (A["n"], B["n"]))
    nbad = []
    for lay in range(max(A["n"], B["n"])):
        for g in GROUPS:
            if A["chains"][g].get(lay, 0) != B["chains"][g].get(lay, 0):
                nbad.append((lay, g, A["chains"][g].get(lay, 0), B["chains"][g].get(lay, 0)))
        if A["types"].get(lay, set()) != B["types"].get(lay, set()):
            nbad.append((lay, "types", sorted(A["types"].get(lay, set())), sorted(B["types"].get(lay, set()))))
    if nbad:
        fails.append("N: %d layer/chain-count/type mismatches, first %s" % (len(nbad), nbad[:3]))
    wbad = {t: (round(A["epm"].get(t, 0), 5), round(v, 5)) for t, v in B["epm"].items()
            if t not in A["epm"] or abs(A["epm"][t] / v - 1) > FLOW_REL}
    wbad.update({t: (round(v, 5), None) for t, v in A["epm"].items() if t not in B["epm"]})
    if wbad:
        fails.append("W: E/mm differs >1%% for %s" % wbad)
    sbad = {t: sorted(A["fset"].get(t, set()) ^ B["fset"].get(t, set())) for t in set(A["fset"]) | set(B["fset"])
            if A["fset"].get(t, set()) != B["fset"].get(t, set())}
    if sbad:
        fails.append("S: extrusion speed sets differ: %s" % {k: v[:6] for k, v in sbad.items()})
    if A["cmds"] != B["cmds"]:
        i = next((k for k in range(min(len(A["cmds"]), len(B["cmds"]))) if A["cmds"][k] != B["cmds"][k]), min(len(A["cmds"]), len(B["cmds"])))
        fails.append("K: fan/temp/accel/flow command sequence differs (cand %d cmds, PRE-29 %d; first difference at #%d: %r vs %r)" % (
            len(A["cmds"]), len(B["cmds"]), i, A["cmds"][i] if i < len(A["cmds"]) else None, B["cmds"][i] if i < len(B["cmds"]) else None))
    rep = {"gate": "G4 outer_match_pre29", "sha256": hashlib.sha256(open(__file__, "rb").read()).hexdigest(),
           "cand_file": a.cand, "pre29_file": a.pre29, "paths": P, "layers": [A["n"], B["n"]],
           "epm_cand": A["epm"], "epm_pre29": B["epm"], "count_mismatches": nbad[:20],
           "result": "PASS" if not fails else "FAIL", "failures": fails}
    if a.json:
        json.dump(rep, open(a.json, "w"), indent=1)
    print(json.dumps({k: rep[k] for k in ("gate", "result", "failures")}), file=sys.stderr)
    print(json.dumps(rep, indent=1))
    sys.exit(0 if not fails else 1)


if __name__ == "__main__":
    main()
