# Phase B grade — PR #45 at cb0569eb

**Verdict: FAIL.** The failing check is frozen G6 on insole: 125 forefoot inner-wall moves exceed 17.1 mm³/s (max 19.741). That failure is not one of the accepted thin residuals and it is not the G7 time waiver. This grade does not call the file printable.

Frozen gates are still `8844a5a839479a8be9a133b509c9a264ec43a761`. `sha256sum -c SHA256SUMS` in `validation/forefoot_cura_gates` matches. Those files were not edited. The worker tree was not edited.

Graded head `cb0569eb19d9502841d73d30f1e4d93bc64bb07d`. The G-code is the pull_request slice from Actions run [45057911334](https://github.com/kdho77/VertexSlice/actions/runs/45057911334), merge commit `490b995adf3d3d5161776d0ae17122b3276533d3` (parents `10b3703b` and `cb0569eb`). The thin-fix parent `420bb430b1eee34681e2936ba502ad004acb368a` is the slice from run [44994397295](https://github.com/kdho77/VertexSlice/actions/runs/44994397295), merge `df31ad48192c838e808efca76bab751fb9c37562`. Phase A BASE is Actions run `44943125841`, orthocad 3,066,080 bytes, SHA-256 `b58621bce3669580bb9b2e1ef67c7251e8155396c87cbe4098b477b05768b66a`.

## Command

```bash
python3 validation/forefoot_cura_gates/run_gates.py \
  --core orthocad:/tmp/fcg_art/base_ortho/BASE_ApexCore_orthocad_sample_top_insole.gcode:/tmp/fcg45/cand/cand_apex_core_orthocad.gcode \
  --core insole:/tmp/fcg_art/base_insole/BASE_ApexCore_insole_sample.gcode:/tmp/fcg45/cand/cand_apex_core_insole2.gcode \
  --pair ApexLight:/tmp/fcg_art/base_light_ortho/BASE_ApexLight_orthocad_sample_top_insole.gcode:/tmp/fcg45/seam/cand_apex_light_orthocad.gcode \
  --pair ApexLight:/tmp/fcg_art/base_light_insole/BASE_ApexLight_insole_sample.gcode:/tmp/fcg45/seam/cand_apex_light_insole2.gcode \
  --json /tmp/fcg45/cand_vs_phaseA.json
```

Exit code 1. The same command with the `420bb430` slice in place of the candidate also exited 1. Those outputs are `phase_b_pr45_cand.json` and `phase_b_pr45_parent.json`.

| File | Bytes | SHA-256 |
|---|---|---|
| CAND orthocad | 3,278,825 | `178dc187019d85696e272865a39eb1bb7ce9bef4cdeee70b8eff0e5a6965a276` |
| CAND insole | 756,641 | `baeb459b4924f80b013a966657b4ab12d4db1fc3019318f0223133b936dd55f1` |
| 420bb430 orthocad | 3,113,305 | `98eaf5a1e087488445bdf04ab9facec2fc9478dd3e3d6ed5b4a66fd436ec740f` |
| 420bb430 insole | 738,950 | `6d07483b97deab429a4b0ceb612ce49ee8ab39bd88534e6528981137f533944e` |

## Frozen G1–G9 versus Phase A BASE

### orthocad, layers 1–165

| Gate | Frozen line | Result | Measured vs threshold |
|---|---|---|---|
| G1 | Skin overlap ≥ 0.05 mm on every 2.0–3.0 mm column. Zero unbonded. | **FAIL** | 3 unbonded of 15,813. Median gap **−0.232554 mm** (overlap; Cura −0.45). The three columns are layer 111 x 112.25 mm (overlap −0.085), layer 113 x 159.75 (−0.084), layer 113 x 159.25 (−0.090). |
| G2 | Every 3–6 mm column overlaps both skins by ≥ 0.15 mm through the stack. Pass line is 0% air. | **PASS** | 0 of 8,443 unbonded. Belt air 0%, dorsal air 0%. Median overlap on the candidate's own thick columns **0.230 mm** belt and **0.230 mm** dorsal. |
| G3 | Printed outer edge p99 ≤ 0.02 mm, max ≤ 0.05 mm. | **FAIL** | p99 **0.003968 mm** (inside 0.02). Max **0.110391 mm** (over 0.05). 98,191 samples. |
| G4 | Plantar starts. X within 0.05 mm of BASE. Y within 0.50 mm. Layer-change end within 0.55 mm of the plantar edge and inside that window. | **PASS** | 165 closed loops. 0 off the plantar face. 0 stations moved. 0 bad layer changes. |
| G5 | Post-forefoot byte-identical. Process M-codes identical. Supplied materials identical after the timestamp strip. | **PASS** on the files supplied | Post-forefoot equal. Process M-codes equal. Both ApexLight files equal after the stamp strip. ApexForge and ApexBounce were not in the artifact. |
| G6 | Q ≤ 17.1 mm³/s and none over 35. | **PASS** | Max **17.087 mm³/s**. 0 over 17.1. 0 over 35. Median 16.790. |
| G7 | Toolpath seconds ≤ BASE + 2%. | **FAIL** | **5,451.245 s** vs BASE 5,326.981 s, **+2.333%**. Limit is 5,433.520 s. Footer stays `1h 27m 15s` on the candidate, on `420bb430`, and on BASE. |
| G8 | Perimeter, surface, and seam-block crossings are 0. Skin-interior travels ≤ BASE. | **PASS** | 0 / 0 / 0. Skin-interior travels 2, BASE 111. |
| G9 | Report forefoot filament. No bead more than 0.02 mm outside the BASE outer edge. | **FAIL** | Forefoot filament **+1,553.545 mm** (+25.48%; 6,098.207 → 7,651.753). **12** samples outside, the same 12 points as `420bb430` (layers 5–14, distances 0.041–0.450 mm). |

### insole, layers 1–31

| Gate | Result | Measured vs threshold |
|---|---|---|
| G1 | **PASS** | 0 of 178 unbonded. Median gap **−0.230418 mm**. |
| G2 | **PASS** | 0 of 534 unbonded. Belt air 0%, dorsal air 0%. Median overlap **0.230 mm** on both faces. |
| G3 | **PASS** | p99 = max = **0.000209 mm**. 24,416 samples. |
| G4 | **PASS** | 30 closed loops. 0 off the plantar face. 0 moved. 0 bad layer changes. |
| G5 | **PASS** on the files supplied | Post-forefoot equal. ApexLight equal. Forge and Bounce unchecked. |
| G6 | **FAIL** | Max **19.741 mm³/s**. **125** moves over 17.1, all Inner wall. 0 over 35. Median 11.256. |
| G7 | **PASS** | **2,054.449 s** vs BASE 2,044.313 s, **+0.496%** (limit +2%). Footer `34m 4s`. |
| G8 | **PASS** | 0 / 0 / 0. Skin-interior travels 1, same as BASE. |
| G9 | **PASS** | Forefoot filament **+71.841 mm** (+2.22%). 0 samples outside. |

`420bb430` against the same Phase A BASE, from the same runner: orthocad G1 fails with the same 3 columns and the same median −0.232554 mm; G3 fails with the same p99 0.003968 mm and the same max 0.110391 mm; G9 fails with the same 12 coordinates. Orthocad G2 on that parent fails, 7,631 of 8,443 unbonded. Insole G2 on that parent fails, 534 of 534. Insole G6 on that parent fails worse than this candidate: 135 inner-wall moves, max 24.397 mm³/s. Orthocad G7 on that parent passes the frozen +2% at 5,335.528 s (+0.160%).

## Kendon's limits, measured here

Whole-print filament is the sum of positive E on G1 moves that also have X or Y, stopped at command G91. `fcg_common.toolpath_seconds` is the clock.

| Check | Threshold | Measured | Result |
|---|---|---|---|
| Filament vs Phase A BASE | ≤ +6.5% | orthocad 28,614.418 / 27,060.873 = **+5.741%**. insole 10,610.950 / 10,539.109 = **+0.682%**. | **PASS** |
| Time vs `420bb430` | ≤ parent + 2% + 17 s, plus a few seconds for the five travels | orthocad parent **5,335.528 s**. +2% + 17 s = **5,459.239 s**. Candidate **5,451.245 s**, **7.994 s under** that cap. The extra routing allowance was not needed. insole parent 2,044.281 s, candidate 2,054.449 s, +10.168 s, 47.7 s under its cap. | **PASS** |
| Frozen G7 vs Phase A BASE | ≤ +2% | orthocad **+2.333%**, FAIL. insole +0.496%, PASS. | orthocad **FAIL** on the frozen gate; the waiver above holds |
| Thin residuals vs `420bb430` | G1 3, G3 max 0.110 mm with p99 ≤ 0.02, G9 12, none worse | Same 3 columns, same max 0.110391 mm, same p99 0.003968 mm, same 12 outside samples. | **PASS** |
| G2 | 0 unbonded | 0 on both models. | **PASS** |
| G8 | 0 crossings | 0 on both models. | **PASS** |
| G6 orthocad | ≤ 17.1 mm³/s | max 17.087, 0 over. | **PASS** |
| G6 insole | ≤ 17.1 mm³/s | 125 over, max 19.741. Better than the parent's 135 / 24.397, and not waived. | **FAIL** |

Forefoot extrusion counts, layers inside the cutoff: orthocad candidate is Outer wall 5,556, Overhang wall 67, Sparse infill 7,795, Inner wall 0. The parent is Outer 5,556, Overhang 67, Inner wall 3,791, Sparse infill 0. Insole candidate is Outer 213, Sparse infill 6,512, Inner wall **125**. The parent insole is Outer 213, Inner wall 140, Sparse infill 5,978. The 125 remaining insole inner walls are the 125 moves over 17.1 mm³/s.

Against `420bb430`, positive-E outer and overhang lines with X or Y are identical (orthocad 29,200 lines, insole 648). Parsed forefoot outer and overhang extrusions are identical (orthocad 5,623, insole 213). Closed-loop seam starts are identical (165 and 30). The first Z-changing travel of each forefoot layer ends at the same XY (165 and 31). G3 of the candidate against the parent file is p99 0 and max under 10⁻¹³ mm.

## Rule compliance

**2026-11-20 12:59 PM MT: "print surface quality is locked permanently; never put seams or layer changes on the top surface or sides, never touch surface quality."**
PASS against `420bb430`. G4: 0 closed starts off the plantar face and 0 bad layer changes on both models. Seam-start coordinates and layer-change XY match that parent. Outer-wall extrusion lines match it. Command: `run_gates.py` G4, then `fcg_common.layer_changes` and the outer-wall line compare above.

**2026-11-20 11:38 AM MT: seams and layer changes must always be on the bottom (plantar/belt face).**
PASS. Same G4 result. 0 off the plantar face. 0 bad layer changes.

**2026-11-20 9:48 AM MT: "the outside surface of a print must not change at all in slicer fixes."**
PASS against `420bb430`: outer-edge max between the two files is numerical noise, and the outer-wall extrusion lines match. The frozen G3 versus Phase A BASE still FAILs at max 0.110391 mm. That is the same sample as `420bb430`, and p99 0.003968 mm is inside 0.02 mm. It is the accepted thin residual, not a new move.

**2026-11-22 8:30 AM MT: "Yes, allow up to 6.5% filament and copy Cura's thick toe exactly."**
Filament PASS, both models, numbers above. Thick toe: G2 PASS, 0 unbonded, median overlap 0.230 mm on both faces, both models. Orthocad forefoot has no inner wall and 7,795 sparse-infill extrusions. Insole still has 125 inner-wall extrusions, so "exactly" is not true on that model. Command: `run_gates.py` G2 and G9, plus the positive-E sum.

**2026-11-22 8:39 AM MT: "Start infill at about 3 mm like Cura and keep the run going."**
PASS on the gate that measures it. G2's band is 3–6 mm and both models have 0 unbonded columns there. Orthocad's forefoot inner walls are gone and sparse infill is present.

**2026-11-22 10:03 AM MT: "17 seconds is ok".**
PASS as part of the time cap below. Orthocad is 7.994 s under `420bb430` + 2% + 17 s.

**2026-11-22 11:45 AM MT: "Allow a few more seconds so the agent can route those five travels around the wall".**
PASS. The candidate is already inside `420bb430` + 2% + 17 s, so the extra few seconds were not used. G8 is 0 crossings on both models. The run artifact does not include the pre-routing slice, so this grade does not split out a 0.977 s walk. Command: `fcg_common.toolpath_seconds` on the two CI files, and `run_gates.py` G8.

**Outer surface, seams, layer-change placement, and outer-wall extrusion lines identical to `420bb430` on both models. Post-forefoot output and ApexLight byte-identical.**
PASS. Evidence is the line compare, the seam-start compare, `layer_changes`, and G5 (post-forefoot equal to Phase A on both this candidate and `420bb430`, and both ApexLight files equal to Phase A after the stamp strip).

## Handoff

- SHA graded: `cb0569eb19d9502841d73d30f1e4d93bc64bb07d`, CI merge `490b995a`, run `45057911334`. Parent slice: `420bb430`, run `44994397295`.
- Command: the `run_gates.py` line above. Exit 1.
- Residuals that match `420bb430` and stay inside the stated limits: orthocad G1 3 columns, G3 max 0.110391 mm with p99 0.003968 mm, G9 12 samples, G7 +2.333% versus Phase A with the time waiver holding by 7.994 s, filament +5.741% / +0.682%.
- Failing check: insole G6, 125 inner-wall moves, max 19.741 mm³/s, threshold 17.1. Not waived.
- Next: do not print from this grade. The gates stay frozen. No merge, deploy, or repin.
