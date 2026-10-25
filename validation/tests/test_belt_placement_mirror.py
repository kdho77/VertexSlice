#!/usr/bin/env python3
"""Belt STL mirror fix: compensate Orca belt trafo reflection (det −1)."""
from __future__ import annotations

import json
import re
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "validation"))
sys.path.insert(0, str(REPO))

from apex_bed_placement import APEX_INSOLE_X_CENTER_MM  # noqa: E402
from apex_belt_y_trim import APEX_INITIAL_LAYER_PRINT_HEIGHT_MM, write_belt_transform_ini  # noqa: E402
from belt_gcode_gate import GcodeValidator  # noqa: E402
from orca_profile_flatten import flatten_profile  # noqa: E402
from server.profile_registry import get_profile  # noqa: E402
from stl_bed_fit import assert_oriented_extents_match_source, bounds_after_z_rotation, stl_signed_volume  # noqa: E402
from stl_preprocess import _read_triangles, _rotate_z, _signed_volume, prepare_belt_stl  # noqa: E402

MACHINE = REPO / "resources/profiles/IdeaFormer/machine/Vertex Apex V2 1.0 nozzle.json"
BIN = REPO / "build/src/Release/orca-slicer"
BELT_K = 2.0  # 45° belt: Y_model = Z_gcode − Y_gcode * (1 + tan θ)


def _write_asymmetric_wedge(path: Path) -> None:
    """Thin wedge with centroid biased to +X (detectable mirror)."""
    # CCW outward: normal +Z
    tris = [
        ((0.0, 0.0, 0.0), (30.0, 0.0, 0.0), (25.0, 20.0, 0.0)),
        ((0.0, 0.0, 0.0), (25.0, 20.0, 0.0), (0.0, 20.0, 0.0)),
        ((0.0, 0.0, 2.0), (25.0, 20.0, 2.0), (30.0, 0.0, 2.0)),
        ((0.0, 0.0, 2.0), (0.0, 20.0, 2.0), (25.0, 20.0, 2.0)),
        ((0.0, 0.0, 0.0), (0.0, 20.0, 0.0), (0.0, 20.0, 2.0)),
        ((0.0, 0.0, 0.0), (0.0, 20.0, 2.0), (0.0, 0.0, 2.0)),
        ((30.0, 0.0, 0.0), (30.0, 0.0, 2.0), (25.0, 20.0, 2.0)),
        ((30.0, 0.0, 0.0), (25.0, 20.0, 2.0), (25.0, 20.0, 0.0)),
        ((25.0, 20.0, 0.0), (25.0, 20.0, 2.0), (0.0, 20.0, 2.0)),
        ((25.0, 20.0, 0.0), (0.0, 20.0, 2.0), (0.0, 20.0, 0.0)),
    ]
    with path.open("wb") as f:
        header = b"belt mirror test wedge" + b"\0" * (80 - 22)
        f.write(header[:80])
        f.write(struct.pack("<I", len(tris)))
        for verts in tris:
            f.write(struct.pack("<12f", 0.0, 0.0, 1.0, *verts[0], *verts[1], *verts[2]))
            f.write(struct.pack("<H", 0))


def _centroid_x(tris: list[tuple[tuple[float, float, float], ...]]) -> float:
    xs = [p[0] for tri in tris for p in tri]
    return sum(xs) / len(xs)


def _rotate_only_tris(src: Path, rotate_z_deg: int) -> list[tuple[tuple[float, float, float], ...]]:
    out: list[tuple[tuple[float, float, float], ...]] = []
    for tri in _read_triangles(src):
        row = []
        for x, y, z in tri:
            xr, yr = _rotate_z(x, y, rotate_z_deg)
            row.append((xr, yr, z))
        out.append(tuple(row))
    return out


def _orca_binary() -> Path | None:
    import os

    env = os.environ.get("ORCA_SLICER_BIN")
    if env and Path(env).is_file():
        return Path(env)
    if BIN.is_file() and BIN.stat().st_size > 500_000:
        return BIN
    return None


def _extract_config_block(gcode_text: str) -> str:
    lines = gcode_text.splitlines()
    out: list[str] = []
    inside = False
    for line in lines:
        if line.strip() == "; CONFIG_BLOCK_START":
            inside = True
            continue
        if line.strip() == "; CONFIG_BLOCK_END":
            break
        if inside:
            out.append(line)
    return "\n".join(out)


