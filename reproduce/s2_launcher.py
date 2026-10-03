"""Persistent S2 launcher; dry-run authenticates metadata, never opens payloads."""
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
from .s2_contract import STEPS, RUN, handoff, state, receipt, finish, stage_dir, record, verify_record

THREADS = ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS')

def separate(s1, work, staging):
    s1 = Path(s1).resolve(strict=True); work = check_execution_root(work)
    staging = Path(staging).absolute()
    for p in (staging, *staging.parents):
        if p.is_symlink(): raise ValueError('staging symlink rejected')
    staging = staging.resolve()
    for a, b in ((s1,work),(staging,work),(s1,staging),(SOURCE_ROOT,staging)):
        if a == b or a in b.parents or b in a.parents: raise ValueError('input/source/work roots overlap')
    return s1, work, staging

def plan(s1, work, staging, resume=False):
    s1, work, staging = separate(s1, work, staging)
    header, active, clear, lock = handoff(s1)
    if resume and work.exists():
        ctx = state(work/'project')
        if ctx['s1'] != header or ctx['staging_root'] != str(staging): raise ValueError('resume input drift')
        done = [s for s in STEPS if (work/'receipts'/(s+'.json')).exists()]
        for s in done: receipt(work/'project', s)
    else: done = []
    return {'mode':'DRY_RUN','scientific_stage_started':False,'DEVELOPMENT_OPENED':False,
            'DEVELOPMENT_RESPONSE_OPENED':False,'SEALED_OPENED':False,
            's1_execution':str(s1),'workdir':str(work),'staging_root':str(staging), 'resume':resume,
            'input_clear_count':header['clear_count'],'unresolved_REFERENCE_count':header['unresolved_count'],
            'exact_execution_classes':header['clear_exact_execution_classes'],
            'scientific_membership':'every clear branch, not execution-class representatives',
            'S1_PF1_receipt_digest':header['PF1_receipt_digest'], 'S1_membership_digest':lock['digest'],
            'steps':[{'stage':s,'receipt_complete':s in done,'boundary':b} for s,b in zip(STEPS,
             ('four DEVELOPMENT coefficient fields only; response/SEALED denied',
              'complete same-AST/theta/gauge zero-refit G33/G65 measurement',
              'complete frozen operator-gate census; preserve UNRESOLVED; all PASS eligible',
              'freeze response/reference/control/fidelity contract; no response opening',
              '32-case response opening; candidate-independent reference; identity/NULL controls first',
              'every operator-PASS branch × all 32 cases; cohort-wide escalation only',
              'post-response descriptive diagnostics; zero membership authority',
              'formal complete S2 freeze; SEALED unopened; external claim audit required'))],
            'workers':16,'coordinator_cpus':1,'threads':dict.fromkeys(THREADS,'1'),
            'high_reference_grid_worker_caps':{'2049':8,'4097':2},
            'resume_policy':'verify immutable receipts; skip complete stages; recover complete output lacking receipt; restart only incomplete stage with durable numerical shards',
            'execution_requires':'external wiring audit acceptance; this task executes dry-run/tests only',
            'public_claim_contract_sha256':file_sha256(SOURCE_ROOT/'PUBLIC_REPRODUCTION_CONTRACT.md')}

def prepare(s1, work, staging, resume=False):
    s1, work, staging = separate(s1,work,staging)
    commit, files, baseline = check_source()
    header, active, clear, lock = handoff(s1, verify_registries=True)
    contract = record(SOURCE_ROOT/'PUBLIC_REPRODUCTION_CONTRACT.md')
    if work.exists():
        if not resume: raise FileExistsError('existing workdir requires --resume')
        ctx = state(work/'project')
        if any(ctx[k] != v for k,v in [('source_commit',commit),('source_files',files),('s1',header),('staging_root',str(staging)),('public_contract',contract)]):
            raise ValueError('source/input/protocol changed on resume')
        return work/'project'
    project = work/'project'; project.mkdir(parents=True)
    for name,row in files.items():
        if '/runs/' in name: raise ValueError('generated historical store in source snapshot')
        dest = project/name; dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(contained(SOURCE_ROOT,name,True),dest); dest.chmod(row['mode'])
    ctx = seal({'schema':'P13_S2_EXECUTION_V1','execution_id':uuid.uuid4().hex,'source_commit':commit,
                'source_files':files,'baseline':baseline,'s1':header,'staging_root':str(staging),
                'public_contract':contract,'scientific_budget_changed':False})
    write(work/'s2_execution.json',ctx); (work/'receipts').mkdir(); (project/RUN).mkdir(parents=True)
    return project

def verify_execution(project):
    ctx = state(project)
    for name,row in ctx['source_files'].items():
        p = contained(project,name,True)
        if file_sha256(p) != row['sha256']: raise ValueError('source snapshot drift')
    for row in ctx['s1']['pins']: verify_record(row)
    for row in ctx['s1'].get('descriptive_pins',[]): verify_record(row)
    verify_record(ctx['public_contract'])
    return ctx

def next_steps(project):
    """Receipts are a prefix; zero-PASS/control-stop paths finish a stopped K7."""
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
            line=f'[S2 heartbeat] stage={step} processed={done}/{total} candidate/case={current} elapsed={elapsed:.1f}s rate={rate:.3f}/s ETA={eta}\n'
        if line is None: break
        count=re.search(r'processed=(\d+)/(\d+)',line)
        cursor=re.search(r'(?:current|candidate/case)=([^\s]+)',line)
        if count: done,total=map(int,count.groups())
        if cursor: current=cursor.group(1)
        print(line,end='',flush=True); output.write(line); output.flush()
    reader.join(); return p.wait()

def run_child(project, step):
    env = os.environ.copy(); env['PYTHONDONTWRITEBYTECODE']='1'
    env['P13_S2_PROJECT']=str(project); env['P13_S2_STEP']=step
    env['PYTHONPATH']=os.pathsep.join(str(p) for p in (project/'reproduce/s2_bootstrap',project,
          project/'phases/p13/coefficient_law_raw_xt/src', project/'phases/p11/raw_xt_td/src'))
    for k in THREADS: env[k]='1'
    log = project.parent/'progress.log'
    with log.open('a') as output:
        p = subprocess.Popen([sys.executable,'-u','-B','-m','reproduce.s2_step','--project-root',str(project),'--step',step],
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
    for name in ('s1-execution','workdir','staging-root'): p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--resume',action='store_true'); a=p.parse_args()
    if not all(x.is_absolute() for x in (a.s1_execution,a.workdir,a.staging_root)):
        p.error('paths must be explicit and absolute')
    if a.dry_run:
        print(json.dumps(plan(a.s1_execution,a.workdir,a.staging_root,a.resume),indent=2)); return
    for k in THREADS:
        if os.environ.get(k)!='1': raise RuntimeError('one BLAS/OpenMP thread required')
    project=prepare(a.s1_execution,a.workdir,a.staging_root,a.resume)
    signal.signal(signal.SIGTERM,lambda sig,frame: (_ for _ in ()).throw(SystemExit(128+sig)))
    with (project.parent/'coordinator.lock').open('a') as f:
        fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB); coordinate(project,run_child)
    print('S2_COMPLETE; SEALED unopened; external claim audit required.',flush=True)

if __name__=='__main__': main()
