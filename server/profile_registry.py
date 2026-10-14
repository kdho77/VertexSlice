"""Maps API profile ids to IdeaFormer Apex preset paths (repo-relative)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILES = REPO_ROOT / "resources" / "profiles" / "IdeaFormer"


@dataclass(frozen=True)
class SliceProfile:
    profile_id: str
    machine: Path
    process: Path
    filament: Path
    description: str


_REGISTRY: dict[str, SliceProfile] = {
    "apex-v2-insole-95a": SliceProfile(
        profile_id="apex-v2-insole-95a",
        machine=PROFILES / "machine" / "Vertex Apex V2 1.0 nozzle.json",
        process=PROFILES / "process" / "0.42mm TPU Insole @Vertex Apex V2.json",
        filament=PROFILES / "filament" / "VERTEX_APEX_CORE_95A.json",
        description="Vertex Apex V2 1.0 mm + VERTEX_APEX_CORE_95A insole (Cura-matched 95A)",
    ),
    "apex-v2-insole-85a": SliceProfile(
        profile_id="apex-v2-insole-85a",
        machine=PROFILES / "machine" / "Vertex Apex V2 1.0 nozzle.json",
        process=PROFILES / "process" / "0.42mm TPU Insole 85A @Vertex Apex V2.json",
        filament=PROFILES / "filament" / "VERTEX_APEX_CORE_85A.json",
        description="Apex 85A insole (expert delta vs 95A baseline)",
    ),
    "apex-v2-insole-90a": SliceProfile(
        profile_id="apex-v2-insole-90a",
        machine=PROFILES / "machine" / "Vertex Apex V2 1.0 nozzle.json",
        process=PROFILES / "process" / "0.42mm TPU Insole 90A @Vertex Apex V2.json",
        filament=PROFILES / "filament" / "VERTEX_APEX_CORE_90A.json",
        description="Apex 90A insole",
    ),
    "apex-v2-insole-98a": SliceProfile(
        profile_id="apex-v2-insole-98a",
        machine=PROFILES / "machine" / "Vertex Apex V2 1.0 nozzle.json",
        process=PROFILES / "process" / "0.42mm TPU Insole 98A @Vertex Apex V2.json",
        filament=PROFILES / "filament" / "VERTEX_APEX_CORE_98A.json",
        description="Apex 98A / 60D insole",
    ),
    "apex-v2-insole-75d": SliceProfile(
        profile_id="apex-v2-insole-75d",
        machine=PROFILES / "machine" / "Vertex Apex V2 1.0 nozzle.json",
        process=PROFILES / "process" / "0.42mm TPU Insole 75D @Vertex Apex V2.json",
        filament=PROFILES / "filament" / "VERTEX_APEX_CORE_75D.json",
        description="Apex 75D insole",
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
        }
        for p in _REGISTRY.values()
    ]
