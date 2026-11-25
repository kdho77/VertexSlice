"""Fill ApexLight forefoot hollows with Cura's sparse gyroid.

This is the band fill already in ``postprocess_g2b`` (``_band_paths`` and
``_band_block``): gyroid pieces in the empty columns. A straight connector
that leaves those columns is replaced by Cura combing: stay inside the
column, or walk a travel, wall bead, or extrusion centerline already in the
file. Every existing line is copied unchanged. Nothing is extruded into a
section the 0.25 mm hollow tool does not already call empty (outer thickness
>= 3 mm, air > 1 mm, no non-skin plastic in the air). Skins, seams, and
layer-change lines are not rewritten, so their stations stay the bytes the
slicer wrote. The ``; total filament used [g]`` footer is rewritten to the
same grams as ``; filament used [g]``.

ApexCore does not import this module. ``finalize_apex_light`` runs first;
this pass only sees the file that pass already emitted.
"""
from __future__ import annotations

import heapq
import math
import sys
from pathlib import Path

_COLUMN_MM = 0.25
_SECTION_MM = 3.0
_AIR_MM = 1.0
_SAMPLE_MM = 0.2
_COS_MIN = 0.25
_LEADER = {"Skirt", "Brim", "Support", "Support interface", "Prime tower", "Wipe tower"}
_HEIGHT_046 = 0.46
_FOREFOOT_046 = 165


def _g2b():
    root = Path(__file__).resolve().parents[2]
    g2b = root / "scripts" / "g2b_heelmod"
    if str(g2b) not in sys.path:
        sys.path.insert(0, str(g2b))
    import postprocess_g2b as mod

    return mod


def _skin_pair(beads: list[tuple[float, float, float]]):
    """(plantar_lo, plantar_hi, dorsal_lo, dorsal_hi, single) or None."""
    if not beads:
        return None
    lo_yc = min(b[2] for b in beads)
    hi_yc = max(b[2] for b in beads)
    if hi_yc - lo_yc < 0.20:
        lo = min(b[0] for b in beads)
        hi = max(b[1] for b in beads)
        return (lo, hi, lo, hi, True)
    mid = (lo_yc + hi_yc) * 0.5
    plow = phigh = dlow = dhigh = None
    for lo, hi, yc in beads:
        if yc <= mid:
            plow = lo if plow is None else min(plow, lo)
            phigh = hi if phigh is None else max(phigh, hi)
        if yc >= mid:
            dlow = lo if dlow is None else min(dlow, lo)
            dhigh = hi if dhigh is None else max(dhigh, hi)
    if plow is None or dlow is None:
        return None
    return (plow, phigh, dlow, dhigh, False)


def _section(pair) -> tuple[float, float]:
    plow, phigh, dlow, dhigh, single = pair
    if single:
        return dhigh - plow, -(dhigh - plow)
    return dhigh - plow, dlow - phigh


