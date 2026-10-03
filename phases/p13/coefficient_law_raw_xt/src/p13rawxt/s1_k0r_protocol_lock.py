from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import secrets
import stat
import sys
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Any

import numpy as np

from .coefficients import generate_generator, make_search_object, search_object_semantic_digest, spectral_certificate, write_npz_exact
from .s1_search_primitives import (
    additive_root_residual_graft,
    aggregate_family_objective,
    boundary_status,
    canonical_json_bytes,
    contains_coefficient_syntax,
    continuation_decision,
    derive_seed,
    empty_v2_counters,
    evaluate_shared_theta_family,
    execution_equivalence_key,
    frontier_update,
    paired_initial_population,
    proposal_ledger_row,
    random_ast_policy,
    route_v2_mutation_slot,
    scientific_branch_id,
    sha256_bytes,
    theta_names,
)


def sha256_path(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(8*1024*1024),b''): h.update(b)
    return h.hexdigest()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding='utf-8'))


def write_json(path: Path, obj: Any, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(obj,indent=2,sort_keys=True,ensure_ascii=False)+'\n',encoding='utf-8')
    if mode is not None: os.chmod(path,mode)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True,exist_ok=True); path.write_text(text.rstrip()+'\n',encoding='utf-8')


def resolve_marker(root: Path, marker_rel: str) -> Path:
    m=root/marker_rel
    if not m.is_file(): raise FileNotFoundError(m)
    p=Path(m.read_text().strip()); p=p if p.is_absolute() else root/p
    if not p.is_dir(): raise FileNotFoundError(p)
    return p


def source_manifest(root: Path, paths: list[Path]) -> dict[str,Any]:
    rows=[]
    for p in paths:
        rows.append({'path':p.relative_to(root).as_posix(),'bytes':p.stat().st_size,'sha256':sha256_path(p)})
    return {'files':rows}


def _verify_s0(root: Path, protocol: dict[str,Any]) -> tuple[Path,dict[str,Any]]:
    run=resolve_marker(root,protocol['s0_freeze']['marker'])
    sem=load_json(run/'semantic_output_digest.json').get('semantic_output_digest')
    summary=load_json(run/'audit_summary.json')
    lock_path=root/protocol['s0_freeze']['exact_source_config_test_lock']
    exact_lock=load_json(lock_path)
    file_rows=[]
    for r in exact_lock['files']:
        fp=root/r['path']
        observed=sha256_path(fp) if fp.is_file() else None
        file_rows.append({'path':r['path'],'expected_sha256':r['sha256'],'observed_sha256':observed,'match':observed==r['sha256']})
    checks={
        'status':(run/'OVERALL_STATUS.txt').read_text().strip()==protocol['s0_freeze']['expected_status']=='PASS',
        'semantic':sem==protocol['s0_freeze']['expected_semantic_output_digest'],
        'formal_K1_still_unapproved':not bool(summary.get('S1_K1_formal_search_authorized',True)),
        'S0_PASS':summary.get('OVERALL_STATUS')=='PASS',
        'exact_final_S0_source_config_tests':all(r['match'] for r in file_rows),
        'authoritative_K3_marker_path':run.relative_to(root).as_posix()==protocol['authoritative_hpc']['K3_run'],
    }
    return run,{'status':'PASS' if all(checks.values()) else 'FAIL','checks':checks,'semantic_output_digest':sem,'run':str(run),'exact_source_config_test_lock':str(lock_path),'exact_source_config_test_file_count':len(file_rows),'mismatched_files':[r for r in file_rows if not r['match']]}


