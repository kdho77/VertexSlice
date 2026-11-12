"""Analyzer checks on synthetic G-code. No slicer binary."""
from __future__ import annotations

import math
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "validation"))

from server.region_infill import (  # noqa: E402
    CORE_Y_MACH_OFFSET_MM,
    analyze_regions,
    verify_sliced_regions,
)
from server.threemf_modifiers import prepare_belt_3mf  # noqa: E402
from tests.test_threemf_modifiers import write_inline_orthocad_3mf, write_modifier_3mf  # noqa: E402

_SQRT2 = math.sqrt(2.0)


def parallel_gcode(*, y_off: float, xs: list[float], mz0: float, mz1: float, model_y: float, layers: int = 4) -> str:
    """Constant-Z extrusions. Gantry Y sweeps thickness; model Y of the midpoint is ``model_y``."""
    lines = ["M83", ";TYPE:Sparse infill"]
    mid = (mz0 + mz1) / 2
    gy0 = mz0 * _SQRT2 + y_off
    gy1 = mz1 * _SQRT2 + y_off
    for i in range(layers):
        gz = (model_y + i * 2) + mid
        for x in xs:
            lines.append(f"G0 X{x:.3f} Y{gy0:.3f} Z{gz:.3f}")
            lines.append(f"G1 X{x:.3f} Y{gy1:.3f} Z{gz:.3f} E0.50")
    return "\n".join(lines) + "\n"


def _xs(box: dict, step: float, margin: float) -> list[float]:
    x = box["xmin"] + margin
    out = []
    while x <= box["xmax"] - margin:
        out.append(round(x, 3))
        x += step
    return out


class AnalyzerTest(unittest.TestCase):
    def test_spacing_recovers_requested_preset(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "model.3mf"
            write_modifier_3mf(src, main=(0, 0, 0, 36, 70, 16), modifier=(0, 40, -1, 36, 68, 18), density="20%")
            _dest, meta = prepare_belt_3mf(src, dest_dir=Path(tmp) / "out")
            part = meta["placed_main_bounds"]
            region = meta["placed_modifiers"][0]
            base = {
                "xmin": part["xmin"],
                "xmax": region["xmin"] - 1,
                "ymin": part["ymin"],
                "ymax": part["ymax"],
                "zmin": part["zmin"],
                "zmax": part["zmax"],
            }
            xs = _xs(base, 4.0, 3.0) + _xs(region, 5.2, 3.0)
            gcode = parallel_gcode(y_off=CORE_Y_MACH_OFFSET_MM, xs=xs, mz0=4, mz1=12, model_y=12)
            report = analyze_regions(
                gcode,
                regions=[region],
                part_bounds=part,
                global_density_pct=26,
                y_off=CORE_Y_MACH_OFFSET_MM,
            )
            self.assertEqual(report["prepared"], 1)
            got = report["regions"][0]
            self.assertTrue(got["verified"], got)
            self.assertAlmostEqual(got["measured_pct"], 20.0, delta=1.0)
            self.assertGreater(got["overlap_fraction"], 0.5)
            self.assertGreaterEqual(got["layers"], 4)
            self.assertLess(got["z_min_mm"], got["z_max_mm"])
            self.assertEqual(got["method"], "spacing")

    def test_unchanged_spacing_is_not_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "model.3mf"
            write_modifier_3mf(src, main=(0, 0, 0, 36, 70, 16), modifier=(0, 40, -1, 36, 68, 18), density="20%")
            _dest, meta = prepare_belt_3mf(src, dest_dir=Path(tmp) / "b")
            part = meta["placed_main_bounds"]
            region = meta["placed_modifiers"][0]
            base = {
                "xmin": part["xmin"],
                "xmax": region["xmin"] - 1,
                "ymin": part["ymin"],
                "ymax": part["ymax"],
                "zmin": part["zmin"],
                "zmax": part["zmax"],
            }
            xs = _xs(base, 4.0, 3.0) + _xs(region, 4.0, 3.0)
            report = verify_sliced_regions(
                src,
                parallel_gcode(y_off=CORE_Y_MACH_OFFSET_MM, xs=xs, mz0=4, mz1=12, model_y=12),
                material="apex_core",
                global_density_pct=26,
            )
            self.assertEqual(report["verified"], 0, report)
            self.assertAlmostEqual(report["regions"][0]["measured_pct"], 26.0, delta=1.0)
            self.assertGreater(report["regions"][0]["overlap_fraction"], 0.5)

    def test_inline_orthocad_pack_places_the_modifier(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "model.3mf"
            write_inline_orthocad_3mf(src, density="20%")
            _dest, meta = prepare_belt_3mf(src, dest_dir=Path(tmp) / "out")
            self.assertEqual(meta["regions_applied"], 1)
            self.assertEqual(meta["modifiers"][0]["sparse_infill_density"], "20%")
            self.assertGreater(meta["placed_modifiers"][0]["xmin"], meta["placed_main_bounds"]["xmin"] + 10)


if __name__ == "__main__":
    unittest.main()