def hollow_columns(segs: list[dict], last_layer: int) -> dict[tuple[int, int], tuple[float, float]]:
    """(layer, 0.25 mm bin) -> (inner plantar face, inner dorsal face).

    Same column rules as the frozen 0.25 mm hollow tool: shallow skins only,
    the E-derived envelope, air strictly over 1 mm, and any non-skin bead
    that meets that air closes the column.
    """
    skins: dict[tuple[int, int], list[tuple[float, float, float]]] = {}
    cover: dict[tuple[int, int], list[tuple[float, float]]] = {}
    for seg in segs:
        layer = seg["layer"]
        if layer < 1 or layer > last_layer:
            continue
        if seg["type"] in _LEADER:
            continue
        x0, y0, x1, y1 = seg["x0"], seg["y0"], seg["x1"], seg["y1"]
        length = seg["L"]
        if length < 1e-6 or seg["e"] <= 1e-9:
            continue
        dx, dy = x1 - x0, y1 - y0
        cos = seg["cos"]
        width = seg["w"]
        if seg["kind"] == "skin":
            if cos < _COS_MIN:
                continue
            yhalf = (width * 0.5) / cos
            xa, xb = (x0, x1) if x0 <= x1 else (x1, x0)
            b0 = int(math.floor(xa / _COLUMN_MM))
            b1 = int(math.floor(xb / _COLUMN_MM))
            for binn in range(b0, b1 + 1):
                lo = max(xa, binn * _COLUMN_MM)
                hi = min(xb, (binn + 1) * _COLUMN_MM)
                if hi - lo <= 1e-9:
                    continue
                xc = (lo + hi) * 0.5
                if abs(dx) > 1e-12:
                    t = (xc - x0) / dx
                    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
                else:
                    t = 0.5
                yc = y0 + t * dy
                skins.setdefault((layer, binn), []).append((yc - yhalf, yc + yhalf, yc))
            continue
        yhalf = width * 0.5
        sin = abs(dy) / length
        xrad = yhalf * sin
        n = max(1, int(math.ceil(length / _SAMPLE_MM)))
        for i in range(n + 1):
            t = i / n
            x = x0 + dx * t
            y = y0 + dy * t
            along = min(t, 1.0 - t) * length
            rad = yhalf if along <= yhalf else xrad
            b0 = int(math.floor((x - rad) / _COLUMN_MM))
            b1 = int(math.floor((x + rad) / _COLUMN_MM))
            for binn in range(b0, b1 + 1):
                blo = binn * _COLUMN_MM
                bhi = blo + _COLUMN_MM
                if x + rad < blo or x - rad > bhi:
                    continue
                cover.setdefault((layer, binn), []).append((y - yhalf, y + yhalf))
    found: dict[tuple[int, int], tuple[float, float]] = {}
    for key, beads in skins.items():
        pair = _skin_pair(beads)
        if pair is None or pair[4]:
            continue
        thickness, air = _section(pair)
        if thickness < _SECTION_MM or air <= _AIR_MM:
            continue
        phigh, dlow = pair[1], pair[2]
        occupied = False
        for ya, yb in cover.get(key, ()):
            if min(yb, dlow) - max(ya, phigh) > 0.0:
                occupied = True
                break
        if not occupied:
            found[key] = (phigh, dlow)
    return found


