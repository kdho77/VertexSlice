# IR3 V2 gate warnings and TPU/1.0 intake (no print)

Source G-code: Windows CLI `plate_1.gcode` from run 36774518246
(`box_10x10x10.stl`, 0.4 mm + Generic PLA). **Do not print that file** on a
1.0 mm / 75D TPU machine.

Checks were **not** relaxed to obtain PASS.

## R6 — Y-hops vs retracts (WARN)

**What it detects.** After `;LAYER_CHANGE`, a “Y-hop” is a `G1` whose Y
increases by more than 0.1 mm and that has **no** E. A “retract” is any `G1`
whose E value drops by more than 0.01 versus the previous E. The rule wants
`y_hops / retracts ≥ 0.5` so travel lifts the gantry (Y) instead of dragging
the nozzle. This is a WARN, not a driver-killer.

**What this file triggered.** 180 Y-hops / 619 counted retracts (ratio 0.29).

Exact hop example (first counted hop):

```
G1 X120.177 Y.65 Z.566 F24000
```

Exact lines the counter treated as retracts include real retracts **and**
relative-E extrusions after `M83`:

```
G1 E-1.5 F2100          ; real retract (line 109)
G1 X129.788 Y.3 E.2653  ; extrusion; counted as retract because 1.5 → 0.2653
```

The start block emits `M82`, then the slicer switches to `M83`. The gate still
compares raw E numbers, so most of the 619 “retracts” are false. The 180 Y
increases are real XY travels; many are path-to-path moves, not dedicated hops.

**Evidence to resolve.** A G-code from the **same** machine/nozzle/filament you
will print, where either (a) true retracts (`G1 E-…` with no XY) are each
followed by a Y lift, or (b) you accept the WARN after reviewing travel in
preview and confirming `z_hop` is a gantry lift. Do not lower `MIN_Y_HOPS_RATIO`.

## R7 — first-layer Y (WARN)

**What it detects.** Y on `G1` moves in the first `;LAYER_CHANGE` block must
exist and lie in `[0, 2]` mm. Y > 2 mm is FAIL (belt slam). Y < 0 is FAIL
(nozzle below belt). No Y at all is WARN.

**What this file triggered.** The first layer block has no Y:

```
;LAYER_CHANGE                 ; line 69
G1 E-1.5 F2100                ; line 76 — only G1 in layer 1
;LAYER_CHANGE                 ; line 82  (gate now calls this layer 2)
G1 X120.212 Y.7 Z.283 F24000  ; line 92 — first Y, not scored as layer 1
G1 X120.212 Y.3               ; line 93
G1 X129.788 Y.3 E.2653        ; line 100 — first extrusion, Y=0.3 mm
```

Manual read of the first extrusion: Y = 0.30–0.70 mm (would pass R7 if the
gate had seen it on layer 1).

**Evidence to resolve.** Exported G-code whose **first** `;LAYER_CHANGE`
section contains `G1` moves with Y, and `max(Y) ≤ 2`. That is a slicer
layer-comment / placement issue, not a reason to skip R7.

## R11 — nozzle below belt plane (WARN)

**What it detects.** `z_mach = Z_gcode − Y_gcode/√2`. If `z_mach < −0.05` mm
the nozzle is geometrically through the 45° belt. Extrusion below the plane
is FAIL. Travel-only is WARN.

**What this file triggered (exactly two travel moves):**

| Line | G-code | Z used | Y | z_mach | Meaning |
|---|---|---|---|---|---|
| 92 | `G1 X120.212 Y.7 Z.283 F24000` | 0.283 | 0.7 | **−0.212** | Approach before first extrusion. Next move is `G1 X120.212 Y.3` (no E), then extrude at Y=0.3 where z_mach ≈ +0.07 (safe). |
| 3346 | `G1 Y50 F1000 ; lift nozzle 50mm` | 19.516 (stale last-layer Z) | 50 | **−15.839** | System **end** G-code, after `G28`. Firmware `position_max` Y is 354. Same `G1 Y50` appears in checked-in `PRINT_END` / `CANCEL_PRINT`. |

**Evidence to resolve.** For line 92: a first-layer approach with
`Y ≤ (Z + 0.05)·√2` (at Z=0.283 that is Y ≤ ~0.47 mm), or a photo/log from a
later physical first-layer test — not a threshold change. For `G1 Y50`:
confirm on **your** live `printer.cfg` that after `G28`, Y=50 is a gantry lift
inside `position_max`, and that you still want this end move. The gate will
keep WARNing while it uses last-print Z across end G-code. Do not delete R11.

## Start / end macros (system IR3 V2 profile)

Inlined in `fdm_ideaformer_common.json`. The slicer does **not** call
`PRINT_START` / `PRINT_END`, though those macros exist in the **checked-in**
`validation/printer_config/printer.cfg` (snapshot, not proven live).

### Start (required names)

