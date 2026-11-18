#!/usr/bin/env python3
"""Closed outer seams leave the plantar face, and retract/prime does not go short."""
from __future__ import annotations

import math
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path[:0] = [str(HERE), str(REPO)]

from check_travel_wall_crossings import analyze  # noqa: E402
from postprocess_g2b import (  # noqa: E402
    CLOSED_GAP_MM,
    OVERLAP_MM,
    PRIME,
    RETRACT,
    SHORT_LOOP_MM,
    _dorsal_split,
    close_thin_skins,
    seam,
)
from tools.belt_pp.pipeline import finalize_apex_light  # noqa: E402

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


def _short_nub_layer() -> str:
    """Arch/heel shape: a ~0.5 mm closed nub between two real outers.

    The first leg is 0.31 mm, so the overlap would replay that whole leg
    (step = min(0.4, span)) from the end, which is not on the leg. The
    lift-and-direct hop between the two loops rides Y+0.4 and cuts that
    extra wall. The nub is longer than OVERLAP_MM and shorter than the
    short-loop cutoff.
    """
    return "\n".join([
        "M83",
        "G90",
        ";LAYER_CHANGE",
        ";Z:199.715",
        "; reduce_crossing_wall = 1",
        "G1 X20 Y2 F1560",
        ";TYPE:Outer wall",
        "G1 X20.05 Y3.94 F12720",
        "G1 X20.05 Y4.25 E0.01 F1560",
        "G1 X20.20 Y4.10 E0.01",
        "G1 X0 Y0 F12720",
        "G1 X20 Y0 E0.2 F1560",
        "G1 X20 Y4 E0.05",
        "G1 X0 Y4 E0.2",
        "G1 X0 Y0 E0.05",
        "G1 X40 Y0 F12720",
        "G1 X60 Y0 E0.2 F1560",
        "G1 X60 Y4 E0.05",
        "G1 X40 Y4 E0.2",
        "G1 X40 Y0 E0.05",
        "",
    ])


def _near_closed_tab() -> str:
    """Tab-window outer. Ends miss by 1.2 mm, so the 0.8 mm test called it open.

    1.2 mm is past the 0.7 mm wipe (the old 0.8 mm margin) and inside two
    1 mm nozzles. The path is the loop. A 6 mm opening is not.
    """
    return "\n".join([
        "M83",
        "G90",
        ";LAYER_CHANGE",
        ";Z:24.72",
        "; reduce_crossing_wall = 1",
        "G1 X70 Y1 F1560",
        ";TYPE:Outer wall",
        "G1 X80 Y0.021 F12720",
        "G1 X160 Y0.021 E0.80 F1560",
        "G1 X160 Y2 E0.02",
        "G1 X80 Y2 E0.80",
        "G1 X81.2 Y0.021 E0.02",
        "",
    ])


def _open_wall() -> str:
    """Ends 6 mm apart. That is an opening, not a seam."""
    return "\n".join([
        "M83",
        "G90",
        ";LAYER_CHANGE",
        ";Z:30",
        "; reduce_crossing_wall = 1",
        "G1 X70 Y1 F1560",
        ";TYPE:Outer wall",
        "G1 X80 Y0.021 F12720",
        "G1 X160 Y0.021 E0.80 F1560",
        "G1 X160 Y2 E0.02",
        "G1 X80 Y2 E0.80",
        "G1 X86 Y0.021 E0.02",
        "",
    ])


def _light_heel_loop(last: str = "G1 X80 Y0.021 E0.02") -> str:
    """ApexLight heel route into a closed outer. Retract and prime are 1.5 mm."""
    return "\n".join([
        "M83",
        "G90",
        ";LAYER_CHANGE",
        ";Z:40",
        "; reduce_crossing_wall = 1",
        "G1 X120 Y3 F1560",
        ";PP_HEELROUTE_START",
        "G1 E-1.5 F1800",
        "G1 X80 Y0.021 F12720 ;heel route",
        "G1 E1.5 F1800",
        ";PP_HEELROUTE_END",
        ";TYPE:Outer wall",
        "G1 X160 Y0.021 E0.80 F1560",
        "G1 X160 Y2 E0.02",
        "G1 X80 Y2 E0.80",
        last,
        "",
    ])


