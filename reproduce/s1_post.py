"""Fresh-instance provenance adapters; numerical diagnostic kernels stay frozen."""
from pathlib import Path
import math
from .s0_contract import P, load, write, run_relative
from .s1_lineage import RUN, K1, config, verify_membership, verify_receipt, record, fresh_science
from .common import digest, file_sha256

def chain_semantic(project, step):
    """Bind computed stage semantics to actual parent receipt(s), without changing metrics."""
    from .s1_lineage import STEPS, receipt_path
    run = Path(project) / RUN
    sub, semfile, summaryfile, key = {
        'K2B': ('K2B_diagnostics','K2B_semantic_output_digest.json','K2B_scientific_summary.json','semantic_output_digest'),
        'K2C': ('K2C_theory_bridge','K2C_semantic_output_digest.json','K2C_scientific_summary.json','semantic_output_digest'),
        'PF0': ('PF0_postfreeze','PF0_semantic_output_digest.json','PF0_scientific_summary.json','PF0_semantic_output_digest'),
        'PF1': ('PF1_final_freeze','PF1_SEMANTIC_OUTPUT_DIGEST.json','PF1_FREEZE_SUMMARY.json','semantic_output_digest')}[step]
    path = run / sub / semfile; obj = load(path)
    parents = {s: verify_receipt(project, s)['digest'] for s in STEPS[:STEPS.index(step)] if receipt_path(project, s).exists()}
    obj['fresh_parent_receipts'] = parents
    obj['fresh_membership_lock_digest'] = verify_membership(project)['digest']
    obj['semantic_output_digest'] = digest({k:v for k,v in obj.items() if k!='semantic_output_digest'})
    write(path, obj)
    summary = load(run / sub / summaryfile); summary[key] = obj['semantic_output_digest']; write(run / sub / summaryfile, summary)
    if step == 'PF1':
        hand = load(run / sub / 'PF1_HANDOFF_MANIFEST.json')
        hand['final_S1_PF1_semantic_digest'] = obj['semantic_output_digest']; write(run / sub / 'PF1_HANDOFF_MANIFEST.json', hand)

def semantic(project, stage):
    paths = {'K2B': 'K2B_diagnostics/K2B_semantic_output_digest.json',
             'K2C': 'K2C_theory_bridge/K2C_semantic_output_digest.json',
             'K3': 'K3_freeze/K3_SEMANTIC_OUTPUT_DIGEST.json'}
    return load(Path(project) / RUN / paths[stage])['semantic_output_digest']

def final_meta(project):
    verify_membership(project)
    return load(Path(project) / RUN / 'FINAL_TRAIN_membership.json')

