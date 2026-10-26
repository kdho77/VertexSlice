"""File C post-process (local test only).

Changes vs B's apex_light_postprocess (all other B behaviour kept: belt-wall F1200, fan ramp,
0.7 mm glide at 15.6 mm/s, travel 212 mm/s):
 1. Seam X per layer = midpoint of that layer's belt-contact span (outer-wall segments within
    0.1 mm of the layer's lowest gantry Y), instead of a fixed X140. Closed outer AND inner loops
    start at the point of their lowest edge nearest that X.
 2. Arachne open inner paths that cross the seam X on the belt side are split there and printed
    as two halves that both START at the seam point (q -> start reversed, then q -> end), so they
    finish where Orca expected. Contiguous open+closed inner runs are decomposed so the closed
    part is rotated too (B left those unrotated).
 3. Every seam travel (into a seam start or out of a loop/glide end) is replaced by one direct
    move: optional 0.5 mm retract (travel >= 2 mm, the profile's retraction_minimum_travel),
    gantry-Y lift 0.4 mm, straight move to the target at the lifted height, lower, unretract.
    No travel follows the belt-side wall any more. Z (belt) is never moved except where Orca
    already moved it (layer change).
"""
from __future__ import annotations
import re, math

LIFT_MM = 0.4
RETRACT_MM = 0.5
RETRACT_F = 30 * 60.0          # profile retraction_speed 30 mm/s
RETRACT_MIN_TRAVEL = 3.0       # D: retract only on travels >= 3 mm
RESTART_EXTRA = -0.05          # D: unretract 0.05 mm less than the retract
BELT_SIDE_BAND = 1.6           # D: travel end within 1.6 mm of the layer's lowest Y = belt side
BELT_BAND = 0.1                # belt-contact: within 0.1 mm of the layer's lowest Y
OPEN_BELT_BAND = 1.6           # open inner paths: split only where they run on the belt side
_NUM = r"(-?\d*\.?\d+)"


def _w(line, letter):
    m = re.search(rf"\b{letter}{_NUM}", line.split(";", 1)[0])
    return float(m.group(1)) if m else None


def _is_move(l):
    return l.startswith(("G0 ", "G1 "))


def _is_ext(l):
    b = l.split(";")[0]
    return _is_move(l) and (_w(l, "E") or 0) > 0 and ("X" in b or "Y" in b)


def _is_xy_travel(l):
    b = l.split(";")[0]
    return _is_move(l) and ("X" in b or "Y" in b) and not ((_w(l, "E") or 0) > 0)


TOP_ENV = {}
TOP_REVERSE = False  # tested: moves rim-tip starts to junctions, but both are within 2 mm of the top in layers 415-437 -> no gain
TOP_BAND = 2.0   # D: "top side" = within 2 mm (in-plane, gantry Y x 0.707) of the upper outer-wall envelope


def is_top(layer, p, ymin):
    if p[1] <= ymin + OPEN_BELT_BAND:
        return False
    return any(math.hypot(p[0] - ex, (p[1] - ey) * 0.7071) <= TOP_BAND for ex, ey in TOP_ENV.get(layer, ()) if abs(ex - p[0]) <= TOP_BAND + 1)


def layer_seam_x(text):
    """layer index (same counting as rotate: -1 before first ;LAYER_CHANGE) -> belt-span midpoint X."""
    layer = -1; role = ""; x = y = 0.0
    segs = {}
    for l in text.splitlines():
        if l.startswith(";LAYER_CHANGE"):
            layer += 1
        elif l.startswith(";TYPE:"):
            role = l[6:].strip()
        if _is_move(l):
            nx, ny = _w(l, "X"), _w(l, "Y")
            nx = x if nx is None else nx; ny = y if ny is None else ny
            if _is_ext(l) and role in ("Outer wall", "Overhang wall"):
                segs.setdefault(layer, []).append((x, y, nx, ny))
            x, y = nx, ny
    out = {}
    for k, ss in segs.items():
        env = {}
        for x0, y0, x1, y1 in ss:
            m = max(1, int(math.hypot(x1 - x0, y1 - y0) / 0.3))
            for t in range(m + 1):
                X = x0 + (x1 - x0) * t / m; Y = y0 + (y1 - y0) * t / m
                b = int(X // 1); env[b] = max(env.get(b, -1e9), Y)
        TOP_ENV[k] = sorted((b + 0.5, v) for b, v in env.items())
        ymin = min(min(s[1], s[3]) for s in ss)
        xs = [v for s in ss if max(s[1], s[3]) <= ymin + BELT_BAND for v in (s[0], s[2])]
        if xs:
            out[k] = ((min(xs) + max(xs)) / 2, ymin)
    return out


def process_walls(text, PP, seam):
    """Seam-rotate closed loops at the per-layer seam X; split open inner paths at it."""
    lines = text.splitlines(keepends=True)
    out = []
    x = y = 0.0; f = 0.0; role = ""; layer = -1
    i, n = 0, len(lines)
    glide_f = PP.GLIDE_MM_S * 60.0
    TF = PP.TRAVEL_F
    allowed_all = ("Outer wall", "Overhang wall", "Inner wall")
    stats = {"loops": 0, "split": 0, "decomposed": 0}

    def emit(segs, cur_role, last_f=None):
        for a2, b2, e2, f2, r2, pre2 in segs:
            out.extend(p for p in pre2 if not p.startswith(";TYPE:"))
            if r2 != cur_role:
                out.append(f";TYPE:{r2}\n"); cur_role = r2
            fs = f" F{f2:g}" if f2 != last_f else ""
            out.append(f"G1 X{b2[0]:.3f} Y{b2[1]:.3f} E{e2:.5f}{fs}\n")
            last_f = f2
        return cur_role

    def travel_to(p, pos, tag):
        if math.dist(p, pos) > 1e-3:
            out.append(f"G1 X{p[0]:.3f} Y{p[1]:.3f} F{TF:g} ;{tag}\n")

    while i < n:
        l = lines[i]
        if l.startswith(";TYPE:"):
            role = l[6:].strip()
        elif l.startswith(";LAYER_CHANGE"):
            layer += 1          # role kept across layer change (B's fix)
        if not (_is_ext(l) and role in allowed_all):
            if _is_move(l):
                nx, ny = _w(l, "X"), _w(l, "Y"); fv = _w(l, "F")
                f = fv if fv is not None else f
                x = nx if nx is not None else x; y = ny if ny is not None else y
            out.append(l); i += 1; continue
        kind_outer = role in ("Outer wall", "Overhang wall")
        allowed = ("Outer wall", "Overhang wall") if kind_outer else ("Inner wall",)
        start = (x, y)
        segs = []; pending = []
        j = i; cx, cy, cf, crole = x, y, f, role
        while j < n:
            lj = lines[j]
            if _is_move(lj):
                body = lj.split(";")[0]; e = _w(lj, "E")
                if e is None or e <= 0 or not ("X" in body or "Y" in body):
                    break
                nx, ny = _w(lj, "X"), _w(lj, "Y")
                nx = cx if nx is None else nx; ny = cy if ny is None else ny
                fv = _w(lj, "F"); cf = fv if fv is not None else cf
                segs.append(((cx, cy), (nx, ny), e, cf, crole, pending)); pending = []
                cx, cy = nx, ny; j += 1
            elif lj.startswith(";TYPE:"):
                r = lj[6:].strip()
                if r not in allowed:
                    break
                crole = r; pending.append(lj); j += 1
            elif lj.startswith(";LAYER_CHANGE") or lj.startswith("; stop printing"):
                break
            else:
                pending.append(lj); j += 1
        sx, ymin_l = seam.get(layer, (PP.SEAM_TARGET_X_MM, 0.0))
        # Decompose the run into pieces: (closed?, segs)
        pts = [segs[0][0]] + [s[1] for s in segs]
        close = lambda a, b: abs(a[0] - b[0]) < PP._CLOSE_TOL and abs(a[1] - b[1]) < PP._CLOSE_TOL
        pieces = None
        if len(segs) >= 3 and close(pts[0], pts[-1]):
            pieces = [(True, segs)]
        else:
            # open prefix + closed suffix (pk == pn), or closed prefix (p0 == pk) + open suffix
            k_suf = next((k for k in range(1, len(segs) - 2) if close(pts[k], pts[-1])), None)
            k_pre = next((k for k in range(len(segs), 2, -1) if close(pts[0], pts[k])), None)
            if k_suf is not None and (k_pre is None or len(segs) - k_suf >= k_pre):
                pieces = [(False, segs[:k_suf]), (True, segs[k_suf:])]
            elif k_pre is not None and k_pre < len(segs):
                pieces = [(True, segs[:k_pre]), (False, segs[k_pre:])]
            else:
                pieces = [(False, segs)]
            if len(pieces) > 1:
                stats["decomposed"] += 1
        stop = j - len(pending)
        if len(pieces) == 1 and not pieces[0][0] and (kind_outer or not (_crosses(segs, sx, ymin_l) or _want_rev(segs, sx, ymin_l) or _top_rev(segs, layer, ymin_l, sx))):
            out.extend(lines[i:stop]); x, y, f, role = cx, cy, cf, crole; i = stop; continue
        pos = start; cur_role = role
        for is_closed, ps in pieces:
            p0 = ps[0][0]
            if not is_closed:
                k = _crosses(ps, sx, ymin_l) if not kind_outer else None
                if k is None and not kind_outer and (_want_rev(ps, sx, ymin_l) or _top_rev(ps, layer, ymin_l, sx)):
                    # orient: start at the end nearer the seam X, finish at the far (end-cap) end
                    rv = [(sg[1], sg[0], sg[2], sg[3], sg[4], sg[5]) for sg in reversed(ps)]
                    travel_to(rv[0][0], pos, "seam travel")
                    out.append(";C_SEAM_START\n")
                    cur_role = emit(rv, cur_role); pos = rv[-1][1]
                    out.append(";C_SEAM_END\n")
                    stats["reversed"] = stats.get("reversed", 0) + 1
                    continue
                if k is None:
                    if math.dist(p0, pos) > 1e-3:
                        travel_to(p0, pos, "seam return")
                    cur_role = emit(ps, cur_role); pos = ps[-1][1]
                    continue
                kk, q = k
                a, b, e, sf, sr, pre = ps[kk]
                L = math.dist(a, b); t = 0.0 if L == 0 else math.dist(a, q) / L
                # half 2 first: q -> a (reversed), then segs kk-1..0 reversed; ends at p0
                rev = [(q, a, e * t, sf, sr, pre)] if t > 1e-9 else []
                for m in range(kk - 1, -1, -1):
                    s = ps[m]; rev.append((s[1], s[0], s[2], s[3], s[4], s[5]))
                fwd = ([(q, b, e * (1 - t), sf, sr, [p for p in pre if p.startswith(";WIDTH")])] if t < 1 - 1e-9 else []) + list(ps[kk + 1:])
                for half in (rev, fwd):
                    if not half: continue
                    travel_to(q, pos, "seam travel")
                    out.append(";C_SEAM_START\n")
                    cur_role = emit(half, cur_role)
                    pos = half[-1][1]
                    out.append(";C_SEAM_END\n")
                stats["split"] += 1
                continue
            # closed loop: rotate to nearest point on lowest edge to sx
            ty = min(min(sg[0][1], sg[1][1]) for sg in ps)

            def _pick(band):
                cand = [k for k, sg in enumerate(ps) if max(sg[0][1], sg[1][1]) <= ty + band] or list(range(len(ps)))
                best = None
                for k in cand:
                    t, q = PP._nearest_on_seg(ps[k][0], ps[k][1], sx, ty)
                    d = (q[0] - sx) ** 2 + (q[1] - ty) ** 2
                    if best is None or d < best[0]:
                        best = (d, k, t, q)
                return best
            best = _pick(PP.SEAM_BAND_MM)
            if not kind_outer and abs(best[3][0] - sx) > 1.0:
                best = _pick(PP.INNER_STACK_BAND_MM)
            _, k, t, q = best
            lp = [ps[0][0]] + [sg[1] for sg in ps]
            cum = PP._arc(lp)
            sQ = cum[k] + t * (cum[k + 1] - cum[k])
            a, b, e, sf, sr, pre = ps[k]
            new = []
            if t < 1.0 - 1e-9: new.append((q, b, e * (1 - t), sf, sr, pre))
            new.extend(ps[k + 1:]); new.extend(ps[:k])
            if t > 1e-9: new.append((a, q, e * t, sf, sr, []))
            travel_to(q, pos, "seam travel")
            out.append(";C_SEAM_START\n")
            cur_role = emit(new, cur_role)
            pos = q
            if kind_outer and PP.GLIDE_MM < cum[-1] / 2:
                out.append(";WIPE_START\n")
                for px, py in PP._path(lp, cum, sQ, sQ + PP.GLIDE_MM):
                    out.append(f"G1 X{px:.3f} Y{py:.3f} F{glide_f:g} ;glide\n")
                    pos = (px, py)
                out.append(";WIPE_END\n")
            out.append(";C_SEAM_END\n")
            stats["loops"] += 1
        out.extend(p for p in pending if not p.startswith(";TYPE:"))
        # Orca's following lines assume the nozzle at the run end (cx, cy): hand back via a
        # seam travel (collapsed later into one lifted direct move).
        travel_to((cx, cy), pos, "seam return")
        if cur_role != crole:
            out.append(f";TYPE:{crole}\n")
        out.append(f"G1 F{cf:g}\n")
        x, y, f, role = cx, cy, cf, crole
        i = j
    return "".join(out), stats


def _top_rev(segs, layer, ymin, sx):
    """D: open path starting on the top side: start at the end nearer the seam X (the junction
    with the inner loop), so the free tip at the rim is only an end (no start / prime there)."""
    if not TOP_REVERSE:
        return False
    a, b = segs[0][0], segs[-1][1]
    return is_top(layer, a, ymin) and abs(b[0] - sx) < abs(a[0] - sx) - 0.5


def _want_rev(segs, sx, ymin):
    a, b = segs[0][0], segs[-1][1]
    return a[1] <= ymin + OPEN_BELT_BAND and b[1] <= ymin + OPEN_BELT_BAND and abs(a[0] - sx) > abs(b[0] - sx) + 0.5


def _crosses(segs, sx, ymin):
    """Index + point where an open path crosses X=sx on the belt side (lowest such crossing)."""
    best = None
    for k, (a, b, *_r) in enumerate(segs):
        if (a[0] - sx) * (b[0] - sx) <= 0 and a[0] != b[0]:
            t = (sx - a[0]) / (b[0] - a[0]); q = (sx, a[1] + t * (b[1] - a[1]))
            if q[1] <= ymin + OPEN_BELT_BAND and (best is None or q[1] < best[1][1]):
                best = (k, q)
    if best is None: return None
    k, q = best
    # avoid degenerate splits right at a path end (< 0.3 mm)
    pts = [segs[0][0]] + [s[1] for s in segs]
    if math.dist(q, pts[0]) < 0.3 or math.dist(q, pts[-1]) < 0.3:
        return None
    return best


# Part tests are pure Python. Do not import a geometry package.
_HEEL_ACCEPT_INSET = 0.5
# Final seam hop and the opening step off a sharp tip. A thick wall only needs
# ~1 mm; a sharp horn can be ~2.4 mm from the tip before a 0.5 mm inset exists.
_HEEL_APPROACH_MAX = 2.5
_HEEL_SAMPLE_STEP = 0.5
# Seam targets sit a fraction of a millimeter off the outer-wall centerline.
_HEEL_BOUND_EPS = 0.25


def _hg_closest_on_seg(px, py, ax, ay, bx, by):
    abx, aby = bx - ax, by - ay
    ab2 = abx * abx + aby * aby
    if ab2 < 1e-18:
        return ax, ay, math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * abx + (py - ay) * aby) / ab2))
    qx, qy = ax + t * abx, ay + t * aby
    return qx, qy, math.hypot(px - qx, py - qy)


