# Forefoot seam split — ApexCore 95A slice (2026-11-13)

Diagnostic only. The locked ApexCore 95A profile was not changed. No printer commands were run.

## Result

The lengthwise split in the thin forefoot is produced by the slicer. The OrthoCAD mesh in this fixture is a solid 2.20–2.31 mm slab there. `/v1/slice` with `apex-v2-insole-95a` and default TPU 95A (`apex_core`) turns that slab into **one closed outer wall whose top and bottom skins meet on a tangent (in-plane gap 0.002 mm, overlap within ±0.004 mm)**. There is no inner wall, no gyroid, and no gap fill on those layers, so nothing stitches the two skins together. In clear TPU, with the locked 50% part fan from layer 0, that joint is a continuous unbonded plane through the forefoot, top skin against base skin, for the full width.

A second, separate line is also in the G-code. The belt-seam postprocess restarts every wall at the **midpoint of the belt-contact edge**. Over the thin forefoot those starts stack into one straight line (lateral residual 0.11 mm) on the belt face. `seam_position` does not place that line. The postprocess does.

## What `/v1/slice` actually runs

`POST /v1/slice` with `profile_id=apex-v2-insole-95a` and `material` omitted resolves to `apex_core` (`server/app.py`, `server/profile_registry.py`). `slice_stl` then leaves the generic Orca path and runs the ApexCore chain `apex_core-g2b-v1`:

1. `prepare_orthocad_insole_stl` — toe-first, Z rotation 270°, X center 140 mm. `print_direction` from the form is not read; the chain hardcodes toe-first.
2. `scripts/g2b_heelmod/slice_apexcore_g2b.py core` — flatten `0.42mm TPU Insole Medium @Vertex Apex V2.json`, overlay `g2b_config.json`, refuse the slice if `apexcore_95a_locked.json` drifts, then apply `VS_SPARSE_INFILL_DENSITY`.
3. `postprocess_g2b.py match` — shear E fix, then belt-side seam (`g2b_anchor=True`).
4. `pullback_v2.py`
5. `topfix_apexcore.py`

`wall_loops` on the form is parsed and echoed, and the profiles endpoint lists it as user-modifiable with default 1. The ApexCore chain does not apply it. The only request field this chain writes onto the locked profile is sparse infill density. For `apex-v2-insole-95a` that default is Soft **20%** gyroid.

The IdeaFormer file the profile id points at (`0.42mm TPU Insole @Vertex Apex V2.json` → `fdm_process_apex_insole_common` → `fdm_process_common`) is the parent. It is not the file that gets sliced. After the G2b overlay the effective values are the ones in the table below.

## Settings in force on this slice

Taken from the process JSON `slice_apexcore_g2b.py` wrote before the slice (`PREP_ONLY=1`), plus the postprocess constants.

| Setting | Value used | Where it comes from |
|---|---|---|
| `wall_loops` | `2` | Locked (`apexcore_95a_locked.json`). Parent insole profile says `1`. API default says `1`. |
| `wall_generator` | `arachne` | `g2b_config.json`, checked by `assert_g2b_identity` |
| `line_width` / outer / inner | `1` mm | G2b overlay |
| `min_bead_width` | `50%` (0.50 mm) | G2b identity |
| `wall_distribution_count` | `1` | G2b overlay |
| `detect_thin_wall` | `0` | Inherited `fdm_process_common`. Arachne does not use this. |
| `filter_out_gap_fill` | `9999` | G2b identity. Drops every gap fill. Parent value is `0.3`. |
| `gap_fill_target` | `nowhere` | G2b overlay |
| `sparse_infill_pattern` | `gyroid` | Locked |
| `sparse_infill_density` | `20%` | Applied after the lock check, from the Soft preset |
| `minimum_sparse_infill_area` | `5` mm² | Locked |
| `infill_wall_overlap` | `15%` (0.15 mm) | Locked. Parent `fdm_process_common` is `25%`. |
| `seam_position` | `aligned` | G2b overlay. Parent `fdm_process_common` is `nearest`. Overwritten again by the postprocess. |
| `seam_gap` | `0` | G2b overlay |
| `staggered_inner_seams` | `0` | G2b overlay |
| `wipe_on_loops` / `wipe_distance` | `1` / `0.7` | G2b overlay. The match postprocess emits its own 0.7 mm dry wipe. |
| `retraction_length` | `0` | Locked (machine and filament). The match postprocess then inserts a 0.5 mm retract and a 0.45 mm prime at each wall start. |
| `ensure_vertical_shell_thickness` | `none` | G2b identity |
| `top_shell_layers` | `0` | G2b identity |
| fan | 50% part (`M106 S127`), 25% aux, from layer 0 | Locked |
| `y_mach_offset` | `-1.146` | Locked |
| `reduce_crossing_wall` | `1` | G2b overlay (travel combing). It does not move walls. |

