#!/usr/bin/env python3
"""seam_bottom.py - FROZEN gate (OrthoCAD PM, 2026-11-20). Do not edit; CI checks its sha256.

Kendon 2026-11-20 11:17 AM MT (verbatim): "And make sure that there are no layer changes on the surface or sides that they're
all on the bottom as required".

usage: seam_bottom.py LIVE.gcode CANDIDATE.gcode [--light-ref LIVE_APEXLIGHT.gcode] [--json out.json]     (needs shapely 2)
Exit 0 PASS, 1 FAIL.  Physical belt G-code frame: every layer is a 45-degree cross-section of the insole; Y is the
section's through-thickness direction on the belt (Y = 0 is the belt). The insole's BOTTOM (sole) face is the belt side of
each section (low Y), its TOP (foot) surface the high-Y side, its medial/lateral SIDES the X ends where the outline turns.

Faces (per layer, from the model's own outline as printed):
  O = union of the layer's Outer + Overhang wall bead footprints (flat-capped buffers of half the E-derived width,
      H = 0.46), closed by 0.1 mm, holes filled; its exterior ring (oriented counter-clockwise) is the outer face.
  For a point p, q = nearest point on the nearest exterior ring of O, n = outward unit normal of the ring at q (tangent over
  +/- NORMAL_SPAN mm). BOTTOM if n_y <= -cos(FACE_HALF_ANGLE) (normal within 45 deg of -Y), TOP if n_y >= cos(45 deg),
  SIDE otherwise.
Points checked (per layer, both files):
  LC  layer change: the end point of every move that changes Z (on this printer the layer change is the ";seam travel"
      X/Y/Z move to the new layer's start point);
  OS  every Outer wall / Overhang wall run start (seam) and stop point;
  VS  start and stop points of Inner wall, Gap infill, Sparse/Internal solid/Solid infill runs that are VISIBLE: their bead
      reaches the outer face (distance from p to the face <= own half width + VIS_TOL 0.05 mm).
Rule (ApexLight, no --light-ref): each category's off-bottom count (TOP + SIDE) must be 0 in the candidate; if live already
has off-bottom points in a category, the candidate may have no more than live there and the report sets
"live_off_bottom_flag" (PM tells Kendon).
Rule (ApexCore, --light-ref LIVE_APEXLIGHT_SAME_MODEL.gcode; Kendon 11:38 AM MT "The seams have always been on the bottom
it's been a permanent rule since the beginning", 12:17 PM MT "return the seam to the bottom"): an ABSOLUTE bar, not
"no worse than live" (live ApexCore has its seams on the top face since PR #31). On every layer except the first and the
last extruding layer (outer_lock C locks those two to live byte-for-byte, so they are held to "no worse than live"):
  LC  bottom fraction of layer changes  >= max(LC_FLOOR 0.98, ApexLight live's LC bottom fraction on the same model and
      layers);
  CS  bottom fraction of CLOSED perimeter-loop starts (Outer+Overhang chain whose path returns to its start <= 0.011 mm
      after >= 3 segments, outer_lock's definition; the start is the seam)  >= ApexLight live's CS fraction (measured
      1.000 on both models: 435/435 orthocad, 92/92 insole2);
  OE  off-bottom count of OPEN perimeter-chain ends (toe-tip/tab walls, geometry that outer_lock keeps byte-identical)
      <= live ApexCore's;   VS  off-bottom count <= live ApexCore's (live: 0).
  Measured (live ApexLight, 918c5373 image, layers as above): LC orthocad 431/436 = 0.9885, insole2 87/92 = 0.9457, so the
  LC bar is 0.9885 (orthocad) and 0.98 (insole2: the floor is stricter than ApexLight there; a plantar-start prototype of
  the fix measured LC 435/435 and 90/90, CS 432/432 and 90/90, so the floor is reachable). The gate recomputes the
  ApexLight fractions from the live file the runner passes, so the bar is the measured one by construction.
The report always lists live's and the candidate's placement (bottom/top/side counts per category, worst examples).
"""
import argparse, hashlib, json, math, sys
import numpy as np
import shapely
from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

