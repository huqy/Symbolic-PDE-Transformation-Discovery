#!/usr/bin/env bash
set -euo pipefail
ROOT="${1:-$PWD}"
cd "$ROOT"
RUN_REL="$(cat phases/p13/coefficient_law_raw_xt/runs/LATEST_P13_S2_K0_RUN.txt)"
RUN="$ROOT/$RUN_REL"
D="$RUN/K0_development_open_lock"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
NAME="P13_S2_K0_DEVELOPMENT_COEFFICIENT_OPENING_PROTOCOL_LOCK_AUDIT_${STAMP}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/$NAME"
for f in \
  K0_ENTRY_AUDIT.json \
  K0_MEMBERSHIP_PROVENANCE_LOCK.json \
  K0_DEVELOPMENT_COEFFICIENT_OPEN_MANIFEST.json \
  K0_DATA_BOUNDARY_GUARD.json \
  K0_S2_OPERATOR_TRANSFER_PROTOCOL_LOCK.json \
  K0_RUNTIME_ENVIRONMENT.json \
  K0_SOURCE_MANIFEST.json \
  K0_SEMANTIC_OUTPUT_DIGEST.json \
  K0_SCIENTIFIC_SUMMARY.json; do
  cp "$D/$f" "$TMP/$NAME/$f"
done
cp "$RUN/K0_OVERALL_STATUS.txt" "$TMP/$NAME/K0_OVERALL_STATUS.txt"
cp "$RUN/K0_NEXT_ACTION.txt" "$TMP/$NAME/K0_NEXT_ACTION.txt"
python - "$ROOT" "$RUN_REL" "$TMP/$NAME" <<'PY'
import hashlib,json,pathlib,sys
root=pathlib.Path(sys.argv[1]); run_rel=pathlib.Path(sys.argv[2]); out=pathlib.Path(sys.argv[3])
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(16*1024*1024),b''): h.update(b)
 return h.hexdigest()
def obj(rel,role,count=None):
 p=root/rel; d={'path':str(rel),'role':role,'bytes':p.stat().st_size,'sha256':sha(p)}
 if count is not None: d['count']=count
 return d
m=json.load(open(root/run_rel/'K0_development_open_lock/K0_DEVELOPMENT_COEFFICIENT_OPEN_MANIFEST.json'))
objects=[
 obj(pathlib.Path('phases/p13/coefficient_law_raw_xt/runs/p13_s1_formal_search_20260827T200612Z/K2A_FROZEN_FULL_CLEAR_MEMBERSHIP.jsonl'),'AUTHORITATIVE_S2_MEMBERSHIP',2307),
 obj(pathlib.Path('phases/p13/coefficient_law_raw_xt/runs/p13_s1_formal_search_20260827T200612Z/K2A_FROZEN_FULL_UNRESOLVED_MEMBERSHIP.jsonl'),'REFERENCE_UNRESOLVED_LINEAGE',80),
 obj(run_rel/'K0_development_open_lock/K0_DEVELOPMENT_COEFFICIENT_OPEN_MANIFEST.json','ACTIVE_K1_INPUT_MANIFEST'),
 obj(pathlib.Path('P13_S2_ROLLING_CONTEXT.md'),'S2_ROLLING_REFERENCE_CONTEXT')
]
opened=[]
for r in m['search_objects']:
 p=root/r['path']; opened.append({'path':r['path'],'bytes':p.stat().st_size,'sha256':sha(p),'semantic_digest':r['semantic_digest'],'field_id':r['field_id'],'grid':r['grid']})
json.dump({
 'authoritative_K0_run':str(run_rel),
 'objects':objects,
 'opened_DEVELOPMENT_search_objects':opened,
 'audit_policy':'Compact review-only audit. It contains protocol/provenance/data-boundary locks and path/SHA/semantic references. It excludes all opened DEVELOPMENT NPZ arrays, S1 candidate/branch/skeleton/proposal ledgers, membership copies, private coefficient archive, private generator/seed material, DEVELOPMENT response, SEALED payloads, PF0 large rows, caches, tmp, and upstream audits.',
 'runtime_input_policy':'K1 reads K0 opened DEVELOPMENT search objects and S1 authoritative membership in place. This audit archive is never a runtime input.'
},open(out/'AUTHORITATIVE_PATHS_AND_SHA.json','w'),sort_keys=True,indent=2); open(out/'AUTHORITATIVE_PATHS_AND_SHA.json','a').write('\n')
PY
(
 cd "$TMP/$NAME"
 sha256sum $(find . -maxdepth 1 -type f ! -name SHA256SUMS.txt -printf '%f\n' | LC_ALL=C sort) > SHA256SUMS.txt
)
tar -C "$TMP" -cJf "$ROOT/${NAME}.tar.xz" "$NAME"
sha256sum "$ROOT/${NAME}.tar.xz"
