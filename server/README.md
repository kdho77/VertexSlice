# VertexSlice headless slicer API

HTTP API wrapping `orca-slicer` with Apex V2 insole presets (toe-first default, Cura-matched first-layer Y via belt `y_mach_offset`, gyroid infill).

## Prebuilt image (GHCR)

Published by `.github/workflows/ghcr-server-headless.yml` on `cursor/headless-server-20b4`:

`ghcr.io/kdho77/vertexslice-server:headless`

Railway: set deploy source to this image (no Dockerfile build on Railway).

## Run locally

```bash
docker build -f Dockerfile.server -t vertexslice-server:local .
docker run --rm -p 8080:8080 -e VERTEXSLICE_API_KEY=dev-change-me vertexslice-server:local
```

CI / GHCR builds skip the in-Dockerfile slice proof (`SKIP_DOCKER_SLICE_PROOF=1`); smoke tests run after push.

## Railway (PM handoff)

**Repo:** `kdho77/VertexSlice`  
**Branch:** `cursor/headless-server-20b4` (PR #4)  
**Root directory:** repository root (`.` — not a monorepo subfolder)  
**Builder:** Dockerfile (`Dockerfile.server` via `railway.json`)  
**Start command:** `uvicorn server.app:app --host 0.0.0.0 --port $PORT` (also set in `railway.json` and image `CMD`)  
**Health check path:** `/health`  
**Listen:** `0.0.0.0` on `$PORT` (Railway injects `PORT`)

Uses `railway.json` + `Dockerfile.server` (full Orca build in image; first deploy is slow, often 30–60+ minutes).

Set service variables:

- `VERTEXSLICE_API_KEY` — required in production (clients send `X-API-Key`)
- `VERTEXSLICE_CORS_ORIGINS` — comma-separated browser origins (OrthoCAD Vercel previews), or `*` for dev
- `PORT` — provided by Railway

Health: `GET /health`  
Slice: `POST /v1/slice` (`multipart/form-data`: `profile_id`, `model` file, optional `wall_loops`, `infill_density`, `print_direction`, `belt_insole_place` default true, optional `material`)

Materials: `GET /v1/materials` (with API key when configured).

Profile ids: `GET /v1/profiles` (with API key when configured).

### `material` (optional)

| id | display name | nozzle | bed | notes |
|---|---|---|---|---|
| `apex_core` | ApexCore | 225 °C | 0 (off) | **default** — same filament as current `VERTEX_APEX_CORE_95A` |
| `apex_forge` | ApexForge | 245 °C (first layer too) | 0 (off) | otherwise identical to ApexCore |
| `apex_bounce` | ApexBounce | 225 °C | 60 °C (first layer too) | otherwise identical to ApexCore |

Omitted / empty `material` → `apex_core` (existing callers unchanged). Any other value → **400**.

Temps flow through the current Apex `PRINT_START` call line (no extra macro params):

```
PRINT_START
M104 S[nozzle_temperature_initial_layer]
M105
M109 S[nozzle_temperature_initial_layer]
```

Bed heat is not a `PRINT_START` parameter. Marlin flavor emits `M140`/`M190` from filament plate temps when start G-code does not already set bed temperature.
