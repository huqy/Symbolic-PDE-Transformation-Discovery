#!/usr/bin/env bash
set -euo pipefail
ROOT="${1:-$(pwd)}"
cd "$ROOT"
export OPENBLAS_NUM_THREADS=1
export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1
export PYTHONPATH="$ROOT/phases/p13/coefficient_law_raw_xt/src:$ROOT/phases/p11/raw_xt_td/src${PYTHONPATH:+:$PYTHONPATH}"
WORKERS="${P13_WORKERS:-${NSLOTS:-17}}"
if [[ "$WORKERS" =~ ^[0-9]+$ ]]; then
  if (( WORKERS > 1 )); then WORKERS=$((WORKERS-1)); fi
else
  WORKERS=16
fi
if (( WORKERS > 16 )); then WORKERS=16; fi
if (( WORKERS < 1 )); then WORKERS=1; fi

echo "P13-S0-K2R2 pre-search attainment repair"
echo "project_root=$ROOT"
echo "workers=$WORKERS"
echo "K1/K2 authoritative runs are read in place through LATEST markers; audit archives are not runtime inputs."
python -m p13rawxt.k2r2_attainment_repair --project-root "$ROOT" --workers "$WORKERS"
