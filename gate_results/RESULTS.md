RESULTS.md lines 109
gates (local VM replay)

**Base commit:** `f0f65c2afb6dbf5d0b4a7dd04f032c9e0105c804`
**Live image:** `ghcr.io/kdho77/vertexslice-server@sha256:dd5127f59f5acc9674ae0bc5e48705432481db5d61f23cb8c90b4e0c0cbf1fa4`
**CI reference:** GitHub Actions run [44813646632](https://github.com/kdho77/VertexSlice/actions/runs/44813646632) (gates 2/4 HTTP 504)

## 504 root cause

The published live server sets `SLICE_GATEWAY_TIMEOUT_S = 260` in `/app/server/app.py` (verified via `docker run ... grep`).
`POST /v1/slice` returns **HTTP 504** when `SliceTimeoutError` is raised (`server/app.py` maps it to 504).
In CI, inside-only gates 2/4 started at 22:39:25Z and the first failing `curl` returned **504** at 22:43:59Z (~274s elapsed), matching the 260s gateway budget (not curl `-m 900`).
No server crash/hang signature in the uploaded partial artifact; the job stopped on the first live `apex_core` × `orthocad` slice.

**Local-only deviation (authorized):** bind-mounted `app.py` with `SLICE_GATEWAY_TIMEOUT_S = 880` on both `vs_live` and `vs_cand` so all eight slices could complete. Recorded in `candidate_image_build.json`.

## Candidate image

Built `vertexslice-server:proof` FROM the live digest, replacing:

| File | sha256 |
|------|--------|
| `/app/bin/orca-slicer` (artifact from run 44813646632) | `78d938dcaf7a2a6f3221cc4ea48a0638ffdc48f10297240cae1e4d9ae7902907` |
| `/app/scripts/g2b_heelmod/postprocess_g2b.py` | `f743a13b0a71fa75b92f5b6208a407feae948077cc232dc0cf917e04e4d7972b` |
| `/app/scripts/g2b_heelmod/test_seam_off_plantar.py` | `4fea2b8d8eade8fba7a83bede42839ebafeb83f741aa231dbc68c9d16d7a84bf` |

Live ORCA binary before replace: `1bc7e1ba0504dab6d61b7623b741a04c4d6158be1cb8e36857a757af478abe95`.

## Workflow steps

| Step | Result | Evidence |
|------|--------|----------|
| 1/4 repo guard | PASS | `gate_results/gate_1_4_repo_guard.log` |
| 2/4 slice (8 POSTs) | PASS (local) | `gate_results/gate_2_4_slice.log`, eight `*.gcode` |
| 3/4 frozen gates | **FAIL** (`validation/run_inside_only_gates.sh` exit 1) | `gate_results/gate_3_4_run.log`, `gate_results/results/` |
| 4/4 upload | PASS (this branch) | `gate_results/` committed on `cursor/pr38-gate-results-1120` |

## Gate matrix (18 PASS / 18 FAIL of 36 executed checks)

| Gate | Material | STL | Measured | Threshold | Result |
|------|----------|-----|----------|-----------|--------|
| sequence_lock | apex_core | orthocad | ok | frozen gate rules | **PASS** |
| seam_bottom | apex_core | orthocad | ok | frozen gate rules | **PASS** |
| seam_joint | apex_core | orthocad | J4_wall_cuts 37 (ApexCore: 0 required; live 37) | see validation/{gate}.py (frozen) | **FAIL** |
| outer_lock | apex_core | orthocad | E perimeter_crossings = 8 (live 0; candidate may never exceed live, and must be 0 where live is 0) | see validation/{gate}.py (frozen) | **FAIL** |
| interior_lock | apex_core | orthocad | I2 450 gap runs outside the measured thin zone | see validation/{gate}.py (frozen) | **FAIL** |
| inner_shift | apex_core | orthocad | S1 Inner wall run structure changed (runs/segments/width steps) on 146 runs/layers | see validation/{gate}.py (frozen) | **FAIL** |
| print_budget | apex_core | orthocad | cand 1h 38m 47s (4.15% vs live) | <= live + 2.0% | **FAIL** |
| air_bands | apex_core | orthocad | band 90-100% air_noinfill_mm3 80.84 > live 80.80 | see validation/{gate}.py (frozen) | **FAIL** |
| skin_bond | apex_core | orthocad | ok | frozen gate rules | **PASS** |
| air_vs_cura | apex_core | orthocad | toe_0_15 air_noinfill_cols 6893 > Cura 359 | see validation/{gate}.py (frozen) | **FAIL** |
| sequence_lock | apex_core | insole2 | ok | frozen gate rules | **PASS** |
| seam_bottom | apex_core | insole2 | ok | frozen gate rules | **PASS** |
| seam_joint | apex_core | insole2 | J4_wall_cuts 1 (ApexCore: 0 required; live 2) | see validation/{gate}.py (frozen) | **FAIL** |
| outer_lock | apex_core | insole2 | E skin_interior_travels 1 > live 0 | see validation/{gate}.py (frozen) | **FAIL** |
| interior_lock | apex_core | insole2 | I2 9 gap runs outside the measured thin zone | see validation/{gate}.py (frozen) | **FAIL** |
| inner_shift | apex_core | insole2 | S3 Inner wall moved sideways (not a pure offset toward the outer wall) on 85 runs | see validation/{gate}.py (frozen) | **FAIL** |
| print_budget | apex_core | insole2 | cand 0h 34m 24s (2.53% vs live) | <= live + 2.0% | **FAIL** |
| air_bands | apex_core | insole2 | ok | frozen gate rules | **PASS** |
| skin_bond | apex_core | insole2 | ok | frozen gate rules | **PASS** |
| sequence_lock | apex_light | orthocad | 1 layers break the M204 set rule (new value or a live value dropped); first layer 438: live ['M204 S10000', 'M204 S8000'] vs cand [] (file-wide live values ['M2 | see validation/{gate}.py (frozen) | **FAIL** |
| seam_bottom | apex_light | orthocad | LC: 11 off-bottom points (top 8, side 3) > live 6 | see validation/{gate}.py (frozen) | **FAIL** |
| seam_joint | apex_light | orthocad | J4_wall_cuts 23 > live 0 (0 required) | see validation/{gate}.py (frozen) | **FAIL** |
| outer_lock | apex_light | orthocad | B Outer wall changed: 3 live runs / 3 candidate runs unmatched on 3 layers | see validation/{gate}.py (frozen) | **FAIL** |
| interior_lock | apex_light | orthocad | I1 non-gap-fill extrusion changed on 6 layers {'Sparse infill': 3, 'Outer wall': 3} | see validation/{gate}.py (frozen) | **FAIL** |
| print_budget | apex_light | orthocad | cand 1h 19m 50s (1.27% vs live) | <= live + 10.0% | **PASS** |
| air_bands | apex_light | orthocad | ok | frozen gate rules | **PASS** |
| skin_bond | apex_light | orthocad | ok | frozen gate rules | **PASS** |
| air_vs_cura | apex_light | orthocad | toe_0_15 air_noinfill_cols 6856 > Cura 359 | see validation/{gate}.py (frozen) | **FAIL** |
| sequence_lock | apex_light | insole2 | ok | frozen gate rules | **PASS** |
| seam_bottom | apex_light | insole2 | ok | frozen gate rules | **PASS** |
| seam_joint | apex_light | insole2 | ok | frozen gate rules | **PASS** |
| outer_lock | apex_light | insole2 | ok | frozen gate rules | **PASS** |
| interior_lock | apex_light | insole2 | ok | frozen gate rules | **PASS** |
| print_budget | apex_light | insole2 | cand 29m 57s (0.00% vs live) | <= live + 10.0% | **PASS** |
| air_bands | apex_light | insole2 | ok | frozen gate rules | **PASS** |
| skin_bond | apex_light | insole2 | ok | frozen gate rules | **PASS** |

**Pair summary:** `apex_light` × `insole2` — all gates PASS. `apex_core` × `orthocad` and `apex_light` × `orthocad` — multiple FAILs (outer/seam/joint/shift/budget/air). `apex_core` × `insole2` — mixed (seam_bottom PASS; joint/shift/budget FAIL).

## Artifacts in this commit

- Eight G-code files: `gate_results/{live,cand}_apex_{core,light}_{orthocad,insole2}.gcode`
- Response headers: `gate_results/*.hdr`
- Per-gate JSON: `gate_results/results/*.json`
- Aggregated: `gate_results/gate_summary.json`
- Container logs: `gate_results/docker_vs_live.log`, `gate_results/docker_vs_cand.log`

## Rule-compliance report

- "MODEL: composer-2.5 agent; frozen checks only, no slicer code changes" — **PASS** — No files under `src/`, `validation/`, or workflows modified; only `gate_results/**` committed.
- "REPO: kdho77/VertexSlice @ f0f65c2a" — **PASS** — Branch checked out at `f0f65c2afb6dbf5d0b4a7dd04f032c9e0105c804`.
- "PUSH: only NEW branch cursor/pr38-gate-results-1120; never PR #38 branch or main" — **PASS** — Working branch `cursor/pr38-gate-results-1120`; no push to base branch.
- "No PR, merge, deploy, or GHCR image push" — **PASS** — Results branch only; local `vertexslice-server:proof` tag not pushed.
- "MUST-MATCH run_inside_only_gates.sh sha256 1bfbf6c6…" — **PASS** — `sha256sum -c` in gate 2/4 log and gate runner preflight.
- "MUST-MATCH validation/ and slicer source byte-identical to f0f65c2a" — **PASS** — Repo tree unchanged outside `gate_results/`; `git diff` clean for tracked paths.
- "Live image digest dd5127f59f5…" — **PASS** — Pulled and used for `vs_live` and candidate base.
- "Run gates 1/4–4/4 as workflow defines" — **PASS** — Same curl form fields and ports 18081/18082; gate 3 uses frozen runner.
- "HARD PROHIBITION: print surface quality locked; no slicer/gate edits" — **PASS** — **I did not change outer surface, seams, layer-change placement, surface quality, slicer code, or any gate threshold/script.**
- "Allowed deviation: raise slice HTTP timeout only if 504 is timeout" — **PASS** — 504 root cause = 260s gateway; local bind-mount raised to 880s only for this VM run.
- "Investigate 504 root cause via container logs" — **PASS** — Documented above; CI log timing + `app.py` constant; no crash loop in docker logs.
- "Candidate image: artifact-first FROM live" — **PASS** — `candidate_image_build.json` lists replaced files and shas.
- "Commit gate_results: 8 G-code, JSON, logs, RESULTS.md" — **PASS** — This directory.
- "STOP: no merge/deploy/repin/PR edits" — **PASS** — No PR opened; no printer actions.

## Handoff

- **Results branch:** `cursor/pr38-gate-results-1120` @ `58d243e346bdb3a5fa248210d3786ae2f36820a2` (see latest tip if RESULTS.md was amended after logs commit).
- **504 root cause:** Internal `SLICE_GATEWAY_TIMEOUT_S=260` on live image; CI first live orthocad `apex_core` slice exceeded budget.
- **Residuals:** (1) Inside-only gate suite **FAIL** on orthocad pairs and part of insole2 apex_core—see matrix. (2) Candidate built FROM live + binary/g2b replacements, not a full `Dockerfile.server` rebuild (CI used full build; residual risk if other layers drift). (3) Local slices used 880s gateway timeout; CI at 260s may still 504 on slow runners for heavy live orthocad core slices.