A_FIL = math.pi * (1.75 / 2) ** 2
H = 0.46
ADJ = H * (1 - math.pi / 4)
FACE_HALF_ANGLE = 45.0
NORMAL_SPAN = 0.5
VIS_TOL = 0.05
OUTER = ("Outer wall", "Overhang wall")
OTHER = ("Inner wall", "Gap infill", "Sparse infill", "Internal solid infill", "Solid infill")


def parse(fn):
    layers = [{"segs": [], "lc": []}]
    x = y = z = 0.0
    E = 0.0; rel = True; t = None; wipe = False
    for line in open(fn, errors="replace"):
        if line.startswith(";LAYER_CHANGE"):
            layers.append({"segs": [], "lc": []}); continue
        if line.startswith(";TYPE:"):
            t = line[6:].strip(); continue
        if line.startswith(";WIPE_START"):
            wipe = True; continue
        if line.startswith(";WIPE_END"):
            wipe = False; continue
        s = line.split(";")[0].split()
        if not s:
            continue
        c = s[0]
        if c == "M83": rel = True; continue
        if c == "M82": rel = False; continue
        if c == "G92":
            for m in s[1:]:
                if m[0] == "E": E = float(m[1:])
            continue
        if c not in ("G0", "G1"):
            continue
        w = {}
        for m in s[1:]:
            try: w[m[0]] = float(m[1:])
            except ValueError: pass
        nx, ny, nz = w.get("X", x), w.get("Y", y), w.get("Z", z)
        de = 0.0
        if "E" in w:
            de = w["E"] if rel else w["E"] - E
            if not rel: E = w["E"]
        if nz != z:
            layers[-1]["lc"].append((nx, ny))
        d = math.hypot(nx - x, ny - y)
        if d > 1e-9 and de > 1e-9 and not wipe:
            layers[-1]["segs"].append((t, x, y, nx, ny, de / d * A_FIL / H + ADJ))
        else:
            layers[-1]["segs"].append(None)
        x, y, z = nx, ny, nz
    return layers


def runs(segs):
    out = []; cur = None
    for s in segs:
        if s is not None and cur is not None and cur[0] == s[0] and abs(cur[-1][3] - s[1]) < 1e-9 and abs(cur[-1][4] - s[2]) < 1e-9:
            cur.append(s); continue
        if cur is not None: out.append(cur[1:] if False else cur)
        cur = [s[0], s] if s is not None else None
        if cur is not None:
            cur = [s[0], s]
    if cur is not None: out.append(cur)
    return [(r[0], r[1:]) for r in out]


def outline(segs):
    fp = [LineString([(s[1], s[2]), (s[3], s[4])]).buffer(s[5] / 2, cap_style="flat") for s in segs if s and s[0] in OUTER]
    if not fp:
        return []
    O = unary_union(fp).buffer(0.1).buffer(-0.1)
    polys = list(O.geoms) if hasattr(O, "geoms") else [O]
    return [orient(Polygon(p.exterior), 1.0) for p in polys if not p.is_empty and p.area > 0.5]


def classify(polys, p):
    pt = Point(p)
    best = None
    for P in polys:
        ring = P.exterior
        d = ring.distance(pt)
        if best is None or d < best[0]:
            best = (d, ring)
    d, ring = best
    s = ring.project(pt); L = ring.length
    a = ring.interpolate((s - NORMAL_SPAN) % L); b = ring.interpolate((s + NORMAL_SPAN) % L)
    tx, ty = b.x - a.x, b.y - a.y; n = math.hypot(tx, ty) or 1.0
    nx, ny = ty / n, -tx / n          # outward normal of a CCW ring
    c = math.cos(math.radians(FACE_HALF_ANGLE))
    face = "bottom" if ny <= -c else ("top" if ny >= c else "side")
    return face, d, (round(nx, 3), round(ny, 3))


