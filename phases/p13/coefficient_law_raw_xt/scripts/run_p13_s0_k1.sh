#!/usr/bin/env bash
set -euo pipefail
ROOT="${1:-$(pwd)}"
K0_AUDIT="${2:?usage: run_p13_s0_k1.sh PROJECT_ROOT K0_AUDIT PRIVATE_ROOT}"
PRIVATE_ROOT="${3:?usage: run_p13_s0_k1.sh PROJECT_ROOT K0_AUDIT PRIVATE_ROOT}"
cd "$ROOT"
export OPENBLAS_NUM_THREADS=1
export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1
export PYTHONPATH="$ROOT/phases/p13/coefficient_law_raw_xt/src${PYTHONPATH:+:$PYTHONPATH}"
python -m p13rawxt.k1_commit \
  --project-root "$ROOT" \
  --k0-audit "$K0_AUDIT" \
  --private-root "$PRIVATE_ROOT"
