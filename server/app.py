"""VertexSlice headless slicing HTTP API."""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse

from server.profile_registry import get_profile, list_profiles
from server.slice_engine import slice_stl

API_KEY = os.environ.get("VERTEXSLICE_API_KEY", "dev-change-me")
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", str(50 * 1024 * 1024)))

app = FastAPI(title="VertexSlice Headless Slicer", version="0.1.0")


def require_api_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> None:
    if not API_KEY or API_KEY == "dev-change-me":
        return
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")


@app.get("/health")
def health():
    return {"status": "ok", "service": "vertexslice-slicer"}


@app.get("/v1/profiles")
def profiles(_: None = Depends(require_api_key)):
    return {"profiles": list_profiles()}


@app.post("/v1/slice", response_class=PlainTextResponse)
async def slice_endpoint(
    profile_id: str = Form(..., description="e.g. apex-v2-insole-95a"),
    model: UploadFile = File(...),
    _: None = Depends(require_api_key),
):
    profile = get_profile(profile_id)
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

        gcode_path, stats = slice_stl(dest, profile, work_dir=tmpdir / "out")
        headers = {
            "X-Slice-Seconds": str(stats.get("elapsed_s", "")),
            "X-Peak-RSS-KB": str(stats.get("peak_rss_kb") or ""),
            "X-Profile-Id": profile_id,
        }
        return PlainTextResponse(
            content=gcode_path.read_text(encoding="utf-8", errors="replace"),
            media_type="text/plain",
            headers=headers,
        )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
