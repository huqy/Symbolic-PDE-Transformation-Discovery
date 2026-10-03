#!/usr/bin/env bash
set -euo pipefail
ROOT="${1:-$PWD}"
cd "$ROOT"
MARKER="phases/p13/coefficient_law_raw_xt/runs/LATEST_P13_S1_K2B_RUN.txt"
RUN="$(cat "$MARKER")"
[[ -d "$RUN" ]] || { echo "missing run: $RUN" >&2; exit 2; }
STATUS="$(cat "$RUN/K2B_OVERALL_STATUS.txt")"
[[ "$STATUS" == "PASS" || "$STATUS" == "FAIL" ]] || { echo "invalid K2B status" >&2; exit 2; }
TS="$(date -u +%Y%m%dT%H%M%SZ)"
NAME="P13_S1_K2B_POST_SEARCH_DIAGNOSTICS_AUDIT_${TS}"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/$NAME"
D="$RUN/K2B_diagnostics"
for f in K2B_entry_provenance.json K2B_diagnostic_opening_receipt.json K2B_data_boundary_guard.json K2B_membership_immutability.json K2B_diagnostic_aggregate.json K2B_hit_rate_vs_parent_J.json K2B_route_interpretation_lock.json K2B_fairness_carryforward.json K2B_scientific_summary.json K2B_semantic_output_digest.json K2B_source_manifest.json; do
  [[ -f "$D/$f" ]] && cp "$D/$f" "$TMP/$NAME/$f"
done
cp "$RUN/K2B_OVERALL_STATUS.txt" "$TMP/$NAME/"
cp "$RUN/K2B_NEXT_ACTION.txt" "$TMP/$NAME/"
python - "$ROOT" "$RUN" "$TMP/$NAME/AUTHORITATIVE_PATHS_AND_SHA.json" <<'PY'
import hashlib,json,sys
from pathlib import Path
root=Path(sys.argv[1]).resolve(); run=root/Path(sys.argv[2]); out=Path(sys.argv[3])
def info(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(16*1024*1024),b''): h.update(b)
 return {'path':str(p.relative_to(root)),'bytes':p.stat().st_size,'sha256':h.hexdigest()}
rows=[]
for rel in ['K2A_FROZEN_FULL_CLEAR_MEMBERSHIP.jsonl','K2A_FROZEN_FULL_UNRESOLVED_MEMBERSHIP.jsonl','K2B_diagnostics/K2B_diagnostic_branch_results.jsonl','K2B_diagnostics/K2B_diagnostic_identity_baselines.json']:
 p=run/rel
 if p.is_file(): rows.append(info(p))
rc=root/'P13_S1_ROLLING_CONTEXT.md'
if rc.is_file(): rows.append(info(rc))
out.write_text(json.dumps({'authoritative_S1_run':str(run.relative_to(root)),'objects':rows,'policy':'audit contains compact K2B diagnostic summaries, governance/decision evidence, and path/SHA/bytes for authoritative membership and branch-diagnostic results. It excludes candidate AST/theta copies, proposal/branch/skeleton ledgers, coefficient NPZ payloads, private within-family archive contents/seeds, DEV/SEALED, responses, caches, restart work, and upstream audits.'},indent=2,sort_keys=True)+'\n')
PY
( cd "$TMP/$NAME" && find . -type f ! -name SHA256SUMS.txt -print0 | xargs -0 sha256sum > SHA256SUMS.txt )
XZ_OPT=-9e tar -cJf "$ROOT/${NAME}.tar.xz" -C "$TMP" "$NAME"
sha256sum "$ROOT/${NAME}.tar.xz"
