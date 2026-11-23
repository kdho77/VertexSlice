# ApexCore band infill grade — PR #46

Independent grade of head `425fc8f7cc4b49fe21f49e0641161e274c1e8a4e` against the printed file `cb0569eb19d9502841d73d30f1e4d93bc64bb07d`. The gates below were written for this grade. They do not call `postprocess_g2b.py` and they do not read `test_seam_fuse.py`.

## Verdict

**NOT PRINTABLE AS SPECIFIED**

G1–G8 pass. G9 and G10 fail.

The source diff against the printed commit touches only the two allowed files. insole2 G-code differs by the slicer timestamp line. Orthocad G-code differs only in layers 156–165, and inside those layers every baseline line is still present, in order. The added extrusions are sparse infill, they stay at least 0.714 mm off the outer wall, and the thinnest section under any added infill is 6.002 mm.

Two things are still wrong:

- The forefoot hollow is not gone. Eleven columns, each 1.1–1.3 mm wide, are still empty in sections about 6 mm thick (layers 156–160 and 165).
- Eight new retracted travels cross the outer-wall centerline. Each has one end inside the outer-wall loop and one end outside it.

## What was measured

| Role | Commit / run | Artifact |
| --- | --- | --- |
| Baseline, the file Kendon printed | `cb0569eb19d9502841d73d30f1e4d93bc64bb07d` | Actions run `45057911334`, `cand-apex-core-gcode-490b995adf3d3d5161776d0ae17122b3276533d3` |
| Candidate, PR #46 head | `425fc8f7cc4b49fe21f49e0641161e274c1e8a4e` | Actions run `45118943295`, `cand-apex-core-gcode-9ec8a7855952441ac7dd7084448a199b14f431a8` |

sha256 of the files that were graded:

| File | sha256 |
| --- | --- |
| baseline `cand_apex_core_orthocad.gcode` | `178dc187019d85696e272865a39eb1bb7ce9bef4cdeee70b8eff0e5a6965a276` |
| baseline `cand_apex_core_insole2.gcode` | `baeb459b4924f80b013a966657b4ab12d4db1fc3019318f0223133b936dd55f1` |
| candidate `cand_apex_core_orthocad.gcode` | `e1a1e65203710f2a71c3eeddfc1e6674185e794af72eb4a7e43c8fa0af9caa29` |
| candidate `cand_apex_core_insole2.gcode` | `288f9459881d6f84edb9b6137daad81ed5dbb382c7775958dece02345b3569cd` |

Run `45118943295` later completed with conclusion `failure`. The artifact bytes were downloaded again after that and the sha256 values matched the table, so the grade is of the finished artifact.

Official command:

```
python3 scripts/apexcore_band_grader/grade.py \
  --repo . \
  --base-sha cb0569eb19d9502841d73d30f1e4d93bc64bb07d \
  --head-sha 425fc8f7cc4b49fe21f49e0641161e274c1e8a4e \
  --baseline-dir /tmp/apex-grader/baseline \
  --candidate-dir /tmp/apex-grader/candidate \
  --json-out docs/proof/apexcore-band-grader/candidate.json \
  --hollow-step 0.1
```

Machine-readable results: `docs/proof/apexcore-band-grader/candidate.json`.

Layer numbers are the 0-based index of `;LAYER_CHANGE` blocks. Orthocad has 439 layers (0–438). insole2 has 95. On this belt file, X runs across the belt and Y is the thickness off the belt. Y near 0 is the belt / plantar face. Z is constant inside a layer.

A hollow column is a vertical ray sampled every 0.10 mm. The shell is `Outer wall` and `Overhang wall`, expanded by half the extrusion width. The column counts when the shell is at least 3.0 mm thick, a gap between shell beads is over 1.0 mm, and no other extrusion (inner wall or infill) occupies that gap. Consecutive rays form a run. A run counts only when its covered width is over 1.0 mm. Columns in that run are `floor(width_mm)`.

## Rules

### R1

