#!/usr/bin/env python3
"""Slice both production chains and golden_gate them against the LOCKED files.

Goldens and the input STLs are fetched, not committed.

  VS_CORE_STL, VS_CORE_GOLDEN
  VS_LIGHT_STL, VS_LIGHT_GOLDEN

or a directory VS_GOLDEN_DIR containing:

  procedural-default-round_2p5mm.stl
  ApexCore_95A_GOLDEN_2026-10-31.gcode
  M9_round_L_bottomtext_big_v3_leader.stl
  ApexLight_90A_GOLDEN_2026-10-31.gcode

Exit 0 only when both normalized diffs are 0. Exit 1 on a diff. Exit 2 when
the locked files were not provided.
"""
from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "scripts")]

from golden_gate import compare_paths, format_report  # noqa: E402

CORE_SHA = "54af16eda2e11dbef354f594dd501afe799d45f3ee679819b2c65908aaef5324"
LIGHT_SHA = "3a0a54a2322e579e2c5cd4e003f061a86af61d49310115299ce6856628424e67"


def _pair(kind: str) -> tuple[Path, Path] | None:
    stl = os.environ.get(f"VS_{kind}_STL")
    golden = os.environ.get(f"VS_{kind}_GOLDEN")
    if stl and golden:
        return Path(stl), Path(golden)
    root = os.environ.get("VS_GOLDEN_DIR")
    if not root:
        return None
    base = Path(root)
    if kind == "CORE":
        return (
            base / "procedural-default-round_2p5mm.stl",
            base / "ApexCore_95A_GOLDEN_2026-10-31.gcode",
        )
    return (
        base / "M9_round_L_bottomtext_big_v3_leader.stl",
        base / "ApexLight_90A_GOLDEN_2026-10-31.gcode",
    )


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _one(material: str, stl: Path, golden: Path, expect: str) -> int:
    digest = _sha(golden)
    if digest != expect:
        print(f"{material} golden sha256 {digest} != {expect}", file=sys.stderr)
        return 1
    from server.chains import run_material_chain
    from server.profile_registry import get_profile

    profile = get_profile("apex-v2-insole-medium", material=material)
    if profile is None:
        print("missing profile", file=sys.stderr)
        return 1
    os.environ["APEX_MIN_LAYER_TIME"] = os.environ.get("APEX_MIN_LAYER_TIME", "4")
    os.environ["VS_GCODE_GOLDEN"] = str(golden)
    with tempfile.TemporaryDirectory(prefix=f"golden_{material}_") as td:
        gcode, stats = run_material_chain(
            material,
            stl,
            profile,
            work_dir=Path(td),
            timeout_s=2400,
            process_overrides=None,
            print_direction="toe_first",
        )
        result = compare_paths(gcode, golden)
    print(material, "chain", stats.get("chain"))
    print(format_report(result))
    return 0 if result.ok else 1


def main() -> int:
    core = _pair("CORE")
    light = _pair("LIGHT")
    if core is None or light is None or not all(p.is_file() for pair in (core, light) for p in pair):
        print(
            "LOCKED goldens/STLs not provided. Set VS_GOLDEN_DIR or "
            "VS_CORE_STL/VS_CORE_GOLDEN and VS_LIGHT_STL/VS_LIGHT_GOLDEN.",
            file=sys.stderr,
        )
        return 2
    rc = _one("apex_core", core[0], core[1], CORE_SHA)
    rc |= _one("apex_light", light[0], light[1], LIGHT_SHA)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
