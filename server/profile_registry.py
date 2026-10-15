"""Maps API profile ids to IdeaFormer Apex preset paths (repo-relative)."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
from validation.apex_insole_tunables import DEFAULT_PRINT_DIRECTION, DEFAULT_WALL_LOOPS  # noqa: E402
from validation.apex_insole_densities import KENDON_INFILL_DENSITY_PRESETS  # noqa: E402
from validation.apex_light import (  # noqa: E402
    BED_C as APEX_LIGHT_BED_C,
    DEFAULT_WALL_LOOPS as APEX_LIGHT_WALL_LOOPS,
    MACHINE_OVERRIDES as APEX_LIGHT_MACHINE_OVERRIDES,
    NOZZLE_C as APEX_LIGHT_NOZZLE_C,
    PROCESS_OVERRIDES as APEX_LIGHT_PROCESS_OVERRIDES,
    TARGET_FIRST_LAYER_Y_MM as APEX_LIGHT_TARGET_FIRST_LAYER_Y_MM,
)
PROFILES = REPO_ROOT / "resources" / "profiles" / "IdeaFormer"
MACHINE = PROFILES / "machine" / "Vertex Apex V2 1.0 nozzle.json"
FILAMENT_95A = PROFILES / "filament" / "VERTEX_APEX_CORE_95A.json"

DEFAULT_MATERIAL_ID = "apex_core"


@dataclass(frozen=True)
class MaterialSpec:
    material_id: str
    display_name: str
    filament: Path
    nozzle_c: int
    bed_c: int
    process_overrides: dict = field(default_factory=dict)
    machine_overrides: dict = field(default_factory=dict)
    default_wall_loops: int | None = None
    target_first_layer_y_mm: float | None = None


_MATERIALS: dict[str, MaterialSpec] = {
    "apex_core": MaterialSpec(
        material_id="apex_core",
        display_name="ApexCore",
        # Same file as today's default so omitted `material` is byte-identical.
        filament=FILAMENT_95A,
        nozzle_c=225,
        bed_c=0,
    ),
    "apex_forge": MaterialSpec(
        material_id="apex_forge",
        display_name="ApexForge",
        filament=PROFILES / "filament" / "ApexForge.json",
        nozzle_c=245,
        bed_c=0,
    ),
    "apex_bounce": MaterialSpec(
        material_id="apex_bounce",
        display_name="ApexBounce",
        filament=PROFILES / "filament" / "ApexBounce.json",
        nozzle_c=225,
        bed_c=60,
    ),
    "apex_light": MaterialSpec(
        material_id="apex_light",
        display_name="ApexLight",
        filament=PROFILES / "filament" / "ApexLight.json",
        nozzle_c=APEX_LIGHT_NOZZLE_C,
        bed_c=APEX_LIGHT_BED_C,
        process_overrides=dict(APEX_LIGHT_PROCESS_OVERRIDES),
        machine_overrides=dict(APEX_LIGHT_MACHINE_OVERRIDES),
        default_wall_loops=APEX_LIGHT_WALL_LOOPS,
        target_first_layer_y_mm=APEX_LIGHT_TARGET_FIRST_LAYER_Y_MM,
    ),
}


@dataclass(frozen=True)
class SliceProfile:
    profile_id: str
    machine: Path
    process: Path
    filament: Path
    description: str
    process_overrides: dict = field(default_factory=dict)
    machine_overrides: dict = field(default_factory=dict)
    target_first_layer_y_mm: float | None = None


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


def resolve_material(material: str | None) -> MaterialSpec:
    """Return the material spec. Empty/omitted → apex_core. Unknown → ValueError."""
    if material is None or str(material).strip() == "":
        return _MATERIALS[DEFAULT_MATERIAL_ID]
    key = str(material).strip().lower()
    spec = _MATERIALS.get(key)
    if spec is None:
        allowed = ", ".join(_MATERIALS)
        raise ValueError(f"Unknown material: {material}. Expected one of: {allowed}")
    return spec


def list_materials() -> list[dict]:
    return [
        {
            "id": m.material_id,
            "display_name": m.display_name,
            "nozzle": m.nozzle_c,
            "bed": m.bed_c,
        }
        for m in _MATERIALS.values()
    ]


def get_profile(profile_id: str, material: str | None = None) -> SliceProfile | None:
    p = _REGISTRY.get(profile_id.strip().lower())
    if p is None:
        return None
    spec = resolve_material(material)
    return replace(
        p,
        filament=spec.filament,
        process_overrides=dict(spec.process_overrides),
        machine_overrides=dict(spec.machine_overrides),
        target_first_layer_y_mm=spec.target_first_layer_y_mm,
    )


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
