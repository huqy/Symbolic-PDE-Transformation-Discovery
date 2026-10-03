"""S3 interface orchestration; numerical kernels and scientific configs frozen."""
import argparse
import copy
import importlib
import json
import os
import sys
import time
from contextlib import contextmanager
from concurrent.futures import ProcessPoolExecutor, as_completed
from multiprocessing import get_context
from pathlib import Path
from .common import digest, file_sha256
from .s0_contract import P, load, write
from .s1_lineage import RUN as S1_RUN
from . import s2_contract as s2
from .s3_contract import (RUN,STEPS,DIRS,MODULES,STRATA,SEALED_OP,RESP,OP,state,stage_dir,
                         receipt,rows,write_rows,record,verify_record,exact_ids,claim_evidence)
from .s2_step import recover_partial, append_result

def module(step): return importlib.import_module('p13rawxt.'+MODULES[STEPS.index(step)])
def config(project,step): return load(Path(project)/P/'configs'/('p13_s3_'+step.lower()+'_protocol.json'))
def parent(project): return Path(state(project)['s2']['root'])/'project'
def s1(project): return Path(state(project)['s2']['S1']['root'])/'project'
def summary(project,step): return load(stage_dir(project,step)/(step+'_SCIENTIFIC_SUMMARY.json'))
def groups(project): return {g:rows(stage_dir(project,'K0')/('K0D_'+g+'.jsonl')) for g in STRATA}
def incoming(project):
    a=load(s1(project)/S1_RUN/'PF1_final_freeze/PF1_S2_ACTIVE_INPUT_MANIFEST.json')
    return rows(s1(project)/a['clear_membership']['path'])
def eligible(project): return rows(stage_dir(project,'K2')/'K2_RESPONSE_ELIGIBLE_CLEAR_PASS_MEMBERSHIP.jsonl')
def publish(project,step,data,basis=None):
    d=stage_dir(project,step); d.mkdir(parents=True,exist_ok=True)
    parents={s:receipt(project,s)['digest'] for s in STEPS[:STEPS.index(step)]}
    obj={'stage':'P13-S3-'+step,'parents':parents,'S2_parent':state(project)['s2']['K7_receipt_digest'],
         'evidence':basis if basis is not None else data}; sem=digest(obj)
    write(d/(step+'_SEMANTIC_OUTPUT_DIGEST.json'),{'semantic_output_digest':sem,'basis':obj})
    write(d/(step+'_SCIENTIFIC_SUMMARY.json'),dict(data,OVERALL_STATUS='PASS',semantic_output_digest=sem))
    write(d/'REPRODUCTION_STAGE_COMPLETE.json',{'schema':'P13_S3_COMPLETE_MARKER_V1','execution_id':state(project)['execution_id'],'stage':step,'semantic_output_digest':sem,'parents':parents})

def progress(step,done,total,current,start):
    elapsed=time.monotonic()-start; rate=done/max(elapsed,1e-9); eta=f'{(total-done)/rate:.1f}s' if rate else 'NA'
    print(f'[S3 {step}] processed={done}/{total} candidate/case={current} elapsed={elapsed:.1f}s rate={rate:.3f}/s ETA={eta}',flush=True)

def stopped(project,step,reason): publish(project,step,{'scientific_stage_executed':False,'stop_reason':reason,'membership_changed':False})

@contextmanager
def scoped(step):
    m=module(step); originals=dict(m.__dict__)
    try: yield m
    finally: m.__dict__.clear(); m.__dict__.update(originals)

def projection(project):
    a=copy.deepcopy(load(s1(project)/S1_RUN/'PF1_final_freeze/PF1_S2_ACTIVE_INPUT_MANIFEST.json'))
    meta=load(Path(project)/'reproduce/s3_public_commitments.json')['commitments']
    from .resolve_inputs import load_manifest
    transport={r['id']:r for r in load_manifest()['historical_random_realizations']}
    for key,label in [('coefficient:SEALED_FINAL_COEF','SEALED_coefficient_guard'),('response:SEALED_FINAL_COEF','SEALED_response_guard')]:
        c=a[label]; frozen=meta[key]
        for k in ('kind','role','archive_bytes','archive_sha256','payload_semantic_digest','row_count'):
            if c[k]!=frozen[k]: raise ValueError('SEALED public commitment drift: '+k)
        c['public_row_commitments']=frozen['public_row_commitments']
        c['archive_absolute_path']=str(Path(state(project)['staging_root'])/transport[c['input_id']]['staged_basename'])
    a['projection_role']='PUBLIC_METADATA_INTERFACE_ZERO_MEMBERSHIP_AUTHORITY'
    return a

def candidate_tasks(project):
    m=module('K1'); strata={r['scientific_branch_id']:r for v in groups(project).values() for r in v}
    clear=incoming(project); out=[]
    for i,loc in enumerate(clear):
        if loc['scientific_branch_id'] not in strata: raise ValueError('incomplete three-stratum partition')
        out.append(m._candidate_task(s1(project),loc,i,strata[loc['scientific_branch_id']]))
    exact_ids(out,clear); return out

