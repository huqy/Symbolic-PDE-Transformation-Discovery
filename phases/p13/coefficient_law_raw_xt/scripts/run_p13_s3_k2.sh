#!/usr/bin/env bash
set -euo pipefail

project_root="${1:-$PWD}"
export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1
export PYTHONPATH="${project_root}/phases/p13/coefficient_law_raw_xt/src:${project_root}/phases/p11/raw_xt_td/src${PYTHONPATH:+:${PYTHONPATH}}"

python3 -m p13rawxt.s3_k2_operator_adjudication --project-root "${project_root}"