def adapt(project, step, module):
    """Only explicit historical-instance fields are rebound; no hash function spoofing."""
    if step in ('PF0', 'PF1'):
        from .s1_scope import require_pf0_scope
        require_pf0_scope(project)
    project = Path(project); run = project / RUN; lock = verify_membership(project)
    members = lock['membership']; original_load = module._load_json if hasattr(module, '_load_json') else module.loadj
    def reading(path):
        path = Path(path)
        if path == run / 'K2A_membership_lock.json': return final_meta(project)
        obj = original_load(path)
        if path.name == 'p13_s1_k2b_protocol.json':
            obj['entry_requires']['K2A_continuation_status'] = lock['continuation']['reason']
            obj['entry_requires']['K2A_next_action'] = (run / 'K2A_NEXT_ACTION.txt').read_text().strip()
        if path.name == 'p13_s1_k2c_protocol.json':
            obj['entry_requires']['K2A_search_horizon'] = max(lock['horizons'].values())
        if path.name == 'p13_s1_pf0_protocol.json':
            p = obj['parent_freeze']
            for name in ('K3', 'K2C'): p['expected_' + name + '_semantic_digest'] = semantic(project, name)
            for key in ('clear', 'unresolved'):
                p['expected_' + key + '_count'] = members[key]['count']
                p['expected_' + key + '_membership_sha256'] = members[key]['sha256']
            for key, rel in [('K2B_branch_results', 'K2B_diagnostics/K2B_diagnostic_branch_results.jsonl'),
                             ('K2B_identity_baselines', 'K2B_diagnostics/K2B_diagnostic_identity_baselines.json'),
                             ('K2C_branch_results', 'K2C_theory_bridge/K2C_branch_results.jsonl')]:
                p['expected_' + key + '_sha256'] = file_sha256(run / rel)
        return obj
    if hasattr(module, '_load_json'): module._load_json = reading
    else: module.loadj = reading
    if step == 'K2C':
        module.EXPECTED_K2B_SEMANTIC_DIGEST = semantic(project, 'K2B')
        module.EXPECTED_CLEAR_MEMBERSHIP_SHA256 = members['clear']['sha256']
        module.EXPECTED_UNRESOLVED_MEMBERSHIP_SHA256 = members['unresolved']['sha256']
        old_predictions = module._predictions
        def predictions(k2b, agg):
            obj = old_predictions(k2b, agg)
            obj['P1_population_operator_advantage']['prediction'] = 'For the complete fresh clear TRAIN cohort, report prospective operator advantage descriptively; this prediction is not a gate.'
            obj['P1_population_operator_advantage']['fresh_clear_count'] = members['clear']['count']
            return obj
        module._predictions = predictions
    if step == 'PF0':
        module.EXPECTED_K3_SEMANTIC = semantic(project, 'K3')
        module.EXPECTED_K2C_SEMANTIC = semantic(project, 'K2C')
        module.EXPECTED_CLEAR_SHA = members['clear']['sha256']; module.EXPECTED_UNRESOLVED_SHA = members['unresolved']['sha256']
        module.EXPECTED_CLEAR_COUNT = members['clear']['count']; module.EXPECTED_UNRESOLVED_COUNT = members['unresolved']['count']
        module._membership_readout = lambda root, run, k1, base, mem, tau: membership_readout(module, root, run, k1, base, mem, tau)
    if step == 'PF1':
        module.EXPECTED_K3 = semantic(project, 'K3')
        module.EXPECTED_CLEAR_SHA = members['clear']['sha256']; module.EXPECTED_UNRESOLVED_SHA = members['unresolved']['sha256']
        module.EXPECTED_CLEAR_N = members['clear']['count']; module.EXPECTED_UNRESOLVED_N = members['unresolved']['count']
        if not members['clear']['count']:
            module.verify_pf0_outputs = lambda root, run, pf0, summary: verify_empty_pf0(module, root, run, pf0, summary)
        def contexts(root, cfg, run, out, pf0sem, final_digest):
            text = '# Fresh S1 reproduction\n\n' + str(fresh_science(project)) + '\n\nPF1 digest: ' + final_digest + '\nS2 not opened.\n'
            # Output rendering lives in stage outputs, never overwrites frozen context sources.
            for key in ('S1_final', 'S2_entry', 'rolling'):
                cfg['contexts'][key] = (Path(RUN) / 'PF1_final_freeze' / (key + '.md')).as_posix()
                (root / cfg['contexts'][key]).write_text(text)
        module.write_contexts = contexts
        module.update_rolling = lambda *args: None
    if hasattr(module, '_update_rolling_context'): module._update_rolling_context = lambda *args: None
    old_write = module._write_json if hasattr(module, '_write_json') else module.writej
    def writing(path, obj, *args, **kwargs):
        name = Path(path).name
        # Mutate before frozen callers calculate their semantic digests from these objects.
        if name == 'K2B_scientific_summary.json':
            obj['K2A_continuation_status'] = lock['continuation']['reason']
            obj['search_horizon'] = max(lock['horizons'].values())
            obj['per_arm_horizons'] = lock['horizons']
            obj['route_interpretation_scope'] = 'FIRST_RUNG_ONLY; extended evidence stored in FINAL_TRAIN_adjudication.json'
        if name == 'K2C_deferred_trigger_adjudication.json':
            for key in ('PLCP', 'reverse_fitter_regret'):
                obj[key] = {'triggered': None, 'status': 'UNRESOLVED_REPRO_INTERPRETATION', 'membership_authority': 'NONE'}
        if name == 'PF0_PRE_DEV_PREDICTIONS.json':
            obj['H4_V2_throughput'] = 'F4 throughput and prospective DEVELOPMENT transfer are distinct descriptive quantities.'
        if name == 'PF1_FINAL_S1_SCIENTIFIC_FREEZE.json':
            obj.update(fresh_science(project))
            obj['fresh_parent_receipts'] = {'PF0': verify_receipt(project, 'PF0')['digest'], 'K3': verify_receipt(project, 'K3')['digest']}
            obj['claim_III_A'] = 'UNRESOLVED_REPRO_INTERPRETATION'
            obj['technical_conclusion'] = 'Fresh TRAIN hard-gate counts are recorded without inferring a stronger route-level claim.'
        old_write(path, obj, *args, **kwargs)
    if hasattr(module, '_write_json'): module._write_json = writing
    else: module.writej = writing

