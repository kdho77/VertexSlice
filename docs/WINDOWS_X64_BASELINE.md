# Windows x64 baseline (Vertex foundation)

Reproducible Windows x64 build of this belt-printer fork, plus an IdeaFormer
IR3 V2 profile baseline. Vertex branding, UI redesign, and custom
material/nozzle presets are out of scope.

## Starting point

| Item | Value |
|---|---|
| Requested repo | `kdho77/ShidaoSlicer` |
| GitHub canonical name | [`kdho77/VertexSlice`](https://github.com/kdho77/VertexSlice) (same repository; rename) |
| Immediate parent | [`tommasobbianchi/ShidaoSlicer`](https://github.com/tommasobbianchi/ShidaoSlicer) |
| Original slicer | [`OrcaSlicer/OrcaSlicer`](https://github.com/OrcaSlicer/OrcaSlicer) (formerly SoftFever/OrcaSlicer) |
| Baseline branch | `main` — this is the intended belt-printer implementation |
| Starting commit | `b66d4e29ca604d1438fa6e9e31d77288cfa4eb76` |
| Other belt branch | `feat/belt-floor-native-support` is **8 commits ahead** of `main` (experimental native belt-floor / support / temp-tower) and **1 commit behind**. It is **not** this baseline. |

`main` already contains the belt transform core (`src/libslic3r/BeltTransform.*`,
`GCodeWriter`, `PrintObject`), the IdeaFormer IR3 V2 system profile, start/end
G-code, and `validation/belt_gcode_gate.py`. Do not assume the later
support-floor branch is required for an IR3 V2 first print.

License remains **AGPL-3.0-or-later** (`LICENSE` / `LICENSE.txt`). This baseline
does not change attribution.

This Linux agent host **cannot** compile VS2022/NSIS packages. Windows artifacts
come from GitHub Actions `windows-2022` only. A Linux AppImage is **not** a
substitute for the Windows deliverable.

## IR3 V2 profile — documented vs verified

A dedicated system profile exists under `resources/profiles/IdeaFormer/`. It was
**not** copied from another vendor tree for this task.

| Setting | Source | Value |
|---|---|---|
| Vendor / model | `resources/profiles/IdeaFormer.json` | IdeaFormer / IdeaFormer IR3 V2 |
| Machine | `IdeaFormer/machine/IdeaFormer IR3 V2 0.4 nozzle.json` | 0.4 mm nozzle |
| Common machine | `IdeaFormer/machine/fdm_ideaformer_common.json` | inherited by the 0.4 nozzle preset |
| `printer_structure` | common | `belt` |
| `printer_is_belt` | common | `1` |
| `belt_angle` | common | `45` |
| `belt_axis` | common | `y` |
| `belt_inclined_gcode` | common | `1` |
| `gcode_flavor` | common | `klipper` |
| Printable area | 0.4 nozzle | `250 x 2000` mm (`0x0` … `250x2000`) |
| `printable_height` | 0.4 nozzle | `250` mm |
| Process | `0.20mm Standard @IdeaFormer IR3 V2` | layer height 0.20 mm |
| Filament (system) | Generic / eSun PLA @IdeaFormer IR3 V2 | bed 75 °C, nozzle 215/220 °C |

The C++ keys `printer_is_belt`, `belt_angle`, and `belt_inclined_gcode` are the
ones `PrintConfig`, `GCodeWriter`, and `PrintObject` actually read. Flattening
the 0.4 nozzle preset (`validation/orca_profile_flatten.py`) resolves those
fields plus start/end G-code. That is **static** evidence.

Checked-in `inverted_L.3mf` embeds the same machine (`printer_model=IdeaFormer IR3 V2`,
`belt_angle=45`, `gcode_flavor=klipper`, `printer_is_belt=1`). Its filament id is
`Generic PETG @System` (a project override), not the system PLA preset.

`tests/fixtures/belt_cli_machine.json` is a **simplified CLI fixture**, not the
system profile. It uses different start G-code (`BED_MESH_CALIBRATE`, no
`FMS_on`) and `printable_area` 250×354. Do not treat it as the IR3 V2 product
preset.

Profile inconsistencies (recorded, not invented or “fixed” here):

- `default_filament_profile` on the common machine is `Generic PLA @IdeaFormer`,
  but the system filament is named `Generic PLA @IdeaFormer IR3 V2`.
- Machine-model `default_materials` lists `Generic PETG @IdeaFormer`, which has
  **no** matching system filament file.

### Axis conventions (slicer + checked-in Klipper config)

From the machine start G-code comment and `validation/printer_config/printer.cfg`:

- **X** — belt width / lateral. Firmware `position_max: 250`.
- **Y** — gantry (probe / height away from belt). Firmware `position_min: -5`,
  `position_max: 354`.
- **Z** — belt travel. Firmware `position_max: 99999` (effectively infinite).
- Kinematics: **CoreXY**. No firmware-side belt transform is present
  (`[printer] kinematics: corexy`). The slicer is expected to emit machine
  coordinates.

README claims the IR3 V2 is “routinely used” and hardware-validated on this math
model. That is a **documented claim**. This session did **not** reprint it.

### Required printer-side macros / firmware assumptions

The **system** start/end G-code calls these names. They exist in the checked-in
Klipper config; they are **not** invented here:

| Token | Where | Role |
|---|---|---|
| `G28` / `G28 Y` / `G28 X` | start + end | Home all; re-probe Y (belt surface); home X at end |
| `FMS_on` / `FMS_off` | start / end | Enable/disable `filament_motion_sensor encoder_sensor` |
| `BED_MESH_CLEAR` | end | Standard Klipper; `[bed_mesh]` is present in `printer.cfg` |
| `M140` / `M104` / `M190` / `M109` | start | Bed/hotend from the **filament** profile |
| Purge + `G92 Z0` | start | Blob, belt advance 20 mm, Y re-probe, 250 mm prime lines, then belt origin = 0 |

`host_type` in the system profile is `octoprint` with
`print_host=http://<PRINTER_HOST>`. That is a placeholder for upload, not a
Moonraker URL. Do not treat Device-tab upload as configured until a real host
is entered.

`use_relative_e_distances` is `1` while start G-code emits `M82`. The Windows
CLI G-code from this commit **does** switch to `M83` immediately after the
start block (verified in `plate_1.gcode`).

## What this environment could and could not check

| Check | Status |
|---|---|
| Profile files exist and match C++ belt keys | **Done** (static; flatten audit) |
| Firmware cfg axis limits and `FMS_*` macros | **Done** (static, from checked-in `printer.cfg`) |
| Windows x64 compile / NSIS / portable package | **Done** — Actions run 36774518246 |
| Application launch (GUI / wizard) | **Not performed** (`orca-slicer.exe --help` and CLI slice only) |
| Slice + export from this commit | **Done** (headless CLI on the Windows runner) |
| Belt-aware preview | **Not performed** |
| Physical print / firmware flash / host connect | **Not performed** (out of scope) |

Historical G-code already in the tree was run through
`validation/belt_gcode_gate.py`. Those files are **old developmental samples**,
not output from this commit:

| File | Gate |
|---|---|
| `validation/orca_gcode/cube_aligned_final.gcode` | **FAIL** (R4, R7, R11) |
| `test_cube_belt.gcode` | **FAIL** (R1, R2, R7, R9, R11) |

Do **not** send those samples to the printer. Do **not** treat a gate PASS on
IdeaMaker reference files as a ShidaoSlicer verification.

## Reproducible Windows x64 build

### A. GitHub Actions (preferred)

Workflow: `.github/workflows/windows-x64-baseline.yml`

- Trigger: **Actions → Windows x64 baseline → Run workflow**, or a push that
  changes that workflow / `scripts/windows_ir3_cli_verify.py`.
- Runner: **`windows-2022`** (GitHub-hosted, no paid/larger runners).
  `windows-latest` currently exposes VS 2026; CMake 3.28 cannot generate for it.
- Toolchain: documented `build_release_vs2022.bat` + MSBuild 17.x. The workflow
  fails if cmake/build/install does not produce `orca-slicer.exe` or if `cpack`
  does not produce a `ShidaoSlicer*.exe`. The `.bat` itself is unchanged so
  this PR does not trip `build_all.yml`’s path filter.
- Platforms: Windows x64 only. No Linux/macOS/Flatpak matrix. No cron.
- Dependencies are cached on the git tree SHAs of `deps/` and `deps_src/`.
  `hashFiles('deps/**')` is **not** used: runs 36761113701 and 36755155238
  failed after the deps compile because that expression exceeds GitHub’s 120s
  `hashFiles` limit. First run still builds deps from scratch (hours). Later
  runs with unchanged `deps/` + `deps_src/` should hit cache.
- After packaging, CI flattens the **system** IR3 V2 presets, slices
  `validation/test_models/box_10x10x10.stl`, and runs the belt gate. That step
  is `continue-on-error` so a known CLI `unprintable_height` failure cannot
  hide a successful installer. Read `ir3_verify/REPORT.txt` before claiming a
  slice PASS.
- Packaging:
  - Installer: `cpack -G NSIS` → `ShidaoSlicer_Windows_Installer_V<ver>.exe`
  - Portable: `ShidaoSlicer_Windows_V<ver>_portable.zip`
- Artifacts: installer, portable zip, and
  `ShidaoSlicer_Windows_baseline_checksums` (`SHA256SUMS.txt` + `BASELINE.txt`
  + `ir3_verify/`).

### B. Local Windows x64 (VS2022)

Same toolchain as upstream OrcaSlicer:

```bat
REM From a VS2022 x64 Native Tools prompt, repository root
build_release_vs2022.bat deps
build_release_vs2022.bat slicer
cd build
cpack -G NSIS
```

Outputs:

- Installer: `build/ShidaoSlicer_Windows_Installer_V2.3.2-dev.exe`
- Portable tree: `build/OrcaSlicer\`
- Binary name stays `orca-slicer.exe` (compat with Moonraker thumbnails / macros).
  Window title / About identify as ShidaoSlicer / OrcaBelt (`version.inc`).

SHA-256:

```bat
certutil -hashfile build\ShidaoSlicer_Windows_Installer_V2.3.2-dev.exe SHA256
```

## Manual test checklist (after the Windows package exists)

Do these on a Windows x64 machine. Stop before any USB/network print.

1. **Launch** — install the NSIS package *or* unzip the portable tree; start
   `orca-slicer.exe`. Confirm the window/About string and that the process stays
   up past the setup wizard.
2. **Select IR3 V2** — printer = **IdeaFormer IR3 V2**, variant **0.4 nozzle**,
   process **0.20mm Standard @IdeaFormer IR3 V2**, filament **Generic PLA @IdeaFormer IR3 V2**
   (or eSun PLA White). Confirm Printer settings show belt / 45° / Klipper.
   If the default filament name does not resolve, pick the IR3 V2 PLA preset
   explicitly (see profile inconsistency above).
3. **Import** — `inverted_L.3mf` (machine already baked) or
   `validation/test_models/box_10x10x10.stl`. Place the keel / first contact
   toward low Y. Do not rotate to “flat on a Cartesian bed.”
4. **Slice** — slice plate 0. If the GUI reports unprintable height, that is a
   known belt vs `printable_height` tension; the GUI path is supposed to allow
   the print button (README). Record the exact message if it still blocks.
5. **Preview** — layers should read as 45° inclined toolpaths, not a collapsed
   horizontal stack. First-layer Y should sit near the belt (gate R7: Y ≤ 2 mm).
6. **Export G-code** — save `ir3v2_baseline.gcode`. Confirm the file starts with
   the IdeaFormer IR3 V2 start block (`G28`, purge, `FMS_on`, `G92 Z0`) and
   ends with `FMS_off` / `BED_MESH_CLEAR` / `M84`.
7. **Gate (required before any physical print)**

   ```bash
   python3 validation/belt_gcode_gate.py ir3v2_baseline.gcode
   ```

   R7 and R11 must **PASS**. Any FAIL → do not print.

## Next physical test (human, on the printer)

Only after the Windows app has sliced a **new** G-code from this commit and the
gate PASSES:

1. Upload that G-code to the IR3 V2 Klipper host (Moonraker). The host-side
   copy of `belt_gcode_gate.py` should be allowed to run if it is still
   installed.
2. First physical test: **first-layer / keel adhesion only** (a 10 mm cube or
   `inverted_L`), not a long belt job. Watch Y at start (must not slam the
   belt) and that the nozzle does not dive through the belt (R11).
3. Do not flash firmware and do not change printer macros for this baseline.

## Artifact record

From Actions run
[36774518246](https://github.com/kdho77/VertexSlice/actions/runs/36774518246)
at commit `2466fd38633a239b89f9152cd7f2d2b740fded09`. Artifacts expire after
14 days. Login to GitHub is required to download.

| Field | Value |
|---|---|
| Run | https://github.com/kdho77/VertexSlice/actions/runs/36774518246 |
| Installer | [ShidaoSlicer_Windows_V2.3.2-dev](https://github.com/kdho77/VertexSlice/actions/runs/36774518246/artifacts/11129399931) (`ShidaoSlicer_Windows_Installer_V2.3.2-dev.exe`) |
| Portable | [ShidaoSlicer_Windows_V2.3.2-dev_portable](https://github.com/kdho77/VertexSlice/actions/runs/36774518246/artifacts/11129389815) (`ShidaoSlicer_Windows_V2.3.2-dev_portable.zip`) |
| Checksums + IR3 verify | [ShidaoSlicer_Windows_baseline_checksums](https://github.com/kdho77/VertexSlice/actions/runs/36774518246/artifacts/11130195839) |
| Source commit | `2466fd38633a239b89f9152cd7f2d2b740fded09` |
| SHA-256 installer | `cf4a31775215940394fabe41a5f7aacf19e6754921579309dad3d515153b1118` |
| SHA-256 portable | `84bb11de55edac21eb5c6949a23248d9033a703b66f8082b2f527c680ec775db` |

The parent nightly at
https://github.com/tommasobbianchi/ShidaoSlicer/releases/tag/nightly
is **not** this baseline.

### CLI slice + gate (this commit’s Windows binary)

`scripts/windows_ir3_cli_verify.py` sliced `validation/test_models/box_10x10x10.stl`
with the flattened system IR3 V2 machine / 0.20 mm process / Generic PLA
presets. Header: belt=1, gantry 45°, layer step 0.283 mm, 70 layers.
Start/end blocks are the IdeaFormer IR3 V2 macros (`FMS_on` / `FMS_off`,
`BED_MESH_CLEAR`, bed 75 °C, nozzle 220/215 °C). First extrusion Y is
0.30–0.70 mm (would satisfy R7). `M83` follows the start `M82`.

CI’s gate step **crashed on Windows cp1252** (em-dash in the start-G-code
comment). The gate now reads UTF-8 and tracks M82/M83, G92, G28, and the
first model-extrusion layer. R11 scores **machine gantry Y**, not
`Z − Y/√2` (that formula does not match this fork’s emit path). Re-run on
the same G-code: **PASS** (exit 0). That is not a physical-print approval
and does not apply to 1.0 mm TPU. Details:
[`docs/IR3_V2_PRE_PRINT_GATES.md`](IR3_V2_PRE_PRINT_GATES.md).
