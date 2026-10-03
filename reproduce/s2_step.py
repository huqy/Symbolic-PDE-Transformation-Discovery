"""S2 reproduction orchestration; all operator/response numerical kernels frozen."""
import argparse
import copy
import importlib
import functools
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from .common import digest, file_sha256
from .s0_contract import P, load, write
from .s2_contract import (RUN, STEPS, DIRS, MODULES, OP, RESP, state, handoff,
    rows, write_rows, exact_ids, stage_dir, receipt, sealed_boundary, claim_evidence)

def module(step): return importlib.import_module('p13rawxt.'+MODULES[STEPS.index(step)])
def config(project, step): return load(Path(project)/P/'configs'/('p13_s2_'+step.lower()+'_protocol.json'))
def s1(project): return Path(state(project)['s1']['root'])/'project'
def active(project):
    from .s1_lineage import RUN as S1_RUN
    return load(s1(project)/S1_RUN/'PF1_final_freeze/PF1_S2_ACTIVE_INPUT_MANIFEST.json')
def incoming(project):
    a=active(project); return rows(s1(project)/a['clear_membership']['path'])
def summary(project, step): return load(stage_dir(project,step)/(step+'_SCIENTIFIC_SUMMARY.json'))
def eligible(project): return rows(stage_dir(project,'K2')/'K2_RESPONSE_ELIGIBLE_PASS_MEMBERSHIP.jsonl')

def publish(project, step, data, basis=None):
    d=stage_dir(project,step); d.mkdir(parents=True,exist_ok=True)
    parents={s:receipt(project,s)['digest'] for s in STEPS[:STEPS.index(step)]}
    obj={'stage':'P13-S2-'+step,'fresh_parent_receipts':parents,
         'S1_parent':state(project)['s1']['PF1_receipt_digest'],'evidence':basis if basis is not None else data}
    sem=digest(obj); write(d/(step+'_SEMANTIC_OUTPUT_DIGEST.json'),{'semantic_output_digest':sem,'basis':obj})
    write(d/(step+'_SCIENTIFIC_SUMMARY.json'),dict(data,OVERALL_STATUS='PASS',semantic_output_digest=sem))
    write(d/'REPRODUCTION_STAGE_COMPLETE.json',{'schema':'P13_S2_COMPLETE_MARKER_V1',
          'execution_id':state(project)['execution_id'],'stage':step,'semantic_output_digest':sem,'parents':parents})
    return sem

def stopped(project, step, reason):
    publish(project,step,{'scientific_stage_executed':False,'stop_reason':reason,
                         'candidate_membership_changed':False,'SEALED_opened':False})

def progress(step, done, total, current, start):
    elapsed=time.monotonic()-start; rate=done/max(elapsed,1e-9); eta=(total-done)/max(rate,1e-12)
    eta_text=f'{eta:.1f}s' if done else 'NA'
    print(f'[S2 {step}] processed={done}/{total} candidate/case={current} elapsed={elapsed:.1f}s rate={rate:.3f}/s ETA={eta_text}',flush=True)

def scoped_adapters(stage):
    def decorate(fun):
        @functools.wraps(fun)
        def invoke(*args,**kwargs):
            m=module(stage); original=dict(m.__dict__)
            try: return fun(*args,**kwargs)
            finally: m.__dict__.update(original)
        return invoke
    return decorate

def recover_partial(path, allowed):
    """Discard only a torn last append, reject corruption of committed records."""
    path=Path(path); values=[]
    if not path.exists(): return {}
    with path.open('r+b') as f:
        good=0
        while True:
            line=f.readline()
            if not line: break
            if not line.endswith(b'\n'):
                f.truncate(good); break
            obj=json.loads(line); sid=obj['scientific_branch_id']
            if sid not in allowed or any(r['scientific_branch_id']==sid for r in values):
                raise ValueError('partial ledger membership/duplicate drift')
            values.append(obj); good=f.tell()
    return {r['scientific_branch_id']:r for r in values}

