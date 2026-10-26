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
# G-code rounds to 0.001 mm. Hold this much extra so the rounded lifted
# leg is still at least 0.5 mm inside the outer-wall centerline.
_HEEL_EMIT_PAD = 0.002
# Final seam hop and the opening step off a sharp tip. A thick wall only needs
# ~1 mm; a sharp horn can be ~2.4 mm from the tip before a 0.5 mm inset exists.
_HEEL_APPROACH_MAX = 2.5
_HEEL_SAMPLE_STEP = 0.5
# Seam targets sit a fraction of a millimeter off the outer-wall centerline.
_HEEL_BOUND_EPS = 0.25
# Box outside test (map3): the outline expanded by 0.001 mm. A leg with more
# than 0.01 mm past that buffer is outside. The seam tip gets nothing else.
_HEEL_OUT_BUFFER = 1e-3
_HEEL_OUT_LEN = 0.01
# A walk through a sliver thinner than this has no 0.5 mm inset. Reject it.
_HEEL_MIN_WIDTH = 1.0
_HG_CELL = 4.0
# Heel band only. Everywhere else the route cap stays 60 mm.
_HEEL_BAND_LO = 355
_HEEL_BAND_HI = 447
_HEEL_BAND_CAP = 200.0
# Pure +Y lifts on these layers stop inside the outline but under 0.5 mm.
# Other layers keep a same-wall shallow lift; only an outline departure is combined.
_HEEL_LIFT_FIX_LAYERS = (40, 41, 251)


def _hg_closest_on_seg(px, py, ax, ay, bx, by):
    abx, aby = bx - ax, by - ay
    ab2 = abx * abx + aby * aby
    if ab2 < 1e-18:
        return ax, ay, math.hypot(px - ax, py - ay)
    t = ((px - ax) * abx + (py - ay) * aby) / ab2
    if t < 0.0:
        t = 0.0
    elif t > 1.0:
        t = 1.0
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


def _hg_shell_thick(outer, geom):
    """True when some interior point is at least 0.5 mm from the wall.

    A concave shell's longest-edge representative can sit 0.01 mm inside
    that edge. The shell is thin only when every probe stays shallow.
    """
    index = geom["index"]
    need = _HEEL_MIN_WIDTH * 0.5

    def clears(px, py):
        if not _hg_strict_inside(px, py, geom):
            return False
        return _hg_dist_point_index(px, py, index) + 1e-3 >= need

    n = len(outer)
    if n < 3:
        return False
    cx = sum(p[0] for p in outer) / n
    cy = sum(p[1] for p in outer) / n
    if clears(cx, cy):
        return True
    best_i, best_l = 0, -1.0
    for i in range(n):
        ax, ay = outer[i]
        bx, by = outer[(i + 1) % n]
        el = math.hypot(bx - ax, by - ay)
        if el > best_l:
            best_l, best_i = el, i
    ax, ay = outer[best_i]
    bx, by = outer[(best_i + 1) % n]
    el = math.hypot(bx - ax, by - ay) or 1.0
    mx, my = 0.5 * (ax + bx), 0.5 * (ay + by)
    if _hg_area(outer) >= 0.0:
        nx, ny = -(by - ay) / el, (bx - ax) / el
    else:
        nx, ny = (by - ay) / el, -(bx - ax) / el
    if not _hg_strict_inside(mx + nx * 0.2, my + ny * 0.2, geom):
        nx, ny = -nx, -ny
    for eps in (0.2, 0.5, 1.0, 2.0, 4.0, 8.0):
        if clears(mx + nx * eps, my + ny * eps):
            return True
    return False


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
        holes = [raw[c] for c in children[i]]
        shells.append({
            "outer": pts,
            "outer_ybins": _hg_ring_ybins(pts),
            "holes": holes,
            "hole_ybins": [_hg_ring_ybins(h) for h in holes],
            "bbox": _hg_bbox(pts),
        })
    return shells


def _hg_ring_ybins(ring, cell=_HG_CELL):
    """Uniform grid of edge indexes, keyed by the edge's Y bins."""
    bins = {}
    n = len(ring)
    for i in range(n):
        ay = ring[i][1]
        by = ring[(i + 1) % n][1]
        iy0 = int(math.floor(min(ay, by) / cell))
        iy1 = int(math.floor(max(ay, by) / cell))
        for iy in range(iy0, iy1 + 1):
            bins.setdefault(iy, []).append(i)
    return bins


def _hg_pip_fast(px, py, ring, ybins, cell=_HG_CELL):
    """Even-odd test using only the edges in this point's Y bin."""
    if ybins is None:
        return _pip(px, py, ring)
    idxs = ybins.get(int(math.floor(py / cell)))
    if not idxs:
        return False
    inside = False
    n = len(ring)
    for i in idxs:
        xi, yi = ring[i]
        xj, yj = ring[(i + 1) % n]
        if (yi > py) == (yj > py):
            continue
        denom = yj - yi
        if abs(denom) < 1e-18:
            denom = 1e-18 if denom >= 0 else -1e-18
        if px <= (xj - xi) * (py - yi) / denom + xi:
            inside = not inside
    return inside


def _hg_point_in_shells(px, py, shells, eps=_HEEL_BOUND_EPS):
    for sh in shells:
        minx, miny, maxx, maxy = sh["bbox"]
        if px < minx - eps or px > maxx + eps or py < miny - eps or py > maxy + eps:
            continue
        outer_in = _hg_pip_fast(px, py, sh["outer"], sh.get("outer_ybins"))
        if not outer_in and _hg_ring_dist(px, py, sh["outer"]) > eps:
            continue
        hole = False
        for hi, h in enumerate(sh["holes"]):
            yb = sh["hole_ybins"][hi] if sh.get("hole_ybins") else None
            if _hg_pip_fast(px, py, h, yb) and _hg_ring_dist(px, py, h) > eps:
                hole = True
                break
        if hole:
            continue
        return True
    return False


def _hg_on_shell(px, py, sh, eps=_HEEL_BOUND_EPS):
    """True when the point lies in this shell, or within eps of its outer wall."""
    return _hg_point_in_shells(px, py, [sh], eps)


def _hg_common_shells(x0, y0, tx, ty, shells):
    """Shell indexes that contain both endpoints (the seam may sit on the wall)."""
    common = []
    for i, sh in enumerate(shells):
        if _hg_on_shell(x0, y0, sh) and _hg_on_shell(tx, ty, sh):
            common.append(i)
    return common


def _hg_build_edge_index(shells, cell=_HG_CELL):
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
    cache = index.get("_near")
    if cache is None:
        cache = index["_near"] = {}
    key = (px, py)
    hit = cache.get(key)
    if hit is not None:
        return hit
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
        # Only the cells at this radius. Inner cells were searched already.
        if rad == 0:
            cells = ((0, 0),)
        else:
            cells = [(d, -rad) for d in range(-rad, rad + 1)]
            cells.extend((d, rad) for d in range(-rad, rad + 1))
            cells.extend((-rad, d) for d in range(-rad + 1, rad))
            cells.extend((rad, d) for d in range(-rad + 1, rad))
        for dx, dy in cells:
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
            hit = (best, bqx, bqy)
            cache[key] = hit
            return hit
        rad += 1
    hit = (best, bqx, bqy)
    cache[key] = hit
    return hit


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


def _hg_segment_closer_than(x0, y0, x1, y1, index, limit):
    """True when some boundary edge comes within ``limit`` of this segment.

    The search is the segment's bbox expanded by ``limit``, via the uniform grid.
    """
    edges = index["edges"]
    if not edges or limit < 0.0:
        return False
    cell = index["cell"]
    grid = index["grid"]
    ix0 = int(math.floor((min(x0, x1) - limit) / cell))
    ix1 = int(math.floor((max(x0, x1) + limit) / cell))
    iy0 = int(math.floor((min(y0, y1) - limit) / cell))
    iy1 = int(math.floor((max(y0, y1) + limit) / cell))
    seen = set()
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
                if _hg_dist_seg_seg(x0, y0, x1, y1, ax, ay, bx, by) < limit:
                    return True
    return False


def _hg_cross_ts(x0, y0, x1, y1, index, end_eps=1e-4):
    """Parameters in (0, 1) where this segment meets the part boundary."""
    edges = index["edges"]
    if not edges:
        return []
    cell = index["cell"]
    grid = index["grid"]
    pad = cell
    ix0 = int(math.floor((min(x0, x1) - pad) / cell))
    ix1 = int(math.floor((max(x0, x1) + pad) / cell))
    iy0 = int(math.floor((min(y0, y1) - pad) / cell))
    iy1 = int(math.floor((max(y0, y1) + pad) / cell))
    rx, ry = x1 - x0, y1 - y0
    ts = []
    seen = set()
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
                sx, sy = bx - ax, by - ay
                den = rx * sy - ry * sx
                if abs(den) < 1e-12:
                    continue
                t = ((ax - x0) * sy - (ay - y0) * sx) / den
                u = ((ax - x0) * ry - (ay - y0) * rx) / den
                if -1e-8 <= t <= 1.0 + 1e-8 and -1e-8 <= u <= 1.0 + 1e-8:
                    ts.append(min(1.0, max(0.0, t)))
    ts.sort()
    out = []
    for t in ts:
        if end_eps < t < 1.0 - end_eps and (not out or t - out[-1] > 1e-4):
            out.append(t)
    return out


def _hg_strict_inside(px, py, geom):
    """Inside the part. The boundary counts; the 0.25 mm outside slop does not."""
    return _hg_point_in_shells(px, py, geom["shells"], 1e-3)


def _hg_band_length(x0, y0, x1, y1, t0, t1, geom, stop_at=None):
    """Length of [t0, t1] whose clearance to the boundary is under 0.5 mm.

    ``stop_at`` returns as soon as the band exceeds that length. Callers only
    compare against the approach allowance, so the exact tail is not needed.
    """
    L = math.hypot(x1 - x0, y1 - y0)
    span = (t1 - t0) * L
    if span < 1e-9:
        return 0.0
    # Nothing within 0.5 mm of this piece. Sampling the length would agree.
    if not _hg_segment_closer_than(
        x0 + (x1 - x0) * t0, y0 + (y1 - y0) * t0,
        x0 + (x1 - x0) * t1, y0 + (y1 - y0) * t1,
        geom["index"], _HEEL_ACCEPT_INSET - 1e-3,
    ):
        return 0.0
    n = max(1, int(math.ceil(span / _HEEL_SAMPLE_STEP)))
    step = span / n
    band = 0.0
    index = geom["index"]
    for i in range(n):
        tm = t0 + (i + 0.5) / float(n) * (t1 - t0)
        d = _hg_dist_point_index(x0 + (x1 - x0) * tm, y0 + (y1 - y0) * tm, index)
        if d + 1e-3 < _HEEL_ACCEPT_INSET:
            band += step
            if stop_at is not None and band > stop_at:
                return band
    return band


def _hg_leg_samples(x0, y0, x1, y1):
    """Points along a leg at the outline sample step, including both ends."""
    L = math.hypot(x1 - x0, y1 - y0)
    n = max(1, int(math.ceil(L / _HEEL_SAMPLE_STEP))) if L > 1e-9 else 1
    pts = []
    for i in range(n + 1):
        t = i / float(n)
        pts.append((x0 + (x1 - x0) * t, y0 + (y1 - y0) * t))
    return pts


def _hg_map3_outside_len(x0, y0, x1, y1, geom):
    """Length of this segment outside the outline expanded by 0.001 mm.

    Same outside test as the box measurement. A leg is outside when the
    result is greater than 0.01 mm. The seam tip has no extra exemption.
    """
    L = math.hypot(x1 - x0, y1 - y0)
    shells = geom["shells"]
    eps = _HEEL_OUT_BUFFER
    if L < 1e-12:
        if _hg_point_in_shells(x0, y0, shells, eps):
            return 0.0
        return 1.0

    def inside_buf(t):
        return _hg_point_in_shells(
            x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, shells, eps)

    cuts = [0.0]
    cuts.extend(_hg_cross_ts(x0, y0, x1, y1, geom["index"], end_eps=1e-9))
    cuts.append(1.0)
    outside = 0.0
    for a, b in zip(cuts, cuts[1:]):
        if b - a < 1e-12:
            continue
        tm = 0.5 * (a + b)
        if inside_buf(tm):
            continue
        t0 = a
        if inside_buf(a):
            lo, hi = a, tm
            for _ in range(16):
                mid = 0.5 * (lo + hi)
                if inside_buf(mid):
                    lo = mid
                else:
                    hi = mid
            t0 = hi
        t1 = b
        if inside_buf(b):
            lo, hi = tm, b
            for _ in range(16):
                mid = 0.5 * (lo + hi)
                if inside_buf(mid):
                    hi = mid
                else:
                    lo = mid
            t1 = lo
        outside += max(0.0, t1 - t0) * L
    return outside


def _x_crossings(poly, sx, sy, closed):
    """Each edge's closest-X point, as (dx, dy, i, t, qx, qy)."""
    n = len(poly)
    limit = n if closed else n - 1
    if n == 0:
        return []
    if limit < 1:
        px, py = poly[0]
        return [(abs(px - sx), abs(py - sy), 0, 0.0, px, py)]
    cands = []
    for i in range(limit):
        ax, ay = poly[i]
        bx, by = poly[(i + 1) % n]
        abx, aby = bx - ax, by - ay
        if abs(abx) < 1e-12:
            if abs(aby) < 1e-12:
                t, qx, qy = 0.0, ax, ay
            elif (ay - sy) * (by - sy) <= 0.0:
                t = (sy - ay) / aby
                qx, qy = ax, sy
            elif abs(ay - sy) <= abs(by - sy):
                t, qx, qy = 0.0, ax, ay
            else:
                t, qx, qy = 1.0, bx, by
        else:
            t_raw = (sx - ax) / abx
            if t_raw <= 0.0:
                t, qx, qy = 0.0, ax, ay
            elif t_raw >= 1.0:
                t, qx, qy = 1.0, bx, by
            else:
                t = t_raw
                qx, qy = sx, ay + aby * t
        cands.append((abs(qx - sx), abs(qy - sy), i, t, qx, qy))
    return cands


