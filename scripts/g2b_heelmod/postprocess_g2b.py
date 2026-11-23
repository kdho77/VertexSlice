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

``match`` then bonds ApexCore forefoot skins and the interior stack. The
printed outer edge stays on the sliced line: a bead that grows moves inward
by half the added width, and the extrusion is computed from that same width.
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


# ApexCore forefoot bond. The bead is the rounded rectangle the forefoot gates
# measure: w = (E/mm) * filament_area / H + H*(1 - pi/4). H is 0.46 mm.
# Under 3 mm the two skins bond. 3 mm or more is one outer wall and sparse
# infill. Thickness picks which, on every layer. ApexLight does not call ``match``.
FOREFOOT_ORTHOCAD_LAST = 165
FOREFOOT_INSOLE_SAMPLE_LAST = 31
FOREFOOT_LAYER_H = 0.46
# 16.80 leaves margin under the 17.1 gate. A short bead's written length can be a
# few thousandths shorter than the length used to size E, and that alone was
# enough to land a widened move at 17.10-17.12.
FOREFOOT_FLOW_CAP = 16.80
# Cura's thin single loop overlaps the two skins by about 0.23 mm. The 0.05 mm
# gate is the floor, not the amount to copy. A thick section keeps that same
# 0.23 mm as the depth the fill is pressed into the single outer wall.
_FOREFOOT_THIN_NEED = 0.23
_FOREFOOT_THICK_PRESS = 0.23
# 2.95 catches the three columns the thin bond pushed just under 3 mm. 7.5 catches
# columns the gate still grades as thick because the baseline section is 3-6 mm
# while a steep bead makes the candidate envelope read a little over 6.
_FOREFOOT_THICK_LO = 2.95
_FOREFOOT_THICK_HI = 7.5
_FOREFOOT_COS_MIN = 0.25
# Cura's sparse infill in the reference is one gyroid line about 1.11 mm wide.
# At 26% that is one line every 4.27 mm. The line is pressed 0.23 mm into the
# outer wall where it meets the skin. A section of 3 mm or more gets this,
# including the old solid 3-6 mm bead.
_OPEN_WIDTH = 1.11
_OPEN_DENSITY = 0.26
_OPEN_PERIOD = 9.0
_FF_WORD = re.compile(r"([XYZEFGMZ])([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)")
_FF_DIAM = re.compile(r"^;\s*filament_diameter:\s*([0-9.]+)")
_SKIN_TYPES = {"Outer wall", "Overhang wall"}


def forefoot_last_layer(layer_count: int) -> int | None:
    """Every layer. Thickness, not the layer count, picks solid or infill.

    Under 3 mm is the solid bond. 3 mm or more is sparse infill. A file is not
    skipped because of how many layers it has.
    """
    if layer_count > 0:
        return layer_count
    return None


def _shipped_skin_last(layer_count: int) -> int:
    """Thin-bond skin already written on the two CI models. Does not choose infill.

    The rim hold leaves these layers byte-identical. Orthocad is 165, the
    insole sample is 31. A file of any other length has no such skin.
    """
    if layer_count >= 300:
        return FOREFOOT_ORTHOCAD_LAST
    if 40 <= layer_count <= 220:
        return FOREFOOT_INSOLE_SAMPLE_LAST
    return 0


def _ff_area(diameter: float) -> float:
    return math.pi * (diameter * 0.5) ** 2


def _ff_width(e_mm: float, length_mm: float, area: float) -> float:
    corner = FOREFOOT_LAYER_H * (1.0 - math.pi / 4.0)
    return (e_mm / length_mm) * area / FOREFOOT_LAYER_H + corner


def _ff_e_for_width(width: float, length_mm: float, area: float) -> float:
    corner = FOREFOOT_LAYER_H * (1.0 - math.pi / 4.0)
    return max(0.0, (width - corner) * FOREFOOT_LAYER_H / area * length_mm)


def _ff_flow(width: float, feed: float) -> float:
    corner = FOREFOOT_LAYER_H * (1.0 - math.pi / 4.0)
    return (width - corner) * FOREFOOT_LAYER_H * (feed / 60.0)


def _ff_feed_cap(width: float, feed: float) -> float:
    """Integer F that keeps a widened bead at or below the flow cap."""
    corner = FOREFOOT_LAYER_H * (1.0 - math.pi / 4.0)
    span = (width - corner) * FOREFOOT_LAYER_H
    if span <= 1e-9 or feed <= 0:
        return feed
    if _ff_flow(width, feed) <= FOREFOOT_FLOW_CAP:
        return feed
    capped = math.floor(FOREFOOT_FLOW_CAP * 60.0 / span)
    return max(1.0, float(capped))


def _ff_feed_for_e(e_mm: float, length_mm: float, feed: float, area: float) -> float:
    """Same cap from the extrusion that will actually be written, after rounding."""
    if length_mm <= 1e-6 or e_mm <= 1e-9 or feed <= 0 or area <= 0:
        return feed
    q = (e_mm / length_mm) * area * (feed / 60.0)
    if q <= FOREFOOT_FLOW_CAP:
        return feed
    capped = math.floor(FOREFOOT_FLOW_CAP * 60.0 * length_mm / (e_mm * area))
    return max(1.0, float(capped))


