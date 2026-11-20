#!/usr/bin/env bash
# run_inside_only_gates.sh - FROZEN gate runner (OrthoCAD PM, 2026-11-20). Do not edit; CI checks its sha256.
# usage (from the repo root): bash validation/run_inside_only_gates.sh GATE_DIR
# GATE_DIR holds live_<mat>_<stl>.gcode and cand_<mat>_<stl>.gcode for mat in apex_core apex_light and
# stl in orthocad (validation/test_models/orthocad_sample_top_insole.stl) and insole2 (validation/test_models/insole_sample.stl).
# Runs every frozen gate on every pair; exit 1 if any gate fails or any file/sha is wrong (incl. repo_guard.sh and the
# frozen workflow block text minus the one line that carries this runner's own sha).
# Kendon's decisions are baked in at build time (the runner is frozen, so a PR cannot change them):
#   CORE_D3   = "--d3"  (D3: "--d3" relaxes outer_lock/interior_lock for the ApexCore inner wall + infill boundary; inner_shift.py then guards them)
#   SCOPE     = "both"     (D2: both = both profiles fixed; core_only = ApexLight must stay identical to live)
#   PCT_CORE  = 2 , PCT_LIGHT = 10  (D2: print-time budget over live, percent, per profile)
#   CORE_ROT  = "--rotate"  (Kendon 11:38 AM / 12:17 PM MT: ApexCore seams back on the bottom: outer_lock/interior_lock allow
#                ONLY a cyclic rotation of closed outer/overhang loop starts; seam_bottom is absolute against ApexLight live;
#                seam_joint holds the prime/overlap and makes ApexCore travel cuts 0)
set -u
D="$1"; V="validation"; R="$D/results"; mkdir -p "$R"; fail=0
MUSTMATCH=(
  "scripts/g2b_heelmod/apexcore_95a_locked.json 7bfbabbca6be4b43129300fd3f1f63f9c90b33468f2800e7acf8a54c9388b000"
  "scripts/g2b_heelmod/check_travel_wall_crossings.py 5641f7ec52424040ca11a14090462ab2448b1fc8f3b51695530a18b22d1faf19"
  "validation/reference_gcode/apex_cura_insole_ref.gcode 05602c928ff2b91d4fac12c82863816dcb13f0c27cb7d9f136645085ac378b7b"
  "validation/test_models/orthocad_sample_top_insole.stl 6c67e279aa33ea15bd6ffb0c9ba8feecee907d73dfdc5eacadda24e09052df14"
  "validation/test_models/insole_sample.stl 38326fce9d20ece4657c8de7be4924a1c15288c17ec43e5910b8246c2f97ef47"
)
FROZEN=(
  "validation/outer_lock.py 1964f64a55b977675e6b143fc0b4db2eb055488154508bbcacbad0267a3533b5"
  "validation/interior_lock.py fcafd884d4c9f51cb35f7189403a37c4af3ea31643e06a2ba7ec662cec24256c"
  "validation/sequence_lock.py e519b67797034da8c194ddde90f65f1e00cb50ca4ce64874b65c9a0a4f99e123"
  "validation/air_vs_cura.py 49fdbc5163084437501b81db01caedd303f084c96fe8b0c51008a83854b5d72e"
  "validation/air_bands.py c82e6ff2a8303c459a422e4141d914a045b2c4bb42180cdf235cc1e977db2bff"
  "validation/print_budget.py c576d0fbcd8cfc940d8f1495a274dcff3316d3baafeff8cea53b3c4251af82e2"
  "validation/skin_bond.py 0fe3627f5e1165c2dd3468b79cdb0fb98c9d5d0f1fe9dd4716daae2a84fca013"
  "validation/inner_shift.py 00db83c491a643bf42ea430b4ff5e0fa4f44806f70139a53f8be9423045ab59d"
  "validation/seam_bottom.py dbc84839afd8dc62506be281b9791567f35f807ec672568a12ecfd2c1c30a075"
  "validation/seam_joint.py 30566f98921b995b1d6f3d300736729f75f6560fe9cd891ea6ac105d85ab654f"
  "validation/belt_air_columns.py 08a65cb5b5dce5f26f9bead182679cdd50b9a3dc61202fc74090c6d8898e116f"
  "validation/repo_guard.sh 73c139ccf65261d79cd7f28945ba7da8485bc352ece4aefed403bb2a30336dc0"
)
for pair in "${MUSTMATCH[@]}" "${FROZEN[@]}"; do
  p="${pair%% *}"; sha="${pair##* }"; got=$(sha256sum "$p" 2>/dev/null | cut -d' ' -f1)
  [ "$got" = "$sha" ] && echo "sha ok   $p" || { echo "sha FAIL $p ($got != $sha)"; fail=1; }