def _gcode_part_points(gcode_text: str) -> list[tuple[float, float, float]]:
    """Map machine XYZ (X across belt, Y height, Z along belt) to prepared STL frame."""
    axis_re = re.compile(r"([XYZ])(-?(?:\d+\.?\d*|\.\d+))")
    x = y = z = 0.0
    layer = -1
    pts: list[tuple[float, float, float]] = []
    for line in gcode_text.splitlines():
        if ";LAYER_CHANGE" in line:
            layer += 1
            continue
        raw = line.split(";")[0].strip()
        if not raw.startswith(("G0", "G1")):
            if raw.startswith("G92"):
                for ax, val in axis_re.findall(raw):
                    if ax == "X":
                        x = float(val)
                    elif ax == "Y":
                        y = float(val)
                    elif ax == "Z":
                        z = float(val)
            continue
        old = (x, y, z)
        has_e = False
        for ax, val in axis_re.findall(raw):
            if ax == "X":
                x = float(val)
            elif ax == "Y":
                y = float(val)
            elif ax == "Z":
                z = float(val)
            elif ax == "E":
                has_e = True
        if layer >= 0 and has_e and (x, y, z) != old:
            y_model = z - y * BELT_K
            pts.append((x, y_model, y))
    return pts


def _chirality_x_bias(tris: list[tuple[tuple[float, float, float], ...]]) -> float:
    return _centroid_x(tris)


def _slice_marker(
    stl: Path,
    *,
    material: str,
    work: Path,
) -> Path:
    profile = get_profile("apex-v2-insole-medium", material=material)
    assert profile is not None
    flat_m = flatten_profile(profile.machine, work / "machine.json")
    flat_p = flatten_profile(profile.process, work / "process.json")
    flat_f = flatten_profile(profile.filament, work / "filament.json")
    proc = json.loads(flat_p.read_text())
    proc["wall_loops"] = "1"
    proc["sparse_infill_density"] = "0%"
    proc["layer_height"] = "0.42"
    flat_p.write_text(json.dumps(proc))

    write_belt_transform_ini(
        work / "belt_transform.ini",
        stl,
        initial_layer_print_height_mm=APEX_INITIAL_LAYER_PRINT_HEIGHT_MM,
        print_direction="toe_first",
    )
    import os

    env = os.environ.copy()
    env["SLIC3R_RESOURCES_DIR"] = str(REPO / "resources")
    env["ORCABELT_TRANSFORM_INI"] = str(work / "belt_transform.ini")
    binary = _orca_binary()
    assert binary is not None
    cmd = [
        str(binary),
        "--slice",
        "1",
        "--allow-newer-file",
        "--arrange",
        "0",
        "--load-settings",
        f"{flat_m};{flat_p}",
        "--load-filaments",
        str(flat_f),
        "--outputdir",
        str(work),
        str(stl),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=900, env=env, errors="replace")
    if r.returncode != 0:
        raise RuntimeError((r.stdout + r.stderr)[-800:])
    gcodes = list(work.glob("**/*.gcode"))
    if not gcodes:
        raise RuntimeError("no gcode produced")
    return gcodes[0]


