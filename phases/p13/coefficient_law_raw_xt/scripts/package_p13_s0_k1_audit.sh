#!/usr/bin/env bash
set -euo pipefail
ROOT="${1:-$(pwd)}"
RUN="${2:?usage: package_p13_s0_k1_audit.sh PROJECT_ROOT RUN_DIR [OUT_DIR]}"
OUT_DIR="${3:-$ROOT}"
cd "$ROOT"
RUN="$(cd "$RUN" && pwd)"
STAMP="$(basename "$RUN" | sed -E 's/^p13_s0_k1_commit_//')"
NAME="P13_S0_K1_AUDIT_${STAMP}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/$NAME/K1"
FILES=(
  OVERALL_STATUS.txt NEXT_ACTION.txt audit_summary.json parent_k0_verification.json
  public_coefficient_registry.jsonl open_generator_provenance.jsonl open_regime_certificates.json
  train_identifiability_report.json private_payload_commitments.json role_family_separation_certificate.json
  objective_response_contract_lock.json open_search_object_manifest.json no_leakage_guard.json
  source_manifest.json runtime_environment.json semantic_output_digest.json
)
for f in "${FILES[@]}"; do
  test -f "$RUN/$f"
  cp "$RUN/$f" "$TMP/$NAME/K1/$f"
done
cat > "$TMP/$NAME/README.md" <<EOF
# P13-S0-K1 compact audit

Contains: parent K0 lock, open coefficient registry/provenance, regime certificates, TRAIN identifiability evidence, objective/response contract, role/leakage records, private payload commitments (path/SHA/schema/count only), source/runtime provenance, status and semantic digest.

Excluded deliberately: open .npz coefficient arrays, all private DEVELOPMENT/SEALED archives and seed material, candidate stores, response outcomes, reference arrays, sparse matrices/LU factors, caches, checkpoints, tmp files, and upstream archives.

Authoritative open arrays remain in-place under:
$RUN/open_search_objects

Private payloads remain outside the project tree at paths recorded only by K1/private_payload_commitments.json.
EOF
(
  cd "$TMP"
  find "$NAME" -type f ! -name SHA256SUMS.txt -print0 | LC_ALL=C sort -z | xargs -0 sha256sum > "$NAME/SHA256SUMS.txt"
)
mkdir -p "$OUT_DIR"
XZ_OPT=-9e tar -C "$TMP" -cJf "$OUT_DIR/$NAME.tar.xz" "$NAME"
sha256sum "$OUT_DIR/$NAME.tar.xz"
echo "$OUT_DIR/$NAME.tar.xz"