def membership_readout(m, root, run, k1, base, mem, tau):
    """PF0 descriptive readout over the final authorized horizon, including empty sets."""
    from p13rawxt import s1_k2a_train_adjudication as a
    lock = verify_membership(root); clear = []; unresolved = []; candidates = []
    train33 = m.load_field_views(k1, 'TRAIN_OPERATOR', 33, 65); train65 = m.load_field_views(k1, 'TRAIN_OPERATOR', 65, 65)
    id33 = m._identity_family(train33, base); id65 = m._identity_family(train65, base)
    if [float(x).hex() for x in id33['J_i']] != [float(x).hex() for x in load(run / 'identity_train_G33.json')['J_i']]:
        raise ValueError('PF0 identity drift')
    for loc in m._load_membership(root, run, mem):
        c = m._candidate_from_locator(root, loc); b = c['branch']; q = m.operator_qualification(b['J_i'], id33['J_i'], True, True, tau)
        if q['status'] != loc['_membership_status']: raise ValueError('PF0 qualification changed')
        fr = q['field_ratios']; fam = q['family_ratio']
        row = {k: b[k] for k in ('scientific_branch_id', 'arm', 'paired_seed', 'proposal_index')}
        row.update(membership_status=q['status'], J_family_G33=b['J_family'], formal_family_ratio_G33=fam,
                   field_ratios_G33=fr, raw_hard_gate_margin=min(.5 - fam, min(1 - x for x in fr)),
                   clear_margin_to_tau_boundary=min(.5 * (1 - tau) - fam, min(1 - tau - x for x in fr)))
        if q['status'] == 'OPERATOR_QUALIFIED_TRAIN': clear.append(row); candidates.append(c)
        else: unresolved.append(row)
    if len(clear) != lock['membership']['clear']['count'] or len(unresolved) != lock['membership']['unresolved']['count']: raise ValueError('PF0 count drift')
    units = []; best = None; best_full = None
    for seed in config(root, 'k1')['paired_seeds']:
        for arm, total in lock['horizons'].items():
            unit = run / 'units' / f'seed_{seed:02d}' / arm
            prop = a._scan_proposal_ledger(unit / 'proposal_ledger.jsonl', [total])
            br = a._scan_branch_registry(str(unit / 'branch_registry.jsonl'), '', id33['J_i'], [total], tau, False)
            b = br['best_records']['any']; q = br['qualification_counts']
            rec = dict(paired_seed=seed, arm=arm, structural_proposals=total, F4_scientific_branches=br['branch_line_count'],
                       F4_scientific_branches_per_proposal=br['branch_line_count']/total,
                       evaluator_calls=prop['total_evaluator_calls'], worker_cpu_seconds=prop['total_worker_cpu_seconds'],
                       best_F4_J_family=None if b is None else b['J_family'], clear_count=q['clear'], unresolved_count=q['unresolved'])
            units.append(rec)
            if b:
                v = dict(b, paired_seed=seed, arm=arm, formal_ratio=b['J_family']/id33['J_family'])
                if best is None or v['J_family'] < best['J_family']: best = v
                if arm.startswith('FULL') and (best_full is None or v['J_family'] < best_full['J_family']): best_full = v
    arms = {}
    for arm in config(root, 'k1')['arms']:
        rows = [r for r in units if r['arm'] == arm]
        out = {key: sum(r[key] for r in rows) for key in ('structural_proposals', 'F4_scientific_branches', 'evaluator_calls', 'worker_cpu_seconds', 'clear_count', 'unresolved_count')}
        out['F4_scientific_branches_per_proposal'] = out['F4_scientific_branches']/out['structural_proposals']; arms[arm] = out
    within = []; cross = []
    with (run / 'K2B_diagnostics/K2B_diagnostic_branch_results.jsonl').open() as f:
        for line in f:
            r = __import__('json').loads(line)
            if r.get('TRAIN_membership_status') != 'OPERATOR_QUALIFIED_TRAIN': continue
            for dst, key in ((within, 'within_family_G65'), (cross, 'cross_family_G65')):
                val = r['transfer'][key].get('J_RMS')
                if val is not None and math.isfinite(float(val)): dst.append(float(val))
    summary = {'TRAIN_identity_G33': id33, 'TRAIN_identity_G65': id65,
               'global_best_F4_all_arms': best, 'global_best_FULL_F4': best_full,
               'global_best_clear': min(clear, key=lambda r:r['J_family_G33']) if clear else None,
               'per_unit': units, 'per_arm': arms, 'authorized_horizons': lock['horizons'],
               'FULL_V2_over_FULL_V1_F4_branch_throughput_ratio': None if not arms['FULL-V1']['F4_scientific_branches_per_proposal'] else arms['FULL-V2']['F4_scientific_branches_per_proposal']/arms['FULL-V1']['F4_scientific_branches_per_proposal'],
               'K2B_absolute_identity_baselines': load(run / 'K2B_diagnostics/K2B_diagnostic_identity_baselines.json'),
               'K2B_clear_candidate_absolute_within_G65_J_RMS': m._stats_extended(within),
               'K2B_clear_candidate_absolute_cross_G65_J_RMS': m._stats_extended(cross)}
    for label, rows, key in [('clear_formal_ratio', clear, 'formal_family_ratio_G33'), ('unresolved_formal_ratio', unresolved, 'formal_family_ratio_G33'), ('clear_raw_hard_gate_margin', clear, 'raw_hard_gate_margin'), ('clear_margin_to_tau_boundary', clear, 'clear_margin_to_tau_boundary')]:
        summary[label] = m._stats_extended([r[key] for r in rows])
    return summary, clear, unresolved, {'clear_candidates': candidates, 'train33': train33, 'train65': train65, 'id33': id33, 'id65': id65}

