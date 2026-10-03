"""S0 transport contract. No numerical imports or scientific execution."""
import json
from pathlib import Path
from .common import contained, file_sha256, digest

P='phases/p13/coefficient_law_raw_xt/'
STEPS=('K0','K1','K2','K2R2','K2R3','K2R4','K3')
MODULES=dict(zip(STEPS,('k0_lock','k1_commit','k2_qualification','k2r2_attainment_repair','k2r3_capacity_continuation','k2r4_operational_fitter','k3_s0_freeze')))
DEPS={step:STEPS[:i] for i,step in enumerate(STEPS)}
def run_relative(step):
    if step not in STEPS:raise ValueError('unknown S0 step')
    return P+'runs/fresh_s0_'+step.lower()
def marker_relative(step):return P+'runs/LATEST_P13_S0_'+step+'_RUN.txt'
def load(path):return json.loads(Path(path).read_text())
def write(path,obj):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.writing');temp.write_text(json.dumps(obj,indent=2,sort_keys=True,allow_nan=False)+'\n');temp.replace(path)

def context(root):
    root=Path(root).absolute()
    if any(p.is_symlink() for p in [root,*root.parents]):raise ValueError('symlink execution namespace')
    root=root.resolve(strict=True)
    cfg=load(contained(root,'.s0_context.json',True))
    lock=load(contained(root.parent,'execution_lock.json',True))
    if cfg['schema']!='P13_S0_CONTEXT_V1' or lock['schema']!='P13_S0_EXECUTION_LOCK_V1' or cfg['execution_id']!=lock['execution_id'] or root.name!='project':
        raise ValueError('execution identity mismatch')
    return root,lock

def active(root):return (Path(root)/'.s0_context.json').is_file()

def parent(root,step):
    root,lock=context(root)
    receipt=load(contained(root.parent,'receipts/'+step+'.json',True))
    if receipt['execution_id']!=lock['execution_id'] or receipt['step']!=step or receipt['run_relative']!=run_relative(step):
        raise ValueError('mixed/stale stage identity')
    payload={k:v for k,v in receipt.items() if k!='receipt_digest'}
    if digest(payload)!=receipt['receipt_digest']:raise ValueError('receipt digest mismatch')
    if set(receipt['parent_receipt_digests'])!=set(DEPS[step]):raise ValueError('missing/extra fresh dependency')
    run=contained(root,run_relative(step),True)
    for rel,row in receipt['artifacts'].items():
        p=contained(run,rel,True)
        if p.stat().st_size!=row['bytes'] or file_sha256(p)!=row['sha256']:raise ValueError('fresh artifact changed: '+rel)
    if load(run/'semantic_output_digest.json')['semantic_output_digest']!=receipt['semantic_output_digest']:
        raise ValueError('fresh semantic digest does not match receipt')
    for prior,expected in receipt['parent_receipt_digests'].items():
        prev=load(contained(root.parent,'receipts/'+prior+'.json',True))
        if prev['execution_id']!=lock['execution_id'] or prev['receipt_digest']!=expected:raise ValueError('parent receipt chain mismatch')
    return run

def semantic(root,step,historical):
    if not active(root):return historical
    return load(parent(root,step)/'semantic_output_digest.json')['semantic_output_digest']

def fresh_parent_k0(root,protocol):
    run=parent(root,'K0');summary=load(run/'audit_summary.json');deriv=load(run/'derivative_semantics_guard.json')
    checks={'overall_status':summary['OVERALL_STATUS']=='PASS',
            'numeric_L3_guard':deriv['numeric_L3_execution_status']==protocol['parent']['numeric_L3_execution_status'] and deriv['blocking_for_formal_search'] is True,
            'fresh_lineage':True}
    return {'mode':'HISTORICAL_REPLAY','status':'PASS' if all(checks.values()) else 'FAIL','checks':checks,
            'observed_semantic_output_digest':load(run/'semantic_output_digest.json')['semantic_output_digest']}

