"""Fresh S2 lineage and public claim contract; no numerical implementation."""
import json
from pathlib import Path
from .common import contained, digest, file_sha256
from .s0_contract import P, load, write
from .s1_lineage import RUN as S1_RUN, unseal, seal, check_record

RUN = P + 'runs/fresh_s2'
STEPS = tuple('K' + str(i) for i in range(8))
DIRS = ('K0_development_open_lock', 'K1_zero_shot_operator_transfer',
        'K2_III_B_adjudication_response_entry_lock', 'K3_response_protocol_fidelity_control_lock',
        'K4_development_response_reference_control_first', 'K5_complete_candidate_response_certification',
        'K6_post_response_diagnostics', 'K7_formal_s2_freeze')
OP = ('OPERATOR_TRANSFER_PASS', 'OPERATOR_TRANSFER_UNRESOLVED', 'OPERATOR_TRANSFER_FAIL')
RESP = ('RESPONSE_PASS', 'RESPONSE_UNRESOLVED', 'RESPONSE_FAIL')
MODULES = ('s2_k0_dev_open_protocol_lock', 's2_k1_operator_transfer', 's2_k2_adjudication',
           's2_k3_response_protocol_lock', 's2_k4_response_reference_controls',
           's2_k5_candidate_response_certification', 's2_k6_post_response_diagnostics',
           's2_k7_formal_s2_freeze')

def rows(path):
    with Path(path).open() as f:
        return [json.loads(line) for line in f if line.strip()]

