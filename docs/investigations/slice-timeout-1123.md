# Slice timeouts and solid-to-G-code time (2026-11-23)

Investigation only. Read against VertexSlice `main` `7868bc9a6a34dec85993ac70cc0509ac79a1c67c` (the commit the live Railway image is built from) and OrthoCAD- `main` `268aac85847160b5b2703a05688721ea4cb93b24`. No slicer, profile, Railway, or production-order change. Production HTTP calls this run: 0 of 3.

## Answer

"Manufacturing timed out" is OrthoCAD writing a failed CAD-operator production after a timeout-shaped error. The slice itself is one synchronous `POST /v1/slice` on a single uvicorn worker. That handler blocks the process inside `orca-slicer` until the slice ends, and it sends no response bytes while it waits. A second Generate (the other foot, or a second order) is accepted by the edge and then sits in that wait. Every deadline in the chain — OrthoCAD's 240s slice cap, the server's 260s kill, the previously observed 270s close, Vercel's 300s function cap, and Railway's current 5-minute no-data limit — starts when the request is sent, so queue time counts as slice time. One job at a time fits. Two or more overlapping jobs pass a deadline.

The time that matters is `orca-slicer`. On this VM the belt placement of the repo's OrthoCAD sample insole took 0.47s, and the ApexCore post-process chain on a 4.5 MB / ~580-layer G-code file took 2.5s. Those stages are not the timeout. This VM has no `orca-slicer` binary and no Docker, so the slicer stage was not re-timed here. The last in-repo measurement of a full slice is ApexLight on the same sample: 164s on an 8-core box and over 270s on a GitHub runner.

## 1. Where the timeout is raised

There are five different timeouts. They fail the job at different layers and store different text.

| Order a single request hits them | Where | Value | What the caller stores |
|---|---|---|---|
| 1 | OrthoCAD CAD-operator slice deadline | `min(240s, 280s − loft time)` | `VertexSlice timed out after Ns (side)` |
| 2 | VertexSlice kills the slicer subprocess | 260s | HTTP 504, stored as `VertexSlice error (504): Slice exceeded 260s…` |
| 3 | Previously observed edge/client close | 270s, HTTP 499, deployment `2f4c2050` | Depends on how the fetch error is worded |
| 4 | Vercel function cap (`api/trpc`) | 300s | Isolate stops. Row can stay `running` until the 360s lease, then `Production lease expired` |
| 5 | Railway public edge, current docs | 5 min with no response bytes; 15 min if bytes keep flowing | Connection closed. No heartbeat is sent today |

The words **"Manufacturing timed out (side)"** are produced in one place: `publicProductionError` in OrthoCAD `vertex/server/src/lib/cad-operator-production.ts:449-450`. That branch runs when the error text matches `timeout`, `timed out`, `ETIMEDOUT`, `ECONNABORTED`, or `UND_ERR_CONNECT/HEADERS/BODY_TIMEOUT`, and the message is not already one of the VertexSlice labels above. The unit test at `cad-operator-production.test.ts:814-815` maps `connect ETIMEDOUT 203.0.113.1:443` to `Manufacturing timed out (right)`. The sanitizer then drops the underlying text, so the row does not say which hop timed out.

A slice that hits OrthoCAD's own deadline is labeled differently, on purpose. `cad-operator-production.ts:398-403` returns `VertexSlice timed out after Ns (side)` unchanged. `isUnclassifiedTimeoutMessage` (`:317-318`) skips that prefix. So a production row that says exactly "Manufacturing timed out" was an unclassified timeout (connect, headers, body, or a download), and a row that says "VertexSlice timed out after 240s (left)" was the slice deadline. Both are timeouts. The 2026-11-23 note names the first string; the stored row is the way to tell them apart. This run did not read production rows.

### Chain, from the button to the slicer

**MCP `generate_gcode` (saved design, the orders path).** `packages/mcp/src/tools/handlers.ts:314-337`. Omitting `side` enqueues left, then right, in one call. Each call awaits `generateFromSavedDesign`, which returns a production id and status within the enqueue, and does not wait for G-code. The tool description says to poll `get_production`. The MCP function itself is capped at 60s (`vertex/api/mcp.ts:11-24`) because it only enqueues.

