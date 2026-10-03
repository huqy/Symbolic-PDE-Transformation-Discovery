"""Persistent S3 launcher; dry-run authenticates metadata, never opens payloads."""
import argparse
import fcntl
import json
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import time
import threading
import uuid
from pathlib import Path
from .common import SOURCE_ROOT, contained, file_sha256
from .s0_contract import load, write
from .s0_launcher import check_execution_root, check_source
from .s1_lineage import seal
from .s3_contract import STEPS, RUN, handoff, state, receipt, finish, stage_dir, record, verify_record

THREADS = ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS')

def separate(s2, work, staging):
    s2 = Path(s2).resolve(strict=True); work = check_execution_root(work)
    staging = Path(staging).absolute()
    for p in (staging, *staging.parents):
        if p.is_symlink(): raise ValueError('staging symlink rejected')
    staging = staging.resolve()
    for a, b in ((s2,work),(staging,work),(s2,staging),(SOURCE_ROOT,staging)):
        if a == b or a in b.parents or b in a.parents: raise ValueError('input/source/work roots overlap')
    return s2, work, staging

def plan(s2, work, staging, resume=False):
    s2, work, staging = separate(s2, work, staging)
    header, groups = handoff(s2)
    done=[]
    if resume and work.exists():
        ctx=verify_execution(work/'project')
        if ctx['s2']!=header or ctx['staging_root']!=str(staging): raise ValueError('resume input drift')
        next_steps(work/'project')
        done=[s for s in STEPS if (work/'receipts'/(s+'.json')).exists()]
    return {'mode':'DRY_RUN','scientific_stage_started':False,'SEALED_OPENED':False,
            'S3_REAL_EXECUTION_STARTED':False,'s2_execution':str(s2),'workdir':str(work),'staging_root':str(staging),
            'formal_count':header['formal_count'],'diagnostic_FAIL_count':header['diagnostic_FAIL_count'],
            'diagnostic_UNRESOLVED_count':header['diagnostic_UNRESOLVED_count'],'S2_K7_receipt_digest':header['K7_receipt_digest'],
            'steps':[{'stage':s,'receipt_complete':s in done,'boundary':b} for s,b in zip(STEPS,(
                'fresh S2 formal/diagnostic locks; TRAIN-only diagnostic calibration; SEALED denied',
                'SEALED coefficient only; all formal and separated diagnostic strata; zero-refit G33/G65',
                'complete formal operator gate; diagnostic PASS never promoted; all formal PASS eligible',
                'only after K2 lock: SEALED response, 32 independent references, identity/NULL first',
                'every formal operator PASS x32; strict 0.15; uniform cohort escalation',
                'complete post-SEALED descriptive diagnostics; zero membership authority',
                'full fresh lineage freeze; external Claim-III audit required; no automatic R-C'))],
            'workers':16,'coordinator_cpus':1,'threads':dict.fromkeys(THREADS,'1'),
            'high_reference_grid_worker_caps':{'2049':8,'4097':2},
            'execution_requires':'external wiring audit then explicit user authorization for SEALED opening',
            'public_claim_contract_sha256':file_sha256(SOURCE_ROOT/'PUBLIC_REPRODUCTION_CONTRACT.md')}

def prepare(s2, work, staging, resume=False):
    s2, work, staging = separate(s2,work,staging)
    commit, files, baseline = check_source()
    header, groups = handoff(s2, full=True)
    from .s2_contract import handoff as s1_handoff
    s1_header,_,_,_=s1_handoff(header['S1']['root'],verify_registries=True)
    if s1_header!=header['S1']:raise ValueError('S1 registry/source lineage changed')
    contract = record(SOURCE_ROOT/'PUBLIC_REPRODUCTION_CONTRACT.md')
    if work.exists():
        if not resume: raise FileExistsError('existing workdir requires --resume')
        ctx = state(work/'project')
        if any(ctx[k] != v for k,v in [('source_commit',commit),('source_files',files),('s2',header),('staging_root',str(staging)),('public_contract',contract)]):
            raise ValueError('source/input/protocol changed on resume')
        return work/'project'
    project = work/'project'; project.mkdir(parents=True)
    for name,row in files.items():
        if '/runs/' in name: raise ValueError('generated historical store in source snapshot')
        dest = project/name; dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(contained(SOURCE_ROOT,name,True),dest); dest.chmod(row['mode'])
    ctx = seal({'schema':'P13_S3_EXECUTION_V1','execution_id':uuid.uuid4().hex,'source_commit':commit,
                'source_files':files,'baseline':baseline,'s2':header,'staging_root':str(staging),
                'public_contract':contract,'scientific_budget_changed':False})
    write(work/'s3_execution.json',ctx); (work/'receipts').mkdir(); (project/RUN).mkdir(parents=True)
    return project