def _hg_dist_pt_seg(px, py, ax, ay, bx, by):
    _qx, _qy, d = _hg_closest_on_seg(px, py, ax, ay, bx, by)
    return d


def _hg_dedup(pts, tol=1e-4):
    out = []
    for p in pts:
        if not out or math.hypot(p[0] - out[-1][0], p[1] - out[-1][1]) >= tol:
            out.append((float(p[0]), float(p[1])))
    return out


def _hg_area(pts):
    a = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        a += x1 * y2 - x2 * y1
    return 0.5 * a


def _hg_bbox(pts):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _hg_ring_dist(px, py, ring):
    best = 1e18
    n = len(ring)
    if n == 0:
        return best
    for i in range(n):
        ax, ay = ring[i]
        bx, by = ring[(i + 1) % n]
        best = min(best, _hg_dist_pt_seg(px, py, ax, ay, bx, by))
    return best


def _hg_ring_contains(px, py, ring, eps=_HEEL_BOUND_EPS):
    """Inside or on the boundary of one ring."""
    minx, miny, maxx, maxy = _hg_bbox(ring)
    if px < minx - eps or px > maxx + eps or py < miny - eps or py > maxy + eps:
        return False
    if _pip(px, py, ring):
        return True
    return _hg_ring_dist(px, py, ring) <= eps


def _hg_ring_strict_inside(px, py, ring, eps=_HEEL_BOUND_EPS):
    """Inside a ring and not on its boundary. Hole boundaries stay material."""
    if not _pip(px, py, ring):
        return False
    return _hg_ring_dist(px, py, ring) > eps


def _hg_rep_inside(pts):
    n = len(pts)
    cx = sum(p[0] for p in pts) / n
    cy = sum(p[1] for p in pts) / n
    if _hg_ring_contains(cx, cy, pts):
        return (cx, cy)
    best_i, best_l = 0, -1.0
    for i in range(n):
        ax, ay = pts[i]
        bx, by = pts[(i + 1) % n]
        L = math.hypot(bx - ax, by - ay)
        if L > best_l:
            best_l = L
            best_i = i
    ax, ay = pts[best_i]
    bx, by = pts[(best_i + 1) % n]
    L = math.hypot(bx - ax, by - ay) or 1.0
    mx, my = 0.5 * (ax + bx), 0.5 * (ay + by)
    area = _hg_area(pts)
    if area >= 0.0:
        nx, ny = -(by - ay) / L, (bx - ax) / L
    else:
        nx, ny = (by - ay) / L, -(bx - ax) / L
    for eps in (0.01, 0.1, 0.5, 1.0, 2.0):
        qx, qy = mx + nx * eps, my + ny * eps
        if _hg_ring_contains(qx, qy, pts):
            return (qx, qy)
    return (cx, cy)


def _hg_shells_from_loops(loops):
    """Closed outer-wall loops. Even nesting depth is material, odd depth is a hole."""
    raw = []
    for pts, closed, _ww in loops:
        if not closed or len(pts) < 3:
            continue
        pts = _hg_dedup(pts)
        if len(pts) >= 3 and abs(_hg_area(pts)) > 1e-6:
            raw.append(pts)
    if not raw:
        return []
    raw.sort(key=lambda p: abs(_hg_area(p)), reverse=True)
    reps = [_hg_rep_inside(p) for p in raw]
    parent = [None] * len(raw)
    children = [[] for _ in raw]
    areas = [abs(_hg_area(p)) for p in raw]
    for i, pts in enumerate(raw):
        rx, ry = reps[i]
        best = None
        best_a = None
        for j, q in enumerate(raw):
            if i == j or areas[j] <= areas[i] + 1e-9:
                continue
            if _hg_ring_contains(rx, ry, q):
                if best is None or areas[j] < best_a:
                    best = j
                    best_a = areas[j]
        if best is not None:
            parent[i] = best
            children[best].append(i)
    shells = []
    for i, pts in enumerate(raw):
        depth = 0
        p = parent[i]
        seen = 0
        while p is not None and seen <= len(raw):
            depth += 1
            p = parent[p]
            seen += 1
        if depth % 2 == 1:
            continue
        shells.append({
            "outer": pts,
            "holes": [raw[c] for c in children[i]],
            "bbox": _hg_bbox(pts),
        })
    return shells


def _hg_point_in_shells(px, py, shells):
    for sh in shells:
        minx, miny, maxx, maxy = sh["bbox"]
        if px < minx - _HEEL_BOUND_EPS or px > maxx + _HEEL_BOUND_EPS or py < miny - _HEEL_BOUND_EPS or py > maxy + _HEEL_BOUND_EPS:
            continue
        if not _hg_ring_contains(px, py, sh["outer"]):
            continue
        if any(_hg_ring_strict_inside(px, py, h) for h in sh["holes"]):
            continue
        return True
    return False


def _hg_build_edge_index(shells, cell=4.0):
    edges = []
    grid = {}
    minx = miny = 1e18
    maxx = maxy = -1e18

    def add_ring(ring):
        nonlocal minx, miny, maxx, maxy
        n = len(ring)
        for i in range(n):
            ax, ay = ring[i]
            bx, by = ring[(i + 1) % n]
            if math.hypot(bx - ax, by - ay) < 1e-12:
                continue
            ei = len(edges)
            edges.append((ax, ay, bx, by))
            minx = min(minx, ax, bx)
            miny = min(miny, ay, by)
            maxx = max(maxx, ax, bx)
            maxy = max(maxy, ay, by)
            ix0 = int(math.floor(min(ax, bx) / cell))
            ix1 = int(math.floor(max(ax, bx) / cell))
            iy0 = int(math.floor(min(ay, by) / cell))
            iy1 = int(math.floor(max(ay, by) / cell))
            for ix in range(ix0, ix1 + 1):
                for iy in range(iy0, iy1 + 1):
                    grid.setdefault((ix, iy), []).append(ei)

    for sh in shells:
        add_ring(sh["outer"])
        for hole in sh["holes"]:
            add_ring(hole)
    if not edges:
        return {"edges": [], "grid": {}, "cell": cell, "bbox": (0.0, 0.0, 0.0, 0.0)}
    return {"edges": edges, "grid": grid, "cell": cell, "bbox": (minx, miny, maxx, maxy)}


def _hg_nearest_boundary(px, py, index):
    """Distance to the part boundary and the closest boundary point."""
    edges = index["edges"]
    if not edges:
        return 1e18, px, py
    cell = index["cell"]
    grid = index["grid"]
    ix = int(math.floor(px / cell))
    iy = int(math.floor(py / cell))
    minx, miny, maxx, maxy = index["bbox"]
    span = max(maxx - minx, maxy - miny, cell)
    max_rad = int(span / cell) + 3
    best = 1e18
    bqx, bqy = px, py
    rad = 0
    while rad <= max_rad:
        for dx in range(-rad, rad + 1):
            for dy in range(-rad, rad + 1):
                if rad != 0 and max(abs(dx), abs(dy)) != rad:
                    continue
                bucket = grid.get((ix + dx, iy + dy))
                if not bucket:
                    continue
                for ei in bucket:
                    ax, ay, bx, by = edges[ei]
                    qx, qy, d = _hg_closest_on_seg(px, py, ax, ay, bx, by)
                    if d < best:
                        best = d
                        bqx, bqy = qx, qy
        if best <= rad * cell:
            return best, bqx, bqy
        rad += 1
    return best, bqx, bqy


