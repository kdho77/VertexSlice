#!/usr/bin/env bash
# Build one OrcaSlicer binary from an exact commit, reusing deps/ already
# present in the image. Unchanged sources keep their mtimes so a later commit
# in the same stage rebuilds incrementally.
# Usage: docker_build_pinned_slicer.sh COMMIT INSTALL_PREFIX BINARY_NAME
set -euo pipefail

if [[ $# -ne 3 ]]; then
    echo "usage: $0 COMMIT INSTALL_PREFIX BINARY_NAME" >&2
    exit 2
fi

COMMIT="$1"
PREFIX="$2"
NAME="$3"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"

workdir="$(mktemp -d)"
trap 'rm -rf "${workdir}"' EXIT

git init "${workdir}/repo"
git -C "${workdir}/repo" remote add origin https://github.com/kdho77/VertexSlice.git
git -C "${workdir}/repo" fetch --depth 1 origin "${COMMIT}"
mkdir -p "${workdir}/tree"
git -C "${workdir}/repo" archive FETCH_HEAD \
    src resources cmake CMakeLists.txt version.inc build_linux.sh \
    | tar -C "${workdir}/tree" -xf -
python3 "${ROOT}/scripts/pin_orca_tree.py" "${workdir}/tree" "${ROOT}"

export CMAKE_BUILD_PARALLEL_LEVEL="${CMAKE_BUILD_PARALLEL_LEVEL:-4}"
./build_linux.sh -sr

mkdir -p "${PREFIX}/bin"
cp -a build/src/Release/orca-slicer "${PREFIX}/bin/${NAME}"
rm -rf "${PREFIX}/resources"
cp -a resources "${PREFIX}/resources"
chmod +x "${PREFIX}/bin/${NAME}"
printf '%s\n' "${COMMIT}" > "${PREFIX}/COMMIT"
