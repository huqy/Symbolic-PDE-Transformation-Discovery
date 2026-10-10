"""Additive historical replay administration; metadata only, no science or archives."""
from pathlib import Path
from .common import SOURCE_ROOT, contained, digest, file_sha256
from .s0_contract import load
from . import s0_contract as s0, s1_lineage as s1, s2_contract as s2, s3_contract as s3
from .release_integrity import MANIFEST, PROVENANCE

POLICY_PATH = 'reproduce/HISTORICAL_REPLAY_POLICY.json'
RELEASE_TAG = 'v1.0.2-paper-submission'

class AuthorizationStop(RuntimeError):
    """Deliberate data-boundary stop, preserving completed S2 receipts."""

def policy():
    obj = load(SOURCE_ROOT / POLICY_PATH)
    basis = obj['policy']
    if digest(basis) != obj['semantic_digest']:
        raise ValueError('historical replay policy digest mismatch')
    if basis['release_tag'] != RELEASE_TAG or basis['scope'] != 'ALREADY_OPENED_PUBLIC_HISTORICAL_ARCHIVES_ONLY':
        raise ValueError('historical replay policy scope mismatch')
    return obj

def authorization(source, opt_in=False):
    if type(opt_in) is not bool: raise ValueError('explicit boolean opt-in required')
    obj = policy()
    return {'schema':'P13_HISTORICAL_REPLAY_AUTHORIZATION_V1', 'opt_in':opt_in,
            'policy_version':obj['policy']['version'], 'policy_digest':obj['semantic_digest'],
            'policy_file_sha256':file_sha256(SOURCE_ROOT / POLICY_PATH),
            'public_tag':RELEASE_TAG, 'public_commit':source,
            'scope':obj['policy']['scope'], 'recorded_before_S0':True,
            'prospective_holdout_authority':False}

def require_authorization(auth, source, readiness):
    if not isinstance(auth,dict) or auth != authorization(source, auth.get('opt_in',False)):
        raise AuthorizationStop('HISTORICAL_REPLAY_AUTHORIZATION_INVALID; start a fresh S0 execution')
    if auth['opt_in'] is not True:
        raise AuthorizationStop('HISTORICAL_REPLAY_OPT_IN_REQUIRED: complete S2 preserved; review the additive policy and start a fresh S0 root with --authorize-historical-sealed-replay')
    if not readiness or readiness.get('decision') != 'S2_FROZEN_GLOBAL_READINESS_PASS':
        raise AuthorizationStop('S2_GLOBAL_READINESS_REQUIRED; SEALED opening denied')
    return {'decision':'HISTORICAL_SEALED_REPLAY_ENTRY_AUTHORIZED',
            'policy_digest':auth['policy_digest'], 'S2_readiness_digest':digest(readiness),
            'R_C':'NOT_AUTOMATICALLY_ASSIGNED', 'prospective_holdout_authority':False}

def snapshot(project, ctx, source):
    if ctx['source_commit'] != source: raise ValueError('stage source commit differs from integrated source')
    manifest = load(contained(project, MANIFEST, True))
    rows = manifest['files']; scientific = [r for r in rows if r['role']=='scientific_frozen']
    accepted = load(SOURCE_ROOT / PROVENANCE)
    if manifest['schema'] != 'PUBLIC_RELEASE_SOURCE_V1' or len(scientific)!=accepted['protected_scientific_files'] or digest(scientific)!=accepted['protected_scientific_digest']:
        raise ValueError('protected scientific source commitment drift')
    expected = {r['path']:{k:r[k] for k in ('sha256','bytes','mode')} for r in rows}
    if len(expected)!=len(rows): raise ValueError('duplicate source paths')
    mp = contained(project, MANIFEST, True)
    expected[MANIFEST] = {'sha256':file_sha256(mp),'bytes':mp.stat().st_size,'mode':mp.stat().st_mode & 0o777}
    if ctx['source_files']!=expected: raise ValueError('source snapshot manifest coverage drift')
    for rel,row in expected.items():
        p=contained(project,rel,True)
        if {'sha256':file_sha256(p),'bytes':p.stat().st_size,'mode':p.stat().st_mode & 0o777}!=row:
            raise ValueError('source snapshot drift: '+rel)
    # Public transport and data-role declarations remain exactly frozen.
    for rel in ('reproduce/PUBLIC_RELEASE_ASSETS.json','reproduce/PUBLIC_INPUT_MANIFEST.json',
                'reproduce/s2_public_commitments.json','reproduce/s3_public_commitments.json',
                'PUBLIC_REPRODUCTION_CONTRACT.md'):
        if file_sha256(contained(project,rel,True))!=file_sha256(SOURCE_ROOT/rel):
            raise ValueError('asset/data-role/contract source drift: '+rel)
    return expected

