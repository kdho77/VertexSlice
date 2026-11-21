#!/usr/bin/env python3
"""G2b post-process: shear E fix, off-belt seam, min-layer-time, coupon cut.

efix rescales every extruding XY move from the belt-shear length
sqrt(dx^2+2*dy^2) back to the true XY length. It never changes F.

Closed outer and overhang walls start at the belt-side (min-Y) edge
midpoint, the same rotate as PRE-29, including the short-edge vertex
fallback. The joint is a butt: the loop ends on its own start. The 0.7 mm
dry glide follows, along the last segment, at 60% of that segment's feed.
Retract stays 0.5 mm and the prime equals it. On a closed outer or overhang
loop the first 10 mm after the seam and the last 10 mm into it run at
F1200, unless that stretch was already slower; a move is split only at
those two arc lengths. Inner walls still restart on the belt
contact at the outer seam's X. A seam travel whose lifted chord would cross
the outer wall is routed inside the wall and lands on the seam start.
ApexLight does not run this seam pass. Its post-edit is the production fan
lock, the pp_c E-pair drop, and the inside-wall travel walk, with heel-route
E±1.5 left as written.

Infill travels get the lift only (no retract). E on split edges is divided
by true XY length so outer E/mm stays flat across a layer.

Min-time slows a layer whose true-XY time is under 8 s. Feed is scaled and
clamped to a 10 mm/s floor. E is not touched.
"""
from __future__ import annotations

import math
import re
import sys
from pathlib import Path

WORD = re.compile(r"([XYZEF])([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)")
WALLS = {"Outer wall", "Inner wall", "Overhang wall"}
INFILL = {
    "Sparse infill",
    "Internal solid infill",
    "Gap infill",
    "Top surface",
    "Bottom surface",
    "Bridge",
    "Internal Bridge",
}
RETRACT = 0.5
PRIME = 0.5
LIFT = 0.4
RETRACT_F = 1800
TRAVEL_F = 12720  # 212 mm/s
WIPE_MM = 0.7
WIPE_FRAC = 0.6
# First and last 10 mm of a closed outer/overhang loop. 20 mm/s, Cura's belt
# wall speed. A feed already at or below this stays as written.
SLOW_END_F = 1200.0
SLOW_END_MM = 10.0
# Extruded past the joint, along the wall the loop just started. The dry wipe
# still follows, so the join is covered instead of left short.
OVERLAP_MM = 0.4
_EDGE_BAND = 0.25
_EDGE_MIN_LEN = 2.0
# Just past the 0.7 mm wipe, so a loop the slicer actually closed still joins.
_CLOSED_GAP_MM = 0.8
# The G2b block's nozzle is 1 mm. Two nozzles is still one seam: the ends are
# a joint that missed, not an open wall. The Jabbour R tab windows at
# Z 24.72–27.97 sit past 0.8 mm and were left on the belt.
CLOSED_GAP_MM = 2.0
# Overlap on a chain this short replays the first leg (step = min(0.4, span))
# and that extra wall cuts the lift-and-direct chord. The chord ends 0.4 mm
# off the seam, outside the checker's 0.3 mm window, so the hit is a crossing.
# 3 mm is under a real outer perimeter and over that arch/heel nub.
SHORT_LOOP_MM = 3.0


def _body(line: str) -> str:
    return line.split(";", 1)[0].strip()


def _words(line: str) -> dict[str, str]:
    return dict(WORD.findall(_body(line)))


def _is_g01(line: str) -> bool:
    b = _body(line)
    return b.startswith("G0 ") or b.startswith("G1 ") or b in ("G0", "G1") or b.startswith("G0") or b.startswith("G1")


def efix(text: str) -> tuple[str, int]:
    """E *= hypot(dx,dy) / sqrt(dx^2+2*dy^2) on extruding XY moves. Relative E only."""
    out: list[str] = []
    x = y = 0.0
    n = 0
    for line in text.splitlines(keepends=True):
        body, sep, com = line.partition(";")
        b = body.strip()
        if b.startswith(("G0 ", "G1 ", "G0", "G1")):
            d = dict(WORD.findall(b))
            nx = float(d["X"]) if "X" in d else x
            ny = float(d["Y"]) if "Y" in d else y
            if "E" in d and float(d["E"]) > 0 and ("X" in d or "Y" in d):
                dx, dy = nx - x, ny - y
                den = math.sqrt(dx * dx + 2 * dy * dy)
                if den > 0:
                    e = float(d["E"]) * math.hypot(dx, dy) / den
                    body = re.sub(r"E[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?", f"E{e:.5f}", body, count=1)
                    n += 1
                    line = body + sep + com
            x, y = nx, ny
        out.append(line)
    return "".join(out), n


def _fmt(n: float, nd: int) -> str:
    return f"{n:.{nd}f}"


def _chain_length(segs: list[dict]) -> float:
    return sum(math.hypot(s["x1"] - s["x0"], s["y1"] - s["y0"]) for s in segs)


def _end_gap(segs: list[dict]) -> float:
    return math.hypot(segs[-1]["x1"] - segs[0]["x0"], segs[-1]["y1"] - segs[0]["y0"])


def _is_closed_loop(segs: list[dict]) -> bool:
    """True when the chain is a loop, including one the 0.8 mm test called open.

    A gap within two 1 mm nozzles is a seam that did not land, and only when
    the path is at least four times that gap. A short open curve whose ends
    happen to sit near each other stays open.
    """
    if not segs:
        return False
    gap = _end_gap(segs)
    if gap <= _CLOSED_GAP_MM:
        return True
    if len(segs) < 2:
        return False
    length = _chain_length(segs)
    return gap <= CLOSED_GAP_MM and length >= gap * 4.0


def _belt_split(segments: list[dict], closed_gap: float = _CLOSED_GAP_MM) -> list[dict]:
    """Rotate a closed wall loop so it starts at the belt-side (min-Y) edge midpoint."""
    if len(segments) < 3:
        return segments
    pts = [(segments[0]["x0"], segments[0]["y0"])] + [(s["x1"], s["y1"]) for s in segments]
    if _end_gap(segments) > closed_gap:
        return segments
    ymin = min(p[1] for p in pts)
    best_i = None
    best_len = -1.0
    for i, s in enumerate(segments):
        if s["y0"] <= ymin + 0.25 and s["y1"] <= ymin + 0.25:
            length = math.hypot(s["x1"] - s["x0"], s["y1"] - s["y0"])
            if length > best_len:
                best_len = length
                best_i = i
    if best_i is None or best_len < 2.0:
        # Fall back to the vertex nearest the belt, ties broken toward the loop's X mid-span.
        xs = [p[0] for p in pts]
        mid_x = 0.5 * (min(xs) + max(xs))
        best_d = 1e9
        best_i = 0
        for i, s in enumerate(segments):
            d = (s["y0"] - ymin) * 1000 + abs(s["x0"] - mid_x)
            if d < best_d:
                best_d = d
                best_i = i
        return segments[best_i:] + segments[:best_i]

    s = segments[best_i]
    length = math.hypot(s["x1"] - s["x0"], s["y1"] - s["y0"]) or 1.0
    t = 0.5
    mx = s["x0"] + (s["x1"] - s["x0"]) * t
    my = s["y0"] + (s["y1"] - s["y0"]) * t
    e = s["e"]
    f = s["f"]
    comments = s["comments"]

    def _synth(x1: float, y1: float, ee: float, ff: str | None) -> str:
        parts = ["G1", f"X{_fmt(x1, 3)}", f"Y{_fmt(y1, 3)}", f"E{ee:.5f}"]
        if ff:
            parts.append(f"F{float(ff):.0f}")
        return " ".join(parts) + "\n"

    # Start at the midpoint: midpoint → edge end, rest of the loop, then edge start → midpoint.
    b = dict(s)
    b.update(x0=mx, y0=my, e=e * (1 - t), raw=_synth(s["x1"], s["y1"], e * (1 - t), f), comments=list(comments))
    a = dict(s)
    a.update(x1=mx, y1=my, e=e * t, raw=_synth(mx, my, e * t, f), comments=list(comments))
    return [b] + segments[best_i + 1 :] + segments[:best_i] + [a]


def _start_on_segment(segments: list[dict], i: int, t: float) -> list[dict]:
    """Rotate a closed loop so it begins at fraction t of segment i. E/mm stays flat."""
    n = len(segments)
    if t <= 1e-4:
        return segments[i:] + segments[:i]
    if t >= 1.0 - 1e-4:
        j = (i + 1) % n
        return segments[j:] + segments[:j]
    s = segments[i]
    mx = s["x0"] + (s["x1"] - s["x0"]) * t
    my = s["y0"] + (s["y1"] - s["y0"]) * t
    e = s["e"]
    f = s["f"]
    comments = list(s["comments"])

    def _synth(x1: float, y1: float, ee: float, ff: str | None) -> str:
        parts = ["G1", f"X{_fmt(x1, 3)}", f"Y{_fmt(y1, 3)}", f"E{ee:.5f}"]
        if ff:
            parts.append(f"F{float(ff):.0f}")
        return " ".join(parts) + "\n"

    b = dict(s)
    b.update(x0=mx, y0=my, e=e * (1 - t), raw=_synth(s["x1"], s["y1"], e * (1 - t), f), comments=list(comments))
    a = dict(s)
    a.update(x1=mx, y1=my, e=e * t, raw=_synth(mx, my, e * t, f), comments=list(comments))
    return [b] + segments[i + 1 :] + segments[:i] + [a]


