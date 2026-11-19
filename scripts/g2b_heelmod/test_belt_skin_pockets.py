#!/usr/bin/env python3
"""Physical bead envelopes on the sample insole.

Orca plans the walls in virtual Y. BeltTransform then writes
Y_mach = sqrt(2) * Y_virt and does not scale the bead width, so the skins
separate. The numbers below are the measured machine coordinates
(layer Z 26.672). fit_skin_beads() is the machine-frame contract of
PerimeterGenerator.cpp. A slot up to 1.5 mm is one bead. A wider slot, up
to 3 mm in a section up to 5 mm thick, is an odd number of beads of at most
1.5 mm so the path still finishes at the far end. A column with no bead
gets that same fill. The 0.2 mm raster over the toe bands is zero air.
"""
from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path[:0] = [str(HERE)]

from check_travel_wall_crossings import travel_crosses_edge  # noqa: E402
from slice_apexcore_g2b import chain_gap_fill_travels  # noqa: E402

# Sample insole, layer ~40, machine Z 26.672, X=133.
BOTTOM = (0.0279, 1.0)
GAP = (0.8759, 0.301)
TOP = (1.7386, 1.0)
MAX_WIDTH = 1.5
MAX_SLOT = 3.0
MAX_THICK = 5.0
# Live 918c5373 strict totals. A later slice fails a layer that goes above
# its own live count; these are the file-level ceilings.
LIVE_STRICT_INNER = 1142
LIVE_STRICT_OUTER = 641
RASTER_MM = 0.2


def air_between(lower: tuple[float, float], upper: tuple[float, float]) -> float:
    """Millimetres of air between two machine-Y beads. Negative is overlap."""
    y0, w0 = lower
    y1, w1 = upper
    return (y1 - w1 / 2.0) - (y0 + w0 / 2.0)


def _slot(bottom: tuple[float, float], top: tuple[float, float]) -> tuple[float, float, float, float]:
    by, bw = bottom
    ty, tw = top
    lo = by + bw / 2.0
    hi = ty - tw / 2.0
    slot = hi - lo
    thick = (ty + tw / 2.0) - (by - bw / 2.0)
    return lo, hi, slot, thick


def through_strips(slot: float) -> int:
    """Odd count so a zig-zag that leaves the near end arrives at the far end."""
    n = max(1, math.ceil(slot / MAX_WIDTH - 1e-9))
    if n % 2 == 0:
        n += 1
    return n


def fit_skin_beads(
    bottom: tuple[float, float],
    gap: tuple[float, float] | None,
    top: tuple[float, float],
) -> list[tuple[float, float]]:
    """Machine-Y centers and widths that tile the slot between two skins.

    No gap bead (gap is None) still gets a tile: that is layer 61, two walls
    and air. A slot wider than 3 mm, or a section thicker than 5 mm, is not
    a thin skin. The original bead is then only capped at 1.5 mm.
    """
    lo, _hi, slot, thick = _slot(bottom, top)
    if gap is None:
        gy, gw = (lo + slot / 2.0, 0.0)
    else:
        gy, gw = gap
    capped = min(gw, MAX_WIDTH) if gw else 0.0
    inside = lo - 0.75 <= gy <= lo + slot + 0.75
    if gap is not None and (slot > MAX_SLOT or thick > MAX_THICK or slot < 0.2 or not inside):
        return [(gy, capped)]
    if slot > MAX_SLOT or thick > MAX_THICK or slot < 0.2:
        return [(gy, capped)] if gap is not None else []
    n = through_strips(slot)
    pitch = slot / n
    width = pitch if n == 1 else min(MAX_WIDTH, pitch + 0.04)
    return [(lo + pitch * (i + 0.5), width) for i in range(n)]


def fit_skin_bead(
    bottom: tuple[float, float],
    gap: tuple[float, float],
    top: tuple[float, float],
) -> tuple[float, float]:
    """The strip nearest the original gap bead. Layer 40 is the one strip."""
    gy = gap[0]
    beads = fit_skin_beads(bottom, gap, top)
    return min(beads, key=lambda bead: (abs(bead[0] - gy), bead[0]))


def raster_air(
    bottom: tuple[float, float],
    beads: list[tuple[float, float]],
    top: tuple[float, float],
    step: float = RASTER_MM,
) -> float:
    """Longest uncovered run inside the slot, sampled every `step` mm.

    A sample sits on a bead when it is within half that bead's width.
    Zero means the 0.2 mm raster sees no air between the skins.
    """
    lo = bottom[0] + bottom[1] / 2.0
    hi = top[0] - top[1] / 2.0
    if hi <= lo:
        return 0.0
    worst = 0.0
    run = 0.0
    y = lo + step / 2.0
    while y < hi:
        covered = False
        for cy, width in beads:
            if abs(y - cy) <= width / 2.0 + 1e-9:
                covered = True
                break
        if covered:
            run = 0.0
        else:
            run += step
            worst = max(worst, run)
        y += step
    return worst


