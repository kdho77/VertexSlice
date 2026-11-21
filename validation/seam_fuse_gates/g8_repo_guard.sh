#!/usr/bin/env bash
# g8_repo_guard.sh - FROZEN gate G8 (PM, 2026-11-20). Do not edit; checked by SHA256SUMS.
# usage (repo root): bash validation/seam_fuse_gates/g8_repo_guard.sh BASE_SHA HEAD_SHA     exit 0 PASS, 1 FAIL
# BASE = the PM's gates commit the PR branches from. Fails if the diff BASE..HEAD:
#  - touches any path outside the FILES allow-list, or deletes/renames anything;
#  - changes any file under validation/seam_fuse_gates/ or validation/ref/ (frozen; also verified by SHA256SUMS);
#  - removes or edits any existing line of .github/workflows/docker-server-proof.yml (additive only), or adds a line with
#    continue-on-error, "|| true", "set +e" or "exit 0".
set -u
BASE="$1"; HEAD="$2"; fail=0
ALLOW=(
  "scripts/g2b_heelmod/apexcore_95a_locked.json"
  "scripts/g2b_heelmod/slice_g2b_heelmod.py"
  "scripts/g2b_heelmod/slice_apexcore_g2b.py"
  "scripts/g2b_heelmod/postprocess_g2b.py"
  "scripts/g2b_heelmod/test_apexcore_95a_locked.py"
  "scripts/g2b_heelmod/test_seam_off_plantar.py"
  "scripts/g2b_heelmod/test_seam_fuse.py"
  ".github/workflows/docker-server-proof.yml"
)
echo "== file list: $BASE..$HEAD"
while read -r st path; do
  [ -z "${st:-}" ] && continue
  ok=0; for a in "${ALLOW[@]}"; do [ "$path" = "$a" ] && ok=1; done
  if [ "$st" != "A" ] && [ "$st" != "M" ]; then echo "FAIL: $st $path (only add/modify allowed)"; fail=1; continue; fi
  if [ $ok = 0 ]; then echo "FAIL: $st $path (not in the FILES allow-list)"; fail=1; else echo "ok:   $st $path"; fi
done < <(git diff --name-status --no-renames "$BASE" "$HEAD")
echo "== frozen gates and reference unchanged"
if ! git diff --quiet "$BASE" "$HEAD" -- validation/seam_fuse_gates validation/ref; then
  echo "FAIL: frozen gate/reference files changed:"; git diff --name-only "$BASE" "$HEAD" -- validation/seam_fuse_gates validation/ref; fail=1
else echo "ok:   validation/seam_fuse_gates and validation/ref identical to BASE"; fi
echo "== workflow additive only"
WF=".github/workflows/docker-server-proof.yml"
removed=$(git diff -U0 "$BASE" "$HEAD" -- "$WF" | grep -c '^-[^-]' || true)
if [ "$removed" != "0" ]; then echo "FAIL: $removed existing workflow lines removed or edited"; fail=1; else echo "ok:   no workflow line removed"; fi
weak=$(git diff -U0 "$BASE" "$HEAD" -- "$WF" | grep '^+[^+]' | grep -E 'continue-on-error|\|\|[[:space:]]*true|set \+e|exit 0' || true)
if [ -n "$weak" ]; then echo "FAIL: added workflow lines weaken checks:"; echo "$weak"; fail=1; else echo "ok:   no continue-on-error / || true / set +e / exit 0 added"; fi
[ $fail = 0 ] && echo "G8 REPO GUARD: PASS" || echo "G8 REPO GUARD: FAIL"
exit $fail
