#!/usr/bin/env bash
set -euo pipefail
ROOT="${1:-$(pwd)}"
cd "$ROOT"
MARKER="phases/p13/coefficient_law_raw_xt/runs/LATEST_P13_S0_K3_RUN.txt"
[[ -f "$MARKER" ]] || { echo "Missing $MARKER" >&2; exit 2; }
RUN="$(cat "$MARKER")"
[[ -d "$RUN" ]] || { echo "Missing run directory $RUN" >&2; exit 2; }
STAMP="$(basename "$RUN" | sed 's/^p13_s0_k3_freeze_//')"
OUT="P13_S0_K3_FREEZE_AUDIT_${STAMP}.tar.xz"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
TOP="$TMP/P13_S0_K3_FREEZE_AUDIT_${STAMP}"
mkdir -p "$TOP/K3"
FILES=(
  OVERALL_STATUS.txt NEXT_ACTION.txt audit_summary.json semantic_output_digest.json
  s0_gate_adjudication.json s0_evidence_freeze.json claim_boundary.json
  data_boundary_freeze.json s1_entry_manifest.json authoritative_lineage_manifest.json
  source_manifest.json runtime_environment.json P13_S0_STAGE_FREEZE_CONTEXT.md
)
for f in "${FILES[@]}"; do
  [[ -f "$RUN/$f" ]] && cp "$RUN/$f" "$TOP/K3/$f"
done
cat > "$TOP/README.md" <<EOF
# P13-S0-K3 stage-freeze compact audit

Contains the S0 scientific adjudication, claim/data boundaries, exact lineage, S1-K0 authorization manifest, semantic/source/runtime provenance, and compact stage-freeze context.

Authoritative K0-K2R4 evidence remains in place under the run paths recorded in K3/authoritative_lineage_manifest.json. K3 does not copy K1 coefficient arrays, burn-in ledgers/checkpoints, fitter/reference JSONLs, V2 proposal ledgers, capacity arrays, DEV/SEALED private payloads, caches/tmp, or upstream audit archives.

No new active-input archive is required. S1-K0 must read K1 authoritative coefficient inputs and K3 freeze manifests in place through stable markers/paths.
EOF
(
  cd "$TOP"
  find . -type f ! -name SHA256SUMS.txt -print0 | LC_ALL=C sort -z | xargs -0 sha256sum > SHA256SUMS.txt
)
tar -C "$TMP" -cJf "$ROOT/$OUT" "$(basename "$TOP")"
sha256sum "$ROOT/$OUT"
echo "$ROOT/$OUT"
