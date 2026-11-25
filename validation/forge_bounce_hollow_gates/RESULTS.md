# Grade log

Gates frozen before any candidate grade. Hashed files are in `SHA256SUMS`. This file is the grade log and is not part of the hash.

## No-op (live/main `d935197976b7cdd2fa89ced4ff43930b0212959f`)

Overall FAIL, as required. ApexForge and ApexBounce fail hollow, G1, and G2. ApexCore identity and surface pass against the same file.

| Gate | ApexForge | ApexBounce | ApexCore |
|---|---|---|---|
| Hollow L<=165 / equivalent | FAIL 177 / 361 (L<=181) | FAIL 177 / 361 | reported 131 / 131 (not a pass line) |
| G1 | FAIL 1474/5833 | FAIL 1474/5833 | reported 3/15493 |
| G2 | FAIL 2776/2968, belt 65.4%, dorsal 80.9% | FAIL 2776/2968, belt 65.4%, dorsal 80.9% | PASS 0/8406 |
| Surface | PASS | PASS | PASS |
| Filament | 24392.72 mm, 70.41 g, increase 0 | 24392.72 mm, 70.41 g, increase 0 | 29857.71 mm, 86.18 g, increase 0 |
| Slice time | PASS 7.218 s | PASS 7.166 s | PASS 9.429 s |
| Identity | — | — | PASS |
| Shared path | PASS (both still hollow) | same | — |

Bad-change proof rejected a layer change at Y=25, an outer wall moved 0.1 mm, one ApexCore byte flipped, a bead outside the outline, slice time 261 s, and a Forge-only fill.

## Worker `baa2c0877b2ac554ef3cf0d872930166511e95ee`