def band_audit(
    bottom: tuple[float, float],
    beads: list[tuple[float, float]],
    top: tuple[float, float],
) -> dict[str, float]:
    """One worst column, scored the way the toe bands are scored."""
    worst = raster_air(bottom, beads, top)
    if worst <= 0.02:
        return {"layers": 0, "mm3": 0.0, "worst_mm": 0.0}
    return {"layers": 1, "mm3": worst * RASTER_MM * 0.46, "worst_mm": worst}


def bond_path(x0: float, x1: float, y0: float, y1: float, lo: float, hi: float) -> tuple[list[tuple[float, float]], float]:
    """The zig-zag from the original start to the original end. No retrace."""
    slot = hi - lo
    n = through_strips(slot)
    pitch = slot / n
    width = pitch if n == 1 else min(MAX_WIDTH, pitch + 0.04)
    pts = [(x0, y0)]
    for strip in range(n):
        forward = strip % 2 == 0
        xs = (x0, x1) if forward else (x1, x0)
        y = lo + pitch * (strip + 0.5)
        for x in xs:
            if pts[-1] != (x, y):
                pts.append((x, y))
    end = (x1, y1)
    if pts[-1] != end:
        pts.append(end)
    return pts, width


def retrace_count(points: list[tuple[float, float]], tol: float = 0.05) -> int:
    """Immediate out-and-back along the same centerline."""
    count = 0
    segs = list(zip(points, points[1:]))
    for (a, b), (c, d) in zip(segs, segs[1:]):
        ab = (b[0] - a[0], b[1] - a[1])
        cd = (d[0] - c[0], d[1] - c[1])
        lab = math.hypot(*ab)
        lcd = math.hypot(*cd)
        if lab < 1e-6 or lcd < 1e-6:
            continue
        dot = (ab[0] * cd[0] + ab[1] * cd[1]) / (lab * lcd)
        if dot > -0.99:
            continue
        if _point_seg_dist(c, a, b) <= tol and _point_seg_dist(d, a, b) <= tol:
            count += 1
    return count


def _point_seg_dist(
    p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]
) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    len2 = dx * dx + dy * dy
    if len2 <= 1e-18:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / len2))
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))


def strict_crossings(text: str, clearance: float = 0.05) -> dict[int, dict[str, int]]:
    """Per-layer travels that cross an inner or outer wall.

    An endpoint within `clearance` mm of the wall is the seam exclusion.
    0.05 mm is stricter than the 0.3 mm gate, which is why live's count is
    1142 inner and 641 outer rather than the older perimeter total.
    """
    layers: dict[int, dict[str, list]] = {}
    layer = -1
    typ = ""
    x = y = None
    for line in text.splitlines():
        if line.startswith(";LAYER_CHANGE"):
            layer += 1
            layers.setdefault(layer, {"inner": [], "outer": [], "travel": []})
            typ = ""
            continue
        if line.startswith(";TYPE:"):
            typ = line[6:].strip()
            continue
        body = line.split(";", 1)[0].strip()
        if not body.startswith(("G0", "G1")) or x is None or y is None:
            if body.startswith(("G0", "G1")):
                words = dict(__import__("re").findall(r"([XY])([+-]?\d+\.?\d*)", body))
                if "X" in words:
                    x = float(words["X"])
                if "Y" in words:
                    y = float(words["Y"])
            continue
        words = dict(__import__("re").findall(r"([XYE])([+-]?\d+\.?\d*)", body))
        nx = float(words["X"]) if "X" in words else x
        ny = float(words["Y"]) if "Y" in words else y
        extruding = "E" in words and float(words["E"]) > 0
        kind = ""
        if typ in ("Outer wall", "Overhang wall"):
            kind = "outer"
        elif typ == "Inner wall":
            kind = "inner"
        bucket = layers.setdefault(layer, {"inner": [], "outer": [], "travel": []})
        if extruding and kind and (nx != x or ny != y):
            bucket[kind].append((x, y, nx, ny))
        elif not extruding and (nx != x or ny != y):
            bucket["travel"].append((x, y, nx, ny))
        x, y = nx, ny
    out: dict[int, dict[str, int]] = {}
    for idx, bucket in layers.items():
        counts = {"inner": 0, "outer": 0}
        for kind in ("inner", "outer"):
            for tx0, ty0, tx1, ty1 in bucket["travel"]:
                for wx0, wy0, wx1, wy1 in bucket[kind]:
                    if not travel_crosses_edge(tx0, ty0, tx1, ty1, wx0, wy0, wx1, wy1):
                        continue
                    if _point_seg_dist((tx0, ty0), (wx0, wy0), (wx1, wy1)) <= clearance:
                        continue
                    if _point_seg_dist((tx1, ty1), (wx0, wy0), (wx1, wy1)) <= clearance:
                        continue
                    counts[kind] += 1
        out[idx] = counts
    return out