def fresh_verify_k1(root,protocol):
    run=parent(root,'K1');s=load(run/'audit_summary.json');sem=load(run/'semantic_output_digest.json');leak=load(run/'no_leakage_guard.json')
    private=load(run/'private_payload_commitments.json')
    checks={'overall_status':s['OVERALL_STATUS']=='PASS','protocol_sha':sem['protocol_sha256']==protocol['parent_k1']['expected_k1_protocol_sha256'],
            'k1_leakage_pass':leak['status']=='PASS' and not any(leak[k] for k in ['historical_response_outcomes_read','formal_candidate_search_run','development_or_sealed_response_solve_run']),
            'formal_search_still_disabled':s['formal_search_authorized'] is False,'L3_guard_carried':s['numeric_L3_execution_status']=='NOT_YET_QUALIFIED',
            'identifiability_k1_pass':s['identifiability_status']=='PASS','private_commitments_present':len(private)==4,
            'private_not_copied':all(x['private_payload_copied_into_active_tree'] is False for x in private.values())}
    if not all(checks.values()):raise RuntimeError('fresh K1 scientific prerequisites fail: '+str(checks))
    return run,{'status':'PASS','k1_run':run_relative('K1'),'checks':checks,'semantic':sem['semantic_output_digest'],'protocol_sha256':sem['protocol_sha256']}

def repair_parent(root,step):
    previous={'K2R2':'K2','K2R3':'K2R2','K2R4':'K2R3'}[step]
    runs={s:parent(root,s) for s in DEPS[step]}
    s=load(runs[previous]/'audit_summary.json');leak=load(runs[previous]/'no_leakage_guard.json')
    if leak['status']!='PASS' or s['formal_S1_search_authorized'] is not False:raise RuntimeError('fresh repair data-boundary prerequisite failed')
    record={'status':'PASS','mode':'FRESH_EXECUTION_PROVENANCE','checks':{'fresh_receipts':True,'no_leakage':True,'formal_search_disabled':True}}
    for k,p in runs.items():record[k+'_run']=str(p.relative_to(root));record[k+'_semantic']=load(p/'semantic_output_digest.json')['semantic_output_digest']
    # Historic outcome/digest equality is a comparison, not execution selection.
    # All numerical/calibration prerequisites inside the repair are unchanged.
    if step=='K2R2':return runs['K2'],runs['K1'],s,record
    if step=='K2R3':return runs['K2R2'],runs['K2'],runs['K1'],record
    return runs['K2R3'],runs['K2R2'],runs['K2'],runs['K1'],record

def resolve_fresh_marker(root,marker_rel):
    for step in STEPS:
        if marker_rel==marker_relative(step):return parent(root,step)
    raise ValueError('unrecognized S0 marker')

def resolve_fresh_run(root,rel):
    for step in STEPS:
        if rel==run_relative(step):return parent(root,step)
    raise ValueError('historical/absolute/unknown run is not a fresh dependency')

def k3_parent_checks(root):
    run=parent(root,'K2R4');s=load(run/'audit_summary.json');h=load(run/'k3_handoff_manifest.json');l=load(run/'no_leakage_guard.json')
    return {'fresh_receipt':True,'formal_S1_disabled':s['formal_S1_search_authorized'] is False,
            'handoff_requires_K3':h['K3_required'] is True,'burnin_not_S1_seed':h['burnin_candidates_eligible_for_S1'] is False,'no_leakage':l['status']=='PASS'}

def replay_commitments(root):
    root,lock=context(root);data=load(contained(root,'s0_commitment_replay.json',True))
    if data['execution_id']!=lock['execution_id'] or data['mode']!='HISTORICAL_REPLAY':raise ValueError('replay identity mismatch')
    if file_sha256(root/'s0_commitment_replay.json')!=lock['commitment_replay_sha256']:raise ValueError('commitment replay changed')
    return data['commitments']

def replay_role_separation(open_registry,commitments,protocol):
    # IDs come only from the manifest's frozen public commitment headers.
    from p13rawxt.k1_commit import _role_separation
    projected={k:dict(v) for k,v in commitments.items()}
    for role in ['DEVELOPMENT_COEF','SEALED_FINAL_COEF']:
        projected['coefficient:'+role]['public_row_commitments']=[{'field_id':fid} for fid in commitments['coefficient:'+role]['committed_field_ids']]
    return _role_separation(open_registry,projected,protocol)

def freeze_text(adjud,evidence):
    return '# Fresh S0 freeze\n\nComputed adjudication (no historical expected decision):\n\n```json\n'+json.dumps({'adjudication':adjud,'evidence':evidence},indent=2,sort_keys=True)+'\n```\n\nCalibration-only evidence; no S1 execution is started.\n'
