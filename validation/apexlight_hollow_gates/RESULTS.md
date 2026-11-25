# ApexLight hollow-band gate results

Gates frozen before any candidate grade. Gate code checksum: `sha256sum -c validation/apexlight_hollow_gates/SHA256SUMS` from the repo root. `RESULTS.md` is the grade record and is not in that checksum.

## No-op and bad-change proof

Command:

```bash
python3 validation/apexlight_hollow_gates/prove_gates.py \
  --live-light live_apex_light_orthocad.gcode \
  --live-core cand_apex_core_orthocad.gcode \
  --slice-seconds 158.672
```

Exit 0. Full rows are in `proof/live_noop.json`.

Live files, docker-server-proof run 45295959179, main `d935197976b7cdd2fa89ced4ff43930b0212959f`. ApexLight live and main's ApexLight are the same bytes after the stamp strip. ApexCore is main's 439-layer slice (the #46 file), not the older pinned image.

| Gate | Live ApexLight vs itself | Bad change |
|---|---|---|
| H hollow | FAIL. Forefoot layers <= 165: 1387. Physical window 165: 1387. Total 5683. Max air 11.633 mm. | Empty >= 3 mm section rejected (240 columns). |
| G1 | FAIL. 14377 / 14813 unbonded. Median gap +0.340 mm. | Thin unbonded section rejected (120 / 120). |
| G2 | FAIL. 8648 / 9003 unbonded. Belt air 91.8%. Dorsal air 82.4%. | Thick unbonded section rejected (120 / 120). |
| S surface | PASS. 30774 wall segments, 436 seams, 0 moved. | Seam on the dorsal edge rejected. Layer change off the belt rejected. One outer-wall X moved 0.2 mm rejected (2 segments). |
| F filament | PASS. Live 26227.75 mm, 75.70 g. Increase 0. Nothing outside the outline. | Bead outside the outline rejected (12 samples, also not inside a hollow section). |
| T slice time | PASS at 158.672 s and at the PM's 176.059 s. Cap 260 s. | 261 s rejected. A missing time is rejected. |
| C ApexCore | PASS. Main vs itself, stamp exempt. Forefoot hollow on that file is 0. | One motion byte rejected. A stamp-only timestamp change passes. |

The PM measured forefoot hollow 1406 and total 6210 on this fixture, with the same max air 11.633 mm, the same G1 14377 / 14813, and the same G2 8648 / 9003 at 91.8% / 82.4%. This tool's hollow count is 1387 / 5683. ApexCore forefoot hollow is 0, which matches the PM. The pass line is 0, so the no-op fails on either count.

Main ApexCore footer: 29857.71 mm, 86.18 g, 439 layers. ApexLight footer: 26227.75 mm, 75.70 g, 439 layers.

## Worker grade

Graded after the gates were frozen. Gate code was not edited. `sha256sum -c validation/apexlight_hollow_gates/SHA256SUMS` still matches.

Worker: draft [#54](https://github.com/kdho77/VertexSlice/pull/54), SHA `83c943ba9d36549703ad5f7d57ad177ce5022be7`, branch `cursor/find-an-apexlight-hollow-fill-method-that-keeps-every-layer-change-and-seam-station-byte-identical-t-c48a`. Diff against `d935197` is only `server/chains/apex_light.py`, `server/chains/apex_light_forefoot_fill.py`, and `tests/test_apex_light_forefoot_fill.py`. ApexCore and the slicer binary are not in that diff.

Candidate ApexLight is that SHA's `fill_forefoot_hollow` applied to live `live_apex_light_orthocad.gcode` from run 45295959179 (the file finalize already wrote). The function reported hollow_before 1387, hollow_after 0, 35 layers, added 71.14956 mm, wall time 1.284 s. Candidate ApexCore is main's #46 file, the same bytes as the live core baseline, because this SHA does not change the ApexCore chain.

Slice seconds passed to the gate: 159.956 = main docker-proof slicer clock 158.672 s plus the measured fill 1.284 s. The PM host clock 176.059 s plus the same fill is 177.343 s. Both are under 260 s. `elapsed_s` in `run_apex_light` includes this fill.

Command:

```bash
python3 validation/apexlight_hollow_gates/run_gates.py \
  --light-base live_apex_light_orthocad.gcode \
  --light-cand cand_apex_light_83c943ba.gcode \
  --core-base cand_apex_core_orthocad.gcode \
  --core-cand cand_apex_core_orthocad.gcode \
  --slice-seconds 159.956
```

Exit 1. Overall FAIL.

| Gate | Result | Residual |
|---|---|---|
| H hollow | PASS | Forefoot layers <= 165: 0. Physical window 165: 0. Total 4296, all on layers > 165. Max air 11.633 mm. |
| G1 | FAIL | 14377 / 14813 unbonded. Median gap +0.340 mm. Unchanged from live. |
| G2 | FAIL | 8604 / 9003 unbonded (live was 8648). Belt air 91.2%. Dorsal air 81.8%. |
| S surface | PASS | 30774 wall segments, 436 seams, 0 missing, 0 added, 0 off the bottom. 0 layer changes moved. |
| F filament | PASS | Live 26227.75 mm, 75.70 g. Candidate 26298.90 mm, 75.91 g. Increase 71.15 mm. 1630 added extrusions, 0 bad type, 0 outside a hollow section, 0 outside the outline. |
| T slice time | PASS | 159.956 s (cap 260 s). PM-host equivalent 177.343 s. |
| C ApexCore | PASS | Stamp-stripped SHA256 `0557d875933ce16a24151b2e4c6e88275528d44ce7b5b1b19cdd6bf6e12d7107` matches live. |
| Overall | FAIL | G1 and G2. Forefoot hollow is 0 and the outer surface is unchanged. |

## Second run of the same SHA

A second host ran `fill_forefoot_hollow` from `83c943ba` on the same live file `f0b78d8dc4f57b8acee18cf9929f60ca4eaa44b8b796a9cbba608c30cdcab3e1`. Fill time 1.357 s. Candidate sha256 `88e926421e938e212eecc8340d526063998a1ffd54bc3741f2c09bea2bd04dd9`, 3117489 bytes. The function reported hollow_before 1387, hollow_after 0, 35 layers, added 71.14956 mm. `run_gates.py` with `--slice-seconds 160.029` (158.672 + 1.357) exited 1. The gate counts match the table above: H 0 / 0 / total 4296 / max air 11.633 mm; G1 14377 / 14813, median gap 0.340 mm; G2 8604 / 9003, belt air 91.236%, dorsal air 81.806%; S 30774 walls, 436 seams, 0 moved; F 26298.90 mm, 75.91 g, increase 71.15 mm, 1630 added extrusions; C normalized sha256 `0557d875933ce16a24151b2e4c6e88275528d44ce7b5b1b19cdd6bf6e12d7107`.

Line check on that candidate, outside the frozen script: 0 live non-footer lines missing, in order. 878 `G1 Z` / `;LAYER_CHANGE` / `;Z:` lines byte-identical. 312 first Outer wall and Overhang wall extrusions byte-identical. 2186 inserted lines: 70 `;TYPE:Sparse infill`, 70 `;WIDTH` comments, 2043 `G1`, and the three filament footer lines. Added extrusion events 1630, 0 with a sample outside a hollow column. Added travel events 194, all typed Sparse infill, 0 change Z. 98 of those travels have a sample outside a previously hollow column. Layer 130 travels from (149.13, 2.12) to (119.48, 1.52), and 97 of 119 samples on that segment are outside.

| Required bar | Result | Evidence |
|---|---|---|
| Every live seam and layer-change station byte-identical | PASS | Station lines and first outer extrusions above. Surface gate: 0 seams added or missing, 0 layer changes moved. |
| No new off-bottom layer changes | PASS | `layer_changes_moved` 0. `layer_changes_off_bottom` 0. No added travel changes Z. |
| Forefoot hollow 0 at the frozen layers | PASS | `forefoot_165` 0. `physical_count` 0. 4296 hollow columns remain above layer 165. |
| Added or altered moves only infill or bond inside previously hollow forefoot sections | FAIL | No live motion line was altered. Added extrusions stay inside hollow columns. 98 of 194 added Sparse infill travels do not. |
| Filament rule | PASS | Frozen F. Increase 71.15 mm. 0 outside the outline. |
| G1 / G2 no worse than live (14377/14813 and 8648/9003) | PASS | G1 equal. G2 8604 / 9003. The frozen gates still fail both, because their pass line is 0. |
| Slice time not worse than 260 s | PASS | 159.956 s on the first host. 160.029 s on the second. PM-host equivalent 177.343 s. |
| ApexCore byte-identical to live, stamp exempt | PASS | Frozen C. The SHA does not change `server/chains/apex_core.py`. |

## Rule compliance

This agent wrote gates only. It did not implement a hollow fill and did not change any G-code that would be printed.

**HARD PROHIBITION.** "you may not change the outer surface, seams, layer-change placement, or surface quality. Seams and layer changes stay on the bottom (plantar/belt) face. You may not deteriorate surface quality."

PASS. `git diff --stat d935197976b7cdd2fa89ced4ff43930b0212959f` is only `validation/apexlight_hollow_gates/`. No slicer source, no profile, no G-code product file. The surface gate locks live outer walls, seams, and layer changes and rejects a move off the plantar face.

**Kendon 2026-11-20 11:38 AM MT:** "The seams have always been on the bottom it's been a permanent rule since the beginning"

PASS. The surface gate rejects a closed seam start that is off that loop's plantar edge. The synthetic seam parked on the dorsal edge fails. Live compared with itself passes, 436 seams, 0 off the bottom relative to live.

**Kendon 2026-11-20 12:59 PM MT:** "print surface quality is locked permanently. Never let any agent or change put seams or layer changes on the top surface or sides, and never touch surface quality."

PASS. Same surface gate. A layer-change travel moved to the dorsal edge fails. An outer-wall endpoint moved 0.2 mm fails, including on the live file. No fan, temperature, or acceleration is edited by this change.

**Kendon 2026-11-18 t2268u:** "Surface quality cannot be deteriorated"

PASS. Nothing in this diff changes a toolpath. The gate's job is to reject a candidate that does.

**Kendon 2026-11-18:** "no production G-code goes out while hollow spots remain; every top skin must bond to the bottom skin either by direct contact or through infill/solid between them (infill is acceptable)."

PASS as a gate, and this PR is not production G-code. Hollow must be 0. G1 and G2 must be 0 unbonded. Live ApexLight fails all three, so it is not passed off as done. Infill added only inside a previously hollow >= 3 mm section is the allowed plastic.

**Kendon 2026-11-21 8:41-8:45 PM MT:** "slicer and geometry briefs must not grow scope. Solve only the defect Kendon has observed; anything else comes to him as a question first. Copy Cura's known method rather than inventing new constructions."

PASS. Scope is the forefoot hollow on ApexLight plus ApexCore byte identity. G1 and G2 are the existing Cura-copy measurement, copied into this folder so the other gate tree is not edited. No new bond construction was invented. The question this leaves for Kendon is the 19-column hollow difference against the PM's 1406 (this tool reads 1387, max air matches 11.633 mm).

**Kendon 2026-11-06:** "Apex Bounce = settings identical to Apex Core except extruder 220 C (Core 225 C), print speed 0.8x Core, heated bed 70 C."

PASS. No profile was edited. Bounce settings are untouched. This grade does not re-slice Bounce.

**Header: MODEL grok-4.7, independent grader, frozen gates then grade.**

PASS. Gates were written and proved before a worker SHA was graded.

**Header: REPO kdho77/VertexSlice. BASE main @ d935197976b7cdd2fa89ced4ff43930b0212959f.**

PASS. Branch `cursor/apexlight-hollow-gates-1124` starts at that commit. `git rev-parse HEAD` at branch creation was that SHA.

**Header: PUSH one draft PR titled "[GATES ONLY - DO NOT MERGE] ApexLight hollow-band gates". Never push the worker branch, main, deploy, Railway, or printers. Do not merge.**

PASS for the branch and the draft. No other push.

**Header: MUST-MATCH. Gates frozen before grading and not edited after. Each gate has a no-op proof and a bad-change proof. ApexCore identity passes on main vs itself. Do not change product code.**

PASS. Proof table above. `git diff --stat` has no product code.

**Header: FILES validation/apexlight_hollow_gates/** only. Read-only everywhere else. Do not modify server/, tools/belt_pp/, scripts/g2b_heelmod/, or validation/seam_fuse_gates/.**

PASS. Those trees are not in the diff.

**Header: CAP 0/3. ONE independent grader. Do not implement the hollow fill.**

PASS. No fill was implemented.

**Header: FINISH A draft gates PR, B this rule report, C worker grade when the worker exists.**

A and B are this PR. C is the worker table above: SHA `83c943ba9d36549703ad5f7d57ad177ce5022be7` overall FAIL. G1 and G2 remain unbonded. H, S, F, T, and C pass.

## Rule compliance for the grade of 83c943ba

This section is the grade turn. It did not implement a fill and it did not edit a hashed gate file.

**HARD PROHIBITION.** "you may not change the outer surface, seams, layer-change placement, or surface quality. Seams and layer changes stay on the bottom (plantar/belt) face. You may not deteriorate surface quality. If the fix would require changing any of those, STOP and report; do not work around it."

PASS for this grader. The only edit on this branch is `RESULTS.md`. The candidate leaves the locked surface in place: 30774 wall segments unchanged, 436 seams unchanged, 0 layer changes moved, 878 station lines byte-identical. The fill does not widen skins. G1 staying at 14377 is the residual of not doing that widen.

**Kendon 2026-11-20 11:38 AM MT:** "The seams have always been on the bottom it's been a permanent rule since the beginning"

PASS. The candidate's 436 closed seam starts match live. 0 are newly off the plantar face.

**Kendon 2026-11-20 12:59 PM MT:** "print surface quality is locked permanently. Never let any agent or change put seams or layer changes on the top surface or sides, and never touch surface quality."

PASS. 0 layer changes moved. 0 layer changes off the plantar face. Outer-wall paths: 0 missing, 0 added. The inserted lines are Sparse infill headers, width comments, G1 moves, and the three filament footer lines.

**Kendon 2026-11-18 t2268u:** "Surface quality cannot be deteriorated"

PASS. The wall, seam, and layer-change stations match live.

**Kendon 2026-11-18:** "no production G-code goes out while hollow spots remain; every top skin must bond to the bottom skin either by direct contact or through infill/solid between them (infill is acceptable)."

FAIL as a finished bond. This draft is not production G-code. Forefoot hollow on layers <= 165 is 0. G1 is still 14377 / 14813 unbonded. G2 is still 8604 / 9003 unbonded. 4296 hollow columns remain above layer 165. Nothing was merged.

**Kendon 2026-11-21 8:41-8:45 PM MT:** "slicer and geometry briefs must not grow scope. Solve only the defect Kendon has observed; anything else comes to him as a question first. Copy Cura's known method rather than inventing new constructions."

PASS for scope. The worker copied `_band_paths` and `_band_block` and applied them only on the ApexLight orthocad forefoot. ApexCore, Apex Bounce settings, and `insole_sample` are not on this path. The question this leaves is that G1 does not reach 0 unless skins are widened, and widening is the change that moves the bottom edge. This grader did not invent a second construction.

**Kendon 2026-11-06:** "Apex Bounce = settings identical to Apex Core except extruder 220 C (Core 225 C), print speed 0.8x Core, heated bed 70 C."

PASS. No profile was edited. Bounce was not resliced.

**Header:** "MODEL: grok-4.7 fast=false context 256k (slicer geometry: independent grade of the ApexLight retry against frozen #50 gates)"

PASS. This turn graded. It did not implement the fill.

**Header:** "REPO: kdho77/VertexSlice"

PASS. The grade is in this repository.

**Header:** "BASE: draft PR #50, frozen hashed gates at cb4741c5d623307eda085a8aa217eb792933afde (SHA256SUMS). Do not rewind or rewrite those hashed files. A new ApexLight worker will open a draft PR from main d935197976b7cdd2fa89ced4ff43930b0212959f. Grade that new head. If the worker STOPs with no PR, record that as the grade (infeasible without moving layer changes) without inventing a candidate."

PASS. The hashed files are the freeze at `cb4741c5`. `sha256sum -c SHA256SUMS` in `validation/apexlight_hollow_gates/` passes, and `RESULTS.md` is not in that list. The worker opened draft PR #54 from that main SHA. It did not STOP. No candidate was invented.

**Header:** "PUSH: commits on the existing #50 branch only, keep the PR draft and the title \"[GATES ONLY - DO NOT MERGE]\". Only RESULTS.md (and a handoff comment) may change. Never edit hashed gate files under validation/apexlight_hollow_gates/ or whatever tree #50 froze (prove SHA256SUMS still passes excluding RESULTS.md). Never push to the worker branch or to #49/#51, never push/merge main, deploy, Railway, printers. Do not merge."

PASS. This commit changes only `RESULTS.md`. The push is this branch. PR #50 stays draft and keeps that title.

**Header:** "MUST-MATCH: hashed #50 gates stay frozen and unmodified; no-op and bad-change proofs already recorded stay valid; the new ApexLight head (if any) is graded with those same gates. Required bars: every live seam and layer-change station byte-identical; no new off-bottom layer changes; forefoot hollow 0 at the frozen layers; added/altered moves only infill/bond inside previously hollow forefoot sections; filament rule; G1/G2 no worse than live (14377/14813 and 8648/9003); slice time not worse than 260 s; ApexCore byte-identical to live (stamp exempt)."

PASS for the freeze and the method. The no-op table was not rewritten. The required-bar table is above. The move bar fails: 98 added travels are not inside previously hollow columns. The frozen script's overall result is FAIL because G1 and G2 are not 0.

**Header:** "FILES: RESULTS.md on PR #50 only (grade log, not hashed). Read-only everything else, including all hashed gate scripts. Don't explore beyond. Do not modify server/, tests/, or the worker's code."

PASS. The worker tree was read from a detached worktree at `83c943ba`. Nothing under `server/` or `tests/` was written on this branch.

**Header:** "TESTS: targeted only; CI is the full run. Run the frozen #50 gates on the new worker SHA. Record PASS/FAIL per gate with measured values vs thresholds in RESULTS.md. If the worker has not opened a PR yet, wait for it (poll; do not edit hashed files). If it STOPs with no PR, write that outcome into RESULTS.md and stop."

PASS. The PR was open. `run_gates.py` exit 1 is recorded above.

**Header:** "CAP: 0/3"

PASS. No fill attempt was made by this agent.

**Header:** "ONE: Independent grade of the new ApexLight worker head (or a documented STOP) using the already frozen #50 gates at cb4741c5; do not edit those gates; do not implement the fill."

PASS. One grade of `83c943ba`. No fill was written by this agent.

## Grade of 098c389d

This section grades the travel-confinement retry. The candidate is no longer `83c943ba`. Gate code was not edited. `sha256sum -c SHA256SUMS` from `validation/apexlight_hollow_gates/` still matches, and `RESULTS.md` is not in that checksum. The no-op table and the `83c943ba` table above are unchanged.

Worker: draft [#54](https://github.com/kdho77/VertexSlice/pull/54), head `098c389d09b8a878783166826d12566a4de12999`, parent `06d442b4b3d06b8bfd14a2108a1cc82e16dfeee9`, then `83c943ba9d36549703ad5f7d57ad177ce5022be7`. Branch `cursor/find-an-apexlight-hollow-fill-method-that-keeps-every-layer-change-and-seam-station-byte-identical-t-c48a`. Diff against `d935197976` is `server/chains/apex_light.py`, `server/chains/apex_light_forefoot_fill.py`, and `tests/test_apex_light_forefoot_fill.py`. Diff against `83c943ba` is the fill module and its test only. `server/chains/apex_core.py` is not in the diff.

Candidate ApexLight is `fill_forefoot_hollow` from this SHA applied to live `live_apex_light_orthocad.gcode` from Actions run 45295959179, sha256 `f0b78d8dc4f57b8acee18cf9929f60ca4eaa44b8b796a9cbba608c30cdcab3e1`. The function reported hollow_before 1387, hollow_after 0, last_layer 165, 35 layers, added 71.14956 mm, wall time 3.082 s. Candidate file sha256 `f800c5a8f4fe300f87369407d332081e760a595ce3b9320c20cb09f70677dc90`, 3254273 bytes. Candidate ApexCore is the frozen README's #46 file `cand_apex_core_orthocad.gcode` compared with itself, because this SHA does not change the ApexCore chain.

Slice seconds passed to the gate: 161.754 = main docker-proof slicer clock 158.672 s plus the measured fill 3.082 s. The PM host clock 176.059 s plus the same fill is 179.141 s. Both are under 260 s.

Command:

```bash
python3 validation/apexlight_hollow_gates/run_gates.py \
  --light-base live_apex_light_orthocad.gcode \
  --light-cand cand_apex_light_098c389d.gcode \
  --core-base cand_apex_core_orthocad.gcode \
  --core-cand cand_apex_core_orthocad.gcode \
  --slice-seconds 161.754
```

Exit 1. Frozen-script overall FAIL, because G1 and G2 are not 0. Those two bars of 0 are the live residuals. They are not the PM acceptance line.

| Gate | Frozen bar | Measured | Frozen result |
|---|---|---|---|
| H hollow | 0 on layers <= 165 and on the physical window | Forefoot 0. Physical window 0. Total 4296, all on layers > 165. Max air 11.633 mm. | PASS |
| G1 thin bond | 0 unbonded | 14377 / 14813. Median gap +0.340 mm. Equal to live and to `83c943ba`. | FAIL |
| G2 thick bond | 0 unbonded | 8604 / 9003. Belt air 91.236%. Dorsal air 81.806%. Equal to `83c943ba`. Live was 8648 / 9003. | FAIL |
| S surface | Walls, seams, and layer changes identical to live. None off the plantar face. | 30774 wall segments, 0 missing, 0 added. 436 seams, 0 missing, 0 added, 0 off the bottom. 0 layer changes moved. 0 off the bottom. | PASS |
| F filament | Added plastic is sparse or solid inside a previously hollow section. None more than 0.02 mm outside the outline. | Live 26227.75 mm, 75.70 g. Candidate 26298.90 mm, 75.91 g. Increase 71.15 mm. 1630 added extrusions, 0 bad type, 0 outside a hollow section, 0 outside the outline. | PASS |
| T slice time | <= 260 s | 161.754 s. PM-host equivalent 179.141 s. | PASS |
| C ApexCore | Byte-identical to live after the stamp strip. | Stamp-stripped SHA256 `0557d875933ce16a24151b2e4c6e88275528d44ce7b5b1b19cdd6bf6e12d7107` matches the #46 file. | PASS |
| Frozen script | Every gate PASS | Exit 1. Failed gates: G1, G2. | FAIL |

### Added-move bar

Counted outside the frozen script. An added travel is a candidate Sparse infill XY travel whose rounded endpoints are not already in the live multiset. A travel is inside a previously hollow column when every sample at 0.25 mm spacing sits in a live hollow column of layers <= 165 (air span, 0.05 mm slack), the same column test as frozen F. A remainder lies on a live combing/travel path when every sample that is outside those columns is within 0.02 mm of a live non-extruding XY travel on that layer. 0.02 mm is the frozen outline tolerance. Wall beads and extrusion centerlines are reported and are not counted as combing/travel paths.

| Added-move measure | Threshold | Measured | Result |
|---|---|---|---|
| Added Sparse infill XY travels | report | 7370 | — |
| Entirely inside a previously hollow >= 3 mm forefoot column | report | 360 | — |
| Outside those columns | 0, or each one on a live combing/travel path | 7010 | — |
| Outside, and on a live combing/travel within 0.02 mm | the allowed remainder | 1280 | — |
| Outside, and not on a live combing/travel path | 0 | 5730 | FAIL |
| Of those 5730, on an outer or overhang wall bead | report | 2504 | — |
| Of those 5730, on a non-wall extrusion centerline | report | 3226 | — |
| Of those 5730, on no live travel, wall, or centerline | report | 0 | — |
| Added travels that change Z | 0 | 0 | PASS |
| Layer 130 chord (149.13, 2.12) to (119.48, 1.52) from `83c943ba` | absent | absent | PASS |

Example that fails the bar: layer 139 from (96.185, 0.016) to (139.995, 0.017), 43.810 mm, 176 samples, all outside the hollow columns, all on an Outer wall bead. The nearest live travel along that chord is 1.302 mm away. Example that passes the remainder clause: layer 130 from (120.241, 1.983) to (120.241, 2.383), on a live travel.

Line check on the same candidate: 0 live non-footer lines missing, in order. 878 `G1 Z` / `;LAYER_CHANGE` / `;Z:` lines byte-identical. Inserted lines 9429: 70 `;TYPE:Sparse infill`, 70 `;WIDTH`, 125 retracts, 7405 travel `G1` lines, 125 primes, 1630 extrusions, and 4 filament footer lines. The 7370 count is the travel events that are new. The other inserted travel lines repeat a live endpoint key.

### Footer consistency

| Footer | Live | Candidate | Result |
|---|---|---|---|
| `; filament used [mm]` | 26227.75 | 26298.90 | increase 71.15 mm, matches frozen F |
| `; filament used [g]` | 75.70 | 75.91 | — |
| `; total filament used [g]` | 75.70 | 75.91 | PASS. The two gram lines are the same text, `75.91`. |

On `83c943ba` the total line stayed 75.70 while `; filament used [g]` was 75.91.

### PM acceptance

Senior architect, 2026-11-24 5:44 PM MT. Observed-defect set. The frozen G1 and G2 bars of 0 stay in the table above and are not this line.

| PM check | Threshold | Measured | Result |
|---|---|---|---|
| Hollow | 0 | Forefoot 0. Physical window 0. | PASS |
| G1 no worse than live | <= 14377 / 14813 | 14377 / 14813 | PASS |
| G2 no worse than live | <= 8648 / 9003 | 8604 / 9003 | PASS |
| G2 no worse than `83c943ba` | <= 8604 / 9003 | 8604 / 9003 | PASS |
| Surface, seams, layer-change stations | byte-identical to live | S gate PASS. 878 station lines identical. 0 live non-footer lines missing. | PASS |
| Filament confinement | sparse or solid inside previously hollow sections, none > 0.02 mm outside the outline | Frozen F PASS. 1630 added, 0 outside hollow, 0 outside the outline. | PASS |
| ApexCore identity | byte-identical, stamp exempt | Frozen C PASS. | PASS |
| Gram footer | `; total filament used [g]` matches `; filament used [g]` | both `75.91` | PASS |
| Slice time | <= 260 s | 161.754 s | PASS |
| Added-move bar | 0 outside hollow, or each remainder on a live combing/travel path | 5730 outside and off every live combing/travel path | FAIL |
| PM acceptance | every row PASS | Added-move bar fails. | FAIL |

## Rule compliance for the grade of 098c389d

This agent wrote the grade log only. It did not implement a fill, did not edit a hashed gate file, and did not push the worker branch, PR #50, or main.

**HARD PROHIBITION.** "you may not change the outer surface, seams, layer-change placement, or surface quality. Seams and layer changes stay on the bottom (plantar/belt) face. You may not deteriorate surface quality. If the fix would require changing any of those, STOP and report; do not work around it."

PASS for this grader. The only edit on this branch for this grade is `RESULTS.md`. The candidate's locked stations stay the live bytes: 30774 wall segments, 436 seams, 0 layer changes moved, 878 station lines byte-identical. 2504 added travels ride an outer or overhang wall bead. Those travels are the added-move failure above. They do not rewrite a wall, a seam, or a layer change.

**Kendon 2026-11-20 11:38 AM MT:** "The seams have always been on the bottom it's been a permanent rule since the beginning"

PASS. The candidate's 436 closed seam starts match live. 0 are off the plantar face.

**Kendon 2026-11-20 12:59 PM MT:** "print surface quality is locked permanently. Never let any agent or change put seams or layer changes on the top surface or sides, and never touch surface quality."

PASS. 0 layer changes moved. 0 layer changes off the plantar face. Outer-wall paths: 0 missing, 0 added. This grader did not edit a profile or a toolpath.

**Kendon 2026-11-18 t2268u:** "Surface quality cannot be deteriorated"

PASS. The wall, seam, and layer-change stations match live.

**Kendon 2026-11-18:** "no production G-code goes out while hollow spots remain; every top skin must bond to the bottom skin either by direct contact or through infill/solid between them (infill is acceptable)."

FAIL as a finished bond, and this draft is not production G-code. Forefoot hollow on layers <= 165 is 0. G1 is still 14377 / 14813 unbonded. G2 is still 8604 / 9003 unbonded. 4296 hollow columns remain above layer 165. Nothing was merged.

**Kendon 2026-11-21 8:41-8:45 PM MT:** "slicer and geometry briefs must not grow scope. Solve only the defect Kendon has observed; anything else comes to him as a question first. Copy Cura's known method rather than inventing new constructions."

PASS for this grader's scope. The grade covers the frozen gates, the added-move bar, and the gram footer. No second construction was written here. The candidate's 5730 travels follow a wall bead or an extrusion centerline rather than a live combing travel. That is the added-move failure, and it is the question already sitting on the worker handoff.

**Kendon 2026-11-06:** "Apex Bounce = settings identical to Apex Core except extruder 220 C (Core 225 C), print speed 0.8x Core, heated bed 70 C."

PASS. No profile was edited. Bounce was not resliced.

**Header:** "MODEL: grok-4.7 fast=false context 256k (slicer geometry: independent grade of the #54 travel-confinement retry against frozen #50 gates plus the added-move bar)"

PASS. This turn graded `098c389d`. It did not implement the fill.

**Header:** "REPO: kdho77/VertexSlice"

PASS. The grade is in this repository.

**Header:** "BASE: new branch from the frozen #50 gates at cb4741c5d623307eda085a8aa217eb792933afde (hashed SHA256SUMS). You may start from #50 head 1fec058f4e24af9f192d8aae82ec0beed0967129, which only adds RESULTS.md. Do not rewind or rewrite hashed gate files. Worker retry will push a new SHA on draft PR #54 after 83c943ba. Grade that new head. Do not grade 83c943ba again as the candidate once the new SHA exists."

PASS. This branch starts at `1fec058f`. The hashed files are the freeze at `cb4741c5`. `sha256sum -c SHA256SUMS` passes. The graded head is `098c389d`, which is after `83c943ba`. The earlier section remains the record of `83c943ba` and is not this candidate.

**Header:** "PUSH: ONE new draft PR titled \"[GATES ONLY - DO NOT MERGE] grade of #54 travel confinement\". Only a grade log (RESULTS.md or an appended RESULTS section) may change. Never edit hashed gate files under validation/apexlight_hollow_gates/ (prove SHA256SUMS still passes excluding RESULTS.md). Never push to PR #50, PR #54, or the worker branch. Never push/merge main, deploy, Railway, printers. Do not merge. Do not implement the fill."

PASS for the file set. This commit changes `RESULTS.md` only. The push is this grade branch. PR #50, PR #54, and the worker branch were not pushed.

**Header:** "MUST-MATCH: hashed #50 gates stay frozen and unmodified; no-op and bad-change proofs already recorded on #50 stay valid; the new #54 head is graded with those same gates PLUS the added-move bar (every added travel is inside a previously hollow >=3 mm forefoot column, or follows an existing live Cura-style combing/travel path). Also report the PM acceptance set separately: hollow 0; G1/G2 no worse than live (14377/14813 and 8648/9003) and no worse than 83c943ba (G2 8604/9003); surface, seams, layer-change stations byte-identical; filament confinement; identity; `; total filament used [g]` matches `; filament used [g]`; slice time <= 260 s. Frozen G1/G2 bars of 0 remain in the table but are live residuals; do not treat them as the observed-defect acceptance line, and do not edit those gate files to change the bar."

PASS for the freeze and the method. The no-op table was not rewritten. The frozen G1/G2 bars of 0 stay in the frozen table and were not edited. The PM table is separate. The candidate fails the added-move bar: 5730 added Sparse infill travels are outside the hollow columns and off every live combing/travel path.

**Header:** "FILES: a grade log only (RESULTS.md on the new draft PR, not hashed). Read-only everything else, including all hashed #50 gate scripts and the worker's code. Don't explore beyond. Do not modify server/, tests/, or PR #50/#54 branches."

PASS. The worker tree was read from a detached worktree at `098c389d`. Nothing under `server/` or `tests/` was written on this branch.

**Header:** "TESTS: targeted only; CI is the full run. Run the frozen #50 gates on the new #54 SHA, and measure the added-move bar (count added Sparse infill travels outside previously hollow columns; must be 0, or each remainder must lie on an existing live combing/travel path). Record PASS/FAIL per gate with measured values vs thresholds. If the new worker SHA is not on #54 yet, wait for it (poll; do not edit hashed files). If the worker STOPs with no new SHA, record that and stop."

PASS. The new head was on #54. `run_gates.py` exit 1 and the added-move count 5730 are recorded above. The worker did not STOP.

**Header:** "CAP: 0/3"

PASS. No fill attempt was made by this agent.

**Header:** "ONE: Independent grade of the new PR #54 head (the travel-confinement retry after 83c943ba) using the already frozen #50 gates plus the added-move bar; do not edit those gates; do not implement the fill."

PASS. One grade of `098c389d`. No fill was written by this agent.