class BeltSkinPocketTest(unittest.TestCase):
    def test_layer_40_has_air_beside_the_gap_bead(self) -> None:
        self.assertAlmostEqual(air_between(BOTTOM, GAP), 0.198, delta=0.002)
        self.assertAlmostEqual(air_between(GAP, TOP), 0.212, delta=0.002)

    def test_layer_40_beads_touch_in_machine_y(self) -> None:
        y, width = fit_skin_bead(BOTTOM, GAP, TOP)
        fitted = (y, width)
        self.assertLessEqual(width, MAX_WIDTH)
        self.assertAlmostEqual(width, 0.7107, delta=0.001)
        self.assertLessEqual(air_between(BOTTOM, fitted), 0.02)
        self.assertLessEqual(air_between(fitted, TOP), 0.02)
        self.assertGreater(air_between(BOTTOM, fitted), -0.02)
        self.assertGreater(air_between(fitted, TOP), -0.02)

    def test_thick_section_is_not_a_skin_pocket(self) -> None:
        # A gyroid core is thicker than 5 mm. The gap bead stays put.
        y, width = fit_skin_bead((0.0, 1.0), (4.0, 0.40), (8.0, 1.0))
        self.assertEqual((y, width), (4.0, 0.40))

    def test_nominal_width_over_1_5_mm_is_capped(self) -> None:
        # Layer 423 reached a nominal gap width of 1.616 mm.
        y, width = fit_skin_bead((0.0, 1.0), (4.0, 1.616), (8.0, 1.0))
        self.assertEqual(y, 4.0)
        self.assertEqual(width, MAX_WIDTH)

    def test_gap_extrusion_does_not_keep_travel_feed(self) -> None:
        src = "\n".join(
            [
                ";TYPE:Gap infill",
                "G1 X0 Y0 F12720",
                "G1 X10 Y0 E0.200",
                "G1 X20 Y0 E0.200 F12720",
                "G1 X30 Y0 E0.200 F1200",
                ";TYPE:Sparse infill",
                "G1 X0 Y5 F12720",
                "",
            ]
        )
        out = chain_gap_fill_travels(src)
        extrusions = [
            line
            for line in out.splitlines()
            if line.startswith("G1") and "E" in line.split(";", 1)[0] and "X" in line
        ]
        self.assertTrue(extrusions)
        for line in extrusions:
            if "Y0" not in line:
                continue
            self.assertNotIn("F12720", line)
            self.assertTrue("F1560" in line or "F1200" in line, line)
        self.assertIn("G1 X0 Y0 F12720", out)

    def test_toe_bands_raster_to_zero_air(self) -> None:
        """0.2 mm raster of the columns the PM still measured as air.

        0-15%: layer 61, X=99.25, two outer walls 1.71 mm apart, no bead,
        0.67 mm of air. Width 1.04 mm makes the edge gap 0.67 mm.
        15-30%: the bead cdd9ac88 shrank (1.074 mm in a 1.726 mm slot) and
        the worst gap, 0.96 mm, all on one side of a 1.074 mm bead.
        """
        bands = {
            "0-15": (
                (0.0, 1.04),
                None,
                (1.71, 1.04),
            ),
            "15-30": (
                (-0.5, 1.0),
                (1.074 / 2.0, 1.074),
                (1.074 + 0.96 + 0.5, 1.0),
            ),
            "15-30-shrink": (
                (-0.5, 1.0),
                (1.726 / 2.0, 1.074),
                (1.726 + 0.5, 1.0),
            ),
        }
        for name, (bottom, gap, top) in bands.items():
            beads = fit_skin_beads(bottom, gap, top)
            self.assertTrue(beads, name)
            for _y, width in beads:
                self.assertLessEqual(width, MAX_WIDTH, name)
                self.assertGreaterEqual(width, 0.15, name)
            audit = band_audit(bottom, beads, top)
            self.assertEqual(audit, {"layers": 0, "mm3": 0.0, "worst_mm": 0.0}, name)
            self.assertEqual(raster_air(bottom, beads, top, step=RASTER_MM), 0.0, name)

    def test_wide_slot_is_not_shrunk_to_one_bead(self) -> None:
        """cdd9ac88 turned 1.074 mm into 0.863 mm and never emitted strip two."""
        bottom = (-0.5, 1.0)
        top = (1.726 + 0.5, 1.0)
        gap = (1.726 / 2.0, 1.074)
        beads = fit_skin_beads(bottom, gap, top)
        self.assertGreaterEqual(len(beads), 3)
        self.assertNotEqual(len(beads), 1)
        self.assertTrue(all(width <= MAX_WIDTH for _y, width in beads))
        # A single 0.863 mm bead is the failure. The tile has to be wider
        # than the original bead, because the original left the slot open.
        covered = sum(width for _y, width in beads)
        self.assertGreater(covered, 1.074)
        self.assertLess(covered, 1.726 + 0.2)

    def test_wide_path_keeps_its_ends_and_does_not_retrace(self) -> None:
        lo, hi = 0.5, 2.5
        pts, width = bond_path(100.0, 140.0, 1.2, 1.4, lo, hi)
        self.assertEqual(pts[0], (100.0, 1.2))
        self.assertEqual(pts[-1], (140.0, 1.4))
        self.assertLessEqual(width, MAX_WIDTH)
        self.assertEqual(retrace_count(pts), 0)
        # Three distinct long centerlines, not one shrunk bead.
        ys = {round(y, 3) for _x, y in pts[1:-1]}
        self.assertGreaterEqual(len(ys), 3)

    def test_added_plastic_is_the_open_slot(self) -> None:
        bottom = (-0.5, 1.0)
        top = (2.0 + 0.5, 1.0)
        gap = (1.0, 1.0)
        beads = fit_skin_beads(bottom, gap, top)
        added = sum(width for _y, width in beads) - 1.0
        air = 2.0 - 1.0
        self.assertLessEqual(added, air * 1.15 + 0.05)
        self.assertGreater(added, 0.0)

    def test_strict_crossings_do_not_rise_on_the_bonded_layer(self) -> None:
        """Inner and outer, 0.05 mm clearance, per layer, against the unbonded path.

        Live's file totals are the slice ceiling. This layer is one column of
        that comparison: the zig-zag is extrusion, so it adds no travel.
        """
        walls = "\n".join([
            ";LAYER_CHANGE",
            ";Z:26.672",
            ";TYPE:Outer wall",
            "G1 X90 Y0 F1560",
            "G1 X150 Y0 E1.0",
            "G1 X150 Y4 E0.2",
            "G1 X90 Y4 E1.0",
            "G1 X90 Y0 E0.2",
            ";TYPE:Inner wall",
            "G1 X92 Y0.9 F1560",
            "G1 X148 Y0.9 E1.0",
            "G1 X148 Y3.1 E0.1",
            "G1 X92 Y3.1 E1.0",
            "G1 X92 Y0.9 E0.1",
        ])
        unbonded = walls + "\n;TYPE:Gap infill\nG1 X100 Y1.5 F1560\nG1 X140 Y1.5 E0.4\n"
        pts, width = bond_path(100.0, 140.0, 1.5, 1.5, 0.9, 3.1)
        moves = [";TYPE:Gap infill", "G1 X100 Y1.5 F1560"]
        for x, y in pts[1:]:
            moves.append(f"G1 X{x:.3f} Y{y:.3f} E0.05")
        bonded = walls + "\n" + "\n".join(moves) + "\n"
        before = strict_crossings(unbonded)
        after = strict_crossings(bonded)
        self.assertEqual(set(before), set(after))
        for layer, counts in after.items():
            self.assertLessEqual(counts["inner"], before[layer]["inner"], layer)
            self.assertLessEqual(counts["outer"], before[layer]["outer"], layer)
            self.assertLessEqual(counts["inner"], LIVE_STRICT_INNER)
            self.assertLessEqual(counts["outer"], LIVE_STRICT_OUTER)
        self.assertLessEqual(width, MAX_WIDTH)
        # The detector does count a travel that crosses both walls.
        crossed = walls + "\n;TYPE:Gap infill\nG1 X120 Y-1 F12720\nG1 X120 Y5 F12720\n"
        hit = strict_crossings(crossed)
        self.assertGreaterEqual(hit[0]["outer"], 1)
        self.assertEqual(sum(c["inner"] + c["outer"] for c in after.values()), 0)

    def test_bond_is_one_multipath_in_the_slicer(self) -> None:
        src = (REPO / "src/libslic3r/PerimeterGenerator.cpp").read_text()
        self.assertIn("ExtrusionMultiPath bonded", src)
        self.assertIn("belt_through_strips", src)
        self.assertIn("append_uncovered_belt_pockets", src)


if __name__ == "__main__":
    unittest.main()
