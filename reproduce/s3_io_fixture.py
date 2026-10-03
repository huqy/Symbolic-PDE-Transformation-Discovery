"""Guard probe over synthetic files only, including spawned workers."""
import os
from pathlib import Path
from .s3_contract import state,stage_dir

def probe():
    project=Path(os.environ['P13_S3_PROJECT']);step=os.environ['P13_S3_STEP'];ctx=state(project)
    from .resolve_inputs import load_manifest
    targets=[Path(ctx['staging_root'])/r['staged_basename'] for r in load_manifest()['historical_random_realizations']]
    targets += [project/'phases/p13/coefficient_law_raw_xt/runs/p13_s3_k0_presealed_lock/historical.json',stage_dir(project,'K6')/'future.json']
    for p in targets:
        try:p.open('rb')
        except PermissionError:pass
        else:raise AssertionError('guard accepted forbidden read')
    try:(project/'reproduce/s3_step.py').write_text('source mutation')
    except PermissionError:pass
    else:raise AssertionError('guard accepted source mutation')
    d=stage_dir(project,step);d.mkdir(parents=True,exist_ok=True);(d/'probe.json').write_text('{}')
    # multiprocessing spawn inherits bootstrap and role policy.
    from multiprocessing import get_context
    proc=get_context('spawn').Process(target=worker_probe);proc.start();proc.join()
    if proc.exitcode!=0:raise AssertionError('spawn guard failed')
    print('GUARD_PROBE_PASS',flush=True)
def worker_probe():
    from .s3_io import ACTIVE_POLICY
    if ACTIVE_POLICY!=(os.environ['P13_S3_PROJECT'],os.environ['P13_S3_STEP']):raise AssertionError('spawn lost role guard')
    p=Path(os.environ['P13_S3_PROJECT'])/'PUBLIC_REPRODUCTION_CONTRACT.md'
    try:p.write_text('mutation')
    except PermissionError:return
    raise AssertionError('spawn accepted immutable source mutation')


def synthetic_stage():
    from contextlib import ExitStack
    from .test_s3_wiring import Fixture
    from . import s3_step as step
    from .s0_contract import load
    f=Fixture.__new__(Fixture);f.project=Path(os.environ['P13_S3_PROJECT']);f.events=[]
    metadata=load(f.project/'reproduce/s3_test_fixture_inputs.json')
    f.header=metadata['header'];f.groups=metadata['groups'];f.clear=metadata['clear']
    stage=os.environ['P13_S3_STEP']
    if stage in ('K0','K1','K2'):f.before_response(stages=(stage,),finish=False)
    else:
        with ExitStack() as st:
            for p in f.response_patches():st.enter_context(p)
            step.run(f.project,stage)
    print('SYNTHETIC_GUARDED_STAGE_PASS '+stage,flush=True)

if __name__=='__main__':
    import sys
    if sys.argv[1:]==['--synthetic-stage']:synthetic_stage()
    else:probe()