def k0(project):
    from .s3_contract import handoff
    header,g=handoff(parent(project).parent,full=True)
    if header!=state(project)['s2']: raise ValueError('S2 handoff changed')
    m=module('K0'); d=stage_dir(project,'K0'); d.mkdir(parents=True,exist_ok=True); cfg=config(project,'K0')
    write(d/'PF1_PROJECTED_S3_INTERFACE.json',projection(project))
    for label,values in g.items(): write_rows(d/('K0D_'+label+'.jsonl'),values)
    write(d/'K0A_PARENT_REPRODUCIBILITY_LOCK.json',{'status':'PASS','fresh_S2':header,'historical_outcome_input':False})
    cfg['claim_boundary_lock'].pop('1955_semantics',None)
    cfg['claim_boundary_lock']['formal_cohort_semantics']='scientific branches; not independent transformation laws'
    write(d/'K0B_POST_OPUS_CLAIM_BOUNDARY_LOCK.json',m._claim_lock(cfg))
    cfg['parent']['K2A_clear_locator_path']=str(s1(project)/S1_RUN/'K2A_FROZEN_FULL_CLEAR_MEMBERSHIP.jsonl')
    passids={r['scientific_branch_id'] for r in g[STRATA[0]]}
    census,values=m._structural_census(s1(project),cfg,passids)
    write(d/'K0C1_STRUCTURAL_DERIVATIVE_ORDER_CENSUS_SUMMARY.json',census)
    write_rows(d/'K0C1_STRUCTURAL_DERIVATIVE_ORDER_CENSUS.jsonl',values)
    # Bind the existing fresh S0 capacity and S1 K2C diagnostic provenance.
    # Frozen TRAIN-only subspace optimizer is unchanged, never fits candidates.
    s1ctx=load(s1(project).parent/'s1_execution.json')
    s0p=s1(project)  # exact S1 receipt-pinned TRAIN copies, no ambient S0 arrays
    capfile=s1(project)/P/'runs/fresh_s0_k2r4/capacity_reuse_lock.json'
    ctrl=load(s1(project)/P/'runs/fresh_s0_k2r3/calibration_only/capacity_instrument_lock_k2r3.json')
    for rel in (str(capfile.relative_to(s1(project))),P+'runs/fresh_s0_k2r3/calibration_only/capacity_instrument_lock_k2r3.json'):
        from .s1_lineage import check_record
        check_record(s1(project),s1ctx['input_files'][rel])
    cfg['diagnostics']['capacity_full_m2_frozen_J_G65']=load(capfile)['stable_J_capacity_G65']
    cfg['diagnostics']['capacity_full_m2_source']=str(capfile)
    cfg['parent']['K2C_branch_results_path']=str(s1(project)/S1_RUN/'K2C_theory_bridge/K2C_branch_results.jsonl')
    # K2C output must be bound by its immutable fresh S1 receipt.
    k2c=load(s1(project).parent/'receipts/K2C.json')
    bound=next(r for r in k2c['outputs'] if r['path'].endswith('/K2C_branch_results.jsonl'))
    from .s1_lineage import check_record
    check_record(s1(project),bound); cfg['parent']['K2C_branch_results_sha256']=bound['sha256']
    with scoped('K0') as native:
        native._resolve_marker=lambda root,rel:s0p/P/'runs/fresh_s0_k1' if rel.endswith('LATEST_P13_S0_K1_RUN.txt') else (_ for _ in ()).throw(ValueError('unknown K0 marker'))
        original_fit=native._fit_subspace
        def diagnostic_fit(pair,evaluate,ref_protocol,seed,label):
            p=d/'work'/('TRAIN_subspace_'+label+'.json')
            key=digest({'pair':pair,'protocol':ref_protocol,'seed':seed,'label':label})
            if p.exists():
                cached=load(p)
                if cached['input_digest']!=key:raise ValueError('TRAIN diagnostic checkpoint drift')
                return cached['result']
            result=original_fit(pair,evaluate,ref_protocol,seed,label)
            write(p,{'input_digest':key,'result':result});return result
        native._fit_subspace=diagnostic_fit
        cap=native._capacity_and_kappa(Path(project),cfg,passids)
    if cap['status'] not in ('PASS','UNRESOLVED_DIAGNOSTIC_OPTIMIZATION'): raise ValueError('K0 diagnostic implementation blocker')
    cap['kappa_obj']['full_capacity_fitted_log_a_coefficient']=ctrl['theta']['full_capacity'][2]
    cap['kappa_obj']['PF0_fresh_formal_effective_B3']=cap['kappa_obj'].pop('PF0_final1955_effective_B3')
    write(d/'K0C2_C3_TRAIN_ONLY_CAPACITY_AND_KAPPA_DIAGNOSTIC.json',cap)
    write(d/'K0_FROZEN_PROTOCOL_LOCK.json',{'operator':cfg['frozen_operator_contract'],'response':cfg['frozen_response_contract'],'strata_counts':{k:len(v) for k,v in g.items()},'report_by':cfg['diagnostic_strata']['report_by'],'all_reporting_descriptive':cfg['diagnostic_strata']['all_reporting_descriptive'],'diagnostic_promotion':False,'membership_authority':'formal only'})
    write(d/'K0_NO_SEALED_OPENING_GUARD.json',{'SEALED_FINAL_COEF_opened':False,'SEALED_FINAL_RESPONSE_opened':False,'private_SEALED_payload_traversed':False})
    publish(project,'K0',{'formal_count':len(g[STRATA[0]]),'diagnostic_counts':{k:len(g[k]) for k in STRATA[1:]},'SEALED_opened':False,'diagnostic_status':cap['status']})

def verify_operator_result(result,task):
    for key in ('source_order','membership_index','scientific_branch_id','stratum','formal_membership_authority',
                'arm','paired_seed','proposal_index','structural_hash','exact_equivalence_class','theta_hex','deterministic_gauge','fit_provenance'):
        if result.get(key)!=task[key]:raise ValueError('K1 shard provenance drift: '+key)
    if result.get('same_AST_theta_gauge_zero_refit') is not True or result.get('K1_final_decision_authority') is not False:raise ValueError('K1 shard authority drift')
    if 'fatal_exception' in result:raise RuntimeError('K1 implementation exception; retain shards')


