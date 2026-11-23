# ApexCore band infill re-grade — PR #46 head 639acd68

Independent re-grade of head `639acd685199396c16446a779e8bb08f0f6eda8f` against the printed file `cb0569eb19d9502841d73d30f1e4d93bc64bb07d`. The gates G1–G10 and `scripts/apexcore_band_grader/` are the same scripts used on `425fc8f7`. Nothing in those scripts was edited for this run.

## Verdict

**PRINTABLE AS SPECIFIED**

G1–G10 all pass on the CI G-code.

The previous head `425fc8f7` failed G9 (11 hollow columns) and G10 (8 travels through the outer wall). On `639acd68` those two measurements are 0.

## What was measured

| Role | Commit / run | Artifact |
| --- | --- | --- |
| Baseline, the file Kendon printed | `cb0569eb19d9502841d73d30f1e4d93bc64bb07d` | Actions run `45057911334`, `cand-apex-core-gcode-490b995adf3d3d5161776d0ae17122b3276533d3` |
| Candidate | `639acd685199396c16446a779e8bb08f0f6eda8f` | Actions run `45152535261`, `cand-apex-core-gcode-b98cabec2586da447318e56746908082d7fae2c7` |

sha256 of the files that were graded:

| File | sha256 |
| --- | --- |
| baseline `cand_apex_core_orthocad.gcode` | `178dc187019d85696e272865a39eb1bb7ce9bef4cdeee70b8eff0e5a6965a276` |
| baseline `cand_apex_core_insole2.gcode` | `baeb459b4924f80b013a966657b4ab12d4db1fc3019318f0223133b936dd55f1` |
| candidate `cand_apex_core_orthocad.gcode` | `e52aa64505996f9bbe0aadd1a346ad707c1b6c12299fda6991dd4ff954d86b12` |
| candidate `cand_apex_core_insole2.gcode` | `5e38cfcf73780cb22204a340c29b7dae30d724451fddc50776d8b5f2a4629f6c` |

Run `45152535261` completed with conclusion `failure` because the job `Seam-fuse gates G1 G3-G8` failed. `Build image and run slice proof` and `Validation scripts (no binary)` succeeded. The G-code artifact above is the one that job uploaded. The same seam-fuse job also failed on the printed baseline; see the end of this report.

Official command, exit code 0:

```
python3 scripts/apexcore_band_grader/grade.py \
  --repo . \
  --base-sha cb0569eb19d9502841d73d30f1e4d93bc64bb07d \
  --head-sha 639acd685199396c16446a779e8bb08f0f6eda8f \
  --baseline-dir /tmp/apex-grader/baseline \
  --candidate-dir /tmp/apex-grader/candidate639 \
  --json-out docs/proof/apexcore-band-grader/candidate.json \
  --hollow-step 0.1
```

Machine-readable results: `docs/proof/apexcore-band-grader/candidate.json`.

Layer numbers are the 0-based index of `;LAYER_CHANGE` blocks. Orthocad has 439 layers (0–438). insole2 has 95. X runs across the belt. Y is the thickness off the belt. Y near 0 is the belt / plantar face. Z is constant inside a layer.

A hollow column is a vertical ray sampled every 0.10 mm. The shell is `Outer wall` and `Overhang wall`, expanded by half the extrusion width. The column counts when the shell is at least 3.0 mm thick, a gap between shell beads is over 1.0 mm, and no other extrusion (inner wall or infill) occupies that gap. Consecutive rays form a run. A run counts only when its covered width is over 1.0 mm. Columns in that run are `floor(width_mm)`.

## Rules

### R1

> go back to the version i printed and fix the one and only problem with that fix which is adding infill when we hit 3mm and printing solid in less than 3mm. I do not tolerate this

Tested by G8 and G9. Both **PASS**.

G8: every added extrusion sample sits in a shell section at least 5.9681 mm thick. Threshold is 3.0 mm. Stations under 3.0 mm in the changed layers keep the baseline extrusions (0 mismatches).