def _hg_dist_point_index(px, py, index):
    d, _qx, _qy = _hg_nearest_boundary(px, py, index)
    return d


def _hg_deeper_exists(px, py, geom):
    """True when a point 0.5 mm from the boundary is available inward of here.

    A thin neck has no such point, so the deepest inside path may sit in the band.
    A thick wall does, so a leg riding that wall is rejected.
    """
    d, qx, qy = _hg_nearest_boundary(px, py, geom["index"])
    if d >= _HEEL_ACCEPT_INSET - 1e-3:
        return False
    vx, vy = px - qx, py - qy
    L = math.hypot(vx, vy)
    if L < 1e-6:
        for k in range(8):
            ang = k * math.pi / 4.0
            sx = px + math.cos(ang) * _HEEL_ACCEPT_INSET
            sy = py + math.sin(ang) * _HEEL_ACCEPT_INSET
            if _hg_point_class(sx, sy, geom) == "deep":
                return True
        return False
    vx /= L
    vy /= L
    sx = qx + vx * _HEEL_ACCEPT_INSET
    sy = qy + vy * _HEEL_ACCEPT_INSET
    return _hg_point_class(sx, sy, geom) == "deep"


def _hg_segments_intersect(ax, ay, bx, by, cx, cy, dx, dy):
    def cross(x1, y1, x2, y2):
        return x1 * y2 - y1 * x2

    def orient(px, py, qx, qy, rx, ry):
        return cross(qx - px, qy - py, rx - px, ry - py)

    o1 = orient(ax, ay, bx, by, cx, cy)
    o2 = orient(ax, ay, bx, by, dx, dy)
    o3 = orient(cx, cy, dx, dy, ax, ay)
    o4 = orient(cx, cy, dx, dy, bx, by)
    if o1 * o2 < 0.0 and o3 * o4 < 0.0:
        return True

    def on(px, py, qx, qy, rx, ry):
        return (min(px, rx) - 1e-9 <= qx <= max(px, rx) + 1e-9 and
                min(py, ry) - 1e-9 <= qy <= max(py, ry) + 1e-9)

    if abs(o1) <= 1e-12 and on(ax, ay, cx, cy, bx, by):
        return True
    if abs(o2) <= 1e-12 and on(ax, ay, dx, dy, bx, by):
        return True
    if abs(o3) <= 1e-12 and on(cx, cy, ax, ay, dx, dy):
        return True
    if abs(o4) <= 1e-12 and on(cx, cy, bx, by, dx, dy):
        return True
    return False


def _hg_dist_seg_seg(ax, ay, bx, by, cx, cy, dx, dy):
    if _hg_segments_intersect(ax, ay, bx, by, cx, cy, dx, dy):
        return 0.0
    return min(
        _hg_dist_pt_seg(ax, ay, cx, cy, dx, dy),
        _hg_dist_pt_seg(bx, by, cx, cy, dx, dy),
        _hg_dist_pt_seg(cx, cy, ax, ay, bx, by),
        _hg_dist_pt_seg(dx, dy, ax, ay, bx, by),
    )


def _hg_dist_segment_index(x0, y0, x1, y1, index):
    edges = index["edges"]
    if not edges:
        return 1e18
    best = min(_hg_dist_point_index(x0, y0, index), _hg_dist_point_index(x1, y1, index))
    cell = index["cell"]
    minx = min(x0, x1) - best
    maxx = max(x0, x1) + best
    miny = min(y0, y1) - best
    maxy = max(y0, y1) + best
    ix0 = int(math.floor(minx / cell))
    ix1 = int(math.floor(maxx / cell))
    iy0 = int(math.floor(miny / cell))
    iy1 = int(math.floor(maxy / cell))
    seen = set()
    grid = index["grid"]
    for ix in range(ix0, ix1 + 1):
        for iy in range(iy0, iy1 + 1):
            bucket = grid.get((ix, iy))
            if not bucket:
                continue
            for ei in bucket:
                if ei in seen:
                    continue
                seen.add(ei)
                ax, ay, bx, by = edges[ei]
                d = _hg_dist_seg_seg(x0, y0, x1, y1, ax, ay, bx, by)
                if d < best:
                    best = d
    return best


def _hg_point_class(px, py, geom):
    """'out' leaves the part, 'band' is inside but within 0.5 mm, 'deep' is inset."""
    shells = geom["shells"]
    if not _hg_point_in_shells(px, py, shells):
        d = _hg_dist_point_index(px, py, geom["index"])
        if d <= _HEEL_BOUND_EPS:
            return "band"
        return "out"
    d = _hg_dist_point_index(px, py, geom["index"])
    if d + 1e-3 < _HEEL_ACCEPT_INSET:
        return "band"
    return "deep"


def _hg_leg_measure(x0, y0, x1, y1, geom):
    """Lengths of this leg that leave the part, and that sit in the outer 0.5 mm band."""
    L = math.hypot(x1 - x0, y1 - y0)
    if L < 1e-9:
        kind = _hg_point_class(x0, y0, geom)
        if kind == "out":
            return (1.0, 0.0)
        if kind == "band":
            return (0.0, 1.0)
        return (0.0, 0.0)
    n = max(1, int(math.ceil(L / _HEEL_SAMPLE_STEP)))
    step = L / n
    kinds = []
    for i in range(n + 1):
        t = i / float(n)
        kinds.append(_hg_point_class(x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, geom))
    outside = 0.0
    band = 0.0
    for i in range(n):
        a, b = kinds[i], kinds[i + 1]
        if a == "out" or b == "out":
            outside += step
        elif a == "band" or b == "band":
            band += step
    return outside, band


def _hg_span_ok(x0, y0, x1, y1, geom, role):
    """Entry and the final seam leg may use the 0.5 mm band for about 1.5 mm.

    A middle leg may sit in that band only where the part is thinner than 1 mm,
    so the walk cannot move any farther inward. It still may not leave the part.
    """
    L = math.hypot(x1 - x0, y1 - y0)
    if L < 1e-9:
        kind = _hg_point_class(x0, y0, geom)
        if kind == "out":
            return False
        if role == "mid" and kind == "band" and _hg_deeper_exists(x0, y0, geom):
            return False
        return True
    outside, band = _hg_leg_measure(x0, y0, x1, y1, geom)
    if outside > 1e-3:
        return False
    if role != "mid":
        return band <= _HEEL_APPROACH_MAX + 0.05
    if band <= 1e-3:
        return True
    n = max(1, int(math.ceil(L / _HEEL_SAMPLE_STEP)))
    for i in range(n + 1):
        t = i / float(n)
        sx = x0 + (x1 - x0) * t
        sy = y0 + (y1 - y0) * t
        if _hg_point_class(sx, sy, geom) == "band" and _hg_deeper_exists(sx, sy, geom):
            return False
    return True


def _hg_route_accept(x0, y0, pts, geom):
    """Every leg stays inside. Only the first hop and the final seam leg may use the band."""
    if not pts:
        return False
    prev = (x0, y0)
    last = len(pts) - 1
    for i, p in enumerate(pts):
        if i == last:
            role = "exit"
        elif i == 0:
            role = "entry"
        else:
            role = "mid"
        if not _hg_span_ok(prev[0], prev[1], p[0], p[1], geom, role):
            return False
        prev = p
    return True


def _hg_edge_normals(pts, closed, left):
    n = len(pts)
    last = n if closed else n - 1
    norms = []
    for i in range(last):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        dx, dy = x2 - x1, y2 - y1
        L = math.hypot(dx, dy)
        if L < 1e-12:
            norms.append((0.0, 0.0))
        elif left:
            norms.append((-dy / L, dx / L))
        else:
            norms.append((dy / L, -dx / L))
    return norms


def _hg_miter_point(vx, vy, n0, n1, dist):
    if n0 == (0.0, 0.0) and n1 == (0.0, 0.0):
        return (vx, vy)
    if n0 == (0.0, 0.0):
        return (vx + n1[0] * dist, vy + n1[1] * dist)
    if n1 == (0.0, 0.0):
        return (vx + n0[0] * dist, vy + n0[1] * dist)
    bx, by = n0[0] + n1[0], n0[1] + n1[1]
    bl = math.hypot(bx, by)
    if bl < 1e-8:
        return (vx + n1[0] * dist, vy + n1[1] * dist)
    bx /= bl
    by /= bl
    denom = bx * n1[0] + by * n1[1]
    # Keep a sharp corner's inset on the 0.5 mm parallel. Clamping this back
    # toward the vertex drops the walk into the outer band on a thin horn.
    if denom < 0.1:
        denom = 0.1
    mlen = dist / denom
    if mlen > dist * 8.0:
        mlen = dist * 8.0
    return (vx + bx * mlen, vy + by * mlen)


def _hg_offset_closed(pts, dist, left):
    norms = _hg_edge_normals(pts, True, left)
    n = len(pts)
    out = []
    for i in range(n):
        out.append(_hg_miter_point(pts[i][0], pts[i][1], norms[(i - 1) % n], norms[i], dist))
    return _hg_dedup(out)


def _hg_offset_open(pts, dist, left):
    norms = _hg_edge_normals(pts, False, left)
    if not norms:
        return list(pts)
    out = [_hg_miter_point(pts[0][0], pts[0][1], norms[0], norms[0], dist)]
    for i in range(1, len(pts) - 1):
        out.append(_hg_miter_point(pts[i][0], pts[i][1], norms[i - 1], norms[i], dist))
    out.append(_hg_miter_point(pts[-1][0], pts[-1][1], norms[-1], norms[-1], dist))
    return _hg_dedup(out)


def _hg_deep_chains(pts, geom, closed):
    """Drop offset edges that leave the deep region so a chord cannot bridge a gap."""
    pts = _hg_dedup(pts)
    n = len(pts)
    if n < 2:
        return []
    limit = n if closed else n - 1
    good = []
    for i in range(limit):
        ax, ay = pts[i]
        bx, by = pts[(i + 1) % n]
        ok = math.hypot(bx - ax, by - ay) >= 1e-6 and _hg_span_ok(ax, ay, bx, by, geom, "mid")
        good.append(ok)
    if not any(good):
        return []
    if closed and all(good):
        return [(pts, True)]

    chains = []

    def emit(run):
        run = _hg_dedup(run)
        if len(run) >= 2:
            chains.append((run, False))

    if not closed:
        run = []
        for i, ok in enumerate(good):
            if ok:
                if not run:
                    run = [pts[i]]
                run.append(pts[i + 1])
            elif run:
                emit(run)
                run = []
        if run:
            emit(run)
        return chains
    start = good.index(False)
    run = []
    for k in range(limit):
        i = (start + 1 + k) % limit
        if good[i]:
            if not run:
                run = [pts[i]]
            run.append(pts[(i + 1) % n])
        elif run:
            emit(run)
            run = []
    if run:
        emit(run)
    return chains


