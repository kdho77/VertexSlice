# Phase B grade — frozen G1–G9 on PR #43

**Verdict: FAIL.** G1–G9 do not all pass. This grade does not authorize a merge, a deploy, a repin, or a test-print send.

Grader gates stay the Phase A freeze, commit `8844a5a839479a8be9a133b509c9a264ec43a761`. `cd validation/forefoot_cura_gates && sha256sum -c SHA256SUMS` still matches. Those files were not edited. The worker tree was not edited.

Worker: draft PR #43, head `c4fda204c251b11c8e4fa12d307c2e4aa57f5e35`, branch `cursor/copy-cura-forefoot-bonding-into-apexcore-forefoot-inside-only-from-main-4fc8`. Artifacts from Actions run `44962830482` (pull_request merge SHA `ec70c8d433b5f80f7a0a3250d870be4144cc2b93`). The still-running image job was not waited on.

## Command

```bash
python3 validation/forefoot_cura_gates/run_gates.py \
  --core orthocad:/tmp/fcg_art/base_ortho/BASE_ApexCore_orthocad_sample_top_insole.gcode:/tmp/fcg_cand/cand-apex-core-gcode-ec70c8d433b5f80f7a0a3250d870be4144cc2b93/cand_apex_core_orthocad.gcode \
  --core insole:/tmp/fcg_art/base_insole/BASE_ApexCore_insole_sample.gcode:/tmp/fcg_cand/cand-apex-core-gcode-ec70c8d433b5f80f7a0a3250d870be4144cc2b93/cand_apex_core_insole2.gcode \
  --pair ApexLight:/tmp/fcg_art/base_light_ortho/BASE_ApexLight_orthocad_sample_top_insole.gcode:/tmp/fcg_cand/seam-fuse-gates-ec70c8d433b5f80f7a0a3250d870be4144cc2b93/cand_apex_light_orthocad.gcode \
  --pair ApexLight:/tmp/fcg_art/base_light_insole/BASE_ApexLight_insole_sample.gcode:/tmp/fcg_cand/seam-fuse-gates-ec70c8d433b5f80f7a0a3250d870be4144cc2b93/cand_apex_light_insole2.gcode \
  --json /tmp/fcg_phase_b.json
```

Exit code 1. `phase_b.json` in this folder is that output, copied unchanged.

## Inputs

| Role | File | Bytes | SHA-256 |
|---|---|---|---|
| BASE orthocad | Phase A, Actions run `44943125841` | 3,066,080 | `b58621bce3669580bb9b2e1ef67c7251e8155396c87cbe4098b477b05768b66a` |
| BASE insole | same run | 738,797 | `c78aecc9c792cd860694bf4dfaee2b495e5e480d607b61c9087d0991d71c5c81` |
| BASE ApexLight orthocad | same run | 3,060,975 | `e247af93dc87ed394a54973367bc87d7bd7b833fcbc7accbe35a7b200b408364` |
| BASE ApexLight insole | same run | 899,122 | `093881568a8d7e60af291dc10a53e53452bf74085b18b67e8e5061f924ec0292` |
| CAND orthocad | `cand_apex_core_orthocad.gcode` | 3,077,829 | `1507fadc97014760c66e688785cd9675bcdef706175c7d05de25f1b9d1a276ae` |
| CAND insole | `cand_apex_core_insole2.gcode` | 740,389 | `70ff67875d03579386f4b5f93a274059143c18475421fc6fcca83df6d73bf393` |
| CAND ApexLight orthocad | seam-fuse artifact | 3,060,975 | `14f94286d872a843fee50f53fca45deccf52400a606c9388e45a9f8c04c8050b` |
| CAND ApexLight insole | seam-fuse artifact | 899,122 | `0761674be2e0edf9faa65f736cf4807fdc3b2543000e0486e43b8e96a395d866` |

The two artifact copies of each ApexCore candidate are byte-identical. `live_apex_core_orthocad.gcode` in the seam-fuse artifact is 3,405,150 bytes. It is not the Phase A BASE and was not used. ApexForge and ApexBounce G-code were not in the artifacts. None were invented. `--strict-materials` was not passed. G5 lists those two as unchecked.

## orthocad_sample_top (layers 1–165)