def measure(fn):
    Ls = parse(fn)
    R = {k: {"bottom": 0, "top": 0, "side": 0} for k in ("LC", "OS", "VS")}
    ex = {k: [] for k in R}
    vis_types = {}
    for li, lay in enumerate(Ls):
        segs = lay["segs"]
        polys = outline(segs)
        if not polys:
            continue
        pts = [("LC", p, None) for p in lay["lc"]]
        for t, r in runs(segs):
            for p in ((r[0][1], r[0][2]), (r[-1][3], r[-1][4])):
                if t in OUTER:
                    pts.append(("OS", p, t))
                elif t in OTHER:
                    pts.append(("VS?", p, (t, max(s[5] for s in r))))
        for k, p, info in pts:
            if k == "VS?":
                t, w = info
                dface = min(P.exterior.distance(Point(p)) for P in polys)
                if dface > w / 2 + VIS_TOL:
                    continue
                k = "VS"; vis_types[t] = vis_types.get(t, 0) + 1
            face, d, n = classify(polys, p)
            R[k][face] += 1
            if face != "bottom" and len(ex[k]) < 8:
                ex[k].append({"layer": li, "x": round(p[0], 3), "y": round(p[1], 3), "face": face, "normal": n, "dist_to_face": round(d, 3), "type": info if k != "LC" else "layer change"})
    return R, ex, vis_types


ROT_CLOSE = 0.011
LC_FLOOR = 0.98


def chains(segs):
    """contiguous Outer/Overhang bead chains (a type change between them does not break a chain)"""
    out = []; cur = None
    for s in segs:
        if s is not None and s[0] in OUTER:
            if cur and abs(cur[-1][3] - s[1]) < 1e-9 and abs(cur[-1][4] - s[2]) < 1e-9:
                cur.append(s); continue
            if cur: out.append(cur)
            cur = [s]; continue
        if cur: out.append(cur)
        cur = None
    if cur: out.append(cur)
    return out


def closed_chain(c):
    return len(c) >= 3 and any(math.hypot(c[j][3] - c[0][1], c[j][4] - c[0][2]) <= ROT_CLOSE for j in range(2, len(c)))


def measure_abs(fn):
    """per-layer LC / CS / OE / VS placement split into 'mid' (all but first+last extruding layer) and 'ends'"""
    Ls = parse(fn)
    ext = [i for i, l in enumerate(Ls) if any(s is not None for s in l["segs"])]
    ends = {ext[0], ext[-1]} if ext else set()
    R = {g: {k: {"bottom": 0, "top": 0, "side": 0} for k in ("LC", "CS", "OE", "VS")} for g in ("mid", "ends")}
    ex = {k: [] for k in ("LC", "CS", "OE", "VS")}
    for li, lay in enumerate(Ls):
        segs = lay["segs"]; polys = outline(segs)
        if not polys:
            continue
        g = "ends" if li in ends else "mid"
        pts = [("LC", p) for p in lay["lc"]]
        for c in chains(segs):
            if closed_chain(c):
                pts.append(("CS", (c[0][1], c[0][2])))
            else:
                pts += [("OE", (c[0][1], c[0][2])), ("OE", (c[-1][3], c[-1][4]))]
        for t, r in runs(segs):
            if t in OTHER:
                for p in ((r[0][1], r[0][2]), (r[-1][3], r[-1][4])):
                    w = max(s[5] for s in r)
                    if min(P.exterior.distance(Point(p)) for P in polys) <= w / 2 + VIS_TOL:
                        pts.append(("VS", p))
        for k, p in pts:
            face, d, n = classify(polys, p)
            R[g][k][face] += 1
            if face != "bottom" and g == "mid" and len(ex[k]) < 8:
                ex[k].append({"layer": li, "x": round(p[0], 3), "y": round(p[1], 3), "face": face, "normal": n, "dist_to_face": round(d, 3)})
    return R, ex


def frac(d):
    n = d["bottom"] + d["top"] + d["side"]
    return (d["bottom"] / n) if n else 1.0