def _hg_pull_shallow(pts, geom, clearance, closed):
    """Move inset vertices that fell inside the 0.5 mm band back out to that clearance.

    A plain miter on a tight curve undershoots. Pulling those vertices keeps the
    walk off the wall without bridging a gap.
    """

    def pull(px, py):
        if _hg_point_class(px, py, geom) == "out":
            return (px, py)
        d = _hg_dist_point_index(px, py, geom["index"])
        if d + 1e-3 >= clearance:
            return (px, py)
        _bd, qx, qy = _hg_nearest_boundary(px, py, geom["index"])
        vx, vy = px - qx, py - qy
        L = math.hypot(vx, vy)
        if L < 1e-6:
            return (px, py)
        sx = qx + vx / L * clearance
        sy = qy + vy / L * clearance
        if _hg_point_class(sx, sy, geom) == "out":
            return (px, py)
        return (sx, sy)

    cur = _hg_dedup([pull(px, py) for px, py in pts])
    n = len(cur)
    if n < 2:
        return cur
    limit = n if closed else n - 1
    out = []
    for i in range(limit):
        a = cur[i]
        b = cur[(i + 1) % n]
        out.append(a)
        el = math.hypot(b[0] - a[0], b[1] - a[1])
        if el > 1.0:
            mx, my = 0.5 * (a[0] + b[0]), 0.5 * (a[1] + b[1])
            if _hg_point_class(mx, my, geom) == "band":
                out.append(pull(mx, my))
    if not closed:
        out.append(cur[-1])
    return _hg_dedup(out)


def _hg_shell_walk_rings(shell, dist, geom):
    rings = []
    outer = shell["outer"]
    if len(outer) >= 3 and dist > 1e-6:
        off = _hg_offset_closed(outer, dist, left=(_hg_area(outer) >= 0.0))
        off = _hg_pull_shallow(off, geom, _HEEL_ACCEPT_INSET, True)
        rings.extend(_hg_deep_chains(off, geom, True))
    for hole in shell["holes"]:
        if len(hole) < 3:
            continue
        # Expand the hole into the material (opposite of shrinking the hole polygon).
        off = _hg_offset_closed(hole, dist, left=(_hg_area(hole) < 0.0))
        off = _hg_pull_shallow(off, geom, _HEEL_ACCEPT_INSET, True)
        rings.extend(_hg_deep_chains(off, geom, True))
    return rings


def _hg_make_walk_rings(shells, geom, walk_d):
    """Inset about one inner line width. Fall back toward 0.5 mm only if that inset vanishes."""
    if not shells:
        return [], []
    primary = []
    for sh in shells:
        primary.extend(_hg_shell_walk_rings(sh, walk_d, geom))
    tight = []
    if walk_d > _HEEL_ACCEPT_INSET + 1e-6:
        for sh in shells:
            tight.extend(_hg_shell_walk_rings(sh, _HEEL_ACCEPT_INSET, geom))
    return primary, tight


def _hg_mode_width(widths, default):
    if not widths:
        return default
    bins = {}
    for w in widths:
        k = round(float(w), 3)
        bins[k] = bins.get(k, 0) + 1
    return max(bins.items(), key=lambda kv: kv[1])[0]


