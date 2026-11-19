#!/usr/bin/env python3
"""Surface quality of the pocket fill versus main 918c5373.

The sample is the insole section (edge), the layer-40 toe column, and the
heel nub. Main is that gcode run through the unstamped gap chain and the
same seam postprocessor 918c5373 ships. This branch stamps the gap feed.
The postprocess does not move outer walls, skins, seams, or retracts.
The slicer does not move or widen an outer wall. An internal bead grows
about its centre.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path[:0] = [str(HERE), str(REPO)]

import slice_apexcore_g2b as sag  # noqa: E402
from check_travel_wall_crossings import analyze  # noqa: E402
from postprocess_g2b import seam  # noqa: E402
from test_belt_skin_pockets import (  # noqa: E402
    BOTTOM,
    GAP,
    TOP,
    air_between,
    fit_skin_bead,
)
from test_seam_off_plantar import (  # noqa: E402
    _insole_section,
    _seam_starts,
    _short_nub_layer,
)

WORD = re.compile(r"([XYZEF])([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)")
VISIBLE = ("Outer wall", "Top surface", "Bottom surface", "Internal solid infill")
# Main 918c5373 already has the #32 stitch: zero perimeter crossings.
MAIN_CROSSINGS = {"forefoot": 0, "arch": 0, "heel": 0, "other": 0}


def _toe_layer() -> str:
    """Layer 40 of the sample, machine Z 26.672, with the measured gap."""
    return "\n".join([
        ";LAYER_CHANGE",
        ";Z:26.672",
        "; reduce_crossing_wall = 1",
        ";TYPE:Outer wall",
        "G1 X120 Y0.0279 F1560",
        "G1 X146 Y0.0279 E0.26 F1560",
        ";TYPE:Bottom surface",
        "G1 X120 Y0.0279 F1200",
        "G1 X146 Y0.0279 E0.20 F1200",
        ";TYPE:Top surface",
        "G1 X120 Y1.7386 F1200",
        "G1 X146 Y1.7386 E0.20 F1200",
        ";TYPE:Gap infill",
        "G1 X120 Y0.8759 F12720",
        "G1 X146 Y0.8759 E0.05",
        "G1 X160 Y0.8759 E0.03 F12720",
        ";TYPE:Outer wall",
        "G1 X120 Y1.7400 F1560",
        "G1 X120.400 Y1.7400 E0.004 F1560",
        "",
    ])


def _sample() -> str:
    return _insole_section() + "\n" + _toe_layer() + "\n" + _short_nub_layer()


def _main_chain(text: str) -> str:
    """chain_gap_fill_travels as 918c5373 left it, before the feed stamp."""
    real = sag._stamp_extrude_feed
    sag._stamp_extrude_feed = lambda line: line
    try:
        return sag.chain_gap_fill_travels(text)
    finally:
        sag._stamp_extrude_feed = real


def _finish(text: str) -> str:
    out, _counts = seam(text, retract_min_mm=2.0, skip_infill_travel=True, g2b_anchor=True)
    return out


def _visible_lines(text: str) -> list[str]:
    typ = ""
    rows = []
    for line in text.splitlines():
        if line.startswith(";TYPE:"):
            typ = line[6:].strip()
            continue
        if typ in VISIBLE:
            rows.append(line.strip())
    return rows


def _gap_feeds(text: str) -> list[str | None]:
    typ = ""
    feeds = []
    for line in text.splitlines():
        if line.startswith(";TYPE:"):
            typ = line[6:].strip()
            continue
        body = line.split(";", 1)[0]
        words = dict(WORD.findall(body))
        if typ == "Gap infill" and "E" in words and float(words["E"]) > 0:
            feeds.append(words.get("F"))
    return feeds


def _retracts(text: str) -> dict[str, int]:
    zone = "edge"
    counts = {"edge": 0, "heel": 0}
    for line in text.splitlines():
        if line.startswith(";Z:"):
            zone = "heel" if float(line.split(":", 1)[1]) >= 100.0 else "edge"
            continue
        body = line.split(";", 1)[0].strip()
        words = dict(WORD.findall(body))
        if (
            body.startswith(("G0", "G1"))
            and "E" in words
            and "X" not in words
            and "Y" not in words
            and float(words["E"]) < 0
        ):
            counts[zone] += 1
    return counts


def _tiny_outer_fragments(text: str) -> dict[str, int]:
    """Outer-wall extrusions of 1 mm or shorter. The heel nub is the baseline."""
    typ = ""
    zone = "edge"
    x = y = None
    counts = {"edge": 0, "heel": 0}
    for line in text.splitlines():
        if line.startswith(";Z:"):
            zone = "heel" if float(line.split(":", 1)[1]) >= 100.0 else "edge"
            continue
        if line.startswith(";TYPE:"):
            typ = line[6:].strip()
            continue
        body = line.split(";", 1)[0].strip()
        if not body.startswith(("G0", "G1")):
            continue
        words = dict(WORD.findall(body))
        nx, ny = x, y
        if "X" in words:
            nx = float(words["X"])
        if "Y" in words:
            ny = float(words["Y"])
        if typ == "Outer wall" and x is not None and "E" in words and float(words["E"]) > 0:
            length = ((nx - x) ** 2 + (ny - y) ** 2) ** 0.5
            if length <= 1.0:
                counts[zone] += 1
        x, y = nx, ny
    return counts


def _feeds_of(text: str, feature: str) -> set[str]:
    typ = ""
    found = set()
    for line in text.splitlines():
        if line.startswith(";TYPE:"):
            typ = line[6:].strip()
            continue
        if typ != feature:
            continue
        body = line.split(";", 1)[0]
        words = dict(WORD.findall(body))
        if "E" in words and float(words["E"]) > 0 and "F" in words:
            found.add(words["F"])
    return found


class SurfaceVersusMain(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        src = _sample()
        cls.main = _finish(_main_chain(src))
        cls.branch = _finish(sag.chain_gap_fill_travels(src))

    def test_outer_and_skin_paths_match_main(self) -> None:
        main_rows = _visible_lines(self.main)
        branch_rows = _visible_lines(self.branch)
        self.assertEqual(len(main_rows), 89)
        self.assertEqual(branch_rows, main_rows)
        self.assertEqual(_feeds_of(self.branch, "Outer wall"), {"1560", "1800"})
        self.assertEqual(_feeds_of(self.branch, "Top surface"), {"1200"})
        self.assertEqual(_feeds_of(self.branch, "Bottom surface"), {"1200"})
        self.assertEqual(_feeds_of(self.main, "Outer wall"), _feeds_of(self.branch, "Outer wall"))
        self.assertEqual(_feeds_of(self.main, "Top surface"), _feeds_of(self.branch, "Top surface"))
        self.assertEqual(_feeds_of(self.main, "Bottom surface"), _feeds_of(self.branch, "Bottom surface"))

    def test_seams_and_retracts_match_main(self) -> None:
        self.assertEqual(
            _seam_starts(self.branch),
            [(120.0, 2.0), (120.0, 0.028), (120.0, 0.876), (20.05, 3.94), (10.0, 4.0), (50.0, 4.0)],
        )
        self.assertEqual(_seam_starts(self.branch), _seam_starts(self.main))
        self.assertEqual(_retracts(self.main), {"edge": 3, "heel": 3})
        self.assertEqual(_retracts(self.branch), {"edge": 3, "heel": 3})

    def test_tiny_fragments_do_not_exceed_main(self) -> None:
        self.assertEqual(_tiny_outer_fragments(self.main), {"edge": 3, "heel": 7})
        self.assertEqual(_tiny_outer_fragments(self.branch), {"edge": 3, "heel": 7})

    def test_wall_crossings_stay_zero(self) -> None:
        for label, text in (("main", self.main), ("branch", self.branch)):
            report = analyze(text)
            self.assertEqual(report["reduce_crossing_wall"], "1", label)
            self.assertEqual(report["totals"]["perimeter_crossings"], 0, label)
            self.assertEqual(report["totals"]["gap_crossings"], 0, label)
            self.assertEqual(report["totals"]["seam_block_crossings"], 0, label)
        self.assertEqual(MAIN_CROSSINGS, {"forefoot": 0, "arch": 0, "heel": 0, "other": 0})

    def test_gap_feed_is_the_only_gcode_change(self) -> None:
        self.assertIn("12720", _gap_feeds(self.main))
        for feed in _gap_feeds(self.branch):
            self.assertNotEqual(feed, "12720")
            self.assertIn(feed, {"1560", "1800"})

    def test_layer_40_fill_stays_inside_the_skins(self) -> None:
        # Solid infill is clipped by the pre-fit gap, so the skin main prints
        # is not eaten. The gap bead stays on its centre and grows until the
        # skins touch. The outer faces are the original walls.
        printed = fit_skin_bead(BOTTOM, GAP, TOP)
        self.assertEqual(BOTTOM, (0.0279, 1.0))
        self.assertEqual(TOP, (1.7386, 1.0))
        self.assertEqual(GAP, (0.8759, 0.301))
        self.assertAlmostEqual(printed[0], 0.8759, delta=0.001)
        self.assertAlmostEqual(printed[1], 0.7254, delta=0.001)
        self.assertLessEqual(air_between(BOTTOM, printed), 0.02)
        self.assertLessEqual(air_between(printed, TOP), 0.02)
        outer_lo = BOTTOM[0] - BOTTOM[1] / 2.0
        outer_hi = TOP[0] + TOP[1] / 2.0
        self.assertGreaterEqual(printed[0] - printed[1] / 2.0, outer_lo)
        self.assertLessEqual(printed[0] + printed[1] / 2.0, outer_hi)
        # A thick midfoot or heel section is not a pocket. The bead stays.
        self.assertEqual(fit_skin_bead((0.0, 1.0), (4.0, 0.40), (8.0, 1.0)), (4.0, 0.40))

    def test_solid_clip_uses_the_pre_fit_gap(self) -> None:
        src = (REPO / "src/libslic3r/PerimeterGenerator.cpp").read_text()
        self.assertIn("ThickPolylines skin_clip", src)
        self.assertIn("variable_width(skin_clip, erGapFill", src)
        clip = src.split("const ExtrusionEntityCollection &clip", 1)[1]
        self.assertIn("clip.polygons_covered_by_width", clip)


if __name__ == "__main__":
    unittest.main()
