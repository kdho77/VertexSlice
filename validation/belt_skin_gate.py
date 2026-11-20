#!/usr/bin/env python3
"""Inside-only belt skin gate.

Beads are planned in virtual Y and emitted at Y_mach = sqrt(2) * Y_virt
without a wider bead, so skins that meet in virtual Y leave air on the belt.
Compensation grows an internal bead about its centre, or adds one internal
bead when the open slot is at most 1.6 mm. Outer-wall centre and width are
not written.

``python3 validation/belt_skin_gate.py`` prints the column contract against
the published live OrthoCAD numbers (main 918c5373). Pass two G-code files
to score a real slice:

    python3 validation/belt_skin_gate.py --live live.gcode --branch branch.gcode
"""
from __future__ import annotations

import argparse
import math
import re
from pathlib import Path

CAP = 1.6
MAX_THICK = 4.0
# Published live OrthoCAD SAMPLE_Top numbers (main 918c5373). Not re-measured here.
LIVE = {
    "apex_core": {
        "toe_mm3": 599.3,
        "band_mm3": 1124.7,
        "time": "1h34m",
        "filament_g": 84.05,
        "travel_m": 66.0,
        "strict_inner": 1142,
        "strict_outer": 641,
    },
    "apex_light": {
        "toe_mm3": 538.4,
        "band_mm3": 871.0,
        "time": "not published in the PR table",
        "filament_g": None,
        "travel_m": None,
        "strict_inner": None,
        "strict_outer": 161,
    },
}

# Machine-Y columns. ApexCore 0-15% is L61 (no gap bead). 15-30% has a gap
# bead that does not reach either skin. ApexLight 15-30% is the 0.80 mm slot.
COLUMNS = {
    ("apex_core", "0-15"): ((0.0, 1.04), None, (1.706, 1.04)),
    ("apex_core", "15-30"): ((0.0, 1.0), (1.4, 0.9), (2.8, 1.0)),
    ("apex_light", "0-15"): ((0.0, 1.04), None, (1.706, 1.04)),
    ("apex_light", "15-30"): ((0.0, 1.0), None, (1.80, 1.0)),
}


def belt_overlap_shortfall(wall_w: float, overlap: float, scale: float = math.sqrt(2.0)) -> float:
    """Machine-Y millimetres left when a wall meets infill that already overlaps it.

    The infill edge overlaps the wall by ``overlap`` in virtual Y. G-code
    multiplies Y by ``scale`` and does not multiply the bead width. Negative
    means the bead already covers the stretch.
    """
    return (scale - 1.0) * wall_w * 0.5 - scale * overlap


def air_between(lower: tuple[float, float], upper: tuple[float, float]) -> float:
    """Millimetres of air between two machine-Y beads. Negative is overlap."""
    y0, w0 = lower
    y1, w1 = upper
    return (y1 - w1 / 2.0) - (y0 + w0 / 2.0)


def close_inside(
    bottom: tuple[float, float],
    gap: tuple[float, float] | None,
    top: tuple[float, float],
) -> tuple[tuple[float, float], list[tuple[float, float]], tuple[float, float]]:
    """Widen an existing internal bead. Never add one.

    The returned outer beads are the inputs. The gap bead keeps its centre
    and grows the same amount on both sides, up to 1.6 mm. A column with no
    internal bead is unchanged: a new bead would be a new path.
    """
    by, bw = bottom
    ty, tw = top
    lo = by + bw / 2.0
    hi = ty - tw / 2.0
    blo = by - bw / 2.0
    bhi = ty + tw / 2.0
    slot = hi - lo
    thick = bhi - blo
    if gap is None or slot < 0.02 or thick > MAX_THICK:
        return bottom, ([] if gap is None else [gap]), top
    gy, gw = gap
    a = gy - gw / 2.0
    b = gy + gw / 2.0
    need_lo = (a - lo) if a > lo + 0.02 else 0.0
    need_hi = (hi - b) if b < hi - 0.02 else 0.0
    g_need = max(need_lo, need_hi)
    g_cap = max(0.0, (CAP - gw) / 2.0)
    g_bound = max(0.0, min(a - blo, bhi - b))
    g = min(g_need, g_cap, g_bound) if g_need >= 0.01 else 0.0
    return bottom, [(gy, gw + 2.0 * g)], top