def collapse_seam_travels(text, TF, seam_map=None, heel_max_route=150.0):
    """Replace every seam travel by lift + move + lower.

    A belt-side seam travel is a heel crossing when the straight segment leaves
    the layer's outer-wall polygon or comes within 0.7 mm of its boundary.
    Qualifying travels are rerouted along an inward inset of the inner-wall loop
    when every leg, sampled at 0.5 mm, stays inside that polygon and at least
    0.5 mm from its boundary. Otherwise the route walks an inset of about one
    inner line width. The opening hop and the final leg to the seam may enter
    the outer 0.5 mm band. A sharp horn may need about 2.5 mm of that band;
    a thick wall only needs about 1 mm. A straight leg that leaves
    the part or bridges a gap is split along the loop or rejected.
    ``heel_max_route`` (default 150 mm) is the preferred maximum length; a longer
    route is still used. The tagged straight move is emitted only when the
    layer has no closed outer loop. Separate shells are not joined by a chord;
    that travel stays an ordinary seam move.
    """
    import sys

    HEEL_E = 1.5
    HEEL_EF = 1800.0
    HEEL_OUTER_NEAR = 0.7
    HEEL_DEF_INNER_W = 0.45
    HEEL_JOIN_TOL = 0.05
    _seam = seam_map or {}
    if heel_max_route is None:
        heel_max_route = 150.0

    def _dist_pt_seg(px, py, ax, ay, bx, by):
        abx, aby = bx - ax, by - ay
        ab2 = abx * abx + aby * aby
        if ab2 < 1e-18:
            return math.hypot(px - ax, py - ay)
        t = max(0.0, min(1.0, ((px - ax) * abx + (py - ay) * aby) / ab2))
        qx, qy = ax + t * abx, ay + t * aby
        return math.hypot(px - qx, py - qy)

    def _dedup(pts, tol=1e-4):
        out = []
        for p in pts:
            if not out or math.hypot(p[0] - out[-1][0], p[1] - out[-1][1]) >= tol:
                out.append((float(p[0]), float(p[1])))
        return out

    def _signed_area(pts):
        a = 0.0
        n = len(pts)
        for i in range(n):
            x1, y1 = pts[i]
            x2, y2 = pts[(i + 1) % n]
            a += x1 * y2 - x2 * y1
        return 0.5 * a

    def _point_in_poly(px, py, poly):
        n = len(poly)
        inside = False
        j = n - 1
        for i in range(n):
            xi, yi = poly[i]
            xj, yj = poly[j]
            if (yi > py) != (yj > py):
                denom = yj - yi
                if abs(denom) < 1e-18:
                    denom = 1e-18 if denom >= 0 else -1e-18
                if px <= (xj - xi) * (py - yi) / denom + xi:
                    inside = not inside
            j = i
        return inside

    def _chain_polylines(segs, tol):
        """Join wall segments that share an endpoint. Never bridge a gap."""
        items = []
        for a, b, w in segs:
            if math.hypot(a[0] - b[0], a[1] - b[1]) >= 1e-6:
                items.append((a, b, w))
        unused = set(range(len(items)))
        loops = []
        while unused:
            j0 = min(unused)
            unused.remove(j0)
            a, b, w = items[j0]
            pts = [a, b]
            ws = [w]
            while unused:
                if len(pts) >= 4 and math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1]) < tol:
                    break
                tail = pts[-1]
                head = pts[0]
                found = None
                for j in list(unused):
                    sa, sb, sw = items[j]
                    if math.hypot(sa[0] - tail[0], sa[1] - tail[1]) < tol:
                        pts.append(sb)
                        found = (j, sw)
                        break
                    if math.hypot(sb[0] - tail[0], sb[1] - tail[1]) < tol:
                        pts.append(sa)
                        found = (j, sw)
                        break
                    if math.hypot(sa[0] - head[0], sa[1] - head[1]) < tol:
                        pts.insert(0, sb)
                        found = (j, sw)
                        break
                    if math.hypot(sb[0] - head[0], sb[1] - head[1]) < tol:
                        pts.insert(0, sa)
                        found = (j, sw)
                        break
                if found is None:
                    break
                unused.remove(found[0])
                ws.append(found[1])
            closed = len(pts) >= 4 and math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1]) < tol
            if closed:
                pts = pts[:-1]
            pts = _dedup(pts)
            if len(pts) >= 2:
                loops.append((pts, bool(closed and len(pts) >= 3), sum(ws) / float(len(ws))))
        return loops

    def _edge_normals(pts, closed, left):
        n = len(pts)
        last = n if closed else n - 1
        norms = []
        for i in range(last):
            x1, y1 = pts[i]
            x2, y2 = pts[(i + 1) % n]
            dx, dy = x2 - x1, y2 - y1
            L = math.hypot(dx, dy)
            if L < 1e-12:
                norms.append((0.0, 0.0))
            elif left:
                norms.append((-dy / L, dx / L))
            else:
                norms.append((dy / L, -dx / L))
        return norms

    def _miter_point(vx, vy, n0, n1, dist):
        if n0 == (0.0, 0.0) and n1 == (0.0, 0.0):
            return (vx, vy)
        if n0 == (0.0, 0.0):
            return (vx + n1[0] * dist, vy + n1[1] * dist)
        if n1 == (0.0, 0.0):
            return (vx + n0[0] * dist, vy + n0[1] * dist)
        bx, by = n0[0] + n1[0], n0[1] + n1[1]
        bl = math.hypot(bx, by)
        if bl < 1e-8:
            return (vx + n1[0] * dist, vy + n1[1] * dist)
        bx /= bl
        by /= bl
        denom = bx * n1[0] + by * n1[1]
        if denom < 0.25:
            denom = 0.25
        mlen = dist / denom
        if mlen > dist * 3.0:
            mlen = dist * 3.0
        return (vx + bx * mlen, vy + by * mlen)

    def _offset_closed(pts, dist, left):
        norms = _edge_normals(pts, True, left)
        n = len(pts)
        out = []
        for i in range(n):
            out.append(_miter_point(pts[i][0], pts[i][1], norms[(i - 1) % n], norms[i], dist))
        return _dedup(out)

    def _offset_open(pts, dist, left):
        norms = _edge_normals(pts, False, left)
        if not norms:
            return list(pts)
        out = [_miter_point(pts[0][0], pts[0][1], norms[0], norms[0], dist)]
        for i in range(1, len(pts) - 1):
            out.append(_miter_point(pts[i][0], pts[i][1], norms[i - 1], norms[i], dist))
        out.append(_miter_point(pts[-1][0], pts[-1][1], norms[-1], norms[-1], dist))
        return _dedup(out)

    def _nearest_on_poly(poly, px, py, closed):
        n = len(poly)
        if n == 0:
            return None
        limit = n if closed else n - 1
        if limit < 1:
            return (math.hypot(px - poly[0][0], py - poly[0][1]), 0, 0.0, poly[0][0], poly[0][1])
        best = None
        for i in range(limit):
            ax, ay = poly[i]
            bx, by = poly[(i + 1) % n]
            abx, aby = bx - ax, by - ay
            ab2 = abx * abx + aby * aby
            if ab2 < 1e-18:
                t = 0.0
                qx, qy = ax, ay
            else:
                t = max(0.0, min(1.0, ((px - ax) * abx + (py - ay) * aby) / ab2))
                qx, qy = ax + t * abx, ay + t * aby
            d = math.hypot(px - qx, py - qy)
            if best is None or d < best[0]:
                best = (d, i, t, qx, qy)
        return best

    def _clamp_pts(off, src, closed, dist):
        out = []
        for px, py in off:
            hit = _nearest_on_poly(src, px, py, closed)
            if hit is None:
                out.append((px, py))
                continue
            d, _i, _t, qx, qy = hit
            if d <= dist + 1e-5 or d < 1e-12:
                out.append((px, py))
            else:
                s = dist / d
                out.append((qx + (px - qx) * s, qy + (py - qy) * s))
        return _dedup(out)

    def _mean_clearance(pts, outer_segs):
        if not pts:
            return -1e18
        if not outer_segs:
            return 0.0
        total = 0.0
        for px, py in pts:
            best = 1e18
            for ax, ay, bx, by, *_rest in outer_segs:
                # Skip segments whose bbox cannot beat the current best.
                if px < min(ax, bx) - best or px > max(ax, bx) + best or py < min(ay, by) - best or py > max(ay, by) + best:
                    continue
                best = min(best, _dist_pt_seg(px, py, ax, ay, bx, by))
            total += best
        return total / float(len(pts))

    def _inset_loop(pts, half, closed, outer_poly, outer_segs):
        if half <= 1e-9 or len(pts) < 2:
            return list(pts)
        if closed and len(pts) >= 3:
            area = _signed_area(pts)
            off = _clamp_pts(_offset_closed(pts, half, left=(area >= 0.0)), pts, True, half)
            inside = sum(1 for p in off if _point_in_poly(p[0], p[1], pts))
            if inside * 2 < len(off):
                off = _clamp_pts(_offset_closed(pts, half, left=(area < 0.0)), pts, True, half)
            return off
        left = _clamp_pts(_offset_open(pts, half, True), pts, False, half)
        right = _clamp_pts(_offset_open(pts, half, False), pts, False, half)

        def _score(poly):
            inside = 0
            if outer_poly and len(outer_poly) >= 3:
                inside = sum(1 for p in poly if _point_in_poly(p[0], p[1], outer_poly))
            return (inside, _mean_clearance(poly, outer_segs))

        return left if _score(left) >= _score(right) else right

    def _qualify_heelroute(lyr, x0, y0, x1, y1, lctx):
        """Leave the outer-wall polygon, or come within 0.7 mm of its boundary.

        This does not use a bead radius. No closed outer loop means the travel
        is not a heel crossing.
        """
        if not lctx:
            return False
        shells = lctx.get("shells") or []
        if not shells:
            return False
        if math.hypot(x1 - x0, y1 - y0) < 1e-9:
            return False
        minx, miny, maxx, maxy = lctx.get("bbox", (0.0, 0.0, 0.0, 0.0))
        sx0, sx1 = (x0, x1) if x0 <= x1 else (x1, x0)
        sy0, sy1 = (y0, y1) if y0 <= y1 else (y1, y0)
        if sx1 < minx or sx0 > maxx or sy1 < miny or sy0 > maxy:
            return True
        geom = lctx.get("geom")
        if geom is None:
            return False
        if _hg_point_class(x0, y0, geom) == "out" or _hg_point_class(x1, y1, geom) == "out":
            return True
        # A segment that crosses out and back has distance 0 to the boundary.
        return _hg_dist_segment_index(x0, y0, x1, y1, geom["index"]) <= HEEL_OUTER_NEAR + 1e-9

    def _paths_between(poly, closed, si, ts, ei, te, qe):
        n = len(poly)

        def add_seq(idxs):
            pts = []
            for ix in idxs:
                p = poly[ix]
                if not pts or math.hypot(p[0] - pts[-1][0], p[1] - pts[-1][1]) > 1e-4:
                    pts.append(p)
            if qe is not None and (not pts or math.hypot(qe[0] - pts[-1][0], qe[1] - pts[-1][1]) > 1e-4):
                pts.append(qe)
            return pts

        if n < 2:
            return [add_seq(())]
        if not closed:
            if si < ei or (si == ei and ts <= te):
                return [add_seq(range(si + 1, ei + 1))]
            return [add_seq(range(si, ei, -1))]
        if si == ei:
            around = []
            k = (si + 1) % n
            for _ in range(n):
                around.append(k)
                if k == si:
                    break
                k = (k + 1) % n
            return [add_seq(()), add_seq(around)]
        fwd = []
        k = (si + 1) % n
        for _ in range(n):
            fwd.append(k)
            if k == ei:
                break
            k = (k + 1) % n
        back = []
        k = si
        end = (ei + 1) % n
        for _ in range(n):
            back.append(k)
            if k == end:
                break
            k = (k - 1) % n
        return [add_seq(fwd), add_seq(back)]

    def _assemble(x0, y0, tx, ty, qs, path):
        raw = [qs] + list(path)
        pts = []
        prev = (x0, y0)
        for p in raw:
            if math.hypot(p[0] - prev[0], p[1] - prev[1]) > 1e-4:
                pts.append((p[0], p[1]))
                prev = p
        if math.hypot(prev[0] - tx, prev[1] - ty) > 1e-4:
            pts.append((tx, ty))
        elif pts:
            pts[-1] = (tx, ty)
        else:
            pts = [(tx, ty)]
        return pts

    def _chain_len(x0, y0, pts):
        L = 0.0
        px, py = x0, y0
        for qx, qy in pts:
            L += math.hypot(qx - px, qy - py)
            px, py = qx, qy
        return L

    def _attach_point(px, py, poly, closed, geom, kind):
        """Closest point on poly whose straight connector is allowed.

        A nearer point whose connector leaves the part or bridges a gap is skipped.
        """
        n = len(poly)
        limit = n if closed else n - 1
        if n == 0 or limit < 1:
            return None

        def ok(qx, qy):
            if kind == "entry":
                return _hg_span_ok(px, py, qx, qy, geom, "entry")
            return _hg_span_ok(qx, qy, px, py, geom, "exit")

        hit = _nearest_on_poly(poly, px, py, closed)
        if hit is not None and ok(hit[3], hit[4]):
            return hit
        best = None
        order = []
        for i in range(limit):
            ax, ay = poly[i]
            bx, by = poly[(i + 1) % n]
            order.append((_dist_pt_seg(px, py, ax, ay, bx, by), i, ax, ay, bx, by))
        order.sort(key=lambda item: item[0])
        for dmin, i, ax, ay, bx, by in order:
            if best is not None and dmin >= best[0] - 1e-9:
                break
            L = math.hypot(bx - ax, by - ay)
            steps = max(1, int(math.ceil(L / _HEEL_SAMPLE_STEP)))
            for s in range(steps + 1):
                t = s / float(steps)
                qx = ax + (bx - ax) * t
                qy = ay + (by - ay) * t
                d = math.hypot(qx - px, qy - py)
                if best is not None and d >= best[0]:
                    continue
                if ok(qx, qy):
                    best = (d, i, t, qx, qy)
        return best

    def _best_on_poly(x0, y0, tx, ty, poly, closed, geom):
        if geom is None or len(poly) < 2:
            return None
        ds = _attach_point(x0, y0, poly, closed, geom, "entry")
        de = _attach_point(tx, ty, poly, closed, geom, "exit")
        if ds is None or de is None:
            return None
        _d0, si, ts, qsx, qsy = ds
        _d1, ei, te, qex, qey = de
        qs = (qsx, qsy)
        qe = (qex, qey)
        valid = []
        for path in _paths_between(poly, closed, si, ts, ei, te, qe):
            pts = _assemble(x0, y0, tx, ty, qs, path)
            if not _hg_route_accept(x0, y0, pts, geom):
                continue
            valid.append((_chain_len(x0, y0, pts), pts))
        if not valid:
            return None
        valid.sort(key=lambda item: item[0])
        # Prefer a route under the cap. If every valid route is longer,
        # keep the shortest anyway — never substitute the straight crossing.
        chosen = valid[0]
        for item in valid:
            if item[0] <= heel_max_route:
                chosen = item
                break
        return chosen

    def _route_waypoints(x0, y0, tx, ty, lctx):
        geom = None if not lctx else lctx.get("geom")
        shells = [] if not lctx else (lctx.get("shells") or [])
        if not shells or geom is None:
            return None, "no_closed_outer_loop", 0.0
        straight = math.hypot(tx - x0, ty - y0)
        # A short hop that already stays inside is the final approach. A long
        # chord, or one that leaves the part, is not used.
        if straight <= _HEEL_APPROACH_MAX + 0.05 and _hg_route_accept(x0, y0, [(tx, ty)], geom):
            return [(tx, ty)], None, straight
        loops = lctx.get("inner_loops") or []
        if loops:
            scored = []
            for poly, closed in loops:
                if len(poly) < 2:
                    continue
                ds = _nearest_on_poly(poly, x0, y0, closed)
                de = _nearest_on_poly(poly, tx, ty, closed)
                if ds is None or de is None:
                    continue
                scored.append((ds[0] + de[0], poly, closed))
            scored.sort(key=lambda item: item[0])
            for _score, poly, closed in scored:
                hit = _best_on_poly(x0, y0, tx, ty, poly, closed, geom)
                if hit is not None:
                    return hit[1], None, hit[0]

        def _pick(rings):
            best = None
            for poly, closed in rings:
                hit = _best_on_poly(x0, y0, tx, ty, poly, closed, geom)
                if hit is not None and (best is None or hit[0] < best[0]):
                    best = hit
            return best

        chosen = _pick(lctx.get("walk_rings") or [])
        if chosen is None:
            chosen = _pick(lctx.get("walk_rings_tight") or [])
        if chosen is not None:
            return chosen[1], None, chosen[0]
        # Closed outer exists, but every straight connector leaves the part.
        # Do not fall back to a chord across the opening.
        return None, "no_valid_route", 0.0

    def _build_layer_ctx(all_lines):
        layer = -1
        role = ""
        w = HEEL_DEF_INNER_W
        ctx = {}
        x = y = 0.0
        for l in all_lines:
            if l.startswith(";LAYER_CHANGE"):
                layer += 1
            elif l.startswith(";TYPE:"):
                role = l[6:].strip()
            elif l.startswith(";WIDTH:"):
                try:
                    w = float(l.split(":", 1)[1].strip())
                except ValueError:
                    pass
            if _is_move(l):
                nx, ny = _w(l, "X"), _w(l, "Y")
                nx = x if nx is None else nx
                ny = y if ny is None else ny
                if _is_ext(l) and role in ("Outer wall", "Overhang wall", "Inner wall"):
                    seg = (x, y, nx, ny, w)
                    c = ctx.setdefault(layer, {"outer_segs": [], "inner_segs": [], "inner_width": w})
                    if role in ("Outer wall", "Overhang wall"):
                        c["outer_segs"].append(seg)
                    else:
                        c["inner_segs"].append(((x, y), (nx, ny), w))
                        c["inner_width"] = w
                x, y = nx, ny
        for lyr, c in ctx.items():
            outer_triples = [((s[0], s[1]), (s[2], s[3]), s[4]) for s in c["outer_segs"]]
            outer_loops = _chain_polylines(outer_triples, HEEL_JOIN_TOL)
            outer_poly = None
            best_area = -1.0
            for pts, closed, _ww in outer_loops:
                if closed and len(pts) >= 3:
                    area = abs(_signed_area(pts))
                    if area > best_area:
                        best_area = area
                        outer_poly = pts
            inset_loops = []
            for pts, closed, ww in _chain_polylines(c["inner_segs"], HEEL_JOIN_TOL):
                half = 0.5 * (ww if ww > 1e-6 else c.get("inner_width", HEEL_DEF_INNER_W))
                op = _inset_loop(pts, half, closed, outer_poly, c["outer_segs"])
                op = _dedup(op)
                if len(op) >= 2:
                    inset_loops.append((op, bool(closed and len(op) >= 3)))
            c["inner_loops"] = inset_loops
            widths = [seg[2] for seg in c["inner_segs"]]
            c["inner_width"] = _hg_mode_width(widths, HEEL_DEF_INNER_W)
            shells = _hg_shells_from_loops(outer_loops)
            index = _hg_build_edge_index(shells)
            geom = {"shells": shells, "index": index}
            c["shells"] = shells
            c["geom"] = geom
            c["bbox"] = index["bbox"]
            walk_d = max(c["inner_width"], _HEEL_ACCEPT_INSET)
            primary, tight = _hg_make_walk_rings(shells, geom, walk_d)
            c["walk_rings"] = primary
            c["walk_rings_tight"] = tight
        return ctx

    lines = text.splitlines(keepends=True)
    layer_ctx = _build_layer_ctx(lines)
    out = []
    x = y = 0.0; z = None; f = None
    first_lc = next((k for k, l in enumerate(lines) if l.startswith(";LAYER_CHANGE")), len(lines))
    region = []      # (line_index_in_lines, line)
    seam_from = False
    stats = {"seam_travels": 0, "retracts": 0, "nonbelt_plain": 0, "heelroute_routed": 0, "heelroute_fallback": 0, "heelroute_rejected": 0}
    layer = -1; pos_layer = -1
    ymin_of = lambda k: _seam.get(k, (0, 0.0))[1]

    def track(l):
        nonlocal x, y, z, f
        if _is_move(l):
            nx, ny, nz, nf = _w(l, "X"), _w(l, "Y"), _w(l, "Z"), _w(l, "F")
            x = nx if nx is not None else x; y = ny if ny is not None else y
            z = nz if nz is not None else z; f = nf if nf is not None else f

    def _emit_heel_retract_prime(blk, extra_comments=()):
        head = [";PP_HEELROUTE_START\n", f"G1 E-{HEEL_E:g} F{HEEL_EF:g}\n"]
        head.extend(extra_comments)
        return head + blk + [f"G1 E{HEEL_E:g} F{HEEL_EF:g}\n", ";PP_HEELROUTE_END\n"]

    def _emit_direct_seam_body(x0, y0, tx, ty, tz, tf, zmove):
        blk = [";C seam travel: lift + direct\n"]
        blk.append(f"G1 Y{y0 + LIFT_MM:.3f} F{TF:g} ;lift\n")
        zs = f" Z{tz:.3f}" if zmove else ""
        blk.append(f"G1 X{tx:.3f} Y{ty + LIFT_MM:.3f}{zs} F{TF:g} ;seam travel\n")
        blk.append(f"G1 Y{ty:.3f} F{TF:g} ;lower\n")
        if tf is not None:
            blk.append(f"G1 F{tf:g}\n")
        return blk

    def flush(seam):
        nonlocal region
        trav = [k for k, (idx, l) in enumerate(region) if idx > first_lc and _is_xy_travel(l)]
        if not (seam and trav):
            for _, l in region:
                out.append(l); track(l)
            region = []; return
        stats["seam_travels"] += 1
        last = trav[-1]
        # target = endpoint of the last travel (track through the travel lines virtually)
        tx, ty, tz, tf = x, y, z, f
        for k, (idx, l) in enumerate(region):
            if _is_move(l):
                nx, ny, nz, nf = _w(l, "X"), _w(l, "Y"), _w(l, "Z"), _w(l, "F")
                tx = nx if nx is not None else tx; ty = ny if ny is not None else ty
                tz = nz if nz is not None else tz; tf = nf if nf is not None else tf
            if k == last: break
        if not (y <= ymin_of(pos_layer) + BELT_SIDE_BAND or ty <= ymin_of(layer) + BELT_SIDE_BAND):
            stats["nonbelt_plain"] += 1; stats["seam_travels"] -= 1
            for _, l in region:
                out.append(l); track(l)
            region = []; return
        zmove = any(_w(region[k][1], "Z") is not None for k in trav)
        x0, y0 = x, y
        lyr = pos_layer if pos_layer >= 0 else layer
        lctx = layer_ctx.get(lyr)
        qualify = _qualify_heelroute(lyr, x0, y0, tx, ty, lctx)
        for k, (idx, l) in enumerate(region):
            if k in trav and k != last:
                continue
            if k == last:
                d = math.hypot(tx - x0, ty - y0)
                if d < 1e-3:
                    if tf is not None:
                        out.append(f"G1 F{tf:g}\n"); track(f"G1 F{tf:g}\n")
                    continue
                waypts = None
                fail = None
                rlen = 0.0
                route_lyr = lyr
                if qualify:
                    # The seam often sits on the layer that is just starting,
                    # while pos_layer is still the previous outline. Try that
                    # outline, then the adjacent one. Never chord a gap.
                    # Only the outline being printed and its immediate neighbor.
                    # An older, still-connected shell must not supply a chord
                    # across a gap that is already open on this layer.
                    order = []
                    for cand in (lyr, layer, layer - 1, layer + 1):
                        if cand >= 0 and abs(cand - layer) <= 1 and cand not in order:
                            order.append(cand)
                    saw_shell = False
                    for cand in order:
                        wpts, reason, rdist = _route_waypoints(x0, y0, tx, ty, layer_ctx.get(cand))
                        if wpts is not None:
                            waypts, fail, rlen, route_lyr = wpts, None, rdist, cand
                            break
                        if reason == "no_valid_route":
                            saw_shell = True
                            fail = reason
                        elif not saw_shell:
                            fail = reason
                if waypts is not None:
                    if rlen > heel_max_route:
                        print(
                            f"pp_d heelroute over_cap layer={route_lyr} length={rlen:.2f} cap={heel_max_route:.2f} using_inset_route",
                            file=sys.stderr,
                        )
                    blk = [f"G1 Y{y0 + LIFT_MM:.3f} F{TF:g} ;lift\n"]
                    for wx, wy in waypts[:-1]:
                        blk.append(f"G1 X{wx:.3f} Y{wy + LIFT_MM:.3f} F{TF:g} ;heel route\n")
                    wz = waypts[-1]
                    zs = f" Z{tz:.3f}" if zmove else ""
                    blk.append(f"G1 X{wz[0]:.3f} Y{wz[1] + LIFT_MM:.3f}{zs} F{TF:g} ;heel route seam\n")
                    blk.append(f"G1 Y{ty:.3f} F{TF:g} ;lower\n")
                    if tf is not None:
                        blk.append(f"G1 F{tf:g}\n")
                    for b_ in _emit_heel_retract_prime(blk):
                        out.append(b_); track(b_)
                    stats["heelroute_routed"] += 1
                elif qualify and fail == "no_closed_outer_loop":
                    blk = _emit_direct_seam_body(x0, y0, tx, ty, tz, tf, zmove)
                    fb = [f";PP_HEELROUTE_FALLBACK reason={fail}\n"]
                    for b_ in _emit_heel_retract_prime(blk, extra_comments=fb):
                        out.append(b_); track(b_)
                    stats["heelroute_fallback"] += 1
                    print(
                        f"pp_d heelroute FALLBACK layer={lyr} reason={fail} "
                        f"start=({x0:.3f},{y0:.3f}) seam=({tx:.3f},{ty:.3f}) "
                        f"straight_crosses=1 "
                        f"— no valid inset route; emitting original straight move",
                        file=sys.stderr,
                    )
                else:
                    if qualify:
                        stats["heelroute_rejected"] += 1
                        print(
                            f"pp_d heelroute REJECT layer={lyr} reason={fail} "
                            f"start=({x0:.3f},{y0:.3f}) seam=({tx:.3f},{ty:.3f}) "
                            f"— no inside route; not bridging the gap",
                            file=sys.stderr,
                        )
                    retract = d >= RETRACT_MIN_TRAVEL and ty <= ymin_of(layer) + BELT_SIDE_BAND
                    blk = [";C seam travel: lift + direct\n"]
                    if retract:
                        blk.append(f"G1 E-{RETRACT_MM:g} F{RETRACT_F:g}\n"); stats["retracts"] += 1
                    blk.append(f"G1 Y{y0 + LIFT_MM:.3f} F{TF:g} ;lift\n")
                    zs = f" Z{tz:.3f}" if zmove else ""
                    blk.append(f"G1 X{tx:.3f} Y{ty + LIFT_MM:.3f}{zs} F{TF:g} ;seam travel\n")
                    blk.append(f"G1 Y{ty:.3f} F{TF:g} ;lower\n")
                    if retract:
                        blk.append(f"G1 E{RETRACT_MM + RESTART_EXTRA:g} F{RETRACT_F:g}\n")
                    if tf is not None:
                        blk.append(f"G1 F{tf:g}\n")
                    for b_ in blk:
                        out.append(b_); track(b_)
                continue
            out.append(l); track(l)
        region = []

    for idx, l in enumerate(lines):
        if l.startswith(";LAYER_CHANGE"):
            layer += 1
        if l.startswith(";C_SEAM_END"):
            flush(False)          # glide etc. before the marker stays as is
            out.append(l); seam_from = True; continue
        if _is_ext(l):
            seam = seam_from or any(r.startswith(";C_SEAM_START") for _, r in region)
            flush(seam); seam_from = False
            out.append(l); track(l); pos_layer = layer; continue
        region.append((idx, l))
    flush(False)
    if stats["heelroute_routed"] or stats["heelroute_fallback"] or stats["heelroute_rejected"]:
        print(
            f"pp_d heelroute summary: routed={stats['heelroute_routed']} "
            f"fallback={stats['heelroute_fallback']} rejected={stats['heelroute_rejected']}",
            file=sys.stderr,
        )
    return "".join(out), stats



