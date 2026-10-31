"""Server production-chain wiring. No slicer binary and no committed G-code."""
from __future__ import annotations

import hashlib
import json
import math
import os
import struct
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "g2b_heelmod"))
sys.path.insert(0, str(REPO / "tools" / "belt_pp"))

import check_gcode  # noqa: E402
import efix  # noqa: E402
from server.chains import apex_core  # noqa: E402
from server.chains.apex_core import stage_commands  # noqa: E402
from server.chains import apex_light  # noqa: E402
from server.chains.apex_light import GOLDEN_E_PO, golden_env  # noqa: E402
from scripts.pin_orca_tree import sync_pin  # noqa: E402
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

    def test_golden_e_po_is_the_bon_set(self):
        self.assertEqual(
            GOLDEN_E_PO,
            {
                "reduce_crossing_wall": "1",
                "avoid_crossing_wall_fallback": "1",
                "top_shell_layers": "0",
                "top_shell_thickness": "0",
                "wall_loops": "2",
                "wall_generator": "arachne",
                "min_bead_width": "50%",
                "min_feature_size": "25%",
                "wall_transition_length": "100%",
                "wall_distribution_count": "1",
                "minimum_sparse_infill_area": "5",
                "solid_infill_below_area": "2",
                "ensure_vertical_shell_thickness": "none",
                "filter_out_gap_fill": "9999",
                "internal_solid_infill_flow_ratio": "1.45",
            },
        )

    def test_h_cura_split_is_one_cut(self):
        src = (REPO / "tools" / "belt_pp" / "h_curaD.py").read_text(encoding="utf-8")
        parts = src.split("spec=dataclasses.replace(spec,")
        self.assertEqual(len(parts), 2)
        compile(parts[0], "h_curaD_head", "exec")

    def test_box_h_cura_d_head(self):
        src = (REPO / "tools" / "belt_pp" / "h_curaD.py").read_text(encoding="utf-8")
        head = src.split("spec=dataclasses.replace(spec,")[0]
        self.assertNotIn("/app", head)
        self.assertNotIn("/workspace/pr20chk", head)
        for needle in (
            "SET_PRESSURE_ADVANCE ADVANCE=0.05",
            "def strip_micro_layers",
            "'filter_out_gap_fill':'9999'",
            "'wall_generator':'arachne'",
            "'min_bead_width':'50%'",
            "'wall_transition_length':'100%'",
            "'reduce_crossing_wall':'1'",
            "'max_travel_detour_distance':'0'",
            "pp_c.postprocess_C(strip_micro_layers(t),PP)",
            '_old=\'elif l.startswith(";LAYER_CHANGE"):\\n            role = ""\\n            layer += 1\'',
            '\'elif l.startswith(";LAYER_CHANGE"):\\n            layer += 1\'',
            "FLY.first_layer_extrusion_y_bounds=lambda t,*a,**k:_fly(strip_micro_layers(t),*a,**k)",
        ):
            self.assertIn(needle, head)


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


