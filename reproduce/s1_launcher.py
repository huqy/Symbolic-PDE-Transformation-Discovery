"""Thin, isolated S1 launcher. Dry-run never launches scientific computation."""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from .common import SOURCE_ROOT, contained, file_sha256, digest
from .s0_contract import P, load, write, run_relative
from .s0_launcher import check_execution_root, check_source, verify_snapshot
from .s1_lineage import (RUN, K0, K1, STEPS, POST, config, context, record, complete,
                         receipt_path, verify_receipt, freeze_membership, verify_membership)
from .s1_scope import STATUS as PF0_SCOPE_STATUS, EXIT_CODE as PF0_SCOPE_EXIT, stop_after_k3

THREADS = ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS')

def validate_entry(entry, adjud, cfg):
    if entry.get('status') != 'AUTHORIZED' or entry.get('authorized_action') != 'P13-S1-K0_FORMAL_SEARCH_IMPLEMENTATION_REGRESSION':
        raise ValueError('S0 authorizes no S1 K0')
    if entry.get('formal_S1_K1_search_authorized') is not False or adjud.get('S1_K1_formal_search_authorized') is not False:
        raise ValueError('S0 must not preauthorize K1')
    if adjud.get('status') != 'PASS' or adjud.get('S1_K0_authorized') is not True:
        raise ValueError('S0 K3 did not PASS')
    rung = entry['first_formal_rung_after_S1_K0_PASS']
    if rung['arms'] != cfg['arms'] or rung['paired_seeds'] != len(cfg['paired_seeds']) or rung['structural_proposals_per_seed_per_arm'] != cfg['budget']['proposals_per_seed_per_arm']:
        raise ValueError('S0/S1 protocol mismatch')
    if entry['active_inputs']['K1_run'] != run_relative('K1'):
        raise ValueError('handoff points outside fresh S0 lineage')

def s0_inputs(project, step, names):
    """Authenticate S0 receipt headers and ONLY the explicitly selected inputs.

    The generic S0 parent verifier hashes all artifacts, including diagnostic
    arrays. S1 must not read those arrays until FINAL; metadata commitments suffice
    before opening. Selected bytes are verified against the authenticated receipt.
    """
    from .s0_contract import context as s0_context, STEPS as s0_steps, DEPS
    project, lock = s0_context(project); receipts = {}
    for prior in s0_steps[:s0_steps.index(step) + 1]:
        rec = load(contained(project.parent, 'receipts/' + prior + '.json', True))
        if rec['execution_id'] != lock['execution_id'] or rec['step'] != prior or rec['run_relative'] != run_relative(prior):
            raise ValueError('mixed S0 receipt')
        if digest({k:v for k,v in rec.items() if k != 'receipt_digest'}) != rec['receipt_digest']:
            raise ValueError('S0 receipt digest mismatch')
        if set(rec['parent_receipt_digests']) != set(DEPS[prior]): raise ValueError('S0 receipt parents missing')
        for parent_step, expected in rec['parent_receipt_digests'].items():
            if receipts[parent_step]['receipt_digest'] != expected: raise ValueError('S0 parent digest mismatch')
        receipts[prior] = rec
    run = contained(project, run_relative(step), True); receipt = receipts[step]
    for name in set(names) | {'semantic_output_digest.json'}:
        row = receipt['artifacts'][name]; path = contained(run, name, True)
        if path.stat().st_size != row['bytes'] or file_sha256(path) != row['sha256']:
            raise ValueError('S0 selected input drift: ' + name)
    if load(run/'semantic_output_digest.json')['semantic_output_digest'] != receipt['semantic_output_digest']:
        raise ValueError('S0 semantic receipt mismatch')
    return run

def handoff(s0):
    s0 = Path(s0).resolve(strict=True); project = s0 / 'project'
    lock = verify_snapshot(s0)
    k3 = s0_inputs(project, 'K3', ('s1_entry_manifest.json','s0_gate_adjudication.json','audit_summary.json','OVERALL_STATUS.txt'))
    k1 = s0_inputs(project, 'K1', ('open_search_object_manifest.json','private_payload_commitments.json','OVERALL_STATUS.txt'))
    entry = load(k3 / 's1_entry_manifest.json'); adjud = load(k3 / 's0_gate_adjudication.json')
    validate_entry(entry, adjud, config(SOURCE_ROOT, 'k1'))
    return {'root': str(s0), 'execution_id': lock['execution_id'], 'entry': entry,
            'K3_receipt': load(s0 / 'receipts/K3.json'), 'K1_receipt': load(s0 / 'receipts/K1.json'),
            'source_commit': lock['source_commit']}, k1, k3

