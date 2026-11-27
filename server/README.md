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
- `VERTEXSLICE_JOB_STORE` — `memory` (default), `redis`, or `postgres`. See Slice jobs.
- `VERTEXSLICE_REDIS_URL` — required when the store is `redis` (for example `redis://host:6379/0`)
- `VERTEXSLICE_JOB_DATABASE_URL` — required when the store is `postgres` (libpq URL)
- `VERTEXSLICE_JOB_RETENTION_S` — optional finished-job lifetime in seconds. Values below 3600 are raised to 3600.

Health: `GET /health`  
Slice: `POST /v1/slice` (`multipart/form-data`: `profile_id`, `model` file, optional `wall_loops`, `infill_density`, `print_direction`, `belt_insole_place` default true, optional `material`)

`model` is `model.stl` when the design has no hardness regions (same request as before). When OrthoCAD has regional hardness, `model` is `model.3mf` (`model/3mf`, or a ZIP body): one main solid plus each region as an Orca `modifier_part` whose `sparse_infill_density` is an infill preset (14%, 20%, 26%, 32%, 38%) and whose `sparse_infill_pattern` is `gyroid`. `infill_density` is the global density for the main solid. After the slice, sparse infill inside each modifier's placed volume is measured against the surrounding base. `X-VertexSlice-Regions` and `X-VertexSlice-Regions-Verified` are the count whose measured density matches the requested preset. A 3MF that names modifiers but yields none, or whose G-code does not show that density, is HTTP 422.

### Slice jobs

One in-process worker runs slices off the HTTP event loop, in FIFO order. `POST /v1/slice` still waits on that worker and returns the same G-code body and headers as before. `POST /v1/slice/jobs` validates the same multipart fields, then returns while the worker is busy. Queue time is not slice time: it is not part of `elapsed_s`, and it is not part of the 260s slicer kill.

Auth on every job route is the same `X-API-Key` check as `POST /v1/slice`. Validation errors (unknown profile, bad material, bad override, upload over the limit) return the same 4xx immediately and do not enter the queue.

`POST /v1/slice/jobs` → **202**

```json
{"job_id": "<hex>", "status": "queued", "position": 1}
```

`position` is 1-based at the moment of acceptance. It counts this job plus every unfinished slice ahead of it, including the one already running. Position 1 means the worker will take it next.

`GET /v1/slice/jobs/{job_id}` → **200**

```json
{
  "job_id": "<hex>",
  "status": "queued",
  "position": 1,
  "queued_at": "2026-11-23T17:00:00.000000Z",
  "started_at": null,
  "finished_at": null,
  "elapsed_s": null,
  "error": null
}
```

`status` is `queued`, `running`, `succeeded`, or `failed`. `position` is the live place in line while `queued`, and `null` otherwise. Timestamps are ISO-8601 UTC. `elapsed_s` is slice time only: null until the worker starts the slice, then `now - started_at` while it runs, then `finished_at - started_at`. `error` is null unless `failed`. A slicer kill is the same text the sync route returns as HTTP 504, and it names the hop (`Slice exceeded 260s`).

`GET /v1/slice/jobs/{job_id}/result`

- `succeeded`: the same `text/plain` body and the same `X-*` headers as `POST /v1/slice` for that slice.
- `queued` or `running`: **409** `{"detail": "job has not finished"}`.
- `failed`: **409** whose `detail` is the job `error`. A failed job stays available so the client can tell it from expiry.
- Unknown id, or a job finished more than 60 minutes ago: **404** `{"detail": "unknown or expired job"}`.

Finished jobs are kept for 60 minutes (`SLICE_JOB_RETENTION_S`, or `VERTEXSLICE_JOB_RETENTION_S` when that is higher). With the default `memory` store a restart drops them. With `redis` or `postgres` a restart of one replica does not drop them; the rows live until the retention window. The client treats **404** on an id it already received as failed and retryable.

The queue is a `JobStore` with `enqueue`, `claim`, `update`, `get`, and `result`. `claim` moves the oldest queued job to `running` in one atomic step (in-memory lock, one Redis `EVAL`, or one `UPDATE ... FOR UPDATE SKIP LOCKED`). Each process still runs one slice thread. A second process calling `claim` gets a different job, or nothing. `POST /v1/slice` stays on the process that accepted it and is not a shared row: its response has to be written on that connection, and a disconnect still kills only that process's slicer subprocess.

If the client drops a synchronous `POST /v1/slice` while that slice is the one on the worker, the server kills that slice's subprocess tree and takes the next item. A slice that already produced G-code still returns that body.

### Capacity

