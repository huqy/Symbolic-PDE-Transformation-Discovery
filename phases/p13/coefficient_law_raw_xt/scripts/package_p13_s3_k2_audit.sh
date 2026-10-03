#!/usr/bin/env bash
set -euo pipefail

project_root="${1:-$PWD}"
cd "${project_root}"
bash phases/p13/coefficient_law_raw_xt/scripts/verify_p13_s3_k2.sh "${project_root}" >/dev/null

marker="phases/p13/coefficient_law_raw_xt/runs/LATEST_P13_S3_K2_RUN.txt"
[[ -f "${marker}" ]]
run_rel="$(tr -d '\r\n' < "${marker}")"
k1_rel="${run_rel}/K1_sealed_coefficient_operator_transfer"
k2_rel="${run_rel}/K2_sealed_operator_adjudication_response_entry_lock"
[[ "$(tr -d '[:space:]' < "${run_rel}/K2_OVERALL_STATUS.txt")" == "PASS" ]]

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
archive="P13_S3_K2_SEALED_OPERATOR_ADJUDICATION_RESPONSE_ENTRY_LOCK_AUDIT_${stamp}.tar.xz"
staging="$(mktemp -d)"
trap 'rm -rf "${staging}"' EXIT
audit_root="${staging}/audit"

mkdir -p \
  "${audit_root}/phases/p13/coefficient_law_raw_xt/configs" \
  "${audit_root}/phases/p13/coefficient_law_raw_xt/docs" \
  "${audit_root}/phases/p13/coefficient_law_raw_xt/scripts" \
  "${audit_root}/phases/p13/coefficient_law_raw_xt/src/p13rawxt" \
  "${audit_root}/phases/p13/coefficient_law_raw_xt/tests" \
  "${audit_root}/phases/p13/coefficient_law_raw_xt/patch_manifests" \
  "${audit_root}/${k1_rel}" \
  "${audit_root}/${k2_rel}" \
  "${audit_root}/phases/p13/coefficient_law_raw_xt/runs"

cp phases/p13/coefficient_law_raw_xt/configs/p13_s3_k2_protocol.json \
  "${audit_root}/phases/p13/coefficient_law_raw_xt/configs/"
cp phases/p13/coefficient_law_raw_xt/docs/P13_S3_K2_SEALED_OPERATOR_ADJUDICATION_AND_RESPONSE_ENTRY_LOCK.md \
  "${audit_root}/phases/p13/coefficient_law_raw_xt/docs/"
cp phases/p13/coefficient_law_raw_xt/scripts/run_p13_s3_k2.sh \
  "${audit_root}/phases/p13/coefficient_law_raw_xt/scripts/"
cp phases/p13/coefficient_law_raw_xt/scripts/verify_p13_s3_k2.sh \
  "${audit_root}/phases/p13/coefficient_law_raw_xt/scripts/"
cp phases/p13/coefficient_law_raw_xt/scripts/package_p13_s3_k2_audit.sh \
  "${audit_root}/phases/p13/coefficient_law_raw_xt/scripts/"
cp phases/p13/coefficient_law_raw_xt/src/p13rawxt/s3_k2_operator_adjudication.py \
  "${audit_root}/phases/p13/coefficient_law_raw_xt/src/p13rawxt/"
cp phases/p13/coefficient_law_raw_xt/tests/test_p13_s3_k2.py \
  "${audit_root}/phases/p13/coefficient_law_raw_xt/tests/"
cp phases/p13/coefficient_law_raw_xt/patch_manifests/P13_S3_K2_SEALED_OPERATOR_ADJUDICATION_RESPONSE_ENTRY_LOCK_PATCH_20260901.json \
  "${audit_root}/phases/p13/coefficient_law_raw_xt/patch_manifests/"

for name in \
  K1_ENTRY_AND_AUTHORIZATION_AUDIT.json \
  K1_COEFFICIENT_OPENING_EVENT.json \
  K1_SEALED_FINAL_COEF_INPUT_MANIFEST.json \
  K1_OPENED_COEFFICIENT_REPRODUCIBILITY_AUDIT.json \
  K1_SEALED_IDENTITY_BASELINES.json \
  K1_OPERATOR_MEASUREMENT_AGGREGATE.json \
  K1_NUMERICAL_FIDELITY_CENSUS.json \
  K1_DATA_BOUNDARY_GUARD.json \
  K1_ADJUDICATION_HANDOFF_LOCK.json \
  K1_SOURCE_AND_PARENT_MANIFEST.json \
  K1_SEMANTIC_OUTPUT_DIGEST.json \
  K1_SCIENTIFIC_SUMMARY.json; do
  cp "${k1_rel}/${name}" "${audit_root}/${k1_rel}/"
done

