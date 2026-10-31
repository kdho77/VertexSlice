#!/usr/bin/env python3
"""Assert Apex V2 machine / sliced g-code start-end blocks match Cura reference."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "validation"))

from apex_gcode_bookends import (  # noqa: E402
    MACHINE_END_GCODE,
    MACHINE_START_GCODE,
    compare_bookends,
    extract_sliced_end_bookend,
    extract_sliced_start_bookend,
    reference_end_bookend,
    reference_start_bookend,
)
from orca_profile_flatten import flatten_profile  # noqa: E402

MACHINE = REPO / "resources/profiles/IdeaFormer/machine/Vertex Apex V2 1.0 nozzle.json"
PROCESS = REPO / "resources/profiles/IdeaFormer/process/0.42mm TPU Insole @Vertex Apex V2.json"
FILAMENT = REPO / "resources/profiles/IdeaFormer/filament/VERTEX_APEX_CORE_95A.json"
MODEL = REPO / "validation/test_models/insole_sample.stl"
BIN = REPO / "build/src/Release/orca-slicer"


def test_machine_preset_bookends() -> list[str]:
    fails: list[str] = []
    flat = flatten_profile(MACHINE)
    d = json.loads(flat.read_text())
    flat.unlink(missing_ok=True)
    start = (d.get("machine_start_gcode") or "").strip() + "\n"
    end = (d.get("machine_end_gcode") or "").strip() + "\n"
    if start.strip() != MACHINE_START_GCODE.strip():
        fails.append("machine_start_gcode does not match Cura-derived template")
    if end.strip() != MACHINE_END_GCODE.strip():
        fails.append("machine_end_gcode does not match Cura-derived template")
    if "printer.cfg" in start or "TODO" in start:
        fails.append("machine_start_gcode still has placeholder note")
    return fails


def test_sliced_bookends() -> list[str]:
    if not BIN.is_file() or BIN.stat().st_size < 1_000_000:
        print("  SKIP sliced bookends: orca-slicer binary not built")
        return []
    if not MODEL.is_file():
        return ["missing insole_sample.stl"]

    ref_start = reference_start_bookend()
    ref_end = reference_end_bookend()
    fails: list[str] = []
    with tempfile.TemporaryDirectory(prefix="apex_bookends_") as td:
        outdir = Path(td)
        flat_m = flatten_profile(MACHINE, outdir / "machine.json")
        flat_p = flatten_profile(PROCESS, outdir / "process.json")
        flat_f = flatten_profile(FILAMENT, outdir / "filament.json")
        cmd = [
            str(BIN),
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
            return [f"slice failed: {(r.stdout + r.stderr)[-1200:]}"]
        gcodes = list(outdir.glob("**/*.gcode"))
        if not gcodes:
            return ["no gcode output"]
        text = gcodes[0].read_text(encoding="utf-8", errors="replace")
        try:
            got_start = extract_sliced_start_bookend(text)
            got_end = extract_sliced_end_bookend(text)
        except ValueError as exc:
            return [str(exc)]
        fails.extend(compare_bookends(got_start, ref_start))
        fails.extend(compare_bookends(got_end, ref_end))
        if not fails:
            print("  sliced start/end bookends match Cura reference")
    return fails


def main() -> int:
    print("Apex V2 g-code bookend tests")
    fails: list[str] = []
    fails.extend(test_machine_preset_bookends())
    fails.extend(test_sliced_bookends())
    if fails:
        for f in fails:
            print(f"  FAIL {f}")
        return 1
    print("OVERALL: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
