#!/usr/bin/env python3
"""Phase 0 forefoot-widening feasibility measurement.

Reads BASE G-code sliced by validation/seam_fuse_gates/slice_all.sh and the
source STLs. Writes JSON/CSV under data/. Does not modify the slicer.

Frame (see FEASIBILITY.md F1):
  sfg_common.parse returns machine coordinates. BeltTransform.cpp locks
  Y_mach = 1.41421356 * Y_virtual. That machine Y is already the distance
  along the 45-degree extrusion plane, so bead gaps and E/mm use machine XY
  directly. Model reconstruction for the anatomical axis uses the inverse:
  Z_model = Y_g / 1.41421356, Y_model = Z_g - Z_model, X_model = X_g.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "validation"))
sys.path.insert(0, str(ROOT))

from seam_fuse_gates.sfg_common import parse, perimeter_chains, seglen, toolpath_seconds  # noqa: E402
from stl_preprocess import _read_triangles, prepare_orthocad_insole_stl  # noqa: E402

# Locked coefficient, BeltTransform.cpp BELT_I_YY (not a freshly computed sqrt).
BELT_I_YY = 1.41421356
H_SPEC = 0.46
A_FIL = math.pi * (1.75 / 2.0) ** 2  # mm^2, 1.75 mm filament
Q_CAP = 35.0
OVERLAP_TARGET = 0.10
OVERLAP_MIN = 0.05
EXTERIOR_MAX = 0.02
ENDPOINT_MAX = 0.001
E_PER_MM_TOL = 0.005
TOUCH_EPS = 1e-4  # mm; floating-point contact, not a bonding allowance
BIN = 0.5
FOREFOOT_FRAC = 0.40

PERIM = {"Outer wall", "Overhang wall", "WALL-OUTER"}
INFILL_TYPES = {"Sparse infill", "Internal solid infill", "Solid infill", "Top surface", "Bottom surface"}


def q3(x: float) -> float:
    return math.copysign(math.floor(abs(x) * 1000.0 + 0.5) / 1000.0, x)


def q5(x: float) -> float:
    return math.copysign(math.floor(abs(x) * 100000.0 + 0.5) / 100000.0, x)


def bead_width(e_per_mm: float, h: float = H_SPEC) -> float:
    """w = (E/mm)*A_fil/H + H*(1 - pi/4). Task formula."""
    return e_per_mm * A_FIL / h + h * (1.0 - math.pi / 4.0)


def area_of_width(w: float, h: float = H_SPEC) -> float:
    return w * h - h * h * (1.0 - math.pi / 4.0)


def seg_seg_dist(ax, ay, bx, by, cx, cy, dx, dy) -> float:
    ux, uy = bx - ax, by - ay
    vx, vy = dx - cx, dy - cy
    wx, wy = ax - cx, ay - cy
    a = ux * ux + uy * uy
    b = ux * vx + uy * vy
    c = vx * vx + vy * vy
    d = ux * wx + uy * wy
    e = vx * wx + vy * wy
    D = a * c - b * b
    SMALL = 1e-14
    sN, sD = 0.0, D
    tN, tD = 0.0, D
    if D < SMALL:
        sN, sD = 0.0, 1.0
        tN, tD = e, c
    else:
        sN = b * e - c * d
        tN = a * e - b * d
        if sN < 0.0:
            sN = 0.0
            tN, tD = e, c
        elif sN > sD:
            sN = sD
            tN, tD = e + b, c
    if tN < 0.0:
        tN = 0.0
        if -d < 0.0:
            sN = 0.0
        elif -d > a:
            sN = sD
        else:
            sN, sD = -d, a
    elif tN > tD:
        tN = tD
        if (-d + b) < 0.0:
            sN = 0.0
        elif (-d + b) > a:
            sN = sD
        else:
            sN, sD = -d + b, a
    sc = 0.0 if abs(sN) < SMALL else sN / sD
    tc = 0.0 if abs(tN) < SMALL else tN / tD
    px, py = ax + sc * ux, ay + sc * uy
    qx, qy = cx + tc * vx, cy + tc * vy
    return math.hypot(px - qx, py - qy)


def point_seg_dist(px, py, ax, ay, bx, by) -> float:
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    if L2 <= 1e-18:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def line_intersect(p, d, q, e):
    det = d[0] * e[1] - d[1] * e[0]
    if abs(det) < 1e-12:
        return None
    rx, ry = q[0] - p[0], q[1] - p[1]
    s = (rx * e[1] - ry * e[0]) / det
    return (p[0] + s * d[0], p[1] + s * d[1])


def safe_offset_vertex(p, n1, d1, t1, n2, d2, t2):
    """Intersection of two inward-offset edges, clamped so a tiny turn cannot throw the vertex.

    A parallel-curve corner is the line intersection. Nearly collinear edges with different
    shifts put that intersection metres away. The clamp keeps the vertex within 1.25x the
    larger shift of p, which is the largest move a pure inward offset of a sane corner needs.
    """
    ip = line_intersect(
        (p[0] + n1[0] * d1, p[1] + n1[1] * d1), t1,
        (p[0] + n2[0] * d2, p[1] + n2[1] * d2), t2,
    )
    limit = max(0.02, 1.25 * max(d1, d2))
    if ip is None or math.hypot(ip[0] - p[0], ip[1] - p[1]) > limit:
        ip = (p[0] + 0.5 * (n1[0] * d1 + n2[0] * d2), p[1] + 0.5 * (n1[1] * d1 + n2[1] * d2))
    return ip


def signed_area(pts) -> float:
    a = 0.0
    for i in range(len(pts) - 1):
        a += pts[i][0] * pts[i + 1][1] - pts[i + 1][0] * pts[i][1]
    return 0.5 * a


def percentile(vals, p):
    if not vals:
        return None
    s = sorted(vals)
    if len(s) == 1:
        return s[0]
    i = (len(s) - 1) * (p / 100.0)
    lo = int(math.floor(i))
    hi = int(math.ceil(i))
    if lo == hi:
        return s[lo]
    return s[lo] * (hi - i) + s[hi] * (i - lo)


def fmt_num(v, nd=4):
    if v is None:
        return None
    return round(float(v), nd)


def annotate(ev):
    width = None
    height = None
    for d in ev:
        if d["kind"] == "comment":
            raw = d["raw"]
            if raw.startswith(";WIDTH:"):
                width = float(raw.split(":", 1)[1])
            elif raw.startswith(";HEIGHT:"):
                try:
                    height = float(raw.split(":", 1)[1])
                except ValueError:
                    pass
        elif d["kind"] == "ext":
            d["width_comment"] = width
            d["height_comment"] = height
            L = seglen(d)
            d["L"] = L
            d["e_per_mm"] = (d["e"] / L) if L > 1e-9 else 0.0
            h = H_SPEC
            if height is not None and 0.2 < height < 0.8:
                h = height
            d["h"] = h
            d["w"] = bead_width(d["e_per_mm"], h) if L > 1e-9 else 0.0


def gcode_decimals(path: Path) -> dict:
    xyz, e_d, f_d = set(), set(), set()
    with path.open() as fh:
        for line in fh:
            if not line.startswith(("G0", "G1")):
                continue
            body = line.split(";", 1)[0]
            for tok in body.split():
                if len(tok) < 2:
                    continue
                key, val = tok[0], tok[1:]
                dec = len(val.split(".", 1)[1]) if "." in val else 0
                if key in "XYZ":
                    xyz.add(dec)
                elif key == "E":
                    e_d.add(dec)
                elif key == "F":
                    f_d.add(dec)
    return {"xyz": sorted(xyz), "E": sorted(e_d), "F": sorted(f_d)}


def model_of(xg, yg, zg):
    zm = yg / BELT_I_YY
    ym = zg - zm
    return xg, ym, zm


def convex_hull(points):
    pts = sorted(set((round(p[0], 5), round(p[1], 5)) for p in points))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def pca_axis(points):
    n = len(points)
    mx = sum(p[0] for p in points) / n
    my = sum(p[1] for p in points) / n
    sxx = sum((p[0] - mx) ** 2 for p in points) / n
    syy = sum((p[1] - my) ** 2 for p in points) / n
    sxy = sum((p[0] - mx) * (p[1] - my) for p in points) / n
    # Principal direction of the covariance.
    ang = 0.5 * math.atan2(2 * sxy, sxx - syy)
    dx, dy = math.cos(ang), math.sin(ang)
    return (mx, my), (dx, dy)


def anatomical_axis(stl: Path) -> dict:
    """Toe-to-heel axis after the same placement ApexCore uses (toe_first, X=140)."""
    placed, meta = prepare_orthocad_insole_stl(stl, x_center_mm=140.0, print_direction="toe_first")
    tris = _read_triangles(placed)
    verts = []
    for tri in tris:
        verts.extend(tri)
    xs = [v[0] for v in verts]
    ys = [v[1] for v in verts]
    zs = [v[2] for v in verts]
    zmin, zmax = min(zs), max(zs)
    # Plantar outline: vertices on the belt-side face (placement puts that face at Z=0).
    zcut = zmin + max(0.35, 0.02 * (zmax - zmin))
    plantar = [(v[0], v[1]) for v in verts if v[2] <= zcut]
    if len(plantar) < 8:
        plantar = [(v[0], v[1]) for v in verts]
    hull = convex_hull([(v[0], v[1]) for v in verts])
    # PCA of the plantar vertices, checked against the footprint hull.
    origin, direction = pca_axis(plantar if len(plantar) >= 8 else hull)
    # Orient heel-ward as increasing placed-model Y (toe_first puts the toe at min Y).
    proj = [((p[0] - origin[0]) * direction[0] + (p[1] - origin[1]) * direction[1], p[1]) for p in hull]
    # Flip so the low-Y end of the hull is the low-s end.
    low = min(proj, key=lambda t: t[1])
    high = max(proj, key=lambda t: t[1])
    if low[0] > high[0]:
        direction = (-direction[0], -direction[1])
    sprojs = [((p[0] - origin[0]) * direction[0] + (p[1] - origin[1]) * direction[1]) for p in hull]
    smin, smax = min(sprojs), max(sprojs)
    length = smax - smin
    # Width near each end, to say whether the toe end is the wide end.
    def end_width(want_low):
        band = [p for p in hull if (s_of(p, origin, direction) - smin) <= 0.15 * length] if want_low else [
            p for p in hull if (smax - s_of(p, origin, direction)) <= 0.15 * length
        ]
        if len(band) < 2:
            return None
        # Width perpendicular to the axis.
        px, py = -direction[1], direction[0]
        ts = [(p[0] * px + p[1] * py) for p in band]
        return max(ts) - min(ts)

    raw = _read_triangles(stl)
    rxs, rys, rzs = [], [], []
    for tri in raw:
        for p in tri:
            rxs.append(p[0])
            rys.append(p[1])
            rzs.append(p[2])
    raw_ext = (max(rxs) - min(rxs), max(rys) - min(rys), max(rzs) - min(rzs))
    box = len(raw) == 12 and all(abs(e - round(e)) < 1e-3 or True for e in raw_ext)
    # A 12-triangle mesh is a triangularized box, not a foot.
    is_box = len(raw) == 12
    ang = math.degrees(math.atan2(direction[1], direction[0]))
    info = {
        "stl": str(stl),
        "placement": {
            "function": "validation.stl_preprocess.prepare_orthocad_insole_stl",
            "print_direction": "toe_first",
            "rotation_z_deg": meta["rotation_z_deg"],
            "x_center_mm": 140.0,
            "note": "Same placement server/chains/apex_core.py uses before slicing.",
        },
        "placed_extents_mm": {
            "x_across": meta["oriented_x_across_belt_mm"],
            "y_along_belt": meta["oriented_y_along_belt_mm"],
            "z_thickness": meta["oriented_z_mm"],
        },
        "raw_extents_mm": {"x": round(raw_ext[0], 3), "y": round(raw_ext[1], 3), "z": round(raw_ext[2], 3)},
        "triangle_count": len(raw),
        "foot_shaped": not is_box,
        "method": "",
        "pca_angle_deg_from_plus_x": round(ang, 3),
        "pca_angle_deg_from_plus_y": round(ang - 90.0, 3),
        "axis_origin_xy": [round(origin[0], 4), round(origin[1], 4)],
        "axis_dir_xy": [round(direction[0], 6), round(direction[1], 6)],
        "s_min": smin,
        "s_max": smax,
        "anatomical_length_mm": length,
        "boundary_s": smin + FOREFOOT_FRAC * length,
        "boundary_fraction": FOREFOOT_FRAC,
        "toe_end_width_mm": None if end_width(True) is None else round(end_width(True), 3),
        "heel_end_width_mm": None if end_width(False) is None else round(end_width(False), 3),
        "plantar_vertex_count": len(plantar),
        "plantar_z_cut_mm": round(zcut, 4),
    }
    if is_box:
        # Not a foot. Principal axis of the rectangle is the longer horizontal
        # side. After toe-first placement that side is across the belt, so it
        # is not a toe-to-heel axis. The axis used for the 40% boundary is the
        # placed print-direction axis (+Y), which is the edge the slicer
        # advances along. Toe end = min Y.
        y0, y1 = min(ys), max(ys)
        info["method"] = (
            "Not foot-shaped: 12-triangle rectangular prism "
            f"{raw_ext[0]:.1f} x {raw_ext[1]:.1f} x {raw_ext[2]:.1f} mm before placement. "
            "PCA of the plantar rectangle is the longer side "
            f"({max(meta['oriented_x_across_belt_mm'], meta['oriented_y_along_belt_mm']):.1f} mm), "
            "which placement maps across the belt, not toe-to-heel. "
            "Axis used: placed model +Y (print direction, toe at min Y), "
            "length = oriented Y extent. The PCA-vs-+Y angle is reported so the "
            "disagreement is visible."
        )
        info["axis_origin_xy"] = [0.0, y0]
        info["axis_dir_xy"] = [0.0, 1.0]
        info["s_min"] = 0.0
        info["s_max"] = y1 - y0
        info["anatomical_length_mm"] = y1 - y0
        info["boundary_s"] = FOREFOOT_FRAC * (y1 - y0)
        info["toe_end_width_mm"] = round(meta["oriented_x_across_belt_mm"], 3)
        info["heel_end_width_mm"] = round(meta["oriented_x_across_belt_mm"], 3)
        info["box_pca_length_mm"] = round(max(meta["oriented_x_across_belt_mm"], meta["oriented_y_along_belt_mm"]), 3)
    else:
        info["method"] = (
            "Placement matches ApexCore (Z rotation "
            f"{meta['rotation_z_deg']} deg, Y and Z mins shifted to 0, X centered at 140). "
            "Plantar outline = convex hull of mesh vertices with Z <= "
            f"{zcut:.2f} mm (belt side) plus the hull of the full footprint for the length. "
            "Axis = first principal component of the plantar vertices, flipped so s increases "
            "toward +Y (heel). Toe is the low-s end. Length = hull extent along that axis. "
            "40% boundary is the plane normal to the axis at s_min + 0.40*L."
        )
    info["anatomical_length_mm"] = round(info["anatomical_length_mm"], 4)
    info["boundary_s"] = round(info["boundary_s"], 4)
    info["s_min"] = round(info["s_min"], 4)
    info["s_max"] = round(info["s_max"], 4)
    return info


def s_of(xy, origin, direction) -> float:
    return (xy[0] - origin[0]) * direction[0] + (xy[1] - origin[1]) * direction[1]


def station_of_gcode(xg, yg, zg, axis) -> float:
    xm, ym, _zm = model_of(xg, yg, zg)
    origin = axis["axis_origin_xy"]
    direction = axis["axis_dir_xy"]
    return s_of((xm, ym), origin, direction)


def clip_to_bin(x0, y0, x1, y1, cid):
    xa = cid * BIN
    xb = xa + BIN
    dx = x1 - x0
    if abs(dx) < 1e-12:
        if xa - 1e-9 <= x0 < xb - 1e-12:
            return [(x0, y0, x1, y1)]
        return []
    ts = [0.0, 1.0]
    for edge in (xa, xb):
        t = (edge - x0) / dx
        if 0.0 < t < 1.0:
            ts.append(t)
    ts = sorted(set(ts))
    out = []
    for a, b in zip(ts, ts[1:]):
        if b - a < 1e-12:
            continue
        tm = 0.5 * (a + b)
        xm = x0 + tm * dx
        if xa - 1e-9 <= xm < xb - 1e-12:
            out.append((x0 + a * dx, y0 + a * (y1 - y0), x0 + b * dx, y0 + b * (y1 - y0)))
    return out


def join_clips(clips):
    """clips: list of (x0,y0,x1,y1,w) in path order. Join those that meet."""
    lines = []
    cur = []
    for c in clips:
        if not cur:
            cur = [c]
            continue
        if math.hypot(cur[-1][2] - c[0], cur[-1][3] - c[1]) <= 1e-5:
            cur.append(c)
        else:
            lines.append(cur)
            cur = [c]
    if cur:
        lines.append(cur)
    if len(lines) >= 2:
        a, b = lines[0], lines[-1]
        if math.hypot(a[0][0] - b[-1][2], a[0][1] - b[-1][3]) <= 1e-5:
            lines[-1].extend(a)
            lines = lines[1:]
    return lines


def poly_mean_y(line) -> float:
    ys = []
    for c in line:
        ys.append(c[1])
        ys.append(c[3])
    return sum(ys) / len(ys)


def poly_centroid(line):
    sx = sy = n = 0.0
    for c in line:
        sx += c[0] + c[2]
        sy += c[1] + c[3]
        n += 2
    return sx / n, sy / n


def min_capsule_sep(line_a, line_b) -> float:
    best = 1e9
    for a in line_a:
        for b in line_b:
            d = seg_seg_dist(a[0], a[1], a[2], a[3], b[0], b[1], b[2], b[3])
            sep = d - 0.5 * a[4] - 0.5 * b[4]
            if sep < best:
                best = sep
    return best


def components_touching(pieces) -> list:
    """Union-find index groups. pieces: (x0,y0,x1,y1,w)."""
    n = len(pieces)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    for i in range(n):
        a = pieces[i]
        ay0, ay1 = (a[1], a[3]) if a[1] <= a[3] else (a[3], a[1])
        ax0, ax1 = (a[0], a[2]) if a[0] <= a[2] else (a[2], a[0])
        ra = 0.5 * a[4] + TOUCH_EPS
        for j in range(i + 1, n):
            b = pieces[j]
            by0, by1 = (b[1], b[3]) if b[1] <= b[3] else (b[3], b[1])
            bx0, bx1 = (b[0], b[2]) if b[0] <= b[2] else (b[2], b[0])
            rb = 0.5 * b[4]
            if ax1 + ra < bx0 - rb or bx1 + rb < ax0 - ra or ay1 + ra < by0 - rb or by1 + rb < ay0 - ra:
                continue
            d = seg_seg_dist(a[0], a[1], a[2], a[3], b[0], b[1], b[2], b[3])
            if d - 0.5 * a[4] - 0.5 * b[4] <= TOUCH_EPS:
                union(i, j)
    return [find(i) for i in range(n)]


def between_beads(seg, bottom, top) -> bool:
    """True if seg's midpoint Y sits strictly between the two skin polylines."""
    xm = 0.5 * (seg[0] + seg[2])
    ym = 0.5 * (seg[1] + seg[3])
    def y_at(line):
        best = None
        bestd = 1e9
        for c in line:
            if abs(c[2] - c[0]) < 1e-9:
                if abs(c[0] - xm) < BIN:
                    return 0.5 * (c[1] + c[3])
                continue
            if (c[0] - xm) * (c[2] - xm) <= 1e-8:
                t = (xm - c[0]) / (c[2] - c[0])
                return c[1] + t * (c[3] - c[1])
            # nearest
            d = min(abs(c[0] - xm), abs(c[2] - xm))
            if d < bestd:
                bestd = d
                best = 0.5 * (c[1] + c[3])
        return best
    yb = y_at(bottom)
    yt = y_at(top)
    if yb is None or yt is None:
        return False
    lo, hi = (yb, yt) if yb < yt else (yt, yb)
    return lo + 1e-4 < ym < hi - 1e-4


