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

## Worker grade of `098c389d`

Head of draft #54 moved. New SHA `098c389d09b8a878783166826d12566a4de12999` (`06d442b4b3` confines added travels, `098c389d09` adds the matching tests). Diff against `d935197` is still only `server/chains/apex_light.py`, `server/chains/apex_light_forefoot_fill.py`, and `tests/test_apex_light_forefoot_fill.py`. ApexCore is not in the diff. Hashed gate files were not edited.

Candidate ApexLight is this SHA's `fill_forefoot_hollow` on the same live file. The function reported hollow_before 1387, hollow_after 0, 35 layers, added 71.14956 mm, wall time 3.084 s. Output 3254273 bytes. Slice clock passed by the gate: 161.756 s = 158.672 + 3.084. PM-host equivalent 179.143 s. Both are under 260 s. Candidate ApexCore is main's #46 file.

`run_gates.py` exit 1. Overall FAIL. The frozen counts match `83c943ba`.

| Gate | Result | Residual |
|---|---|---|
| H hollow | PASS | Forefoot layers <= 165: 0. Physical window 165: 0. Total 4296, all on layers > 165. Max air 11.633 mm. |
| G1 | FAIL | 14377 / 14813 unbonded. Median gap +0.340 mm. Equal to live. |
| G2 | FAIL | 8604 / 9003 unbonded (live was 8648). Belt air 91.236%. Dorsal air 81.806%. |
| S surface | PASS | 30774 wall segments, 436 seams, 0 missing, 0 added, 0 off the bottom. 0 layer changes moved. |
| F filament | PASS | Live 26227.75 mm, 75.70 g. Candidate 26298.90 mm, 75.91 g. Increase 71.15 mm. 1630 added extrusions, 0 bad type, 0 outside a hollow section, 0 outside the outline. |
| T slice time | PASS | 161.756 s (cap 260 s). |
| C ApexCore | PASS | Stamp-stripped SHA256 `0557d875933ce16a24151b2e4c6e88275528d44ce7b5b1b19cdd6bf6e12d7107` matches live. |
| Overall | FAIL | G1 and G2. The travel commits do not change these frozen counts. |

## Worker grade of `513f924c`

Draft #54 was force-updated. New head `513f924c936b090b2e053ece7ab79bc0697ccd4d` (`Splice each forefoot pocket onto the live travel that already meets it`). The branch now sits on `d928bdbd53`, which is main after #52 and #56. The commit itself changes only `server/chains/apex_light_forefoot_fill.py` and its test. `scripts/g2b_heelmod` matches `d935197`. `server/chains/apex_core.py` is not in the fill commits. Hashed gate files were not edited.

Candidate ApexLight is this SHA's `fill_forefoot_hollow` on the same live file `f0b78d8dc4f57b8acee18cf9929f60ca4eaa44b8b796a9cbba608c30cdcab3e1`. The function reported hollow_before 1387, hollow_after 841, 16 layers, added 28.34303 mm, wall time 1.525 s. Output 3084261 bytes. Slice clock 160.197 s = 158.672 + 1.525. PM-host equivalent 177.584 s. Candidate ApexCore is main's #46 file, because this fill is not called from ApexCore. A fresh slice of the rebased tree was not run.

`run_gates.py` exit 1. Overall FAIL. This head is worse than `098c389d` on the hollow pass line.

| Gate | Result | Residual |
|---|---|---|
| H hollow | FAIL | Forefoot layers <= 165: 841 (live 1387, previous head 0). Physical window 165: 841. Total 5137. Max air 11.633 mm. |
| G1 | FAIL | 14377 / 14813 unbonded. Median gap +0.340 mm. Equal to live and to `098c389d`. |
| G2 | FAIL | 8627 / 9003 unbonded. Live was 8648. Previous head was 8604. Belt air 91.558%. Dorsal air 82.117%. |
| S surface | PASS | 30774 wall segments, 436 seams, 0 missing, 0 added, 0 off the bottom. 0 layer changes moved. |
| F filament | PASS | Live 26227.75 mm, 75.70 g. Candidate 26256.09 mm, 75.78 g. Increase 28.34 mm. 644 added extrusions, 0 bad type, 0 outside a hollow section, 0 outside the outline. |
| T slice time | PASS | 160.197 s (cap 260 s). |
| C ApexCore | PASS | Stamp-stripped SHA256 `0557d875933ce16a24151b2e4c6e88275528d44ce7b5b1b19cdd6bf6e12d7107` matches the #46 file. |
| Overall | FAIL | H, G1, and G2. Forefoot hollow is no longer 0. |

## Worker grade of `ac1a4103`