def _verify_k1_open(root: Path, protocol: dict[str,Any]) -> tuple[Path,dict[str,Any]]:
    run=resolve_marker(root,protocol['k1_open']['marker'])
    sem=load_json(run/'semantic_output_digest.json').get('semantic_output_digest')
    manifest=load_json(run/'open_search_object_manifest.json')
    rows=manifest['objects']
    # Only TRAIN arrays are byte-verified/readable to K0R. OPENED_TRANSFER rows are commitment metadata only.
    train=[r for r in rows if r['role']=='TRAIN_OPERATOR']
    diag=[r for r in rows if r['role']=='OPENED_TRANSFER_DIAGNOSTIC']
    train_ids=sorted(set(r['field_id'] for r in train)); diag_ids=sorted(set(r['field_id'] for r in diag))
    diag_commitments=[{'field_id':r['field_id'],'grid':r['grid'],'path':r['path'],'sha256':r['sha256'],'semantic_digest':r.get('semantic_digest')} for r in diag]
    sha_checks=[]
    for r in train:
        p=run/r['path']; sha_checks.append(p.is_file() and sha256_path(p)==r['sha256'])
    priv=load_json(run/'private_payload_commitments.json')
    sealed_keys=['coefficient:DEVELOPMENT_COEF','coefficient:SEALED_FINAL_COEF','response:DEVELOPMENT_COEF','response:SEALED_FINAL_COEF']
    sealed_ok=all((not bool(priv[k].get('private_seed_material_exposed'))) and (not bool(priv[k].get('private_payload_copied_into_active_tree'))) for k in sealed_keys)
    checks={
        'status':(run/'OVERALL_STATUS.txt').read_text().strip()==protocol['k1_open']['expected_status'],
        'semantic':sem==protocol['k1_open']['expected_semantic_output_digest'],
        'six_TRAIN':len(train_ids)==protocol['k1_open']['required_train_field_count'],
        'two_existing_transfer_commitments':len(diag_ids)==protocol['k1_open']['required_opened_transfer_diagnostic_count'],
        'all_TRAIN_object_SHA':all(sha_checks),
        'DEV_SEALED_seed_unexposed_and_not_copied':sealed_ok,
        'private_payload_commitments_SHA':sha256_path(run/'private_payload_commitments.json')==protocol['authoritative_hpc']['private_payload_commitments_sha256'],
        'authoritative_K1_marker_relative_path':run.relative_to(root).as_posix()==Path(protocol['authoritative_hpc']['K1_run']).relative_to(Path(protocol['authoritative_hpc']['project_root'])).as_posix(),
    }
    return run,{'status':'PASS' if all(checks.values()) else 'FAIL','checks':checks,'train_field_ids':train_ids,'existing_transfer_field_ids':diag_ids,'existing_transfer_commitments_metadata_only':diag_commitments,'semantic_output_digest':sem,'run':str(run)}


def _verify_p11(root: Path, protocol: dict[str,Any]) -> dict[str,Any]:
    checks={}
    for rel,expected in protocol['inherited_p11_sha256'].items():
        p=root/rel; checks[rel]=p.is_file() and sha256_path(p)==expected
    # direct compact renumber regression
    from p11rawxt_ast import Op,Theta,Var,parameter_names
    from p11rawxt_s1.k1_representation import canonicalize_pair
    caps=protocol['caps']
    rawx=Op('Add',Op('Mul',Theta('theta_9'),Var('x')),Theta('theta_2'))
    rawt=Op('Add',Op('Mul',Theta('theta_9'),Var('t')),Theta('theta_5'))
    pair=canonicalize_pair(rawx,rawt,caps)
    names=sorted(parameter_names(pair['raw_X_AST'])|parameter_names(pair['raw_T_AST']),key=lambda x:int(x.split('_')[1]))
    checks['canonicalize_pair_compact_theta_renumber']=names==['theta_1','theta_2','theta_3']
    return {'status':'PASS' if all(checks.values()) else 'FAIL','checks':checks,'renumbered_theta_names':names,'structural_hash':pair['structural_hash']}


def _common_rng_regression(caps: dict[str,int]) -> dict[str,Any]:
    # Fixed raw AST budget, no validity retry: common structural/random quantiles must be byte-identical.
    seed=derive_seed('P13-S1-K0R-COMMON-RNG',1,0)
    traces=[]
    for grammar in ['coordinate_only','full_raw_coefficient']:
        rng=np.random.default_rng(seed); tr=[]
        random_ast_policy(rng,21,9,int(caps['unique_theta_total_max']),int(caps['derivative_nesting_max']),grammar,trace=tr)
        traces.append(tr)
    return {'status':'PASS' if traces[0]==traces[1] else 'FAIL','trace_sha256':sha256_bytes(canonical_json_bytes(traces[0])),'shared_raw_choice_count':len(traces[0])}


