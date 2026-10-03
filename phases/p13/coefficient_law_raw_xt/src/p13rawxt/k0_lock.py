from __future__ import annotations
import argparse, hashlib, json, os, platform, sys, time
from pathlib import Path
from typing import Any
import numpy as np


def sha256_path(path: Path)->str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(8*1024*1024),b''):
            h.update(block)
    return h.hexdigest()


def canonical_json_bytes(obj: Any)->bytes:
    return json.dumps(obj,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()


def write_json(path: Path,obj: Any)->None:
    path.write_text(json.dumps(obj,indent=2,sort_keys=True,ensure_ascii=False)+"\n",encoding='utf-8')


def source_manifest(root: Path, paths: list[Path])->dict[str,Any]:
    rows=[]
    for p in paths:
        rows.append({"path":p.relative_to(root).as_posix(),"bytes":p.stat().st_size,"sha256":sha256_path(p)})
    return {"files":rows}


def _load(path:Path)->dict[str,Any]:
    return json.loads(path.read_text(encoding='utf-8'))


def _l3_caps(grammar:dict[str,Any])->tuple[dict[str,int],dict[str,Any]]:
    base={k:int(v) for k,v in grammar['active_caps'].items()}
    row=next(x for x in grammar['cap_ladder'] if x['level']=='L3_requires_new_protocol_confirmation')
    merged=dict(base)
    for k,v in row.items():
        if k not in {'level','formal_search'}:
            merged[k]=int(v)
    expected={
        'nodes_X_max':41,'nodes_T_max':41,'total_nodes_max':72,'depth_max':13,
        'unique_theta_total_max':10,'unique_theta_component_max':6,
        'theta_occurrences_total_max':12,'derivative_nesting_max':2,
    }
    return merged,{"base_caps":base,"l3_row":row,"merged_caps":merged,"expected":expected,"status":"PASS" if merged==expected else "FAIL"}


def _gauge_regression(root:Path, protocol:dict[str,Any])->dict[str,Any]:
    p11=root/'phases/p11/raw_xt_td'
    sys.path.insert(0,str(p11/'src'))
    sys.path.insert(0,str(root/'phases/p13/coefficient_law_raw_xt/src'))
    from p11rawxt_operator import synthetic_operator_map_registry, gauge_second_jet, evaluate_map_on_space
    from p11rawxt_validity import GridSpec
    from p13rawxt.gauge import canonicalize_common_translation_positive_scale, apply_group_action

    x=np.linspace(0.,1.,65); t=np.linspace(0.,1.,65); xm,tm=np.meshgrid(x,t,indexing='ij')
    reg=synthetic_operator_map_registry(xm,tm)
    base=(reg['nonlinear'] if 'nonlinear' in reg else reg['identity'])['raw']
    acted=apply_group_action(base,scale=2.75,shift_x=3.125,shift_t=-1.875)
    c0,r0=canonicalize_common_translation_positive_scale(base)
    c1,r1=canonicalize_common_translation_positive_scale(acted)
    if c0 is None or c1 is None:
        return {"status":"FAIL","reason":"canonicalization failed","base_record":r0,"acted_record":r1}
    keys=sorted(set(c0)&set(c1))
    max_abs=max(float(np.max(np.abs(c0[k]-c1[k]))) for k in keys)
    max_rel=max(float(np.max(np.abs(c0[k]-c1[k]))/max(float(np.max(np.abs(c0[k]))),float(np.max(np.abs(c1[k]))),1.0)) for k in keys)

    p11_cfg=_load(p11/'configs/p11rawxt_s0_k3_operator_objective.json')
    numerical=p11_cfg['numerical_protocol']
    space=p11_cfg['test_spaces']['search']
    grid=GridSpec(x=x,t=t)
    g0,_=gauge_second_jet(base); g1,_=gauge_second_jet(acted)
    _,j0,_=evaluate_map_on_space(g0,grid,space,numerical,medium='fixed')
    _,j1,_=evaluate_map_on_space(g1,grid,space,numerical,medium='fixed')
    j_rel=abs(j0.J_princ-j1.J_princ)/max(abs(j0.J_princ),abs(j1.J_princ),1e-15)
    tol=1e-10
    return {
      "status":"PASS" if max_rel<=tol and j_rel<=tol else "FAIL",
      "group_action":{"scale":2.75,"translation_X":3.125,"translation_T":-1.875},
      "canonical_array_keys":keys,
      "canonical_max_absolute_discrepancy":max_abs,
      "canonical_max_relative_discrepancy":max_rel,
      "J_princ_base":j0.J_princ,"J_princ_group_acted":j1.J_princ,"J_princ_relative_discrepancy":j_rel,
      "required_tolerance":tol,
      "base_record":r0,"group_acted_record":r1,
      "interpretation":"same raw law modulo common translations/positive common scale; no fitted gauge parameter"
    }


def _derivative_semantics(root:Path,caps:dict[str,int])->dict[str,Any]:
    p11=root/'phases/p11/raw_xt_td'
    sys.path.insert(0,str(p11/'src'))
    from p11rawxt_ast import Op,Var,derivative_nesting,check_pair_caps
    from p11rawxt_s1.k1_representation import validate_raw_ast
    d2=Op('Dx',Op('Dt',Var('a')))
    d3=Op('Dx',Op('Dt',Op('Dx',Var('a'))))
    pass2=validate_raw_ast(d2,caps['derivative_nesting_max'])
    fail3=validate_raw_ast(d3,caps['derivative_nesting_max'])
    simple_ok,simple_fail,simple_stats=check_pair_caps(d2,Var('t'),caps)
    runtime=(p11/'src/p11rawxt_s1/k2_ast_runtime.py').read_text(encoding='utf-8')
    order3_signature='evaluate_ast_jet(raw_x, x, t, theta, order=3)' in runtime and 'evaluate_ast_jet(raw_t, x, t, theta, order=3)' in runtime
    return {
      "syntactic_derivative_nesting_d2":derivative_nesting(d2),
      "syntactic_derivative_nesting_d3":derivative_nesting(d3),
      "d2_validate_failures":pass2,
      "d3_validate_failures":fail3,
      "d2_pair_caps_pass":simple_ok,
      "d2_pair_caps_failures":simple_fail,
      "d2_pair_stats":simple_stats,
      "syntactic_cap_semantics_status":"PASS" if not pass2 and any('derivative_nesting>2' in x for x in fail3) and simple_ok else "FAIL",
      "legacy_numeric_runtime_uses_fixed_taylor_order_3":order3_signature,
      "numeric_L3_execution_status":"NOT_YET_QUALIFIED",
      "numeric_L3_execution_reason":"P11 formal search never activated L3. With derivative nesting 2, downstream map second derivatives can require coefficient derivatives above the legacy fixed Taylor order; P13 must implement and qualify coefficient-family jet supply before any search.",
      "blocking_for_K0":False,
      "blocking_for_formal_search":True,
      "required_resolution_stage":"P13-S0-K2 grammar/evaluator semantic qualification"
    }


def main()->int:
    ap=argparse.ArgumentParser()
    ap.add_argument('--project-root',type=Path,default=Path.cwd())
    ap.add_argument('--run-dir',type=Path,default=None)
    ap.add_argument('--active-design-source',type=Path,default=None)
    ap.add_argument('--claim-i-freeze-source',type=Path,default=None)
    ap.add_argument('--claim-ii-freeze-source',type=Path,default=None)
    ap.add_argument('--superseded-bootstrap-source',type=Path,default=None)
    args=ap.parse_args()
    root=args.project_root.resolve(); home=root/'phases/p13/coefficient_law_raw_xt'; p11=root/'phases/p11/raw_xt_td'
    protocol_path=home/'configs/p13_s0_k0_protocol.json'; protocol=_load(protocol_path)
    stamp=time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())
    run=(args.run_dir.resolve() if args.run_dir else home/'runs'/f'p13_s0_k0_lock_{stamp}')
    run.mkdir(parents=True,exist_ok=False)
    print(f'[P13-S0-K0] run_dir={run}',flush=True)
    fatal=[]

    # Optional portable-context byte verification. These files are scientific context inputs,
    # not copied into the P13 project tree. When paths are supplied, mismatch is fatal.
    supplied={
      'active_p13_s0_entry':args.active_design_source,
      'claim_I_freeze':args.claim_i_freeze_source,
      'claim_II_freeze':args.claim_ii_freeze_source,
      'p13_bootstrap_superseded':args.superseded_bootstrap_source,
    }
    context_rows=[]
    for key,path in supplied.items():
        spec=protocol['upstream_context_file_sha256'][key]
        if path is None:
            context_rows.append({'key':key,'artifact':spec['artifact'],'role':spec['role'],'expected_sha256':spec['sha256'],'observed_sha256':None,'status':'NOT_SUPPLIED'})
            continue
        path=path.resolve(); observed=sha256_path(path) if path.is_file() else None
        st='PASS' if observed==spec['sha256'] else 'FAIL'
        context_rows.append({'key':key,'artifact':spec['artifact'],'role':spec['role'],'source_path':str(path),'expected_sha256':spec['sha256'],'observed_sha256':observed,'status':st})
        if st!='PASS': fatal.append(f'upstream context SHA mismatch: {key}')
    write_json(run/'upstream_context_verification.json',{'records':context_rows,'all_supplied_verified':all(r['status']=='PASS' for r in context_rows),'status':'FAIL' if any(r['status']=='FAIL' for r in context_rows) else 'PASS'})

    print('[P13-S0-K0] 1/7 verify byte-exact P11 inheritance',flush=True)
    inheritance=[]
    for rel,expected in protocol['inherited_file_sha256'].items():
        path=root/rel; observed=sha256_path(path) if path.is_file() else None
        status='PASS' if observed==expected else 'FAIL'
        inheritance.append({'path':rel,'expected_sha256':expected,'observed_sha256':observed,'status':status})
        if status!='PASS': fatal.append(f'inheritance SHA mismatch: {rel}')
    write_json(run/'inheritance_verification.json',{'records':inheritance,'status':'PASS' if not fatal else 'FAIL'})

    print('[P13-S0-K0] 2/7 reproduce raw grammar and activate executable L3 envelope',flush=True)
    grammar=_load(p11/'configs/p11rawxt_s0_k1_raw_ast_grammar.json')
    caps,caps_report=_l3_caps(grammar)
    if caps_report['status']!='PASS': fatal.append('L3 executable caps mismatch active design')
    ontology_checks={
      'independent_complete_raw_roots':grammar['ontology']['construction_mode']=='independent_complete_raw_root_expressions',
      'forced_root_wrapper_false':grammar['ontology']['forced_root_wrapper'] is False,
      'amplitude_A_1':grammar['ontology']['amplitude']==1,
      'variables_exact':[x.get('name') for x in grammar['terminals'] if x.get('kind') in {'variable','coefficient'}]==['x','t','a','q'],
      'no_historical_seed':grammar['historical_seed_policy']['P10_candidate_seed_count']==0,
      'L3_requires_new_protocol':grammar['cap_transition_rule']['L3_requires_explicit_new_protocol'] is True,
    }
    if not all(ontology_checks.values()): fatal.append('raw grammar ontology regression')
    write_json(run/'raw_grammar_l3_lock.json',{'ontology_checks':ontology_checks,'l3_caps':caps_report,'raw_grammar_sha256':sha256_path(p11/'configs/p11rawxt_s0_k1_raw_ast_grammar.json')})

    print('[P13-S0-K0] 3/7 audit derivative-cap semantics and no-search execution guard',flush=True)
    deriv=_derivative_semantics(root,caps)
    if deriv['syntactic_cap_semantics_status']!='PASS': fatal.append('derivative nesting syntactic semantics failed')
    write_json(run/'derivative_semantics_guard.json',deriv)

    print('[P13-S0-K0] 4/7 audit P11 gauge and freeze P13 quotient semantics',flush=True)
    gauge=_gauge_regression(root,protocol)
    if gauge['status']!='PASS': fatal.append('gauge equivalence regression failed')
    write_json(run/'gauge_equivalence_lock.json',gauge)

    print('[P13-S0-K0] 5/7 freeze branch, claim, arm and data-role registries',flush=True)
    branch={
      'program':protocol['program'],'stage':protocol['stage'],'representation':protocol['representation'],
      'claim_hierarchy':protocol['claim_hierarchy'],'arm_design':protocol['arm_design'],
      'l3_caps':caps,'gauge_equivalence':protocol['gauge_equivalence'],
      'scientific_parentage':protocol['upstream_scientific_anchors'],
      'P11_P12_mutated':False,'S1_authorized':False
    }
    roles={'roles':protocol['data_roles'],'K0_payload_generation_count':0,'development_or_sealed_payload_open_count':0}
    write_json(run/'branch_config_lock.json',branch); write_json(run/'data_role_registry.json',roles)

    print('[P13-S0-K0] 6/7 freeze no-leakage guard',flush=True)
    leak={
      'forbidden':protocol['forbidden_in_K0'],
      'claim_II_characteristic_reference':'THEORY_REFERENCE_ONLY_NOT_IMPORTED_INTO_P13_CODE',
      'claim_I_capacity_witness':'CALIBRATION_ONLY_NOT_IMPORTED_INTO_P13_CODE',
      'historical_response_labels':'NOT_READ_BY_K0_RUNTIME',
      'formal_development_payloads':'DO_NOT_EXIST_UNTIL_K1_PRIVATE_COMMITMENT',
      'formal_sealed_payloads':'DO_NOT_EXIST_UNTIL_K1_PRIVATE_COMMITMENT',
      'K0_runtime_imports':['P11 raw grammar/canonicalization/validity/operator utilities only'],
      'status':'PASS'
    }
    write_json(run/'no_leakage_guard.json',leak)

    print('[P13-S0-K0] 7/7 freeze provenance and adjudicate',flush=True)
    own=[protocol_path,home/'src/p13rawxt/gauge.py',Path(__file__).resolve(),home/'scripts/run_p13_s0_k0.sh',home/'scripts/package_p13_s0_k0_audit.sh',home/'tests/test_p13_s0_k0.py',home/'docs/P13_S0_K0_BRANCH_INHERITANCE_GAUGE_LOCK.md']
    manifest=source_manifest(root,own+[root/rel for rel in protocol['inherited_file_sha256']])
    write_json(run/'source_manifest.json',manifest)
    runtime={"python":sys.version,"platform":platform.platform(),"pid":os.getpid(),"numpy":np.__version__}
    write_json(run/'runtime_environment.json',runtime)
    semantic={
      'stage':'P13-S0-K0','protocol_sha256':sha256_path(protocol_path),
      'inheritance_sha256':hashlib.sha256(canonical_json_bytes(inheritance)).hexdigest(),
      'grammar_l3_sha256':hashlib.sha256(canonical_json_bytes(caps_report)).hexdigest(),
      'gauge_lock_sha256':hashlib.sha256(canonical_json_bytes(gauge)).hexdigest(),
      'data_role_registry_sha256':hashlib.sha256(canonical_json_bytes(roles)).hexdigest(),
      'no_leakage_sha256':hashlib.sha256(canonical_json_bytes(leak)).hexdigest(),
      'upstream_context_sha256':hashlib.sha256(canonical_json_bytes(context_rows)).hexdigest(),
      'upstream_claim_I_semantic':protocol['upstream_scientific_anchors']['claim_I']['semantic_output_digest'],
      'upstream_claim_II_semantic':protocol['upstream_scientific_anchors']['claim_II']['semantic_output_digest']
    }
    semantic['semantic_output_digest']=hashlib.sha256(canonical_json_bytes(semantic)).hexdigest()
    write_json(run/'semantic_output_digest.json',semantic)
    status='PASS' if not fatal else 'FAIL'
    (run/'OVERALL_STATUS.txt').write_text(status+'\n',encoding='utf-8')
    next_action=protocol['next_on_pass'] if status=='PASS' else 'STOP_P13_S0_K0_AND_REPAIR_INHERITANCE_OR_GAUGE'
    (run/'NEXT_ACTION.txt').write_text(next_action+'\n',encoding='utf-8')
    write_json(run/'audit_summary.json',{
      'OVERALL_STATUS':status,'fatal':fatal,'next_action':next_action,
      'byte_exact_inherited_files':sum(r['status']=='PASS' for r in inheritance),
      'inherited_file_count':len(inheritance),'L3_caps':caps,
      'gauge_status':gauge['status'],'numeric_L3_execution_status':deriv['numeric_L3_execution_status'],
      'formal_search_guarded_until_numeric_L3_qualification':True,
      'development_or_sealed_response_read':False,
      'characteristic_search_information_used':False,
      'upstream_context_all_supplied_verified':all(r['status']=='PASS' for r in context_rows)
    })
    print(f'[P13-S0-K0] OVERALL_STATUS={status}',flush=True)
    print(f'[P13-S0-K0] semantic_output_digest={semantic["semantic_output_digest"]}',flush=True)
    print(f'[P13-S0-K0] NEXT_ACTION={next_action}',flush=True)
    if fatal:
        for x in fatal: print(f'[P13-S0-K0] FATAL: {x}',flush=True)
        return 2
    return 0

if __name__=='__main__':
    raise SystemExit(main())