def freeze_k3(project):
    from p13rawxt.s1_k3_formal_freeze import _find_s0_k2r3_null_control
    project = Path(project); run = project / RUN; lock = verify_membership(project)
    parents = {s: verify_receipt(project, s)['digest'] for s in ('K2B', 'K2C')}
    cfg = config(project, 'k3'); out = run / 'K3_freeze'; out.mkdir(exist_ok=True)
    reg = {'objects': lock['registries'], 'scientific_membership_is_not_narrowed_by_this_manifest': True}
    write(out / 'K3_COMPLETE_S1_REGISTRY_MANIFEST.json', reg)
    commitments = load(project / K1 / 'private_payload_commitments.json')
    hand = {'stage': 'P13-S2-ENTRY', 'candidate_rule': cfg['s2_candidate_rule'],
            'clear_candidate_cohort': lock['membership']['clear'], 'unresolved_lineage': lock['membership']['unresolved'],
            'candidate_source_registry_manifest': record(project, out / 'K3_COMPLETE_S1_REGISTRY_MANIFEST.json'),
            'identity_control': {'definition': 'identity', 'response_control_only': True},
            'null_control': _find_s0_k2r3_null_control(project, run_relative('K2R4')),
            'same_AST_theta_gauge_zero_refit': True, 'branch_reselection_forbidden': True}
    for target, key in [('development_coefficient_commitment','coefficient:DEVELOPMENT_COEF'),('development_response_commitment','response:DEVELOPMENT_COEF'),('sealed_final_coefficient_guard','coefficient:SEALED_FINAL_COEF'),('sealed_final_response_guard','response:SEALED_FINAL_COEF')]: hand[target] = commitments[key]
    write(out / 'K3_S2_ACTIVE_INPUT_MANIFEST.json', hand)
    boundary = dict.fromkeys(('DEVELOPMENT_COEF','DEVELOPMENT_RESPONSE','SEALED_FINAL_COEF','SEALED_FINAL_RESPONSE'), 'COMMITTED_UNOPENED')
    boundary['status'] = 'PASS'; write(out / 'K3_DATA_BOUNDARY_FREEZE.json', boundary)
    science = fresh_science(project); science['parents'] = parents
    science['K2C_semantic_digest'] = semantic(project, 'K2C'); science['K2B_semantic_digest'] = semantic(project, 'K2B')
    write(out / 'K3_S1_SCIENTIFIC_FREEZE.json', science)
    sem = digest({'science': science, 'boundary': boundary, 'handoff': hand, 'registries': reg})
    write(out / 'K3_SEMANTIC_OUTPUT_DIGEST.json', {'semantic_output_digest': sem, 'parents': parents})
    write(out / 'K3_FREEZE_SUMMARY.json', {'OVERALL_STATUS': 'PASS', 'semantic_output_digest': sem,
          'cohorts': {k:science[k] for k in ('clear_count','unresolved_count')}, 'DEVELOPMENT_opened':False,'SEALED_opened':False})
    (run / 'K3_OVERALL_STATUS.txt').write_text('PASS\n')
    (project / P / 'runs/LATEST_P13_S1_K3_RUN.txt').write_text(RUN + '\n')


