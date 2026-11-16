"""Production slice_stl: avoid-crossing-walls stays on and no travel cuts a wall.

Slices the OrthoCAD sample top through every enabled material. Needs the
orca-slicer binary (ORCA_SLICER_BIN or the usual build/image path).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "g2b_heelmod"))

from check_travel_wall_crossings import analyze  # noqa: E402
from server.profile_registry import get_profile, list_materials  # noqa: E402
from server.slice_engine import slice_stl  # noqa: E402

STL = REPO / "validation" / "test_models" / "orthocad_sample_top_insole.stl"
PROFILE = "apex-v2-insole-medium"


def _slicer_present() -> bool:
    try:
        from server.slice_engine import _slicer_binary

        _slicer_binary()
    except FileNotFoundError:
        return False
    return True


@unittest.skipUnless(_slicer_present(), "orca-slicer binary not present")
class ProductionWallCrossings(unittest.TestCase):
    def test_every_enabled_material_has_no_perimeter_crossing(self) -> None:
        self.assertTrue(STL.is_file(), f"missing {STL}")
        materials = [row["id"] for row in list_materials()]
        self.assertEqual(materials, ["apex_core", "apex_forge", "apex_bounce", "apex_light"])
        for material in materials:
            profile = get_profile(PROFILE, material=material)
            self.assertIsNotNone(profile, material)
            gcode, _stats = slice_stl(
                STL,
                profile,
                belt_insole_place=True,
                print_direction="toe_first",
                material=material,
                timeout_s=2400,
            )
            report = analyze(gcode.read_text(encoding="utf-8", errors="replace"))
            self.assertEqual(report["reduce_crossing_wall"], "1", material)
            self.assertEqual(report["totals"]["perimeter_crossings"], 0, material)


if __name__ == "__main__":
    unittest.main()
