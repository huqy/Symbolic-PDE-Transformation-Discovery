"""Fresh S3 provenance, roles and claim evidence; no scientific kernels."""
from pathlib import Path
from .common import digest, file_sha256
from .s0_contract import P, load, write
from .s1_lineage import seal, unseal
from .s2_contract import rows, write_rows, record, verify_record, exact_ids, OP, RESP
from . import s2_contract as parent

RUN=P+'runs/fresh_s3'
STEPS=tuple('K'+str(i) for i in range(7))
DIRS=('K0_presealed_lock','K1_sealed_coefficient_operator_transfer',
      'K2_sealed_operator_adjudication_response_entry_lock','K3_sealed_response_reference_control_first',
      'K4_complete_candidate_response_certification','K5_post_sealed_diagnostics','K6_final_first_branch_freeze')
MODULES=('s3_k0_presealed_lock','s3_k1_sealed_operator_transfer','s3_k2_operator_adjudication',
         's3_k3_sealed_response_reference_controls','s3_k4_candidate_response_certification',
         's3_k5_post_sealed_diagnostics','s3_k6_final_first_branch_freeze')
STRATA=('FORMAL','DIAGNOSTIC_DEV_FAIL','DIAGNOSTIC_DEV_UNRESOLVED')
SEALED_OP=('SEALED_OPERATOR_PASS','SEALED_OPERATOR_UNRESOLVED','SEALED_OPERATOR_FAIL')

def handoff(root, full=False):
    """Only fresh execution/receipts/decision metadata; never inspect archives."""
    root=Path(root).resolve(strict=True); project=root/'project'; ctx=parent.state(project)
    if full:
        for rel,row in ctx['source_files'].items():
            p=project/rel
            if file_sha256(p)!=row['sha256'] or p.stat().st_size!=row['bytes']:raise ValueError('S2 source snapshot drift')
    receipts={}
    for s in parent.STEPS:
        if full: r=parent.receipt(project,s)
        else:
            r=unseal(load(root/'receipts'/(s+'.json')))
            if r['step']!=s or r['execution_id']!=ctx['execution_id'] or r['parents']!={k:v['digest'] for k,v in receipts.items()} or r['S1_parent']!=ctx['s1']['PF1_receipt_digest']:
                raise ValueError('mixed/incomplete S2 lineage')
        receipts[s]=r
    def bound(s,name):
        p=parent.stage_dir(project,s)/name
        r=next((r for r in receipts[s]['outputs'] if r['path']==str(p)),None)
        if r is None: raise ValueError('S2 interface not receipt-bound: '+name)
        verify_record(r); return p
    pins=[record(root/'s2_execution.json'),*[record(root/'receipts'/(s+'.json')) for s in parent.STEPS]]
    names=[('K7','K7_SCIENTIFIC_SUMMARY.json'),('K7','K7_S3_DECISION_LOCK.json'),
           ('K5','K5_RESPONSE_PASS_MEMBERSHIP.jsonl'),('K5','K5_RESPONSE_DECISION_MAP.jsonl'),
           ('K2','K2_III_B_DECISION_MAP.jsonl'),('K4','K4_SCIENTIFIC_SUMMARY.json')]
    paths={name:bound(s,name) for s,name in names}; pins += [record(p) for p in paths.values()]
    summary=load(paths['K7_SCIENTIFIC_SUMMARY.json']); lock=load(paths['K7_S3_DECISION_LOCK.json'])
    if summary['OVERALL_STATUS']!='PASS' or summary['SEALED_opened'] is not False or lock['opening_performed'] is not False:
        raise ValueError('S2 is incomplete or SEALED already opened/authorized')
    formal=rows(paths['K5_RESPONSE_PASS_MEMBERSHIP.jsonl']); responses=rows(paths['K5_RESPONSE_DECISION_MAP.jsonl'])
    ops=rows(paths['K2_III_B_DECISION_MAP.jsonl']); exact_ids(ops,ops)
    exact_ids(formal,[r for r in responses if r['decision']==RESP[0]])
    counts={k:sum(r['III_B_decision']==k for r in ops) for k in OP}
    rc={k:sum(r['decision']==k for r in responses) for k in RESP}
    if counts!=summary['operator_counts'] or rc!=summary['response_counts']:
        raise ValueError('S2 recorded census drift')
    if len(formal)!=lock['eligible_count_if_authorized'] or file_sha256(paths['K5_RESPONSE_PASS_MEMBERSHIP.jsonl'])!=lock['eligible_membership_sha256']:
        raise ValueError('S2 formal membership lock drift')
    exact_ids(responses,[r for r in ops if r['III_B_decision']==OP[0]])
    byid={r['scientific_branch_id']:r for r in ops}; formalids={r['scientific_branch_id'] for r in formal}
    if any(byid[r['scientific_branch_id']]['III_B_decision']!=OP[0] for r in formal): raise ValueError('non-operator-PASS in formal cohort')
    # All fresh S2 operator-PASS branches passed response. The historical
    # three-stratum protocol defines no role for operator-PASS/response-nonPASS.
    # Refuse to invent a fourth stratum if a different parent ever requires it.
    if formalids!={r['scientific_branch_id'] for r in ops if r['III_B_decision']==OP[0]}:
        raise ValueError('scientific role unresolved: S2 operator-PASS / response-nonPASS')
    groups={STRATA[0]:[dict(byid[r['scientific_branch_id']],stratum=STRATA[0],formal_membership_authority=True) for r in formal]}
    for label,decision in zip(STRATA[1:],(OP[2],OP[1])):
        groups[label]=[dict(r,stratum=label,formal_membership_authority=False) for r in ops if r['III_B_decision']==decision]
    controls=load(paths['K4_SCIENTIFIC_SUMMARY.json'])
    if controls['reference_certified_count']!=32 or any(controls[k]!=RESP[2] for k in ('identity_decision','frozen_null_decision')): raise ValueError('S2 controls/reference incomplete')
    header={'root':str(root),'execution_id':ctx['execution_id'],'source_commit':ctx['source_commit'],
            'K7_receipt_digest':receipts['K7']['digest'],'formal_count':len(formal),
            'diagnostic_FAIL_count':len(groups[STRATA[1]]),'diagnostic_UNRESOLVED_count':len(groups[STRATA[2]]),
            'S1':ctx['s1'],'pins':pins,'operator_counts':counts,'response_counts':rc}
    return header,groups

