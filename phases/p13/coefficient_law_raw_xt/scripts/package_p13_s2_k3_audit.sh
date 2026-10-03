#!/usr/bin/env bash
set -euo pipefail
ROOT="${1:-$PWD}"
cd "$ROOT"
MARKER="phases/p13/coefficient_law_raw_xt/runs/LATEST_P13_S2_K3_RUN.txt"
[[ -f "$MARKER" ]] || { echo "missing $MARKER" >&2; exit 2; }
RUN_REL="$(cat "$MARKER")"
K3="$RUN_REL/K3_response_protocol_fidelity_control_lock"
[[ -f "$RUN_REL/K3_OVERALL_STATUS.txt" ]] || { echo "missing K3 status" >&2; exit 2; }
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="P13_S2_K3_RESPONSE_PROTOCOL_FIDELITY_CONTROL_LOCK_AUDIT_${STAMP}.tar.xz"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
D="$TMP/P13_S2_K3_AUDIT_${STAMP}"
mkdir -p "$D"
for f in \
 K3_ENTRY_AND_PROVENANCE_AUDIT.json \
 K3_RESPONSE_FIDELITY_PROTOCOL_LOCK.json \
 K3_CONTROL_FIRST_AND_NONDISCRIMINATION_LOCK.json \
 K3_DATA_BOUNDARY_GUARD.json \
 K3_SOURCE_MANIFEST.json \
 K3_RUNTIME_ENVIRONMENT.json \
 K3_SEMANTIC_OUTPUT_DIGEST.json \
 K3_SCIENTIFIC_SUMMARY.json; do
  cp "$K3/$f" "$D/$f"
done
cp "$RUN_REL/K3_OVERALL_STATUS.txt" "$D/"
cp "$RUN_REL/K3_NEXT_ACTION.txt" "$D/"
printf '%s\n' "$RUN_REL" > "$D/AUTHORITATIVE_S2_RUN.txt"
printf '%s\n' "$RUN_REL/K2_III_B_adjudication_response_entry_lock/K2_III_B_DECISION_MAP.jsonl" > "$D/AUTHORITATIVE_K2_DECISION_MAP_PATH.txt"
printf '%s\n' "$RUN_REL/K2_III_B_adjudication_response_entry_lock/K2_RESPONSE_ELIGIBLE_PASS_MEMBERSHIP.jsonl" > "$D/AUTHORITATIVE_RESPONSE_ELIGIBLE_MEMBERSHIP_PATH.txt"
sha256sum "$RUN_REL/K2_III_B_adjudication_response_entry_lock/K2_RESPONSE_ELIGIBLE_PASS_MEMBERSHIP.jsonl" > "$D/AUTHORITATIVE_RESPONSE_ELIGIBLE_MEMBERSHIP_SHA256.txt"
cat > "$D/AUDIT_CONTENTS.txt" <<EOF
INCLUDES: K2 provenance/1955-cohort entry review; frozen causal/source-domain reference and candidate fidelity ladders; interval semantics; identity/NULL control-first and NONDISCRIMINATIVE_ABSOLUTE_GATE policy; DEVELOPMENT/SEALED data-boundary guard; source/runtime/semantic provenance; status and authoritative paths/hashes.
EXCLUDES: DEVELOPMENT response payload/archive, SEALED payloads, K2 decision-map rows, 1955 response-eligible membership rows, K1 measurements, DEVELOPMENT coefficient arrays, S1 candidate stores, PF0 branch rows, caches/tmp/checkpoints, upstream audit archives.
RUNTIME INPUT POLICY: this compact audit is review-only. K4/K5 must read the authoritative S2 run and frozen source/config manifests, never this audit archive.
IMPORTANT: K3 opens no response data. K4 remains blocked pending post-K3 user review/confirmation.
EOF
XZ_OPT=-9e tar -cJf "$OUT" -C "$TMP" "$(basename "$D")"
sha256sum "$OUT"
printf '%s\n' "$OUT"