> go back to the version i printed and fix the one and only problem with that fix which is adding infill when we hit 3mm and printing solid in less than 3mm. I do not tolerate this

Tested by G8 and G9.

G8 **PASS**. Every added extrusion sample sits in a shell section at least 6.0017 mm thick. Stations under 3.0 mm in the changed layers still have the baseline extrusions (0 mismatches).

G9 **FAIL**. The candidate still has 11 hollow columns in layers ≤ 165. The baseline has 298, all of them in layers 156–165. The fix removes most of that hollow and leaves the 11 slots below.

### R2

> fix only the problem and leave everything else the same

Tested by G1, G2, G3, G4, and the “must not increase” half of G9.

G1 **PASS**. G2 **PASS**. G3 **PASS**. G4 **PASS**. Layers above 165 keep 111 hollow columns on both files.

G9 still **FAIL** on the forefoot absolute count, and G10 **FAIL** because new travels cross the outer wall. Those travels are new motion, so “everything else” did not stay the same.

### R3

> DO NOT let the agent change anything elese!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

Same evidence as R2. The source tree change is confined to the two allowed files (G1 **PASS**). The G-code change is not confined to filling the hollow: G10 **FAIL**.

### R4

> print surface quality is locked permanently; never seams or layer changes on top surface or sides.

Tested by G5 and G6.

G5 **PASS**. 29197 outer-wall and overhang-wall extrusions match in coordinates, E, and width.

G6 **PASS** for this diff. Seam coordinates and layer-change Z values are identical. 436 shell seam starts are on the bottom. Two shell seam starts on layer 436 are on the top, and they are already on the top in the printed file (see Questions).

### R5

> seams and layer changes must always be on the bottom (plantar/belt face).

Tested by G6. **PASS** for movement: nothing moved. The two pre-existing top seams on layer 436 are unchanged. Bottom means within 1.0 mm of the minimum outer-wall Y on that layer.

### R6

> the outside surface of a print must not change at all in slicer fixes.

Tested by G4, G5, and G7.

G4 **PASS**: no baseline line removed or rewritten. G5 **PASS**: outer and overhang extrusions match. G7 **PASS**: added extrusions stay at least 0.714 mm from an outer-wall path (threshold 0.20 mm).

G10 **FAIL** is the surface-path exception: eight added travels cross that outer-wall path. They are retracted, so they deposit no filament, and they still take the nozzle across the wall.

## Gates

### G1 — PASS

> git diff cb0569eb..425fc8f7 touches only scripts/g2b_heelmod/postprocess_g2b.py and scripts/g2b_heelmod/test_seam_fuse.py.

Command: `git diff --name-only cb0569eb19d9502841d73d30f1e4d93bc64bb07d..425fc8f7cc4b49fe21f49e0641161e274c1e8a4e`

Measured files:

- `scripts/g2b_heelmod/postprocess_g2b.py` — 595 insertions, 2 deletions
- `scripts/g2b_heelmod/test_seam_fuse.py` — 108 insertions, 0 deletions

Extra files: none. Threshold is “no file outside that pair.”

The two deleted source lines are:

```
return _thick_cura_fill(text, stats)
return _thick_cura_fill("".join(lines), stats)
```

### G2 — PASS

> insole2 G-code identical to baseline except metadata/timestamp comment lines.

Command: the `grade.py` invocation above.

One differing line, and both sides match `; generated by OrcaSlicer <version> on <date> at <time>`:

- baseline: `; generated by OrcaSlicer 2.3.2-dev on 2026-11-22 at 19:10:19`
- candidate: `; generated by OrcaSlicer 2.3.2-dev on 2026-11-23 at 05:52:50`

Non-stamp diffs: 0. insole2 has 95 layers and none of them differ.

### G3 — PASS

> orthocad G-code: list every layer that differs. Expected only the forefoot hollow band (about layers 156-165, 0-indexed). Any differing layer outside the band = FAIL.

Differing layers: **156, 157, 158, 159, 160, 161, 162, 163, 164, 165**.

Layers outside 156–165: none. Preamble difference is only the timestamp:

- baseline: `; generated by OrcaSlicer 2.3.2-dev on 2026-11-22 at 19:10:12`
- candidate: `; generated by OrcaSlicer 2.3.2-dev on 2026-11-23 at 05:52:42`

### G4 — PASS

> Inside differing layers: every baseline line still present in the same order; only lines added. Classify added extrusions (sparse infill / other) and report counts per layer. Any removed or modified baseline line = FAIL.

Removed or modified baseline lines: **0**. Added extrusions whose type is not `Sparse infill`: **0**.

| Layer | Added lines | Sparse extrusions | Other extrusions | Travels | Retracts | Primes |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 156 | 49 | 33 | 0 | 5 | 3 | 3 |
| 157 | 66 | 53 | 0 | 4 | 2 | 2 |
| 158 | 94 | 77 | 0 | 6 | 3 | 3 |
| 159 | 153 | 103 | 0 | 37 | 4 | 4 |
| 160 | 176 | 119 | 0 | 38 | 7 | 7 |
| 161 | 218 | 160 | 0 | 45 | 4 | 4 |
| 162 | 272 | 195 | 0 | 62 | 5 | 5 |
| 163 | 262 | 228 | 0 | 15 | 7 | 7 |
| 164 | 286 | 246 | 0 | 17 | 9 | 9 |
| 165 | 287 | 245 | 0 | 17 | 10 | 10 |

Total added sparse extrusions: **1459**. Each changed layer also adds two `;TYPE:` lines, two `;WIDTH:` lines, and one feed-only line.

### G5 — PASS

> Outer wall / surface: outer-wall moves in candidate identical to baseline in all layers (count, coordinates, widths).

The check covers `;TYPE:Outer wall` and `;TYPE:Overhang wall` extrusions on all 439 layers. `;TYPE:` is carried across layers when the file does not repeat it.

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

Added extrusions checked: **1459**. Minimum distance to an outer-wall or overhang-wall centerline: **0.714 mm**. Threshold: distance ≤ 0.20 mm, or a proper crossing, fails. Violations: **0**.

Inner-wall extrusion geometry is included when the file emits it. In the band, many `;TYPE:Inner wall` blocks in the printed file are seam markers and `;WIDTH:` lines with no inner-wall `G1` extrusion; that text is unchanged (G4).

### G8 — PASS

> Added sparse infill only where thickness >= 3.0 mm; regions under 3.0 mm in the band still print exactly as baseline (solid). Report min thickness under any added infill.

Thickness is the outer-surface distance at that X: top outer-wall centerline plus half its width, minus the bottom centerline minus half its width. Samples are taken every 0.25 mm along each added extrusion.

Samples: **3119**. Minimum thickness: **6.0017 mm**. Samples under 3.0 mm: **0**. Samples outside the shell: **0**. X stations under 3.0 mm whose extrusion crossings differ from baseline: **0**.

### G9 — FAIL

> in forefoot layers (<=165), count columns at least 3 mm thick with no interior material and an air gap over 1 mm, baseline vs candidate. Candidate must be 0. Also report counts above layer 165 for both (informational, must not increase).

Sample step: **0.10 mm**. Threshold: candidate columns in layers ≤ 165 equal **0**, and columns in layers ≥ 166 do not increase. The same rule is applied to insole2.

Orthocad:

| | Layers ≤ 165 | Layers ≥ 166 |
| --- | ---: | ---: |
| Baseline columns | 298 | 111 |
| Candidate columns | 11 | 111 |
| Baseline width (mm) | 300.1 | 150.4 |
| Candidate width (mm) | 13.9 | 150.4 |

Above layer 165 the count does not increase (111 = 111). The forefoot count is 11, so the gate fails.

Baseline forefoot columns are only in the band:

| Layer | Baseline columns | Baseline width mm | Candidate columns | Candidate width mm | Candidate runs (X start–end, mm) |
| ---: | ---: | ---: | ---: | ---: | --- |
| 156 | 9 | 9.1 | 2 | 2.6 | 127.5–128.7 (1.3); 135.3–136.5 (1.3) |
| 157 | 14 | 14.1 | 2 | 2.6 | 125.0–126.2 (1.3); 137.8–139.0 (1.3) |
| 158 | 18 | 18.1 | 2 | 2.4 | 123.0–124.0 (1.1); 139.8–141.0 (1.3) |
| 159 | 22 | 22.1 | 2 | 2.6 | 120.5–121.7 (1.3); 141.3–142.5 (1.3) |
| 160 | 26 | 26.6 | 2 | 2.4 | 117.5–118.5 (1.1); 142.8–144.0 (1.3) |
| 161 | 32 | 32.5 | 0 | 0 | |
| 162 | 38 | 38.0 | 0 | 0 | |
| 163 | 44 | 44.0 | 0 | 0 | |
| 164 | 47 | 47.0 | 0 | 0 | |
| 165 | 48 | 48.6 | 1 | 1.3 | 149.3–150.5 (1.3) |
| 0–155 | 0 | 0 | 0 | 0 | |

The 11 remaining runs sit at the two ends of the old hollow, in the gap between the infill that was already in the printed file and the infill this change adds. They are still about 6 mm thick.

insole2: baseline 0, candidate 0, on every layer (all 95 are ≤ 165). insole2 passes this gate on its own. The orthocad count fails it.

### G10 — FAIL

> no added move with E-per-mm outside the range of baseline infill moves; no retraction/travel anomalies (travel through outer wall, unretracted travels > 2 mm across empty space).

Baseline sparse-infill moves longer than 0.20 mm: **28603**. E-per-mm range: **0.05762 to 0.81814**.

Added sparse moves longer than 0.20 mm: **1073**. E-per-mm range: **0.17930 to 0.18054**. Violations: **0**.

Unretracted added travels with more than 2.0 mm clear of every extrusion bead: **0**.

Travels through the outer wall: **8**. A travel counts when it properly crosses an outer-wall centerline and an endpoint is more than 0.20 mm on the other side of that line. All eight are retracted. Each has one endpoint inside the outer-wall loop and one endpoint outside it.

| Layer | From (X, Y) | To (X, Y) | Length mm | Signed offset mm | Retracted |
| ---: | --- | --- | ---: | --- | --- |
| 159 | 185.191, 2.275 | 184.287, 1.128 | 1.46 | +0.045 → −0.910 | yes |
| 160 | 185.147, 2.320 | 184.272, 1.201 | 1.42 | +0.049 → −0.874 | yes |
| 161 | 184.658, 1.537 | 185.504, 2.633 | 1.385 | −0.870 → +0.023 | yes |
| 161 | 183.668, 0.255 | 184.658, 1.537 | 1.62 | +0.024 → −0.705 | yes |
| 162 | 184.705, 1.657 | 185.528, 2.719 | 1.344 | −0.842 → +0.015 | yes |
| 162 | 178.196, 1.608 | 175.678, 1.696 | 2.52 | +1.895 → −0.209 | yes |
| 162 | 175.678, 1.696 | 178.196, 1.608 | 2.52 | −0.209 → +1.895 | yes |
| 162 | 185.528, 2.719 | 184.705, 1.657 | 1.344 | +0.015 → −0.842 | yes |

The layer 162 pair at X≈175.7–178.2 is the deep one: one end is 1.895 mm off the wall line, past the 0.5 mm half-width of the bead.

## Proof that the gates can pass and can fail

Command:

```
python3 scripts/apexcore_band_grader/prove.py \
  --baseline-dir /tmp/apex-grader/baseline \
  --work-dir /tmp/apex-grader/prove \
  --json-out docs/proof/apexcore-band-grader/prove.json \
  --hollow-step 0.25
```

Exit code 0. `problems` is empty. This proof samples hollow columns every 0.25 mm, so its column totals differ slightly from the official 0.10 mm grade. Pass/fail of the equality gates does not depend on that step.

### Baseline against itself

