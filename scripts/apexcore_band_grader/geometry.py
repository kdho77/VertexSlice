"""Geometry checks on belt-layer toolpaths.

X is across the belt. Y is the part thickness off the belt (small Y is the
plantar / belt face). A layer's Z is constant. Part thickness at an X station
is the outer-surface distance between the lower and upper outer-wall beads.

Shell paths are ``Outer wall`` and ``Overhang wall``. Every other extrusion
(inner wall, sparse infill, solid infill) is interior material. A column is
hollow only when the shell is at least 3 mm thick, the clear gap between the
shell beads is over 1 mm, and no interior extrusion occupies that gap.
"""

from __future__ import annotations

import math

from gcode import Layer, Segment

SHELL = {"Outer wall", "Overhang wall"}
THICK_MIN_MM = 3.0
AIR_GAP_MM = 1.0
SAMPLE_MM = 0.1


def capsule_y_span(x0: float, y0: float, x1: float, y1: float, radius: float, x: float) -> tuple[float, float] | None:
    """Y interval where the vertical line x=const hits a stadium of ``radius`` around a segment.

    A stadium is convex, so the boundary hits are the endpoints of one interval.
    """
    ys: list[float] = []
    radius_sq = radius * radius
    for px, py in ((x0, y0), (x1, y1)):
        dx = px - x
        if dx * dx <= radius_sq + 1e-12:
            half = math.sqrt(max(0.0, radius_sq - dx * dx))
            ys.append(py - half)
            ys.append(py + half)
    dx = x1 - x0
    dy = y1 - y0
    length = math.hypot(dx, dy)
    if length > 1e-12 and radius > 0.0:
        nx = -dy / length
        ny = dx / length
        for sign in (1.0, -1.0):
            ax = x0 + sign * radius * nx
            ay = y0 + sign * radius * ny
            bx = x1 + sign * radius * nx
            by = y1 + sign * radius * ny
            if abs(bx - ax) < 1e-12:
                if abs(ax - x) <= 1e-8:
                    ys.append(ay)
                    ys.append(by)
            else:
                t = (x - ax) / (bx - ax)
                if -1e-8 <= t <= 1.0 + 1e-8:
                    ys.append(ay + t * (by - ay))
    if not ys:
        return None
    return min(ys), max(ys)


def merge_intervals(intervals: list[tuple[float, float]], gap: float = 0.02) -> list[list[float]]:
    if not intervals:
        return []
    ordered = sorted(intervals)
    merged = [[ordered[0][0], ordered[0][1]]]
    for start, end in ordered[1:]:
        if start <= merged[-1][1] + gap:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def _extrusions(segments: list[Segment]) -> list[Segment]:
    return [seg for seg in segments if seg.kind == "extrude"]


def shell_and_interior_at(segments: list[Segment], x: float) -> tuple[list[list[float]], list[tuple[float, float]]]:
    shell: list[tuple[float, float]] = []
    interior: list[tuple[float, float]] = []
    for seg in _extrusions(segments):
        radius = max(seg.width, 0.0) / 2.0
        if x < min(seg.x0, seg.x1) - radius or x > max(seg.x0, seg.x1) + radius:
            continue
        span = capsule_y_span(seg.x0, seg.y0, seg.x1, seg.y1, radius, x)
        if span is None:
            continue
        if seg.typ in SHELL:
            shell.append(span)
        else:
            interior.append(span)
    return merge_intervals(shell), interior


def ray_is_hollow(segments: list[Segment], x: float) -> bool:
    shell, interior = shell_and_interior_at(segments, x)
    if len(shell) < 2:
        return False
    thickness = shell[-1][1] - shell[0][0]
    if thickness < THICK_MIN_MM:
        return False
    for (bottom_lo, bottom_hi), (top_lo, top_hi) in zip(shell, shell[1:]):
        gap = top_lo - bottom_hi
        if gap <= AIR_GAP_MM:
            continue
        filled = False
        for start, end in interior:
            if min(end, top_lo) - max(start, bottom_hi) > 0.01:
                filled = True
                break
        if not filled:
            return True
    return False


def hollow_runs(segments: list[Segment], step: float = SAMPLE_MM) -> list[tuple[float, float]]:
    prepared = []
    for seg in _extrusions(segments):
        radius = max(seg.width, 0.0) / 2.0
        prepared.append((min(seg.x0, seg.x1) - radius, max(seg.x0, seg.x1) + radius, radius, seg))
    if not prepared:
        return []
    xs = [coord for seg in _extrusions(segments) for coord in (seg.x0, seg.x1)]
    x = math.floor(min(xs) / step) * step
    limit = max(xs)
    runs: list[tuple[float, float]] = []
    current: list[float] | None = None
    while x <= limit + 1e-9:
        shell: list[tuple[float, float]] = []
        interior: list[tuple[float, float]] = []
        for lo, hi, radius, seg in prepared:
            if x < lo or x > hi:
                continue
            span = capsule_y_span(seg.x0, seg.y0, seg.x1, seg.y1, radius, x)
            if span is None:
                continue
            if seg.typ in SHELL:
                shell.append(span)
            else:
                interior.append(span)
        shell_m = merge_intervals(shell)
        hollow = False
        if len(shell_m) >= 2 and shell_m[-1][1] - shell_m[0][0] >= THICK_MIN_MM:
            for (_blo, bhi), (tlo, _thi) in zip(shell_m, shell_m[1:]):
                if tlo - bhi <= AIR_GAP_MM:
                    continue
                if any(min(end, tlo) - max(start, bhi) > 0.01 for start, end in interior):
                    continue
                hollow = True
                break
        if hollow:
            if current is None:
                current = [x, x]
            else:
                current[1] = x
        elif current is not None:
            runs.append((current[0], current[1]))
            current = None
        x += step
    if current is not None:
        runs.append((current[0], current[1]))
    return runs


