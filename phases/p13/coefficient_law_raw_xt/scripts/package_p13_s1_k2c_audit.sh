#!/usr/bin/env bash
set -euo pipefail
ROOT="${1:-$PWD}"
cd "$ROOT"
RUN_REL="$(cat phases/p13/coefficient_law_raw_xt/runs/LATEST_P13_S1_K2C_RUN.txt)"
RUN="$ROOT/$RUN_REL"
D="$RUN/K2C_theory_bridge"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
NAME="P13_S1_K2C_POST_MEMBERSHIP_THEORY_BRIDGE_AUDIT_${STAMP}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/$NAME"
for f in \
  K2C_entry_provenance.json \
  K2C_data_boundary_guard.json \
  K2C_membership_immutability.json \
  K2C_epsilon_shape_manifest.json \
  K2C_alignment_basis_lock.json \
  K2C_aggregate.json \
  K2C_deferred_trigger_adjudication.json \
  K2C_S2_MECHANISTIC_PREDICTIONS.json \
  K2C_scientific_summary.json \
  K2C_semantic_output_digest.json \
  K2C_source_manifest.json; do
  cp "$D/$f" "$TMP/$NAME/$f"
done
cp "$RUN/K2C_OVERALL_STATUS.txt" "$TMP/$NAME/K2C_OVERALL_STATUS.txt"
cp "$RUN/K2C_NEXT_ACTION.txt" "$TMP/$NAME/K2C_NEXT_ACTION.txt"
python - "$ROOT" "$RUN_REL" "$TMP/$NAME" <<'PY'
import hashlib,json,pathlib,sys
root=pathlib.Path(sys.argv[1]); run=root/sys.argv[2]; out=pathlib.Path(sys.argv[3])
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(16*1024*1024),b''): h.update(b)
 return h.hexdigest()
paths=[]
for rel in [
 pathlib.Path(sys.argv[2])/"K2A_FROZEN_FULL_CLEAR_MEMBERSHIP.jsonl",
 pathlib.Path(sys.argv[2])/"K2A_FROZEN_FULL_UNRESOLVED_MEMBERSHIP.jsonl",
 pathlib.Path(sys.argv[2])/"K2C_theory_bridge/K2C_branch_results.jsonl",
 pathlib.Path(sys.argv[2])/"K2C_theory_bridge/K2C_S2_MECHANISTIC_PREDICTIONS.json",
 pathlib.Path("P13_S1_ROLLING_CONTEXT.md")]:
 p=root/rel
 paths.append({'path':str(rel),'bytes':p.stat().st_size,'sha256':sha(p)})
json.dump({'authoritative_S1_run':sys.argv[2],'objects':paths,'policy':'compact K2C audit contains protocol/provenance, membership lock, aggregate theory-bridge evidence, prediction freeze, and authoritative path/SHA/bytes only. It excludes branch-result payload, candidate AST/theta copies, coefficient arrays, K1 ledgers/checkpoints, K2B diagnostic payload, DEV/SEALED, responses, caches, restart work, and upstream audits.'},open(out/'AUTHORITATIVE_PATHS_AND_SHA.json','w'),sort_keys=True,indent=2); open(out/'AUTHORITATIVE_PATHS_AND_SHA.json','a').write('\n')
PY
(
 cd "$TMP/$NAME"
 sha256sum $(find . -maxdepth 1 -type f ! -name SHA256SUMS.txt -printf '%f\n' | LC_ALL=C sort) > SHA256SUMS.txt
)
tar -C "$TMP" -cJf "$ROOT/${NAME}.tar.xz" "$NAME"
sha256sum "$ROOT/${NAME}.tar.xz"