def load_layers(path: Path):
    ev = parse(str(path))
    annotate(ev)
    chains = perimeter_chains(ev)
    by_layer_ext = defaultdict(list)
    for i, d in enumerate(ev):
        if d["kind"] == "ext":
            by_layer_ext[d["layer"]].append(i)
    by_layer_chains = defaultdict(list)
    for ci, c in enumerate(chains):
        by_layer_chains[c["layer"]].append(ci)
    return ev, chains, by_layer_ext, by_layer_chains


def classify(path: Path, axis: dict, want_transform: bool) -> dict:
    ev, chains, by_layer_ext, by_layer_chains = load_layers(path)
    origin = axis["axis_origin_xy"]
    direction = axis["axis_dir_xy"]
    s_min = axis["s_min"]
    length = axis["anatomical_length_mm"]
    s_bound = s_min + FOREFOOT_FRAC * length

    # Layer progression from extruding layers.
    ext_layers = sorted(by_layer_ext)
    z_of = {}
    for lay in ext_layers:
        d = ev[by_layer_ext[lay][0]]
        z_of[lay] = d["z1"]
    z_first = z_of[ext_layers[0]]
    z_last = z_of[ext_layers[-1]]
    l_prog = z_last - z_first
    z_prog_bound = z_first + FOREFOOT_FRAC * l_prog
    # Anatomical boundary as a belt station: the plantar point on the axis at s_bound
    # has model Y = origin_y + (s_bound - s_at_origin)*dir_y. s is measured from origin,
    # and s_of(origin) = 0, but s_min may be negative. The point at s_bound:
    # xy = origin + (s_bound - 0) * direction, only if s is relative to origin.
    # s_of uses origin, so a point at s=s_bound lies at origin + s_bound*direction
    # ONLY when we didn't re-zero. s_bound is an absolute projection (not shifted).
    # Wait: anatomical_axis stores s_min as the min projection, and boundary_s as
    # s_min+0.4*L which IS an absolute projection value. And for the box case I
    # overwrote s_min to 0 and origin to (0, y0) with dir (0,1), boundary_s = 0.4*L.
    # Use axis['boundary_s'] which is absolute.
    s_bound = axis["boundary_s"]
    bx = origin[0] + s_bound * direction[0]
    by = origin[1] + s_bound * direction[1]
    # Plantar point (model Z=0) on that station has Z_g = Y_m + Z_m = by.
    z_anat_bound = by

    def layer_for_z(z):
        best = None
        bestd = 1e9
        for lay, zz in z_of.items():
            dd = abs(zz - z)
            if dd < bestd:
                bestd = dd
                best = lay
        return best, bestd

    lay_anat, _ = layer_for_z(z_anat_bound)
    lay_prog, _ = layer_for_z(z_prog_bound)

    zone_rows = []
    unbonded_rows = []
    counts = {"zone": 0, "bonded": 0, "unbonded": 0, "forefoot_columns": 0}
    layer_gaps = {}
    # For the transform: per layer, set of zone column ids, and per (layer, col, side) dw.
    zone_cols = defaultdict(set)  # layer -> set of cid
    # segment id -> list of (cid, side, gap, dw10)
    # We'll recompute dw from stored zone rows.

    first_inner = None
    type_counts = defaultdict(int)
    q_live = []
    for d in ev:
        if d["kind"] != "ext":
            continue
        type_counts[d["type"]] += 1
        if d["type"] == "Inner wall" and first_inner is None:
            first_inner = d["layer"]
        if d["type"] == "Outer wall" and 21 <= d["layer"] <= 119 and d["L"] > 1e-6 and d["f"] > 0:
            q_live.append(d["e"] * A_FIL / (d["L"] / (d["f"] / 60.0)))

    for lay in ext_layers:
        idxs = by_layer_ext[lay]
        # Build clips per column from every extrusion, and outer-chain polylines.
        col_pieces = defaultdict(list)  # cid -> list of dicts
        chain_ids_closed = []
        for ci in by_layer_chains[lay]:
            c = chains[ci]
            if c["closed"] and all(t in PERIM or t == "Outer wall" for t in c["types"]):
                chain_ids_closed.append(ci)
        closed_set = set(chain_ids_closed)

        # Walk extrusions in order; tag chain membership.
        seg_chain = {}
        for ci in by_layer_chains[lay]:
            for si in chains[ci]["seg_idx"]:
                seg_chain[si] = ci

        # Ordered clips per (chain, cid)
        ordered = defaultdict(list)  # (chain or -1, cid) -> clips in order
        for si in idxs:
            d = ev[si]
            if d["L"] < 1e-9 or d["w"] <= 0:
                continue
            x0, y0, x1, y1 = d["x0"], d["y0"], d["x1"], d["y1"]
            c0 = math.floor(min(x0, x1) / BIN) - 1
            c1 = math.floor(max(x0, x1) / BIN) + 1
            ci = seg_chain.get(si, -1)
            for cid in range(c0, c1 + 1):
                for cl in clip_to_bin(x0, y0, x1, y1, cid):
                    rec = {
                        "x0": cl[0], "y0": cl[1], "x1": cl[2], "y1": cl[3],
                        "w": d["w"], "type": d["type"], "chain": ci,
                        "si": si, "h": d["h"], "e": d["e"], "L": d["L"], "f": d["f"],
                    }
                    col_pieces[cid].append(rec)
                    ordered[(ci, cid)].append((cl[0], cl[1], cl[2], cl[3], d["w"], si))

        occupied = sorted(col_pieces)
        if lay % 40 == 0:
            print(f"    layer {lay} cols {len(occupied)}", flush=True)
        for cid in occupied:
            pieces = col_pieces[cid]
            # Outer polylines from each closed chain.
            outer_lines = []  # (chain, line, mean_y)
            for ci in chain_ids_closed:
                clips = ordered.get((ci, cid), [])
                if not clips:
                    continue
                for line in join_clips(clips):
                    outer_lines.append((ci, line, poly_mean_y(line)))
            outer_lines.sort(key=lambda t: t[2])
            # Station from the lowest-Y piece of any kind (belt / plantar side).
            lowest = min(pieces, key=lambda p: min(p["y0"], p["y1"]))
            sx, sy = 0.5 * (lowest["x0"] + lowest["x1"]), min(lowest["y0"], lowest["y1"])
            # Use the bead centre, not the min corner: centroid of lowest outer line if any.
            if outer_lines:
                cx, cy = poly_centroid(outer_lines[0][1])
            else:
                cx, cy = 0.5 * (lowest["x0"] + lowest["x1"]), 0.5 * (lowest["y0"] + lowest["y1"])
            # Z of this layer is constant.
            zg = z_of[lay]
            st = station_of_gcode(cx, cy, zg, axis)
            in_ff = st <= s_bound + 1e-6
            if not in_ff:
                continue
            counts["forefoot_columns"] += 1

            # Zone test.
            is_zone = False
            gap = None
            bottom = top = None
            if len(outer_lines) == 2 and outer_lines[0][0] == outer_lines[1][0]:
                bottom = outer_lines[0][1]
                top = outer_lines[1][1]
                # Nothing else between them: inner wall or infill.
                blocked = False
                for p in pieces:
                    if p["type"] in PERIM:
                        continue
                    if p["type"] == "Inner wall" or p["type"] in INFILL_TYPES or p["type"] not in PERIM:
                        if between_beads((p["x0"], p["y0"], p["x1"], p["y1"]), bottom, top):
                            blocked = True
                            break
                if not blocked:
                    is_zone = True
                    gap = min_capsule_sep(bottom, top)

            if is_zone:
                counts["zone"] += 1
                zone_cols[lay].add(cid)
                dw10 = max(0.0, (gap + OVERLAP_TARGET) / 2.0)
                dw05 = max(0.0, (gap + OVERLAP_MIN) / 2.0)
                zone_rows.append({
                    "layer": lay, "x0": round(cid * BIN, 3), "gap": gap,
                    "s": st, "dw10": dw10, "dw05": dw05,
                    "zg": zg,
                })
                layer_gaps.setdefault(lay, []).append(gap)
                continue

            # Bond graph.
            caps = [(p["x0"], p["y0"], p["x1"], p["y1"], p["w"]) for p in pieces]
            if len(caps) == 1:
                counts["bonded"] += 1
                continue
            groups = components_touching(caps)
            # Skins: extreme outer lines if present, else extreme pieces.
            if outer_lines:
                i_bot = next(i for i, p in enumerate(pieces)
                             if p["si"] in {c[5] for c in outer_lines[0][1]} and min(p["y0"], p["y1"]) <= outer_lines[0][2] + 1.0)
                # More reliable: piece whose midpoint is closest to each skin centroid.
                cb = poly_centroid(outer_lines[0][1])
                ct = poly_centroid(outer_lines[-1][1])
                def nearest(target):
                    best_i, best_d = 0, 1e9
                    for i, p in enumerate(pieces):
                        mx, my = 0.5 * (p["x0"] + p["x1"]), 0.5 * (p["y0"] + p["y1"])
                        dd = (mx - target[0]) ** 2 + (my - target[1]) ** 2
                        if dd < best_d:
                            best_i, best_d = i, dd
                    return best_i
                ib, it = nearest(cb), nearest(ct)
            else:
                ib = min(range(len(pieces)), key=lambda i: min(pieces[i]["y0"], pieces[i]["y1"]))
                it = max(range(len(pieces)), key=lambda i: max(pieces[i]["y0"], pieces[i]["y1"]))
            if groups[ib] == groups[it]:
                counts["bonded"] += 1
            else:
                counts["unbonded"] += 1
                def nearest_sep(idx):
                    best = 1e9
                    a = caps[idx]
                    for j, b in enumerate(caps):
                        if j == idx:
                            continue
                        sep = seg_seg_dist(a[0], a[1], a[2], a[3], b[0], b[1], b[2], b[3]) - 0.5 * a[4] - 0.5 * b[4]
                        if sep < best:
                            best = sep
                    return best
                unbonded_rows.append({
                    "layer": lay, "x0": round(cid * BIN, 3), "s": round(st, 3),
                    "zg": round(zg, 3),
                    "n_pieces": len(pieces),
                    "types": sorted({p["type"] for p in pieces}),
                    "sep_bottom_mm": nearest_sep(ib),
                    "sep_top_mm": nearest_sep(it),
                })

    # Aggregate gaps.
    all_gaps = [r["gap"] for r in zone_rows]
    per_layer = []
    for lay in sorted(layer_gaps):
        gs = layer_gaps[lay]
        per_layer.append({
            "layer": lay,
            "z_gcode": round(z_of[lay], 3),
            "n": len(gs),
            "median": percentile(gs, 50),
            "p5": percentile(gs, 5),
            "p95": percentile(gs, 95),
            "max": max(gs),
            "min": min(gs),
        })

    # Required filament if every column-local dw is applied on the two beads,
    # estimated from the zone segments below.
    result = {
        "gcode": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "decimals": gcode_decimals(path),
        "n_events_ext": sum(1 for d in ev if d["kind"] == "ext"),
        "extruding_layers": [ext_layers[0], ext_layers[-1], len(ext_layers)],
        "z_first": z_first,
        "z_last": z_last,
        "layer_pitch_mm": (z_last - z_first) / (ext_layers[-1] - ext_layers[0]) if len(ext_layers) > 1 else None,
        "L_prog_mm": l_prog,
        "z_prog_boundary": z_prog_bound,
        "layer_prog_boundary": lay_prog,
        "z_anat_boundary": z_anat_bound,
        "layer_anat_boundary": lay_anat,
        "boundary_difference_mm": z_prog_bound - z_anat_bound,
        "first_inner_wall_layer": first_inner,
        "type_counts": dict(type_counts),
        "live_Q_layers_21_119_outer": {
            "n": len(q_live),
            "max": max(q_live) if q_live else None,
            "p99": percentile(q_live, 99) if q_live else None,
            "median": percentile(q_live, 50) if q_live else None,
        },
        "counts": counts,
        "gap_overall": {
            "n": len(all_gaps),
            "median": percentile(all_gaps, 50) if all_gaps else None,
            "p5": percentile(all_gaps, 5) if all_gaps else None,
            "p95": percentile(all_gaps, 95) if all_gaps else None,
            "max": max(all_gaps) if all_gaps else None,
            "min": min(all_gaps) if all_gaps else None,
        },
        "per_layer": per_layer,
        "zone_rows": zone_rows,
        "unbonded_rows": unbonded_rows,
        "s_bound": s_bound,
    }
    if want_transform:
        result["transform"] = scripted_transform(ev, chains, by_layer_ext, by_layer_chains, zone_rows, path)
    # Drop bulky duplicates from the in-memory copy returned; caller writes files.
    return result


