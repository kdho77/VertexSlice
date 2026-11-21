# Forefoot inward widening — Phase 0 feasibility

**Verdict: INFEASIBLE for both STLs.** ApexCore `orthocad_sample_top_insole.stl` and ApexCore `insole_sample.stl`.

The thin single-loop skin gap is real (about 0.41 mm on the orthotic). Widening those two beads enough to overlap by 0.10 mm stays under the 35 mm³/s flow cap on the orthotic. The same transform cannot be applied under the locked constraints: it opens a 0.486 mm break at every zone boundary, moves the bottom seam by up to 0.161 mm, and moves the exterior by up to 0.152 mm. Separately, thousands of forefoot columns outside that thin zone are already unbonded, and this widening does not touch them. No tolerance was relaxed to chase a FEASIBLE verdict.

This is a measurement only. No slicer change, no gate, no CI edit, no merge, no deploy, no reference repin.

## G-code that was measured

Docker was not present on the VM. `docker.io` was installed and `validation/seam_fuse_gates/slice_all.sh` was run unchanged against the image built from main `10b3703b155be44e21269f37ef927595f06485bd`:

- tag `ghcr.io/kdho77/vertexslice-server:sha-10b3703b155be44e21269f37ef927595f06485bd`
- digest `sha256:29164fdb03a495a24ca6cae14d0bf06ef77b09cf953d7bdc9352a5118dd51913`
- published by GitHub Actions run [44921694810](https://github.com/kdho77/VertexSlice/actions/runs/44921694810) (`GHCR headless slicer image`, `workflow_dispatch` on main at that commit, success). That run's smoke slice uses `apex-v2-insole-medium` and was not used here.

`slice_all.sh` (ApexCore and ApexLight, both STLs), all HTTP 200, `SLICE_EXIT:0`:

| file | seconds | bytes | sha256 |
| --- | ---: | ---: | --- |
| live_apex_core_orthocad.gcode | 4 | 3066080 | `27f36512cf2ad1d00652b23aabeb045d9cc4397a23e4c9e790ee9e278c238fdd` |
| live_apex_core_insole2.gcode | 1 | 738797 | `a7633fee9d60738247dbe54def45def045d58385658235437fd1785512cd17bd` |
| live_apex_light_orthocad.gcode | 168 | 3060975 | `d146625169b4465fc2f05cb5323abf0029bcbe91168acd6f7ae578746f5bffba` |
| live_apex_light_insole2.gcode | (same script, HTTP 200) |  | `ffe3c36d3a1e929809a87e728f50f1be76b1a42c1aaffd53f35361fa71049f3d` |

ApexForge and ApexBounce are not in `slice_all.sh` (it hardcodes `apex_core` and `apex_light`). They were sliced with the same form fields, the same image, and the same endpoint, without editing the script: `profile_id=apex-v2-insole-95a`, `wall_loops=1`, `infill_density=26`, `print_direction=toe_first`, `belt_insole_place=true`. All four returned HTTP 200 (forge orthocad 7 s / 3047988 bytes, forge insole 883841 bytes, bounce orthocad 8 s / 3048061 bytes, bounce insole 883914 bytes).

Filament cap: `resources/profiles/IdeaFormer/filament/VERTEX_APEX_CORE_95A @base.json` has `filament_diameter` 1.75 and `filament_max_volumetric_speed` 35. `A_fil = π*(1.75/2)² = 2.405282 mm²`. Bead height `H = 0.46` mm, taken from the feature `;HEIGHT:0.46` lines (the `;HEIGHT` directly under `;LAYER_CHANGE` is the belt spacing, 0.650538 mm, and was not used as H).

## F1 — frame

`src/libslic3r/BeltTransform.cpp` lines 21–25 and 37–39 lock the inverse: machine Y = `BELT_I_YY *` virtual Y with `BELT_I_YY = 1.41421356`. Lines 129–144 (`inverse_transform_point`) write machine X, Y, Z. `validation/seam_fuse_gates/sfg_common.py` lines 3–6 state that the parser reads that physical belt G-code, and that G-code Y is `1.41421356 ×` virtual Y with Y = 0 on the belt. `parse()` starts at line 26.

Machine XY **is** the extrusion plane. A pure-Y machine move is longer than virtual Y by exactly √2, and that longer length is the distance along the 45° plane. Evidence from ApexCore orthocad layer 40 outer wall: E-derived width `w = (E/mm)·A_fil/H + H·(1−π/4)` is 1.146 mm on a 0° move and 1.143–1.146 mm on moves at 68° and 155°. Dividing the Y component by 1.41421356 before measuring length inflates those angled moves to 1.48 mm and 1.19 mm, while `;WIDTH` stays 1.098–1.102 mm. So gaps and E/mm use machine XY as parsed. No second √2 is applied.

Model point used only for the anatomical axis: `X = Xg`, `Z = Yg / 1.41421356`, `Y = Zg − Z`. Check: after the ApexCore placement the orthotic's Y extent is 0 to 273.10 mm; the last extruding layer reconstructs to Y 272.97–272.99 mm.

Layer index below is `sfg_common`'s index. The first `;LAYER_CHANGE` is layer 0 and does not extrude. The first extruding layer is 1. Belt advance per layer is 0.650538 mm = `0.46 × 1.41421356`.

G-code decimal precision, scanned on the ApexCore files: XYZ at most 3 places, E at most 5, F an integer. The smallest nonzero XYZ step the file can carry is 0.001 mm. A shared vertex written with the same 3-decimal text has gap 0, so the format can meet a 0.001 mm continuity limit when both sides name the same point. It cannot hide a 0.486 mm break: 0.486 written to 3 places is still 0.486.

## How a column was classed

Forefoot station of a column is the model-Y projection of the belt-side (minimum machine-Y) bead onto the axis in F0. The column is inside the forefoot when that station is ≤ the 40% mark.

Occupied 0.5 mm X columns in that region are classed, and no occupied column is left out:

1. **Zone** — the clips come from exactly one closed outer/overhang loop, there are exactly two of them, and no inner wall, infill, or solid lies strictly between them.
2. **Bonded** — not zone, and the belt-side skin and the foot-side skin are in one component of beads whose capsule separation is ≤ 0.0001 mm (numerical contact, not a bonding allowance).
3. **Unbonded** — not zone, and those two skins are in different components.

Bead width is the E formula above, not `;WIDTH`. Envelope separation is the minimum segment-to-segment distance minus the two half-widths, on the pieces clipped to that 0.5 mm column.

The scripted transform (F4–F6), applied only for ApexCore, raises E and shifts the zone centre line inward along the local inward normal by `dw/2`, with `dw = max(0, (gap + 0.10) / 2)` per bead, F unchanged. A segment that crosses several zone columns takes the maximum `dw` on that segment. Moves that leave the zone are split at the boundary. The outside piece stays on the BASE line (collinear, E/mm within 0.5%, same F). The inside piece is the shifted segment. Because a G1 only stores its end point, the writer emits a G0 from the frozen boundary point to the shifted start so the following G1 is actually the shifted segment. That G0 is the discontinuity. It is not an extruding bridge, and it was not rounded away.

## orthocad_sample_top_insole.stl — ApexCore — INFEASIBLE

### F0 — region and zone

Placement is the one `server/chains/apex_core.py` calls: `prepare_orthocad_insole_stl`, toe-first, Z rotation 270°, Y and Z minimums shifted to 0, X centred at 140. Placed extents: across-belt X 92.695 mm, along-belt Y 273.104 mm, thickness Z 25.240 mm. Raw mesh 74940 triangles, extents 273.104 × 92.695 × 25.240 mm. It is foot-shaped.

Axis: principal component of the plantar vertices (mesh Z ≤ 0.50 mm on the belt side), direction flipped so the low-Y end is the toe. The axis is **−2.175°** from placed +Y. Footprint length along it is **273.338 mm**. The toe-end 15% of the hull is 74.881 mm wide; the heel-end 15% is 72.964 mm wide, so the toe end is the slightly wider end, matching toe-first placement.

40% of 273.338 mm is **109.335 mm** from the toe tip. The plantar point at that station has belt coordinate `Zg = 108.921 mm`, which is extruding **layer 165**.

Layer-progression length, first extruding layer to last: `Zg` 1.952 → 286.237, **L = 284.285 mm** = 437 × 0.650538 mm. Forty percent of that span is `Zg = 115.666 mm`, **layer 176**. The layer-progression boundary sits **6.745 mm** further toward the heel than the true-axis boundary. The extra length is the 45° slice invariant `Y + Z`: the last layer is at the top of the heel, not at the plantar heel tip, so counting layers overstates the sole length.

Zone mask (frozen as data, not as a gate): **17,760** columns, layers 1–174. Full list: `data/orthocad_apex_core_zone_mask.csv`.

### F2 — true gap inside the zone

Overall on 17,760 zone columns: median **0.4099 mm**, p5 **0.3622 mm**, p95 **0.4609 mm**, max **1.8451 mm**, min **−1.2266 mm** (negative means the two envelopes already overlap, which happens on 537 edge columns where the loop is turning). 16,820 of the 17,760 columns lie in 0.20–0.50 mm.

The PM's centre-line estimate was median 0.454 mm using `;WIDTH`. On layer 40 the E-width is 1.146 mm against `;WIDTH` 1.102 mm, 0.022 mm of extra half-width on each bead, 0.044 mm together. 0.454 − 0.044 = 0.410. The envelope measurement lands on that number.

| layer | Zg mm | n | median | p5 | p95 | max | min |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 40 | 27.323 | 127 | 0.4097 | 0.4079 | 0.4115 | 0.4117 | −0.448 |
| 60 | 40.333 | 145 | 0.4082 | 0.4050 | 0.4113 | 0.4117 | −0.220 |
| 80 | 53.344 | 160 | 0.4033 | 0.3676 | 0.4247 | 0.4285 | −1.019 |
| 100 | 66.355 | 171 | 0.4153 | 0.3764 | 0.4687 | 0.4790 | −1.169 |
| 120 | 79.366 | 74 | 0.4108 | 0.2239 | 0.4479 | 0.7004 | −1.000 |
| 140 | 92.376 | 38 | 0.3676 | −0.257 | 0.4355 | 0.6005 | −0.892 |
| 160 | 105.387 | 28 | 0.4175 | −0.714 | 0.8792 | 1.4808 | −0.909 |

Every forefoot layer is in `data/orthocad_apex_core_layers.csv`. Inner wall first appears at layer 118. Feature counts: Outer wall 28,106, Inner wall 27,451, Sparse infill 21,523, Overhang wall 50, Internal solid infill 20.

Live volumetric flow on outer-wall extrusions in layers 21–119, `Q = dE·A_fil / (L / (F/60))`: n=1932, max **17.087**, p99 **16.364**, median **12.657** mm³/s. This matches the PM's 17.09 / 16.37 / 12.66. Commanded Q on some inner-wall moves elsewhere in the same BASE file reaches 125.3 mm³/s at F12720 (short moves). That spike is already in BASE. It is not a product of the widening, and it is not the outer-wall population the PM measured.

### F2b — every forefoot column

24,850 occupied columns inside the forefoot. 17,760 zone + 341 bonded + 6,749 unbonded = 24,850. None unclassified.

The 6,749 unbonded columns are **outside the zone**. Layers 118–171. 6,740 of them contain only Inner wall and Outer wall; 9 also contain sparse infill. Median separation from the belt-side skin to the nearest other bead is **0.229 mm** (p5 0.175, p95 0.314). The foot-side skin is the same story (median 0.227 mm). A checked column at layer 143, X 156.0–156.5: outer at Y=0.021 (w=1.04) and inner at Y=1.15 (w=0.79), envelope gap 0.212 mm; the two inners are 0.049 mm apart; the top pair repeats the 0.212 mm gap. No infill in that column. Full list: `data/orthocad_apex_core_unbonded.csv`.

These columns are hollow and the thin-zone widening does not move them. That alone is INFEASIBLE.

### F3 — what closing the zone gap would take

For a median gap of 0.4099 mm, overlap 0.10 mm needs `dw = (0.4099+0.10)/2 = 0.255 mm` more width on each bead (centre line inward by 0.127 mm). Overlap 0.05 mm needs `dw = 0.230 mm`. The worst zone gap, 1.845 mm, needs `dw = 0.973 mm` and a centre shift of 0.486 mm.

The scripted transform's added filament, summed from the written E, is **886.85 mm** of 1.75 mm filament, **2133.1 mm³**. Peak layer is layer 117 at 9.57 mm of filament. Per-layer table: `data/orthocad_apex_core_added_by_layer.csv`. Added volume equals added filament volume because E is the commanded filament length; the flow ratio in the profile is already inside the BASE E values and was not applied again.

Zone-bead Q after the widening, F unchanged: max **22.895**, p99 **20.210** mm³/s. Both are under 35. Flow does **not** block the orthotic thin-zone columns. The whole-file commanded peak stays 125.3 mm³/s because those inner-wall moves are copied unchanged from BASE.

### F4 — verdict inputs

INFEASIBLE. Any one of the following is enough:

- 6,749 unbonded columns outside the zone (F2b). The transform never moves them. Locations: layers 118–171, listed in the unbonded CSV. Reason: an inner wall sits between the skins and does not touch them (median gap 0.229 mm).
- Zone-boundary extrusion is not continuous (F5): written gap up to 0.4865 mm.
- Exterior envelope moves by up to 0.152 mm, above 0.02 mm.
- The bottom-edge seam moves by up to 0.161 mm.

Flow is not the reason on this STL. No zone column was left over the 35 mm³/s cap by the widening.

### F5 — boundary continuity

1,122 zone-boundary ends. Geometric gap between the frozen outside endpoint and the shifted inside start: max **0.4863 mm**. The same gap read back from the written G0/G1 coordinates: max **0.4865 mm**. Both are above 0.001 mm. The 0.486 mm figure is the centre shift of the worst column, `(1.845+0.10)/4`, not a rounding artifact. Three-decimal XYZ cannot turn 0.486 into a value ≤ 0.001.

Outside pieces of split moves: 0 failures of collinearity (endpoint off the BASE segment by more than 0.001 mm) and 0 failures of E/mm within 0.5% of BASE. F is the BASE F. Unsplit outside moves were copied as the original line text.

Exterior, sampled on the outward side of each bead only: max protrusion **0.152 mm**, worst on layer 171 (recession 0.037 mm). Layers 154, 165, and 166 are next (0.135, 0.131, 0.130 mm). All above 0.02 mm. These are the layers where the loop is only partly inside the zone, so the clamped corner between a shifted edge and a frozen edge kicks the outside surface.

Seam: the closed-loop start is the bottom-edge seam (`|Y − minY| ≤ 0.25 mm`) on 174 zone layers. It moves on 117 of them. Maximum move **0.161 mm**.

### F6 — time

`sfg_common.toolpath_seconds`: BASE **5326.98 s**, transformed **5332.18 s**, delta **+5.20 s** (+0.098%). F on extrusions is unchanged. The extra seconds are the boundary G0s that expose the 0.486 mm breaks. This is not a +25 minute path. It is also not zero, because the discontinuity is a travel.

## insole_sample.stl — ApexCore — INFEASIBLE

### F0

The mesh is 12 triangles, a rectangular prism **50.0 × 90.0 × 12.0 mm** before placement. It is not foot-shaped. There is no toe and no heel.

PCA of that rectangle is the 90 mm side. After the same 270° toe-first placement that side lies across the belt, so it is not a toe-to-heel axis (PCA is −99.992° from +Y, i.e. along X). The axis used for the 40% mark is placed model **+Y**, the direction the slicer advances, toe end at minimum Y. Length **50.000 mm**. Both ends are 90 mm wide because it is a box. The PCA disagreement is recorded in `data/insole_axis.json`.

40% is **20.0 mm**, belt `Zg = 20.0 mm`, extruding **layer 31**.

Layer-progression length: `Zg` 0.651 → 61.151, **L = 60.500 mm**, 40% at `Zg = 24.851 mm`, **layer 38**. Difference **4.851 mm** (progression boundary further along the belt). The 60.5 mm span is the 50 mm length plus most of the 12 mm thickness folded into `Y+Z` by the 45° slice. Zone mask: **383** columns, 29 layers, `data/insole_apex_core_zone_mask.csv`.

### F2

383 zone columns: median **0.4252 mm**, p5 **0.1939 mm**, p95 **8.034 mm**, max **14.516 mm**, min 0.1939 mm. The distribution is two populations: 354 columns under 0.50 mm (the thin edge, same order as the orthotic) and **27 columns at or above 3 mm** (2 between 1 and 3 mm). The large values are the hollow height of the box at an edge column that has exactly two outer beads and nothing between them. They meet the zone definition. They are not a 0.4 mm skin gap.

Inner wall first appears at layer 4. Counts: Sparse infill 21,139, Outer wall 644, Inner wall 423.

Live outer-wall Q on layers 21–119 (this file only has 94 extruding layers, so the window is whatever falls in it): n=512, max = p99 = median = **11.261** mm³/s. The outer wall on this box is one speed.

### F2b

5,389 occupied forefoot columns. 383 zone + 256 bonded + 4,750 unbonded = 5,389. None unclassified.

4,750 unbonded columns are outside the zone, layers 4–30. 4,014 are Inner wall + Outer wall + Sparse infill; 736 are Inner wall + Outer wall only. Median separation from the belt-side skin to the nearest other bead is **0.265 mm** (p5 0.248, p95 0.265). The infill and the inner wall do not form a touching chain to the skins. Full list: `data/insole_apex_core_unbonded.csv`.

### F3

Median thin gap 0.425 mm would need `dw = 0.263 mm` per bead for 0.10 mm overlap, or `0.238 mm` for 0.05 mm. The 14.516 mm columns would need `dw = 7.308 mm` per bead.

Written added filament: **43.10 mm**, **103.67 mm³**. Peak layer is layer 3 at 8.84 mm of filament. Zone Q after the transform: max = p99 = **98.663 mm³/s**, which is the widened tall columns, and it is over 35.

### F4

INFEASIBLE.

- 4,750 unbonded columns outside the zone, layers 4–30, median gap 0.265 mm. The widening does not reach them.
- 27 zone columns have a gap ≥ 3 mm (max 14.516 mm). Closing them at the 0.10 mm target produces zone Q **98.66 mm³/s > 35**. Those columns cannot close inside the cap. Reason: the "two beads, nothing between" test is matching the hollow height of the box, and the required bead is several millimetres wider than the flow cap allows at the unchanged feed.
- Boundary gap 3.654 mm (F5).
- Bottom seam moves 0.131 mm.

Exterior on this box stays within 1.5×10⁻⁷ mm on the straight edges (a pure inward parallel shift). Exterior is not the blocker here. Flow, continuity, the seam, and the outside-zone hollows are.

### F5

124 boundary ends. Geometric gap max **3.6539 mm**. Written-coordinate gap max **3.6540 mm**. That is the centre shift of the 14.5 mm column, `(14.516+0.10)/4`. Above 0.001 mm. Three-decimal XYZ does not conceal it.

Outside partials: 0 collinearity failures, 0 E/mm failures beyond 0.5%. Bottom-edge seams: 29 layers, maximum move **0.131 mm**, 2 of them over 0.001 mm (the others are runs whose seam vertex was not on a shifted piece).

### F6

BASE **2044.31 s**, transformed **2050.55 s**, delta **+6.24 s**. Same cause as the orthotic: boundary G0s. F on extrusions is unchanged.

## Other three profiles — does the thin single-loop forefoot exist?

Same anatomical boundary, same column test, no transform. ApexLight, ApexForge, and ApexBounce G-code from the same image.

| profile | STL | thin zone columns | median zone gap | unbonded outside the zone | inner wall in the file |
| --- | --- | ---: | ---: | ---: | --- |
| ApexLight | orthocad | 20,640 | 0.341 mm | 3,623 | none |
| ApexLight | insole box | 592 | 0.354 mm | 4,507 | none |
| ApexForge | orthocad | 0 | — | 5,164 | none |
| ApexForge | insole box | 0 | — | 5,485 | none |
| ApexBounce | orthocad | 0 | — | 5,164 | none |
| ApexBounce | insole box | 0 | — | 5,485 | none |

ApexLight **does** have the same thin single-loop forefoot, with a slightly smaller median gap (0.341 mm orthocad, 0.354 mm box) and no Inner wall feature at all. Its unbonded columns are Outer wall + Sparse infill that do not touch (orthocad median belt-side separation 1.381 mm).

ApexForge and ApexBounce **do not**. Every forefoot column that has two skins also has infill or solid between them, so the single-loop zone is empty. They still have thousands of unbonded columns (infill that does not touch the outer wall). Forge and Bounce orthocad type counts match each other (Outer 36,090, Sparse infill 42,555, Internal solid 1,643, Overhang 35) but the files are not the same hash.

## Constraints that block full coverage

1. **Unbonded columns the widening cannot reach.** Orthocad ApexCore: 6,749 columns, layers 118–171, outer-to-inner gap median 0.229 mm. Insole box: 4,750 columns, layers 4–30, median 0.265 mm. F4 says any such column makes the verdict INFEASIBLE. They were not reclassified to make the count smaller.
2. **Continuity at the zone boundary.** The allowed transform shifts only the zone centre line and freezes everything outside. The shared vertex cannot be both frozen and shifted. The written gap equals the shift: 0.486 mm on the orthotic, 3.654 mm on the box. Limit is 0.001 mm. G-code precision is 0.001 mm and does not hide either number.
3. **Exterior.** Orthotic worst protrusion 0.152 mm against a 0.02 mm limit, on the partly-in-zone layers at the forefoot boundary (171, 154, 165, 166).
4. **Seam.** Bottom-edge seam moves 0.161 mm (orthotic) and 0.131 mm (box). The seam lock is a hard prohibition, separate from the exterior.
5. **Flow, box only.** 27 zone columns on the box need a bead wide enough that zone Q reaches 98.66 mm³/s against a 35 mm³/s cap. The orthotic thin-zone widening peaks at 22.89 mm³/s and is inside the cap. The cap was not raised.
6. **Not a blocker:** print time (+5.2 s and +6.2 s). Feed on extrusions is unchanged.

What would have to be true, and is not: a single inward offset of a fully closed loop, including the turn that the zone definition leaves frozen, can keep the exterior and stay continuous. The zone definition stops at the two-bead columns, and the turn is outside the zone, so the path is required to be frozen exactly where the shift has to taper to zero. Doing that taper with only a boundary split and a constant E per segment is what produces the 0.486 mm break or, if the vertex is kept continuous, a moved outside endpoint. Neither meets 0.001 mm and 0.02 mm together. That statement is the measurement, not a proposed fix.

## Reproduction

```bash
bash validation/seam_fuse_gates/slice_all.sh /tmp/sfg_slices \
  live=ghcr.io/kdho77/vertexslice-server:sha-10b3703b155be44e21269f37ef927595f06485bd
python3 docs/proof/forefoot-widen-gates/analyze_forefoot.py
```

Outputs under `docs/proof/forefoot-widen-gates/data/`: axis JSON, per-profile summary JSON, zone mask CSV, per-layer gap CSV, unbonded CSV, and (ApexCore) added-filament-by-layer CSV. The transformed G-code used for F5/F6 was written under `/tmp` and is not part of the commit.

## 1. RULE-COMPLIANCE REPORT

Evidence is this document plus `analyze_forefoot.py` and the CSVs. No file under `scripts/`, `src/`, `server/`, `validation/seam_fuse_gates/`, `validation/ref/`, or `.github/workflows/` was edited. `git status` shows only `docs/proof/forefoot-widen-gates/`.

**Hard prohibition** (Kendon 2026-11-20 12:59 PM MT and 1:00 PM MT): print surface quality is locked; no seams or layer changes on the top surface or sides; never touch surface quality; do not change the outer edge of any bead, seam positions, the butt joint, the 0.5 mm prime, the 0.7 mm dry wipe, the 10 mm F1200 seam-end slow-down, layer-change placement, wall count, wall generator, speeds/feeds, accelerations, fan (M106 S127), temperatures, retraction, infill, travel routing, anything outside the thin forefoot zone, or ApexLight/Forge/Bounce output. Protected machine commands keep their ordered sequence.

PASS for this run. No production G-code, preset, or slicer source was changed. The hypothetical transform was measured on a `/tmp` copy and shows that obeying the prohibition (frozen exterior, frozen seam, frozen outside-zone path) makes the widening discontinuous. That is why the verdict is INFEASIBLE, not a waiver.

**L1** "The seams have always been on the bottom it's been a permanent rule since the beginning." PASS. Seams were not moved in any committed G-code. The measurement finds the BASE seam on the bottom edge (`|Y−minY|≤0.25`) and records that the hypothetical shift would move it 0.161 mm.

**L2** "ABSOLUTLEY UNDER NO CONDITIONS DO YOU REINTRODUCE ANY OTHE RFUCKING PROBLEMS AND DO NOT DARE BREAK THE DO NOT CROSS THE TOP SURFACE RULE." PASS. No new G-code was committed. Travel routing and the top surface of BASE were not edited.

**L3** The outside surface of a print must not change at all in slicer fixes. PASS for this run (no fix landed). The measured transform moves the exterior by 0.152 mm on the orthotic, which is one reason it is not a permissible fix.

**L4** Slicer fixes must not meaningfully increase print time versus live. PASS as a constraint check: extrusion F is unchanged; toolpath time rises 5.20 s and 6.24 s, far under the +25 min example. The rise is the boundary G0, reported rather than omitted.

**L5** `reduce_crossing_wall=1` stays on and no travel crosses a wall. PASS. Wall generator, crossing setting, and BASE travels were not edited. The measurement G0s exist only in `/tmp`.

**L6** No production G-code goes out while hollow spots remain; every top skin must bond to the bottom skin by direct contact or through infill/solid. PASS as a process rule (nothing was sent out). The audit is the evidence the rule is not met by BASE: 6,749 and 4,750 unbonded forefoot columns.

**L7** Cooling changes did not bond the skins; the idea to study is widening the extrusion so the layers touch. PASS. Fan was not changed. Widening was assessed and found not feasible under the locked constraints.

**L8** Two Grok 4.7 agents, one to do the work and one to grade. PASS for the role assigned here: this run is the grader's Phase 0 measurement and stops before any worker.

**L9** Ditch PR 37 and start fresh from main. PASS. Base commit is main `10b3703b`. No PR 37 code was reused.

**L10** D1 inward-only widening must preserve the actual printed exterior. D2 establish whether the overlap is achievable at the cap and do not accept residual gaps. D3 no authorization to unfreeze references. PASS. Exterior was measured (0.152 mm, fails 0.02 mm). Residual unbonded columns are listed, not waived. References were not repinned.

**L11** Approve the 35 mm³/s cap for feasibility testing; a gate that passes while a forefoot pocket stays hollow is worse than a failing gate; no merge or repin follows. PASS. The cap was used as 35, not raised. Zone Q 22.89 (orthotic) is under it; zone Q 98.66 (box) is over it and is reported as a failure. Unbonded pockets force INFEASIBLE. Nothing was merged.

**Kendon item 1.** Phase 0 only. Stop after FEASIBILITY.md whether FEASIBLE or INFEASIBLE. Do not begin Phase 1, freeze gates, modify CI, or launch the worker. PASS. This file is the stop.

**Kendon item 2.** Use 40% as a provisional anatomical boundary. Verify it on the actual toe-to-heel axis of both STLs rather than assuming layer progression equals anatomical length. PASS. Orthotic axis is the plantar principal component, −2.175° from +Y, length 273.338 mm, boundary layer 165. Layer progression says layer 176, 6.745 mm heel-ward. The box is not a foot; the axis is placed +Y, length 50 mm, boundary layer 31 versus progression layer 38, 4.851 mm apart. Both are stated above.

**Kendon item 3.** Zone-boundary transitions maintain continuous extrusion within 0.001 mm, preserving the locked exterior and unchanged outside-zone paths. Do not allow discontinuities or conceal them by rounding. PASS as a measurement rule (the gaps were computed from the written text at the file's own precision and reported). FAIL as a property of the transform: written gap 0.4865 mm and 3.6540 mm, both > 0.001 mm. Rounding to 3 decimals does not change either value.

**Kendon item 4.** At least 0.05 mm geometric overlap at every bond-critical thin-zone column, target 0.10 mm. No residual gaps or unclassified columns. FAIL for the candidate. Occupied columns are fully classed (zero unclassified), but 6,749 and 4,750 residual unbonded columns sit outside the zone, and the transform that would overlap the zone columns breaks continuity, so those columns are not delivered a continuous overlap. The 0.05 mm floor was not lowered.

**Kendon item 5.** Report required peak volumetric flow, added filament, affected layers and columns, and any constraint that makes full coverage infeasible. Do not modify limits to force a successful verdict. PASS. Orthotic zone Q 22.895 / p99 20.210, added 886.85 mm / 2133.1 mm³, 17,760 zone columns, layers 1–174. Box zone Q 98.663, added 43.10 mm / 103.67 mm³, 383 zone columns. Cap left at 35. Verdict INFEASIBLE.

## 2. HANDOFF NOTE

Measurement commit `0940888cf39bee6b1eac470d037aaa2e286dcfda` adds this report, the script, and the data. `git rev-parse HEAD` on the branch tip is the handoff SHA (this sentence is the only change after that measurement commit). Residuals: Phase 0 only. No gates, no controls, no CI, no slicer patch, no worker. Next, if anyone continues, is a different approach that can bond the outer-to-inner gap on layers 118–171 (orthotic) and layers 4–30 (box) without moving the exterior or the bottom seam; this widening, as specified, cannot. Nothing here authorizes implementation, merge, deploy, or a reference repin. Physical TPU testing is still required before any of those, and only Kendon can authorize them.
