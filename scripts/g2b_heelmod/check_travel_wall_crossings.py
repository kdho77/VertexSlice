#!/usr/bin/env python3
"""Count final-G-code travels that cross an outer wall.

Per layer, outer-wall extrusion moves are chained into polygons. A non-extruding
travel counts as a crossing when its XY segment intersects that boundary and
neither endpoint is within 0.3 mm of the wall. Travels that do intersect but
start or end on the wall (seam entry/exit, including the belt-seam lift) are
counted separately and are not crossings.

Overhang-wall beads are part of the same belt perimeter (the match postprocess
treats outer and overhang as one loop). They are included in the perimeter
crossing count. Outer-wall-only crossings are reported beside that.

Top / internal-solid / bottom extrusion lines are a second boundary. A travel
that crosses one of those, with the same 0.3 mm endpoint exclusion, is a
surface crossing. Forefoot layers whose only plastic is the outer wall also
report travels whose midpoint sits inside the closed wall and clear of it:
that is the visible skin, with no separate top-surface feature (top shells
are off on this profile).
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

WORD = re.compile(r"([XYZEF])([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)")
SETTING = re.compile(r"^;\s*reduce_crossing_wall\s*=\s*(\S+)", re.M)
TIME_FOOTER = re.compile(r"^; estimated printing time \(normal mode\) = (.+)$", re.M)
FILAMENT_MM = re.compile(r"^; filament used \[mm\] = (.+)$", re.M)
FILAMENT_G = re.compile(r"^; filament used \[g\] = (.+)$", re.M)

SEAM_MM = 0.3
CELL_MM = 4.0
CLOSE_MM = 0.05
OUTER = "Outer wall"
OVERHANG = "Overhang wall"
SURFACE_TYPES = {"Top surface", "Internal solid infill", "Bottom surface"}
# Inclusive belt-Z bands from the forefoot-stitch measurements.
# Z == 200 belongs to the heel band so a layer is in one band.
REGIONS = (
    ("forefoot", 2.0, 77.0, True),
    ("arch", 150.0, 200.0, False),
    ("heel", 200.0, 250.0, True),
)


def _body(line: str) -> str:
    return line.split(";", 1)[0].strip()


def _words(line: str) -> dict[str, str]:
    return dict(WORD.findall(_body(line)))


def _is_move(line: str) -> bool:
    body = _body(line)
    return body.startswith("G0") or body.startswith("G1")


def point_seg_dist(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    dx, dy = bx - ax, by - ay
    len2 = dx * dx + dy * dy
    if len2 <= 1e-18:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / len2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _orient(ax: float, ay: float, bx: float, by: float, cx: float, cy: float) -> float:
    return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)


def _near(ax: float, ay: float, bx: float, by: float, eps: float = 1e-6) -> bool:
    return math.hypot(ax - bx, ay - by) <= eps


def travel_crosses_edge(
    ax: float, ay: float, bx: float, by: float,
    cx: float, cy: float, dx: float, dy: float,
    eps: float = 1e-7,
) -> bool:
    """True when travel AB crosses wall CD somewhere strictly inside AB."""
    o1 = _orient(ax, ay, bx, by, cx, cy)
    o2 = _orient(ax, ay, bx, by, dx, dy)
    o3 = _orient(cx, cy, dx, dy, ax, ay)
    o4 = _orient(cx, cy, dx, dy, bx, by)
    if abs(o1) <= eps and abs(o2) <= eps:
        # Collinear. Count an overlap that covers more than an endpoint of AB.
        def proj(px: float, py: float) -> float:
            return (px - ax) * (bx - ax) + (py - ay) * (by - ay)

        span = proj(bx, by)
        if abs(span) <= eps:
            return False
        lo, hi = sorted((proj(cx, cy), proj(dx, dy)))
        # Overlap with the open segment (0, span).
        a, b = (0.0, span) if span > 0 else (span, 0.0)
        left = max(lo, a)
        right = min(hi, b)
        return right - left > eps * max(1.0, abs(span))
    if o1 * o2 < -eps and o3 * o4 < -eps:
        return True

    def interior_hit(px: float, py: float) -> bool:
        if _near(px, py, ax, ay) or _near(px, py, bx, by):
            return False
        abx, aby = bx - ax, by - ay
        apx, apy = px - ax, py - ay
        ab2 = abx * abx + aby * aby
        if ab2 <= eps:
            return False
        t = (apx * abx + apy * aby) / ab2
        if t <= eps or t >= 1.0 - eps:
            return False
        qx, qy = ax + t * abx, ay + t * aby
        return math.hypot(px - qx, py - qy) <= 1e-5

    if abs(o1) <= eps and interior_hit(cx, cy):
        return True
    if abs(o2) <= eps and interior_hit(dx, dy):
        return True
    return False


class _Grid:
    def __init__(self, edges: list[tuple[float, float, float, float]]):
        self.edges = edges
        self.cells: dict[tuple[int, int], list[int]] = {}
        for i, (x0, y0, x1, y1) in enumerate(edges):
            for key in _cells_touched(x0, y0, x1, y1):
                self.cells.setdefault(key, []).append(i)
        self._stamp = 0
        self._seen = [0] * len(edges)

    def hits(self, x0: float, y0: float, x1: float, y1: float) -> bool:
        self._stamp += 1
        stamp = self._stamp
        for key in _cells_touched(x0, y0, x1, y1, pad=1):
            for i in self.cells.get(key, ()):
                if self._seen[i] == stamp:
                    continue
                self._seen[i] = stamp
                ex0, ey0, ex1, ey1 = self.edges[i]
                if travel_crosses_edge(x0, y0, x1, y1, ex0, ey0, ex1, ey1):
                    return True
        return False

    def near_endpoint(self, x: float, y: float, tol: float) -> bool:
        cx, cy = _cell(x, y)
        reach = int(math.ceil(tol / CELL_MM)) + 1
        for ix in range(cx - reach, cx + reach + 1):
            for iy in range(cy - reach, cy + reach + 1):
                for i in self.cells.get((ix, iy), ()):
                    ex0, ey0, ex1, ey1 = self.edges[i]
                    if point_seg_dist(x, y, ex0, ey0, ex1, ey1) <= tol:
                        return True
        return False


def _cell(x: float, y: float) -> tuple[int, int]:
    return int(math.floor(x / CELL_MM)), int(math.floor(y / CELL_MM))


def _cells_touched(x0: float, y0: float, x1: float, y1: float, pad: int = 0) -> list[tuple[int, int]]:
    c0x, c0y = _cell(x0, y0)
    c1x, c1y = _cell(x1, y1)
    keys = []
    for ix in range(min(c0x, c1x) - pad, max(c0x, c1x) + 1 + pad):
        for iy in range(min(c0y, c1y) - pad, max(c0y, c1y) + 1 + pad):
            keys.append((ix, iy))
    return keys


def _chain_polygons(edges: list[tuple[float, float, float, float]]) -> tuple[int, int, list[list[tuple[float, float]]]]:
    """Return (closed count, open count, closed rings as point lists)."""
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
        # Walk forward.
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


def _point_in_ring(x: float, y: float, ring: list[tuple[float, float]]) -> bool:
    inside = False
    n = len(ring)
    for i in range(n - 1):
        x0, y0 = ring[i]
        x1, y1 = ring[i + 1]
        if (y0 > y) != (y1 > y):
            xcross = x0 + (y - y0) * (x1 - x0) / (y1 - y0)
            if x < xcross:
                inside = not inside
    return inside


def _region(z: float) -> str:
    for name, lo, hi, hi_inclusive in REGIONS:
        if z < lo:
            continue
        if hi_inclusive and z <= hi:
            return name
        if not hi_inclusive and z < hi:
            return name
    return "other"


def _empty_bucket() -> dict[str, int]:
    return {
        "perimeter_crossings": 0,
        "outer_only_crossings": 0,
        "seam_approaches": 0,
        "surface_crossings": 0,
        "skin_interior_travels": 0,
        "gap_crossings": 0,
        "chain_hop_crossings": 0,
        "gap_exit_crossings": 0,
        "seam_block_crossings": 0,
    }


def _parse(text: str, seam_mm: float, sample_limit: int = 12) -> dict:
    layers: list[dict] = []
    cur: dict | None = None
    x = y = None
    z = 0.0
    abs_e = 0.0
    relative_e = True
    absolute_xyz = True
    typ = ""
    arm_hop = False
    seam_block = False
    gap_extruded = False
    last_gap_travel: int | None = None
    match = SETTING.search(text)
    header_rcw = match.group(1) if match else None
    time_m = TIME_FOOTER.search(text)
    fil_mm = FILAMENT_MM.search(text)
    fil_g = FILAMENT_G.search(text)
    extrude_mm = 0.0
    filament_e = 0.0

    def close_gap() -> None:
        nonlocal last_gap_travel, gap_extruded
        if cur is not None and last_gap_travel is not None and gap_extruded:
            cur["travels"][last_gap_travel]["gap_exit"] = True
        last_gap_travel = None
        gap_extruded = False

    for raw in text.splitlines():
        if raw.startswith(";LAYER_CHANGE"):
            close_gap()
            cur = {
                "z": None,
                "outer": [],
                "overhang": [],
                "surface": [],
                "travels": [],
                "gap_mm": 0.0,
                "features": set(),
            }
            layers.append(cur)
            continue
        if raw.startswith(";Z:") and cur is not None and cur["z"] is None:
            try:
                cur["z"] = float(raw[3:].strip())
            except ValueError:
                pass
            continue
        if raw.startswith(";TYPE:"):
            close_gap()
            typ = raw[6:].strip()
            if cur is not None and typ:
                cur["features"].add(typ)
            continue
        if "seam travel" in raw or raw.startswith(";C_SEAM"):
            seam_block = True
        body = _body(raw)
        if not body:
            continue
        if body.startswith("M83"):
            relative_e = True
            continue
        if body.startswith("M82"):
            relative_e = False
            continue
        if body.startswith("G90"):
            absolute_xyz = True
            continue
        if body.startswith("G91"):
            absolute_xyz = False
            continue
        if body.startswith("G92"):
            w = _words(raw)
            if "X" in w:
                x = float(w["X"])
            if "Y" in w:
                y = float(w["Y"])
            if "Z" in w:
                z = float(w["Z"])
            if "E" in w:
                abs_e = float(w["E"])
            continue
        if body.startswith("M204") and re.search(r"\bS10000\b", body):
            arm_hop = True
            continue
        if not _is_move(raw):
            continue
        w = _words(raw)
        px, py = x, y
        if "X" in w:
            x = float(w["X"]) if absolute_xyz or x is None else x + float(w["X"])
        if "Y" in w:
            y = float(w["Y"]) if absolute_xyz or y is None else y + float(w["Y"])
        if "Z" in w:
            z = float(w["Z"]) if absolute_xyz else z + float(w["Z"])
        de = 0.0
        if "E" in w:
            e = float(w["E"])
            if relative_e:
                de = e
            else:
                de = e - abs_e
                abs_e = e
        if px is None or py is None or x is None or y is None:
            continue
        if "X" not in w and "Y" not in w:
            continue
        dist = math.hypot(x - px, y - py)
        if dist <= 1e-6:
            arm_hop = False
            continue
        layer = cur
        if layer is None:
            continue
        if de > 1e-9:
            extrude_mm += dist
            filament_e += de
            if typ == "Gap infill":
                layer["gap_mm"] += dist
                gap_extruded = True
                last_gap_travel = None
            edge = (px, py, x, y)
            if typ == OUTER:
                layer["outer"].append(edge)
            elif typ == OVERHANG:
                layer["overhang"].append(edge)
            elif typ in SURFACE_TYPES:
                layer["surface"].append(edge)
            arm_hop = False
            seam_block = False
            continue
        hop = arm_hop
        arm_hop = False
        layer["travels"].append({
            "x0": px, "y0": py, "x1": x, "y1": y,
            "feature": typ,
            "chain_hop": hop,
            "gap_exit": False,
            "seam_block": seam_block,
            "length": dist,
        })
        if typ == "Gap infill":
            last_gap_travel = len(layer["travels"]) - 1
    close_gap()

    totals = _empty_bucket()
    regions = {name: _empty_bucket() for name, *_ in REGIONS}
    regions["other"] = _empty_bucket()
    by_feature: dict[str, int] = {}
    per_layer = []
    samples = []
    closed_layers = 0
    open_layers = 0
    forefoot_layers = 0
    forefoot_gap_layers = 0
    forefoot_gap_mm = 0.0

    for index, layer in enumerate(layers):
        z_layer = layer["z"] if layer["z"] is not None else 0.0
        outer_edges = layer["outer"]
        peri_edges = outer_edges + layer["overhang"]
        outer_grid = _Grid(outer_edges)
        peri_grid = _Grid(peri_edges)
        surf_grid = _Grid(layer["surface"])
        closed, open_n, rings = _chain_polygons(outer_edges)
        if closed:
            closed_layers += 1
        if open_n and not closed:
            open_layers += 1
        only_walls = not (layer["features"] - {OUTER, OVERHANG, "Custom", ""})
        bucket = _empty_bucket()
        region = _region(z_layer)
        if 2.0 <= z_layer <= 77.0:
            forefoot_layers += 1
            if layer["gap_mm"] > 0:
                forefoot_gap_layers += 1
                forefoot_gap_mm += layer["gap_mm"]
        for tr in layer["travels"]:
            x0, y0, x1, y1 = tr["x0"], tr["y0"], tr["x1"], tr["y1"]
            hit_outer = outer_grid.hits(x0, y0, x1, y1) if outer_edges else False
            hit_peri = peri_grid.hits(x0, y0, x1, y1) if peri_edges else False
            hit_surf = surf_grid.hits(x0, y0, x1, y1) if layer["surface"] else False
            on_wall = False
            if hit_peri:
                on_wall = peri_grid.near_endpoint(x0, y0, seam_mm) or peri_grid.near_endpoint(x1, y1, seam_mm)
            if hit_outer:
                on_outer = outer_grid.near_endpoint(x0, y0, seam_mm) or outer_grid.near_endpoint(x1, y1, seam_mm)
                if not on_outer:
                    bucket["outer_only_crossings"] += 1
            if hit_peri and on_wall:
                bucket["seam_approaches"] += 1
            elif hit_peri:
                bucket["perimeter_crossings"] += 1
                feat = tr["feature"] or "(none)"
                by_feature[feat] = by_feature.get(feat, 0) + 1
                if feat == "Gap infill":
                    bucket["gap_crossings"] += 1
                if tr["chain_hop"]:
                    bucket["chain_hop_crossings"] += 1
                if tr["gap_exit"]:
                    bucket["gap_exit_crossings"] += 1
                if tr["seam_block"]:
                    bucket["seam_block_crossings"] += 1
                if len(samples) < sample_limit:
                    samples.append({
                        "layer": index,
                        "z": round(z_layer, 3),
                        "feature": feat,
                        "chain_hop": tr["chain_hop"],
                        "gap_exit": tr["gap_exit"],
                        "seam_block": tr["seam_block"],
                        "x0": round(x0, 3), "y0": round(y0, 3),
                        "x1": round(x1, 3), "y1": round(y1, 3),
                        "length": round(tr["length"], 3),
                    })
            if hit_surf:
                on_surf = surf_grid.near_endpoint(x0, y0, seam_mm) or surf_grid.near_endpoint(x1, y1, seam_mm)
                if not on_surf:
                    bucket["surface_crossings"] += 1
            if only_walls and rings and tr["length"] > seam_mm:
                mx, my = (x0 + x1) / 2.0, (y0 + y1) / 2.0
                if any(_point_in_ring(mx, my, ring) for ring in rings):
                    on_boundary = peri_grid.near_endpoint(x0, y0, seam_mm) or peri_grid.near_endpoint(x1, y1, seam_mm)
                    if not on_boundary and not hit_peri:
                        bucket["skin_interior_travels"] += 1
        for key, value in bucket.items():
            totals[key] += value
            regions[region][key] += value
        if any(bucket.values()):
            per_layer.append({
                "layer": index,
                "z": round(z_layer, 3),
                "region": region,
                "closed_outer": closed,
                "open_outer": open_n,
                **bucket,
            })

    worst = sorted(per_layer, key=lambda row: (-row["perimeter_crossings"], -row["z"]))[:8]
    return {
        "layers": len(layers),
        "reduce_crossing_wall": header_rcw,
        "seam_mm": seam_mm,
        "closed_outer_layers": closed_layers,
        "open_outer_only_layers": open_layers,
        "filament_e_mm": round(filament_e, 2),
        "extrusion_mm": round(extrude_mm, 2),
        "footer_time": time_m.group(1).strip() if time_m else None,
        "footer_filament_mm": fil_mm.group(1).strip() if fil_mm else None,
        "footer_filament_g": fil_g.group(1).strip() if fil_g else None,
        "forefoot_layers_z2_77": forefoot_layers,
        "forefoot_gap_layers": forefoot_gap_layers,
        "forefoot_gap_mm": round(forefoot_gap_mm, 1),
        "totals": totals,
        "regions": regions,
        "by_feature": by_feature,
        "worst_layers": worst,
        "samples": samples,
        "layers_with_crossings": [row for row in per_layer if row["perimeter_crossings"]],
    }


def analyze(text: str, seam_mm: float = SEAM_MM, *, sample_limit: int = 12) -> dict:
    return _parse(text, seam_mm, sample_limit)


def assert_crossings_not_increased(current: dict, baseline: dict, key: str = "perimeter_crossings") -> None:
    """Regression gate. A higher travel-crosses-wall count fails the check."""
    got = current["totals"][key] if "totals" in current else current[key]
    exp = baseline["totals"][key] if "totals" in baseline else baseline[key]
    if got > exp:
        raise AssertionError(f"{key} increased from {exp} to {got}")


def format_report(name: str, report: dict) -> str:
    t = report["totals"]
    lines = [
        f"{name}",
        f"  layers {report['layers']}  reduce_crossing_wall={report['reduce_crossing_wall']}",
        f"  footer time {report['footer_time']}  filament {report['footer_filament_mm']} mm / {report['footer_filament_g']} g",
        f"  summed E {report['filament_e_mm']} mm  extrusion length {report['extrusion_mm']} mm",
        (
            f"  perimeter crossings {t['perimeter_crossings']}"
            f"  outer-only {t['outer_only_crossings']}"
            f"  seam approaches {t['seam_approaches']}"
            f"  surface crossings {t['surface_crossings']}"
            f"  skin-interior {t['skin_interior_travels']}"
        ),
        (
            f"  of perimeter crossings: seam-block {t['seam_block_crossings']}"
            f"  gap-infill {t['gap_crossings']}"
            f"  chain hops {t['chain_hop_crossings']}"
            f"  gap-exit {t['gap_exit_crossings']}"
        ),
        (
            f"  forefoot Z 2-77: {report['forefoot_gap_layers']}/{report['forefoot_layers_z2_77']} layers with gap fill"
            f"  ({report['forefoot_gap_mm']} mm)"
        ),
    ]
    for region, bucket in report["regions"].items():
        if region == "other" and bucket["perimeter_crossings"] == 0 and bucket["surface_crossings"] == 0:
            continue
        lines.append(
            f"  {region}: crossings {bucket['perimeter_crossings']}"
            f" (outer {bucket['outer_only_crossings']}, gap {bucket['gap_crossings']},"
            f" chain-hop {bucket['chain_hop_crossings']}, exit {bucket['gap_exit_crossings']})"
            f"  seam {bucket['seam_approaches']}  surface {bucket['surface_crossings']}"
        )
    if report["by_feature"]:
        feat = ", ".join(f"{k} {v}" for k, v in sorted(report["by_feature"].items()))
        lines.append(f"  crossing features: {feat}")
    if report["worst_layers"]:
        bits = [
            f"L{row['layer']} Z{row['z']} n={row['perimeter_crossings']} gap={row['gap_crossings']}"
            for row in report["worst_layers"]
            if row["perimeter_crossings"]
        ]
        lines.append("  worst: " + ("; ".join(bits) if bits else "(none)"))
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Count travels that cross an outer wall")
    parser.add_argument("gcode", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--seam-mm", type=float, default=SEAM_MM)
    parser.add_argument("--baseline", type=Path, help="Fail if perimeter crossings exceed this report")
    parser.add_argument("--name", default="")
    args = parser.parse_args()
    report = analyze(args.gcode.read_text(errors="replace"), seam_mm=args.seam_mm)
    if args.baseline:
        baseline = json.loads(args.baseline.read_text())
        assert_crossings_not_increased(report, baseline)
    name = args.name or args.gcode.name
    if args.json:
        print(json.dumps(report))
    else:
        print(format_report(name, report))


if __name__ == "__main__":
    main()
