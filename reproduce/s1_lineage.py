"""Fresh S1 receipts and immutable membership. No historical result constants."""
from pathlib import Path
from .common import contained, digest, file_sha256
from .s0_contract import load, write, P

RUN = P + 'runs/fresh_s1'
K0 = P + 'runs/fresh_s1_k0'
K1 = P + 'runs/fresh_s0_k1'
STEPS = ('K0', 'K1A', 'K1B', 'K2A', 'K1C', 'FINAL', 'K2B', 'K2C', 'K3', 'PF0', 'PF1')
POST = ('K2B', 'K2C', 'K3', 'PF0', 'PF1')

def config(project, stage):
    return load(Path(project) / P / 'configs' / ('p13_s1_' + stage.lower() + '_protocol.json'))

def context(project):
    project = Path(project).resolve(strict=True)
    obj = load(project.parent / 's1_execution.json')
    if project.name != 'project' or obj['schema'] != 'P13_S1_EXECUTION_V1':
        raise ValueError('invalid S1 execution')
    return obj

def record(project, path, rows=False):
    path = contained(project, Path(path).relative_to(project).as_posix(), True)
    out = {'path': path.relative_to(project).as_posix(), 'sha256': file_sha256(path), 'bytes': path.stat().st_size}
    if rows:
        with path.open('rb') as stream:
            out['count'] = sum(1 for line in stream if line.strip())
    return out

def check_record(project, row):
    path = contained(project, row['path'], True)
    if record(project, path, 'count' in row) != row:
        raise ValueError('immutable artifact changed: ' + row['path'])
    return path

def seal(obj):
    return dict(obj, digest=digest(obj))

def unseal(obj):
    if digest({k: v for k, v in obj.items() if k != 'digest'}) != obj.get('digest'):
        raise ValueError('receipt/lock digest mismatch')
    return obj

def receipt_path(project, step):
    if step not in STEPS:
        raise ValueError('unknown S1 step')
    return Path(project).parent / 'receipts' / (step + '.json')

def verify_receipt(project, step):
    r = unseal(load(receipt_path(project, step)))
    if r['step'] != step or r['execution_id'] != context(project)['execution_id']:
        raise ValueError('mixed S1 execution')
    for s, value in r['parents'].items():
        previous = unseal(load(receipt_path(project, s)))
        if previous['digest'] != value or previous['execution_id'] != r['execution_id']:
            raise ValueError('fresh parent changed')
    for row in r['outputs']:
        check_record(project, row)
    return r

def complete(project, step, paths, semantic=None):
    parents = {}
    for s in STEPS[:STEPS.index(step)]:
        if receipt_path(project, s).exists():
            parents[s] = verify_receipt(project, s)['digest']
    obj = seal({'schema': 'P13_S1_RECEIPT_V1', 'execution_id': context(project)['execution_id'],
                'step': step, 'parents': parents, 'outputs': [record(project, p) for p in sorted(set(paths))],
                'semantic_digest': semantic, 'historical_outcomes_used': False})
    p = receipt_path(project, step)
    if p.exists() and load(p) != obj:
        raise ValueError('refuse receipt overwrite')
    write(p, obj)
    return obj

def horizons(project, decision):
    first = config(project, 'k1')['budget']['proposals_per_seed_per_arm']
    rule = config(project, 'k2a')['continuation_rule']
    return {arm: (rule['continuation_total_horizon'] if decision['authorized'] and arm in rule['eligible_arms'] else first)
            for arm in config(project, 'k1')['arms']}

def membership_path(project):
    return Path(project).parent / 'membership_lock.json'

def freeze_membership(project):
    """Coordinator only, after FINAL receipt; bind registries as well as locators."""
    final = verify_receipt(project, 'FINAL')
    run = Path(project) / RUN
    decision = load(run / 'K2A_continuation_decision.json')
    meta = load(run / 'FINAL_TRAIN_membership.json')
    if meta.get('frozen') is not True:
        raise ValueError('TRAIN membership not final')
    files = {key: record(project, Path(project) / meta[key + '_membership_index'], True)
             for key in ('clear', 'unresolved')}
    for key in files:
        if files[key]['count'] != meta[key + '_FULL_branch_count']:
            raise ValueError('final membership row count mismatch')
    units = []
    for seed in config(project, 'k1')['paired_seeds']:
        for arm, total in horizons(project, decision).items():
            unit = run / 'units' / f'seed_{seed:02d}' / arm
            summary = load(unit / 'unit_summary.json')
            if summary['status'] != 'PASS' or summary['processed'] != total or summary['total'] != total:
                raise ValueError('cannot freeze incomplete authorized horizon')
            units.extend(record(project, unit / name, name.endswith('.jsonl')) for name in
                         ('proposal_ledger.jsonl', 'branch_registry.jsonl', 'skeleton_registry.jsonl', 'equivalence_map.jsonl', 'unit_summary.json'))
    obj = seal({'schema': 'P13_S1_MEMBERSHIP_V1', 'execution_id': context(project)['execution_id'],
                'membership': files, 'registries': units, 'horizons': horizons(project, decision),
                'continuation': decision, 'parents': {'FINAL': final['digest']},
                'membership_authority': 'UNCHANGED_TRAIN_OPERATOR_HARD_GATES'})
    path = membership_path(project)
    if path.exists() and load(path) != obj:
        raise ValueError('membership lock overwrite refused')
    write(path, obj)
    return obj

def verify_membership(project, registries=True):
    obj = unseal(load(membership_path(project)))
    if obj['execution_id'] != context(project)['execution_id']:
        raise ValueError('membership from another execution')
    if verify_receipt(project, 'FINAL')['digest'] != obj['parents']['FINAL']:
        raise ValueError('membership parent changed')
    for row in list(obj['membership'].values()) + (obj['registries'] if registries else []):
        check_record(project, row)
    if obj['horizons'] != horizons(project, obj['continuation']):
        raise ValueError('horizon drift')
    return obj

def diagnostic_capability(project, step):
    if step != 'K2B':
        raise PermissionError('diagnostic payload is K2B-only')
    return verify_membership(project)

def fresh_science(project):
    lock = verify_membership(project)
    n = lock['membership']['clear']['count']
    u = lock['membership']['unresolved']['count']
    return {'clear_count': n, 'unresolved_count': u, 'horizons': lock['horizons'],
            'TRAIN_clear_exists': n > 0,
            'TRAIN_hard_gate_evidence': 'NONEMPTY_CLEAR_COHORT' if n else ('UNRESOLVED_ONLY' if u else 'NO_CLEAR_OR_UNRESOLVED_FULL_BRANCH'),
            'route_interpretation': 'UNRESOLVED_REPRO_INTERPRETATION',
            'claim_III_B': 'UNTESTED', 'claim_III_C': 'UNTESTED', 'claim_III_D': 'UNTESTED',
            'membership_lock_digest': lock['digest']}