def bonded(bottom: tuple[float, float], middles: list[tuple[float, float]], top: tuple[float, float]) -> bool:
    beads = [bottom, *middles, top]
    beads.sort(key=lambda b: b[0])
    return all(air_between(lo, hi) <= 0.02 for lo, hi in zip(beads, beads[1:]))


def column_report() -> list[dict]:
    rows = []
    for (material, band), (bottom, gap, top) in COLUMNS.items():
        nb, middles, nt = close_inside(bottom, gap, top)
        widths = [nb[1], nt[1], *[m[1] for m in middles]]
        beads = sorted([nb, *middles, nt], key=lambda p: p[0])
        air = max(air_between(a, b) for a, b in zip(beads, beads[1:]))
        rows.append(
            {
                "material": material,
                "band": band,
                "air_mm": max(0.0, air),
                "outer_moved_mm": max(abs(nb[0] - bottom[0]), abs(nt[0] - top[0]), abs(nb[1] - bottom[1]), abs(nt[1] - top[1])),
                "max_bead_mm": max(widths) if widths else 0.0,
                "new_beads": len(middles) - (0 if gap is None else 1),
                "gap_center_moved_mm": 0.0 if gap is None or not middles else abs(middles[0][0] - gap[0]),
            }
        )
    return rows


def _fmt(value) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def print_contract() -> int:
    """Print the gate. Returns 0 when every synthetic column meets the geometry rules."""
    print("belt skin gate — synthetic columns vs published live 918c5373")
    print("OrthoCAD SAMPLE_Top was not sliced in this run. Shoe-level time,")
    print("filament, travel, and crossings are not measured on G-code here.")
    print()
    failed = 0
    header = f"{'material':<12} {'band':<8} {'air':>8} {'outer_dY':>8} {'max_w':>8} {'new':>5} {'gap_dY':>8}"
    print(header)
    for row in column_report():
        print(
            f"{row['material']:<12} {row['band']:<8} {row['air_mm']:8.3f} {row['outer_moved_mm']:8.3f} "
            f"{row['max_bead_mm']:8.3f} {row['new_beads']:5d} {row['gap_center_moved_mm']:8.3f}"
        )
        if row["new_beads"] != 0 or row["outer_moved_mm"] > 0.0 or row["max_bead_mm"] > CAP + 1e-6:
            failed += 1
        if row["gap_center_moved_mm"] > 0.0:
            failed += 1
    print()
    by_key = {(row["material"], row["band"]): row for row in column_report()}
    print(f"{'metric':<28} {'live 918c5373':<28} {'this change'}")
    for material, live in LIVE.items():
        toe = by_key[(material, "0-15")]["air_mm"]
        band = by_key[(material, "15-30")]["air_mm"]
        print(f"{material + ' toe 0-15% air mm3':<28} {_fmt(live['toe_mm3']):<28} column air {toe:.3f} mm; shoe not sliced")
        print(f"{material + ' 15-30% air mm3':<28} {_fmt(live['band_mm3']):<28} column air {band:.3f} mm; shoe not sliced")
        print(f"{material + ' time':<28} {_fmt(live['time']):<28} not sliced")
        print(f"{material + ' filament g':<28} {_fmt(live['filament_g']):<28} not sliced")
        print(f"{material + ' travel m':<28} {_fmt(live['travel_m']):<28} not sliced")
        print(
            f"{material + ' strict i/o':<28} "
            f"{_fmt(live['strict_inner'])}/{_fmt(live['strict_outer']):<26} not sliced"
        )
    print()
    print("No bead is added. Outer centre and width stay put. Nothing grown here exceeds 1.6 mm.")
    print("Air stays where the column has no internal bead, or the 1.6 mm cap cannot cover the slot.")
    print("A new bead would be a new path. That is the travel and crossing budget, so it is not spent.")
    return 1 if failed else 0


_MOVE = re.compile(
    r"^(?P<cmd>G0|G1)\s*(?P<body>.*)$",
    re.IGNORECASE,
)
_WORD = re.compile(r"([XYZEF])\s*(-?\d+(?:\.\d+)?)", re.IGNORECASE)


def _words(body: str) -> dict[str, float]:
    return {m.group(1).upper(): float(m.group(2)) for m in _WORD.finditer(body.split(";", 1)[0])}