def _x_nearest_hits(poly, sx, sy, closed):
    """Every point that ties for the seam's nearest X.

    An open inset can cross that X twice. The caller keeps the crossing
    whose route is usable, instead of the first one in ring order.
    """
    cands = _x_crossings(poly, sx, sy, closed)
    if not cands:
        return []
    best_dx = min(c[0] for c in cands)
    hits = []
    for dx, _dy, i, t, qx, qy in cands:
        if dx <= best_dx + 1e-6:
            hits.append((math.hypot(qx - sx, qy - sy), i, t, qx, qy))
    return hits


def _x_nearest_on_poly(poly, sx, sy, closed):
    """Point on poly whose X is nearest the seam, then nearest in Y.

    An edge that crosses the seam X contributes that intersection, so the
    last leg drops straight in Y. Several crossings keep the belt-side one.
    """
    cands = _x_crossings(poly, sx, sy, closed)
    if not cands:
        return None
    _dx, _dy, i, t, qx, qy = min(cands, key=lambda c: (c[0], c[1]))
    return (math.hypot(qx - sx, qy - sy), i, t, qx, qy)


def _hg_samples_outside(x0, y0, x1, y1, geom):
    """True when any 0.5 mm sample, including both ends, is outside the outline."""
    for px, py in _hg_leg_samples(x0, y0, x1, y1):
        if not _hg_strict_inside(px, py, geom):
            return True
    return False


def _hg_exit_leg_ok(x0, y0, x1, y1, geom):
    """Final seam leg. Same sampled inside test as the other legs; the tip is exempt.

    Every 0.5 mm sample except the seam endpoint must be inside the outline.
    The endpoint may sit on the wall. The approach may use the outer 0.5 mm
    band, but not for longer than the horn allowance, and it may not travel
    through the outside.
    """
    L = math.hypot(x1 - x0, y1 - y0)
    index = geom["index"]
    if L < 1e-9:
        if _hg_strict_inside(x0, y0, geom):
            return True
        return _hg_dist_point_index(x0, y0, index) <= _HEEL_BOUND_EPS
    samples = _hg_leg_samples(x0, y0, x1, y1)
    for px, py in samples[:-1]:
        if not _hg_strict_inside(px, py, geom):
            return False
    if not _hg_strict_inside(x1, y1, geom):
        if _hg_dist_point_index(x1, y1, index) > _HEEL_BOUND_EPS:
            return False
    # A corner clip between samples still crosses an edge. Only a hair at the
    # seam tip may lie outside.
    cuts = [0.0] + _hg_cross_ts(x0, y0, x1, y1, index) + [1.0]
    for a, b in zip(cuts, cuts[1:]):
        mx = x0 + (x1 - x0) * 0.5 * (a + b)
        my = y0 + (y1 - y0) * 0.5 * (a + b)
        if _hg_strict_inside(mx, my, geom):
            continue
        # The seam may sit a fraction of a millimetre off the centerline.
        # Anything longer than that, or an outside interval that is not the tip, fails.
        if b < 1.0 - 1e-6 or (b - a) * L > _HEEL_BOUND_EPS + 0.05:
            return False
    return _hg_band_length(
        x0, y0, x1, y1, 0.0, 1.0, geom, _HEEL_APPROACH_MAX + 0.05
    ) <= _HEEL_APPROACH_MAX + 0.05


def _hg_end_span_ok(x0, y0, x1, y1, geom, role):
    """Entry and the final seam leg stay inside. Only the seam tip may sit just outside.

    The opening hop and the final approach may use the outer 0.5 mm band.
    They may not travel along the outside of the outline.
    """
    if role == "exit":
        return _hg_exit_leg_ok(x0, y0, x1, y1, geom)
    L = math.hypot(x1 - x0, y1 - y0)
    if L < 1e-9:
        if _hg_strict_inside(x0, y0, geom):
            return True
        return _hg_dist_point_index(x0, y0, geom["index"]) <= _HEEL_BOUND_EPS
    cuts = [0.0] + _hg_cross_ts(x0, y0, x1, y1, geom["index"]) + [1.0]
    outside = 0.0
    band = 0.0
    for a, b in zip(cuts, cuts[1:]):
        tm = 0.5 * (a + b)
        mx = x0 + (x1 - x0) * tm
        my = y0 + (y1 - y0) * tm
        if _hg_strict_inside(mx, my, geom):
            remain = _HEEL_APPROACH_MAX + 0.05 - band
            if remain <= 0.0:
                return False
            band += _hg_band_length(x0, y0, x1, y1, a, b, geom, remain)
            if band > _HEEL_APPROACH_MAX + 0.05:
                return False
            continue
        seg_len = (b - a) * L
        if a > 1e-6:
            return False
        outside += seg_len
    if outside > _HEEL_BOUND_EPS + 0.05:
        return False
    index = geom["index"]
    if not _hg_strict_inside(x0, y0, geom):
        if _hg_dist_point_index(x0, y0, index) > _HEEL_BOUND_EPS:
            return False
    if not _hg_strict_inside(x1, y1, geom):
        return False
    return band <= _HEEL_APPROACH_MAX + 0.05


def _hg_span_ok(x0, y0, x1, y1, geom, role):
    """Middle legs stay at least 0.5 mm inside. Ends may use the band, not the outside.

    One point-in-polygon per piece of the leg, not one per sample. Clearance uses
    the segment grid. A middle leg through a region thinner than 1 mm cannot
    keep that clearance, so it is rejected.
    """
    L = math.hypot(x1 - x0, y1 - y0)
    if role == "mid":
        if L < 1e-9:
            if not _hg_strict_inside(x0, y0, geom):
                return False
            if _hg_dist_point_index(x0, y0, geom["index"]) + 1e-3 < _HEEL_ACCEPT_INSET:
                return False
            lifted = _hg_dist_point_index(x0, y0 + LIFT_MM, geom["index"])
            # Emitted coordinates. 0.5 mm unlifted can sit ~0.1 mm from a wall
            # above the leg after the 0.4 mm lift.
            return lifted + 1e-3 >= _HEEL_ACCEPT_INSET + _HEEL_EMIT_PAD
        if not _hg_strict_inside(x0, y0, geom) or not _hg_strict_inside(x1, y1, geom):
            return False
        # Half of _HEEL_MIN_WIDTH. A thinner sliver has no such inset.
        if _hg_segment_closer_than(x0, y0, x1, y1, geom["index"], _HEEL_MIN_WIDTH * 0.5 - 1e-3):
            return False
        # The leg is emitted 0.4 mm higher in Y. Every non-seam leg, on this
        # layer's outer wall, stays at least 0.5 mm inside after that shift.
        if _hg_segment_closer_than(
            x0, y0 + LIFT_MM, x1, y1 + LIFT_MM, geom["index"],
            _HEEL_ACCEPT_INSET + _HEEL_EMIT_PAD - 1e-3,
        ):
            return False
        return True
    return _hg_end_span_ok(x0, y0, x1, y1, geom, role)


def _hg_route_reverses(x0, y0, pts):
    """True when the route turns back along a leg it already traveled."""
    segs = []
    prev = (x0, y0)
    for p in pts:
        if math.hypot(p[0] - prev[0], p[1] - prev[1]) >= 0.2:
            segs.append((prev[0], prev[1], p[0], p[1]))
        prev = p
    for i, (ax, ay, bx, by) in enumerate(segs):
        dx, dy = bx - ax, by - ay
        L = math.hypot(dx, dy)
        if L < 0.3:
            continue
        ux, uy = dx / L, dy / L
        for cx, cy, ex, ey in segs[i + 1:]:
            exx, eyy = ex - cx, ey - cy
            L2 = math.hypot(exx, eyy)
            if L2 < 0.3:
                continue
            if (exx * ux + eyy * uy) / L2 > -0.85:
                continue
            # Perpendicular distance to the first leg. A parallel track
            # farther than this is the other side of a wall, not a reversal.
            def side(px, py):
                return abs((px - ax) * (-uy) + (py - ay) * ux)

            if side(cx, cy) > 0.35 or side(ex, ey) > 0.35:
                continue

            def proj(px, py):
                return (px - ax) * ux + (py - ay) * uy

            lo, hi = proj(cx, cy), proj(ex, ey)
            if lo > hi:
                lo, hi = hi, lo
            if min(hi, L) - max(lo, 0.0) > 0.5:
                return True
    return False


def _hg_nonfinal_problem(x0, y0, x1, y1, geom, allow_departure):
    """None when this leg stays 0.5 mm inside the outer-wall centerline.

    Every routed leg except the final seam leg has to clear that centerline
    after the 0.4 mm lift. The opening hop may leave the wall, but only while
    lifted clearance is increasing and only until it reaches 0.5 mm. A hop
    that travels along the wall is a wall ride. Later legs have no departure.
    """
    limit = _HEEL_ACCEPT_INSET - 1e-3
    lift_limit = _HEEL_ACCEPT_INSET + _HEEL_EMIT_PAD - 1e-3
    index = geom["index"]
    L = math.hypot(x1 - x0, y1 - y0)

    def at(t):
        return (x0 + (x1 - x0) * t, y0 + (y1 - y0) * t)

    def lifted_d(px, py):
        return _hg_dist_point_index(px, py + LIFT_MM, index)

    if L < 1e-9:
        if allow_departure:
            return None
        if not _hg_strict_inside(x0, y0, geom):
            return "outside"
        ud0 = _hg_dist_point_index(x0, y0, index)
        if ud0 + 1e-3 < _HEEL_ACCEPT_INSET:
            return "wall_ride"
        if lifted_d(x0, y0) + 1e-3 < _HEEL_ACCEPT_INSET + _HEEL_EMIT_PAD:
            return "wall_ride"
        return None
    lifted_close = _hg_segment_closer_than(x0, y0 + LIFT_MM, x1, y1 + LIFT_MM, index, lift_limit)
    unlifted_close = _hg_segment_closer_than(x0, y0, x1, y1, index, limit)
    cut = 0.0
    if allow_departure and (lifted_close or unlifted_close):
        n = max(4, int(math.ceil(L / 0.25)))
        prev_u = _hg_dist_point_index(x0, y0, index)
        cut = None
        for i in range(1, n + 1):
            t = i / float(n)
            px, py = at(t)
            d = lifted_d(px, py)
            ud = _hg_dist_point_index(px, py, index)
            # The emitted leg is lifted. More than 0.01 mm outside that
            # outline is the box failure, including on the opening hop.
            if (not _hg_strict_inside(px, py + LIFT_MM, geom)) and t * L > _HEEL_OUT_LEN:
                return "outside"
            outside = (not _hg_strict_inside(px, py, geom)) and ud > _HEEL_BOUND_EPS
            if outside:
                return "outside"
            # Back toward the wall we left is a ride. Lifted clearance can
            # fall in a narrow channel once the far wall is the closer one.
            if prev_u + 1e-3 < _HEEL_ACCEPT_INSET and ud < prev_u - 0.05:
                return "wall_ride"
            prev_u = ud
            # Arrival is 0.5 mm inside after the lift, not merely unlifted.
            if ud + 1e-3 >= _HEEL_ACCEPT_INSET and d >= lift_limit:
                cut = t
                break
        # Deep enough for this channel, and not a long skim along the wall.
        if cut is None or cut * L > 3.0:
            return "wall_ride"
    qx, qy = at(cut)
    if _hg_span_ok(qx, qy, x1, y1, geom, "mid"):
        return None
    if _hg_strict_inside(qx, qy, geom) and _hg_strict_inside(x1, y1, geom):
        if lifted_close or unlifted_close:
            return "wall_ride"
        return "thin_region"
    return "outside"


def _hg_route_problem(x0, y0, pts, geom):
    """None when the route may be emitted, else a reject reason."""
    if not pts:
        return "no_valid_route"
    prev = (x0, y0)
    last = len(pts) - 1
    for i, p in enumerate(pts):
        if i == last:
            if not _hg_span_ok(prev[0], prev[1], p[0], p[1], geom, "exit"):
                return "outside_approach"
        else:
            # The final seam leg is the only one allowed inside the outer 0.5 mm.
            reason = _hg_nonfinal_problem(
                prev[0], prev[1], p[0], p[1], geom, allow_departure=(i == 0))
            if reason:
                return reason
        # Emitted travels are lifted. The box test covers every leg, including
        # the one before the seam drop. The seam tip has no extra exemption.
        lifted = _hg_map3_outside_len(
            prev[0], prev[1] + LIFT_MM, p[0], p[1] + LIFT_MM, geom)
        if lifted > _HEEL_OUT_LEN:
            if i >= last - 1:
                return "outside_approach"
            return "outside"
        prev = p
    if _hg_route_reverses(x0, y0, pts):
        return "reversal"
    return None


def _hg_route_accept(x0, y0, pts, geom):
    """Every non-seam leg stays 0.5 mm inside the outer wall. The seam leg may approach."""
    return _hg_route_problem(x0, y0, pts, geom) is None


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


def _hg_ray_width(px, py, nx, ny, geom, reach):
    """Distance along (nx, ny) to the next wall. ``reach`` when the ray stays inside."""
    x1 = px + nx * reach
    y1 = py + ny * reach
    ts = _hg_cross_ts(px, py, x1, y1, geom["index"], end_eps=1e-4)
    hits = [t * reach for t in ts if t * reach > 0.05]
    if not hits:
        return reach
    return min(hits)


