# Handedness grade of the belt-mirror fix

**GRADE: FAIL** (regrade on frozen gates v3, `19dff419554bdd2e9daed4b05b57c030507d3035`)

Worker head `0415b656d42d7ae0828e435b98f6fdae4f6f0486` (PR #52, `cursor/lr-swap-belt-mirror-1124`). Same slices as the v1 grade. `sha256sum -c handedness_gates/SHA256SUMS` at `19dff419` printed OK for all six files. This branch still changes no product code and does not edit the frozen gates.

v3 compares each candidate side with the live file of the opposite side and does not mirror. H1 passes on all four materials. H1h passes (LEFT). H3 v3 passes on all eight toe-first slices (outer-wall median 0.0 mm). H2 passes ApexCore and ApexLight. H2 fails ApexForge and ApexBounce, both sides, on one counter:

| Material | Side | Partner | Result | open-only layer changes off bottom (cand vs opposite live) |
| --- | --- | --- | --- | --- |
| apex_forge | left | live right | FAIL | 308 > 305 |
| apex_forge | right | live left | FAIL | 294 > 293 |
| apex_bounce | left | live right | FAIL | 308 > 305 |
| apex_bounce | right | live left | FAIL | 294 > 293 |

Closed seams off the bottom stay 0 on Forge and Bounce, and the other H2 counters match the opposite live file (open ends 156=156 on left, 149=149 on right; layer changes 480=480). The runner exits `HANDEDNESS_GATES_FAIL`.

## Regrade: run_gates.sh at 19dff419

Same G-code as the first grade. H2 and H3 partners are opposite-side live files.

```
PASS H1_handedness_apex_core PASS H1 left-input->LEFT right-input->RIGHT
PASS H2_seam_bottom_apex_core_left
PASS H3_surface_equal_apex_core_left W_median=0.0 W_frac_le_0.30=1.0 E 29191.36/29192.86 F_mean 1508.91/1508.91
PASS H2_seam_bottom_apex_core_right
PASS H3_surface_equal_apex_core_right W_median=0.0 W_frac_le_0.30=1.0 E 29273.50/29271.99 F_mean 1509.49/1509.49
PASS H1_handedness_apex_light
PASS H2_seam_bottom_apex_light_left
PASS H3_surface_equal_apex_light_left W_median=0.0 W_frac_le_0.30=0.9543 E 24873.59/24869.09 F_mean 1466.67/1466.67
PASS H2_seam_bottom_apex_light_right
PASS H3_surface_equal_apex_light_right W_median=0.0 W_frac_le_0.30=0.968 E 24918.89/24915.89 F_mean 1467.31/1467.31
PASS H1_handedness_apex_forge
FAIL H2_seam_bottom_apex_forge_left open_only_layer_changes_off_bottom 308 > 305
PASS H3_surface_equal_apex_forge_left W_median=0.0 W_frac_le_0.30=0.9666 E 1538054.85/1533852.19 F_mean 1824.70/1824.71
FAIL H2_seam_bottom_apex_forge_right open_only_layer_changes_off_bottom 294 > 293
PASS H3_surface_equal_apex_forge_right W_median=0.0 W_frac_le_0.30=0.9624 E 1542719.98/1542985.65 F_mean 1823.77/1823.77
PASS H1_handedness_apex_bounce
FAIL H2_seam_bottom_apex_bounce_left open_only_layer_changes_off_bottom 308 > 305
PASS H3_surface_equal_apex_bounce_left W_median=0.0 W_frac_le_0.30=0.9666 E 1538054.85/1533852.19 F_mean 1824.70/1824.71
FAIL H2_seam_bottom_apex_bounce_right open_only_layer_changes_off_bottom 294 > 293
PASS H3_surface_equal_apex_bounce_right W_median=0.0 W_frac_le_0.30=0.9624 E 1542719.98/1542985.65 F_mean 1823.77/1823.77
PASS H1h_heel_first_left LEFT
materials gated: 4
HANDEDNESS_GATES_FAIL
```

H2 counts that pass, cand vs opposite live: ApexCore both sides closed off bottom 0=0, layer-change off bottom 0=0, open ends 2=2, open-only layer changes 1=1, closed loops 436=436, layer changes 438=438. ApexLight left: closed off bottom 68=68, layer-change off bottom 53≤55, open ends 2=2, loops 436=436. ApexLight right: closed off bottom 76=76, layer-change off bottom 40=40. Config blocks are byte-identical on every H3 v3 pair. Layers 439/439 (core, light) and 481/481 (forge, bounce).

### Independent path check

`path_equal.py` expands modal G0/G1 to absolute extrusion XYZ and compares candidate left with live right (and the reverse) with no mirror. Belt-contact X centres match with shift 0.000 mm on every pair. Median nearest-extrusion distance is 0. The paths are not identical: extrusion counts differ by 1 to 5, and the farthest extrusion is 0.72–2.39 mm from the other file. At least 99.93% of extrusions are within 0.05 mm.

| Pair | Extrusions cand/live | X centre mm | max nn mm (cand→live / live→cand) | fraction ≤ 0.05 mm |
| --- | --- | --- | --- | --- |
| core left vs live right | 84643/84642 | 140.375 | 1.090 / 0.956 | 0.9998 |
| core right vs live left | 84957/84954 | 139.692 | 0.716 / 0.692 | 0.9999 |
| light left vs live right | 69894/69890 | 140.314 | 1.325 / 1.094 | 0.9993 |
| light right vs live left | 69917/69916 | 139.697 | 1.319 / 1.125 | 0.9996 |
| forge left vs live right | 80529/80530 | 140.375 | 1.234 / 2.391 | 0.9995 |
| forge right vs live left | 80445/80442 | 139.623 | 1.358 / 1.204 | 0.9996 |
| bounce left vs live right | 80529/80530 | 140.375 | 1.234 / 2.391 | 0.9995 |
| bounce right vs live left | 80445/80442 | 139.623 | 1.358 / 1.204 | 0.9996 |

Heel-first ApexCore left matches toe-first candidate left on this check (84643 extrusions, max 1.090 mm vs live right). It is still the toe-first toolpath plus a timestamp.

## Noise floor

Live image only (`ghcr.io/kdho77/vertexslice-server@sha256:8cac1d54c5e1a12f4693412c32fe40dd9c5682b9279e2b1d38f1225760aca579`). No candidate. Frozen `h2_seam_bottom.sh` at `19dff419`. This does not change the verdict: **GRADE: FAIL** still, because Forge and Bounce H2 fail the frozen rule by 308 > 305 and 294 > 293.

PR #52's head at the time of this measurement is `e1d535910758c8dff731efe6489eab7d0ccde510`. These slices do not regrade that head. The FAIL above is still the v3 result on `0415b656`.

### (a) Identical requests, sliced twice

Each repeat is byte-identical to its first run, including the timestamp, and the body matches the live file used in the v3 grade. H2 is run2 against run1. Open-only layer changes off the bottom do not move. Extrusion counts do not move.

| Material | Side | open-only layer changes run2 / run1 | Extrusions run2 / run1 | total E run2 / run1 | H2 |
| --- | --- | --- | --- | --- | --- |
| apex_forge | left | 293 / 293 | 80442 / 80442 | 24454.58748 / 24454.58748 | PASS |
| apex_forge | right | 305 / 305 | 80530 / 80530 | 24353.18722 / 24353.18722 | PASS |
| apex_bounce | left | 293 / 293 | 80442 / 80442 | 24454.58748 / 24454.58748 | PASS |
| apex_bounce | right | 305 / 305 | 80530 / 80530 | 24353.18722 / 24353.18722 | PASS |

The grade's live partners are these same counts: live right is 305, live left is 293. A second identical slice does not produce +3 or +1.

### (b) Float noise, +1e-5 mm on every vertex X

RIGHT fixture, winding unchanged. Packed as float32, 210099 of 224820 vertex X values actually change. Sliced on live and compared with that material's right run1.

| Material | open-only layer changes perturbed / run1 | open ends | Extrusions perturbed / run1 | total E perturbed / run1 | H2 |
| --- | --- | --- | --- | --- | --- |
| apex_forge | 305 / 305 | 156 / 156 | 80524 / 80530 (Δ −6) | 24352.02774 / 24353.18722 (Δ −1.15948) | PASS |
| apex_bounce | 305 / 305 | 156 / 156 | 80524 / 80530 (Δ −6) | 24352.02774 / 24353.18722 (Δ −1.15948) | PASS |

The open-only counter stays 305. Extrusion count does move, by 6.

### (c) ApexBounce is a separate profile

It is not byte-identical to ApexForge. The new live files differ in the config block (Forge: nozzle 245 °C, bed off; Bounce: nozzle 225 °C, bed 60 °C, plus `M190 S60`). G0/G1 motion is identical (left 98476 lines, right 99375 lines). H2 counters and extrusion counts match Forge on every row above, so the Bounce noise floor is the same measurement.

## History: first grade against a7d2d60 (H3 v1)

The block below is the v1 runner. It compared each candidate with the mirror of the same-side live file. That measured Orca's own left/right non-equivariance. Kept as history. The v3 numbers above replace it as the verdict.

H1 (physical foot) passes for apex_core, apex_light, apex_forge, and apex_bounce, toe-first left and right. The heel-first ApexCore left file also classifies LEFT. Under v1, H2 and H3 do not pass for every material.

## What was sliced

Independent production path, not the worker's G-code.

- Live image: `ghcr.io/kdho77/vertexslice-server@sha256:8cac1d54c5e1a12f4693412c32fe40dd9c5682b9279e2b1d38f1225760aca579`, tag `sha-d935197976b7cdd2fa89ced4ff43930b0212959f` (GHCR workflow run 45296127492 on origin/main `d935197976`).
- Candidate image: that same image with the worker's `validation/stl_preprocess.py` and `server/threemf_modifiers.py` copied in. `orca-slicer` SHA-256 is `ea6e883cc8198714e8b2059c1bc564c26cf54cac06a8a9954a3c5e764bd203e9` in both images.
- Request: `POST /v1/slice`, `profile_id=apex-v2-insole-95a`, `wall_loops=1`, `infill_density=26`, `belt_insole_place=true`, `print_direction=toe_first`. LEFT = `validation/test_models/orthocad_sample_top_insole.stl`. RIGHT = that STL with Y negated and triangle winding reversed (`make_right_stl.py`).
- Gateway timeout overlay, copied from `validation/seam_fuse_gates/slice_all.sh`: `SLICE_GATEWAY_TIMEOUT_S` 260 → 880 in a throwaway `app.py` bind-mount only. The image, the repo, and production were not modified.
- Forge and bounce were offered (HTTP 200) and were gated. Heel-first ApexCore left was accepted (HTTP 200, `X-Print-Direction: heel_first`).

v1 runner output, kept as history (`run_gates.sh` at `a7d2d60f`, same-side mirror comparison):

```
PASS H1_handedness_apex_core
PASS H2_seam_bottom_apex_core_left
FAIL H3_surface_mirror_equal_apex_core_left
PASS H2_seam_bottom_apex_core_right
FAIL H3_surface_mirror_equal_apex_core_right
PASS H1_handedness_apex_light
FAIL H2_seam_bottom_apex_light_left
FAIL H3_surface_mirror_equal_apex_light_left
FAIL H2_seam_bottom_apex_light_right
FAIL H3_surface_mirror_equal_apex_light_right
PASS H1_handedness_apex_forge
FAIL H2_seam_bottom_apex_forge_left
FAIL H3_surface_mirror_equal_apex_forge_left
PASS H2_seam_bottom_apex_forge_right
FAIL H3_surface_mirror_equal_apex_forge_right
PASS H1_handedness_apex_bounce
FAIL H2_seam_bottom_apex_bounce_left
FAIL H3_surface_mirror_equal_apex_bounce_left
PASS H2_seam_bottom_apex_bounce_right
FAIL H3_surface_mirror_equal_apex_bounce_right
PASS H1h_heel_first_left
materials gated: 4
HANDEDNESS_GATES_FAIL
```

## (1) Frozen gates

On the worker tree, `cd validation && sha256sum -c handedness_gates/SHA256SUMS` printed OK for all six files. `git diff a7d2d60f..0415b656 -- validation/handedness_gates validation/seam_fuse_gates` is empty. The same check inside `run_gates.sh` on this checkout also printed OK. Gates were not re-hashed and none was skipped.

While this grade was running, `cursor/lr-swap-gates-1124` moved from `a7d2d60f` to `2d6d27512d` (`H3 v2: dense 0.05 mm outer-wall sampling and feed distribution (measurement fix, thresholds unchanged)`). That commit edits `h3_surface_mirror_equal.py` and re-hashes `SHA256SUMS`. This grade did not take that edit. The tables below are the pinned runner at `a7d2d60f`. The same G-code was then re-measured with the v2 file, read-only, and those numbers are in the addendum. H2 was not changed by that commit.

## (2) Gate × material

H1. PASS only if the left-input slice classifies LEFT and the right-input slice classifies RIGHT.

| Material | Result | Left input | Right input | Left arch Y (X− / X+) mm | Right arch Y (X− / X+) mm | Length mm |
| --- | --- | --- | --- | --- | --- | --- |
| apex_core | PASS | LEFT, medial X+, heel at high zt | RIGHT, medial X− | 18.29 / 33.41 | 33.41 / 18.29 | 264.3 / 264.4 |
| apex_light | PASS | LEFT, medial X+, heel at high zt | RIGHT, medial X− | 18.28 / 33.41 | 33.41 / 18.28 | 264.3 / 264.3 |
| apex_forge | PASS | LEFT, medial X+, heel at high zt | RIGHT, medial X− | 17.34 / 31.86 | 31.86 / 17.33 | 266.1 / 266.1 |
| apex_bounce | PASS | LEFT, medial X+, heel at high zt | RIGHT, medial X− | 17.34 / 31.86 | 31.86 / 17.33 | 266.1 / 266.1 |

H1h, `cand_apex_core_left_heel.gcode`: PASS, foot LEFT, medial X+, arch Y 18.29 / 33.41, length 264.3 mm, heel at high zt. Those figures match toe-first ApexCore left. The two files differ only on the `; generated by OrcaSlicer` timestamp line (133154 lines, 1 differing line). Live heel-first vs live toe-first is the same kind of pair (134162 lines, timestamp only) and both classify RIGHT. `server/chains/apex_core.py` still calls placement with `print_direction="toe_first"`. The worker did not change that file. The HTTP header says `heel_first`; the toolpath is the toe-first slice. H1h therefore re-checks the toe-first left foot. It passed. It does not show a distinct heel-first toolpath.

H2. Candidate counts must be ≤ live for the four off-bottom counters, and closed loops / layer changes within 2% of live. Counts are cand vs live.

| Material | Side | Result | closed off bottom | layer changes off bottom | open ends off bottom | open-only layer changes off bottom | closed loops | layer changes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| apex_core | left | PASS | 0 = 0 | 0 = 0 | 2 = 2 | 1 = 1 | 436 = 436 | 438 = 438 |
| apex_core | right | PASS | 0 = 0 | 0 = 0 | 2 = 2 | 1 = 1 | 436 = 436 | 438 = 438 |
| apex_light | left | FAIL | 68 ≤ 76 | **53 > 40** | 2 = 2 | 1 = 1 | 436 = 436 | 438 = 438 |
| apex_light | right | FAIL | **76 > 68** | 40 ≤ 55 | 2 = 2 | 1 = 1 | 436 = 436 | 438 = 438 |
| apex_forge | left | FAIL | 0 = 0 | 0 = 0 | **156 > 149** | **308 > 293** | 0 = 0 | 480 = 480 |
| apex_forge | right | PASS | 0 = 0 | 0 = 0 | 149 ≤ 156 | 294 ≤ 305 | 0 = 0 | 480 = 480 |
| apex_bounce | left | FAIL | 0 = 0 | 0 = 0 | **156 > 149** | **308 > 293** | 0 = 0 | 480 = 480 |
| apex_bounce | right | PASS | 0 = 0 | 0 = 0 | 149 ≤ 156 | 294 ≤ 305 | 0 = 0 | 480 = 480 |

On ApexLight, Forge, and Bounce the failing left-input counts equal the other input's live counts (Light closed-off 68/76 swapped; Forge/Bounce open-ends 156/149 swapped). ApexCore's counts match on both sides.

H3. Config block must be byte-identical, layer count equal, outer-wall block count within 2%, extrusion within 1%, outer wall a belt mirror (95% of layers ≤ 0.30 mm and median ≤ 0.15 mm), outer-wall feed multiset within 1% per value.

| Material | Side | Result | Config identical | Layers cand/live | Outer-wall blocks cand/live | Extrusion cand/live | Layers compared | Fraction ≤ 0.30 mm | Median mm | Feeds outside 1% |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| apex_core | left | FAIL | yes | 439/439 | 338/338 | 29191.36 / 29271.99 | 438 | 0.1895 | 0.3538 | 160; largest \|Δ\| 56 at F1462 (14 vs 70) |
| apex_core | right | FAIL | yes | 439/439 | 338/338 | 29273.50 / 29192.86 | 438 | 0.1918 | 0.3538 | 160; largest \|Δ\| 56 at F1462 (70 vs 14) |
| apex_light | left | FAIL | yes | 439/439 | 304/304 | 24873.59 / 24915.89 | 438 | 0.0434 | 1.0604 | 25; largest \|Δ\| 77 at F1475 (0 vs 77) |
| apex_light | right | FAIL | yes | 439/439 | 304/304 | 24918.89 / 24869.09 | 438 | 0.0411 | 1.0613 | 25; largest \|Δ\| 77 at F1475 (77 vs 0) |
| apex_forge | left | FAIL | yes | 481/481 | 328/327 | 1538054.85 / 1542985.65 | 479 | 0.0731 | 0.8471 | 143; largest \|Δ\| 583 at F2100 (28201 vs 27618) |
| apex_forge | right | FAIL | yes | 481/481 | 327/328 | 1542719.98 / 1533852.19 | 479 | 0.0731 | 0.8739 | 145; largest \|Δ\| 583 at F2100 (27618 vs 28201) |
| apex_bounce | left | FAIL | yes | 481/481 | 328/327 | 1538054.85 / 1542985.65 | 479 | 0.0731 | 0.8471 | 143; largest \|Δ\| 583 at F2100 (28201 vs 27618) |
| apex_bounce | right | FAIL | yes | 481/481 | 327/328 | 1542719.98 / 1533852.19 | 479 | 0.0731 | 0.8739 | 145; largest \|Δ\| 583 at F2100 (27618 vs 28201) |

Every H3 v1 failure is W and F. C, N, and E pass on every file. The config block in the G-code is byte-identical to live for every material and side. Under v1's 0.5 mm samples the outer-wall median is 0.35 mm on ApexCore, 1.06 mm on ApexLight, and 0.85–0.87 mm on Forge and Bounce, and fewer than 20% of layers are within 0.30 mm.

### H3 v2 addendum (not the pinned runner)

`2d6d275` says v1's 0.5 mm samples and per-value feed bins fail a real re-slice even when the mesh mirror is good. Thresholds stay 0.30 mm / 0.15 mm median. Re-ran that script on these same files:

| Material | Side | v2 result | Median mm | Fraction ≤ 0.30 mm | F |
| --- | --- | --- | --- | --- | --- |
| apex_core | left | PASS | 0.0884 | 0.9909 | pass |
| apex_core | right | PASS | 0.0884 | 0.9909 | pass |
| apex_light | left | FAIL | 1.0375 | 0.0753 | pass |
| apex_light | right | FAIL | 1.0414 | 0.0753 | pass |
| apex_forge | left | FAIL | 0.8198 | 0.1587 | pass |
| apex_forge | right | FAIL | 0.8496 | 0.1524 | pass |
| apex_bounce | left | FAIL | 0.8198 | 0.1587 | pass |
| apex_bounce | right | FAIL | 0.8496 | 0.1524 | pass |

ApexCore's v1 wall/feed failure is the sampling artefact the v2 note describes (same 0.0884 mm median the PM recorded for this worker SHA). ApexLight, Forge, and Bounce still miss the wall tolerance with dense samples. Feeds pass v2 on all eight. H2 is unchanged, so the runner still fails.

## (3) No-op and bad-change proofs

All four required proofs fail the gate, which is what the frozen checks are supposed to do.

| Proof | Command | Required | Result |
| --- | --- | --- | --- |
| No-op, live vs live | H1 on live left-input and live right-input | FAIL | FAIL on all four materials: `left-input->RIGHT right-input->LEFT` |
| 180° on the belt | `gtransform.py rot180` of each live file, then H1 | FAIL | FAIL on all four materials: `left-input->RIGHT right-input->LEFT` |
| X scaled 1% | `bad_proofs.py scale-x` on candidate ApexCore left, then H3 vs live ApexCore left | FAIL | FAIL `W_ok=False F_ok=False W_median=0.4779 W_frac_le_0.30=0.0411` |
| Seam off the belt edge | `bad_proofs.py move-seam` on candidate ApexCore left (one closed outer start/end lifted 4 mm, loop kept closed), then H2 vs live | FAIL | FAIL `closed_off_bottom 2 > live 0; layer_changes_off_bottom 1 > live 0` |

A second X-scale check, live ApexCore left scaled 1% against itself, fails only the wall term: `C_config_identical=True N_ok=True E_ok=True F_ok=True W_ok=False W_median=8.0006 W_frac_le_0.30=0.0`. The same seam lift on live ApexCore left also fails H2: `closed_off_bottom 2 > live 0`.

Live no-op classifier, true-LEFT STL: ApexCore medial X−, arch Y 33.41 / 18.29, length 264.4 mm, foot RIGHT. The Y-mirror input is medial X+, foot LEFT. ApexLight, Forge, and Bounce match that swap (Light arch 33.41 / 18.28; Forge and Bounce arch 31.86 / 17.33).

## (4) Diff review

`git diff --name-status a7d2d60f..0415b656`:

| Path | Role |
| --- | --- |
| `validation/stl_preprocess.py` | After Z rotation and before X centring: model-X mirror, triangle winding reversed, `mirror_x: True`. No scale. |
| `server/threemf_modifiers.py` | Same mirror composed into the 3MF placement transform, winding reversed on the placed-main STL, `mirror_x: True`. Placement only. |
| `tests/test_region_infill.py` | Expectations updated for the mirrored modifier side. |
| `tests/test_threemf_modifiers.py` | Same. |
| `validation/tests/test_belt_placement_mirror.py` | New placement test. |
| `.github/workflows/docker-server-proof.yml` | Additive `handedness_gates` job, plus one unit-test line. Existing jobs otherwise unchanged. |

No profile JSON, no `CONFIG_BLOCK` source, no postprocess, no ApexLight nozzle/bed/process/machine setting. The sliced config blocks being byte-identical agrees with that. `src/` is unchanged, which matches the identical `orca-slicer` hash.

Commits on the worker branch: `b337442db0` mirror placement, `885d35c871` tests, `0415b656d4` CI gate job.

## (5) Rule-compliance ledger

L1. Kendon 2026-10-24 9:33 PM MT (memory): he "confirmed the belt slicing chain mirrors parts (the golden, sliced from a true left STL, printed as a right foot). He approved fixing the mirror at VertexSlice belt placement". **PASS.** The change is an X mirror in belt placement (`prepare_belt_stl` and `prepare_belt_3mf`), which is the approved place. H1 then reads the true-LEFT fixture as a physical LEFT.

L2. Kendon 2026-10-24: "Text a user adds to a left or right foot must read correctly on screen and print correctly on that foot, which means true-handed STL export and no net mirror in the slicing chain." **PASS** on the sliced fixtures. H1 is LEFT for a left input and RIGHT for a right input on every material that was offered. No text-on-mesh fixture was in the frozen gates, so this line is scored from the foot the G-code classifier reports.

L3. Kendon 2026-10-24: "No other fix, including the belt mirror fix, may change or lose any of his ApexLight settings." **PASS.** The diff does not touch ApexLight presets or postprocess. H3 v3 config blocks are byte-identical on both ApexLight sides, and the outer-wall median against the opposite live file is 0.0 mm.

L4. Kendon 2026-11-20 11:38 AM MT: "The seams have always been on the bottom it's been a permanent rule since the beginning". **FAIL** under v3, on a narrower measurement than v1. Closed seams off the bottom are 0 for ApexCore, Forge, and Bounce, and ApexLight matches the opposite live file (68 and 76). H2 still fails Forge and Bounce because open-only layer changes off the bottom are 308 > 305 (left vs live right) and 294 > 293 (right vs live left). The v1 same-side failures (ApexLight 53 > 40 and 76 > 68) were the mirror pairing; they are history.

L5. Kendon standing rule: locked surface quality, seams and layer changes (bottom only); no scope growth; copy Cura's method. **FAIL.** File scope stays inside placement, tests, and one CI job. H3 v3 passes all eight slices (outer-wall median 0.0 mm). H2 still fails four Forge/Bounce sides, so layer changes off the bottom are not held to the opposite live file. The full extrusion cloud is not identical either: median distance 0, but 1–5 extra or missing extrusions and a farthest point of 0.72–2.39 mm (`path_equal.py`).

L6. Kendon 2026-11-24 4:18 PM MT (P0): MCP belt G-code labelled left is physically a right foot. Correct handedness is mandatory. **PASS** on these slices. Live no-op is still RIGHT for a left input. The candidate is LEFT for a left input and RIGHT for a right input, every offered material.

L7. Kendon 2026-11-24 4:28 PM MT: "(a) An automated handedness check in the generate/export production pipeline, run on the final sliced G-code. It detects the physical foot (arch/medial side on the bottom layer, relative to print direction) and compares it with the requested side. On a mismatch or an inconclusive result, it fails the production with a clear error instead of delivering or attaching the file. It must work for both print directions. (b) A regression test covering heel-first and toe-first print directions times left and right, which fails if the label and the physical foot disagree." **FAIL.** Nothing in the diff runs a classifier inside `POST /v1/slice` or refuses a mismatched file. The new CI job runs the frozen gates, which is a regression run, not a production reject. The heel-first request is accepted and the returned toolpath is toe-first, so (a)'s "both print directions" and (b)'s heel-first × left/right matrix are not actually sliced.

L8. No merge or deploy without Kendon's explicit go. Draft PR only. **PASS** for this grade. This PR is a draft. The worker branch was not merged, deployed, or edited. No Railway change.

L9. Frozen gate files are byte-identical to the PM's (SHA256SUMS); never edit, re-hash or skip a gate. If a gate cannot pass, stop and report why. **PASS.** `sha256sum -c` at `19dff419` is OK. This regrade did not edit or re-hash the gates. H2 cannot pass on Forge and Bounce; that is reported above and the gate file was left as issued.

## Verdict

**GRADE: FAIL** on `run_gates.sh` at `19dff419`. H1, H1h, and H3 v3 pass for every offered material. H2 passes ApexCore and ApexLight and fails ApexForge and ApexBounce by 3 and 1 open-only layer changes off the bottom (308 > 305, 294 > 293) against the opposite live file. The live-only noise floor of that counter is 0 on a repeated identical slice and 0 under a +1e-5 mm X perturbation (extrusion count does move, by 6). v1 and v2 numbers above are history. Frozen files were not edited. Do not merge.
