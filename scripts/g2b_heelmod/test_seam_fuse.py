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
    PRIME,
    RETRACT,
    SLOW_END_F,
    SLOW_END_MM,
    WIPE_FRAC,
    WIPE_MM,
    _band_crosses_wall,
    _band_tip_paths,
    _band_travel,
    _belt_split,
    _crossing,
    _ff_e_for_width,
    forefoot_cura_bond,
    forefoot_type_audit,
    seam,
)
from check_travel_wall_crossings import _Grid  # noqa: E402
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


_FILAMENT_AREA = math.pi * (1.75 * 0.5) ** 2


def _forefoot_e(width: float, length: float) -> float:
    return _ff_e_for_width(width, length, _FILAMENT_AREA)


def _g1(x: float, y: float, e: float | None = None, f: float | None = None) -> str:
    parts = ["G1", f"X{x:.3f}", f"Y{y:.3f}"]
    if f is not None:
        parts.append(f"F{f:.0f}")
    if e is not None:
        parts.append(f"E{e:.5f}")
    return " ".join(parts)


def _loop(points: list[tuple[float, float, float]], z: float, feature: str | None) -> list[str]:
    """Closed loop. points are (x, y, width) at each vertex; the last joins the first."""
    lines = [
        ";LAYER_CHANGE",
        f";Z:{z:.5f}",
        _g1(points[0][0], points[0][1], f=12720) + f" Z{z:.3f}",
    ]
    if feature:
        lines.append(f";TYPE:{feature}")
    lines.append("G1 F1560")
    nxt = points[1:] + [points[0]]
    for (x0, y0, _w0), (x1, y1, w1) in zip(points, nxt):
        length = math.hypot(x1 - x0, y1 - y0)
        lines.append(_g1(x1, y1, _forefoot_e(w1, length)))
    return lines


def _program(layers: list[list[str]]) -> str:
    # A leading marker is layer 0. Forefoot extrusion starts at layer 1, matching
    # the frozen grader, so every fixture begins with an empty layer change.
    return "\n".join([
        "; filament_diameter = 1.75",
        "M83",
        "G90",
        ";LAYER_CHANGE",
        ";Z:0.00000",
        *sum(layers, []),
        "",
    ])


def _extrusions(text: str) -> list[tuple[float, float, float, float, float]]:
    """(x0, y0, x1, y1, e) for positive extrusions, in order."""
    x = y = 0.0
    out = []
    for line in text.splitlines():
        if not line.startswith("G1") and not line.startswith("G0"):
            continue
        words = {}
        for tok in line.split(";")[0].split():
            if tok[0] in "XYZE":
                words[tok[0]] = float(tok[1:])
        nx = words.get("X", x)
        ny = words.get("Y", y)
        if "E" in words and words["E"] > 1e-6:
            out.append((x, y, nx, ny, words["E"]))
        if "X" in words or "Y" in words:
            x, y = nx, ny
    return out


def _bottom_outer(x0, y0, x1, y1, e) -> float:
    length = math.hypot(x1 - x0, y1 - y0)
    width = (e / length) * _FILAMENT_AREA / 0.46 + 0.46 * (1.0 - math.pi / 4.0)
    return 0.5 * (y0 + y1) - width * 0.5