def complete_coefficient_marker(project, d, manifest, commit, cfg, native):
    """Complete a missing administrative marker after authenticated publication.

    Called only after the frozen existing-manifest branch has passed its full
    byte/semantic verification. Check its objects against the public commitments
    as well. Never reopen an archive, replace an object, rewrite the opening event,
    or read an ambient marker. Marker contents have no scientific input authority.
    """
    expected={(row['field_id'],int(obj['grid'])):obj
              for row in commit['public_row_commitments']
              for obj in row['search_object_commitments']}
    objects=manifest.get('search_objects',[])
    if len(expected)!=12 or len(objects)!=12 or manifest.get('status')!='PASS':
        raise ValueError('administrative completion requires exact committed objects')
    seen=set(); opened=Path(d)/cfg['outputs']['opened_coefficient_subdir']
    for row in objects:
        key=(row['field_id'],int(row['grid'])); target=Path(project)/row['path']
        if key in seen or key not in expected or target.is_symlink() or opened not in target.parents:
            raise ValueError('administrative completion object identity/path drift')
        seen.add(key)
        if row['sha256']!=expected[key]['sha256'] or row['semantic_digest']!=expected[key]['semantic_digest'] or file_sha256(target)!=expected[key]['sha256']:
            raise ValueError('administrative completion commitment drift')
    if seen!=set(expected):raise ValueError('administrative completion census drift')
    from .s3_io import COEFFICIENT_MARKER
    target=Path(project)/P/'runs'/COEFFICIENT_MARKER
    if not target.exists():
        native._write_text(target,(Path(d)/'K1_SEALED_FINAL_COEF_INPUT_MANIFEST.json').relative_to(project).as_posix()+'\n')


def k1(project):
    receipt(project,'K0'); m=module('K1'); cfg=config(project,'K1'); d=stage_dir(project,'K1'); d.mkdir(parents=True,exist_ok=True)
    a=load(stage_dir(project,'K0')/'PF1_PROJECTED_S3_INTERFACE.json'); commit=a['SEALED_coefficient_guard']
    cfg['sealed_final_coefficient_commitment']['archive_absolute_path']=commit['archive_absolute_path']
    for k in ('archive_bytes','archive_sha256','payload_semantic_digest'):
        if cfg['sealed_final_coefficient_commitment'][k]!=commit[k]: raise ValueError('K1 commitment drift')
    # Original opener is fail-closed at torn opening; retain its immutable payload
    # checks. A missing manifest with a published opened_root requires audit.
    opened=d/cfg['outputs']['opened_coefficient_subdir']
    if opened.exists() and not (d/'K1_SEALED_FINAL_COEF_INPUT_MANIFEST.json').exists():
        # Crash between positive-directory publication and manifest commit.
        # Delete only the exact authenticated 12 objects in this K1-owned tree,
        # then reopen the same committed bytes. Unknown content fails closed.
        allowed={opened/field/f'{field}_G{grid}.npz':obj for row in commit['public_row_commitments'] for field in [row['field_id']] for obj in row['search_object_commitments'] for grid in [obj['grid']]}
        observed={p for p in opened.rglob('*') if p.is_file()}
        if observed!=set(allowed) or any(p.is_symlink() or file_sha256(p)!=allowed[p]['sha256'] for p in observed):raise ValueError('torn opening contains unauthenticated objects; audit required')
        for p in observed:p.unlink()
        for p in sorted((p for p in opened.rglob('*') if p.is_dir()),key=lambda p:len(p.parts),reverse=True):p.rmdir()
        opened.rmdir()
    had_manifest=(d/'K1_SEALED_FINAL_COEF_INPUT_MANIFEST.json').is_file()
    manifest=m._open_sealed_coefficient(Path(project),d,commit,cfg)
    if had_manifest:complete_coefficient_marker(Path(project),d,manifest,commit,cfg,m)
    base=load(Path(project)/cfg['operator_protocol']['base_protocol_path'])
    candidates=candidate_tasks(project); work=d/'work'; work.mkdir(exist_ok=True); path=work/'partial_results.jsonl'
    done=recover_partial(path,{r['scientific_branch_id'] for r in candidates})
    taskmap={t['scientific_branch_id']:t for t in candidates}
    for sid,r in done.items():verify_operator_result(r,taskmap[sid])
    i33=m._identity(m._load_views(Path(project),manifest,33,65),base)
    i65=m._identity(m._load_views(Path(project),manifest,65,65),base)
    write(d/'K1_SEALED_IDENTITY_BASELINES.json',{'G33':i33,'G65':i65})
    start=time.monotonic(); progress('K1',len(done),len(candidates),'resume',start)
    with path.open('a') as f, ProcessPoolExecutor(max_workers=16,mp_context=get_context('spawn'),initializer=m._worker_init,initargs=(str(project),manifest,base)) as pool:
        for future in as_completed({pool.submit(m._worker_eval,c):c for c in candidates if c['scientific_branch_id'] not in done}):
            r=future.result()
            verify_operator_result(r,taskmap[r['scientific_branch_id']])
            done[r['scientific_branch_id']]=r; append_result(f,r); progress('K1',len(done),len(candidates),r['scientific_branch_id']+'/4 fields G33/G65',start)
    values=[m._enrich(done[c['scientific_branch_id']],i33,i65,.005) for c in candidates]; exact_ids(values,candidates)
    for label in STRATA:
        write_rows(d/('K1_'+label+'_OPERATOR_LEDGER.jsonl'),[r for r in values if r['stratum']==label])
    write(d/'K1_OPERATOR_TRANSFER_AGGREGATE.json',m._aggregate_rows(values,.005))
    publish(project,'K1',{'formal_count':len(groups(project)[STRATA[0]]),'complete_measurement_count':len(values),'SEALED_coefficient_opened':True,'SEALED_response_opened':False,'same_AST_theta_gauge_zero_refit':True})

