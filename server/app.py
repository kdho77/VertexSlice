"""VertexSlice headless slicing HTTP API."""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

from server.profile_registry import (
    DEFAULT_MATERIAL_ID,
    get_profile,
    list_materials,
    list_profiles,
    resolve_material,
)
from server.slice_engine import slice_stl
from server.slice_overrides import parse_slice_overrides
from validation.apex_insole_tunables import (
    DEFAULT_PRINT_DIRECTION,
    DEFAULT_WALL_LOOPS,
    INFILL_DENSITY_PCT_MAX,
    INFILL_DENSITY_PCT_MIN,
    PRINT_DIRECTION_CHOICES,
    WALL_LOOPS_MAX,
    WALL_LOOPS_MIN,
    validate_print_direction,
)

API_KEY = os.environ.get("VERTEXSLICE_API_KEY", "dev-change-me")
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", str(50 * 1024 * 1024)))

app = FastAPI(title="VertexSlice Headless Slicer", version="0.1.0")

_cors_origins = os.environ.get("VERTEXSLICE_CORS_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _cors_origins if o.strip()],
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


def require_api_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> None:
    if not API_KEY or API_KEY == "dev-change-me":
        return
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")


@app.get("/health")
def health():
    return {"status": "ok", "service": "vertexslice-slicer"}


@app.get("/v1/materials")
def materials(_: None = Depends(require_api_key)):
    return {
        "materials": list_materials(),
        "default": DEFAULT_MATERIAL_ID,
    }


@app.get("/v1/profiles")
def profiles(_: None = Depends(require_api_key)):
    return {
        "profiles": list_profiles(),
        "tunables": {
            "wall_loops": {
                "default": DEFAULT_WALL_LOOPS,
                "min": WALL_LOOPS_MIN,
                "max": WALL_LOOPS_MAX,
                "user_modifiable": True,
            },
            "infill_density_percent": {
                "min": INFILL_DENSITY_PCT_MIN,
                "max": INFILL_DENSITY_PCT_MAX,
                "user_modifiable": True,
                "hardness_preset_defaults": {
                    "extra_soft": 14,
                    "soft": 20,
                    "medium": 26,
                    "hard": 32,
                    "extra_hard": 38,
                },
            },
            "print_direction": {
                "default": DEFAULT_PRINT_DIRECTION,
                "choices": list(PRINT_DIRECTION_CHOICES),
                "user_modifiable": True,
            },
        },
    }


@app.post("/v1/slice", response_class=PlainTextResponse)
async def slice_endpoint(
    profile_id: str = Form(..., description="e.g. apex-v2-insole-95a"),
    model: UploadFile = File(...),
    wall_loops: str | None = Form(
        default=None,
        description=f"Optional override ({WALL_LOOPS_MIN}–{WALL_LOOPS_MAX}); default from preset",
    ),
    infill_density: str | None = Form(
        default=None,
        description=f"Optional infill % ({INFILL_DENSITY_PCT_MIN}–{INFILL_DENSITY_PCT_MAX}); default from hardness preset",
    ),
    print_direction: str | None = Form(
        default=None,
        description=f"Belt advance direction ({'|'.join(PRINT_DIRECTION_CHOICES)}); default {DEFAULT_PRINT_DIRECTION}",
    ),
    belt_insole_place: bool = Form(
        default=True,
        description="OrthoCAD belt preprocess (width on X, length on belt Z, X center ~140 mm)",
    ),
    material: str | None = Form(
        default=None,
        description="Filament: apex_core (default), apex_forge, apex_bounce, apex_light",
    ),
    _: None = Depends(require_api_key),
):
    try:
        material_spec = resolve_material(material)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    profile = get_profile(profile_id, material=material_spec.material_id)
    if profile is None:
        raise HTTPException(status_code=400, detail=f"Unknown profile_id: {profile_id}")

    suffix = Path(model.filename or "model.stl").suffix or ".stl"
    tmpdir = Path(tempfile.mkdtemp(prefix="vertexslice_upload_"))
    try:
        dest = tmpdir / f"model{suffix}"
        size = 0
        with dest.open("wb") as out:
            while chunk := await model.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="Upload too large")
                out.write(chunk)

        try:
            overrides = parse_slice_overrides(wall_loops, infill_density, print_direction)
            overrides["print_direction"] = validate_print_direction(print_direction)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if "wall_loops" not in overrides and material_spec.default_wall_loops is not None:
            overrides["wall_loops"] = material_spec.default_wall_loops

        gcode_path, stats = slice_stl(
            dest,
            profile,
            work_dir=tmpdir / "out",
            process_overrides=overrides,
            belt_insole_place=belt_insole_place,
            print_direction=overrides.get("print_direction"),
        )
        headers = {
            "X-Slice-Seconds": str(stats.get("elapsed_s", "")),
            "X-Peak-RSS-KB": str(stats.get("peak_rss_kb") or ""),
            "X-Profile-Id": profile_id,
            "X-Material": material_spec.material_id,
        }
        if overrides.get("wall_loops") is not None:
            headers["X-Wall-Loops"] = str(overrides["wall_loops"])
        if overrides.get("sparse_infill_density"):
            headers["X-Infill-Density"] = overrides["sparse_infill_density"]
        if overrides.get("print_direction"):
            headers["X-Print-Direction"] = overrides["print_direction"]
        return PlainTextResponse(
            content=gcode_path.read_text(encoding="utf-8", errors="replace"),
            media_type="text/plain",
            headers=headers,
        )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