G9: candidate forefoot hollow columns are 0. Baseline has 298, all in layers 156–165.

### R2

> fix only the problem and leave everything else the same

Tested by G1, G2, G3, G4, and the “must not increase” half of G9. All **PASS**.

The source diff against the printed commit is the two allowed files. insole2 differs by the timestamp line. Orthocad differs only in layers 156–165, by added lines. Layers ≥ 166 keep 111 hollow columns on both files.

### R3

> DO NOT let the agent change anything elese!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

Same evidence as R2. G1–G4 **PASS**. No baseline G-code line in the changed layers was removed or rewritten.

### R4

> print surface quality is locked permanently; never seams or layer changes on top surface or sides.

Tested by G5 and G6. Both **PASS**.

G5: 29197 outer-wall and overhang-wall extrusions match. G6: seam coordinates and layer-change Z values are identical. 436 shell seam starts are on the bottom. Two shell seam starts on layer 436 are on the top, and they are already on the top in the printed file.

### R5

> seams and layer changes must always be on the bottom (plantar/belt face).

Tested by G6. **PASS** for this diff: nothing moved. Bottom means within 1.0 mm of the minimum outer-wall Y on that layer. The two pre-existing top seams on layer 436 are unchanged.

### R6

> the outside surface of a print must not change at all in slicer fixes.

Tested by G4, G5, G7, and G10. All **PASS**.

No baseline line was removed. Outer and overhang extrusions match. Added extrusions stay at least 0.714 mm from an outer-wall path (threshold 0.20 mm). Added travels that cross an outer-wall centerline with an endpoint more than 0.20 mm on the other side: 0.

## Gates

### G1 — PASS

> git diff cb0569eb..425fc8f7 touches only scripts/g2b_heelmod/postprocess_g2b.py and scripts/g2b_heelmod/test_seam_fuse.py.

Applied to the new head. The frozen check is “no file outside that pair.”

Command: `git diff --name-only cb0569eb19d9502841d73d30f1e4d93bc64bb07d..639acd685199396c16446a779e8bb08f0f6eda8f`

Measured files:

- `scripts/g2b_heelmod/postprocess_g2b.py` — 844 insertions, 2 deletions
- `scripts/g2b_heelmod/test_seam_fuse.py` — 137 insertions, 0 deletions

Extra files: none.

The two deleted source lines are still:

```
return _thick_cura_fill(text, stats)
return _thick_cura_fill("".join(lines), stats)
```

Commits on top of the printed file:

- `425fc8f7cc` Fill the empty 3 mm forefoot band with the file's sparse infill.
- `639acd6851` Fill the leftover forefoot hollow tips and keep band travels inside the wall.

### G2 — PASS

> insole2 G-code identical to baseline except metadata/timestamp comment lines.

One differing line, and both sides match `; generated by OrcaSlicer <version> on <date> at <time>`:

- baseline: `; generated by OrcaSlicer 2.3.2-dev on 2026-11-22 at 19:10:19`
- candidate: `; generated by OrcaSlicer 2.3.2-dev on 2026-11-23 at 10:22:08`

Non-stamp diffs: 0. insole2 has 95 layers and none of them differ.

### G3 — PASS

> orthocad G-code: list every layer that differs. Expected only the forefoot hollow band (about layers 156-165, 0-indexed). Any differing layer outside the band = FAIL.

Differing layers: **156, 157, 158, 159, 160, 161, 162, 163, 164, 165**.

Layers outside 156–165: none. Preamble difference is only the timestamp:

- baseline: `; generated by OrcaSlicer 2.3.2-dev on 2026-11-22 at 19:10:12`
- candidate: `; generated by OrcaSlicer 2.3.2-dev on 2026-11-23 at 10:21:55`

### G4 — PASS

> Inside differing layers: every baseline line still present in the same order; only lines added. Classify added extrusions (sparse infill / other) and report counts per layer. Any removed or modified baseline line = FAIL.