def k2(project):
    m=module('K2'); d=stage_dir(project,'K2'); d.mkdir(parents=True,exist_ok=True); allvalues=[]
    for label in STRATA:
        values=rows(stage_dir(project,'K1')/('K1_'+label+'_OPERATOR_LEDGER.jsonl')); exact_ids(values,groups(project)[label])
        allvalues+=values
    byid={r['scientific_branch_id']:r for v in groups(project).values() for r in v}
    decisions=[]
    for r in sorted(allvalues,key=lambda r:r['source_order']):
        if r['same_AST_theta_gauge_zero_refit'] is not True or r['K1_final_decision_authority'] is not False: raise ValueError('K1 zero-refit/authority drift')
        if r['K1_measurement_status']=='COMPLETE': m._verify_stored_measurement_row(r,.005)
        elif r['K1_measurement_status']!='OPERATOR_OR_NUMERICAL_UNRESOLVED_FOR_K2': raise ValueError('K1 implementation blocker')
        locked=byid[r['scientific_branch_id']]
        if r['stratum']!=locked['stratum'] or r['formal_membership_authority'] is not locked['formal_membership_authority']:raise ValueError('K1 prelocked stratum/authority mismatch')
        decisions.append(m._adjudicate_row(r,byid[r['scientific_branch_id']]['III_B_decision']))
    for label in STRATA: write_rows(d/('K2_'+label+'_DECISION_MAP.jsonl'),[r for r in decisions if r['stratum']==label])
    formal=[r for r in decisions if r['formal_membership_authority']]
    exact_ids(formal,groups(project)[STRATA[0]])
    passes=[r for r in formal if r['response_membership_authority']]
    write_rows(d/'K2_RESPONSE_ELIGIBLE_CLEAR_PASS_MEMBERSHIP.jsonl',passes)
    counts={k:sum(r['SEALED_operator_decision']==k for r in formal) for k in SEALED_OP}
    lock={'formal_counts':counts,'eligible_count':len(passes),'eligible_membership':record(d/'K2_RESPONSE_ELIGIBLE_CLEAR_PASS_MEMBERSHIP.jsonl'),'diagnostic_promotion':False,'SEALED_FINAL_RESPONSE':'SEALED_COMMITTED_UNOPENED'}
    write(d/'K2_RESPONSE_ENTRY_LOCK.json',lock)
    publish(project,'K2',{'formal_operator_decision_counts':counts,'response_eligible_count':len(passes),'SEALED_response_opened':False,'diagnostic_promotion':False},lock)

def native_adapters(project,step,m):
    cfg=copy.deepcopy(config(project,step)); n=len(eligible(project)); oldload=m.load_json; oldwrite=m.write_json; oldtext=m.write_text
    if step=='K3':
        cfg['expected_k2_semantic_output_digest']=summary(project,'K2')['semantic_output_digest']
    else:
        cfg['expected_k3']['semantic_output_digest']=summary(project,'K3')['semantic_output_digest']
    ep=stage_dir(project,'K2')/'K2_RESPONSE_ELIGIBLE_CLEAR_PASS_MEMBERSHIP.jsonl'
    cfg['response_eligible_membership'].update(path=str(ep),sha256=file_sha256(ep),count=n)
    if step=='K4': cfg['pf1_active_input_manifest']['path']=str(stage_dir(project,'K0')/'PF1_PROJECTED_S3_INTERFACE.json')
    m.load_json=lambda p:cfg if Path(p)==Path(project)/P/'configs'/('p13_s3_'+step.lower()+'_protocol.json') else oldload(p)
    def writing(p,obj):
        name=Path(p).name
        if name in ('K3_SCIENTIFIC_SUMMARY.json','K4_RESPONSE_DECISION_SUMMARY.json','K4_SCIENTIFIC_SUMMARY.json'):
            for key in ('response_eligible_count','complete_census','complete_response_cohort'):
                if key in obj: obj[key]=n
        if name=='K4_DATA_BOUNDARY_GUARD.json':
            obj.pop('complete_423_evaluated',None); obj['complete_formal_operator_PASS_evaluated']=True
        oldwrite(p,obj)
    m.write_json=writing
    def text(p,value):
        p=Path(p)
        if p.name.startswith('LATEST_P13_S3_') or p.parent==Path(project)/RUN:
            target=stage_dir(project,step)/'native_status'/p.name; oldtext(target,value)
        else: oldtext(p,value)
    m.write_text=text; m._update_context=lambda *args:None
    return cfg

def marker(project,rel):
    if rel.endswith('LATEST_P13_S3_SEALED_FINAL_COEF_INPUT.txt'):
        return stage_dir(project,'K1')/'K1_SEALED_FINAL_COEF_INPUT_MANIFEST.json'
    raise ValueError('ambient/historical marker denied: '+rel)

