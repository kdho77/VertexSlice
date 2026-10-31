"""Server production-chain wiring. No slicer binary and no committed G-code."""
from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "g2b_heelmod"))
sys.path.insert(0, str(REPO / "tools" / "belt_pp"))

import check_gcode  # noqa: E402
import efix  # noqa: E402
from server.chains.apex_core import stage_commands  # noqa: E402
from server.chains.apex_light import GOLDEN_E_PO, golden_env  # noqa: E402
from server.profile_registry import get_profile  # noqa: E402
import server.slice_engine as slice_engine  # noqa: E402

# tools/belt_pp/pp_d.py content hash (pp_d_pr17q). The chain must not edit it.
PP_D_SHA256 = "a13dbafec4fea5e1a215142d12618f719b95b7354e6eca08af091236e4da985c"


def _block(direction: str = "toe_first", layer_time: str = "4") -> str:
    return (
        "; CONFIG_BLOCK_START\n"
        f"; print_direction = {direction}\n"
        f"; slow_down_layer_time = {layer_time}\n"
        "; CONFIG_BLOCK_END\n"
    )


class StageOrderTest(unittest.TestCase):
    def test_apex_core_stage_order(self):
        work = Path("/tmp/core-work")
        cmds = stage_commands(work, Path("/in.stl"), work / "final.gcode")
        names = [Path(cmd[1]).name for cmd in cmds]
        self.assertEqual(
            names,
            [
                "slice_apexcore_g2b.py",
                "postprocess_g2b.py",
                "pullback_v2.py",
                "topfix_apexcore.py",
                "check_gcode.py",
            ],
        )
        self.assertEqual(cmds[1][2], "match")
        self.assertEqual(cmds[0][2], "core")

    def test_check_appends_golden(self):
        work = Path("/tmp/core-work")
        cmds = stage_commands(work, Path("/in.stl"), work / "final.gcode", Path("/golden.gcode"))
        self.assertEqual(cmds[-1][-1], "/golden.gcode")


class EfixTest(unittest.TestCase):
    def test_rescales_positive_e_xy(self):
        text = "G1 X0 Y0\nG1 X3 Y4 E1.00000\n"
        out = efix.fix(text)
        den = math.sqrt(3 * 3 + 2 * 4 * 4)
        expect = 1.0 * 5.0 / den
        self.assertIn(f"E{expect:.5f}", out)


class CheckGcodeTest(unittest.TestCase):
    def test_toe_first_passes(self):
        check_gcode.check(_block())

    def test_rejects_other_direction(self):
        with self.assertRaises(SystemExit):
            check_gcode.check(_block(direction="heel_first"))

    def test_config_block_must_match_golden(self):
        with self.assertRaises(SystemExit):
            check_gcode.check(_block(layer_time="4"), _block(layer_time="8"))


class LightEnvTest(unittest.TestCase):
    def test_golden_defaults(self):
        env = golden_env(Path("/in.stl"), print_direction="toe_first")
        self.assertEqual(env["VS_EFIX"], "1")
        self.assertEqual(env["VS_MAT"], "apex_light")
        self.assertEqual(env["VS_FORCE_OFFSET"], "-1.151")
        self.assertEqual(env["VS_WALLS"], "2")
        self.assertEqual(env["VS_FLOWMUL"], "1.088")
        self.assertEqual(env["VS_FLOWMUL_INNER"], "1.083")
        self.assertEqual(env["VS_FLOWMUL_INFILL"], "1.19")
        self.assertEqual(json.loads(env["VS_E_PO"]), GOLDEN_E_PO)
        self.assertNotIn("VS_BELTSLOW", env)
        self.assertNotIn("VS_TOPTIDY", env)

    def test_h_cura_split_is_one_cut(self):
        src = (REPO / "tools" / "belt_pp" / "h_curaD.py").read_text(encoding="utf-8")
        parts = src.split("spec=dataclasses.replace(spec,")
        self.assertEqual(len(parts), 2)
        compile(parts[0], "h_curaD_head", "exec")


class DispatchTest(unittest.TestCase):
    def test_explicit_material_uses_chain(self):
        profile = get_profile("apex-v2-insole-medium", material="apex_core")
        self.assertIsNotNone(profile)
        with tempfile.TemporaryDirectory() as td:
            stl = Path(td) / "in.stl"
            stl.write_bytes(b"solid x\nendsolid x\n")
            out = Path(td) / "out.gcode"
            out.write_text("; chain\n", encoding="utf-8")

            def fake(material, stl_path, prof, work_dir, timeout_s, process_overrides, print_direction):
                self.assertEqual(material, "apex_core")
                self.assertEqual(stl_path, stl.resolve())
                return out, {"chain": "apex_core-g2b-v1", "elapsed_s": 0.0}

            with mock.patch("server.chains.run_material_chain", fake):
                path, stats = slice_engine.slice_stl(stl, profile, material="apex_core")
            self.assertEqual(path, out)
            self.assertEqual(stats["chain"], "apex_core-g2b-v1")

    def test_omitted_material_does_not_dispatch(self):
        profile = get_profile("apex-v2-insole-medium", material="apex_light")

        def boom(*_a, **_k):
            raise AssertionError("chain must not run when material is omitted")

        with mock.patch("server.chains.run_material_chain", boom):
            with self.assertRaises(FileNotFoundError):
                slice_engine.slice_stl(Path("/no/such/model.stl"), profile)

    def test_legacy_hook_is_not_the_profile(self):
        profile = get_profile("apex-v2-insole-medium", material="apex_light")
        self.assertIsNone(profile.gcode_postprocess)

    def test_forced_offset(self):
        old = os.environ.get("VS_FORCE_OFFSET")
        try:
            os.environ["VS_FORCE_OFFSET"] = "-1.151"
            self.assertEqual(slice_engine.forced_belt_y_offset_mm(), -1.151)
            os.environ["VS_FORCE_OFFSET"] = "  "
            self.assertIsNone(slice_engine.forced_belt_y_offset_mm())
        finally:
            if old is None:
                os.environ.pop("VS_FORCE_OFFSET", None)
            else:
                os.environ["VS_FORCE_OFFSET"] = old


class LockedFileTest(unittest.TestCase):
    def test_pp_d_bytes(self):
        digest = hashlib.sha256((REPO / "tools" / "belt_pp" / "pp_d.py").read_bytes()).hexdigest()
        self.assertEqual(digest, PP_D_SHA256)


class ProofMissingInputTest(unittest.TestCase):
    def test_exit_2_without_goldens(self):
        import scripts.golden_chain_proof as proof

        saved = {k: os.environ.get(k) for k in ("VS_GOLDEN_DIR", "VS_CORE_STL", "VS_CORE_GOLDEN", "VS_LIGHT_STL", "VS_LIGHT_GOLDEN")}
        try:
            for key in saved:
                os.environ.pop(key, None)
            self.assertEqual(proof.main(), 2)
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


if __name__ == "__main__":
    unittest.main()