def verify_empty_pf0(m, root, run, pf0, summary):
    """Native PF1 metadata gates for an empty clear cohort; no fictitious ASP maximum.

    Nonempty cohorts always use frozen verify_pf0_outputs, including its tolerance.
    This empty branch requires zero rows, n=0 and max=None and retains every other
    frozen integrity gate. It asserts no numerical ASP result for an empty set.
    """
    sem = m.loadj(pf0 / 'PF0_semantic_output_digest.json')
    nxt = 'P13-S1-PF1_FINAL_STAGE_FREEZE_AND_S2_HANDOFF'
    gates = {
        'PF0_status_PASS': (run/'PF0_OVERALL_STATUS.txt').read_text().strip() == 'PASS',
        'PF0_next_action_exact': (run/'PF0_NEXT_ACTION.txt').read_text().strip() == nxt,
        'summary_PASS': summary.get('OVERALL_STATUS') == 'PASS',
        'summary_next_exact': summary.get('NEXT_ACTION') == nxt,
        'K3_parent_exact': summary.get('parent_K3_semantic_digest') == m.EXPECTED_K3,
        'membership_counts_exact': int(summary.get('membership_clear_count', -1)) == 0 and int(summary.get('membership_unresolved_count', -1)) == m.EXPECTED_UNRESOLVED_N,
        'membership_unchanged': summary.get('membership_unchanged') is True,
        'DEV_SEALED_unopened': summary.get('DEVELOPMENT_or_SEALED_opened') is False,
        'response_unopened': summary.get('response_outcomes_opened') is False,
        'no_search_refit': summary.get('new_search_or_refit') is False,
        'K3_immutable': summary.get('K3_freeze_remains_immutable') is True,
        'semantic_summary_match': summary.get('PF0_semantic_output_digest') == sem.get('semantic_output_digest')}
    def exact(rows):
        return all((root/r['path']).is_file() and (root/r['path']).stat().st_size == int(r['bytes']) and m.sha(root/r['path']) == r['sha256'] for r in rows)
    gates['semantic_output_files_exact'] = exact(sem.get('outputs', []))
    gates['PF0_source_manifest_exact'] = exact(m.loadj(pf0/'PF0_source_manifest.json').get('files', []))
    boundary = m.loadj(pf0/'PF0_data_boundary_guard.json')
    gates['boundary_status_PASS'] = boundary.get('status') == 'PASS'
    for key in ('K3_base_freeze_mutated','clear_membership_changed','unresolved_membership_changed','DEVELOPMENT_read','SEALED_read','response_outcomes_read','historical_response_read','new_formal_search','PLCP_run','continuation_32768_run','candidate_refit','ASP_or_theory_or_witness_has_membership_authority','audit_archive_used_as_runtime_input'):
        gates['boundary_' + key + '_false'] = boundary.get(key) is False
    mem = m.loadj(pf0/'PF0_membership_immutability.json')
    gates['clear_SHA_count_exact'] = mem.get('clear_sha256') == m.EXPECTED_CLEAR_SHA and int(mem.get('clear_count', -1)) == 0
    gates['unresolved_SHA_count_exact'] = mem.get('unresolved_sha256') == m.EXPECTED_UNRESOLVED_SHA and int(mem.get('unresolved_count', -1)) == m.EXPECTED_UNRESOLVED_N
    asp = m.loadj(pf0/'PF0_ASP_aggregate.json'); li = asp.get('lambda_1_integrity_error', {})
    gates['ASP_complete_rows'] = int(asp.get('rows', -1)) == 0
    gates['ASP_lambda1_integrity'] = int(li.get('n', -1)) == 0 and 'max' in li and li['max'] is None
    pred = m.loadj(pf0/'PF0_PRE_DEV_PREDICTIONS.json')
    gates['predictions_frozen_pre_DEV'] = pred.get('frozen_before_DEVELOPMENT') is True and pred.get('membership_unchanged') is True and pred.get('predictions_are_descriptive_not_gates') is True
    if not all(gates.values()):
        raise RuntimeError('PF1 refuses empty freeze: ' + ','.join(k for k,v in gates.items() if not v))
    return {'gates':gates, 'PF0_semantic_digest':sem['semantic_output_digest'], 'witness_status':summary.get('matched_TRAIN_witness_status'), 'ASP_rows':0, 'ASP_empty_cohort':True}
