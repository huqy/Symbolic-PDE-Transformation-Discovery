#!/usr/bin/env bash
set -euo pipefail
ROOT="${1:-$PWD}"
cd "$ROOT"
RUN_REL="$(cat phases/p13/coefficient_law_raw_xt/runs/LATEST_P13_S1_PF0_RUN.txt)"
RUN="$ROOT/$RUN_REL"
D="$RUN/PF0_postfreeze"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
NAME="P13_S1_PF0_POSTFREEZE_ATTAINMENT_RADIAL_AUDIT_${STAMP}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/$NAME"
for f in \
  PF0_entry_provenance.json \
  PF0_data_boundary_guard.json \
  PF0_membership_immutability.json \
  PF0_attainment_summary.json \
  PF0_theory_geometry_aggregate.json \
  PF0_theory_gram_lock.json \
  PF0_matched_TRAIN_witness.json \
  PF0_witness_epsilon_comparator.json \
  PF0_ASP_aggregate.json \
  PF0_PRE_DEV_PREDICTIONS.json \
  PF0_scientific_summary.json \
  PF0_semantic_output_digest.json \
  PF0_source_manifest.json; do
  cp "$D/$f" "$TMP/$NAME/$f"
done
cp "$RUN/PF0_OVERALL_STATUS.txt" "$TMP/$NAME/PF0_OVERALL_STATUS.txt"
cp "$RUN/PF0_NEXT_ACTION.txt" "$TMP/$NAME/PF0_NEXT_ACTION.txt"
python - "$ROOT" "$RUN_REL" "$TMP/$NAME" <<'PY'
import hashlib,json,pathlib,sys
root=pathlib.Path(sys.argv[1]); runrel=pathlib.Path(sys.argv[2]); run=root/runrel; out=pathlib.Path(sys.argv[3])
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(16*1024*1024),b''): h.update(b)
 return h.hexdigest()
rels=[
 runrel/'K2A_FROZEN_FULL_CLEAR_MEMBERSHIP.jsonl',
 runrel/'K2A_FROZEN_FULL_UNRESOLVED_MEMBERSHIP.jsonl',
 runrel/'K3_freeze/K3_SEMANTIC_OUTPUT_DIGEST.json',
 runrel/'PF0_postfreeze/PF0_membership_attainment_rows.jsonl',
 runrel/'PF0_postfreeze/PF0_theory_geometry_rows.jsonl',
 runrel/'PF0_postfreeze/PF0_ASP_branch_results.jsonl',
 pathlib.Path('P13_S1_ROLLING_CONTEXT.md'),
]
rows=[]
for rel in rels:
 p=root/rel
 if p.is_file(): rows.append({'path':str(rel),'bytes':p.stat().st_size,'sha256':sha(p)})
obj={
 'authoritative_S1_run':str(runrel),
 'objects':rows,
 'policy':'compact PF0 audit contains protocol/provenance, aggregate attainment/theory/witness/ASP evidence, prediction lock, and path/SHA/bytes for authoritative per-branch PF0 outputs. It excludes candidate AST/theta copies, K1 ledgers, coefficient NPZ arrays, DEV/SEALED, response data, restart state, upstream audit archives, and duplicate scientific stores.'
}
json.dump(obj,open(out/'AUTHORITATIVE_PATHS_AND_SHA.json','w'),sort_keys=True,indent=2); open(out/'AUTHORITATIVE_PATHS_AND_SHA.json','a').write('\n')
PY
(
 cd "$TMP/$NAME"
 sha256sum $(find . -maxdepth 1 -type f ! -name SHA256SUMS.txt -printf '%f\n' | LC_ALL=C sort) > SHA256SUMS.txt
)
tar -C "$TMP" -cJf "$ROOT/${NAME}.tar.xz" "$NAME"
sha256sum "$ROOT/${NAME}.tar.xz"
