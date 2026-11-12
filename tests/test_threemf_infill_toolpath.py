"""Slice a slab 3MF on ApexCore and ApexLight and measure regional infill.

Skipped when orca-slicer is not installed. OrthoCAD's real left/right fixtures
are vendored from kdho77/OrthoCAD- vertex/tests/fixtures/regional-hardness/
when that directory exists; until then this proof uses the same analyzer on a slab.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from server.profile_registry import get_profile  # noqa: E402
from server.region_infill import verify_sliced_regions  # noqa: E402
from server.slice_engine import _slicer_binary, slice_stl  # noqa: E402
from tests.test_threemf_modifiers import write_inline_orthocad_3mf, write_modifier_3mf  # noqa: E402

FIXTURES = REPO / "tests" / "fixtures" / "regional-hardness"


def _binary() -> Path | None:
    env = os.environ.get("ORCA_SLICER_BIN")
    if env and Path(env).is_file():
        return Path(env)
    try:
        return _slicer_binary()
    except FileNotFoundError:
        return None


class ToolpathTest(unittest.TestCase):
    def test_region_density_on_both_chains(self):
        binary = _binary()
        if binary is None:
            self.skipTest("orca-slicer binary not available")
        os.environ["ORCA_SLICER_BIN"] = str(binary)
        os.environ.setdefault("ORCA_RESOURCES", str(REPO / "resources"))
        os.environ.setdefault("APEX_MIN_LAYER_TIME", "4")
        folder = REPO / "tests" / "_toolpath_work"
        if folder.exists():
            import shutil

            shutil.rmtree(folder)
        folder.mkdir()
        report = {}
        cases = [
            ("apex_core", "20%", "component"),
            ("apex_core", "14%", "component"),
            ("apex_core", "20%", "orthocad"),
            ("apex_light", "20%", "component"),
            ("apex_light", "14%", "component"),
        ]
        try:
            for material, density, layout in cases:
                label = f"{material}_{density.rstrip('%')}_{layout}"
                src = folder / f"{label}.3mf"
                if layout == "orthocad":
                    write_inline_orthocad_3mf(src, density=density)
                else:
                    write_modifier_3mf(
                        src,
                        main=(0, 0, 0, 36, 70, 16),
                        modifier=(0, 40, -1, 36, 68, 18),
                        density=density,
                        pattern="gyroid",
                    )
                profile = get_profile("apex-v2-insole-95a", material=material)
                self.assertIsNotNone(profile)
                gcode_path, _stats = slice_stl(
                    src,
                    profile,
                    work_dir=folder / label,
                    timeout_s=900,
                    process_overrides={"sparse_infill_density": "26%"},
                    belt_insole_place=True,
                    print_direction="toe_first",
                    material=material,
                )
                measured = verify_sliced_regions(
                    src,
                    gcode_path.read_text(encoding="utf-8", errors="replace"),
                    material=material,
                    global_density_pct=26,
                    print_direction="toe_first",
                )
                report[label] = measured
                self.assertEqual(measured["prepared"], 1, measured)
                self.assertEqual(measured["verified"], 1, measured)
                region = measured["regions"][0]
                self.assertAlmostEqual(region["measured_pct"], int(density.rstrip("%")), delta=3.5, msg=measured)
                self.assertGreater(region["layers"], 3, measured)
                self.assertGreater(region["overlap_fraction"], 0.5, measured)
            if FIXTURES.is_dir():
                for src in sorted(FIXTURES.glob("*.3mf")):
                    for material in ("apex_core", "apex_light"):
                        label = f"fixture_{src.stem}_{material}"
                        profile = get_profile("apex-v2-insole-95a", material=material)
                        gcode_path, _stats = slice_stl(
                            src,
                            profile,
                            work_dir=folder / label,
                            timeout_s=900,
                            process_overrides={"sparse_infill_density": "26%"},
                            belt_insole_place=True,
                            print_direction="toe_first",
                            material=material,
                        )
                        measured = verify_sliced_regions(
                            src,
                            gcode_path.read_text(encoding="utf-8", errors="replace"),
                            material=material,
                            global_density_pct=26,
                            print_direction="toe_first",
                        )
                        report[label] = measured
                        self.assertGreater(measured["prepared"], 0, measured)
                        self.assertEqual(measured["verified"], measured["prepared"], measured)
        finally:
            print("TOOLPATH", report)
