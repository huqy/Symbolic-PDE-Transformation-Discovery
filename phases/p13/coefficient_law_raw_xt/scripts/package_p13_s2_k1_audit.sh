#!/usr/bin/env bash
set -euo pipefail
ROOT="${1:-$PWD}"
cd "$ROOT"
MARKER="phases/p13/coefficient_law_raw_xt/runs/LATEST_P13_S2_K1_RUN.txt"
[[ -f "$MARKER" ]] || { echo "missing $MARKER" >&2; exit 2; }
RUN_REL="$(cat "$MARKER")"
K1="$RUN_REL/K1_zero_shot_operator_transfer"
[[ -f "$RUN_REL/K1_OVERALL_STATUS.txt" ]] || { echo "missing K1 status" >&2; exit 2; }
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="P13_S2_K1_COMPLETE_ZERO_SHOT_OPERATOR_TRANSFER_AUDIT_${STAMP}.tar.xz"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
D="$TMP/P13_S2_K1_AUDIT_${STAMP}"
mkdir -p "$D"
for f in \
 K1_ENTRY_AUDIT.json \
 K1_DEVELOPMENT_IDENTITY_BASELINES.json \
 K1_OPERATOR_TRANSFER_AGGREGATE.json \
 K1_PF0_TRANSFER_ASSOCIATIONS_DESCRIPTIVE.json \
 K1_DATA_BOUNDARY_GUARD.json \
 K1_ADJUDICATION_HANDOFF_LOCK.json \
 K1_SOURCE_MANIFEST.json \
 K1_RUNTIME_ENVIRONMENT.json \
 K1_SEMANTIC_OUTPUT_DIGEST.json \
 K1_SCIENTIFIC_SUMMARY.json; do
  cp "$K1/$f" "$D/$f"
done
cp "$RUN_REL/K1_OVERALL_STATUS.txt" "$D/"
cp "$RUN_REL/K1_NEXT_ACTION.txt" "$D/"
printf '%s\n' "$RUN_REL" > "$D/AUTHORITATIVE_S2_RUN.txt"
printf '%s\n' "$K1/K1_OPERATOR_TRANSFER_MEASUREMENTS.jsonl" > "$D/AUTHORITATIVE_K1_MEASUREMENTS_PATH.txt"
sha256sum "$K1/K1_OPERATOR_TRANSFER_MEASUREMENTS.jsonl" > "$D/AUTHORITATIVE_K1_MEASUREMENTS_SHA256.txt"
cat > "$D/AUDIT_CONTENTS.txt" <<EOF
INCLUDES: compact K1 entry/provenance lock, identity baselines, complete-cohort aggregate/fidelity census, PF0 descriptive association, data-boundary lock, adjudication handoff lock, runtime/source/semantic provenance, status and authoritative paths.
EXCLUDES: 2307-row K1 measurement JSONL, DEVELOPMENT NPZ arrays, S1 candidate/branch/skeleton/proposal ledgers, S1 membership copies, PF0 branch-level rows, private coefficient archives, DEVELOPMENT response, SEALED payloads, caches/tmp/checkpoints, upstream audit archives.
RUNTIME INPUT POLICY: this compact audit is review-only and must never become K2 runtime input.
EOF
XZ_OPT=-9e tar -cJf "$OUT" -C "$TMP" "$(basename "$D")"
sha256sum "$OUT"
printf '%s\n' "$OUT"