def durable_tasks(project,tasks,workers,label,fun):
    if not tasks:return []
    step='K3' if label.startswith('reference_') or label.startswith('controls_') else 'K4'
    work=stage_dir(project,step)/'work'; work.mkdir(exist_ok=True); p=work/(label+'_tasks.jsonl')
    keyed=[(digest({k:v for k,v in t.items() if k!='output'}),t) for t in tasks]
    # Stable task digest includes candidate-independent settings and reference
    # paths; a changed input cannot accidentally reuse a numerical checkpoint.
    done=recover_partial(p,{key for key,t in keyed}); start=time.monotonic()
    for key,rec in done.items():
        if 'output' in dict(keyed)[key]: verify_record(rec['output_record'])
    with p.open('a') as f, ProcessPoolExecutor(max_workers=workers,mp_context=get_context('spawn')) as pool:
        pending={pool.submit(fun,t):(key,t) for key,t in keyed if key not in done}
        for future in as_completed(pending):
            key,t=pending[future]; r=future.result(); obj={'scientific_branch_id':key,'result':r}
            if 'output' in t: obj['output_record']=record(t['output'])
            done[key]=obj; append_result(f,obj); progress(label,len(done),len(tasks),r.get('field_id','control')+'/'+r.get('case_type',''),start)
    return [done[key]['result'] for key,t in keyed]

def controls_ready(project):
    if not eligible(project):return False
    s=summary(project,'K3')
    return s.get('reference_status')=='PASS' and s.get('reference_certified_count')==32 and all(s.get(k)==RESP[2] for k in ('identity_decision','frozen_null_decision'))

def k3(project):
    if not eligible(project):return stopped(project,'K3','ZERO_FORMAL_OPERATOR_PASS')
    with scoped('K3') as m:
        native_adapters(project,'K3',m)
        def entry(root,cfg):
            receipt(project,'K2'); lock=load(stage_dir(project,'K2')/'K2_RESPONSE_ENTRY_LOCK.json')
            if lock['eligible_count']!=len(eligible(project)) or lock['SEALED_FINAL_RESPONSE']!='SEALED_COMMITTED_UNOPENED':raise ValueError('K2 membership not frozen')
            verify_record(lock['eligible_membership'])
            return Path(project)/RUN,load(stage_dir(project,'K0')/'PF1_PROJECTED_S3_INTERFACE.json'),{'status':'PASS','fresh_K2_receipt':receipt(project,'K2')['digest']}
        m._verify_k2_entry=entry; m.resolve_marker=lambda root,rel:marker(project,rel)
        m.generate_reference_tasks=lambda tasks,workers,label:durable_tasks(project,tasks,workers,label,m.solve_original_reference)
        m.run_control_tasks=lambda tasks,workers,label:durable_tasks(project,tasks,workers,label,m.control_pair_score)
        if m.run(Path(project))!=0:raise RuntimeError('K3 failed')
        publish(project,'K3',summary(project,'K3'),{'native_result':summary(project,'K3'),'K2_eligible_membership':record(stage_dir(project,'K2')/'K2_RESPONSE_ELIGIBLE_CLEAR_PASS_MEMBERSHIP.jsonl')})

def verify_response_result(result,task,grid,casekeys):
    for key in ('scientific_branch_id','membership_index','arm'):
        if result.get(key)!=task[key]:raise ValueError('response shard identity drift: '+key)
    if result.get('grid')!=grid or result.get('same_AST_theta_gauge_zero_refit') is not True:raise ValueError('response shard fidelity/refit drift')
    if not result.get('numerical_unresolved'):
        for key in ('paired_seed','proposal_index','structural_hash','theta_hex'):
            if result.get(key)!=task[key]:raise ValueError('response shard provenance drift: '+key)
        observed=[(r['field_id'],r['case_type']) for f in result['fields'] for r in f['records']]
        if len(observed)!=32 or len(set(observed))!=32 or set(observed)!=casekeys:raise ValueError('response shard case census drift')


def candidate_grid(root,d,candidates,grid,workers,response_rows,generators,refmap,causal_cfg,progress_seconds,checkpoint_every):
    m=module('K4'); work=d/'work'; work.mkdir(exist_ok=True); p=work/f'G{grid}_candidate_results.jsonl'
    done=recover_partial(p,{c['scientific_branch_id'] for c in candidates}); start=time.monotonic()
    tasks={c['scientific_branch_id']:c for c in candidates};casekeys={(r['field_id'],r['case_type']) for r in response_rows}
    for sid,r in done.items():verify_response_result(r,tasks[sid],grid,casekeys)
    with p.open('a') as f, ProcessPoolExecutor(max_workers=workers,mp_context=get_context('spawn'),initializer=m._worker_init,initargs=(str(root),response_rows,generators,refmap,causal_cfg)) as pool:
        pending={pool.submit(m._worker,dict(c,grid=grid)):c for c in candidates if c['scientific_branch_id'] not in done}
        for future in as_completed(pending):
            r=future.result(); verify_response_result(r,tasks[r['scientific_branch_id']],grid,casekeys); done[r['scientific_branch_id']]=r; append_result(f,r); progress('K4/G'+str(grid),len(done),len(candidates),r['scientific_branch_id']+'/32 cases',start)
    if len(done)!=len(candidates):raise ValueError('incomplete full-cohort grid')
    return done