def default_workdir():
    base = Path(os.environ.get('TMPDIR', tempfile.gettempdir()))
    return base / ('p13_s1_' + uuid.uuid4().hex)

def plan(s0, work, staging, resume=False):
    hs = config(SOURCE_ROOT, 'k2a')['continuation_rule']; first = config(SOURCE_ROOT, 'k1')
    project = Path(work) / 'project'
    return {'mode': 'DRY_RUN', 'scientific_stage_started': False, 's0_execution': str(s0),
        'workdir': str(work), 'staging_root': str(staging), 'resume': resume,
        'steps': [{'step': s, 'command': [sys.executable, '-B', '-m', 'reproduce.s1_step', '--project-root', str(project), '--step', s],
                   'gate': ('K3 receipt + immutable continuation=false; historical PF0/PF1 scope' if s in ('PF0', 'PF1') else
                            'FINAL immutable membership + fresh predecessor receipt' if s in POST else
                            'fresh K2A continuation authorized' if s == 'K1C' else 'fresh predecessor receipt; K1A requires K0 PASS')}
                  for s in STEPS],
        'continuation': {'rule': hs, 'false': 'skip K1C; freeze first-rung membership',
                         'true': 'K1C extends eligible arms once; final complete TRAIN adjudication; no second decision'},
        'first_rung': {'seeds': first['paired_seeds'], 'arms': first['arms'], 'budget': first['budget']},
        'pf0_pf1_scope': {'false': 'K3 -> PF0 -> PF1 -> STOP before S2',
                         'true': 'freeze K3 receipt -> fail-closed scope stop; never invoke PF0/PF1',
                         'status': PF0_SCOPE_STATUS, 'exit_code': PF0_SCOPE_EXIT,
                         'receipt': str(Path(work) / 'pf0_scope_guard.json')},
        'resume_plan': 'same source/input/execution lock; verify receipts; unchanged frozen checkpoint truncate/append; no completed-stage rerun; existing continuation-true K3 stops at the same PF0 scope guard',
        'workers': first['runtime']['default_workers'], 'coordinator_cpus': 1, 'threads': dict.fromkeys(THREADS, '1'),
        'outputs': str(project / RUN), 'stop_after': 'continuation=false: PF1; continuation=true: K3 scope guard; never open S2',
        'data_boundary': 'TRAIN only until final freeze; diagnostic archive/cross fields K2B only; DEVELOPMENT/SEALED always denied'}