def _ff_set_word(line: str, axis: str, value: float, nd: int) -> str:
    nl = "\n" if line.endswith("\n") else ""
    body = line[:-1] if nl else line
    code, sep, com = body.partition(";")
    token = f"{axis}{int(round(value))}" if nd <= 0 else f"{axis}{value:.{nd}f}"
    pat = re.compile(rf"{axis}[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
    if pat.search(code):
        code = pat.sub(token, code, count=1)
    else:
        spot = re.search(r"\s[EF]", code)
        if spot:
            code = code[: spot.start()] + " " + token + code[spot.start() :]
        else:
            code = code.rstrip() + " " + token
    return code + sep + com + nl


def _ff_words(line: str) -> dict[str, str]:
    return dict(_FF_WORD.findall(line.split(";", 1)[0]))


def _layer_count(lines: list[str]) -> int:
    return sum(1 for line in lines if line.startswith(";LAYER_CHANGE"))


def _sample_inset(length: float) -> float:
    """How far a side wall can shorten before a gate sample leaves the original edge."""
    if length < 1e-6:
        return 0.0
    n = max(1, int(length / 0.5))
    return (0.5 / n) * length


def _parse_moves(lines: list[str], *, reset_type: bool) -> dict:
    """Motion parse. ``;TYPE:`` is kept across ``;LAYER_CHANGE`` unless ``reset_type``.

    Position, feed, absolute/relative E and ``G92 E`` follow the same rules as
    the frozen forefoot grader: nothing is cleared just because the layer changed.
    """
    x = y = 0.0
    e_abs = 0.0
    rel = True
    feed = 0.0
    layer = -1
    typ = ""
    in_end = False
    diameter = None
    segs: list[dict] = []
    for index, line in enumerate(lines):
        raw = line.strip()
        if not raw:
            continue
        if raw.startswith(";"):
            dm = _FF_DIAM.match(raw)
            if dm and diameter is None:
                diameter = float(dm.group(1))
            if raw.startswith(";LAYER_CHANGE"):
                layer += 1
                if reset_type:
                    typ = ""
            elif raw.startswith(";TYPE:"):
                typ = raw[6:].strip()
            continue
        if in_end:
            continue
        body = raw.split(";", 1)[0].strip()
        if not body:
            continue
        w = _ff_words(body)
        if "M" in w:
            code = int(float(w["M"]))
            if code == 82:
                rel = False
            elif code == 83:
                rel = True
            continue
        if "G" not in w:
            continue
        g = int(float(w["G"]))
        if g == 91:
            in_end = True
            continue
        if g == 92:
            if "E" in w:
                e_abs = float(w["E"])
            continue
        if g not in (0, 1):
            continue
        nx = float(w["X"]) if "X" in w else x
        ny = float(w["Y"]) if "Y" in w else y
        if "F" in w:
            feed = float(w["F"])
        de = 0.0
        if "E" in w:
            ev = float(w["E"])
            de = ev if rel else ev - e_abs
            if not rel:
                e_abs = ev
        moved = abs(nx - x) > 1e-9 or abs(ny - y) > 1e-9
        if moved and de > 1e-9 and layer >= 1:
            length = math.hypot(nx - x, ny - y)
            segs.append(
                {
                    "i": index,
                    "layer": layer,
                    "type": typ,
                    "x0": x,
                    "y0": y,
                    "x1": nx,
                    "y1": ny,
                    "e": de,
                    "f": feed,
                    "L": length,
                    "rel": rel,
                }
            )
        x, y = nx, ny
    if diameter is None:
        diameter = 1.75
    area = _ff_area(diameter)
    corner = FOREFOOT_LAYER_H * (1.0 - math.pi / 4.0)
    for seg in segs:
        seg["w"] = _ff_width(seg["e"], seg["L"], area) if seg["L"] > 1e-9 else corner
        seg["kind"] = "skin" if seg["type"] in _SKIN_TYPES else "inner"
        dx = seg["x1"] - seg["x0"]
        seg["cos"] = abs(dx) / seg["L"] if seg["L"] > 1e-9 else 0.0
    return {"segs": segs, "area": area, "diameter": diameter, "layers": _layer_count(lines)}


def forefoot_type_audit(text: str, *, reset_type: bool, last_layer: int | None = None) -> dict:
    """Which forefoot layers still have a skin chain, with and without the type reset."""
    lines = text.splitlines(keepends=True)
    parsed = _parse_moves(lines, reset_type=reset_type)
    if last_layer is None:
        last_layer = forefoot_last_layer(parsed["layers"]) or 0
    skin_layers = sorted({s["layer"] for s in parsed["segs"] if s["kind"] == "skin" and 1 <= s["layer"] <= last_layer})
    present = sorted({s["layer"] for s in parsed["segs"] if 1 <= s["layer"] <= last_layer})
    missed = [layer for layer in present if layer not in skin_layers]
    chains = _skin_chains([s for s in parsed["segs"] if s["kind"] == "skin" and 1 <= s["layer"] <= last_layer])
    by_layer: dict[int, int] = {}
    segs_by_layer: dict[int, int] = {}
    for chain in chains:
        by_layer[chain[0]["layer"]] = by_layer.get(chain[0]["layer"], 0) + 1
        segs_by_layer[chain[0]["layer"]] = segs_by_layer.get(chain[0]["layer"], 0) + len(chain)
    return {
        "reset_type": reset_type,
        "last_layer": last_layer,
        "skin_layers": skin_layers,
        "missed_layers": missed,
        "chains": by_layer,
        "skin_segs": segs_by_layer,
    }


def _skin_chains(segs: list[dict]) -> list[list[dict]]:
    chains: list[list[dict]] = []
    cur: list[dict] = []
    for seg in segs:
        if cur and (
            seg["layer"] != cur[-1]["layer"]
            or math.hypot(seg["x0"] - cur[-1]["x1"], seg["y0"] - cur[-1]["y1"]) > 0.05
        ):
            chains.append(cur)
            cur = []
        cur.append(seg)
    if cur:
        chains.append(cur)
    return chains


def _chain_normals(chain: list[dict]) -> list[tuple[float, float]]:
    area = 0.0
    for seg in chain:
        area += seg["x0"] * seg["y1"] - seg["x1"] * seg["y0"]
    normals = []
    for seg in chain:
        dx = seg["x1"] - seg["x0"]
        dy = seg["y1"] - seg["y0"]
        length = math.hypot(dx, dy) or 1.0
        if area >= 0.0:
            normals.append((dy / length, -dx / length))
        else:
            normals.append((-dy / length, dx / length))
    return normals


def _columns(segs: list[dict]) -> dict[tuple[int, int], list[tuple[float, float, float, int]]]:
    """(layer, x_bin) -> (lo, hi, yc, seg index) using the frozen 0.5 mm column."""
    cols: dict[tuple[int, int], list[tuple[float, float, float, int]]] = {}
    for index, seg in enumerate(segs):
        if seg["cos"] < _FOREFOOT_COS_MIN or seg["L"] < 1e-6:
            continue
        yhalf = (seg["w"] * 0.5) / seg["cos"]
        x0, x1 = seg["x0"], seg["x1"]
        dx = x1 - x0
        xa, xb = (x0, x1) if x0 <= x1 else (x1, x0)
        b0 = int(math.floor(xa / 0.5))
        b1 = int(math.floor(xb / 0.5))
        for binn in range(b0, b1 + 1):
            lo = max(xa, binn * 0.5)
            hi = min(xb, (binn + 1) * 0.5)
            if hi - lo <= 1e-9:
                continue
            xc = (lo + hi) * 0.5
            if abs(dx) > 1e-12:
                t = (xc - x0) / dx
                t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
            else:
                t = 0.5
            yc = seg["y0"] + t * (seg["y1"] - seg["y0"])
            cols.setdefault((seg["layer"], binn), []).append((yc - yhalf, yc + yhalf, yc, index))
    return cols


def _pair_of(beads: list[tuple[float, float, float, int]], kinds: list[str]):
    skins = [b for b in beads if kinds[b[3]] == "skin"]
    if not skins:
        return None
    lo_yc = min(b[2] for b in skins)
    hi_yc = max(b[2] for b in skins)
    if hi_yc - lo_yc < 0.20:
        return None
    mid = (lo_yc + hi_yc) * 0.5
    plow = phigh = dlow = dhigh = None
    for lo, hi, yc, _idx in skins:
        if yc <= mid:
            plow = lo if plow is None else min(plow, lo)
            phigh = hi if phigh is None else max(phigh, hi)
        if yc >= mid:
            dlow = lo if dlow is None else min(dlow, lo)
            dhigh = hi if dhigh is None else max(dhigh, hi)
    if plow is None or dlow is None:
        return None
    return plow, phigh, dlow, dhigh, dhigh - plow, dlow - phigh


def _assign_needs(segs: list[dict]) -> None:
    for seg in segs:
        seg["need_lo"] = 0.0
        seg["need_hi"] = 0.0
    cols = _columns(segs)
    kinds = [s["kind"] for s in segs]
    for beads in cols.values():
        pair = _pair_of(beads, kinds)
        if pair is None:
            continue
        _pl, _ph, _dl, _dh, thick, gap = pair
        # Thick columns (3-6 mm) stay as sliced. Only a single-loop thin section
        # is bonded, by widening the two skins toward each other.
        if not (2.0 <= thick < 3.0):
            continue
        if gap + _FOREFOOT_THIN_NEED <= 0:
            continue
        skins = [b for b in beads if kinds[b[3]] == "skin"]
        if len(skins) < 2:
            continue
        # The plantar skin cannot rise more than 0.20 mm or an unmoved thick
        # neighbour becomes the whole bottom. The dorsal skin takes the rest,
        # so the two still overlap by 0.23 mm.
        closure = gap + _FOREFOOT_THIN_NEED
        lo = min(skins, key=lambda b: b[2])
        hi = max(skins, key=lambda b: b[2])
        lo_adv = min(closure / 2.0, 0.40)
        hi_adv = closure - lo_adv
        segs[lo[3]]["need_hi"] = max(segs[lo[3]]["need_hi"], lo_adv)
        segs[hi[3]]["need_lo"] = max(segs[hi[3]]["need_lo"], hi_adv)


def _segment_dir(seg: dict) -> tuple[float, float]:
    dx = seg["x1"] - seg["x0"]
    dy = seg["y1"] - seg["y0"]
    length = math.hypot(dx, dy) or 1.0
    return dx / length, dy / length


def _end_margin(length: float) -> float:
    """Distance from a segment end to its nearest outer-edge sample."""
    if length < 1e-6:
        return 0.0
    n = max(1, int(length / 0.5))
    return length / (2.0 * n)


def _pt_seg(px: float, py: float, x0: float, y0: float, x1: float, y1: float) -> float:
    dx, dy = x1 - x0, y1 - y0
    span = dx * dx + dy * dy
    if span < 1e-12:
        return math.hypot(px - x0, py - y0)
    t = ((px - x0) * dx + (py - y0) * dy) / span
    if t < 0.0:
        t = 0.0
    elif t > 1.0:
        t = 1.0
    return math.hypot(px - (x0 + t * dx), py - (y0 + t * dy))


def _outward(dx: float, dy: float, ccw: bool) -> tuple[float, float]:
    length = math.hypot(dx, dy) or 1.0
    if ccw:
        return dy / length, -dx / length
    return -dy / length, dx / length


def _blend_normal(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
    sx, sy = a[0] + b[0], a[1] + b[1]
    length = math.hypot(sx, sy)
    if length >= 0.30:
        return sx / length, sy / length
    return a


def _rim_fit(p: tuple[float, float], q: tuple[float, float], ccw: bool,
             edges: list[tuple[float, float, float, float, float, float]],
             ref: tuple[float, float]) -> tuple[float, float] | None:
    """Width of the short join pq whose outward rim lands on the near sliced edge.

    The join is one sample, at its midpoint. The width is the distance along the
    outward ray to the first sliced outer edge, so a wide bead cannot lock onto
    the skin on the far side of the section. None when that ray misses, the join
    is not a single sample, or the inward side of the bead would leave the part.
    """
    dx, dy = q[0] - p[0], q[1] - p[1]
    length = math.hypot(dx, dy)
    if length < 0.02 or length >= 0.50 or not edges:
        return None
    ox, oy = _outward(dx, dy, ccw)
    if ox * ref[0] + oy * ref[1] < 0.20:
        return None
    mx = (p[0] + q[0]) * 0.5
    my = (p[1] + q[1]) * 0.5
    best_t: float | None = None
    for x0, y0, x1, y1, _ex, _ey in edges:
        sx, sy = x1 - x0, y1 - y0
        den = ox * sy - oy * sx
        if abs(den) < 1e-8:
            continue
        rx, ry = x0 - mx, y0 - my
        t = (rx * sy - ry * sx) / den
        u = (rx * oy - ry * ox) / den
        if t < 0.20 or t > 1.30 or u < -0.05 or u > 1.05:
            continue
        if _pt_seg(mx + ox * t, my + oy * t, x0, y0, x1, y1) > 0.045:
            continue
        if _pt_seg(mx, my, x0, y0, x1, y1) > 1.8:
            continue
        if best_t is None or t < best_t:
            best_t = t
    if best_t is None:
        return None
    # The ray leaves the centre along the outward normal and stops at the first
    # sliced edge, so the far skin is never the one that sets the width.
    return (0.0, best_t * 2.0)


def _signed_outside(px: float, py: float,
                    edges: list[tuple[float, float, float, float, float, float]]) -> float:
    """Signed distance to the nearest sliced rim. Positive is outside that rim."""
    best = 1e9
    signed = 0.0
    for x0, y0, x1, y1, ox, oy in edges:
        dx, dy = x1 - x0, y1 - y0
        span = dx * dx + dy * dy
        if span < 1e-12:
            qx, qy = x0, y0
        else:
            t = ((px - x0) * dx + (py - y0) * dy) / span
            if t < 0.0:
                t = 0.0
            elif t > 1.0:
                t = 1.0
            qx = x0 + t * dx
            qy = y0 + t * dy
        dist = math.hypot(px - qx, py - qy)
        if dist < best:
            best = dist
            signed = (px - qx) * ox + (py - qy) * oy
    return signed


def _rim_excess(x0: float, y0: float, x1: float, y1: float, nx: float, ny: float, half: float,
                edges: list[tuple[float, float, float, float, float, float]]) -> float:
    """How far one side of a bead sits outside the sliced rim, in mm."""
    dx, dy = x1 - x0, y1 - y0
    length = math.hypot(dx, dy)
    if length < 1e-9:
        return max(0.0, _signed_outside(x0 + nx * half, y0 + ny * half, edges))
    worst = 0.0
    steps = max(1, int(length / 0.40))
    for k in range(steps):
        t = (k + 0.5) / steps
        cx = x0 + dx * t
        cy = y0 + dy * t
        worst = max(worst, _signed_outside(cx + nx * half, cy + ny * half, edges))
    return worst


def _bead_outside(x0: float, y0: float, x1: float, y1: float, width: float,
                  edges: list[tuple[float, float, float, float, float, float]]) -> float:
    dx, dy = x1 - x0, y1 - y0
    length = math.hypot(dx, dy)
    if length < 1e-9 or not edges:
        return 0.0
    nx, ny = dy / length, -dx / length
    half = width * 0.5
    return max(
        _rim_excess(x0, y0, x1, y1, nx, ny, half, edges),
        _rim_excess(x0, y0, x1, y1, -nx, -ny, half, edges),
    )


def _hold_outer_line(chain: list[dict], plan: dict) -> None:
    """Pull a widened bead back so its printed rim stays on its own sliced edge.

    The corner join can slide the two ends of a wall in opposite directions.
    The width still grows, and the outer rim leaves that wall's sliced line.
    Shift the centreline until the outward rim is back on the line. The added
    width stays on the inside, so the bond is not undone. The seam station
    (the loop start) is not moved.
    """
    edges = plan["edges"]
    normals = plan["normals"]
    if not edges:
        return
    for i, seg in enumerate(chain):
        own = [edges[i]]
        for _step in range(4):
            x0, y0 = plan["starts"][i]
            x1, y1 = plan["ends"][i]
            length = math.hypot(x1 - x0, y1 - y0)
            if length < 0.02:
                break
            nx, ny = normals[i]
            half = (seg["w"] + plan["dws"][i]) * 0.5
            excess = _rim_excess(x0, y0, x1, y1, nx, ny, half, own)
            if excess <= 0.02:
                break
            # The loop start is the seam. Drop the added width instead of sliding it.
            if i == 0:
                plan["dws"][i] = max(0.0, plan["dws"][i] - 2.0 * excess)
                continue
            step = min(excess, 0.60)
            plan["starts"][i] = (x0 - nx * step, y0 - ny * step)
            plan["ends"][i] = (x1 - nx * step, y1 - ny * step)
    # Two centrelines that cross leave a chord between their ends. That chord's
    # rim sits inside the corner, off the sliced line. Trim both walls to the
    # crossing so the loop stays one bead and the rim stays on the line.
    n = len(chain)
    for i in range(n):
        j = (i + 1) % n
        if j == 0 or plan["conn_w"][i] is not None:
            continue
        ax, ay = plan["starts"][i]
        bx, by = plan["ends"][i]
        cx, cy = plan["starts"][j]
        dx, dy = plan["ends"][j]
        ex, ey = bx - ax, by - ay
        fx, fy = dx - cx, dy - cy
        den = ex * fy - ey * fx
        if abs(den) < 1e-8:
            continue
        t = ((cx - ax) * fy - (cy - ay) * fx) / den
        u = ((cx - ax) * ey - (cy - ay) * ex) / den
        if not (0.02 < t < 1.05 and -0.05 < u < 0.98):
            continue
        pt = (ax + t * ex, ay + t * ey)
        wi = chain[i]["w"] + plan["dws"][i]
        wj = chain[j]["w"] + plan["dws"][j]
        if _bead_outside(ax, ay, pt[0], pt[1], wi, edges) > 0.05:
            continue
        if _bead_outside(pt[0], pt[1], dx, dy, wj, edges) > 0.05:
            continue
        plan["ends"][i] = pt
        plan["starts"][j] = pt


def _join_inside(p: tuple[float, float], q: tuple[float, float], width: float,
                 edges: list[tuple[float, float, float, float, float, float]]) -> float | None:
    """Shrink a corner join until both rims are inside the sliced edge."""
    if not edges:
        return width
    for _step in range(6):
        excess = _bead_outside(p[0], p[1], q[0], q[1], width, edges)
        if excess <= 0.02:
            return width
        width -= 2.0 * excess
        if width < 0.20:
            return None
    if _bead_outside(p[0], p[1], q[0], q[1], width, edges) <= 0.02:
        return width
    return None


def _width_on_line(fit: tuple[float, float] | None, p: tuple[float, float], q: tuple[float, float],
                   edges: list[tuple[float, float, float, float, float, float]], hold: bool) -> float | None:
    if fit is None:
        return None
    if not hold:
        return fit[1]
    return _join_inside(p, q, fit[1], edges)


def _plan_chain(chain: list[dict]) -> dict:
    """Parallel insets for one skin loop, with a short connector at each corner.

    Cura keeps the outer boundary fixed and joins two variable-width centrelines
    with a short segment. Each wall here is the sliced segment shifted inward by
    half its own added width, so the printed outer edge of that wall stays on
    the sliced line. A thick neighbour is not given the thin wall's inset. Where
    the two offsets do not meet, the join cuts a short step into one wall and
    the step's width is chosen so its outer rim lands on the sliced edge. The
    step stays inside the unsampled margin at the end of that wall, so the
    wall's own edge samples are not left behind.
    """
    n = len(chain)
    normals = _chain_normals(chain)
    closed = math.hypot(chain[-1]["x1"] - chain[0]["x0"], chain[-1]["y1"] - chain[0]["y0"]) <= 0.05
    advances = []
    for i, seg in enumerate(chain):
        if normals[i][1] < -0.5:
            advances.append(seg["need_hi"])
        elif normals[i][1] > 0.5:
            advances.append(seg["need_lo"])
        else:
            advances.append(max(seg["need_lo"], seg["need_hi"]))
    run_s = [0.0] * n
    for i in range(n):
        s = advances[i] / 2.0
        neighbors = []
        if closed or i > 0:
            neighbors.append((i - 1) % n)
        if closed or i + 1 < n:
            neighbors.append((i + 1) % n)
        for j in neighbors:
            if chain[j]["L"] < 0.6 or chain[j]["cos"] >= 0.45:
                continue
            s = min(s, _sample_inset(chain[j]["L"]))
        if i == 0:
            s = min(s, 0.45)
        # Belt-facing centreline shift stays inside the 0.25 mm bottom window.
        if normals[i][1] < -0.5:
            s = min(s, 0.20)
        run_s[i] = s
    # A short corner left at inset 0 stays below the raised plantar wall, and the
    # seam check then treats that corner as the whole bottom. Give the corner
    # the neighbouring wall's inset. The corner still moves on its own normal.
    # A short belt notch left below a raised neighbour becomes the bottom, and
    # the seam then misses the plantar band. Give it the neighbour's inset.
    # Repeat so a run of short notches all rise together; one pass stops at
    # the first notch and the rest stay low.
    for _ in range(n):
        moved = False
        for i in range(n):
            if chain[i]["L"] >= 0.30 or normals[i][1] > -0.35:
                continue
            neighbors = []
            if closed or i > 0:
                neighbors.append((i - 1) % n)
            if closed or i + 1 < n:
                neighbors.append((i + 1) % n)
            for j in neighbors:
                if run_s[j] > run_s[i] + 1e-9:
                    run_s[i] = run_s[j]
                    moved = True
        if not moved:
            break
    # The seam is raised with the plantar wall. Every belt vertex has to stay
    # inside the 0.25 mm band under that seam, or the seam reads as off the
    # bottom. Lift the vertex on its own normal and add the same width, so
    # the printed outer edge does not move.
    if normals[0][1] < -0.5:
        seam_y = chain[0]["y0"] - normals[0][1] * run_s[0]
        floor = seam_y - 0.22
        for i, seg in enumerate(chain):
            rise = -normals[i][1]
            if rise < 0.50:
                continue
            for y in (seg["y0"], seg["y1"]):
                need = (y - floor) / normals[i][1]
                if 0.0 < need <= 0.25:
                    run_s[i] = max(run_s[i], need)
    signed = 0.0
    for seg in chain:
        signed += seg["x0"] * seg["y1"] - seg["x1"] * seg["y0"]
    ccw = signed >= 0.0
    edges: list[tuple[float, float, float, float, float, float]] = []
    for i, seg in enumerate(chain):
        ox, oy = normals[i]
        half = seg["w"] * 0.5
        edges.append((
            seg["x0"] + ox * half, seg["y0"] + oy * half,
            seg["x1"] + ox * half, seg["y1"] + oy * half,
            ox, oy,
        ))
    starts: list[tuple[float, float]] = []
    ends: list[tuple[float, float]] = []
    dirs: list[tuple[float, float]] = []
    for i, seg in enumerate(chain):
        sx, sy = normals[i]
        shift = run_s[i]
        starts.append((seg["x0"] - sx * shift, seg["y0"] - sy * shift))
        ends.append((seg["x1"] - sx * shift, seg["y1"] - sy * shift))
        dirs.append(_segment_dir(seg))
    # A notch shorter than one outer-edge sample, or one whose ends were pulled
    # past each other, has no legal rim. Drop it to a point so it is not extruded.
    for i in range(n):
        dx = ends[i][0] - starts[i][0]
        dy = ends[i][1] - starts[i][1]
        if dx * dirs[i][0] + dy * dirs[i][1] < 0.02:
            ends[i] = starts[i]
    conn_w: list[float | None] = [None] * n
    # The seam sits on the belt. A corner vertex lower than this pulls the
    # plantar band off that station (the band is 0.25 mm tall).
    seam_floor = starts[0][1] - 0.22 if normals[0][1] < -0.5 else None
    steps = (0.0, 0.04, 0.08, 0.12, 0.16, 0.20, 0.24, 0.28, 0.32, 0.36, 0.40)

    def _on_floor(pt: tuple[float, float]) -> bool:
        return seam_floor is None or pt[1] >= seam_floor - 1e-3

    def _eat(i: int, dist: float, at_end: bool) -> tuple[float, float] | None:
        # Never slide the seam along the belt. Its station is the loop start.
        if dist > 0.0 and not at_end and i == 0:
            return None
        cap = _end_margin(chain[i]["L"]) - 0.01
        along = (ends[i][0] - starts[i][0]) * dirs[i][0] + (ends[i][1] - starts[i][1]) * dirs[i][1]
        if dist > 0.0 and (dist > cap or dist > along - 0.02):
            return None
        if at_end:
            point = (ends[i][0] - dirs[i][0] * dist, ends[i][1] - dirs[i][1] * dist)
        else:
            point = (starts[i][0] + dirs[i][0] * dist, starts[i][1] + dirs[i][1] * dist)
        if not _on_floor(point):
            return None
        return point

    for i in range(n):
        j = (i + 1) % n
        if not closed and i == n - 1:
            continue
        gap = math.hypot(starts[j][0] - ends[i][0], starts[j][1] - ends[i][1])
        if gap <= 0.012:
            # Keep the seam where the parallel inset put it. Move the other wall.
            if j == 0:
                ends[i] = starts[0]
            elif chain[i]["L"] >= chain[j]["L"]:
                starts[j] = ends[i]
            else:
                ends[i] = starts[j]
            continue
        ref = _blend_normal(normals[i], normals[j])
        best: tuple[tuple[float, float], float, float, float] | None = None
        for da in steps:
            end_i = _eat(i, da, True)
            if end_i is None:
                continue
            for db in steps:
                start_j = _eat(j, db, False)
                if start_j is None:
                    continue
                if da == 0.0 and db == 0.0 and gap < 0.02:
                    continue
                fit = _rim_fit(end_i, start_j, ccw, edges, ref)
                if fit is None:
                    continue
                score = (da + db, math.hypot(start_j[0] - end_i[0], start_j[1] - end_i[1]))
                if best is None or score < best[0]:
                    best = (score, fit[1], da, db)
        if best is not None:
            _score, width, da, db = best
            if da > 0.0:
                ends[i] = (ends[i][0] - dirs[i][0] * da, ends[i][1] - dirs[i][1] * da)
            if db > 0.0:
                starts[j] = (starts[j][0] + dirs[j][0] * db, starts[j][1] + dirs[j][1] * db)
            conn_w[i] = width
            continue
        # No ray hits the near edge. Sharing the vertex moves the last sample
        # by about the whole gap, so it is only safe inside the 0.05 mm budget.
        # The seam itself is not the point that moves.
        if gap <= 0.045 and _on_floor(ends[i]) and _on_floor(starts[j]):
            if j == 0:
                ends[i] = starts[0]
            elif chain[i]["L"] >= chain[j]["L"]:
                starts[j] = ends[i]
            else:
                ends[i] = starts[j]
    if closed and conn_w[-1] is None:
        # Close on the seam. A 0.02 mm gap still counts as one loop; a larger
        # one has to be pulled in, and only the incoming wall may move.
        gap = math.hypot(starts[0][0] - ends[-1][0], starts[0][1] - ends[-1][1])
        if gap <= 0.045:
            ends[-1] = starts[0]
    # A stub shorter than 0.02 mm is still one outer-edge sample, and its normal
    # is noise. Stay on the incoming vertex so the stub is not extruded.
    for i in range(n):
        if math.hypot(ends[i][0] - starts[i][0], ends[i][1] - starts[i][1]) >= 0.02:
            continue
        prev = ends[(i - 1) % n] if closed or i else starts[i]
        if not closed and i == 0:
            prev = starts[0]
        starts[i] = prev
        ends[i] = prev
        conn_w[(i - 1) % n] = None
    return {
        "starts": starts,
        "ends": ends,
        "dws": [2.0 * s for s in run_s],
        "conn_w": conn_w,
        "closed": closed,
        "ccw": ccw,
        "edges": edges,
        "normals": normals,
    }


def _shift_approach(lines: list[str], first: int, old: tuple[float, float], new: tuple[float, float]) -> bool:
    """Point the non-Z approach at the new seam so the bead starts on its shifted centre."""
    if math.hypot(new[0] - old[0], new[1] - old[1]) < 1e-4:
        return False
    j = first - 1
    while j >= 0 and not lines[j].startswith(";LAYER_CHANGE"):
        if not _is_g01(lines[j]):
            j -= 1
            continue
        w = _ff_words(lines[j])
        if "Z" in w:
            # The layer-change travel may follow the centreline inward, inside the
            # seam window, and its Z stays. A larger shift would move the station.
            zx = float(w["X"]) if "X" in w else None
            zy = float(w["Y"]) if "Y" in w else None
            if zx is None or zy is None or abs(zx - old[0]) > 0.03 or abs(zy - old[1]) > 0.03:
                return False
            if abs(new[0] - old[0]) > 0.04 or abs(new[1] - old[1]) > 0.45:
                return False
            if abs(new[0] - old[0]) > 1e-4:
                lines[j] = _ff_set_word(lines[j], "X", new[0], 3)
            if abs(new[1] - old[1]) > 1e-4:
                lines[j] = _ff_set_word(lines[j], "Y", new[1], 3)
            return True
        if "X" not in w and "Y" not in w:
            j -= 1
            continue
        ex = float(w["X"]) if "X" in w else None
        ey = float(w["Y"]) if "Y" in w else None
        if ex is not None and abs(ex - old[0]) > 0.03:
            break
        if ey is not None and abs(ey - old[1]) > 0.03:
            break
        if abs(new[0] - old[0]) > 1e-4:
            lines[j] = _ff_set_word(lines[j], "X", new[0], 3)
        if abs(new[1] - old[1]) > 1e-4:
            lines[j] = _ff_set_word(lines[j], "Y", new[1], 3)
        return True
    return False


def _restore_modal_feed(lines: list[str], used: dict[int, float], slowed: set[int]) -> None:
    """A slowed bead must not change the feed of the next move that did not ask for it."""
    modal = 0.0
    for index, line in enumerate(lines):
        if not _is_g01(line):
            continue
        w = _ff_words(line)
        if index in slowed:
            if "F" in w:
                modal = float(w["F"])
            continue
        want = used.get(index)
        if want is None:
            if "F" in w:
                modal = float(w["F"])
            continue
        incoming = float(w["F"]) if "F" in w else modal
        if abs(incoming - want) >= 1.0:
            lines[index] = _ff_set_word(line, "F", want, 0)
            modal = want
        else:
            modal = incoming


def _thick_nearest(beads: list[tuple[float, float, float, int]], skin_lo: float, skin_hi: float, above: bool) -> float | None:
    """Overlap of the nearest bead on one side of a skin. Same rule as the frozen gate."""
    best = None
    mid = (skin_lo + skin_hi) * 0.5
    for lo, hi, yc, _idx in beads:
        if abs(lo - skin_lo) < 1e-9 and abs(hi - skin_hi) < 1e-9:
            continue
        if above and yc <= mid:
            continue
        if not above and yc >= mid:
            continue
        ov = min(skin_hi, hi) - max(skin_lo, lo)
        if best is None or ov > best:
            best = ov
    return best


def _thick_stack(beads: list[tuple[float, float, float, int]]) -> bool:
    ivs = sorted((b[0], b[1]) for b in beads)
    if not ivs:
        return False
    comps = [[ivs[0][0], ivs[0][1]]]
    for lo, hi in ivs[1:]:
        placed = False
        for comp in comps:
            if min(comp[1], hi) - max(comp[0], lo) >= 0.15 - 1e-12:
                comp[0] = min(comp[0], lo)
                comp[1] = max(comp[1], hi)
                placed = True
                break
        if not placed:
            comps.append([lo, hi])
    changed = True
    while changed and len(comps) > 1:
        changed = False
        nxt: list[list[float]] = []
        for comp in comps:
            hit = None
            for other in nxt:
                if min(other[1], comp[1]) - max(other[0], comp[0]) >= 0.15 - 1e-12:
                    hit = other
                    break
            if hit is None:
                nxt.append(comp)
            else:
                hit[0] = min(hit[0], comp[0])
                hit[1] = max(hit[1], comp[1])
                changed = True
        comps = nxt
    return len(comps) == 1


def _thick_bonded(beads: list[tuple[float, float, float, int]], plow: float, phigh: float, dlow: float, dhigh: float) -> bool:
    belt = _thick_nearest(beads, plow, phigh, True)
    dorsal = _thick_nearest(beads, dlow, dhigh, False)
    if belt is None or belt < 0.15 - 1e-9 or dorsal is None or dorsal < 0.15 - 1e-9:
        return False
    return _thick_stack(beads)


def _thick_span(bounds: tuple[float, float, float, float]) -> tuple[float, float]:
    """Y interval of a fill bead pressed 0.23 mm into each outer skin, kept inside the outer rim."""
    plow, phigh, dlow, dhigh = bounds
    flo = max(phigh - _FOREFOOT_THICK_PRESS, plow + 0.02)
    fhi = min(dlow + _FOREFOOT_THICK_PRESS, dhigh - 0.02)
    if fhi < flo + 0.16:
        mid = 0.5 * (max(phigh, plow) + min(dlow, dhigh))
        flo, fhi = mid - 0.10, mid + 0.10
    return flo, fhi


def _thick_ex(x: float, y: float, cx: float, cy: float, width: float, area: float, nl: str) -> tuple[str | None, float, float]:
    length = math.hypot(round(x, 3) - round(cx, 3), round(y, 3) - round(cy, 3))
    if length < 1e-4:
        return None, cx, cy
    e = round(_ff_e_for_width(width, length, area), 5)
    if e < 1e-5:
        e = 1e-5
    feed = _ff_feed_for_e(e, length, 100000.0, area)
    return f"G1 X{x:.3f} Y{y:.3f} E{e:.5f} F{int(feed)}{nl}", x, y


def _thick_link(x: float, y: float, cx: float, cy: float, area: float, nl: str) -> tuple[str | None, float, float]:
    """Short joins are narrow extrusions. A long join is a travel, so it is not paid as plastic."""
    dist = math.hypot(x - cx, y - cy)
    if dist < 1e-4:
        return None, cx, cy
    if dist > 3.0:
        return f"G1 X{x:.3f} Y{y:.3f} F30000{nl}", x, y
    return _thick_ex(x, y, cx, cy, 0.50, area, nl)


def _thick_runs(bins: list[int], increasing: bool) -> list[list[int]]:
    ordered = sorted(bins)
    runs: list[list[int]] = []
    cur: list[int] = []
    for binn in ordered:
        if cur and binn != cur[-1] + 1:
            runs.append(cur)
            cur = []
        cur.append(binn)
    if cur:
        runs.append(cur)
    if not increasing:
        runs = [list(reversed(run)) for run in reversed(runs)]
    return runs


def _thick_run_lines(
    run: list[int],
    failing: dict,
    layer: int,
    area: float,
    nl: str,
    cx: float,
    cy: float,
) -> tuple[list[str], float, float]:
    """One continuous bead. Each column is centred on the 0.23 mm press into both skins."""
    out: list[str] = []
    y_edge = None
    for binn in run:
        flo, fhi = _thick_span(failing[(layer, binn)])
        yc = 0.5 * (flo + fhi)
        span = fhi - flo
        x_lo = binn * 0.5
        x_hi = (binn + 1) * 0.5
        increasing = len(run) < 2 or run[1] > run[0]
        x0, x1 = (x_lo, x_hi) if increasing else (x_hi, x_lo)
        if y_edge is None or abs((2 * yc - y_edge) - y_edge) > 0.35:
            link, cx, cy = _thick_link(x0, yc, cx, cy, area, nl)
            if link:
                out.append(link)
            y0 = yc
            y1 = yc
        else:
            y0 = y_edge
            y1 = 2 * yc - y0
        length = math.hypot(x1 - x0, y1 - y0) or 1.0
        cos = abs(x1 - x0) / length
        if cos < _FOREFOOT_COS_MIN:
            link, cx, cy = _thick_link(x0, yc, cx, cy, area, nl)
            if link:
                out.append(link)
            y0 = yc
            y1 = yc
            cos = 1.0
        width = max(0.40, span * cos)
        line, cx, cy = _thick_ex(x1, y1, cx, cy, width, area, nl)
        if line:
            out.append(line)
        y_edge = cy
    return out, cx, cy


def _thick_next_xy(lines: list[str], last_index: int) -> tuple[float, float] | None:
    """The next absolute travel, so a replacement can finish next to it."""
    j = last_index + 1
    seen = 0
    while j < len(lines) and seen < 12:
        raw = lines[j].strip()
        if raw.startswith(("G0", "G1")):
            seen += 1
            words = _ff_words(lines[j])
            de = float(words["E"]) if "E" in words else 0.0
            if "X" in words and "Y" in words and de <= 1e-9:
                return float(words["X"]), float(words["Y"])
            if de > 1e-9 and ("X" in words or "Y" in words):
                return None
        j += 1
    return None


def _thick_position_needed(lines: list[str], last_index: int) -> bool:
    """True when the next extrusion has no absolute XY travel ahead of it."""
    j = last_index + 1
    while j < len(lines):
        if lines[j].startswith(";LAYER_CHANGE"):
            return False
        if not _is_g01(lines[j]):
            j += 1
            continue
        words = _ff_words(lines[j])
        has_xy = "X" in words or "Y" in words
        has_e = "E" in words and float(words["E"]) > 1e-9
        if has_xy and has_e:
            return True
        if "X" in words and "Y" in words:
            return False
        j += 1
    return False


def _open_lane_count(bounds: tuple[float, float, float, float]) -> int:
    """How many ~1 mm lines fit the open gap at about 26% without filling it solid."""
    flo, fhi = _thick_span(bounds)
    room = fhi - flo - _OPEN_WIDTH
    if room <= 0.15:
        return 1
    spacing = _OPEN_WIDTH / _OPEN_DENSITY
    return max(1, int(math.ceil(room / spacing - 1e-9)))


def _open_y_at(bounds: tuple[float, float, float, float], x: float, x0: float, span: float, lane: int, n: int) -> float:
    """Centreline pressed 0.23 mm into the skins. One line waves from wall to wall."""
    flo, fhi = _thick_span(bounds)
    lo = flo + _OPEN_WIDTH * 0.5
    hi = fhi - _OPEN_WIDTH * 0.5
    if hi < lo + 0.02 or n < 2:
        if hi < lo + 0.02:
            return 0.5 * (flo + fhi)
        mid = 0.5 * (lo + hi)
        amp = 0.5 * (hi - lo)
        # A short run stays on the plantar wall. Standing the wave up would
        # fill that column and can miss the 0.5 mm column test.
        if span < _OPEN_PERIOD * 0.5:
            return lo
        phase = -math.pi / 2.0 + 2.0 * math.pi * (x - x0) / _OPEN_PERIOD
        return mid + amp * math.sin(phase)
    t = lane / (n - 1)
    return lo + t * (hi - lo)


def _open_travel(cx: float, cy: float, tx: float, ty: float, grid, nl: str) -> tuple[list[str], float, float]:
    """Travel to the next infill point without crossing a solid surface.

    A straight move that crosses internal solid infill is two legs, horizontal
    then vertical or the other way, when that pair stays clear. The straight
    move is kept when it does not cross, so an earlier layer is unchanged.
    """
    def crosses(ax: float, ay: float, bx: float, by: float) -> bool:
        return grid is not None and _crossing(grid, ax, ay, bx, by)

    if not crosses(cx, cy, tx, ty):
        return [f"G1 X{tx:.3f} Y{ty:.3f} F30000{nl}"], tx, ty
    for vx, vy in ((tx, cy), (cx, ty)):
        if math.hypot(vx - cx, vy - cy) < 1e-4 or math.hypot(tx - vx, ty - vy) < 1e-4:
            continue
        if crosses(cx, cy, vx, vy) or crosses(vx, vy, tx, ty):
            continue
        return [
            f"G1 X{vx:.3f} Y{vy:.3f} F30000{nl}",
            f"G1 X{tx:.3f} Y{ty:.3f} F30000{nl}",
        ], tx, ty
    return [f"G1 X{tx:.3f} Y{ty:.3f} F30000{nl}"], tx, ty


def _open_run_lines(
    run: list[int],
    hollow: dict,
    layer: int,
    area: float,
    nl: str,
    cx: float,
    cy: float,
    grid=None,
) -> tuple[list[str], float, float]:
    """Sparse lines across one contiguous hollow. Travels stay inside the gap."""
    n = _open_lane_count(hollow[(layer, run[0])])
    increasing = len(run) < 2 or run[1] > run[0]
    samples: list[tuple[float, int]] = []
    for i, binn in enumerate(run):
        near = binn * 0.5 if increasing else (binn + 1) * 0.5
        far = (binn + 1) * 0.5 if increasing else binn * 0.5
        if i == 0:
            samples.append((near, binn))
        samples.append((far, binn))
    xs = [x for x, _binn in samples]
    x0 = min(xs)
    span = max(xs) - x0
    lanes: list[list[tuple[float, float]]] = []
    for lane in range(n):
        seq = [(x, _open_y_at(hollow[(layer, binn)], x, x0, span, lane, n)) for x, binn in samples]
        if lane % 2 == 1:
            seq.reverse()
        lanes.append(seq)
    out: list[str] = []
    sx, sy = lanes[0][0]
    if math.hypot(sx - cx, sy - cy) > 1e-4:
        # A travel, not an extrusion: a short bead here would sit on the outer wall.
        part, cx, cy = _open_travel(cx, cy, sx, sy, grid, nl)
        out.extend(part)
    for lane, seq in enumerate(lanes):
        if lane:
            # Travel inside the gap. Extruding the turn would fill that column solid.
            tx, ty = seq[0]
            if math.hypot(tx - cx, ty - cy) > 1e-4:
                part, cx, cy = _open_travel(cx, cy, tx, ty, grid, nl)
                out.extend(part)
        for x, y in seq:
            line, cx, cy = _thick_ex(x, y, cx, cy, _OPEN_WIDTH, area, nl)
            if line:
                out.append(line)
    return out, cx, cy


def _open_band(text: str, last: int, allow: set | None = None) -> tuple[str, int]:
    """Fill an enclosed column of 3 mm or more that has no extrusion in the gap.

    Cura's thick section is one outer wall and sparse infill about 1.11 mm wide
    at about 26%. The lines are pressed about 0.23 mm into the outer wall and
    are not a solid span of the gap. ``allow`` is the columns whose section was
    already 3 mm or more before the thin bond. A file with no such column is
    returned unchanged.
    """
    if last < 1:
        return text, 0
    lines = text.splitlines(keepends=True)
    parsed = _parse_moves(lines, reset_type=False)
    if parsed["area"] <= 0:
        return text, 0
    segs = parsed["segs"]
    area = parsed["area"]
    cols = _columns(segs)
    kinds = [seg["kind"] for seg in segs]
    hollow: dict[tuple[int, int], tuple[float, float, float, float]] = {}
    for key, beads in cols.items():
        if not (1 <= key[0] <= last):
            continue
        pair = _pair_of(beads, kinds)
        if pair is None:
            continue
        plow, phigh, dlow, dhigh, thick, gap = pair
        if allow is not None and key not in allow:
            continue
        if thick < 3.0 or gap <= 0.0:
            continue
        covered = False
        for lo, hi, _yc, idx in beads:
            if kinds[idx] == "skin":
                continue
            if min(hi, dlow) - max(lo, phigh) > 0.02:
                covered = True
                break
        if not covered:
            hollow[key] = (plow, phigh, dlow, dhigh)
    if not hollow:
        return text, 0
    by_layer: dict[int, dict[int, list[int]]] = {}
    for layer, binn in hollow:
        n = _open_lane_count(hollow[(layer, binn)])
        by_layer.setdefault(layer, {}).setdefault(n, []).append(binn)
    inserts: dict[int, list[str]] = {}
    from check_travel_wall_crossings import _Grid
    surf_edges: dict[int, list[tuple[float, float, float, float]]] = {}
    for seg in segs:
        if seg["type"] in {"Top surface", "Internal solid infill", "Bottom surface"}:
            surf_edges.setdefault(seg["layer"], []).append((seg["x0"], seg["y0"], seg["x1"], seg["y1"]))
    surf_grids = {layer: _Grid(edges) for layer, edges in surf_edges.items()}
    changes = [i for i, line in enumerate(lines) if line.startswith(";LAYER_CHANGE")]
    for layer, groups in by_layer.items():
        at = changes[layer + 1] if layer + 1 < len(changes) else len(lines)
        nl = "\n" if at >= len(lines) or lines[at].endswith("\n") else ""
        px, py, typ, modal = 0.0, 0.0, "", 0.0
        for i, line in enumerate(lines):
            if i >= at:
                break
            raw = line.strip()
            if raw.startswith(";TYPE:"):
                typ = raw[6:].strip()
                continue
            if not _is_g01(line):
                continue
            words = _ff_words(line)
            if "F" in words:
                modal = float(words["F"])
            if "X" in words:
                px = float(words["X"])
            if "Y" in words:
                py = float(words["Y"])
        block = [f";TYPE:Sparse infill{nl}"]
        cx, cy = px, py
        # Wide and narrow stretches are separate, so a 14 mm gap gets several
        # lines and a 4 mm gap stays one line. Start at the end nearest the nozzle.
        pending: list[list[int]] = []
        for bins in groups.values():
            pending.extend(_thick_runs(sorted(bins), True))
        while pending:
            run = min(pending, key=lambda run: min(abs(cx - run[0] * 0.5), abs(cx - (run[-1] + 1) * 0.5)))
            pending.remove(run)
            left = run[0] * 0.5
            right = (run[-1] + 1) * 0.5
            ordered = run if abs(cx - left) <= abs(cx - right) else list(reversed(run))
            part, cx, cy = _open_run_lines(ordered, hollow, layer, area, nl, cx, cy, surf_grids.get(layer))
            block.extend(part)
        if typ:
            block.append(f";TYPE:{typ}{nl}")
        # Put the feed back without rewriting the next layer's line.
        if modal > 0:
            block.append(f"G1 F{int(modal)}{nl}")
        inserts[at] = block
    out: list[str] = []
    for index, line in enumerate(lines):
        if index in inserts:
            out.extend(inserts[index])
        out.append(line)
    if len(lines) in inserts:
        out.extend(inserts[len(lines)])
    return "".join(out), len(hollow)


def _finish_open(base: str, original: str, stats: dict) -> tuple[str, dict]:
    """Add the over-6 mm sparse band, then walk any new wall-crossing travel inside."""
    last = stats.get("last_layer") or 0
    banded, nopen = _open_band(base, int(last), stats.get("pre_infill"))
    stats["open_bins"] = nopen
    if nopen == 0 and base == original:
        return original, stats
    routed = clear_perimeter_crossings(banded)
    stats["thick_routes"] = 0 if routed == banded else routed.count("\n") - banded.count("\n")
    if routed != original:
        stats["applied"] = True
    return routed, stats


def _thick_cura_fill(text: str, stats: dict) -> tuple[str, dict]:
    """Drop the inner wall where the section was 3 mm or more, then fill the gap.

    Cura's thick section is one outer wall and sparse infill about 1.11 mm wide
    at about 26%, pressed about 0.23 mm into the outer wall. The outer wall is
    not moved. Under 3 mm is left to the thin bond. The lines stay at or under
    16.80 mm^3/s. A seam travel that cuts the outer wall is walked inside the
    part.
    """
    lines = text.splitlines(keepends=True)
    parsed = _parse_moves(lines, reset_type=False)
    last = stats.get("last_layer")
    if not last or last < 1 or parsed["area"] <= 0:
        return text, stats
    segs = parsed["segs"]
    area = parsed["area"]
    cols = _columns(segs)
    kinds = [seg["kind"] for seg in segs]
    failing: dict[tuple[int, int], tuple[float, float, float, float]] = {}
    for key, beads in cols.items():
        if not (1 <= key[0] <= last):
            continue
        pair = _pair_of(beads, kinds)
        if pair is None:
            continue
        plow, phigh, dlow, dhigh, thick, _gap = pair
        # Membership is the section before the thin bond. A loop under 3 mm stays
        # the thin bond even if the widened skins read a little over 3 mm after.
        pre = stats.get("pre_infill")
        if pre is None:
            pre = stats.get("pre_thick")
        if pre is not None:
            if key not in pre:
                continue
        elif thick < 3.0:
            continue
        failing[key] = (plow, phigh, dlow, dhigh)
    stats["thick_bins"] = len(failing)
    if not failing:
        return _finish_open(text, text, stats)
    fail_bins: dict[int, set[int]] = {}
    for layer, binn in failing:
        fail_bins.setdefault(layer, set()).add(binn)
    inner = [seg for seg in segs if seg["type"] == "Inner wall" and 1 <= seg["layer"] <= last]
    chains = _skin_chains(inner)

    def bins_of(chain: list[dict]) -> set[int]:
        layer = chain[0]["layer"]
        got: set[int] = set()
        want = fail_bins.get(layer, ())
        for seg in chain:
            if seg["cos"] < _FOREFOOT_COS_MIN:
                continue
            xa, xb = sorted((seg["x0"], seg["x1"]))
            b0 = int(math.floor(xa / 0.5))
            b1 = int(math.floor((xb - 1e-9) / 0.5))
            for binn in range(b0, b1 + 1):
                if binn not in want:
                    continue
                lo = max(xa, binn * 0.5)
                hi = min(xb, (binn + 1) * 0.5)
                if hi - lo > 1e-9:
                    got.add(binn)
        return got

    uncovered = {layer: set(bins) for layer, bins in fail_bins.items()}
    chosen: list[tuple[list[dict], set[int]]] = []
    order = sorted(range(len(chains)), key=lambda i: -len(bins_of(chains[i])))
    for index in order:
        chain = chains[index]
        layer = chain[0]["layer"]
        got = bins_of(chain) & uncovered.get(layer, set())
        if not got:
            continue
        chosen.append((chain, got))
        uncovered[layer] -= got
    # A hole inside a chain's span is still failing, so the bead has to cross it.
    # Fill it on the way across instead of travelling the hole and visiting it again.
    for chain, got in chosen:
        layer = chain[0]["layer"]
        if len(got) < 2 or not uncovered.get(layer):
            continue
        lo, hi = min(got), max(got)
        extra = {binn for binn in uncovered[layer] if lo < binn < hi}
        if extra:
            got |= extra
            uncovered[layer] -= extra
    chosen_ids = {id(chain) for chain, _got in chosen}
    repl: dict[int, list[str]] = {}
    drop: set[int] = set()
    replaced = 0
    for chain, _got in chosen:
        # One fill sweep per layer prints the bead. Drop the inner wall here.
        for seg in chain:
            drop.add(seg["i"])
        replaced += 1
    for chain in chains:
        if id(chain) in chosen_ids:
            continue
        layer = chain[0]["layer"]
        want = fail_bins.get(layer, set())
        group: list[dict] = []

        def flush(group: list[dict] = group) -> None:
            if not group:
                return
            entire = len(group) == len(chain) and group[0] is chain[0]
            if entire and not _thick_position_needed(lines, chain[-1]["i"]):
                for seg in group:
                    drop.add(seg["i"])
            else:
                nl = "\n" if lines[group[0]["i"]].endswith("\n") else ""
                ex, ey = group[-1]["x1"], group[-1]["y1"]
                repl[group[0]["i"]] = [f"G1 X{ex:.3f} Y{ey:.3f} F30000{nl}"]
                for seg in group[1:]:
                    drop.add(seg["i"])
            group.clear()

        for seg in chain:
            hit = bool(bins_of([seg]) & want)
            if hit:
                group.append(seg)
            else:
                flush()
        flush()
    # The inner wall comes out. The sparse lines are added by _open_band, one
    # about-1.11 mm line at about 26%, not a bead that fills the whole gap.
    inserts: dict[int, list[str]] = {}
    if not repl and not drop and not inserts:
        return _finish_open(text, text, stats)
    out: list[str] = []
    modal = 0.0
    restore: float | None = None
    for index, line in enumerate(lines):
        if index in inserts:
            # Before the next layer marker, so the bead stays on this layer.
            restore = modal
            out.extend(inserts[index])
        if index in repl:
            restore = modal
            out.extend(repl[index])
            continue
        if index in drop:
            continue
        if restore is not None and _is_g01(line):
            words = _ff_words(line)
            if "F" not in words and ("X" in words or "Y" in words or "E" in words):
                line = _ff_set_word(line, "F", restore, 0)
            restore = None
        if _is_g01(line):
            words = _ff_words(line)
            if "F" in words:
                modal = float(words["F"])
        out.append(line)
    if len(lines) in inserts:
        out.extend(inserts[len(lines)])
    stats["applied"] = True
    stats["thick_chains"] = replaced
    stats["thick_orphans"] = sum(len(bins) for bins in uncovered.values())
    joined = "".join(out)
    # The over-6 mm band is added to this text, then a travel that cuts the
    # wall is walked inside. The walk keeps that travel's end point, Z, and comment.
    return _finish_open(joined, text, stats)


def forefoot_cura_bond(text: str, last_layer: int | None = None) -> tuple[str, dict]:
    """Bond the forefoot the way Cura does.

    A 2-3 mm section is one skin loop. Each skin grows inward until the two
    overlap by about 0.23 mm. The centreline moves inward by half the added
    width, so the printed outer edge stays. At a corner the two offsets meet
    through a short connector whose outer rim stays on the sliced edge. A
    widened bead keeps its feed unless that would push it over 16.80 mm^3/s,
    in which case the feed drops to the cap. A section of 3 mm or more loses
    its inner wall and gets sparse infill about 1.11 mm wide at about 26%,
    pressed about 0.23 mm into the outer wall. Under 3 mm is the solid bond
    on every layer. Thickness picks solid or infill, on every layer. A bond
    past the skin already shipped on the two CI models keeps its printed rim
    on the sliced line.
    """
    lines = text.splitlines(keepends=True)
    parsed = _parse_moves(lines, reset_type=False)
    if last_layer is None:
        last_layer = forefoot_last_layer(parsed["layers"])
    stats = {
        "applied": False,
        "layers": parsed["layers"],
        "last_layer": last_layer,
        "widened": 0,
        "segments": 0,
        "added_e": 0.0,
        "approaches": 0,
        "max_q_widened": 0.0,
    }
    if not last_layer or last_layer < 1:
        stats["reason"] = "out of scope"
        return text, stats
    # Feeds every G1 used before this pass, so a later slowdown can be put back.
    used: dict[int, float] = {}
    modal = 0.0
    for index, line in enumerate(lines):
        if not _is_g01(line):
            continue
        w = _ff_words(line)
        if "F" in w:
            modal = float(w["F"])
        if "X" in w or "Y" in w or "E" in w:
            used[index] = modal
    area = parsed["area"]
    # Infill membership is the section before the thin bond moves a centreline.
    # Under 3 mm is the solid bond. 3 mm or more is infill. Both apply on every
    # layer. The rim hold below does not change that choice.
    zone = [s for s in parsed["segs"] if 1 <= s["layer"] <= last_layer]
    zone_cols = _columns(zone)
    zone_kinds = [seg["kind"] for seg in zone]
    pre_infill: set[tuple[int, int]] = set()
    for key, beads in zone_cols.items():
        pair = _pair_of(beads, zone_kinds)
        if pair is None:
            continue
        if pair[4] >= 3.0:
            pre_infill.add(key)
    stats["pre_infill"] = pre_infill
    stats["pre_thick"] = pre_infill
    shipped_skin = _shipped_skin_last(parsed["layers"])
    segs = [s for s in parsed["segs"] if s["layer"] >= 1]
    for seg in segs:
        seg["ox0"], seg["oy0"] = seg["x0"], seg["y0"]
        seg["ox1"], seg["oy1"] = seg["x1"], seg["y1"]
    if not segs:
        stats["reason"] = "no forefoot extrusion"
        return text, stats
    _assign_needs(segs)
    skin = [s for s in segs if s["kind"] == "skin"]
    chains = _skin_chains(skin)
    planned: dict[int, tuple[float, float, float, float]] = {}
    slowed: set[int] = set()
    # Extra corner moves, inserted after the named original line. Later inserts
    # are applied from the bottom of the file so the line numbers stay put.
    inserts: dict[int, list[str]] = {}
    final_feed: dict[int, float] = {}

    def _bead(x0: float, y0: float, x1: float, y1: float, width: float, feed: float, old_e: float) -> tuple[float, float, float]:
        # E is sized from the coordinates that will be written. A shorter written
        # segment would otherwise push the same E over the flow cap.
        length = math.hypot(round(x1, 3) - round(x0, 3), round(y1, 3) - round(y0, 3))
        if length < 1e-4:
            return 1e-5, feed, length
        e = round(_ff_e_for_width(width, length, area), 5)
        if e < 1e-5:
            e = 1e-5
        # Full flow matching slows the forefoot enough to miss the +2% time cap.
        # Hold the bead at 16.80 mm^3/s and never above the feed it already had.
        feed = _ff_feed_for_e(e, length, feed, area)
        return e, feed, length

    def _note(width: float, feed: float, old_e: float, e: float, widened: bool) -> None:
        stats["segments"] += 1
        if widened:
            stats["widened"] += 1
        stats["added_e"] += e - old_e
        q = _ff_flow(width, feed)
        if widened and q > stats["max_q_widened"]:
            stats["max_q_widened"] = q

    for chain in chains:
        if len(chain) < 2:
            continue
        plan = _plan_chain(chain)
        hold = shipped_skin > 0 and chain[0]["layer"] > shipped_skin
        if hold:
            _hold_outer_line(chain, plan)
        starts = plan["starts"]
        ends = plan["ends"]
        dws = plan["dws"]
        conn_w = plan["conn_w"]
        old_start = (chain[0]["x0"], chain[0]["y0"])
        new_start = starts[0]
        normals = plan["normals"]
        lead_at = None
        if _shift_approach(lines, chain[0]["i"], old_start, new_start):
            stats["approaches"] += 1
        elif math.hypot(new_start[0] - old_start[0], new_start[1] - old_start[1]) > 1e-4:
            # The layer-change station cannot follow. Keep the seam on the
            # original point and step onto the shifted centre. The closing
            # join has to land on that same station or the loop opens.
            dx = chain[0]["x1"] - chain[0]["x0"]
            dy = chain[0]["y1"] - chain[0]["y0"]
            length = math.hypot(dx, dy) or 1.0
            step = min(0.20, max(0.0, _end_margin(chain[0]["L"]) - 0.01), chain[0]["L"] * 0.45)
            stepped = (new_start[0] + dx / length * step, new_start[1] + dy / length * step)
            fit = _rim_fit(old_start, new_start, plan["ccw"], plan["edges"], normals[0])
            lead_at = new_start if fit is not None else stepped
            starts[0] = old_start
            stats["pinned"] = stats.get("pinned", 0) + 1
        nl = "\n" if lines[chain[0]["i"]].endswith("\n") else ""
        for i, seg in enumerate(chain):
            x1, y1 = ends[i]
            start = starts[i]
            if lead_at is not None and i == 0:
                fit = _rim_fit(old_start, lead_at, plan["ccw"], plan["edges"], normals[0])
                lead_w = _width_on_line(fit, old_start, lead_at, plan["edges"], hold)
                if lead_w is None:
                    lead_w = seg["w"] + dws[i]
                e, feed, _lead_len = _bead(old_start[0], old_start[1], lead_at[0], lead_at[1], lead_w, seg["f"], 0.0)
                planned[seg["i"]] = (lead_at[0], lead_at[1], e, feed)
                final_feed[seg["i"]] = feed
                if abs(feed - seg["f"]) >= 1.0:
                    slowed.add(seg["i"])
                _note(lead_w, feed, 0.0, e, True)
                width = seg["w"] + dws[i]
                be, bf, _body = _bead(lead_at[0], lead_at[1], x1, y1, width, seg["f"], seg["e"])
                inserts.setdefault(seg["i"], []).append(
                    f"G1 X{x1:.3f} Y{y1:.3f} E{be:.5f} F{bf:.0f}{nl}"
                )
                _note(width, bf, seg["e"], be, dws[i] > 1e-6)
                if abs(bf - seg["f"]) >= 1.0:
                    slowed.add(seg["i"])
            else:
                start_moved = math.hypot(start[0] - seg["x0"], start[1] - seg["y0"]) > 1e-4
                end_moved = math.hypot(x1 - seg["x1"], y1 - seg["y1"]) > 1e-4
                width = seg["w"] + dws[i]
                # The machine is at the previous join, which is this start only
                # when that join exists. Otherwise the bead actually runs from
                # the previous end to this end, and its width has to be fitted
                # to that cut or the rim leaves the sliced edge.
                cursor = starts[i]
                if i:
                    cursor = starts[i] if conn_w[i - 1] is not None else ends[i - 1]
                cut = math.hypot(cursor[0] - start[0], cursor[1] - start[1]) > 0.02
                cut_len = math.hypot(x1 - cursor[0], y1 - cursor[1])
                fitted = None
                fitted_end = (x1, y1)
                keep = 0.0
                if cut and 0.02 <= cut_len < 0.50:
                    # Leave as much of this segment's own tail as the join allows.
                    # The tail carries the sliced sample the join itself stops short of.
                    dx = x1 - start[0]
                    dy = y1 - start[1]
                    seg_len = math.hypot(dx, dy) or 1.0
                    ux, uy = dx / seg_len, dy / seg_len
                    for trial in (0.08, 0.06, 0.04, 0.0):
                        if trial >= seg_len - 0.02:
                            continue
                        end_pt = (x1 - ux * trial, y1 - uy * trial) if trial else (x1, y1)
                        if math.hypot(end_pt[0] - cursor[0], end_pt[1] - cursor[1]) < 0.02:
                            continue
                        trial_fit = _rim_fit(cursor, end_pt, plan["ccw"], plan["edges"], normals[i])
                        trial_w = _width_on_line(trial_fit, cursor, end_pt, plan["edges"], hold)
                        if trial_w is not None:
                            fitted = (0.0, trial_w)
                            fitted_end = end_pt
                            keep = trial
                            break
                if fitted is not None:
                    e, feed, _length = _bead(cursor[0], cursor[1], fitted_end[0], fitted_end[1], fitted[1], seg["f"], seg["e"])
                    planned[seg["i"]] = (fitted_end[0], fitted_end[1], e, feed)
                    final_feed[seg["i"]] = feed
                    _note(fitted[1], feed, seg["e"], e, True)
                    if abs(feed - seg["f"]) >= 1.0:
                        slowed.add(seg["i"])
                    if keep > 0.0:
                        te, tf, _tl = _bead(fitted_end[0], fitted_end[1], x1, y1, width, seg["f"], 0.0)
                        inserts.setdefault(seg["i"], []).append(
                            f"G1 X{x1:.3f} Y{y1:.3f} E{te:.5f} F{tf:.0f}{nl}"
                        )
                        _note(width, tf, 0.0, te, dws[i] > 1e-6)
                elif dws[i] <= 1e-6 and not start_moved and not end_moved:
                    final_feed[seg["i"]] = seg["f"]
                else:
                    e, feed, _length = _bead(start[0], start[1], x1, y1, width, seg["f"], seg["e"])
                    planned[seg["i"]] = (x1, y1, e, feed)
                    final_feed[seg["i"]] = feed
                    _note(width, feed, seg["e"], e, dws[i] > 1e-6)
                    if abs(feed - seg["f"]) >= 1.0:
                        slowed.add(seg["i"])
            j = (i + 1) % len(chain)
            if not plan["closed"] and j == 0:
                continue
            cx, cy = starts[j]
            anchor = ends[i]
            gap = math.hypot(cx - anchor[0], cy - anchor[1])
            # No connector means the next extrusion starts at this end and
            # bridges the gap. Past the shipped skin that bridge is the bead
            # that leaves the sliced line, so close it with its own join.
            if conn_w[i] is None and not (hold and gap > 0.02):
                continue
            if gap < 1e-4:
                continue
            ref = _blend_normal(normals[i], normals[j])
            fit = _rim_fit(anchor, (cx, cy), plan["ccw"], plan["edges"], ref)
            if fit is None and hold:
                # The join is longer than one sample. The next wall would
                # bridge it, and that bridge is what leaves the sliced line.
                fit = (0.0, seg["w"] + dws[i])
            join_w = _width_on_line(fit, anchor, (cx, cy), plan["edges"], hold)
            if join_w is None:
                # The planned width belonged to these endpoints. A pin can move
                # the seam after that, and the old width would miss the edge.
                continue
            ce, cf, _clen = _bead(anchor[0], anchor[1], cx, cy, join_w, seg["f"], 0.0)
            inserts.setdefault(seg["i"], []).append(
                f"G1 X{cx:.3f} Y{cy:.3f} E{ce:.5f} F{cf:.0f}{nl}"
            )
            _note(join_w, cf, 0.0, ce, True)
    layer_of = {seg["i"]: seg["layer"] for seg in segs}
    stats["thin_layers_after"] = sorted({layer_of[i] for i in planned if layer_of.get(i, 0) > last_layer})
    if not planned:
        stats["reason"] = "nothing to bond"
        return _thick_cura_fill(text, stats)
    for index, (x1, y1, e, feed) in planned.items():
        line = lines[index]
        line = _ff_set_word(line, "X", x1, 3)
        line = _ff_set_word(line, "Y", y1, 3)
        line = _ff_set_word(line, "E", e, 5)
        if index in slowed:
            line = _ff_set_word(line, "F", feed, 0)
        lines[index] = line
    _restore_modal_feed(lines, used, slowed)
    # A corner insert carries its own feed. The next original move keeps the
    # feed it had before the insert, written on the line so the insert cannot
    # change it.
    for index in inserts:
        j = index + 1
        while j < len(lines) and not _is_g01(lines[j]):
            if lines[j].startswith(";LAYER_CHANGE"):
                break
            j += 1
        if j >= len(lines) or not _is_g01(lines[j]):
            continue
        keep = final_feed.get(j, used.get(j))
        if keep:
            lines[j] = _ff_set_word(lines[j], "F", keep, 0)
    for index in sorted(inserts, reverse=True):
        lines[index + 1:index + 1] = inserts[index]
    stats["applied"] = True
    stats["added_e"] = round(stats["added_e"], 3)
    stats["max_q_widened"] = round(stats["max_q_widened"], 3)
    return _thick_cura_fill("".join(lines), stats)


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
        out, bond = forefoot_cura_bond(out)
        Path(sys.argv[3]).write_text(out)
        print("efix", n)
        print("seam", counts)
        print("forefoot_cura", bond)
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
