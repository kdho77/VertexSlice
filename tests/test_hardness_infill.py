"""Hardness must change gyroid sparse infill and nothing else in the locked profile.

The stand-in slicer writes the process file it was given into the G-code config
block and a sparse-infill move whose X/Y scale with that density. Orca is not
installed here; the test still drives the real server slice invocation.
"""
from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "g2b_heelmod"))

from server.profile_registry import applied_infill_density  # noqa: E402
from server.slice_engine import SliceTimeoutError  # noqa: E402
from validation.apex_insole_densities import KENDON_INFILL_DENSITY_PRESETS  # noqa: E402

HARDNESS = {
    "extra_soft": ("apex-v2-insole-extra-soft", 14),
    "soft": ("apex-v2-insole-soft", 20),
    "medium": ("apex-v2-insole-medium", 26),
    "hard": ("apex-v2-insole-hard", 32),
    "extra_hard": ("apex-v2-insole-extra-hard", 38),
}

FAKE_SLICER = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    import json, sys
    from pathlib import Path
    args = sys.argv
    settings = outdir = filament = None
    for i, a in enumerate(args):
        if a == "--load-settings":
            settings = args[i + 1]
        elif a == "--load-filaments":
            filament = args[i + 1]
        elif a == "--outputdir":
            outdir = Path(args[i + 1])
    stl = Path(args[-1])
    machine, process = settings.split(";")

    def load(p):
        return json.loads(Path(p).read_text())

    merged = {}
    for blob in (load(machine), load(filament), load(process)):
        for k, v in blob.items():
            if isinstance(v, list):
                v = v[0] if v else ""
            if isinstance(v, str):
                v = v.replace("\\n", "\\\\n")
            merged[k] = v
    lines = ["; CONFIG_BLOCK_START"]
    for k in sorted(merged):
        lines.append(f"; {k} = {merged[k]}")
    lines.append("; CONFIG_BLOCK_END")
    raw = load(process).get("sparse_infill_density", "0")
    if isinstance(raw, list):
        raw = raw[0] if raw else "0"
    d = float(str(raw).rstrip("%"))
    lines += [
        ";LAYER_CHANGE",
        ";TYPE:Sparse infill",
        "G1 X0 Y0 F1000",
        f"G1 X{d:.3f} Y{d / 10:.3f} E1.00000",
        f"G1 X{d:.3f} Y{d:.3f} E0.50000",
    ]
    (outdir / (stl.stem + ".gcode")).write_text("\\n".join(lines) + "\\n")
    """
)


def _scalar(value) -> str:
    if isinstance(value, list):
        return "" if not value else str(value[0])
    return str(value)


def _config(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    on = False
    for line in text.splitlines():
        if line.startswith("; CONFIG_BLOCK_START"):
            on = True
            continue
        if line.startswith("; CONFIG_BLOCK_END"):
            break
        if on and line.startswith("; ") and " = " in line:
            key, val = line[2:].split(" = ", 1)
            out[key] = val
    return out


def _toolpath(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln.startswith(("G0 ", "G1 "))]


def _box_stl(path: Path) -> None:
    tri = struct.pack("<12fH", 0, 0, 1, 0, 0, 0, 40, 0, 0, 0, 100, 5, 0)
    path.write_bytes(b"\0" * 80 + struct.pack("<I", 1) + tri)


def _fake_slicer(path: Path) -> None:
    path.write_text(FAKE_SLICER)
    path.chmod(0o755)


class HardnessMapTest(unittest.TestCase):
    def test_preset_numbers(self):
        self.assertEqual(
            KENDON_INFILL_DENSITY_PRESETS,
            {"Extra Soft": 14, "Soft": 20, "Medium": 26, "Hard": 32, "Extra Hard": 38},
        )

    def test_profile_default_and_explicit_override(self):
        for _name, (pid, pct) in HARDNESS.items():
            self.assertEqual(applied_infill_density(pid, None), f"{pct}%")
        # 95a at 32 is an explicit override on the soft profile.
        self.assertEqual(applied_infill_density("apex-v2-insole-95a", None), "20%")
        self.assertEqual(applied_infill_density("apex-v2-insole-95a", "32"), "32%")
        self.assertEqual(applied_infill_density("apex-v2-insole-soft", "32%"), "32%")


class ApexCoreConfigDiffTest(unittest.TestCase):
    def test_only_sparse_infill_density_moves(self):
        import slice_apexcore_g2b as core

        g2b = json.loads((REPO / "scripts/g2b_heelmod/g2b_config.json").read_text())
        self.assertEqual(len(g2b), 579)
        saved = os.environ.get("VS_SPARSE_INFILL_DENSITY")
        try:
            os.environ.pop("VS_SPARSE_INFILL_DENSITY", None)
            with tempfile.TemporaryDirectory() as td:
                base_dir = Path(td) / "base"
                base_dir.mkdir()
                base_m, base_p, base_f, _ = core.prepare(base_dir, "core")
            base = _snapshot(base_m, base_p, base_f)
            self.assertEqual(_scalar(base_p.get("sparse_infill_density")), "26%")
            self.assertEqual(_scalar(base_p.get("sparse_infill_pattern")), "gyroid")
            for _name, (_pid, pct) in HARDNESS.items():
                os.environ["VS_SPARSE_INFILL_DENSITY"] = f"{pct}%"
                with tempfile.TemporaryDirectory() as td:
                    variant = Path(td) / "v"
                    variant.mkdir()
                    machine, process, filament, _ = core.prepare(variant, "core")
                got = _snapshot(machine, process, filament)
                diff = {
                    k: (base.get(k), got.get(k))
                    for k in set(base) | set(got)
                    if base.get(k) != got.get(k)
                }
                self.assertEqual(
                    diff,
                    {} if pct == 26 else {"process.sparse_infill_density": ("26%", f"{pct}%")},
                    msg=f"{pct}% config diff {diff}",
                )
                for key in g2b:
                    got_val = _found(got, key)
                    self.assertIsNotNone(got_val, msg=key)
                    expected = f"{pct}%" if key == "sparse_infill_density" else _found(base, key)
                    self.assertEqual(got_val, expected, msg=key)
        finally:
            if saved is None:
                os.environ.pop("VS_SPARSE_INFILL_DENSITY", None)
            else:
                os.environ["VS_SPARSE_INFILL_DENSITY"] = saved


def _snapshot(machine, process, filament) -> dict[str, str]:
    out = {}
    for name, prof in ("machine", machine), ("process", process), ("filament", filament):
        for key, value in prof.items():
            out[f"{name}.{key}"] = _scalar(value)
    return out


def _found(snap: dict[str, str], key: str) -> str | None:
    for name in ("process", "machine", "filament"):
        if f"{name}.{key}" in snap:
            return snap[f"{name}.{key}"]
    return None


class GcodeToolpathTest(unittest.TestCase):
    def test_core_and_light_gcode(self):
        paths: dict[str, dict[str, str]] = {}
        configs: dict[str, dict[str, dict[str, str]]] = {}
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fake = root / "orca-slicer"
            _fake_slicer(fake)
            stl = root / "foot.stl"
            _box_stl(stl)
            os.environ["ORCA_SLICER_BIN"] = str(fake)
            try:
                for label, (_pid, pct) in HARDNESS.items():
                    os.environ["VS_SPARSE_INFILL_DENSITY"] = f"{pct}%"
                    core_out = root / f"core_{label}.gcode"
                    proc = subprocess.run(
                        [
                            sys.executable,
                            str(REPO / "scripts/g2b_heelmod/slice_apexcore_g2b.py"),
                            "core",
                            str(root / f"core_{label}"),
                            str(stl),
                            str(core_out),
                        ],
                        capture_output=True,
                        text=True,
                        cwd=str(REPO),
                        env=os.environ.copy(),
                        timeout=120,
                    )
                    self.assertEqual(proc.returncode, 0, proc.stderr[-2000:] + proc.stdout[-2000:])
                    text = core_out.read_text()
                    cfg = _config(text)
                    self.assertEqual(cfg.get("sparse_infill_density"), f"{pct}%")
                    self.assertEqual(cfg.get("sparse_infill_pattern"), "gyroid")
                    paths.setdefault("core", {})[label] = "\n".join(_toolpath(text))
                    configs.setdefault("core", {})[label] = cfg

                    from server.chains.apex_light import run_apex_light

                    light_gcode, _stats = run_apex_light(
                        stl,
                        work_dir=root / f"light_{label}",
                        timeout_s=180,
                        process_overrides={"sparse_infill_density": f"{pct}%", "wall_loops": 2},
                        print_direction="toe_first",
                    )
                    light = light_gcode.read_text(encoding="utf-8", errors="replace")
                    lcfg = _config(light)
                    self.assertEqual(lcfg.get("sparse_infill_density"), f"{pct}%")
                    self.assertEqual(lcfg.get("sparse_infill_pattern"), "gyroid")
                    paths.setdefault("light", {})[label] = "\n".join(_toolpath(light))
                    configs.setdefault("light", {})[label] = lcfg
            finally:
                os.environ.pop("VS_SPARSE_INFILL_DENSITY", None)
                os.environ.pop("ORCA_SLICER_BIN", None)

        for material in ("core", "light"):
            toolpaths = list(paths[material].values())
            self.assertEqual(len(toolpaths), 5)
            self.assertEqual(len(set(toolpaths)), 5, msg=f"{material} toolpaths did not all differ")
            base = configs[material]["medium"]
            for label, cfg in configs[material].items():
                diff = {k: (base.get(k), cfg.get(k)) for k in set(base) | set(cfg) if base.get(k) != cfg.get(k)}
                expected_pct = {name: pct for name, (_pid, pct) in HARDNESS.items()}[label]
                if label == "medium":
                    self.assertEqual(diff, {}, msg=f"{material} medium diff {diff}")
                else:
                    self.assertEqual(
                        diff,
                        {"sparse_infill_density": (base["sparse_infill_density"], f"{expected_pct}%")},
                        msg=f"{material} {label} diff {diff}",
                    )


class ChainPassThroughTest(unittest.TestCase):
    def test_core_env(self):
        from server.chains.apex_core import run_apex_core

        seen = {}

        def fake_run(cmd, **kwargs):
            seen["env"] = kwargs.get("env", {})
            name = Path(cmd[1]).name
            if name == "slice_apexcore_g2b.py":
                Path(cmd[5]).write_text("; raw\n", encoding="utf-8")
            elif name == "topfix_apexcore.py":
                Path(cmd[3]).write_text("; CONFIG_BLOCK_START\n; print_direction = toe_first\n; slow_down_layer_time = 4\n; CONFIG_BLOCK_END\n", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with tempfile.TemporaryDirectory() as td:
            stl = Path(td) / "in.stl"
            _box_stl(stl)
            with unittest.mock.patch("server.chains.apex_core.subprocess.run", fake_run):
                run_apex_core(
                    stl,
                    work_dir=Path(td) / "out",
                    timeout_s=30,
                    process_overrides={"sparse_infill_density": "32%"},
                )
        self.assertEqual(seen["env"].get("VS_SPARSE_INFILL_DENSITY"), "32%")

    def test_light_timeout_is_a_clear_error(self):
        from server.chains.apex_light import run_apex_light

        def fake_run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 0))

        with tempfile.TemporaryDirectory() as td:
            stl = Path(td) / "in.stl"
            _box_stl(stl)
            with unittest.mock.patch("server.chains.apex_light.subprocess.run", fake_run):
                with self.assertRaises(SliceTimeoutError) as caught:
                    run_apex_light(
                        stl,
                        work_dir=Path(td) / "out",
                        timeout_s=260,
                        process_overrides={"sparse_infill_density": "22%"},
                    )
        message = str(caught.exception)
        self.assertIn("260", message)
        self.assertIn("270", message)
        self.assertIn("499", message)
        self.assertIn("No G-code", message)


import unittest.mock  # noqa: E402


# Absolute print speeds the Core g2b profile sets, and the 0.8× Bounce values.
CORE_PRINT_SPEEDS = {
    "outer_wall_speed": "26",
    "inner_wall_speed": "42",
    "sparse_infill_speed": "26",
    "internal_solid_infill_speed": "26",
    "top_surface_speed": "26",
    "initial_layer_speed": "10",
    "initial_layer_infill_speed": "60",
    "gap_infill_speed": "26",
    "bridge_speed": "25",
    "ironing_speed": "20",
    "skirt_speed": "50",
    "support_speed": "40",
    "support_interface_speed": "80",
}
BOUNCE_PRINT_SPEEDS = {
    "outer_wall_speed": "20.8",
    "inner_wall_speed": "33.6",
    "sparse_infill_speed": "20.8",
    "internal_solid_infill_speed": "20.8",
    "top_surface_speed": "20.8",
    "initial_layer_speed": "8",
    "initial_layer_infill_speed": "48",
    "gap_infill_speed": "20.8",
    "bridge_speed": "20",
    "ironing_speed": "16",
    "skirt_speed": "40",
    "support_speed": "32",
    "support_interface_speed": "64",
}
# Left alone: travel, percentage speeds, accel, retract, fan, flow, line width.
UNSCALED = {
    "travel_speed": "212",
    "travel_speed_z": "0",
    "initial_layer_travel_speed": "100%",
    "internal_bridge_speed": "150%",
    "small_perimeter_speed": "50%",
    "scarf_joint_speed": "100%",
    "wipe_speed": "60%",
    "overhang_1_4_speed": "0",
    "overhang_4_4_speed": "0",
    "default_acceleration": "8000",
    "outer_wall_acceleration": "8000",
    "sparse_infill_acceleration": "8000",
    "travel_acceleration": "10000",
    "bridge_acceleration": "50%",
    "retraction_speed": "30",
    "deretraction_speed": "30",
    "filament_retraction_speed": "30",
    "filament_deretraction_speed": "30",
    "fan_min_speed": "50",
    "fan_max_speed": "50",
    "additional_cooling_fan_speed": "25",
    "slow_down_min_speed": "10",
    "outer_wall_flow_ratio": "1.0142",
    "sparse_infill_flow_ratio": "1.0138",
    "line_width": "1",
    "initial_layer_line_width": "1",
    "outer_wall_line_width": "1",
    "inner_wall_line_width": "1",
    "sparse_infill_line_width": "1",
    "internal_solid_infill_line_width": "1",
    "top_surface_line_width": "1",
    "sparse_infill_pattern": "gyroid",
    "nozzle_temperature_range_low": "220",
    "nozzle_temperature_range_high": "230",
}


class BounceCloneTest(unittest.TestCase):
    def test_hard_gyroid_and_scaled_speeds(self):
        import slice_apexcore_g2b as core
        from server.chains.apex_core import bounce_after_lock
        from apex_material_temps import render_expected_start_gcode

        saved = {
            "VS_SPARSE_INFILL_DENSITY": os.environ.get("VS_SPARSE_INFILL_DENSITY"),
            "VS_AFTER_LOCK_JSON": os.environ.get("VS_AFTER_LOCK_JSON"),
        }
        try:
            os.environ["VS_SPARSE_INFILL_DENSITY"] = "32%"
            os.environ.pop("VS_AFTER_LOCK_JSON", None)
            with tempfile.TemporaryDirectory() as td:
                core_m, core_p, core_f, _ = core.prepare(Path(td), "core")
            core_snap = _snapshot(core_m, core_p, core_f)
            os.environ["VS_AFTER_LOCK_JSON"] = json.dumps(bounce_after_lock())
            with tempfile.TemporaryDirectory() as td:
                bounce_m, bounce_p, bounce_f, _ = core.prepare(Path(td), "core")
            bounce_snap = _snapshot(bounce_m, bounce_p, bounce_f)
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        for key, expect in CORE_PRINT_SPEEDS.items():
            self.assertEqual(core_snap[f"process.{key}"], expect, msg=key)
        for key, expect in BOUNCE_PRINT_SPEEDS.items():
            self.assertEqual(bounce_snap[f"process.{key}"], expect, msg=key)
        for key, expect in UNSCALED.items():
            self.assertEqual(core_snap.get(f"process.{key}") or core_snap.get(f"filament.{key}") or core_snap.get(f"machine.{key}"), expect, msg=f"core {key}")
            self.assertEqual(
                bounce_snap.get(f"process.{key}") or bounce_snap.get(f"filament.{key}") or bounce_snap.get(f"machine.{key}"),
                expect,
                msg=f"bounce {key}",
            )

        self.assertEqual(core_snap["filament.nozzle_temperature"], "225")
        self.assertEqual(core_snap["filament.nozzle_temperature_initial_layer"], "225")
        self.assertEqual(core_snap["process.first_layer_temperature"], "260")
        self.assertEqual(core_snap["filament.cool_plate_temp"], "0")
        self.assertEqual(core_snap["filament.cool_plate_temp_initial_layer"], "0")
        self.assertEqual(bounce_snap["filament.nozzle_temperature"], "220")
        self.assertEqual(bounce_snap["filament.nozzle_temperature_initial_layer"], "220")
        self.assertEqual(bounce_snap["process.first_layer_temperature"], "220")
        for bed_key in (
            "cool_plate_temp",
            "cool_plate_temp_initial_layer",
            "hot_plate_temp",
            "hot_plate_temp_initial_layer",
            "eng_plate_temp",
            "eng_plate_temp_initial_layer",
            "textured_plate_temp",
            "textured_plate_temp_initial_layer",
        ):
            self.assertEqual(bounce_snap[f"filament.{bed_key}"], "70", msg=bed_key)
        for bed_key in (
            "first_layer_bed_temperature",
            "supertack_plate_temp",
            "supertack_plate_temp_initial_layer",
            "textured_cool_plate_temp",
            "textured_cool_plate_temp_initial_layer",
        ):
            self.assertEqual(bounce_snap[f"process.{bed_key}"], "70", msg=bed_key)

        self.assertEqual(bounce_snap["process.sparse_infill_density"], "32%")
        self.assertEqual(bounce_snap["process.sparse_infill_pattern"], "gyroid")
        self.assertEqual(float(bounce_snap["process.sparse_infill_line_width"]), 1.0)
        self.assertEqual(float(bounce_snap["process.line_width"]), 1.0)
        self.assertEqual(bounce_snap["process.wall_loops"], core_snap["process.wall_loops"])

        changed = {
            key: (core_snap.get(key), bounce_snap.get(key))
            for key in set(core_snap) | set(bounce_snap)
            if core_snap.get(key) != bounce_snap.get(key)
        }
        expected_changed = {f"process.{key}" for key in BOUNCE_PRINT_SPEEDS}
        expected_changed.add("filament.nozzle_temperature")
        expected_changed.add("filament.nozzle_temperature_initial_layer")
        expected_changed.add("process.first_layer_temperature")
        expected_changed.update(
            f"filament.{key}"
            for key in (
                "cool_plate_temp",
                "cool_plate_temp_initial_layer",
                "hot_plate_temp",
                "hot_plate_temp_initial_layer",
                "eng_plate_temp",
                "eng_plate_temp_initial_layer",
                "textured_plate_temp",
                "textured_plate_temp_initial_layer",
            )
        )
        expected_changed.update(
            f"process.{key}"
            for key in (
                "first_layer_bed_temperature",
                "supertack_plate_temp",
                "supertack_plate_temp_initial_layer",
                "textured_cool_plate_temp",
                "textured_cool_plate_temp_initial_layer",
            )
        )
        self.assertEqual(set(changed), expected_changed, msg=f"unexpected profile diff {changed}")

        start = bounce_m["machine_start_gcode"]
        rendered = render_expected_start_gcode(start, 220, 70, _scalar(bounce_m.get("gcode_flavor")))
        self.assertIn("PRINT_START\n", rendered)
        self.assertIn("M104 S220", rendered)
        self.assertIn("M109 S220", rendered)
        self.assertIn("M190 S70", rendered)
        self.assertNotIn("M104 S225", rendered)
        self.assertNotIn("M190 S0", rendered)
        self.assertNotIn("M190 S60", rendered)

    def test_bounce_gcode_header_from_fake_slicer(self):
        import slice_apexcore_g2b as core
        from server.chains.apex_core import bounce_after_lock

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fake = root / "orca-slicer"
            _fake_slicer(fake)
            stl = root / "foot.stl"
            _box_stl(stl)
            saved_bin = os.environ.get("ORCA_SLICER_BIN")
            saved_density = os.environ.get("VS_SPARSE_INFILL_DENSITY")
            saved_lock = os.environ.get("VS_AFTER_LOCK_JSON")
            os.environ["ORCA_SLICER_BIN"] = str(fake)
            os.environ["VS_SPARSE_INFILL_DENSITY"] = "32%"
            os.environ["VS_AFTER_LOCK_JSON"] = json.dumps(bounce_after_lock())
            try:
                dest = root / "bounce.gcode"
                proc = subprocess.run(
                    [
                        sys.executable,
                        str(REPO / "scripts/g2b_heelmod/slice_apexcore_g2b.py"),
                        "core",
                        str(root / "work"),
                        str(stl),
                        str(dest),
                    ],
                    capture_output=True,
                    text=True,
                    cwd=str(REPO),
                    env=os.environ.copy(),
                    timeout=120,
                )
                self.assertEqual(proc.returncode, 0, proc.stderr[-2000:] + proc.stdout[-2000:])
                cfg = _config(dest.read_text())
            finally:
                if saved_bin is None:
                    os.environ.pop("ORCA_SLICER_BIN", None)
                else:
                    os.environ["ORCA_SLICER_BIN"] = saved_bin
                if saved_density is None:
                    os.environ.pop("VS_SPARSE_INFILL_DENSITY", None)
                else:
                    os.environ["VS_SPARSE_INFILL_DENSITY"] = saved_density
                if saved_lock is None:
                    os.environ.pop("VS_AFTER_LOCK_JSON", None)
                else:
                    os.environ["VS_AFTER_LOCK_JSON"] = saved_lock

        self.assertEqual(cfg.get("sparse_infill_density"), "32%")
        self.assertEqual(cfg.get("sparse_infill_pattern"), "gyroid")
        self.assertEqual(float(cfg.get("sparse_infill_line_width", "0")), 1.0)
        self.assertEqual(float(cfg.get("line_width", "0")), 1.0)
        self.assertEqual(cfg.get("nozzle_temperature"), "220")
        self.assertEqual(cfg.get("nozzle_temperature_initial_layer"), "220")
        self.assertEqual(cfg.get("cool_plate_temp_initial_layer"), "70")
        self.assertEqual(cfg.get("hot_plate_temp_initial_layer"), "70")
        self.assertEqual(cfg.get("first_layer_bed_temperature"), "70")
        self.assertEqual(cfg.get("travel_speed"), "212")
        for key, expect in BOUNCE_PRINT_SPEEDS.items():
            self.assertEqual(cfg.get(key), expect, msg=key)
        # Same argv the chain uses. Importing prepare must still be the Core material.
        self.assertTrue(callable(core.prepare))


if __name__ == "__main__":
    unittest.main()