| Gate | Result | What it measured |
| --- | --- | --- |
| G2 | PASS | files identical, including the timestamp |
| G3 | PASS | 0 differing layers |
| G4 | PASS | 0 removed lines |
| G5 | PASS | outer-wall moves match |
| G6 | PASS | seams match |
| G7 | PASS | 0 added extrusions |
| G8 | PASS | 0 added extrusions; thin stations match |
| G9 | FAIL | forefoot columns 296 vs 296, and 296 is not 0 |
| G10 | PASS | 0 added moves |

G9 fails here because the rule is absolute: the candidate forefoot count must be 0. Feeding the printed file in as the candidate correctly rejects it. The two sides are equal (296 and 296 forefoot, 102 and 102 above layer 165), so the counter is stable.

G1 on an empty diff (`git diff` of a commit against itself): **PASS**. G1 on the allowed pair plus `README.md`: **FAIL** (`extra_files: ["README.md"]`).

A synthetic shell, two 20 mm outer walls 5 mm apart, width 1 mm:

- no infill: **20** hollow columns (the gate fails a hollow part)
- one sparse line across the gap: **0** hollow columns (the gate passes a filled part)

### Deliberate bad copy

Built from the baseline G-code by `prove.py`:

- insole2: the first `G1 X…` coordinate rewritten to `X1.000`
- orthocad layer 50: one positive-E outer-wall `G1` deleted (`G1 X144.584 Y0.253 E2.61634 F1200`)
- orthocad layer 50: the `;seam travel` X shifted by +5 mm
- orthocad layer 160: an extrusion appended on the outer wall (`X150 Y0.021` to `X160 Y0.021 E50`), an extrusion appended in the thin toe (`X180 Y0.900 E10`), and an unretracted travel appended across the wall (`X120 Y2` to `X120 Y-2`)
- orthocad layer 180: one sparse-infill extrusion deleted

| Gate | Result |
| --- | --- |
| G2 | FAIL |
| G3 | FAIL |
| G4 | FAIL |
| G5 | FAIL |
| G6 | FAIL |
| G7 | FAIL |
| G8 | FAIL |
| G9 | FAIL |
| G10 | FAIL |

## Questions

These are observations. Nothing here was changed.

- The 11 remaining orthocad slots are 1.1–1.3 mm wide and still about 6 mm thick. Layers 161–164 of the old hollow are closed under the “over 1 mm” rule. Is a slot just over 1 mm something you still will not print?
- Eight new travels cross the outer wall while retracted. The layer 162 pair leaves the wall line by 1.895 mm. Do you want that return path off the outside surface before any test print?
- Layer 436 already has two outer-wall seam starts on the top (Y 18.487 and Y 18.698) in the file you printed. This diff leaves them there. Are those two acceptable because they were already in that print?
- Layers 166 and above still have 111 hollow columns (150.4 mm of run width) on both files. The diff does not add any. Do those stay, since that region is the one you said printed well?
- `git diff cb0569eb..425fc8f7` is the two scripts above. The open PR is against `main` (`10b3703b`), and `cb0569eb` is ahead of `main`, so the GitHub files list also shows `.github/workflows/docker-server-proof.yml` and `docs/proof/forefoot-geometry/G4_AUDIT.md`. Those two are in the printed commit; they are not part of `425fc8f7` relative to that commit.
- Actions run `45118943295` concluded `failure`. This grade used only the named G-code artifact.

## Handoff

- Grader branch: `cursor/apexcore-band-grader-1122` (SHA is the commit that adds this report).
- No pull request was opened. `main` was not pushed. PR #46 and PR #45 branches were not updated.
- Scripts: `scripts/apexcore_band_grader/grade.py`, `gates.py`, `geometry.py`, `gcode.py`, `prove.py`.
- Evidence: `docs/proof/apexcore-band-grader/candidate.json`, `docs/proof/apexcore-band-grader/prove.json`, this report.
- Residuals the grade did not rewrite: the 11 orthocad hollow columns, the 8 wall-crossing travels, the 111 columns above layer 165, and the two pre-existing top seams on layer 436.
