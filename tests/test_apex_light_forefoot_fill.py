"""ApexLight forefoot hollow fill. No slicer binary.

The fill may only add Cura sparse-infill moves inside a >= 3 mm hollow.
Every other line, including seams and layer-change travels, stays byte-identical.
"""
from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from server.chains.apex_light_forefoot_fill import fill_forefoot_hollow, hollow_columns  # noqa: E402


def _e_for(length: float, width: float = 1.0) -> float:
    area = math.pi * (0.875 ** 2)
    corner = 0.46 * (1.0 - math.pi / 4.0)
    return (width - corner) * 0.46 / area * length


def _loop(pts, z: float, kind: str) -> list[str]:
    lines = [";LAYER_CHANGE\n", f";Z:{z:.5f}\n", f";TYPE:{kind}\n", ";WIDTH:1\n"]
    x0, y0 = pts[0]
    lines.append(f"G1 Z{z:.3f} X{x0:.3f} Y{y0:.3f} F30000\n")
    lines.append(f"G1 X{pts[1][0]:.3f} Y{pts[1][1]:.3f} E{_e_for(math.hypot(pts[1][0] - x0, pts[1][1] - y0)):.5f} F1560\n")
    for (xa, ya), (xb, yb) in zip(pts[1:], pts[2:]):
        lines.append(f"G1 X{xb:.3f} Y{yb:.3f} E{_e_for(math.hypot(xb - xa, yb - ya)):.5f}\n")
    xa, ya = pts[-1]
    lines.append(f"G1 X{x0:.3f} Y{y0:.3f} E{_e_for(math.hypot(x0 - xa, y0 - ya)):.5f}\n")
    return lines


def _program(layers: list[list[str]]) -> str:
    head = [
        "; filament_diameter: 1.75\n",
        "M82 ;absolute extrusion mode\n",
        "M83 ;relative extrusion mode\n",
        "G90\n",
        ";HEIGHT:0.46\n",
    ]
    tail = [
        "G91\n",
        "; filament used [mm] = 10.00\n",
        "; filament used [cm3] = 0.02\n",
        "; filament used [g] = 0.03\n",
    ]
    body: list[str] = []
    for layer in layers:
        body.extend(layer)
    return "".join(head + body + tail)


def _thick() -> list[str]:
    return _loop(
        [(100.0, 0.40), (160.0, 0.40), (160.0, 8.40), (100.0, 8.40)],
        0.92,
        "Outer wall",
    )


def _thin() -> list[str]:
    return _loop(
        [(170.0, 0.40), (190.0, 0.40), (190.0, 2.20), (170.0, 2.20)],
        1.38,
        "Outer wall",
    )


def _subsequence(original: list[str], new: list[str], skip) -> None:
    cursor = 0
    for line in original:
        if skip(line):
            continue
        while cursor < len(new) and new[cursor] != line:
            cursor += 1
        if cursor >= len(new):
            raise AssertionError(f"lost line {line!r}")
        cursor += 1


