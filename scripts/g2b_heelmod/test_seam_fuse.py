#!/usr/bin/env python3
"""ApexCore seam joint: belt-edge butt, full prime, slow 10 mm ends.

The closed outer ends on its own start. The prime equals the 0.5 mm retract.
The first 10 mm after the seam and the last 10 mm into it are F1200; a feed
already at or below that stays. The 0.7 mm dry glide is the PRE-29 glide.
A hop that would cross the wall still lands inside, on that belt-edge start.
"""
from __future__ import annotations

import math
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path[:0] = [str(HERE), str(REPO / "validation" / "seam_fuse_gates")]

from postprocess_g2b import (  # noqa: E402
    FOREFOOT_FILAMENT_AREA,
    FOREFOOT_FLOW_CAP,
    PRIME,
    RETRACT,
    SLOW_END_F,
    SLOW_END_MM,
    WIPE_FRAC,
    WIPE_MM,
    _belt_split,
    bead_width,
    forefoot_cura_bond,
    forefoot_last_layer,
    seam,
)
import check_travel_wall_crossings as crossings  # noqa: E402
import g3_seam_joint as g3  # noqa: E402


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


def _short_loop() -> str:
    """Closed outer shorter than 20 mm. The whole loop is the slow zone."""
    return "\n".join([
        "M83",
        "G90",
        ";LAYER_CHANGE",
        ";Z:40",
        "G1 X0 Y1 F1560",
        ";TYPE:Outer wall",
        "G1 X0 Y0 F12720",
        "G1 X4 Y0 E0.04 F1560",
        "G1 X4 Y2 E0.02",
        "G1 X0 Y2 E0.04",
        "G1 X0 Y0 E0.02",
        "",
    ])


def _words(line: str) -> dict[str, str]:
    import re
    return dict(re.findall(r"([XYZEF])([+-]?(?:\d+\.?\d*|\.\d+))", line.split(";", 1)[0]))


def _extrusions_after_seam(text: str) -> list[str]:
    body = text.split(";C_SEAM_START", 1)[1].split(";WIPE_START", 1)[0]
    return [ln for ln in body.splitlines() if " E" in ln and ln.startswith("G1")]