def _longest_edge_run(segments: list[dict], on_edge) -> tuple[int, int, float] | None:
    """Longest consecutive run of on-edge segments, wrapping the closed loop.

    Returns (start index, count, length).
    """
    n = len(segments)
    flags = [bool(on_edge(s)) for s in segments]
    if not any(flags):
        return None
    if all(flags):
        runs = [(0, n)]
    else:
        shift = flags.index(False)
        rotated = flags[shift:] + flags[:shift]
        runs = []
        i = 0
        while i < n:
            if not rotated[i]:
                i += 1
                continue
            j = i
            while j < n and rotated[j]:
                j += 1
            runs.append(((i + shift) % n, j - i))
            i = j
    best = None
    for start, count in runs:
        length = 0.0
        for k in range(count):
            s = segments[(start + k) % n]
            length += math.hypot(s["x1"] - s["x0"], s["y1"] - s["y0"])
        if best is None or length > best[2]:
            best = (start, count, length)
    return best


def _run_midpoint(segments: list[dict], start: int, count: int) -> tuple[int, float]:
    """Segment index and fraction where the run's arc-length midpoint falls."""
    n = len(segments)
    lengths = []
    for k in range(count):
        s = segments[(start + k) % n]
        lengths.append(math.hypot(s["x1"] - s["x0"], s["y1"] - s["y0"]))
    half = 0.5 * sum(lengths)
    acc = 0.0
    for k, length in enumerate(lengths):
        if acc + length >= half - 1e-9:
            t = 0.0 if length <= 1e-9 else (half - acc) / length
            return (start + k) % n, min(1.0, max(0.0, t))
        acc += length
    return (start + count - 1) % n, 1.0


def _dorsal_vertex_start(segments: list[dict], pts: list[tuple[float, float]]) -> list[dict]:
    """Start at the dorsal-most vertex. Ties break toward a lateral end, off the midline."""
    ymax = max(p[1] for p in pts)
    xs = [p[0] for p in pts]
    mid_x = 0.5 * (min(xs) + max(xs))
    best_d = 1e18
    best_i = 0
    for i, s in enumerate(segments):
        d = (ymax - s["y0"]) * 1000.0 - abs(s["x0"] - mid_x)
        if d < best_d:
            best_d = d
            best_i = i
    return segments[best_i:] + segments[:best_i]


def _bridge_near_gap(segments: list[dict]) -> list[dict]:
    """Extrude the opening of a near-closed loop before it is rotated.

    The ends miss by more than the 0.8 mm margin. Rotating the raw lines
    leaves the nozzle on one end and the next line aimed at the far end of
    the first leg, so the wall becomes a chord across the part. A gap-infill
    hop that missed the real wall then crosses that chord. The bridge is the
    short joint between the ends, at the same E/mm as the first leg.
    """
    gap = _end_gap(segments)
    if gap <= _CLOSED_GAP_MM or gap <= 1e-6:
        return segments
    end = segments[-1]
    start = segments[0]
    span = math.hypot(start["x1"] - start["x0"], start["y1"] - start["y0"]) or 1.0
    ee = (start["e"] / span) * gap if start.get("e") else 0.0
    bridge = dict(start)
    bridge.update(
        x0=end["x1"],
        y0=end["y1"],
        x1=start["x0"],
        y1=start["y0"],
        e=ee,
        raw=_synth_move(start["x0"], start["y0"], ee, start.get("f")),
        comments=[],
    )
    return [*segments, bridge]


def _dorsal_split(segments: list[dict]) -> list[dict]:
    """Rotate a closed loop so it starts off the plantar (belt) face.

    The start is the midpoint of the max-Y edge, the face away from the belt.
    A crest shorter than 2 mm uses the dorsal-most vertex instead, and a tie
    there prefers the lateral end. A flat loop with no point above the belt
    band keeps the toe-file belt start: that is a leader tab with no dorsal face.
    A near-closed loop is bridged first, so the rotate cannot emit a chord.
    """
    if len(segments) < 3:
        return segments
    if not _is_closed_loop(segments):
        return segments
    segments = _bridge_near_gap(segments)
    pts = [(segments[0]["x0"], segments[0]["y0"])] + [(s["x1"], s["y1"]) for s in segments]
    ymin = min(p[1] for p in pts)
    ymax = max(p[1] for p in pts)
    if ymax <= ymin + _EDGE_BAND:
        return _belt_split(segments, CLOSED_GAP_MM)

    def on_dorsal(s: dict) -> bool:
        return s["y0"] >= ymax - _EDGE_BAND and s["y1"] >= ymax - _EDGE_BAND

    run = _longest_edge_run(segments, on_dorsal)
    if run is not None and run[2] >= _EDGE_MIN_LEN:
        i, t = _run_midpoint(segments, run[0], run[1])
        started = _start_on_segment(segments, i, t)
        if started[0]["y0"] > ymin + _EDGE_BAND:
            return started
    started = _dorsal_vertex_start(segments, pts)
    if started[0]["y0"] <= ymin + _EDGE_BAND:
        return _belt_split(segments, CLOSED_GAP_MM)
    return started


def _synth_move(x1: float, y1: float, ee: float, ff: str | None) -> str:
    parts = ["G1", f"X{_fmt(x1, 3)}", f"Y{_fmt(y1, 3)}", f"E{ee:.5f}"]
    if ff:
        parts.append(f"F{float(ff):.0f}")
    return " ".join(parts) + "\n"


def _rev_seg(s: dict) -> dict:
    r = dict(s)
    r["x0"], r["x1"] = s["x1"], s["x0"]
    r["y0"], r["y1"] = s["y1"], s["y0"]
    r["raw"] = _synth_move(r["x1"], r["y1"], s["e"], s.get("f"))
    r["comments"] = list(s.get("comments") or [])
    return r


def _partial(s: dict, t0: float, t1: float) -> dict:
    def pt(t: float) -> tuple[float, float]:
        return s["x0"] + (s["x1"] - s["x0"]) * t, s["y0"] + (s["y1"] - s["y0"]) * t

    x0, y0 = pt(t0)
    x1, y1 = pt(t1)
    ee = s["e"] * (t1 - t0)
    r = dict(s)
    r.update(x0=x0, y0=y0, x1=x1, y1=y1, e=ee, raw=_synth_move(x1, y1, ee, s.get("f")))
    r["comments"] = list(s.get("comments") or []) if t0 <= 1e-6 else []
    return r


def _path_closed(segs: list[dict]) -> bool:
    # Inner walls keep the old margin. Near-closed is an outer-wall seam.
    return _end_gap(segs) <= _CLOSED_GAP_MM


def _hit_anchor(segs: list[dict], anchor_x: float) -> tuple[float, int, float] | None:
    """Belt-side crossing of x=anchor_x. Returns (y, segment index, fraction)."""
    ymin = min(min(s["y0"], s["y1"]) for s in segs)
    best = None
    for i, s in enumerate(segs):
        x0, x1 = s["x0"], s["x1"]
        if abs(x1 - x0) < 1e-9:
            continue
        t = (anchor_x - x0) / (x1 - x0)
        if t < -1e-4 or t > 1 + 1e-4:
            continue
        t = min(1.0, max(0.0, t))
        y = s["y0"] + (s["y1"] - s["y0"]) * t
        # Only the belt contact. Loops up the heel have their own low point
        # and must not be restarted on the outer seam's X.
        if y > 2.5 or y > ymin + 0.75:
            continue
        if best is None or y < best[0] - 1e-6:
            best = (y, i, t)
    return best


def _open_chains(segs: list[dict], i: int, t: float) -> list[list[dict]]:
    """Split an open wall at the belt anchor: back to the original start, then on to the end."""
    s = segs[i]
    first: list[dict] = []
    if t > 1e-4:
        first.append(_rev_seg(_partial(s, 0.0, t)))
    for s2 in reversed(segs[:i]):
        first.append(_rev_seg(s2))
    second: list[dict] = []
    if t < 1 - 1e-4:
        second.append(_partial(s, t, 1.0))
    second.extend(segs[i + 1 :])
    return [c for c in (first, second) if c]


def _rotate_at(segs: list[dict], i: int, t: float) -> list[dict]:
    s = segs[i]
    out: list[dict] = []
    if t < 1 - 1e-4:
        out.append(_partial(s, t, 1.0))
    out.extend(segs[i + 1 :])
    out.extend(segs[:i])
    if t > 1e-4:
        out.append(_partial(s, 0.0, t))
    return out or segs