One worker, one slice at a time. Throughput is `3600/S` slices per hour, where `S` is one ApexCore slice on the CPU that runs it. A pair of feet is two jobs. Two orders, both feet, is four. Those jobs wait in line and finish; the wait does not fail them.

| S | slices/hour | queue for the 2nd job | queue for the 4th job |
|---|---|---|---|
| 164s | 22 | 164s | 492s |
| 270s | 13 | 270s | 810s |

164s is the last in-repo full slice of `validation/test_models/orthocad_sample_top_insole.stl` on an 8-core box, and >270s is the same ApexLight slice on a GitHub runner (`validation/seam_fuse_gates/slice_all.sh`, recorded in the 2026-11-23 timeout investigation). Both numbers are ApexLight, not ApexCore, and not the Railway service. Placement of that sample is 0.47s and the ApexCore post-process on a 4.5 MB reference is 2.5s, so the long stage is `orca-slicer`. This VM has no `orca-slicer` binary. ApexCore `S` on the production CPU was not measured here, and this change does not call that service.

The 260s process kill still applies to a single slicer invocation. A lone slice with `S` over that cap still fails. Raising the cap or resizing the service waits on a measured production `S`. A second slice thread on a CPU that is already full during one slice makes both slices slower.

`memory` is one process's queue. That is the right store for the current single replica, and it is the default, so this service does not need a new database to keep today's behavior. It cannot be shared: a second replica would have its own jobs, and a client that lands on the other replica gets **404** for an id the first replica accepted. At the ApexLight figures above, one replica finishes on the order of 300–500 slices a day (13/hour × 24 at 270s, 22/hour × 24 at 164s), so a day with thousands of slices needs more than one replica once `S` is in that range. Point every replica at the same store:

- `VERTEXSLICE_JOB_STORE=redis` and `VERTEXSLICE_REDIS_URL`, or
- `VERTEXSLICE_JOB_STORE=postgres` and `VERTEXSLICE_JOB_DATABASE_URL`

The application creates its Redis keys or its `vertexslice_slice_jobs` table on first use. It does not provision Redis, Postgres, or a Railway service. Pick the store that already exists for that deployment; do not add one from this change. Each replica still slices one job at a time. Throughput becomes about `replicas × 3600/S` only when those CPUs are not the same full machine. A claimed job stays `running` if that process dies mid-slice; the client should treat a job that never finishes as failed and submit again. There is no automatic reclaim, because reclaiming a live slice would run it twice.

Measured on this 4-vCPU VM on 2026-11-23, production calls 0, slicer patched so the worker blocks inside the slice call:

- `GET /health` during that blocked sync slice: max **0.0011s** over 10 calls (limit 1s). `python3 -m unittest tests.test_slice_jobs.SliceJobTests.test_health_answers_during_sync_slice`
- Same sample STL through `POST /v1/slice` and `GET /v1/slice/jobs/{id}/result`, slicer patched to return one fixed G-code file: bodies and `X-*` headers match. Both calls plus the two 3.6 MB uploads finished in **0.105s**.

Until callers use the job routes, `POST /v1/slice` still holds the connection for the whole slice and still shares this one worker. Overlapping sync calls can still hit the caller's own deadline. The poll path is what takes queue wait off that clock.

Materials: `GET /v1/materials` (with API key when configured).

Profile ids: `GET /v1/profiles` (with API key when configured).

### `material` (optional)

| id | display name | nozzle | bed | notes |
|---|---|---|---|---|
| `apex_core` | ApexCore | 225 °C | 0 (off) | **default** — same filament as current `VERTEX_APEX_CORE_95A` |
| `apex_forge` | ApexForge | 245 °C (first layer too) | 0 (off) | otherwise identical to ApexCore |
| `apex_bounce` | ApexBounce | 220 °C (first layer too) | 70 °C (first layer too) | ApexCore g2b chain; print speeds 0.8× Core. `ApexBounce.json` is not the base (it sets bed 60 °C). |
| `apex_light` | ApexLight | 260 °C | 0 (off) | foaming TPU (Cura `VERTEX_APEX_LITE_90A`): flow 72%, belt step 0.65 / layer 0.46, 2 walls, no retraction, fan 0→35% |

Omitted / empty `material` → `apex_core` (existing callers unchanged). Any other value → **400**.

Temps flow through the current Apex `PRINT_START` call line (no extra macro params):

```
PRINT_START
M104 S[nozzle_temperature_initial_layer]
M105
M109 S[nozzle_temperature_initial_layer]
```

Bed heat is not a `PRINT_START` parameter. Marlin flavor emits `M140`/`M190` from filament plate temps when start G-code does not already set bed temperature.
