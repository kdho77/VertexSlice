#!/usr/bin/env python3
"""
Belt / toe-first slice validation for Vertex Apex V2 (see test_apex_v2_cura_match.py
for Cura reference parity).
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "validation"))

from orca_profile_flatten import flatten_profile  # noqa: E402

MACHINE = REPO / "resources/profiles/IdeaFormer/machine/Vertex Apex V2 1.0 nozzle.json"
PROCESS = REPO / "resources/profiles/IdeaFormer/process/0.42mm TPU Insole @Vertex Apex V2.json"
FILAMENT = REPO / "resources/profiles/IdeaFormer/filament/VERTEX_APEX_CORE_95A.json"
MODEL = REPO / "validation/test_models/insole_sample.stl"
GEN = REPO / "validation/test_models/generate_insole_sample.py"
BIN = REPO / "build/src/Release/orca-slicer"
BIN_DBG = REPO / "build/src/Debug/orca-slicer"

Z_STEP_EXPECT = 0.6
Z_STEP_TOL = 0.08


def _binary() -> Path | None:
    for p in (BIN, BIN_DBG):
        if p.exists() and p.stat().st_size > 1_000_000:
            return p
    return None


def _run(cmd, timeout=600):
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, errors="replace"
    )


def test_flatten_profiles() -> list[str]:
    import json

    fails: list[str] = []
    flat = flatten_profile(MACHINE)
    j = json.loads(flat.read_text())
    if "PRINT_START" not in flat.read_text():
        fails.append("flattened machine missing PRINT_START macro mode")
    if j.get("belt_axis") != "y":
        fails.append(f"belt_axis expected y, got {j.get('belt_axis')}")
    if str(j.get("printer_is_belt")) not in ("1", "true", "True"):
        fails.append("printer_is_belt not set on Apex machine")
    flat.unlink(missing_ok=True)
    return fails


def _parse_gcode(path: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    info = {
        "has_print_start": any(l.strip() == "PRINT_START" for l in lines[:40]),
        "belt_axis_y": "; belt_axis = y" in text,
        "printer_is_belt": "; printer_is_belt = 1" in text,
        "temp_225": bool(re.search(r"M10[49] S225", text)),
        "z_steps": [],
        "first_model_y": None,
        "toe_first": None,
    }
    y = z = None
    seen_model_e = False
    pending_layer = False
    layer_z: list[float] = []
    axis_re = re.compile(r"([XYZ])(-?(?:\d+\.?\d*|\.\d+))")

    for line in lines:
        if ";LAYER_CHANGE" in line or line.startswith(";LAYER:"):
            pending_layer = True
            if layer_z:
                info["z_steps"].append(layer_z[0])
            layer_z = []
            continue
        raw = line.split(";")[0].strip()
        if not raw:
            continue
        code = raw.split()[0].upper()
        if code == "G92":
            for ax, val in axis_re.findall(raw):
                if ax == "Z":
                    z = float(val)
            continue
        if code not in ("G0", "G1"):
            continue
        m = dict(axis_re.findall(raw))
        has_e = "E" in m
        if "Z" in m:
            z = float(m["Z"])
        if "Y" in m:
            y = float(m["Y"])
        if has_e and not seen_model_e and y is not None:
            seen_model_e = True
            info["first_model_y"] = y
        if has_e and z is not None and pending_layer:
            layer_z.append(z)
            pending_layer = False

    if info["first_model_y"] is not None:
        info["toe_first"] = info["first_model_y"] < 5.0
    return info


def test_slice_gcode() -> list[str]:
    fails: list[str] = []
    binary = _binary()
    if binary is None:
        print("  SKIP slice: orca-slicer binary not found")
        return fails

    if not MODEL.exists():
        _run([sys.executable, str(GEN)], timeout=60)

    with tempfile.TemporaryDirectory(prefix="apex_v2_test_") as td:
        outdir = Path(td)
        flat_m = flatten_profile(MACHINE, outdir / "machine.json")
        flat_p = flatten_profile(PROCESS, outdir / "process.json")
        flat_f = flatten_profile(FILAMENT, outdir / "filament.json")
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
        r = _run(cmd, timeout=600)
        if r.returncode != 0:
            fails.append(f"slicer exit {r.returncode}: {(r.stdout + r.stderr)[-1500:]}")
            return fails
        gcodes = list(outdir.glob("**/*.gcode"))
        if not gcodes:
            fails.append("no gcode output")
            return fails
        info = _parse_gcode(gcodes[0])
        if not info["has_print_start"]:
            fails.append("gcode missing PRINT_START at job start")
        if not info["printer_is_belt"]:
            fails.append("gcode config missing printer_is_belt = 1")
        if not info["belt_axis_y"]:
            fails.append("gcode config missing belt_axis = y")
        if not info["temp_225"]:
            fails.append("gcode missing M104/M109 S225")
        steps = [z for z in info["z_steps"] if z > 0.01]
        if len(steps) >= 3:
            deltas = [round(steps[i + 1] - steps[i], 3) for i in range(min(5, len(steps) - 1))]
            bad = [d for d in deltas if abs(d - Z_STEP_EXPECT) > Z_STEP_TOL]
            if bad:
                fails.append(f"belt Z step expected ~{Z_STEP_EXPECT}, got deltas {deltas[:5]}")
        else:
            fails.append("could not measure belt Z steps from gcode layers")
        if info["toe_first"] is False:
            fails.append(
                f"toe-first check failed: first model Y={info['first_model_y']} (want < 5 mm)"
            )
        print(
            f"  slice OK: first_model_y={info['first_model_y']}, z_steps_sample={steps[:4]}"
        )
    return fails


def main() -> int:
    print("Apex V2 belt / toe-first validation")
    fails = test_flatten_profiles()
    fails.extend(test_slice_gcode())
    if fails:
        for f in fails:
            print(f"  FAIL {f}")
        return 1
    print("OVERALL: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
