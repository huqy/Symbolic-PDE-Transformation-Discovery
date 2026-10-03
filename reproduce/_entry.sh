#!/usr/bin/env bash
# Shared launcher: explicit clean source root, candidate interpreter and threads.
set -euo pipefail
P13_SOURCE_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd -- "$P13_SOURCE_ROOT"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONNOUSERSITE=1
export PYTHONPATH="$P13_SOURCE_ROOT"
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
P13_PYTHON="${P13_PYTHON:-python3}"

# Publication-only interpreter routing; scientific stage launchers remain exact.
export P13_RELEASE_PYTHON="$P13_PYTHON"
P13_PYTHON="$P13_SOURCE_ROOT/reproduce/publication_python.sh"
