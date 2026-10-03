#!/usr/bin/env bash
set -euo pipefail
ROOT="${1:-$PWD}"
cd "$ROOT"
MARKER="phases/p13/coefficient_law_raw_xt/runs/LATEST_P13_S2_K2_RUN.txt"
[[ -f "$MARKER" ]] || { echo "missing $MARKER" >&2; exit 2; }
RUN_REL="$(cat "$MARKER")"
K2="$RUN_REL/K2_III_B_adjudication_response_entry_lock"
[[ -f "$RUN_REL/K2_OVERALL_STATUS.txt" ]] || { echo "missing K2 status" >&2; exit 2; }
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="P13_S2_K2_III_B_ADJUDICATION_RESPONSE_ENTRY_LOCK_AUDIT_${STAMP}.tar.xz"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
D="$TMP/P13_S2_K2_AUDIT_${STAMP}"
mkdir -p "$D"
for f in \
 K2_ENTRY_AND_K1_EVIDENCE_REVIEW.json \
 K2_III_B_DECISION_SUMMARY.json \
 K2_RESPONSE_ELIGIBLE_INPUT_MANIFEST.json \
 K2_DATA_BOUNDARY_GUARD.json \
 K2_RESPONSE_ENTRY_LOCK.json \
 K2_SOURCE_MANIFEST.json \
 K2_RUNTIME_ENVIRONMENT.json \
 K2_SEMANTIC_OUTPUT_DIGEST.json \
 K2_SCIENTIFIC_SUMMARY.json; do
  cp "$K2/$f" "$D/$f"
done
cp "$RUN_REL/K2_OVERALL_STATUS.txt" "$D/"
cp "$RUN_REL/K2_NEXT_ACTION.txt" "$D/"
printf '%s\n' "$RUN_REL" > "$D/AUTHORITATIVE_S2_RUN.txt"
printf '%s\n' "$K2/K2_III_B_DECISION_MAP.jsonl" > "$D/AUTHORITATIVE_K2_DECISION_MAP_PATH.txt"
sha256sum "$K2/K2_III_B_DECISION_MAP.jsonl" > "$D/AUTHORITATIVE_K2_DECISION_MAP_SHA256.txt"
printf '%s\n' "$K2/K2_RESPONSE_ELIGIBLE_PASS_MEMBERSHIP.jsonl" > "$D/AUTHORITATIVE_RESPONSE_ELIGIBLE_MEMBERSHIP_PATH.txt"
sha256sum "$K2/K2_RESPONSE_ELIGIBLE_PASS_MEMBERSHIP.jsonl" > "$D/AUTHORITATIVE_RESPONSE_ELIGIBLE_MEMBERSHIP_SHA256.txt"
cat > "$D/AUDIT_CONTENTS.txt" <<EOF2
INCLUDES: embedded K1 evidence/provenance review, complete III-B decision census summary, response-eligible manifest, response-entry/data-boundary lock, source/runtime/semantic provenance, status and authoritative paths/hashes.
EXCLUDES: 2307-row K2 decision map, response-eligible branch locator JSONL, K1 2307-row measurements, DEVELOPMENT NPZ arrays, S1 branch/skeleton/proposal ledgers, S1 membership copies, PF0 branch rows, DEVELOPMENT response, SEALED payloads, caches/tmp/checkpoints, upstream audit archives.
RUNTIME INPUT POLICY: this compact audit is review-only. K3/K4/K5 must read authoritative manifests/paths in the S2 run, never this audit archive.
EOF2
XZ_OPT=-9e tar -cJf "$OUT" -C "$TMP" "$(basename "$D")"
sha256sum "$OUT"
printf '%s\n' "$OUT"
