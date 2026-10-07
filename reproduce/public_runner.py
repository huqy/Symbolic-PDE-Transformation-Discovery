"""Thin coordinator for the existing audited launchers. No scientific kernels."""
import argparse
import fcntl
import importlib.metadata
import json
import os
import platform
import signal
import subprocess
import sys
import time
from pathlib import Path
from .common import SOURCE_ROOT, file_sha256
from .release_integrity import source_identity, source_clean, verify as verify_release
from .s0_contract import load, write
from .s0_launcher import check_execution_root, check_source, git
from .verify_public_assets import verify_assets, no_symlinks

STAGES = ('S0','S1','S2','S3')
FINAL = {'S0':'K3','S1':'PF1','S2':'K7','S3':'K6'}
THREADS = ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS')
LOCK_NAME = 'public_execution.json'

class ProtocolStop(RuntimeError): pass

def roots(work_root, staging_root):
    for path in (work_root, staging_root):
        if not Path(path).is_absolute(): raise ValueError('paths must be explicit and absolute')
        no_symlinks(path)
    work = check_execution_root(work_root); staging = Path(staging_root).resolve()
    for a,b in ((work,staging),(SOURCE_ROOT,staging)):
        if a==b or a in b.parents or b in a.parents: raise ValueError('source/staging/work roots overlap')
    return work,staging

def installed_builds():
    """Authenticate installed conda records against the complete public build lock."""
    expected=load(SOURCE_ROOT/'environment/public-release-lock.json')['packages']
    records={r['name']:r for p in (Path(sys.prefix)/'conda-meta').glob('*.json') for r in [load(p)]}
    keys=('name','version','build','build_number','subdir','md5','sha256')
    return [{k:records.get(e['name'],{}).get(k) for k in keys} for e in expected]

def runtime():
    import numpy, scipy.linalg
    from threadpoolctl import threadpool_info
    return {'python':platform.python_version(), 'executable':sys.executable, 'platform':platform.platform(),
            'packages':{n:importlib.metadata.version(n) for n in ('numpy','scipy','threadpoolctl')},
            'threads':{k:os.environ.get(k) for k in THREADS}, 'threadpools':threadpool_info(),
            'system':platform.system(), 'machine':platform.machine(), 'builds':installed_builds(),
            'canonical_lock_sha256':file_sha256(SOURCE_ROOT/'environment/public-release-lock.json')}

def require_canonical(env):
    expected=load(SOURCE_ROOT/'environment/public-release-lock.json')
    keys=('name','version','build','build_number','subdir','md5','sha256')
    builds=[{k:e[k] for k in keys} for e in expected['packages']]
    if env.get('system')!='Linux' or env.get('machine')!='x86_64':
        raise ValueError('canonical Linux x86_64 required')
    if env.get('builds')!=builds or env.get('canonical_lock_sha256')!=file_sha256(SOURCE_ROOT/'environment/public-release-lock.json'):
        raise ValueError('canonical installed package/build lock required')
    versions={r['name']:r['version'] for r in expected['packages']}
    if env['python']!=expected['python'] or any(env['packages'][k]!=versions[k] for k in env['packages']):
        raise ValueError('canonical Python/package versions required; other environments are not scientifically validated')
    if any(env['threads'][k]!='1' for k in THREADS) or not env['threadpools'] or any(r['num_threads']!=1 for r in env['threadpools']):
        raise ValueError('one BLAS/OpenMP thread required')
    if any(r.get('internal_api')!='openblas' or r.get('version')!='0.3.30' or r.get('threading_layer')!='pthreads' or r.get('user_api')!='blas' for r in env['threadpools']):
        raise ValueError('canonical OpenBLAS runtime required')

def resume_fingerprint(env):
    """Keep numerical identity; omit node/kernel/path observations from comparison."""
    keys=('user_api','internal_api','version','threading_layer','num_threads','prefix')
    pools=[{k:p.get(k) for k in keys} for p in env['threadpools']]
    return {'schema':'P13_CANONICAL_RESUME_ENVIRONMENT_V1',
            **{k:env[k] for k in ('python','packages','builds','system','machine','threads','canonical_lock_sha256')},
            'threadpools':sorted(pools,key=lambda p:json.dumps(p,sort_keys=True))}

def execution_identity(work, staging, env, assets, source=None):
    return {'source_commit':source or source_identity(), 'environment':resume_fingerprint(env),
            'work_root':str(work), 'staging_root':str(staging), 'asset_verification':assets,
            'asset_manifest_sha256':file_sha256(SOURCE_ROOT/'reproduce/PUBLIC_RELEASE_ASSETS.json')}

def command(stage, work, staging, execute=False, resume=False):
    root=work/stage
    cmd=['bash',str(SOURCE_ROOT/'reproduce'/('run_'+stage.lower()+'.sh')),'--execute' if execute else '--dry-run',
         '--execution-root' if stage=='S0' else '--workdir',str(root),'--staging-root',str(staging)]
    if stage!='S0': cmd+=['--'+STAGES[STAGES.index(stage)-1].lower()+'-execution',str(work/STAGES[STAGES.index(stage)-1])]
    if resume: cmd+=['--resume']
    return cmd