def _hg_deeper_clears(px, py, geom):
    """True when a step further inside still keeps 0.5 mm after the lift.

    A channel narrower than that has no deeper inset. The 0.5 mm offset is
    the path, and the leg is kept.
    """
    index = geom["index"]
    lifted = _hg_dist_point_index(px, py + LIFT_MM, index)
    if lifted + 1e-3 >= _HEEL_ACCEPT_INSET:
        return True
    _bd, qx, qy = _hg_nearest_boundary(px, py + LIFT_MM, index)
    vx, vy = px - qx, (py + LIFT_MM) - qy
    span = math.hypot(vx, vy)
    if span < 1e-6:
        return False
    need = (_HEEL_ACCEPT_INSET - lifted) + 0.05
    sx = px + vx / span * need
    sy = py + vy / span * need
    if not _hg_strict_inside(sx, sy, geom):
        return False
    if _hg_dist_point_index(sx, sy, index) + 1e-3 < _HEEL_ACCEPT_INSET:
        return False
    if _hg_dist_point_index(sx, sy + LIFT_MM, index) + 1e-3 < _HEEL_ACCEPT_INSET:
        return False
    return True


def _hg_prime_anchor(px, py, geom):
    """A point at least 0.5 mm inside, for a prime that would otherwise sit on the wall.

    None when the start is already clear. The caller adds the short leg.
    """
    index = geom["index"]

    def lifted(qx, qy):
        return _hg_dist_point_index(qx, qy + LIFT_MM, index)

    def plain(qx, qy):
        return _hg_dist_point_index(qx, qy, index)

    if lifted(px, py) + 1e-3 >= _HEEL_ACCEPT_INSET and plain(px, py) + 1e-3 >= _HEEL_ACCEPT_INSET:
        return None
    _bd, qx, qy = _hg_nearest_boundary(px, py + LIFT_MM, index)
    vx, vy = px - qx, (py + LIFT_MM) - qy
    span = math.hypot(vx, vy)
    dirs = []
    if span > 1e-6:
        dirs.append((vx / span, vy / span))
    dirs.append((0.0, 1.0))
    dirs.append((0.0, -1.0))
    for sh in geom["shells"]:
        rx, ry = _hg_rep_inside(sh["outer"])
        dx, dy = rx - px, ry - py
        mag = math.hypot(dx, dy)
        if mag > 1e-6:
            dirs.append((dx / mag, dy / mag))
            break
    best = None
    for dx, dy in dirs:
        for step in (0.05, 0.10, 0.12, 0.15, 0.2, 0.35, 0.5, 0.8, 1.2, 1.8, 2.6):
            sx, sy = px + dx * step, py + dy * step
            if not _hg_strict_inside(sx, sy, geom):
                break
            if lifted(sx, sy) + 1e-3 < _HEEL_ACCEPT_INSET or plain(sx, sy) + 1e-3 < _HEEL_ACCEPT_INSET:
                continue
            if best is None or step < best[0]:
                best = (step, sx, sy)
            break
    if best is None:
        return None
    return (best[1], best[2])


