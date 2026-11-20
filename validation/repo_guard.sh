#!/usr/bin/env bash
# repo_guard.sh - FROZEN gate (OrthoCAD PM, 2026-11-20). Do not edit; CI checks its sha256.
# usage (from the repo root): bash validation/repo_guard.sh BASE_SHA HEAD_SHA     e.g. b7e90bd1... <PR head>
# Fails (exit 1) if the PR touches any path outside the FILES allow-list (any profile/process JSON, golden/hash/locked
# file, server/, scripts/, other src/ file, ...), deletes or renames anything, edits/removes an existing workflow line,
# puts the frozen block anywhere but immediately after the Golden chain gate step and before validation_unit:, or if a
# MUST-MATCH file differs from main b7e90bd1, or if a frozen gate file at HEAD differs from its sha256. The block TEXT and
# this file's own sha256 are checked by run_inside_only_gates.sh (FROZEN list) and by the PM.
set -u
BASE="$1"; HEAD="$2"; fail=0
ALLOW=(
  "src/libslic3r/PerimeterGenerator.cpp"
  "src/libslic3r/PerimeterGenerator.hpp"
  ".github/workflows/docker-server-proof.yml"
  "validation/outer_lock.py" "validation/interior_lock.py" "validation/sequence_lock.py" "validation/air_vs_cura.py"
  "validation/air_bands.py" "validation/print_budget.py" "validation/belt_air_columns.py" "validation/skin_bond.py" "validation/inner_shift.py" "validation/seam_bottom.py" "validation/seam_joint.py"
  "scripts/g2b_heelmod/postprocess_g2b.py" "scripts/g2b_heelmod/test_seam_off_plantar.py"
  "validation/run_inside_only_gates.sh" "validation/repo_guard.sh"
)
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
)
echo "== file list: $BASE..$HEAD"
git diff --name-status --no-renames "$BASE" "$HEAD" | while read -r st path; do
  ok=0
  for a in "${ALLOW[@]}"; do [ "$path" = "$a" ] && ok=1; done
  if [ "$st" != "A" ] && [ "$st" != "M" ]; then echo "FAIL: $st $path (only add/modify allowed)"; echo 1 > /tmp/.rg_fail; continue; fi
  if [ $ok = 0 ]; then
    why="not in the FILES allow-list"
    case "$path" in
      resources/profiles/*|*process*.json|*profile*.json) why="profile/process JSON change";;
      *golden*|*locked*|*hash*|*.sha256) why="golden/hash/locked file";;
      scripts/g2b_heelmod/*) why="g2b_heelmod change (post-process / goldens)";;
      server/*) why="server change";;
    esac
    echo "FAIL: $st $path ($why)"; echo 1 > /tmp/.rg_fail
  else echo "ok:   $st $path"; fi
done
[ -f /tmp/.rg_fail ] && { fail=1; rm -f /tmp/.rg_fail; }
echo "== workflow must be additive inside the frozen block"
WF=".github/workflows/docker-server-proof.yml"; s=""; e=""
if git diff --quiet "$BASE" "$HEAD" -- "$WF"; then echo "FAIL: workflow has no gate block"; fail=1; else
  removed=$(git diff -U0 "$BASE" "$HEAD" -- "$WF" | grep -c '^-[^-]')
  [ "$removed" != "0" ] && { echo "FAIL: $removed existing workflow lines removed/edited"; fail=1; }
  s=$(git show "$HEAD:$WF" | grep -n '# >>> INSIDE-ONLY GATES' | head -1 | cut -d: -f1)
  e=$(git show "$HEAD:$WF" | grep -n '# <<< INSIDE-ONLY GATES' | head -1 | cut -d: -f1)
  if [ -z "$s" ] || [ -z "$e" ]; then echo "FAIL: frozen block markers missing"; fail=1; else
    git diff -U0 "$BASE" "$HEAD" -- "$WF" | grep '^@@' | sed -E 's/^@@ -[0-9,]+ \+([0-9]+)(,([0-9]+))? @@.*/\1 \3/' | while read -r st cnt; do
      cnt=${cnt:-1}; en=$((st+cnt-1))
      for ((n=st; n<=en; n++)); do
        if [ "$n" -lt "$s" ] || [ "$n" -gt "$e" ]; then
          line=$(git show "$HEAD:$WF" | sed -n "${n}p" | tr -d '[:space:]')
          [ -n "$line" ] && { echo "FAIL: workflow line $n changed outside the frozen block (block is $s-$e; only blank separator lines are tolerated)"; echo 1 > /tmp/.rg_fail; }
        fi
      done
    done
    [ -f /tmp/.rg_fail ] && { fail=1; rm -f /tmp/.rg_fail; }
  fi
fi
echo "== frozen block position: immediately after the Golden chain gate step, before validation_unit:"
if [ -n "$s" ] && [ -n "$e" ]; then
  prevstep=$(git show "$HEAD:$WF" | head -n "$s" | grep -E '^      - name:' | tail -1 | sed -E 's/^ *- name: *//')
  nextline=$(git show "$HEAD:$WF" | tail -n +"$((e+1))" | grep -v '^[[:space:]]*$' | head -1)
  bad_inside=$(git show "$HEAD:$WF" | sed -n "$((s+1)),$((e-1))p" | grep -c '^  [A-Za-z_][A-Za-z0-9_-]*:$')
  if [ "$prevstep" = "Golden chain gate" ] && [ "$nextline" = "  validation_unit:" ] && [ "$bad_inside" = "0" ]; then
    echo "ok:   block follows the Golden chain gate step and precedes validation_unit:"
  else
    echo "FAIL: block position (previous step '$prevstep', next line '$nextline'); it must sit immediately after the Golden chain gate step and before validation_unit:"; fail=1
  fi
fi
echo "== MUST-MATCH files (HEAD must equal main)"
for pair in "${MUSTMATCH[@]}"; do
  p="${pair%% *}"; sha="${pair##* }"
  got=$(git show "$HEAD:$p" 2>/dev/null | sha256sum | cut -d' ' -f1)
  if [ "$got" = "$sha" ]; then echo "ok:   $p"; else echo "FAIL: $p sha256 $got != $sha"; fail=1; fi
done
echo "== frozen gate files at HEAD"
for pair in "${FROZEN[@]}"; do
  p="${pair%% *}"; sha="${pair##* }"
  got=$(git show "$HEAD:$p" 2>/dev/null | sha256sum | cut -d' ' -f1)
  if [ "$got" = "$sha" ]; then echo "ok:   $p"; else echo "FAIL: $p sha256 $got != $sha"; fail=1; fi
done
[ $fail = 0 ] && echo "REPO GUARD: PASS" || echo "REPO GUARD: FAIL"
exit $fail
