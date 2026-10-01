# IR3 V2 gate corrections and pre-print status

Validator parser defects (M82/M83, empty LAYER_CHANGE, stale G28 coordinates,
and a wrong R11 formula) were corrected in `validation/belt_gcode_gate.py`.
Checks were not relaxed to obtain PASS: invalid calculations were replaced
with ones that match this fork’s emit path and IR3 axis names.

Source G-code: Windows CLI `validation/tests/fixtures/plate_1_windows_cli_box10.gcode`
(`box_10x10x10.stl`, **0.4 mm + Generic PLA**). Do **not** print that file on a
1.0 mm / 75D TPU machine.

Regression: `python3 validation/tests/test_belt_gcode_gate_parser.py`

## R11 formula vs this fork (traced)

`GCodeWriter` applies `BeltTransform::inverse_transform_point` before emit
(`src/libslic3r/GCodeWriter.hpp` `emit_xy` / `emit_xyz`). Locked inverse
(`src/libslic3r/BeltTransform.cpp`):

- `Y_mach = √2 · Y_virt` (gantry)
- `Z_mach = −Y_virt + Z_virt` (belt; inclined Z cancels so Z_mach ≈ layer_z)

IR3 firmware (`validation/printer_config/printer.cfg`, snapshot):
`[printer] kinematics: corexy` — **no** firmware belt transform. Start G-code
comments: X=lateral, **Y=gantry height**, **Z=belt**.

Therefore **file Y/Z are already machine axes**. Nozzle-through-belt is
**gantry Y below the belt plane** (`Y_mach < 0`, same idea as the C++ clamp
`Y_mach >= 0`). It is **not** `Z_file − Y_file/√2`.

That old quantity on line 92 (`Y=0.7`, `Z=0.283`) gave −0.212 and was a
**validator false positive**. Line 92 was not changed. R11 now scores known
machine Y only (`R11-GANTRY`). After `G28`, Y/Z are **unknown** until an
absolute move or `G92`; unknown positions are not scored as collisions.

## Re-run on the Windows CLI G-code (corrected gate)

| Rule | Result |
|---|---|
| R1–R5, R8, R9 | PASS |
| R6 | PASS (heuristic: 180 Y-lifts / 70 retracts). See below. |
| R7 | PASS — first **model-extrusion** layer Y [0.250, 0.700] mm |
| R11 | PASS — known gantry Y ≥ −0.05 mm on model moves |

Gate exit 0 on this file is **not** a physical-print recommendation and does
**not** apply to 1.0 mm TPU.

## Separate findings

### Confirmed G-code defects (this 0.4/PLA file)

None demonstrated after the parser corrections. Start/end blocks match the
system IR3 V2 profile (`FMS_on` / `FMS_off`, `BED_MESH_CLEAR`, `G1 Y50`).
First model extrusion is at Y≈0.3 mm. `M83` follows start `M82`.

### Validator false positives (previous report)

| Old finding | Cause |
|---|---|
| R6 180/619 = 0.29 WARN | Relative `M83` `G1 … E0.2653` after `E1.5` was counted as retract. True retracts are `G1 E-1.5`. |
| R7 “no first layer Y” | First `;LAYER_CHANGE` was retract-only (`G1 E-1.5`). First model Y is on the next block. |
| R11 line 92 `z_mach=−0.212` | Wrong formula `Z − Y/√2` on machine coords. Y=0.7 mm is gantry height above the belt. |
| R11 line 3346 `G1 Y50` | Stale last-layer Z=19.516 after `G28`. Homing clears known position; Y=50 is then a known gantry lift, not a dive. |

### Uncertainties (need live printer / Windows GUI)

- Live `printer.cfg` and macros (repo file is a snapshot).
- Whether `FMS_*` / `encoder_sensor` / `[bed_mesh]` exist on the machine.
- Whether Y50 after `G28` is still a gantry lift on **this** printer (`position_max` Y ≥ 50).
- GUI launch, profile load, belt preview (not run on this Linux host).
- 1.0 mm nozzle and 75D TPU ~250 °C: **no system profiles**. Do not use this G-code.

## R6 hop/retract ratio

**Heuristic only**, not a demonstrated machine interlock. Firmware has no
“must hop after retract” rule. The slicer *can* emit gantry Y-lifts
(`z_hop` reinterpreted in `GCodeWriter`). The 0.5 ratio was an informal
review aid. It is WARN-only and must not be used to demand motion changes
by itself.

## Start / end macros (unchanged; verify live)

Custom names in the **system** profile: `FMS_on`, `FMS_off`, `BED_MESH_CLEAR`.
Also `G28` / `G28 Y` / `G28 X`, bed/hotend waits from the **filament** profile,
purge/prime, `G92 Z0`, end `G1 Y50 F1000`, `M84`. `PRINT_START` / `PRINT_END`
exist in the snapshot cfg but the system profile does not call them.

## Files still needed for a 1.0 mm / 75D TPU test

1. Live `printer.cfg` + includes (XYZ limits, extruder `nozzle_diameter` / `max_temp`, bed, mesh, filament sensor).
2. Live `FMS_on` / `FMS_off` and actual start/end macros.
3. Working 1.0 mm machine profile (JSON or 3MF).
4. Working process + 75D TPU filament (~250 °C, bed, flow, volumetric, retract, 1.0 mm line width).
5. One known-good G-code from that setup, plus slicer name and on-machine result.

## GUI / preview

| Check | This host | You on Windows |
|---|---|---|
| Launch `orca-slicer.exe` | **Not performed** | Required |
| Load IR3 V2 0.4 | Static JSON only | Confirm belt / 45° / Klipper |
| 1.0 mm / TPU | **No system preset** | Import your profiles after you send them |
| Belt preview | **Not performed** | Required |
| Physical print | Not done | Not requested here |
