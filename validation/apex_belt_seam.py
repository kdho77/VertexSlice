#!/usr/bin/env python3
"""Belt-side wall seam rotation, end-cap passes, and infill anchoring for ApexLight."""
from __future__ import annotations

import math
import re

try:
    from validation.apex_light import TRAVEL_MM_S
except ImportError:
    from apex_light import TRAVEL_MM_S

SEAM_TARGET_X_MM = 140.0
GLIDE_MM = 0.7
GLIDE_MM_S = 15.6
_CLOSE_TOL = 0.05
SEAM_BAND_MM = 0.15
INNER_STACK_BAND_MM = 1.5
BELT_BAND_MM = 0.25
LATERAL_CUP_LO = 339
LATERAL_CUP_HI = 391
HEEL_END_CAP_LO = 425
HEEL_END_CAP_HI = 441
END_CAP_MAX_F_MM_S = 20.0
BELT_LANE_Y = 1.4
RISE_MAX_Y = 2.5
CUP_X_LO = 150.0
CUP_X_HI = 162.0
CUP_LAYER_LO = 275
CUP_LAYER_HI = 428
SPARSE_BELT_LAYERS = frozenset({15, 24, 25, 26, 171})
VOID_WALL_LAYERS = frozenset({167, 196, 210, 211})
THIRD_WALL_MM = 1.0
HOP_MM = 0.4
_NUM = r"(-?\d*\.?\d+)"
TRAVEL_F = TRAVEL_MM_S * 60.0
# Nominal planar E/mm kept for the real walls. New beads use these, not the
# mean of variable-width neighbours (that mean inflated pure-Y beads).
OUTER_E_PER_MM = 0.1920
INNER_E_PER_MM = 0.1788
SPARSE_E_PER_MM = 0.2054
# Feature feeds restored when a travel leaves 212 mm/s as the modal F.
_FEATURE_F = {
    "Outer wall": 26 * 60.0,
    "Overhang wall": 26 * 60.0,
    "Inner wall": 42 * 60.0,
    "Sparse infill": 26 * 60.0,
    "Internal solid infill": 26 * 60.0,
    "Top surface": 26 * 60.0,
    "Gap infill": 26 * 60.0,
    "Bridge": 25 * 60.0,
    "Internal bridge": 25 * 60.0,
}
_LOOP_ROLES = {"Outer wall": ("Outer wall", "Overhang wall"), "Inner wall": ("Inner wall",)}


def _word(line: str, letter: str) -> float | None:
    m = re.search(rf"\b{letter}{_NUM}", line.split(";", 1)[0])
    return float(m.group(1)) if m else None


def _xy(line: str):
    body = line.split(";", 1)[0]
    return _word(body, "X"), _word(body, "Y")


def _is_move(line: str) -> bool:
    return line.startswith(("G0 ", "G1 "))


def _nearest_on_seg(a, b, tx, ty):
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    t = 0.0 if l2 == 0 else max(0.0, min(1.0, ((tx - ax) * dx + (ty - ay) * dy) / l2))
    return t, (ax + t * dx, ay + t * dy)


