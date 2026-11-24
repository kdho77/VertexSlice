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

## Worker

No draft PR from `cursor/forge-bounce-hollow-band-1124` (or an equivalent ApexForge/ApexBounce generic-path hollow fix) was open when this log was written. No worker SHA has been graded. The gates were not edited to wait for it.

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
