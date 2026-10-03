#!/usr/bin/env bash
set -euo pipefail
ROOT="${1:-$PWD}"
cd "$ROOT"
bash phases/p13/coefficient_law_raw_xt/scripts/verify_p13_s3_k1.sh "$ROOT" >/dev/null
MARKER="phases/p13/coefficient_law_raw_xt/runs/LATEST_P13_S3_K1_RUN.txt"
[[ -f "$MARKER" ]]
RUN_REL="$(tr -d '\r\n' < "$MARKER")"
K1="$RUN_REL/K1_sealed_coefficient_operator_transfer"
[[ "$(tr -d '[:space:]' < "$RUN_REL/K1_OVERALL_STATUS.txt")" == "PASS" ]]
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="P13_S3_K1_SEALED_COEFFICIENT_OPERATOR_TRANSFER_AUDIT_${STAMP}.tar.xz"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
DEST="$TMP/audit"
mkdir -p "$DEST/phases/p13/coefficient_law_raw_xt"/{configs,docs,scripts,src/p13rawxt,tests,patch_manifests,runs/p13_s3_k0_presealed_lock/K1_sealed_coefficient_operator_transfer}
cp phases/p13/coefficient_law_raw_xt/configs/p13_s3_k1_protocol.json "$DEST/phases/p13/coefficient_law_raw_xt/configs/"
cp phases/p13/coefficient_law_raw_xt/docs/P13_S3_K1_SEALED_COEFFICIENT_OPERATOR_TRANSFER.md "$DEST/phases/p13/coefficient_law_raw_xt/docs/"
cp phases/p13/coefficient_law_raw_xt/scripts/run_p13_s3_k1.sh "$DEST/phases/p13/coefficient_law_raw_xt/scripts/"
cp phases/p13/coefficient_law_raw_xt/scripts/verify_p13_s3_k1.sh "$DEST/phases/p13/coefficient_law_raw_xt/scripts/"
cp phases/p13/coefficient_law_raw_xt/scripts/package_p13_s3_k1_audit.sh "$DEST/phases/p13/coefficient_law_raw_xt/scripts/"
cp phases/p13/coefficient_law_raw_xt/src/p13rawxt/s3_k1_sealed_operator_transfer.py "$DEST/phases/p13/coefficient_law_raw_xt/src/p13rawxt/"
cp phases/p13/coefficient_law_raw_xt/tests/test_p13_s3_k1.py "$DEST/phases/p13/coefficient_law_raw_xt/tests/"
cp phases/p13/coefficient_law_raw_xt/patch_manifests/P13_S3_K1_SEALED_COEFFICIENT_OPERATOR_TRANSFER_PATCH_20260901.json "$DEST/phases/p13/coefficient_law_raw_xt/patch_manifests/"
OUTDIR="$DEST/phases/p13/coefficient_law_raw_xt/runs/p13_s3_k0_presealed_lock/K1_sealed_coefficient_operator_transfer"
for name in \
  K1_ENTRY_AND_AUTHORIZATION_AUDIT.json \
  K1_COEFFICIENT_OPENING_EVENT.json \
  K1_SEALED_FINAL_COEF_INPUT_MANIFEST.json \
  K1_OPENED_COEFFICIENT_REPRODUCIBILITY_AUDIT.json \
  K1_SEALED_IDENTITY_BASELINES.json \
  K1_FORMAL_1955_OPERATOR_LEDGER.jsonl \
  K1_DIAGNOSTIC_DEV_FAIL_348_OPERATOR_LEDGER.jsonl \
  K1_DIAGNOSTIC_DEV_UNRESOLVED_4_OPERATOR_LEDGER.jsonl \
  K1_OPERATOR_MEASUREMENT_AGGREGATE.json \
  K1_NUMERICAL_FIDELITY_CENSUS.json \
  K1_DATA_BOUNDARY_GUARD.json \
  K1_ADJUDICATION_HANDOFF_LOCK.json \
  K1_SOURCE_AND_PARENT_MANIFEST.json \
  K1_RUNTIME_ENVIRONMENT.json \
  K1_SEMANTIC_OUTPUT_DIGEST.json \
  K1_SCIENTIFIC_SUMMARY.json; do
  cp "$K1/$name" "$OUTDIR/"
done
cp "$RUN_REL/K1_OVERALL_STATUS.txt" "$DEST/"
cp "$RUN_REL/K1_NEXT_ACTION.txt" "$DEST/"
python - "$ROOT" "$K1" "$DEST/AUTHORITATIVE_INPUT_MANIFEST.json" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
k1 = (root / sys.argv[2]).resolve()
out = Path(sys.argv[3])

def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(16 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()

summary = json.loads((k1 / "K1_SCIENTIFIC_SUMMARY.json").read_text())
opened = json.loads((k1 / "K1_SEALED_FINAL_COEF_INPUT_MANIFEST.json").read_text())
rows = []
for record in summary["ledger_manifest"].values():
    path = root / record["path"]
    rows.append({"path": record["path"], "bytes": path.stat().st_size, "sha256": sha(path), "copied_into_audit": True})
rows.append({
    "path": opened["source_archive"]["absolute_path"],
    "bytes": opened["source_archive"]["bytes"],
    "sha256": opened["source_archive"]["sha256"],
    "copied_into_audit": False,
    "role": "REFERENCE_COMMITMENT_SOURCE_AFTER_AUTHORIZED_OPENING",
})
for record in opened["search_objects"]:
    path = root / record["path"]
    rows.append({
        "path": record["path"],
        "bytes": path.stat().st_size,
        "sha256": record["sha256"],
        "semantic_digest": record["semantic_digest"],
        "copied_into_audit": False,
        "role": "AUTHORITATIVE_OPENED_SEALED_COEFFICIENT_SEARCH_OBJECT",
    })
out.write_text(json.dumps({
    "authoritative_inputs_and_ledgers": rows,
    "SEALED_FINAL_COEF_NPZ_copied": False,
    "SEALED_FINAL_RESPONSE_opened_or_copied": False,
    "candidate_registry_copied": False,
    "work_or_checkpoint_copied": False,
}, sort_keys=True, indent=2) + "\n")
PY
printf '%s\n' \
  'INCLUDES: K1 protocol/documentation/source/scripts/tests; authorization and opening audit; public opened-input manifest; complete 1955/348/4 scalar operator ledgers; identity baseline; numerical-fidelity census; governance locks; runtime/source/semantic provenance; status and next action.' \
  'EXCLUDES: opened SEALED coefficient NPZ objects; private coefficient archive and private seed/generator payload; SEALED_FINAL_RESPONSE archive/content; S1 branch/skeleton/candidate stores; DEVELOPMENT response data; work shards/checkpoints; cache/tmp/__pycache__; upstream archives.' \
  'RUNTIME POLICY: later S3 steps read the authoritative opened coefficient objects and K1 ledgers in place. This compact audit is review evidence, not a duplicated active-input archive.' \
  > "$DEST/AUDIT_CONTENTS.txt"
( cd "$DEST" && find . -type f ! -name FILE_SHA256SUMS.txt -print0 | xargs -0 sha256sum > FILE_SHA256SUMS.txt )
XZ_OPT=-9e tar -C "$DEST" -cJf "$OUT" .
sha256sum "$OUT"
printf '%s\n' "$OUT"
