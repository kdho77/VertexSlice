# VertexSlice headless slicer API

HTTP API wrapping `orca-slicer` with Apex V2 insole presets (toe-first default, Cura-matched first-layer Y via belt `y_mach_offset`, gyroid infill).

## Run locally

```bash
docker build -f Dockerfile.server -t vertexslice-server:local .
docker run --rm -p 8080:8080 -e VERTEXSLICE_API_KEY=dev-change-me vertexslice-server:local
```

## Railway

Uses `railway.json` + `Dockerfile.server` (full Orca build in image; first deploy is slow).

Set service variables:

- `VERTEXSLICE_API_KEY` — required in production (clients send `X-API-Key`)
- `VERTEXSLICE_CORS_ORIGINS` — comma-separated browser origins (OrthoCAD Vercel previews), or `*` for dev
- `PORT` — provided by Railway

Health: `GET /health`  
Slice: `POST /v1/slice` (`multipart/form-data`: `profile_id`, `model` file, optional `wall_loops`, `infill_density`, `print_direction`, `belt_insole_place` default true)

Profile ids: `GET /v1/profiles` (with API key when configured).
