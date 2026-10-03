#!/usr/bin/env bash
set -euo pipefail
ROOT="${1:-$(pwd)}"
RUN_DIR="${2:-}"
cd "$ROOT"
HOME="$ROOT/phases/p13/coefficient_law_raw_xt"
if [[ -z "$RUN_DIR" ]]; then
  RUN_DIR=$(find "$HOME/runs" -maxdepth 1 -type d -name 'p13_s0_k0_lock_*' | sort | tail -n 1)
fi
[[ -n "$RUN_DIR" && -d "$RUN_DIR" ]] || { echo "K0 run directory not found" >&2; exit 2; }
[[ "$(cat "$RUN_DIR/OVERALL_STATUS.txt")" == "PASS" ]] || { echo "K0 is not PASS" >&2; exit 3; }
STAMP=$(basename "$RUN_DIR" | sed 's/^p13_s0_k0_lock_//')
AUDIT_DIR="$HOME/audits/P13_S0_K0_AUDIT_${STAMP}"
ARCHIVE="$ROOT/P13_S0_K0_AUDIT_${STAMP}.tar.xz"
rm -rf "$AUDIT_DIR"
mkdir -p "$AUDIT_DIR/K0"
for f in OVERALL_STATUS.txt NEXT_ACTION.txt audit_summary.json semantic_output_digest.json upstream_context_verification.json inheritance_verification.json raw_grammar_l3_lock.json derivative_semantics_guard.json gauge_equivalence_lock.json branch_config_lock.json data_role_registry.json no_leakage_guard.json source_manifest.json runtime_environment.json; do
  cp "$RUN_DIR/$f" "$AUDIT_DIR/K0/$f"
done
cat > "$AUDIT_DIR/README.md" <<EOF
# P13-S0-K0 compact audit

Contains protocol/inheritance/gauge/data-role/leakage/provenance evidence only.

Excluded by design: coefficient payloads, response data/outcomes, candidate stores, arrays, caches, checkpoints, LU factors, and historical archives.
Authoritative upstream scientific objects are referenced by SHA/semantic digest rather than copied.
EOF
(
  cd "$HOME/audits"
  find "$(basename "$AUDIT_DIR")" -type f ! -name SHA256SUMS.txt -print0 | xargs -0 sha256sum > "$(basename "$AUDIT_DIR")/SHA256SUMS.txt"
  XZ_OPT=-9e tar -cJf "$ARCHIVE" "$(basename "$AUDIT_DIR")"
)
sha256sum "$ARCHIVE"
