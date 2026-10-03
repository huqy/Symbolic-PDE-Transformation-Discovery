#!/usr/bin/env bash
set -euo pipefail
ROOT="${1:-$(pwd)}"
PRIVATE_ROOT="${P13_PRIVATE_ROOT:-${2:-}}"
RUN_DIR="${P13_K0R_RUN_DIR:-${3:-}}"
cd "$ROOT"
[[ -n "$PRIVATE_ROOT" ]] || { echo "Set P13_PRIVATE_ROOT or pass private-root as argument 2." >&2; exit 2; }
case "$PRIVATE_ROOT" in "$ROOT"|"$ROOT"/*) echo "P13_PRIVATE_ROOT must be outside project root." >&2; exit 2;; esac
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
export P13_WORKERS="${P13_WORKERS:-16}"
export PYTHONPATH="$ROOT/phases/p13/coefficient_law_raw_xt/src:$ROOT/phases/p11/raw_xt_td/src${PYTHONPATH:+:$PYTHONPATH}"

echo "P13-S1-K0R post-S0 adversarial-review protocol lock"
echo "project_root=$ROOT"
echo "allocation_contract=17 CPUs = 16 workers + 1 coordinator"
echo "workers=$P13_WORKERS BLAS/OpenMP_threads=1"
echo "formal_discovery_search=false"
echo "authoritative K1 coefficient objects are read in place through LATEST_P13_S0_K1_RUN.txt"
echo "within-family diagnostic payload will be written only under private_root=$PRIVATE_ROOT"
args=(--project-root "$ROOT" --private-root "$PRIVATE_ROOT")
[[ -z "$RUN_DIR" ]] || args+=(--run-dir "$RUN_DIR")
[[ "${P13_K0R_ALLOW_NONCANONICAL_TEST_ROOT:-0}" != 1 ]] || args+=(--allow-noncanonical-test-root)
python -m p13rawxt.s1_k0r_protocol_lock "${args[@]}"