def _bead_height(text: str) -> float | None:
    vals = []
    for line in text.splitlines():
        if not line.startswith(";HEIGHT:"):
            continue
        try:
            hv = float(line[8:])
        except ValueError:
            continue
        if 0.2 <= hv <= 0.55:
            vals.append(hv)
    if not vals:
        return None
    vals.sort()
    return vals[len(vals) // 2]


def _forefoot_limit(layer_count: int, text: str, g2b) -> int | None:
    """Orthocad forefoot only: layers 1-165, or the physical window at another bead height.

    ``insole_sample`` (about 40-220 layers) is not this defect. A partial fill
    there would change that file and leave the columns above layer 31 hollow,
    so those files are copied through.
    """
    last = g2b.forefoot_last_layer(layer_count)
    if last != g2b.FOREFOOT_ORTHOCAD_LAST:
        return None
    height = _bead_height(text)
    if height is None or height <= 1e-6:
        return last
    physical = int(round(_FOREFOOT_046 * _HEIGHT_046 / height))
    return max(last, physical)


def _words(g2b, line: str) -> dict[str, str]:
    return g2b._ff_words(line)


def _block_e(g2b, block: list[str]) -> float:
    total = 0.0
    for line in block:
        words = _words(g2b, line)
        if "E" in words:
            total += float(words["E"])
    return total


def _bump_filament(text: str, added_mm: float, area: float) -> str:
    if added_mm <= 1e-6:
        return text
    lines = text.splitlines(keepends=True)
    old_mm = None
    for line in lines:
        if line.startswith("; filament used [mm] = "):
            old_mm = float(line.split("=", 1)[1])
            break
    if old_mm is None:
        return text
    new_mm = old_mm + added_mm
    new_cm3 = new_mm * area / 1000.0
    old_cm3 = old_mm * area / 1000.0
    density = 1.2
    for line in lines:
        if line.startswith("; filament used [g] = ") and old_cm3 > 1e-9:
            density = float(line.split("=", 1)[1]) / old_cm3
            break

    def rewrite(line: str, value: float) -> str:
        body = line.split("=", 1)[1].strip()
        nd = len(body.split(".")[1]) if "." in body else 0
        nl = ""
        if line.endswith("\r\n"):
            nl = "\r\n"
        elif line.endswith("\n"):
            nl = "\n"
        prefix = line.split("=", 1)[0]
        return f"{prefix}= {value:.{nd}f}{nl}"

    out = []
    for line in lines:
        if line.startswith("; filament used [mm] = "):
            out.append(rewrite(line, new_mm))
        elif line.startswith("; filament used [cm3] = "):
            out.append(rewrite(line, new_cm3))
        elif line.startswith("; filament used [g] = "):
            out.append(rewrite(line, new_cm3 * density))
        elif line.startswith("; total filament used [g] = "):
            # One extruder: the total is the same grams as the line above.
            out.append(rewrite(line, new_cm3 * density))
        else:
            out.append(line)
    return "".join(out)


def _newline(lines: list[str]) -> str:
    for line in lines:
        if line.endswith("\r\n"):
            return "\r\n"
        if line.endswith("\n"):
            return "\n"
    return "\n"


_WALLS = {"Outer wall", "Overhang wall"}
_ON_PATH_MM = 0.02
_HOLLOW_SLACK = 0.05


def _rnd(v: float) -> float:
    return round(v, 3)


def _node(x: float, y: float) -> tuple[float, float]:
    return (_rnd(x), _rnd(y))


def _dist_seg(px: float, py: float, x0: float, y0: float, x1: float, y1: float) -> float:
    dx, dy = x1 - x0, y1 - y0
    length_sq = dx * dx + dy * dy
    if length_sq < 1e-18:
        return math.hypot(px - x0, py - y0)
    t = ((px - x0) * dx + (py - y0) * dy) / length_sq
    if t < 0.0:
        t = 0.0
    elif t > 1.0:
        t = 1.0
    return math.hypot(px - (x0 + t * dx), py - (y0 + t * dy))


def _near_any(x: float, y: float, segs, tol: float) -> bool:
    for x0, y0, x1, y1 in segs:
        if _dist_seg(x, y, x0, y0, x1, y1) <= tol:
            return True
    return False


def _sample_count(x0: float, y0: float, x1: float, y1: float) -> int:
    return max(1, int(math.ceil(math.hypot(x1 - x0, y1 - y0) / _COLUMN_MM)))


def _in_hollow(hollow, layer: int, x: float, y: float) -> bool:
    gap = hollow.get((layer, int(math.floor(x / _COLUMN_MM))))
    return gap is not None and gap[0] - _HOLLOW_SLACK <= y <= gap[1] + _HOLLOW_SLACK


def _sample_confined(hollow, layer: int, x: float, y: float, net: dict) -> bool:
    """Inside a hollow column, on a live travel, on a wall bead, or on a live centerline."""
    if _in_hollow(hollow, layer, x, y):
        return True
    if _near_any(x, y, net["travels"], _ON_PATH_MM):
        return True
    for x0, y0, x1, y1, half in net["walls"]:
        if _dist_seg(x, y, x0, y0, x1, y1) <= half + _ON_PATH_MM:
            return True
    return _near_any(x, y, net["centers"], _ON_PATH_MM)


def _segment_confined(hollow, layer: int, x0: float, y0: float, x1: float, y1: float, net: dict) -> bool:
    n = _sample_count(x0, y0, x1, y1)
    for i in range(n):
        t = (i + 0.5) / n
        if not _sample_confined(hollow, layer, x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, net):
            return False
    return True


def _components(hollow, layer: int) -> list[list[int]]:
    bins = sorted(b for (lay, b) in hollow if lay == layer)
    comps: list[list[int]] = []
    cur: list[int] = []

    def overlaps(a: int, b: int) -> bool:
        ga, gb = hollow[(layer, a)], hollow[(layer, b)]
        return min(ga[1], gb[1]) + _HOLLOW_SLACK >= max(ga[0], gb[0]) - _HOLLOW_SLACK

    for binn in bins:
        if cur and (binn != cur[-1] + 1 or not overlaps(cur[-1], binn)):
            comps.append(cur)
            cur = []
        cur.append(binn)
    if cur:
        comps.append(cur)
    return comps


def _comp_of(hollow, bin_comp: dict[int, int], layer: int, x: float, y: float):
    binn = int(math.floor(x / _COLUMN_MM))
    for bb in (binn, binn - 1, binn + 1):
        gap = hollow.get((layer, bb))
        if gap is None or bb not in bin_comp:
            continue
        if not (gap[0] - _HOLLOW_SLACK <= y <= gap[1] + _HOLLOW_SLACK):
            continue
        if bb * _COLUMN_MM - _ON_PATH_MM <= x <= (bb + 1) * _COLUMN_MM + _ON_PATH_MM:
            return bin_comp[bb]
    return None


def _closest_on_rect(x0, y0, x1, y1, xa, xb, ya, yb):
    length = math.hypot(x1 - x0, y1 - y0)
    n = max(1, int(math.ceil(length / 0.05)))
    best = None
    for i in range(n + 1):
        t = i / n
        px = x0 + (x1 - x0) * t
        py = y0 + (y1 - y0) * t
        cx = min(max(px, xa), xb)
        cy = min(max(py, ya), yb)
        dist = math.hypot(px - cx, py - cy)
        if best is None or dist < best[0]:
            best = (dist, (cx, cy), (px, py))
    return best


def _link(adj, a, b, cost: float) -> None:
    if a == b:
        return
    adj.setdefault(a, []).append((b, cost))
    adj.setdefault(b, []).append((a, cost))


def _split_segment(adj, x0, y0, x1, y1, qx, qy, mult: float) -> None:
    """Put (qx, qy) on the segment so a dock can leave from there."""
    q = _node(qx, qy)
    a, b = _node(x0, y0), _node(x1, y1)
    _link(adj, a, q, math.hypot(qx - x0, qy - y0) * mult)
    _link(adj, q, b, math.hypot(x1 - qx, y1 - qy) * mult)


def _inside_poly(hollow, layer: int, start, dest) -> list[tuple[float, float]] | None:
    """A polyline from start to dest that the hollow-column sampler accepts."""
    x0, y0 = start
    x1, y1 = dest
    if _straight_inside(hollow, layer, x0, y0, x1, y1):
        return [start, dest]
    b0 = int(math.floor(x0 / _COLUMN_MM))
    b1 = int(math.floor(x1 / _COLUMN_MM))
    if b0 == b1:
        return None
    step = 1 if b1 > b0 else -1
    pts = [start]
    y = y0
    binn = b0
    while binn != b1:
        nxt = binn + step
        gap = hollow.get((layer, binn))
        ngap = hollow.get((layer, nxt))
        if gap is None or ngap is None:
            return None
        lo = max(gap[0], ngap[0])
        hi = min(gap[1], ngap[1])
        if lo > hi:
            lo = max(gap[0], ngap[0]) - _HOLLOW_SLACK
            hi = min(gap[1], ngap[1]) + _HOLLOW_SLACK
        if lo > hi:
            return None
        # Stay off the exact edges so a midpoint still lands in the gap.
        pad = min(_HOLLOW_SLACK * 0.5, max(0.0, (hi - lo) * 0.25))
        ny = min(max(y1, lo + pad), hi - pad)
        boundary = nxt * _COLUMN_MM if step > 0 else binn * _COLUMN_MM
        # Vertical move inside the current bin, then across the shared edge.
        vx = min(max(pts[-1][0], binn * _COLUMN_MM + 1e-3), (binn + 1) * _COLUMN_MM - 1e-3)
        if abs(vx - pts[-1][0]) > 1e-6:
            pts.append((vx, pts[-1][1]))
        if abs(ny - pts[-1][1]) > 1e-6:
            pts.append((vx, ny))
        hx = boundary - 1e-3 * step
        pts.append((hx, ny))
        y = ny
        binn = nxt
    end_x = min(max(x1, b1 * _COLUMN_MM + 1e-3), (b1 + 1) * _COLUMN_MM - 1e-3)
    if abs(pts[-1][0] - end_x) > 1e-6 or abs(pts[-1][1] - y) > 1e-6:
        pts.append((end_x, y))
    if abs(y - y1) > 1e-6:
        pts.append((end_x, y1))
    pts.append(dest)
    cleaned = [pts[0]]
    for px, py in pts[1:]:
        if math.hypot(px - cleaned[-1][0], py - cleaned[-1][1]) > 1e-6:
            cleaned.append((px, py))
    for a, b in zip(cleaned, cleaned[1:]):
        if not _straight_inside(hollow, layer, a[0], a[1], b[0], b[1]):
            return None
    return cleaned


def _straight_inside(hollow, layer, x0, y0, x1, y1) -> bool:
    n = _sample_count(x0, y0, x1, y1)
    for i in range(n):
        t = (i + 0.5) / n
        if not _in_hollow(hollow, layer, x0 + (x1 - x0) * t, y0 + (y1 - y0) * t):
            return False
    return True


def _layer_network(hollow, layer: int, travels, walls, centers) -> dict:
    comps = _components(hollow, layer)
    bin_comp = {}
    for cid, bins in enumerate(comps):
        for binn in bins:
            bin_comp[binn] = cid
    adj: dict[tuple[float, float], list] = {}
    for x0, y0, x1, y1 in travels:
        _link(adj, _node(x0, y0), _node(x1, y1), math.hypot(x1 - x0, y1 - y0))
    for x0, y0, x1, y1, _half in walls:
        _link(adj, _node(x0, y0), _node(x1, y1), math.hypot(x1 - x0, y1 - y0))
    # Existing extrusion centerlines are only the fallback that reaches a station
    # the wall and travel graph does not already contain.
    for x0, y0, x1, y1 in centers:
        _link(adj, _node(x0, y0), _node(x1, y1), math.hypot(x1 - x0, y1 - y0) * 30.0)
    docks = {}
    for cid, bins in enumerate(comps):
        best = None
        for binn in bins:
            lo, hi = hollow[(layer, binn)]
            xa, xb = binn * _COLUMN_MM, (binn + 1) * _COLUMN_MM
            for x0, y0, x1, y1, half in walls:
                if max(x0, x1) < xa - 1.5 or min(x0, x1) > xb + 1.5:
                    continue
                if max(y0, y1) < lo - 1.5 or min(y0, y1) > hi + 1.5:
                    continue
                hit = _closest_on_rect(x0, y0, x1, y1, xa, xb, lo, hi)
                if hit is None:
                    continue
                dist, c_pt, q_pt = hit
                slack = dist - half
                if best is None or slack < best[0]:
                    best = (slack, c_pt, q_pt, (x0, y0, x1, y1), half, binn)
        if best is None or best[0] > _ON_PATH_MM:
            continue
        _slack, c_pt, q_pt, wall, half, binn = best
        # The closest rectangle point sits on the closed edge. Step 0.001 mm
        # into that same cell so the column test and the bead test both see it.
        cx, cy = c_pt
        lo, hi = hollow[(layer, binn)]
        xa, xb = binn * _COLUMN_MM, (binn + 1) * _COLUMN_MM
        cx = min(max(cx, xa + 1e-3), xb - 1e-3)
        cy = min(max(cy, lo + 1e-3), hi - 1e-3)
        qx, qy = q_pt
        if not _in_hollow(hollow, layer, cx, cy):
            continue
        if _dist_seg(cx, cy, wall[0], wall[1], wall[2], wall[3]) > half + _ON_PATH_MM:
            continue
        _split_segment(adj, wall[0], wall[1], wall[2], wall[3], qx, qy, 1.0)
        _link(adj, _node(cx, cy), _node(qx, qy), math.hypot(qx - cx, qy - cy))
        docks[cid] = ((cx, cy), (qx, qy))
    return {
        "adj": adj,
        "travels": travels,
        "walls": walls,
        "centers": centers,
        "docks": docks,
        "bin_comp": bin_comp,
    }


def _collect_networks(lines: list[str], segs: list[dict], hollow, last_layer: int) -> dict[int, dict]:
    travels: dict[int, list] = {}
    walls: dict[int, list] = {}
    centers: dict[int, list] = {}
    layer = -1
    x = y = 0.0
    for line in lines:
        raw = line.strip()
        if raw.startswith(";LAYER_CHANGE"):
            layer += 1
            continue
        body = raw.split(";", 1)[0].strip()
        if not (body.startswith("G0") or body.startswith("G1")):
            continue
        words = {}
        for tok in body.split():
            if len(tok) > 1 and tok[0] in "XYZE":
                try:
                    words[tok[0]] = float(tok[1:])
                except ValueError:
                    pass
        nx = words.get("X", x)
        ny = words.get("Y", y)
        moved = math.hypot(nx - x, ny - y) > 1e-6
        if 1 <= layer <= last_layer and moved and "E" not in words and "Z" not in words:
            travels.setdefault(layer, []).append((x, y, nx, ny))
        x, y = nx, ny
    for seg in segs:
        lay = seg["layer"]
        if lay < 1 or lay > last_layer:
            continue
        piece = (seg["x0"], seg["y0"], seg["x1"], seg["y1"])
        centers.setdefault(lay, []).append(piece)
        if seg.get("type") in _WALLS:
            walls.setdefault(lay, []).append((*piece, seg["w"] * 0.5))
    out = {}
    layers = {lay for (lay, _b) in hollow if 1 <= lay <= last_layer}
    layers.update(travels)
    for lay in layers:
        if lay < 1 or lay > last_layer:
            continue
        out[lay] = _layer_network(hollow, lay, travels.get(lay, []), walls.get(lay, []), centers.get(lay, []))
    return out


def _connect_point(net, hollow, layer: int, pt) -> tuple[float, float]:
    adj = net["adj"]
    key = _node(*pt)
    adj.setdefault(key, [])
    comp = _comp_of(hollow, net["bin_comp"], layer, pt[0], pt[1])
    dock = net["docks"].get(comp) if comp is not None else None
    if dock is None:
        return key
    c_pt, _q_pt = dock
    if math.hypot(pt[0] - c_pt[0], pt[1] - c_pt[1]) <= _ON_PATH_MM:
        _link(adj, key, _node(*c_pt), math.hypot(pt[0] - c_pt[0], pt[1] - c_pt[1]))
        return key
    poly = _inside_poly(hollow, layer, pt, c_pt)
    if not poly:
        return key
    prev = pt
    prev_key = key
    for nxt in poly[1:]:
        nxt_key = _node(*nxt)
        adj.setdefault(nxt_key, [])
        _link(adj, prev_key, nxt_key, math.hypot(nxt[0] - prev[0], nxt[1] - prev[1]))
        prev, prev_key = nxt, nxt_key
    return key


def _route(net, hollow, layer: int, start, dest) -> list[tuple[float, float]] | None:
    if _segment_confined(hollow, layer, start[0], start[1], dest[0], dest[1], net):
        return [start, dest]
    comp_a = _comp_of(hollow, net["bin_comp"], layer, start[0], start[1])
    comp_b = _comp_of(hollow, net["bin_comp"], layer, dest[0], dest[1])
    if comp_a is not None and comp_a == comp_b:
        poly = _inside_poly(hollow, layer, start, dest)
        if poly:
            return poly
    src = _connect_point(net, hollow, layer, start)
    dst = _connect_point(net, hollow, layer, dest)
    adj = net["adj"]
    if src not in adj or dst not in adj:
        return None
    dist = {src: 0.0}
    prev = {src: None}
    heap = [(0.0, src)]
    found = False
    while heap:
        cost, node = heapq.heappop(heap)
        if cost != dist.get(node):
            continue
        if node == dst:
            found = True
            break
        for nxt, step in adj.get(node, ()):
            nxt_cost = cost + step
            if nxt_cost < dist.get(nxt, 1e18):
                dist[nxt] = nxt_cost
                prev[nxt] = node
                heapq.heappush(heap, (nxt_cost, nxt))
    if not found:
        return None
    path = []
    node = dst
    while node is not None:
        path.append(node)
        node = prev[node]
    path.reverse()
    if not path:
        return None
    path[0] = start
    path[-1] = dest
    cleaned = [path[0]]
    for px, py in path[1:]:
        if math.hypot(px - cleaned[-1][0], py - cleaned[-1][1]) > 1e-6:
            cleaned.append((px, py))
    for a, b in zip(cleaned, cleaned[1:]):
        if not _segment_confined(hollow, layer, a[0], a[1], b[0], b[1], net):
            return None
    return cleaned


def _emit_travel(g2b, points, feed: str | None, nl: str) -> list[str]:
    out = []
    for i, (px, py) in enumerate(points):
        line = f"G1 X{g2b._fmt(px, 3)} Y{g2b._fmt(py, 3)}"
        if i == 0 and feed:
            line += f" F{feed}"
        out.append(line + nl)
    return out


def _confine_block(block: list[str], layer: int, start, net: dict, hollow, g2b, nl: str) -> list[str]:
    """Replace sparse-infill travels that leave the hollow columns.

    A kept or replacement segment stays inside a hollow column, or on a live
    travel, a live wall bead, or a live extrusion centerline. That is Cura's
    comb: the straight hop when it fits, otherwise the path already in the file.
    """
    if not net:
        return block
    out: list[str] = []
    x, y = start
    i = 0
    while i < len(block):
        nxt = _xy_of_travel(g2b, block[i], x, y)
        if nxt is None:
            out.append(block[i])
            moved = _xy_of_move(g2b, block[i], x, y)
            if moved is not None:
                x, y = moved
            i += 1
            continue
        seq = []
        cx, cy = x, y
        while i < len(block):
            step = _xy_of_travel(g2b, block[i], cx, cy)
            if step is None:
                break
            seq.append(block[i])
            cx, cy = step
            i += 1
        legal = True
        px, py = x, y
        for line in seq:
            nx, ny = _xy_of_travel(g2b, line, px, py)
            if not _segment_confined(hollow, layer, px, py, nx, ny, net):
                legal = False
                break
            px, py = nx, ny
        if legal:
            out.extend(seq)
            x, y = cx, cy
            continue
        routed = _route(net, hollow, layer, (x, y), (cx, cy))
        if not routed or len(routed) < 2:
            out.extend(seq)
            x, y = cx, cy
            continue
        feed = None
        for line in seq:
            words = g2b._ff_words(line.split(";", 1)[0])
            if "F" in words:
                feed = words["F"]
                break
        length = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(routed, routed[1:]))
        wrapped = bool(out) and _is_retract(out[-1]) and i < len(block) and _is_prime(block[i])
        if length > 2.0 and not wrapped and not (out and _is_retract(out[-1])):
            out.append(f"G1 E-{g2b.RETRACT:.1f} F{g2b.RETRACT_F:.0f}{nl}")
        out.extend(_emit_travel(g2b, routed[1:], feed, nl))
        if length > 2.0 and not wrapped and not (i < len(block) and _is_prime(block[i])):
            out.append(f"G1 E{g2b.PRIME:.2f} F{g2b.RETRACT_F:.0f}{nl}")
        x, y = cx, cy
    return out