class ForefootHollowFillTest(unittest.TestCase):
    def test_thick_gap_is_filled_and_every_other_line_stays(self) -> None:
        text = _program([[";LAYER_CHANGE\n", ";Z:0.46000\n", ";keep 0\n"], _thick(), _thin()])
        new, stats = fill_forefoot_hollow(text, last_layer=2)
        self.assertTrue(stats["applied"])
        self.assertGreater(stats["hollow_before"], 0)
        self.assertEqual(stats["hollow_after"], 0)
        _subsequence(
            text.splitlines(keepends=True),
            new.splitlines(keepends=True),
            lambda line: line.startswith("; filament used ["),
        )
        # The thin loop is not a >= 3 mm hollow. No new extrusion lands in it.
        feature = ""
        x = y = None
        for line in new.splitlines():
            if line.startswith(";TYPE:"):
                feature = line[6:].strip()
                continue
            if not line.startswith("G1"):
                continue
            words = {}
            for token in line.split(";", 1)[0].split():
                if token[0] in "XYE":
                    words[token[0]] = float(token[1:])
            if "X" in words:
                x = words["X"]
            if "Y" in words:
                y = words["Y"]
            if feature != "Sparse infill" or "E" not in words or words["E"] <= 0:
                continue
            self.assertIsNotNone(x)
            self.assertLess(x, 165.0, line)

    def test_added_moves_are_sparse_infill_inside_the_gap(self) -> None:
        text = _program([[";LAYER_CHANGE\n", ";Z:0.46000\n"], _thick()])
        new, stats = fill_forefoot_hollow(text, last_layer=1)
        self.assertEqual(stats["hollow_after"], 0)
        feature = ""
        saw = False
        x = y = None
        for line in new.splitlines():
            if line.startswith(";TYPE:"):
                feature = line[6:].strip()
                continue
            if not line.startswith("G1"):
                continue
            words = {}
            for token in line.split(";", 1)[0].split():
                if token[0] in "XYE":
                    words[token[0]] = float(token[1:])
            if "X" in words:
                x = words["X"]
            if "Y" in words:
                y = words["Y"]
            if feature != "Sparse infill" or "E" not in words or words["E"] <= 0:
                continue
            if "X" not in words and "Y" not in words:
                continue
            saw = True
            self.assertGreater(x, 99.5)
            self.assertLess(x, 160.5)
            self.assertGreater(y, 0.9)
            self.assertLess(y, 7.9)
        self.assertTrue(saw)

    def test_layer_change_and_seam_lines_stay_byte_identical(self) -> None:
        text = _program([[";LAYER_CHANGE\n", ";Z:0.46000\n"], _thick()])
        new, _stats = fill_forefoot_hollow(text, last_layer=1)
        def stations(body: str) -> list[str]:
            return [line for line in body.splitlines() if line.startswith("G1 Z") or line.startswith(";LAYER_CHANGE") or line.startswith(";Z:")]
        self.assertEqual(stations(text), stations(new))
        def seams(body: str) -> list[str]:
            out = []
            feature = ""
            for line in body.splitlines():
                if line.startswith(";TYPE:"):
                    feature = line[6:].strip()
                    continue
                if feature == "Outer wall" and line.startswith("G1") and " E" in line:
                    out.append(line)
                    break
            return out
        self.assertEqual(seams(text), seams(new))

    def test_second_pass_adds_nothing(self) -> None:
        text = _program([[";LAYER_CHANGE\n", ";Z:0.46000\n"], _thick()])
        once, stats = fill_forefoot_hollow(text, last_layer=1)
        twice, again = fill_forefoot_hollow(once, last_layer=1)
        self.assertEqual(stats["hollow_after"], 0)
        self.assertFalse(again["applied"])
        self.assertEqual(again["hollow_before"], 0)
        self.assertEqual(once, twice)

    def test_insole_length_file_is_not_this_pass(self) -> None:
        # 95 layers is insole_sample's range. The orthocad defect is the 439-layer file.
        layers = []
        for i in range(95):
            if i == 10:
                layers.append(_thick())
            else:
                layers.append([";LAYER_CHANGE\n", f";Z:{i * 0.46:.5f}\n"])
        text = _program(layers)
        new, stats = fill_forefoot_hollow(text)
        self.assertFalse(stats["applied"])
        self.assertIsNone(stats["last_layer"])
        self.assertEqual(new, text)

    def test_orthocad_length_file_is_filled(self) -> None:
        layers = []
        for i in range(320):
            if i == 40:
                layers.append(_thick())
            else:
                layers.append([";LAYER_CHANGE\n", f";Z:{i * 0.46:.5f}\n"])
        text = _program(layers)
        new, stats = fill_forefoot_hollow(text)
        self.assertTrue(stats["applied"])
        self.assertEqual(stats["last_layer"], 165)
        self.assertEqual(stats["hollow_after"], 0)
        self.assertGreater(stats["hollow_before"], 0)

    def test_empty_file_is_unchanged(self) -> None:
        text = _program([[";LAYER_CHANGE\n", ";Z:0.46000\n", "G1 X1 Y1 F30000\n"]])
        new, stats = fill_forefoot_hollow(text, last_layer=1)
        self.assertFalse(stats["applied"])
        self.assertEqual(new, text)
        self.assertEqual(hollow_columns([], 1), {})

    def test_only_apex_light_calls_it(self) -> None:
        light = (REPO / "server/chains/apex_light.py").read_text()
        core = (REPO / "server/chains/apex_core.py").read_text()
        self.assertIn("fill_forefoot_hollow", light)
        self.assertLess(light.index("finalize_apex_light(text)"), light.index("fill_forefoot_hollow(text)"))
        self.assertNotIn("fill_forefoot_hollow", core)
        self.assertNotIn("apex_light_forefoot_fill", core)


if __name__ == "__main__":
    unittest.main()
