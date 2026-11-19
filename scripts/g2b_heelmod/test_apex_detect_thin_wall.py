#!/usr/bin/env python3
"""detect_thin_wall on Apex Light, Forge, and Bounce stays at the pre-#27 value.

#27 (c3510806d2), #28 (3dfc95abb8), and #29 (3717053d3c) did not write
detect_thin_wall on these three profiles. The value they slice with is the
IdeaFormer common process, set to 0 in f1b79c4779 and still 0. ApexCore is
the profile #29 turned on; that key is covered by test_apexcore_95a_locked.

reduce_crossing_wall stays 1. Light already had it (validation/apex_light.py
and the golden chain). Forge and Bounce got it on the shared insole process
in #28.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PROFILES = REPO / "resources" / "profiles" / "IdeaFormer"
sys.path[:0] = [str(HERE), str(REPO), str(REPO / "validation"), str(REPO / "server")]

# Last commit that set this key: f1b79c4779, on fdm_process_common.json.
# Unchanged from the parent of #27 (c3510806d2^) through #30 (9e2fe13cca).
PRE_27_DETECT_THIN_WALL = {
    "apex_light": "0",
    "apex_forge": "0",
    "apex_bounce": "0",
}


def _scalar(value) -> str:
    if isinstance(value, list):
        return "" if not value else str(value[0])
    return str(value)


def _flat_process(name: str, work: Path) -> dict:
    from orca_profile_flatten import flatten_profile

    dest = work / (Path(name).name + ".flat.json")
    return json.loads(flatten_profile(PROFILES / "process" / name, dest).read_text())


class OtherApexDetectThinWall(unittest.TestCase):
    def test_common_process_is_still_the_pre_27_value(self) -> None:
        common = json.loads((PROFILES / "process" / "fdm_process_common.json").read_text())
        apex = json.loads((PROFILES / "process" / "fdm_process_apex_insole_common.json").read_text())
        self.assertEqual(common["detect_thin_wall"], "0")
        self.assertEqual(set(PRE_27_DETECT_THIN_WALL.values()), {"0"})
        self.assertNotIn("detect_thin_wall", apex)
        self.assertEqual(apex["reduce_crossing_wall"], "1")
        for name in (
            "0.42mm TPU Insole @Vertex Apex V2.json",
            "0.42mm TPU Insole Extra Soft @Vertex Apex V2.json",
            "0.42mm TPU Insole Medium @Vertex Apex V2.json",
            "0.42mm TPU Insole Hard @Vertex Apex V2.json",
            "0.42mm TPU Insole Extra Hard @Vertex Apex V2.json",
        ):
            child = json.loads((PROFILES / "process" / name).read_text())
            self.assertNotIn("detect_thin_wall", child, name)
            self.assertNotIn("reduce_crossing_wall", child, name)
        for name in ("ApexLight.json", "ApexForge.json", "ApexBounce.json"):
            filament = json.loads((PROFILES / "filament" / name).read_text())
            self.assertNotIn("detect_thin_wall", filament, name)
            self.assertNotIn("reduce_crossing_wall", filament, name)

    def test_forge_and_bounce_resolve_pre_27_and_keep_reduce_crossing_wall(self) -> None:
        from server.profile_registry import get_profile

        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            for material in ("apex_forge", "apex_bounce"):
                profile = get_profile("apex-v2-insole-medium", material=material)
                self.assertIsNotNone(profile, material)
                self.assertEqual(profile.process_overrides, {}, material)
                process = _flat_process(profile.process.name, work)
                self.assertEqual(
                    _scalar(process["detect_thin_wall"]),
                    PRE_27_DETECT_THIN_WALL[material],
                    material,
                )
                self.assertEqual(_scalar(process["reduce_crossing_wall"]), "1", material)

    def test_light_resolve_pre_27_and_keep_reduce_crossing_wall(self) -> None:
        from server.chains.apex_light import GOLDEN_E_PO
        from server.profile_registry import get_profile
        from slice_apexcore_g2b import prepare
        from validation.apex_light import PROCESS_OVERRIDES

        self.assertNotIn("detect_thin_wall", PROCESS_OVERRIDES)
        self.assertEqual(PROCESS_OVERRIDES["reduce_crossing_wall"], "1")
        self.assertNotIn("detect_thin_wall", GOLDEN_E_PO)
        self.assertEqual(GOLDEN_E_PO["reduce_crossing_wall"], "1")

        # h_curaD's common fixes, then the golden chain env. Neither writes
        # detect_thin_wall, so the flattened process value is what is sliced.
        po = dict(PROCESS_OVERRIDES)
        po.update(
            {
                "filter_out_gap_fill": "9999",
                "wall_generator": "arachne",
                "min_bead_width": "50%",
                "wall_transition_length": "100%",
                "reduce_crossing_wall": "1",
                "max_travel_detour_distance": "0",
            }
        )
        po.update(GOLDEN_E_PO)
        self.assertNotIn("detect_thin_wall", po)
        self.assertEqual(po["reduce_crossing_wall"], "1")

        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            profile = get_profile("apex-v2-insole-medium", material="apex_light")
            self.assertIsNotNone(profile)
            process = _flat_process(profile.process.name, work)
            resolved = dict(process)
            resolved.update(po)
            self.assertEqual(
                _scalar(resolved["detect_thin_wall"]),
                PRE_27_DETECT_THIN_WALL["apex_light"],
            )
            self.assertEqual(_scalar(resolved["reduce_crossing_wall"]), "1")

            # G2b light path. The shared overlay's detect_thin_wall is 0, the
            # same value g2b_config had before #27. The forefoot stitch is
            # ApexCore only.
            _machine, g2b_process, _filament, _y = prepare(work, "light")
            self.assertEqual(_scalar(g2b_process["detect_thin_wall"]), PRE_27_DETECT_THIN_WALL["apex_light"])
            self.assertEqual(_scalar(g2b_process["reduce_crossing_wall"]), "1")
            self.assertNotEqual(_scalar(g2b_process["wall_generator"]), "classic")
            # ApexLight stays on the shared G2b shells. The forefoot stitch
            # (ensure_all, one top shell) is ApexCore only. Walls stay 1 mm.
            self.assertEqual(_scalar(g2b_process["outer_wall_line_width"]), "1")
            self.assertEqual(_scalar(g2b_process["inner_wall_line_width"]), "1")
            self.assertEqual(_scalar(g2b_process["ensure_vertical_shell_thickness"]), "none")
            self.assertEqual(_scalar(g2b_process["top_shell_layers"]), "0")


if __name__ == "__main__":
    unittest.main()