def stage_dir(project,step): return Path(project)/RUN/DIRS[STEPS.index(step)]
def state(project): return unseal(load(Path(project).parent/'s3_execution.json'))
def receipt(project,step):
    ctx=state(project); r=unseal(load(Path(project).parent/'receipts'/(step+'.json')))
    parents={s:receipt(project,s)['digest'] for s in STEPS[:STEPS.index(step)]}
    if r['step']!=step or r['execution_id']!=ctx['execution_id'] or r['parents']!=parents or r['S2_parent']!=ctx['s2']['K7_receipt_digest']:
        raise ValueError('mixed/incomplete S3 receipt lineage')
    for row in r['outputs']: verify_record(row)
    return r

def finish(project,step):
    ctx=state(project); d=stage_dir(project,step); summary=load(d/(step+'_SCIENTIFIC_SUMMARY.json'))
    sem=load(d/(step+'_SEMANTIC_OUTPUT_DIGEST.json')); parents={s:receipt(project,s)['digest'] for s in STEPS[:STEPS.index(step)]}
    if summary['OVERALL_STATUS']!='PASS' or sem['semantic_output_digest']!=summary['semantic_output_digest'] or digest(sem['basis'])!=sem['semantic_output_digest']:
        raise ValueError('unsealed/incomplete stage')
    if load(d/'REPRODUCTION_STAGE_COMPLETE.json')!={'schema':'P13_S3_COMPLETE_MARKER_V1','execution_id':ctx['execution_id'],'stage':step,'semantic_output_digest':sem['semantic_output_digest'],'parents':parents}:
        raise ValueError('completion marker drift')
    obj=seal({'schema':'P13_S3_RECEIPT_V1','execution_id':ctx['execution_id'],'step':step,
              'parents':parents,'S2_parent':ctx['s2']['K7_receipt_digest'],'semantic_digest':sem['semantic_output_digest'],
              'outputs':[record(p) for p in sorted(d.rglob('*')) if p.is_file() and p.relative_to(d).parts[0] not in ('work','opening_work')],
              'historical_outcomes_used':False})
    path=Path(project).parent/'receipts'/(step+'.json')
    if path.exists() and load(path)!=obj: raise ValueError('immutable receipt overwrite')
    write(path,obj); return obj

def claim_evidence(op,resp,controls,complete,response_stop=None):
    contradictory=(op.get(SEALED_OP[0],0)==0 and op.get(SEALED_OP[1],0)==0) or response_stop=='NONDISCRIMINATIVE' or any(v.get('decision')==RESP[0] for v in controls.values()) or (complete and resp.get(RESP[0],0)==0 and resp.get(RESP[1],0)==0)
    unresolved=(op.get(SEALED_OP[0],0)==0 and op.get(SEALED_OP[1],0)>0) or (not complete and not contradictory) or not all(controls.get(k,{}).get('decision')==RESP[2] for k in ('identity','frozen_null')) or (complete and resp.get(RESP[0],0)==0 and resp.get(RESP[1],0)>0)
    return {'status':'CLAIM_CONTRADICTED_CANDIDATE' if contradictory else 'UNRESOLVED' if unresolved else 'CLAIM_COMPATIBLE_CANDIDATE',
            'complete_response_certification':complete,'nonempty_formal_operator_PASS':op.get(SEALED_OP[0],0)>0,
            'nonempty_formal_response_PASS':resp.get(RESP[0],0)>0,'controls':controls,
            'external_claim_adjudication':'PENDING_EXTERNAL_POST_COMPUTATION_AUDIT','R_C':'NOT_AUTOMATICALLY_ASSIGNED',
            'R_D':'OPTIONAL_REPORTED_SEPARATELY','diagnostic_promotion':False,'membership_authority':False,
            'historical_reproduction_is_new_sealed_holdout_validation':False}