class SeamFuse(unittest.TestCase):
    def test_butt_full_prime_and_slow_ten_mm_ends(self) -> None:
        out, counts = seam(_rect(), retract_min_mm=2.0, skip_infill_travel=True, g2b_anchor=True)
        self.assertEqual(counts["overlap"], 0)
        self.assertNotIn(";seam overlap", out)
        self.assertEqual(PRIME, RETRACT)
        self.assertEqual(PRIME, 0.5)
        self.assertIn("G1 E-0.5 F1800", out)
        self.assertIn("G1 E0.50 F1800", out)
        self.assertNotIn("G1 E0.45", out)

        moves = _extrusions_after_seam(out)
        self.assertGreaterEqual(len(moves), 4)
        # The seam travel lands on the midpoint. The first extrusion is the
        # first 10 mm of the bottom edge, not the whole edge.
        self.assertIn("X120.000 Y0.000", out.split(";C_SEAM_START", 1)[0])
        first, second = _words(moves[0]), _words(moves[1])
        self.assertAlmostEqual(float(first["X"]), 130.0, places=3)
        self.assertAlmostEqual(float(first["Y"]), 0.0, places=3)
        self.assertAlmostEqual(float(first["F"]), SLOW_END_F, places=3)
        self.assertAlmostEqual(float(first["E"]), 0.10, places=5)
        self.assertAlmostEqual(float(second["X"]), 160.0, places=3)
        self.assertAlmostEqual(float(second["F"]), 1560.0, places=3)
        self.assertAlmostEqual(float(second["E"]), 0.30, places=5)

        # Last 10 mm into the seam is slow. The move before it keeps the wall feed.
        last, before = _words(moves[-1]), _words(moves[-2])
        self.assertAlmostEqual(float(last["X"]), 120.0, places=3)
        self.assertAlmostEqual(float(last["Y"]), 0.0, places=3)
        self.assertAlmostEqual(float(last["F"]), SLOW_END_F, places=3)
        self.assertAlmostEqual(float(last["E"]), 0.10, places=5)
        self.assertAlmostEqual(float(before["X"]), 110.0, places=3)
        self.assertAlmostEqual(float(before["F"]), 1560.0, places=3)

        x, y = 120.0, 0.0
        acc = 0.0
        feed = None
        total = None
        ends = []
        for ln in moves:
            w = _words(ln)
            nx = float(w["X"]) if "X" in w else x
            ny = float(w["Y"]) if "Y" in w else y
            length = math.hypot(nx - x, ny - y)
            if "F" in w:
                feed = float(w["F"])
            a0, a1 = acc, acc + length
            ends.append((a0, a1, feed, length))
            acc = a1
            x, y = nx, ny
        total = acc
        self.assertGreater(total, 2 * SLOW_END_MM)
        for a0, a1, feed, length in ends:
            self.assertIsNotNone(feed)
            mid = 0.5 * (a0 + a1)
            if mid < SLOW_END_MM or mid > total - SLOW_END_MM:
                self.assertLessEqual(feed, SLOW_END_F + 0.5)
            else:
                self.assertAlmostEqual(feed, 1560.0, places=3)
            self.assertGreater(length, 1e-6)

        glide = next(ln for ln in out.splitlines() if ";glide" in ln)
        gw = _words(glide)
        self.assertAlmostEqual(float(gw["X"]), 120.0 + WIPE_MM, places=3)
        self.assertAlmostEqual(float(gw["Y"]), 0.0, places=3)
        self.assertAlmostEqual(float(gw["F"]), 1560.0 * WIPE_FRAC, places=3)
        self.assertIn(";WIPE_START", out)
        self.assertIn(";WIPE_END", out)

    def test_short_loop_is_slow_the_whole_way(self) -> None:
        out, _counts = seam(_short_loop(), retract_min_mm=2.0, skip_infill_travel=True, g2b_anchor=True)
        moves = _extrusions_after_seam(out)
        self.assertGreaterEqual(len(moves), 3)
        acc = 0.0
        x, y = 2.0, 0.0  # bottom edge x=0..4, midpoint (2, 0)
        for ln in moves:
            w = _words(ln)
            nx = float(w["X"]) if "X" in w else x
            ny = float(w["Y"]) if "Y" in w else y
            acc += math.hypot(nx - x, ny - y)
            self.assertLessEqual(float(w["F"]), SLOW_END_F + 0.5)
            x, y = nx, ny
        self.assertLess(acc, 2 * SLOW_END_MM)
        self.assertAlmostEqual(x, 2.0, places=3)
        self.assertAlmostEqual(y, 0.0, places=3)

    def test_slower_than_1200_is_kept(self) -> None:
        src = "\n".join([
            "M83",
            "G90",
            ";LAYER_CHANGE",
            ";Z:40",
            "G1 X70 Y1 F1560",
            ";TYPE:Outer wall",
            "G1 X80 Y0 F12720",
            "G1 X160 Y0 E0.80 F800",
            "G1 X160 Y2 E0.02 F1560",
            "G1 X80 Y2 E0.80",
            "G1 X80 Y0 E0.02",
            "",
        ])
        out, _counts = seam(src, retract_min_mm=2.0, skip_infill_travel=True, g2b_anchor=True)
        moves = _extrusions_after_seam(out)
        first = _words(moves[0])
        # The bottom half is already F800, so it is not raised and not split.
        # The return half of that same edge is the last 10 mm, and stays F800.
        self.assertAlmostEqual(float(first["X"]), 160.0, places=3)
        self.assertAlmostEqual(float(first["F"]), 800.0, places=3)
        self.assertAlmostEqual(float(_words(moves[-1])["F"]), 800.0, places=3)
        self.assertAlmostEqual(float(_words(moves[1])["F"]), 1560.0, places=3)

    def test_gate_j3_accepts_the_split(self) -> None:
        import postprocess_g2b as pp

        saved = pp.SLOW_END_F
        try:
            pp.SLOW_END_F = 1.0e9
            ref, _counts = seam(_rect(), retract_min_mm=2.0, skip_infill_travel=True, g2b_anchor=True)
        finally:
            pp.SLOW_END_F = saved
        cand, _counts = seam(_rect(), retract_min_mm=2.0, skip_infill_travel=True, g2b_anchor=True)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "ref.gcode").write_text(ref)
            (root / "cand.gcode").write_text(cand)
            pref = g3.loops(str(root / "ref.gcode"))
            pcand = g3.loops(str(root / "cand.gcode"))
        self.assertEqual(len(pref[0]), len(pcand[0]))
        why = g3.j3(pcand[0][0], pref[0][0])
        self.assertEqual(why, [])

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