def column_report(layer: Layer, step: float = SAMPLE_MM) -> dict:
    """Count 1 mm hollow columns. A run counts only when its covered width is over 1 mm."""
    runs = hollow_runs(layer.segments, step)
    columns = 0
    width = 0.0
    kept = []
    for first, last in runs:
        coverage = (last - first) + step
        if coverage > AIR_GAP_MM + 1e-9:
            count = max(1, int(math.floor(coverage + 1e-9)))
            columns += count
            width += coverage
            kept.append({"x0": round(first, 3), "x1": round(last, 3), "width_mm": round(coverage, 3), "columns": count})
    return {"columns": columns, "width_mm": round(width, 3), "runs": kept}


def outer_crossings(segments: list[Segment], x: float) -> list[tuple[float, float]]:
    """Centerline Y and width where an outer/overhang wall crosses the vertical line."""
    hits: list[tuple[float, float]] = []
    for seg in _extrusions(segments):
        if seg.typ not in SHELL:
            continue
        dx = seg.x1 - seg.x0
        if abs(dx) < 1e-9:
            if abs(x - seg.x0) <= 1e-6:
                hits.append((seg.y0, seg.width))
                hits.append((seg.y1, seg.width))
            continue
        if min(seg.x0, seg.x1) - 1e-9 <= x <= max(seg.x0, seg.x1) + 1e-9:
            t = (x - seg.x0) / dx
            if -1e-6 <= t <= 1.0 + 1e-6:
                hits.append((seg.y0 + t * (seg.y1 - seg.y0), seg.width))
    hits.sort()
    return hits


def part_thickness(segments: list[Segment], x: float) -> float | None:
    """Outer-surface thickness of the whole section at x, or None if the shell is not closed."""
    hits = outer_crossings(segments, x)
    if len(hits) < 2:
        return None
    y0, w0 = hits[0]
    y1, w1 = hits[-1]
    if y1 - y0 < 0.05:
        return None
    return (y1 + w1 / 2.0) - (y0 - w0 / 2.0)


def thickness_at_point(segments: list[Segment], x: float, y: float) -> float | None:
    """Thickness of the shell section that contains (x, y). None if the point is outside the shell."""
    hits = outer_crossings(segments, x)
    best: tuple[float, float] | None = None
    for (y0, w0), (y1, w1) in zip(hits, hits[1:]):
        if y1 - y0 <= 0.05:
            continue
        if y0 - 1e-4 <= y <= y1 + 1e-4:
            span = y1 - y0
            thick = (y1 + w1 / 2.0) - (y0 - w0 / 2.0)
            if best is None or span < best[0]:
                best = (span, thick)
    if best is None:
        return None
    return best[1]


def _orient(ax: float, ay: float, bx: float, by: float, cx: float, cy: float) -> float:
    return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)


def segments_properly_cross(a: Segment, b: Segment, eps: float = 1e-9) -> bool:
    o1 = _orient(a.x0, a.y0, a.x1, a.y1, b.x0, b.y0)
    o2 = _orient(a.x0, a.y0, a.x1, a.y1, b.x1, b.y1)
    o3 = _orient(b.x0, b.y0, b.x1, b.y1, a.x0, a.y0)
    o4 = _orient(b.x0, b.y0, b.x1, b.y1, a.x1, a.y1)
    return o1 * o2 < -eps and o3 * o4 < -eps


def point_segment_distance(px: float, py: float, seg: Segment) -> float:
    dx = seg.x1 - seg.x0
    dy = seg.y1 - seg.y0
    length_sq = dx * dx + dy * dy
    if length_sq < 1e-18:
        return math.hypot(px - seg.x0, py - seg.y0)
    t = max(0.0, min(1.0, ((px - seg.x0) * dx + (py - seg.y0) * dy) / length_sq))
    return math.hypot(px - (seg.x0 + t * dx), py - (seg.y0 + t * dy))


def segment_distance(a: Segment, b: Segment) -> float:
    if segments_properly_cross(a, b):
        return 0.0
    return min(
        point_segment_distance(a.x0, a.y0, b),
        point_segment_distance(a.x1, a.y1, b),
        point_segment_distance(b.x0, b.y0, a),
        point_segment_distance(b.x1, b.y1, a),
    )


def segment_length(seg: Segment) -> float:
    return math.hypot(seg.x1 - seg.x0, seg.y1 - seg.y0)


def sample_segment(seg: Segment, step: float = 0.25) -> list[tuple[float, float]]:
    length = segment_length(seg)
    points = [(seg.x0, seg.y0), (seg.x1, seg.y1)]
    if length < 1e-9:
        return points
    count = max(1, int(length / step))
    for i in range(1, count):
        t = i / count
        points.append((seg.x0 + t * (seg.x1 - seg.x0), seg.y0 + t * (seg.y1 - seg.y0)))
    return points