def scripted_transform(ev, chains, by_layer_ext, by_layer_chains, zone_rows, src: Path) -> dict:
    """Specified transform: shift zone centre lines inward by dw/2, raise E, F unchanged.

    Non-zone geometry is not moved. A move that crosses the zone boundary is split.
    The outside piece keeps the BASE line; the inside piece is shifted, so the
    written endpoint gap is the quantity F5 measures.
    """
    # Map (layer, cid) -> gap, and which segment indices are the top/bottom.
    gap_of = {}
    for r in zone_rows:
        gap_of[(r["layer"], int(round(r["x0"] / BIN)))] = r["gap"]

    # Identify, per closed chain, ordered segments and whether each sample column is zone.
    # Build new geometry for ext segment indices that we replace.
    replacements = {}  # line number -> list of new line strings (without deciding byte copy)

    moved_seam = []
    transitions = []
    new_ext = []  # dicts for Q and filament, all ext moves in the output (zone changed + unchanged)
    exterior_samples_old = []
    exterior_samples_new = []
    # We'll collect per-layer old/new segments for exterior on zone layers only.
    layer_old = defaultdict(list)
    layer_new = defaultdict(list)

    zone_layers = {r["layer"] for r in zone_rows}
    # segment index -> dw (0 if not zone). Computed while walking chains.
    seg_dw = {}
    seg_shift_geom = {}  # si -> (x0,y0,x1,y1) new, or None to keep

    for ci, c in enumerate(chains):
        if not c["closed"]:
            continue
        if c["layer"] not in zone_layers:
            continue
        if not all(t in PERIM or t == "Outer wall" for t in c["types"]):
            continue
        segs = c["segs"]
        if len(segs) < 3:
            continue
        pts = c["pts"]
        area = signed_area(pts)
        if abs(area) < 1e-8:
            continue
        sign = 1.0 if area > 0 else -1.0
        # Per original segment, find zone coverage and dw.
        piece_seq = []  # across the whole chain, subpieces in order
        for s_i, s in enumerate(segs):
            x0, y0, x1, y1 = s["x0"], s["y0"], s["x1"], s["y1"]
            dx, dy = x1 - x0, y1 - y0
            L = math.hypot(dx, dy)
            if L < 1e-9:
                continue
            # Column membership along the segment.
            atomic = []
            if abs(dx) < 1e-9:
                cid = math.floor(x0 / BIN)
                atomic.append((0.0, 1.0, cid))
            else:
                ts = [0.0, 1.0]
                xa, xb = (x0, x1) if x0 < x1 else (x1, x0)
                c_lo = math.floor(xa / BIN)
                c_hi = math.floor((xb - 1e-9) / BIN)
                for cid in range(c_lo, c_hi + 1):
                    for edge in (cid * BIN, (cid + 1) * BIN):
                        t = (edge - x0) / dx
                        if 1e-8 < t < 1.0 - 1e-8:
                            ts.append(t)
                ts = sorted(set(ts))
                for a, b in zip(ts, ts[1:]):
                    if b - a < 1e-8:
                        continue
                    xm = x0 + 0.5 * (a + b) * dx
                    atomic.append((a, b, math.floor(xm / BIN)))
            # Merge consecutive atomics with the same zone flag.
            merged = []
            for a, b, cid in atomic:
                flag = (s["layer"], cid) in gap_of
                if merged and merged[-1][2] == flag and merged[-1][3] == cid or (
                    merged and merged[-1][2] == flag
                ):
                    # extend; keep max dw
                    g = gap_of.get((s["layer"], cid), None)
                    dw = max(0.0, (g + OVERLAP_TARGET) / 2.0) if g is not None else 0.0
                    prev = merged[-1]
                    merged[-1] = (prev[0], b, flag, max(prev[3], dw), s_i)
                else:
                    g = gap_of.get((s["layer"], cid), None)
                    dw = max(0.0, (g + OVERLAP_TARGET) / 2.0) if flag else 0.0
                    merged.append((a, b, flag, dw, s_i))
            for a, b, flag, dw, _ in merged:
                piece_seq.append({
                    "a": a, "b": b, "zone": flag, "dw": dw if flag else 0.0,
                    "seg": s, "s_i": s_i, "L": L, "dx": dx, "dy": dy,
                })

        if not piece_seq:
            continue
        # Group consecutive zone pieces into runs. A run that wraps the closed
        # chain (first and last both zone, and the chain's seam is inside) is closed
        # only when EVERY piece is zone.
        all_zone = all(p["zone"] for p in piece_seq)
        # Build polyline runs.
        runs = []
        cur = []
        for p in piece_seq:
            if p["zone"]:
                cur.append(p)
            else:
                if cur:
                    runs.append(cur)
                    cur = []
        if cur:
            runs.append(cur)
        if all_zone and runs:
            runs = [piece_seq]  # one closed run

        def edge_normal(p):
            L = math.hypot(p["dx"], p["dy"])
            tx, ty = p["dx"] / L, p["dy"] / L
            return (sign * (-ty), sign * tx), (tx, ty)

        def offset_run(run, closed):
            # Build vertex list from the run's sub-segments (they may subdivide a straight seg).
            pts_r = []
            dists = []
            normals = []
            dirs = []
            for p in run:
                x0 = p["seg"]["x0"] + p["a"] * p["dx"]
                y0 = p["seg"]["y0"] + p["a"] * p["dy"]
                x1 = p["seg"]["x0"] + p["b"] * p["dx"]
                y1 = p["seg"]["y0"] + p["b"] * p["dy"]
                if not pts_r:
                    pts_r.append((x0, y0))
                pts_r.append((x1, y1))
                nrm, dr = edge_normal(p)
                normals.append(nrm)
                dirs.append(dr)
                dists.append(0.5 * p["dw"])  # centre shift
            n = len(dists)
            if closed:
                # drop the duplicate end if it matches the start (it should, within the chain)
                if math.hypot(pts_r[-1][0] - pts_r[0][0], pts_r[-1][1] - pts_r[0][1]) < 0.05:
                    pts_r = pts_r[:-1]
                    # dists/normals correspond to edges; if we dropped a vertex the last edge
                    # still connects last to first. pts_r length should equal n.
                if len(pts_r) != n:
                    # fall back: treat as open
                    closed = False
            new_pts = [None] * (n + (0 if closed else 1))
            if not closed:
                new_pts = [None] * (n + 1)
                nrm0 = normals[0]
                new_pts[0] = (pts_r[0][0] + nrm0[0] * dists[0], pts_r[0][1] + nrm0[1] * dists[0])
                nrm1 = normals[-1]
                new_pts[-1] = (pts_r[-1][0] + nrm1[0] * dists[-1], pts_r[-1][1] + nrm1[1] * dists[-1])
                for i in range(1, n):
                    new_pts[i] = safe_offset_vertex(
                        pts_r[i], normals[i - 1], dists[i - 1], dirs[i - 1],
                        normals[i], dists[i], dirs[i],
                    )
            else:
                new_pts = [None] * n
                for i in range(n):
                    im = (i - 1) % n
                    new_pts[i] = safe_offset_vertex(
                        pts_r[i], normals[im], dists[im], dirs[im],
                        normals[i], dists[i], dirs[i],
                    )
                new_pts.append(new_pts[0])
                pts_r.append(pts_r[0])
            return pts_r, new_pts, dists, closed

        # Seam is chain start (pts[0]), which is the start of the first segment.
        seam_old = (segs[0]["x0"], segs[0]["y0"])

        if all_zone:
            old_pts, new_pts, dists, closed = offset_run(piece_seq, True)
            # Map each piece to a new segment between new_pts[k] and new_pts[k+1].
            for k, p in enumerate(piece_seq):
                p["p0"] = new_pts[k]
                p["p1"] = new_pts[k + 1]
                p["old0"] = old_pts[k]
                p["old1"] = old_pts[k + 1]
            seam_new = new_pts[0]
            moved_seam.append({
                "layer": c["layer"],
                "old": seam_old,
                "new": seam_new,
                "disp": math.hypot(seam_new[0] - seam_old[0], seam_new[1] - seam_old[1]),
                "miny": c["miny"],
                "on_bottom": abs(seam_old[1] - c["miny"]) <= 0.25,
                "fully_zone": True,
            })
        else:
            # Offset each run independently. Piece endpoints stored on the pieces.
            # Also record transitions at run ends.
            consumed = set()
            for run in runs:
                old_pts, new_pts, dists, _ = offset_run(run, False)
                for k, p in enumerate(run):
                    p["p0"] = new_pts[k]
                    p["p1"] = new_pts[k + 1]
                    p["old0"] = old_pts[k]
                    p["old1"] = old_pts[k + 1]
                # Transition gap: shifted end vs the unshifted boundary point.
                gap0 = math.hypot(new_pts[0][0] - old_pts[0][0], new_pts[0][1] - old_pts[0][1])
                gap1 = math.hypot(new_pts[-1][0] - old_pts[-1][0], new_pts[-1][1] - old_pts[-1][1])
                transitions.append({
                    "layer": c["layer"], "end": "start",
                    "gap_mm": gap0,
                    "shift_mm": dists[0],
                })
                transitions.append({
                    "layer": c["layer"], "end": "end",
                    "gap_mm": gap1,
                    "shift_mm": dists[-1],
                })
            # Seam displacement if the seam vertex is on a zone piece.
            seam_new = seam_old
            if piece_seq[0]["zone"] and "p0" in piece_seq[0]:
                seam_new = piece_seq[0]["p0"]
            moved_seam.append({
                "layer": c["layer"],
                "old": seam_old,
                "new": seam_new,
                "disp": math.hypot(seam_new[0] - seam_old[0], seam_new[1] - seam_old[1]),
                "miny": c["miny"],
                "on_bottom": abs(seam_old[1] - c["miny"]) <= 0.25,
                "fully_zone": False,
            })

        # Emit replacement lines grouped by original segment line number.
        # Outside subpieces and zone subpieces of the same original G1 become
        # several lines replacing that one line.
        by_line = defaultdict(list)
        for p in piece_seq:
            by_line[p["seg"]["line"]].append(p)
        for line_no, parts in by_line.items():
            # Preserve order along the segment (increasing a).
            parts.sort(key=lambda p: p["a"])
            seg = parts[0]["seg"]
            out_lines = []
            for p in parts:
                if not p["zone"]:
                    x0 = seg["x0"] + p["a"] * p["dx"]
                    y0 = seg["y0"] + p["a"] * p["dy"]
                    x1 = seg["x0"] + p["b"] * p["dx"]
                    y1 = seg["y0"] + p["b"] * p["dy"]
                    # Quantize the new split endpoint only; if this subpiece is the
                    # entire original move, the caller keeps the original text.
                    frac = p["b"] - p["a"]
                    e = seg["e"] * frac
                    out_lines.append(("out", x0, y0, x1, y1, e, seg["f"], seg["w"], seg["h"], frac, p))
                else:
                    x0, y0 = p["p0"]
                    x1, y1 = p["p1"]
                    Ln = math.hypot(x1 - x0, y1 - y0)
                    w_new = seg["w"] + p["dw"]
                    e = area_of_width(w_new, seg["h"]) / A_FIL * Ln if Ln > 1e-9 else 0.0
                    out_lines.append(("zone", x0, y0, x1, y1, e, seg["f"], w_new, seg["h"], p["b"] - p["a"], p))
            # If the only part is a full outside piece, mark byte-copy.
            full_out = (
                len(parts) == 1 and not parts[0]["zone"]
                and parts[0]["a"] <= 1e-8 and parts[0]["b"] >= 1.0 - 1e-8
            )
            replacements[line_no] = {"full_out": full_out, "parts": out_lines, "seg": seg}

            lay = seg["layer"]
            sdx, sdy = seg["x1"] - seg["x0"], seg["y1"] - seg["y0"]
            sL = math.hypot(sdx, sdy) or 1.0
            # Outward unit normal from the chain winding (opposite the inward normal).
            ox, oy = sign * sdy / sL, sign * (-sdx) / sL
            layer_old[lay].append((seg["x0"], seg["y0"], seg["x1"], seg["y1"], seg["w"], ox, oy))
            for kind, x0, y0, x1, y1, e, f, w, h, frac, p in out_lines:
                layer_new[lay].append((x0, y0, x1, y1, w, ox, oy))
                Ln = math.hypot(x1 - x0, y1 - y0)
                if Ln > 1e-9 and f > 0 and e > 0:
                    new_ext.append({
                        "layer": lay, "kind": kind, "e": e, "L": Ln, "f": f, "w": w,
                        "Q": e * A_FIL / (Ln / (f / 60.0)),
                        "dw": p["dw"] if kind == "zone" else 0.0,
                    })

    # Exterior Hausdorff-style sample on zone layers.
    def outward_samples(segs):
        samples = []
        for x0, y0, x1, y1, w, ox, oy in segs:
            dx, dy = x1 - x0, y1 - y0
            if w <= 0:
                continue
            r = 0.5 * w
            for u in (0.2, 0.5, 0.8):
                samples.append((x0 + u * dx + ox * r, y0 + u * dy + oy * r))
        return samples

    exterior_change = []
    for lay in zone_layers:
        old_s = layer_old[lay]
        new_s = layer_new[lay]
        if not old_s or not new_s:
            continue
        # Protrusion: new boundary points outside the old union.
        prot = 0.0
        rec = 0.0
        for px, py in outward_samples(new_s):
            cl = min(point_seg_dist(px, py, *s[:4]) - 0.5 * s[4] for s in old_s)
            if cl > prot:
                prot = cl
        for px, py in outward_samples(old_s):
            cl = min(point_seg_dist(px, py, *s[:4]) - 0.5 * s[4] for s in new_s)
            if cl > rec:
                rec = cl
        exterior_change.append({"layer": lay, "protrusion": prot, "recession": rec})

    # Write G-code and measure continuity from the text.
    src_lines = src.read_text(errors="replace").splitlines(keepends=True)
    # A G1 only stores its end point. Emitting the shifted end while the nozzle
    # is still at the frozen boundary would draw a diagonal and hide the gap.
    # A G0 to the shifted start is written so the following G1 is the shifted
    # segment. F5 measures that G0. It is the discontinuity, not a bridge that
    # extrudes across it.
    out_lines = []
    replaced_lines = set(replacements)
    for ln, line in enumerate(src_lines, 1):
        if ln not in replaced_lines:
            out_lines.append(line if line.endswith("\n") else line + "\n")
            continue
        rep = replacements[ln]
        if rep["full_out"]:
            out_lines.append(line if line.endswith("\n") else line + "\n")
            continue
        for kind, x0, y0, x1, y1, e, f, w, h, frac, p in rep["parts"]:
            ftxt = f"{f:.0f}" if abs(f - round(f)) < 1e-6 else f"{f:.3f}"
            # Bridge note: if we need the extrusion to START at (x0,y0), write a
            # G0 to that point first when it is not the natural previous point.
            # Always write G0 to the exact start, then G1 to the end. For an
            # outside partial whose start equals the previous end, the G0 is a
            # zero-length reposition to the same (quantized) point. A zero-length
            # G0 is still an added command. Only emit G0 when the start differs
            # from the quantized previous target.
            out_lines.append(("__PART__", kind, q3(x0), q3(y0), q3(x1), q3(y1), q5(e), ftxt))

    # Second pass: resolve __PART__ against running position.
    final_lines = []
    mx = my = None
    # Seed modal XY by scanning until the first replaced part. Copied lines can
    # update position; we re-scan the built list.
    pending = out_lines
    resolved = []
    pos = [None, None]

    def apply_copied(text):
        body = text.split(";", 1)[0]
        if not body.startswith(("G0", "G1", "G00", "G01")):
            return
        xs = ys = None
        for tok in body.split():
            if tok.startswith("X"):
                xs = float(tok[1:])
            elif tok.startswith("Y"):
                ys = float(tok[1:])
        if xs is not None:
            pos[0] = xs
        if ys is not None:
            pos[1] = ys

    for item in pending:
        if isinstance(item, str):
            resolved.append(item)
            apply_copied(item)
            continue
        _, kind, x0, y0, x1, y1, e, ftxt = item
        # Gap from current position to this part's start.
        if pos[0] is not None:
            gap = math.hypot(pos[0] - x0, pos[1] - y0)
        else:
            gap = 0.0
        if gap > 1e-6:
            # Reposition so the extrusion segment is the intended one.
            # Recorded as a zone-boundary discontinuity (the G0 spans it).
            resolved.append(f"G0 X{x0:.3f} Y{y0:.3f}\n")
            transitions.append({
                "layer": None, "end": "written",
                "gap_mm": gap, "kind": kind,
                "from": [pos[0], pos[1]], "to": [x0, y0],
            })
        resolved.append(f"G1 X{x1:.3f} Y{y1:.3f} E{e:.5f} F{ftxt}\n")
        pos[0], pos[1] = x1, y1

    out_path = Path("/tmp") / f"forefoot_transform_{src.stem}.gcode"
    out_path.write_text("".join(resolved))

    # Re-measure endpoint gaps from the written text: consecutive extrusions
    # whose starts do not equal the previous extrusion's end. parse() chains
    # modal position, so a G0 in between yields ext moves that DO meet (the G0
    # moved the start). The discontinuity is then a travel between extrusions.
    # Measure travel length between extrusions that we inserted (G0 we added).
    written_gaps = [t["gap_mm"] for t in transitions if t.get("end") == "written"]
    geom_gaps = [t["gap_mm"] for t in transitions if t.get("end") in ("start", "end")]

    # Q from new_ext plus unchanged ext that were not replaced.
    replaced_seg_ids = set()
    # new_ext only has replaced chain segments. Add Q of every original ext
    # whose line was not rewritten as zone.
    q_all = []
    q_zone = []
    for rec in new_ext:
        q_all.append(rec["Q"])
        if rec["kind"] == "zone":
            q_zone.append(rec["Q"])
    # Unchanged extrusions keep their live Q.
    rewritten_lines = {ln for ln, rep in replacements.items() if not rep["full_out"]}
    for d in ev:
        if d["kind"] != "ext" or d["L"] <= 1e-9 or d["f"] <= 0:
            continue
        if d["line"] in rewritten_lines:
            continue
        q_all.append(d["e"] * A_FIL / (d["L"] / (d["f"] / 60.0)))

    # Filament.
    e_zone_new = sum(r["e"] for r in new_ext if r["kind"] == "zone")
    e_zone_old = 0.0
    e_by_layer_new = defaultdict(float)
    e_by_layer_old = defaultdict(float)
    for ln, rep in replacements.items():
        if rep["full_out"]:
            continue
        seg = rep["seg"]
        for kind, x0, y0, x1, y1, e, f, w, h, frac, p in rep["parts"]:
            if kind != "zone":
                continue
            e_zone_old += seg["e"] * frac
            e_by_layer_new[seg["layer"]] += e
            e_by_layer_old[seg["layer"]] += seg["e"] * frac
    added_e = e_zone_new - e_zone_old
    per_layer_added = []
    for lay in sorted(set(e_by_layer_new) | set(e_by_layer_old)):
        de = e_by_layer_new[lay] - e_by_layer_old[lay]
        per_layer_added.append({
            "layer": lay,
            "added_filament_mm": de,
            "added_volume_mm3": de * A_FIL,
        })

    # Time. toolpath_seconds on both files.
    t_base = toolpath_seconds(str(src))
    t_new = toolpath_seconds(str(out_path))

    # Outside-part checks: collinearity and E/mm of written outside partials.
    outside_fail = []
    for ln, rep in replacements.items():
        if rep["full_out"]:
            continue
        seg = rep["seg"]
        for kind, x0, y0, x1, y1, e, f, w, h, frac, p in rep["parts"]:
            if kind != "out":
                continue
            # Distance of quantized endpoints from the original infinite line.
            def dist_line(px, py):
                return point_seg_dist(px, py, seg["x0"], seg["y0"], seg["x1"], seg["y1"])
            dev = max(dist_line(q3(x0), q3(y0)), dist_line(q3(x1), q3(y1)))
            Ln = math.hypot(x1 - x0, y1 - y0)
            e_mm = (e / Ln) if Ln > 1e-9 else None
            base = seg["e_per_mm"] if "e_per_mm" in seg else (seg["e"] / seg["L"] if seg["L"] else None)
            rel = abs(e_mm - base) / base if e_mm is not None and base else None
            if dev > ENDPOINT_MAX or (rel is not None and rel > E_PER_MM_TOL):
                outside_fail.append({
                    "line": ln, "dev_mm": dev, "e_mm_rel": rel, "L": Ln, "layer": seg["layer"],
                })

    ext_max = 0.0
    if exterior_change:
        ext_max = max(max(r["protrusion"], r["recession"]) for r in exterior_change)

    seam_on_bottom = [s for s in moved_seam if s["on_bottom"]]
    max_seam = max((s["disp"] for s in moved_seam), default=0.0)
    max_seam_bottom = max((s["disp"] for s in seam_on_bottom), default=0.0)
    max_gap_written = max(written_gaps, default=0.0)
    max_gap_geom = max(geom_gaps, default=0.0)

    # Columns that the ideal shift would close, ignoring the boundary gap:
    # new_gap = gap - 2*(dw/ something). dw per bead = (gap+0.10)/2, closure = dw+dw = gap+0.10,
    # new separation = -0.10. That holds on segment interiors. Boundary columns
    # sit on the split and are not fully shifted. Count zone columns whose
    # segment was only partially shifted by checking transitions.
    n_trans = len([t for t in transitions if t.get("end") in ("start", "end")])

    return {
        "out_gcode": str(out_path),
        "n_rewritten_lines": len(rewritten_lines),
        "n_zone_layers": len(zone_layers),
        "transitions_geom_n": n_trans,
        "max_geom_endpoint_gap_mm": max_gap_geom,
        "max_written_endpoint_gap_mm": max_gap_written,
        "written_gap_n": len(written_gaps),
        "exterior_max_mm": ext_max,
        "exterior_by_layer_worst": sorted(exterior_change, key=lambda r: -max(r["protrusion"], r["recession"]))[:8],
        "seam_max_disp_mm": max_seam,
        "seam_bottom_max_disp_mm": max_seam_bottom,
        "seam_bottom_n": len(seam_on_bottom),
        "seam_moved_over_0_001": sum(1 for s in moved_seam if s["disp"] > ENDPOINT_MAX),
        "Q_all_max": max(q_all) if q_all else None,
        "Q_all_p99": percentile(q_all, 99) if q_all else None,
        "Q_zone_max": max(q_zone) if q_zone else None,
        "Q_zone_p99": percentile(q_zone, 99) if q_zone else None,
        "added_filament_mm": added_e,
        "added_filament_mm3": added_e * A_FIL,
        "added_volume_mm3": added_e * A_FIL,
        "per_layer_added": per_layer_added,
        "time_base_s": t_base,
        "time_new_s": t_new,
        "time_delta_s": t_new - t_base,
        "outside_partial_failures": outside_fail[:30],
        "outside_partial_failure_n": len(outside_fail),
        "Q_cap": Q_CAP,
        "overlap_target": OVERLAP_TARGET,
        "note": (
            "Centre-line shift on a zone run moves its endpoints off the frozen "
            "outside path by dw/2. The writer emits G0 across that jump so the "
            "following G1 is the shifted segment rather than a diagonal that hides "
            "the gap. F5 reports both the geometric gap and the gap re-read from "
            "the written coordinates."
        ),
    }


