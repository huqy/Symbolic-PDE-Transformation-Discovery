#!/usr/bin/env bash
set -euo pipefail
ROOT="${1:-$(pwd)}"
ACTIVE="${2:-}"
CLAIM_I="${3:-}"
CLAIM_II="${4:-}"
BOOTSTRAP="${5:-}"
cd "$ROOT"
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
export PYTHONPATH="$ROOT/phases/p13/coefficient_law_raw_xt/src:$ROOT/phases/p11/raw_xt_td/src${PYTHONPATH:+:$PYTHONPATH}"
ARGS=(--project-root "$ROOT")
[[ -n "$ACTIVE" ]] && ARGS+=(--active-design-source "$ACTIVE")
[[ -n "$CLAIM_I" ]] && ARGS+=(--claim-i-freeze-source "$CLAIM_I")
[[ -n "$CLAIM_II" ]] && ARGS+=(--claim-ii-freeze-source "$CLAIM_II")
[[ -n "$BOOTSTRAP" ]] && ARGS+=(--superseded-bootstrap-source "$BOOTSTRAP")
python -m p13rawxt.k0_lock "${ARGS[@]}"
