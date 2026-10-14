"""Maps API profile ids to IdeaFormer Apex preset paths (repo-relative)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
from validation.apex_insole_tunables import DEFAULT_PRINT_DIRECTION, DEFAULT_WALL_LOOPS  # noqa: E402
from validation.apex_insole_densities import KENDON_INFILL_DENSITY_PRESETS  # noqa: E402
PROFILES = REPO_ROOT / "resources" / "profiles" / "IdeaFormer"
MACHINE = PROFILES / "machine" / "Vertex Apex V2 1.0 nozzle.json"
FILAMENT_95A = PROFILES / "filament" / "VERTEX_APEX_CORE_95A.json"


@dataclass(frozen=True)
class SliceProfile:
    profile_id: str
    machine: Path
    process: Path
    filament: Path
    description: str


def _entry(profile_id: str, process_name: str, description: str) -> SliceProfile:
    return SliceProfile(
        profile_id=profile_id,
        machine=MACHINE,
        process=PROFILES / "process" / process_name,
        filament=FILAMENT_95A,
        description=description,
    )


_PROFILE_DEFAULT_INFILL_PCT: dict[str, int] = {
    "apex-v2-insole-extra-soft": KENDON_INFILL_DENSITY_PRESETS["Extra Soft"],
    "apex-v2-insole-95a": KENDON_INFILL_DENSITY_PRESETS["Soft"],
    "apex-v2-insole-soft": KENDON_INFILL_DENSITY_PRESETS["Soft"],
    "apex-v2-insole-medium": KENDON_INFILL_DENSITY_PRESETS["Medium"],
    "apex-v2-insole-hard": KENDON_INFILL_DENSITY_PRESETS["Hard"],
    "apex-v2-insole-extra-hard": KENDON_INFILL_DENSITY_PRESETS["Extra Hard"],
}

_REGISTRY: dict[str, SliceProfile] = {
    "apex-v2-insole-95a": _entry(
        "apex-v2-insole-95a",
        "0.42mm TPU Insole @Vertex Apex V2.json",
        "Kendon Soft 20% — VERTEX_APEX_CORE_95A (Cura-validated baseline)",
    ),
    "apex-v2-insole-soft": _entry(
        "apex-v2-insole-soft",
        "0.42mm TPU Insole @Vertex Apex V2.json",
        "Kendon Soft 20% — VERTEX_APEX_CORE_95A",
    ),
    "apex-v2-insole-extra-soft": _entry(
        "apex-v2-insole-extra-soft",
        "0.42mm TPU Insole Extra Soft @Vertex Apex V2.json",
        "Kendon Extra Soft 14% — VERTEX_APEX_CORE_95A",
    ),
    "apex-v2-insole-medium": _entry(
        "apex-v2-insole-medium",
        "0.42mm TPU Insole Medium @Vertex Apex V2.json",
        "Kendon Medium 26% — VERTEX_APEX_CORE_95A",
    ),
    "apex-v2-insole-hard": _entry(
        "apex-v2-insole-hard",
        "0.42mm TPU Insole Hard @Vertex Apex V2.json",
        "Kendon Hard 32% — VERTEX_APEX_CORE_95A",
    ),
    "apex-v2-insole-extra-hard": _entry(
        "apex-v2-insole-extra-hard",
        "0.42mm TPU Insole Extra Hard @Vertex Apex V2.json",
        "Kendon Extra Hard 38% — VERTEX_APEX_CORE_95A",
    ),
}


def get_profile(profile_id: str) -> SliceProfile | None:
    return _REGISTRY.get(profile_id.strip().lower())


def list_profiles() -> list[dict]:
    return [
        {
            "id": p.profile_id,
            "description": p.description,
            "machine": str(p.machine.relative_to(REPO_ROOT)),
            "process": str(p.process.relative_to(REPO_ROOT)),
            "filament": str(p.filament.relative_to(REPO_ROOT)),
            "defaults": {
                "wall_loops": DEFAULT_WALL_LOOPS,
                "infill_density_percent": _PROFILE_DEFAULT_INFILL_PCT.get(
                    p.profile_id, KENDON_INFILL_DENSITY_PRESETS["Soft"]
                ),
                "print_direction": DEFAULT_PRINT_DIRECTION,
            },
            "user_modifiable": ["wall_loops", "infill_density_percent", "print_direction"],
        }
        for p in _REGISTRY.values()
    ]