Belt math used to read the G-code (`src/libslic3r/BeltTransform.cpp`): machine Y = √2 × virtual Y, and bead width is the in-plane width. A gap computed as `(ΔY_machine − width)` is about √2 too large. The numbers below are in the slice plane, where the 1.10 mm width applies.

## Slice

- Fixture: `validation/test_models/orthocad_sample_top_insole.stl` (OrthoCAD sample top, 74 940 triangles). Bounds before placement: length 273.1 mm, width 92.7 mm, height 25.2 mm.
- Placement: rotation 270°, length along belt Y 273.1 mm, X center 140 mm.
- Binary: `OrcaBelt-2.3.2-dev` from the main Docker-proof artifact `orca-slicer-c3510806d2`. Perimeter and seam C++ are unchanged since that commit. The slice used the current tree’s `g2b_config.json` (`reduce_crossing_wall` `1`).
- Chain env: `VS_SPARSE_INFILL_DENSITY=20%`, `APEX_MIN_LAYER_TIME=4`.
- Output: 439 layers, max belt Z 286.89 mm. `topfix_apexcore.py` removed 0 pairs on this mesh (its locked table is for a different file).

Mesh thickness along the scan, 2 mm bins, median of local Z span: the toe end (original X 210–260 mm, which the 270° placement prints first) is **2.20–2.31 mm**. OrthoCAD’s “>1.2 mm, single loop, watertight” check is consistent with this. The slab is thick enough for a 1.0 mm bead and too thin for two closed 1.0 mm loops plus infill.

## G-code evidence

Feature counts in the final file: Outer wall 316, Inner wall 308, Sparse infill 248, Overhang wall 8, Internal solid infill 4. **Gap infill: 0.**

| Belt Z | What is extruded |
|---|---|
| 2.0–77.4 mm (117 layers) | Outer wall only. No inner wall, no sparse infill, no gap fill. |
| 78.1 mm (layer 118) | First inner wall. |
| 109.3 mm (layer 166) | First sparse infill. The 20% gyroid never reaches the thin forefoot. |

### Tangent joint (the through-thickness split)

Layer 40, belt Z 27.32 mm, is in the middle of that thin run. The loop’s long edges:

| Skin | Machine Y | In-plane Y (Y/√2) | `;WIDTH` |
|---|---|---|---|
| Bottom (belt) | 0.093–0.097 | 0.066–0.069 | 1.10153 mm |
| Top | 1.655 | 1.170 | 1.10153 mm |

In-plane centerline spacing is 1.103 mm. In-plane gap = 1.103 − 1.10153 = **0.002 mm**. The two skins are planned to touch and not to overlap.

The same measurement on every 2 mm station across the width, for the single-wall layers: median in-plane gap stays between **−0.004 and +0.004 mm**. 0 of 117 layers have a median gap above 0.05 mm. Arachne widened the single bead until it exactly spanned the section (`wall_loops` is 2, and the section cannot hold a second closed loop, so the inner wall is absent). With `gap_fill_target=nowhere` and `filter_out_gap_fill=9999`, the 0.002 mm sliver is not filled. With `minimum_sparse_infill_area=5` and a 1.0 mm infill line, gyroid cannot start. `infill_wall_overlap=15%` has no infill to overlap in this region.

`detect_thin_wall=0` does not change this. The generator is Arachne.

That joint runs the full width of the forefoot and the full 2–77 mm of belt travel. It is the plane between the top skin and the base skin. Outer-wall speed in this profile is 26 mm/s, so the nozzle finishes the bottom edge, goes around the end, and comes back along the top a few seconds later, into a 50% fan. A tangent joint with that delay is the unbonded split.

### Stacked belt seam (a straight line, on the belt face)

`postprocess_g2b.py match` calls `_belt_split`: a closed wall is restarted at the midpoint of its minimum-Y edge. Landings below are the `;seam travel` X plus the following `;lower` Y in `final.gcode`.

Thin forefoot, layers 8–78 (belt Z 6.50–52.04 mm), one start per layer:

| | |
|---|---|
| Seam Y | 0.093–0.100 mm, standard deviation **0.002 mm** (the belt face) |
| Seam X vs layer midpoint | mean offset **0.33 mm**, standard deviation 0.37 mm |
| Seam X vs belt Z | slope 0.124 mm per mm of belt (the foot’s own center shifts as it widens), residual standard deviation **0.108 mm**, max residual 0.214 mm |