def _light_two_islands() -> str:
    """Two closed outers. The hop between them already runs below the belt.

    Shifting that hop up to the dorsal edge is what cut the islands. The M204
    marks the same hop as a chain hop.
    """
    return "\n".join([
        "M83",
        "G90",
        ";LAYER_CHANGE",
        ";Z:40",
        "; reduce_crossing_wall = 1",
        "G1 X20 Y-1 F1560",
        ";PP_HEELROUTE_START",
        "G1 E-1.5 F1800",
        ";TYPE:Outer wall",
        ";C seam travel: lift + direct",
        "G1 Y-0.600 F12720 ;lift",
        "G1 X20.000 Y-0.400 F12720 ;seam travel",
        "G1 Y0.000 F12720 ;lower",
        "G1 E1.5 F1800",
        ";PP_HEELROUTE_END",
        "G1 X40 Y0 E0.20 F1560",
        "G1 X40 Y10 E0.10",
        "G1 X0 Y10 E0.40",
        "G1 X0 Y0 E0.10",
        "G1 X20 Y0 E0.20",
        ";TYPE:Outer wall",
        ";C seam travel: lift + direct",
        "G1 Y-0.400 F12720 ;lift",
        "M204 S10000",
        "G1 X80.000 Y-0.400 F12720 ;seam travel",
        "M204 S8000",
        "G1 Y0.000 F12720 ;lower",
        "G1 X100 Y0 E0.20 F1560",
        "G1 X100 Y10 E0.10",
        "G1 X60 Y10 E0.40",
        "G1 X60 Y0 E0.10",
        "G1 X80 Y0 E0.20",
        "",
    ])


def _light_zipper_y_hop() -> str:
    """Short Y hop at the Jabbour zipper X, ending on the lifted belt seam.

    The hop sits just inside a straight belt edge, so it crosses nothing.
    The dorsal edge is the line y=10 whose midpoint is X 149.786. Moving the
    hop up to that edge (the ec4b4b retarget) makes both ends more than
    0.3 mm from every wall and the segment crosses the top.
    """
    return "\n".join([
        "M83",
        "G90",
        ";LAYER_CHANGE",
        ";Z:40",
        "; reduce_crossing_wall = 1",
        "G1 X149.786 Y0.500 F1560",
        ";PP_HEELROUTE_START",
        "G1 E-1.5 F1800",
        ";TYPE:Outer wall",
        ";C seam travel: lift + direct",
        "G1 X149.786 Y0.422 F12720 ;seam travel",
        "G1 Y0.022 F12720 ;lower",
        "G1 E1.5 F1800",
        ";PP_HEELROUTE_END",
        "G1 X220.000 Y0.022 E0.70214 F1560",
        "G1 X220.000 Y10.000 E0.09978",
        "G1 X79.572 Y10.000 E1.40428",
        "G1 X79.572 Y0.022 E0.09978",
        "G1 X149.786 Y0.022 E0.70214",
        ";C seam travel: lift + direct",
        "G1 Y-1.000 F12720 ;lift",
        "G1 X80.000 Y-1.000 F12720 ;seam travel",
        "",
    ])


