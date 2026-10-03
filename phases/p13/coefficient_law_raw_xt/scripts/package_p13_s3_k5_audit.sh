#!/usr/bin/env bash
set -euo pipefail
ROOT="${1:-$PWD}"; cd "$ROOT"
MARKER="phases/p13/coefficient_law_raw_xt/runs/LATEST_P13_S3_K5_RUN.txt"
test -f "$MARKER"; RUN="$(cat "$MARKER")"; K5="$RUN/K5_post_sealed_diagnostics"
test "$(cat "$RUN/K5_OVERALL_STATUS.txt")" = PASS
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="P13_S3_K5_POST_SEALED_DIAGNOSTICS_REPRESENTATIVE_ANALYSIS_AUDIT_${STAMP}.tar.xz"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
BASE="$TMP/phases/p13/coefficient_law_raw_xt"
mkdir -p "$BASE/configs" "$BASE/src/p13rawxt" "$BASE/tests" "$BASE/scripts" "$BASE/docs" "$BASE/patch_manifests" "$TMP/run"
cp phases/p13/coefficient_law_raw_xt/configs/p13_s3_k5_protocol.json "$BASE/configs/"
cp phases/p13/coefficient_law_raw_xt/src/p13rawxt/s3_k5_post_sealed_diagnostics.py "$BASE/src/p13rawxt/"
cp phases/p13/coefficient_law_raw_xt/tests/test_p13_s3_k5.py "$BASE/tests/"
cp phases/p13/coefficient_law_raw_xt/scripts/run_p13_s3_k5.sh "$BASE/scripts/"
cp phases/p13/coefficient_law_raw_xt/scripts/verify_p13_s3_k5.sh "$BASE/scripts/"
cp phases/p13/coefficient_law_raw_xt/scripts/package_p13_s3_k5_audit.sh "$BASE/scripts/"
cp phases/p13/coefficient_law_raw_xt/docs/P13_S3_K5_POST_SEALED_DIAGNOSTICS_AND_REPRESENTATIVE_ANALYSIS.md "$BASE/docs/"
cp phases/p13/coefficient_law_raw_xt/patch_manifests/P13_S3_K5_POST_SEALED_DIAGNOSTICS_REPRESENTATIVE_ANALYSIS_PATCH_20260902.json "$BASE/patch_manifests/"
for f in K5_ENTRY_AND_K4_REVIEW.json K5_COMPLETE_423_BRANCH_DIAGNOSTICS.jsonl K5_DEVELOPMENT_TO_SEALED_TRANSITIONS.json K5_FAILURE_WITNESS_CENSUS.json K5_STRUCTURE_AND_EQUIVALENCE_CLASSES.json K5_FAILURE_SIGNATURE_CLASSES.json K5_SEED_RELIABILITY_DESCRIPTIVE.json K5_COEFFICIENT_ABLATION_DESCRIPTIVE.json K5_OPERATOR_RESPONSE_ASSOCIATIONS.json K5_CASE_CONTROL_EFFECTS.json K5_DESCRIPTIVE_PARETO_VIEW.json K5_PAPER_FACING_EXEMPLARS.json K5_K4_DECISION_MEMBERSHIP_IMMUTABILITY_AUDIT.json K5_DATA_BOUNDARY_GUARD.json K5_RUNTIME_ENVIRONMENT.json K5_SOURCE_MANIFEST.json K5_SEMANTIC_OUTPUT_DIGEST.json K5_SCIENTIFIC_SUMMARY.json; do cp "$K5/$f" "$TMP/run/$f"; done
cp "$RUN/K5_OVERALL_STATUS.txt" "$RUN/K5_NEXT_ACTION.txt" "$TMP/run/"
printf '%s\n' "$RUN" > "$TMP/run/AUTHORITATIVE_S3_RUN.txt"
printf '%s\n' 'Includes the complete 423-row post-SEALED diagnostic ledger, all descriptive summaries, all three formal PASS dossiers/formulas, deterministic failure anchors, implementation/config/tests, governance guard, and semantic digest. Excludes K4 measurement arrays, reference banks, opened coefficient/response payloads, large registries, caches, and upstream archives. K6 must read authoritative HPC paths, not this audit copy.' > "$TMP/AUDIT_CONTENTS.txt"
SUMS_TMP="$(mktemp)"; (cd "$TMP" && find . -type f -print0 | sort -z | xargs -0 sha256sum) > "$SUMS_TMP"; mv "$SUMS_TMP" "$TMP/FILE_SHA256SUMS.txt"
XZ_OPT=-9e tar -C "$TMP" -cJf "$OUT" .
sha256sum "$OUT"; echo "$OUT"