def prepare(work, s0, staging, resume=False):
    work = check_execution_root(work); s0 = Path(s0).resolve(strict=True)
    if work == s0 or work in s0.parents or s0 in work.parents: raise ValueError('S0 and S1 roots overlap')
    staging = Path(staging).resolve(strict=True)
    if work == staging or work in staging.parents or staging in work.parents: raise ValueError('staging/work overlap')
    source_commit, files, baseline = check_source()
    hand, k1, k3 = handoff(s0)
    if work.exists():
        if not resume: raise FileExistsError('existing workdir requires --resume')
        ctx = context(work / 'project')
        if ctx['source_commit'] != source_commit or ctx['source_files'] != files or ctx['s0'] != hand:
            raise ValueError('source or S0 lineage changed on resume')
        if ctx['staging_root'] != str(staging): raise ValueError('staging changed')
        verify_execution(work / 'project'); return work / 'project'
    project = work / 'project'; project.mkdir(parents=True)
    # Positive source snapshot. No historical generated runs are included.
    for rel, info in files.items():
        if '/runs/' in rel: raise ValueError('source snapshot contains generated run')
        p = project / rel; p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(contained(SOURCE_ROOT, rel, True), p); p.chmod(info['mode'])
    input_files = {}; diag_files = []; control_files = []
    def copy_input(src, rel):
        dst = project / rel; dst.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(src, dst)
        input_files[rel] = record(project, dst)
    for name in ('open_search_object_manifest.json', 'private_payload_commitments.json', 'OVERALL_STATUS.txt', 'semantic_output_digest.json'):
        copy_input(k1 / name, K1 + '/' + name)
    manifest = load(k1 / 'open_search_object_manifest.json')
    s0_inputs(s0 / 'project', 'K1', [r['path'] for r in manifest['objects'] if r['role'] == 'TRAIN_OPERATOR'])
    for row in manifest['objects']:
        if row['role'] == 'TRAIN_OPERATOR': copy_input(contained(k1, row['path'], True), K1 + '/' + row['path'])
        elif row['role'] == 'OPENED_TRANSFER_DIAGNOSTIC': diag_files.append(K1 + '/' + row['path'])
    for name in ('s1_entry_manifest.json', 's0_gate_adjudication.json', 'audit_summary.json', 'semantic_output_digest.json', 'OVERALL_STATUS.txt'):
        copy_input(k3 / name, run_relative('K3') + '/' + name)
    from .resolve_inputs import load_manifest
    meta = next(r for r in load_manifest()['historical_random_realizations'] if r['id'] == 'within_family_diagnostic')
    ctx = {'schema': 'P13_S1_EXECUTION_V1', 'execution_id': uuid.uuid4().hex, 'source_commit': source_commit,
           'source_files': files, 'baseline': baseline, 's0': hand, 'input_files': input_files,
           'diagnostic_files': diag_files, 'control_files': control_files, 'staging_root': str(staging),
           'diagnostic_archive': str(staging / meta['staged_basename']), 'diagnostic_commitment': meta}
    write(work / 's1_execution.json', ctx)
    (work / 'receipts').mkdir()
    for rel in (RUN, K0): (project / rel).mkdir(parents=True, exist_ok=True)
    for stage, rel in [('S0_K1', K1), ('S0_K3', run_relative('K3')), ('S1_K0R', K0), ('S1_FORMAL', RUN)]:
        (project / P / 'runs' / ('LATEST_P13_' + stage + '_RUN.txt')).write_text(rel + '\n')
    return project

def verify_execution(project):
    ctx = context(project)
    for rel, row in ctx['source_files'].items():
        p = contained(project, rel, True)
        if file_sha256(p) != row['sha256']: raise ValueError('execution source drift: ' + rel)
    from .s1_lineage import check_record
    for row in ctx['input_files'].values(): check_record(project, row)

def open_diagnostics(project):
    from .s1_lineage import diagnostic_capability
    from .resolve_inputs import resolve_input
    diagnostic_capability(project, 'K2B'); ctx = context(project)
    resolve_input('within_family_diagnostic', 'S1-K2B', Path(ctx['staging_root']))
    # Copy only fresh public cross-family arrays at their original relative paths.
    s0project = Path(ctx['s0']['root']) / 'project'
    k1 = s0_inputs(s0project, 'K1', ('open_search_object_manifest.json',))
    for row in load(k1 / 'open_search_object_manifest.json')['objects']:
        if row['role'] != 'OPENED_TRANSFER_DIAGNOSTIC': continue
        src = contained(k1, row['path'], True); dst = project / K1 / row['path']
        if file_sha256(src) != row['sha256']: raise ValueError('diagnostic commitment changed')
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists() and file_sha256(dst) != row['sha256']: raise ValueError('projected diagnostic changed')
        if not dst.exists(): shutil.copyfile(src, dst)
        ctx['input_files'][dst.relative_to(project).as_posix()] = record(project, dst)
    write(project.parent / 's1_execution.json', ctx)

def stage_outputs(project, step):
    run = project / RUN
    if step == 'K0': return list((project / K0).glob('*.json')) + list((project / K0).glob('*.txt'))
    if step in ('K2B', 'K2C', 'K3', 'PF0', 'PF1'):
        sub = {'K2B': 'K2B_diagnostics', 'K2C': 'K2C_theory_bridge', 'K3': 'K3_freeze', 'PF0': 'PF0_postfreeze', 'PF1': 'PF1_final_freeze'}[step]
        return ([p for p in (run / sub).rglob('*') if p.is_file()]
                + [p for p in run.glob(step + '_*.txt') if p.is_file()])
    return [p for p in run.glob(step + '_*') if p.is_file() and p.suffix in ('.json', '.txt')]