class ForefootBondTests(unittest.TestCase):
    def test_feature_type_persists_across_layer_change(self) -> None:
        # ;TYPE: is emitted only when the feature changes, so a later layer with
        # no comment is still the outer wall. Resetting the type at LAYER_CHANGE
        # drops that layer.
        pts = [(100.0, 0.5, 1.146), (120.0, 0.5, 1.146), (120.0, 2.2, 1.146), (100.0, 2.2, 1.146)]
        text = _program([
            _loop(pts, 0.46, "Outer wall"),
            _loop(pts, 0.92, None),
        ])
        kept = forefoot_type_audit(text, reset_type=False, last_layer=2)
        reset = forefoot_type_audit(text, reset_type=True, last_layer=2)
        self.assertEqual(kept["missed_layers"], [])
        self.assertIn(2, kept["chains"])
        self.assertEqual(reset["missed_layers"], [2])
        _out, stats = forefoot_cura_bond(text, last_layer=2)
        self.assertTrue(stats["applied"])
        self.assertGreater(stats["widened"], 0)

    def test_variable_width_join_keeps_the_outer_edge(self) -> None:
        # Two bottom beads of different width share one outer edge. The centreline
        # steps inward by half the extra width. The bond may widen them; the
        # printed outer edge stays on that line and the path stays continuous.
        outer = 0.05
        w1, w2 = 1.00, 1.40
        y1, y2 = outer + w1 * 0.5, outer + w2 * 0.5
        pts = [
            (100.0, y1, w1),
            (112.0, y1, w1),
            (112.0, y2, w2),
            (124.0, y2, w2),
            (124.0, 2.4, 1.146),
            (100.0, 2.4, 1.146),
        ]
        text = _program([_loop(pts, 0.46, "Outer wall")])
        out, stats = forefoot_cura_bond(text, last_layer=1)
        self.assertTrue(stats["applied"])
        moves = _extrusions(out)
        self.assertGreaterEqual(len(moves), 4)
        for prev, nxt in zip(moves, moves[1:]):
            self.assertLess(math.hypot(nxt[0] - prev[2], nxt[1] - prev[3]), 0.05)
        bottoms = [m for m in moves if abs(m[3] - m[1]) < 0.05 and m[2] > m[0] and m[1] < 1.5]
        self.assertGreaterEqual(len(bottoms), 2)
        for move in bottoms:
            self.assertLess(abs(_bottom_outer(*move) - outer), 0.02)

    def test_thick_section_fill_presses_into_the_skins(self) -> None:
        # A 4 mm section keeps the outer wall and replaces the inner wall with
        # one fill bead pressed into both skins. The outer edge does not move.
        outer_pts = [
            (90.0, 0.50, 1.146),
            (130.0, 0.50, 1.146),
            (130.0, 3.40, 1.146),
            (90.0, 3.40, 1.146),
        ]
        low = [(95.0, 1.55, 0.80), (125.0, 1.55, 0.80), (125.0, 1.90, 0.80), (95.0, 1.90, 0.80)]
        high = [(95.0, 2.50, 0.80), (125.0, 2.50, 0.80), (125.0, 2.85, 0.80), (95.0, 2.85, 0.80)]
        body = [
            ";LAYER_CHANGE",
            ";Z:0.46000",
            _g1(90.0, 0.50, f=12720) + " Z0.460",
            ";TYPE:Outer wall",
            "G1 F1560",
        ]
        def emit(points):
            nxt = points[1:] + [points[0]]
            for (x0, y0, _w0), (x1, y1, w1) in zip(points, nxt):
                length = math.hypot(x1 - x0, y1 - y0)
                body.append(_g1(x1, y1, _forefoot_e(w1, length)))
        emit(outer_pts)
        body += [";TYPE:Inner wall", _g1(95.0, 1.55, f=12720)]
        emit(low)
        body += [_g1(95.0, 2.50, f=12720)]
        emit(high)
        text = _program([body])
        before = _extrusions(text)
        out, stats = forefoot_cura_bond(text, last_layer=1)
        self.assertTrue(stats["applied"])
        self.assertGreater(stats["thick_bins"], 0)
        after = _extrusions(out)
        outer_before = [m for m in before if min(m[1], m[3]) < 0.6 or max(m[1], m[3]) > 3.2]
        outer_after = [m for m in after if min(m[1], m[3]) < 0.6 or max(m[1], m[3]) > 3.2]
        self.assertEqual(outer_after, outer_before)
        # The old inner-wall centreline at y=1.55 is gone.
        self.assertFalse(any(abs(m[1] - 1.55) < 0.01 and abs(m[3] - 1.55) < 0.01 for m in after))
        corner = 0.46 * (1.0 - math.pi / 4.0)
        covered = False
        for x0, y0, x1, y1, e in after:
            length = math.hypot(x1 - x0, y1 - y0)
            if length < 0.4 or abs(x1 - x0) / length < 0.25:
                continue
            if max(x0, x1) < 100.0 or min(x0, x1) > 120.0:
                continue
            width = (e / length) * _FILAMENT_AREA / 0.46 + corner
            yc = 0.5 * (y0 + y1)
            half = (width * 0.5) / (abs(x1 - x0) / length)
            lo, hi = yc - half, yc + half
            # Plantar skin inner edge is 1.073, dorsal inner edge is 2.827.
            if min(1.073, hi) - max(0.50 - 0.573, lo) >= 0.15 and min(3.40 + 0.573, hi) - max(2.827, lo) >= 0.15:
                covered = True
        self.assertTrue(covered, "fill bead does not reach both skins")

    def test_thick_fill_routes_a_crossing_seam_travel_inside(self) -> None:
        # The fill leaves the nozzle at the toe tip. The next seam travel cuts
        # a notch in the outer wall. The walk stays inside and still lands on
        # the seam point, Z included.
        outer_pts = [
            (90.0, 0.50, 1.146),
            (140.0, 0.50, 1.146),
            (140.0, 3.40, 1.146),
            (122.0, 3.40, 1.146),
            (118.0, 1.70, 1.146),
            (114.0, 3.40, 1.146),
            (90.0, 3.40, 1.146),
        ]
        low = [(95.0, 1.55, 0.80), (135.0, 1.55, 0.80), (135.0, 1.90, 0.80), (95.0, 1.90, 0.80)]
        high = [(95.0, 2.50, 0.80), (135.0, 2.50, 0.80), (135.0, 2.85, 0.80), (95.0, 2.85, 0.80)]
        body = [
            ";LAYER_CHANGE",
            ";Z:0.46000",
            _g1(90.0, 0.50, f=12720) + " Z0.460",
            ";TYPE:Outer wall",
            "G1 F1560",
        ]

        def emit(points):
            nxt = points[1:] + [points[0]]
            for (x0, y0, _w0), (x1, y1, w1) in zip(points, nxt):
                length = math.hypot(x1 - x0, y1 - y0)
                body.append(_g1(x1, y1, _forefoot_e(w1, length)))

        emit(outer_pts)
        body += [";TYPE:Inner wall", _g1(95.0, 1.55, f=12720)]
        emit(low)
        body += [_g1(95.0, 2.50, f=12720)]
        emit(high)
        body += [
            ";TYPE:Outer wall",
            "G1 X128.000 Y2.200 F12720",
            "G1 X100.000 Y2.200 Z0.920 F12720 ;seam travel",
        ]
        text = _program([body])
        out, stats = forefoot_cura_bond(text, last_layer=1)
        self.assertGreater(stats["thick_routes"], 0)
        landed = [ln for ln in out.splitlines() if ln.endswith(";seam travel")]
        self.assertEqual(len(landed), 1)
        w = _words(landed[0])
        self.assertEqual(w["X"], "100.000")
        self.assertEqual(w["Y"], "2.200")
        self.assertEqual(w["Z"], "0.920")
        # The straight chord crossed. The emitted pieces do not.
        edges = []
        x = y = None
        typ = ""
        for line in out.splitlines():
            if line.startswith(";TYPE:"):
                typ = line[6:].strip()
                continue
            if not line.startswith("G1"):
                continue
            words = _words(line)
            px, py = x, y
            if "X" in words:
                x = float(words["X"])
            if "Y" in words:
                y = float(words["Y"])
            if px is None or x is None or ("X" not in words and "Y" not in words):
                continue
            if "E" in words and float(words["E"]) > 1e-9 and typ == "Outer wall":
                edges.append((px, py, x, y))
        grid = _Grid(edges)
        sx, sy = 128.0, 2.2
        self.assertTrue(_crossing(grid, sx, sy, 100.0, 2.2))
        for line in out.splitlines():
            if not line.startswith("G1"):
                continue
            words = _words(line)
            if "E" in words and float(words["E"]) > 1e-9:
                continue
            if "X" not in words and "Y" not in words:
                continue
            nx = float(words["X"]) if "X" in words else sx
            ny = float(words["Y"]) if "Y" in words else sy
            self.assertFalse(_crossing(grid, sx, sy, nx, ny), line)
            sx, sy = nx, ny

    def test_corner_join_keeps_both_outer_edges(self) -> None:
        # A thin loop: long plantar wall, vertical side, long dorsal wall.
        # The plantar wall widens and the side wall does not. The corner is a
        # short connector. Both long outer edges stay on the sliced line, and
        # the path stays one continuous loop.
        plantar_y, dorsal_y, side_x = 0.55, 2.35, 180.0
        width = 1.10
        pts = [
            (100.0, plantar_y, width),
            (side_x, plantar_y, width),
            (side_x, dorsal_y, width),
            (100.0, dorsal_y, width),
        ]
        text = _program([_loop(pts, 0.46, "Outer wall")])
        out, stats = forefoot_cura_bond(text, last_layer=1)
        self.assertTrue(stats["applied"])
        moves = _extrusions(out)
        self.assertGreaterEqual(len(moves), 4)
        for prev, nxt in zip(moves, moves[1:]):
            self.assertLess(math.hypot(nxt[0] - prev[2], nxt[1] - prev[3]), 0.05)
        # The loop returns to its start.
        self.assertLess(math.hypot(moves[0][0] - moves[-1][2], moves[0][1] - moves[-1][3]), 0.05)
        bottoms = [m for m in moves if m[2] - m[0] > 20.0 and abs(m[3] - m[1]) < 0.05]
        tops = [m for m in moves if m[0] - m[2] > 20.0 and abs(m[3] - m[1]) < 0.05]
        self.assertTrue(bottoms)
        self.assertTrue(tops)
        for move in bottoms:
            self.assertLess(abs(_bottom_outer(*move) - (plantar_y - width * 0.5)), 0.02)
        for x0, y0, x1, y1, e in tops:
            length = math.hypot(x1 - x0, y1 - y0)
            bead = (e / length) * _FILAMENT_AREA / 0.46 + 0.46 * (1.0 - math.pi / 4.0)
            outer = 0.5 * (y0 + y1) + bead * 0.5
            self.assertLess(abs(outer - (dorsal_y + width * 0.5)), 0.02)
        sides = [
            m for m in moves
            if min(m[0], m[2]) > side_x - 1.0 and abs(m[2] - m[0]) < 0.05 and abs(m[3] - m[1]) > 0.8
        ]
        self.assertTrue(sides)
        for x0, y0, x1, y1, e in sides:
            length = math.hypot(x1 - x0, y1 - y0)
            bead = (e / length) * _FILAMENT_AREA / 0.46 + 0.46 * (1.0 - math.pi / 4.0)
            # The upward side faces +X, so the outer edge is the centre plus half the width.
            outer = 0.5 * (x0 + x1) + (bead * 0.5 if y1 >= y0 else -bead * 0.5)
            self.assertLess(abs(outer - (side_x + width * 0.5)), 0.05)

    def test_short_notch_is_kept_and_its_outer_edge_stays(self) -> None:
        # A 0.16 mm toe notch between the plantar wall and the side wall. Widening
        # the long wall must not average the notch away or collapse it.
        notch = ((180.000, 0.630), (180.148, 0.695))
        pts = [
            (100.0, 0.55, 1.10),
            (notch[0][0], notch[0][1], 1.10),
            (notch[1][0], notch[1][1], 1.10),
            (181.6, 2.40, 1.10),
            (100.0, 1.95, 1.10),
        ]
        text = _program([_loop(pts, 0.46, "Outer wall")])
        out, stats = forefoot_cura_bond(text, last_layer=1)
        self.assertTrue(stats["applied"])
        moves = _extrusions(out)
        notch_len = math.hypot(notch[1][0] - notch[0][0], notch[1][1] - notch[0][1])
        ndx, ndy = notch[1][0] - notch[0][0], notch[1][1] - notch[0][1]
        kept = []
        for m in moves:
            length = math.hypot(m[2] - m[0], m[3] - m[1])
            if min(m[0], m[2]) <= 179.0 or length < 0.10:
                continue
            dot = ((m[2] - m[0]) * ndx + (m[3] - m[1]) * ndy) / (length * notch_len)
            if dot > 0.95:
                kept.append(m)
        self.assertTrue(kept, "short notch was dropped or flipped")
        for prev, nxt in zip(moves, moves[1:]):
            self.assertLess(math.hypot(nxt[0] - prev[2], nxt[1] - prev[3]), 0.05)

    def test_band_sparse_infill_fills_the_hollow_and_keeps_other_layers(self) -> None:
        # Layers 156-165 hold one empty section thicker than 3 mm, plus a
        # thinner loop that must stay solid. Every other layer is a marker.
        bodies = []
        for i in range(1, 301):
            z = i * 0.65
            if 156 <= i <= 165:
                thick = _loop(
                    [(100.0, 0.40, 1.0), (160.0, 0.40, 1.0), (160.0, 8.40, 1.0), (100.0, 8.40, 1.0)],
                    z,
                    "Outer wall",
                )
                thin = _loop(
                    [(170.0, 0.40, 1.0), (190.0, 0.40, 1.0), (190.0, 2.20, 1.0), (170.0, 2.20, 1.0)],
                    z,
                    "Outer wall",
                )
                bodies.append(thick + thin[2:])
            else:
                bodies.append([";LAYER_CHANGE", f";Z:{z:.5f}", f";keep {i}"])
        text = _program(bodies)
        new, _stats = forefoot_cura_bond(text)
        old = _printed_bond()(text)[0]
        new_parts = new.split(";LAYER_CHANGE")
        old_parts = old.split(";LAYER_CHANGE")
        self.assertEqual(len(new_parts), len(old_parts))
        for index, (got, exp) in enumerate(zip(new_parts, old_parts)):
            layer = index - 1
            if layer < 156 or layer > 165:
                self.assertEqual(got, exp, f"layer {layer} changed")
                continue
            exp_lines = exp.splitlines()
            got_lines = got.splitlines()
            cursor = 0
            added = []
            for line in exp_lines:
                while cursor < len(got_lines) and got_lines[cursor] != line:
                    added.append(got_lines[cursor])
                    cursor += 1
                self.assertLess(cursor, len(got_lines), f"layer {layer} lost {line!r}")
                cursor += 1
            added.extend(got_lines[cursor:])
            self.assertTrue(any(line.startswith(";TYPE:Sparse infill") for line in added))
            feature = ""
            for line in added:
                if line.startswith(";TYPE:"):
                    feature = line[6:].strip()
                    continue
                words = _words(line)
                if "X" not in words or "E" not in words or float(words["E"]) <= 0.0:
                    continue
                self.assertEqual(feature, "Sparse infill")
                self.assertGreater(float(words["X"]), 99.0)
                self.assertLess(float(words["X"]), 161.0)
            self.assertEqual(_last_type(got), _last_type(exp))
        self.assertEqual(_synthetic_hollow_columns(new), 0)

    def test_band_tip_and_travel_stay_inside_the_wall(self) -> None:
        # The centerline infill stops at x=128. The shell stadium still leaves
        # a gap wider than 1 mm past that, and the tip line has to cover it.
        beads = [
            (100.0, 0.0, 130.0, 0.0, True, 1.0),
            (130.0, 0.0, 130.0, 8.0, True, 1.0),
            (130.0, 8.0, 100.0, 8.0, True, 1.0),
            (100.0, 8.0, 100.0, 0.0, True, 1.0),
        ]
        tips = _band_tip_paths(beads, [[(100.5, 4.0), (128.0, 4.0)]])
        xs = [point[0] for path in tips for point in path]
        self.assertTrue(xs)
        self.assertGreater(max(xs), 129.0)
        self.assertTrue(all(128.0 < x < 130.0 and 1.0 < y < 7.0 for path in tips for x, y in path))
        # One end is 0.05 mm past the centerline (inside the bead) and the
        # other is 0.9 mm beyond it. The chord has to be nicked, not left straight.
        wall = [(0.0, 0.0, 10.0, 0.0)]
        nicked = _band_travel(5.0, 0.05, 5.0, -0.9, wall, None)
        self.assertEqual(nicked[0], (5.0, 0.05))
        self.assertEqual(nicked[-1], (5.0, -0.9))
        self.assertGreater(len(nicked), 2)
        for start, dest in zip(nicked, nicked[1:]):
            self.assertFalse(_band_crosses_wall(start[0], start[1], dest[0], dest[1], wall))
        shallow = _band_travel(5.0, 0.05, 5.0, -0.10, wall, None)
        self.assertEqual(shallow, [(5.0, 0.05), (5.0, -0.10)])