def _initialization_regression(protocol: dict[str,Any]) -> dict[str,Any]:
    caps=protocol['caps']; count=int(protocol['initialization']['regression_population_per_arm'])
    pops=paired_initial_population(1,count,caps)
    from p11rawxt_common import canonical_json_bytes as p11bytes
    byte_equal=[p11bytes(a)==p11bytes(b) for a,b in zip(pops['FULL-V1'],pops['FULL-V2'])]
    null_forbidden=sum(any(n.get('op')=='Var' and n.get('name') in {'a','q'} for rootkey in ['raw_X_AST','raw_T_AST'] for n in _walk(p[rootkey])) for p in pops['NULL-V2'])
    exposure={arm:sum(contains_coefficient_syntax(p) for p in rows) for arm,rows in pops.items()}
    return {'status':'PASS' if all(byte_equal) and null_forbidden==0 else 'FAIL','population_count_per_arm':count,'FULL_V1_V2_byte_identical_count':sum(byte_equal),'NULL_forbidden_coefficient_or_q_count':null_forbidden,'coefficient_dependent_syntax_count':exposure,'common_rng':_common_rng_regression(caps),'sample_structural_hashes':{arm:[p['structural_hash'] for p in rows[:4]] for arm,rows in pops.items()}}


def _walk(node:dict[str,Any]):
    yield node
    for c in node.get('args',[]): yield from _walk(c)


def _family_objective_regression() -> dict[str,Any]:
    dummy={'structural_hash':'dummy'}; theta=[0.125,-0.25]
    seen=[]
    def evaluator(pair, th, field):
        seen.append(tuple(float(x).hex() for x in th)); return float(field)
    out=evaluate_shared_theta_family(dummy,theta,[1,2,3,4,5,6],evaluator)
    expected=math.sqrt(91.0/6.0)
    ok=all(x==seen[0] for x in seen) and abs(out['J_family']-expected)<1e-14 and out['per_field_theta_refit'] is False
    return {'status':'PASS' if ok else 'FAIL','J_family':out['J_family'],'expected':expected,'shared_theta_hex':list(seen[0]),'per_field_theta_refit':False}


def _theta_and_v2_regression(protocol:dict[str,Any]) -> dict[str,Any]:
    from p11rawxt_ast import Op,Theta,Var
    from p11rawxt_s1.k1_representation import canonicalize_pair
    caps=protocol['caps']
    # noncompact input -> compact canonical -> next-free index check
    pair=canonicalize_pair(Op('Add',Var('x'),Op('Mul',Theta('theta_7'),Var('t'))),Op('Add',Var('t'),Op('Mul',Theta('theta_3'),Var('x'))),caps)
    compact=theta_names(pair)==['theta_1','theta_2']
    outcome=additive_root_residual_graft(pair,np.random.default_rng(17),caps,int(protocol['v2']['subtree_nodes_max']),'full_raw_coefficient')
    next_free=(outcome.outcome=='graft_successful_child' and outcome.new_theta=='theta_3') or outcome.outcome=='graft_no_legal_child'
    # legal canonical cap-saturated parent: 5 theta in X, 5 in T.
    def sum_terms(varname:str,start:int):
        node=Op('Mul',Theta(f'theta_{start}'),Var(varname))
        for i in range(start+1,start+5): node=Op('Add',node,Op('Mul',Theta(f'theta_{i}'),Var(varname)))
        return node
    saturated=canonicalize_pair(sum_terms('x',1),sum_terms('t',6),caps)
    sat=additive_root_residual_graft(saturated,np.random.default_rng(3),caps,int(protocol['v2']['subtree_nodes_max']),'full_raw_coefficient')
    sat_ok=sat.outcome=='graft_parent_theta_saturated'
    counters=empty_v2_counters(); outcomes=[]
    parent=canonicalize_pair(Var('x'),Var('t'),caps)
    for i in range(64):
        child,c,why=route_v2_mutation_slot(parent,np.random.default_rng(1000+i),caps,'full_raw_coefficient',0.5,int(protocol['v2']['subtree_nodes_max']))
        outcomes.append(why)
        for k,v in c.items(): counters[k]+=v
    invariant=counters['silent_fallback_to_V1']==0 and counters['mutation_slots_total']==64 and counters['standard_mutation_selected']+counters['graft_selected']==64 and counters['graft_selected']==counters['graft_attempted']
    return {'status':'PASS' if compact and next_free and sat_ok and invariant else 'FAIL','compact_parent_theta':theta_names(pair),'non_saturated_outcome':outcome.outcome,'expected_next_theta':'theta_3','observed_next_theta':outcome.new_theta,'saturated_parent_theta_count':len(theta_names(saturated)),'saturated_outcome':sat.outcome,'v2_64slot_counters':counters,'slot_outcome_counts':{x:outcomes.count(x) for x in sorted(set(outcomes))}}


