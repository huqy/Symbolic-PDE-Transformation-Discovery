"""Section 15 orchestration; reuse frozen proposal and qualification primitives."""
import json
import hashlib
from pathlib import Path
from .s0_contract import load, write, P
from .s1_lineage import RUN, K1, config, horizons, verify_receipt

def verify_first_rung_prefixes(project):
    """Append-only extension: the adjudicated first-rung evidence cannot be rewritten."""
    from .common import contained
    manifest = load(Path(project) / RUN / 'K2A_authoritative_input_manifest.json')
    for row in manifest['objects']:
        if not row['path'].endswith('.jsonl'): continue
        path = contained(project, row['path'], True); remaining = row['bytes']; h = hashlib.sha256()
        with path.open('rb') as f:
            while remaining:
                chunk = f.read(min(remaining, 1024 * 1024))
                if not chunk: raise ValueError('first-rung ledger shortened')
                h.update(chunk); remaining -= len(chunk)
        if h.hexdigest() != row['sha256']: raise ValueError('first-rung ledger prefix changed')

def extend(project, runner=None):
    from p13rawxt.s1_k1_formal_search import run_arm
    verify_receipt(project, 'K2A')
    project = Path(project); run = project / RUN
    decision = load(run / 'K2A_continuation_decision.json')
    if not decision['authorized']:
        return []
    if not decision['integrity_pass'] or decision['clear_FULL_operator_qualified_exists']:
        raise ValueError('invalid continuation authorization')
    verify_first_rung_prefixes(project)
    cfg = config(project, 'k1'); rule = config(project, 'k2a')['continuation_rule']
    base = load(project / P / 'configs/p13_s0_k2_protocol.json'); base['caps'] = cfg['caps']
    out = []
    for seed in cfg['paired_seeds']:
        for arm in rule['eligible_arms']:
            result = (runner or run_arm)(project, run, project / K1, base, cfg, seed, arm,
                                          cfg['runtime']['default_workers'], rule['continuation_total_horizon'])
            if result['status'] != 'PASS' or result['processed'] != rule['continuation_total_horizon']:
                raise ValueError('continuation unit incomplete')
            out.append(result)
            verify_first_rung_prefixes(project)
    write(run / 'K1C_summary.json', {'status': 'PASS', 'units': out, 'horizons': horizons(project, decision),
                                    'second_continuation_allowed': False})
    return out

def final_adjudication(project):
    """No second continuation calculation. All F4 identities enter frozen scanner."""
    from p13rawxt import s1_k2a_train_adjudication as a
    project = Path(project); run = project / RUN
    verify_receipt(project, 'K2A')
    decision = load(run / 'K2A_continuation_decision.json')
    if not decision['authorized']:
        meta = load(run / 'K2A_membership_lock.json')
        if meta.get('frozen') is not True:
            raise ValueError('first rung did not freeze membership')
        write(run / 'FINAL_TRAIN_membership.json', meta)
        return
    verify_receipt(project, 'K1C')
    cfg = config(project, 'k2a'); hs = horizons(project, decision)
    work = run / 'FINAL_work'; candidates = []; summaries = {}; identity = None
    for seed in cfg['paired_seeds']:
        for arm in cfg['arms']:
            unit = run / 'units' / f'seed_{seed:02d}' / arm
            us = load(unit / 'unit_summary.json'); total = hs[arm]
            if us['status'] != 'PASS' or us['processed'] != total or us['total'] != total:
                raise ValueError('incomplete final horizon')
            counts = us['counters']
            if counts.get('silent_fallback_to_V1', 0) or counts.get('fatal_exception_count', 0):
                raise ValueError('search integrity failure')
            ids = us['identity_baseline']['J_i']; hx = [float(x).hex() for x in ids]
            if identity is None: identity = hx
            if identity != hx: raise ValueError('identity drift between arms/seeds')
            checkpoints = sorted(set(cfg['checkpoints'] + [total]))
            prop = a._scan_proposal_ledger(unit / 'proposal_ledger.jsonl', checkpoints)
            if prop['row_count'] != total: raise ValueError('incomplete proposal ledger')
            cand = work / f'seed_{seed:02d}' / (arm + '_membership_candidates.jsonl')
            br = a._scan_branch_registry(str(unit / 'branch_registry.jsonl'), str(cand), ids,
                                         checkpoints, cfg['tau_num'], arm in cfg['full_arms'])
            if br['qualification_recompute_mismatch_count'] or br['branch_line_count'] != counts.get('F4_scientific_branches', 0) or br['branch_line_count'] != counts.get('F4_branch_emissions', 0):
                raise ValueError('incomplete F4 registry or qualification mismatch')
            with (unit / 'branch_registry.jsonl').open() as f:
                for line in f:
                    row = json.loads(line)
                    if row['arm'] != arm or row['paired_seed'] != seed or not 1 <= row['proposal_index'] <= total:
                        raise ValueError('branch outside authorized unit/horizon')
            if arm in cfg['full_arms']: candidates.append(cand)
            summaries[f'seed_{seed:02d}/{arm}'] = {'proposal': prop, 'branch': br}
            print(f'[S1 FINAL] stage=TRAIN-adjudication seed={seed} arm={arm} processed={total}/{total}', flush=True)
    meta = {'frozen': True, **a._finalize_membership(project, run, candidates)}
    write(run / 'FINAL_TRAIN_membership.json', meta)
    # Retain first-rung comparisons untouched. Extended evidence has only the matched V2 pair.
    matched = []
    from p13rawxt.s1_k2a_train_adjudication import _effect, _direction_summary
    arms = cfg['continuation_rule']['eligible_arms']
    for seed in cfg['paired_seeds']:
        left = summaries[f'seed_{seed:02d}/FULL-V2']['branch']['best_records']['any']
        right = summaries[f'seed_{seed:02d}/NULL-V2']['branch']['best_records']['any']
        matched.append({'paired_seed': seed, **_effect(None if left is None else left['J_family'],
                                                      None if right is None else right['J_family'], cfg['tau_num'])})
    write(run / 'FINAL_TRAIN_adjudication.json', {'status': 'PASS', 'horizons': hs,
          'membership': meta, 'units': summaries, 'continuation_decision_repeated': False,
          'first_rung_evidence': 'K2A_scientific_summary.json',
          'extended_equal_proposal_comparison': {'left': 'FULL-V2', 'right': 'NULL-V2',
              'horizon': hs[arms[0]], 'per_seed': matched, 'summary': _direction_summary(matched)},
          'FULL_V1_extended_equal_proposal_comparison': None})
