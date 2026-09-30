# Windows x64 baseline (Vertex foundation)

This document records the starting point for a reproducible Windows x64 build of
this belt-printer fork and a verified IdeaFormer IR3 V2 profile baseline.
Vertex branding, UI redesign, and custom material/nozzle presets are out of
scope.

## Starting point

| Item | Value |
|---|---|
| Workspace remote at inspect | `https://github.com/kdho77/ShidaoSlicer` |
| GitHub canonical name | `kdho77/VertexSlice` (repo rename; same repository) |
| Immediate parent | [`tommasobbianchi/ShidaoSlicer`](https://github.com/tommasobbianchi/ShidaoSlicer) |
| Original slicer | [`OrcaSlicer/OrcaSlicer`](https://github.com/OrcaSlicer/OrcaSlicer) (formerly SoftFever/OrcaSlicer) |
| Baseline branch | `main` — this is the intended belt-printer implementation |
| Starting commit | `b66d4e29ca604d1438fa6e9e31d77288cfa4eb76` |
| Other belt branch | `feat/belt-floor-native-support` is **8 commits ahead** of `main` (experimental native belt-floor support) and **1 commit behind**. It is **not** this baseline. |

`main` already contains the belt transform core, the IdeaFormer IR3 V2 system
profile, start/end G-code, and the `validation/belt_gcode_gate.py` safety gate.
Do not assume a later support-floor branch is required for IR3 V2 first prints.

License remains **AGPL-3.0-or-later** (`LICENSE.txt`). This baseline does not
change attribution.

## IR3 V2 profile — documented vs verified

A dedicated system profile exists. It was **not** copied from another vendor
tree for this task.

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

The C++ config keys `printer_is_belt`, `belt_angle`, and `belt_inclined_gcode`
are the ones `PrintConfig`, `GCodeWriter`, and `PrintObject` actually read.

Checked-in fixture `inverted_L.3mf` already embeds this same IR3 V2 machine
(`printer_model=IdeaFormer IR3 V2`, `belt_angle=45`, `gcode_flavor=klipper`).

### Axis conventions (slicer + checked-in Klipper config)

From the machine start G-code comment and `validation/printer_config/printer.cfg`:

- **X** — belt width / lateral. Firmware `position_max: 250`.
- **Y** — gantry (probe / height away from belt). Firmware `position_min: -5`, `position_max: 354`.
- **Z** — belt travel. Firmware `position_max: 99999` (effectively infinite).
- Kinematics: **CoreXY**. No firmware-side belt transform is present in the
  checked-in `printer.cfg` (`[printer] kinematics: corexy`). The slicer is
  expected to emit machine coordinates.

The README claims the IR3 V2 is “routinely used” and hardware-validated on this
math model. That is a **documented claim**. This session did **not** reprint it.

### Required printer-side macros / firmware assumptions

The system start/end G-code calls these names. They exist in the checked-in
Klipper config; they are **not** invented here:

| Token | Where | Role |
|---|---|---|
| `G28` / `G28 Y` / `G28 X` | start + end | Home all; re-probe Y (belt surface); home X at end |
| `FMS_on` / `FMS_off` | start / end | Enable/disable `filament_motion_sensor encoder_sensor` |
| `BED_MESH_CLEAR` | end | Standard Klipper; `[bed_mesh]` is present in `printer.cfg` |
| `M140` / `M104` / `M190` / `M109` | start | Bed/hotend from the **filament** profile |
| Purge + `G92 Z0` | start | Blob, belt advance 20 mm, Y re-probe, 250 mm prime lines, then belt origin = 0 |

`host_type` in the system profile is `octoprint` with `print_host=http://<PRINTER_HOST>`.
That is a placeholder for upload, not a Moonraker URL. Do not treat Device-tab
upload as configured until a real host is entered.

`use_relative_e_distances` is `1` while start G-code emits `M82`. Whether the
slicer later switches to `M83` is **not** re-verified in this environment.

## What this environment could and could not check

| Check | Status |
|---|---|
| Profile files exist and match C++ belt keys | **Done** (static) |
| Firmware cfg axis limits and `FMS_*` macros | **Done** (static, from checked-in `printer.cfg`) |
| Windows x64 compile / NSIS / portable package | **CI only** — this host is Linux |
| Application launch | **Not performed** |
| Slice + export from this commit | **Not performed** (no `orca-slicer` binary here) |
| Belt-aware preview | **Not performed** |
| Physical print / firmware flash / host connect | **Not performed** (out of scope) |

Historical G-code already in the tree was run through
`validation/belt_gcode_gate.py`. Those files are **old developmental samples**,
not output from this commit:

| File | Gate |
|---|---|
| `validation/orca_gcode/cube_aligned_final.gcode` | **FAIL** (R4, R7, R11) |
| `validation/orca_gcode/cube_petg_final.gcode` | **FAIL** (R1, R4, R9) |
| `Cube_PLA_10x10x10.gcode` | **FAIL** (R1, R2, R7, R9, R11) — Cartesian-like Y≈1000 / negative Z |
| IdeaMaker `validation/reference_gcode/default-box_*.gcode` | Gate skips IdeaMaker (`SLICER-SKIP`) |

Do **not** send those Orca samples to the printer. Do **not** treat a gate PASS
on IdeaMaker files as an Orca/ShidaoSlicer verification.

## Reproducible Windows x64 build

This host cannot run Visual Studio 2022. Use the GitHub Actions workflow or a
local VS2022 machine.

### A. GitHub Actions (preferred)

Workflow: `.github/workflows/windows-x64-baseline.yml`

- Trigger: **Actions → Windows x64 baseline → Run workflow**, or a push that
  changes that workflow file / `build_release_vs2022.bat`.
- Runner: **`windows-2022`** (GitHub-hosted, no paid/larger runners).
  `windows-latest` currently exposes VS 2026; CMake 3.28 cannot generate for
  it. First attempt (run
  [36754464383](https://github.com/kdho77/VertexSlice/actions/runs/36754464383))
  failed that way: `build_release_vs.bat` picked VS 2026, cmake printed the
  generator list, the bat still exited 0, and the slicer job died on
  `fail-on-cache-miss`.
- Toolchain: `build_release_vs2022.bat` + MSBuild 17.x. The bat now fails if
  cmake/build/install fails or if `pack` is asked to zip a missing prefix.
- Platforms: Windows x64 only. No Linux/macOS/Flatpak matrix. No cron.
- Dependencies are cached on `hashFiles('deps/**')`. First run builds deps
  from scratch (hours). Later runs should hit cache.
- Packaging:
  - Installer: `cpack -G NSIS` → `ShidaoSlicer_Windows_Installer_V<ver>.exe`
  - Portable: `ShidaoSlicer_Windows_V<ver>_portable.zip`
- Artifacts: installer, portable zip, and
  `ShidaoSlicer_Windows_baseline_checksums` (`SHA256SUMS.txt` + `BASELINE.txt`).

### B. Local Windows x64 (VS2022)

Same toolchain as upstream OrcaSlicer:

```bat
REM From a VS2022 x64 Native Tools prompt, repository root
build_release_vs2022.bat deps
build_release_vs2022.bat slicer
cd build
cpack -G NSIS
```

`build_release_vs.bat` is the CI entry point; it auto-detects VS 2019/2022/2026
and is what the workflow calls.

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
3. **Import** — `inverted_L.3mf` (preset already baked) or `test_cube_20mm.stl`.
   Place the keel / first contact toward low Y. Do not rotate to “flat on a
   Cartesian bed.”
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

Produced by Actions run
[36755155238](https://github.com/kdho77/VertexSlice/actions/runs/36755155238)
on `windows-2022` from commit `a3732051731caed69186334dfbb764445b667386`.
The job is marked **failure** only because the post-step
`hashFiles('deps/**')` cache save timed out after the packages were already
uploaded. Do not treat that red X as a missing installer.

| Field | Installer (NSIS) | Portable zip |
|---|---|---|
| File | `ShidaoSlicer_Windows_Installer_V2.3.2-dev.exe` | `ShidaoSlicer_Windows_V2.3.2-dev_portable.zip` |
| Download | [artifact 11121367048](https://github.com/kdho77/VertexSlice/actions/runs/36755155238/artifacts/11121367048) | [artifact 11121481978](https://github.com/kdho77/VertexSlice/actions/runs/36755155238/artifacts/11121481978) |
| Size | 187,806,463 bytes | 209,402,102 bytes |
| SHA-256 | `d7092e070c5a640f5b3c0c1056573a0e2c7367aea9f7f7c89a34d830180af4f7` | `fffc9803836c2f8922df18120efae75121b29d3ff6a62f0c717315937f26ce9e` |

Checksum bundle (same hashes + `BASELINE.txt`):
[artifact 11121586956](https://github.com/kdho77/VertexSlice/actions/runs/36755155238/artifacts/11121586956).

Verified on download from that run: installer is a PE32 NSIS package; the
portable zip contains `orca-slicer.exe`, `OrcaSlicer.dll`, `LICENSE.txt`, and
`resources/profiles/IdeaFormer/` (IR3 V2 machine/process/filament JSONs).

The parent nightly at
https://github.com/tommasobbianchi/ShidaoSlicer/releases/tag/nightly
is **not** this baseline.
