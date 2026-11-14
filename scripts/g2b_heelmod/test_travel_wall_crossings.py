#!/usr/bin/env python3
"""Geometry checks for travels that cross an outer wall.

The gate is `assert_crossings_not_increased`: a later slice whose perimeter
crossing count is higher than the recorded baseline fails. The recorded
ApexCore Soft 20% counts live in RECORDED_SLICE and are filled from the
orthocad fixture run (main pre-stitch vs this branch).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE)]

from check_travel_wall_crossings import (  # noqa: E402
    SEAM_MM,
    analyze,
    assert_crossings_not_increased,
)

# OrthoCAD sample top, Soft 20% gyroid, final G-code, measured 2026-11-14.
# Perimeter crossings are travels whose XY segment crosses an outer or
# overhang wall and whose endpoints are more than 0.3 mm from that wall.
# The ceiling is main's pre-stitch final, region by region. A later slice
# fails when any region goes above that. The straight seam hop on the first
# stitch measured 267; the inside route brought this branch to 0.
MAIN_REGION_CROSSINGS = {
    "forefoot": 0,
    "arch": 0,
    "heel": 89,
    "other": 32,
}
RECORDED_SLICE = {
    "apexcore_main_perimeter": 121,
    "apexcore_main_regions": MAIN_REGION_CROSSINGS,
    "apexcore_pr29_perimeter": 0,
    "apexcore_pr29_regions": {
        "forefoot": 0,
        "arch": 0,
        "heel": 0,
        "other": 0,
    },
    "apexcore_pr29_chain_hops": 0,
    "apex_light_perimeter": 0,
}


def _square_wall() -> str:
    return "\n".join([
        "M83",
        "G90",
        ";LAYER_CHANGE",
        ";Z:10",
        ";TYPE:Outer wall",
        "G1 X0 Y0 F1560",
        "G1 X10 Y0 E0.2",
        "G1 X10 Y10 E0.2",
        "G1 X0 Y10 E0.2",
        "G1 X0 Y0 E0.2",
    ])


class TravelWallCrossings(unittest.TestCase):
    def test_a_travel_through_the_wall_counts_once(self) -> None:
        text = _square_wall() + "\n;TYPE:Sparse infill\nG1 X-1 Y5 F12720\nG1 X11 Y5 F12720\n"
        report = analyze(text)
        self.assertEqual(report["totals"]["perimeter_crossings"], 1)
        self.assertEqual(report["totals"]["outer_only_crossings"], 1)
        self.assertEqual(report["by_feature"].get("Sparse infill"), 1)
        self.assertEqual(report["regions"]["forefoot"]["perimeter_crossings"], 1)

    def test_seam_entry_within_0_3_mm_is_not_a_crossing(self) -> None:
        # From outside, through the bottom edge, ending 0.2 mm inside the wall.
        text = _square_wall() + "\n;TYPE:Outer wall\nG1 X5 Y-2 F12720\nG1 X5 Y0.2 F12720\n"
        report = analyze(text)
        self.assertEqual(report["totals"]["perimeter_crossings"], 0)
        self.assertGreaterEqual(report["totals"]["seam_approaches"], 1)

    def test_endpoint_just_outside_the_seam_band_counts(self) -> None:
        text = _square_wall() + "\n;TYPE:Custom\nG1 X5 Y-2 F12720\nG1 X5 Y0.4 F12720\n"
        report = analyze(text, seam_mm=SEAM_MM)
        self.assertEqual(report["totals"]["perimeter_crossings"], 1)
        self.assertEqual(report["totals"]["seam_approaches"], 0)

    def test_travel_inside_the_part_does_not_count(self) -> None:
        text = _square_wall() + "\n;TYPE:Sparse infill\nG1 X2 Y2 F12720\nG1 X8 Y2 F12720\n"
        report = analyze(text)
        self.assertEqual(report["totals"]["perimeter_crossings"], 0)
        self.assertEqual(report["totals"]["seam_approaches"], 0)

    def test_overhang_wall_is_part_of_the_perimeter(self) -> None:
        text = "\n".join([
            "M83",
            "G90",
            ";LAYER_CHANGE",
            ";Z:180",
            ";TYPE:Overhang wall",
            "G1 X0 Y0 F1560",
            "G1 X10 Y0 E0.2",
            ";TYPE:Custom",
            "G1 X5 Y-2 F12720",
            "G1 X5 Y2 F12720",
        ])
        report = analyze(text)
        self.assertEqual(report["totals"]["perimeter_crossings"], 1)
        self.assertEqual(report["totals"]["outer_only_crossings"], 0)
        self.assertEqual(report["regions"]["arch"]["perimeter_crossings"], 1)

    def test_surface_line_crossing_is_separate_from_the_wall(self) -> None:
        text = _square_wall() + "\n".join([
            "",
            ";TYPE:Internal solid infill",
            "G1 X2 Y4 F1560",
            "G1 X8 Y4 E0.2",
            ";TYPE:Custom",
            "G1 X2 Y6 F12720",
            "G1 X8 Y2 F12720",
        ])
        report = analyze(text)
        self.assertEqual(report["totals"]["perimeter_crossings"], 0)
        self.assertEqual(report["totals"]["surface_crossings"], 1)

    def test_chain_hop_and_gap_exit_are_marked(self) -> None:
        text = "\n".join([
            "M83",
            "G90",
            ";LAYER_CHANGE",
            ";Z:220",
            ";TYPE:Outer wall",
            "G1 X0 Y0 F1560",
            "G1 X10 Y0 E0.2",
            "G1 X10 Y10 E0.2",
            "G1 X0 Y10 E0.2",
            "G1 X0 Y0 E0.2",
            ";TYPE:Gap infill",
            "G1 X-2 Y5 F12720",
            "G1 X-2 Y4 E0.05",
            "M204 S10000",
            "G1 X12 Y4 F12720",
            "M204 S8000",
            "G1 X14 Y4 E0.05",
            "G1 X5 Y5 F12720",
            ";TYPE:Sparse infill",
            "G1 X14 Y-3 E0.01",
        ])
        report = analyze(text)
        self.assertGreaterEqual(report["totals"]["perimeter_crossings"], 1)
        self.assertGreaterEqual(report["totals"]["chain_hop_crossings"], 1)
        self.assertGreaterEqual(report["totals"]["gap_exit_crossings"], 1)
        self.assertEqual(report["regions"]["heel"]["chain_hop_crossings"], report["totals"]["chain_hop_crossings"])

    def test_chaining_beads_that_already_meet_inside_does_not_add_a_crossing(self) -> None:
        from slice_apexcore_g2b import chain_gap_fill_travels

        src = "\n".join([
            "M83",
            "G90",
            ";LAYER_CHANGE",
            ";Z:40",
            ";TYPE:Outer wall",
            "G1 X0 Y0 F1560",
            "G1 X30 Y0 E0.4",
            "G1 X30 Y20 E0.4",
            "G1 X0 Y20 E0.4",
            "G1 X0 Y0 E0.4",
            ";TYPE:Gap infill",
            "G1 X5 Y10 F12720",
            "G1 X15 Y10 E0.2",
            "G1 X15.1 Y10 F12720",
            "G1 X25 Y10 E0.2",
            "G1 X5 Y5 F12720",
            ";TYPE:Sparse infill",
            "G1 X6 Y5 E0.05",
            "",
        ])
        before = analyze(src)["totals"]["perimeter_crossings"]
        after = analyze(chain_gap_fill_travels(src))["totals"]["perimeter_crossings"]
        self.assertEqual(before, 0)
        self.assertLessEqual(after, before)
        self.assertNotIn("X15.1", chain_gap_fill_travels(src))

    def test_gate_rejects_an_increase(self) -> None:
        baseline = {"totals": {"perimeter_crossings": 2}}
        current = {"totals": {"perimeter_crossings": 3}}
        with self.assertRaises(AssertionError):
            assert_crossings_not_increased(current, baseline)
        assert_crossings_not_increased({"totals": {"perimeter_crossings": 2}}, baseline)
        assert_crossings_not_increased({"perimeter_crossings": 1}, {"perimeter_crossings": 4})

    def test_chain_does_not_emit_a_hop_across_the_outer_wall(self) -> None:
        from slice_apexcore_g2b import chain_gap_fill_travels

        # The comb goes around the square. A straight hop from the left bead
        # to the right bead would cross the outer wall.
        src = "\n".join([
            "M83",
            "G90",
            ";LAYER_CHANGE",
            ";Z:40",
            ";TYPE:Outer wall",
            "G1 X0 Y0 F1560",
            "G1 X10 Y0 E0.2",
            "G1 X10 Y10 E0.2",
            "G1 X0 Y10 E0.2",
            "G1 X0 Y0 E0.2",
            ";TYPE:Gap infill",
            "G1 X-2 Y5 F12720",
            "G1 X-2 Y6 E0.02",
            "M204 S10000",
            "G1 X-2 Y-1 F12720",
            "G1 X12 Y-1 F12720",
            "G1 X12 Y5 F12720",
            "M204 S8000",
            "G1 X12 Y6 E0.02",
            "G1 X14 Y6 F12720",
            "G1 X14 Y12 F12720",
            ";TYPE:Sparse infill",
            "G1 X14 Y13 E0.01",
            "",
        ])
        before = analyze(src)["totals"]
        out = chain_gap_fill_travels(src)
        after = analyze(out)["totals"]
        self.assertEqual(before["perimeter_crossings"], 0)
        self.assertLessEqual(after["perimeter_crossings"], before["perimeter_crossings"])
        self.assertEqual(after["chain_hop_crossings"], 0)
        self.assertNotIn("G1 X12.000 Y5.000 F12720", out)
        self.assertIn("G1 X12 Y-1 F12720", out)

    def test_recorded_stitch_stays_at_or_under_main_per_region(self) -> None:
        recorded = RECORDED_SLICE
        main_regions = recorded["apexcore_main_regions"]
        pr_regions = recorded["apexcore_pr29_regions"]
        self.assertEqual(sum(main_regions.values()), recorded["apexcore_main_perimeter"])
        self.assertEqual(recorded["apexcore_main_perimeter"], 121)
        self.assertEqual(recorded["apex_light_perimeter"], 0)
        self.assertEqual(recorded["apexcore_pr29_chain_hops"], 0)
        for name, cap in main_regions.items():
            self.assertLessEqual(pr_regions[name], cap, name)
        self.assertLessEqual(sum(pr_regions.values()), recorded["apexcore_main_perimeter"])
        baseline = {"totals": {"perimeter_crossings": recorded["apexcore_main_perimeter"]}}
        assert_crossings_not_increased(
            {"totals": {"perimeter_crossings": recorded["apexcore_pr29_perimeter"]}},
            baseline,
        )
        with self.assertRaises(AssertionError):
            assert_crossings_not_increased(
                {"totals": {"perimeter_crossings": recorded["apexcore_main_perimeter"] + 1}},
                baseline,
            )

    def test_seam_routes_around_a_wall_and_keeps_the_landing(self) -> None:
        from postprocess_g2b import seam

        # The open wall ends on the left. Sparse infill starts on the right.
        # A straight hop crosses the closed outer wall between them.
        src = "\n".join([
            "M83",
            "G90",
            ";LAYER_CHANGE",
            ";Z:40",
            "G1 X8 Y0 F1560",
            ";TYPE:Outer wall",
            "G1 X14 Y0 E0.2",
            "G1 X14 Y12 E0.2",
            "G1 X8 Y12 E0.2",
            "G1 X8 Y0 E0.2",
            "G1 X1 Y2 F12720",
            ";TYPE:Outer wall",
            "G1 X1 Y10 E0.3",
            "G1 X20 Y10 F12720",
            ";TYPE:Sparse infill",
            "G1 X21 Y10 E0.05",
            "",
        ])
        out, _counts = seam(src, retract_min_mm=2.0, skip_infill_travel=True, g2b_anchor=True)
        self.assertIn(";C seam travel: inside", out)
        self.assertNotIn("G1 X20.000 Y10.400", out)
        report = analyze(out)
        self.assertEqual(report["totals"]["perimeter_crossings"], 0)
        self.assertIn("G1 X21 Y10 E0.05", out)


class PullbackSeamChordTest(unittest.TestCase):
    """A long infill seam keeps its retract when the hop is split along the wall."""

    def _run(self, text: str) -> str:
        import subprocess
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "in.gcode"
            dst = Path(tmp) / "out.gcode"
            src.write_text(text)
            subprocess.run(
                [sys.executable, str(HERE / "pullback_v2.py"), str(src), str(dst)],
                check=True,
                capture_output=True,
                text=True,
            )
            return dst.read_text()

    def test_inside_route_keeps_the_chord_retract(self) -> None:
        # Chord is 30 mm. The along-wall steps are short, so a per-segment
        # test would drop the retract the single hop used to get.
        src = "\n".join([
            "G1 E-0.5 F1800",
            ";LAYER_CHANGE",
            ";TYPE:Sparse infill",
            "G1 X0 Y0 E0.1",
            ";C seam travel: inside",
            "G1 X10 Y0 F12720 ;seam inside",
            "G1 X20 Y0 F12720 ;seam inside",
            "G1 X30 Y0 F12720 ;seam travel",
            ";TYPE:Sparse infill",
            "G1 X31 Y0 E0.1",
            "",
        ])
        out = self._run(src)
        self.assertEqual(out.count("G1 E-0.5 F1800"), 2)
        self.assertEqual(out.count("G1 E0.45"), 1)
        self.assertLess(out.index("G1 E-0.5 F1800", 1), out.index(";seam inside"))

    def test_short_chord_does_not_gain_a_retract(self) -> None:
        src = "\n".join([
            "G1 E-0.5 F1800",
            ";LAYER_CHANGE",
            ";TYPE:Sparse infill",
            "G1 X0 Y0 E0.1",
            ";C seam travel: inside",
            "G1 X0 Y10 F12720 ;seam inside",
            "G1 X0 Y20 F12720 ;seam inside",
            "G1 X3 Y0 F12720 ;seam travel",
            ";TYPE:Sparse infill",
            "G1 X4 Y0 E0.1",
            "",
        ])
        out = self._run(src)
        self.assertEqual(out.count("G1 E-0.5 F1800"), 1)
        self.assertNotIn("G1 E0.45", out)

    def test_direct_hop_still_retracts(self) -> None:
        src = "\n".join([
            "G1 E-0.5 F1800",
            ";LAYER_CHANGE",
            ";TYPE:Sparse infill",
            "G1 X0 Y0 E0.1",
            ";C seam travel: lift + direct",
            "G1 Y0.4 F12720 ;lift",
            "G1 X30 Y0.4 F12720 ;seam travel",
            ";TYPE:Sparse infill",
            "G1 X31 Y0 E0.1",
            "",
        ])
        out = self._run(src)
        self.assertEqual(out.count("G1 E-0.5 F1800"), 2)
        self.assertEqual(out.count("G1 E0.45"), 1)


if __name__ == "__main__":
    unittest.main()