**CAD-operator production.** `beginCadOperatorProduction` (`cad-operator-production.ts:752-778`) inserts a `pending` row and schedules the work with `waitUntil` (`:785-791`). `executeCadOperatorProduction` (`:642-700`) lofts the STL, then sets the slice deadline from time already spent:

- `CAD_PRODUCTION_MAX_DURATION_SEC = 300` (`:17`), matching Vercel Pro `maxDuration` on `vertex/api/trpc/[[...trpc]].ts:32-40` and `vertex/vercel.json:8-10`.
- `CAD_VERTEX_SLICE_SAFETY_BUFFER_SEC = 20` (`:20`), so the row can be marked failed before the platform kills the isolate.
- `CAD_VERTEX_SLICE_MAX_TIMEOUT_SEC = 240` (`:23`).
- `cadVertexSliceSliceTimeoutMs` (`:32-37`) returns `min(240s, 280s − elapsed)`.
- Lease is `(300 + 60) * 1000` ms (`:26`), so 360s.

The slice call passes that deadline and the foot (`vertex/server/src/routers/cad-operator-generate.ts:215-233`).

**Client fetch.** `vertex/server/src/lib/vertexslice-client.ts:409-418` arms a ref'd `AbortController`. The comment at `:406-407` records that `AbortSignal.timeout` did not reject the in-flight upload before Vercel killed the isolate, which is why the timer is manual. On abort, `:394-396` throws `VertexSlice timed out after ${timeoutSec}s (side)`. The same deadline covers `GET /v1/materials`, `POST /v1/slice`, and reading the G-code body (`:546-548`).

**Browser Generate / Export.** `runSessionGenerateSolid` calls `sliceWithVertexSlice` with profile, material, and bytes, and does not pass `sliceTimeoutMs` (`vertex/server/src/routers/manufacturing-session.ts:318-325`). That path holds the tRPC request open for the whole slice. The browser client (`vertex/src/lib/trpc.ts:34-36`) sets no request timeout. The cap is the 300s Vercel function. A failure there is a tRPC error in the export panel, not the production-row string "Manufacturing timed out".

**VertexSlice.** `POST /v1/slice` is `async def slice_endpoint` (`server/app.py:107`). It calls blocking `slice_stl` on the event loop (`:164-170`) with `timeout_s=SLICE_GATEWAY_TIMEOUT_S`. That constant is 260 (`server/app.py:36-38`). The comment says Railway's edge closed `/v1/slice` at 270s with HTTP 499 on deployment `2f4c2050`, and the handler stops early so it can still return an error on the open connection. `SliceTimeoutError` becomes HTTP 504 (`:178-179`).

ApexCore (`material=apex_core`) does not use the generic double slice. `server/chains/apex_core.py:83-105` runs five subprocesses with limits `[caller_timeout, 120, 120, 120, 60]`. The first is `scripts/g2b_heelmod/slice_apexcore_g2b.py`, and that process's limit is the 260s from the HTTP handler. Inside it, `slice_model` (`scripts/g2b_heelmod/slice_g2b_heelmod.py:241-258`) runs `orca-slicer` once, with its own 2400s timeout, which the parent kills first. Y offset is the locked constant `CORE_Y_OFFSET = -1.146` (`slice_apexcore_g2b.py:79`), so this path does not do the probe slice plus re-slice that `server/slice_engine.py:272-308` does when `VS_FORCE_OFFSET` is unset. One production ApexCore request is one `orca-slicer` process, then `postprocess_g2b.py match`, `pullback_v2.py`, `topfix_apexcore.py`, and `check_gcode.py`.

**Process model.** `railway.json:7` and `Dockerfile.server:99` start `uvicorn server.app:app --host 0.0.0.0 --port $PORT` with no `--workers`. Nothing in `server/` takes a lock or a semaphore. One process, one event loop, and the slice blocks that loop.