def k4(project):
    if not controls_ready(project):return stopped(project,'K4','CONTROL_OR_REFERENCE_STOP')
    with scoped('K4') as m:
        cfg=native_adapters(project,'K4',m)
        def entry(root,c):
            receipt(project,'K3'); d=stage_dir(project,'K3'); ref=load(d/'K3_REFERENCE_CERTIFICATION.json'); response=load(d/'K3_OPENED_SEALED_FINAL_RESPONSE.json')
            if ref['status']!='PASS' or ref['certified_count']!=32 or len(response['rows'])!=32:raise ValueError('incomplete reference bank')
            return Path(project)/RUN,d,ref,response,{'status':'PASS','fresh_K3_receipt':receipt(project,'K3')['digest']}
        m._verify_entry=entry
        ids={r['scientific_branch_id'] for r in eligible(project)}
        m._load_candidates=lambda *args:[c for c in candidate_tasks(project) if c['scientific_branch_id'] in ids]
        m.run_candidate_grid=candidate_grid
        # Native K4 imports K3 control kernels by value. Durable controls remain
        # current-stage owned; numerical function is unchanged.
        m.run_control_tasks=lambda tasks,workers,label:durable_tasks(project,tasks,workers,label,module('K3').control_pair_score)
        # Retain authoritative shards until the wrapper completion marker and
        # receipt exist; native cleanup would otherwise create a crash window.
        oldrmtree=m.shutil.rmtree
        def retain(p,*a,**kw):
            if Path(p)!=stage_dir(project,'K4')/'work':raise ValueError('unknown cleanup denied')
        m.shutil.rmtree=retain
        try:
            if m.run(Path(project))!=0:raise RuntimeError('K4 failed')
        finally:m.shutil.rmtree=oldrmtree
        decisions=rows(stage_dir(project,'K4')/cfg['outputs']['decision_map']);exact_ids(decisions,eligible(project))
        publish(project,'K4',summary(project,'K4'),{'native_result':summary(project,'K4'),'complete_formal_cohort':len(eligible(project))})