def main_abs(a):
    lr, _ = measure_abs(a.live); cr, ce = measure_abs(a.cand); xr, _ = measure_abs(a.light_ref)
    fails = []; bars = {}
    for k in ("LC", "CS"):
        bars[k] = max(frac(xr["mid"][k]), LC_FLOOR) if k == "LC" else frac(xr["mid"][k])
        if frac(cr["mid"][k]) + 1e-12 < bars[k]:
            fails.append("%s bottom fraction %.4f (%d/%d) < bar %.4f (ApexLight live %d/%d, LC floor %.2f)" % (k, frac(cr["mid"][k]), cr["mid"][k]["bottom"], sum(cr["mid"][k].values()), bars[k], xr["mid"][k]["bottom"], sum(xr["mid"][k].values()), LC_FLOOR))
    for k in ("OE", "VS"):
        lo = lr["mid"][k]["top"] + lr["mid"][k]["side"]; co = cr["mid"][k]["top"] + cr["mid"][k]["side"]
        if co > lo:
            fails.append("%s off-bottom %d > live ApexCore %d" % (k, co, lo))
    for k in ("LC", "CS", "OE", "VS"):
        lo = lr["ends"][k]["top"] + lr["ends"][k]["side"]; co = cr["ends"][k]["top"] + cr["ends"][k]["side"]
        if co > lo:
            fails.append("first/last layer %s off-bottom %d > live %d" % (k, co, lo))
    rep = {"seam_bottom_sha256": hashlib.sha256(open(__file__, "rb").read()).hexdigest(), "mode": "absolute (ApexCore, bar = ApexLight live)",
           "params": {"FACE_HALF_ANGLE": FACE_HALF_ANGLE, "NORMAL_SPAN": NORMAL_SPAN, "VIS_TOL": VIS_TOL, "ROT_CLOSE": ROT_CLOSE, "LC_FLOOR": LC_FLOOR},
           "bars_from_apexlight_live": {k: round(v, 4) for k, v in bars.items()},
           "cand_fraction": {k: round(frac(cr["mid"][k]), 4) for k in ("LC", "CS")},
           "live_apexcore": lr, "cand": cr, "apexlight_live": xr, "cand_off_bottom_examples": ce,
           "result": "PASS" if not fails else "FAIL", "failures": fails}
    if a.json:
        json.dump(rep, open(a.json, "w"), indent=1)
    print(json.dumps(rep, indent=1))
    sys.exit(0 if not fails else 1)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("live"); ap.add_argument("cand"); ap.add_argument("--json"); ap.add_argument("--light-ref")
    a = ap.parse_args()
    if a.light_ref:
        main_abs(a)
    lr, le, lv = measure(a.live)
    cr, ce, cv = measure(a.cand)
    fails = []; flag = []
    for k in ("LC", "OS", "VS"):
        lo = lr[k]["top"] + lr[k]["side"]; co = cr[k]["top"] + cr[k]["side"]
        if lo:
            flag.append("%s: live already has %d off-bottom points (top %d, side %d)" % (k, lo, lr[k]["top"], lr[k]["side"]))
        if co > lo:
            fails.append("%s: %d off-bottom points (top %d, side %d) > %s" % (k, co, cr[k]["top"], cr[k]["side"], "live %d" % lo if lo else "0 required"))
    names = {"LC": "layer changes", "OS": "outer/overhang wall seams (run start+stop)", "VS": "visible inner/gap/infill starts+stops"}
    rep = {"seam_bottom_sha256": hashlib.sha256(open(__file__, "rb").read()).hexdigest(),
           "params": {"FACE_HALF_ANGLE": FACE_HALF_ANGLE, "NORMAL_SPAN": NORMAL_SPAN, "VIS_TOL": VIS_TOL},
           "categories": names, "live": lr, "cand": cr, "live_visible_types": lv, "cand_visible_types": cv,
           "live_off_bottom_examples": le, "cand_off_bottom_examples": ce,
           "live_off_bottom_flag": flag, "result": "PASS" if not fails else "FAIL", "failures": fails}
    if a.json:
        json.dump(rep, open(a.json, "w"), indent=1)
    print(json.dumps(rep, indent=1))
    sys.exit(0 if not fails else 1)


if __name__ == "__main__":
    main()