def _e_for(width: float, length: float) -> float:
    corner = 0.46 * (1.0 - math.pi / 4.0)
    return (width - corner) * 0.46 / FOREFOOT_FILAMENT_AREA * length


def _loop(typ: str, pts: list[tuple[float, float]], width: float = 1.15) -> list[str]:
    out = [f";TYPE:{typ}", f"G1 X{pts[0][0]} Y{pts[0][1]} F12720"]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        length = math.hypot(x1 - x0, y1 - y0)
        out.append(f"G1 X{x1:.3f} Y{y1:.3f} E{_e_for(width, length):.5f} F1560")
    return out


def _slice(loops: list[tuple[str, list[tuple[float, float]]]]) -> str:
    lines = ["M83", "G90", ";LAYER_CHANGE", ";Z:0.46", "G1 X70 Y1 F1560"]
    for typ, pts in loops:
        lines.extend(_loop(typ, pts))
    lines += ["", ";LAYER_CHANGE", ";Z:1.06", ";TYPE:Outer wall", "G1 X10 Y1 E0.10000 F1560"]
    return "\n".join(lines) + "\n"


def _horiz(text: str, layer: int = 1) -> list[tuple[float, float, float, float]]:
    """Horizontal extrusions in one layer: (y, width, feed, length)."""
    body = text.split(";LAYER_CHANGE")[layer]
    x = y = None
    found = []
    for line in body.splitlines():
        if not line.startswith("G1"):
            continue
        w = _words(line)
        if "X" not in w and "Y" not in w:
            continue
        nx = float(w["X"]) if "X" in w else x
        ny = float(w["Y"]) if "Y" in w else y
        if x is not None and "E" in w and float(w["E"]) > 0:
            length = math.hypot(nx - x, ny - y)
            if length > 5 and abs(ny - y) < 1e-6:
                feed = float(w["F"]) if "F" in w else 0.0
                found.append((ny, bead_width(float(w["E"]) / length), feed, length))
        x, y = nx, ny
    return found


