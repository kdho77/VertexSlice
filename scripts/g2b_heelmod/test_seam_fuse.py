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
    _belt_split,
    _chain_normals,
    _columns,
    _crossing,
    _ff_e_for_width,
    _pair_of,
    _parse_moves,
    _skin_chains,
    forefoot_cura_bond,
    forefoot_last_layer,
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


def _open_gaps(text: str, last: int) -> list[tuple[tuple[int, int], float, float, float]]:
    """Enclosed columns thicker than 6 mm with a positive gap.

    Each row is (key, gap, millimetres of non-skin extrusion in the gap,
    deepest press of that extrusion into either skin).
    """
    lines = text.splitlines(keepends=True)
    parsed = _parse_moves(lines, reset_type=False)
    cols = _columns(parsed["segs"])
    kinds = [seg["kind"] for seg in parsed["segs"]]
    rows = []
    for key, beads in cols.items():
        if not (1 <= key[0] <= last):
            continue
        pair = _pair_of(beads, kinds)
        if pair is None:
            continue
        plow, phigh, dlow, dhigh, thick, gap = pair
        if thick <= 6.0 or gap <= 0.0:
            continue
        covered = 0.0
        reach = 0.0
        for lo, hi, _yc, idx in beads:
            if kinds[idx] == "skin":
                continue
            overlap = min(hi, dlow) - max(lo, phigh)
            if overlap > covered:
                covered = overlap
            reach = max(reach, phigh - lo, hi - dlow)
        rows.append((key, gap, covered, reach))
    return rows


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
        # The gap is infill, about 1.11 mm, not a bead that fills the 4 mm section.
        corner = 0.46 * (1.0 - math.pi / 4.0)
        widths = []
        for x0, y0, x1, y1, e in after:
            length = math.hypot(x1 - x0, y1 - y0)
            if length < 0.4 or abs(x1 - x0) / length < 0.25:
                continue
            if max(x0, x1) < 100.0 or min(x0, x1) > 120.0:
                continue
            if min(y0, y1) < 0.6 or max(y0, y1) > 3.2:
                continue
            widths.append((e / length) * _FILAMENT_AREA / 0.46 + corner)
        self.assertTrue(widths, "3-6 mm section has no infill")
        self.assertTrue(all(0.9 <= w <= 1.4 for w in widths), widths)

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

    def test_over_six_millimetre_column_is_not_left_empty(self) -> None:
        # The thick pass drops a whole inner wall when any of it sits in a
        # 3-6 mm column. The part of that wall over 6 mm used to print as two
        # outer walls and nothing between them. Every such column has to keep
        # some extrusion in the open gap, and that extrusion stays a ~1 mm
        # line rather than a solid fill of the gap.
        outer = [
            (80.0, 0.50, 1.146),
            (160.0, 0.50, 1.146),
            (160.0, 4.30, 1.146),
            (120.0, 4.30, 1.146),
            (120.0, 5.646, 1.146),
            (80.0, 5.646, 1.146),
        ]
        inner = [(85.0, 2.20, 0.80), (155.0, 2.20, 0.80), (155.0, 2.60, 0.80), (85.0, 2.60, 0.80)]
        body = [
            ";LAYER_CHANGE",
            ";Z:0.46000",
            _g1(80.0, 0.50, f=12720) + " Z0.460",
            ";TYPE:Outer wall",
            "G1 F1560",
        ]

        def emit(points):
            nxt = points[1:] + [points[0]]
            for (x0, y0, _w0), (x1, y1, w1) in zip(points, nxt):
                length = math.hypot(x1 - x0, y1 - y0)
                body.append(_g1(x1, y1, _forefoot_e(w1, length)))

        emit(outer)
        body += [";TYPE:Inner wall", _g1(85.0, 2.20, f=12720)]
        emit(inner)
        text = _program([body])
        out, _stats = forefoot_cura_bond(text, last_layer=1)
        hollow = _open_gaps(out, 1)
        self.assertGreater(len(hollow), 10)
        for _key, gap, covered, reach in hollow:
            self.assertGreater(covered, 0.02, "over-6 mm column has no extrusion between the walls")
            self.assertLess(covered, gap * 0.60, "over-6 mm column was filled solid")
        self.assertTrue(any(reach >= 0.15 for _key, _gap, _covered, reach in hollow))

    def test_thickness_picks_solid_or_infill_on_a_skipped_layer_count(self) -> None:
        # 250 layers used to be left untouched (the old rule skipped 221-299).
        # A 2.4 mm section is the thin solid bond. A 5 mm section is infill.
        # The section height is the existing forefoot loop, scaled into this file.
        self.assertEqual(forefoot_last_layer(250), 250)
        self.assertEqual(forefoot_last_layer(95), 95)
        self.assertEqual(forefoot_last_layer(400), 400)
        thin = _loop(
            [(100.0, 0.55, 1.10), (140.0, 0.55, 1.10), (140.0, 2.35, 1.10), (100.0, 2.35, 1.10)],
            0.46,
            "Outer wall",
        )
        thick = [
            ";LAYER_CHANGE",
            ";Z:18.40000",
            _g1(90.0, 0.50, f=12720) + " Z18.400",
            ";TYPE:Outer wall",
            "G1 F1560",
        ]
        outer = [(90.0, 0.50, 1.146), (140.0, 0.50, 1.146), (140.0, 4.20, 1.146), (90.0, 4.20, 1.146)]
        inner = [(95.0, 1.80, 0.80), (135.0, 1.80, 0.80), (135.0, 2.20, 0.80), (95.0, 2.20, 0.80)]

        def emit(points, into):
            nxt = points[1:] + [points[0]]
            for (x0, y0, _w0), (x1, y1, w1) in zip(points, nxt):
                length = math.hypot(x1 - x0, y1 - y0)
                into.append(_g1(x1, y1, _forefoot_e(w1, length)))

        emit(outer, thick)
        thick += [";TYPE:Inner wall", _g1(95.0, 1.80, f=12720)]
        emit(inner, thick)
        layers = []
        for index in range(1, 250):
            if index == 10:
                layers.append(thin)
            elif index == 40:
                layers.append(thick)
            else:
                layers.append([";LAYER_CHANGE", f";Z:{index * 0.46:.5f}"])
        text = _program(layers)
        self.assertEqual(text.count(";LAYER_CHANGE"), 250)
        out, stats = forefoot_cura_bond(text)
        self.assertNotEqual(stats.get("reason"), "out of scope")
        self.assertGreater(stats.get("open_bins", 0), 0)
        # The thin layer's two skins now overlap. The thick layer's inner wall is
        # gone and the gap has an infill line rather than a solid span.
        lines = out.splitlines(keepends=True)
        parsed = _parse_moves(lines, reset_type=False)
        cols = _columns(parsed["segs"])
        kinds = [seg["kind"] for seg in parsed["segs"]]
        thin_gaps = []
        thick_infill = []
        for key, beads in cols.items():
            pair = _pair_of(beads, kinds)
            if pair is None:
                continue
            _pl, phigh, dlow, _dh, thick_mm, gap = pair
            if key[0] == 10 and thick_mm < 3.0:
                thin_gaps.append(gap)
            if key[0] == 40 and thick_mm >= 3.0:
                covered = 0.0
                for lo, hi, _yc, idx in beads:
                    if kinds[idx] == "skin":
                        continue
                    covered = max(covered, min(hi, dlow) - max(lo, phigh))
                thick_infill.append(covered)
        self.assertTrue(thin_gaps)
        self.assertTrue(all(gap <= 0.0 for gap in thin_gaps), thin_gaps)
        self.assertTrue(thick_infill)
        self.assertTrue(all(covered > 0.02 for covered in thick_infill))
        self.assertFalse(any(abs(m[1] - 1.80) < 0.01 and abs(m[3] - 1.80) < 0.01 for m in _extrusions(out)))

    def test_heel_outer_edge_stays_on_the_sliced_line(self) -> None:
        # The heel chamfer, parser layer 180 of a 300-layer file, so it is past
        # the shipped forefoot (layers 166 and up). The corner join used to slide
        # the wall's ends apart and leave the printed rim up to 0.44 mm outside
        # the sliced line. The bond still closes the 2-3 mm section. The rim must
        # not move more than 0.05 mm outside that line. The loop is the sliced
        # sample's own wall, not a patient file.
        pts = [
            (147.099, 0.027, 1.04), (157.098, 0.028, 1.04), (179.934, 0.032, 1.04),
            (180.516, 0.077, 1.041), (180.853, 0.287, 1.04), (181.236, 0.701, 1.039),
            (184.73, 5.856, 1.04), (184.69, 5.893, 1.035), (184.391, 5.919, 1.042),
            (182.874, 5.193, 1.04), (181.336, 4.639, 1.04), (179.629, 4.157, 1.04),
            (177.355, 3.775, 1.04), (175.525, 3.644, 1.04), (174.029, 3.578, 1.04),
            (172.72, 3.583, 1.04), (170.88, 3.647, 1.041), (169.166, 3.759, 1.04),
            (167.649, 3.896, 1.04), (164.996, 4.216, 1.04), (161.93, 4.691, 1.04),
            (153.857, 6.06, 1.04), (151.008, 6.492, 1.04), (149.501, 6.708, 1.04),
            (146.506, 7.094, 1.04), (143.374, 7.433, 1.04), (141.59, 7.587, 1.04),
            (136.851, 7.928, 1.04), (122.209, 8.684, 1.04), (120.278, 8.761, 1.04),
            (113.798, 9.095, 1.04), (111.791, 9.227, 1.04), (102.77, 9.947, 1.04),
            (101.266, 10.084, 1.04), (97.958, 10.475, 1.04), (104.54, 6.058, 1.04),
            (112.228, 0.768, 1.04), (112.996, 0.329, 1.04), (113.747, 0.077, 1.04),
            (114.263, 0.021, 1.04), (137.099, 0.025, 1.04),
        ]
        layers = []
        for index in range(1, 300):
            if index == 180:
                layers.append(_loop(pts, 180 * 0.46, "Outer wall"))
            else:
                layers.append([";LAYER_CHANGE", f";Z:{index * 0.46:.5f}"])
        text = _program(layers)
        self.assertEqual(text.count(";LAYER_CHANGE"), 300)
        out, stats = forefoot_cura_bond(text)
        self.assertTrue(stats["applied"])

        def rim_edges(gcode: str) -> list[tuple[float, float, float, float, float, float]]:
            parsed = _parse_moves(gcode.splitlines(keepends=True), reset_type=False)
            segs = [s for s in parsed["segs"] if s["layer"] == 180 and s["kind"] == "skin"]
            edges = []
            for chain in _skin_chains(segs):
                for seg, normal in zip(chain, _chain_normals(chain)):
                    half = seg["w"] * 0.5
                    edges.append((
                        seg["x0"] + normal[0] * half, seg["y0"] + normal[1] * half,
                        seg["x1"] + normal[0] * half, seg["y1"] + normal[1] * half,
                        normal[0], normal[1],
                    ))
            return edges

        src = rim_edges(text)
        dst = rim_edges(out)
        self.assertTrue(src and dst)
        worst = 0.0
        for x0, y0, x1, y1, _nx, _ny in dst:
            length = math.hypot(x1 - x0, y1 - y0)
            steps = max(1, int(max(length, 1e-9) / 0.40))
            for k in range(steps):
                t = (k + 0.5) / steps
                px = x0 + (x1 - x0) * t
                py = y0 + (y1 - y0) * t
                nearest = 1e9
                signed = 0.0
                for sx0, sy0, sx1, sy1, ox, oy in src:
                    dx, dy = sx1 - sx0, sy1 - sy0
                    span = dx * dx + dy * dy
                    if span < 1e-12:
                        dist = math.hypot(px - sx0, py - sy0)
                        qx, qy = sx0, sy0
                    else:
                        u = ((px - sx0) * dx + (py - sy0) * dy) / span
                        u = 0.0 if u < 0.0 else (1.0 if u > 1.0 else u)
                        qx, qy = sx0 + u * dx, sy0 + u * dy
                        dist = math.hypot(px - qx, py - qy)
                    if dist < nearest:
                        nearest = dist
                        signed = (px - qx) * ox + (py - qy) * oy
                # Positive is past the sliced line. A join that stays inside
                # the part is not the outer edge moving.
                worst = max(worst, signed)
        self.assertLessEqual(worst, 0.05, worst)
        parsed = _parse_moves(out.splitlines(keepends=True), reset_type=False)
        cols = _columns(parsed["segs"])
        kinds = [seg["kind"] for seg in parsed["segs"]]
        gaps = []
        for key, beads in cols.items():
            if key[0] != 180:
                continue
            pair = _pair_of(beads, kinds)
            if pair is None:
                continue
            thick_mm, gap = pair[4], pair[5]
            if 2.0 <= thick_mm < 3.0:
                gaps.append(gap)
        self.assertTrue(gaps)
        self.assertTrue(all(gap <= 0.0 for gap in gaps), gaps)

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


if __name__ == "__main__":
    unittest.main()
