#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="${PROJECT_ROOT:-$PWD}"
cd "$PROJECT_ROOT"
MARKER="phases/p13/coefficient_law_raw_xt/runs/LATEST_P13_S0_K2_RUN.txt"
[[ -f "$MARKER" ]] || { echo "ERROR: missing $MARKER" >&2; exit 2; }
RUN_REL="$(cat "$MARKER")"
RUN="$PROJECT_ROOT/$RUN_REL"
[[ -f "$RUN/OVERALL_STATUS.txt" ]] || { echo "ERROR: K2 run is not complete: $RUN_REL" >&2; exit 2; }

stamp="$(basename "$RUN" | sed 's/^p13_s0_k2_qualification_//')"
OUT="$PROJECT_ROOT/P13_S0_K2_AUDIT_${stamp}.tar.xz"
TMP="$(mktemp -d "$PROJECT_ROOT/.p13_k2_audit.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/P13_S0_K2_AUDIT_${stamp}/K2"
DST="$TMP/P13_S0_K2_AUDIT_${stamp}/K2"

# Compact scientific evidence only. Authoritative large ledgers stay in the K2 run and are referenced by SHA/path.
for f in \
  OVERALL_STATUS.txt NEXT_ACTION.txt audit_summary.json semantic_output_digest.json \
  parent_k1_inplace_verification.json m2_semantics.json identifiability.json \
  gauge_equivalence.json capacity_null_separation.json fitter_qualification.json \
  proposal_geometry_qualification.json evaluator_fidelity_cost.json lower_order_diagnostics.json \
  causal_response_feasibility.json no_leakage_guard.json k3_handoff_manifest.json \
  authoritative_evidence_manifest.json source_manifest.json runtime_environment.json; do
  [[ -f "$RUN/$f" ]] && cp "$RUN/$f" "$DST/$f"
done

cat > "$TMP/P13_S0_K2_AUDIT_${stamp}/README.md" <<EOF
# P13-S0-K2 compact audit

Contains compact K2 gate/adjudication evidence, source/runtime provenance, parent K1 in-place lock, and SHA/path manifests for authoritative K2 ledgers.

Excluded deliberately: K1 coefficient arrays, K1 private DEV/SEALED payloads, K2 fitter/proposal JSONL ledgers, calibration-only exact capacity AST artifact, caches/checkpoints/tmp, sparse matrices/LU factors, and upstream archives.

Authoritative K2 run remains:
$RUN_REL
EOF

(
  cd "$TMP/P13_S0_K2_AUDIT_${stamp}"
  find . -type f ! -name SHA256SUMS.txt -print0 | xargs -0 sha256sum > SHA256SUMS.txt
)
XZ_OPT=-9e tar -C "$TMP" -cJf "$OUT" "P13_S0_K2_AUDIT_${stamp}"
sha256sum "$OUT"
echo "AUDIT=$OUT"