Overall **FAIL**. Draft [PR #51](https://github.com/kdho77/VertexSlice/pull/51). The same commit is `cursor/forge-bounce-hollow-band-1124`. Graded with the frozen gates against live slices from Actions run 45295959179 (main `d935197`). Candidate slices are `server.slice_stl` on that same orca-slicer binary, using this SHA's `server/slice_engine.py`. Fixture `validation/test_models/orthocad_sample_top_insole.stl`, `apex-v2-insole-95a`, wall_loops 1, infill 26, toe_first, belt_insole_place. Hashed gate files were not edited.

ApexForge and ApexBounce produced the same gate numbers. ApexCore is the live residual and is byte-identical.

| Gate | ApexForge | ApexBounce | ApexCore |
|---|---|---|---|
| Hollow L<=165 / L<=181 | PASS 0 / 0 | PASS 0 / 0 | reported 131 / 131 (not a pass line) |
| G1 | FAIL 1474/5833 | FAIL 1474/5833 | reported 3/15493 (not a pass line) |
| G2 | FAIL 2440/2968, belt 54.0%, dorsal 71.7% | FAIL, same | PASS 0/8406 |
| Surface | PASS | PASS | PASS |
| Filament | FAIL 24392.72 mm / 70.41 g -> 25401.40 mm / 73.32 g | FAIL, same | PASS 29857.71 mm / 86.18 g, increase 0 |
| Slice time | PASS 12.348 s | PASS 12.153 s | PASS 9.263 s |
| Identity | — | — | PASS |
| Shared path | PASS | PASS | — |

Hollow remaining after layer 165 is 4297 on both materials, max air 15.918 mm. That air is outside the required window.

G1 is the live count, unchanged. First residuals: layer 125 x-bin 265, layer 150 x-bin 214. G2 dropped from the live 2776 unbonded to 2440. First residuals: layer 165 x-bin 216, layer 151 x-bin 281, layer 141 x-bin 273.

Filament increased 1008.68 mm / 2.91 g. Added extrusions are sparse infill (5384, none of another type, none more than 0.02 mm outside the live outline). 5097 of them are outside a previously hollow section of at least 3 mm. First samples: layer 129 sparse infill at (128.5, 0.747), (129.0, 0.749), (129.5, 0.750).

Surface lock holds: 36125 locked segments, delta 0, no closed-loop start moved, no layer change moved. ApexCore is 3349500 bytes after the stamp line is ignored. The diff names both materials in one `apex_forge` / `apex_bounce` block, and both hollow pass lines match.

## CI on the gates head

Tested commit `e359a446d8e382c8d9472bf12e63090cc303daf2`. Actions run [45508570872](https://github.com/kdho77/VertexSlice/actions/runs/45508570872).

| Job | Result |
|---|---|
| Build image and run slice proof | PASS |
| Validation scripts (no binary) | PASS |
| Seam-fuse gates G1 G3-G8 | FAIL |

The seam-fuse job already fails on main `d935197976b7cdd2fa89ced4ff43930b0212959f` (run [45295959179](https://github.com/kdho77/VertexSlice/actions/runs/45295959179), job Seam-fuse gates G1 G3-G8). This branch does not change `src/`, `server/`, `scripts/g2b_heelmod/`, or `.github/workflows/docker-server-proof.yml`. The candidate image in that job is the published live image plus this branch's `scripts/g2b_heelmod`, which is identical to main.

Same slice failures as main: G3 seam joint (orthocad J2 6 and J3 165 of 436; insole2 J3 1 of 92), G4 outer match vs pre-29 (orthocad perimeter p99 0.1682 mm and 15798.53 mm moved; insole2 175.52 mm moved), G7 orthocad print budget (91.29 min vs pre-29 86.46 min, filament 28686.5 mm vs 27008.2 mm). G1, G5, G6, and insole2 G7 pass. G8 compares `6d01dff5b6a174ec37468671579ee7d3bbafb230` to the head, not main to the head. On main it already rejects `server/` files from #48 and one workflow line main replaced (`python -m unittest ...` gained `tests.test_slice_jobs` and `tests.test_job_store`). On this head it also rejects the new `validation/forge_bounce_hollow_gates/` files because they are outside that frozen allow-list. `validation/seam_fuse_gates` and `validation/ref` are identical to that base.

Not retried. The hashed gate files were not edited. Widening the frozen seam-fuse allow-list would change a gate this PR is not allowed to touch.

## Worker `3225c7e6dd403f778cd4699d1a678048b81aaf2f`

Overall **FAIL**. Draft [PR #51](https://github.com/kdho77/VertexSlice/pull/51) head after `baa2c087`. Graded with the frozen gates. Hashed gate files were not edited. `sha256sum -c SHA256SUMS` passed before this log line was added.

This commit does not change the fill. Against `baa2c087` it edits only `tests/test_forefoot_hollow_band.py` and `.github/workflows/docker-server-proof.yml`. `server/forefoot_hollow_band.py` and `server/slice_engine.py` are the same bytes as `baa2c087`. Candidate slices are `server.slice_stl` on the orca-slicer binary from Actions run 45295959179, using this SHA's `server/`. Fixture `validation/test_models/orthocad_sample_top_insole.stl`, `apex-v2-insole-95a`, wall_loops 1, infill 26, toe_first, belt_insole_place. Live slices are the same binary and the live `server/` at `d935197`.

ApexForge and ApexBounce produced the same gate numbers. ApexCore is the live residual and is byte-identical.

| Gate | ApexForge | ApexBounce | ApexCore |
|---|---|---|---|
| Hollow L<=165 / L<=181 | PASS 0 / 0 | PASS 0 / 0 | reported 131 / 131 (not a pass line) |
| G1 | FAIL 1474/5833 | FAIL 1474/5833 | reported 3/15493 (not a pass line) |
| G2 | FAIL 2440/2968, belt 54.0%, dorsal 71.7% | FAIL, same | PASS 0/8406 |
| Surface | PASS | PASS | PASS |
| Filament | FAIL 24392.72 mm / 70.41 g -> 25401.40 mm / 73.32 g | FAIL, same | PASS 29857.71 mm / 86.18 g, increase 0 |
| Slice time | PASS 4.387 s | PASS 4.17 s | PASS 8.681 s |
| Identity | — | — | PASS |
| Shared path | PASS | PASS | — |

Hollow remaining after layer 165 is 4297 on both materials, max air 15.918 mm. That air is outside the required window.

G1 is the live count, unchanged, and equal to `baa2c087` (1474/5833). It is not worse than live and it is not a regression against `baa2c087`. The frozen pass line is still 0, so the gate is FAIL. First residuals: layer 125 x-bin 265, layer 150 x-bin 214. G2 is 2440/2968. That is below the live 2776/2968 and equal to `baa2c087`, so it is not worse than live and not a regression against `baa2c087`. The frozen pass line is still 0. Belt air 54.009434%, dorsal air 71.731806%. First residuals: layer 165 x-bin 216, layer 151 x-bin 281, layer 141 x-bin 273.

Filament increased 1008.68 mm / 2.91 g. Added extrusions are sparse infill (5384, none of another type, none more than 0.02 mm outside the live outline). 5097 of them are outside a previously hollow section of at least 3 mm. First samples: layer 129 sparse infill at (128.5, 0.747), (129.0, 0.749), (129.5, 0.750). The filament rule is still FAIL. This head did not confine the added fill.

Surface lock holds: 36125 locked segments, delta 0, no closed-loop start moved, no layer change moved. ApexCore surface is 52857 locked segments, delta 0. ApexCore is 3349500 bytes after the stamp line is ignored (one stamp line on each file). The diff against `d935197` names both materials in one `apex_forge` / `apex_bounce` block, and both hollow pass lines match.

Slice wall-clock on this machine is under the 260 s cap. ApexBounce on this slice is still M109 S225 and M190 S60. ApexForge is M109 S245 with no bed wait. This grade did not retune temperature or speed.

## Worker `2ea145ef384c4182499eb8fa2afd1d7895164bd5`

Overall **FAIL**. Draft [PR #51](https://github.com/kdho77/VertexSlice/pull/51) head after `3225c7e6`. This is the fill-confinement commit. It changes `server/forefoot_hollow_band.py` and `tests/test_forefoot_hollow_band.py`. `server/slice_engine.py` is unchanged. Graded with the frozen gates. `sha256sum -c SHA256SUMS` still matches the freeze. Hashed gate files were not edited.

Candidate slices are `server.slice_stl` on the orca-slicer binary from Actions run 45295959179, using this SHA's `server/`. Fixture `validation/test_models/orthocad_sample_top_insole.stl`, `apex-v2-insole-95a`, wall_loops 1, infill 26, toe_first, belt_insole_place. Live slices are the same binary and the live `server/` at `d935197`. ApexForge and ApexBounce match. ApexCore is the live residual and is byte-identical.

Two independent runs measured the same geometry. Slice times differ by machine and both are under 260 s. The table below is this run.

| Gate | ApexForge | ApexBounce | ApexCore |
|---|---|---|---|
| Hollow L<=165 / L<=181 | PASS 0 / 0 | PASS 0 / 0 | reported 131 / 131 (not a pass line) |
| G1 | FAIL 1474/5833 | FAIL 1474/5833 | reported 3/15493 (not a pass line) |
| G2 | FAIL 2776/2968, belt 65.431%, dorsal 80.660% | FAIL, same | PASS 0/8406 |
| Surface | PASS | PASS | PASS |
| Filament | PASS 24392.72 mm / 70.41 g -> 24432.60 mm / 70.53 g | PASS, same | PASS 29857.71 mm / 86.18 g, increase 0 |
| Slice time | PASS 4.087 s | PASS 4.101 s | PASS 8.875 s |
| Identity | — | — | PASS |
| Shared path | PASS | PASS | — |

The other run recorded slice time PASS 14.054 s, 12.952 s, and 9.665 s.

Hollow remaining after layer 165 is 4297 on both materials, max air 15.918 mm. That air is outside the required window.

G1 is 1474/5833. That equals live and equals `baa2c087`. It is not worse than live and it is not a regression against `baa2c087`. The frozen pass line is still 0, so the gate is FAIL. First residuals: layer 125 x-bin 265, layer 150 x-bin 214.

G2 is 2776/2968. That equals the live unbonded count and is worse than `baa2c087` (2440/2968), so it regresses against `baa2c087`. The frozen pass line is still 0. Belt air 65.431267% matches the live re-measure. Dorsal air is 80.660377%, against the live re-measure of 80.896226% at the same unbonded count. First residuals: layer 132 x-bin 268, layer 165 x-bin 216, layer 151 x-bin 281.

Filament increased 39.88 mm / 0.12 g. Added extrusions are sparse infill (287, none of another type, none more than 0.02 mm outside the live outline). 0 of them are outside a previously hollow section of at least 3 mm. The filament rule PASSes. The 5097 out-of-section extrusions on `baa2c087` are gone.

Surface lock holds: 36125 locked segments, delta 0, no closed-loop start moved, no layer change moved. ApexCore surface is 52857 locked segments, delta 0. ApexCore is 3349500 bytes after the stamp line is ignored (one stamp line on each file). The diff against `d935197` names both materials, and both hollow pass lines match.

ApexBounce on this slice is still M109 S225 and M190 S60. This grade did not retune temperature or speed.

## Worker `a84d814f99d3eed440827aff0fee919fa9727ec7`

Overall **FAIL**, the same grade as `2ea145ef`. Draft [PR #51](https://github.com/kdho77/VertexSlice/pull/51) head. This commit adds one line to `.github/workflows/docker-server-proof.yml`: `git fetch --depth=1 origin d935197976b7cdd2fa89ced4ff43930b0212959f` before the ApexCore identity diff. It does not change the fill. `server/forefoot_hollow_band.py` blob `ad968e2c13fbfe071e725fb51da8253a4f5bd3a8` and `server/slice_engine.py` blob `a242a06185289e1d3edc2df1c6db37e07935c6db` match `2ea145ef`.

The frozen `run_gates.py` was run again on the slices from that identical server code, with the diff of this SHA against `d935197`. Hashed gate files were not edited.

| Gate | ApexForge | ApexBounce | ApexCore |
|---|---|---|---|
| Hollow L<=165 / L<=181 | PASS 0 / 0 | PASS 0 / 0 | reported 131 / 131 (not a pass line) |
| G1 | FAIL 1474/5833 | FAIL 1474/5833 | reported 3/15493 (not a pass line) |
| G2 | FAIL 2776/2968, belt 65.431%, dorsal 80.660% | FAIL, same | PASS 0/8406 |
| Surface | PASS | PASS | PASS |
| Filament | PASS 24392.72 mm / 70.41 g -> 24432.60 mm / 70.53 g | PASS, same | PASS 29857.71 mm / 86.18 g, increase 0 |
| Slice time | PASS 4.087 s | PASS 4.101 s | PASS 8.875 s |
| Identity | — | — | PASS |
| Shared path | PASS | PASS | — |

G1 equals live and equals `baa2c087`. G2 equals the live unbonded count and regresses against `baa2c087` (2440/2968). Filament stays PASS: 287 added sparse-infill extrusions, 0 outside a previously hollow >=3 mm section. Surface delta 0. ApexCore identity PASS, 3349500 bytes, stamp ignored. Shared path PASS on this SHA's diff.

A later run sliced this SHA again with `server.slice_stl` on the orca-slicer binary from Actions run 45295959179. `sha256sum -c SHA256SUMS` matched the freeze before that run. File sizes match the `2ea145ef` slices: Forge 3203086 bytes, Bounce 3203159 bytes, Core 3349500 bytes. The frozen gates returned the same counts as the table. Slice time on that run is PASS 13.579 s, 13.478 s, and 9.111 s. ApexBounce is still M109 S225 and M190 S60. ApexForge is M109 S245 with no bed wait.

## CI on `5ffabefe52`

Actions run [45517874520](https://github.com/kdho77/VertexSlice/actions/runs/45517874520), commit `5ffabefe52068b268b41387f18a656d2436d1204`. That commit changes only this grade log.

| Job | Result |
|---|---|
| Build image and run slice proof | PASS |
| Validation scripts (no binary) | PASS |
| Seam-fuse gates G1 G3-G8 | FAIL |

Same seam-fuse failures already recorded for `e359a446` and for main run [45295959179](https://github.com/kdho77/VertexSlice/actions/runs/45295959179): G3, G4, and orthocad G7, plus G8 because `validation/forge_bounce_hollow_gates/` is outside the frozen allow-list. G8 also reports one existing workflow line removed or edited, which main already reports. Not retried. The hashed gate files were not edited.
