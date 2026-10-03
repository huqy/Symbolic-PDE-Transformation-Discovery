"""One isolated S1 scientific child; normal imported modules preserve pool pickling."""
import argparse
import importlib
import os
import shutil
from contextlib import contextmanager
from pathlib import Path
from .s0_contract import P, load, write, run_relative
from .s1_lineage import RUN, K0, K1, STEPS, POST, context, config, verify_receipt, verify_membership

MODULES = {'K1A':'s1_k1_formal_search', 'K1B':'s1_k1b_remaining_seeds', 'K2A':'s1_k2a_train_adjudication',
           'K2B':'s1_k2b_post_search_diagnostics', 'K2C':'s1_k2c_theory_bridge',
           'PF0':'s1_pf0_postfreeze_diagnostics', 'PF1':'s1_pf1_final_freeze'}

@contextmanager
def k2a_cleanup(project):
    """Use path traversal only for the frozen K2A checkpoint cleanup target.

    Python 3.10 open audit events omit dir_fd. Non-fd traversal supplies the
    complete paths to the unchanged guard. Scientific workers have joined
    before frozen K2A calls rmtree. All other targets retain stdlib behavior.
    """
    target = (Path(project) / RUN / 'K2A_work').resolve()
    original = shutil.rmtree
    def rmtree(path, *args, **kwargs):
        if Path(os.fsdecode(path)).resolve() != target:
            return original(path, *args, **kwargs)
        fd_policy = shutil._use_fd_functions
        try:
            shutil._use_fd_functions = False
            return original(path, *args, **kwargs)
        finally:
            shutil._use_fd_functions = fd_policy
    shutil.rmtree = rmtree
    try:
        yield
    finally:
        shutil.rmtree = original

@contextmanager
def k0_marker_writer(project, module):
    """Keep the legacy K0 incomplete marker inside its explicit mutable run."""
    legacy = project / P / 'runs/ACTIVE_P13_S1_K0R_INCOMPLETE_RUN.txt'
    local = project / K0 / legacy.name
    original = module.write_text
    def write_text(path, text):
        return original(local if path == legacy else path, text)
    module.write_text = write_text
    try:
        yield local
        local.unlink(missing_ok=True)
    finally:
        # Exceptions retain the incomplete marker for the explicit K0 rerun.
        module.write_text = original

def k0(project):
    from p13rawxt import s1_k0r_protocol_lock as m
    from .s1_regression import restart_regression
    ctx = context(project); cfg = config(project, 'k0r'); k3 = project / run_relative('K3')
    # Fresh S0 has been verified before projection and is pinned by the execution lock.
    def verify_s0(root, protocol):
        adjud = load(k3 / 's0_gate_adjudication.json'); entry = load(k3 / 's1_entry_manifest.json')
        from .s1_launcher import validate_entry
        validate_entry(entry, adjud, config(root, 'k1'))
        return k3, {'status':'PASS','semantic_output_digest':load(k3/'semantic_output_digest.json')['semantic_output_digest'],
                    'fresh_parent_receipt':ctx['s0']['K3_receipt']['receipt_digest'], 'exact_source_lock':ctx['baseline']}
    def verify_k1(root, protocol):
        from .s1_k2b_interface import cross_commitment_metadata
        manifest = load(root / K1 / 'open_search_object_manifest.json')
        train = [r for r in manifest['objects'] if r['role']=='TRAIN_OPERATOR']
        diag = [r for r in manifest['objects'] if r['role']=='OPENED_TRANSFER_DIAGNOSTIC']
        from .common import contained, file_sha256
        if len({r['field_id'] for r in train}) != protocol['k1_open']['required_train_field_count']: raise ValueError('TRAIN count')
        if len({r['field_id'] for r in diag}) != protocol['k1_open']['required_opened_transfer_diagnostic_count']: raise ValueError('diagnostic commitments count')
        for r in train:
            if file_sha256(contained(root / K1, r['path'], True)) != r['sha256']: raise ValueError('TRAIN SHA')
        return root / K1, {'status':'PASS', 'semantic_output_digest':load(root/K1/'semantic_output_digest.json')['semantic_output_digest'],
                          'train_field_ids':sorted({r['field_id'] for r in train}), 'existing_transfer_field_ids':sorted({r['field_id'] for r in diag}),
                          'existing_transfer_commitments_metadata_only':cross_commitment_metadata(root)}
    m._verify_s0 = verify_s0; m._verify_k1_open = verify_k1
    def commitment(*args):
        obj = load(project / 'reproduce/s1_commitment.json')
        if obj['archive_sha256'] != ctx['diagnostic_commitment']['sha256']: raise ValueError('commitment mismatch')
        obj['archive_absolute_path'] = ctx['diagnostic_archive']; return obj
    m._private_within_family_commitment = commitment
    original = m._branch_and_ledger_regression
    def ledgers(protocol):
        obj = original(protocol)
        obj['restart_regression'] = restart_regression(project / K0 / 'regression', config(project, 'k1'))
        if obj['restart_regression']['status'] != 'PASS': obj['status'] = 'FAIL'
        return obj
    m._branch_and_ledger_regression = ledgers
    with k0_marker_writer(project, m):
        result = m.run(project, project.parent / 'never_open_private', project / K0, allow_noncanonical_test_root=True)
        if (result / 'OVERALL_STATUS.txt').read_text().strip() != 'PASS': raise ValueError('K0 did not PASS')

