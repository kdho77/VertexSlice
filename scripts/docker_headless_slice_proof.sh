#!/usr/bin/env bash
# Run inside the server image after build (or locally with ORCA_SLICER_BIN set).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="${ROOT}"
export ORCA_RESOURCES="${ORCA_RESOURCES:-${ROOT}/resources}"
export ORCA_SLICER_BIN="${ORCA_SLICER_BIN:-/app/bin/orca-slicer}"
exec python3 "${ROOT}/server/run_insole_proof.py"
