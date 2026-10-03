#!/usr/bin/env bash
set -euo pipefail
ROOT="${1:-$PWD}"
cd "$ROOT"
RUN_REL="$(cat phases/p13/coefficient_law_raw_xt/runs/LATEST_P13_S1_K3_RUN.txt)"
RUN="$ROOT/$RUN_REL"
D="$RUN/K3_freeze"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
NAME="P13_S1_K3_FORMAL_S1_FREEZE_AUDIT_${STAMP}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/$NAME"
for f in \
  K3_FREEZE_SUMMARY.json \
  K3_S1_SCIENTIFIC_FREEZE.json \
  K3_DATA_BOUNDARY_FREEZE.json \
  K3_ARTIFACT_REGISTRY.json \
  K3_COMPLETE_S1_REGISTRY_MANIFEST.json \
  K3_S2_ACTIVE_INPUT_MANIFEST.json \
  K3_S2_HANDOFF_MANIFEST.json \
  K3_SEMANTIC_OUTPUT_DIGEST.json \
  K3_SOURCE_MANIFEST.json; do
  cp "$D/$f" "$TMP/$NAME/$f"
done
cp "$RUN/K3_OVERALL_STATUS.txt" "$TMP/$NAME/K3_OVERALL_STATUS.txt"
cp "$RUN/K3_NEXT_ACTION.txt" "$TMP/$NAME/K3_NEXT_ACTION.txt"
cp "$ROOT/P13_COMPREHENSIVE_CONTEXT_S2_ENTRY_20260829.md" "$TMP/$NAME/P13_COMPREHENSIVE_CONTEXT_S2_ENTRY_20260829.md"
python - "$ROOT" "$RUN_REL" "$TMP/$NAME" <<'PY'
import hashlib,json,pathlib,sys
root=pathlib.Path(sys.argv[1]); run=root/sys.argv[2]; out=pathlib.Path(sys.argv[3])
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(16*1024*1024),b''): h.update(b)
 return h.hexdigest()
rels=[
 pathlib.Path(sys.argv[2])/"K2A_FROZEN_FULL_CLEAR_MEMBERSHIP.jsonl",
 pathlib.Path(sys.argv[2])/"K2A_FROZEN_FULL_UNRESOLVED_MEMBERSHIP.jsonl",
 pathlib.Path(sys.argv[2])/"K3_freeze/K3_COMPLETE_S1_REGISTRY_MANIFEST.json",
 pathlib.Path(sys.argv[2])/"K3_freeze/K3_S2_ACTIVE_INPUT_MANIFEST.json",
 pathlib.Path("P13_S1_ROLLING_CONTEXT.md"),
 pathlib.Path("P13_COMPREHENSIVE_CONTEXT_S2_ENTRY_20260829.md"),
]
rows=[]
for rel in rels:
 p=root/rel
 rows.append({'path':str(rel),'bytes':p.stat().st_size,'sha256':sha(p)})
json.dump({'authoritative_S1_run':sys.argv[2],'objects':rows,'policy':'stage-final compact audit contains freeze decisions/manifests/context plus path/SHA/bytes only. It excludes proposal/branch/skeleton/equivalence ledger contents, candidate AST/theta copies, coefficient arrays, diagnostic branch payloads, DEV/SEALED payloads, responses, caches, checkpoints, restart work, and upstream audit archives. No new S2 active-input archive is required.'},open(out/'AUTHORITATIVE_PATHS_AND_SHA.json','w'),sort_keys=True,indent=2); open(out/'AUTHORITATIVE_PATHS_AND_SHA.json','a').write('\n')
PY
(
 cd "$TMP/$NAME"
 sha256sum $(find . -maxdepth 1 -type f ! -name SHA256SUMS.txt -printf '%f\n' | LC_ALL=C sort) > SHA256SUMS.txt
)
tar -C "$TMP" -cJf "$ROOT/${NAME}.tar.xz" "$NAME"
sha256sum "$ROOT/${NAME}.tar.xz"