def append_result(f, result):
    f.write(json.dumps(result,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n')
    f.flush(); os.fsync(f.fileno())

def projection(project):
    """Complete metadata only from receipt-bound fresh PF1 + public S0 headers."""
    a=copy.deepcopy(active(project)); meta=load(Path(project)/'reproduce/s2_public_commitments.json')['commitments']
    from .resolve_inputs import load_manifest
    transport={r['id']:r for r in load_manifest()['historical_random_realizations']}
    ctx=state(project)
    for key,label in [('coefficient:DEVELOPMENT_COEF','DEVELOPMENT_coefficient_commitment'),
                      ('response:DEVELOPMENT_COEF','DEVELOPMENT_response_commitment')]:
        o=a[label]; expected=meta[key]
        for name in ('archive_bytes','archive_sha256','payload_semantic_digest','row_count','kind','role'):
            if o[name]!=expected[name]: raise ValueError('projection commitment drift')
        o['public_row_commitments']=expected['public_row_commitments']
        o['archive_absolute_path']=str(Path(ctx['staging_root'])/transport[o['input_id']]['staged_basename'])
    a['projection_role']='REPRODUCTION_INTERFACE_METADATA_NOT_NEW_SCIENTIFIC_AUTHORITY'
    a['fresh_PF1_parent_receipt_digest']=ctx['s1']['PF1_receipt_digest']
    return a

def k0(project):
    m=module('K0'); d=stage_dir(project,'K0'); d.mkdir(parents=True,exist_ok=True)
    a=projection(project); cfg=config(project,'K0'); n=len(incoming(project))
    write(d/'PF1_PROJECTED_S2_INTERFACE.json',a)
    # Transaction recovery never invents coefficients; reopen committed archive
    # and deterministically replace only the same K0-owned opened objects.
    c=a['DEVELOPMENT_coefficient_commitment']
    result=m.inspect_and_open_development_coefficient_archive(Path(c['archive_absolute_path']),c,c['public_row_commitments'],d/'opened_development_coefficients')
    result=m._relative_open_manifest(Path(project),result)
    if result['status']!='PASS': raise ValueError('K0 coefficient opening failed')
    write(d/'K0_DEVELOPMENT_COEFFICIENT_OPEN_MANIFEST.json',result)
    boundary=sealed_boundary(); write(d/'K0_DATA_BOUNDARY_GUARD.json',boundary)
    lock={'status':'PASS','S2_active_cohort':n,'S1_unresolved_reference':a['unresolved_lineage']['count'],
          'same_AST_theta_gauge_zero_refit':cfg['zero_shot_contract'],
          'operator_transfer_protocol':cfg['operator_transfer_protocol'],
          'scientific_branch_identity_preserved':True,'exact_equivalence_role':'execution_sharing_only',
          'K1_complete_cohort_required':True,'K1_shortlist_forbidden':True}
    lock['operator_transfer_protocol']['candidate_cohort']='complete fresh S1 clear scientific cohort'
    write(d/'K0_S2_OPERATOR_TRANSFER_PROTOCOL_LOCK.json',lock)
    publish(project,'K0',{'complete_cohort':n,'unresolved_REFERENCE':a['unresolved_lineage']['count'],
             'same_AST_theta_gauge_zero_refit':True,'tau_num':0.005,
             'DEVELOPMENT_response_opened':False,'SEALED_opened':False},
            {'opened_manifest':result,'protocol_lock':lock,'boundary':boundary,'projection_sha256':file_sha256(d/'PF1_PROJECTED_S2_INTERFACE.json')})

def k1(project):
    m=module('K1'); d=stage_dir(project,'K1'); d.mkdir(parents=True,exist_ok=True)
    cfg=config(project,'K1'); base=load(Path(project)/cfg['base_operator_protocol'])
    cohort=incoming(project); candidates=[m._candidate_from_locator(s1(project),r,i) for i,r in enumerate(cohort)]
    exact_ids(candidates,cohort)
    dev=load(stage_dir(project,'K0')/'K0_DEVELOPMENT_COEFFICIENT_OPEN_MANIFEST.json')
    for row in dev['search_objects']:
        p=Path(project)/row['path']
        if file_sha256(p)!=row['sha256'] or not m._verify_npz_semantic(p,row['semantic_digest']): raise ValueError('DEV object drift')
    id33=m._identity(m._load_dev_views(Path(project),dev,33,65),base)
    id65=m._identity(m._load_dev_views(Path(project),dev,65,65),base)
    write(d/'K1_DEVELOPMENT_IDENTITY_BASELINES.json',{'G33':id33,'G65':id65,'membership_authority':False})
    work=d/'work'; work.mkdir(exist_ok=True); path=work/'partial_results.jsonl'
    completed=recover_partial(path,{r['scientific_branch_id'] for r in candidates})
    remaining=[r for r in candidates if r['scientific_branch_id'] not in completed]
    start=time.monotonic(); progress('K1',len(completed),len(candidates),'resume',start)
    with path.open('a') as f, ProcessPoolExecutor(max_workers=16,initializer=m._worker_init,initargs=(str(project),dev,base)) as ex:
        for fut in as_completed({ex.submit(m._worker_eval,r):r for r in remaining}):
            r=fut.result(); completed[r['scientific_branch_id']]=r; append_result(f,r)
            progress('K1',len(completed),len(candidates),r['scientific_branch_id'][:12]+'/4 fields G33,G65',start)
    values=[m._enrich(completed[r['scientific_branch_id']],id33,id65,0.005) for r in candidates]
    exact_ids(values,cohort)
    # An implementation exception is not a scientific invalidity/FAIL. Stop for
    # repair and preserve shards rather than inventing missing operator quantities.
    if any('fatal_exception' in r for r in values): raise RuntimeError('K1 implementation exception; unresolved evidence requires repair')
    path=d/'K1_OPERATOR_TRANSFER_MEASUREMENTS.jsonl'; write_rows(path,values)
    agg=m._aggregate(values,0.005); write(d/'K1_OPERATOR_TRANSFER_AGGREGATE.json',agg)
    from .s1_lineage import RUN as S1_RUN
    pf0=m._pf0_associations(s1(project)/S1_RUN,values)
    write(d/'K1_PF0_TRANSFER_ASSOCIATIONS_DESCRIPTIVE.json',pf0)
    boundary=sealed_boundary(); write(d/'K1_DATA_BOUNDARY_GUARD.json',boundary)
    write(d/'K1_ADJUDICATION_HANDOFF_LOCK.json',{'complete_cohort':len(cohort),'same_AST_theta_gauge_zero_refit':True,
           'K1_assigns_final_III_B_decision':False,'tau_num':0.005,'candidate_specific_rescue':False})
    publish(project,'K1',{'complete_cohort':len(cohort),'aggregate':agg,'K1_measurements_sha256':file_sha256(path),
                         'DEVELOPMENT_response_opened':False,'SEALED_opened':False},
            {'measurement_sha256':file_sha256(path),'aggregate':agg,'boundary':boundary,'PF0_descriptive_associations':pf0})

def k2(project):
    m=module('K2'); d=stage_dir(project,'K2'); d.mkdir(parents=True,exist_ok=True)
    values=rows(stage_dir(project,'K1')/'K1_OPERATOR_TRANSFER_MEASUREMENTS.jsonl')
    exact_ids(values,incoming(project))
    if not all(r['same_AST_theta_gauge_zero_refit'] for r in values): raise ValueError('refit')
    decisions=[m._adjudicate_row(r) for r in values]; exact_ids(decisions,values)
    counts={k:sum(r['III_B_decision']==k for r in decisions) for k in OP}
    dmap=d/'K2_III_B_DECISION_MAP.jsonl'; write_rows(dmap,decisions)
    passes=[{'membership_index':r['membership_index'],'scientific_branch_id':r['scientific_branch_id'],
             'K2_decision':'OPERATOR_TRANSFER_PASS','K2_decision_map_path':str(dmap.relative_to(project))}
            for r in decisions if r['III_B_decision']==OP[0]]
    path=d/'K2_RESPONSE_ELIGIBLE_PASS_MEMBERSHIP.jsonl'; write_rows(path,passes)
    manifest={'response_eligible_count':len(passes),'all_clear_PASS_included':True,
              'response_eligible_membership_path':str(path.relative_to(project)), 'response_eligible_membership_sha256':file_sha256(path),
              'complete_K2_decision_map_path':str(dmap.relative_to(project)), 'complete_K2_decision_map_sha256':file_sha256(dmap),
              'DEVELOPMENT_RESPONSE':'SEALED_COMMITTED_UNOPENED','scientific_branch_identity_preserved':True}
    write(d/'K2_RESPONSE_ELIGIBLE_INPUT_MANIFEST.json',manifest)
    write(d/'K2_III_B_DECISION_SUMMARY.json',{'complete_census':len(values),'decision_counts':counts,
           'top_k_or_Pareto_used':False,'target_survivor_count_used':False,'new_numeric_thresholds':[]})
    boundary=sealed_boundary(); write(d/'K2_DATA_BOUNDARY_GUARD.json',boundary)
    publish(project,'K2',{'complete_census':len(values),'decision_counts':counts,
            'response_eligible_membership':str(path.relative_to(project)), 'response_eligible_membership_sha256':file_sha256(path),
            'DEVELOPMENT_response_opened':False,'SEALED_opened':False,'response_stage_blocked_pending_K3':True},
            {'manifest':manifest,'counts':counts,'boundary':boundary,'gate':'unchanged frozen _adjudicate_row; grid disagreement remains UNRESOLVED'})

def k3(project):
    if not eligible(project): return stopped(project,'K3','ZERO_OPERATOR_PASS_RESPONSE_PATH_STOP')
    m=module('K3'); cfg=config(project,'K3'); d=stage_dir(project,'K3'); d.mkdir(parents=True,exist_ok=True)
    a=active(project); response_contract=load(next(r['path'] for r in state(project)['s1']['pins'] if r['path'].endswith('/objective_response_contract_lock.json')))
    if not all(m._verify_contract(response_contract).values()): raise ValueError('response contract drift')
    for key in ('causal_solver_core','null_control_builder'):
        if file_sha256(Path(project)/cfg[key]['path'])!=cfg[key]['sha256']: raise ValueError('frozen response core drift')
    null=a['null_control']; expected=config(project,'K4')['null_control_lock']
    if a['identity_control']!={'definition':'identity','response_control_only':True}:
        raise ValueError('identity control is not the fresh PF1 frozen identity')
    if any(null[k]!=expected[k] for k in ('control_id','structural_hash','theta')): raise ValueError('NULL control changed')
    policy=copy.deepcopy(cfg['candidate_control_fidelity_protocol'])
    policy['candidate_escalation_scope']='all operator-PASS scientific branches + identity + frozen NULL together'
    policy['candidate_escalation_trigger']='at least one member of the complete response-eligible cohort remains threshold-unresolved'
    response={'status':'PASS','response_data_opened':False,'complete_response_eligible_scientific_cohort':len(eligible(project)),
              'scientific_response_contract':cfg['scientific_response_contract'],'reference_protocol':cfg['reference_protocol'],
              'candidate_control_fidelity_protocol':policy,'exact_execution_sharing':cfg['exact_execution_sharing'],
              'causal_solver_core':cfg['causal_solver_core']}
    controls={'status':'PASS','response_data_opened':False,'absolute_gate':0.15,'membership_authority':'NONE_FOR_CONTROLS',
              'identity_control':a['identity_control'],'null_control':null,'control_first_protocol':cfg['control_first_protocol']}
    write(d/'K3_RESPONSE_FIDELITY_PROTOCOL_LOCK.json',response)
    write(d/'K3_CONTROL_FIRST_AND_NONDISCRIMINATION_LOCK.json',controls)
    write(d/'K3_DATA_BOUNDARY_GUARD.json',sealed_boundary())
    publish(project,'K3',{'response_eligible_count':len(eligible(project)),'DEVELOPMENT_response_opened':False,
                         'SEALED_opened':False,'control_first':True}, {'response_lock':response,'control_lock':controls})

def marker(project, name):
    # Exact generated stage mapping; never consult historical or ambient LATEST.
    if name=='LATEST_P13_S2_DEVELOPMENT_COEF_INPUT.txt':
        return stage_dir(project,'K0')/'K0_DEVELOPMENT_COEFFICIENT_OPEN_MANIFEST.json'
    if name in {'LATEST_P13_S2_'+s+'_RUN.txt' for s in STEPS}:
        return Path(project)/RUN
    raise PermissionError('unknown marker interface: '+name)

def native_adapters(project, step, m):
    """Only cfg lookup, input verification, marker projection and count rendering."""
    cfg=config(project,step); n=len(eligible(project))
    pf1=stage_dir(project,'K0')/'PF1_PROJECTED_S2_INTERFACE.json'
    cfg['pf1_active_input_manifest']={'path':str(pf1.relative_to(project)),'sha256':file_sha256(pf1)}
    ep=stage_dir(project,'K2')/'K2_RESPONSE_ELIGIBLE_PASS_MEMBERSHIP.jsonl'
    cfg['response_eligible_membership']={'path':str(ep.relative_to(project)),'sha256':file_sha256(ep),'count':n}
    if step=='K4': cfg['expected_k3_semantic_output_digest']=summary(project,'K3')['semantic_output_digest']
    else:
        a=active(project); cfg['s1_clear_membership']={**a['clear_membership'],'path':str(s1(project)/a['clear_membership']['path'])}
        k4sum=summary(project,'K4'); exp=cfg['expected_k4']
        exp['semantic_output_digest']=k4sum['semantic_output_digest']; exp['k4r_semantic_output_digest']=k4sum['K4R_semantic_output_digest']
    old_load=m.load_json
    def reading(path):
        return copy.deepcopy(cfg) if Path(path).name=='p13_s2_'+step.lower()+'_protocol.json' else old_load(path)
    m.load_json=reading
    m.resolve_marker=lambda root, rel: marker(project,Path(rel).name)
    m._update_context=lambda *args:None
    old_text=m.write_text
    def text(path,value):
        p=Path(path)
        if p.name.startswith('LATEST_P13_S2_'): p=Path(project)/RUN/p.name
        elif p.parent==Path(project)/RUN and p.name in (step+'_OVERALL_STATUS.txt',step+'_NEXT_ACTION.txt'):
            p=stage_dir(project,step)/p.name
        old_text(p,value)
    m.write_text=text
    old_write=m.write_json
    def writing(path,obj):
        name=Path(path).name
        if name=='K4_SCIENTIFIC_SUMMARY.json': obj['response_eligible_count']=n
        if name in ('K5_RESPONSE_DECISION_SUMMARY.json','K5_SCIENTIFIC_SUMMARY.json'):
            for key in ('complete_census','complete_response_cohort'):
                if key in obj: obj[key]=n
        if name=='K5_DATA_BOUNDARY_GUARD.json':
            obj.pop('complete_1955_evaluated',None); obj['complete_response_cohort_evaluated']=True
        old_write(path,obj)
    m.write_json=writing
    return cfg

@scoped_adapters('K4')
def k4(project):
    if not eligible(project): return stopped(project,'K4','ZERO_OPERATOR_PASS_RESPONSE_PATH_STOP')
    m=module('K4'); cfg=native_adapters(project,'K4',m)
    # K3 receipt authenticates the actual, already-frozen response policy and
    # entire incoming eligible cohort. No historical count/digest requirements.
    def entry(root,c):
        receipt(project,'K3'); lock=load(stage_dir(project,'K3')/'K3_RESPONSE_FIDELITY_PROTOCOL_LOCK.json')
        if lock['complete_response_eligible_scientific_cohort']!=len(eligible(project)) or lock['response_data_opened'] is not False:
            raise ValueError('K3 policy not frozen before response opening')
        return Path(project)/RUN,load(stage_dir(project,'K0')/'PF1_PROJECTED_S2_INTERFACE.json'),{'status':'PASS','K3_semantic_output_digest':summary(project,'K3')['semantic_output_digest']}
    m._verify_k3_entry=entry
    repair=importlib.import_module('p13rawxt.s2_k4r_coefficient_reproduction_repair')
    lock=repair._repair_lock(Path(project))
    if lock['status']!='PASS': raise ValueError('historical K4R source lock failed')
    m.validate_regenerated_coefficients=repair.validate_regenerated_coefficients_roundoff
    d=stage_dir(project,'K4'); d.mkdir(exist_ok=True)
    write(d/'K4R_ENGINEERING_REPAIR_LOCK.json',lock)
    if m.run(Path(project))!=0: raise RuntimeError('K4 failed')
    result=load(d/'K4_SCIENTIFIC_SUMMARY.json')
    overlay={'base_K4_semantic_output_digest':result['semantic_output_digest'],
             'historical_repair_lock_sha256':file_sha256(d/'K4R_ENGINEERING_REPAIR_LOCK.json'),
             'coefficient_reproduction_precheck_sha256':file_sha256(d/'K4_HIGH_GRID_COEFFICIENT_REPRODUCTION_PRECHECK.json'),
             'repair_integrated_before_first_fresh_response_opening':True,'scientific_threshold_changed':False}
    result['K4R_semantic_output_digest']=digest(overlay)
    write(d/'K4R_SEMANTIC_OUTPUT_DIGEST.json',{'semantic_output_digest':digest(overlay),'basis':overlay})
    publish(project,'K4',result,{'frozen_native_result':result,'K4R':overlay})

def candidate_list(project):
    m=module('K1'); clear=incoming(project); out=[]
    for row in eligible(project):
        i=int(row['membership_index'])
        if i<0 or i>=len(clear): raise ValueError('eligible index out of range')
        candidate=m._candidate_from_locator(s1(project),clear[i],i)
        if candidate['scientific_branch_id']!=row['scientific_branch_id']: raise ValueError('eligible/S1 mismatch')
        out.append(candidate)
    exact_ids(out,eligible(project)); return out

def candidate_grid(root,k5,candidates,grid,workers,response_rows,generators,refmap,causal_cfg,progress_seconds,checkpoint_every):
    m=module('K5'); work=k5/'work'; work.mkdir(exist_ok=True)
    path=work/f'G{grid}_candidate_results.jsonl'; done=recover_partial(path,{r['scientific_branch_id'] for r in candidates})
    from multiprocessing import get_context
    start=time.monotonic(); progress('K5/G'+str(grid),len(done),len(candidates),'resume',start)
    with path.open('a') as f, ProcessPoolExecutor(max_workers=workers,mp_context=get_context('spawn'),initializer=m._worker_init,
         initargs=(str(root),response_rows,generators,refmap,causal_cfg)) as ex:
        futures={ex.submit(m._worker,dict(r,grid=grid)):r for r in candidates if r['scientific_branch_id'] not in done}
        for fut in as_completed(futures):
            r=fut.result(); done[r['scientific_branch_id']]=r; append_result(f,r)
            progress('K5/G'+str(grid),len(done),len(candidates),r['scientific_branch_id'][:12]+'/32 cases',start)
    if len(done)!=len(candidates): raise RuntimeError('incomplete response cohort')
    return done

def controls_ready(project):
    if not eligible(project): return False
    result=summary(project,'K4')
    return result.get('reference_status')=='PASS' and result.get('reference_certified_count')==32 and all(result.get(k)=='RESPONSE_FAIL' for k in ('identity_decision','frozen_null_decision'))

@scoped_adapters('K5')
def k5(project):
    if not controls_ready(project): return stopped(project,'K5','CONTROL_OR_REFERENCE_STOP_NO_CANDIDATE_RESPONSE')
    m=module('K5'); cfg=native_adapters(project,'K5',m)
    def entry(root,c):
        receipt(project,'K4'); d=stage_dir(project,'K4')
        ref=load(d/'K4_REFERENCE_CERTIFICATION.json'); response=load(d/'K4_OPENED_DEVELOPMENT_RESPONSE.json')
        if ref['status']!='PASS' or ref['certified_count']!=32 or len(response['rows'])!=32: raise ValueError('reference/cases incomplete')
        return Path(project)/RUN,d,ref,response,{'status':'PASS','K4_semantic_output_digest':summary(project,'K4')['semantic_output_digest']}
    m._verify_entry=entry; m._load_candidates=lambda *args:candidate_list(project)
    m.run_candidate_grid=candidate_grid
    if m.run(Path(project))!=0: raise RuntimeError('K5 failed')
    d=stage_dir(project,'K5'); result=load(d/'K5_SCIENTIFIC_SUMMARY.json')
    decisions=rows(d/'K5_RESPONSE_DECISION_MAP.jsonl'); exact_ids(decisions,eligible(project))
    publish(project,'K5',result,{'frozen_native_result':result,'fresh_complete_cohort':len(eligible(project))})

def k6(project):
    if not controls_ready(project): return stopped(project,'K6','CONTROL_OR_REFERENCE_STOP_DIAGNOSTIC_ONLY')
    m=module('K6'); cfg=config(project,'K6'); a=active(project)
    from .s1_lineage import RUN as S1_RUN
    parent=s1(project)/S1_RUN; d=stage_dir(project,'K6'); d.mkdir(parents=True,exist_ok=True)
    k5d=stage_dir(project,'K5'); decisions=m._dict_by_id(k5d/'K5_RESPONSE_DECISION_MAP.jsonl')
    operator=m._dict_by_id(stage_dir(project,'K1')/'K1_OPERATOR_TRANSFER_MEASUREMENTS.jsonl')
    lower=m._dict_by_id(parent/'K2B_diagnostics/K2B_diagnostic_branch_results.jsonl')
    geom=m._dict_by_id(parent/'PF0_postfreeze/PF0_theory_geometry_rows.jsonl')
    asp=m._dict_by_id(parent/'PF0_postfreeze/PF0_ASP_branch_results.jsonl')
    fine=summary(project,'K5')['final_fidelity_pair'][1]; values=[]; start=time.monotonic()
    measurements=rows(k5d/'K5_CANDIDATE_RESPONSE_MEASUREMENTS.jsonl'); exact_ids(measurements,eligible(project))
    for r in measurements:
        sid=r['scientific_branch_id']; op=operator[sid]; decision=decisions[sid]
        row={'scientific_branch_id':sid,'membership_index':r['membership_index'],'arm':r['arm'],
             'paired_seed':r['paired_seed'],'proposal_index':r['proposal_index'],'structural_hash':r['structural_hash'],
             'K5_decision':decision['decision'],'K5_decision_immutable':True,
             'membership_authority':False,'response_worst_upper':decision['worst_upper'],
             'response_worst_lower':decision['worst_lower'],
             'response_margin_to_0p15':None if decision['worst_upper'] is None else .15-float(decision['worst_upper']),
             'TRAIN_family_ratio_G33':op['TRAIN_family_ratio_G33'],'DEV_family_ratio_G33':op['DEV_G33'].get('family_ratio_to_identity'),
             'DEV_family_ratio_G65':op['DEV_G65'].get('family_ratio_to_identity'),
             'rho_transfer_G33':op.get('rho_transfer'),'rho_transfer_G65':op.get('rho_transfer_G65')}
        try:
            cases,fields=m._final_grid_response(r,fine)
            import math
            import numpy as np
            vals=list(cases.values())
            row.update(response_cases=cases,response_worst_by_field=fields,response_worst_nominal=max(vals),
                       response_RMS_nominal=math.sqrt(sum(v*v for v in vals)/len(vals)),
                       response_median_nominal=float(np.median(vals)),diagnostic_status='RESOLVED')
        except RuntimeError:
            row.update(response_cases=None,response_worst_nominal=None,response_RMS_nominal=None,response_median_nominal=None,
                       diagnostic_status='UNRESOLVED_NUMERICAL_MEASUREMENT')
        # Join only recorded fresh parents. Missing required lineage is a blocker.
        row.update(m._extract_lower(lower[sid])); row.update(m._extract_pf0(geom[sid],asp[sid])); values.append(row)
        progress('K6',len(values),len(measurements),sid[:12],start)
    exact_ids(values,eligible(project)); path=d/'K6_BRANCH_DIAGNOSTICS.jsonl'; write_rows(path,values)
    dist={'n':len(values),'decision_counts':summary(project,'K5')['decision_counts'],
          'all_K5_RESPONSE_PASS':all(r['K5_decision']==RESP[0] for r in values),
          'final_fidelity_pair':summary(project,'K5')['final_fidelity_pair'],
          'formal_membership_change':False,'membership_authority':False}
    for key,label in [('response_worst_nominal','worst_nominal'),('response_RMS_nominal','RMS_nominal'),
                      ('response_median_nominal','median_nominal'),('response_worst_upper','worst_upper'),
                      ('response_margin_to_0p15','response_margin_to_0p15')]: dist[label]=m.qstats(r[key] for r in values)
    write(d/'K6_RESPONSE_DISTRIBUTIONS.json',dist)
    xkeys=['TRAIN_family_ratio_G33','DEV_family_ratio_G33','DEV_family_ratio_G65','rho_transfer_G33','rho_transfer_G65',
           'coefficient_ablation_delta_rel','lower_max_abs_L_T_over_C_TT','lower_max_abs_L_X_over_C_TT','lower_min_C_TT',
           'lower_max_abs_q_over_C_TT','lower_max_abs_inv_C_TT']
    ykeys=['response_worst_nominal','response_RMS_nominal','response_worst_upper','response_margin_to_0p15']
    associations={x:{y:m.spearman_pairs(values,x,y) for y in ykeys} for x in xkeys}
    write(d/cfg['outputs']['operator_response_associations'],{'status':'DESCRIPTIVE_ONLY','p_values_reported':False,'membership_authority':False,'associations':associations})
    pfkeys=['pf0_s_norm_ratio','pf0_theory_cosine','pf0_tangent_relative_residual','pf0_radial_fraction','pf0_signed_radial_offset',
            'pf0_projection_relative_residual','pf0_G_metric_coefficient_cosine','pf0_G_metric_coefficient_relative_residual','asp_abs_log_lambda_star','asp_radial_slack']
    write(d/cfg['outputs']['pf0_response_associations'],{'status':'EXPLORATORY_POST_RESPONSE_DESCRIPTIVE','p_values_reported':False,
          'membership_authority':False,'associations':{x:{y:m.spearman_pairs(values,x,y) for y in ykeys} for x in pfkeys}})
    controls=m._control_nominals(k5d); resolved=[r for r in values if r['response_cases'] is not None]
    case=m._case_census(resolved,controls) if resolved else {'rows':[],'membership_authority':False}
    case['diagnostic_resolved_count']=len(resolved); case['diagnostic_unresolved_count']=len(values)-len(resolved)
    write(d/cfg['outputs']['case_census'],case); write(d/cfg['outputs']['arm_seed_descriptive'],m._arm_seed(values))
    compare={'status':'DESCRIPTIVE_ONLY','controls':controls,'membership_authority':False,'diagnostic_resolved_count':len(resolved)}
    for name in ('identity','frozen_null'):
        nominal=controls[name]['worst_nominal']
        compare['candidate_to_'+name+'_worst_nominal_ratio']=None if nominal is None or nominal<=0 else m.qstats(r['response_worst_nominal']/nominal for r in resolved)
        compare['fraction_candidates_below_'+name+'_worst_nominal']=None if nominal is None or not resolved else sum(r['response_worst_nominal']<nominal for r in resolved)/len(resolved)
    write(d/cfg['outputs']['control_comparisons'],compare)
    boundary=sealed_boundary(True); write(d/'K6_DATA_BOUNDARY_GUARD.json',boundary)
    publish(project,'K6',{'diagnostic_rows':len(values),'K5_decisions_immutable':True,'K5_response_pass_membership_immutable':True,
                         'all_K5_RESPONSE_PASS':all(r['K5_decision']==RESP[0] for r in values),'SEALED_opened':False},
            {'diagnostics_sha256':file_sha256(path),'distributions':dist,'associations':associations,'boundary':boundary})

def k7(project):
    d=stage_dir(project,'K7'); d.mkdir(parents=True,exist_ok=True)
    op=summary(project,'K2')['decision_counts']; n=len(incoming(project)); k5s=summary(project,'K5')
    response=k5s.get('decision_counts',dict.fromkeys(RESP,0)); controls={}
    if controls_ready(project): controls=load(stage_dir(project,'K5')/'K5_FINAL_CONTROL_RESULTS.json')['controls']
    evidence=claim_evidence(op,response,controls,complete='stop_reason' not in k5s)
    if controls_ready(project):
        decisions=rows(stage_dir(project,'K5')/'K5_RESPONSE_DECISION_MAP.jsonl'); exact_ids(decisions,eligible(project))
        passes=[r for r in decisions if r['decision']==RESP[0]]
        cohort=rows(stage_dir(project,'K5')/'K5_RESPONSE_PASS_MEMBERSHIP.jsonl'); exact_ids(cohort,passes)
        hand={'eligible_count_if_authorized':len(cohort),'eligible_membership':str((stage_dir(project,'K5')/'K5_RESPONSE_PASS_MEMBERSHIP.jsonl').relative_to(project)),
              'eligible_membership_sha256':file_sha256(stage_dir(project,'K5')/'K5_RESPONSE_PASS_MEMBERSHIP.jsonl')}
    else: hand={'eligible_count_if_authorized':0,'eligible_membership':None}
    claims={'III_A':{'S1_clear':n,'S1_unresolved_REFERENCE':active(project)['unresolved_lineage']['count'],
                    'accepted_external_S1_adjudication':'separate immutable S1 context freeze'},
            'III_B':{'counts':op,'nonempty_operator_PASS':evidence['nonempty_operator_PASS']},
            'III_C':{'counts':response,'nonempty_response_PASS':evidence['nonempty_response_PASS'],
                     'discriminative_controls':evidence['discriminative_controls']},'III_D':{'status':'UNTESTED_SEALED'},
            'external_claim_audit':'REQUIRED; no automatic R-C PASS'}
    write(d/'K7_CLAIM_HIERARCHY_FREEZE.json',claims)
    write(d/'K7_S3_DECISION_LOCK.json',dict(hand,SEALED_FINAL_COEF='SEALED_COMMITTED_UNOPENED',SEALED_FINAL_RESPONSE='SEALED_COMMITTED_UNOPENED',opening_performed=False,explicit_S3_authorization_required=True,shortlist_forbidden=True))
    write(d/'K7_DATA_BOUNDARY_GUARD.json',sealed_boundary('stop_reason' not in summary(project,'K4')))
    publish(project,'K7',{'S1_clear':n,'S1_unresolved_REFERENCE':active(project)['unresolved_lineage']['count'],
                         'operator_counts':op,'response_counts':response,'claim_contract_evidence':evidence,'SEALED_opened':False},
            {'claims':claims,'S3_handoff':hand,'claim_contract_evidence':evidence})

def run(project, step):
    for prior in STEPS[:STEPS.index(step)]: receipt(project,prior)
    globals()[step.lower()](Path(project))

def main():
    p=argparse.ArgumentParser(); p.add_argument('--project-root',type=Path,required=True); p.add_argument('--step',choices=STEPS,required=True); a=p.parse_args()
    from .s2_io import ACTIVE_POLICY
    if ACTIVE_POLICY!=(str(a.project_root.resolve()),a.step): raise RuntimeError('S2 child requires guarded bootstrap')
    for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):
        if os.environ.get(key)!='1': raise RuntimeError('one thread required')
    run(a.project_root,a.step)

if __name__=='__main__':main()