for name in \
  K2_ENTRY_AND_K1_EVIDENCE_REVIEW.json \
  K2_FORMAL_1955_SEALED_OPERATOR_DECISION_MAP.jsonl \
  K2_RESPONSE_ELIGIBLE_CLEAR_PASS_MEMBERSHIP.jsonl \
  K2_RESPONSE_ELIGIBLE_INPUT_MANIFEST.json \
  K2_DIAGNOSTIC_DEV_FAIL_348_OUTCOME_MAP.jsonl \
  K2_DIAGNOSTIC_DEV_UNRESOLVED_4_OUTCOME_MAP.jsonl \
  K2_FORMAL_OPERATOR_DECISION_SUMMARY.json \
  K2_DEVELOPMENT_TO_SEALED_OPERATOR_CROSSTAB.json \
  K2_NUMERICAL_FIDELITY_REVIEW.json \
  K2_CLAIM_AND_RESPONSE_ENTRY_LOCK.json \
  K2_DATA_BOUNDARY_GUARD.json \
  K2_SOURCE_AND_PARENT_MANIFEST.json \
  K2_RUNTIME_ENVIRONMENT.json \
  K2_SEMANTIC_OUTPUT_DIGEST.json \
  K2_SCIENTIFIC_SUMMARY.json; do
  cp "${k2_rel}/${name}" "${audit_root}/${k2_rel}/"
done

cp "${run_rel}/K2_OVERALL_STATUS.txt" "${audit_root}/"
cp "${run_rel}/K2_NEXT_ACTION.txt" "${audit_root}/"
cp phases/p13/coefficient_law_raw_xt/runs/LATEST_P13_S3_K2_RUN.txt \
  "${audit_root}/phases/p13/coefficient_law_raw_xt/runs/"
cp phases/p13/coefficient_law_raw_xt/runs/LATEST_P13_S3_RESPONSE_ELIGIBLE_INPUT.txt \
  "${audit_root}/phases/p13/coefficient_law_raw_xt/runs/"

python3 - "${project_root}" "${k1_rel}" "${k2_rel}" "${audit_root}/AUTHORITATIVE_INPUT_MANIFEST.json" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
k1 = (root / sys.argv[2]).resolve()
k2 = (root / sys.argv[3]).resolve()
output = Path(sys.argv[4])

def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(16 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()

config = json.loads((root / "phases/p13/coefficient_law_raw_xt/configs/p13_s3_k2_protocol.json").read_text())
k2_summary = json.loads((k2 / "K2_SCIENTIFIC_SUMMARY.json").read_text())
records = []
for name in ("formal_ledger", "diagnostic_dev_fail_ledger", "diagnostic_dev_unresolved_ledger"):
    record = next(row for row in config["parent"]["files"] if row["name"] == name)
    path = k1 / record["path"]
    records.append({
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha(path),
        "count": record["count"],
        "role": "AUTHORITATIVE_FROZEN_K1_ADJUDICATION_INPUT",
        "copied_into_audit": False,
    })
for key in ("formal_decision_map", "response_eligible_membership"):
    record = k2_summary[key]
    records.append({
        "path": record["path"],
        "bytes": record["bytes"],
        "sha256": record["sha256"],
        "count": record["count"],
        "role": "K2_OUTPUT",
        "copied_into_audit": True,
    })
output.write_text(json.dumps({
    "authoritative_inputs_and_outputs": records,
    "K1_large_operator_ledgers_copied": False,
    "K1_small_lineage_evidence_copied": True,
    "opened_SEALED_coefficient_NPZ_read_or_copied_by_K2_package": False,
    "SEALED_FINAL_RESPONSE_opened_read_hashed_or_copied": False,
    "candidate_registry_copied": False,
    "work_or_checkpoint_copied": False,
}, sort_keys=True, indent=2) + "\n")
PY

printf '%s\n' \
  'INCLUDES: K2 protocol/documentation/source/scripts/tests; compact K1 lineage and boundary evidence; complete K2 formal and diagnostic decision maps; the complete response-eligible membership; fidelity, claim, data-boundary, source and semantic locks; status and next action.' \
  'EXCLUDES: the large K1 measurement ledgers (recorded by path/count/SHA); opened SEALED coefficient NPZ objects; private coefficient archive or seed/generator material; SEALED_FINAL_RESPONSE archive/content; candidate/branch/skeleton registries; response arrays/references; work/checkpoint/cache/tmp/__pycache__; upstream archives.' \
  'RUNTIME POLICY: K2 is deterministic postprocessing. K3 must read the authoritative K2 response-eligible manifest in place only after K2 audit and explicit authorization. This audit is review evidence, not a replacement active-input archive.' \
  > "${audit_root}/AUDIT_CONTENTS.txt"

if find "${audit_root}" -type f \( -name '*.npz' -o -name '*.npy' -o -name '*.pkl' -o -name '*.pickle' \) | grep -q .; then
  echo "forbidden binary scientific payload entered K2 audit staging" >&2
  exit 2
fi

(
  cd "${audit_root}"
  find . -type f ! -name FILE_SHA256SUMS.txt -print0 | xargs -0 sha256sum > FILE_SHA256SUMS.txt
)
XZ_OPT=-9e tar -C "${audit_root}" -cJf "${archive}" .
sha256sum "${archive}"
printf '%s\n' "${archive}"