def _xy_of_move(g2b, line: str, x: float, y: float):
    body = line.split(";", 1)[0].strip()
    if not (body.startswith("G0") or body.startswith("G1")):
        return None
    words = g2b._ff_words(body)
    if "X" not in words and "Y" not in words:
        return None
    nx = float(words["X"]) if "X" in words else x
    ny = float(words["Y"]) if "Y" in words else y
    return nx, ny


def _xy_of_travel(g2b, line: str, x: float, y: float):
    body = line.split(";", 1)[0].strip()
    if not (body.startswith("G0") or body.startswith("G1")):
        return None
    words = g2b._ff_words(body)
    if "E" in words or "Z" in words:
        return None
    if "X" not in words and "Y" not in words:
        return None
    nx = float(words["X"]) if "X" in words else x
    ny = float(words["Y"]) if "Y" in words else y
    if abs(nx - x) < 1e-9 and abs(ny - y) < 1e-9:
        return None
    return nx, ny


def _is_retract(line: str) -> bool:
    body = line.split(";", 1)[0]
    return "E-" in body and "X" not in body.split(";")[0] and "Y" not in body


def _is_prime(line: str) -> bool:
    body = line.split(";", 1)[0]
    return "E" in body and "E-" not in body and "X" not in body and "Y" not in body


