#!/usr/bin/env bash
set -euo pipefail
ROOT="${1:-$(pwd)}"
cd "$ROOT"
export OPENBLAS_NUM_THREADS=1
export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1
export PYTHONPATH="$ROOT/phases/p13/coefficient_law_raw_xt/src:$ROOT/phases/p11/raw_xt_td/src${PYTHONPATH:+:$PYTHONPATH}"

echo "P13-S0-K3 scientific adjudication and freeze"
echo "project_root=$ROOT"
echo "heavy_compute=false workers=1"
echo "K2R4 authoritative evidence is read in place through LATEST_P13_S0_K2R4_RUN.txt; no audit archive is a runtime input."
python -m p13rawxt.k3_s0_freeze --project-root "$ROOT"
