#!/usr/bin/env python3
"""ApexCore / ApexForge / ApexBounce / ApexLight materials: temps, API, PRINT_START call line."""
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
    if ids != ["apex_core", "apex_forge", "apex_bounce", "apex_light"]:
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
    for name in ("ApexCore", "ApexForge", "ApexBounce", "ApexLight"):
        if name not in names:
            fails.append(f"{name} not in IdeaFormer.json filament_list")
        path = REPO / "resources/profiles/IdeaFormer/filament" / f"{name}.json"
        if not path.is_file():
            fails.append(f"missing {path}")
    model = json.loads((REPO / "resources/profiles/IdeaFormer/machine/Vertex Apex V2.json").read_text())
    mats = model.get("default_materials") or ""
    for name in ("ApexCore", "ApexForge", "ApexBounce", "ApexLight"):
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


def main() -> int:
    print("Apex materials")
    tests = (
        test_registry_and_api_contract,
        test_http_400_and_materials_endpoint,
        test_builtin_presets_registered,
        test_profile_temps_and_print_start,
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