def selected(stage): return STAGES if stage=='all' else (stage,)

def boundary(stage, work, source):
    """Delegate receipt authentication to unchanged stage contracts; inspect metadata."""
    root=work/stage; project=root/'project'
    if stage=='S0':
        from .s0_contract import context, parent
        _,ctx=context(project); final=parent(project,'K3'); r=load(root/'receipts/K3.json')
        s=load(final/'audit_summary.json')
        if s['OVERALL_STATUS']!='PASS' or s.get('S1_K0_authorized') is not True:
            raise ProtocolStop('S0 final entry gate not authorized')
    elif stage=='S1':
        from .s1_lineage import context, verify_receipt
        ctx=context(project); r=verify_receipt(project,'PF1')
        from .s2_contract import handoff
        handoff(root)  # authenticates frozen applicability, membership and handoff
    else:
        from . import s2_contract, s3_contract
        c=s2_contract if stage=='S2' else s3_contract
        ctx=c.state(project); r=c.receipt(project,FINAL[stage])
        for step in c.STEPS:
            s=load(c.stage_dir(project,step)/(step+'_SCIENTIFIC_SUMMARY.json'))
            if s['OVERALL_STATUS']!='PASS' or s.get('stop_reason'):
                raise ProtocolStop(stage+' '+step+' protocol stop: '+str(s.get('stop_reason',s['OVERALL_STATUS'])))
        evidence=s.get('claim_contract_evidence',{})
        if evidence.get('status')!='CLAIM_COMPATIBLE_CANDIDATE':
            raise ProtocolStop(stage+' claim evidence requires external review: '+str(evidence.get('status')))
    if ctx['source_commit']!=source: raise ValueError('stage source commit differs from integrated source')
    if stage!='S0':
        prior=STAGES[STAGES.index(stage)-1]; header=ctx[prior.lower()]
        if Path(header['root'])!=work/prior: raise ValueError('historical/external generated parent refused')
    path=root/'receipts'/(FINAL[stage]+'.json')
    return {'execution_id':ctx['execution_id'],'receipt':str(path),'sha256':file_sha256(path),
            'receipt_digest':r.get('digest',r.get('receipt_digest'))}

def launch(cmd):
    env=os.environ.copy(); env['P13_PYTHON']=sys.executable
    child=subprocess.Popen(cmd,cwd=SOURCE_ROOT,env=env,start_new_session=True)
    try: return child.wait()
    except BaseException:
        os.killpg(child.pid,signal.SIGTERM)
        try: child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid,signal.SIGKILL); child.wait()
        raise

def orchestrate(work, staging, stages, resume, identity, launch_child=launch, check_boundary=boundary, runtime_observation=None):
    """Persist identity first; completed stages reauthenticate, incomplete stages resume."""
    path=work/LOCK_NAME
    no_symlinks(path)
    if work.exists():
        if not resume: raise FileExistsError('existing integrated root requires --resume')
        if not path.is_file(): raise ValueError('root lacks integrated lock; historical output fallback forbidden')
        state=load(path)
        if state.get('schema')!='P13_PUBLIC_EXECUTION_V2': raise ValueError('legacy execution diagnostic only; start a clean v1.0.1 run')
        if state['identity']!=identity: raise ValueError('source/environment/input drift on resume')
    else:
        if stages[0]!='S0': raise ValueError('start S0 in a fresh integrated root before later stages')
        if resume: raise ValueError('--resume requires an existing integrated root')
        work.mkdir(parents=True); state={'schema':'P13_PUBLIC_EXECUTION_V2','identity':identity,'completed':{},'events':[]}
        write(path,state)
    no_symlinks(path)
    for stage in stages[:1]:
        prior_index=STAGES.index(stage)-1
        if prior_index>=0:
            prior=STAGES[prior_index]
            if prior not in state['completed']: raise ValueError('prior stage not complete in this integrated execution: '+prior)
            if check_boundary(prior,work,identity['source_commit'])!=state['completed'][prior]: raise ValueError('parent receipt changed')
    # This initial prerequisite check applies only to the first selected stage.
    # Later stages are completed below in the same full-chain invocation.
    return coordinate(work,staging,stages,state,path,launch_child,check_boundary,runtime_observation)