**Railway, as published today.** [Public networking specs](https://docs.railway.com/networking/public-networking/specs-and-limits): an HTTP request may run up to 15 minutes if data keeps transferring, and is otherwise closed after 5 minutes with no data. This handler writes the G-code only after the slice returns (`server/app.py:180-227`), so a live request is in the no-data case. The in-repo 270s / HTTP 499 note is a specific deployment observation. HTTP 499 means the downstream client closed before a response. That matches either an edge idle close or OrthoCAD aborting. Current Railway docs put the no-data close at 5 minutes, which is after the 240s client cap and the 260s server kill. The client cap is the first deadline on the CAD path.

## 2. Why two Generate jobs fail and one does not

A Generate on the saved-design path starts the slice and returns. Left and right of one order are two productions (`handlers.ts:314`). Two orders at once are four. `claimProduction` (`cad-operator-production.ts:180-206`) reuses an in-flight or succeeded row for the same design, foot, preset, and settings fingerprint. It does not serialize different feet or different orders. There is no global job queue.

On the server the requests pile up in order:

1. The first `POST /v1/slice` enters `slice_endpoint` and blocks the worker in `subprocess.run` for the whole `orca-slicer` run.
2. The next connections are accepted by the kernel and by Railway. The event loop does not read them until the first handler returns, so `/health` waits too (`server/app.py:62-64`).
3. The second request's OrthoCAD timer and Railway's no-data timer are already running. They include the wait.
4. When the client aborts, the server is still inside `subprocess.run`. Nothing in the handler watches disconnect, so the abandoned slice keeps the only worker until the slicer exits or the 260s kill fires. Jobs behind it wait on work whose caller has already given up.

One job at a time starts its 240s clock when its own slice starts, and a single ApexCore slice that finishes under that cap succeeds. Overlap makes the later job's clock include the earlier slices.

Let `S` be one ApexCore slice, in seconds, on the production CPU. The CAD cap is at most 240s, and loft has already spent some of the 280s Vercel budget before the fetch starts. Then:

- 1 job succeeds when `S` is under that cap. The PM's serial run says production `S` is under the cap.
- 2 overlapping jobs need about `2S` on the second clock. That fails when `S` is over ~120s.
- 2 orders × left and right, all in flight, need about `4S` on the last clock.

The in-repo ApexLight number on the sample insole (164s on 8 cores, over 270s on a GitHub runner, `validation/seam_fuse_gates/slice_all.sh:7-8`) sits in the band where one slice fits on a fast box and a second overlapping slice does not. That number is ApexLight, on a different chain, and it was not re-measured here. It is the reason the 260s constant exists. ApexCore on the Railway CPU is the number still missing. The repo does not record that service's vCPU or RAM.

The same queue applies to the browser Export path. Two session `generateSolid` calls hold two tRPC requests against the same worker. Their cap is 300s because they pass no slice deadline.

## 3. Time breakdown

Production shape from the PM: profile `apex-v2-insole-95a`, `belt_insole_place=true`, `material=apex_core`, about 439 layers. Locked layer height is 0.46 mm (`scripts/g2b_heelmod/apexcore_95a_locked.json:26` and `:38`), so 439 layers is about 202 mm along the belt.

| Stage | Where | Measured this run | Share of a multi-minute slice |
|---|---|---|---|
| Mesh export in the browser / loft on Vercel | OrthoCAD, before the slice fetch | Not timed (no patient mesh, no app server) | Eats the 280s budget before the 240s cap is applied. A 30s loft makes the slice cap 240s still; a 100s loft makes the slice cap 180s (`cadVertexSliceSliceTimeoutMs`). |
| STL upload to storage, then download into the function | `downloadManufacturingStl` | Not timed | Seconds for a few MB. A `connect ETIMEDOUT` here is stored as "Manufacturing timed out" (`manufacturing-stl-download.ts:10-16` plus the sanitizer). |
| Belt placement | `prepare_orthocad_insole_stl` | **0.474 s** | Negligible |
| `orca-slicer` | `slice_model`, one process | **Not measured this run** | This is the long stage. Prior ApexLight sample: **164 s** (8-core) and **>270 s** (GitHub runner). |
| `postprocess_g2b.py match` | ApexCore chain stage 2, limit 120s | **1.642 s** | Negligible |
| `pullback_v2.py` | stage 3, limit 120s | **0.490 s** | Negligible |
| `topfix_apexcore.py` | stage 4, limit 120s | **0.338 s** | Negligible |
| `check_gcode.py` | stage 5, limit 60s | **0.053 s** | Negligible |
| G-code body back to Vercel, then storage upload | response is the full text (`server/app.py:180`, `:223`) | Not timed | A 4.5 MB body is seconds. It still sits inside the same open request. |

Local method, this VM (4 vCPU, no Docker, no `orca-slicer`, production calls 0):

- `validation/test_models/orthocad_sample_top_insole.stl`: binary STL, 74,940 triangles, 3,747,084 bytes. `prepare_orthocad_insole_stl(..., x_center_mm=140, print_direction="toe_first")` → 0.474 s, rotation 270°, X center 140 mm, belt length 273.104 mm, width 92.695 mm, height 25.24 mm. At 0.46 mm that length is about 594 layers, longer than the PM's 439-layer production insole. `insole_sample.stl` is a 12-triangle stub and was not used as a timing model.
- `validation/ref/cura_apexcore.gcode`: 4,549,058 bytes, 154,048 lines, 580 layer markers. Ran the chain into `/tmp/slice-timing` (not the repo): match 1.642 s (`efix` 131171, `seam_travel` 1729), pullback 0.490 s, topfix 0.338 s (removed 0), check 0.053 s. Check exited 1 with `CONFIG_BLOCK missing` because this file is a Cura reference, not an Orca chain product. The three mutating stages together are 2.47 s. They are the right order of magnitude for "pure Python after the slicer" (`apex_core.py:83-84`). They are not a production G-code and were not compared to one.

Upload, download, and the return trip are bounded by file size (sample STL 3.6 MB, reference G-code 4.5 MB) and are not the multi-minute part. The unmeasured part is one `orca-slicer` run of a ~439-layer gyroid insole on the Railway CPU.

## 4. Ranked recommendations

None of these change layer height, infill, walls, seams, or speeds. The ApexCore chain stays `slice_apexcore_g2b.py` → `match` → `pullback_v2.py` → `topfix_apexcore.py` → `check_gcode.py`.

### 1. Slice as a job, and poll it

**Gain.** Removes the timeout class. One slice still takes `S`. Two orders no longer fail because their HTTP clocks include the queue. This matches the path that already exists: `generate_gcode` returns a production id, and the caller polls `get_production`.

**What is wrong today.** The poll is only in front of Vercel. Inside `waitUntil`, OrthoCAD still holds one `POST /v1/slice` open for the whole slicer run, on a 240s cap, against one blocking worker.

**Shape.** `POST /v1/slice` stores the upload and returns a job id immediately (202). A worker process runs the existing `run_apex_core` function. `GET /v1/slice/{id}` returns status, then the G-code (or a storage URL). OrthoCAD's `waitUntil` function submits and returns; a later short invocation, or the poll the operator already does, collects the result. The 240s / 260s / 300s clocks then bound one HTTP round trip, not the slice.

**Risk.** G-code bytes stay the same if the worker calls the same chain with the same inputs. Failure mode to test: a retry must not start a second slicer on the same job, and a failed job must still mark the production failed.

**Effort.** New server route plus a worker, and the CAD slice callback stops waiting on the POST. No profile edit.

### 2. If the HTTP call stays synchronous, do not park it in the queue

**Gain.** A busy server answers at once (429 or 202 with queue position) so the caller's 240s clock does not include someone else's slice. Combined with (1), this is the admission rule. Alone, it stops the pile-up where a timed-out client still occupies the worker.

**Also.** Run the subprocess off the event loop (`asyncio.to_thread` or a process), and kill that subprocess when the client disconnects. Today the handler cannot notice a disconnect until `subprocess.run` returns, so an aborted request still burns a full slice of the only worker.

**Risk.** G-code unchanged for the request that actually runs. A client that ignores 429 and opens more connections would need the server to keep the concurrency cap at 1 until the CPU is known to handle more.

**Effort.** Smaller than a full job API, and it does not by itself let a 200s slice finish inside a 240s call that started 200s late. The caller still has to retry or poll.

### 3. One measured ApexCore `S` on the production CPU, then set the single-job cap from it

**Gain.** Tells us whether a lone slice is close to 240s or comfortably under it. Raising the CAD cap from 240s toward the 280s Vercel remainder only helps a slice that already lands between 240 and 280. It does not fit two serialized slices of 164s. Railway's 15-minute window applies only while response bytes flow; this handler sends none until the end. A comment heartbeat would keep the 5-minute no-data timer from being the one that fires, and it would not change G-code. It also would not beat the 240s client abort. Do that only as a bridge if a single slice is measured over 260s and under the Vercel cap.

**Risk.** None to G-code. The 260s constant is what stops the process so the handler can still answer; moving it without a measured `S` just changes which layer reports the failure.

**Effort.** One private timing run of the existing image on `orthocad_sample_top_insole.stl` (and, if a non-patient fixture of ~439 layers exists, that too). Not against the live server.

### 4. More CPU, and a second worker only after that measurement

**Gain.** Wall-clock `S` drops as cores are added, until the slicer stops scaling. That is the way to shorten one solid-to-G-code run without touching the toolpath. A second uvicorn worker, or parallel left/right, is safe when two slices at once on that box still finish inside the single-job cap. On a box where one slice already uses the CPU, a second worker makes both slower and can push both over 240s. The PM result (serial works, overlap fails) is what a queue of one worker looks like, so adding workers is not the first change.

**Risk.** G-code unchanged if the profile is unchanged. OOM if memory is tight; peak RSS is already returned as `X-Peak-RSS-KB` (`server/app.py:207`) and was not collected here.

**Effort.** Railway service size. Out of this repo. Do not do it in the same change as a profile edit.

### 5. Leave the post-process and the placement alone

**Gain.** About 2.5s plus 0.5s, against a slice of minutes. Not a timeout fix.

**Risk.** Editing `postprocess_g2b.py`, `pullback_v2.py`, or `topfix_apexcore.py` to "make them faster" can change travels and seams. That is the print. Don't.

### Already in place

Identical design + foot + preset + settings reuses the in-flight or succeeded production (`claimProduction`). Left and right are different meshes, so a pair is two slices. `force_new_slice` still reuses an in-flight row (`cad-operator-production.ts:188-192`).

## Out of scope — would change the printed part

Listed because they would cut slicer or print time. They are not proposals. R1 and R2 lock the top surface, the sides, and seam placement on the belt face.

- Layer height 0.46 mm, gyroid infill, `wall_loops` 2, line width, flow ratios, speeds, and `APEX_MIN_LAYER_TIME` (`Dockerfile.server:64`, default 4). Locked in `scripts/g2b_heelmod/apexcore_95a_locked.json`.
- Seam position, wipe, retract, and the match / pullback / topfix passes.
- Any "fewer walls" or "sparser infill" or "thicker layers" change.

## Open questions

1. On the two failed orders, is the production `error` exactly `Manufacturing timed out (left|right)`, or `VertexSlice timed out after Ns (side)`, or `VertexSlice error (504)`? Those three are different hops. The generic string hides the hop.
2. What is `S` for ApexCore on the Railway service that runs image digest `sha256:3c217579…`? This run did not call that service and could not run the binary locally.
3. What vCPU and memory is that service? It is not in `railway.json`.
4. Did those Generate calls omit `side` (left and right both enqueued) or wait for one foot before the next?

## Recommended next step

Ship nothing that changes G-code. Next change, in a separate PR: job + poll (recommendation 1), with the server still calling `run_apex_core`. Before or with it, one private timing of the current image on the repo sample STL, recorded as `S`, so the single-job cap is a measured number. Ask Kendon before any profile, seam, or layer change.