def _last_type(part: str) -> str | None:
    found = None
    for line in part.splitlines():
        if line.startswith(";TYPE:"):
            found = line
    return found


def _printed_bond():
    """cb0569eb's forefoot bond, the file Kendon printed."""
    import importlib.util
    import subprocess
    import tempfile

    src = subprocess.check_output(
        ["git", "show", "cb0569eb19d9502841d73d30f1e4d93bc64bb07d:scripts/g2b_heelmod/postprocess_g2b.py"],
        cwd=REPO,
    )
    path = Path(tempfile.mkdtemp()) / "postprocess_g2b_cb0569.py"
    path.write_bytes(src)
    spec = importlib.util.spec_from_file_location("postprocess_g2b_cb0569", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.forefoot_cura_bond


def _synthetic_hollow_columns(text: str) -> int:
    """Per band layer, columns of the thick loop with no extrusion in the gap."""
    hollow = 0
    # split keeps the preamble in front, so part i+1 is layer i.
    for part in text.split(";LAYER_CHANGE")[157:167]:
        moves = _extrusions(part)
        x = 100.125
        while x < 160.0:
            hit = False
            for x0, y0, x1, y1, _e in moves:
                if abs(x1 - x0) < 1e-9:
                    continue
                if x < min(x0, x1) - 1e-9 or x > max(x0, x1) + 1e-9:
                    continue
                t = (x - x0) / (x1 - x0)
                y = y0 + t * (y1 - y0)
                if 1.0 < y < 7.8:
                    hit = True
                    break
            if not hit:
                hollow += 1
            x += 0.25
    return hollow


if __name__ == "__main__":
    unittest.main()
