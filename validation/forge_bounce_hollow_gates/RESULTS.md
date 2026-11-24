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