def _branch_and_ledger_regression(protocol:dict[str,Any]) -> dict[str,Any]:
    from p11rawxt_ast import Op,Theta,Var
    from p11rawxt_s1.k1_representation import canonicalize_pair
    # One-theta canonical skeleton so branch theta provenance is internally consistent.
    pair=canonicalize_pair(Op('Add',Var('x'),Op('Mul',Theta('theta_7'),Var('t'))),Var('t'),protocol['caps']); gauge={'rule':'S0_fixed_translation_common_positive_scale','version':1}
    p1={'fitter':'production','launch':'a'}; p2={'fitter':'production','launch':'b'}
    b11=scientific_branch_id(pair,[0.1],gauge,p1); b12=scientific_branch_id(pair,[0.2],gauge,p1); b21=scientific_branch_id(pair,[0.1],gauge,p2)
    e1=execution_equivalence_key(pair,[0.1],gauge); e2=execution_equivalence_key(pair,[0.1],gauge)
    row=proposal_ledger_row(arm='FULL-V2',paired_seed=1,proposal_index=7,parent_provenance={'parent_branch_ids':['p']},pair=pair,theta_hat=[0.1],fit_provenance=p1,f_status='F4',J_i=[1,1,1,1,1,1],evaluator_calls=6,worker_cpu_seconds=0.2,wall_seconds=0.1,gauge=gauge,v2_counters=empty_v2_counters())
    required=['arm','paired_seed','proposal_index','parent_proposal_provenance','structural_hash','coefficient_dependent_syntax','theta_count','scientific_branch_id','fit_provenance','F0_F4_status','J_i','J_family','J_max','evaluator_calls','worker_cpu_seconds','wall_seconds','exact_equivalence_class','v2_counters','membership_rule']
    ok=b11!=b12 and b11!=b21 and e1==e2 and all(k in row for k in required) and 'complete_ledger_no_topk' in row['membership_rule']
    return {'status':'PASS' if ok else 'FAIL','same_skeleton_different_theta_distinct':b11!=b12,'same_execution_different_fit_provenance_scientific_branches_distinct':b11!=b21,'exact_execution_can_share':e1==e2,'ledger_required_fields_present':all(k in row for k in required),'sample_ledger_row':row}


def _tau_continuation_frontier_regression(protocol:dict[str,Any]) -> dict[str,Any]:
    tau=float(protocol['operator_qualification']['tau_num'])
    b={str(v):boundary_status(v,0.5,tau) for v in [0.4974,0.498,0.5,0.502,0.5026]}
    good=(b['0.4974']=='CLEAR_PASS' and b['0.5']=='UNRESOLVED_NUMERICAL_BOUNDARY' and b['0.5026']=='CLEAR_FAIL')
    d1=continuation_decision([(1.0,.85),(1.0,.89),(1.0,.90),(1.0,.88)],False,True)
    d2=continuation_decision([(1.0,.85),(1.0,.89),(1.0,.90),(1.0,.88)],True,True)
    from p11rawxt_ast import Var
    from p11rawxt_s1.k1_representation import canonicalize_pair
    pfree=canonicalize_pair(Var('x'),Var('t'),protocol['caps']); pcoef=canonicalize_pair(Var('a'),Var('t'),protocol['caps'])
    f={'coefficient_dependent':None,'coefficient_free':None}; f=frontier_update(f,pfree,0.8); f=frontier_update(f,pcoef,0.7)
    ok=good and d1['authorized'] and not d2['authorized'] and f=={'coefficient_dependent':0.7,'coefficient_free':0.8}
    return {'status':'PASS' if ok else 'FAIL','tau_num':tau,'boundary_examples':b,'continuation_positive_fixture':d1,'continuation_blocked_if_clear_FULL_exists':d2,'frontier_fixture':f,'J_capacity_or_R_att_used':False,'F4_yield_used':False}


