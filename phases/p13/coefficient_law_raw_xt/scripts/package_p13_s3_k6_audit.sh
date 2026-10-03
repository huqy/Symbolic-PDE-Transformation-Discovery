#!/usr/bin/env bash
set -euo pipefail
ROOT="${1:-$PWD}"
cd "$ROOT"
bash phases/p13/coefficient_law_raw_xt/scripts/verify_p13_s3_k6.sh "$ROOT"
MARKER="phases/p13/coefficient_law_raw_xt/runs/LATEST_P13_S3_K6_RUN.txt"
test -f "$MARKER"
RUN="$(cat "$MARKER")"
test "$RUN" = "phases/p13/coefficient_law_raw_xt/runs/p13_s3_k0_presealed_lock"
test "$(cat "$RUN/K6_OVERALL_STATUS.txt")" = PASS
K6="$RUN/K6_final_claim_III_first_branch_freeze"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="P13_S3_K6_FINAL_CLAIM_III_P13_FIRST_BRANCH_FREEZE_AUDIT_${STAMP}.tar.xz"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
BASE="$TMP/phases/p13/coefficient_law_raw_xt"
mkdir -p "$BASE/configs" "$BASE/src/p13rawxt" "$BASE/tests" "$BASE/scripts" "$BASE/docs" "$BASE/patch_manifests" "$TMP/run" "$TMP/context"
cp phases/p13/coefficient_law_raw_xt/configs/p13_s3_k6_protocol.json "$BASE/configs/"
cp phases/p13/coefficient_law_raw_xt/src/p13rawxt/s3_k6_final_first_branch_freeze.py "$BASE/src/p13rawxt/"
cp phases/p13/coefficient_law_raw_xt/tests/test_p13_s3_k6.py "$BASE/tests/"
cp phases/p13/coefficient_law_raw_xt/scripts/run_p13_s3_k6.sh "$BASE/scripts/"
cp phases/p13/coefficient_law_raw_xt/scripts/verify_p13_s3_k6.sh "$BASE/scripts/"
cp phases/p13/coefficient_law_raw_xt/scripts/package_p13_s3_k6_audit.sh "$BASE/scripts/"
cp phases/p13/coefficient_law_raw_xt/docs/P13_S3_K6_FINAL_CLAIM_III_P13_FIRST_BRANCH_FREEZE.md "$BASE/docs/"
cp phases/p13/coefficient_law_raw_xt/patch_manifests/P13_S3_K6_FINAL_CLAIM_III_P13_FIRST_BRANCH_FREEZE_PATCH_20260902.json "$BASE/patch_manifests/"
for f in K6_ENTRY_AND_K5_REVIEW.json K6_CLAIM_III_ADJUDICATION.json K6_FORMAL_COHORT_LINEAGE.json K6_UNRESOLVED_AND_CONTROL_STATE.json K6_FINAL_CERTIFIED_TRANSFORMATION_DOSSIERS.json K6_POST_FREEZE_INTERPRETATION.json K6_FUTURE_COMPLETENESS_BOUNDARY.json K6_DATA_BOUNDARY_GUARD.json K6_AUTHORITATIVE_PATH_MANIFEST.json K6_GENERATED_ARTIFACT_REGISTRY.json K6_SOURCE_MANIFEST.json K6_SEMANTIC_OUTPUT_DIGEST.json K6_SCIENTIFIC_SUMMARY.json P13_COMPREHENSIVE_CONTEXT_FINAL_FIRST_BRANCH_FREEZE_20260902.md P13_S3_K6_PAPER_REVIEWER_HANDOFF.md; do
  cp "$K6/$f" "$TMP/run/$f"
done
cp "$RUN/K6_OVERALL_STATUS.txt" "$RUN/K6_NEXT_ACTION.txt" "$TMP/run/"
cp P13_COMPREHENSIVE_CONTEXT_FINAL_FIRST_BRANCH_FREEZE_20260902.md "$TMP/context/"
printf '%s\n' "$RUN" > "$TMP/run/AUTHORITATIVE_S3_RUN.txt"
printf '%s\n' 'Final compact P13 first-branch freeze. Includes the separately adjudicated Claim-III hierarchy, exact frozen cohort lineage, all three formal transformation dossiers, unresolved/control state, interpretation and future-completeness boundaries, canonical context, source/config/tests/scripts, manifests, and semantic digest. Excludes coefficient/response payloads, candidate measurements, 423/1955/2307-row ledgers, reference arrays/banks, registries, caches, checkpoints, and upstream archives. Large authoritative objects remain in place and are locked by path, count, SHA-256, and provenance.' > "$TMP/AUDIT_CONTENTS.txt"
SUMS_TMP="$(mktemp)"
(cd "$TMP" && find . -type f -print0 | sort -z | xargs -0 sha256sum) > "$SUMS_TMP"
mv "$SUMS_TMP" "$TMP/FILE_SHA256SUMS.txt"
XZ_OPT=-9e tar -C "$TMP" -cJf "$OUT" .
sha256sum "$OUT"
echo "$OUT"
