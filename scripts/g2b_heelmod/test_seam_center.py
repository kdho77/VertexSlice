#!/usr/bin/env python3
"""Closed ApexCore outers seam at the center of that layer's bottom face.

A curved plantar face is many near-equal bottom moves. The seam is the X
midpoint of that face, not the midpoint of whichever move is longest. A
single flat bottom move is unchanged: its midpoint is the face center. A
face narrower than 3 mm keeps the longest-move midpoint.
"""
from __future__ import annotations

import math
import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path[:0] = [str(HERE), str(REPO)]

from check_travel_wall_crossings import analyze  # noqa: E402
from postprocess_g2b import _belt_split, seam  # noqa: E402

CENTER_TOL_MM = 0.10


def _seg(x0: float, y0: float, x1: float, y1: float, e: float = 0.1) -> dict:
    return {
        "x0": x0,
        "y0": y0,
        "x1": x1,
        "y1": y1,
        "e": e,
        "f": "1560",
        "comments": [],
        "raw": "",
    }


def _curved_bottom() -> list[dict]:
    """Five near-equal plantar moves from x=0 to x=40. The longest is the last.

    Face center is x=20 on the third move, at (20, 0.02). The longest move's
    midpoint is (35.5, 0.15).
    """
    return [
        _seg(0, 0.20, 8, 0.05, 0.08),
        _seg(8, 0.05, 16, 0.00, 0.08),
        _seg(16, 0.00, 24, 0.04, 0.08),
        _seg(24, 0.04, 31, 0.10, 0.07),
        _seg(31, 0.10, 40, 0.20, 0.09),
        _seg(40, 0.20, 40, 8, 0.08),
        _seg(40, 8, 0, 8, 0.40),
        _seg(0, 8, 0, 0.20, 0.08),
    ]


def _flat_bottom() -> list[dict]:
    """One plantar move, x=80 to x=160 at y=0. Center and midpoint are both 120."""
    return [
        _seg(80, 0, 160, 0, 0.80),
        _seg(160, 0, 160, 2, 0.02),
        _seg(160, 2, 80, 2, 0.80),
        _seg(80, 2, 80, 0, 0.02),
    ]


def _narrow_bottom() -> list[dict]:
    """Face x=0..2.8, narrower than 3 mm. Longest move midpoint is (1.65, 0.05)."""
    return [
        _seg(0, 0.10, 0.5, 0.00, 0.01),
        _seg(0.5, 0.00, 2.8, 0.10, 0.03),
        _seg(2.8, 0.10, 2.8, 5, 0.05),
        _seg(2.8, 5, 0, 5, 0.03),
        _seg(0, 5, 0, 0.10, 0.05),
    ]


def _chain_length(segs: list[dict]) -> float:
    return sum(math.hypot(s["x1"] - s["x0"], s["y1"] - s["y0"]) for s in segs)


def _dist_to_segs(px: float, py: float, segs: list[dict]) -> float:
    best = float("inf")
    for s in segs:
        dx, dy = s["x1"] - s["x0"], s["y1"] - s["y0"]
        l2 = dx * dx + dy * dy
        if l2 <= 1e-18:
            best = min(best, math.hypot(px - s["x0"], py - s["y0"]))
            continue
        t = max(0.0, min(1.0, ((px - s["x0"]) * dx + (py - s["y0"]) * dy) / l2))
        qx, qy = s["x0"] + t * dx, s["y0"] + t * dy
        best = min(best, math.hypot(px - qx, py - qy))
    return best


def _gcode(loops: list[str]) -> str:
    lines = ["M83", "G90", "; reduce_crossing_wall = 1"]
    lines.extend(loops)
    return "\n".join(lines) + "\n"


def _outer(z: float, moves: list[str], approach: str) -> str:
    body = [f";LAYER_CHANGE", f";Z:{z}", approach, ";TYPE:Outer wall", *moves]
    return "\n".join(body)


def _seam_starts(text: str) -> list[tuple[float, float]]:
    x = y = None
    want = False
    found = []
    for line in text.splitlines():
        if line.startswith(";C_SEAM_START"):
            want = True
            continue
        body = line.split(";", 1)[0].strip()
        if not (body.startswith("G0") or body.startswith("G1")):
            continue
        words = dict(re.findall(r"([XYZEF])([+-]?(?:\d+\.?\d*|\.\d+))", body))
        if want and "E" in words and float(words["E"]) > 0 and ("X" in words or "Y" in words):
            found.append((x, y))
            want = False
        if "X" in words:
            x = float(words["X"])
        if "Y" in words:
            y = float(words["Y"])
    return found