def write_rows(path, values):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    with tmp.open('w') as f:
        for value in values:
            f.write(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n')
    tmp.replace(path)

def record(path):
    path = Path(path)
    if path.is_symlink(): raise ValueError('symlink input refused')
    return {'path': str(path.resolve()), 'sha256': file_sha256(path), 'bytes': path.stat().st_size}

def verify_record(row):
    path = Path(row['path'])
    if record(path) != row: raise ValueError('input/output drift: ' + str(path))
    return path

def exact_ids(values, expected):
    ids = [r['scientific_branch_id'] for r in values]
    exp = [r['scientific_branch_id'] for r in expected]
    if len(ids) != len(set(ids)) or ids != exp:
        raise ValueError('incomplete/reordered/duplicate scientific cohort')
    return ids

def handoff(root, verify_registries=False):
    """Metadata and locator authentication only; no DEVELOPMENT/archive reads."""
    root = Path(root).resolve(strict=True); project = root / 'project'
    ctx = load(root / 's1_execution.json')
    lock = unseal(load(root / 'membership_lock.json'))
    expected = ('K0','K1A','K1B','K2A','FINAL','K2B','K2C','K3','PF0','PF1')
    receipts = {}
    for step in expected:
        r = unseal(load(contained(root, 'receipts/' + step + '.json', True)))
        if r['execution_id'] != ctx['execution_id'] or r['step'] != step:
            raise ValueError('mixed S1 lineage')
        if r['parents'] != {s: receipts[s]['digest'] for s in receipts}:
            raise ValueError('incomplete S1 receipt ancestry')
        receipts[step] = r
    if lock['execution_id'] != ctx['execution_id'] or lock['parents']['FINAL'] != receipts['FINAL']['digest']:
        raise ValueError('membership parent mismatch')
    if lock['continuation']['authorized'] is not False:
        raise ValueError('S2 requires audited continuation=false lineage')
    for row in lock['membership'].values(): check_record(project, row)
    if verify_registries:
        for row in lock['registries']: check_record(project, row)
    manifest = project / S1_RUN / 'PF1_final_freeze/PF1_S2_ACTIVE_INPUT_MANIFEST.json'
    bound = next((r for r in receipts['PF1']['outputs'] if r['path'] == str(manifest.relative_to(project))), None)
    if bound is None: raise ValueError('PF1 ACTIVE manifest is not receipt-bound')
    check_record(project, bound); active = load(manifest)
    for flag, value in [('role','ACTIVE_S2_INPUT_MANIFEST_AFTER_PF0'),
                        ('same_AST_theta_gauge_zero_refit',True), ('branch_reselection_forbidden',True),
                        ('PF0_has_membership_authority',False), ('response_stage_blocked_at_entry',True)]:
        if active.get(flag) != value: raise ValueError('PF1 contract drift: ' + flag)
    for label, key in [('clear','clear_membership'), ('unresolved','unresolved_lineage')]:
        if any(active[key][k] != lock['membership'][label][k] for k in ('path','sha256','count')):
            raise ValueError('PF1 membership mismatch')
    if active['unresolved_lineage'].get('S2_eligible') is not False:
        raise ValueError('S1 unresolved branches cannot enter S2')
    # Metadata-only dry-run reads receipt headers, not descriptive data stores.
    # Execution/resume authenticates these fresh S1 inputs before any child reads.
    descriptive = []
    for step, rel in [('FINAL', S1_RUN+'/FINAL_TRAIN_membership.json'),
                      ('K2B', S1_RUN+'/K2B_diagnostics/K2B_diagnostic_branch_results.jsonl'),
                      ('PF0', S1_RUN+'/PF0_postfreeze/PF0_theory_geometry_rows.jsonl'),
                      ('PF0', S1_RUN+'/PF0_postfreeze/PF0_ASP_branch_results.jsonl')]:
        bound = next((r for r in receipts[step]['outputs'] if r['path']==rel), None)
        if bound is None: raise ValueError('S1 descriptive interface is not receipt-bound: '+rel)
        descriptive.append(dict(bound, path=str(contained(project,rel,True))))
        if verify_registries or step=='FINAL': check_record(project,bound)
    clear = rows(project / active['clear_membership']['path']); exact_ids(clear, clear)
    commits = load(Path(__file__).with_name('s2_public_commitments.json'))['commitments']
    for key, name in [('coefficient:DEVELOPMENT_COEF','DEVELOPMENT_coefficient_commitment'),
                      ('response:DEVELOPMENT_COEF','DEVELOPMENT_response_commitment')]:
        for field in ('archive_bytes','archive_sha256','payload_semantic_digest','row_count','kind','role'):
            if active[name][field] != commits[key][field]: raise ValueError('public commitment mismatch')
    null = active['null_control']
    for key in ('instrument_lock','pair_builder_source'):
        row = null[key]; p = contained(project, row['path'], True)
        if file_sha256(p) != row['sha256']: raise ValueError('frozen NULL drift')
    parent = Path(ctx['s0']['root']) / 'project'
    contract = parent / P / 'runs/fresh_s0_k1/objective_response_contract_lock.json'
    # Authenticate against the existing S0 K1 receipt, without reading S0 arrays.
    s0 = load(parent.parent / 'receipts/K1.json')
    if s0 != ctx['s0']['K1_receipt']: raise ValueError('S0 response contract parent changed')
    if digest({k:v for k,v in s0.items() if k != 'receipt_digest'}) != s0['receipt_digest']:
        raise ValueError('S0 contract receipt unsealed')
    row = s0['artifacts']['objective_response_contract_lock.json']
    if file_sha256(contract) != row['sha256'] or contract.stat().st_size != row['bytes']:
        raise ValueError('response contract drift')
    header = {'root':str(root), 'execution_id':ctx['execution_id'],
              'PF1_receipt_digest':receipts['PF1']['digest'], 'membership_digest':lock['digest'],
              'clear_count':len(clear), 'unresolved_count':lock['membership']['unresolved']['count'],
              'source_commit':ctx['source_commit'],
              'clear_exact_execution_classes':load(project / S1_RUN / 'FINAL_TRAIN_membership.json')['clear_FULL_exact_execution_class_count'],
              'pins':[record(root / 's1_execution.json'), record(root / 'membership_lock.json'), record(manifest),
                      record(contract), *[record(project / r['path']) for r in lock['membership'].values()],
                      *[record(root / 'receipts' / (s + '.json')) for s in expected]],
              'descriptive_pins':descriptive}
    return header, active, clear, lock

def stage_dir(project, step):
    return Path(project) / RUN / DIRS[STEPS.index(step)]

def state(project): return unseal(load(Path(project).parent / 's2_execution.json'))

def receipt(project, step):
    rec = unseal(load(Path(project).parent / 'receipts' / (step + '.json')))
    ctx = state(project)
    if rec['step'] != step or rec['execution_id'] != ctx['execution_id']:
        raise ValueError('mixed S2 receipt')
    expected = {s:unseal(load(Path(project).parent/'receipts'/(s+'.json')))['digest']
                for s in STEPS[:STEPS.index(step)]}
    if rec['parents'] != expected: raise ValueError('missing or altered S2 parents')
    if rec['S1_parent'] != ctx['s1']['PF1_receipt_digest']: raise ValueError('S1 parent changed')
    for row in rec['outputs']: verify_record(row)
    return rec

def finish(project, step):
    ctx = state(project); d = stage_dir(project, step)
    summary = load(d / (step + '_SCIENTIFIC_SUMMARY.json'))
    if summary['OVERALL_STATUS'] != 'PASS': raise ValueError('stage not complete')
    sem = load(d / (step + '_SEMANTIC_OUTPUT_DIGEST.json'))['semantic_output_digest']
    if sem != summary['semantic_output_digest']: raise ValueError('summary semantic mismatch')
    marker = load(d/'REPRODUCTION_STAGE_COMPLETE.json')
    expected_parents={s:receipt(project,s)['digest'] for s in STEPS[:STEPS.index(step)]}
    if marker != {'schema':'P13_S2_COMPLETE_MARKER_V1','execution_id':ctx['execution_id'],
                  'stage':step,'semantic_output_digest':sem,'parents':expected_parents}:
        raise ValueError('wrapper completion marker mismatch')
    if digest(load(d/(step+'_SEMANTIC_OUTPUT_DIGEST.json'))['basis'])!=sem:
        raise ValueError('unsealed semantic basis')
    obj = seal({'schema':'P13_S2_RECEIPT_V1','execution_id':ctx['execution_id'],'step':step,
                'parents':{s:receipt(project,s)['digest'] for s in STEPS[:STEPS.index(step)]},
                'S1_parent':ctx['s1']['PF1_receipt_digest'], 'semantic_digest':sem,
                'outputs':[record(p) for p in sorted(d.rglob('*')) if p.is_file() and p.relative_to(d).parts[0]!='work'],
                'historical_outcomes_used':False})
    path = Path(project).parent/'receipts'/(step+'.json')
    if path.exists() and load(path) != obj: raise ValueError('immutable receipt overwrite')
    write(path, obj); return obj

def sealed_boundary(response=False):
    return {'DEVELOPMENT_COEF':'OPENED_K0_READ_IN_PLACE',
            'DEVELOPMENT_RESPONSE':'OPENED_K4_USED_K5' if response else 'SEALED_COMMITTED_UNOPENED',
            'SEALED_FINAL_COEF':'SEALED_COMMITTED_UNOPENED','SEALED_FINAL_RESPONSE':'SEALED_COMMITTED_UNOPENED',
            'candidate_refit':False,'branch_reselection':False,'top_k_or_proxy_filter':False,
            'historical_response_information_read':False,'diagnostics_have_membership_authority':False}

def claim_evidence(op_counts, response_counts, controls, complete=True):
    """Evidence flags only. External audit decides claim compatibility / R-C."""
    return {'complete_frozen_protocol_campaign':complete,
            'nonempty_operator_PASS':op_counts.get(OP[0],0)>0,
            'discriminative_controls':all(controls.get(k,{}).get('decision')==RESP[2] for k in ('identity','frozen_null')),
            'nonempty_response_PASS':response_counts.get(RESP[0],0)>0,
            'external_claim_adjudication':'PENDING_EXTERNAL_POST_COMPUTATION_AUDIT',
            'R_C':'NOT_AUTOMATICALLY_ASSIGNED', 'R_D':'REPORTED_SEPARATELY_OPTIONAL',
            'S3_required_for_full_public_readiness':True, 'SEALED_opened':False}