class ForefootCuraBond(unittest.TestCase):
    def test_layer_cutoff(self) -> None:
        self.assertEqual(forefoot_last_layer(439), 165)
        self.assertEqual(forefoot_last_layer(90), 31)
        self.assertIsNone(forefoot_last_layer(10))
        self.assertIsNone(forefoot_last_layer(250))

    def test_short_file_is_untouched(self) -> None:
        src = _slice([("Outer wall", [(80, 0.5), (160, 0.5), (160, 2.06), (80, 2.06), (80, 0.5)])])
        seamed, _counts = seam(src, retract_min_mm=2.0, skip_infill_travel=True, g2b_anchor=True)
        out, stats = forefoot_cura_bond(seamed)
        self.assertFalse(stats["applied"])
        self.assertEqual(out, seamed)

    def test_thin_skins_overlap_with_the_outer_edge_fixed(self) -> None:
        src = _slice([("Outer wall", [(80, 0.5), (160, 0.5), (160, 2.06), (80, 2.06), (80, 0.5)])])
        seamed, _counts = seam(src, retract_min_mm=2.0, skip_infill_travel=True, g2b_anchor=True)
        out, stats = forefoot_cura_bond(seamed, last_layer=1)
        self.assertTrue(stats["applied"])
        self.assertEqual(seamed.split(";LAYER_CHANGE")[2], out.split(";LAYER_CHANGE")[2])
        rows = _horiz(out)
        low = [r for r in rows if r[0] < 1.2]
        high = [r for r in rows if r[0] > 1.2]
        self.assertTrue(low and high)
        for y, width, feed, length in low:
            self.assertAlmostEqual(y - width / 2, 0.5 - 1.15 / 2, delta=0.02)
            self.assertGreaterEqual(width - (high[0][0] - y), 0.05)
            self.assertAlmostEqual(width - (high[0][0] - y), 0.45, delta=0.02)
            if feed:
                q = (width - 0.46 * (1 - math.pi / 4)) * 0.46 * (feed / 60.0)
                self.assertLessEqual(q, FOREFOOT_FLOW_CAP + 1e-6)
        for y, width, _feed, _length in high:
            self.assertAlmostEqual(y + width / 2, 2.06 + 1.15 / 2, delta=0.02)
        # Side centres stay on the outline, so the printed side edge does too.
        body = out.split(";LAYER_CHANGE")[1]
        x = y = None
        sides = []
        for line in body.splitlines():
            if not line.startswith("G1"):
                continue
            w = _words(line)
            if "X" not in w and "Y" not in w:
                continue
            nx = float(w["X"]) if "X" in w else x
            ny = float(w["Y"]) if "Y" in w else y
            if x is not None and "E" in w and float(w["E"]) > 0 and abs(nx - x) < 1e-6 and abs(ny - y) > 0.2:
                sides.append(nx)
            x, y = nx, ny
        self.assertTrue(sides)
        for sx in sides:
            self.assertTrue(any(abs(sx - edge) < 0.02 for edge in (80.0, 160.0)))
        report = crossings.analyze(out)
        for key in ("perimeter_crossings", "surface_crossings", "seam_block_crossings"):
            self.assertEqual(report["totals"][key], 0)
        # The seam stays on the loop's own bottom edge.
        start = None
        for line in out.split(";LAYER_CHANGE")[1].splitlines():
            if line.endswith(";seam travel") or " ;seam travel" in line:
                w = _words(line)
                if "Y" in w:
                    start = float(w["Y"])
        self.assertIsNotNone(start)
        self.assertAlmostEqual(start, min(r[0] for r in low), delta=0.15)

    def test_thick_outer_overlaps_an_unmoved_inner_wall(self) -> None:
        inner = [(84, 1.886), (156, 1.886), (156, 3.614), (84, 3.614), (84, 1.886)]
        src = _slice([
            ("Outer wall", [(80, 0.5), (160, 0.5), (160, 5.0), (80, 5.0), (80, 0.5)]),
            ("Inner wall", inner),
        ])
        seamed, _counts = seam(src, retract_min_mm=2.0, skip_infill_travel=True, g2b_anchor=True)
        out, _stats = forefoot_cura_bond(seamed, last_layer=1)
        self.assertEqual(seamed.split(";LAYER_CHANGE")[2], out.split(";LAYER_CHANGE")[2])
        rows = _horiz(out)
        plantar = min(rows, key=lambda r: r[0])
        dorsal = max(rows, key=lambda r: r[0])
        self.assertAlmostEqual(plantar[0] - plantar[1] / 2, 0.5 - 1.15 / 2, delta=0.02)
        self.assertAlmostEqual(dorsal[0] + dorsal[1] / 2, 5.0 + 1.15 / 2, delta=0.02)
        # Inner wall centre lines stay where seam left them.
        for y in (1.886, 3.614):
            self.assertTrue(any(abs(r[0] - y) < 1e-3 and abs(r[1] - 1.15) < 0.01 for r in rows))
        inner_near = 1.886 - 1.15 / 2
        overlap = (plantar[0] + plantar[1] / 2) - inner_near
        self.assertGreaterEqual(overlap, 0.15)
        self.assertAlmostEqual(overlap, 0.23, delta=0.02)
        report = crossings.analyze(out)
        for key in ("perimeter_crossings", "surface_crossings", "seam_block_crossings"):
            self.assertEqual(report["totals"][key], 0)


if __name__ == "__main__":
    unittest.main()