def run(project, step):
    project = Path(project).resolve(); ctx = context(project)
    if step in ('PF0', 'PF1'):
        from .s1_scope import require_pf0_scope
        require_pf0_scope(project)
    prior = [s for s in STEPS[:STEPS.index(step)] if not (s=='K1C' and not load(project/RUN/'K2A_continuation_decision.json')['authorized'])]
    for s in prior: verify_receipt(project, s)
    if step in POST: verify_membership(project)
    if step == 'K0': return k0(project)
    if step == 'K1C':
        from .s1_continuation import extend
        return extend(project)
    if step == 'FINAL':
        from .s1_continuation import final_adjudication
        return final_adjudication(project)
    if step == 'K3':
        from .s1_post import freeze_k3
        return freeze_k3(project)
    m = importlib.import_module('p13rawxt.' + MODULES[step])
    if hasattr(m, '_update_rolling_context'): m._update_rolling_context = lambda *args: None
    if step in POST:
        from .s1_post import adapt
        adapt(project, step, m)
    if step == 'K2A':
        with k2a_cleanup(project):
            rc = m.main(['--project-root', str(project), '--workers', str(config(project, 'k1')['runtime']['default_workers'])])
    elif step == 'K2B':
        from .s1_k2b_interface import k2b_metadata
        with k2b_metadata(project, m):
            rc = m.main(['--project-root', str(project), '--workers', str(config(project, 'k1')['runtime']['default_workers'])])
    elif step == 'PF1': rc = m.run(project)
    else: rc = m.main(['--project-root', str(project), '--workers', str(config(project, 'k1')['runtime']['default_workers'])])
    if rc != 0: raise RuntimeError(step + ' failed')
    if step in ('K2B', 'K2C', 'PF0'):
        from .s1_post import chain_semantic
        chain_semantic(project, step)

def main():
    p = argparse.ArgumentParser(); p.add_argument('--project-root', type=Path, required=True); p.add_argument('--step', choices=STEPS, required=True)
    a = p.parse_args()
    if os.environ.get('P13_S1_PROJECT') != str(a.project_root.resolve()) or os.environ.get('P13_S1_STEP') != a.step:
        raise RuntimeError('S1 child requires guarded launcher')
    from .s1_io import ACTIVE_POLICY
    if ACTIVE_POLICY != (str(a.project_root.resolve()), a.step):
        raise RuntimeError('S1 audit policy was not installed')
    for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):
        if os.environ.get(key) != '1': raise RuntimeError('one BLAS/OpenMP thread required')
    run(a.project_root, a.step)

if __name__ == '__main__': main()