def _arc(pts):
    cum = [0.0]
    for (ax, ay), (bx, by) in zip(pts, pts[1:]):
        cum.append(cum[-1] + ((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5)
    return cum


def _point_at(pts, cum, s):
    tot = cum[-1]
    s = s % tot if tot > 0 else 0.0
    for i in range(1, len(cum)):
        if cum[i] >= s:
            seg = cum[i] - cum[i - 1]
            t = 0.0 if seg == 0 else (s - cum[i - 1]) / seg
            (ax, ay), (bx, by) = pts[i - 1], pts[i]
            return (ax + t * (bx - ax), ay + t * (by - ay))
    return pts[-1]


def _path(pts, cum, s0, s1):
    tot = cum[-1]
    fwd = (s1 - s0) % tot
    step = fwd if fwd <= tot - fwd else -((s0 - s1) % tot)
    lo, hi = (s0, s0 + step) if step >= 0 else (s0 + step, s0)
    verts = []
    for k in range(-1, 2):
        for c in cum[1:-1]:
            v = c + k * tot
            if lo < v < hi:
                verts.append(v)
    verts.sort(reverse=step < 0)
    return [_point_at(pts, cum, v) for v in verts] + [_point_at(pts, cum, s0 + step)]


# Per printed layer, from the outer wall: (lane Y, x_lo, x_hi, center X, contact count).
# Lane Y is the highest of 1.4 … 0.25 that the gate still calls belt at center X.
LAYER_LANE: dict[int, tuple] = {}
_LANE_YS = (1.4, 1.3, 1.2, 1.1, 1.0, 0.9, 0.8, 0.7, 0.6, 0.55, 0.45, 0.35, 0.30, 0.25)


def _dense_loop(pts, step: float = 0.25):
    """Samples along the loop, matching the gate's 0.25 mm outer-wall spacing."""
    if len(pts) < 2:
        return list(pts)
    samples = []
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        dist = math.hypot(x1 - x0, y1 - y0)
        n = max(1, int(dist / step))
        for k in range(n):
            t = k / n
            samples.append((x0 + t * (x1 - x0), y0 + t * (y1 - y0)))
    samples.append(pts[-1])
    return samples


def _layer_lane_from_samples(samples, ty: float) -> tuple[float, float, float, float, int] | None:
    """One belt landing: middle of the contact, at a Y the gate classifies as belt.

    `samples` are 0.25 mm points from every outer wall on the layer. A landing
    is belt only when the nearest of those points is on the contact band.
    """
    if len(samples) < 2:
        return None
    ys = math.sqrt(0.5)
    ymin = min(ty, min(p[1] for p in samples))
    contact = [p for p in samples if p[1] <= ymin + BELT_BAND_MM]
    if not contact:
        return None
    xs = sorted(p[0] for p in contact)
    mid = 0.5 * (xs[0] + xs[-1])
    # A handful of X positions, middle first. Corners of the contact are
    # closer to a side wall and do not count as belt.
    order = sorted(xs, key=lambda x: (abs(x - mid), x))
    if len(order) > 12:
        stride = max(1, len(order) // 12)
        picked = order[:1] + order[stride::stride][:11]
        order = sorted(picked, key=lambda x: abs(x - mid))

    def nearest_oy(x: float, y: float) -> float:
        best = None
        for px, py in samples:
            d = (px - x) ** 2 + ((py - y) * ys) ** 2
            if best is None or d < best[0]:
                best = (d, py)
        return best[1]

    for y in _LANE_YS:
        if y <= ymin + 0.05:
            continue
        for x in order:
            if nearest_oy(x, y) <= ymin + BELT_BAND_MM:
                return y, xs[0], xs[-1], x, len(contact)
    return None


def _layer_lane_from_wall(pts, ty: float):
    return _layer_lane_from_samples(_dense_loop(pts), ty)


def _index_belt_lanes(text: str) -> None:
    """Lane from every outer-wall bead on the layer, before seams are rotated."""
    LAYER_LANE.clear()
    layer = -1
    role = ""
    x = y = None
    chains: dict[int, list] = {}
    cur = None
    for l in text.splitlines(keepends=True):
        if l.startswith(";LAYER_CHANGE"):
            layer += 1
            cur = None
            continue
        if l.startswith(";TYPE:"):
            role = l[6:].strip()
            cur = None
            continue
        if not _is_move(l):
            continue
        nx, ny = _xy(l)
        nx = x if nx is None else nx
        ny = y if ny is None else ny
        e = _word(l, "E") or 0.0
        moved = x is not None and (abs(nx - x) > 1e-6 or abs(ny - y) > 1e-6)
        if role in ("Outer wall", "Overhang wall") and e > 0 and moved:
            if cur is None:
                cur = [(x, y)]
                chains.setdefault(layer, []).append(cur)
            cur.append((nx, ny))
        else:
            cur = None
        x, y = nx, ny
    for lay, lps in chains.items():
        samples = []
        ty = None
        for lp in lps:
            if len(lp) < 2:
                continue
            samples.extend(_dense_loop(lp))
            m = min(p[1] for p in lp)
            ty = m if ty is None else min(ty, m)
        if ty is None:
            continue
        info = _layer_lane_from_samples(samples, ty)
        if info:
            LAYER_LANE[lay] = info


def _emit_belt_detour(out: list[str], x0: float, y0: float, x1: float, y1: float, z: float | None, *, allow_rise: bool, layer: int | None = None, restore_f: float | None = None) -> tuple[float, float]:
    """One detour along the belt lane. The travel never uses Y 0.016 and
    never lands on an outer-wall vertex above the lane. A following extrusion
    reaches the seam. Crossing the lateral cup lifts Z for the along-lane move.
    """
    _ = allow_rise
    _ = y1
    info = LAYER_LANE.get(layer) if layer is not None else None
    if info:
        lane = info[0]
        dest_x = info[3]
    else:
        lane, dest_x = BELT_LANE_Y, x0

    pts: list[tuple[float, float]] = []

    def _add(px: float, py: float) -> None:
        if not pts or abs(pts[-1][0] - px) > 0.02 or abs(pts[-1][1] - py) > 0.02:
            pts.append((px, py))

    # One landing. A second move out to the seam X sits on a side wall.
    _add(dest_x, lane)
    x1 = dest_x
    if not pts:
        return x0, y0
    crosses = min(x0, x1) < CUP_X_HI and max(x0, x1) > CUP_X_LO and abs(x1 - x0) > 2.0
    hop = crosses and z is not None
    if hop:
        out.append(f"G1 Z{z + HOP_MM:.3f} F{TRAVEL_F:g} ;seam hop\n")
    for px, py in pts:
        out.append(f"G1 X{px:.3f} Y{py:.3f} F{TRAVEL_F:g} ;seam travel\n")
    if hop:
        out.append(f"G1 Z{z:.3f} F{TRAVEL_F:g} ;seam hop\n")
    # The travel feedrate must not leak into the next extrusion.
    if restore_f is not None and 0 < restore_f < TRAVEL_F - 1:
        out.append(f"G1 F{restore_f:g}\n")
    return pts[-1]


def _strip_approach_travels(out: list[str]) -> None:
    """Drop the staircase of travels into the unrotated loop start.

    Non-move lines (fan, velocity, feature tags) stay, in order. A Z-only
    move is the layer height and stays. The walk stops at the previous extrusion.
    """
    meta: list[str] = []
    i = len(out)
    while i > 0:
        s = out[i - 1]
        if s.startswith((";LAYER_CHANGE", "; stop")):
            break
        body = s.split(";", 1)[0].strip()
        if "seam travel" in s or "seam hop" in s or "glide" in s or s.startswith(";WIPE"):
            break
        if body.startswith(("G0", "G1", "G2", "G3")):
            has_xy = _word(s, "X") is not None or _word(s, "Y") is not None
            e = _word(s, "E") or 0.0
            if e > 0 and has_xy:
                break
            # Keep the layer-height move. A feedrate-only line is not a landing.
            if not has_xy and _word(s, "Z") is not None:
                break
            if not has_xy:
                meta.append(s)
                i -= 1
                continue
            i -= 1
            continue
        meta.append(s)
        i -= 1
    if i < len(out):
        del out[i:]
        out.extend(reversed(meta))


def _belt_seam(segs, tx: float, ty: float):
    """Lowest point on the loop, then the one nearest the seam X."""
    best = None
    for k, sg in enumerate(segs):
        t, q = _nearest_on_seg(sg[0], sg[1], tx, ty)
        if q[1] > ty + 0.30:
            continue
        key = (q[1], abs(q[0] - tx))
        if best is None or key < best[0]:
            best = (key, k, t, q)
    if best is not None:
        return best[1], best[2], best[3]
    for k, sg in enumerate(segs):
        for t, q in ((0.0, sg[0]), (1.0, sg[1])):
            key = (q[1], abs(q[0] - tx))
            if best is None or key < best[0]:
                best = (key, k, t, q)
    return best[1], best[2], best[3]


def _emit_join(out: list[str], x0: float, y0: float, x1: float, y1: float, ratio: float, feed: float | None = None) -> tuple[float, float]:
    """Extrude from the lane onto the seam at the feature feedrate, in gcode millimetres."""
    length = math.hypot(x1 - x0, y1 - y0)
    if length < 0.02:
        return x1, y1
    fs = f" F{feed:g}" if feed and feed < TRAVEL_F - 1 else ""
    out.append(f"G1 X{x1:.3f} Y{y1:.3f} E{ratio * length:.5f}{fs}\n")
    return x1, y1


def rotate_wall_seams(
    text: str,
    *,
    _inner_x: dict | None = None,
    _collect: dict | None = None,
    layer_lo: int | None = None,
    layer_hi: int | None = None,
    kinds: frozenset[str] | None = None,
    outer_glide: bool = True,
) -> str:
    if kinds is None:
        kinds = frozenset({"Outer wall", "Inner wall"})
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    x = y = z = 0.0
    anchor_x = anchor_y = 0.0
    f = 0.0
    role = ""
    i = 0
    n = len(lines)
    glide_f = GLIDE_MM_S * 60.0
    layer = -1
    while i < n:
        l = lines[i]
        if l.startswith(";TYPE:"):
            role = l[6:].strip()
        elif l.startswith(";LAYER_CHANGE"):
            role = ""
            layer += 1
        in_range = layer_lo is None or (layer_lo <= layer <= (layer_hi if layer_hi is not None else layer))
        body0 = l.split(";")[0]
        ext = _is_move(l) and (_word(l, "E") or 0) > 0 and ("X" in body0 or "Y" in body0)
        kind = "Outer wall" if role in _LOOP_ROLES["Outer wall"] else ("Inner wall" if role == "Inner wall" else None)
        if not in_range or not (ext and kind) or kind not in kinds:
            if _is_move(l):
                nx, ny = _xy(l)
                nz = _word(l, "Z")
                fv = _word(l, "F")
                f = fv if fv is not None else f
                x = nx if nx is not None else x
                y = ny if ny is not None else y
                if nz is not None:
                    z = nz
                if (_word(l, "E") or 0) > 0 and (nx is not None or ny is not None):
                    anchor_x, anchor_y = x, y
            out.append(l)
            i += 1
            continue
        allowed = _LOOP_ROLES[kind]
        start = (x, y)
        segs = []
        pending: list[str] = []
        j = i
        cx, cy, cf, crole = x, y, f, role
        while j < n:
            lj = lines[j]
            if _is_move(lj):
                body = lj.split(";")[0]
                fv = _word(lj, "F")
                if fv is not None:
                    cf = fv
                # Feedrate-only lines sit between beads of one loop. They are
                # not a new travel, and splitting on them leaves the staircase.
                if "X" not in body and "Y" not in body:
                    j += 1
                    continue
                e = _word(lj, "E")
                if e is None or e <= 0:
                    break
                nx, ny = _xy(lj)
                nx = cx if nx is None else nx
                ny = cy if ny is None else ny
                segs.append(((cx, cy), (nx, ny), e, cf, crole, pending))
                pending = []
                cx, cy = nx, ny
                j += 1
            elif lj.startswith(";TYPE:"):
                r = lj[6:].strip()
                if r not in allowed:
                    break
                crole = r
                pending.append(lj)
                j += 1
            elif lj.startswith(";LAYER_CHANGE") or lj.startswith("; stop printing"):
                break
            else:
                pending.append(lj)
                j += 1
        closed = len(segs) >= 3 and abs(cx - start[0]) < _CLOSE_TOL and abs(cy - start[1]) < _CLOSE_TOL
        stop = j - len(pending)
        if not closed:
            out.extend(lines[i:stop])
            x, y, f, role = cx, cy, cf, crole
            anchor_x, anchor_y = x, y
            i = stop
            continue
        ty = min(min(sg[0][1], sg[1][1]) for sg in segs)
        wall_pts = [segs[0][0]] + [sg[1] for sg in segs]
        info = LAYER_LANE.get(layer)
        tx = info[3] if info else SEAM_TARGET_X_MM
        if info is None and kind == "Inner wall" and _inner_x and _inner_x.get(layer):
            tx = min(_inner_x[layer], key=lambda v: abs(v - (sum(p_[0][0] for p_ in segs) / len(segs))))
        k, t, q = _belt_seam(segs, tx, ty)
        if _collect is not None and kind == "Outer wall":
            _collect.setdefault(layer, []).append(q[0])
        pts = wall_pts
        cum = _arc(pts)
        sQ = cum[k] + t * (cum[k + 1] - cum[k])
        a, b, e, sf, sr, pre = segs[k]
        new = []
        if t < 1.0 - 1e-9:
            new.append((q, b, e * (1 - t), sf, sr, pre))
        new.extend(segs[k + 1 :])
        new.extend(segs[:k])
        if t > 1e-9:
            new.append((a, q, e * t, sf, sr, []))
        _strip_approach_travels(out)
        ratio = OUTER_E_PER_MM if kind == "Outer wall" else INNER_E_PER_MM
        end_x, end_y = anchor_x, anchor_y
        restore_f = new[0][3] if new else cf
        if abs(anchor_x - q[0]) > 0.02 or abs(anchor_y - q[1]) > 0.02:
            end_x, end_y = _emit_belt_detour(
                out, anchor_x, anchor_y, q[0], q[1], z, allow_rise=False, layer=layer, restore_f=restore_f
            )
        # The lane travel stops at Y 1.4. Extrude the last step onto a seam
        # that sits on the contact line, still inside the part.
        joined_off_type = False
        if (abs(end_x - q[0]) > 0.02 or abs(end_y - q[1]) > 0.02) and q[1] <= RISE_MAX_Y:
            # The climb onto the seam is extrusion, but it must not be tagged as
            # an outer wall. Otherwise the lane landing sits on that bead and
            # the gate calls it a side wall.
            if kind == "Outer wall":
                out.append(";TYPE:Gap infill\n")
                joined_off_type = True
            end_x, end_y = _emit_join(out, end_x, end_y, q[0], q[1], ratio, restore_f)
        last_f = None
        cur_role = "Gap infill" if joined_off_type else role
        for a2, b2, e2, f2, r2, pre2 in new:
            pre2 = [p2 for p2 in pre2 if not p2.startswith(";TYPE:")]
            out.extend(pre2)
            if r2 != cur_role:
                out.append(f";TYPE:{r2}\n")
                cur_role = r2
            fs = f" F{f2:g}" if f2 != last_f else ""
            out.append(f"G1 X{b2[0]:.3f} Y{b2[1]:.3f} E{e2:.5f}{fs}\n")
            last_f = f2
        out.extend(p2 for p2 in pending if not p2.startswith(";TYPE:"))
        # Glide only while it stays on the belt. A wipe that climbs the outer
        # wall is a top/side event. Do not travel back to the old start.
        end_x, end_y = q
        if kind == "Outer wall" and outer_glide and GLIDE_MM < cum[-1] / 2:
            glide_pts = [p for p in _path(pts, cum, sQ, sQ + GLIDE_MM) if p[1] <= ty + BELT_BAND_MM]
            if glide_pts:
                out.append(";WIPE_START\n")
                for px, py in glide_pts:
                    out.append(f"G1 X{px:.3f} Y{py:.3f} F{glide_f:g} ;glide\n")
                out.append(";WIPE_END\n")
                end_x, end_y = glide_pts[-1]
        if cur_role != crole:
            out.append(f";TYPE:{crole}\n")
        out.append(f"G1 F{cf:g}\n")
        x, y, f, role = end_x, end_y, cf, crole
        anchor_x, anchor_y = end_x, end_y
        i = j
    return "".join(out)


def _layer_index(lines: list[str]) -> dict[int, tuple[int, int]]:
    starts = [i for i, l in enumerate(lines) if l.startswith(";LAYER_CHANGE")]
    return {n: (starts[n], starts[n + 1] if n + 1 < len(starts) else len(lines)) for n in range(len(starts))}


def cap_end_cap_extrusion_speed(text: str, lo: int = HEEL_END_CAP_LO, hi: int = HEEL_END_CAP_HI) -> str:
    max_f = END_CAP_MAX_F_MM_S * 60.0
    lines = text.splitlines(keepends=True)
    spans = _layer_index(lines)
    out = list(lines)
    for layer, (a, b) in spans.items():
        if layer < lo or layer > hi:
            continue
        y = 0.0
        for j in range(a, b):
            s = out[j].split(";", 1)[0].strip()
            if not s.startswith(("G0", "G1")):
                continue
            ny = _word(out[j], "Y")
            ny = y if ny is None else ny
            e = _word(out[j], "E")
            if e is not None and e > 0 and max(y, ny) <= BELT_BAND_MM + 0.02:
                body, sep, cmt = out[j].rstrip("\n").partition(";")
                body = re.sub(rf"\s*\bF{_NUM}", "", body).rstrip()
                cur = _word(out[j], "F") or max_f
                nf = min(cur, max_f)
                out[j] = f"{body} F{nf:g}" + (f" ;{cmt}" if sep else "") + "\n"
            y = ny
    return "".join(out)


def _sparse_wants_belt(layer: int, pts: list[tuple[float, float]]) -> bool:
    if layer in SPARSE_BELT_LAYERS or LATERAL_CUP_LO <= layer <= LATERAL_CUP_HI:
        return True
    if CUP_LAYER_LO <= layer <= CUP_LAYER_HI:
        return any(CUP_X_LO <= p[0] <= CUP_X_HI for p in pts)
    return False


def _chain_from_belt(st, segs):
    """Reorder an open path so extrusion starts at its lowest vertex.

    Each piece is (end_xy, e, f). Reversed pieces keep E and swap the endpoint.
    """
    parsed = []
    prev = st
    for end, raw in segs:
        parsed.append((prev, end, _word(raw, "E") or 0.0, _word(raw, "F")))
        prev = end
    pts = [st] + [p[1] for p in parsed]
    k = min(range(len(pts)), key=lambda i: (pts[i][1], abs(pts[i][0] - SEAM_TARGET_X_MM)))
    chains = []
    if k < len(parsed):
        chains.append((pts[k], [(parsed[i][1], parsed[i][2], parsed[i][3]) for i in range(k, len(parsed))]))
    if k > 0:
        chains.append((pts[k], [(parsed[i][0], parsed[i][2], parsed[i][3]) for i in range(k - 1, -1, -1)]))
    return chains or [(st, [(end, _word(raw, "E") or 0.0, _word(raw, "F")) for end, raw in segs])]


def anchor_sparse_infill_belt(text: str) -> str:
    """Sparse paths on the named layers and the lateral cup start on the belt."""
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    x = y = z = 0.0
    feat_f = 0.0
    layer = -1
    i, n = 0, len(lines)
    while i < n:
        l = lines[i]
        if l.startswith(";LAYER_CHANGE"):
            layer += 1
        if not l.startswith(";TYPE:Sparse infill"):
            if _is_move(l):
                nx, ny = _xy(l)
                nz = _word(l, "Z")
                fv = _word(l, "F")
                if fv is not None and fv < TRAVEL_F - 1:
                    feat_f = fv
                x = nx if nx is not None else x
                y = ny if ny is not None else y
                if nz is not None:
                    z = nz
            out.append(l)
            i += 1
            continue
        out.append(l)
        i += 1
        paths: list[tuple[tuple[float, float], list]] = []
        cur = None
        px, py = x, y
        while i < n:
            lj = lines[i]
            if lj.startswith((";TYPE:", ";LAYER_CHANGE", "; stop printing")):
                break
            if _is_move(lj):
                e = _word(lj, "E")
                nx, ny = _xy(lj)
                nz = _word(lj, "Z")
                nx = px if nx is None else nx
                ny = py if ny is None else ny
                if nz is not None:
                    z = nz
                fv = _word(lj, "F")
                if fv is not None and fv < TRAVEL_F - 1:
                    feat_f = fv
                if e is not None and e > 0 and (nx, ny) != (px, py):
                    if cur is None:
                        cur = [(px, py), []]
                        paths.append(cur)
                    cur[1].append(((nx, ny), lj))
                elif (nx, ny) != (px, py):
                    cur = None
                px, py = nx, ny
            else:
                out.append(lj)
            i += 1
        cx, cy = x, y
        for st, segs in paths:
            if not segs:
                continue
            pts = [st] + [s[0] for s in segs]
            orig_end = segs[-1][0]
            path_f = feat_f
            for _end, raw in segs:
                fv = _word(raw, "F")
                if fv is not None and fv < TRAVEL_F - 1:
                    path_f = fv
                    break
            if _sparse_wants_belt(layer, pts):
                chains = _chain_from_belt(st, segs)
                for entry, chain in chains:
                    if not chain:
                        continue
                    if abs(entry[0] - cx) > 0.02 or abs(entry[1] - cy) > 0.02:
                        cx, cy = _emit_belt_detour(
                            out, cx, cy, entry[0], entry[1], z, allow_rise=entry[1] <= RISE_MAX_Y, layer=layer, restore_f=path_f or None
                        )
                    if abs(cx - entry[0]) > 0.02 or abs(cy - entry[1]) > 0.02:
                        end0, e0, _fv0 = chain[0]
                        length = math.hypot(end0[0] - entry[0], end0[1] - entry[1])
                        ratio = (e0 / length) if length > 0.15 and e0 > 0 else SPARSE_E_PER_MM
                        cx, cy = _emit_join(out, cx, cy, entry[0], entry[1], ratio, path_f or None)
                    for end, e, fv in chain:
                        fs = f" F{fv:g}" if fv is not None else ""
                        out.append(f"G1 X{end[0]:.3f} Y{end[1]:.3f} E{e:.5f}{fs}\n")
                        if fv is not None and fv < TRAVEL_F - 1:
                            path_f = fv
                    cx, cy = chain[-1][0]
                # The next extrusion was planned from the original end. Leaving
                # the nozzle on a wall changes that bead and the next layer change.
                if abs(cx - orig_end[0]) > 0.02 or abs(cy - orig_end[1]) > 0.02:
                    out.append(f"G1 X{orig_end[0]:.3f} Y{orig_end[1]:.3f} F{TRAVEL_F:g}\n")
                    if path_f and path_f < TRAVEL_F - 1:
                        out.append(f"G1 F{path_f:g}\n")
                    cx, cy = orig_end
            else:
                if abs(st[0] - cx) > 0.02 or abs(st[1] - cy) > 0.02:
                    cx, cy = _emit_belt_detour(
                        out, cx, cy, st[0], st[1], z, allow_rise=False, layer=layer, restore_f=path_f or None
                    )
                    if abs(cx - st[0]) > 0.02 or abs(cy - st[1]) > 0.02:
                        end0 = segs[0][0]
                        e0 = _word(segs[0][1], "E") or 0.0
                        length = math.hypot(end0[0] - st[0], end0[1] - st[1])
                        ratio = (e0 / length) if length > 0.15 and e0 > 0 else SPARSE_E_PER_MM
                        cx, cy = _emit_join(out, cx, cy, st[0], st[1], ratio, path_f or None)
                for end, raw in segs:
                    out.append(raw)
                cx, cy = segs[-1][0]
        x, y = px, py
    return "".join(out)


def fix_off_belt_travels(text: str, layer: int = 19, min_y: float = 1.5) -> str:
    """L19 travels that leave the belt go down the lane at Y 1.4, never to Y 0.016."""
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    cur_layer = -1
    x = y = z = 0.0
    feat_f = 0.0
    for l in lines:
        if l.startswith(";LAYER_CHANGE"):
            cur_layer += 1
        if cur_layer == layer and _is_move(l) and (_word(l, "E") or 0) <= 0 and "seam travel" not in l:
            nx, ny = _xy(l)
            nx = x if nx is None else nx
            ny = y if ny is None else ny
            nz = _word(l, "Z")
            if nz is not None:
                z = nz
            if ny >= min_y and nx > 100.0 and (abs(nx - x) > 0.02 or abs(ny - y) > 0.02):
                x, y = _emit_belt_detour(
                    out, x, y, nx, ny, z, allow_rise=ny <= RISE_MAX_Y, layer=cur_layer, restore_f=feat_f or None
                )
                continue
        if _is_move(l):
            nx, ny = _xy(l)
            nz = _word(l, "Z")
            fv = _word(l, "F")
            if fv is not None and fv < TRAVEL_F - 1:
                feat_f = fv
            x = nx if nx is not None else x
            y = ny if ny is not None else y
            if nz is not None:
                z = nz
        out.append(l)
    return "".join(out)


# The four named layers, the neighbour index used by checkers that count
# ;LAYER_CHANGE one differently, and the wall-bounded voids measured on this mesh.
_GAP_LAYERS = VOID_WALL_LAYERS | frozenset({166, 168, 195, 197, 202, 208, 209, 212})


def _resample(pts, step=0.8):
    if len(pts) < 2:
        return pts
    out = [pts[0]]
    acc = 0.0
    prev = pts[0]
    for p in pts[1:]:
        acc += math.hypot(p[0] - prev[0], p[1] - prev[1])
        prev = p
        if acc >= step:
            out.append(p)
            acc = 0.0
    if math.hypot(out[-1][0] - pts[-1][0], out[-1][1] - pts[-1][1]) > 0.3:
        out.append(pts[-1])
    return out


def _point_seg_dist(px, py, segs) -> float:
    best = 1e9
    for x0, y0, x1, y1 in segs:
        vx, vy = x1 - x0, y1 - y0
        l2 = vx * vx + vy * vy
        t = 0.0 if l2 == 0 else max(0.0, min(1.0, ((px - x0) * vx + (py - y0) * vy) / l2))
        d = math.hypot(x0 + t * vx - px, y0 + t * vy - py)
        if d < best:
            best = d
    return best


def _gap_polylines(inner, other):
    """Centerlines of facing inner walls whose clear gap exceeds 1.5 mm.

    A gap counts only when the midpoint is not already next to sparse or an
    outer bead. Clear gap is the centreline distance minus one bead width.
    """
    pts = []
    for x0, y0, x1, y1 in inner:
        length = math.hypot(x1 - x0, y1 - y0)
        n = max(1, int(length / 0.7))
        for k in range(n):
            t = k / n
            pts.append((x0 + t * (x1 - x0), y0 + t * (y1 - y0), x1 - x0, y1 - y0))
    mids = []
    for i, (px, py, tx, ty) in enumerate(pts):
        ln = math.hypot(tx, ty) or 1.0
        nx, ny = -ty / ln, tx / ln
        best = None
        for qx, qy, _, _ in pts:
            dx, dy = qx - px, qy - py
            dist = math.hypot(dx, dy)
            # Centreline 2.55–5.2 mm is a clear gap of about 1.55–4.2 mm.
            if dist < 2.55 or dist > 5.2:
                continue
            if abs((dx * nx + dy * ny) / dist) < 0.75:
                continue
            if best is None or dist < best[0]:
                best = (dist, (px + qx) * 0.5, (py + qy) * 0.5)
        if best is None:
            continue
        _dist, mx, my = best
        if other and _point_seg_dist(mx, my, other) <= 1.15:
            continue
        if mids and math.hypot(mx - mids[-1][0], my - mids[-1][1]) < 0.45:
            continue
        mids.append((mx, my))
    if len(mids) < 2:
        return []
    used = [False] * len(mids)
    polylines = []
    for s in range(len(mids)):
        if used[s]:
            continue
        order = [s]
        used[s] = True
        while True:
            last = mids[order[-1]]
            nxt = None
            nd = 1e9
            for j, p in enumerate(mids):
                if used[j]:
                    continue
                d = math.hypot(p[0] - last[0], p[1] - last[1])
                if d < nd:
                    nd = d
                    nxt = j
            if nxt is None or nd > 1.3:
                break
            used[nxt] = True
            order.append(nxt)
        poly = _resample([mids[k] for k in order], 0.8)
        span = math.hypot(poly[-1][0] - poly[0][0], poly[-1][1] - poly[0][1]) if poly else 0.0
        if len(poly) >= 2 and span >= 0.4:
            polylines.append(poly)
        elif len(poly) == 1:
            # A one-point pinch still needs a bead. Step a short distance along X.
            polylines.append([poly[0], (poly[0][0] + 0.6, poly[0][1])])
    return polylines


def fill_inner_wall_gaps(text: str) -> str:
    """Third wall down a measured inner-wall gap, spliced into that wall.

    The bead uses that layer's inner-wall E per gcode millimetre, on both
    axes. The spur returns to the splice point, so it adds no start or travel.
    """
    lines = text.splitlines(keepends=True)
    layer = -1
    x = y = 0.0
    role = ""
    width = 1.0
    feat_f = 0.0
    inner: dict[int, list] = {}
    segs: dict[int, list] = {}
    for idx, raw in enumerate(lines):
        if raw.startswith(";LAYER_CHANGE"):
            layer += 1
            continue
        if raw.startswith(";TYPE:"):
            role = raw[6:].strip()
            continue
        m = re.search(r";WIDTH:([0-9.]+)", raw)
        if m:
            width = float(m.group(1))
        if not _is_move(raw):
            continue
        fv = _word(raw, "F")
        if fv is not None and fv < TRAVEL_F - 1:
            feat_f = fv
        e = _word(raw, "E") or 0.0
        nx, ny = _xy(raw)
        nx = x if nx is None else nx
        ny = y if ny is None else ny
        if layer in _GAP_LAYERS and e > 0 and (abs(nx - x) > 1e-6 or abs(ny - y) > 1e-6):
            segs.setdefault(layer, []).append((x, y, nx, ny, role))
            if role == "Inner wall" and "third wall" not in raw:
                inner.setdefault(layer, []).append((idx, x, y, nx, ny, e, feat_f))
        if _word(raw, "X") is not None:
            x = nx
        if _word(raw, "Y") is not None:
            y = ny
    inserts: dict[int, list[str]] = {}
    for layer, layer_segs in segs.items():
        walls = inner.get(layer) or []
        if len(walls) < 3 or not layer_segs:
            continue
        inner_xy = [(a, b, c, d) for _i, a, b, c, d, _e, _f in walls]
        other = [(a, b, c, d) for a, b, c, d, r in layer_segs if r != "Inner wall"]
        polylines = _gap_polylines(inner_xy, other)
        ratio = INNER_E_PER_MM
        for poly in polylines:
            best_d = 1e9
            best = None
            wall_f = 0.0
            for widx, x0, y0, x1, y1, _e, wf in walls:
                for cand in (poly, list(reversed(poly))):
                    vx, vy = x1 - x0, y1 - y0
                    l2 = vx * vx + vy * vy
                    t = 0.0 if l2 == 0 else max(0.0, min(1.0, ((cand[0][0] - x0) * vx + (cand[0][1] - y0) * vy) / l2))
                    qx, qy = x0 + t * vx, y0 + t * vy
                    d = math.hypot(qx - cand[0][0], qy - cand[0][1])
                    if d < best_d:
                        best_d = d
                        best = (widx, x1, y1, qx, qy, cand)
                        wall_f = wf
            # The gap centre is about half the centreline spacing off each wall.
            if best is None or best_d > 2.6:
                continue
            widx, x1, y1, qx, qy, cand = best
            path = [(x1, y1), (qx, qy)] + cand + [(qx, qy), (x1, y1)]
            block = []
            px, py = path[0]
            for qx, qy in path[1:]:
                length = math.hypot(qx - px, qy - py)
                if length < 0.05:
                    px, py = qx, qy
                    continue
                fs = f" F{wall_f:g}" if wall_f and wall_f < TRAVEL_F - 1 else ""
                block.append(f"G1 X{qx:.3f} Y{qy:.3f} E{ratio * length:.5f}{fs} ;third wall\n")
                px, py = qx, qy
            if block:
                inserts.setdefault(widx, []).extend(block)
    if not inserts:
        return text
    out: list[str] = []
    for idx, raw in enumerate(lines):
        out.append(raw)
        extra = inserts.get(idx)
        if extra:
            out.extend(extra)
    return "".join(out)


def restore_feature_after_travel(text: str) -> str:
    """Put the feature feed back before an extrusion that would inherit travel F.

    Seam travels and the slicer's G0 moves both set F12720. A following
    extrusion with no feed word keeps that 212 mm/s.
    """
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    role = ""
    feat = 0.0
    modal_travel = False
    for raw in lines:
        if raw.startswith(";TYPE:"):
            role = raw[6:].strip()
            out.append(raw)
            continue
        if raw.startswith(";LAYER_CHANGE"):
            role = ""
            out.append(raw)
            continue
        body = raw.split(";", 1)[0].strip()
        if not body.startswith(("G0", "G1", "G2", "G3")):
            out.append(raw)
            continue
        f = _word(raw, "F")
        e = _word(raw, "E") or 0.0
        has_xy = _word(raw, "X") is not None or _word(raw, "Y") is not None
        if f is not None and f < TRAVEL_F - 1:
            feat = f
            modal_travel = False
        elif f is not None and f >= TRAVEL_F - 1 and e <= 0:
            modal_travel = True
        if e > 0 and has_xy and (modal_travel or (f is not None and f >= TRAVEL_F - 1)):
            role_f = _FEATURE_F.get(role) or feat
            if role_f and role_f < TRAVEL_F - 1:
                if f is not None and f >= TRAVEL_F - 1:
                    raw = re.sub(rf"\bF{_NUM}", f"F{role_f:g}", raw, count=1)
                elif f is None:
                    out.append(f"G1 F{role_f:g}\n")
                feat = role_f
            modal_travel = False
        out.append(raw)
    return "".join(out)


def apply_belt_seam_postprocess(text: str) -> str:
    _index_belt_lanes(text)
    outer_x: dict = {}
    text = rotate_wall_seams(text, _collect=outer_x, kinds=frozenset({"Outer wall"}))
    text = rotate_wall_seams(text, _inner_x=outer_x, kinds=frozenset({"Inner wall"}))
    text = rotate_wall_seams(
        text,
        _inner_x=outer_x,
        layer_lo=LATERAL_CUP_LO,
        layer_hi=LATERAL_CUP_HI,
        kinds=frozenset({"Inner wall"}),
        outer_glide=False,
    )
    text = rotate_wall_seams(
        text,
        _inner_x=outer_x,
        layer_lo=HEEL_END_CAP_LO,
        layer_hi=HEEL_END_CAP_HI,
        kinds=frozenset({"Outer wall", "Inner wall"}),
        outer_glide=False,
    )
    text = anchor_sparse_infill_belt(text)
    text = cap_end_cap_extrusion_speed(text)
    text = fix_off_belt_travels(text)
    text = fill_inner_wall_gaps(text)
    text = restore_feature_after_travel(text)
    return text
