"""Production slice chains. ApexCore and ApexLight only."""
from __future__ import annotations

from pathlib import Path

from server.profile_registry import SliceProfile

CHAIN_APEX_CORE = "apex_core-g2b-v1"
CHAIN_APEX_LIGHT = "apex_light-pr17q-postedit"


def run_material_chain(
    material: str,
    stl_path: Path,
    profile: SliceProfile,
    work_dir: Path | None,
    timeout_s: int,
    process_overrides: dict | None,
    print_direction: str | None,
) -> tuple[Path, dict]:
    if material == "apex_core":
        from server.chains.apex_core import run_apex_core

        return run_apex_core(stl_path, work_dir=work_dir, timeout_s=timeout_s)
    if material == "apex_light":
        from server.chains.apex_light import run_apex_light

        return run_apex_light(
            stl_path,
            work_dir=work_dir,
            timeout_s=timeout_s,
            process_overrides=process_overrides,
            print_direction=print_direction,
        )
    raise ValueError(material)