def _gapped_corner_with_infill() -> str:
    """Near-closed loop whose opening is not collinear with the first leg.

    Rotating the raw lines drew the first leg from the open end, a chord
    the gap hop crosses. The hop is gap-to-gap, so it is not a seam travel.
    """
    return "\n".join([
        "M83",
        "G90",
        ";LAYER_CHANGE",
        ";Z:27.973",
        "; reduce_crossing_wall = 1",
        "G1 X-2 Y3 F1560",
        ";TYPE:Outer wall",
        "G1 X0 Y0 F12720",
        "G1 X0 Y10 E0.10 F1560",
        "G1 X40 Y10 E0.40",
        "G1 X40 Y0 E0.10",
        "G1 X1.5 Y0.2 E0.40",
        "G1 X8 Y1 F12720",
        ";TYPE:Gap infill",
        "G1 X12 Y1 E0.1",
        "G1 X0.5 Y5 F12720",
        "G1 X5 Y5 E0.2",
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


def _move_bodies(text: str) -> list[str]:
    """G-code commands, comments stripped. Comment-only lines are not moves."""
    bodies = []
    for line in text.splitlines():
        body = line.split(";", 1)[0].strip()
        if body:
            bodies.append(body)
    return bodies


_MAIN_LIGHT = "9e2fe13ccab1e6eee78f223a0135acd47dca4ba4"
_MAIN_LIGHT_FILES = (
    "tools/belt_pp/__init__.py",
    "tools/belt_pp/pipeline.py",
    "tools/belt_pp/apex_light_post_edit.py",
    "scripts/g2b_heelmod/postprocess_g2b.py",
    "scripts/g2b_heelmod/check_travel_wall_crossings.py",
)


def _materialize_main_light() -> Path:
    """Production ApexLight post-edit tree, isolated from this branch's modules."""
    root = Path(tempfile.mkdtemp(prefix="apexlight_main_"))
    for rel in _MAIN_LIGHT_FILES:
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        data = subprocess.check_output(["git", "-C", str(REPO), "show", f"{_MAIN_LIGHT}:{rel}"])
        dest.write_bytes(data)
    return root


def _finalize_in_tree(root: Path, text: str) -> str:
    script = (
        "import sys\n"
        "from pathlib import Path\n"
        "root = Path(sys.argv[1])\n"
        "sys.path.insert(0, str(root))\n"
        "from tools.belt_pp.pipeline import finalize_apex_light\n"
        "sys.stdout.write(finalize_apex_light(sys.stdin.read()))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script, str(root)],
        input=text,
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-2000:])
    return proc.stdout


def _production_analyze(root: Path, text: str) -> dict:
    script = (
        "import json, sys\n"
        "from pathlib import Path\n"
        "root = Path(sys.argv[1])\n"
        "sys.path.insert(0, str(root / 'scripts' / 'g2b_heelmod'))\n"
        "from check_travel_wall_crossings import analyze\n"
        "report = analyze(sys.stdin.read())\n"
        "totals = report['totals']\n"
        "zs = [row['z'] for row in report['layers_with_crossings']]\n"
        "json.dump({\n"
        "  'perimeter_crossings': totals['perimeter_crossings'],\n"
        "  'seam_block_crossings': totals['seam_block_crossings'],\n"
        "  'chain_hop_crossings': totals['chain_hop_crossings'],\n"
        "  'gap_crossings': totals['gap_crossings'],\n"
        "  'reduce_crossing_wall': report['reduce_crossing_wall'],\n"
        "  'z': zs,\n"
        "}, sys.stdout)\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script, str(root)],
        input=text,
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-2000:])
    import json
    return json.loads(proc.stdout)


def _overlap_spur_square() -> str:
    """Closed outer plus a 0.4 mm spur, and a travel that cuts the wall.

    The Core ring walk stops when it returns to the start, so the spur does
    not pull the ring open and the hop can be routed inside. Production follows
    the spur and keeps the straight hop. ApexLight must do the latter.
    """
    return "\n".join([
        "M83",
        "G90",
        ";LAYER_CHANGE",
        ";Z:40",
        "; reduce_crossing_wall = 1",
        "G1 X-2 Y5 F1560",
        ";TYPE:Outer wall",
        "G1 X0 Y0 F12720",
        "G1 X20 Y0 E0.2 F1560",
        "G1 X20 Y10 E0.1",
        "G1 X0 Y10 E0.2",
        "G1 X0 Y0 E0.1",
        "G1 X0.400 Y0 E0.004 ;seam overlap",
        "G1 X10 Y5 F12720",
        ";TYPE:Sparse infill",
        "G1 X11 Y5 E0.01",
        "",
    ])