def postprocess_C(text, PP):
    text = PP.reorder_gap_fill(text)
    seam = layer_seam_x(text)
    text, s1 = process_walls(text, PP, seam)
    text, s2 = collapse_seam_travels(text, PP.TRAVEL_F, seam)
    # B's tail (belt-wall F1200 + per-printed-layer fan ramp), with B's rotation disabled.
    orig = PP.rotate_wall_seams
    PP.rotate_wall_seams = lambda t, **kw: t
    try:
        text = PP.postprocess_apex_light(text)
    finally:
        PP.rotate_wall_seams = orig
    import sys
    print("pp_c stats", s1, s2, file=sys.stderr)
    return text


def _extrusion_lines(text):
    return [l for l in text.splitlines() if _is_ext(l)]


def _closed_walls(outer, inners, travel_xy, anchor=(1.0, 1.0)):
    """Outer loop plus inner loops, then one seam travel. Anchor extrusion is not a wall."""
    lines = [
        ";LAYER_CHANGE\n",
        f"G1 X{outer[0][0]:.3f} Y{outer[0][1]:.3f} F6000\n",
        ";TYPE:Outer wall\n",
        ";WIDTH:0.8\n",
    ]
    for p in list(outer[1:]) + [outer[0]]:
        lines.append(f"G1 X{p[0]:.3f} Y{p[1]:.3f} E0.01000\n")
    for loop in inners:
        lines.append(f"G1 X{loop[0][0]:.3f} Y{loop[0][1]:.3f} F6000\n")
        lines.append(";TYPE:Inner wall\n")
        lines.append(";WIDTH:0.45\n")
        for p in list(loop[1:]) + [loop[0]]:
            lines.append(f"G1 X{p[0]:.3f} Y{p[1]:.3f} E0.01000\n")
    lines.append(";C_SEAM_END\n")
    lines.append(f"G1 X{travel_xy[0]:.3f} Y{travel_xy[1]:.3f} F12000\n")
    lines.append(";TYPE:Gap infill\n")
    lines.append(f"G1 X{anchor[0]:.3f} Y{anchor[1]:.3f} E0.02000\n")
    return "".join(lines)


def _heel_pts(text):
    pts = []
    for l in text.splitlines():
        if ";heel route" in l:
            pts.append((_w(l, "X"), _w(l, "Y") - LIFT_MM))
    return pts


def _path_len(start, pts):
    cur = start
    total = 0.0
    for p in pts:
        total += math.hypot(p[0] - cur[0], p[1] - cur[1])
        cur = p
    return total


def _pip(px, py, poly):
    n = len(poly)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > py) != (yj > py):
            denom = yj - yi
            if abs(denom) < 1e-18:
                denom = 1e-18 if denom >= 0 else -1e-18
            if px <= (xj - xi) * (py - yi) / denom + xi:
                inside = not inside
        j = i
    return inside


