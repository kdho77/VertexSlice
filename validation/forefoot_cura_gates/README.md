# Forefoot Cura-copy gates (frozen, Phase A)

Independent grader gates for the ApexCore forefoot Cura-copy. These files are frozen. A change to any file under this folder invalidates `SHA256SUMS`. Check them from this directory: `sha256sum -c SHA256SUMS`. Phase A stops here. Phase B grades a worker's CI artifacts with these files unchanged. A pass does not merge, deploy, or repin anything.

This folder is additive. Every file that already existed at `10b3703b155be44e21269f37ef927595f06485bd` stays byte-identical.

## Scope

ApexCore forefoot only.

| Model | STL | Forefoot layers |
|---|---|---|
| `orthocad` | `orthocad_sample_top_insole.stl` | 1–165 |
| `insole` | `insole_sample.stl` | 1–31 |

Layers after that boundary, and ApexLight / ApexForge / ApexBounce, are out of scope and must stay identical to BASE.

The sacrificial leader is ignored (L14). On a Cura file, layers 0–57 are dropped, and on layers 58–66 every segment whose both ends have Y > 4.5 mm is dropped. VertexSlice skirt, brim, support, prime tower, and wipe tower are dropped. Moves after command `G91` are ignored.

## How to run

Synthetic proof (no printer, no CI):

```bash
python3 validation/forefoot_cura_gates/prove_gates.py
```

Exit 0 means every expected PASS/FAIL in `proof/phase_a.json` held. Fixtures live in `fixtures/`. Deliberate bad edits are written under `/tmp` and are not part of the freeze.

Phase B, once a worker PR supplies candidate G-code. Do not edit these gates first.

```bash
python3 validation/forefoot_cura_gates/run_gates.py \
  --core orthocad:BASE_orthocad.gcode:CAND_orthocad.gcode \
  --core insole:BASE_insole.gcode:CAND_insole.gcode \
  --pair ApexLight:BASE_light_orthocad.gcode:CAND_light_orthocad.gcode \
  --pair ApexLight:BASE_light_insole.gcode:CAND_light_insole.gcode \
  --json /tmp/phase_b.json
```

Add `--pair ApexForge:...:...` and `--pair ApexBounce:...:...` when those files exist. Add `--strict-materials` to fail closed if Forge or Bounce is missing. Without that flag a missing pair is listed as unchecked and is not a silent pass. BASE CI only uploads Core and Light.

Overall PASS only if G1–G9 all pass.

## Gates

Bead width is `w = (E/mm) * A / H + H * (1 - pi/4)` with `H = 0.46` and filament diameter 1.75 mm. Flow ratio is already in E. Columns are 0.5 mm in X. Only segments with `|dx| / length >= 0.25` enter a column. Skin is Outer wall, Overhang wall, or `WALL-OUTER`.

| Gate | Pass line | What is reported |
|---|---|---|
| G1 Thin bond | Every frozen 2.0–3.0 mm forefoot column has skin overlap ≥ 0.05 mm. Zero unbonded. | Median envelope gap vs Cura −0.45 mm. |
| G2 Thick toe | Every 3–6 mm column has inner material touching both skins, overlap ≥ 0.15 mm, and the stack is connected. Zero residual air. | Belt and dorsal air % vs Cura 46% / 39%. The pass line is 0%, not Cura's residual. |
| G3 Outer edge | Printed outer edge (centre ± half E-width, true perpendicular rim) within 0.02 mm of BASE at p99. Nothing over 0.05 mm. Both directions. | p99 and max, mm. |
| G4 Seams | See the station rule below. | Closed loops, off-bottom starts, starts that left the BASE station. |
| G5 Outside scope | Layers after the forefoot boundary byte-identical. Process M-codes identical. Each supplied ApexLight / Forge / Bounce file byte-identical after the Orca stamp strip. | Unchecked materials. |
| G6 Flow | Every forefoot extrusion of length ≥ 0.05 mm has `Q = (E/L) * A * (F/60) ≤ 17.1` mm³/s, and none over 35 mm³/s. | Max Q, counts over each cap. |
| G7 Time | Toolpath seconds ≤ BASE + 2%. | Candidate, BASE, delta %. The footer time is not the pass line. |
| G8 Crossings | `perimeter_crossings`, `surface_crossings`, and `seam_block_crossings` are 0. `skin_interior_travels` does not exceed BASE. | The crossing counts. |
| G9 Filament | No extrusion more than 0.02 mm outside the BASE printed outer outline. | Forefoot filament increase, mm. The increase itself is not a failure. |