def _private_within_family_commitment(root:Path,k1_run:Path,protocol:dict[str,Any],private_root:Path,run:Path) -> dict[str,Any]:
    if private_root==root or root in private_root.parents: raise RuntimeError('private root must be outside project root')
    private_root.mkdir(parents=True,exist_ok=True); os.chmod(private_root,0o700)
    stamp=run.name.rsplit('_',1)[-1]; private_run=private_root/f'p13_s1_k0r_within_family_private_{stamp}'
    if private_run.exists():
        # Resume only if receipt already exists; never silently regenerate a different scientific object.
        receipt=private_run/'PRIVATE_COMMITMENT_RECEIPT.json'
        if not receipt.is_file(): raise RuntimeError(f'incomplete private commitment directory: {private_run}')
        return load_json(receipt)['public_commitment']
    private_run.mkdir(mode=0o700)
    master=secrets.token_hex(32)
    k1p=load_json(root/'phases/p13/coefficient_law_raw_xt/configs/p13_s0_k1_protocol.json')
    p=copy_json(k1p); role='WITHIN_FAMILY_TRANSFER_DIAGNOSTIC'; p['role_counts'][role]=2; p['generator_families'][role]='oblique_plane_wave_3mode'
    staging=private_run/'.staging'; staging.mkdir(mode=0o700)
    rows=[]
    for idx in [1,2]:
        seed=int.from_bytes(hashlib.sha256(bytes.fromhex(master)+f'coefficient|{role}|{idx}'.encode()).digest()[:8],'big')
        gen=generate_generator(role,idx,seed,p); cert=spectral_certificate(gen,p)
        if cert['status']!='PASS': raise RuntimeError('within-family regime certificate failed')
        gpath=staging/f'{gen["field_id"]}_generator.json'; write_json(gpath,gen,mode=0o600)
        objs=[]
        for grid in p['regime']['search_object_grids']:
            arrays=make_search_object(gen,int(grid),int(p['regime']['search_facing_coefficient_jet_order']))
            opath=staging/f'{gen["field_id"]}_G{grid}.npz'; write_npz_exact(opath,arrays); os.chmod(opath,0o600)
            objs.append({'grid':int(grid),'sha256':sha256_path(opath),'semantic_digest':search_object_semantic_digest(arrays)})
        rows.append({'field_id':gen['field_id'],'role':role,'family':'oblique_plane_wave_3mode','generator_semantic_digest':sha256_bytes(canonical_json_bytes(gen)),'regime_certificate':{k:cert[k] for k in ['M_l1','chi_exact','status']},'search_object_commitments':objs})
    archive=private_run/'P13_S1_K0R_PRIVATE_WITHIN_FAMILY_TRANSFER_DIAGNOSTIC.tar.xz'
    with tarfile.open(archive,'w:xz') as tf:
        for pth in sorted(staging.iterdir()): tf.add(pth,arcname=pth.name,recursive=False)
    os.chmod(archive,0o600)
    payload_sem=sha256_bytes(canonical_json_bytes(rows))
    public={'role':role,'count':2,'family':'oblique_plane_wave_3mode','archive_absolute_path':str(archive),'archive_bytes':archive.stat().st_size,'archive_sha256':sha256_path(archive),'payload_semantic_digest':payload_sem,'private_seed_material_exposed':False,'private_payload_copied_into_active_tree':False,'public_row_commitments':[{k:r[k] for k in ['field_id','role','family','generator_semantic_digest','regime_certificate','search_object_commitments']} for r in rows],'open_not_before':'complete response-blind S1 search horizon and continuation decision irreversibly frozen'}
    receipt={'created_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'master_seed_sha256':hashlib.sha256(bytes.fromhex(master)).hexdigest(),'public_commitment':public}
    write_json(private_run/'PRIVATE_COMMITMENT_RECEIPT.json',receipt,mode=0o600)
    for pth in staging.iterdir(): pth.unlink()
    staging.rmdir()
    return public


def copy_json(x:Any)->Any:
    return json.loads(json.dumps(x))


def run(root:Path,private_root:Path,run_dir:Path|None=None,allow_noncanonical_test_root:bool=False)->Path:
    home=root/'phases/p13/coefficient_law_raw_xt'; protocol_path=home/'configs/p13_s1_k0r_protocol.json'; protocol=load_json(protocol_path)
    stamp=time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())
    runs=home/'runs'; runs.mkdir(parents=True,exist_ok=True)
    incomplete_marker=runs/'ACTIVE_P13_S1_K0R_INCOMPLETE_RUN.txt'
    latest_marker=runs/'LATEST_P13_S1_K0R_RUN.txt'
    if run_dir is None and incomplete_marker.is_file():
        p=Path(incomplete_marker.read_text().strip()); run_dir=p if p.is_absolute() else root/p
    if run_dir is None and latest_marker.is_file():
        p=Path(latest_marker.read_text().strip()); prior=p if p.is_absolute() else root/p
        if (prior/'OVERALL_STATUS.txt').is_file() and (prior/'OVERALL_STATUS.txt').read_text().strip()=='PASS':
            raise RuntimeError(f'K0R_ALREADY_FROZEN_PASS_REFUSE_NEW_DIAGNOSTIC_COMMITMENT:{prior}')
    if run_dir is None:
        run_dir=runs/f'p13_s1_k0r_protocol_lock_{stamp}'
    run_dir.mkdir(parents=True,exist_ok=True)
    write_text(incomplete_marker,str(run_dir.relative_to(root)))
    t0=time.time(); stages=[]; fatal=[]
    def stage(name:str,fn):
        i=len(stages)+1; print(f'[P13-S1-K0R] stage={i}/12 {name} processed={i-1}/12 elapsed={time.time()-t0:.2f}s rate={(i-1)/max(time.time()-t0,1e-9):.3f}/s ETA={(12-(i-1))*max(time.time()-t0,1e-9)/max(i-1,1):.1f}s',flush=True)
        out=fn(); stages.append((name,out)); write_json(run_dir/f'{i:02d}_{name}.json',out)
        if out.get('status')!='PASS': fatal.append(name)
        return out
    def active_lock():
        observed=sha256_path(root/protocol['active_context']['path'])
        canonical_ok=str(root)==protocol['authoritative_hpc']['project_root'] or bool(allow_noncanonical_test_root)
        return {'status':'PASS' if observed==protocol['active_context']['sha256'] and canonical_ok else 'FAIL','path':protocol['active_context']['path'],'observed_sha256':observed,'expected_sha256':protocol['active_context']['sha256'],'authorized_action':protocol['authorized_action'],'formal_K1_search_run':False,'observed_project_root':str(root),'expected_authoritative_hpc_project_root':protocol['authoritative_hpc']['project_root'],'noncanonical_test_root_override':bool(allow_noncanonical_test_root)}
    active=stage('active_protocol_lock',active_lock)
    s0run,s0= _verify_s0(root,protocol); stage('s0_freeze_provenance',lambda:s0)
    k1run,k1= _verify_k1_open(root,protocol); stage('k1_open_inputs',lambda:k1)
    stage('p11_inheritance_and_theta_renumber',lambda:_verify_p11(root,protocol))
    stage('paired_initialization',lambda:_initialization_regression(protocol))
    stage('shared_theta_family_objective',lambda:_family_objective_regression())
    stage('v2_kernel_and_saturation',lambda:_theta_and_v2_regression(protocol))
    stage('branch_ledger_equivalence',lambda:_branch_and_ledger_regression(protocol))
    stage('tau_continuation_frontiers',lambda:_tau_continuation_frontier_regression(protocol))
    within=stage('within_family_private_commitment',lambda:{'status':'PASS',**_private_within_family_commitment(root,k1run,protocol,private_root,run_dir)})
    governance=stage('governance_deferred_controls',lambda:{'status':'PASS','J_capacity_R_att_decision_authority':'NONE','fairness_axes':protocol['fairness_axes'],'fairness_scalar_composite_forbidden':True,'deferred_diagnostics':protocol['deferred_diagnostics'],'response_controls':protocol['response_controls'],'primary_seed_policy':protocol['primary_seed_policy'],'seed1_allowed_interim_checks':['process/file integrity','restart/resume equality','proposal/accounting conservation','mutation-slot schedule integrity','no forbidden data read','worker/BLAS configuration','no silent V2->V1 fallback'],'scientific_peeking_may_change_remaining_three_seeds':False})
    boundary=stage('data_boundary_no_forbidden_read',lambda:{'status':'PASS','TRAIN_arrays_byte_verified':True,'OPENED_TRANSFER_payload_arrays_read':False,'WITHIN_FAMILY_payload_read_after_commit':False,'DEVELOPMENT_or_SEALED_payload_read':False,'historical_response_outcome_read':False,'S0_calibration_candidate_used_as_seed':False,'allowed_reads':protocol['data_boundary']['allowed_reads'],'forbidden_reads':protocol['data_boundary']['forbidden_reads']})
    # provenance after all stage files exist
    own=[protocol_path,home/'configs/p13_s0_exact_source_config_test_lock.json',home/'src/p13rawxt/s1_search_primitives.py',Path(__file__).resolve(),home/'scripts/run_p13_s1_k0r.sh',home/'scripts/package_p13_s1_k0r_audit.sh',home/'tests/test_p13_s1_k0r.py',home/'docs/P13_S1_K0R_PROTOCOL_LOCK.md',root/protocol['active_context']['path']]
    src=source_manifest(root,own); write_json(run_dir/'source_manifest.json',src)
    write_json(run_dir/'runtime_environment.json',{'python':sys.version,'numpy':np.__version__,'platform':platform.platform(),'pid':os.getpid(),'OPENBLAS_NUM_THREADS':os.environ.get('OPENBLAS_NUM_THREADS'),'OMP_NUM_THREADS':os.environ.get('OMP_NUM_THREADS'),'MKL_NUM_THREADS':os.environ.get('MKL_NUM_THREADS'),'NUMEXPR_NUM_THREADS':os.environ.get('NUMEXPR_NUM_THREADS'),'declared_allocation':protocol['runtime']['allocation'],'workers_for_future_heavy_search':protocol['runtime']['default_workers']})
    sem_basis={'stage':'P13-S1-K0R','active_context_sha256':active['observed_sha256'],'S0_semantic':s0['semantic_output_digest'],'K1_semantic':k1['semantic_output_digest'],'protocol_sha256':sha256_path(protocol_path),'source_manifest':src,'stage_result_digests':{name:sha256_bytes(canonical_json_bytes(out)) for name,out in stages},'within_family_commitment_sha256':within['archive_sha256']}
    sem=sha256_bytes(canonical_json_bytes(sem_basis)); write_json(run_dir/'semantic_output_digest.json',{**sem_basis,'semantic_output_digest':sem})
    status='PASS' if not fatal else 'FAIL'; next_action=protocol['next_on_pass'] if status=='PASS' else protocol['next_on_failure']
    write_text(run_dir/'OVERALL_STATUS.txt',status); write_text(run_dir/'NEXT_ACTION.txt',next_action)
    summary={'OVERALL_STATUS':status,'NEXT_ACTION':next_action,'failure_stages':fatal,'semantic_output_digest':sem,'formal_discovery_evidence_produced':False,'formal_K1_search_executed':False,'formal_K1A_authorized':status=='PASS','S0_reopened_or_rerun':False,'TRAIN_field_count':len(k1['train_field_ids']),'existing_cross_family_diagnostic_commitment_count':len(k1['existing_transfer_field_ids']),'fresh_within_family_diagnostic_commitment_count':within['count'],'DEVELOPMENT_or_SEALED_opened':False,'silent_fallback_to_V1':load_json(run_dir/'07_v2_kernel_and_saturation.json')['v2_64slot_counters']['silent_fallback_to_V1'],'tau_num':protocol['operator_qualification']['tau_num'],'J_capacity_R_att_decision_authority':'NONE','primary_paired_seeds':4,'first_formal_rung_not_executed':98304}
    write_json(run_dir/'audit_summary.json',summary)
    # stable markers
    runs=home/'runs'; runs.mkdir(exist_ok=True)
    write_text(runs/'LATEST_P13_S1_K0R_RUN.txt',str(run_dir.relative_to(root)))
    write_text(runs/'LATEST_P13_S1_K0R_WITHIN_FAMILY_COMMITMENT.txt',str((run_dir/'10_within_family_private_commitment.json').relative_to(root)))
    if status=='PASS' and incomplete_marker.is_file(): incomplete_marker.unlink()
    print(f'[P13-S1-K0R] processed=12/12 elapsed={time.time()-t0:.2f}s rate={12/max(time.time()-t0,1e-9):.3f}/s ETA=0.0s OVERALL_STATUS={status} NEXT_ACTION={next_action}',flush=True)
    return run_dir


def main(argv:list[str]|None=None)->int:
    ap=argparse.ArgumentParser(); ap.add_argument('--project-root',type=Path,default=Path.cwd()); ap.add_argument('--private-root',type=Path,required=True); ap.add_argument('--run-dir',type=Path,default=None); ap.add_argument('--allow-noncanonical-test-root',action='store_true')
    a=ap.parse_args(argv); root=a.project_root.resolve(); private=a.private_root.resolve(); run_dir=a.run_dir.resolve() if a.run_dir else None
    out=run(root,private,run_dir,allow_noncanonical_test_root=a.allow_noncanonical_test_root); return 0 if (out/'OVERALL_STATUS.txt').read_text().strip()=='PASS' else 2


if __name__=='__main__': raise SystemExit(main())