def slim(result: dict) -> dict:
    """Drop per-column rows from the summary JSON; those go to CSV."""
    out = {k: v for k, v in result.items() if k not in ("zone_rows", "unbonded_rows", "per_layer")}
    out["per_layer_n"] = len(result.get("per_layer") or [])
    if result.get("transform"):
        tr = dict(result["transform"])
        # per-layer added can be long; keep it in CSV
        tr["per_layer_added_n"] = len(tr.get("per_layer_added") or [])
        tr.pop("per_layer_added", None)
        out["transform"] = tr
    return out


def write_outputs(name: str, result: dict, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = slim(result)
    (out_dir / f"{name}_summary.json").write_text(json.dumps(summary, indent=2, default=str))
    with (out_dir / f"{name}_layers.csv").open("w", newline="") as fh:
        fields = ["layer", "z_gcode", "n", "median", "p5", "p95", "max", "min"]
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for row in result.get("per_layer") or []:
            w.writerow({k: row[k] for k in fields})
    with (out_dir / f"{name}_zone_mask.csv").open("w", newline="") as fh:
        fields = ["layer", "x0", "gap", "s", "dw10", "dw05", "zg"]
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for row in result.get("zone_rows") or []:
            w.writerow({k: fmt_num(row[k], 5) if isinstance(row[k], float) else row[k] for k in fields})
    with (out_dir / f"{name}_unbonded.csv").open("w", newline="") as fh:
        fields = ["layer", "x0", "s", "zg", "n_pieces", "types", "sep_bottom_mm", "sep_top_mm"]
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for row in result.get("unbonded_rows") or []:
            rr = dict(row)
            rr["types"] = "|".join(row["types"])
            w.writerow(rr)
    if result.get("transform"):
        with (out_dir / f"{name}_added_by_layer.csv").open("w", newline="") as fh:
            fields = ["layer", "added_filament_mm", "added_volume_mm3"]
            w = csv.DictWriter(fh, fieldnames=fields)
            w.writeheader()
            for row in result["transform"]["per_layer_added"]:
                w.writerow({k: fmt_num(row[k], 6) if k != "layer" else row[k] for k in fields})


def self_test():
    # Parallel segments 1.0 mm apart, widths 0.4 => gap 0.6.
    d = seg_seg_dist(0, 0, 10, 0, 0, 1, 10, 1)
    assert abs(d - 1.0) < 1e-9, d
    # Crossing segments.
    d = seg_seg_dist(0, 0, 1, 1, 0, 1, 1, 0)
    assert d < 1e-9, d
    # Width formula round-trip.
    h = 0.46
    w = 1.10
    area = area_of_width(w, h)
    e_mm = area / A_FIL
    w2 = bead_width(e_mm, h)
    assert abs(w2 - w) < 1e-9, (w, w2)
    # Offset of a CCW unit square's bottom edge normal is +Y.
    pts = [(0, 0), (10, 0), (10, 4), (0, 4), (0, 0)]
    assert signed_area(pts) > 0
    print("self_test ok", "A_fil", round(A_FIL, 6))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parent / "data")
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    self_test()
    stls = {
        "orthocad": ROOT / "validation/test_models/orthocad_sample_top_insole.stl",
        "insole": ROOT / "validation/test_models/insole_sample.stl",
    }
    gcodes = {
        ("orthocad", "apex_core"): Path("/tmp/sfg_slices/live_apex_core_orthocad.gcode"),
        ("insole", "apex_core"): Path("/tmp/sfg_slices/live_apex_core_insole2.gcode"),
        ("orthocad", "apex_light"): Path("/tmp/sfg_slices/live_apex_light_orthocad.gcode"),
        ("insole", "apex_light"): Path("/tmp/sfg_slices/live_apex_light_insole2.gcode"),
        ("orthocad", "apex_forge"): Path("/tmp/sfg_slices/live_apex_forge_orthocad.gcode"),
        ("insole", "apex_forge"): Path("/tmp/sfg_slices/live_apex_forge_insole2.gcode"),
        ("orthocad", "apex_bounce"): Path("/tmp/sfg_slices/live_apex_bounce_orthocad.gcode"),
        ("insole", "apex_bounce"): Path("/tmp/sfg_slices/live_apex_bounce_insole2.gcode"),
    }
    axes = {}
    for name, stl in stls.items():
        print("axis", name, flush=True)
        axes[name] = anatomical_axis(stl)
        (args.out / f"{name}_axis.json").write_text(json.dumps(axes[name], indent=2))
        print("  L", axes[name]["anatomical_length_mm"], "bound_s", axes[name]["boundary_s"], "foot", axes[name]["foot_shaped"])

    for (model, mat), gpath in gcodes.items():
        key = f"{model}_{mat}"
        if args.only and args.only not in key:
            continue
        if not gpath.is_file():
            print("missing", gpath)
            continue
        full = mat == "apex_core"
        print("classify", key, "transform" if full else "audit", flush=True)
        result = classify(gpath, axes[model], want_transform=full)
        result["axis_length_mm"] = axes[model]["anatomical_length_mm"]
        result["boundary_s"] = axes[model]["boundary_s"]
        write_outputs(key, result, args.out)
        c = result["counts"]
        g = result["gap_overall"]
        print(f"  columns ff={c['forefoot_columns']} zone={c['zone']} bonded={c['bonded']} unbonded={c['unbonded']}")
        print(f"  gap median={g['median']} p5={g['p5']} p95={g['p95']} max={g['max']}")
        print(f"  Lprog={result['L_prog_mm']:.3f} dBoundary={result['boundary_difference_mm']:.3f} "
              f"lay_anat={result['layer_anat_boundary']} lay_prog={result['layer_prog_boundary']}")
        if full and result.get("transform"):
            t = result["transform"]
            print(f"  F5 geom_gap={t['max_geom_endpoint_gap_mm']:.4f} written_gap={t['max_written_endpoint_gap_mm']:.4f} "
                  f"ext={t['exterior_max_mm']:.4f} Qmax={t['Q_all_max']} seam={t['seam_bottom_max_disp_mm']:.4f} "
                  f"dT={t['time_delta_s']:.3f}s addedE={t['added_filament_mm']:.2f}")


if __name__ == "__main__":
    main()
