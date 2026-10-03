#!/usr/bin/env bash
set -euo pipefail
ROOT="${1:-$PWD}"
cd "$ROOT"
RUN_REL="$(cat phases/p13/coefficient_law_raw_xt/runs/LATEST_P13_S1_PF1_RUN.txt)"
RUN="$ROOT/$RUN_REL"
D="$RUN/PF1_final_freeze"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
NAME="P13_S1_PF1_FINAL_STAGE_FREEZE_AUDIT_${STAMP}"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/$NAME"
for f in PF1_FREEZE_SUMMARY.json PF1_FINAL_S1_SCIENTIFIC_FREEZE.json PF1_DATA_BOUNDARY_FREEZE.json PF1_S2_ACTIVE_INPUT_MANIFEST.json PF1_S2_PROTOCOL_ROADMAP.json PF1_SOURCE_MANIFEST.json PF1_HANDOFF_MANIFEST.json PF1_SEMANTIC_OUTPUT_DIGEST.json; do cp "$D/$f" "$TMP/$NAME/$f"; done
cp "$RUN/PF1_OVERALL_STATUS.txt" "$TMP/$NAME/"
cp "$RUN/PF1_NEXT_ACTION.txt" "$TMP/$NAME/"
cp "$ROOT/P13_COMPREHENSIVE_CONTEXT_S1_FINAL_FREEZE_20260831.md" "$TMP/$NAME/"
cp "$ROOT/P13_COMPREHENSIVE_CONTEXT_S2_ENTRY_PF0_LOCK_20260831.md" "$TMP/$NAME/"
python - "$ROOT" "$RUN_REL" "$TMP/$NAME" <<'PY'
import hashlib,json,pathlib,sys
root=pathlib.Path(sys.argv[1]); run=root/sys.argv[2]; out=pathlib.Path(sys.argv[3])
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(16*1024*1024),b''): h.update(b)
 return h.hexdigest()
rels=[
 pathlib.Path(sys.argv[2])/'K2A_FROZEN_FULL_CLEAR_MEMBERSHIP.jsonl',
 pathlib.Path(sys.argv[2])/'K2A_FROZEN_FULL_UNRESOLVED_MEMBERSHIP.jsonl',
 pathlib.Path(sys.argv[2])/'PF0_postfreeze/PF0_scientific_summary.json',
 pathlib.Path(sys.argv[2])/'PF0_postfreeze/PF0_semantic_output_digest.json',
 pathlib.Path('P13_S1_ROLLING_CONTEXT.md'),
 pathlib.Path('P13_COMPREHENSIVE_CONTEXT_S1_FINAL_FREEZE_20260831.md'),
 pathlib.Path('P13_COMPREHENSIVE_CONTEXT_S2_ENTRY_PF0_LOCK_20260831.md')]
rows=[]
for rel in rels:
 p=root/rel; rows.append({'path':str(rel),'bytes':p.stat().st_size,'sha256':sha(p)})
json.dump({'authoritative_S1_run':sys.argv[2],'objects':rows,'policy':'Compact PF1 audit contains final decisions/contexts/manifests plus path/SHA/bytes. It excludes full ledgers, candidate payload copies, coefficient arrays, PF0 per-branch payloads, DEV/SEALED, responses, caches, restart state, and upstream audits.'},open(out/'AUTHORITATIVE_PATHS_AND_SHA.json','w'),sort_keys=True,indent=2); open(out/'AUTHORITATIVE_PATHS_AND_SHA.json','a').write('\n')
PY
(
 cd "$TMP/$NAME"
 sha256sum $(find . -maxdepth 1 -type f ! -name SHA256SUMS.txt -printf '%f\n' | LC_ALL=C sort) > SHA256SUMS.txt
)
tar -C "$TMP" -cJf "$ROOT/${NAME}.tar.xz" "$NAME"
sha256sum "$ROOT/${NAME}.tar.xz"