| Gate | Frozen line | Result | Measured vs threshold |
|---|---|---|---|
| G1 | Every thin column (section 2.0–3.0 mm) has skin overlap ≥ 0.05 mm. Zero unbonded. Median gap is reported against Cura −0.45 mm and is not the pass line. | **FAIL** | 13,640 unbonded of 15,807 (2,167 bonded). Median gap **+0.409187 mm** (BASE +0.409522). Need overlap ≥ 0.05 mm, so the gap must be ≤ −0.05 mm. |
| G2 | Every 3–6 mm column has inner material touching both skins, overlap ≥ 0.15 mm, through the stack. Pass line is 0% air, not Cura 46% / 39%. | **FAIL** | 6,016 of 8,144 unbonded. Belt air **21.89%**, dorsal air **21.13%**. Threshold 0 unbonded and 0% air. |
| G3 | Printed outer edge (centre ± half E-width) p99 ≤ 0.02 mm, nothing over 0.05 mm. Both directions. Bead height stays 0.46 mm. | **FAIL** | p99 **0.046595 mm** (limit 0.02). Max **1.679040 mm** (limit 0.05). 97,348 samples, 0 missing. Height 0.46 mm both files. |
| G4 | Loop starts on the plantar face (within 0.15 mm of that loop's bottom). Belt-edge station X within 0.05 mm of BASE. Centreline Y may move inward by up to 0.50 mm. Layer-change end within 0.55 mm of the plantar edge and inside that same window. | **FAIL** | 0 closed starts off the plantar face. **5** closed-loop starts left the BASE station (layers 1, 2, 3, 4, 6). Closed loops 160 vs BASE 165. Layer changes off the plantar face: 0. |
| G5 | Layers after the forefoot boundary byte-identical. Process M-codes identical. Supplied ApexLight / Forge / Bounce identical after the timestamp strip. Unchecked is not a pass on that material. | **PASS** on the files supplied | Post-forefoot equal (2,611,915 chars). Process M-codes equal. Both ApexLight files equal after the stamp strip. **ApexForge and ApexBounce unchecked.** |
| G6 | Every forefoot extrusion of length ≥ 0.05 mm has Q ≤ 17.1 mm³/s, and none over 35 mm³/s. | **FAIL** | Max **125.341 mm³/s** (inner wall, layer 123, line 7649, 212 mm/s). 1,808 moves over 17.1. 83 moves over 35. Median 15.817. |
| G7 | Toolpath seconds ≤ BASE + 2%. Footer is reported and is not the pass line. | **PASS** | 5,351.238 s vs BASE 5,326.981 s, **+0.455%** (limit +2%). Footer stays `1h 27m 15s` on both. |
| G8 | perimeter, surface, and seam-block crossings are 0. Skin-interior travels do not exceed BASE. | **PASS** | 0 / 0 / 0, same as BASE. Skin-interior travels 111, same as BASE. |
| G9 | Forefoot filament increase is reported and is not itself a fail. No bead more than 0.02 mm outside the BASE printed outer edge. | **FAIL** | Increase **+936.499 mm** (+15.36%; 6,098.207 → 7,034.706). **12** samples outside, all on layer 1, distances 0.026–0.177 mm, Overhang wall and Outer wall. |

The same frozen edge sampler that feeds G3 counts 1,541 orthocad samples over 0.02 mm and 920 over 0.05 mm. The 1.679 mm sample is a candidate edge on layer 135 at about (96.32, 1.18). Samples over 0.2 mm sit on many forefoot layers, including layer 1 and layers 119–164. This is the printed outer edge, not the centre line.

## insole_sample (layers 1–31)

| Gate | Result | Measured vs threshold |
|---|---|---|
| G1 | **FAIL** | 178 of 178 unbonded. Median gap **+0.425165 mm**, identical to BASE. Need ≤ −0.05 mm. |
| G2 | **FAIL** | 534 of 534 unbonded. Belt air **34.46%**, dorsal air **34.46%**. Need 0. |
| G3 | **PASS** | p99 **0.000407 mm** (limit 0.02). Max **0.008334 mm** (limit 0.05). 24,284 samples. Height 0.46 mm. |
| G4 | **PASS** | 30 closed loops, same as BASE. 0 off the plantar face. 0 stations moved. 0 bad layer changes. |
| G5 | **PASS** on the files supplied | Post-forefoot equal (509,481 chars). Process M-codes equal. ApexLight equal. Forge and Bounce unchecked, same as orthocad. |
| G6 | **FAIL** | Max **24.397 mm³/s** (inner wall, layer 6, line 293, 42 mm/s). 135 moves over 17.1. 0 over 35. |
| G7 | **PASS** | 2,044.349 s vs BASE 2,044.313 s, **+0.0017%** (limit +2%). Footer `34m 4s` both. |
| G8 | **PASS** | 0 / 0 / 0 crossings. Skin-interior travels 1, same as BASE. |
| G9 | **PASS** | Increase **+443.978 mm** (+13.70%; 3,239.672 → 3,683.650). 0 samples outside the 0.02 mm outline. |

## What this says about the existing CI job

The seam-fuse job on run `44962830482` is a different set of gates, mostly against PRE-29. Measured here against Phase A BASE:

- Its G4 (centre-line path vs PRE-29, orthocad p99 0.3610 mm) is not this G3. This G3, the printed outer edge vs BASE, also **fails** on orthocad (p99 0.0466 mm, max 1.679 mm) and **passes** on insole. The outer-edge gate does not replace that failure with a pass.
- Its G1 ("OE: 6 open-chain ends off the bottom > PRE-29 2") is not this G4. This G4 finds **0** orthocad closed starts off the plantar face and **fails** because 5 stations left BASE. Insole G4 **passes**.
- Its seam-joint G3 failed both STLs. This outer-edge G3 fails orthocad and passes insole.
- Its crossings and ApexLight-unchanged passes agree with this G8 (0 crossings) and this G5 (both ApexLight files match BASE after the stamp strip).
- Its G7 failed vs PRE-29 (+3.16% on orthocad). This G7 vs BASE **passes**: orthocad +0.455%, insole +0.002%.
- `summary.txt` lists `G8_repo_guard rc=1`. `G8_repo_guard.txt` ends with `G8 REPO GUARD: PASS`. Noted only. That script was not edited and is not one of these nine gates.

Against the Phase A no-op on the same BASE, orthocad G1 bonded columns rose from 464 to 2,167 and the median gap stayed +0.409 mm. Orthocad G2 air fell from 93.70% / 87.54% to 21.89% / 21.13%, and 6,016 columns are still unbonded through the stack. G6's peak is the same 125.341 mm³/s inner-wall move; moves over 17.1 rose from 1,691 to 1,808.