Draft #54 moved again. New head `ac1a4103712c9688f2d8ccc7a6aa2544ff930c39` (`Comb every forefoot hollow through the inset interior of the insole`). The commit changes `server/chains/apex_light_forefoot_fill.py` and its test. Against the worker base `d928bdbd53` the branch is still only `server/chains/apex_light.py`, `server/chains/apex_light_forefoot_fill.py`, and `tests/test_apex_light_forefoot_fill.py`. `scripts/g2b_heelmod` matches `d935197`. ApexCore is not in the fill commits. Hashed gate files were not edited. `sha256sum -c SHA256SUMS` still matches.

Candidate ApexLight is this SHA's `fill_forefoot_hollow` on the same live file `f0b78d8dc4f57b8acee18cf9929f60ca4eaa44b8b796a9cbba608c30cdcab3e1`. The function reported hollow_before 1387, hollow_after 0, 35 layers, added 71.14956 mm, wall time 3.969 s. Its own extrusion counter is 1730, which is the 1630 extrusion events below plus 100 primes. Output 3121156 bytes, sha256 `a2b8af2e68f5ce29a67149bee7cf684ff0d292c7c284f8e713a807300c974c0c`. Slice clock 162.641 s = 158.672 + 3.969. PM-host equivalent 180.028 s. Candidate ApexCore is the #46 file. A fresh slice of the rebased tree was not run.

```bash
python3 validation/apexlight_hollow_gates/run_gates.py \
  --light-base live_apex_light_orthocad.gcode \
  --light-cand cand_apex_light_ac1a4103.gcode \
  --core-base cand_apex_core_orthocad.gcode \
  --core-cand cand_apex_core_orthocad.gcode \
  --slice-seconds 162.641
```

Exit 1. Overall FAIL. Hollow is closed again. The frozen counts match `83c943ba` and `098c389d`.

| Gate | Result | Residual |
|---|---|---|
| H hollow | PASS | Forefoot layers <= 165: 0 (previous head 841, live 1387). Physical window 165: 0. Total 4296, all on layers > 165. Max air 11.633 mm. |
| G1 | FAIL | 14377 / 14813 unbonded. Median gap +0.340 mm. Equal to live and to `098c389d`. |
| G2 | FAIL | 8604 / 9003 unbonded. Live was 8648. Previous head was 8627. Belt air 91.236%. Dorsal air 81.806%. |
| S surface | PASS | 30774 wall segments, 436 seams, 0 missing, 0 added, 0 off the bottom. 0 layer changes moved. |
| F filament | PASS | Live 26227.75 mm, 75.70 g. Candidate 26298.90 mm, 75.91 g. Increase 71.15 mm. 1630 added extrusions, 0 bad type, 0 outside a hollow section, 0 outside the outline. |
| T slice time | PASS | 162.641 s (cap 260 s). |
| C ApexCore | PASS | Stamp-stripped SHA256 `0557d875933ce16a24151b2e4c6e88275528d44ce7b5b1b19cdd6bf6e12d7107` matches the #46 file. |
| Overall | FAIL | G1 and G2. Forefoot hollow is 0 again. |

Line check outside the frozen script: 0 live lines before the end `G91` are missing, in order (116681 live, 119094 candidate). 878 `;LAYER_CHANGE` / `;Z:` / `G1 Z` station lines byte-identical. Added retracts 100, added primes 100, 0 removed. Added travels 268, all Sparse infill, 0 change Z, 0 removed. 107 of 268 have a sample outside a previously hollow column at the gate's 0.25 mm step. 117 of 268 do at 0.01 mm. Both gram lines read 75.91.

| Required bar | Result | Evidence |
|---|---|---|
| Every live seam and layer-change station byte-identical | PASS | 878 station lines equal. Surface gate: 0 seams added or missing, 0 layer changes moved. |
| No new off-bottom layer changes | PASS | `layer_changes_moved` 0. `layer_changes_off_bottom` 0. No added travel changes Z. |
| Forefoot hollow 0 at the frozen layers | PASS | `forefoot_165` 0. `physical_count` 0. 4296 hollow columns remain above layer 165. |
| Added or altered moves only infill or bond inside previously hollow forefoot sections | FAIL | No live motion line was removed. Added extrusions stay inside hollow columns. 107 of 268 added Sparse infill travels do not, at 0.25 mm. |
| Filament rule | PASS | Frozen F. Increase 71.15 mm. 0 outside the outline. Gram lines agree at 75.91. |
| G1 and G2 no worse than live | PASS | G1 equal to live. G2 8604 is below live 8648. Neither is 0. |
| Slice time not worse than 260 s | PASS | 162.641 s. |
| ApexCore byte-identical to live, stamp exempt | PASS | Normalized sha256 matches. |

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

A and B are this PR. C for `83c943ba`, `098c389d`, and `513f924c` are the earlier tables. C for the current head `ac1a4103712c9688f2d8ccc7a6aa2544ff930c39` is the latest table: overall FAIL. Forefoot hollow is 0, G1 is 14377 / 14813, and G2 is 8604 / 9003. S, F, T, and C pass.

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