def k5(project):
    if not controls_ready(project):return stopped(project,'K5','CONTROL_OR_REFERENCE_STOP_DIAGNOSTIC_ONLY')
    m=module('K5'); d=stage_dir(project,'K5');d.mkdir(parents=True,exist_ok=True); cfg=config(project,'K5'); k4=stage_dir(project,'K4')
    k4cfg=config(project,'K4'); decisions=rows(k4/k4cfg['outputs']['decision_map']); exact_ids(decisions,eligible(project))
    measurements=rows(k4/k4cfg['outputs']['measurements']); exact_ids(measurements,eligible(project))
    decision={r['scientific_branch_id']:r for r in decisions}
    k1={r['scientific_branch_id']:r for r in rows(stage_dir(project,'K1')/'K1_FORMAL_OPERATOR_LEDGER.jsonl')}
    k0={r['scientific_branch_id']:r for r in rows(stage_dir(project,'K0')/'K0C1_STRUCTURAL_DERIVATIVE_ORDER_CENSUS.jsonl')}
    development={r['scientific_branch_id']:r for r in rows(s2.stage_dir(parent(project),'K6')/'K6_BRANCH_DIAGNOSTICS.jsonl')}
    refmap=m._reference_map(load(stage_dir(project,'K3')/'K3_REFERENCE_CERTIFICATION.json'))
    controls=m._control_map(k4); fine=summary(project,'K4')['final_fidelity_pair'][1]; values=[]; start=time.monotonic()
    for measurement in measurements:
        sid=measurement['scientific_branch_id']; dev=development[sid]; op=k1[sid]; dec=decision[sid]
        r=m.final_response_record(measurement,fine,refmap,.15)
        if r['decision']!=dec['decision']:raise ValueError('K5 independent frozen interval replay differs')
        # Insufficient fidelity remains UNRESOLVED. Missing descriptive scalars
        # are null, never guessed and never used to remove a formal branch.
        row={k:measurement[k] for k in ('scientific_branch_id','membership_index','arm','paired_seed','proposal_index','structural_hash','theta_hex')}
        row.update(exact_equivalence_class=op['exact_equivalence_class'],K4_decision=r['decision'],K4_decision_immutable=True,membership_authority=False,
                   diagnostic_status='UNRESOLVED' if r['numerical_unresolved'] else 'RESOLVED',
                   SEALED_operator_family_ratio_G33=op['SEALED_G33']['family_ratio_to_identity'],SEALED_operator_family_ratio_G65=op['SEALED_G65']['family_ratio_to_identity'],
                   SEALED_operator_field_ratios_G65=op['SEALED_G65']['field_ratios_to_identity'],
                   DEVELOPMENT_operator_family_ratio_G33=dev['DEV_family_ratio_G33'],DEVELOPMENT_operator_family_ratio_G65=dev['DEV_family_ratio_G65'],
                   DEVELOPMENT_response_decision=dev['K5_decision'],max_coefficient_derivative_order_pair=k0[sid]['max_coefficient_derivative_order_pair'])
        for key in ('worst_nominal','worst_upper','worst_lower','RMS_nominal','median_nominal'):
            row['SEALED_response_'+key]=r.get(key)
            if key!='worst_lower':row['DEVELOPMENT_response_'+key]=dev.get('response_'+key)
        row.update(SEALED_response_margin_to_0p15=None if r.get('worst_upper') is None else .15-r['worst_upper'],
                   SEALED_response_cases=r['cases'],SEALED_response_intervals=r['intervals'],SEALED_response_worst_by_field=r.get('worst_by_field'),
                   failure_witness_keys=r['witness_keys'],threshold_overlap_keys=r.get('threshold_overlap_keys',[]),
                   exact_serialized_response_vector_sha256=None if r['numerical_unresolved'] else digest([r['cases'][key] for key in sorted(r['cases'])]))
        for key in ('coefficient_ablation_delta_rel','pf0_theory_cosine','pf0_s_norm_ratio','pf0_tangent_relative_residual','pf0_projection_relative_residual'):row[key]=dev.get(key)
        values.append(row); progress('K5',len(values),len(measurements),sid[:12],start)
    exact_ids(values,eligible(project));write_rows(d/'K5_COMPLETE_BRANCH_DIAGNOSTICS.jsonl',values)
    counts={key:sum(r['K4_decision']==key for r in values) for key in RESP}
    if counts!=summary(project,'K4')['decision_counts']:raise ValueError('K5 census drift')
    resolved=[r for r in values if r['diagnostic_status']=='RESOLVED']; casekeys=sorted(refmap)
    authority={'membership_authority':False,'complete_cohort_count':len(values),'resolved_descriptive_count':len(resolved),'unresolved_descriptive_count':len(values)-len(resolved)}
    transition=dict(authority,status='DESCRIPTIVE_ONLY',response_crosstab=counts,
                    DEVELOPMENT_response_worst_upper=m.qstats(r['DEVELOPMENT_response_worst_upper'] for r in values),
                    SEALED_response_worst_upper=m.qstats(r['SEALED_response_worst_upper'] for r in values),
                    DEV_SEALED_response_spearman=m.spearman(values,'DEVELOPMENT_response_worst_upper','SEALED_response_worst_upper'),
                    DEV_SEALED_operator_spearman=m.spearman(values,'DEVELOPMENT_operator_family_ratio_G65','SEALED_operator_family_ratio_G65'))
    write(d/cfg['outputs']['development_sealed_transitions'],transition)
    from collections import Counter
    witnesses=Counter(key for r in values for key in r['failure_witness_keys'])
    write(d/cfg['outputs']['failure_witness_census'],dict(authority,clear_fail_branch_count=counts[RESP[2]],by_case=dict(witnesses),by_field=dict(Counter(k.split('::')[0] for r in values for k in r['failure_witness_keys']))))
    write(d/cfg['outputs']['structure_equivalence'],m._structure_classes(values))
    signatures=m._failure_signature_classes(resolved,casekeys);write(d/cfg['outputs']['failure_signature_classes'],dict(signatures,**{k:v for k,v in authority.items() if k!='membership_authority'}))
    def group_summary(rr):return {'n':len(rr),'decision_counts':dict(Counter(r['K4_decision'] for r in rr)),'SEALED_response_worst_upper':m.qstats(r['SEALED_response_worst_upper'] for r in rr)}
    write(d/cfg['outputs']['seed_reliability'],dict(authority,status='CONDITIONAL_DESCRIPTIVE_ONLY',p_values_reported=False,
        by_paired_seed={str(seed):group_summary([r for r in values if r['paired_seed']==seed]) for seed in sorted({r['paired_seed'] for r in values})},
        by_arm={arm:group_summary([r for r in values if r['arm']==arm]) for arm in sorted({r['arm'] for r in values})}))
    write(d/cfg['outputs']['coefficient_ablation'],dict(authority,status='CONTINUOUS_DESCRIPTIVE_ONLY_NO_POST_RESPONSE_BINS',
        by_K4_decision={key:m.qstats(r['coefficient_ablation_delta_rel'] for r in values if r['K4_decision']==key) for key in RESP},
        association=m.spearman(values,'coefficient_ablation_delta_rel','SEALED_response_worst_upper')))
    xkeys=['DEVELOPMENT_operator_family_ratio_G65','SEALED_operator_family_ratio_G65','DEVELOPMENT_response_worst_upper','coefficient_ablation_delta_rel','pf0_theory_cosine','pf0_s_norm_ratio','pf0_tangent_relative_residual','pf0_projection_relative_residual','max_coefficient_derivative_order_pair']
    ykeys=['SEALED_response_worst_nominal','SEALED_response_worst_upper','SEALED_response_RMS_nominal','SEALED_response_margin_to_0p15']
    write(d/cfg['outputs']['operator_response_associations'],dict(authority,status='DESCRIPTIVE_ONLY',p_values_reported=False,weighted_composite_score=False,associations={x:{y:m.spearman(values,x,y) for y in ykeys} for x in xkeys}))
    effects=m._case_control_effects(resolved,controls,casekeys) if resolved else {'records':[],'membership_authority':False}
    for row in effects.get('rows',[]):
        for key in list(row):
            fresh=key.replace('all_423','all_resolved_formal').replace('K4_PASS_3','K4_PASS').replace('K4_FAIL_420','K4_FAIL')
            if fresh!=key:row[fresh]=row.pop(key)
    write(d/cfg['outputs']['case_control_effects'],dict(effects,**{k:v for k,v in authority.items() if k!='membership_authority'}))
    axes=cfg['diagnostic_contract']['descriptive_pareto_axes']; ids=m._nondominated(resolved,axes)
    write(d/cfg['outputs']['descriptive_pareto'],dict(authority,status='POST_DECISION_PRESENTATION_ONLY',axes=axes,nondominated_scientific_branch_ids=ids,formal_selection=False,epsilon_or_weighted_score=None))
    passrows=[r for r in values if r['K4_decision']==RESP[0]]; failrows=[r for r in resolved if r['K4_decision']==RESP[2]]
    selected=[(r,'ALL_FORMAL_K4_RESPONSE_PASS') for r in passrows]
    if failrows:
        nearest=min(failrows,key=lambda r:(r['SEALED_response_worst_lower'],r['scientific_branch_id']));selected.append((nearest,'BOUNDARY_CONTRAST_NEAREST_CLEAR_FAIL'))
        modalid=signatures['modal_class']['medoid_scientific_branch_id']
        if modalid!=nearest['scientific_branch_id']:selected.append((next(r for r in values if r['scientific_branch_id']==modalid),'MODAL_FAILURE_SIGNATURE_MEDOID'))
    dossiers=[m._dossier(s1(project),incoming(project),r,k1[r['scientific_branch_id']],role) for r,role in selected]
    write(d/cfg['outputs']['paper_exemplars'],dict(authority,status='POST_DECISION_PRESENTATION_ONLY',dossiers=dossiers,all_formal_K4_RESPONSE_PASS_included=True,formal_PASS_count=len(passrows),changes_K4_membership=False))
    immutable={'K4_decision_map':record(k4/k4cfg['outputs']['decision_map']),'K4_pass_membership':record(k4/k4cfg['outputs']['pass_membership']),'decision_counts':counts,'membership_authority':False}
    write(d/'K5_K4_DECISION_MEMBERSHIP_IMMUTABILITY_AUDIT.json',immutable)
    publish(project,'K5',dict(authority,K4_decisions_immutable=True,frozen_K4_decision_counts=counts),{'immutable':immutable,'diagnostic_files':[record(p) for p in sorted(d.glob('K5_*')) if p.is_file()]})