G4 station rule. The brief asked for seam positions within 0.05 mm of BASE. A correct inside-only widen keeps the outer edge put and shifts the plantar centreline inward by about 0.23 mm, so a 0.05 mm ball on the centreline would reject that copy. The frozen check locks the belt-edge station (X) to 0.05 mm and allows the centreline (Y) to move inward by up to 0.50 mm. A start on the top is several millimetres away and fails. Loop starts must stay within 0.15 mm of that loop's own plantar edge. The first Z-changing travel of each forefoot layer must end within 0.55 mm of the plantar edge (the 0.40 mm belt-seam lift fits) and inside the same X / Y window. This is a grader judgment. The PM can reject it.

G8's checker, `crossings_frozen.py`, is a byte copy of `scripts/g2b_heelmod/check_travel_wall_crossings.py`. Its SHA-256 is `5641f7ec52424040ca11a14090462ab2448b1fc8f3b51695530a18b22d1faf19`.

## What "fails on BASE" means

G1, G2, and G6 fail on unchanged BASE. That is the no-op failure: the forefoot is not yet a Cura-copy.

G3, G4, G5, G7, G8, and G9 are ceilings. An identical file passes them. Forcing those to fail on an identical file would make the gate wrong. Each of them fails on its deliberate bad edit:

| Edit | Gate that must fail |
|---|---|
| Outer wall moved 0.1 mm inward | G3 |
| Seam moved to the top | G4 |
| One byte changed after the forefoot | G5 |
| A forefoot move sped past 35 mm³/s | G6 |
| A long move slowed so time exceeds +2% | G7 |
| A travel inserted across the wall | G8 |
| A bead poked outside the BASE outline | G9 |

The synthetic inside-only widen passes G1–G4 and G6–G9. Overall no-op on real BASE is FAIL.

## Phase A evidence (measured)

BASE G-code is the existing artifact from Actions run `44943125841` (Docker server proof, head `43e9861a`, not `10b3703b`). This grader did not trigger CI and did not re-slice.

| File | Bytes | SHA-256 |
|---|---|---|
| `BASE_ApexCore_orthocad_sample_top_insole.gcode` | 3,066,080 | `b58621bce3669580bb9b2e1ef67c7251e8155396c87cbe4098b477b05768b66a` |
| `BASE_ApexCore_insole_sample.gcode` | 738,797 | `c78aecc9c792cd860694bf4dfaee2b495e5e480d607b61c9087d0991d71c5c81` |
| `BASE_ApexLight_orthocad_sample_top_insole.gcode` | 3,060,975 | `e247af93dc87ed394a54973367bc87d7bd7b833fcbc7accbe35a7b200b408364` |
| `BASE_ApexLight_insole_sample.gcode` | 899,122 | `093881568a8d7e60af291dc10a53e53452bf74085b18b67e8e5061f924ec0292` |

The orthocad file is the same size as the PR #39 slice of `10b3703b`. The header timestamp differs from the appendix hash `e9110fe0…`. Geometry matches that appendix: 165/165 bottom seams, thin-zone median gap 0.410 mm, footer `1h 27m 15s`. The LIVE image pin on that run is `ghcr.io/kdho77/vertexslice-server@sha256:dd5127f59f5acc9674ae0bc5e48705432481db5d61f23cb8c90b4e0c0cbf1fa4`. Run `44921694810` (the GHCR run on `10b3703b`) has no G-code artifact. No Forge or Bounce artifact exists. `slice_all.sh` only emits ApexCore and ApexLight.

Real orthocad, candidate = BASE:

| Gate | Result | Measured |
|---|---|---|
| G1 | FAIL | 15,300 unbonded of 15,764 thin columns; median gap +0.409522 mm (Cura −0.45) |
| G2 | FAIL | 7,941 of 8,117 thick columns unbonded; belt air 93.70%; dorsal air 87.54% (Cura 46 / 39) |
| G3 | PASS | p99 0 mm, max 0 mm |
| G4 | PASS | 165 closed loops, 0 off the plantar face |
| G5 | PASS | Core post-forefoot and ApexLight identical; ApexForge and ApexBounce unchecked |
| G6 | FAIL | max 125.341 mm³/s; 1,691 moves over 17.1; 83 over 35 |
| G7 | PASS | 5,326.98 s, delta 0% |
| G8 | PASS | 0 wall crossings |
| G9 | PASS | filament increase 0 mm, nothing outside the outline |

Insole G1 no-op: FAIL, 178 of 178 thin columns unbonded, median gap +0.425165 mm.

Deliberate bad edits of the real orthocad file:

| Edit | Result | Measured |
|---|---|---|
| Outer wall 0.1 mm inward | G3 FAIL | p99 0.086917 mm, max 0.098308 mm |
| Seam moved to the top | G4 FAIL | 37 closed starts off the plantar face; 202 closed starts left the BASE station; 39 layer changes left the plantar face |

Full rows, including the synthetic matrix, are in `proof/phase_a.json`.

## Surface lock

These gates do not move an outer edge, a seam, a layer change, a fan, a temperature, a retraction, an acceleration, or a travel. They do not change any slicer source. The printed exterior stays the comparison target.
