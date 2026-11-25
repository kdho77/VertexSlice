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
