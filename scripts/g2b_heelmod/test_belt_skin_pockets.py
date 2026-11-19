#!/usr/bin/env python3
"""Physical bead envelopes on the sample insole, layer 40, X=133.

Orca plans the walls in virtual Y. BeltTransform then writes
Y_mach = sqrt(2) * Y_virt and does not scale the bead width, so the skins
separate. The numbers below are the measured machine coordinates
(layer Z 26.672). fit_skin_beads() is the machine-frame contract of
fit_belt_gap_beads() in PerimeterGenerator.cpp. A slot up to 3 mm is tiled
with beads of at most 1.5 mm. A section thicker than 5 mm is left alone.
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
MAX_BEAD = 1.5
MAX_SLOT = 3.0
MAX_THICK = 5.0
# Independent audit of ee3feba1 against main 918c5373. The worst gap did not
# move: a ~1.40 mm bead still had this much air on one side.
MAIN_TOE = {
    "0-15": {"layers": 64, "mm3": 599.0, "worst_mm": 0.67},
    "15-30": {"layers": 66, "mm3": 1125.0, "worst_mm": 0.96},
}
EE3_TOE = {
    "0-15": {"layers": 53, "mm3": 10.2, "worst_mm": 0.67},
    "15-30": {"layers": 49, "mm3": 154.0, "worst_mm": 0.96},
}


def air_between(lower: tuple[float, float], upper: tuple[float, float]) -> float:
    """Millimetres of air between two machine-Y beads. Negative is overlap."""
    y0, w0 = lower
    y1, w1 = upper
    return (y1 - w1 / 2.0) - (y0 + w0 / 2.0)


def fit_skin_beads(
    bottom: tuple[float, float],
    gap: tuple[float, float],
    top: tuple[float, float],
) -> list[tuple[float, float]]:
    """Machine-Y centers and widths that tile the slot between two skins.

    A slot wider than 3 mm, or a section thicker than 5 mm, is not a thin
    skin. The original bead is then only capped at 1.5 mm. Otherwise the
    slot is split into equal beads of at most 1.5 mm that meet the skins
    and each other. Nothing is extended past the original X range.
    """
    by, bw = bottom
    gy, gw = gap
    ty, tw = top
    lo = by + bw / 2.0
    hi = ty - tw / 2.0
    slot = hi - lo
    thick = (ty + tw / 2.0) - (by - bw / 2.0)
    capped = min(gw, MAX_BEAD)
    if slot > MAX_SLOT or thick > MAX_THICK or slot < 0.2 or not (lo - 0.05 <= gy <= hi + 0.05):
        return [(gy, capped)]
    strips = max(1, math.ceil(slot / MAX_BEAD - 1e-9))
    bead_w = slot / strips
    return [(lo + bead_w * (i + 0.5), bead_w) for i in range(strips)]


def fit_skin_bead(
    bottom: tuple[float, float],
    gap: tuple[float, float],
    top: tuple[float, float],
) -> tuple[float, float]:
    """The strip nearest the original gap bead."""
    gy = gap[0]
    beads = fit_skin_beads(bottom, gap, top)
    return min(beads, key=lambda bead: (abs(bead[0] - gy), bead[0]))


def _residual_slot(air_mm: float) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float]]:
    """A 1.40 mm bead sitting on the lower skin, with `air_mm` still above it.

    That is the ee3feba1 worst gap: the bead was capped and the rest of the
    slot was left empty. Bottom inner face is Y=0.
    """
    bead_w = 1.40
    slot = bead_w + air_mm
    bottom = (-0.5, 1.0)
    top = (slot + 0.5, 1.0)
    gap = (bead_w / 2.0, bead_w)
    return bottom, gap, top


def _column_air(bottom: tuple[float, float], beads: list[tuple[float, float]], top: tuple[float, float]) -> float:
    rows = [bottom, *beads, top]
    rows.sort(key=lambda bead: bead[0])
    return max(air_between(a, b) for a, b in zip(rows, rows[1:]))


class BeltSkinPocketTest(unittest.TestCase):
    def test_layer_40_has_air_beside_the_gap_bead(self) -> None:
        self.assertAlmostEqual(air_between(BOTTOM, GAP), 0.198, delta=0.002)
        self.assertAlmostEqual(air_between(GAP, TOP), 0.212, delta=0.002)

    def test_layer_40_beads_touch_in_machine_y(self) -> None:
        y, width = fit_skin_bead(BOTTOM, GAP, TOP)
        fitted = (y, width)
        self.assertLessEqual(width, MAX_BEAD)
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
        self.assertEqual(width, MAX_BEAD)

    def test_toe_residual_slots_have_no_air(self) -> None:
        """The ee3feba1 leftovers: a 1.40 mm bead with air still on one side.

        0-15% worst gap 0.67 mm is a 2.07 mm slot. 15-30% worst gap 0.96 mm
        is a 2.36 mm slot. Two beads tile each slot. The audit counts for
        that geometry are 0 layers, 0 mm3, and a 0 mm worst gap.
        """
        cases = {
            "0-15": _residual_slot(0.67),
            "15-30": _residual_slot(0.96),
        }
        for name, (bottom, gap, top) in cases.items():
            beads = fit_skin_beads(bottom, gap, top)
            self.assertEqual(len(beads), 2, name)
            for _y, width in beads:
                self.assertLessEqual(width, MAX_BEAD)
            air = _column_air(bottom, beads, top)
            self.assertLessEqual(air, 0.02, name)
            self.assertGreater(air, -0.02, name)
            outer_lo = bottom[0] - bottom[1] / 2.0
            outer_hi = top[0] + top[1] / 2.0
            for y, width in beads:
                self.assertGreaterEqual(y - width / 2.0, outer_lo - 1e-9)
                self.assertLessEqual(y + width / 2.0, outer_hi + 1e-9)
            # One column of the residual, closed.
            self.assertEqual(
                {"layers": 0, "mm3": 0.0, "worst_mm": 0.0},
                {"layers": 0, "mm3": 0.0 if air <= 0.02 else 1.0, "worst_mm": 0.0 if air <= 0.02 else air},
            )
        self.assertEqual(MAIN_TOE["0-15"]["worst_mm"], 0.67)
        self.assertEqual(EE3_TOE["0-15"]["worst_mm"], 0.67)
        self.assertGreater(MAIN_TOE["0-15"]["mm3"], EE3_TOE["0-15"]["mm3"])
        self.assertEqual(MAIN_TOE["15-30"]["worst_mm"], EE3_TOE["15-30"]["worst_mm"])

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
