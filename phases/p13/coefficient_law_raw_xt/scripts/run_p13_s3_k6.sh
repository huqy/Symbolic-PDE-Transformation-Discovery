#!/usr/bin/env bash
set -euo pipefail
ROOT="${1:-$PWD}"
cd "$ROOT"
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-1}"
export MKL_NUM_THREADS="${MKL_NUM_THREADS:-1}"
export OPENBLAS_NUM_THREADS="${OPENBLAS_NUM_THREADS:-1}"
export NUMEXPR_NUM_THREADS="${NUMEXPR_NUM_THREADS:-1}"
export PYTHONPATH="$ROOT/phases/p13/coefficient_law_raw_xt/src:$ROOT/phases/p11/raw_xt_td/src${PYTHONPATH:+:$PYTHONPATH}"
python3 -m p13rawxt.s3_k6_final_first_branch_freeze --project-root "$ROOT"
