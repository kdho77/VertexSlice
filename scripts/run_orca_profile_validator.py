#!/usr/bin/env python3
"""Run upstream OrcaSlicer_profile_validator with fork-only enum values accepted.

The SoftFever binary does not know `printer_structure: belt`. Copy the profile
tree and rewrite that value to `corexy` in the copy only so JSON loads. Real
presets are unchanged (printer behavior stays belt).

Once belt parses, the same binary also rejects this fork's still-valid
`layer_change_gcode` key and instantiation filaments that omit `filament_id`.
Those are stripped/filled in the copy only so IdeaFormer can fully load.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

IGNORE_COPY = shutil.ignore_patterns("*.png", "*.jpg", "*.jpeg", "*.svg", "*.stl", "*.3mf", "*.zip")


def _prepare_copy(src: Path, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest, ignore=IGNORE_COPY)
    for path in dest.rglob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        changed = False
        if data.get("printer_structure") == "belt":
            # Upstream enum is undefine/corexy/i3/hbot/delta — belt is this fork.
            data["printer_structure"] = "corexy"
            changed = True
        if "layer_change_gcode" in data:
            data.pop("layer_change_gcode")
            changed = True
        if (
            data.get("type") == "filament"
            and str(data.get("instantiation", "")).lower() == "true"
            and not data.get("filament_id")
        ):
            data["filament_id"] = "GF0000"
            changed = True
        if changed:
            path.write_text(json.dumps(data, indent=4) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("validator")
    parser.add_argument("-p", "--path")
    args, rest = parser.parse_known_args(argv)
    if not args.validator or not args.path:
        print("usage: run_orca_profile_validator.py <validator> -p <profiles> [validator args...]", file=sys.stderr)
        return 2

    src = Path(args.path).resolve()
    validator = Path(args.validator).resolve()
    with tempfile.TemporaryDirectory(prefix="orca_profile_validator_") as td:
        dest = Path(td) / "profiles"
        _prepare_copy(src, dest)
        cmd = [str(validator), "-p", str(dest), *rest]
        proc = subprocess.run(cmd)
        return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
