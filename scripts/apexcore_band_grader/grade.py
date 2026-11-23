"""Run gates G1-G10 on two ApexCore G-code directories.

Usage (from the repo root):

    python3 scripts/apexcore_band_grader/grade.py \\
        --repo . \\
        --base-sha cb0569eb19d9502841d73d30f1e4d93bc64bb07d \\
        --head-sha 425fc8f7cc4b49fe21f49e0641161e274c1e8a4e \\
        --baseline-dir /tmp/apex-grader/baseline \\
        --candidate-dir /tmp/apex-grader/candidate \\
        --json-out docs/proof/apexcore-band-grader/candidate.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

_DIR = Path(__file__).resolve().parent
if str(_DIR) not in sys.path:
    sys.path.insert(0, str(_DIR))

from gcode import load_gcode  # noqa: E402
from gates import gate_g9, git_name_only, git_numstat, grade_pair  # noqa: E402


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_pair(directory: Path) -> dict:
    ortho = directory / "cand_apex_core_orthocad.gcode"
    insole = directory / "cand_apex_core_insole2.gcode"
    return {
        "ortho_path": str(ortho),
        "insole_path": str(insole),
        "ortho_sha256": _sha256(ortho),
        "insole_sha256": _sha256(insole),
        "ortho": load_gcode(str(ortho)),
        "insole_lines": insole.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True),
        "insole_layers": load_gcode(str(insole))[1],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Grade ApexCore baseline vs candidate G-code")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--baseline-dir", required=True)
    parser.add_argument("--candidate-dir", required=True)
    parser.add_argument("--json-out", required=True)
    parser.add_argument("--hollow-step", type=float, default=0.1)
    parser.add_argument("--skip-g1", action="store_true")
    args = parser.parse_args()

    command = "python3 scripts/apexcore_band_grader/grade.py " + " ".join(sys.argv[1:])
    print("loading baseline", flush=True)
    base = _read_pair(Path(args.baseline_dir))
    print("loading candidate", flush=True)
    cand = _read_pair(Path(args.candidate_dir))
    g1_files = None
    g1_numstat = None
    g1_command = None
    if not args.skip_g1:
        g1_command = f"git diff --name-only {args.base_sha}..{args.head_sha}"
        g1_files = git_name_only(args.repo, args.base_sha, args.head_sha)
        g1_numstat = git_numstat(args.repo, args.base_sha, args.head_sha)
        print("G1 files", g1_files, flush=True)

    base_pre, base_layers = base["ortho"]
    cand_pre, cand_layers = cand["ortho"]
    print(f"orthocad layers {len(base_layers)} vs {len(cand_layers)}; running gates", flush=True)
    results = grade_pair(
        base_pre,
        base_layers,
        cand_pre,
        cand_layers,
        command=command,
        g1_files=g1_files,
        g1_numstat=g1_numstat,
        g1_command=g1_command,
        hollow_step=args.hollow_step,
        include_g2=True,
        insole_base=base["insole_lines"],
        insole_cand=cand["insole_lines"],
    )
    print("insole2 hollow columns", flush=True)
    insole_g9 = gate_g9(base["insole_layers"], cand["insole_layers"], command + "  # insole2", args.hollow_step)
    for gate in results:
        if gate["gate"] == "G9":
            gate["measured"]["insole2"] = {
                "baseline_forefoot_columns": insole_g9["measured"]["baseline_forefoot_columns"],
                "candidate_forefoot_columns": insole_g9["measured"]["candidate_forefoot_columns"],
                "baseline_above_165_columns": insole_g9["measured"]["baseline_above_165_columns"],
                "candidate_above_165_columns": insole_g9["measured"]["candidate_above_165_columns"],
                "pass": insole_g9["pass"],
            }
            gate["pass"] = bool(gate["pass"] and insole_g9["pass"])
            gate["threshold"] += "; same rule on insole2"
    payload = {
        "baseline_ortho_sha256": base["ortho_sha256"],
        "baseline_insole_sha256": base["insole_sha256"],
        "candidate_ortho_sha256": cand["ortho_sha256"],
        "candidate_insole_sha256": cand["insole_sha256"],
        "base_sha": args.base_sha,
        "head_sha": args.head_sha,
        "hollow_step_mm": args.hollow_step,
        "gates": results,
    }
    out = Path(args.json_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    for gate in results:
        status = "PASS" if gate["pass"] else "FAIL"
        print(f"{gate['gate']} {status}")
    print("wrote", out)
    return 0 if all(gate["pass"] for gate in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