def _outer_starts(text: str) -> list[tuple[float, float]]:
    """XY where each outer or overhang extrusion begins."""
    x = y = None
    typ = ""
    found = []
    for line in text.splitlines():
        if line.startswith(";TYPE:"):
            typ = line[6:].strip()
        words = _words(line)
        body = line.split(";", 1)[0].strip()
        if (
            typ in ("Outer wall", "Overhang wall")
            and (body.startswith("G0") or body.startswith("G1"))
            and "E" in words
            and float(words["E"]) > 0
            and ("X" in words or "Y" in words)
        ):
            found.append((x, y))
        if "X" in words:
            x = float(words["X"])
        if "Y" in words:
            y = float(words["Y"])
    return found


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

    def test_short_closed_chain_skips_overlap(self) -> None:
        pts = [(20.05, 3.94), (20.05, 4.25), (20.20, 4.10)]
        length = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:]))
        self.assertGreater(length, OVERLAP_MM)
        self.assertLessEqual(length, SHORT_LOOP_MM)
        gap = math.hypot(pts[-1][0] - pts[0][0], pts[-1][1] - pts[0][1])
        self.assertLessEqual(gap, 0.8)
        out, counts = seam(_short_nub_layer(), retract_min_mm=2.0, skip_infill_travel=True, g2b_anchor=True)
        # The two real loops still overlap. The nub does not.
        self.assertEqual(counts["overlap"], 2)
        self.assertNotIn("X20.200 Y4.410", out)
        report = analyze(out)
        self.assertEqual(report["totals"]["perimeter_crossings"], 0)
        self.assertEqual(report["totals"]["seam_block_crossings"], 0)
        self.assertIn("G1 E-0.5 F1800", out)
        self.assertIn("G1 E0.50 F1800", out)

    def test_near_closed_tab_loop_leaves_the_belt(self) -> None:
        self.assertGreater(1.2, 0.8)
        self.assertLessEqual(1.2, CLOSED_GAP_MM)
        out, counts = seam(_near_closed_tab(), retract_min_mm=2.0, skip_infill_travel=True, g2b_anchor=True)
        starts = _seam_starts(out)
        self.assertEqual(len(starts), 1)
        self.assertAlmostEqual(starts[0][0], 120.0, places=2)
        self.assertAlmostEqual(starts[0][1], 2.0, places=2)
        self.assertGreater(starts[0][1], 0.25)
        self.assertEqual(counts["overlap"], 1)
        self.assertIn(";seam overlap", out)
        self.assertIn("G1 E-0.5 F1800", out)
        self.assertIn("G1 E0.50 F1800", out)
        self.assertEqual(analyze(out)["totals"]["perimeter_crossings"], 0)

        opened, open_counts = seam(_open_wall(), retract_min_mm=2.0, skip_infill_travel=True, g2b_anchor=True)
        open_starts = _seam_starts(opened)
        self.assertEqual(len(open_starts), 1)
        self.assertAlmostEqual(open_starts[0][0], 80.0, places=2)
        self.assertAlmostEqual(open_starts[0][1], 0.021, places=2)
        self.assertEqual(open_counts["overlap"], 0)

        flat = "\n".join([
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
            "G1 X1.2 Y0.05 E0.002",
            "",
        ])
        flat_out, _counts = seam(flat, retract_min_mm=2.0, skip_infill_travel=True, g2b_anchor=True)
        flat_start = _seam_starts(flat_out)[0]
        self.assertAlmostEqual(flat_start[0], 15.0, places=2)
        self.assertAlmostEqual(flat_start[1], 0.05, places=2)

    def test_apex_light_keeps_the_production_heel_route(self) -> None:
        for src in (_light_heel_loop(), _light_heel_loop("G1 X81.2 Y0.021 E0.02")):
            out = finalize_apex_light(src)
            self.assertEqual(out.count("G1 E-1.5 F1800"), 1)
            self.assertEqual(out.count("G1 E1.5 F1800"), 1)
            self.assertNotIn("G1 E-0.5", out)
            self.assertNotIn("G1 E0.45", out)
            self.assertNotIn("G1 E0.50", out)
            self.assertIn("G1 X80 Y0.021 F12720 ;heel route", out)
            self.assertNotIn(";seam overlap", out)
            self.assertNotIn("X119.600 Y2.000", out)
            start = _outer_starts(out)[0]
            self.assertAlmostEqual(start[0], 80.0, places=2)
            self.assertAlmostEqual(start[1], 0.021, places=2)
            report = analyze(out)
            self.assertEqual(report["totals"]["perimeter_crossings"], 0)
            self.assertEqual(report["reduce_crossing_wall"], "1")

    def test_light_belt_hop_stays_where_pp_d_wrote_it(self) -> None:
        src = _light_two_islands()
        out = finalize_apex_light(src)
        report = analyze(out)
        self.assertEqual(report["totals"]["perimeter_crossings"], 0)
        self.assertEqual(report["totals"]["seam_block_crossings"], 0)
        self.assertEqual(report["totals"]["chain_hop_crossings"], 0)
        self.assertEqual(report["reduce_crossing_wall"], "1")
        self.assertEqual(out.count("G1 E-1.5 F1800"), 1)
        self.assertEqual(out.count("G1 E1.5 F1800"), 1)
        self.assertNotIn("G1 E-0.5", out)
        self.assertNotIn("G1 E0.50", out)
        self.assertIn("G1 X80.000 Y-0.400 F12720 ;seam travel", out)
        self.assertNotIn("Y10.400", out)
        self.assertNotIn(";seam overlap", out)
        starts = _outer_starts(out)
        self.assertGreaterEqual(len(starts), 2)
        for start in starts[:2]:
            self.assertAlmostEqual(start[1], 0.0, places=2)

    def test_light_zipper_y_hop_is_not_moved(self) -> None:
        src = _light_zipper_y_hop()
        shifted = src.replace(
            "G1 X149.786 Y0.422 F12720 ;seam travel",
            "G1 X149.786 Y10.400 F12720 ;seam travel",
        )
        shifted_report = analyze(shifted)
        self.assertGreater(shifted_report["totals"]["perimeter_crossings"], 0)
        self.assertGreater(shifted_report["totals"]["seam_block_crossings"], 0)
        out = finalize_apex_light(src)
        report = analyze(out)
        self.assertEqual(report["totals"]["perimeter_crossings"], 0)
        self.assertEqual(report["totals"]["seam_block_crossings"], 0)
        self.assertEqual(report["totals"]["chain_hop_crossings"], 0)
        self.assertEqual(report["reduce_crossing_wall"], "1")
        self.assertIn("G1 X149.786 Y0.422 F12720 ;seam travel", out)
        self.assertNotIn("Y10.400", out)
        self.assertIn("G1 E-1.5 F1800", out)
        self.assertIn("G1 E1.5 F1800", out)
        self.assertNotIn("G1 E-0.5", out)
        self.assertNotIn("G1 E0.50", out)
        self.assertNotIn(";seam overlap", out)
        start = _outer_starts(out)[0]
        self.assertAlmostEqual(start[0], 149.786, places=2)
        self.assertAlmostEqual(start[1], 0.022, places=2)

    def test_apex_light_post_edit_matches_production_main(self) -> None:
        main_tree = _materialize_main_light()
        head_tree = REPO
        sources = {
            "heel": _light_heel_loop(),
            "heel_near": _light_heel_loop("G1 X81.2 Y0.021 E0.02"),
            "islands": _light_two_islands(),
            "zipper": _light_zipper_y_hop(),
            "square": _square_hop(),
            "spur": _overlap_spur_square(),
        }
        for name, src in sources.items():
            head = _finalize_in_tree(head_tree, src)
            prod = _finalize_in_tree(main_tree, src)
            self.assertEqual(_move_bodies(head), _move_bodies(prod), name)
            self.assertEqual(head.count("G1 E-1.5 F1800"), prod.count("G1 E-1.5 F1800"), name)
            self.assertEqual(head.count("G1 E1.5 F1800"), prod.count("G1 E1.5 F1800"), name)
            report = _production_analyze(main_tree, head)
            self.assertEqual(report["reduce_crossing_wall"], "1", name)
        for name in ("heel", "heel_near", "islands", "zipper"):
            head = _finalize_in_tree(head_tree, sources[name])
            report = _production_analyze(main_tree, head)
            self.assertEqual(report["perimeter_crossings"], 0, name)
            self.assertEqual(report["z"], [], name)

    def test_near_closed_corner_does_not_add_a_gap_crossing(self) -> None:
        out, counts = seam(
            _gapped_corner_with_infill(),
            retract_min_mm=2.0,
            skip_infill_travel=True,
            g2b_anchor=True,
        )
        starts = _seam_starts(out)
        self.assertGreaterEqual(len(starts), 1)
        self.assertAlmostEqual(starts[0][0], 20.0, places=2)
        self.assertAlmostEqual(starts[0][1], 10.0, places=2)
        self.assertEqual(counts["overlap"], 1)
        self.assertIn("G1 X0.000 Y0.000", out)
        self.assertIn("G1 X0.5 Y5 F12720", out)
        self.assertIn("G1 E-0.5 F1800", out)
        self.assertIn("G1 E0.50 F1800", out)
        report = analyze(out)
        self.assertEqual(report["totals"]["perimeter_crossings"], 0)
        self.assertEqual(report["totals"]["gap_crossings"], 0)
        self.assertEqual(report["totals"]["seam_block_crossings"], 0)
        self.assertEqual(report["reduce_crossing_wall"], "1")

    def test_wall_crossings_stay_zero(self) -> None:
        for src in (
            _insole_section(),
            _square_hop(),
            _flat_leader(),
            _short_nub_layer(),
            _near_closed_tab(),
            _open_wall(),
            _gapped_corner_with_infill(),
        ):
            out, _counts = seam(src, retract_min_mm=2.0, skip_infill_travel=True, g2b_anchor=True)
            report = analyze(out)
            self.assertEqual(report["totals"]["perimeter_crossings"], 0)
            self.assertEqual(report["totals"]["seam_block_crossings"], 0)
        insole, _counts = seam(_insole_section(), retract_min_mm=2.0, skip_infill_travel=True, g2b_anchor=True)
        self.assertEqual(analyze(insole)["reduce_crossing_wall"], "1")
        for src in (
            _light_heel_loop(),
            _light_heel_loop("G1 X81.2 Y0.021 E0.02"),
            _short_nub_layer(),
            _light_two_islands(),
            _light_zipper_y_hop(),
        ):
            out = finalize_apex_light(src)
            report = analyze(out)
            self.assertEqual(report["totals"]["perimeter_crossings"], 0)
            self.assertEqual(report["totals"]["seam_block_crossings"], 0)
            self.assertNotIn("G1 E-0.5", out)
            self.assertNotIn("G1 E0.50", out)
        self.assertEqual(analyze(finalize_apex_light(_light_heel_loop()))["reduce_crossing_wall"], "1")

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

    def test_close_thin_skins_fills_the_slot_without_a_crossing(self) -> None:
        # Two 1.04 mm skins 1.6 mm apart and a 0.30 mm bead between them.
        height = 0.46
        area = math.pi * (0.875 ** 2)
        adj = height * (1.0 - 0.25 * math.pi)

        def e_for(width: float, length: float) -> float:
            return height * (width - adj) * length / area

        src = "\n".join([
            ";LAYER_CHANGE",
            ";Z:1",
            ";TYPE:Outer wall",
            "G1 X0 Y0 F1000",
            f"G1 X10 Y0 E{e_for(1.04, 10):.5f}",
            ";TYPE:Outer wall",
            "G1 X10 Y1.6 F1000",
            f"G1 X0 Y1.6 E{e_for(1.04, 10):.5f}",
            ";TYPE:Gap infill",
            "G1 X0 Y0.8 F1000",
            f"G1 X10 Y0.8 E{e_for(0.30, 10):.5f}",
            "",
        ])
        out, stats = close_thin_skins(src)
        self.assertGreater(stats["widened"], 0)
        gap_es = [
            float(ln.split("E", 1)[1].split()[0])
            for ln in out.splitlines()
            if "Y0.8" in ln.split(";", 1)[0] and "E" in ln.split(";", 1)[0]
        ]
        self.assertGreater(max(gap_es), e_for(0.30, 10) * 1.5)
        self.assertNotIn("F12720", out)
        self.assertEqual(analyze(out)["totals"]["perimeter_crossings"], 0)

        # No bead at all: the fill is extruded, both ways, from a wall end.
        empty = "\n".join([
            ";LAYER_CHANGE",
            ";Z:1",
            ";TYPE:Outer wall",
            "G1 X0 Y0 F1000",
            f"G1 X10 Y0 E{e_for(1.04, 10):.5f}",
            ";TYPE:Outer wall",
            "G1 X10 Y1.6 F1000",
            f"G1 X0 Y1.6 E{e_for(1.04, 10):.5f}",
            "",
        ])
        filled, stats = close_thin_skins(empty)
        self.assertGreater(stats["inserted"], 0)
        self.assertIn(";skin fill", filled)
        self.assertNotIn("F12720", filled)
        self.assertEqual(analyze(filled)["totals"]["perimeter_crossings"], 0)


if __name__ == "__main__":
    unittest.main()