class BeltMirrorUnitTests(unittest.TestCase):
    def test_mirror_x_meta_and_x_flip_vs_rotate_only(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "wedge.stl"
            _write_asymmetric_wedge(src)
            rot = 270
            rotated = _rotate_only_tris(src, rot)
            cx_rot = _chirality_x_bias(rotated)
            self.assertGreater(cx_rot, 0.1)

            out, meta = prepare_belt_stl(src, rot, bed_width_x_mm=250.0, x_center_mm=APEX_INSOLE_X_CENTER_MM)
            self.assertTrue(meta.get("mirror_x"))
            mirrored_only: list[tuple[tuple[float, float, float], ...]] = []
            for tri in rotated:
                mirrored_only.append(tuple((-x, y, z) for x, y, z in reversed(tri)))
            self.assertLess(_chirality_x_bias(mirrored_only), -0.1)
            self.assertGreater(cx_rot, 0.1)

            fails = assert_oriented_extents_match_source(src, meta)
            self.assertEqual(fails, [])

            expected = bounds_after_z_rotation(src, rot, bed_width_x_mm=250.0)
            self.assertAlmostEqual(meta["oriented_x_across_belt_mm"], expected["oriented_x_across_belt_mm"], places=2)

    def test_winding_stays_outward(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "wedge.stl"
            _write_asymmetric_wedge(src)
            src_vol = _signed_volume(_read_triangles(src))
            self.assertNotAlmostEqual(src_vol, 0.0, places=3)
            out, _meta = prepare_belt_stl(src, 0, bed_width_x_mm=250.0, x_center_mm=100.0)
            prep_vol = stl_signed_volume(out)
            self.assertNotAlmostEqual(prep_vol, 0.0, places=3)
            self.assertGreater(prep_vol * src_vol, 0.0, "mirror+reverse must preserve outward winding sign")
            self.assertAlmostEqual(abs(prep_vol), abs(src_vol), delta=abs(src_vol) * 0.02 + 1.0)


class BeltMirrorSettingsGateTests(unittest.TestCase):
    def test_profile_override_payloads_unchanged(self) -> None:
        """Geometry prep must not alter flattened profile JSON on disk."""
        for material in ("apex_light", "apex_core"):
            profile = get_profile("apex-v2-insole-medium", material=material)
            self.assertIsNotNone(profile)
            with tempfile.TemporaryDirectory() as td:
                work = Path(td)
                flat_m = flatten_profile(profile.machine, work / "m.json")
                flat_p = flatten_profile(profile.process, work / "p.json")
                flat_f = flatten_profile(profile.filament, work / "f.json")
                before = (flat_m.read_text(), flat_p.read_text(), flat_f.read_text())
                _write_asymmetric_wedge(work / "marker.stl")
                prepare_belt_stl(
                    work / "marker.stl",
                    270,
                    bed_width_x_mm=250.0,
                    x_center_mm=APEX_INSOLE_X_CENTER_MM,
                )
                after = (flat_m.read_text(), flat_p.read_text(), flat_f.read_text())
                self.assertEqual(before, after)


class BeltMirrorOrcaGuardTests(unittest.TestCase):
    def test_slice_no_net_reflection_vs_stl(self) -> None:
        binary = _orca_binary()
        if binary is None:
            self.skipTest("orca-slicer binary not available")

        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            src = work / "marker.stl"
            _write_asymmetric_wedge(src)
            prep, meta = prepare_belt_stl(src, 270, bed_width_x_mm=250.0, x_center_mm=APEX_INSOLE_X_CENTER_MM)
            self.assertTrue(meta.get("mirror_x"))
            prep_tris = _read_triangles(prep)
            stl_bias = _chirality_x_bias(prep_tris) - APEX_INSOLE_X_CENTER_MM

            gcode_path = _slice_marker(prep, material="apex_light", work=work / "slice")
            gtext = gcode_path.read_text(encoding="utf-8", errors="replace")
            pts = _gcode_part_points(gtext)
            self.assertGreater(len(pts), 20)
            gc_x = sum(p[0] for p in pts) / len(pts) - APEX_INSOLE_X_CENTER_MM
            if stl_bias * gc_x < 0 and abs(stl_bias) > 0.5 and abs(gc_x) > 0.5:
                self.fail(
                    f"net X reflection: STL centroid bias {stl_bias:.2f} vs gcode {gc_x:.2f} "
                    f"(axis map: part_x=gcode X, part_y=gcode Z−Y*{BELT_K})"
                )

    def test_config_block_unchanged_vs_pre_mirror_geometry(self) -> None:
        """CONFIG_BLOCK byte-identical; total E within 0.01% when only X-mirror prep changes."""
        binary = _orca_binary()
        if binary is None:
            self.skipTest("orca-slicer binary not available")

        from stl_preprocess import _write_triangles  # noqa: E402

        def _legacy_prepare(src: Path, rot: int) -> Path:
            tris = _rotate_only_tris(src, rot)
            xs = [p[0] for tri in tris for p in tri]
            ys = [p[1] for tri in tris for p in tri]
            zs = [p[2] for tri in tris for p in tri]
            min_x, min_y, min_z = min(xs), min(ys), min(zs)
            max_x, max_y, _max_z = max(xs), max(ys), max(zs)
            width = max_x - min_x
            x_shift = APEX_INSOLE_X_CENTER_MM - (width / 2.0) - min_x
            shifted = [
                tuple((x + x_shift, y - min_y, z - min_z) for x, y, z in tri) for tri in tris
            ]
            out = src.parent / f"{src.stem}_legacy{rot}.stl"
            _write_triangles(out, shifted)
            return out

        def _total_positive_e(gtext: str) -> float:
            return sum(float(x) for x in re.findall(r"\bE(-?\d+\.?\d*)", gtext) if float(x) > 0)

        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            src = work / "marker.stl"
            _write_asymmetric_wedge(src)
            rot = 270
            legacy = _legacy_prepare(src, rot)
            mirrored, _ = prepare_belt_stl(src, rot, bed_width_x_mm=250.0, x_center_mm=APEX_INSOLE_X_CENTER_MM)

            for material in ("apex_light", "apex_core"):
                g_legacy = _slice_marker(legacy, material=material, work=work / f"{material}_legacy")
                g_mirror = _slice_marker(mirrored, material=material, work=work / f"{material}_mirror")
                t_legacy = g_legacy.read_text(encoding="utf-8", errors="replace")
                t_mirror = g_mirror.read_text(encoding="utf-8", errors="replace")
                cfg_l = _extract_config_block(t_legacy)
                cfg_m = _extract_config_block(t_mirror)
                self.assertEqual(cfg_l, cfg_m, f"CONFIG_BLOCK differs for {material}")
                e_l = _total_positive_e(t_legacy)
                e_m = _total_positive_e(t_mirror)
                if e_l > 0:
                    self.assertLessEqual(abs(e_m - e_l) / e_l, 1e-4, f"total E drift for {material}")
                v_l = GcodeValidator(str(g_legacy))
                v_m = GcodeValidator(str(g_mirror))
                v_l.parse()
                v_m.parse()
                self.assertEqual(v_l.retract_count, v_m.retract_count)


if __name__ == "__main__":
    unittest.main(verbosity=2)