class ChainBinaryMapTest(unittest.TestCase):
    def _isolated(self, extra: dict[str, str] | None = None):
        env = {
            "ORCA_SLICER_BIN": "/app/bin/orca-slicer",
            "ORCA_RESOURCES": "/app/resources",
        }
        if extra:
            env.update(extra)
        return mock.patch.dict(os.environ, env, clear=False)

    def _clear_overrides(self):
        os.environ.pop("ORCA_SLICER_BIN_APEX_CORE", None)
        os.environ.pop("ORCA_SLICER_BIN_APEX_LIGHT", None)

    def test_defaults_map_each_chain_to_its_commit(self):
        with self._isolated():
            self._clear_overrides()
            core = apex_core._env()
            light = golden_env(Path("/in.stl"))
        self.assertEqual(core["ORCA_SLICER_BIN"], "/app/slicers/def8eff480/bin/orca-slicer-def8eff480")
        self.assertEqual(core["ORCA_RESOURCES"], "/app/slicers/def8eff480/resources")
        self.assertEqual(light["ORCA_SLICER_BIN"], "/app/slicers/pr16/bin/orca-slicer-pr16")
        self.assertEqual(light["ORCA_RESOURCES"], "/app/slicers/pr16/resources")
        self.assertNotEqual(core["ORCA_SLICER_BIN"], light["ORCA_SLICER_BIN"])
        self.assertNotEqual(core["ORCA_SLICER_BIN"], "/app/bin/orca-slicer")
        self.assertNotEqual(light["ORCA_SLICER_BIN"], "/app/bin/orca-slicer")

    def test_per_chain_env_overrides_the_binary_and_its_resources(self):
        with self._isolated(
            {
                "ORCA_SLICER_BIN_APEX_CORE": "/opt/core/bin/orca-slicer-def8eff480",
                "ORCA_SLICER_BIN_APEX_LIGHT": "/opt/light/bin/orca-slicer-pr16",
            }
        ):
            core = apex_core._env()
            light = golden_env(Path("/in.stl"))
        self.assertEqual(core["ORCA_SLICER_BIN"], "/opt/core/bin/orca-slicer-def8eff480")
        self.assertEqual(core["ORCA_RESOURCES"], "/opt/core/resources")
        self.assertEqual(light["ORCA_SLICER_BIN"], "/opt/light/bin/orca-slicer-pr16")
        self.assertEqual(light["ORCA_RESOURCES"], "/opt/light/resources")

    def test_blank_override_uses_the_image_default(self):
        with self._isolated(
            {
                "ORCA_SLICER_BIN_APEX_CORE": "  ",
                "ORCA_SLICER_BIN_APEX_LIGHT": "",
            }
        ):
            core = apex_core._env()
            light = golden_env(Path("/in.stl"))
        self.assertEqual(core["ORCA_SLICER_BIN"], apex_core.DEFAULT_APEX_CORE_BIN)
        self.assertEqual(light["ORCA_SLICER_BIN"], apex_light.DEFAULT_APEX_LIGHT_BIN)

    def test_dockerfile_ships_those_binaries_and_env(self):
        text = (REPO / "Dockerfile.server").read_text(encoding="utf-8")
        self.assertIn("def8eff480e3c9baa3560ec44d0aaaee419f1a9e", text)
        self.assertIn("4432a211e62e18487f67ee3f837091c539f0c4f0", text)
        self.assertIn("ORCA_SLICER_BIN_APEX_CORE=/app/slicers/def8eff480/bin/orca-slicer-def8eff480", text)
        self.assertIn("ORCA_SLICER_BIN_APEX_LIGHT=/app/slicers/pr16/bin/orca-slicer-pr16", text)
        self.assertIn(apex_core.DEFAULT_APEX_CORE_BIN, text)
        self.assertIn(apex_light.DEFAULT_APEX_LIGHT_BIN, text)
        self.assertIn("AS deps", text)
        self.assertIn("FROM deps AS slicer-pr16", text)
        self.assertIn("FROM slicer-pr16 AS slicer-core", text)

    def test_run_apex_core_passes_its_binary(self):
        import subprocess

        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            stl = work / "raw.stl"
            tri = struct.pack("<12fH", 0, 0, 1, 0, 0, 0, 10, 0, 1, 0, 20, 2, 0)
            stl.write_bytes(b"\0" * 80 + struct.pack("<I", 1) + tri)
            seen: dict = {}

            def fake_run(cmd, **kwargs):
                seen["env"] = kwargs["env"]
                name = Path(cmd[1]).name
                if name == "slice_apexcore_g2b.py":
                    Path(cmd[5]).write_text("; raw\n", encoding="utf-8")
                elif name == "topfix_apexcore.py":
                    Path(cmd[3]).write_text("; final\n", encoding="utf-8")
                return subprocess.CompletedProcess(cmd, 0, "", "")

            with self._isolated({"ORCA_SLICER_BIN_APEX_CORE": "/opt/core/bin/orca-slicer-def8eff480"}):
                with mock.patch("server.chains.apex_core.subprocess.run", fake_run):
                    _path, stats = apex_core.run_apex_core(stl, work_dir=work / "out")
            self.assertEqual(seen["env"]["ORCA_SLICER_BIN"], "/opt/core/bin/orca-slicer-def8eff480")
            self.assertEqual(seen["env"]["ORCA_RESOURCES"], "/opt/core/resources")
            self.assertEqual(stats["binary"], "/opt/core/bin/orca-slicer-def8eff480")

    def test_run_apex_light_passes_its_binary(self):
        import subprocess

        with tempfile.TemporaryDirectory() as td:
            stl = Path(td) / "in.stl"
            stl.write_text("solid x\nendsolid x\n", encoding="utf-8")
            seen: dict = {}

            def fake_run(cmd, **kwargs):
                seen["env"] = kwargs["env"]
                Path(cmd[-1]).write_text("; gcode\n", encoding="utf-8")
                return subprocess.CompletedProcess(cmd, 0, "", "")

            with self._isolated({"ORCA_SLICER_BIN_APEX_LIGHT": "/opt/light/bin/orca-slicer-pr16"}):
                with mock.patch("server.chains.apex_light.subprocess.run", fake_run):
                    _path, stats = apex_light.run_apex_light(stl, work_dir=Path(td) / "out")
            self.assertEqual(seen["env"]["ORCA_SLICER_BIN"], "/opt/light/bin/orca-slicer-pr16")
            self.assertEqual(seen["env"]["ORCA_RESOURCES"], "/opt/light/resources")
            self.assertEqual(stats["binary"], "/opt/light/bin/orca-slicer-pr16")


