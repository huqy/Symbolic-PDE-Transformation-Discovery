#!/usr/bin/env bash
set -euo pipefail
ROOT="${1:-$PWD}"; cd "$ROOT"
MARKER="phases/p13/coefficient_law_raw_xt/runs/LATEST_P13_S3_K5_RUN.txt"
test -f "$MARKER"; RUN="$(cat "$MARKER")"; K5="$RUN/K5_post_sealed_diagnostics"
python3 - "$RUN" "$K5" <<'PY'
import collections,hashlib,json,sys
from pathlib import Path
run=Path(sys.argv[1]); k5=Path(sys.argv[2])
assert (run/'K5_OVERALL_STATUS.txt').read_text().strip()=='PASS'
summary=json.loads((k5/'K5_SCIENTIFIC_SUMMARY.json').read_text())
assert summary['OVERALL_STATUS']=='PASS' and summary['complete_diagnostic_cohort']==423
assert summary['frozen_K4_decision_counts']=={'RESPONSE_PASS':3,'RESPONSE_UNRESOLVED':0,'RESPONSE_FAIL':420}
assert summary['K4_decisions_immutable'] is True and summary['K4_pass_membership_immutable'] is True
assert summary['presentation_set_membership_authority'] is False
assert summary['top_k_or_Pareto_formal_selection'] is False and summary['proxy_filter'] is False
rows=[json.loads(x) for x in (k5/'K5_COMPLETE_423_BRANCH_DIAGNOSTICS.jsonl').read_text().splitlines() if x.strip()]
assert len(rows)==423 and len({x['scientific_branch_id'] for x in rows})==423
counts=collections.Counter(x['K4_decision'] for x in rows)
assert counts==collections.Counter({'RESPONSE_FAIL':420,'RESPONSE_PASS':3})
for row in rows:
    intervals=row['SEALED_response_intervals']
    reproduced='RESPONSE_FAIL' if any(float(x['lower'])>.15 for x in intervals) else ('RESPONSE_PASS' if all(float(x['upper'])<.15 for x in intervals) else 'RESPONSE_UNRESOLVED')
    assert reproduced==row['K4_decision']
immut=json.loads((k5/'K5_K4_DECISION_MEMBERSHIP_IMMUTABILITY_AUDIT.json').read_text())
assert immut['status']=='PASS' and immut['decision_map_modified'] is False and immut['pass_membership_modified'] is False
guard=json.loads((k5/'K5_DATA_BOUNDARY_GUARD.json').read_text())
assert guard['K4_decision_map_modified'] is False and guard['K4_pass_membership_modified'] is False
assert guard['candidate_refit'] is False and guard['proxy_filter'] is False and guard['new_response_threshold'] is False
ex=json.loads((k5/'K5_PAPER_FACING_EXEMPLARS.json').read_text())
assert ex['all_formal_K4_RESPONSE_PASS_included'] is True and ex['formal_PASS_count']==3 and ex['membership_authority'] is False
assert sum(x['K4_decision']=='RESPONSE_PASS' for x in ex['dossiers'])==3
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
assert sha(Path(summary['branch_diagnostics']))==summary['branch_diagnostics_sha256']
sem=json.loads((k5/'K5_SEMANTIC_OUTPUT_DIGEST.json').read_text())
canonical=json.dumps(sem['basis'],sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf-8')
assert hashlib.sha256(canonical).hexdigest()==sem['semantic_output_digest']==summary['semantic_output_digest']
for name,digest in sem['basis']['output_sha256'].items():
    config=json.loads((Path('phases/p13/coefficient_law_raw_xt/configs/p13_s3_k5_protocol.json')).read_text())
    assert sha(k5/config['outputs'][name])==digest
print('P13_S3_K5_VERIFY=PASS')
PY
