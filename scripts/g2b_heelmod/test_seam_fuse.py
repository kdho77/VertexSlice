#!/usr/bin/env python3
"""ApexCore seam joint: belt-edge butt, full prime, slow first segment.

The closed outer ends on its own start. The prime equals the 0.5 mm retract.
The first extruding move is F1200 and the next move keeps the wall feed.
The 0.7 mm dry glide is the PRE-29 glide. A hop that would cross the wall
still lands inside, on that belt-edge start.
"""
from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE)]

from postprocess_g2b import (  # noqa: E402
    PRIME,
    RETRACT,
    SLOW_FIRST_F,
    WIPE_FRAC,
    WIPE_MM,
    _belt_split,
    seam,
)


def _rect() -> str:
    """Closed outer. Bottom edge y=0 from x=80 to x=160. Entered from outside."""
    return "\n".join([
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
        "",
    ])


def _words(line: str) -> dict[str, str]:
    import re
    return dict(re.findall(r"([XYZEF])([+-]?(?:\d+\.?\d*|\.\d+))", line.split(";", 1)[0]))


def _extrusions_after_seam(text: str) -> list[str]:
    body = text.split(";C_SEAM_START", 1)[1].split(";WIPE_START", 1)[0]
    return [ln for ln in body.splitlines() if " E" in ln and ln.startswith("G1")]


class SeamFuse(unittest.TestCase):
    def test_butt_full_prime_and_slow_first_segment(self) -> None:
        out, counts = seam(_rect(), retract_min_mm=2.0, skip_infill_travel=True, g2b_anchor=True)
        self.assertEqual(counts["overlap"], 0)
        self.assertNotIn(";seam overlap", out)
        self.assertEqual(PRIME, RETRACT)
        self.assertEqual(PRIME, 0.5)
        self.assertIn("G1 E-0.5 F1800", out)
        self.assertIn("G1 E0.50 F1800", out)
        self.assertNotIn("G1 E0.45", out)

        moves = _extrusions_after_seam(out)
        self.assertGreaterEqual(len(moves), 2)
        first, second = _words(moves[0]), _words(moves[1])
        # The seam travel lands on the midpoint. The first extrusion is the
        # bottom-edge half from that midpoint to the edge end (160, 0).
        self.assertIn("X120.000 Y0.000", out.split(";C_SEAM_START", 1)[0])
        self.assertAlmostEqual(float(first["X"]), 160.0, places=3)
        self.assertAlmostEqual(float(first["Y"]), 0.0, places=3)
        self.assertAlmostEqual(float(first["F"]), SLOW_FIRST_F, places=3)
        self.assertAlmostEqual(float(second["F"]), 1560.0, places=3)
        # The loop's last extrusion ends on the same point. Nothing is past it.
        last = _words(moves[-1])
        self.assertAlmostEqual(float(last["X"]), 120.0, places=3)
        self.assertAlmostEqual(float(last["Y"]), 0.0, places=3)

        glide = next(ln for ln in out.splitlines() if ";glide" in ln)
        gw = _words(glide)
        self.assertAlmostEqual(float(gw["X"]), 120.0 + WIPE_MM, places=3)
        self.assertAlmostEqual(float(gw["Y"]), 0.0, places=3)
        self.assertAlmostEqual(float(gw["F"]), 1560.0 * WIPE_FRAC, places=3)
        self.assertIn(";WIPE_START", out)
        self.assertIn(";WIPE_END", out)

    def test_short_bottom_edge_uses_the_vertex_fallback(self) -> None:
        # No segment lies entirely in the bottom band. The nearest vertex is (5, 0).
        segs = [
            {"x0": 5, "y0": 0, "x1": 20, "y1": 4, "e": 0.2, "f": "1560", "raw": "", "comments": []},
            {"x0": 20, "y0": 4, "x1": 0, "y1": 4, "e": 0.2, "f": "1560", "raw": "", "comments": []},
            {"x0": 0, "y0": 4, "x1": 5, "y1": 0, "e": 0.2, "f": "1560", "raw": "", "comments": []},
        ]
        rotated = _belt_split(segs)
        self.assertAlmostEqual(rotated[0]["x0"], 5.0, places=3)
        self.assertAlmostEqual(rotated[0]["y0"], 0.0, places=3)
        self.assertEqual(len(rotated), len(segs))

    def test_inside_travel_lands_on_the_belt_start(self) -> None:
        src = "\n".join([
            "M83",
            "G90",
            ";LAYER_CHANGE",
            ";Z:40",
            "G1 X1 Y6 F1560",
            ";TYPE:Outer wall",
            "G1 X8 Y0 F12720",
            "G1 X14 Y0 E0.2 F1560",
            "G1 X14 Y12 E0.2",
            "G1 X8 Y12 E0.2",
            "G1 X8 Y0 E0.2",
            "",
        ])
        out, _counts = seam(src, retract_min_mm=2.0, skip_infill_travel=True, g2b_anchor=True)
        self.assertIn(";C seam travel: inside", out)
        self.assertNotIn(";seam overlap", out)
        # Bottom edge is y=0 from x=8 to x=14. The inside path lands on (11, 0).
        self.assertIn("X11.000 Y0.000", out)
        landings = []
        for line in out.splitlines():
            if line.endswith(";seam travel") or " ;seam travel" in line:
                w = _words(line)
                if "X" in w and "Y" in w:
                    landings.append((float(w["X"]), float(w["Y"])))
        self.assertTrue(landings)
        self.assertAlmostEqual(landings[-1][0], 11.0, places=3)
        self.assertAlmostEqual(landings[-1][1], 0.0, places=3)


if __name__ == "__main__":
    unittest.main()