def fill_forefoot_hollow(text: str, last_layer: int | None = None) -> tuple[str, dict]:
    """Add Cura sparse infill into hollow forefoot columns. Other lines stay.

    ``last_layer`` overrides the cutoff. Production leaves it unset and only
    fills an orthocad-length file (layers 1-165, or the physical window when
    the bead height is not 0.46 mm).
    """
    g2b = _g2b()
    lines = text.splitlines(keepends=True)
    parsed = g2b._parse_moves(lines, reset_type=False)
    if last_layer is None:
        last_layer = _forefoot_limit(parsed["layers"], text, g2b)
    stats: dict = {
        "applied": False,
        "last_layer": last_layer,
        "hollow_before": 0,
        "hollow_after": 0,
        "layers": 0,
        "extrusions": 0,
        "added_mm": 0.0,
    }
    if not last_layer or last_layer < 1:
        stats["reason"] = "out of scope"
        return text, stats
    hollow = hollow_columns(parsed["segs"], last_layer)
    stats["hollow_before"] = len(hollow)
    if not hollow:
        stats["reason"] = "no hollow"
        return text, stats
    by_layer: dict[int, dict[int, tuple[float, float]]] = {}
    for (layer, binn), gap in hollow.items():
        by_layer.setdefault(layer, {})[binn] = gap

    nl = _newline(lines)
    layer = -1
    x = y = 0.0
    feed = 0.0
    absolute = True
    e_rel = False
    typ = ""
    width = ""
    z_at: dict[int, float] = {}
    end_at: dict[int, int] = {}
    state: dict[int, tuple[float, float, float, str, str, bool]] = {}
    for index, line in enumerate(lines):
        raw = line.strip()
        if raw.startswith(";LAYER_CHANGE"):
            if layer >= 0:
                end_at[layer] = index
                state[layer] = (x, y, feed, typ, width, e_rel)
            layer += 1
            continue
        if raw.startswith(";TYPE:"):
            typ = raw[6:].strip()
            continue
        if raw.startswith(";WIDTH:"):
            width = raw
            continue
        if raw.startswith(";Z:") and layer not in z_at:
            try:
                z_at[layer] = float(raw[3:])
            except ValueError:
                pass
            continue
        body = raw.split(";", 1)[0].strip()
        if body.startswith("G90"):
            absolute = True
            continue
        if body.startswith("G91"):
            if layer >= 0 and layer not in end_at:
                end_at[layer] = index
                state[layer] = (x, y, feed, typ, width, e_rel)
            break
        if body.startswith("M83"):
            e_rel = True
            continue
        if body.startswith("M82"):
            e_rel = False
            continue
        if not g2b._is_g01(line):
            continue
        words = _words(g2b, body)
        if "X" in words:
            x = float(words["X"]) if absolute else x + float(words["X"])
        if "Y" in words:
            y = float(words["Y"]) if absolute else y + float(words["Y"])
        if "F" in words:
            feed = float(words["F"])
    if layer >= 0 and layer not in end_at:
        end_at[layer] = len(lines)
        state[layer] = (x, y, feed, typ, width, e_rel)

    edges: dict[int, list[tuple[float, float, float, float]]] = {}
    for seg in parsed["segs"]:
        if seg["kind"] == "skin" and 1 <= seg["layer"] <= last_layer:
            edges.setdefault(seg["layer"], []).append((seg["x0"], seg["y0"], seg["x1"], seg["y1"]))
    networks = _collect_networks(lines, parsed["segs"], hollow, last_layer)

    blocks: dict[int, list[str]] = {}
    added_mm = 0.0
    n_ext = 0
    for lay, cols in by_layer.items():
        if lay not in state:
            continue
        ex, ey, fd, tp, wd, rel = state[lay]
        if not rel or not absolute:
            stats["reason"] = "extrusion or positioning is not the ApexLight mode"
            return text, stats
        paths = g2b._band_paths(z_at.get(lay), cols)
        if not paths:
            continue
        block = g2b._band_block(paths, ex, ey, fd, tp, wd, edges.get(lay, []), nl)
        block = _confine_block(block, lay, (ex, ey), networks.get(lay), hollow, g2b, nl)
        n_ext += sum(1 for line in block if " E" in line.split(";", 1)[0] and "E-" not in line.split(";", 1)[0])
        added_mm += _block_e(g2b, block)
        blocks[end_at[lay]] = block
    if not blocks:
        stats["reason"] = "nothing to insert"
        return text, stats
    out: list[str] = []
    for index, line in enumerate(lines):
        if index in blocks:
            out.extend(blocks[index])
        out.append(line)
    if len(lines) in blocks:
        out.extend(blocks[len(lines)])
    joined = "".join(out)
    joined = _bump_filament(joined, added_mm, parsed["area"])
    after = g2b._parse_moves(joined.splitlines(keepends=True), reset_type=False)
    stats["applied"] = True
    stats["layers"] = len(blocks)
    stats["extrusions"] = n_ext
    stats["added_mm"] = round(added_mm, 5)
    stats["hollow_after"] = len(hollow_columns(after["segs"], last_layer))
    return joined, stats