def _test_heelroute():
    import io
    import contextlib

    TF = 12720.0

    def _on_loop(px, py, poly, eps=1e-3):
        return _pip(px, py, poly) or _hg_ring_dist(px, py, poly) <= eps

    def _in_buf(px, py, poly, buf):
        return _pip(px, py, poly) or _hg_ring_dist(px, py, poly) <= buf + 1e-9

    def _seg_samples(a, b, step=0.5):
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        n = max(1, int(math.ceil(L / step))) if L > 1e-9 else 1
        return [(a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n) for i in range(n + 1)]

    def _seg_dist(px, py, ax, ay, bx, by):
        abx, aby = bx - ax, by - ay
        ab2 = abx * abx + aby * aby
        if ab2 < 1e-18:
            return math.hypot(px - ax, py - ay)
        t = max(0.0, min(1.0, ((px - ax) * abx + (py - ay) * aby) / ab2))
        return math.hypot(px - (ax + t * abx), py - (ay + t * aby))

    def _wall_dist(px, py, loops):
        best = 1e18
        for loop in loops:
            for a, b in zip(loop, loop[1:] + loop[:1]):
                best = min(best, _seg_dist(px, py, a[0], a[1], b[0], b[1]))
        return best

    # Inset route runs across the bottom arm, off every wall bead, over empty
    # space. The bead test rejected this; the part-region test must accept it.
    c_outer = [(0, 0), (40, 0), (40, 8), (12, 8), (12, 32), (40, 32), (40, 40), (0, 40)]
    c_inner = [(4, 20), (4, 36), (8, 36), (8, 4), (4, 4)]
    infill_g = _closed_walls(c_outer, [c_inner], (36, 1.2), anchor=(2, -2))
    out, st = collapse_seam_travels(infill_g, TF, {0: (20.0, 0.0)})
    assert st["heelroute_routed"] == 1 and st["heelroute_fallback"] == 0, st
    assert ";PP_HEELROUTE_START" in out and ";PP_HEELROUTE_END" in out
    assert "PP_HEELROUTE_FALLBACK" not in out
    assert "G1 E-1.5 F1800" in out and "G1 E1.5 F1800" in out
    for l in out.splitlines():
        if ";heel route" in l:
            assert " E" not in l.split(";")[0]
    assert _extrusion_lines(infill_g) == _extrusion_lines(out)
    pts = _heel_pts(out)
    assert pts and math.hypot(pts[-1][0] - 36, pts[-1][1] - 1.2) < 1e-6, pts[-1]
    assert any(3.0 <= p[0] <= 9.0 for p in pts[:-1]), pts
    seq = [(4.0, 20.0)] + pts
    off_bead = False
    for a, b in zip(seq, seq[1:]):
        mx, my = 0.5 * (a[0] + b[0]), 0.5 * (a[1] + b[1])
        if _wall_dist(mx, my, (c_outer, c_inner)) > 0.5 and _pip(mx, my, c_outer):
            off_bead = True
            break
    assert off_bead, pts
    z_g = infill_g.replace(
        "G1 X36.000 Y1.200 F12000\n",
        "G1 X36.000 Y1.200 Z3.500 F12000\n",
    )
    out_z, st_z = collapse_seam_travels(z_g, TF, {0: (20.0, 0.0)})
    assert st_z["heelroute_routed"] == 1, st_z
    seam_lines = [l for l in out_z.splitlines() if l.endswith(";heel route seam")]
    mid_lines = [l for l in out_z.splitlines() if l.endswith(";heel route")]
    assert seam_lines and "Z3.500" in seam_lines[-1], seam_lines
    assert mid_lines and all("Z" not in l.split(";")[0] for l in mid_lines), mid_lines

    # Deep inside a convex outer (>0.7 mm from the boundary): not a heel crossing.
    # 0.4 mm from the boundary is, with no dependence on a bead tolerance.
    def _box_travel(y):
        return (
            ";LAYER_CHANGE\n"
            "G1 X0 Y0 F6000\n"
            ";TYPE:Outer wall\n"
            ";WIDTH:0.8\n"
            "G1 X40 Y0 E0.01\n"
            "G1 X40 Y20 E0.01\n"
            "G1 X0 Y20 E0.01\n"
            "G1 X0 Y0 E0.01\n"
            f"G1 X10 Y{y:.1f} F6000\n"
            ";C_SEAM_END\n"
            f"G1 X30 Y{y:.1f} F12000\n"
            ";TYPE:Gap infill\n"
            "G1 X5 Y-1 E0.02\n"
        )

    out_deep, st_deep = collapse_seam_travels(_box_travel(1.2), TF, {0: (20.0, 0.0)})
    assert st_deep["heelroute_routed"] == 0 and st_deep["heelroute_fallback"] == 0, st_deep
    assert ";PP_HEELROUTE" not in out_deep
    assert ";C seam travel: lift + direct" in out_deep
    out_near, st_near = collapse_seam_travels(_box_travel(0.4), TF, {0: (20.0, 0.0)})
    assert st_near["heelroute_routed"] == 1 and st_near["heelroute_fallback"] == 0, st_near
    assert "PP_HEELROUTE_FALLBACK" not in out_near

    along_wall = """;LAYER_CHANGE
G1 X100 Y0 F6000
;TYPE:Outer wall
;WIDTH:0.8
G1 X100 Y0 E0.01
G1 X120 Y0 E0.01
G1 X120 Y2 E0.01
G1 X100 Y2 E0.01
G1 X100 Y0 E0.01
;TYPE:Inner wall
;WIDTH:0.45
G1 X101 Y1 E0.01
G1 X119 Y1 E0.01
;C_SEAM_END
G1 X119 Y1 F12000
G1 X115 Y1 F12000
;TYPE:Inner wall
G1 X101 Y1.5 E0.02
"""
    out2, st2 = collapse_seam_travels(along_wall, TF, {0: (110.0, 0.0)})
    assert st2.get("heelroute_routed", 0) == 0 and st2.get("heelroute_fallback", 0) == 0
    assert ";PP_HEELROUTE" not in out2
    assert out2 == (
        ";LAYER_CHANGE\n"
        "G1 X100 Y0 F6000\n"
        ";TYPE:Outer wall\n"
        ";WIDTH:0.8\n"
        "G1 X100 Y0 E0.01\n"
        "G1 X120 Y0 E0.01\n"
        "G1 X120 Y2 E0.01\n"
        "G1 X100 Y2 E0.01\n"
        "G1 X100 Y0 E0.01\n"
        ";TYPE:Inner wall\n"
        ";WIDTH:0.45\n"
        "G1 X101 Y1 E0.01\n"
        "G1 X119 Y1 E0.01\n"
        ";C_SEAM_END\n"
        ";C seam travel: lift + direct\n"
        "G1 E-0.5 F1800\n"
        "G1 Y1.400 F12720 ;lift\n"
        "G1 X115.000 Y1.400 F12720 ;seam travel\n"
        "G1 Y1.000 F12720 ;lower\n"
        "G1 E0.45 F1800\n"
        "G1 F12000\n"
        ";TYPE:Inner wall\n"
        "G1 X101 Y1.5 E0.02\n"
    )

    # No closed outer loop: the travel is not a heel crossing, so the normal
    # collapse is unchanged and no straight tagged fallback is emitted.
    no_inner = """;LAYER_CHANGE
G1 X100 Y0 F6000
;TYPE:Outer wall
;WIDTH:0.8
G1 X100 Y0 E0.01
G1 X120 Y0 E0.01
G1 X120 Y20 E0.01
G1 X118 Y18 E0.01
;C_SEAM_END
G1 X110 Y0.4 F12000
;TYPE:Outer wall
G1 X100 Y0 E0.02
"""
    out3, st3 = collapse_seam_travels(no_inner, TF, {0: (110.0, 0.0)})
    assert st3["heelroute_routed"] == 0 and st3["heelroute_fallback"] == 0, st3
    assert "PP_HEELROUTE" not in out3
    assert out3 == (
        ";LAYER_CHANGE\n"
        "G1 X100 Y0 F6000\n"
        ";TYPE:Outer wall\n"
        ";WIDTH:0.8\n"
        "G1 X100 Y0 E0.01\n"
        "G1 X120 Y0 E0.01\n"
        "G1 X120 Y20 E0.01\n"
        "G1 X118 Y18 E0.01\n"
        ";C_SEAM_END\n"
        ";C seam travel: lift + direct\n"
        "G1 E-0.5 F1800\n"
        "G1 Y18.400 F12720 ;lift\n"
        "G1 X110.000 Y0.800 F12720 ;seam travel\n"
        "G1 Y0.400 F12720 ;lower\n"
        "G1 E0.45 F1800\n"
        "G1 F12000\n"
        ";TYPE:Outer wall\n"
        "G1 X100 Y0 E0.02\n"
    )

    # Closed outer and no inner wall: still route on the 0.7 mm inset boundary.
    closed_only = _closed_walls([(0, 0), (50, 0), (50, 25), (0, 25)], [], (40, 0.2), anchor=(10, -2))
    out_ci, st_ci = collapse_seam_travels(closed_only, TF, {0: (25.0, 0.0)})
    assert st_ci["heelroute_routed"] == 1 and st_ci["heelroute_fallback"] == 0, st_ci
    assert "PP_HEELROUTE_FALLBACK" not in out_ci
    ci_pts = _heel_pts(out_ci)
    assert len(ci_pts) >= 2, ci_pts
    assert math.hypot(ci_pts[-1][0] - 40, ci_pts[-1][1] - 0.2) < 1e-6

    # Crescent outer. Horn-to-horn chord crosses the bay and must be routed.
    crescent = [(0, 0), (8, 10), (22, 10), (30, 0), (24, 3), (15, 5), (6, 3)]
    cx = sum(p[0] for p in crescent) / len(crescent)
    cy = sum(p[1] for p in crescent) / len(crescent)
    assert not _pip(cx, cy, crescent), (cx, cy)
    assert not _on_loop(15.0, 0.0, crescent, 1e-3)
    cres_g = _closed_walls(crescent, [], (30, 0), anchor=(15, -2))
    out_c, st_c = collapse_seam_travels(cres_g, TF, {0: (15.0, 0.0)})
    assert st_c["heelroute_routed"] == 1 and st_c["heelroute_fallback"] == 0, st_c
    assert "PP_HEELROUTE_FALLBACK" not in out_c
    cpts = _heel_pts(out_c)
    assert _path_len((0, 0), cpts) > 30.0 + 1.0
    assert any(p[1] > 2.0 for p in cpts[:-1]), cpts
    prev = (0.0, 0.0)
    for p in cpts:
        for sx, sy in _seg_samples(prev, p, 0.5):
            assert _on_loop(sx, sy, crescent, 1e-3), (sx, sy)
        prev = p
    assert _extrusion_lines(cres_g) == _extrusion_lines(out_c)

    # A second outer island in the bay must not win by a straight chord across
    # the opening. The shorter inset walk of the crescent stays in the crescent.
    # The return to the horn is before C_SEAM_END, so the seam travel still
    # starts at (0, 0).
    island = [(13.0, 0.2), (17.0, 0.2), (17.0, 1.5), (13.0, 1.5)]
    assert all(not _on_loop(px, py, crescent, 1e-6) for px, py in island)
    isle_g = _closed_walls(crescent, [], (30, 0), anchor=(15, -2))
    isle_lines = [
        "G1 X13.000 Y0.200 F6000\n",
        ";TYPE:Outer wall\n",
        ";WIDTH:0.8\n",
        "G1 X17.000 Y0.200 E0.01000\n",
        "G1 X17.000 Y1.500 E0.01000\n",
        "G1 X13.000 Y1.500 E0.01000\n",
        "G1 X13.000 Y0.200 E0.01000\n",
        "G1 X0.000 Y0.000 F6000\n",
    ]
    isle_g = isle_g.replace(";C_SEAM_END\n", "".join(isle_lines) + ";C_SEAM_END\n", 1)
    out_i, st_i = collapse_seam_travels(isle_g, TF, {0: (15.0, 0.0)})
    assert st_i["heelroute_routed"] == 1 and st_i["heelroute_fallback"] == 0, st_i
    ipts = _heel_pts(out_i)
    assert _path_len((0, 0), ipts) > 30.0 + 1.0
    prev = (0.0, 0.0)
    for p in ipts:
        assert not _in_buf(p[0], p[1], island, 0.2), p
        assert _in_buf(p[0], p[1], crescent, 0.2), p
        for sx, sy in _seg_samples(prev, p, 0.25):
            assert not _in_buf(sx, sy, island, 0.2), (sx, sy)
            assert _in_buf(sx, sy, crescent, 0.2), (sx, sy)
        prev = p

    # Two inner loops and a notch the straight travel crosses. The route stays
    # on the near loop (around the notch), not the decoy on the far left.
    notch = [(0, 0), (40, 0), (50, 20), (60, 0), (100, 0), (100, 40), (0, 40)]
    two_g = _closed_walls(
        notch,
        [
            [(2, 30), (12, 30), (12, 36), (2, 36)],
            [(30, 1.2), (30, 28), (70, 28), (70, 1.2)],
        ],
        (70, 1.2),
        anchor=(50, -2),
    )
    out_t, st_t = collapse_seam_travels(two_g, TF, {0: (50.0, 0.0)})
    assert st_t["heelroute_routed"] == 1 and st_t["heelroute_fallback"] == 0, st_t
    tpts = _heel_pts(out_t)
    assert tpts and all(p[0] > 20.0 for p in tpts), tpts
    assert any(p[1] > 20.0 for p in tpts), tpts
    assert _extrusion_lines(two_g) == _extrusion_lines(out_t)

    # Around-path is between the old 60 mm cap and the 150 mm default.
    long_outer = [(0, 0), (12, 0), (12, 35), (50, 35), (50, 0), (62, 0), (62, 48), (0, 48)]
    long_inner = [(4, 1.2), (4, 42), (58, 42), (58, 1.2)]
    long_g = _closed_walls(long_outer, [long_inner], (58, 1.2), anchor=(1, -2))
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        out_l, st_l = collapse_seam_travels(long_g, TF, {0: (30.0, 0.0)})
    long_pts = _heel_pts(out_l)
    long_len = _path_len((4, 1.2), long_pts)
    straight = math.hypot(58 - 4, 0)
    assert st_l["heelroute_routed"] == 1 and st_l["heelroute_fallback"] == 0, st_l
    assert 60.0 < long_len < 150.0, long_len
    assert long_len > straight + 5.0, (long_len, straight)
    assert "PP_HEELROUTE_FALLBACK" not in out_l
    assert "over_cap" not in err.getvalue()
    assert _extrusion_lines(long_g) == _extrusion_lines(out_l)
    err60 = io.StringIO()
    with contextlib.redirect_stderr(err60):
        out60, st60 = collapse_seam_travels(long_g, TF, {0: (30.0, 0.0)}, heel_max_route=60.0)
    assert st60["heelroute_routed"] == 1 and st60["heelroute_fallback"] == 0, st60
    assert "PP_HEELROUTE_FALLBACK" not in out60
    log60 = err60.getvalue()
    assert "over_cap" in log60, log60
    assert "emitting original straight move" not in log60
    assert abs(_path_len((4, 1.2), _heel_pts(out60)) - long_len) < 1e-6

    # Inner loop cannot reach a seam in the margin. Closed outer still routes
    # on the inset boundary; the straight move is not emitted.
    far_g = _closed_walls(
        [(-5, -5), (100, -5), (100, 30), (-5, 30)],
        [[(0, 10), (15, 10), (15, 2), (0, 2)]],
        (70, -4.4),
        anchor=(70, -6),
    )
    err_f = io.StringIO()
    with contextlib.redirect_stderr(err_f):
        out_f, st_f = collapse_seam_travels(far_g, TF, {0: (0.0, -5.0)})
    assert st_f["heelroute_routed"] == 1 and st_f["heelroute_fallback"] == 0, st_f
    assert "PP_HEELROUTE_FALLBACK" not in out_f
    assert "emitting original straight move" not in err_f.getvalue()
    fpts = _heel_pts(out_f)
    assert len(fpts) >= 2 and math.hypot(fpts[-1][0] - 70, fpts[-1][1] + 4.4) < 1e-6, fpts[-1]
    assert _extrusion_lines(far_g) == _extrusion_lines(out_f)

    # Open inner chain. Offset moves the crossbar away from the nearer outer wall.
    open_g = """;LAYER_CHANGE
G1 X0 Y0 F6000
;TYPE:Outer wall
;WIDTH:0.8
G1 X0 Y0 E0.01
G1 X35 Y0 E0.01
G1 X40 Y12 E0.01
G1 X60 Y12 E0.01
G1 X65 Y0 E0.01
G1 X100 Y0 E0.01
G1 X100 Y40 E0.01
G1 X0 Y40 E0.01
G1 X0 Y0 E0.01
G1 X30 Y1.2 F6000
;TYPE:Inner wall
;WIDTH:0.45
G1 X30 Y15 E0.01
G1 X70 Y15 E0.01
G1 X70 Y1.2 E0.01
;C_SEAM_END
G1 X30 Y1.2 F12000
;TYPE:Gap infill
G1 X30 Y-2 E0.02
"""
    out_o, st_o = collapse_seam_travels(open_g, TF, {0: (50.0, 0.0)})
    assert st_o["heelroute_routed"] == 1 and st_o["heelroute_fallback"] == 0, st_o
    opts = _heel_pts(out_o)
    high = [p for p in opts if p[1] > 5.0]
    assert high and min(p[1] for p in high) > 15.0, opts
    assert _extrusion_lines(open_g) == _extrusion_lines(out_o)

    # Bay in the belt edge. The straight seam travel bridges the opening.
    # The route must go around it, and no 0.5 mm sample of an emitted leg may leave.
    gap_outer = [(0, 0), (15, 0), (15, 20), (40, 20), (40, 0), (55, 0), (55, 30), (0, 30)]
    assert any(not _on_loop(sx, sy, gap_outer, 1e-3) for sx, sy in _seg_samples((0.0, 0.0), (55.0, 0.2), 0.5))
    gap_g = _closed_walls(gap_outer, [], (55, 0.2), anchor=(10, -2))
    out_g, st_g = collapse_seam_travels(gap_g, TF, {0: (27.0, 0.0)})
    assert st_g["heelroute_routed"] == 1 and st_g["heelroute_fallback"] == 0, st_g
    assert "PP_HEELROUTE_FALLBACK" not in out_g
    gpts = _heel_pts(out_g)
    assert gpts and math.hypot(gpts[-1][0] - 55, gpts[-1][1] - 0.2) < 1e-6, gpts[-1]
    assert any(p[1] > 18.0 for p in gpts), gpts
    assert _path_len((0.0, 0.0), gpts) > math.hypot(55.0, 0.2) + 5.0
    prev = (0.0, 0.0)
    for p in gpts:
        for sx, sy in _seg_samples(prev, p, 0.5):
            assert _on_loop(sx, sy, gap_outer, 1e-3), (sx, sy, prev, p)
        prev = p
    assert _extrusion_lines(gap_g) == _extrusion_lines(out_g)

    # Two islands with a real gap. A straight jump leaves both polygons.
    # There is no inside path, so the travel is not emitted as a heel leg.
    islands = """;LAYER_CHANGE
G1 X0 Y0 F6000
;TYPE:Outer wall
;WIDTH:0.8
G1 X0 Y0 E0.01
G1 X10 Y0 E0.01
G1 X10 Y2 E0.01
G1 X0 Y2 E0.01
G1 X0 Y0 E0.01
G1 X0 Y6 F6000
;TYPE:Outer wall
G1 X0 Y6 E0.01
G1 X10 Y6 E0.01
G1 X10 Y12 E0.01
G1 X0 Y12 E0.01
G1 X0 Y6 E0.01
;C_SEAM_END
G1 X5 Y0.2 F12000
;TYPE:Gap infill
G1 X1 Y-1 E0.02
"""
    low = [(0, 0), (10, 0), (10, 2), (0, 2)]
    high_poly = [(0, 6), (10, 6), (10, 12), (0, 12)]
    assert any(
        not _on_loop(sx, sy, low, 1e-3) and not _on_loop(sx, sy, high_poly, 1e-3)
        for sx, sy in _seg_samples((0.0, 6.0), (5.0, 0.2), 0.5)
    )
    out_is, st_is = collapse_seam_travels(islands, TF, {0: (5.0, 0.0)})
    assert st_is["heelroute_routed"] == 0 and st_is["heelroute_fallback"] == 0, st_is
    assert st_is["heelroute_rejected"] == 1, st_is
    assert "PP_HEELROUTE" not in out_is
    assert ";heel route" not in out_is
    assert _extrusion_lines(islands) == _extrusion_lines(out_is)

    # Layer 0 is one solid. Later layers have split, so the old shell must
    # not be used to chord the gap that is already open.
    split = """;LAYER_CHANGE
G1 X0 Y0 F6000
;TYPE:Outer wall
;WIDTH:0.8
G1 X0 Y0 E0.01
G1 X20 Y0 E0.01
G1 X20 Y14 E0.01
G1 X0 Y14 E0.01
G1 X0 Y0 E0.01
;LAYER_CHANGE
;TYPE:Outer wall
G1 X0 Y0 E0.01
G1 X10 Y0 E0.01
G1 X10 Y2 E0.01
G1 X0 Y2 E0.01
G1 X0 Y0 E0.01
G1 X0 Y8 F6000
G1 X10 Y8 E0.01
G1 X10 Y14 E0.01
G1 X0 Y14 E0.01
G1 X0 Y8 E0.01
;LAYER_CHANGE
G1 X0 Y0 F6000
;TYPE:Outer wall
G1 X10 Y0 E0.01
G1 X10 Y2 E0.01
G1 X0 Y2 E0.01
G1 X0 Y0 E0.01
G1 X0 Y8 F6000
G1 X0 Y8 E0.01
G1 X10 Y8 E0.01
G1 X10 Y14 E0.01
G1 X0 Y14 E0.01
G1 X0 Y8 E0.01
;C_SEAM_END
G1 X5 Y0.2 F12000
;TYPE:Gap infill
G1 X1 Y-1 E0.02
"""
    out_sp, st_sp = collapse_seam_travels(split, TF, {0: (10.0, 0.0), 1: (10.0, 0.0), 2: (5.0, 0.0)})
    assert st_sp["heelroute_routed"] == 0 and st_sp["heelroute_fallback"] == 0, st_sp
    assert st_sp["heelroute_rejected"] == 1, st_sp
    assert ";heel route" not in out_sp
    assert _extrusion_lines(split) == _extrusion_lines(out_sp)

    print(
        "heelroute tests ok",
        st, st_deep, st_near, st2, st3, st_ci, st_c, st_i, st_t, st_l, round(long_len, 2), st60, st_f, st_o, st_g, st_is, st_sp,
    )


if __name__ == "__main__":
    _test_heelroute()
