# ApexLight hollow-band gates (frozen)

Independent grader gates for the ApexLight forefoot hollow fix. These files are frozen before any candidate is graded. A later edit to the gate code invalidates `SHA256SUMS`. Check them from this directory:

```bash
sha256sum -c SHA256SUMS
```

This folder is additive. It does not change product code, slicer settings, seams, layer changes, or the outer surface. It does not merge, deploy, or touch a printer.

## What is gated

Fixture, same as #46: `validation/test_models/orthocad_sample_top_insole.stl`, `profile_id=apex-v2-insole-95a`, `wall_loops=1`, `infill_density=26`, `toe_first`, `belt_insole_place=true`.

| Gate | Pass line |
|---|---|
| H hollow | 0 hollow columns on the 0.25 mm tool. Section >= 3 mm, no non-skin material in the air, air > 1 mm. 0.46 mm material uses layers <= 165. 0.42 mm material also uses layers <= round(165 * 0.46 / layer height), which is 181 at 0.42 mm. Both counts are reported. |
| G1 thin bond | Frozen Cura-copy G1. Every 2.0–3.0 mm forefoot column overlaps by >= 0.05 mm. Zero unbonded. |
| G2 thick bond | Frozen Cura-copy G2. Every 3–6 mm forefoot column is connected through the stack with >= 0.15 mm overlap. Zero unbonded. |
| S surface lock | Outer wall and Overhang wall paths, closed seam starts, and layer-change end points are identical to live ApexLight. A seam or layer change that leaves the plantar face fails. |
| F filament | Filament may rise only by sparse infill or solid added into a section that was hollow on live. No extrusion more than 0.02 mm outside the live printed outer outline (frozen G9). Footer `[mm]` and `[g]` are quoted. |
| T slice time | Slicer wall time <= 260 s. A missing time fails closed. |
| C ApexCore identity | ApexCore G-code is byte-identical to live/main after the one OrcaSlicer stamp line is removed. |

Overall PASS only if every gate passes. Hollow, G1, and G2 fail on unchanged live ApexLight. That is the no-op. Surface lock, filament, slice time, and ApexCore identity pass when the candidate is live compared with itself. Forcing those ceilings to fail on an identical file would make the gate wrong.

## How to run

Synthetic proof, no slicer:

```bash
python3 validation/apexlight_hollow_gates/prove_gates.py
```

Live no-op, once the main slices are on disk:

```bash
python3 validation/apexlight_hollow_gates/prove_gates.py \
  --live-light LIVE_ApexLight_orthocad.gcode \
  --live-core LIVE_ApexCore_orthocad.gcode \
  --slice-seconds 158.672
```

Grade a candidate. Do not edit these gates first.

```bash
python3 validation/apexlight_hollow_gates/run_gates.py \
  --light-base LIVE_ApexLight_orthocad.gcode \
  --light-cand CAND_ApexLight_orthocad.gcode \
  --core-base LIVE_ApexCore_orthocad.gcode \
  --core-cand CAND_ApexCore_orthocad.gcode \
  --slice-seconds 176.059 \
  --json /tmp/apexlight_hollow_grade.json
```

The live files this freeze was measured on are the docker-server-proof slices of main `d935197976b7cdd2fa89ced4ff43930b0212959f`, Actions run `45295959179`:

| File | Role | SHA-256 |
|---|---|---|
| `live_apex_light_orthocad.gcode` | live ApexLight, and main's ApexLight after the stamp strip | `f0b78d8dc4f57b8acee18cf9929f60ca4eaa44b8b796a9cbba608c30cdcab3e1` |
| `cand_apex_core_orthocad.gcode` | main's ApexCore, the #46 file, 439 layers | `318de8f96bfe2455edc7b5aa4a886ee62b1fe6e3cba4c7729400d254762e15f3` |

## No-op on that live ApexLight

| Gate | Result | Measured |
|---|---|---|
| H | FAIL | forefoot layers <= 165: 1387. Physical window is the same 165. Total 5683. Max air 11.633 mm. |
| G1 | FAIL | 14377 unbonded of 14813. |
| G2 | FAIL | 8648 unbonded of 9003. Belt air 91.8%. Dorsal air 82.4%. |
| S | PASS | walls, seams, and layer changes identical. |
| F | PASS | 26227.75 mm, 75.70 g, increase 0, nothing outside the outline. |
| T | PASS | 158.672 s on this host. The PM's 176.059 s is also under 260 s. |
| C | PASS | main ApexCore vs itself. Forefoot hollow on that file is 0. |

The PM's hollow count on the same fixture was 1406 in the forefoot and 6210 total, with the same max air 11.633 mm. This tool's forefoot count is 19 columns lower and its total is 5683. G1, G2, the max air, and ApexCore's forefoot 0 match the PM's readings on these bytes. The pass line is 0 either way, so the no-op fails.

## Bad changes that must fail

| Edit | Gate |
|---|---|
| An empty >= 3 mm section left between the skins | H, and G2 |
| A thin section left unbonded | G1 |
| Seam start moved to the dorsal edge | S |
| Layer-change travel moved off the belt face | S |
| One outer-wall endpoint moved | S |
| A bead poked outside the live outline | F |
| Slice time 261 s, or no time supplied | T |
| Any ApexCore byte other than the stamp | C |

A fill of sparse infill placed only in the hollow gap, with the walls left alone, clears H and keeps S passing. That is the shape of an acceptable change. It is not a slicer change.