def verify_execution(project):
    ctx = state(project)
    for name,row in ctx['source_files'].items():
        p = contained(project,name,True)
        if file_sha256(p) != row['sha256']: raise ValueError('source snapshot drift')
    for row in ctx['s2']['pins']: verify_record(row)
    for row in ctx['s2']['S1']['pins']+ctx['s2']['S1'].get('descriptive_pins',[]): verify_record(row)
    from .s1_lineage import unseal,check_record
    s1root=Path(ctx['s2']['S1']['root'])
    lock=unseal(load(s1root/'membership_lock.json'))
    for row in lock['registries']:check_record(s1root/'project',row)
    s1ctx=load(s1root/'s1_execution.json')
    for rel,row in s1ctx['input_files'].items():
        if '/fresh_s0_k1/' in rel or rel.endswith('capacity_reuse_lock.json') or rel.endswith('capacity_instrument_lock_k2r3.json'):check_record(s1root/'project',row)
    verify_record(ctx['public_contract'])
    return ctx

def next_steps(project):
    """Receipts are a prefix; zero-PASS/control-stop paths freeze evidence in K6."""
    done = [s for s in STEPS if (Path(project).parent/'receipts'/(s+'.json')).exists()]
    if done != list(STEPS[:len(done)]): raise ValueError('non-prefix receipt chain')
    for step in done: receipt(project,step)
    return list(STEPS[len(done):])

def coordinate(project, child):
    """Dependency-injected child used by synthetic walltime/resume tests."""
    for step in next_steps(project):
        verify_execution(project)
        # Crash after scientific completion but before coordinator receipt: seal
        # the already completed output, do not rerun the numerical campaign.
        if not (stage_dir(project,step)/'REPRODUCTION_STAGE_COMPLETE.json').exists():
            rc = child(project,step)
            if rc: raise RuntimeError(f'{step} exited {rc}; preserve workdir and resume')
        finish(project,step)

def relay_progress(p, output, step, heartbeat_seconds=30):
    """Drain stdout continuously; heartbeat does not touch a numerical worker."""
    lines=queue.Queue(); start=time.monotonic(); done=0; total=1; current='waiting_for_stage_progress'
    def drain():
        try:
            for line in p.stdout: lines.put(line)
        finally: lines.put(None)
    reader=threading.Thread(target=drain,daemon=True); reader.start()
    while True:
        try: line=lines.get(timeout=heartbeat_seconds)
        except queue.Empty:
            elapsed=time.monotonic()-start; rate=done/max(elapsed,1e-9)
            eta=f'{(total-done)/rate:.1f}s' if rate else 'NA'
            line=f'[S3 heartbeat] stage={step} processed={done}/{total} candidate/case={current} elapsed={elapsed:.1f}s rate={rate:.3f}/s ETA={eta}\n'
        if line is None: break
        count=re.search(r'processed=(\d+)/(\d+)',line)
        cursor=re.search(r'(?:current|candidate/case)=([^\s]+)',line)
        if count: done,total=map(int,count.groups())
        if cursor: current=cursor.group(1)
        print(line,end='',flush=True); output.write(line); output.flush()
    reader.join(); return p.wait()

def run_child(project, step):
    env = os.environ.copy(); env['PYTHONDONTWRITEBYTECODE']='1'
    env['P13_S3_PROJECT']=str(project); env['P13_S3_STEP']=step
    env['PYTHONPATH']=os.pathsep.join(str(p) for p in (project/'reproduce/s3_bootstrap',project,
          project/'phases/p13/coefficient_law_raw_xt/src', project/'phases/p11/raw_xt_td/src'))
    for k in THREADS: env[k]='1'
    log = project.parent/'progress.log'
    with log.open('a') as output:
        p = subprocess.Popen([sys.executable,'-u','-B','-m','reproduce.s3_step','--project-root',str(project),'--step',step],
                             cwd=project,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,start_new_session=True)
        try:
            return relay_progress(p,output,step)
        except BaseException:
            os.killpg(p.pid,signal.SIGTERM)
            try: p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid,signal.SIGKILL); p.wait()
            raise
        finally:
            p.stdout.close()

def main():
    p=argparse.ArgumentParser(description=__doc__); g=p.add_mutually_exclusive_group(required=True)
    g.add_argument('--dry-run',action='store_true'); g.add_argument('--execute',action='store_true')
    for name in ('s2-execution','workdir','staging-root'): p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--resume',action='store_true'); a=p.parse_args()
    if not all(x.is_absolute() for x in (a.s2_execution,a.workdir,a.staging_root)):
        p.error('paths must be explicit and absolute')
    if a.dry_run:
        print(json.dumps(plan(a.s2_execution,a.workdir,a.staging_root,a.resume),indent=2)); return
    for k in THREADS:
        if os.environ.get(k)!='1': raise RuntimeError('one BLAS/OpenMP thread required')
    project=prepare(a.s2_execution,a.workdir,a.staging_root,a.resume)
    signal.signal(signal.SIGTERM,lambda sig,frame: (_ for _ in ()).throw(SystemExit(128+sig)))
    with (project.parent/'coordinator.lock').open('a') as f:
        fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB); coordinate(project,run_child)
    print('S3_COMPLETE; historical SEALED reproduction; external claim audit required.',flush=True)

if __name__=='__main__': main()
