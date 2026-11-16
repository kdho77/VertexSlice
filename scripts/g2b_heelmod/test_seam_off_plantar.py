#!/usr/bin/env python3
"""Closed outer seams leave the plantar face, and retract/prime does not go short."""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE)]

from check_travel_wall_crossings import analyze  # noqa: E402
from postprocess_g2b import (  # noqa: E402
    OVERLAP_MM,
    PRIME,
    RETRACT,
    _dorsal_split,
    seam,
)

WORD = re.compile(r"([XYZEF])([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)")


def _insole_section() -> str:
    """Thin closed loop: plantar edge y=0, dorsal edge y=2, entered from outside."""
    return "\n".join([
        "M83",
        "G90",
        ";LAYER_CHANGE",
        ";Z:40",
        "; reduce_crossing_wall = 1",
        "G1 X70 Y1 F1560",
        ";TYPE:Outer wall",
        "G1 X80 Y0 F12720",
        "G1 X160 Y0 E0.80 F1560",
        "G1 X160 Y2 E0.02",
        "G1 X80 Y2 E0.80",
        "G1 X80 Y0 E0.02",
        "",
    ])


def _flat_leader() -> str:
    """A tab whose whole loop sits in the belt band. No face away from the belt."""
    return "\n".join([
        "M83",
        "G90",
        ";LAYER_CHANGE",
        ";Z:1",
        "G1 X0 Y1 F1560",
        ";TYPE:Outer wall",
        "G1 X0 Y0.05 F12720",
        "G1 X30 Y0.05 E0.30 F1560",
        "G1 X30 Y0.20 E0.002",
        "G1 X0 Y0.20 E0.30",
        "G1 X0 Y0.05 E0.002",
        "",
    ])