class PinOrcaTreeTest(unittest.TestCase):
    def test_sync_updates_only_changed_files_and_keeps_deps(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pin = root / "pin"
            dest = root / "dest"
            for base in (pin, dest):
                (base / "src").mkdir(parents=True)
                (base / "resources" / "profiles").mkdir(parents=True)
                (base / "cmake").mkdir(parents=True)
                (base / "deps" / "build").mkdir(parents=True)
                (base / "src" / "keep.cpp").write_text("same\n", encoding="utf-8")
                (base / "src" / "change.cpp").write_text("old\n", encoding="utf-8")
                (base / "resources" / "profiles" / "a.json").write_text("{}\n", encoding="utf-8")
                (base / "cmake" / "t.cmake").write_text("t\n", encoding="utf-8")
                (base / "CMakeLists.txt").write_text("cmake\n", encoding="utf-8")
                (base / "version.inc").write_text("v\n", encoding="utf-8")
                (base / "build_linux.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            (dest / "deps" / "build" / "lib.a").write_text("deps-bytes", encoding="utf-8")
            (dest / "src" / "extra.cpp").write_text("gone\n", encoding="utf-8")
            keep = dest / "src" / "keep.cpp"
            before = keep.stat().st_mtime_ns
            (pin / "src" / "change.cpp").write_text("new\n", encoding="utf-8")
            (pin / "resources" / "profiles" / "b.json").write_text("{}\n", encoding="utf-8")

            changed = sync_pin(pin, dest)

            self.assertGreater(changed, 0)
            self.assertEqual(keep.stat().st_mtime_ns, before)
            self.assertEqual((dest / "src" / "change.cpp").read_text(encoding="utf-8"), "new\n")
            self.assertFalse((dest / "src" / "extra.cpp").exists())
            self.assertTrue((dest / "resources" / "profiles" / "b.json").is_file())
            self.assertEqual((dest / "deps" / "build" / "lib.a").read_text(encoding="utf-8"), "deps-bytes")
            self.assertEqual(sync_pin(pin, dest), 0)


class LockedFileTest(unittest.TestCase):
    def test_pp_d_bytes(self):
        digest = hashlib.sha256((REPO / "tools" / "belt_pp" / "pp_d.py").read_bytes()).hexdigest()
        self.assertEqual(digest, PP_D_SHA256)


class ApexCorePrepTest(unittest.TestCase):
    def test_belt270_before_slice(self):
        import subprocess

        from server.chains.apex_core import run_apex_core

        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            stl = work / "raw.stl"
            tri = struct.pack("<12fH", 0, 0, 1, 0, 0, 0, 10, 0, 1, 0, 20, 2, 0)
            stl.write_bytes(b"\0" * 80 + struct.pack("<I", 1) + tri)
            seen: dict = {}

            def fake_run(cmd, **_kwargs):
                name = Path(cmd[1]).name
                if name == "slice_apexcore_g2b.py":
                    seen["stl"] = Path(cmd[4])
                    Path(cmd[5]).write_text("; raw\n", encoding="utf-8")
                elif name == "topfix_apexcore.py":
                    Path(cmd[3]).write_text("; final\n", encoding="utf-8")
                return subprocess.CompletedProcess(cmd, 0, "", "")

            with mock.patch("server.chains.apex_core.subprocess.run", fake_run):
                _path, stats = run_apex_core(stl, work_dir=work / "out")
            prepped = seen["stl"]
            self.assertTrue(prepped.is_file())
            self.assertNotEqual(prepped.resolve(), stl.resolve())
            self.assertIn("belt270", prepped.name)
            self.assertEqual(stats["rotation_z_deg"], 270)
            self.assertEqual(stats["x_center_mm"], 140)


class HttpProofClientTest(unittest.TestCase):
    def test_posts_v1_slice(self):
        import scripts.golden_chain_proof as proof

        captured: dict = {}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                captured["path"] = self.path
                captured["body"] = body
                payload = b"; gcode\n"
                self.send_response(200)
                self.send_header("X-Chain", "apex_core-g2b-v1")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, _fmt, *_args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            with tempfile.TemporaryDirectory() as td:
                stl = Path(td) / "in.stl"
                stl.write_bytes(b"solid-bytes")
                dest = Path(td) / "out.gcode"
                chain = proof.post_slice(f"http://127.0.0.1:{port}", "apex_core", stl, dest)
                gcode_text = dest.read_text(encoding="utf-8")
            self.assertEqual(captured["path"], "/v1/slice")
            self.assertIn(b'name="material"', captured["body"])
            self.assertIn(b"apex_core", captured["body"])
            self.assertIn(b'name="profile_id"', captured["body"])
            self.assertIn(b"apex-v2-insole-medium", captured["body"])
            self.assertIn(b'name="print_direction"', captured["body"])
            self.assertIn(b"toe_first", captured["body"])
            self.assertIn(b"solid-bytes", captured["body"])
            self.assertEqual(gcode_text, "; gcode\n")
            self.assertEqual(chain, "apex_core-g2b-v1")
        finally:
            server.shutdown()
            server.server_close()


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
