#!/usr/bin/env python3
"""Physical bead envelopes on the sample insole.

Orca plans the walls in virtual Y. BeltTransform then writes
Y_mach = sqrt(2) * Y_virt and does not scale the bead width, so the skins
separate. fit_skin_bead() is the machine-frame contract of the one-bead fit:
the existing gap bead moves to the slot center and its width becomes the
slot, capped at 1.5 mm. close_classic_skins() and arachne_bead() are the
leftover contracts: the same beads get wider, no bead is added, and the
outer edge of a skin wall stays put. Nothing is wider than 1.6 mm.
"""
from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE)]

from slice_apexcore_g2b import chain_gap_fill_travels  # noqa: E402

# Sample insole, layer ~40, machine Z 26.672, X=133.
BOTTOM = (0.0279, 1.0)
GAP = (0.8759, 0.301)
TOP = (1.7386, 1.0)
MAX_WIDTH = 1.5
MAX_THICK = 4.0


def air_between(lower: tuple[float, float], upper: tuple[float, float]) -> float:
    """Millimetres of air between two machine-Y beads. Negative is overlap."""
    y0, w0 = lower
    y1, w1 = upper
    return (y1 - w1 / 2.0) - (y0 + w0 / 2.0)


def fit_skin_bead(
    bottom: tuple[float, float],
    gap: tuple[float, float],
    top: tuple[float, float],
) -> tuple[float, float]:
    """Return the gap bead's machine Y and width so it touches both walls.

    A slot wider than 1.5 mm, or a section thicker than 4 mm, is not a thin
    skin. The width is still capped at 1.5 mm. The centerline is not extended
    and nothing is retraced.
    """
    by, bw = bottom
    gy, gw = gap
    ty, tw = top
    lo = by + bw / 2.0
    hi = ty - tw / 2.0
    slot = hi - lo
    thick = (ty + tw / 2.0) - (by - bw / 2.0)
    capped = min(gw, MAX_WIDTH)
    if slot > MAX_WIDTH or thick > MAX_THICK or slot < 0.2:
        return gy, capped
    mid = (lo + hi) / 2.0
    if gw + 0.02 >= slot and abs(mid - gy) <= 0.02:
        return gy, capped
    return mid, min(slot, MAX_WIDTH)


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
        # A gyroid core is thicker than 4 mm. The gap bead stays put.
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


CAP = 1.6
# Cura widens the two sides of one wall up to about this section thickness.
ARACHNE_THICK = 2.85
SCALE = math.sqrt(2)


def _outer_edge(center: float, width: float, sign: float) -> float:
    """sign +1 is the upper edge, -1 the lower edge."""
    return center + sign * width / 2.0


def close_classic_skins(
    bottom: tuple[float, float],
    gap: tuple[float, float] | None,
    top: tuple[float, float],
    *,
    thick_limit: float = MAX_THICK,
) -> tuple[tuple[float, float], tuple[float, float] | None, tuple[float, float]]:
    """Widen the beads already in a thin classic slot. No new bead.

    No gap bead: each wall grows by slot/2 and its centre moves inward by
    slot/4. A slot the 1.5 mm fit refused: the gap bead takes a third of the
    uncovered width and each wall takes a third. A bead already covering the
    slot, or a section thicker than 4 mm, is left alone.
    """
    by, bw = bottom
    ty, tw = top
    slot = (ty - tw / 2.0) - (by + bw / 2.0)
    thick = (ty + tw / 2.0) - (by - bw / 2.0)
    if slot < 0.05 or thick > thick_limit:
        return bottom, gap, top
    gap_w = 0.0 if gap is None else gap[1]
    if gap is not None and slot <= MAX_WIDTH:
        # The one-bead fit owns this column.
        return bottom, gap, top
    if gap_w + 0.02 >= slot:
        return bottom, gap, top
    if gap is None or gap_w <= 0.05:
        add = min(slot / 2.0, CAP - bw, CAP - tw)
    else:
        share = (slot - gap_w) / 3.0
        add = min(share, CAP - bw, CAP - tw)
        gap_w = min(CAP, gap_w + share)
    add = max(0.0, add)
    if add < 0.01:
        return bottom, gap, top
    new_bottom = (by + add / 2.0, bw + add)
    new_top = (ty - add / 2.0, tw + add)
    new_gap = None if gap is None else (gap[0], gap_w)
    return new_bottom, new_gap, new_top