def _wall_chains(segs: list[dict], kind: str, anchor_x: float | None, anchor: bool) -> tuple[list[list[dict]], float | None]:
    """Outer walls start at their belt-edge midpoint. Other walls start at that same X.

    An open wall that crosses the anchor in its middle is extruded as two runs,
    both beginning on the belt. That is the extra seam the G2b toe file inserts.
    """
    if not segs:
        return [segs], anchor_x
    if not anchor or kind in ("Outer wall", "Overhang wall"):
        segs = _belt_split(segs)
        new_anchor = segs[0]["x0"] if kind in ("Outer wall", "Overhang wall") else anchor_x
        return [segs], new_anchor
    if anchor_x is None:
        return [segs], anchor_x
    hit = _hit_anchor(segs, anchor_x)
    if hit is None:
        return [segs], anchor_x
    _, i, t = hit
    if _path_closed(segs):
        return [_rotate_at(segs, i, t)], anchor_x
    total = sum(math.hypot(s["x1"] - s["x0"], s["y1"] - s["y0"]) for s in segs) or 1.0
    along = 0.0
    for j, s in enumerate(segs):
        length = math.hypot(s["x1"] - s["x0"], s["y1"] - s["y0"])
        if j < i:
            along += length
        else:
            along += length * t
            break
    if along < 1.0 or total - along < 1.0:
        return [segs], anchor_x
    return _open_chains(segs, i, t), anchor_x


def _perimeter_by_layer(lines: list[str]) -> list[list[tuple[float, float, float, float]]]:
    """Outer and overhang extrusion edges. Index 0 is the preamble, before the first layer."""
    layers: list[list[tuple[float, float, float, float]]] = [[]]
    x = y = None
    typ = ""
    for line in lines:
        if line.startswith(";LAYER_CHANGE"):
            # A layer that omits ;TYPE: keeps the previous feature. The tip of
            # this insole is still outer wall, and the crossing check treats it
            # that way.
            layers.append([])
            continue
        if line.startswith(";TYPE:"):
            typ = line[6:].strip()
            continue
        if typ not in ("Outer wall", "Overhang wall") or not _is_g01(line):
            if _is_g01(line):
                w = _words(line)
                if "X" in w:
                    x = float(w["X"])
                if "Y" in w:
                    y = float(w["Y"])
            continue
        w = _words(line)
        if "E" not in w or float(w["E"]) <= 0 or ("X" not in w and "Y" not in w):
            if "X" in w:
                x = float(w["X"])
            if "Y" in w:
                y = float(w["Y"])
            continue
        if x is None or y is None:
            if "X" in w:
                x = float(w["X"])
            if "Y" in w:
                y = float(w["Y"])
            continue
        nx = float(w["X"]) if "X" in w else x
        ny = float(w["Y"]) if "Y" in w else y
        layers[-1].append((x, y, nx, ny))
        x, y = nx, ny
    return layers


def _crossing(grid, ax: float, ay: float, bx: float, by: float) -> bool:
    if math.hypot(bx - ax, by - ay) <= 1e-4:
        return False
    if not grid.hits(ax, ay, bx, by):
        return False
    return not (grid.near_endpoint(ax, ay, 0.3) or grid.near_endpoint(bx, by, 0.3))


def _closest_on_ring(ring: list[tuple[float, float]], px: float, py: float):
    best = (px, py)
    best_d = 1e18
    best_i = 0
    best_t = 0.0
    n = len(ring) - 1
    for i in range(n):
        ax, ay = ring[i]
        bx, by = ring[i + 1]
        dx, dy = bx - ax, by - ay
        len2 = dx * dx + dy * dy
        t = 0.0 if len2 <= 1e-18 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / len2))
        qx, qy = ax + t * dx, ay + t * dy
        dist = math.hypot(px - qx, py - qy)
        if dist < best_d:
            best_d, best, best_i, best_t = dist, (qx, qy), i, t
    return best, best_i, best_t


def _walk_ring(ring, i0: int, t0: float, i1: int, t1: float, direction: int) -> list[tuple[float, float]]:
    n = len(ring) - 1

    def pt(i: int, t: float) -> tuple[float, float]:
        ax, ay = ring[i]
        bx, by = ring[i + 1]
        return (ax + (bx - ax) * t, ay + (by - ay) * t)

    start, goal = pt(i0, t0), pt(i1, t1)
    if i0 == i1 and ((direction > 0 and t0 <= t1 + 1e-9) or (direction < 0 and t1 <= t0 + 1e-9)):
        return [start, goal]
    pts = [start]
    i = i0
    for _ in range(n + 3):
        if direction > 0:
            nxt = ring[i + 1]
            i = (i + 1) % n
        else:
            nxt = ring[i]
            i = (i - 1) % n
        if math.hypot(pts[-1][0] - nxt[0], pts[-1][1] - nxt[1]) > 1e-6:
            pts.append((nxt[0], nxt[1]))
        if i == i1:
            pts.append(goal)
            return pts
    pts.append(goal)
    return pts


def _radial_inset(ring: list[tuple[float, float]], dist: float) -> list[tuple[float, float]]:
    """Pull each vertex toward the ring centroid, staying short of it."""
    pts = ring[:-1]
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    inn = []
    for px, py in pts:
        dx, dy = cx - px, cy - py
        length = math.hypot(dx, dy) or 1.0
        step = min(dist, length * 0.4)
        inn.append((px + dx / length * step, py + dy / length * step))
    inn.append(inn[0])
    return inn


def _dock(grid, ring, inn, px: float, py: float):
    """Points from (px, py) onto the inset ring, entering through the boundary when needed."""
    ip, ii, it = _closest_on_ring(inn, px, py)
    if not _crossing(grid, px, py, ip[0], ip[1]):
        return [ (px, py), ip ], ii, it
    op, _oi, _ot = _closest_on_ring(ring, px, py)
    ip2, ii2, it2 = _closest_on_ring(inn, op[0], op[1])
    return [(px, py), op, ip2], ii2, it2


