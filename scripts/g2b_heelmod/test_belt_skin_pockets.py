#!/usr/bin/env python3
"""Physical bead envelopes on the sample insole, layer 40, X=133.

Orca plans the walls in virtual Y. BeltTransform then writes
Y_mach = sqrt(2) * Y_virt and does not scale the bead width, so the skins
separate. The numbers below are the measured machine coordinates
(layer Z 26.672). fit_skin_bead() is the machine-frame contract of
fit_belt_gap_beads() in PerimeterGenerator.cpp: the gap bead moves to the
slot center and its width becomes the slot, and it stays at or under 1.5 mm.
"""
from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main()
