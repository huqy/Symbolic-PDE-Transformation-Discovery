"""K0 restart regression: frozen ledger loop with synthetic fitting, no search fitness."""
import json
import shutil
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import patch
from .s0_contract import load, write

def restart_regression(directory, protocol):
    from p13rawxt import s1_k1_formal_search as s
    from p13rawxt.s1_k2a_train_adjudication import _scan_proposal_ledger
    from p11rawxt_ast import Var
    from p11rawxt_s1.k1_representation import canonicalize_pair
    directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
    pair = canonicalize_pair(Var('x'), Var('t'), protocol['caps'])
    batch = protocol['budget']['batch_size']; checkpoint = protocol['runtime']['checkpoint_every_batches']
    total = batch * (checkpoint + 2); fail_at = batch * (checkpoint + 1) + 1
    class Inline:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def submit(self, fn, task):
            result = Future(); idx = task['proposal_index']
            result.set_result({'fit': {'all_F4_branches': [], 'completed_calls': idx % 3 + 1},
                              'worker_cpu_seconds': idx * .000123456789, 'worker_wall_seconds': .001})
            return result
    interrupted = [False]
    def generate(arm, seed, idx, cfg, islands, duplicates):
        if interrupted[0] and idx == fail_at: raise InterruptedError('synthetic allocation end')
        return {'pair': pair, 'island': 0, 'proposal_index': idx, 'proposal_status': 'SYNTHETIC', 'operation': 'REGRESSION_ONLY'}
    base = directory / 'baseline'; resumed = directory / 'resumed'
    for run in (base, resumed):
        # These two directories contain only synthetic mock-loop outputs, never science.
        if run.exists(): shutil.rmtree(run)
        run.mkdir(exist_ok=True)
        write(run / 'identity_train_G33.json', {'J_i':[1.] * 6, 'J_family':1.})
    with patch.object(s, 'ProcessPoolExecutor', Inline), patch.object(s, 'generate_proposal', generate):
        s.run_arm(directory, base, directory, {}, protocol, 1, 'FULL-V2', 1, total)
        interrupted[0] = True
        try: s.run_arm(directory, resumed, directory, {}, protocol, 1, 'FULL-V2', 1, total)
        except InterruptedError: pass
        else: raise AssertionError('interruption fixture failed')
        unit = resumed / 'units/seed_01/FULL-V2'
        state = load(unit / 'checkpoint_state.json')
        if state['processed'] != checkpoint * batch: raise AssertionError('checkpoint location')
        if (unit / 'proposal_ledger.jsonl').stat().st_size <= state['ledger_offsets']['proposal_ledger.jsonl']:
            raise AssertionError('fixture needs uncommitted ledger tail')
        interrupted[0] = False
        s.run_arm(directory, resumed, directory, {}, protocol, 1, 'FULL-V2', 1, total)
    for name in ('proposal_ledger.jsonl','skeleton_registry.jsonl','branch_registry.jsonl','equivalence_map.jsonl'):
        if (base / 'units/seed_01/FULL-V2' / name).read_bytes() != (unit / name).read_bytes():
            raise AssertionError('restart ledger mismatch: ' + name)
    stats = _scan_proposal_ledger(unit / 'proposal_ledger.jsonl', [total])
    rows = [json.loads(line) for line in (unit / 'proposal_ledger.jsonl').read_text().splitlines()]
    if stats['row_count'] != total or stats['total_worker_cpu_seconds'] != sum(r['worker_cpu_seconds'] for r in rows):
        raise AssertionError('CPU accounting mismatch')
    if stats['total_evaluator_calls'] != sum(i % 3 + 1 for i in range(1, total + 1)):
        raise AssertionError('evaluator accounting mismatch')
    return {'status':'PASS', 'synthetic_only':True, 'processed':total,
            'checkpoint_processed':state['processed'], 'ledger_cpu_seconds':stats['total_worker_cpu_seconds'],
            'evaluator_calls':stats['total_evaluator_calls'], 'formal_search_run':False}