def arachne_bead(
    center: float,
    width: float,
    nx: float,
    ny: float,
    *,
    inward: float,
    slot: float,
    thick: float,
    outer: bool,
) -> tuple[float, float]:
    """Machine centre and width after the belt stretch.

    inward is +1 when the opposite skin is at higher Y. The outer wall moves
    by half the added width; an inner bead stays on its centreline.
    """
    factor = math.sqrt(nx * nx + 2.0 * ny * ny)
    if factor < 1.02 or slot <= 0.02 or thick > ARACHNE_THICK or width >= CAP:
        return center, width
    grown = min(CAP, width * factor)
    if grown < width + 0.01:
        return center, width
    extra = grown - width
    if not outer:
        return center, grown
    return center + inward * extra / 2.0, grown


def _bonded(bottom: tuple[float, float], middles: list[tuple[float, float]], top: tuple[float, float]) -> bool:
    beads = [bottom, *middles, top]
    beads.sort(key=lambda b: b[0])
    for lo, hi in zip(beads, beads[1:]):
        if air_between(lo, hi) > 0.02:
            return False
    return True


class BeltSkinLeftoverTest(unittest.TestCase):
    """Both materials. The raster is the toe column repeated every 0.2 mm."""

    def test_no_bead_slot_closes_and_keeps_the_outer_edge(self) -> None:
        # L61 X=99.25: walls 1.706 apart, 1.04 wide, 0.67 mm of air, no gap bead.
        for material in ("apex_core", "apex_light"):
            with self.subTest(material=material):
                bottom = (0.0, 1.04)
                top = (1.706, 1.04)
                lo = _outer_edge(bottom[0], bottom[1], -1)
                hi = _outer_edge(top[0], top[1], +1)
                if material == "apex_core":
                    nb, gap, nt = close_classic_skins(bottom, None, top)
                    self.assertIsNone(gap)
                else:
                    nb = arachne_bead(bottom[0], bottom[1], 0.0, 1.0, inward=1, slot=0.67, thick=2.75, outer=True)
                    nt = arachne_bead(top[0], top[1], 0.0, -1.0, inward=-1, slot=0.67, thick=2.75, outer=True)
                self.assertLessEqual(nb[1], CAP)
                self.assertLessEqual(nt[1], CAP)
                self.assertAlmostEqual(_outer_edge(nb[0], nb[1], -1), lo, delta=0.02)
                self.assertAlmostEqual(_outer_edge(nt[0], nt[1], +1), hi, delta=0.02)
                self.assertTrue(_bonded(nb, [], nt))

    def test_wide_slot_splits_across_the_existing_beads(self) -> None:
        # 15-30% band: the 1.5 mm fit refuses the slot. Three beads, none new.
        bottom = (0.0, 1.0)
        gap = (1.4, 0.9)
        top = (2.8, 1.0)
        lo = _outer_edge(bottom[0], bottom[1], -1)
        hi = _outer_edge(top[0], top[1], +1)
        nb, ng, nt = close_classic_skins(bottom, gap, top)
        self.assertIsNotNone(ng)
        for bead in (nb, ng, nt):
            self.assertLessEqual(bead[1], CAP)
        self.assertAlmostEqual(_outer_edge(nb[0], nb[1], -1), lo, delta=0.02)
        self.assertAlmostEqual(_outer_edge(nt[0], nt[1], +1), hi, delta=0.02)
        self.assertTrue(_bonded(nb, [ng], nt))
        # The added width is the uncovered slot, not a second copy of it.
        added = (nb[1] - bottom[1]) + (ng[1] - gap[1]) + (nt[1] - top[1])
        uncovered = air_between(bottom, gap) + air_between(gap, top)
        self.assertAlmostEqual(added, uncovered, delta=0.05)

    def test_layer_40_fit_is_not_widened_again(self) -> None:
        fitted = fit_skin_bead(BOTTOM, GAP, TOP)
        nb, ng, nt = close_classic_skins(BOTTOM, fitted, TOP)
        self.assertEqual(nb, BOTTOM)
        self.assertEqual(nt, TOP)
        self.assertEqual(ng, fitted)

    def test_thick_body_is_not_a_skin(self) -> None:
        bottom = (0.0, 1.0)
        top = (8.0, 1.0)
        self.assertEqual(close_classic_skins(bottom, (4.0, 0.4), top), (bottom, (4.0, 0.4), top))
        self.assertEqual(
            arachne_bead(0.0, 1.0, 0.0, 1.0, inward=1, slot=6.0, thick=8.0, outer=True),
            (0.0, 1.0),
        )

    def test_raster_both_bands_have_no_air_column(self) -> None:
        # Every 0.2 mm along the toe. 0-15% has no gap bead. 15-30% is wider.
        # ApexLight's 15-30% column stays inside the 2.85 mm section Cura widens.
        columns = {
            ("apex_core", "0-15"): ((0.0, 1.04), None, (1.706, 1.04)),
            ("apex_core", "15-30"): ((0.0, 1.0), (1.4, 0.9), (2.8, 1.0)),
            ("apex_light", "0-15"): ((0.0, 1.04), None, (1.706, 1.04)),
            ("apex_light", "15-30"): ((0.0, 1.0), None, (1.80, 1.0)),
        }
        for (material, band), (bottom, gap, top) in columns.items():
            with self.subTest(material=material, band=band):
                for i in range(0, 100):
                    self.assertGreaterEqual(i * 0.2, 0.0)
                    if material == "apex_core":
                        nb, ng, nt = close_classic_skins(bottom, gap, top)
                        middles = [] if ng is None else [ng]
                    else:
                        slot = (top[0] - top[1] / 2.0) - (bottom[0] + bottom[1] / 2.0)
                        thick = (top[0] + top[1] / 2.0) - (bottom[0] - bottom[1] / 2.0)
                        nb = arachne_bead(bottom[0], bottom[1], 0.0, 1.0, inward=1, slot=slot, thick=thick, outer=True)
                        nt = arachne_bead(top[0], top[1], 0.0, -1.0, inward=-1, slot=slot, thick=thick, outer=True)
                        middles = []
                    self.assertTrue(_bonded(nb, middles, nt), (material, band, i * 0.2))
                    for bead in (nb, nt, *middles):
                        self.assertLessEqual(bead[1], CAP)

    def test_arachne_stretch_matches_the_normal(self) -> None:
        # Horizontal wall: factor √2. Vertical wall: factor 1, no move.
        horizontal = arachne_bead(0.0, 1.0, 0.0, 1.0, inward=1, slot=0.41, thick=2.4, outer=True)
        self.assertAlmostEqual(horizontal[1], math.sqrt(2), delta=0.001)
        self.assertAlmostEqual(_outer_edge(horizontal[0], horizontal[1], -1), _outer_edge(0.0, 1.0, -1), delta=0.02)
        vertical = arachne_bead(0.0, 1.0, 1.0, 0.0, inward=1, slot=0.41, thick=2.4, outer=True)
        self.assertEqual(vertical, (0.0, 1.0))
        capped = arachne_bead(0.0, 1.2, 0.0, 1.0, inward=1, slot=0.5, thick=2.5, outer=True)
        self.assertAlmostEqual(capped[1], CAP, delta=0.001)
        self.assertAlmostEqual(_outer_edge(capped[0], capped[1], -1), _outer_edge(0.0, 1.2, -1), delta=0.02)
        inner = arachne_bead(1.0, 1.0, 0.0, 1.0, inward=1, slot=0.4, thick=2.5, outer=False)
        self.assertEqual(inner[0], 1.0)
        self.assertGreater(inner[1], 1.0)

    def test_apex_light_min_bead_moves_the_two_to_three_switch(self) -> None:
        # wall_split_middle_threshold = 2 * min_bead / line_width - 1.
        # The third bead appears near (2 + threshold) line widths.
        threshold = 2.0 * 0.85 / 1.0 - 1.0
        switch = (2.0 + threshold) * 1.0
        self.assertAlmostEqual(switch, 2.70, delta=0.05)
        chain = (HERE.parents[1] / "server" / "chains" / "apex_light.py").read_text()
        self.assertIn('"min_bead_width": "85%"', chain)
        self.assertNotIn('"min_bead_width": "50%"', chain)
        cfg = __import__("json").loads((HERE / "g2b_config.json").read_text())
        self.assertEqual(cfg["min_bead_width"], "85%")
        locked = (HERE / "apexcore_95a_locked.json").read_text()
        self.assertIn('"value": "25%"', locked)
        self.assertNotIn("odd number", locked)


if __name__ == "__main__":
    unittest.main()