done
WF=".github/workflows/docker-server-proof.yml"
s=$(grep -n '# >>> INSIDE-ONLY GATES' "$WF" | head -1 | cut -d: -f1); e=$(grep -n '# <<< INSIDE-ONLY GATES' "$WF" | head -1 | cut -d: -f1)
if [ -n "$s" ] && [ -n "$e" ]; then
  core=$(sed -n "${s},${e}p" "$WF" | grep -v 'validation/run_inside_only_gates.sh" | sha256sum -c -' | sha256sum | cut -d' ' -f1)
  if [ "$core" = "e18a6732066735ee4517210682de1e0032024c78ed228848c5cc2613724707f3" ]; then echo "sha ok   frozen workflow block (minus the runner-sha line)"; else echo "sha FAIL frozen workflow block text ($core != e18a6732066735ee4517210682de1e0032024c78ed228848c5cc2613724707f3)"; fail=1; fi
else echo "sha FAIL frozen workflow block markers missing in $WF"; fail=1; fi
CORE_D3="--d3"; SCOPE="both"; PCT_CORE="2"; PCT_LIGHT="10"; CORE_ROT="--rotate"
CURA="$V/reference_gcode/apex_cura_insole_ref.gcode"; CTC="scripts/g2b_heelmod/check_travel_wall_crossings.py"
run() { # name cmd...
  name="$1"; shift
  if "$@" > "$R/$name.txt" 2>&1; then echo "PASS $name"; echo "PASS $name" >> "$R/summary.txt"; else echo "FAIL $name"; echo "FAIL $name" >> "$R/summary.txt"; fail=1; fi
}
: > "$R/summary.txt"
for mat in apex_core apex_light; do
  for stl in orthocad insole2; do
    L="$D/live_${mat}_${stl}.gcode"; C="$D/cand_${mat}_${stl}.gcode"; n="${mat}_${stl}"
    if [ ! -s "$L" ] || [ ! -s "$C" ]; then echo "FAIL missing $L or $C"; fail=1; continue; fi
    if [ "$mat" = "apex_core" ]; then D3="$CORE_D3"; PCT="$PCT_CORE"; ROT="$CORE_ROT"; SBREF="--light-ref $D/live_apex_light_${stl}.gcode"; SJ="--zero-travel-cuts"
    else D3=""; PCT="$PCT_LIGHT"; ROT=""; SBREF=""; SJ=""; fi
    if [ "$mat" = "apex_core" ] && [ ! -s "$D/live_apex_light_${stl}.gcode" ]; then echo "FAIL missing $D/live_apex_light_${stl}.gcode (ApexCore seam bar)"; fail=1; continue; fi
    if [ "$stl" = "orthocad" ]; then SB=""; else SB="--relative-only"; fi
    run "$n.sequence_lock" python3 $V/sequence_lock.py "$L" "$C" --json "$R/$n.sequence_lock.json"
    run "$n.seam_bottom"   python3 $V/seam_bottom.py "$L" "$C" $SBREF --json "$R/$n.seam_bottom.json"
    run "$n.seam_joint"    python3 $V/seam_joint.py "$L" "$C" $SJ --json "$R/$n.seam_joint.json"
    if [ "$mat" = "apex_light" ] && [ "$SCOPE" = "core_only" ]; then
      # ApexLight must be identical to live: every extrusion type locked, no goal gates
      run "$n.outer_lock_all" python3 $V/outer_lock.py "$L" "$C" --all --ctc "$CTC" --json "$R/$n.outer_lock_all.json"
      run "$n.print_budget"  python3 $V/print_budget.py "$L" "$C" --time-pct 0 --json "$R/$n.print_budget.json"
      run "$n.skin_bond"     python3 $V/skin_bond.py "$C" --live "$L" --relative-only --json "$R/$n.skin_bond.json"
      continue
    fi
    run "$n.outer_lock"    python3 $V/outer_lock.py "$L" "$C" $D3 $ROT --ctc "$CTC" --json "$R/$n.outer_lock.json"
    run "$n.interior_lock" python3 $V/interior_lock.py "$L" "$C" $D3 $ROT --json "$R/$n.interior_lock.json"
    if [ -n "$D3" ]; then
      run "$n.inner_shift" python3 $V/inner_shift.py "$L" "$C" --json "$R/$n.inner_shift.json"
    fi
    run "$n.print_budget"  python3 $V/print_budget.py "$L" "$C" --time-pct "$PCT" --json "$R/$n.print_budget.json"
    run "$n.air_bands"     python3 $V/air_bands.py "$L" "$C" --air $V/belt_air_columns.py --json "$R/$n.air_bands.json"
    run "$n.skin_bond"     python3 $V/skin_bond.py "$C" --live "$L" $SB --json "$R/$n.skin_bond.json"
    if [ "$stl" = "orthocad" ]; then
      run "$n.air_vs_cura" python3 $V/air_vs_cura.py "$C" "$CURA" --air $V/belt_air_columns.py --json "$R/$n.air_vs_cura.json"
    fi
  done
done
[ $fail = 0 ] && echo "ALL GATES PASS" || echo "GATES FAILED"
exit $fail
