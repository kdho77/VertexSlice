# ApexForge / ApexBounce hollow-band gates (frozen)

Independent grader gates. These files do not implement a hollow fill, do not change slicer source, and must not be merged.

Freeze is `SHA256SUMS` in this directory. `RESULTS.md` is the grade log and is outside the hash. A change to any hashed file invalidates the freeze.

Check from this directory:

```bash
sha256sum -c SHA256SUMS
python3 prove_gates.py
```

`prove_gates.py` reads live G-code from `FBH_LIVE_DIR` (default `/tmp/fbh_live`). Exit 0 means every expected PASS/FAIL held.

## What is frozen

Live slice is `server.slice_stl` on the orca-slicer binary from Actions run `45295959179` (head `d935197976b7cdd2fa89ced4ff43930b0212959f`), fixture `validation/test_models/orthocad_sample_top_insole.stl`, `profile_id=apex-v2-insole-95a`, `wall_loops=1`, `infill_density=26`, `print_direction=toe_first`, `belt_insole_place=true`.

| Material | Layers | Layer height | Slice wall-clock | Filament |
|---|---|---|---|---|
| ApexForge | 481 | 0.42 mm | 7.218 s | 24392.72 mm, 70.41 g |
| ApexBounce | 481 | 0.42 mm | 7.166 s | 24392.72 mm, 70.41 g |
| ApexCore | 439 | 0.46 mm | 9.429 s | 29857.71 mm, 86.18 g |

G1 and G2 use the frozen envelope with bead height 0.46 mm, including on the 0.42 mm files. That is the instrument that matches the live counts below. Self-vs-self, model orthocad, layers 1–165.

| Gate | Pass line | Live ApexForge and ApexBounce | Live ApexCore |
|---|---|---|---|
| Hollow | 0 at L<=165 and 0 at L<=ceil(165*0.46/layer_height) | FAIL. L<=165 = 177, L<=181 = 361, total 4658, max air 15.918 mm | Reported only. L<=165 = 131. Not a pass line. Byte identity locks the file. |
| G1 thin bond | 0 unbonded, overlap >= 0.05 mm | FAIL 1474/5833 | Reported 3/15493. Not a pass line. |
| G2 thick bond | 0 unbonded, overlap >= 0.15 mm through the stack | FAIL 2776/2968, belt air 65.4%, dorsal air 80.9% | PASS 0/8406 |
| Surface | Outer wall, overhang wall, and inner wall identical to live. Closed starts on the plantar face and at the live station (0.05 mm). Layer changes at the live station. | PASS against itself | PASS against itself |
| Filament | Footer mm and g quoted. Increase only by sparse infill or solid inside a previously hollow >= 3 mm forefoot section. Nothing more than 0.02 mm outside the live outer outline. | PASS, increase 0 | PASS, increase 0 |
| Slice time | Wall-clock <= 260 s | PASS | PASS |
| Core identity | Byte-identical to live after the OrcaSlicer stamp line | — | PASS against itself |
| Shared path | Hollow pass line is reached on both materials or on neither. A diff that names only one of ApexForge / ApexBounce fails. | PASS (both still hollow) | — |

The hollow tool is a 0.25 mm X column. A column counts when the outer-envelope section is >= 3 mm, the skin-to-skin air is > 1 mm, and no non-wall bead overlaps that air. The physical-equivalent window is layers <= 181 on 0.42 mm material (ceil(165 * 0.46 / 0.42)) and layers <= 165 on 0.46 mm material.

The PM's probe on the same live defect read hollow 147 at L<=165 and 299 at L<=181 (total 3321, max air 10.994 mm). This frozen tool reads 177 and 361. Both readings are not 0, so the no-op fails. The pass line is 0, not 147.

## Bad-change proof

`proof/bad_change.json`. Each of these is rejected:

| Change | Gate |
|---|---|
| Forefoot hollow left in place (the live file) | hollow FAIL |
| Layer change moved to Y=25 | surface FAIL |
| One outer-wall vertex moved 0.1 mm | surface FAIL |
| One ApexCore byte flipped off the stamp line | core identity FAIL |
| A bead at X0 Y80 | filament FAIL |
| Slice time 261 s | slice time FAIL |
| Hollow pass on Forge only, or a diff that names only `apex_forge` | shared path FAIL |

## Grading a candidate

Do not edit these gates first.

```bash
python3 validation/forge_bounce_hollow_gates/run_gates.py \
  --live /path/to/live \
  --cand /path/to/candidate \
  --json /tmp/grade.json
```

Both directories contain `apex_forge_orthocad.gcode`, `apex_bounce_orthocad.gcode`, `apex_core_orthocad.gcode`, and `slice_times.json` with `elapsed_s` per material. Exit 0 only when Forge and Bounce pass hollow, G1, G2, surface, filament, and slice time; ApexCore passes byte identity, surface, and slice time; and the shared path passes.

## Surface lock

These gates do not move an outer edge, a seam, a layer change, or a skin. Seams and layer changes stay at the live station on the plantar face. No slicer source is edited.