def _square_hop() -> str:
    """The existing inside-wall fixture: open wall on the left, infill on the right."""
    return "\n".join([
        "M83",
        "G90",
        ";LAYER_CHANGE",
        ";Z:40",
        "; reduce_crossing_wall = 1",
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


def _words(line: str) -> dict[str, str]:
    return dict(WORD.findall(line.split(";", 1)[0]))


def _seam_starts(text: str) -> list[tuple[float, float]]:
    """XY where each `;C_SEAM_START` begins extruding."""
    x = y = None
    want = False
    found = []
    for line in text.splitlines():
        if line.startswith(";C_SEAM_START"):
            want = True
            continue
        words = _words(line)
        if not words and not line.split(";", 1)[0].strip().startswith(("G0", "G1")):
            continue
        body = line.split(";", 1)[0].strip()
        if not (body.startswith("G0") or body.startswith("G1")):
            continue
        if want and "E" in words and float(words["E"]) > 0 and ("X" in words or "Y" in words):
            found.append((x, y))
            want = False
        if "X" in words:
            x = float(words["X"])
        if "Y" in words:
            y = float(words["Y"])
    return found


def _retract_prime_sums(text: str) -> list[float]:
    """Net E of the E-only moves in each seam approach, up to the wall start."""
    lines = text.splitlines()
    sums = []
    i = 0
    while i < len(lines):
        if lines[i].startswith(";C seam travel"):
            total = 0.0
            saw = False
            i += 1
            while i < len(lines) and not lines[i].startswith(";C_SEAM_START"):
                if lines[i].startswith(";LAYER_CHANGE"):
                    break
                words = _words(lines[i])
                body = lines[i].split(";", 1)[0].strip()
                if body.startswith(("G0", "G1")) and "E" in words and "X" not in words and "Y" not in words:
                    total += float(words["E"])
                    saw = True
                i += 1
            if saw:
                sums.append(total)
            continue
        i += 1
    return sums


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


class SeamOffPlantar(unittest.TestCase):
    def test_insole_section_starts_on_the_dorsal_edge(self) -> None:
        out, counts = seam(_insole_section(), retract_min_mm=2.0, skip_infill_travel=True, g2b_anchor=True)
        starts = _seam_starts(out)
        self.assertEqual(len(starts), 1)
        x, y = starts[0]
        # Plantar edge is y=0. The start is the dorsal midpoint (120, 2).
        self.assertGreater(y, 0.25)
        self.assertAlmostEqual(x, 120.0, places=2)
        self.assertAlmostEqual(y, 2.0, places=2)
        self.assertIn(";C seam travel: inside", out)
        self.assertIn("X120.000 Y2.000", out)
        self.assertEqual(counts["overlap"], 1)
        self.assertIn(";seam overlap", out)
        self.assertIn(";glide", out)
        self.assertEqual(PRIME, RETRACT)
        self.assertEqual(OVERLAP_MM, 0.4)

    def test_tessellated_dorsal_edge_uses_the_run_midpoint(self) -> None:
        segs = [
            _seg(80, 0, 160, 0, 0.8),
            _seg(160, 0, 160, 2, 0.02),
            _seg(160, 2, 140, 2, 0.2),
            _seg(140, 2, 120, 2, 0.2),
            _seg(120, 2, 100, 2, 0.2),
            _seg(100, 2, 80, 2, 0.2),
            _seg(80, 2, 80, 0, 0.02),
        ]
        rotated = _dorsal_split(segs)
        self.assertAlmostEqual(rotated[0]["x0"], 120.0, places=3)
        self.assertAlmostEqual(rotated[0]["y0"], 2.0, places=3)
        self.assertGreater(rotated[0]["y0"], min(s["y0"] for s in segs) + 0.25)

    def test_short_crest_uses_the_dorsal_vertex(self) -> None:
        segs = [
            _seg(0, 0, 40, 0, 0.4),
            _seg(40, 0, 20, 8, 0.2),
            _seg(20, 8, 0, 0, 0.2),
        ]
        rotated = _dorsal_split(segs)
        self.assertAlmostEqual(rotated[0]["x0"], 20.0, places=3)
        self.assertAlmostEqual(rotated[0]["y0"], 8.0, places=3)
        self.assertGreater(rotated[0]["y0"], 0.25)

    def test_flat_leader_keeps_the_belt_start(self) -> None:
        out, _counts = seam(_flat_leader(), retract_min_mm=2.0, skip_infill_travel=True, g2b_anchor=True)
        starts = _seam_starts(out)
        self.assertEqual(len(starts), 1)
        x, y = starts[0]
        self.assertAlmostEqual(x, 15.0, places=2)
        self.assertAlmostEqual(y, 0.05, places=2)

    def test_retract_prime_is_not_short(self) -> None:
        out, _counts = seam(_insole_section(), retract_min_mm=2.0, skip_infill_travel=True, g2b_anchor=True)
        sums = _retract_prime_sums(out)
        self.assertTrue(sums)
        for net in sums:
            self.assertGreaterEqual(net, 0.0)
        self.assertIn("G1 E-0.5 F1800", out)
        self.assertIn("G1 E0.50 F1800", out)
        self.assertNotIn("G1 E0.45 F1800", out)

    def test_wall_crossings_stay_zero(self) -> None:
        for src in (_insole_section(), _square_hop(), _flat_leader()):
            out, _counts = seam(src, retract_min_mm=2.0, skip_infill_travel=True, g2b_anchor=True)
            report = analyze(out)
            self.assertEqual(report["totals"]["perimeter_crossings"], 0)
        insole, _counts = seam(_insole_section(), retract_min_mm=2.0, skip_infill_travel=True, g2b_anchor=True)
        self.assertEqual(analyze(insole)["reduce_crossing_wall"], "1")

    def test_inside_travel_ends_on_the_seam_point(self) -> None:
        out, _counts = seam(_insole_section(), retract_min_mm=2.0, skip_infill_travel=True, g2b_anchor=True)
        landings = []
        for line in out.splitlines():
            if line.endswith(";seam travel") or " ;seam travel" in line:
                words = _words(line)
                landings.append((float(words["X"]), float(words["Y"])))
        self.assertTrue(landings)
        start = _seam_starts(out)[0]
        self.assertAlmostEqual(landings[-1][0], start[0], places=3)
        self.assertAlmostEqual(landings[-1][1], start[1], places=3)

    def test_inner_wall_stays_on_the_belt_anchor(self) -> None:
        src = "\n".join([
            "M83",
            "G90",
            ";LAYER_CHANGE",
            ";Z:40",
            "G1 X70 Y1 F1560",
            ";TYPE:Outer wall",
            "G1 X80 Y0 F12720",
            "G1 X160 Y0 E0.80 F1560",
            "G1 X160 Y2 E0.02",
            "G1 X80 Y2 E0.80",
            "G1 X80 Y0 E0.02",
            "G1 X90 Y0.4 F12720",
            ";TYPE:Inner wall",
            "G1 X150 Y0.4 E0.60 F1560",
            "G1 X150 Y1.6 E0.012",
            "G1 X90 Y1.6 E0.60",
            "G1 X90 Y0.4 E0.012",
            "",
        ])
        out, _counts = seam(src, retract_min_mm=2.0, skip_infill_travel=True, g2b_anchor=True)
        starts = _seam_starts(out)
        self.assertEqual(len(starts), 2)
        self.assertAlmostEqual(starts[0][0], 120.0, places=2)
        self.assertAlmostEqual(starts[0][1], 2.0, places=2)
        self.assertAlmostEqual(starts[1][0], 120.0, places=2)
        self.assertAlmostEqual(starts[1][1], 0.4, places=2)
        self.assertEqual(analyze(out)["totals"]["perimeter_crossings"], 0)


if __name__ == "__main__":
    unittest.main()