def readiness(work, source):
    """Authenticate frozen S0/S1/S2 lineage and complete S2 evidence read-only."""
    work=Path(work).resolve(strict=True)
    p0=work/'S0/project'; p1=work/'S1/project'; p2=work/'S2/project'
    _,c0=s0.context(p0); c1=s1.context(p1); c2=s2.state(p2)
    snapshots=[snapshot(p,c,source) for p,c in ((p0,c0),(p1,c1),(p2,c2))]
    if not snapshots[0]==snapshots[1]==snapshots[2]: raise ValueError('mixed stage source snapshots')
    for step in s0.STEPS:
        s0.parent(p0,step)
        r=load(work/'S0/receipts'/(step+'.json'))
        if r['source_commit']!=source:
            raise ValueError('S0 incomplete/source-mixed receipt')
    final0=load(s0.parent(p0,'K3')/'audit_summary.json')
    if final0['OVERALL_STATUS']!='PASS' or final0['S1_K0_authorized'] is not True:
        raise ValueError('S0 final entry gate not authorized')
    if Path(c1['s0']['root'])!=work/'S0' or c1['s0']['execution_id']!=c0['execution_id'] or c1['s0']['source_commit']!=source:
        raise ValueError('mixed/external S0 parent')
    for step in ('K1','K3'):
        if c1['s0'][step+'_receipt']!=load(work/'S0/receipts'/(step+'.json')):
            raise ValueError('S0 parent receipt changed')
    # Full bound S1 output authentication, including TRAIN registries.
    for step in ('K0','K1A','K1B','K2A','FINAL','K2B','K2C','K3','PF0','PF1'):
        s1.verify_receipt(p1,step)
    header,active,clear,lock=s2.handoff(work/'S1',verify_registries=True)
    if c2['s1']!=header: raise ValueError('S1 parent pins changed')
    if c2['scientific_budget_changed'] is not False: raise ValueError('scientific budget drift')
    s2.verify_record(c2['public_contract'])
    if c2['public_contract']['sha256']!=file_sha256(SOURCE_ROOT/'PUBLIC_REPRODUCTION_CONTRACT.md'):
        raise ValueError('public contract drift')
    assets=load(SOURCE_ROOT/'reproduce/PUBLIC_RELEASE_ASSETS.json')['assets']
    transport=c0['input_transport_verification']
    expected=[{'id':a['id'],'bytes':a['bytes'],'sha256':a['sha256'],'verified':True} for a in assets]
    if transport['status']!='PASS' or transport['archives']!=expected or transport['payloads_inspected'] is not False:
        raise ValueError('five-asset transport lock drift')
    if c1['staging_root']!=c2['staging_root']: raise ValueError('stage asset roots differ')
    receipts={}; summaries={}
    for step in s2.STEPS:
        r=s2.receipt(p2,step); d=s2.stage_dir(p2,step)
        def bound(name):
            p=d/name
            if s2.record(p) not in r['outputs']: raise ValueError('unbound S2 metadata: '+name)
            return load(p)
        summary=bound(step+'_SCIENTIFIC_SUMMARY.json'); sem=bound(step+'_SEMANTIC_OUTPUT_DIGEST.json')
        if summary['OVERALL_STATUS']!='PASS' or summary.get('stop_reason') or summary['SEALED_opened'] is not False:
            raise ValueError('S2 global readiness stopped: '+step)
        if digest(sem['basis'])!=sem['semantic_output_digest'] or summary['semantic_output_digest']!=sem['semantic_output_digest'] or r['semantic_digest']!=sem['semantic_output_digest']:
            raise ValueError('S2 semantic integrity failure: '+step)
        if bound('REPRODUCTION_STAGE_COMPLETE.json')!={'schema':'P13_S2_COMPLETE_MARKER_V1','execution_id':c2['execution_id'],'stage':step,'semantic_output_digest':sem['semantic_output_digest'],'parents':{s:v['digest'] for s,v in receipts.items()}}:
            raise ValueError('S2 completion marker drift: '+step)
        receipts[step]=r; summaries[step]=summary
    ops=s2.rows(s2.stage_dir(p2,'K2')/'K2_III_B_DECISION_MAP.jsonl')
    s2.exact_ids(ops,clear)
    if any(r.get('same_AST_theta_gauge_zero_refit') is not True for r in ops):
        raise ValueError('zero-refit identity commitment drift')
    header3,groups=s3.handoff(work/'S2',full=True)
    def metadata(step,name):
        p=s2.stage_dir(p2,step)/name
        if s2.record(p) not in receipts[step]['outputs']: raise ValueError('unbound readiness metadata: '+name)
        return load(p)
    cert=metadata('K4','K4_REFERENCE_CERTIFICATION.json')
    controls=metadata('K4','K4_CONTROL_FIRST_RESULTS.json')['controls']
    k4=summaries['K4']; k5=summaries['K5']; k7=summaries['K7']
    cfg=load(p2/s0.P/'configs/p13_s2_k4_protocol.json')
    if cert['status']!='PASS' or cert['candidate_independent'] is not True or cert['certified_count']!=32 or cert['unresolved_count']!=0 or cert['reference_uncertainty_ceiling']!=cfg['reference_uncertainty_ceiling']:
        raise ValueError('GLOBAL_REFERENCE_READINESS_FAIL_OR_UNRESOLVED')
    if k4['reference_status']!='PASS' or k4['reference_unresolved_count']!=0 or k4['scientific_outcome']!='DISCRIMINATIVE_ABSOLUTE_GATE' or any(controls.get(k,{}).get('decision')!=s2.RESP[2] for k in ('identity','frozen_null')):
        raise ValueError('GLOBAL_CONTROL_READINESS_FAIL_OR_UNRESOLVED')
    if header3['operator_counts'][s2.OP[0]]<=0 or header3['response_counts'][s2.RESP[0]]<=0:
        raise ValueError('GLOBAL_NONEMPTY_PASS_READINESS_FAILED')
    if k5['complete_response_cohort']!=header3['operator_counts'][s2.OP[0]] or k5['decision_counts']!=header3['response_counts'] or k5['candidate_specific_rescue'] is not False or k5['top_k_or_proxy_filter'] is not False:
        raise ValueError('GLOBAL_COMPLETE_CERTIFICATION_READINESS_FAILED')
    decision=metadata('K7','K7_S3_DECISION_LOCK.json')
    if decision['explicit_S3_authorization_required'] is not True or decision['shortlist_forbidden'] is not True:
        raise ValueError('scientific opening/shortlist lock drift')
    evidence=s2.claim_evidence(header3['operator_counts'],header3['response_counts'],controls)
    if k7['claim_contract_evidence']!=evidence or not all(evidence[k] for k in ('complete_frozen_protocol_campaign','nonempty_operator_PASS','nonempty_response_PASS','discriminative_controls')):
        raise ValueError('GLOBAL_S2_EVIDENCE_READINESS_FAILED')
    return {'decision':'S2_FROZEN_GLOBAL_READINESS_PASS','S2_execution_id':c2['execution_id'],
            'S2_K7_receipt_digest':receipts['K7']['digest'], 'formal_count':header3['formal_count'],
            'diagnostic_FAIL_count':header3['diagnostic_FAIL_count'],
            'diagnostic_UNRESOLVED_count':header3['diagnostic_UNRESOLVED_count'],
            'diagnostic_membership_authority':False, 'R_C':'NOT_AUTOMATICALLY_ASSIGNED',
            'external_claim_adjudication':'PENDING_EXTERNAL_POST_COMPUTATION_AUDIT',
            'SEALED_opened':False,'count_targets_used':False}