def k6(project):
    # All stage receipts/output hashes and complete S2 lineage are re-read.
    # This stage has no numerical worker, evaluator or selection authority.
    from .s3_contract import handoff
    header,g=handoff(parent(project).parent,full=True)
    if header!=state(project)['s2']:raise ValueError('S2 parent changed at final freeze')
    d=stage_dir(project,'K6');d.mkdir(parents=True,exist_ok=True)
    op=summary(project,'K2')['formal_operator_decision_counts']; responses=dict.fromkeys(RESP,0); controls={};stop=None; complete=False; final=[]
    if eligible(project):
        s=summary(project,'K3')
        if s.get('scientific_outcome','').startswith('NONDISCRIMINATIVE'):stop='NONDISCRIMINATIVE'
        if s.get('reference_status')=='PASS':controls=load(stage_dir(project,'K3')/'K3_CONTROL_FIRST_RESULTS.json')['controls']
        if controls_ready(project):
            cfg=config(project,'K4'); k4=stage_dir(project,'K4');decisions=rows(k4/cfg['outputs']['decision_map']);exact_ids(decisions,eligible(project))
            responses=summary(project,'K4')['decision_counts'];complete=True
            final=rows(k4/cfg['outputs']['pass_membership']);exact_ids(final,[r for r in decisions if r['decision']==RESP[0]])
            controls=load(k4/'K4_FINAL_CONTROL_RESULTS.json')['controls']
            imm=load(stage_dir(project,'K5')/'K5_K4_DECISION_MEMBERSHIP_IMMUTABILITY_AUDIT.json')
            for key in ('K4_decision_map','K4_pass_membership'):verify_record(imm[key])
    evidence=claim_evidence(op,responses,controls,complete,stop)
    write_rows(d/'K6_FINAL_FORMAL_RESPONSE_PASS_MEMBERSHIP.jsonl',final)
    lineage={'S1':header['S1'],'S2_operator_counts':header['operator_counts'],'S2_response_counts':header['response_counts'],
             'S3_formal_input_count':header['formal_count'],'S3_diagnostic_FAIL_count':header['diagnostic_FAIL_count'],
             'S3_diagnostic_UNRESOLVED_count':header['diagnostic_UNRESOLVED_count'],'S3_operator_counts':op,'S3_response_counts':responses,
             'final_formal_cohort_count':len(final),'membership_rule_changed_in_K5_or_K6':False,'diagnostic_promotion':False}
    write(d/'K6_COHORT_LINEAGE_FREEZE.json',lineage);write(d/'K6_PUBLIC_CLAIM_EVIDENCE.json',evidence)
    dossiers=[]
    if complete:
        exemplars=load(stage_dir(project,'K5')/config(project,'K5')['outputs']['paper_exemplars'])
        ids={r['scientific_branch_id'] for r in final}
        for r in exemplars['dossiers']:
            if r['scientific_branch_id'] in ids:
                item=dict(r);item['formal_role']='ALL_K4_RESPONSE_PASS_NO_FURTHER_SELECTION';item['membership_authority_provenance']='IMMUTABLE_K4_PASS_MEMBERSHIP';dossiers.append(item)
        if {r['scientific_branch_id'] for r in dossiers}!=ids or len(dossiers)!=len(final):raise ValueError('incomplete final dossier cohort')
    write(d/'K6_FINAL_FORMAL_DOSSIERS.json',{'dossiers':dossiers,'formal_cohort_count':len(final),'new_selection_performed':False})
    publish(project,'K6',{'lineage':lineage,'claim_contract_evidence':evidence,'scientific_evaluation_performed':False,'external_claim_audit_required':True})

def run(project,step):
    for s in STEPS[:STEPS.index(step)]:receipt(project,s)
    globals()[step.lower()](Path(project))
def main():
    p=argparse.ArgumentParser();p.add_argument('--project-root',type=Path,required=True);p.add_argument('--step',choices=STEPS,required=True);a=p.parse_args()
    from .s3_io import ACTIVE_POLICY
    if ACTIVE_POLICY!=(str(a.project_root.resolve()),a.step):raise RuntimeError('S3 requires guarded bootstrap')
    for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):
        if os.environ.get(key)!='1':raise RuntimeError('one thread required')
    run(a.project_root,a.step)
if __name__=='__main__':main()
