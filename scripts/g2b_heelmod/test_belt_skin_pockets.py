#!/usr/bin/env python3
"""Physical bead envelopes on the sample insole.

Orca plans the walls in virtual Y. BeltTransform then writes
Y_mach = sqrt(2) * Y_virt and does not scale the bead width, so the skins
separate. close_inside() is the machine-frame contract: outer walls keep
their centre and their width, and an existing internal bead grows in place
up to 1.6 mm. A column with no internal bead is not given one.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path[:0] = [str(HERE), str(REPO / "validation")]

from belt_skin_gate import CAP, air_between, close_inside  # noqa: E402
from slice_apexcore_g2b import chain_gap_fill_travels  # noqa: E402

# Sample insole, layer ~40, machine Z 26.672, X=133.
BOTTOM = (0.0279, 1.0)
GAP = (0.8759, 0.301)
TOP = (1.7386, 1.0)


def fit_skin_bead(
    bottom: tuple[float, float],
    gap: tuple[float, float],
    top: tuple[float, float],
) -> tuple[float, float]:
    """Grown gap bead. The centre stays. Width does not pass 1.6 mm."""
    _b, middles, _t = close_inside(bottom, gap, top)
    return middles[0]


class BeltSkinPocketTest(unittest.TestCase):
    def test_layer_40_has_air_beside_the_gap_bead(self) -> None:
        self.assertAlmostEqual(air_between(BOTTOM, GAP), 0.198, delta=0.002)
        self.assertAlmostEqual(air_between(GAP, TOP), 0.212, delta=0.002)

    def test_layer_40_beads_touch_in_machine_y(self) -> None:
        y, width = fit_skin_bead(BOTTOM, GAP, TOP)
        fitted = (y, width)
        self.assertAlmostEqual(y, GAP[0], delta=0.001)
        self.assertAlmostEqual(width, 0.7254, delta=0.001)
        self.assertLessEqual(width, CAP)
        self.assertLessEqual(air_between(BOTTOM, fitted), 0.02)
        self.assertLessEqual(air_between(fitted, TOP), 0.02)
        self.assertGreater(air_between(BOTTOM, fitted), -0.05)
        self.assertGreater(air_between(fitted, TOP), -0.05)

    def test_thick_section_is_not_a_skin_pocket(self) -> None:
        # A gyroid core is thicker than 4 mm. The gap bead stays put.
        y, width = fit_skin_bead((0.0, 1.0), (4.0, 0.40), (8.0, 1.0))
        self.assertEqual((y, width), (4.0, 0.40))

    def test_growth_stops_at_1_6_mm(self) -> None:
        # Slot 1.8 mm, gap 0.4 mm. Symmetric growth hits the cap and stops.
        y, width = fit_skin_bead((0.0, 1.0), (1.4, 0.4), (2.8, 1.0))
        self.assertAlmostEqual(y, 1.4, delta=0.001)
        self.assertAlmostEqual(width, CAP, delta=0.001)

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


class BeltSkinLeftoverTest(unittest.TestCase):
    """Both materials. The raster is the toe column repeated every 0.2 mm."""

    def test_no_bead_slot_is_not_given_a_new_path(self) -> None:
        # L61 X=99.25: walls 1.706 apart, 1.04 wide, 0.67 mm of air, no gap bead.
        # Closing it needs a new bead. A new bead is a new travel, so the air stays.
        bottom = (0.0, 1.04)
        top = (1.706, 1.04)
        for material in ("apex_core", "apex_light"):
            with self.subTest(material=material):
                nb, middles, nt = close_inside(bottom, None, top)
                self.assertEqual((nb, middles, nt), (bottom, [], top))
                self.assertAlmostEqual(air_between(nb, nt), 0.666, delta=0.001)

    def test_wide_slot_grows_the_gap_and_not_the_walls(self) -> None:
        bottom = (0.0, 1.0)
        gap = (1.4, 0.9)
        top = (2.8, 1.0)
        nb, middles, nt = close_inside(bottom, gap, top)
        self.assertEqual(nb, bottom)
        self.assertEqual(nt, top)
        self.assertEqual(len(middles), 1)
        self.assertAlmostEqual(middles[0][0], gap[0], delta=0.001)
        self.assertAlmostEqual(middles[0][1], CAP, delta=0.001)
        # The cap leaves 0.10 mm on each side. A second bead is not added.
        self.assertAlmostEqual(air_between(nb, middles[0]), 0.10, delta=0.02)
        self.assertAlmostEqual(air_between(middles[0], nt), 0.10, delta=0.02)

    def test_empty_slot_wider_than_the_cap_is_left_for_infill(self) -> None:
        bottom = (0.0, 1.0)
        top = (3.2, 1.0)
        nb, middles, nt = close_inside(bottom, None, top)
        self.assertEqual((nb, middles, nt), (bottom, [], top))

    def test_thick_body_is_not_a_skin(self) -> None:
        bottom = (0.0, 1.0)
        top = (8.0, 1.0)
        self.assertEqual(close_inside(bottom, (4.0, 0.4), top), (bottom, [(4.0, 0.4)], top))

    def test_raster_columns_do_not_add_a_bead(self) -> None:
        from belt_skin_gate import COLUMNS

        for (material, band), (bottom, gap, top) in COLUMNS.items():
            with self.subTest(material=material, band=band):
                nb, middles, nt = close_inside(bottom, gap, top)
                self.assertEqual(nb, bottom)
                self.assertEqual(nt, top)
                self.assertEqual(len(middles), 0 if gap is None else 1)
                for bead in (nb, nt, *middles):
                    self.assertLessEqual(bead[1], CAP)
                if gap is not None:
                    self.assertAlmostEqual(middles[0][0], gap[0], delta=0.001)

    def test_outer_walls_are_not_rewritten(self) -> None:
        src = (REPO / "src" / "libslic3r" / "PerimeterGenerator.cpp").read_text()
        self.assertIn("close_belt_from_inside", src)
        self.assertIn("close_arachne_belt_beads", src)
        self.assertIn("ThickPolylines skin_clip", src)
        self.assertIn("apply_uniform_inner_width", src)
        self.assertNotIn("widen_belt_skin_walls", src)
        self.assertNotIn("fit_belt_gap_beads", src)
        self.assertNotIn("widen_arachne_belt_beads", src)
        self.assertNotIn("belt_holes_to_polylines", src)
        self.assertNotIn("belt_extra", src)
        # Inset 0 is the outer wall. The closer does not write it.
        self.assertIn("line.inset_idx == 0", src)
        self.assertIn("if (!skin)", src)

    def test_apex_light_min_bead_stays_at_live(self) -> None:
        # 85% re-beads the outer wall and the skeletal pass exceeds the server cutoff.
        # wall_split_middle_threshold = 2 * min_bead / line_width - 1.
        threshold = 2.0 * 0.50 / 1.0 - 1.0
        self.assertAlmostEqual(threshold, 0.0, delta=0.001)
        chain = (REPO / "server" / "chains" / "apex_light.py").read_text()
        self.assertIn('"min_bead_width": "50%"', chain)
        self.assertNotIn('"min_bead_width": "85%"', chain)
        cfg = json.loads((HERE / "g2b_config.json").read_text())
        self.assertEqual(cfg["min_bead_width"], "50%")
        self.assertEqual(cfg["initial_layer_min_bead_width"], "85%")
        locked = (HERE / "apexcore_95a_locked.json").read_text()
        self.assertIn('"value": "25%"', locked)
        self.assertNotIn("odd number", locked)
        self.assertIn("The postprocessor widens that gap bead until it meets both skins.", locked)
        self.assertNotIn("fitted in the slicer", locked)


if __name__ == "__main__":
    unittest.main()
