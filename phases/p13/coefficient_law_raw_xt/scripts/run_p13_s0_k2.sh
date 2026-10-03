#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="${PROJECT_ROOT:-$PWD}"
cd "$PROJECT_ROOT"

export PYTHONPATH="$PROJECT_ROOT/phases/p13/coefficient_law_raw_xt/src:$PROJECT_ROOT/phases/p11/raw_xt_td/src${PYTHONPATH:+:$PYTHONPATH}"
export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1

if [[ ! -f phases/p13/coefficient_law_raw_xt/runs/LATEST_P13_S0_K1_RUN.txt ]]; then
  echo "ERROR: missing authoritative K1 marker" >&2
  exit 2
fi

workers="${P13_WORKERS:-}"
if [[ -z "$workers" ]]; then
  slots="${NSLOTS:-17}"
  if (( slots > 1 )); then workers=$((slots-1)); else workers=1; fi
  if (( workers > 16 )); then workers=16; fi
fi

echo "P13-S0-K2 consolidated qualification"
echo "project_root=$PROJECT_ROOT"
echo "workers=$workers"
echo "K1 input is read in place via LATEST_P13_S0_K1_RUN.txt; no K1 audit archive is used."

python -m p13rawxt.k2_qualification \
  --project-root "$PROJECT_ROOT" \
  --workers "$workers" \
  --resume

RUN_REL="$(cat phases/p13/coefficient_law_raw_xt/runs/LATEST_P13_S0_K2_RUN.txt)"
echo "K2 run: $RUN_REL"
echo "OVERALL_STATUS=$(tr -d '\n' < "$RUN_REL/OVERALL_STATUS.txt")"