def _hg_subdivide_closed(pts, step=2.0):
    """Split long edges so a pinch does not throw away the whole segment."""
    if len(pts) < 3 or step <= 0.0:
        return list(pts)
    out = []
    n = len(pts)
    for i in range(n):
        ax, ay = pts[i]
        bx, by = pts[(i + 1) % n]
        out.append((ax, ay))
        el = math.hypot(bx - ax, by - ay)
        pieces = int(el // step)
        for s in range(1, pieces + 1):
            dist = s * step
            if dist >= el - 0.05:
                break
            t = dist / el
            out.append((ax + (bx - ax) * t, ay + (by - ay) * t))
    return out


def _hg_edge_inset(ax, ay, bx, by, nx, ny, desired, geom):
    """Inset for one wall edge. None when the material is under 1.0 mm.

    The parallel inset is used where it fits. A narrower channel, still at
    least 1.0 mm, is clamped to the 0.5 mm offset so the far wall is not hit.
    """
    if abs(nx) < 1e-12 and abs(ny) < 1e-12:
        return None, (nx, ny)
    reach = desired + _HEEL_ACCEPT_INSET + 0.25
    mx, my = 0.5 * (ax + bx), 0.5 * (ay + by)
    if not _hg_strict_inside(mx + nx * 0.2, my + ny * 0.2, geom):
        nx, ny = -nx, -ny
    width = _hg_ray_width(mx, my, nx, ny, geom, reach)
    room = width - _HEEL_ACCEPT_INSET
    if room < _HEEL_ACCEPT_INSET - 1e-3:
        return None, (nx, ny)
    if room + 1e-9 >= desired:
        return desired, (nx, ny)
    # 0.5 mm off this wall, and not past the far wall.
    inset = min(room, _HEEL_ACCEPT_INSET + _HEEL_EMIT_PAD)
    return inset, (nx, ny)


def _hg_offset_corner(vx, vy, n0, d0, n1, d1, dir0, dir1):
    """Intersection of the two offset edges. A long spike falls back to the miter."""
    p0x, p0y = vx + n0[0] * d0, vy + n0[1] * d0
    p1x, p1y = vx + n1[0] * d1, vy + n1[1] * d1
    den = dir0[0] * dir1[1] - dir0[1] * dir1[0]
    if abs(den) < 1e-12:
        return _hg_miter_point(vx, vy, n0, n1, min(d0, d1))
    s = ((p1x - p0x) * dir1[1] - (p1y - p0y) * dir1[0]) / den
    ix, iy = p0x + s * dir0[0], p0y + s * dir0[1]
    if math.hypot(ix - vx, iy - vy) > 8.0 * max(d0, d1):
        return _hg_miter_point(vx, vy, n0, n1, min(d0, d1))
    return (ix, iy)


def _hg_clearance_offset(pts, desired, geom, left):
    """Closed walk at the deep inset, clamped to 0.5 mm where the wall is narrow.

    Edges narrower than 1.0 mm are skipped. A shell that can hold the full
    inset everywhere is left to the parallel offset, so this does not add a
    second copy of a wide ring.
    """
    if len(pts) < 3 or desired <= 1e-6:
        return []
    pts = _hg_subdivide_closed(pts, 2.0)
    norms = _hg_edge_normals(pts, True, left)
    n = len(pts)
    edge_d = []
    edge_n = []
    limited = False
    for i in range(n):
        ax, ay = pts[i]
        bx, by = pts[(i + 1) % n]
        inset, normal = _hg_edge_inset(ax, ay, bx, by, norms[i][0], norms[i][1], desired, geom)
        edge_d.append(inset)
        edge_n.append(normal)
        if inset is not None and inset < desired - 1e-3:
            limited = True
    if not limited:
        return []
    out = []
    good = []
    for i in range(n):
        prev = (i - 1) % n
        d0, d1 = edge_d[prev], edge_d[i]
        if d0 is None or d1 is None:
            good.append(False)
            out.append(pts[i])
            continue
        dir0 = (pts[i][0] - pts[prev][0], pts[i][1] - pts[prev][1])
        nxt = (i + 1) % n
        dir1 = (pts[nxt][0] - pts[i][0], pts[nxt][1] - pts[i][1])
        out.append(_hg_offset_corner(pts[i][0], pts[i][1], edge_n[prev], d0, edge_n[i], d1, dir0, dir1))
        good.append(True)
    if not any(good):
        return []
    if all(good):
        return [(out, True)]
    chains = []

    def emit(run):
        run = _hg_dedup(run)
        if len(run) >= 2:
            chains.append((run, False))

    start = good.index(False)
    run = []
    for k in range(n):
        i = (start + 1 + k) % n
        nxt = (i + 1) % n
        if good[i] and good[nxt]:
            if not run:
                run = [out[i]]
            run.append(out[nxt])
        elif run:
            emit(run)
            run = []
    if run:
        emit(run)
    return chains


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


def _hg_shell_walk_rings(shell, dist, geom, shell_i):
    """Deep inset where the wall is wide, and the 0.5 mm offset where it is narrow.

    The parallel inset is what a wide section can hold after the lift. A
    section between 1.0 mm and that depth uses the clearance-limited offset
    instead. Narrower than 1.0 mm, that edge is skipped.
    """
    rings = []

    def add_offset(poly, left):
        if len(poly) < 3 or dist <= 1e-6:
            return
        off = _hg_offset_closed(poly, dist, left=left)
        off = _hg_pull_shallow(off, geom, _HEEL_ACCEPT_INSET + LIFT_MM + _HEEL_EMIT_PAD, True)
        parts = _hg_deep_chains(off, geom, True)
        for part, part_closed in parts:
            rings.append((part, part_closed, shell_i))
        # A closed deep ring already covers this shell. The clamped offset is
        # only for the edges that inset could not keep.
        if len(parts) == 1 and parts[0][1] and len(parts[0][0]) + 2 >= len(poly):
            return
        for part, part_closed in _hg_clearance_offset(poly, dist, geom, left):
            for piece, piece_closed in _hg_deep_chains(part, geom, part_closed):
                rings.append((piece, piece_closed, shell_i))

    add_offset(shell["outer"], _hg_area(shell["outer"]) >= 0.0)
    for hole in shell["holes"]:
        # Expand the hole into the material (opposite of shrinking the hole polygon).
        add_offset(hole, _hg_area(hole) < 0.0)
    return rings


def _hg_make_walk_rings(shells, geom, walk_d):
    """Inset about half an inner bead, clamped to 0.5 mm on narrow edges."""
    if not shells:
        return []
    primary = []
    for i, sh in enumerate(shells):
        primary.extend(_hg_shell_walk_rings(sh, walk_d, geom, i))
    return primary


def _hg_route_cap(layer, heel_max_route):
    """60 mm everywhere, 200 mm on heel layers 355–447.

    An explicit cap above 200 mm is kept. The heel band only raises a
    smaller cap, so other layers stay on the caller's value.
    """
    cap = 60.0 if heel_max_route is None else float(heel_max_route)
    if layer is not None and _HEEL_BAND_LO <= layer <= _HEEL_BAND_HI and cap < _HEEL_BAND_CAP:
        return _HEEL_BAND_CAP
    return cap


def _hg_point_deep(px, py, geom):
    """True when the point and its 0.4 mm lift are both at least 0.5 mm inside."""
    if not _hg_strict_inside(px, py, geom):
        return False
    index = geom["index"]
    if _hg_dist_point_index(px, py, index) + 1e-3 < _HEEL_ACCEPT_INSET:
        return False
    if _hg_dist_point_index(px, py + LIFT_MM, index) + 1e-3 < _HEEL_ACCEPT_INSET + _HEEL_EMIT_PAD - 1e-3:
        return False
    return True


def _hg_lift_departs(x0, y0, geom):
    """True when a pure +Y lift leaves the outline or lands on another wall.

    The lift is the first emitted move. Landing within 0.5 mm of a wall other
    than the one under the nozzle is the same failure as starting outside.
    """
    lx, ly = x0, y0 + LIFT_MM
    if not _hg_strict_inside(lx, ly, geom):
        return True
    if _hg_map3_outside_len(x0, y0, lx, ly, geom) > _HEEL_OUT_LEN:
        return True
    index = geom["index"]
    _d0, qx, qy = _hg_nearest_boundary(x0, y0, index)
    d1, q2x, q2y = _hg_nearest_boundary(lx, ly, index)
    if math.hypot(q2x - qx, q2y - qy) > 1.0 and d1 + 1e-3 < _HEEL_ACCEPT_INSET:
        return True
    return False


def _hg_opening_segment_ok(x0, y0, x1, y1, geom):
    """Direct move from the unlifted start to a lifted inside point.

    Used when the pure +Y lift would leave the outline. The end has to be
    0.5 mm inside, and the segment itself may not leave the outline.
    """
    if _hg_map3_outside_len(x0, y0, x1, y1, geom) > _HEEL_OUT_LEN:
        return False
    index = geom["index"]
    if not _hg_strict_inside(x1, y1, geom):
        return False
    if _hg_dist_point_index(x1, y1, index) + 1e-3 < _HEEL_ACCEPT_INSET:
        return False
    _d0, qx, qy = _hg_nearest_boundary(x0, y0, index)
    d1, q2x, q2y = _hg_nearest_boundary(x1, y1, index)
    if math.hypot(q2x - qx, q2y - qy) > 1.0 and d1 + 1e-3 < _HEEL_ACCEPT_INSET:
        return False
    return True


def _hg_top_envelope(geom):
    """Max outer-wall Y in 1 mm X bins. The top silhouette of this layer."""
    env = {}
    ymin = 1e18
    for sh in geom.get("shells") or []:
        ymin = min(ymin, sh["bbox"][1])
        ring = sh["outer"]
        n = len(ring)
        for i in range(n):
            ax, ay = ring[i]
            bx, by = ring[(i + 1) % n]
            el = math.hypot(bx - ax, by - ay)
            steps = max(1, int(math.ceil(el / 0.5)))
            for s in range(steps + 1):
                t = s / float(steps)
                x = ax + (bx - ax) * t
                y = ay + (by - ay) * t
                b = int(math.floor(x))
                if y > env.get(b, -1e18):
                    env[b] = y
    if not env:
        return {}, 0.0
    return env, ymin


def _hg_top_cross_count(x0, y0, x1, y1, geom):
    """How many times this segment crosses the upper outer-wall silhouette.

    Belt-side intersections are not the top. A point counts when it meets the
    boundary at least 2 mm above the layer's lowest Y and within 2 mm of the
    upper envelope.
    """
    if not geom or not geom.get("shells"):
        return 0
    env, ymin = _hg_top_envelope(geom)
    if not env:
        return 0
    ts = _hg_cross_ts(x0, y0, x1, y1, geom["index"], end_eps=1e-3)
    count = 0
    dx, dy = x1 - x0, y1 - y0
    for t in ts:
        qx = x0 + dx * t
        qy = y0 + dy * t
        if qy <= ymin + OPEN_BELT_BAND:
            continue
        b = int(math.floor(qx))
        top = max(env.get(b - 1, -1e18), env.get(b, -1e18), env.get(b + 1, -1e18))
        if top < -1e17:
            continue
        if qy >= top - TOP_BAND:
            count += 1
    return count


def _hg_inset_ring(ring, geom, dist):
    """Inward offset, subdivided so a finger is followed instead of chorded.

    Points that are not 0.5 mm inside after the lift are gaps. An arc may
    not jump a gap.
    """
    if len(ring) < 3:
        return []
    area = _hg_area(ring)
    left = area >= 0.0
    pts = _hg_subdivide_closed(ring, 0.6)
    inset = _hg_offset_closed(pts, dist, left)
    if len(inset) < 3:
        return []
    inset = _hg_pull_shallow(inset, geom, _HEEL_ACCEPT_INSET + _HEEL_EMIT_PAD, True)
    out = []
    for px, py in inset:
        if _hg_point_deep(px, py, geom):
            out.append((px, py))
        else:
            out.append(None)
    return out


def _hg_wall_approach(x0, y0, tx, ty, geom, cap):
    """Route along a 0.5 mm inset of the wall, then drop to the seam.

    The straight approach is what ``outside_approach`` rejects. Snapping the
    end to the nearest deep point along the wall, and walking there, keeps
    the connector inside. None when no such walk fits under ``cap``.
    """
    if not geom or not geom.get("shells") or cap is None:
        return None

    def nearest(samples, px, py):
        best = None
        for i, p in enumerate(samples):
            if p is None:
                continue
            d = math.hypot(p[0] - px, p[1] - py)
            if best is None or d < best[0]:
                best = (d, i)
        return best

    def arc(samples, i0, i1, step):
        n = len(samples)
        pts = []
        i = i0
        length = 0.0
        prev = samples[i0]
        for _ in range(n):
            if i == i1:
                return pts, length
            i = (i + step) % n
            p = samples[i]
            if p is None:
                return None
            length += math.hypot(p[0] - prev[0], p[1] - prev[1])
            if length > cap:
                return None
            pts.append(p)
            prev = p
        return None

    best = None
    rings = []
    for sh in geom["shells"]:
        rings.append(sh["outer"])
        rings.extend(sh["holes"])
    for ring in rings:
        for dist in (0.55, 0.90):
            samples = _hg_inset_ring(ring, geom, dist)
            if not samples or all(p is None for p in samples):
                continue
            ns = nearest(samples, x0, y0)
            ne = nearest(samples, tx, ty)
            if ns is None or ne is None:
                continue
            # A snap more than a few millimetres off the endpoint is a different wall.
            if ns[0] > 4.0 or ne[0] > 4.0:
                continue
            i0, i1 = ns[1], ne[1]
            cands = []
            if i0 == i1:
                cands.append(([], 0.0))
            else:
                for step in (1, -1):
                    got = arc(samples, i0, i1, step)
                    if got is not None:
                        cands.append(got)
            for pts, alen in cands:
                raw = [samples[i0]] + list(pts)
                if math.hypot(raw[-1][0] - tx, raw[-1][1] - ty) > 1e-4:
                    raw.append((tx, ty))
                # Drop a repeated start so the opening hop is one leg.
                seq = []
                prev = (x0, y0)
                for p in raw:
                    if math.hypot(p[0] - prev[0], p[1] - prev[1]) > 1e-4:
                        seq.append(p)
                        prev = p
                if not seq:
                    continue
                length = 0.0
                px, py = x0, y0
                for qx, qy in seq:
                    length += math.hypot(qx - px, qy - py)
                    px, py = qx, qy
                if length > cap + 1e-6:
                    continue
                if _hg_route_problem(x0, y0, seq, geom):
                    continue
                if best is None or length < best[0]:
                    best = (length, seq)
    if best is None:
        return None
    return best[1], best[0]


def _hg_mode_width(widths, default):
    if not widths:
        return default
    bins = {}
    for w in widths:
        k = round(float(w), 3)
        bins[k] = bins.get(k, 0) + 1
    return max(bins.items(), key=lambda kv: kv[1])[0]


def collapse_seam_travels(text, TF, seam_map=None, heel_max_route=60.0):
    """Replace every seam travel by lift + move + lower.

    A belt-side seam travel is a heel crossing when the straight segment leaves
    the layer's outer-wall polygon or comes within 0.7 mm of its boundary.
    The walk is the inner-wall centerline offset by half the inner width
    toward the part interior, never toward the outer wall. An offset segment
    that collapses is skipped. Every routed leg except the final seam leg
    stays at least 0.5 mm inside the outer-wall centerline. The opening hop
    may leave the wall only while its clearance is increasing. The inset walk
    ends at the inset point nearest the seam's X, and the last leg drops
    straight in Y to the seam. The retract prime is emitted at that last
    interior point, and the seam leg carries no E. Travels inside a
    ``;COMB_LIFT`` block use the same test. A route longer than
    ``heel_max_route`` (default 60 mm) is rejected and logged, not used.
    Layers 355–447 use a 200 mm cap instead; a long heel route still
    retracts before the walk and primes on the last interior point.
    Every emitted leg is tested at the lifted height: more than 0.01 mm
    outside a 0.001 mm outline buffer rejects the travel. The seam tip has
    no further exemption. A route through a region thinner than 1 mm, a
    route that reverses along itself, or a route whose start and seam are
    not on one loop is rejected. A reject keeps the straight move, with a
    retract before it and the prime at the seam. Rejects are listed at the end.
    """
    import sys

    HEEL_E = 1.5
    HEEL_EF = 1800.0
    HEEL_OUTER_NEAR = 0.7
    HEEL_DEF_INNER_W = 0.45
    HEEL_JOIN_TOL = 0.05
    _seam = seam_map or {}
    if heel_max_route is None:
        heel_max_route = 60.0

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
        """Offset the inner centerline by ``half`` toward the part interior.

        The side that moves closer to the outer wall is never used. An empty
        result means both sides collapsed toward that wall.
        """
        if half <= 1e-9 or len(pts) < 2:
            return list(pts)
        base = _mean_clearance(pts, outer_segs)

        def _keep(poly):
            if not poly:
                return False
            return _mean_clearance(poly, outer_segs) + 1e-3 >= base

        if closed and len(pts) >= 3:
            area = _signed_area(pts)

            def build(left):
                return _clamp_pts(_offset_closed(pts, half, left=left), pts, True, half)

            inward = build(area >= 0.0)
            outward = build(area < 0.0)
            # Interior of the inner loop is the part. A side that leaves that
            # loop, or that moves toward the outer wall, is discarded.
            ranked = []
            for poly in (inward, outward):
                if not _keep(poly):
                    continue
                inside = sum(1 for p in poly if _point_in_poly(p[0], p[1], pts))
                ranked.append((inside, _mean_clearance(poly, outer_segs), poly))
            if not ranked:
                return []
            ranked.sort(key=lambda item: (item[0], item[1]))
            return ranked[-1][2]
        left = _clamp_pts(_offset_open(pts, half, True), pts, False, half)
        right = _clamp_pts(_offset_open(pts, half, False), pts, False, half)
        cands = []
        for poly in (left, right):
            if _keep(poly):
                cands.append((_mean_clearance(poly, outer_segs), poly))
        if not cands:
            return []
        # Farther from the outer wall is the part interior.
        cands.sort(key=lambda item: item[0])
        return cands[-1][1]

    def _inset_chains(pts, half, closed, outer_poly, outer_segs, geom=None):
        """Drop offset edges that collapsed or fell inside the outer 0.5 mm.

        An edge already 0.5 mm inside is kept when the channel cannot hold a
        deeper inset. Under 1.0 mm of material the edge is still skipped.
        """
        off = _inset_loop(pts, half, closed, outer_poly, outer_segs)
        off = _dedup(off)
        n = len(off)
        if n < 2:
            return []
        limit = n if closed else n - 1
        aligned = len(off) == len(pts)

        def _clr(px, py):
            return _mean_clearance([(px, py)], outer_segs)

        good = []
        for i in range(limit):
            ax, ay = off[i]
            bx, by = off[(i + 1) % n]
            el = math.hypot(bx - ax, by - ay)
            mid_x, mid_y = 0.5 * (ax + bx), 0.5 * (ay + by)
            plain = _clr(mid_x, mid_y)
            lifted_c = _clr(mid_x, mid_y + LIFT_MM)
            lift_ok = lifted_c + 1e-3 >= _HEEL_ACCEPT_INSET + _HEEL_EMIT_PAD
            plain_ok = plain + 1e-3 >= _HEEL_ACCEPT_INSET
            # Wide material still has to clear the wall after the lift. A
            # channel that cannot take a deeper step keeps the 0.5 mm offset.
            narrow_ok = (
                plain_ok
                and geom is not None
                and not _hg_deeper_clears(mid_x, mid_y, geom)
            )
            ok = el >= 1e-4 and plain_ok and (lift_ok or narrow_ok)
            if ok and aligned:
                sx, sy = pts[i]
                tx, ty = pts[(i + 1) % len(pts)]
                dot = (bx - ax) * (tx - sx) + (by - ay) * (ty - sy)
                src_c = _clr(0.5 * (sx + tx), 0.5 * (sy + ty))
                mid_c = _clr(0.5 * (ax + bx), 0.5 * (ay + by))
                # Reversed or shifted toward the outer wall: the offset collapsed.
                if dot < 0.0 or mid_c + 0.05 < src_c:
                    ok = False
            good.append(ok)
        if not any(good):
            return []
        chains = []

        def emit(run):
            run = _dedup(run)
            if len(run) >= 2:
                chains.append((run, False))

        if closed and all(good):
            return [(off, True)]
        if not closed:
            run = []
            for i, ok in enumerate(good):
                if ok:
                    if not run:
                        run = [off[i]]
                    run.append(off[i + 1])
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
                    run = [off[i]]
                run.append(off[(i + 1) % n])
            elif run:
                emit(run)
                run = []
        if run:
            emit(run)
        return chains

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
        # Same 0.5 mm outline samples as an emitted leg. A chord that leaves
        # the polygon between its ends is outside even when both ends look in.
        if _hg_samples_outside(x0, y0, x1, y1, geom):
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
            steps = max(1, int(math.ceil(L / 2.0)))
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

    def _best_on_poly(x0, y0, tx, ty, poly, closed, geom, why, nearest_exit=False):
        if geom is None or len(poly) < 2:
            return None
        # A dropped edge leaves the two inset ends a fraction of a millimetre
        # apart. Treat that as a loop so the shorter direction is still there.
        if (
            not closed
            and len(poly) >= 3
            and 1e-4 < math.hypot(poly[0][0] - poly[-1][0], poly[0][1] - poly[-1][1]) <= 1.0
        ):
            closed = True
        ds = _attach_point(x0, y0, poly, closed, geom, "entry")
        if ds is None:
            return None
        # The inset walk ends at the point nearest the seam's X so the last
        # leg drops in Y. If that drop leaves the outline, try the previous
        # exit. A diagonal that still fails the box test is not emitted.
        exits = list(_x_nearest_hits(poly, tx, ty, closed))
        if nearest_exit:
            old = _nearest_on_poly(poly, tx, ty, closed)
        else:
            old = _attach_point(tx, ty, poly, closed, geom, "exit")
        if old is not None:
            exits.append(old)
        # The belt-side crossings. A wavy ring can cross the seam X many
        # times; the nearest ones are the short drop, and the rest are the
        # long way around.
        exits.sort(key=lambda de: de[0])
        if len(exits) > 4:
            exits = exits[:4]
        _d0, si, ts, qsx, qsy = ds
        qs = (qsx, qsy)
        # Shortest chain first. The first one that stays inside is the route.
        # Later chains are longer, so they cannot win.
        scored = []
        for de in exits:
            _d1, ei, te, qex, qey = de
            qe = (qex, qey)
            for path in _paths_between(poly, closed, si, ts, ei, te, qe):
                pts = _assemble(x0, y0, tx, ty, qs, path)
                scored.append((_chain_len(x0, y0, pts), pts))
            # An open chain's usable end can sit next to the start while its
            # index is the far end of the chain. Hop there instead of walking
            # the long way around the broken loop.
            if math.hypot(qex - x0, qey - y0) <= _HEEL_APPROACH_MAX + 0.05:
                direct = _assemble(x0, y0, tx, ty, qe, [])
                scored.append((_chain_len(x0, y0, direct), direct))
        scored.sort(key=lambda item: item[0])
        # The shorter direction first. A chain longer than the cap is only
        # checked when nothing shorter stayed inside.
        over = None
        for dist, pts in scored:
            if dist > heel_max_route + 1e-6:
                if over is None:
                    over = (dist, pts)
                continue
            reason = _hg_route_problem(x0, y0, pts, geom)
            if reason:
                why.append(reason)
                continue
            return (dist, pts)
        if over is not None:
            dist, pts = over
            reason = _hg_route_problem(x0, y0, pts, geom)
            if reason:
                why.append(reason)
            else:
                return (dist, pts)
        return None

    def _route_waypoints(x0, y0, tx, ty, lctx):
        geom = None if not lctx else lctx.get("geom")
        shells = [] if not lctx else (lctx.get("shells") or [])
        if not shells or geom is None:
            return None, "no_closed_outer_loop", 0.0
        common = _hg_common_shells(x0, y0, tx, ty, shells)
        if not common:
            # Start and seam are not on one loop. Do not jump between shells.
            return None, "different_shells", 0.0
        common_set = set(common)
        why = []
        straight = math.hypot(tx - x0, ty - y0)

        def _prime_inside(px, py):
            index = geom["index"]
            return (
                _hg_strict_inside(px, py, geom)
                and _hg_dist_point_index(px, py, index) + 1e-3 >= _HEEL_ACCEPT_INSET
                and _hg_dist_point_index(px, py + LIFT_MM, index) + 1e-3 >= _HEEL_ACCEPT_INSET
            )

        # A short hop that already stays inside is the final approach. A long
        # chord, or one that leaves the part, is not used. The prime lands on
        # the start of that hop, so the start has to be 0.5 mm inside.
        if straight <= _HEEL_APPROACH_MAX + 0.05:
            hop = _hg_route_problem(x0, y0, [(tx, ty)], geom)
            if hop is None and _prime_inside(x0, y0):
                return [(tx, ty)], None, straight
            if hop is not None:
                why.append(hop)

        def _on_common(poly):
            step = max(1, len(poly) // 4)
            for px, py in poly[::step]:
                for i in common:
                    if _hg_on_shell(px, py, shells[i]):
                        return True
            return False

        loops = lctx.get("inner_loops") or []
        if loops:
            scored = []
            for poly, closed in loops:
                if len(poly) < 2 or not _on_common(poly):
                    continue
                ds = _nearest_on_poly(poly, x0, y0, closed)
                de = _nearest_on_poly(poly, tx, ty, closed)
                if ds is None or de is None:
                    continue
                scored.append((ds[0] + de[0], poly, closed))
            scored.sort(key=lambda item: item[0])
            for _score, poly, closed in scored:
                hit = _best_on_poly(x0, y0, tx, ty, poly, closed, geom, why)
                if hit is not None:
                    return hit[1], None, hit[0]

        if not lctx.get("walk_ready"):
            # 0.5 mm after the lift. Half an inner bead is the other candidate;
            # a full 1 mm bead inset leaves the belt lip and the route is lost.
            half_w = 0.5 * (lctx.get("inner_width") or HEEL_DEF_INNER_W)
            walk_d = max(half_w, _HEEL_ACCEPT_INSET) + LIFT_MM + _HEEL_EMIT_PAD
            primary = _hg_make_walk_rings(shells, geom, walk_d)
            lctx["walk_rings"] = primary
            lctx["walk_ready"] = True

        def _final_len(pts):
            if len(pts) >= 2:
                return math.hypot(pts[-1][0] - pts[-2][0], pts[-1][1] - pts[-2][1])
            if pts:
                return math.hypot(pts[-1][0] - x0, pts[-1][1] - y0)
            return 1e18

        # Both inset distances. The exit is the inset point nearest the seam's
        # X, so the last leg drops in Y. A diagonal corner cut is not used.
        best = None
        for rings in (lctx.get("walk_rings") or [],):
            for poly, closed, shell_i in rings:
                if shell_i not in common_set:
                    continue
                hit = _best_on_poly(x0, y0, tx, ty, poly, closed, geom, why, nearest_exit=True)
                if hit is None:
                    continue
                key = (_final_len(hit[1]), hit[0])
                if best is None or key < best[0]:
                    best = (key, hit)
        if best is not None:
            return best[1][1], None, best[1][0]
        thin = True
        for i in common:
            if _hg_shell_thick(shells[i]["outer"], geom):
                thin = False
                break
        if thin:
            return None, "thin_region", 0.0
        if "reversal" in why:
            return None, "reversal", 0.0
        if "outside_approach" in why:
            return None, "outside_approach", 0.0
        if "wall_ride" in why:
            return None, "wall_ride", 0.0
        # Closed outer exists, but every connector leaves the part or doubles back.
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
            shells = _hg_shells_from_loops(outer_loops)
            index = _hg_build_edge_index(shells)
            geom = {"shells": shells, "index": index}
            inset_loops = []
            for pts, closed, ww in _chain_polylines(c["inner_segs"], HEEL_JOIN_TOL):
                half = 0.5 * (ww if ww > 1e-6 else c.get("inner_width", HEEL_DEF_INNER_W))
                for op, op_closed in _inset_chains(pts, half, closed, outer_poly, c["outer_segs"], geom):
                    op = _dedup(op)
                    if len(op) >= 2:
                        inset_loops.append((op, bool(op_closed and len(op) >= 3)))
            c["inner_loops"] = inset_loops
            widths = [seg[2] for seg in c["inner_segs"]]
            c["inner_width"] = _hg_mode_width(widths, HEEL_DEF_INNER_W)
            c["shells"] = shells
            c["geom"] = geom
            c["bbox"] = index["bbox"]
        return ctx

    lines = text.splitlines(keepends=True)
    layer_ctx = _build_layer_ctx(lines)
    out = []
    x = y = 0.0; z = None; f = None
    first_lc = next((k for k, l in enumerate(lines) if l.startswith(";LAYER_CHANGE")), len(lines))
    region = []      # (line_index_in_lines, line)
    seam_from = False
    stats = {"seam_travels": 0, "retracts": 0, "nonbelt_plain": 0, "heelroute_routed": 0, "heelroute_fallback": 0, "heelroute_rejected": 0}
    reject_log = []
    flag_log = []
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

    def _emit_routed_block(x0, y0, waypts, ty, tz, tf, zmove, combine_lift=False):
        """Retract, walk, prime on the last interior point, then the seam leg with no E.

        ``combine_lift`` skips the pure +Y lift. The first travel rises while
        it moves to the first inside point, so a lift that would leave the
        outline is not emitted. The retract and the prime stay.
        """
        blk = [";PP_HEELROUTE_START\n", f"G1 E-{HEEL_E:g} F{HEEL_EF:g}\n"]
        if not combine_lift:
            blk.append(f"G1 Y{y0 + LIFT_MM:.3f} F{TF:g} ;lift\n")
        for wx, wy in waypts[:-1]:
            blk.append(f"G1 X{wx:.3f} Y{wy + LIFT_MM:.3f} F{TF:g} ;heel route\n")
        blk.append(f"G1 E{HEEL_E:g} F{HEEL_EF:g}\n")
        wz = waypts[-1]
        zs = f" Z{tz:.3f}" if zmove else ""
        blk.append(f"G1 X{wz[0]:.3f} Y{wz[1] + LIFT_MM:.3f}{zs} F{TF:g} ;heel route seam\n")
        blk.append(f"G1 Y{ty:.3f} F{TF:g} ;lower\n")
        if tf is not None:
            blk.append(f"G1 F{tf:g}\n")
        blk.append(";PP_HEELROUTE_END\n")
        return blk

    def _emit_reject_block(x0, y0, tx, ty, tz, tf, zmove):
        """Straight move kept. Retract before it, prime after it at the seam."""
        body = _emit_direct_seam_body(x0, y0, tx, ty, tz, tf, zmove)
        prime = f"G1 E{HEEL_E:g} F{HEEL_EF:g}\n"
        if body and body[-1].startswith("G1 F"):
            body = body[:-1] + [prime, body[-1]]
        else:
            body.append(prime)
        return [";PP_HEELROUTE_REJECT\n", f"G1 E-{HEEL_E:g} F{HEEL_EF:g}\n"] + body

    def _comb_travel_keys(reg):
        """Uncommented XY travels after ;COMB_LIFT until the comb unretract."""
        active = False
        keys = []
        for k, (idx, l) in enumerate(reg):
            if idx <= first_lc:
                continue
            if l.startswith(";COMB_LIFT"):
                active = True
                continue
            if not active:
                continue
            if l.startswith((";C_SEAM", ";C seam", ";LAYER_CHANGE", ";PP_", ";WIPE")):
                active = False
                continue
            if _is_move(l) and (_w(l, "E") or 0) > 0:
                active = False
                continue
            if _is_xy_travel(l) and ";" not in l:
                keys.append(k)
        return set(keys)

    def _apply_heel(x0, y0, tx, ty, tz, tf, zmove, src_line=0):
        """Route one crossing. ``plain`` means it is not a heel crossing."""
        lyr = pos_layer if pos_layer >= 0 else layer
        lctx = layer_ctx.get(lyr)
        qualify = _qualify_heelroute(lyr, x0, y0, tx, ty, lctx)
        # A move flushed at the start of a layer still belongs to that outline.
        if layer != lyr:
            qualify = qualify or _qualify_heelroute(layer, x0, y0, tx, ty, layer_ctx.get(layer))
        if not qualify:
            return "plain", []
        waypts = None
        fail = None
        rlen = 0.0
        route_lyr = lyr
        order = []
        for cand in (lyr, layer):
            if cand >= 0 and abs(cand - layer) <= 1 and cand not in order:
                order.append(cand)
        saw_shell = False
        rank = {
            "different_shells": 1,
            "no_valid_route": 2,
            "outside": 3,
            "wall_ride": 3,
            "outside_approach": 4,
            "thin_region": 5,
            "reversal": 6,
        }
        for cand in order:
            wpts, reason, rdist = _route_waypoints(x0, y0, tx, ty, layer_ctx.get(cand))
            if wpts is not None and cand != layer:
                octx = layer_ctx.get(layer)
                if octx and octx.get("shells"):
                    problem = _hg_route_problem(x0, y0, wpts, octx["geom"])
                    if problem:
                        wpts = None
                        reason = problem
            if wpts is not None:
                waypts, fail, rlen, route_lyr = wpts, None, rdist, cand
                break
            if reason == "no_closed_outer_loop":
                if not saw_shell:
                    fail = reason
            else:
                saw_shell = True
                if fail is None or rank.get(reason, 0) >= rank.get(fail, 0):
                    fail = reason
        # The prime is the last interior point, lifted. It has to be 0.5 mm
        # inside the printed layer's outer wall, not only the layer the route
        # was searched on. A one-point hop primes at the start.
        report_layer = layer if layer >= 0 else lyr

        def _layer_geoms(*cands):
            geoms = []
            for cand in cands:
                cctx = layer_ctx.get(cand)
                if not cctx:
                    continue
                geom = cctx.get("geom")
                if geom and geom.get("shells") and geom not in geoms:
                    geoms.append(geom)
            return geoms

        def _prime_ok_geom(px, py, geom):
            index = geom["index"]
            return (
                _hg_strict_inside(px, py, geom)
                and _hg_dist_point_index(px, py, index) + 1e-3 >= _HEEL_ACCEPT_INSET
                and _hg_dist_point_index(px, py + LIFT_MM, index) + 1e-3 >= _HEEL_ACCEPT_INSET
            )

        tried_wall = False

        def _try_wall():
            """Snap a failed approach onto a point 0.5 mm inside and walk the wall."""
            nonlocal waypts, fail, rlen, route_lyr, tried_wall
            if tried_wall or waypts is not None:
                return
            # Snap an outside approach on any layer. Shell jumps and dead
            # routes are only retried in the heel, where a miss is flagged.
            in_heel = _HEEL_BAND_LO <= report_layer <= _HEEL_BAND_HI
            if fail != "outside_approach" and not (
                in_heel and fail in ("different_shells", "no_valid_route", "wall_ride")
            ):
                return
            tried_wall = True
            for cand in order:
                cctx = layer_ctx.get(cand)
                if not cctx or not cctx.get("shells"):
                    continue
                got = _hg_wall_approach(
                    x0, y0, tx, ty, cctx["geom"], _hg_route_cap(cand, heel_max_route))
                if got is None:
                    continue
                wpts, rdist = got
                if cand != layer:
                    octx = layer_ctx.get(layer)
                    if octx and octx.get("shells") and _hg_route_problem(x0, y0, wpts, octx["geom"]):
                        continue
                waypts, fail, rlen, route_lyr = wpts, None, rdist, cand
                return

        def _repair_prime():
            nonlocal waypts, fail, rlen
            if waypts is None:
                return
            geoms = _layer_geoms(route_lyr)
            if not geoms:
                return
            prime_at = waypts[-2] if len(waypts) >= 2 else (x0, y0)
            # The route may have been searched on the previous layer, whose
            # outline is missing the wall the prime is actually under.
            for geom in _layer_geoms(layer):
                if geom in geoms:
                    continue
                lifted = _hg_dist_point_index(
                    prime_at[0], prime_at[1] + LIFT_MM, geom["index"])
                if lifted + 1e-3 < _HEEL_ACCEPT_INSET:
                    geoms.append(geom)

            def _ok(px, py):
                return all(_prime_ok_geom(px, py, g) for g in geoms)

            if _ok(prime_at[0], prime_at[1]):
                return
            trial = None
            for geom in geoms:
                anchor = _hg_prime_anchor(prime_at[0], prime_at[1], geom)
                if anchor is None and len(waypts) == 1:
                    anchor = _hg_prime_anchor(x0, y0, geom)
                if anchor is None or not _ok(anchor[0], anchor[1]):
                    continue
                if len(waypts) == 1:
                    cand_pts = [anchor, waypts[0]]
                else:
                    cand_pts = waypts[:-1] + [anchor, waypts[-1]]
                if any(_hg_route_problem(x0, y0, cand_pts, g) for g in geoms):
                    continue
                trial = cand_pts
                break
            if trial is not None:
                waypts = trial
                rlen = _chain_len(x0, y0, trial)
            else:
                fail = "wall_ride"
                waypts = None

        _try_wall()
        _repair_prime()
        if waypts is None:
            _try_wall()
            _repair_prime()

        route_cap = _hg_route_cap(report_layer, heel_max_route)
        if waypts is not None and rlen > route_cap:
            fail = "over_cap"
            waypts = None

        combine = False
        if waypts is not None:
            geoms = _layer_geoms(route_lyr, layer, lyr)
            fix_lift = any(
                c in _HEEL_LIFT_FIX_LAYERS for c in (report_layer, layer, lyr, route_lyr))
            depart = False
            for geom in geoms:
                if _hg_lift_departs(x0, y0, geom):
                    depart = True
                    break
                if fix_lift and _hg_strict_inside(x0, y0 + LIFT_MM, geom):
                    if _hg_dist_point_index(x0, y0 + LIFT_MM, geom["index"]) + 1e-3 < _HEEL_ACCEPT_INSET:
                        depart = True
                        break
            if depart:
                opened = False
                if len(waypts) >= 2 and geoms:
                    wx, wy = waypts[0]
                    opened = all(
                        _hg_opening_segment_ok(x0, y0, wx, wy + LIFT_MM, g) for g in geoms)
                if opened:
                    combine = True
                elif fix_lift:
                    fail = "outside"
                    waypts = None

        if waypts is not None:
            stats["heelroute_routed"] += 1
            return "routed", _emit_routed_block(
                x0, y0, waypts, ty, tz, tf, zmove, combine_lift=combine)
        if fail == "no_closed_outer_loop":
            stats["heelroute_fallback"] += 1
            blk = _emit_direct_seam_body(x0, y0, tx, ty, tz, tf, zmove)
            fb = [f";PP_HEELROUTE_FALLBACK reason={fail}\n"]
            print(
                f"pp_d heelroute FALLBACK layer={lyr} reason={fail} "
                f"start=({x0:.3f},{y0:.3f}) seam=({tx:.3f},{ty:.3f}) "
                f"straight_crosses=1 "
                f"— no valid inset route; emitting original straight move",
                file=sys.stderr,
            )
            return "fallback", _emit_heel_retract_prime(blk, extra_comments=fb)
        stats["heelroute_rejected"] += 1
        extra = ""
        if fail == "over_cap":
            extra = f" length={rlen:.2f} cap={route_cap:.2f}"
        msg = (
            f"pp_d heelroute REJECT layer={report_layer} reason={fail}{extra} "
            f"start=({x0:.3f},{y0:.3f}) seam=({tx:.3f},{ty:.3f}) "
            f"— kept original move"
        )
        print(msg, file=sys.stderr)
        reject_log.append(msg)
        if _HEEL_BAND_LO <= report_layer <= _HEEL_BAND_HI and fail in (
            "different_shells", "no_valid_route", "outside_approach", "outside", "wall_ride", "over_cap",
        ):
            gctx = layer_ctx.get(report_layer) or layer_ctx.get(lyr)
            geom = gctx.get("geom") if gctx else None
            crosses = _hg_top_cross_count(x0, y0, tx, ty, geom) if geom else 0
            flag = (
                f"pp_d heelroute FLAG layer={report_layer} line={src_line} reason={fail} "
                f"start=({x0:.3f},{y0:.3f}) seam=({tx:.3f},{ty:.3f}) crosses_top={1 if crosses else 0}"
            )
            print(flag, file=sys.stderr)
            flag_log.append(flag)
        return "rejected", _emit_reject_block(x0, y0, tx, ty, tz, tf, zmove)

    def _emit_lines(blk):
        for b_ in blk:
            out.append(b_)
            track(b_)

    def flush(seam):
        nonlocal region
        trav = [k for k, (idx, l) in enumerate(region) if idx > first_lc and _is_xy_travel(l)]
        comb_ks = _comb_travel_keys(region)
        seam_active = False
        last = None
        tx = ty = tz = tf = None
        zmove = False
        if seam and trav:
            stats["seam_travels"] += 1
            last = trav[-1]
            tx, ty, tz, tf = x, y, z, f
            for k, (idx, l) in enumerate(region):
                if _is_move(l):
                    nx, ny, nz, nf = _w(l, "X"), _w(l, "Y"), _w(l, "Z"), _w(l, "F")
                    tx = nx if nx is not None else tx
                    ty = ny if ny is not None else ty
                    tz = nz if nz is not None else tz
                    tf = nf if nf is not None else tf
                if k == last:
                    break
            belt = y <= ymin_of(pos_layer) + BELT_SIDE_BAND or ty <= ymin_of(layer) + BELT_SIDE_BAND
            if not belt:
                stats["nonbelt_plain"] += 1
                stats["seam_travels"] -= 1
                last = None
            else:
                seam_active = True
                zmove = any(_w(region[k][1], "Z") is not None for k in trav)
        if not seam_active and not comb_ks:
            for _, l in region:
                out.append(l)
                track(l)
            region = []
            return

        def _take_comb(l, src_idx):
            """Route a comb crossing. False leaves the caller's byte choice."""
            nx, ny, nz, nf = _w(l, "X"), _w(l, "Y"), _w(l, "Z"), _w(l, "F")
            cx = x if nx is None else nx
            cy = y if ny is None else ny
            if not (y <= ymin_of(pos_layer) + BELT_SIDE_BAND or cy <= ymin_of(layer) + BELT_SIDE_BAND):
                return False
            if math.hypot(cx - x, cy - y) < 1e-3:
                return False
            status, blk = _apply_heel(
                x, y, cx, cy, z if nz is None else nz, f if nf is None else nf, nz is not None,
                src_idx + 1)
            if status == "plain":
                return False
            _emit_lines(blk)
            return True

        for k, (idx, l) in enumerate(region):
            if k in comb_ks and k != last:
                if _take_comb(l, idx):
                    continue
                if seam_active and k in trav and k != last:
                    continue
                out.append(l)
                track(l)
                continue
            if seam_active and k in trav and k != last:
                continue
            if seam_active and k == last:
                if math.hypot(tx - x, ty - y) < 1e-3:
                    if tf is not None:
                        _emit_lines([f"G1 F{tf:g}\n"])
                    continue
                status, blk = _apply_heel(x, y, tx, ty, tz, tf, zmove, idx + 1)
                if status == "plain":
                    d = math.hypot(tx - x, ty - y)
                    retract = d >= RETRACT_MIN_TRAVEL and ty <= ymin_of(layer) + BELT_SIDE_BAND
                    body = [";C seam travel: lift + direct\n"]
                    if retract:
                        body.append(f"G1 E-{RETRACT_MM:g} F{RETRACT_F:g}\n")
                        stats["retracts"] += 1
                    body.append(f"G1 Y{y + LIFT_MM:.3f} F{TF:g} ;lift\n")
                    zs = f" Z{tz:.3f}" if zmove else ""
                    body.append(f"G1 X{tx:.3f} Y{ty + LIFT_MM:.3f}{zs} F{TF:g} ;seam travel\n")
                    body.append(f"G1 Y{ty:.3f} F{TF:g} ;lower\n")
                    if retract:
                        body.append(f"G1 E{RETRACT_MM + RESTART_EXTRA:g} F{RETRACT_F:g}\n")
                    if tf is not None:
                        body.append(f"G1 F{tf:g}\n")
                    _emit_lines(body)
                else:
                    _emit_lines(blk)
                continue
            out.append(l)
            track(l)
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
    if reject_log:
        print(f"pp_d heelroute reject list: {len(reject_log)}", file=sys.stderr)
        for msg in reject_log:
            print(msg, file=sys.stderr)
    if flag_log:
        print(f"pp_d heelroute flag list: {len(flag_log)}", file=sys.stderr)
        for msg in flag_log:
            print(msg, file=sys.stderr)
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

    # Crescent outer. The lifted hop off either horn leaves the outline, so a
    # route cannot be emitted. The travel is rejected and the original move kept.
    crescent = [(0, 0), (8, 10), (22, 10), (30, 0), (24, 3), (15, 5), (6, 3)]
    cx = sum(p[0] for p in crescent) / len(crescent)
    cy = sum(p[1] for p in crescent) / len(crescent)
    assert not _pip(cx, cy, crescent), (cx, cy)
    assert not _on_loop(15.0, 0.0, crescent, 1e-3)
    cres_g = _closed_walls(crescent, [], (30, 0), anchor=(15, -2))
    err_c = io.StringIO()
    with contextlib.redirect_stderr(err_c):
        out_c, st_c = collapse_seam_travels(cres_g, TF, {0: (15.0, 0.0)})
    assert st_c["heelroute_routed"] == 0 and st_c["heelroute_fallback"] == 0, st_c
    assert st_c["heelroute_rejected"] == 1, st_c
    assert "REJECT" in err_c.getvalue() and "reject list: 1" in err_c.getvalue()
    assert ";heel route" not in out_c
    assert _extrusion_lines(cres_g) == _extrusion_lines(out_c)

    # A second outer island in the bay. The travel still starts on the horn,
    # whose lifted hop leaves the outline, so it is rejected rather than
    # routed across the island or the opening.
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
    err_i = io.StringIO()
    with contextlib.redirect_stderr(err_i):
        out_i, st_i = collapse_seam_travels(isle_g, TF, {0: (15.0, 0.0)})
    assert st_i["heelroute_routed"] == 0 and st_i["heelroute_fallback"] == 0, st_i
    assert st_i["heelroute_rejected"] == 1, st_i
    assert ";heel route" not in out_i
    assert "REJECT" in err_i.getvalue()

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
    out_t, st_t = collapse_seam_travels(two_g, TF, {0: (50.0, 0.0)}, heel_max_route=150.0)
    assert st_t["heelroute_routed"] == 1 and st_t["heelroute_fallback"] == 0, st_t
    tpts = _heel_pts(out_t)
    assert tpts and all(p[0] > 20.0 for p in tpts), tpts
    assert any(p[1] > 20.0 for p in tpts), tpts
    assert _extrusion_lines(two_g) == _extrusion_lines(out_t)

    # The default cap is 60 mm. A longer inset route is rejected, not used.
    # Raising the cap still emits that route.
    long_outer = [(0, 0), (12, 0), (12, 35), (50, 35), (50, 0), (62, 0), (62, 48), (0, 48)]
    long_inner = [(4, 1.2), (4, 42), (58, 42), (58, 1.2)]
    long_g = _closed_walls(long_outer, [long_inner], (58, 1.2), anchor=(1, -2))
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        out_l, st_l = collapse_seam_travels(long_g, TF, {0: (30.0, 0.0)})
    straight = math.hypot(58 - 4, 0)
    assert st_l["heelroute_routed"] == 0 and st_l["heelroute_rejected"] == 1, st_l
    assert ";heel route" not in out_l
    assert ";PP_HEELROUTE_REJECT" in out_l
    log_l = err.getvalue()
    assert "over_cap" in log_l and "cap=60.00" in log_l, log_l
    assert "using_inset_route" not in log_l
    rej = out_l.find(";PP_HEELROUTE_REJECT")
    ret = out_l.find("G1 E-1.5 F1800", rej)
    seam_at = out_l.find(";seam travel", ret)
    prime_at = out_l.find("G1 E1.5 F1800", seam_at)
    assert 0 <= rej < ret < seam_at < prime_at
    assert _extrusion_lines(long_g) == _extrusion_lines(out_l)
    err150 = io.StringIO()
    with contextlib.redirect_stderr(err150):
        out150, st150 = collapse_seam_travels(long_g, TF, {0: (30.0, 0.0)}, heel_max_route=150.0)
    long_pts = _heel_pts(out150)
    long_len = _path_len((4, 1.2), long_pts)
    assert st150["heelroute_routed"] == 1 and st150["heelroute_rejected"] == 0, st150
    assert 60.0 < long_len < 150.0, long_len
    assert long_len > straight + 5.0, (long_len, straight)
    assert "over_cap" not in err150.getvalue()
    assert ";PP_HEELROUTE_REJECT" not in out150
    st60 = st_l

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
        out_f, st_f = collapse_seam_travels(far_g, TF, {0: (0.0, -5.0)}, heel_max_route=150.0)
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
    out_o, st_o = collapse_seam_travels(open_g, TF, {0: (50.0, 0.0)}, heel_max_route=150.0)
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
    out_g, st_g = collapse_seam_travels(gap_g, TF, {0: (27.0, 0.0)}, heel_max_route=150.0)
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
    err_is = io.StringIO()
    with contextlib.redirect_stderr(err_is):
        out_is, st_is = collapse_seam_travels(islands, TF, {0: (5.0, 0.0)})
    assert st_is["heelroute_routed"] == 0 and st_is["heelroute_fallback"] == 0, st_is
    assert st_is["heelroute_rejected"] == 1, st_is
    assert ";PP_HEELROUTE_REJECT" in out_is and "PP_HEELROUTE_FALLBACK" not in out_is
    assert ";heel route" not in out_is
    # A reject keeps the straight move, with the retract before it and the prime at the seam.
    rej = out_is.find(";PP_HEELROUTE_REJECT")
    ret = out_is.find("G1 E-1.5 F1800", rej)
    seam_at = out_is.find(";seam travel", ret)
    prime_at = out_is.find("G1 E1.5 F1800", seam_at)
    assert 0 <= rej < ret < seam_at < prime_at, out_is
    assert "REJECT" in err_is.getvalue()
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

    # A sliver thinner than 1 mm has no inside route. Keep the original move.
    thin = """;LAYER_CHANGE
G1 X0 Y0 F6000
;TYPE:Outer wall
;WIDTH:0.8
G1 X0 Y0 E0.01
G1 X15 Y0 E0.01
G1 X15 Y0.400 E0.01
G1 X0 Y0.400 E0.01
G1 X0 Y0 E0.01
;C_SEAM_END
G1 X12 Y0.200 F12000
;TYPE:Gap infill
G1 X1 Y-1 E0.02
"""
    err_thin = io.StringIO()
    with contextlib.redirect_stderr(err_thin):
        out_thin, st_thin = collapse_seam_travels(thin, TF, {0: (7.0, 0.0)})
    assert st_thin["heelroute_routed"] == 0 and st_thin["heelroute_fallback"] == 0, st_thin
    assert st_thin["heelroute_rejected"] == 1, st_thin
    assert "thin_region" in err_thin.getvalue()
    assert "reject list: 1" in err_thin.getvalue()
    assert ";heel route" not in out_thin
    assert ";seam travel" in out_thin
    assert _extrusion_lines(thin) == _extrusion_lines(out_thin)
    assert _hg_route_reverses(0.0, 0.0, [(5.0, 0.2), (10.0, 0.2), (5.0, 0.25), (0.2, 0.2)])
    assert not _hg_route_reverses(0.0, 0.0, [(5.0, 0.2), (10.0, 1.0), (15.0, 0.2)])

    # Seam sits just outside the wall. The final leg must arrive from inside.
    rect = [(0, 0), (40, 0), (40, 20), (0, 20)]
    seam_out = (30.0, -0.12)
    approach = _closed_walls(rect, [], seam_out, anchor=(10, -2))
    # _closed_walls starts the travel from the last outer vertex (0, 0).
    out_ap, st_ap = collapse_seam_travels(approach, TF, {0: (20.0, 0.0)})
    assert st_ap["heelroute_routed"] == 1 and st_ap["heelroute_fallback"] == 0, st_ap
    apts = _heel_pts(out_ap)
    assert apts and math.hypot(apts[-1][0] - seam_out[0], apts[-1][1] - seam_out[1]) < 1e-6
    prev = (0.0, 0.0)
    for i, p in enumerate(apts):
        samples = _seg_samples(prev, p, 0.5)
        check = samples[:-1] if i == len(apts) - 1 else samples
        for sx, sy in check:
            assert _on_loop(sx, sy, rect, 1e-3), (sx, sy, prev, p)
        prev = p

    # Narrow belt finger. The last leg must run straight in from the closest
    # inset point, not diagonally across the finger mouth.
    finger = [(0, 1.2), (20, 1.2), (20, 0), (21.2, 0), (21.2, 1.2), (40, 1.2), (40, 20), (0, 20)]
    out_fi, st_fi = collapse_seam_travels(_closed_walls(finger, [], (20.6, 0.0), anchor=(5, -2)), TF, {0: (20.0, 0.0)})
    assert st_fi["heelroute_routed"] == 1 and st_fi["heelroute_fallback"] == 0, st_fi
    fpts = _heel_pts(out_fi)
    assert len(fpts) >= 2 and abs(fpts[-1][0] - 20.6) < 1e-6 and abs(fpts[-1][1] - 0.0) < 1e-6
    assert abs(fpts[-2][0] - fpts[-1][0]) < 0.05, fpts[-2:]
    # The inward offset plus the lift makes this vertical drop about 2.1 mm.
    assert math.hypot(fpts[-1][0] - fpts[-2][0], fpts[-1][1] - fpts[-2][1]) < 2.5
    prev = (0.0, 1.2)
    for i, p in enumerate(fpts):
        samples = _seg_samples(prev, p, 0.5)
        check = samples[:-1] if i == len(fpts) - 1 else samples
        for sx, sy in check:
            assert _on_loop(sx, sy, finger, 1e-3), (sx, sy, prev, p)
        prev = p

    # The finger is too narrow to walk. The route stays in the body and drops
    # straight at the finger's X. The lifted drop stays inside the outline.
    pocket = [(0, 2), (10, 2), (10, 0), (10.4, 0), (10.4, 2), (30, 2), (30, 20), (0, 20)]
    seam_pk = (10.2, -0.1)
    out_pk, st_pk = collapse_seam_travels(_closed_walls(pocket, [], seam_pk, anchor=(5, -2)), TF, {0: (15.0, 0.0)})
    assert st_pk["heelroute_routed"] == 1 and st_pk["heelroute_rejected"] == 0, st_pk
    pkpts = _heel_pts(out_pk)
    assert len(pkpts) >= 2 and abs(pkpts[-1][0] - seam_pk[0]) < 1e-6, pkpts[-1]
    assert abs(pkpts[-2][0] - pkpts[-1][0]) < 0.05, pkpts[-2:]
    prev = (0.0, 2.0)
    for p in pkpts:
        for sx, sy in _seg_samples((prev[0], prev[1] + LIFT_MM), (p[0], p[1] + LIFT_MM), 0.25):
            assert _on_loop(sx, sy, pocket, 1e-3), (sx, sy, prev, p)
        prev = p
    prev = (0.0, 2.0)
    for i, p in enumerate(pkpts[:-1]):
        samples = _seg_samples(prev, p, 0.25)
        if i == 0:
            samples = [s for s in samples if _wall_dist(s[0], s[1], (pocket,)) + 1e-3 >= 0.5]
            assert samples, p
        for sx, sy in samples:
            assert _wall_dist(sx, sy + LIFT_MM, (pocket,)) + 1e-3 >= 0.5, (sx, sy, p)
        prev = p

    # A seam just outside a side wall still leaves the outline after the lift.
    side = [(0, 0), (40, 0), (40, 20), (0, 20)]
    err_pk = io.StringIO()
    with contextlib.redirect_stderr(err_pk):
        out_side, st_side = collapse_seam_travels(
            _closed_walls(side, [], (-0.20, 10.0), anchor=(5, 12)), TF, {0: (20.0, 0.0)})
    assert st_side["heelroute_routed"] == 0 and st_side["heelroute_rejected"] == 1, st_side
    assert "outside_approach" in err_pk.getvalue() and "reject list: 1" in err_pk.getvalue()
    assert ";heel route" not in out_side and ";seam travel" in out_side

    # The previous layer is an open line, so it has no outline. The travel is
    # outside the layer being printed. It must be routed or logged, not skipped.
    missed = """;LAYER_CHANGE
G1 X139.608 Y0 F6000
;TYPE:Outer wall
;WIDTH:0.8
G1 X158.359 Y0 E3.1
;LAYER_CHANGE
;TYPE:Outer wall
;WIDTH:0.8
;C seam travel: lift + direct
G1 Y0.400 F12720 ;lift
G1 X148.983 Y0.400 F12720 ;seam travel
G1 Y0.000 F12720 ;lower
;C_SEAM_START
G1 X158.200 Y0.000 E0.1
G1 X158.400 Y1.000 E0.1
G1 X140.000 Y1.000 E0.1
G1 X140.200 Y0.000 E0.1
G1 X148.983 Y0.000 E0.1
;C_SEAM_END
;TYPE:Gap infill
G1 X1 Y-1 E0.02
"""
    quad = [(148.983, 0.0), (158.2, 0.0), (158.4, 1.0), (140.0, 1.0), (140.2, 0.0)]
    err_ms = io.StringIO()
    with contextlib.redirect_stderr(err_ms):
        out_ms, st_ms = collapse_seam_travels(missed, TF, {0: (150.0, 0.0), 1: (148.0, 0.0)})
    assert st_ms["heelroute_routed"] + st_ms["heelroute_rejected"] == 1, st_ms
    assert st_ms["heelroute_fallback"] == 0
    if st_ms["heelroute_routed"]:
        assert ";heel route" in out_ms
        mpts = _heel_pts(out_ms)
        prev = (158.359, 0.0)
        for i, p in enumerate(mpts):
            samples = _seg_samples(prev, p, 0.5)
            check = samples[:-1] if i == len(mpts) - 1 else samples
            if i == 0 and check:
                check = check[1:]
            for sx, sy in check:
                assert _on_loop(sx, sy, quad, 1e-3), (sx, sy, prev, p)
            prev = p
    else:
        assert "REJECT" in err_ms.getvalue() and "reject list:" in err_ms.getvalue()
        assert ";heel route" not in out_ms

    def _box_out_len(a, b, poly):
        """Length outside the outline expanded by 0.001 mm. Box threshold is 0.01 mm."""
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        if L < 1e-9:
            inside = _pip(a[0], a[1], poly) or _hg_ring_dist(a[0], a[1], poly) <= _HEEL_OUT_BUFFER
            return 0.0 if inside else 1.0
        n = max(1, int(math.ceil(L / 0.002)))
        step = L / n
        outside = 0.0
        for i in range(n):
            t = (i + 0.5) / float(n)
            px = a[0] + (b[0] - a[0]) * t
            py = a[1] + (b[1] - a[1]) * t
            if not (_pip(px, py, poly) or _hg_ring_dist(px, py, poly) <= _HEEL_OUT_BUFFER):
                outside += step
        return outside

    def _lifted_legs(start, pts):
        prev = (start[0], start[1] + LIFT_MM)
        legs = []
        for px, py in pts:
            cur = (px, py + LIFT_MM)
            legs.append((prev, cur))
            prev = cur
        return legs

    # Belt lip. The Euclidean-nearest inset corner sits off the seam's X, and
    # the diagonal from it runs under the lip. The walk must end at the inset
    # point nearest that X and drop in Y. The lifted seam is still outside the
    # outline, so the travel is rejected and logged — not emitted.
    belt_lip = [
        (160.0, 4.0), (160.0, 0.016), (136.635, 0.016),
        (136.664, 0.570), (136.560, 0.743), (136.362, 0.860), (135.777, 0.865),
        (124.385, 0.865), (124.385, 0.016), (100.0, 0.016), (100.0, 8.0), (160.0, 8.0),
    ]
    lip_seam = (136.635, 0.016)
    err_lip = io.StringIO()
    with contextlib.redirect_stderr(err_lip):
        out_lip, st_lip = collapse_seam_travels(
            _closed_walls(belt_lip, [], lip_seam, anchor=(120, 6)), TF, {0: (140.0, 0.0)})
    assert st_lip["heelroute_fallback"] == 0, st_lip
    if st_lip["heelroute_routed"]:
        lpts = _heel_pts(out_lip)
        assert len(lpts) >= 2, lpts
        assert abs(lpts[-1][0] - lpts[-2][0]) < 0.02, lpts[-2:]
        assert abs(lpts[-1][0] - lip_seam[0]) < 0.02, lpts[-1]
        for a, b in _lifted_legs(belt_lip[0], lpts):
            assert _box_out_len(a, b, belt_lip) <= _HEEL_OUT_LEN + 1e-4, (a, b, _box_out_len(a, b, belt_lip))
    else:
        assert st_lip["heelroute_rejected"] == 1, st_lip
        assert "REJECT" in err_lip.getvalue() and "reject list: 1" in err_lip.getvalue()
        assert ";heel route" not in out_lip
        assert _extrusion_lines(_closed_walls(belt_lip, [], lip_seam, anchor=(120, 6))) == _extrusion_lines(out_lip)

    # Same lip, seam on the flat belt where a vertical drop stays inside.
    # The last leg shares the seam's X. Every lifted leg, including the one
    # before the drop, passes the box outside test.
    flat_seam = (150.0, 0.016)
    flat_start = (100.0, 0.016)
    flat = [
        flat_start, (100.0, 8.0), (160.0, 8.0), (160.0, 0.016), (136.635, 0.016),
        (136.664, 0.570), (136.560, 0.743), (136.362, 0.860), (135.777, 0.865),
        (124.385, 0.865), (124.385, 0.016),
    ]
    out_fl, st_fl = collapse_seam_travels(
        _closed_walls(flat, [], flat_seam, anchor=(120, 6)), TF, {0: (140.0, 0.0)})
    assert st_fl["heelroute_routed"] == 1 and st_fl["heelroute_fallback"] == 0, st_fl
    fpts = _heel_pts(out_fl)
    assert len(fpts) >= 2 and abs(fpts[-1][0] - flat_seam[0]) < 1e-6, fpts[-1]
    assert abs(fpts[-2][0] - fpts[-1][0]) < 0.02, fpts[-2:]
    for a, b in _lifted_legs(flat_start, fpts):
        got = _box_out_len(a, b, flat)
        assert got <= _HEEL_OUT_LEN + 1e-4, (a, b, got)

    # Inner centerline is 0.40 mm off the outer wall. Offset it by half the
    # inner width toward the interior. Every non-final leg stays at least
    # 0.5 mm inside the outer centerline, and the prime lands on that point.
    clear_outer = [(0, 0), (80, 0), (80, 30), (0, 30)]
    clear_inner = [(2.0, 0.40), (2.0, 20.0), (70.0, 20.0), (70.0, 0.40)]
    clear_g = _closed_walls(clear_outer, [clear_inner], (60.0, 0.05), anchor=(10, -2))
    out_cl, st_cl = collapse_seam_travels(clear_g, TF, {0: (40.0, 0.0)}, heel_max_route=150.0)
    assert st_cl["heelroute_routed"] == 1 and st_cl["heelroute_rejected"] == 0, st_cl
    cpts = _heel_pts(out_cl)
    assert len(cpts) >= 2, cpts
    prev = (2.0, 0.40)
    for i, p in enumerate(cpts[:-1]):
        samples = _seg_samples(prev, p, 0.25)
        if i == 0:
            kept = []
            armed = False
            for sx, sy in samples:
                if _wall_dist(sx, sy, (clear_outer,)) + 1e-3 >= 0.5:
                    armed = True
                if armed:
                    kept.append((sx, sy))
            samples = kept
            assert samples, (prev, p)
        for sx, sy in samples:
            dist = _wall_dist(sx, sy, (clear_outer,))
            lifted = _wall_dist(sx, sy + LIFT_MM, (clear_outer,))
            assert dist + 1e-3 >= 0.5, (sx, sy, dist, prev, p)
            assert lifted + 1e-3 >= 0.5, (sx, sy + LIFT_MM, lifted, prev, p)
        # The walk moved off the inner centerline toward the interior, not the outer wall.
        if p[1] < 5.0:
            assert p[1] > 0.40 + 1e-3, p
        prev = p
    cl_lines = out_cl.splitlines()
    seam_i = max(i for i, ln in enumerate(cl_lines) if ln.endswith(";heel route seam"))
    prime_i = max(i for i, ln in enumerate(cl_lines) if ln.startswith("G1 E1.5 F1800") and i < seam_i)
    assert "E" not in cl_lines[seam_i].split(";")[0]
    prev_move = next(
        ln for ln in reversed(cl_lines[:prime_i]) if ln.startswith("G1 ") and (";heel route" in ln)
    )
    assert prev_move.endswith(";heel route") and not prev_move.endswith(";heel route seam")
    px, py = _w(prev_move, "X"), _w(prev_move, "Y") - LIFT_MM
    assert abs(px - cpts[-2][0]) < 1e-6 and abs(py - cpts[-2][1]) < 1e-6
    assert _wall_dist(px, py, (clear_outer,)) + 1e-3 >= 0.5, (px, py)
    assert _wall_dist(px, py + LIFT_MM, (clear_outer,)) + 1e-3 >= 0.5, (px, py)
    assert _extrusion_lines(clear_g) == _extrusion_lines(out_cl)

    # A 1.2 mm channel cannot keep 0.5 mm after the 0.4 mm lift. Reject it
    # instead of emitting a leg ~0.1 mm from the far wall.
    narrow = [(0, 0), (20, 0), (20, 1.2), (0, 1.2)]
    narrow_g = _closed_walls(narrow, [], (16.0, 0.05), anchor=(2, -2))
    err_nw = io.StringIO()
    with contextlib.redirect_stderr(err_nw):
        out_nw, st_nw = collapse_seam_travels(narrow_g, TF, {0: (10.0, 0.0)})
    assert st_nw["heelroute_routed"] == 0 and st_nw["heelroute_rejected"] == 1, (st_nw, err_nw.getvalue())
    assert ";PP_HEELROUTE_REJECT" in out_nw
    assert "G1 E-1.5 F1800" in out_nw
    assert ";heel route\n" not in out_nw and not any(
        ln.endswith(";heel route") for ln in out_nw.splitlines()
    )
    assert _extrusion_lines(narrow_g) == _extrusion_lines(out_nw)

    # Already 0.5 mm inside, but the lift puts the point 0.4 mm from the ceiling.
    # That is a wall ride on this layer's outer wall, not an opening hop.
    ceil = [(0.0, 0.0), (40.0, 0.0), (40.0, 20.0), (0.0, 20.0)]
    ceil_shells = _hg_shells_from_loops([(ceil, True, 0.8)])
    ceil_geom = {"shells": ceil_shells, "index": _hg_build_edge_index(ceil_shells)}
    assert _hg_nonfinal_problem(10.0, 19.2, 12.0, 19.2, ceil_geom, True) == "wall_ride"
    assert _hg_nonfinal_problem(10.0, 0.0, 10.0, 1.2, ceil_geom, True) is None

    # One-point hop: no inset legs, so the prime would sit on the lifted start.
    # That start is 0.6 mm under the top wall (0.2 mm after the lift). The
    # block gains a short inward leg, and the prime is at least 0.5 mm inside.
    prime_outer = [(0.0, 0.0), (20.0, 0.0), (20.0, 10.0), (0.0, 10.0)]
    prime_inner = [(5.0, 9.4), (6.0, 9.4), (6.0, 8.8), (5.0, 8.8)]
    prime_g = _closed_walls(prime_outer, [prime_inner], (6.2, 9.4), anchor=(4.0, 8.0))
    out_pr, st_pr = collapse_seam_travels(prime_g, TF, {0: (10.0, 8.0)})
    assert st_pr["heelroute_routed"] == 1 and st_pr["heelroute_rejected"] == 0, st_pr
    pr_lines = out_pr.splitlines()
    pr_seam = max(i for i, ln in enumerate(pr_lines) if ln.endswith(";heel route seam"))
    pr_prime = max(i for i, ln in enumerate(pr_lines) if ln.startswith("G1 E1.5 F1800") and i < pr_seam)
    pr_move = next(ln for ln in reversed(pr_lines[:pr_prime]) if ln.startswith("G1 ") and ";heel route" in ln)
    assert pr_move.endswith(";heel route") and not pr_move.endswith(";heel route seam")
    ppx, ppy = _w(pr_move, "X"), _w(pr_move, "Y")
    assert _wall_dist(ppx, ppy, (prime_outer,)) + 1e-3 >= 0.5, (ppx, ppy)
    assert "E" not in pr_lines[pr_seam].split(";")[0]
    assert _extrusion_lines(prime_g) == _extrusion_lines(out_pr)

    # A deep comb travel is not a heel crossing and stays byte-identical.
    # A comb travel along the outer wall is routed. The travel after the
    # comb unretract is outside the block and stays byte-identical.
    deep_comb = (
        ";LAYER_CHANGE\n"
        "G1 X0 Y0 F6000\n"
        ";TYPE:Outer wall\n"
        ";WIDTH:0.8\n"
        "G1 X0 Y0 E0.01\n"
        "G1 X50 Y0 E0.01\n"
        "G1 X50 Y30 E0.01\n"
        "G1 X0 Y30 E0.01\n"
        "G1 X0 Y0 E0.01\n"
        "G1 X5 Y15 F6000\n"
        ";COMB_LIFT\n"
        "G1 X20 Y15\n"
        "G1 E2 F2100\n"
        "G1 X30 Y15 F6000\n"
        ";TYPE:Gap infill\n"
        "G1 X30 Y16 E0.02\n"
    )
    out_dc, st_dc = collapse_seam_travels(deep_comb, TF, {0: (25.0, 0.0)})
    assert st_dc["heelroute_routed"] == 0 and st_dc["heelroute_rejected"] == 0, st_dc
    assert out_dc == deep_comb
    cross_comb = (
        ";LAYER_CHANGE\n"
        "G1 X0 Y0 F6000\n"
        ";TYPE:Outer wall\n"
        ";WIDTH:0.8\n"
        "G1 X0 Y0 E0.01\n"
        "G1 X50 Y0 E0.01\n"
        "G1 X50 Y30 E0.01\n"
        "G1 X0 Y30 E0.01\n"
        "G1 X0 Y0 E0.01\n"
        ";COMB_LIFT\n"
        "G1 X10.000 Y0.200\n"
        "G1 X40.000 Y0.200\n"
        "G1 E2 F2100\n"
        "G1 X25 Y15 F6000\n"
        ";TYPE:Gap infill\n"
        "G1 X25 Y16 E0.02\n"
    )
    out_cc, st_cc = collapse_seam_travels(cross_comb, TF, {0: (25.0, 0.0)}, heel_max_route=150.0)
    assert st_cc["heelroute_routed"] >= 1 and st_cc["heelroute_fallback"] == 0, st_cc
    assert ";PP_HEELROUTE_START" in out_cc and ";heel route" in out_cc
    assert "G1 X10.000 Y0.200\n" not in out_cc and "G1 X40.000 Y0.200\n" not in out_cc
    assert "G1 X25 Y15 F6000\n" in out_cc
    assert _extrusion_lines(cross_comb) == _extrusion_lines(out_cc)

    assert _hg_route_cap(0, 60.0) == 60.0
    assert _hg_route_cap(354, 60.0) == 60.0
    assert _hg_route_cap(355, 60.0) == 200.0
    assert _hg_route_cap(447, 60.0) == 200.0
    assert _hg_route_cap(448, 60.0) == 60.0
    assert _hg_route_cap(400, 250.0) == 250.0

    # Layers 355–447 raise a 60 mm cap to 200 mm. The route still retracts and primes.
    # Layer 0 with the default cap stays a reject (asserted above).
    heel_g = "".join(";LAYER_CHANGE\n" for _ in range(355)) + long_g
    err_h = io.StringIO()
    with contextlib.redirect_stderr(err_h):
        out_h, st_h = collapse_seam_travels(heel_g, TF, {355: (30.0, 0.0)})
    hpts = _heel_pts(out_h)
    hlen = _path_len((4.0, 1.2), hpts)
    assert st_h["heelroute_routed"] == 1 and st_h["heelroute_rejected"] == 0, (st_h, err_h.getvalue())
    assert 60.0 < hlen <= 200.0, hlen
    assert "G1 E-1.5 F1800" in out_h and "G1 E1.5 F1800" in out_h
    assert "over_cap" not in err_h.getvalue() and "FLAG" not in err_h.getvalue()
    assert ";PP_HEELROUTE_REJECT" not in out_h

    # One-point hop under a low ceiling. The prime is the start, 0.385 mm from
    # the ceiling after the lift. An inward anchor has to clear 0.5 mm.
    lip = [(144.483, 0.016), (153.483, 0.016), (153.483, 1.43), (144.483, 1.43)]
    lip_g = (
        ";LAYER_CHANGE\n"
        "G1 X144.483 Y0.016 F6000\n"
        ";TYPE:Outer wall\n"
        ";WIDTH:0.8\n"
        "G1 X153.483 Y0.016 E0.01\n"
        "G1 X153.483 Y1.430 E0.01\n"
        "G1 X144.483 Y1.430 E0.01\n"
        "G1 X144.483 Y0.016 E0.01\n"
        "G1 X145.335 Y0.645 F6000\n"
        ";C_SEAM_END\n"
        "G1 X144.483 Y0.416 F12000\n"
        ";TYPE:Gap infill\n"
        "G1 X140 Y-1 E0.02\n"
    )
    out_lip34, st_lip34 = collapse_seam_travels(lip_g, TF, {0: (148.0, 0.016)})
    assert st_lip34["heelroute_routed"] == 1 and st_lip34["heelroute_rejected"] == 0, st_lip34
    lip_lines = out_lip34.splitlines()
    lip_seam = max(i for i, ln in enumerate(lip_lines) if ln.endswith(";heel route seam"))
    lip_prime = max(i for i, ln in enumerate(lip_lines) if ln.startswith("G1 E1.5 F1800") and i < lip_seam)
    lip_move = next(ln for ln in reversed(lip_lines[:lip_prime]) if ln.startswith("G1 ") and ";heel route" in ln)
    lpx, lpy = _w(lip_move, "X"), _w(lip_move, "Y")
    assert _wall_dist(lpx, lpy, (lip,)) + 1e-3 >= 0.5, (lpx, lpy, lip_move)
    assert _extrusion_lines(lip_g) == _extrusion_lines(out_lip34)

    # A pure +Y lift off the top edge leaves the outline. The first travel has
    # to rise while it moves inside, or the crossing is rejected.
    top_g = (
        ";LAYER_CHANGE\n"
        "G1 X0 Y0 F6000\n"
        ";TYPE:Outer wall\n"
        ";WIDTH:0.8\n"
        "G1 X30 Y0 E0.01\n"
        "G1 X30 Y10 E0.01\n"
        "G1 X0 Y10 E0.01\n"
        "G1 X0 Y0 E0.01\n"
        "G1 X15 Y10 F6000\n"
        ";C_SEAM_END\n"
        "G1 X15 Y0.200 F12000\n"
        ";TYPE:Gap infill\n"
        "G1 X1 Y-1 E0.02\n"
    )
    err_top = io.StringIO()
    with contextlib.redirect_stderr(err_top):
        out_top, st_top = collapse_seam_travels(top_g, TF, {0: (15.0, 0.0)})
    if st_top["heelroute_routed"]:
        assert "G1 Y10.400" not in out_top
        assert ";heel route" in out_top
    else:
        assert st_top["heelroute_rejected"] == 1, st_top
        assert "REJECT" in err_top.getvalue()
        assert ";heel route" not in out_top
    assert _extrusion_lines(top_g) == _extrusion_lines(out_top)

    # Two shells in the heel band. A travel that cannot stay inside is flagged
    # with the source line and whether the straight move crosses the top.
    two_shell = (
        ";LAYER_CHANGE\n"
        "G1 X0 Y0 F6000\n"
        ";TYPE:Outer wall\n"
        ";WIDTH:0.8\n"
        "G1 X20 Y0 E0.01\n"
        "G1 X20 Y8 E0.01\n"
        "G1 X0 Y8 E0.01\n"
        "G1 X0 Y0 E0.01\n"
        "G1 X40 Y0 F6000\n"
        ";TYPE:Outer wall\n"
        ";WIDTH:0.8\n"
        "G1 X60 Y0 E0.01\n"
        "G1 X60 Y8 E0.01\n"
        "G1 X40 Y8 E0.01\n"
        "G1 X40 Y0 E0.01\n"
        "G1 X50 Y0.200 F6000\n"
        ";C_SEAM_END\n"
        "G1 X10 Y0.200 F12000\n"
        ";TYPE:Gap infill\n"
        "G1 X1 Y-1 E0.02\n"
    )
    flag_g = "".join(";LAYER_CHANGE\n" for _ in range(355)) + two_shell
    err_flag = io.StringIO()
    with contextlib.redirect_stderr(err_flag):
        out_flag, st_flag = collapse_seam_travels(flag_g, TF, {355: (30.0, 0.0)})
    flag_txt = err_flag.getvalue()
    assert st_flag["heelroute_rejected"] >= 1, st_flag
    assert "pp_d heelroute FLAG" in flag_txt
    assert "crosses_top=" in flag_txt and "line=" in flag_txt
    assert "start=(50.000,0.200)" in flag_txt and "seam=(10.000,0.200)" in flag_txt
    assert ";PP_HEELROUTE_REJECT" in out_flag
    assert "G1 E-1.5 F1800" in out_flag and "G1 E1.5 F1800" in out_flag
    assert _extrusion_lines(flag_g) == _extrusion_lines(out_flag)

    print(
        "heelroute tests ok",
        st, st_deep, st_near, st2, st3, st_ci, st_c, st_i, st_t, st_l, round(long_len, 2), st60, st_f, st_o, st_g, st_is, st_sp, st_thin, st_ap,
    )


if __name__ == "__main__":
    _test_heelroute()