def coordinate(work,staging,stages,state,path,launch_child,check_boundary,runtime_observation):
    with (work/'public_coordinator.lock').open('a') as guard:
        fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
        state=load(path)
        if runtime_observation is not None:
            state['events'].append({'status':'COORDINATOR_INVOCATION',
                                    'time_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
                                    'runtime_observation':runtime_observation})
            write(path,state)
        for stage in stages:
            index=STAGES.index(stage)
            if index:
                prior=STAGES[index-1]
                if prior not in state['completed'] or check_boundary(prior,work,state['identity']['source_commit'])!=state['completed'][prior]:
                    raise ValueError('fresh parent incomplete or changed')
            if stage in state['completed']:
                if check_boundary(stage,work,state['identity']['source_commit'])!=state['completed'][stage]: raise ValueError('completed receipt changed')
                print('[PUBLIC] verified; skip '+stage,flush=True); continue
            stage_root=work/stage; no_symlinks(stage_root)
            cmd=command(stage,work,staging,execute=True,resume=stage_root.exists())
            print('[PUBLIC] stage='+stage+' workdir='+str(stage_root),flush=True)
            rc=launch_child(cmd)
            if rc:
                state['events'].append({'stage':stage,'returncode':rc,'status':'STOPPED_PRESERVE_AND_RESUME','time_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())})
                write(path,state); raise ProtocolStop(stage+' launcher exited '+str(rc)+'; preserve root')
            try: pin=check_boundary(stage,work,state['identity']['source_commit'])
            except Exception as exc:
                state['events'].append({'stage':stage,'status':'BOUNDARY_STOP','reason':str(exc)}); write(path,state); raise
            state['completed'][stage]=pin; write(path,state)
        return {'status':'COMPLETE_PENDING_EXTERNAL_CLAIM_AUDIT','completed_stages':list(state['completed']),
                'R_C':'NOT_AUTOMATICALLY_ASSIGNED','source_commit':state['identity']['source_commit']}

def dry_plan(work, staging, stages, resume, env):
    verify_release()
    assets=verify_assets(staging)
    if work.exists():
        no_symlinks(work/LOCK_NAME)
        if not resume or not (work/LOCK_NAME).is_file(): raise ValueError('existing root requires --resume and integrated lock')
        state=load(work/LOCK_NAME); identity=state['identity']
        if state.get('schema')!='P13_PUBLIC_EXECUTION_V2': raise ValueError('legacy execution diagnostic only; start a clean v1.0.1 run')
        if identity!=execution_identity(work,staging,env,assets):
            raise ValueError('source/environment/input drift on resume dry-run')
        for pin in state['completed'].values():
            path=Path(pin['receipt']); no_symlinks(path)
            if work not in path.parents or file_sha256(path)!=pin['sha256']: raise ValueError('completed receipt drift on dry-run')
    elif resume: raise ValueError('--resume requires an existing integrated root')
    return {'schema':'P13_PUBLIC_INTEGRATED_DRY_RUN_V1','status':'PLAN_ONLY',
            'work_root':str(work),'staging_root':str(staging),'source_commit':source_identity(),
            'source_clean':source_clean(),'environment':env,'resume':resume,
            'asset_verification':assets,'scientific_stage_started':False,'payloads_parsed':False,
            'work_root_created_or_written':False,'count_targets_used':False,'historical_generated_fallback':False,
            'stage_plans':[{'stage':s,'command':command(s,work,staging,False,resume),
                            'parent':'none' if s=='S0' else str(work/STAGES[STAGES.index(s)-1]),
                            'dependency_validation':'At execution boundary by existing audited contracts; absent parents are not fabricated'} for s in stages],
            'stage_launcher_invoked':False,'protocol_stops_preserved':True,'R_C':'REQUIRES_EXTERNAL_POST_COMPUTATION_AUDIT'}

def main():
    p=argparse.ArgumentParser(description=__doc__); g=p.add_mutually_exclusive_group(required=True)
    g.add_argument('--dry-run',action='store_true'); g.add_argument('--execute',action='store_true')
    p.add_argument('--work-root',type=Path,required=True); p.add_argument('--staging-root',type=Path,required=True)
    p.add_argument('--stage',choices=('all',)+STAGES,default='all'); p.add_argument('--resume',action='store_true')
    a=p.parse_args()
    try:
        work,staging=roots(a.work_root,a.staging_root); env=runtime(); require_canonical(env); stages=selected(a.stage)
        if a.dry_run: report=dry_plan(work,staging,stages,a.resume,env)
        else:
            source,files,baseline=check_source(); assets=verify_assets(staging)
            if assets['status']!='PASS': raise ValueError('five exact staged archives required; no download/substitution')
            identity=execution_identity(work,staging,env,assets,source)
            signal.signal(signal.SIGTERM,lambda sig,frame: (_ for _ in ()).throw(KeyboardInterrupt()))
            report=orchestrate(work,staging,stages,a.resume,identity,runtime_observation=env)
        print(json.dumps(report,indent=2,sort_keys=True)); return 0
    except KeyboardInterrupt:
        print('PUBLIC_REPRODUCTION_STOPPED: interrupted; preserve root and use --resume',file=sys.stderr); return 130
    except (ValueError,FileExistsError,ProtocolStop,RuntimeError,OSError,KeyError) as exc:
        print('PUBLIC_REPRODUCTION_STOPPED: '+str(exc),file=sys.stderr); return 3

if __name__ == '__main__': raise SystemExit(main())