Removed or modified baseline lines: **0**. Added extrusions whose type is not `Sparse infill`: **0**.

| Layer | Added lines | Sparse extrusions | Other extrusions | Travels | Retracts | Primes |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 156 | 75 | 57 | 0 | 7 | 3 | 3 |
| 157 | 94 | 77 | 0 | 6 | 3 | 3 |
| 158 | 118 | 99 | 0 | 8 | 3 | 3 |
| 159 | 180 | 127 | 0 | 40 | 4 | 4 |
| 160 | 201 | 141 | 0 | 41 | 7 | 7 |
| 161 | 220 | 160 | 0 | 47 | 4 | 4 |
| 162 | 278 | 195 | 0 | 68 | 5 | 5 |
| 163 | 262 | 228 | 0 | 15 | 7 | 7 |
| 164 | 286 | 246 | 0 | 17 | 9 | 9 |
| 165 | 300 | 257 | 0 | 18 | 10 | 10 |

Total added sparse extrusions: **1587**. Each changed layer also adds two `;TYPE:` lines, two `;WIDTH:` lines, and one feed-only line.

### G5 — PASS

> Outer wall / surface: outer-wall moves in candidate identical to baseline in all layers (count, coordinates, widths).

The check covers `;TYPE:Outer wall` and `;TYPE:Overhang wall` extrusions on all 439 layers.

Baseline moves: **29197**. Candidate moves: **29197**. Mismatched layers: none.

### G6 — PASS

> Seams/layer changes: layer-change positions and seam start points identical to baseline; none moved to top or sides.

Moved layers: **0**. Shell seam starts: **436 bottom**, **2 top**, **0 side**. Newly placed on the top or the side: **0**.

The two top starts are pre-existing, layer 436:

- Outer wall at X=130.334, Y=18.487
- Outer wall at X=145.611, Y=18.698

Layer-change Z and every `;seam travel` / `;C_SEAM_START` coordinate match the baseline.

### G7 — PASS

> Added infill stays strictly inside the inner wall (no added extrusion within 0.2 mm of, or crossing, an outer-wall path).

Added extrusions checked: **1587**. Minimum distance to an outer-wall or overhang-wall centerline: **0.714 mm**. Threshold: distance ≤ 0.20 mm, or a proper crossing, fails. Violations: **0**.

### G8 — PASS

> Added sparse infill only where thickness >= 3.0 mm; regions under 3.0 mm in the band still print exactly as baseline (solid). Report min thickness under any added infill.

Thickness is the outer-surface distance at that X: top outer-wall centerline plus half its width, minus the bottom centerline minus half its width. Samples are taken every 0.25 mm along each added extrusion.

Samples: **3375**. Minimum thickness: **5.9681 mm**. Threshold: 3.0 mm. Samples under 3.0 mm: **0**. Samples outside the shell: **0**. X stations under 3.0 mm whose extrusion crossings differ from baseline: **0**.

### G9 — PASS

> in forefoot layers (<=165), count columns at least 3 mm thick with no interior material and an air gap over 1 mm, baseline vs candidate. Candidate must be 0. Also report counts above layer 165 for both (informational, must not increase).

Sample step: **0.10 mm**. Threshold: candidate columns in layers ≤ 165 equal **0**, and columns in layers ≥ 166 do not increase. The same rule is applied to insole2.

Orthocad:

| | Layers ≤ 165 | Layers ≥ 166 |
| --- | ---: | ---: |
| Baseline columns | 298 | 111 |
| Candidate columns | 0 | 111 |
| Baseline width (mm) | 300.1 | 150.4 |
| Candidate width (mm) | 0.0 | 150.4 |

Above layer 165 the count does not increase (111 = 111). The forefoot count is 0.

Baseline forefoot columns are only in the band. The candidate has no qualifying run in any of those layers:

| Layer | Baseline columns | Baseline width mm | Candidate columns |
| ---: | ---: | ---: | ---: |
| 156 | 9 | 9.1 | 0 |
| 157 | 14 | 14.1 | 0 |
| 158 | 18 | 18.1 | 0 |
| 159 | 22 | 22.1 | 0 |
| 160 | 26 | 26.6 | 0 |
| 161 | 32 | 32.5 | 0 |
| 162 | 38 | 38.0 | 0 |
| 163 | 44 | 44.0 | 0 |
| 164 | 47 | 47.0 | 0 |
| 165 | 48 | 48.6 | 0 |
| 0–155 | 0 | 0 | 0 |

insole2: baseline 0, candidate 0, on every layer (all 95 are ≤ 165).

### G10 — PASS

> no added move with E-per-mm outside the range of baseline infill moves; no retraction/travel anomalies (travel through outer wall, unretracted travels > 2 mm across empty space).

Baseline sparse-infill moves longer than 0.20 mm: **28603**. E-per-mm range: **0.05762 to 0.81814**.

Added sparse moves longer than 0.20 mm: **1073**. E-per-mm range: **0.17930 to 0.18054**. Violations: **0**.

Travels through the outer wall: **0**. A travel counts when it properly crosses an outer-wall centerline and an endpoint is more than 0.20 mm on the other side of that line.

Unretracted added travels with more than 2.0 mm clear of every extrusion bead: **0**.

## Seam-fuse CI job

`Seam-fuse gates G1 G3-G8` fails on step `Gate images and slices` in both runs:

- printed baseline, run `45057911334`, job `137317153229`
- this head, run `45152535261`, job `137617246172`

The failing check names match. These CI checks compare the slice with PRE-29. They are not G1–G10 above.

Shared with the printed file, same figures:

- orthocad `G4_outer_match_pre29`: perimeter p99 0.1682 mm, 15798.53 mm and 15794.89 mm of wall moved, inner 5641.68 mm, 118 layer/type mismatches, overhang E/mm 0.19039 vs 0.16892
- orthocad `G3_seam_joint`: `J3: 165 fail (of 436 matched loops)`
- insole2 `G3_seam_joint`: `J3: 1 fail (of 92 matched loops)`
- insole2 `G4_outer_match_pre29`: 175.52 mm and 176.12 mm of perimeter, inner 522.46 mm, 6 mismatches, the same sparse-infill speed set
- `G8_repo_guard`: `docs/proof/forefoot-geometry/G4_AUDIT.md` is not in that job’s file allow-list
- G1 seam-bottom, G5 crossings, G6 apexlight, and insole2 print budget pass on both runs

Two figures moved, and the job was already red before they moved:

- orthocad `G3_seam_joint` on this head also reports `J2: 6 fail (of 436 matched loops)`. The printed-file log reported only the J3 line.
- orthocad `G7_print_budget` still fails the same PRE-29 +2% rule. Printed file: 90.85 min (+5.08%) and 28613.4 mm filament. This head: 91.29 min (+5.59%) and 28686.5 mm filament. PRE-29 reference is 86.46 min and 27008.2 mm on both logs.

## Handoff

- Grader branch: `cursor/apexcore-band-grader-1122`. This report and `candidate.json` are the re-grade commit. The gate scripts were not changed.
- Command: the `grade.py` invocation above, `--hollow-step 0.1`, exit 0. Result file: `docs/proof/apexcore-band-grader/candidate.json`.
- The earlier identity-pair and bad-copy proof of these same gates remains in `docs/proof/apexcore-band-grader/prove.json`. It was not re-run; the scripts are unchanged.
- Residuals, unchanged from the printed file and not a G1–G10 failure: 111 hollow columns above layer 165 (150.4 mm of run width); two outer-wall seam starts on layer 436 at Y 18.487 and Y 18.698.
- Residuals in the other CI suite: the seam-fuse job is still red for the reasons above. J2’s 6 loops and the print-budget move (time +0.44 min, filament +73.1 mm versus the printed file) are the only seam-fuse lines that are not already in the baseline log.
- Next: this grade does not print, merge, deploy, or update the PR #46 branch. A test print is Kendon’s call.
