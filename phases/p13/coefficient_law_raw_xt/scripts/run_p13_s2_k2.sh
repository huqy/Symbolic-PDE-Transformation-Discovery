#!/usr/bin/env bash
set -euo pipefail
ROOT="${1:-$PWD}"
cd "$ROOT"
export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1
export PYTHONPATH="$ROOT/phases/p13/coefficient_law_raw_xt/src:$ROOT/phases/p11/raw_xt_td/src${PYTHONPATH:+:$PYTHONPATH}"
python -m p13rawxt.s2_k2_adjudication --project-root "$ROOT"
