#!/usr/bin/env bash
set -euo pipefail
ROOT="${1:-$PWD}"
cd "$ROOT"
MARKER="phases/p13/coefficient_law_raw_xt/runs/LATEST_P13_S2_K6_RUN.txt"
[[ -f "$MARKER" ]] || { echo "Missing $MARKER" >&2; exit 2; }
RUN_REL=$(cat "$MARKER")
K6="$RUN_REL/K6_post_response_diagnostics"
[[ -f "$RUN_REL/K6_OVERALL_STATUS.txt" ]] || { echo "Missing K6 status" >&2; exit 2; }
TS=$(date -u +%Y%m%dT%H%M%SZ)
NAME="P13_S2_K6_POST_RESPONSE_DIAGNOSTICS_AUDIT_${TS}"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
A="$TMP/$NAME"; mkdir -p "$A"
for f in \
 K6_ENTRY_AND_K5_REVIEW.json K6_RESPONSE_DISTRIBUTIONS.json K6_CASE_DIFFICULTY_CENSUS.json \
 K6_OPERATOR_RESPONSE_ASSOCIATIONS.json K6_PF0_RESPONSE_ASSOCIATIONS_EXPLORATORY.json \
 K6_ARM_SEED_DESCRIPTIVE.json K6_CONTROL_COMPARISONS.json K6_DATA_BOUNDARY_GUARD.json \
 K6_RUNTIME_ENVIRONMENT.json K6_SOURCE_MANIFEST.json K6_SEMANTIC_OUTPUT_DIGEST.json K6_SCIENTIFIC_SUMMARY.json; do
  [[ -f "$K6/$f" ]] && cp "$K6/$f" "$A/$f"
done
cp "$RUN_REL/K6_OVERALL_STATUS.txt" "$A/K6_OVERALL_STATUS.txt"
cp "$RUN_REL/K6_NEXT_ACTION.txt" "$A/K6_NEXT_ACTION.txt"
printf '%s\n' "$RUN_REL" > "$A/AUTHORITATIVE_S2_RUN.txt"
printf '%s\n' "$K6/K6_BRANCH_DIAGNOSTICS.jsonl" > "$A/AUTHORITATIVE_K6_BRANCH_DIAGNOSTICS_PATH.txt"
sha256sum "$K6/K6_BRANCH_DIAGNOSTICS.jsonl" | awk '{print $1}' > "$A/AUTHORITATIVE_K6_BRANCH_DIAGNOSTICS_SHA256.txt"
cat > "$A/AUDIT_CONTENTS.txt" <<EOF
Compact review-only K6 audit.
Includes: parent K5 evidence lock, response/case distributions, operator/PF0 associations, control and arm/seed descriptives, data-boundary guard, provenance and semantic digest.
Excludes: full 1955-row K6 branch diagnostics, K5 measurements/decision map/pass-membership rows, response/reference arrays, candidate stores, private DEVELOPMENT payloads, all SEALED payloads, caches/checkpoints, and upstream audit copies.
Runtime input for K7 must use authoritative paths, never this audit archive.
EOF
(cd "$A" && sha256sum * > SHA256SUMS.txt)
XZ_OPT=-9e tar -cJf "$ROOT/${NAME}.tar.xz" -C "$TMP" "$NAME"
sha256sum "$ROOT/${NAME}.tar.xz"
printf '%s\n' "${NAME}.tar.xz"