They stack. The line is straight to about a tenth of a millimeter, and it sits on the center of the belt-contact edge.

Samples:

| Layer | Belt Z | Seam X | Seam Y | Layer mid X | Offset |
|---|---|---|---|---|---|
| 8 | 6.50 | 130.90 | 0.097 | 129.64 | +1.27 |
| 20 | 14.31 | 131.96 | 0.097 | 131.29 | +0.67 |
| 40 | 27.32 | 133.88 | 0.097 | 133.61 | +0.27 |
| 60 | 40.33 | 135.35 | 0.098 | 135.27 | +0.08 |
| 78 | 52.04 | 136.56 | 0.098 | 136.51 | +0.06 |
| 80 | 53.34 | 113.16 | 0.113 | 136.64 | −23.48 |

Layer 80 is where the section stops being one clean loop and the start jumps off the center line. From there to the arch the starts scatter.

Raw layer 40, before the postprocess, already uses the bottom edge, and it starts at the **left end** of that edge (X 102.585, Y 0.093), not the midpoint. The center placement is added by `_belt_split`. Setting `seam_position` to `nearest` or `random` does not move this start while `match` still runs with `g2b_anchor=True`.

At that start the postprocess also writes, on a travel longer than 2 mm: `G1 E-0.5`, a 0.4 mm Y lift, `G1 E0.45`, and a 0.7 mm dry wipe (`RETRACT`, `PRIME`, `WIPE_MM` in `postprocess_g2b.py`). The locked retraction length of 0 is what the slicer emits. The 0.5 mm retract is added afterward. For TPU that is a second scar on the same straight line. It is a surface line on the belt face. It does not run from the top skin to the base skin; the tangent joint does that.

## Fix status

Not applied. No test was added.

A change that would actually stitch the forefoot has to move locked keys and G2b-identity keys together. `prepare()` aborts if a locked value drifts, and `assert_g2b_identity` aborts if `wall_generator`, `filter_out_gap_fill`, or `min_bead_width` drift. The printed-file sha the lock protects would change. That needs Kendon’s approval before it is edited.

Gap fill alone does not fix the joint. The in-plane gap is already 0.002 mm, so a gap-fill pass has nothing to extrude until the generator leaves a real interior.

### Proposed values (not written)

For the tangent joint, as one set:

| Setting | Old | Proposed | Guard |
|---|---|---|---|
| `wall_generator` | `arachne` | `classic` | G2b identity |
| `wall_loops` | `2` | `1` | Locked. This is already the parent insole value and the API default. |
| `filter_out_gap_fill` | `9999` | `0` | G2b identity. Parent is `0.3`. |
| `gap_fill_target` | `nowhere` | `everywhere` | G2b overlay |
| `min_bead_width` | `50%` | `25%` | G2b identity. Lets a bead narrower than 0.50 mm fill a leftover sliver. |
| `detect_thin_wall` | `0` | `1` | Inherited. Used by classic walls. |

Why this set: one classic wall on a ~2.2 mm section leaves an interior on the order of 1 mm, and gap fill can stitch it. Arachne with the current keys spends the whole thickness on one bead and then refuses the stitch. `wall_loops=1` without gap fill would open a hole; the two changes belong together.

`infill_wall_overlap` `15%` → `25%` (the parent value) would only change layers from belt Z 109 mm onward, where gyroid exists. It does not touch the forefoot joint. Left unchanged.

Fan stays at the locked 50% from layer 0 in this proposal. That fan is what gives the return pass time to cool on a tangent joint. If a stitched wall still splits on a print, the next locked knob is `close_fan_the_first_x_layers` (`0` today), not a higher flow. `outer_wall_flow_ratio` is locked at `1.0142` to hit the measured 0.18 E/mm and should stay there.

For the straight belt-face line, the edit is in `postprocess_g2b.py`, not in `seam_position`:

| Knob | Old | Proposed |
|---|---|---|
| `_belt_split` midpoint restart (`g2b_anchor=True`) | on | off, so the slicer’s seam is the one that prints |
| `seam_position` (only after that) | `aligned` | `nearest` |
| `seam_gap` | `0` | `0` (keep) |
| injected retract / prime | 0.5 / 0.45 mm | `0` / `0`, matching locked `retraction_length` `0` |
| dry wipe | 0.7 mm | `0` for this TPU |

`seam_gap=0` is already the right TPU value. Aligned versus nearest does not matter until the postprocess stops moving the start.
