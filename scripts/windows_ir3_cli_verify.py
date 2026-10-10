#!/usr/bin/env python3
"""Headless IR3 V2 slice + belt G-code gate for the Windows baseline package.

Uses the just-built orca-slicer.exe and the system IdeaFormer IR3 V2 presets
(flattened; the CLI does not follow `inherits`). Does not upload to a printer.

Exit codes:
  0 = G-code produced and belt gate PASS
  1 = gate FAIL (unsafe G-code; workflow must fail)
  2 = gate WARN, or slicer missing / no G-code / timeout / unexpected error
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO / "validation") not in sys.path:
    sys.path.insert(0, str(REPO / "validation"))

from orca_profile_flatten import flatten_profile  # noqa: E402

MACHINE = REPO / "resources/profiles/IdeaFormer/machine/IdeaFormer IR3 V2 0.4 nozzle.json"
PROCESS = REPO / "resources/profiles/IdeaFormer/process/0.20mm Standard @IdeaFormer IR3 V2.json"
FILAMENT = REPO / "resources/profiles/IdeaFormer/filament/Generic PLA @IdeaFormer IR3 V2.json"
MODEL = REPO / "validation/test_models/box_10x10x10.stl"
GATE = REPO / "validation/belt_gcode_gate.py"


def _run(cmd, timeout, cwd=None):
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=cwd,
        errors="replace",
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slicer-dir", required=True, help="Directory containing orca-slicer.exe")
    ap.add_argument("--outdir", required=True)
    args = ap.parse_args()

    slicer_dir = Path(args.slicer_dir).resolve()
    outdir = Path(args.outdir).resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    report_path = outdir / "REPORT.txt"
    log_path = outdir / "slice.log"

    exe = slicer_dir / "orca-slicer.exe"
    if not exe.exists():
        fallback = list(slicer_dir.rglob("orca-slicer.exe"))
        exe = fallback[0] if fallback else exe
    if not exe.exists():
        report_path.write_text("ERROR: orca-slicer.exe not found\n")
        print(report_path.read_text())
        return 2

    help_run = _run([str(exe), "--help"], timeout=60, cwd=str(slicer_dir))
    (outdir / "slicer_help.txt").write_text(help_run.stdout + "\n" + help_run.stderr)
    launch_ok = help_run.returncode == 0 or "slice" in (help_run.stdout + help_run.stderr).lower()

    flat_machine = flatten_profile(MACHINE, outdir / "flat_machine.json")
    flat_process = flatten_profile(PROCESS, outdir / "flat_process.json")
    flat_filament = flatten_profile(FILAMENT, outdir / "flat_filament.json")

    if not MODEL.exists():
        report_path.write_text(f"ERROR: model missing: {MODEL}\n")
        print(report_path.read_text())
        return 2

    cmd = [
        str(exe),
        "--slice",
        "1",
        "--allow-newer-file",
        "--load-settings",
        f"{flat_machine};{flat_process}",
        "--load-filaments",
        str(flat_filament),
        "--outputdir",
        str(outdir),
        str(MODEL),
    ]
    t0 = time.time()
    try:
        proc = _run(cmd, timeout=300, cwd=str(slicer_dir))
        elapsed = time.time() - t0
        log_path.write_text(proc.stdout + "\n--- stderr ---\n" + proc.stderr)
    except subprocess.TimeoutExpired as exc:
        elapsed = time.time() - t0
        log_path.write_text(f"TIMEOUT after {elapsed:.1f}s\n{exc}")
        report_path.write_text(
            "\n".join(
                [
                    "cli_launch_help=" + ("ok" if launch_ok else "fail"),
                    "slice_status=TIMEOUT",
                    f"elapsed_s={elapsed:.1f}",
                    "gate_status=NOT_RUN",
                    "note=Headless CLI slice timed out; installer/portable still the Windows deliverable.",
                ]
            )
            + "\n"
        )
        print(report_path.read_text())
        return 2

    gcodes = sorted(outdir.rglob("*.gcode"))
    lines = [
        "cli_launch_help=" + ("ok" if launch_ok else "fail"),
        f"slicer={exe}",
        f"model={MODEL.name}",
        "machine=IdeaFormer IR3 V2 0.4 nozzle (flattened system profile)",
        "process=0.20mm Standard @IdeaFormer IR3 V2 (flattened)",
        "filament=Generic PLA @IdeaFormer IR3 V2 (flattened)",
        f"slice_exit={proc.returncode}",
        f"elapsed_s={elapsed:.1f}",
        f"gcode_count={len(gcodes)}",
    ]

    if not gcodes:
        lines += [
            "slice_status=NO_GCODE",
            "gate_status=NOT_RUN",
            "note=CLI produced no G-code. Known risk: CLI unprintable_height check (README). Not a physical-print result.",
        ]
        report_path.write_text("\n".join(lines) + "\n")
        print(report_path.read_text())
        return 2

    gcode = gcodes[0]
    lines += [f"gcode={gcode.name}", "slice_status=PRODUCED"]
    gate = _run([sys.executable, str(GATE), str(gcode)], timeout=60)
    (outdir / "gate.txt").write_text(gate.stdout + "\n" + gate.stderr)
    if gate.returncode == 0:
        gate_status = "PASS"
        exit_code = 0
    elif gate.returncode == 2:
        gate_status = "WARN"
        exit_code = 2
    else:
        gate_status = "FAIL"
        exit_code = 1
    lines += [
        f"gate_status={gate_status}",
        f"gate_exit={gate.returncode}",
        "physical_print=NOT_PERFORMED",
        "note=G-code is from this Windows binary + system IR3 V2 presets. Do not print if gate is FAIL.",
    ]
    report_path.write_text("\n".join(lines) + "\n")
    print(report_path.read_text())
    summary = {
        "slice_status": "PRODUCED",
        "gate_status": gate_status,
        "gcode": gcode.name,
        "elapsed_s": round(elapsed, 1),
    }
    (outdir / "report.json").write_text(json.dumps(summary, indent=2) + "\n")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