| Token | Kind | Role |
|---|---|---|
| `G28` | standard | Home all |
| `G1 Y20` | motion | Lift gantry 20 mm |
| `M140` / `M190` | standard + optional `[gcode_macro M190]` | Bed from **filament** `[hot_plate_temp_initial_layer]` |
| `M104` / `M109` | standard + optional `[gcode_macro M109]` | Hotend from **filament** `[nozzle_temperature_initial_layer]` |
| `G1 Y.1` + `G1 E15` | motion | Purge blob |
| `G1 Z20 E25` | motion | Belt advance + extrude |
| `G28 Y` | standard | Re-probe belt |
| **`FMS_on`** | **custom** | `SET_FILAMENT_SENSOR SENSOR=encoder_sensor ENABLE=1` |
| `G1 X250 E50` / `G1 X0 E75` | motion | Prime across 250 mm |
| `G92 Z0` | standard | Belt origin |
| `M83` | slicer (after start `M82`) | Relative E for the print |

### End (required names)

| Token | Kind | Role |
|---|---|---|
| `G28 X` | standard | Home X |
| `M104 S0` / `M140 S0` | standard | Heaters off |
| `G1 E-5` / `G4 P7000` | motion | Retract + ooze wait |
| `G28` | standard | Home all (Y/Z at endstops — typically Y=0, Z=0) |
| **`G1 Y50 F1000`** | motion | Gantry lift 50 mm. Safe only if live Y `position_max` ≥ 50 and Y is gantry, not belt. |
| **`FMS_off`** | **custom** | Disable `encoder_sensor` |
| **`BED_MESH_CLEAR`** | Klipper | Requires `[bed_mesh]` |
| `M84` | standard | Motors off |

Slicer body also emits `SET_VELOCITY_LIMIT` (built-in Klipper, not a user macro).

**Check on the live printer (do not use the repo snapshot as proof):**

1. `[gcode_macro FMS_on]` / `FMS_off` exist and name `encoder_sensor`.
2. `[filament_motion_sensor encoder_sensor]` exists (or change/remove `FMS_*`
   in **your** profile — do not invent a replacement here).
3. `[bed_mesh]` exists if you keep `BED_MESH_CLEAR`.
4. Axis map still X=lateral, Y=gantry, Z=belt; Y `position_max` ≥ 50.
5. `G1 Y50` after `G28` is a lift, not a belt advance. If your live end macro
   differs, send that file; do not assume the snapshot.
6. `PRINT_START` / `PRINT_END` are **not** invoked by the system profile. If
   your working slicer calls them instead, send that profile.
7. Heater caps: checked-in machine `max_nozzle_temp` is 260 °C; firmware
   `max_temp` 310 °C. Your 250 °C TPU is inside those **snapshot** numbers
   only — confirm live `[extruder]` / `[heater_bed]`.

## Files needed for a 1.0 mm / 75D TPU test

There is **no** IdeaFormer 1.0 mm machine and **no** TPU filament in
`resources/profiles/IdeaFormer/`. Other vendors’ TPU JSON will not be copied
in and called compatible.

Send these from the setup that already prints 1.0 mm 75D TPU (IdeaMaker,
another Orca, or machine macros). Prefer the live copies, not repo samples.

1. **Live Klipper config** — `printer.cfg` plus every `include`. Need
   `[printer]`, steppers (X/Y/Z limits), `[extruder]` (`nozzle_diameter`,
   `max_temp`), `[heater_bed]`, `[bed_mesh]`, `[filament_motion_sensor]`.
2. **Live macros** — at least `FMS_on`, `FMS_off`, and whatever start/end you
   actually run (`PRINT_START` / `PRINT_END` or slicer-inlined blocks).
3. **Working slicer machine profile** — 1.0 mm nozzle, belt flags, start/end
   G-code, printable area, `gcode_flavor` (export JSON or the 3MF you slice
   with).
4. **Working process + filament** — layer height / line width for 1.0 mm, and
   the 75D TPU temps, flow, volumetric limit, retraction, bed temp (the
   numbers you already use at ~250 °C).
5. **One known-good G-code** you have printed (or safely dry-run) with that
   1.0 mm TPU setup, plus a one-line note of slicer name and whether it
   succeeded on-machine.

Until those exist, no 1.0 mm / TPU profile or test G-code will be authored
here.

## GUI / preview (this host vs Windows)

| Check | This host (Linux agent) | You on Windows x64 |
|---|---|---|
| Install / unzip package | Not done | Required |
| Launch `orca-slicer.exe` / wizard | **Not performed** | Required |
| Load IdeaFormer IR3 V2 0.4 (system) | Static JSON only | Confirm in Printer settings: belt, 45°, Klipper |
| Load 1.0 mm / TPU | **No such system preset** | Import **your** profiles after you send them |
| Slice + belt preview at 45° | **Not performed** | Required — first-layer Y near belt, no collapsed stack |
| Export + `belt_gcode_gate.py` | Done for 0.4/PLA CLI file only | Required on any new export |
| Physical print | Not done | Not requested in this step |