class SeamCenter(unittest.TestCase):
    def test_curved_bottom_seams_at_the_face_center(self) -> None:
        segs = _curved_bottom()
        rotated = _belt_split(segs)
        # Longest plantar move is the last one; its midpoint is not the center.
        self.assertAlmostEqual(35.5, 31.0 + 0.5 * (40.0 - 31.0), places=3)
        self.assertGreater(abs(rotated[0]["x0"] - 35.5), 1.0)
        self.assertLessEqual(abs(rotated[0]["x0"] - 20.0), CENTER_TOL_MM)
        self.assertAlmostEqual(rotated[0]["x0"], 20.0, places=3)
        self.assertAlmostEqual(rotated[0]["y0"], 0.02, places=3)
        self.assertLessEqual(math.hypot(rotated[0]["x0"] - 20.0, rotated[0]["y0"] - 0.02), CENTER_TOL_MM)

    def test_flat_bottom_loop_is_unchanged(self) -> None:
        segs = _flat_bottom()
        rotated = _belt_split(segs)
        # One bottom move. Face center and the old midpoint are the same point.
        self.assertAlmostEqual(rotated[0]["x0"], 120.0, places=3)
        self.assertAlmostEqual(rotated[0]["y0"], 0.0, places=3)
        self.assertAlmostEqual(rotated[0]["x1"], 160.0, places=3)

    def test_narrow_face_keeps_the_longest_move_midpoint(self) -> None:
        segs = _narrow_bottom()
        rotated = _belt_split(segs)
        self.assertAlmostEqual(rotated[0]["x0"], 1.65, places=3)
        self.assertAlmostEqual(rotated[0]["y0"], 0.05, places=3)
        self.assertGreater(abs(rotated[0]["x0"] - 1.4), 0.10)
        self.assertEqual(len(rotated), len(segs) + 1)

    def test_split_stays_on_the_original_path(self) -> None:
        segs = _curved_bottom()
        rotated = _belt_split(segs)
        self.assertAlmostEqual(_chain_length(rotated), _chain_length(segs), places=6)
        self.assertAlmostEqual(sum(s["e"] for s in rotated), sum(s["e"] for s in segs), places=6)
        for s in rotated:
            self.assertLessEqual(_dist_to_segs(s["x0"], s["y0"], segs), 1e-6)
            self.assertLessEqual(_dist_to_segs(s["x1"], s["y1"], segs), 1e-6)
        # The new vertex is collinear with the move it split: the third bottom edge.
        self.assertLessEqual(_dist_to_segs(20.0, 0.02, segs[2:3]), 1e-6)

    def test_open_wall_is_not_rotated(self) -> None:
        segs = _curved_bottom()
        segs[-1] = _seg(0, 8, 6, 0.20, 0.08)
        self.assertIs(_belt_split(segs), segs)

    def test_seam_gcode_uses_each_layers_own_center(self) -> None:
        curved = _outer(
            40,
            [
                "G1 X0.000 Y0.200 F12720",
                "G1 X8.000 Y0.050 E0.080 F1560",
                "G1 X16.000 Y0.000 E0.080",
                "G1 X24.000 Y0.040 E0.080",
                "G1 X31.000 Y0.100 E0.070",
                "G1 X40.000 Y0.200 E0.090",
                "G1 X40.000 Y8.000 E0.080",
                "G1 X0.000 Y8.000 E0.400",
                "G1 X0.000 Y0.200 E0.080",
            ],
            "G1 X20 Y1 F1560",
        )
        shifted = _outer(
            40.3,
            [
                "G1 X10 Y0 F12720",
                "G1 X70 Y0 E0.60 F1560",
                "G1 X70 Y6 E0.06",
                "G1 X10 Y6 E0.60",
                "G1 X10 Y0 E0.06",
            ],
            "G1 X40 Y1 F1560",
        )
        out, _counts = seam(
            _gcode([curved, shifted]),
            retract_min_mm=2.0,
            skip_infill_travel=True,
            g2b_anchor=True,
        )
        starts = _seam_starts(out)
        self.assertEqual(len(starts), 2)
        self.assertLessEqual(abs(starts[0][0] - 20.0), CENTER_TOL_MM)
        self.assertAlmostEqual(starts[0][1], 0.02, places=3)
        self.assertLessEqual(abs(starts[1][0] - 40.0), CENTER_TOL_MM)
        self.assertAlmostEqual(starts[1][1], 0.0, places=3)
        self.assertGreater(abs(starts[0][0] - starts[1][0]), 10.0)
        report = analyze(out)
        self.assertEqual(report["totals"]["perimeter_crossings"], 0)
        self.assertEqual(report["totals"]["seam_block_crossings"], 0)

    def test_overhang_and_inner_follow_the_same_center(self) -> None:
        src = _gcode([
            "\n".join([
                ";LAYER_CHANGE",
                ";Z:40",
                "G1 X20 Y1 F1560",
                ";TYPE:Overhang wall",
                "G1 X0.000 Y0.200 F12720",
                "G1 X8.000 Y0.050 E0.080 F1560",
                "G1 X16.000 Y0.000 E0.080",
                "G1 X24.000 Y0.040 E0.080",
                "G1 X31.000 Y0.100 E0.070",
                "G1 X40.000 Y0.200 E0.090",
                "G1 X40.000 Y8.000 E0.080",
                "G1 X0.000 Y8.000 E0.400",
                "G1 X0.000 Y0.200 E0.080",
                "G1 X12 Y0.400 F12720",
                ";TYPE:Inner wall",
                "G1 X28 Y0.400 E0.16 F1560",
                "G1 X28 Y6.000 E0.06",
                "G1 X12 Y6.000 E0.16",
                "G1 X12 Y0.400 E0.06",
            ]),
        ])
        out, _counts = seam(src, retract_min_mm=2.0, skip_infill_travel=True, g2b_anchor=True)
        starts = _seam_starts(out)
        self.assertEqual(len(starts), 2)
        self.assertLessEqual(abs(starts[0][0] - 20.0), CENTER_TOL_MM)
        self.assertAlmostEqual(starts[0][1], 0.02, places=3)
        self.assertAlmostEqual(starts[1][0], 20.0, places=2)
        self.assertAlmostEqual(starts[1][1], 0.4, places=2)
        self.assertEqual(analyze(out)["totals"]["perimeter_crossings"], 0)

    def test_flat_gcode_start_matches_the_old_midpoint(self) -> None:
        src = _gcode([
            _outer(
                40,
                [
                    "G1 X80 Y0 F12720",
                    "G1 X160 Y0 E0.80 F1560",
                    "G1 X160 Y2 E0.02",
                    "G1 X80 Y2 E0.80",
                    "G1 X80 Y0 E0.02",
                ],
                "G1 X70 Y1 F1560",
            ),
        ])
        out, counts = seam(src, retract_min_mm=2.0, skip_infill_travel=True, g2b_anchor=True)
        start = _seam_starts(out)[0]
        self.assertAlmostEqual(start[0], 120.0, places=3)
        self.assertAlmostEqual(start[1], 0.0, places=3)
        self.assertEqual(counts["overlap"], 0)
        self.assertIn("G1 E-0.5 F1800", out)
        self.assertIn("G1 E0.50 F1800", out)
        self.assertEqual(analyze(out)["totals"]["perimeter_crossings"], 0)

    def test_first_xy_moves_to_the_face_center_without_a_wipe(self) -> None:
        """The first move in the file has no previous XY. It still rotates.

        This is the orthocad layer-1 outline: the face is 3.119 mm wide, so
        it is not the narrow fallback, and the slicer had started at the
        right-hand vertex. No retract and no wipe are added.
        """
        moves = [
            "G1 X133.083 Y0.351 E0.17199",
            "G1 X133.631 Y0.564 E0.09615",
            "G1 X134.232 Y1.058 E0.12703",
            "G1 X133.911 Y1.145 E0.05423",
            "G1 X132.787 Y1.302 E0.18548",
            "G1 X132.062 Y1.380 E0.11905",
            "G1 X131.374 Y1.435 E0.11655",
            "G1 X130.686 Y1.490 E0.12027",
            "G1 X130.655 Y1.492 E0.00540",
            "G1 X129.828 Y1.536 E0.14709",
            "G1 X129.002 Y1.580 E0.14216",
            "G1 X128.423 Y1.620 E0.09615",
            "G1 X127.366 Y1.681 E0.16811",
            "G1 X126.888 Y1.692 E0.07132",
            "G1 X126.411 Y1.704 E0.06748",
            "G1 X125.933 Y1.715 E0.06363",
            "G1 X125.468 Y1.722 E0.05819",
            "G1 X125.004 Y1.729 E0.05571",
            "G1 X124.564 Y1.736 E0.04957",
            "G1 X124.125 Y1.743 E0.04665",
            "G1 X123.544 Y1.666 E0.05837",
            "G1 X121.823 Y1.379 E0.16910",
            "G1 X123.538 Y1.034 E0.16945",
            "G1 X124.271 Y0.889 E0.07441",
            "G1 X124.522 Y0.859 E0.02655",
            "G1 X124.774 Y0.829 E0.02785",
            "G1 X125.344 Y0.760 E0.06779",
            "G1 X125.914 Y0.692 E0.07171",
            "G1 X126.442 Y0.616 E0.07109",
            "G1 X126.971 Y0.540 E0.07543",
            "G1 X127.500 Y0.465 E0.07978",
            "G1 X128.381 Y0.360 E0.14102",
            "G1 X128.385 Y0.359 E0.00061",
            "G1 X128.950 Y0.269 E0.09502",
            "G1 X129.681 Y0.191 E0.12423",
            "G1 X130.412 Y0.113 E0.12822",
            "G1 X131.241 Y0.091 E0.14446",
            "G1 X132.069 Y0.068 E0.13992",
        ]
        src = "\n".join([
            "M83",
            ";LAYER_CHANGE",
            "G1 X132.069 Y0.068 Z1.952 F12720",
            ";TYPE:Overhang wall",
            "G1 F600",
            *moves,
        ]) + "\n"
        out, counts = seam(src, retract_min_mm=2.0, skip_infill_travel=True, g2b_anchor=True)
        self.assertEqual(counts["retract"], 0)
        self.assertEqual(counts["prime"], 0)
        self.assertEqual(counts["wipe"], 0)
        self.assertNotIn(";WIPE_START", out)
        self.assertIn("G1 X130.510 Y0.110 Z1.952 F12720", out)
        # The right-hand vertex is still on the path. It is no longer the start.
        self.assertNotIn("G1 X132.069 Y0.068 Z1.952", out)
        x = y = None
        start = None
        for line in out.splitlines():
            body = line.split(";", 1)[0]
            words = dict(re.findall(r"([XYZEF])([+-]?(?:\d+\.?\d*|\.\d+))", body))
            if "E" in words and float(words["E"]) > 0 and ("X" in words or "Y" in words) and start is None:
                start = (x, y)
                break
            if "X" in words:
                x = float(words["X"])
            if "Y" in words:
                y = float(words["Y"])
        self.assertIsNotNone(start)
        self.assertLessEqual(abs(start[0] - 130.5095), CENTER_TOL_MM)
        self.assertLessEqual(math.hypot(start[0] - 130.5095, start[1] - 0.1104), CENTER_TOL_MM)

    def test_moved_seam_keeps_f1200_and_retract_on_the_old_seam(self) -> None:
        """The face center is not the longest-edge midpoint.

        F1200 stays on the old 10 mm, and the retract still keys off the
        distance to that old point. The approach here is under 2 mm from
        the new start and over 2 mm from the old one.
        """
        src = _gcode([
            _outer(
                40,
                [
                    "G1 X0.000 Y0.200 F12720",
                    "G1 X8.000 Y0.050 E0.080 F1560",
                    "G1 X16.000 Y0.000 E0.080",
                    "G1 X24.000 Y0.040 E0.080",
                    "G1 X31.000 Y0.100 E0.070",
                    "G1 X40.000 Y0.200 E0.090",
                    "G1 X40.000 Y8.000 E0.080",
                    "G1 X0.000 Y8.000 E0.400",
                    "G1 X0.000 Y0.200 E0.080",
                ],
                "G1 X20 Y1 F1560",
            ),
        ])
        out, counts = seam(src, retract_min_mm=2.0, skip_infill_travel=True, g2b_anchor=True)
        starts = _seam_starts(out)
        self.assertEqual(len(starts), 1)
        self.assertLessEqual(abs(starts[0][0] - 20.0), CENTER_TOL_MM)
        self.assertEqual(counts["retract"], 1)
        self.assertEqual(counts["prime"], 1)
        self.assertIn("G1 E-0.5 F1800", out)
        self.assertIn("G1 E0.50 F1800", out)
        # Walk feeds. The new start is outside the old 10 mm, so it stays
        # at the wall feed. The old midpoint (35.5, 0.15) stays at F1200.
        x, y = None, None
        feed = None
        saw_start = False
        start_feed = None
        old_feed = None
        for line in out.splitlines():
            if line.startswith(";C_SEAM_START"):
                saw_start = True
            body = line.split(";", 1)[0]
            words = dict(re.findall(r"([XYZEF])([+-]?(?:\d+\.?\d*|\.\d+))", body))
            if "F" in words:
                feed = float(words["F"])
            extruding = "E" in words and float(words["E"]) > 0 and ("X" in words or "Y" in words)
            if saw_start and extruding and start_feed is None:
                start_feed = feed
                saw_start = False
            if extruding and x is not None:
                nx = float(words["X"]) if "X" in words else x
                ny = float(words["Y"]) if "Y" in words else y
                if math.hypot(nx - 35.5, ny - 0.15) < 0.05:
                    old_feed = feed
            if "X" in words:
                x = float(words["X"])
            if "Y" in words:
                y = float(words["Y"])
        self.assertIsNotNone(start_feed)
        self.assertAlmostEqual(start_feed, 1560.0, places=3)
        self.assertIsNotNone(old_feed)
        self.assertLessEqual(old_feed, 1200.5)
        self.assertEqual(analyze(out)["totals"]["perimeter_crossings"], 0)


if __name__ == "__main__":
    unittest.main()