def project_controls(project):
    """Post-membership S0 NULL provenance only; never a search seed input."""
    verify_membership(project); ctx = context(project)
    src_project = Path(ctx['s0']['root']) / 'project'
    for stage, names in [('K2R4', ['capacity_reuse_lock.json']),
                         ('K2R3', ['calibration_only/capacity_instrument_lock_k2r3.json'])]:
        src = s0_inputs(src_project, stage, names)
        for name in names:
            rel = run_relative(stage) + '/' + name; dst = project / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not dst.exists(): shutil.copyfile(src / name, dst)
            if file_sha256(dst) != file_sha256(src / name): raise ValueError('control provenance changed')
            ctx['input_files'][rel] = record(project, dst)
            if rel not in ctx['control_files']: ctx['control_files'].append(rel)
    write(project.parent / 's1_execution.json', ctx)

def execute(work, s0, staging, resume):
    import fcntl
    import platform, numpy, scipy
    from threadpoolctl import threadpool_info
    if (platform.python_version(), numpy.__version__, scipy.__version__) != ('3.10.19', '2.2.6', '1.15.2'):
        raise RuntimeError('use the qualified Python/NumPy/SciPy candidate')
    if any(x['num_threads'] != 1 for x in threadpool_info()):
        raise RuntimeError('BLAS thread count must be one')
    project = prepare(work, s0, staging, resume)
    with (project.parent / 'coordinator.lock').open('a') as guard:
        fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for step in STEPS:
            verify_execution(project)
            if receipt_path(project, step).exists():
                verify_receipt(project, step)
                if step == 'FINAL': freeze_membership(project)
                if step == 'K3' and stop_after_k3(project): return PF0_SCOPE_EXIT
                continue
            if step == 'K1C' and not load(project / RUN / 'K2A_continuation_decision.json')['authorized']: continue
            if step in POST: verify_membership(project)
            if step == 'K2B': open_diagnostics(project)
            if step == 'K3': project_controls(project)
            env = os.environ.copy(); env.update(dict.fromkeys(THREADS, '1'))
            env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1', P13_S1_PROJECT=str(project), P13_S1_STEP=step)
            env.pop('P13_S0_PROJECT_ROOT', None)
            env['PYTHONPATH'] = os.pathsep.join(map(str, [project / 'reproduce/s1_bootstrap', project,
                       project / P / 'src', project / 'phases/p11/raw_xt_td/src']))
            print(f'[S1] stage={step} source={context(project)["source_commit"]} workdir={project.parent}', flush=True)
            subprocess.run([sys.executable, '-B', '-m', 'reproduce.s1_step', '--project-root', str(project), '--step', step],
                           cwd=project, env=env, check=True)
            if step in POST: verify_membership(project)
            outputs = stage_outputs(project, step)
            if not outputs: raise ValueError('stage produced no outputs: ' + step)
            complete(project, step, outputs)
            if step == 'FINAL': freeze_membership(project)
            if step == 'K3' and stop_after_k3(project): return PF0_SCOPE_EXIT
        print('S1_COMPLETE; STOP before S2; run the separate R-D comparator only after computation.', flush=True)
    return 0

def main():
    p = argparse.ArgumentParser(description=__doc__); g = p.add_mutually_exclusive_group(required=True)
    g.add_argument('--dry-run', action='store_true'); g.add_argument('--execute', action='store_true')
    p.add_argument('--s0-execution', type=Path, required=True); p.add_argument('--workdir', '--execution-root', type=Path)
    p.add_argument('--staging-root', type=Path, required=True); p.add_argument('--resume', action='store_true')
    a = p.parse_args()
    work = a.workdir or (Path(os.environ['P13_WORKDIR']) if os.environ.get('P13_WORKDIR') else default_workdir())
    work = Path(work).absolute()
    if a.dry_run:
        import json
        print(json.dumps(plan(a.s0_execution.absolute(), work, a.staging_root.absolute(), a.resume), indent=2)); return
    if a.resume and not a.workdir and not os.environ.get('P13_WORKDIR'): p.error('--resume requires explicit workdir')
    raise SystemExit(execute(work, a.s0_execution, a.staging_root, a.resume))

if __name__ == '__main__': main()
