#!/usr/bin/env python3
"""ApexCore / ApexForge / ApexBounce / ApexLight / ApexVylon materials: temps, API, PRINT_START call line."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "validation"))

from apex_material_temps import (  # noqa: E402
    MACHINE,
    assert_material_temps_in_gcode,
    filament_temps,
    render_expected_start_gcode,
    substitute_print_start,
)
from orca_profile_flatten import flatten_profile  # noqa: E402
from server.profile_registry import (  # noqa: E402
    DEFAULT_MATERIAL_ID,
    FILAMENT_95A,
    get_profile,
    list_materials,
    resolve_material,
)

PROCESS = REPO / "resources/profiles/IdeaFormer/process/0.42mm TPU Insole @Vertex Apex V2.json"
MODEL = REPO / "validation/test_models/insole_sample.stl"
VENDOR = REPO / "resources/profiles/IdeaFormer.json"

SKIP_FLAT_KEYS = {
    "name",
    "setting_id",
    "filament_id",
    "filament_notes",
    "filament_settings_id",
    "compatible_printers",
}


def _binary() -> Path | None:
    for p in (
        Path("/app/bin/orca-slicer"),
        REPO / "build/src/Release/orca-slicer",
        REPO / "build/src/Debug/orca-slicer",
        REPO / "build/src/orca-slicer",
    ):
        if p.is_file() and p.stat().st_size > 500_000:
            return p
    return None


def _flatten(path: Path) -> dict:
    flat = flatten_profile(path)
    data = json.loads(flat.read_text())
    flat.unlink(missing_ok=True)
    return data


def test_registry_and_api_contract() -> list[str]:
    fails: list[str] = []
    mats = {m["id"]: m for m in list_materials()}
    expect = {
        "apex_core": ("ApexCore", 225, 0),
        "apex_forge": ("ApexForge", 245, 0),
        "apex_bounce": ("ApexBounce", 225, 60),
        "apex_light": ("ApexLight", 260, 0),
        "apex_vylon": ("ApexVylon", 245, 60),
    }
    if set(mats) != set(expect):
        fails.append(f"material ids {sorted(mats)} != {sorted(expect)}")
    for mid, (name, nozzle, bed) in expect.items():
        row = mats.get(mid) or {}
        if row.get("display_name") != name:
            fails.append(f"{mid} display_name {row.get('display_name')!r}")
        if row.get("nozzle") != nozzle or row.get("bed") != bed:
            fails.append(f"{mid} temps {row.get('nozzle')}/{row.get('bed')} != {nozzle}/{bed}")
        spec = resolve_material(mid)
        if not spec.filament.is_file():
            fails.append(f"{mid} missing filament {spec.filament}")
    if resolve_material(None).material_id != DEFAULT_MATERIAL_ID:
        fails.append("omitted material is not apex_core")
    if resolve_material("").material_id != "apex_core":
        fails.append("empty material is not apex_core")
    if resolve_material("APEX_CORE").material_id != "apex_core":
        fails.append("case-insensitive apex_core")
    try:
        resolve_material("pla")
        fails.append("pla should be rejected")
    except ValueError:
        pass
    try:
        resolve_material("apex-core")
        fails.append("hyphenated apex-core should be rejected")
    except ValueError:
        pass
    default = get_profile("apex-v2-insole-95a")
    core = get_profile("apex-v2-insole-95a", material="apex_core")
    if default is None or core is None:
        fails.append("missing default profile")
    elif default.filament != FILAMENT_95A or core.filament != FILAMENT_95A:
        fails.append("default/apex_core must use VERTEX_APEX_CORE_95A.json")
    forge = get_profile("apex-v2-insole-95a", material="apex_forge")
    if forge is None or forge.filament.name != "ApexForge.json":
        fails.append("apex_forge did not select ApexForge.json")
    light = get_profile("apex-v2-insole-95a", material="apex_light")
    if light is None or light.filament.name != "ApexLight.json":
        fails.append("apex_light did not select ApexLight.json")
    elif not light.process_overrides or light.process_overrides.get("wall_loops") != "2":
        fails.append("apex_light missing 2-wall process override")
    return fails


def test_http_400_and_materials_endpoint() -> list[str]:
    fails: list[str] = []
    try:
        from fastapi.testclient import TestClient
        from server.app import app
    except Exception as exc:
        print(f"  SKIP HTTP TestClient ({exc})")
        return fails
    client = TestClient(app)
    r = client.get("/v1/materials")
    if r.status_code != 200:
        fails.append(f"GET /v1/materials -> {r.status_code}")
        return fails
    body = r.json()
    if body.get("default") != "apex_core":
        fails.append(f"GET /v1/materials default {body.get('default')!r}")
    ids = [m["id"] for m in body.get("materials") or []]
    if ids != ["apex_core", "apex_forge", "apex_bounce", "apex_light", "apex_vylon"]:
        fails.append(f"GET /v1/materials ids {ids}")
    bad = client.post(
        "/v1/slice",
        data={"profile_id": "apex-v2-insole-95a", "material": "not_a_filament"},
        files={"model": ("x.stl", b"solid x\nendsolid x\n", "model/stl")},
    )
    if bad.status_code != 400:
        fails.append(f"bad material HTTP {bad.status_code} != 400 body={bad.text[:200]}")
    return fails


def test_builtin_presets_registered() -> list[str]:
    fails: list[str] = []
    vendor = json.loads(VENDOR.read_text())
    names = {e["name"] for e in vendor.get("filament_list") or []}
    for name in ("ApexCore", "ApexForge", "ApexBounce", "ApexLight", "ApexVylon"):
        if name not in names:
            fails.append(f"{name} not in IdeaFormer.json filament_list")
        path = REPO / "resources/profiles/IdeaFormer/filament" / f"{name}.json"
        if not path.is_file():
            fails.append(f"missing {path}")
    model = json.loads((REPO / "resources/profiles/IdeaFormer/machine/Vertex Apex V2.json").read_text())
    mats = model.get("default_materials") or ""
    for name in ("ApexCore", "ApexForge", "ApexBounce", "ApexLight", "ApexVylon"):
        if name not in mats:
            fails.append(f"{name} missing from Vertex Apex V2 default_materials")
    return fails


def test_profile_temps_and_print_start() -> list[str]:
    fails: list[str] = []
    machine = _flatten(MACHINE)
    start = machine.get("machine_start_gcode") or ""
    if not start.startswith("PRINT_START\n"):
        fails.append("machine_start_gcode PRINT_START call line changed")
    if "M104 S[nozzle_temperature_initial_layer]" not in start:
        fails.append("PRINT_START block no longer uses [nozzle_temperature_initial_layer]")
    if "BED=" in start.splitlines()[0] or "EXTRUDER=" in start.splitlines()[0]:
        fails.append("PRINT_START gained macro params")

    core95 = _flatten(FILAMENT_95A)
    core_named = _flatten(REPO / "resources/profiles/IdeaFormer/filament/ApexCore.json")
    n95, b95 = filament_temps(core95)
    ncore, bcore = filament_temps(core_named)
    if (n95, b95) != (225, 0) or (ncore, bcore) != (225, 0):
        fails.append(f"ApexCore/95A temps {(n95, b95)} {(ncore, bcore)}")
    for k, v in core95.items():
        if k in SKIP_FLAT_KEYS:
            continue
        if core_named.get(k) != v:
            fails.append(f"ApexCore differs from 95A at {k}: {core_named.get(k)!r} vs {v!r}")
            break

    for mid in ("apex_core", "apex_forge", "apex_bounce", "apex_light"):
        spec = resolve_material(mid)
        flat = _flatten(spec.filament)
        nozzle, bed = filament_temps(flat)
        if (nozzle, bed) != (spec.nozzle_c, spec.bed_c):
            fails.append(f"{mid} flatten temps {nozzle}/{bed} != {spec.nozzle_c}/{spec.bed_c}")
        start_for_mid = spec.machine_overrides.get("machine_start_gcode") or start
        rendered = render_expected_start_gcode(start_for_mid, nozzle, bed, str(machine.get("gcode_flavor") or ""))
        fails.extend(f"{mid}: {f}" for f in assert_material_temps_in_gcode(rendered, nozzle, bed))
        sub = substitute_print_start(start_for_mid, nozzle, bed)
        if f"M104 S{nozzle}" not in sub or f"M109 S{nozzle}" not in sub:
            fails.append(f"{mid} PRINT_START placeholders did not receive {nozzle}")
        if "[nozzle_temperature_initial_layer]" in sub:
            fails.append(f"{mid} leftover nozzle placeholder")
        if not start_for_mid.startswith("PRINT_START\n"):
            fails.append(f"{mid} start gcode PRINT_START call line changed")
    return fails


def test_sliced_gcode_temps() -> list[str]:
    binary = _binary()
    if binary is None:
        print("  SKIP live gcode temps: orca-slicer not present")
        return []
    if not MODEL.is_file():
        return ["missing insole_sample.stl"]
    fails: list[str] = []
    machine = _flatten(MACHINE)
    start = machine.get("machine_start_gcode") or ""
    default_text = None
    core_text = None
    for mid in ("apex_core", "apex_forge", "apex_bounce", "apex_light"):
        spec = resolve_material(mid)
        with tempfile.TemporaryDirectory(prefix=f"mat_{mid}_") as td:
            outdir = Path(td)
            flat_m = flatten_profile(MACHINE, outdir / "machine.json")
            flat_p = flatten_profile(PROCESS, outdir / "process.json")
            flat_f = flatten_profile(spec.filament, outdir / "filament.json")
            cmd = [
                str(binary),
                "--slice",
                "1",
                "--allow-newer-file",
                "--load-settings",
                f"{flat_m};{flat_p}",
                "--load-filaments",
                str(flat_f),
                "--outputdir",
                str(outdir),
                str(MODEL),
            ]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=600, errors="replace")
            if r.returncode != 0:
                fails.append(f"{mid} slice exit {r.returncode}: {(r.stdout + r.stderr)[-800:]}")
                continue
            gcodes = list(outdir.glob("**/*.gcode"))
            if not gcodes:
                fails.append(f"{mid} no gcode")
                continue
            text = gcodes[0].read_text(encoding="utf-8", errors="replace")
            fails.extend(f"{mid}: {f}" for f in assert_material_temps_in_gcode(text, spec.nozzle_c, spec.bed_c))
            rendered = render_expected_start_gcode(start, spec.nozzle_c, spec.bed_c, str(machine.get("gcode_flavor") or ""))
            if mid == "apex_core":
                core_text = text
            if f"M104 S{spec.nozzle_c}" not in text and f"M109 S{spec.nozzle_c}" not in text:
                fails.append(f"{mid} sliced gcode missing M104/M109 S{spec.nozzle_c}")
            _ = rendered
    with tempfile.TemporaryDirectory(prefix="mat_default_") as td:
        outdir = Path(td)
        flat_m = flatten_profile(MACHINE, outdir / "machine.json")
        flat_p = flatten_profile(PROCESS, outdir / "process.json")
        flat_f = flatten_profile(FILAMENT_95A, outdir / "filament.json")
        cmd = [
            str(_binary()),
            "--slice",
            "1",
            "--allow-newer-file",
            "--load-settings",
            f"{flat_m};{flat_p}",
            "--load-filaments",
            str(flat_f),
            "--outputdir",
            str(outdir),
            str(MODEL),
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600, errors="replace")
        if r.returncode == 0:
            gcodes = list(outdir.glob("**/*.gcode"))
            if gcodes:
                default_text = gcodes[0].read_text(encoding="utf-8", errors="replace")
    if default_text is not None and core_text is not None:
        if default_text != core_text:
            fails.append("default 95A gcode != apex_core gcode")
    return fails


def test_validator_accepts_belt() -> list[str]:
    fails: list[str] = []
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "run_orca_profile_validator",
        REPO / "scripts" / "run_orca_profile_validator.py",
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    _prepare_copy = mod._prepare_copy

    with tempfile.TemporaryDirectory(prefix="belt_val_") as td:
        src = Path(td) / "src"
        dest = Path(td) / "dest"
        (src / "IdeaFormer" / "machine").mkdir(parents=True)
        sample = src / "IdeaFormer" / "machine" / "fdm_ideaformer_common.json"
        sample.write_text(json.dumps({"printer_structure": "belt", "type": "machine"}) + "\n")
        _prepare_copy(src, dest)
        copied = json.loads((dest / "IdeaFormer" / "machine" / "fdm_ideaformer_common.json").read_text())
        if copied.get("printer_structure") != "corexy":
            fails.append(f"validator copy did not accept belt: {copied}")
    return fails


_VYLON_BED_KEYS = (
    "hot_plate_temp",
    "hot_plate_temp_initial_layer",
    "cool_plate_temp",
    "cool_plate_temp_initial_layer",
    "textured_plate_temp",
    "textured_plate_temp_initial_layer",
    "eng_plate_temp",
    "eng_plate_temp_initial_layer",
)
_FILAMENT_IDENTITY_KEYS = {
    "name",
    "setting_id",
    "filament_notes",
    "filament_settings_id",
    "filament_id",
}
_SPEC_DELTA_FIELDS = {
    "material_id",
    "display_name",
    "filament",
    "bed_c",
    "default_wall_loops",
}


def test_apex_vylon_is_forge_except_bed_and_walls() -> list[str]:
    """ApexVylon matches ApexForge except belt/bed 60 °C and wall loops 6.

    OrthoCAD sends profile_id, material, infill_density, and the model. It does
    not send wall_loops. The slice API injects default_wall_loops on that path.
    Vylon stays on Forge's single orca-slicer path (no Core/Light chain).
    """
    import dataclasses

    from server.slice_overrides import apply_process_overrides

    fails: list[str] = []
    forge = resolve_material("apex_forge")
    vylon = resolve_material("apex_vylon")
    if vylon.display_name != "ApexVylon" or vylon.material_id != "apex_vylon":
        fails.append(f"apex_vylon identity {vylon.material_id}/{vylon.display_name}")
    if (vylon.nozzle_c, vylon.bed_c, vylon.default_wall_loops) != (245, 60, 6):
        fails.append(
            f"apex_vylon nozzle/bed/walls {vylon.nozzle_c}/{vylon.bed_c}/{vylon.default_wall_loops}"
        )
    if (forge.nozzle_c, forge.bed_c) != (245, 0) or forge.default_wall_loops is not None:
        fails.append(
            f"apex_forge temps/walls changed {forge.nozzle_c}/{forge.bed_c}/{forge.default_wall_loops}"
        )
    if vylon.filament.name != "ApexVylon.json":
        fails.append(f"apex_vylon filament {vylon.filament.name}")
    for field in dataclasses.fields(forge):
        if field.name in _SPEC_DELTA_FIELDS:
            continue
        if getattr(forge, field.name) != getattr(vylon, field.name):
            fails.append(f"apex_vylon {field.name} differs from apex_forge")
    if vylon.process_overrides or vylon.machine_overrides or vylon.filament_overrides:
        fails.append("apex_vylon gained process, machine, or filament overrides")
    if vylon.target_first_layer_y_mm is not None or vylon.gcode_postprocess is not None:
        fails.append("apex_vylon gained a first-layer target or gcode postprocess")

    raw_forge = json.loads(forge.filament.read_text())
    raw_vylon = json.loads(vylon.filament.read_text())
    if raw_vylon.get("inherits") != "ApexForge":
        fails.append(f"ApexVylon.json inherits {raw_vylon.get('inherits')!r}, expected ApexForge")
    for key in ("type", "from", "instantiation", "compatible_printers"):
        if raw_vylon.get(key) != raw_forge.get(key):
            fails.append(f"ApexVylon.json {key} differs from ApexForge")
    for key in (
        "nozzle_temperature",
        "nozzle_temperature_initial_layer",
        "nozzle_temperature_range_low",
        "nozzle_temperature_range_high",
    ):
        if key in raw_vylon:
            fails.append(f"ApexVylon.json overrides {key} instead of inheriting ApexForge")
        if key not in raw_forge:
            fails.append(f"ApexForge.json missing {key}")
    extra_raw = set(raw_vylon) - set(raw_forge) - set(_VYLON_BED_KEYS)
    if extra_raw:
        fails.append(f"ApexVylon.json extra keys {sorted(extra_raw)}")
    for key in _VYLON_BED_KEYS:
        if raw_vylon.get(key) != ["60"]:
            fails.append(f"ApexVylon.json {key} {raw_vylon.get(key)!r} != ['60']")

    forge_flat = _flatten(forge.filament)
    vylon_flat = _flatten(vylon.filament)
    nozzle, bed = filament_temps(vylon_flat)
    if (nozzle, bed) != (245, 60):
        fails.append(f"apex_vylon flatten temps {nozzle}/{bed} != 245/60")
    if filament_temps(forge_flat) != (245, 0):
        fails.append(f"apex_forge flatten temps changed {filament_temps(forge_flat)}")
    for key in set(forge_flat) | set(vylon_flat):
        if forge_flat.get(key) == vylon_flat.get(key):
            continue
        if key in _VYLON_BED_KEYS:
            if forge_flat.get(key) != ["0"] or vylon_flat.get(key) != ["60"]:
                fails.append(f"bed field {key}: forge {forge_flat.get(key)!r} vylon {vylon_flat.get(key)!r}")
            continue
        if key in _FILAMENT_IDENTITY_KEYS:
            continue
        fails.append(f"flattened filament {key} differs from ApexForge")

    machine = _flatten(MACHINE)
    start = machine.get("machine_start_gcode") or ""
    rendered = render_expected_start_gcode(start, 245, 60, str(machine.get("gcode_flavor") or ""))
    fails.extend(f"apex_vylon: {item}" for item in assert_material_temps_in_gcode(rendered, 245, 60))
    if "M190 S60" not in rendered and "M140 S60" not in rendered:
        fails.append("rendered ApexVylon start gcode missing M140/M190 S60")
    forge_rendered = render_expected_start_gcode(start, 245, 0, str(machine.get("gcode_flavor") or ""))
    fails.extend(f"apex_forge: {item}" for item in assert_material_temps_in_gcode(forge_rendered, 245, 0))

    with tempfile.TemporaryDirectory(prefix="vylon_walls_") as td:
        flat_p = flatten_profile(PROCESS, Path(td) / "process.json")
        apply_process_overrides(flat_p, {"wall_loops": vylon.default_wall_loops})
        applied = json.loads(flat_p.read_text())
        if str(applied.get("wall_loops")) != "6":
            fails.append(f"process wall_loops {applied.get('wall_loops')!r} != 6")

    fails.extend(_assert_single_orca_path("apex_vylon"))
    fails.extend(_assert_single_orca_path("apex_forge"))
    fails.extend(_assert_vylon_http_injects_walls())
    fails.extend(_slice_vylon_gcode_if_binary())
    return fails


def _assert_single_orca_path(material_id: str) -> list[str]:
    """Forge and Vylon must not enter the Core/Light production chain."""
    from unittest.mock import patch

    import server.slice_engine as slice_engine

    fails: list[str] = []
    profile = get_profile("apex-v2-insole-95a", material=material_id)
    if profile is None:
        return [f"{material_id} profile missing"]
    seen = {"binary": 0}

    def binary() -> Path:
        seen["binary"] += 1
        raise FileNotFoundError("orca-slicer binary not found")

    def chain(*_args, **_kwargs):
        raise AssertionError(f"{material_id} dispatched to a production chain")

    with tempfile.TemporaryDirectory(prefix=f"path_{material_id}_") as td:
        stl = Path(td) / "model.stl"
        stl.write_bytes(b"solid x\nendsolid x\n")
        with patch("server.slice_engine._slicer_binary", binary), patch(
            "server.chains.run_material_chain", chain
        ):
            try:
                slice_engine.slice_stl(
                    stl,
                    profile,
                    material=material_id,
                    belt_insole_place=False,
                )
            except FileNotFoundError:
                pass
            except AssertionError as exc:
                fails.append(str(exc))
    if seen["binary"] != 1 and not any("production chain" in item for item in fails):
        fails.append(f"{material_id} did not stay on the single orca path")
    return fails


def _assert_vylon_http_injects_walls() -> list[str]:
    """POST /v1/slice accepts apex_vylon and injects wall_loops 6 when omitted."""
    from unittest.mock import patch

    fails: list[str] = []
    try:
        from fastapi.testclient import TestClient
        from server.app import app
    except Exception as exc:
        print(f"  SKIP ApexVylon HTTP ({exc})")
        return fails

    seen: dict[str, dict] = {}

    def fake(stl, profile, **kwargs):
        material_id = kwargs.get("material")
        seen[material_id] = {
            "overrides": dict(kwargs.get("process_overrides") or {}),
            "filament": profile.filament.name,
            "process_overrides": dict(profile.process_overrides),
        }
        dest = Path(kwargs["work_dir"]) / "out.gcode"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("PRINT_START\nM104 S245\nM109 S245\nM190 S60\n", encoding="utf-8")
        return dest, {"elapsed_s": 0.0}

    client = TestClient(app)
    stl_bytes = b"solid x\nendsolid x\n"
    with patch("server.app.slice_stl", fake):
        vylon = client.post(
            "/v1/slice",
            data={
                "profile_id": "apex-v2-insole-95a",
                "material": "apex_vylon",
                "infill_density": "32",
            },
            files={"model": ("afo.stl", stl_bytes, "model/stl")},
        )
        forge = client.post(
            "/v1/slice",
            data={"profile_id": "apex-v2-insole-95a", "material": "apex_forge"},
            files={"model": ("afo.stl", stl_bytes, "model/stl")},
        )
    if vylon.status_code != 200:
        fails.append(f"apex_vylon slice HTTP {vylon.status_code} body={vylon.text[:300]}")
        return fails
    if vylon.headers.get("x-material") != "apex_vylon":
        fails.append(f"X-Material {vylon.headers.get('x-material')!r}")
    if vylon.headers.get("x-wall-loops") != "6":
        fails.append(f"X-Wall-Loops {vylon.headers.get('x-wall-loops')!r} != 6")
    if vylon.headers.get("x-infill-density") != "32%":
        fails.append(f"X-Infill-Density {vylon.headers.get('x-infill-density')!r}")
    got = seen.get("apex_vylon") or {}
    if got.get("filament") != "ApexVylon.json":
        fails.append(f"slice filament {got.get('filament')!r}")
    if got.get("process_overrides"):
        fails.append(f"apex_vylon profile process overrides {got.get('process_overrides')!r}")
    overrides = got.get("overrides") or {}
    if overrides.get("wall_loops") != 6:
        fails.append(f"request wall_loops {overrides.get('wall_loops')!r} != 6")
    if overrides.get("sparse_infill_density") != "32%":
        fails.append(f"request infill {overrides.get('sparse_infill_density')!r} != 32%")
    if forge.status_code != 200:
        fails.append(f"apex_forge slice HTTP {forge.status_code}")
    else:
        forge_over = (seen.get("apex_forge") or {}).get("overrides") or {}
        if "wall_loops" in forge_over:
            fails.append(f"apex_forge request gained wall_loops {forge_over.get('wall_loops')!r}")
        if (seen.get("apex_forge") or {}).get("filament") != "ApexForge.json":
            fails.append("apex_forge slice filament changed")
    return fails


def _slice_vylon_gcode_if_binary() -> list[str]:
    binary = _binary()
    if binary is None:
        print("  SKIP ApexVylon live gcode: orca-slicer not present")
        return []
    if not MODEL.is_file():
        return ["missing insole_sample.stl"]
    fails: list[str] = []
    import re

    with tempfile.TemporaryDirectory(prefix="mat_apex_vylon_") as td:
        outdir = Path(td)
        flat_m = flatten_profile(MACHINE, outdir / "machine.json")
        flat_p = flatten_profile(PROCESS, outdir / "process.json")
        from server.slice_overrides import apply_process_overrides

        apply_process_overrides(flat_p, {"wall_loops": 6})
        flat_f = flatten_profile(
            REPO / "resources/profiles/IdeaFormer/filament/ApexVylon.json",
            outdir / "filament.json",
        )
        cmd = [
            str(binary),
            "--slice",
            "1",
            "--allow-newer-file",
            "--load-settings",
            f"{flat_m};{flat_p}",
            "--load-filaments",
            str(flat_f),
            "--outputdir",
            str(outdir),
            str(MODEL),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600, errors="replace")
        if proc.returncode != 0:
            return [f"apex_vylon slice exit {proc.returncode}: {(proc.stdout + proc.stderr)[-800:]}"]
        gcodes = list(outdir.glob("**/*.gcode"))
        if not gcodes:
            return ["apex_vylon no gcode"]
        text = gcodes[0].read_text(encoding="utf-8", errors="replace")
        fails.extend(f"apex_vylon: {item}" for item in assert_material_temps_in_gcode(text, 245, 60))
        if not re.search(r"; wall_loops = 6\b", text) and not re.search(r"\bperimeters\s*=\s*6\b", text):
            fails.append("sliced gcode missing wall_loops/perimeters = 6")
    return fails


def main() -> int:
    print("Apex materials")
    tests = (
        test_registry_and_api_contract,
        test_http_400_and_materials_endpoint,
        test_builtin_presets_registered,
        test_profile_temps_and_print_start,
        test_apex_vylon_is_forge_except_bed_and_walls,
        test_validator_accepts_belt,
        test_sliced_gcode_temps,
    )
    fails: list[str] = []
    for fn in tests:
        part = fn()
        if part:
            print(f"  FAIL {fn.__name__}")
            fails.extend(part)
        else:
            print(f"  PASS {fn.__name__}")
    if fails:
        for f in fails:
            print(f"FAIL {f}")
        return 1
    print("OVERALL: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