def score_gcode(path: Path) -> dict:
    """Outer-wall samples, bead widths, travel, and extrusion length from one file."""
    role = ""
    width = 0.0
    x = y = None
    layer = 0
    travel = 0.0
    extrude = 0.0
    over = []
    outer = []
    for raw in path.read_text(errors="replace").splitlines():
        line = raw.strip()
        if line.startswith(";TYPE:"):
            role = line.split(":", 1)[1].strip()
            continue
        if line.startswith(";WIDTH:"):
            try:
                width = float(line.split(":", 1)[1])
            except ValueError:
                pass
            continue
        if line.startswith(";LAYER_CHANGE") or line.startswith(";Z:"):
            if line.startswith(";LAYER_CHANGE"):
                layer += 1
            continue
        m = _MOVE.match(line)
        if not m:
            continue
        w = _words(m.group("body"))
        nx = w.get("X", x)
        ny = w.get("Y", y)
        if x is None or y is None or nx is None or ny is None:
            x, y = nx, ny
            continue
        dist = math.hypot(nx - x, ny - y)
        if "E" in w and abs(w["E"]) > 1e-9 and dist > 0.0:
            extrude += dist
            if width > CAP + 1e-6:
                over.append(width)
            if role.lower().startswith("outer"):
                outer.append((layer, x, y, nx, ny, width))
        elif dist > 0.0 and m.group("cmd").upper() == "G1" and "E" not in w:
            travel += dist
        elif dist > 0.0 and m.group("cmd").upper() == "G0":
            travel += dist
        x, y = nx, ny
    return {
        "travel_mm": travel,
        "extrude_mm": extrude,
        "beads_over_1_6": len(over),
        "max_bead_mm": max(over) if over else 0.0,
        "outer_segments": len(outer),
        "outer": outer,
    }


def _outer_delta(live: dict, branch: dict) -> float:
    """Max distance from a branch outer-wall sample to a live sample on the same layer.

    Points are bucketed by X so a full insole layer stays linear in the local window.
    """
    buckets: dict[tuple[int, int], list[tuple[float, float]]] = {}
    for layer, x0, y0, x1, y1, _w in live["outer"]:
        for x, y in ((x0, y0), (x1, y1)):
            buckets.setdefault((layer, int(math.floor(x))), []).append((x, y))
    worst = 0.0
    for layer, x0, y0, x1, y1, _w in branch["outer"]:
        for x, y in ((x0, y0), (x1, y1)):
            k = int(math.floor(x))
            pts = []
            for dx in (-1, 0, 1):
                pts.extend(buckets.get((layer, k + dx), ()))
            if not pts:
                return math.inf
            worst = max(worst, min(math.hypot(x - px, y - py) for px, py in pts))
    return worst


def print_gcode(live_path: Path, branch_path: Path) -> int:
    live = score_gcode(live_path)
    branch = score_gcode(branch_path)
    edge = _outer_delta(live, branch)
    print(f"live   {live_path}")
    print(f"branch {branch_path}")
    print(f"{'metric':<24} {'live':>14} {'branch':>14} {'delta':>14}")
    rows = [
        ("travel_mm", live["travel_mm"], branch["travel_mm"]),
        ("extrude_mm", live["extrude_mm"], branch["extrude_mm"]),
        ("beads_over_1_6", live["beads_over_1_6"], branch["beads_over_1_6"]),
        ("outer_segments", live["outer_segments"], branch["outer_segments"]),
    ]
    failed = 0
    for name, a, b in rows:
        print(f"{name:<24} {a:14.3f} {b:14.3f} {b - a:14.3f}")
    print(f"{'outer_sample_delta_mm':<24} {'0':>14} {edge:14.3f}")
    if branch["travel_mm"] > live["travel_mm"] + 0.05:
        failed += 1
    if branch["beads_over_1_6"] > live["beads_over_1_6"]:
        failed += 1
    if edge > 0.02:
        failed += 1
    return failed


def main() -> int:
    parser = argparse.ArgumentParser(description="Belt skin gate: synthetic columns, or two G-code files")
    parser.add_argument("--live", type=Path)
    parser.add_argument("--branch", type=Path)
    args = parser.parse_args()
    status = print_contract()
    if args.live or args.branch:
        if not (args.live and args.branch):
            print("pass both --live and --branch")
            return 2
        print()
        status |= print_gcode(args.live, args.branch)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