def _clean_path(path: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out = [path[0]]
    for px, py in path[1:]:
        if math.hypot(px - out[-1][0], py - out[-1][1]) > 0.02:
            out.append((px, py))
    return out


def _chain_polygons_production(edges: list[tuple[float, float, float, float]]):
    """Production ring walk. ApexLight, Forge, and Bounce travels use this.

    ApexCore ``seam()`` keeps ``check_travel_wall_crossings._chain_polygons``,
    which stops on the return to the start so a 0.4 mm overlap spur stays on
    the ring. That walk is not part of the ApexLight post-edit.
    """
    from check_travel_wall_crossings import CLOSE_MM, _near

    if not edges:
        return 0, 0, []

    def key(x: float, y: float) -> tuple[int, int]:
        return (int(round(x / CLOSE_MM)), int(round(y / CLOSE_MM)))

    unused = set(range(len(edges)))
    starts: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for i, (x0, y0, x1, y1) in enumerate(edges):
        starts.setdefault(key(x0, y0), []).append((i, 0))
        starts.setdefault(key(x1, y1), []).append((i, 1))

    closed = 0
    open_n = 0
    rings: list[list[tuple[float, float]]] = []
    while unused:
        i0 = unused.pop()
        x0, y0, x1, y1 = edges[i0]
        ring = [(x0, y0), (x1, y1)]
        guard = 0
        while guard < len(edges) + 2:
            guard += 1
            k = key(ring[-1][0], ring[-1][1])
            nxt = None
            for i, end in starts.get(k, ()):
                if i not in unused:
                    continue
                nxt = (i, end)
                break
            if nxt is None:
                break
            i, end = nxt
            unused.remove(i)
            ex0, ey0, ex1, ey1 = edges[i]
            ring.append((ex0, ey0) if end == 1 else (ex1, ey1))
        is_closed = len(ring) >= 4 and _near(ring[0][0], ring[0][1], ring[-1][0], ring[-1][1], CLOSE_MM * 2)
        if is_closed:
            closed += 1
            rings.append(ring)
        else:
            open_n += 1
    return closed, open_n, rings


def route_inside_wall(
    edges: list[tuple[float, float, float, float]],
    start: tuple[float, float],
    dest: tuple[float, float],
    chain_polygons=None,
) -> list[tuple[float, float]]:
    """Path from start to dest that does not cut across an outer wall.

    A straight hop is kept when it does not cross. Otherwise the path follows
    the largest outer ring, inset toward its centroid, so the nozzle stays
    inside the part. The seam point itself is unchanged: the path ends at dest.

    The default ring walk is the Core one. ApexLight passes the production walk.
    """
    from check_travel_wall_crossings import _Grid

    if chain_polygons is None:
        from check_travel_wall_crossings import _chain_polygons as chain_polygons

    if not edges:
        return [start, dest]
    grid = _Grid(edges)
    ax, ay = start
    bx, by = dest
    if not grid.hits(ax, ay, bx, by):
        return [start, dest]
    _closed, _open, rings = chain_polygons(edges)
    if not rings:
        return [start, dest]
    ring = max(rings, key=lambda r: abs(sum(
        r[i][0] * r[i + 1][1] - r[i + 1][0] * r[i][1] for i in range(len(r) - 1)
    )))
    for dist in (0.25, 0.15, 0.08):
        inn = _radial_inset(ring, dist)
        left, i0, t0 = _dock(grid, ring, inn, ax, ay)
        right, i1, t1 = _dock(grid, ring, inn, bx, by)
        cands = []
        for direction in (1, -1):
            mid = _walk_ring(inn, i0, t0, i1, t1, direction)
            path = _clean_path(left[:-1] + mid + list(reversed(right)))
            if not any(_crossing(grid, p[0], p[1], q[0], q[1]) for p, q in zip(path, path[1:])):
                length = sum(math.hypot(q[0] - p[0], q[1] - p[1]) for p, q in zip(path, path[1:]))
                cands.append((length, path))
        if cands:
            cands.sort(key=lambda item: item[0])
            return cands[0][1]
    return [start, dest]


# Open outer walls (a seam gap, or the last few tip layers) have no closed ring,
# so route_inside_wall keeps the straight hop. Pairing the two loose ends up to
# this gap lets that same inset walk run. The checker still uses the real edges.
_OPEN_WALL_CLOSE_MM = 30.0


def _path_crosses(grid, path: list[tuple[float, float]]) -> bool:
    return any(_crossing(grid, p[0], p[1], q[0], q[1]) for p, q in zip(path, path[1:]))


def _loose_ends(edges: list[tuple[float, float, float, float]]) -> list[tuple[float, float]]:
    from check_travel_wall_crossings import CLOSE_MM

    def key(x: float, y: float) -> tuple[int, int]:
        return (int(round(x / CLOSE_MM)), int(round(y / CLOSE_MM)))

    degree: dict[tuple[int, int], int] = {}
    pos: dict[tuple[int, int], tuple[float, float]] = {}
    for x0, y0, x1, y1 in edges:
        for point in ((x0, y0), (x1, y1)):
            k = key(*point)
            degree[k] = degree.get(k, 0) + 1
            pos[k] = point
    return [pos[k] for k, count in degree.items() if count == 1]


def _closing_edges(edges: list[tuple[float, float, float, float]]) -> list[tuple[float, float, float, float]]:
    """Join each loose end to its nearest loose end within _OPEN_WALL_CLOSE_MM."""
    ends = _loose_ends(edges)
    extra: list[tuple[float, float, float, float]] = []
    used: set[int] = set()
    for i, start in enumerate(ends):
        if i in used:
            continue
        best: tuple[float, int, tuple[float, float]] | None = None
        for j in range(i + 1, len(ends)):
            if j in used:
                continue
            other = ends[j]
            dist = math.hypot(start[0] - other[0], start[1] - other[1])
            if dist <= _OPEN_WALL_CLOSE_MM and dist > 1e-6 and (best is None or dist < best[0]):
                best = (dist, j, other)
        if best is None:
            continue
        used.add(i)
        used.add(best[1])
        extra.append((start[0], start[1], best[2][0], best[2][1]))
    return extra


def _route_off_wall(
    edges: list[tuple[float, float, float, float]],
    start: tuple[float, float],
    dest: tuple[float, float],
    grid,
    chain_polygons=None,
) -> list[tuple[float, float]] | None:
    """Inside-wall path, or None when no path stays off the real perimeter."""
    path = route_inside_wall(edges, start, dest, chain_polygons)
    if len(path) >= 3 and not _path_crosses(grid, path):
        return path
    extra = _closing_edges(edges)
    if not extra:
        return None
    path = route_inside_wall([*edges, *extra], start, dest, chain_polygons)
    if len(path) >= 3 and not _path_crosses(grid, path):
        return path
    return None


def clear_perimeter_crossings(text: str) -> str:
    """Reroute travels that cut an outer or overhang wall.

    Same inside-wall walk the G2b seam pass uses. Extrusion lines, feeds on
    those lines, and every travel that does not cross are copied unchanged.
    A file with no perimeter crossing is returned as the same string.
    """
    from check_travel_wall_crossings import OUTER, OVERHANG, _Grid, _body, _is_move, _words

    lines = text.splitlines(keepends=True)
    x = y = None
    absolute = True
    relative_e = False
    abs_e = 0.0
    typ = ""
    layer_i = -1
    edges: list[list[tuple[float, float, float, float]]] = []
    travels: list[tuple[int, int, float, float, float, float]] = []

    for index, raw in enumerate(lines):
        if raw.startswith(";LAYER_CHANGE"):
            layer_i += 1
            edges.append([])
            continue
        if raw.startswith(";TYPE:"):
            typ = raw[6:].splitlines()[0].strip()
            continue
        body = _body(raw)
        if body.startswith("M83"):
            relative_e = True
            continue
        if body.startswith("M82"):
            relative_e = False
            continue
        if body.startswith("G90"):
            absolute = True
            continue
        if body.startswith("G91"):
            absolute = False
            continue
        if body.startswith("G92"):
            w = _words(raw)
            if "X" in w:
                x = float(w["X"])
            if "Y" in w:
                y = float(w["Y"])
            if "E" in w:
                abs_e = float(w["E"])
            continue
        if not _is_move(raw):
            continue
        w = _words(raw)
        px, py = x, y
        if "X" in w:
            x = float(w["X"]) if absolute or x is None else x + float(w["X"])
        if "Y" in w:
            y = float(w["Y"]) if absolute or y is None else y + float(w["Y"])
        de = 0.0
        if "E" in w:
            amount = float(w["E"])
            if relative_e:
                de = amount
            else:
                de = amount - abs_e
                abs_e = amount
        if px is None or py is None or x is None or y is None:
            continue
        if "X" not in w and "Y" not in w:
            continue
        if layer_i < 0:
            continue
        if de > 1e-9:
            if typ in (OUTER, OVERHANG):
                edges[layer_i].append((px, py, x, y))
        elif "E" not in w:
            travels.append((index, layer_i, px, py, x, y))

    if not travels:
        return text

    replacements: dict[int, list[str]] = {}
    for index, layer_i, px, py, nx, ny in travels:
        layer_edges = edges[layer_i] if layer_i < len(edges) else []
        if not layer_edges:
            continue
        grid = _Grid(layer_edges)
        if not _crossing(grid, px, py, nx, ny):
            continue
        path = _route_off_wall(layer_edges, (px, py), (nx, ny), grid, _chain_polygons_production)
        if path is None:
            continue
        rendered = _render_routed_travel(lines[index], path)
        if rendered is None:
            continue
        # Rounding can put a vertex back on the wall. Keep the original line then.
        if _rendered_crosses(grid, (px, py), rendered):
            continue
        replacements[index] = rendered

    if not replacements:
        return text
    out: list[str] = []
    for index, raw in enumerate(lines):
        out.extend(replacements.get(index, [raw]))
    return "".join(out)


def _render_routed_travel(raw: str, path: list[tuple[float, float]]) -> list[str] | None:
    """Replace one straight travel with the inside path. Destination tokens stay."""
    newline = "\r\n" if raw.endswith("\r\n") else "\n" if raw.endswith("\n") else ""
    content = raw[: len(raw) - len(newline)] if newline else raw
    body, sep, comment = content.partition(";")
    words = dict(WORD.findall(body))
    cmd = body.strip().split(" ", 1)[0]
    if cmd not in ("G0", "G1"):
        return None
    points = path[1:]
    if len(points) < 2:
        return None
    f_token = words.get("F")
    z_token = words.get("Z")
    dest_x = words.get("X")
    dest_y = words.get("Y")
    comment_text = (" " + sep + comment) if sep else ""
    rendered: list[str] = []
    for i, (px, py) in enumerate(points):
        last = i == len(points) - 1
        if last and dest_x is not None and dest_y is not None:
            x_token, y_token = dest_x, dest_y
        else:
            x_token, y_token = _fmt(px, 3), _fmt(py, 3)
        parts = [cmd, f"X{x_token}", f"Y{y_token}"]
        if last and z_token is not None:
            parts.append(f"Z{z_token}")
        if i == 0 and f_token is not None:
            parts.append(f"F{f_token}")
        piece = " ".join(parts)
        if last and comment_text:
            piece += comment_text
        rendered.append(piece + newline)
    return rendered


def _rendered_crosses(grid, start: tuple[float, float], rendered: list[str]) -> bool:
    x, y = start
    for line in rendered:
        words = dict(WORD.findall(_body(line)))
        if "X" not in words and "Y" not in words:
            continue
        nx = float(words["X"]) if "X" in words else x
        ny = float(words["Y"]) if "Y" in words else y
        if _crossing(grid, x, y, nx, ny):
            return True
        x, y = nx, ny
    return False


def _chain_wall_feed(chain: list[dict], fallback: float = 1560.0) -> float:
    """Extrusion feed on a wall chain, never the travel feed left on the approach."""
    for seg in chain:
        tok = seg.get("f")
        if tok is None:
            continue
        feed = float(tok)
        if abs(feed - TRAVEL_F) > 1.0:
            return feed
    if abs(fallback - TRAVEL_F) > 1.0:
        return fallback
    return 1560.0


def _overlap_line(chain: list[dict], feed_fallback: float = 1560.0) -> tuple[str, float, float] | None:
    """0.4 mm of extrusion past a closed joint. None when the chain is not a loop,
    or when it is short enough that the replay would be a second wall.
    """
    if not chain or not _is_closed_loop(chain) or _chain_length(chain) <= SHORT_LOOP_MM:
        return None
    guide = chain[0]
    span = math.hypot(guide["x1"] - guide["x0"], guide["y1"] - guide["y0"])
    if span <= 1e-6 or not guide.get("e") or guide["e"] <= 0:
        return None
    step = min(OVERLAP_MM, span)
    ux = (guide["x1"] - guide["x0"]) / span
    uy = (guide["y1"] - guide["y0"]) / span
    sx = chain[-1]["x1"] + ux * step
    sy = chain[-1]["y1"] + uy * step
    feed = _chain_wall_feed(chain, feed_fallback)
    line = f"G1 X{_fmt(sx, 3)} Y{_fmt(sy, 3)} E{guide['e'] / span * step:.5f} F{feed:.0f} ;seam overlap\n"
    return line, sx, sy


def _emit_outer_finish(
    out: list[str],
    chain: list[dict],
    feed_fallback: float,
    counts: dict,
) -> tuple[float, float] | None:
    """Cover a closed joint, then the dry wipe. Returns the nozzle XY after the wipe.

    The wipe hides a blob, so it stays. The 0.4 mm before it is extruded along
    the wall the loop started on, which is the direction past the joint. An
    open wall keeps the dry glide only. A closed chain of a few millimetres
    keeps the glide and skips the overlap.
    """
    if not chain:
        return None
    closed = _is_closed_loop(chain)
    guide = chain[0] if closed else chain[-1]
    span = math.hypot(guide["x1"] - guide["x0"], guide["y1"] - guide["y0"])
    if span <= 1e-6:
        return None
    ux = (guide["x1"] - guide["x0"]) / span
    uy = (guide["y1"] - guide["y0"]) / span
    feed = _chain_wall_feed(chain, feed_fallback)
    sx, sy = chain[-1]["x1"], chain[-1]["y1"]
    hit = _overlap_line(chain, feed)
    if hit is not None:
        line, sx, sy = hit
        out.append(line)
        counts["overlap"] += 1
    wx = sx + ux * WIPE_MM
    wy = sy + uy * WIPE_MM
    out.append(";WIPE_START\n")
    out.append(f"G1 X{_fmt(wx, 3)} Y{_fmt(wy, 3)} F{feed * WIPE_FRAC:.0f} ;glide\n")
    out.append(";WIPE_END\n")
    counts["wipe"] += 1
    return wx, wy


def _emit_approach(
    out: list[str],
    x: float,
    y: float,
    z: float,
    dx: float,
    dy: float,
    dz: float,
    retract: bool,
    route: list[tuple[float, float]] | None = None,
) -> None:
    inside = route is not None and len(route) >= 2
    out.append(";C seam travel: inside\n" if inside else ";C seam travel: lift + direct\n")
    if retract:
        out.append(f"G1 E-{RETRACT:.1f} F{RETRACT_F:.0f}\n")
    if not inside:
        out.append(f"G1 Y{_fmt(y + LIFT, 3)} F{TRAVEL_F:.0f} ;lift\n")
        travel = f"G1 X{_fmt(dx, 3)} Y{_fmt(dy + LIFT, 3)}"
        if z is None or abs((dz or 0) - (z or 0)) > 1e-4:
            travel += f" Z{_fmt(dz, 3)}"
        travel += f" F{TRAVEL_F:.0f} ;seam travel\n"
        out.append(travel)
        out.append(f"G1 Y{_fmt(dy, 3)} F{TRAVEL_F:.0f} ;lower\n")
    else:
        # The +0.4 Y lift is what swings a chord across the wall. The inside
        # path is at the layer's own XY. A 0.02 mm cleanup can drop the real
        # destination, so the last point is put back on the seam.
        pts = list(route[1:])
        pts[-1] = (dx, dy)
        z_move = z is None or abs((dz or 0) - (z or 0)) > 1e-4
        for i, (px, py) in enumerate(pts):
            last = i == len(pts) - 1
            travel = f"G1 X{_fmt(px, 3)} Y{_fmt(py, 3)}"
            if last and z_move:
                travel += f" Z{_fmt(dz, 3)}"
            tag = " ;seam travel" if last else " ;seam inside"
            travel += f" F{TRAVEL_F:.0f}{tag}\n"
            out.append(travel)
        out.append(f"G1 Y{_fmt(dy, 3)} F{TRAVEL_F:.0f} ;lower\n")
    if retract:
        out.append(f"G1 E{PRIME:.2f} F{RETRACT_F:.0f}\n")
    out.append(f"G1 F{TRAVEL_F:.0f}\n")


def _move_extrusion(line: str) -> bool:
    if not _is_g01(line):
        return False
    w = _words(line)
    return "E" in w and float(w["E"]) > 0 and ("X" in w or "Y" in w)


def _move_travel(line: str) -> bool:
    if not _is_g01(line):
        return False
    w = _words(line)
    if "E" in w and float(w["E"]) > 0:
        return False
    return "X" in w or "Y" in w or "Z" in w


def _collect_outer_run(
    lines: list[str], i: int, typ: str, x: float | None, y: float | None
) -> dict | None:
    """Travel plus the outer/overhang extrusion it lands on. None for anything else."""
    n = len(lines)
    j = i
    gap: list[str] = []
    dest = typ
    dx, dy = x, y
    while j < n:
        lj = lines[j]
        if lj.startswith(";TYPE:"):
            dest = lj[6:].strip()
            gap.append(lj)
            j += 1
            continue
        if lj.startswith(";LAYER_CHANGE") and j != i:
            break
        if _move_extrusion(lj):
            break
        if (
            _move_travel(lj)
            or lj.startswith(";")
            or _body(lj).startswith(("M", "G92"))
            or _body(lj) == ""
        ):
            if _move_travel(lj):
                w = _words(lj)
                if "X" in w:
                    dx = float(w["X"])
                if "Y" in w:
                    dy = float(w["Y"])
            gap.append(lj)
            j += 1
            continue
        if _is_g01(lj) and not _move_travel(lj) and not _move_extrusion(lj):
            gap.append(lj)
            j += 1
            continue
        break
    if j >= n or not _move_extrusion(lines[j]) or dx is None or dy is None:
        return None
    if dest not in ("Outer wall", "Overhang wall"):
        return None
    segs: list[dict] = []
    comments: list[str] = []
    k = j
    cx, cy = dx, dy
    end_type = dest
    while k < n and not _move_travel(lines[k]) and not lines[k].startswith(";LAYER_CHANGE"):
        lj = lines[k]
        if lj.startswith(";TYPE:"):
            new_type = lj[6:].strip()
            if segs and new_type not in ("Outer wall", "Overhang wall"):
                break
            end_type = new_type
            comments.append(lj if lj.endswith("\n") else lj + "\n")
            k += 1
            continue
        if _move_extrusion(lj):
            w = _words(lj)
            nx = float(w["X"]) if "X" in w else cx
            ny = float(w["Y"]) if "Y" in w else cy
            segs.append(
                {
                    "x0": cx,
                    "y0": cy,
                    "x1": nx,
                    "y1": ny,
                    "e": float(w["E"]),
                    "f": w.get("F"),
                    "raw": lj if lj.endswith("\n") else lj + "\n",
                    "comments": comments,
                }
            )
            comments = []
            cx, cy = nx, ny
            k += 1
            continue
        if lj.startswith(";") or _body(lj).startswith("M") or _body(lj) == "":
            comments.append(lj if lj.endswith("\n") else lj + "\n")
            k += 1
            continue
        break
    if end_type not in ("Outer wall", "Overhang wall") or not segs:
        return None
    return {
        "gap": gap,
        "segs": segs,
        "end": k,
        "typ": end_type,
        "tail": comments,
    }


def _segment_t(seg: dict, x: float, y: float, tol: float) -> float | None:
    x0, y0, x1, y1 = seg["x0"], seg["y0"], seg["x1"], seg["y1"]
    vx, vy = x1 - x0, y1 - y0
    span2 = vx * vx + vy * vy
    if span2 < 1e-18:
        if math.hypot(x - x0, y - y0) <= tol:
            return 0.0
        return None
    t = ((x - x0) * vx + (y - y0) * vy) / span2
    if t < -1e-3 or t > 1.0 + 1e-3:
        return None
    t = min(1.0, max(0.0, t))
    if math.hypot(x0 + vx * t - x, y0 + vy * t - y) <= tol:
        return t
    return None


def _locate_on_chain(segments: list[dict], x: float, y: float, tol: float = 0.02) -> tuple[int, float] | None:
    for i, seg in enumerate(segments):
        if math.hypot(seg["x0"] - x, seg["y0"] - y) <= tol:
            return i, 0.0
    for i, seg in enumerate(segments):
        t = _segment_t(seg, x, y, tol)
        if t is not None:
            return i, t
    return None


def _walk_forward(
    segments: list[dict], start: tuple[float, float], goal: tuple[float, float]
) -> list[tuple[float, float]] | None:
    """Vertices after start, ending at goal, in chain order. None if either point is off the chain."""
    found_start = _locate_on_chain(segments, start[0], start[1])
    found_goal = _locate_on_chain(segments, goal[0], goal[1])
    if found_start is None or found_goal is None:
        return None
    i0, t0 = found_start
    i1, _t1 = found_goal
    n = len(segments)
    if i0 == i1 and t0 <= _t1 + 1e-6:
        if math.hypot(goal[0] - start[0], goal[1] - start[1]) <= 1e-3:
            return []
        return [goal]
    points: list[tuple[float, float]] = []
    if t0 < 1.0 - 1e-6:
        points.append((segments[i0]["x1"], segments[i0]["y1"]))
    i = (i0 + 1) % n
    guard = 0
    while guard <= n:
        guard += 1
        if i == i1:
            points.append(goal)
            return points
        points.append((segments[i]["x1"], segments[i]["y1"]))
        i = (i + 1) % n
    return None


def _reversed_chain(segments: list[dict]) -> list[dict]:
    return [{"x0": seg["x1"], "y0": seg["y1"], "x1": seg["x0"], "y1": seg["y0"]} for seg in reversed(segments)]


def _walk_length(origin: tuple[float, float], points: list[tuple[float, float]]) -> float:
    total = 0.0
    cx, cy = origin
    for px, py in points:
        total += math.hypot(px - cx, py - cy)
        cx, cy = px, py
    return total


def _along_wall(
    segments: list[dict], start: tuple[float, float], goal: tuple[float, float]
) -> list[tuple[float, float]] | None:
    """Shorter perimeter walk from start to goal. None when a point is not on the loop."""
    if math.hypot(goal[0] - start[0], goal[1] - start[1]) <= 1e-3:
        return []
    forward = _walk_forward(segments, start, goal)
    backward = _walk_forward(_reversed_chain(segments), start, goal)
    if forward is None and backward is None:
        return None
    if forward is None:
        chosen = backward
    elif backward is None:
        chosen = forward
    elif _walk_length(start, forward) <= _walk_length(start, backward):
        chosen = forward
    else:
        chosen = backward
    if chosen:
        chosen[-1] = goal
    return chosen


def _emit_wall_walk(points: list[tuple[float, float]]) -> list[str]:
    lines: list[str] = []
    prev: tuple[float, float] | None = None
    for px, py in points:
        if prev is not None and math.hypot(px - prev[0], py - prev[1]) <= 1e-3:
            continue
        lines.append(f"G1 X{_fmt(px, 3)} Y{_fmt(py, 3)} F{TRAVEL_F:.0f}\n")
        prev = (px, py)
    return lines


def _gap_nozzle(gap: list[str], x: float | None, y: float | None) -> tuple[float | None, float | None]:
    for line in gap:
        if not _is_g01(line):
            continue
        words = _words(line)
        if "X" in words:
            x = float(words["X"])
        if "Y" in words:
            y = float(words["Y"])
    return x, y


def _render_reseat(run: dict) -> tuple[list[str], float, float] | None:
    """New lines for one outer run, or None when the run should be left alone.

    The lift-and-direct block is not moved. Sliding it from the belt seam to
    the dorsal seam is a short Y hop along the zipper, and both ends of that
    hop sit outside the 0.3 mm seam window. The nozzle instead walks the
    perimeter, extrudes from the dorsal start, and walks back to the point
    the following travels were written from.
    """
    segs = run["segs"]
    rotated = _dorsal_split(segs)
    hit = _overlap_line(rotated)
    old_start = (segs[0]["x0"], segs[0]["y0"])
    old_end = (segs[-1]["x1"], segs[-1]["y1"])
    new_start = (rotated[0]["x0"], rotated[0]["y0"])
    moved = math.hypot(new_start[0] - old_start[0], new_start[1] - old_start[1]) > 1e-4
    if not moved and hit is None:
        return None
    arrive: list[tuple[float, float]] = []
    if moved:
        landed = _gap_nozzle(run["gap"], run["incoming"][0], run["incoming"][1])
        if landed[0] is None or math.hypot(landed[0] - old_start[0], (landed[1] or 0.0) - old_start[1]) > 0.05:
            return None
        walked = _along_wall(rotated, old_start, new_start)
        if walked is None:
            return None
        arrive = walked
    piece = list(run["gap"])
    piece.extend(_emit_wall_walk(arrive))
    for seg in rotated:
        piece.extend(seg["comments"])
        raw = seg["raw"] if seg["raw"].endswith("\n") else seg["raw"] + "\n"
        piece.append(raw)
    nx, ny = rotated[-1]["x1"], rotated[-1]["y1"]
    if hit is not None:
        line, nx, ny = hit
        piece.append(line)
    back = _along_wall(rotated, (nx, ny), old_end)
    if back is None:
        return None
    piece.extend(_emit_wall_walk(back))
    piece.extend(run["tail"])
    return piece, old_end[0], old_end[1]


def reseat_closed_outers(text: str) -> str:
    """Dorsal reseat kept for the Core experiments. Not on the ApexLight path.

    ``finalize_apex_light`` does not call this. ApexCore seams go through
    ``seam()`` / ``match()``. Calling this on ApexLight is what put the
    belt hop and the dorsal walk on the wall.
    """
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    x = y = None
    typ = ""
    i = 0
    n = len(lines)
    changed = False

    def track(line: str) -> None:
        nonlocal x, y
        if not _is_g01(line):
            return
        w = _words(line)
        if "X" in w:
            x = float(w["X"])
        if "Y" in w:
            y = float(w["Y"])

    while i < n:
        line = lines[i]
        if line.startswith(";TYPE:"):
            typ = line[6:].strip()
            out.append(line)
            i += 1
            continue
        if _move_travel(line):
            run = _collect_outer_run(lines, i, typ, x, y)
            if run is not None:
                run["incoming"] = (x, y)
                rendered = _render_reseat(run)
                if rendered is None:
                    for ln in lines[i : run["end"]]:
                        track(ln)
                        out.append(ln)
                else:
                    piece, x, y = rendered
                    out.extend(piece)
                    changed = True
                typ = run["typ"]
                i = run["end"]
                continue
        track(line)
        out.append(line)
        i += 1
    if not changed:
        return text
    return "".join(out)


def _line_has_feed(raw: str) -> bool:
    return re.search(r"F[+-]?(?:\d+\.?\d*|\.\d+)", raw.split(";", 1)[0]) is not None


def _rewrite_feed(raw: str, feed: float, *, replace: bool) -> str:
    """Set F on one G1. replace rewrites an existing token; otherwise only a missing one is added."""
    body, sep, com = raw.partition(";")
    nl = "\n" if body.endswith("\n") else ""
    core = body[:-1] if nl else body
    if _line_has_feed(core):
        if not replace:
            return raw
        core = re.sub(
            r"F[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?",
            f"F{feed:.0f}",
            core,
            count=1,
        )
    else:
        core = core + f" F{feed:.0f}"
    return core + nl + sep + com


def _slow_end_lines(chain: list[dict], pre_feeds: list[float]) -> list[tuple[list[str], str]]:
    """First and last 10 mm of a closed outer loop at F1200.

    A feed already at or below 1200 is left alone. A segment is split only
    where 10 mm or L-10 mm falls inside it and the two sides need different
    feeds. The split is collinear and keeps that segment's E per mm. The
    segment dicts themselves are not rewritten, so the dry glide still reads
    the original end feed.
    """
    lengths = [math.hypot(s["x1"] - s["x0"], s["y1"] - s["y0"]) for s in chain]
    total = sum(lengths)
    bounds: list[float] = []
    if total + 1e-9 >= 2 * SLOW_END_MM:
        for b in (SLOW_END_MM, total - SLOW_END_MM):
            if not bounds or abs(b - bounds[-1]) > 1e-6:
                bounds.append(b)

    def in_slow(a0: float, a1: float) -> bool:
        if total < 2 * SLOW_END_MM:
            return True
        if a0 >= -0.02 and a1 <= SLOW_END_MM + 0.02:
            return True
        return a0 >= total - SLOW_END_MM - 0.02 and a1 <= total + 0.02

    pieces: list[tuple[int, float, float, float, float]] = []
    acc = 0.0
    for i, s in enumerate(chain):
        length = lengths[i]
        cuts = [0.0, 1.0]
        if length > 1e-9 and pre_feeds[i] > SLOW_END_F + 0.5:
            start_r = (round(s["x0"], 3), round(s["y0"], 3))
            end_r = (round(s["x1"], 3), round(s["y1"], 3))
            for b in bounds:
                if acc + 1e-6 < b < acc + length - 1e-6:
                    t = (b - acc) / length
                    x = s["x0"] + (s["x1"] - s["x0"]) * t
                    y = s["y0"] + (s["y1"] - s["y0"]) * t
                    cut_r = (round(x, 3), round(y, 3))
                    # A cut that rounds onto an existing vertex would emit a
                    # zero-length move and break the perimeter chain.
                    if cut_r != start_r and cut_r != end_r:
                        cuts.append(t)
        cuts = sorted(cuts)
        dedup = [cuts[0]]
        for t in cuts[1:]:
            if t - dedup[-1] > 1e-9:
                dedup.append(t)
        for t0, t1 in zip(dedup, dedup[1:]):
            pieces.append((i, t0, t1, acc + t0 * length, acc + t1 * length))
        acc += length

    lines: list[tuple[list[str], str]] = []
    emitted: float | None = None
    for i, t0, t1, a0, a1 in pieces:
        s = chain[i]
        orig = pre_feeds[i]
        dest = min(orig, SLOW_END_F) if in_slow(a0, a1) else orig
        comments = list(s.get("comments") or []) if t0 <= 1e-6 else []
        raw = s["raw"] if str(s.get("raw", "")).endswith("\n") else str(s.get("raw", "")) + "\n"
        unsplit = t0 <= 1e-9 and t1 >= 1.0 - 1e-9
        if unsplit and abs(dest - orig) <= 0.5:
            if emitted is not None and abs(emitted - orig) > 0.5 and not _line_has_feed(raw):
                raw = _rewrite_feed(raw, orig, replace=False)
            emitted = orig
        elif unsplit:
            raw = _rewrite_feed(raw, dest, replace=True)
            emitted = dest
        else:
            x1 = s["x0"] + (s["x1"] - s["x0"]) * t1
            y1 = s["y0"] + (s["y1"] - s["y0"]) * t1
            ee = float(s.get("e") or 0.0) * (t1 - t0)
            words = _words(raw)
            parts = ["G1", f"X{_fmt(x1, 3)}", f"Y{_fmt(y1, 3)}"]
            if "Z" in words:
                parts.append(f"Z{words['Z']}")
            parts.append(f"E{ee:.5f}")
            parts.append(f"F{dest:.0f}")
            raw = " ".join(parts) + "\n"
            emitted = dest
        lines.append((comments, raw))
    return lines


def seam(
    text: str,
    *,
    retract_min_mm: float | None = None,
    skip_infill_travel: bool = False,
    g2b_anchor: bool = False,
) -> tuple[str, dict]:
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    x = y = z = None
    f = None
    typ = ""
    anchor_x: float | None = None
    i = 0
    n = len(lines)
    counts = {"seam_travel": 0, "retract": 0, "prime": 0, "wipe": 0, "overlap": 0, "seam_start": 0}
    from check_travel_wall_crossings import _Grid

    walls_by_layer = _perimeter_by_layer(lines)
    wall_grids = [_Grid(edges) if edges else None for edges in walls_by_layer]
    layer_i = 0

    def extrusion(line: str) -> bool:
        if not _is_g01(line):
            return False
        w = _words(line)
        return "E" in w and float(w["E"]) > 0 and ("X" in w or "Y" in w)

    def travelish(line: str) -> bool:
        if not _is_g01(line):
            return False
        w = _words(line)
        if "E" in w and float(w["E"]) > 0:
            return False
        return "X" in w or "Y" in w or "Z" in w

    while i < n:
        line = lines[i]
        if line.startswith(";LAYER_CHANGE"):
            layer_i += 1
            out.append(line)
            i += 1
            continue
        if line.startswith(";TYPE:"):
            typ = line[6:].strip()
            out.append(line)
            i += 1
            continue
        if travelish(line):
            j = i
            gap: list[str] = []
            dest_type = typ
            while j < n:
                lj = lines[j]
                if lj.startswith(";TYPE:"):
                    dest_type = lj[6:].strip()
                    gap.append(lj)
                    j += 1
                    continue
                if lj.startswith(";LAYER_CHANGE") and j != i:
                    break
                if extrusion(lj):
                    break
                if travelish(lj) or lj.startswith(";") or _body(lj).startswith("M") or _body(lj).startswith("G92") or _body(lj) == "":
                    gap.append(lj)
                    j += 1
                    continue
                if _is_g01(lj) and not travelish(lj) and not extrusion(lj):
                    # Feed-only or E-only. Keep it inside the gap only when no XY follows immediately.
                    gap.append(lj)
                    j += 1
                    continue
                break
            if j < n and extrusion(lines[j]) and x is not None:
                src_type = typ
                dx, dy, dz = x, y, z
                feeds: list[str] = []
                for tl in gap:
                    if travelish(tl):
                        w = _words(tl)
                        if "X" in w:
                            dx = float(w["X"])
                        if "Y" in w:
                            dy = float(w["Y"])
                        if "Z" in w:
                            dz = float(w["Z"])
                        if "F" in w:
                            f = float(w["F"])
                    elif _is_g01(tl) and not extrusion(tl):
                        feeds.append(tl if tl.endswith("\n") else tl + "\n")
                # Parse the extrusion run before emitting the approach, so a rotated
                # wall can be travelled to at its seam start.
                segs: list[dict] = []
                comments: list[str] = []
                k = j
                cx, cy, cz = dx, dy, dz
                while k < n and not travelish(lines[k]) and not lines[k].startswith(";LAYER_CHANGE"):
                    lj = lines[k]
                    if lj.startswith(";TYPE:"):
                        # Outer and overhang share one perimeter. A type comment
                        # is not a new path; splitting there misses the belt edge.
                        dest_type = lj[6:].strip()
                        comments.append(lj if lj.endswith("\n") else lj + "\n")
                        k += 1
                        continue
                    if extrusion(lj):
                        w = _words(lj)
                        nx = float(w["X"]) if "X" in w else cx
                        ny = float(w["Y"]) if "Y" in w else cy
                        if "F" in w:
                            f = float(w["F"])
                        segs.append(
                            {
                                "x0": cx,
                                "y0": cy,
                                "x1": nx,
                                "y1": ny,
                                "e": float(w["E"]),
                                "f": w.get("F"),
                                "raw": lj if lj.endswith("\n") else lj + "\n",
                                "comments": comments,
                            }
                        )
                        comments = []
                        cx, cy = nx, ny
                        if "Z" in w:
                            cz = float(w["Z"])
                        k += 1
                        continue
                    if lj.startswith(";") or _body(lj).startswith("M") or _body(lj) == "":
                        comments.append(lj if lj.endswith("\n") else lj + "\n")
                        k += 1
                        continue
                    break
                # G2b leaves infill-to-infill travels as plain moves. Seaming
                # them is what produced 1752 travels / 1134 retracts against
                # the reference file's 1368 / 682.
                if skip_infill_travel and src_type in INFILL and dest_type in INFILL:
                    for ln in lines[i:k]:
                        out.append(ln if ln.endswith("\n") else ln + "\n")
                        if ln.startswith(";TYPE:"):
                            typ = ln[6:].strip()
                        if _is_g01(ln):
                            w = _words(ln)
                            if "X" in w:
                                x = float(w["X"])
                            if "Y" in w:
                                y = float(w["Y"])
                            if "Z" in w:
                                z = float(w["Z"])
                            if "F" in w:
                                f = float(w["F"])
                    i = k
                    continue
                wall = dest_type in WALLS
                if wall and segs:
                    chains, anchor_x = _wall_chains(segs, dest_type, anchor_x, g2b_anchor)
                else:
                    chains = [segs]
                for ci, chain in enumerate(chains):
                    if not chain:
                        continue
                    dx, dy = chain[0]["x0"], chain[0]["y0"]
                    travel_dist = math.hypot(dx - x, dy - y)
                    # Retract only on wall approaches of at least retraction_minimum_travel (2 mm).
                    do_retract = wall and (retract_min_mm is None or travel_dist + 1e-6 >= retract_min_mm)
                    if ci == 0:
                        for tl in gap:
                            if not travelish(tl) and not (_is_g01(tl) and not extrusion(tl)):
                                out.append(tl)
                                if tl.startswith(";TYPE:"):
                                    typ = tl[6:].strip()
                    use_dz = dz if ci == 0 else z
                    route = None
                    grid = wall_grids[layer_i] if layer_i < len(wall_grids) else None
                    if grid is not None and _crossing(grid, x, y + LIFT, dx, dy + LIFT):
                        route = route_inside_wall(walls_by_layer[layer_i], (x, y), (dx, dy))
                    _emit_approach(
                        out, x, y, z if z is not None else 0.0, dx, dy,
                        use_dz if use_dz is not None else 0.0, do_retract, route,
                    )
                    counts["seam_travel"] += 1
                    if do_retract:
                        counts["retract"] += 1
                        counts["prime"] += 1
                    if ci == 0:
                        out.extend(feeds)
                    x, y, z = dx, dy, dz
                    if wall:
                        out.append(";C_SEAM_START\n")
                        counts["seam_start"] += 1
                    modal = float(TRAVEL_F)
                    if ci == 0:
                        for fl in feeds:
                            fw = _words(fl)
                            if "F" in fw:
                                modal = float(fw["F"])
                    pre_feeds: list[float] = []
                    for s in chain:
                        if s.get("f"):
                            modal = float(s["f"])
                        pre_feeds.append(modal)
                    slow = (
                        dest_type in ("Outer wall", "Overhang wall")
                        and len(chain) >= 3
                        and _path_closed(chain)
                    )
                    if slow:
                        for comments, raw in _slow_end_lines(chain, pre_feeds):
                            out.extend(comments)
                            out.append(raw)
                            w = _words(raw)
                            if "X" in w:
                                x = float(w["X"])
                            if "Y" in w:
                                y = float(w["Y"])
                    else:
                        for s in chain:
                            out.extend(s["comments"])
                            raw = s["raw"] if s["raw"].endswith("\n") else s["raw"] + "\n"
                            out.append(raw)
                            x, y = s["x1"], s["y1"]
                    last_chain = ci == len(chains) - 1
                    if wall:
                        if last_chain and dest_type in ("Outer wall", "Overhang wall"):
                            last = chain[-1]
                            span = math.hypot(last["x1"] - last["x0"], last["y1"] - last["y0"])
                            if span > 1e-6:
                                ux = (last["x1"] - last["x0"]) / span
                                uy = (last["y1"] - last["y0"]) / span
                                feed = float(last["f"]) if last.get("f") else (f or 1560.0)
                                wx = last["x1"] + ux * WIPE_MM
                                wy = last["y1"] + uy * WIPE_MM
                                out.append(";WIPE_START\n")
                                out.append(f"G1 X{_fmt(wx, 3)} Y{_fmt(wy, 3)} F{feed * WIPE_FRAC:.0f} ;glide\n")
                                out.append(";WIPE_END\n")
                                counts["wipe"] += 1
                                x, y = wx, wy
                        out.append(";C_SEAM_END\n")
                    elif last_chain:
                        out.extend(comments)
                z = cz
                i = k
                continue
        # Default: copy the line and track position.
        if _is_g01(line):
            w = _words(line)
            if "X" in w:
                x = float(w["X"])
            if "Y" in w:
                y = float(w["Y"])
            if "Z" in w:
                z = float(w["Z"])
            if "F" in w:
                f = float(w["F"])
        out.append(line)
        i += 1
    return "".join(out), counts


def mintime(text: str, target_s: float = 8.0, floor_mm_s: float = 10.0) -> tuple[str, dict]:
    """Scale F on XY moves so a layer takes at least target_s, never below floor_mm_s. E unchanged."""
    lines = text.splitlines(keepends=True)
    # Split into preamble + layers + whatever trails the last layer (kept with the last layer).
    breaks = [i for i, l in enumerate(lines) if l.startswith(";LAYER_CHANGE")]
    if not breaks:
        return text, {"under_target": 0, "still_under": 0, "stretched": 0}
    chunks = []
    if breaks[0] > 0:
        chunks.append(("pre", lines[: breaks[0]]))
    for a, b in zip(breaks, breaks[1:] + [len(lines)]):
        chunks.append(("layer", lines[a:b]))

    floor_f = floor_mm_s * 60.0
    stretched = 0
    still_under = 0
    under_before = 0
    out_lines: list[str] = []

    def layer_time(chunk: list[str], x0, y0, f0: float) -> tuple[float, object, object, float]:
        x, y, f = x0, y0, f0
        total = 0.0
        for line in chunk:
            if not _is_g01(line):
                continue
            w = _words(line)
            if "F" in w:
                f = float(w["F"])
            nx = float(w["X"]) if "X" in w else x
            ny = float(w["Y"]) if "Y" in w else y
            if x is not None and nx is not None and ("X" in w or "Y" in w) and f > 0:
                total += math.hypot(nx - x, ny - y) / (f / 60.0)
            x, y = nx, ny
        return total, x, y, f

    def _scale_line(ln: str, scale: float) -> str:
        if not _is_g01(ln):
            return ln
        w = _words(ln)
        # Retract and prime are E-only. Leave their feed alone so a short layer
        # does not turn the seam retract into a crawl. E is never rewritten.
        if "E" in w and "X" not in w and "Y" not in w:
            return ln

        def _scale_f(m: re.Match) -> str:
            return "F" + f"{max(float(m.group(1)) * scale, floor_f):.0f}"

        return re.sub(r"F([+-]?(?:\d+\.?\d*|\.\d+))", _scale_f, ln)

    cx = cy = None
    cf = 1200.0
    for kind, chunk in chunks:
        if kind != "layer":
            _, cx, cy, cf = layer_time(chunk, cx, cy, cf)
            out_lines.extend(chunk)
            continue
        t, _, _, _ = layer_time(chunk, cx, cy, cf)
        if t <= 0 or t >= target_s - 1e-6:
            _, cx, cy, cf = layer_time(chunk, cx, cy, cf)
            out_lines.extend(chunk)
            continue
        under_before += 1
        scale = t / target_s
        scaled = [_scale_line(ln, scale) for ln in chunk]
        t2, cx, cy, cf = layer_time(scaled, cx, cy, cf)
        stretched += 1
        if t2 < target_s - 1e-3:
            still_under += 1
        out_lines.extend(scaled)
    return "".join(out_lines), {"under_target_before": under_before, "stretched": stretched, "still_under": still_under}


def splice(toe: str, heel: str, index: int) -> str:
    """Layers [0, index) from the toe slice, index..end from the bead slice.

    Both files must share layer count and Z. min_bead_width is a print-object
    key on this binary, so the heel bead floor is a second global slice spliced
    in rather than a layer-range option.
    """
    toe_lines = toe.splitlines(keepends=True)
    heel_lines = heel.splitlines(keepends=True)
    tb = [i for i, l in enumerate(toe_lines) if l.startswith(";LAYER_CHANGE")]
    hb = [i for i, l in enumerate(heel_lines) if l.startswith(";LAYER_CHANGE")]
    if index >= len(tb) or index >= len(hb):
        raise SystemExit(f"splice index {index} but toe has {len(tb)} layers and heel has {len(hb)}")
    if len(tb) != len(hb):
        raise SystemExit(f"splice layer count mismatch toe {len(tb)} heel {len(hb)}")
    return "".join(toe_lines[: tb[index]] + heel_lines[hb[index] :])


def coupon(text: str, start_index: int) -> str:
    """Keep the preamble and layers whose 0-based ;LAYER_CHANGE ordinal is >= start_index."""
    lines = text.splitlines(keepends=True)
    breaks = [i for i, l in enumerate(lines) if l.startswith(";LAYER_CHANGE")]
    if start_index >= len(breaks):
        raise SystemExit(f"coupon start {start_index} but file has {len(breaks)} layers")
    keep = lines[: breaks[0]] + lines[breaks[start_index] :]
    return "".join(keep)


def feature_stats(text: str, end_index: int | None = None) -> dict:
    """Path length and E/mm by feature for 0-based layers [0, end_index). None = whole file."""
    lines = text.splitlines()
    layer = -1
    typ = ""
    x = y = None
    acc: dict[str, list[float]] = {}
    filament = 0.0
    time_s = 0.0
    f = 1200.0
    dirs = {0: [0.0, 0.0], 30: [0.0, 0.0], 60: [0.0, 0.0], 90: [0.0, 0.0]}
    for line in lines:
        if line.startswith(";LAYER_CHANGE"):
            layer += 1
            if end_index is not None and layer >= end_index:
                break
            continue
        if end_index is not None and layer >= end_index:
            break
        if layer < 0:
            continue
        if line.startswith(";TYPE:"):
            typ = line[6:].strip()
            continue
        if line.startswith(";WIPE") or "glide" in line:
            continue
        if not _is_g01(line):
            continue
        w = _words(line)
        if "F" in w:
            f = float(w["F"])
        nx = float(w["X"]) if "X" in w else x
        ny = float(w["Y"]) if "Y" in w else y
        if x is not None and nx is not None and ("X" in w or "Y" in w):
            dist = math.hypot((nx or 0) - x, (ny or 0) - y)
            if f > 0:
                time_s += dist / (f / 60.0)
            e = float(w["E"]) if "E" in w else 0.0
            if e > 0 and dist > 0 and typ:
                key = typ
                slot = acc.setdefault(key, [0.0, 0.0])
                slot[0] += dist
                slot[1] += e
                filament += e
                if typ in ("Outer wall", "Overhang wall"):
                    ang = abs(math.degrees(math.atan2(ny - y, nx - x)))
                    bucket = min(dirs, key=lambda b: abs(b - ang))
                    dirs[bucket][0] += dist
                    dirs[bucket][1] += e
        x, y = nx, ny
    feats = {}
    for k, (length, e) in sorted(acc.items()):
        feats[k] = {"length_mm": length, "e_mm": e, "e_per_mm": (e / length) if length else 0.0}
    outer_dir = {b: (dirs[b][1] / dirs[b][0] if dirs[b][0] else 0.0) for b in dirs}
    return {"features": feats, "filament_mm": filament, "time_s": time_s, "outer_e_per_mm_by_dir": outer_dir, "layers": layer}


def layer_times(text: str) -> list[float]:
    lines = text.splitlines()
    times: list[float] = []
    x = y = None
    f = 1200.0
    cur = 0.0
    started = False
    for line in lines:
        if line.startswith(";LAYER_CHANGE"):
            if started:
                times.append(cur)
            started = True
            cur = 0.0
            continue
        if not started or not _is_g01(line):
            continue
        w = _words(line)
        if "F" in w:
            f = float(w["F"])
        nx = float(w["X"]) if "X" in w else x
        ny = float(w["Y"]) if "Y" in w else y
        if x is not None and nx is not None and ("X" in w or "Y" in w) and f > 0:
            cur += math.hypot(nx - x, ny - y) / (f / 60.0)
        x, y = nx, ny
    if started:
        times.append(cur)
    return times


def main() -> None:
    cmd = sys.argv[1]
    src = Path(sys.argv[2])
    text = src.read_text(errors="replace")
    if cmd == "efix":
        out, n = efix(text)
        Path(sys.argv[3]).write_text(out)
        print("efix", n)
    elif cmd == "splice":
        heel = Path(sys.argv[3]).read_text(errors="replace")
        index = int(sys.argv[4])
        Path(sys.argv[5]).write_text(splice(text, heel, index))
        print("spliced at", index)
    elif cmd == "coupon":
        Path(sys.argv[3]).write_text(coupon(text, int(sys.argv[4])))
        print("coupon from", sys.argv[4])
    elif cmd == "match":
        # G2b toe seam, without the heelmod min-time pass.
        out, n = efix(text)
        out, counts = seam(out, retract_min_mm=2.0, skip_infill_travel=True, g2b_anchor=True)
        Path(sys.argv[3]).write_text(out)
        print("efix", n)
        print("seam", counts)
    elif cmd == "all":
        out, n = efix(text)
        out, counts = seam(out)
        out, mt = mintime(out)
        Path(sys.argv[3]).write_text(out)
        print("efix", n)
        print("seam", counts)
        print("mintime", mt)
        times = layer_times(out)
        under = sum(1 for t in times if t < 8.0 - 1e-3)
        print("layers", len(times), "under_8s", under)
    else:
        raise SystemExit(f"unknown {cmd}")


if __name__ == "__main__":
    main()
