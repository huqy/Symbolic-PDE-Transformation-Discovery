"""Fresh-scratch S0 orchestration. --execute is reserved for later authorization."""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from .common import SOURCE_ROOT,contained,file_sha256,digest
from .s0_contract import P,STEPS,MODULES,DEPS,load,write,run_relative,marker_relative,parent,context
from .verify_baseline import verify
from .resolve_inputs import audit_inventory,load_manifest

PARENT='d3105025113b24a66c95bebc8ae1491bd1f8a54d'
def git(*args):return subprocess.check_output(['git','-C',str(SOURCE_ROOT),*args],text=True).strip()

def check_execution_root(path,source_root=SOURCE_ROOT):
    p=Path(path).absolute()
    forbidden={'DEV_ORIGINAL','DEV_FORENSIC','own_sMoEs','sMoEs_PDE_SR_solver','TD_Discovery_Context','TD_Discovery_Releases','R3_INPUT_STAGING','R3_ENV_CAPTURE','R4_CLEAN_ENV_QUALIFICATION','.envs'}
    if set(p.parts)&forbidden:raise ValueError('protected/original/forensic path refused')
    for part in [p,*p.parents]:
        if part.is_symlink():raise ValueError('symlink execution path refused')
    p=p.resolve();src=Path(source_root).resolve()
    if p==src or src in p.parents or p in src.parents:raise ValueError('execution under/over source tree refused')
    return p

def check_source():
    result=verify()
    if git('status','--porcelain'):raise RuntimeError('source must be committed and clean')
    subprocess.run(['git','-C',str(SOURCE_ROOT),'merge-base','--is-ancestor',PARENT,'HEAD'],check=True,capture_output=True)
    rows={}
    for rel in git('ls-files').splitlines():
        p=contained(SOURCE_ROOT,rel,True)
        rows[rel]={'sha256':file_sha256(p),'bytes':p.stat().st_size,'mode':p.stat().st_mode&0o777}
    return git('rev-parse','HEAD'),rows,result

def commitment_metadata(execution_id):
    rows={}
    for r in load_manifest()['historical_random_realizations']:
        if r['historical_commitment_stage']!='S0-K1':continue
        key=r['commitment_kind']+':'+r['commitment_role']
        rows[key]={'kind':r['commitment_kind'],'role':r['commitment_role'],'input_id':r['id'],
          'archive_bytes':r['bytes'],'archive_sha256':r['sha256'],'payload_semantic_digest':r['payload_semantic_digest'],
          'row_count':r['row_count'],'committed_field_ids':r['committed_field_ids'],
          'private_seed_material_exposed':False,'private_payload_copied_into_active_tree':False,
          'mode':'HISTORICAL_REPLAY','semantic_digest_verification':'FROZEN_COMMITMENT_NOT_PAYLOAD_RECOMPUTATION'}
    if len(rows)!=4:raise ValueError('four S0 commitments required')
    return {'schema':'P13_S0_HISTORICAL_REPLAY_V1','execution_id':execution_id,'mode':'HISTORICAL_REPLAY','commitments':rows}

def plan():
    from .s0_schema import contract
    schema=contract();steps=[]
    for step in STEPS:
        outputs=list(schema['stages'][step]['scientific_files'])+['OVERALL_STATUS.txt','NEXT_ACTION.txt','semantic_output_digest.json','source_manifest.json','runtime_environment.json']
        if step=='K1':outputs+=['private_payload_commitments.json','open_search_object_manifest.json','open_search_objects/**/*.npz','public_coefficient_registry.jsonl','open_generator_provenance.jsonl']
        if step in {'K2','K2R2','K2R3','K2R4'}:outputs+=['authoritative/*','calibration_only/*','k3_handoff_manifest.json']
        steps.append({'step':'S0-'+step,'command':['$PYTHON','-B','-m','reproduce.s0_step','--project-root','$EXECUTION_ROOT/project','--step',step],
          'scientific_module':'p13rawxt.'+MODULES[step],
          'source_config_protocol_inputs':[P+'src/p13rawxt/'+MODULES[step]+'.py',P+'configs/p13_s0_'+step.lower()+'_protocol.json','52-module verified runtime closure','original frozen source/config/test locks'],
          'prior_fresh_stages':list(DEPS[step]),'prior_outputs_consumed':dependencies(step),
          'run_directory':'project/'+run_relative(step),'marker_written':'receipts/'+step+'.json',
          'legacy_marker_projection':'project/'+marker_relative(step),'marker_read':['receipts/'+s+'.json' for s in DEPS[step]],
          'outputs':outputs,'next_consumer':STEPS[STEPS.index(step)+1] if step!='K3' else 'S1 entry adapter only; no S1 execution',
          'private_payload_capability':False,'workers':'frozen module default min(16, max(1, NSLOTS-1)); K0/K1/K3 coordinator-only',
          'progress_resume':'unchanged module ledgers/checkpoints; wrapper completion receipts bind source + same execution ID'})
    return {'schema':'P13_R4_S0_EXECUTION_PLAN_V1','mode':'PLAN_ONLY','scientific_stage_started':False,'steps':steps}

def dependencies(step):
    common={s:['audit_summary.json','semantic_output_digest.json','no_leakage_guard.json','source_manifest.json'] for s in DEPS[step]}
    if 'K1' in common:common['K1']+=['open_search_object_manifest.json','open_search_objects/CALIBRATION_COEF/*.npz','open_search_objects/TRAIN_OPERATOR/*.npz','private_payload_commitments.json (metadata only)']
    if step in {'K2R2','K2R3','K2R4'}:common['K2']+=['authoritative/fitter_fresh_prior_membership.json','authoritative/fitter_results.jsonl']
    if step=='K2R3':common['K2R2']+=['capacity_null_requalification.json','authoritative/reference_capacity_*.jsonl']
    if step=='K2R4':common['K2R3']+=['authoritative/reference_fitter_R*.jsonl','capacity_evidence_repaired.json','full_capacity_optimum_polish.json','calibration_only/capacity_instrument_lock_k2r3.json','calibration_only/stable_full_capacity_reference_k2r3.json']
    if step=='K3':common['K2R4']+=['capacity_reuse_lock.json','operational_burnin_qualification.json','operational_fitter_qualification.json','proposal_geometry_requalification.json','evaluator_fidelity_cost.json','lower_order_diagnostics.json','causal_response_feasibility.json','k3_handoff_manifest.json']
    return common

def verify_snapshot(root):
    project,lock=context(root/'project')
    for rel,row in lock['source_files'].items():
        p=contained(project,rel,True)
        if file_sha256(p)!=row['sha256'] or p.stat().st_size!=row['bytes']:raise ValueError('execution source changed: '+rel)
    return lock

def prepare(root,staging_root,resume=False):
    root=check_execution_root(root);commit,files,baseline=check_source()
    staged=Path(staging_root).resolve(strict=True)
    if staged==SOURCE_ROOT or SOURCE_ROOT in staged.parents or staged==root or root in staged.parents:
        raise ValueError('private staging must remain outside source/execution trees')
    transport=audit_inventory(staging_root) # opaque compressed bytes only
    if root.exists():
        if not resume:raise FileExistsError('existing root requires --resume and valid wrapper lock')
        lock=verify_snapshot(root)
        if lock['source_commit']!=commit:raise ValueError('resume source commit mismatch')
        if lock['source_files']!=files or lock['stage_sequence']!=list(STEPS):raise ValueError('resume snapshot contract mismatch')
        if lock['input_transport_verification']!=transport:raise ValueError('resume commitments mismatch')
        return root
    root.mkdir(parents=True,exist_ok=False);project=root/'project';project.mkdir()
    execution_id=uuid.uuid4().hex
    # Positive snapshot, no Git history, no historical outputs or symlinks.
    for rel,row in files.items():
        target=project/rel;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(contained(SOURCE_ROOT,rel,True),target);target.chmod(row['mode'])
    write(project/'s0_commitment_replay.json',commitment_metadata(execution_id))
    lock={'schema':'P13_S0_EXECUTION_LOCK_V1','execution_id':execution_id,'source_commit':commit,
          'source_files':files,'baseline_verification':baseline,'input_transport_verification':transport,
          'commitment_replay_sha256':file_sha256(project/'s0_commitment_replay.json'),
          'mode':'HISTORICAL_REPLAY','created_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
          'stage_sequence':list(STEPS),'private_payload_access_allowed':False,'advance_to_S1':False,
          'worker_policy':'unchanged frozen defaults','thread_environment':{k:'1' for k in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS']}}
    write(root/'execution_lock.json',lock)
    write(project/'.s0_context.json',{'schema':'P13_S0_CONTEXT_V1','execution_id':execution_id})
    (root/'receipts').mkdir();(root/'logs').mkdir();return root

def prepare_step(project,step):
    project,lock=context(project)
    for prior in DEPS[step]:parent(project,prior)
    run=contained(project,run_relative(step))
    if step in {'K0','K1','K3'}:
        if run.exists():raise RuntimeError('non-checkpoint stage interrupted; use a new fresh root, never overwrite')
    else:
        run.mkdir(parents=True,exist_ok=True)
        if (run/'OVERALL_STATUS.txt').exists():raise RuntimeError('completed stage lacks receipt; refuse ambiguous resume')
        marker=contained(project,marker_relative(step));marker.parent.mkdir(parents=True,exist_ok=True)
        marker.write_text(run_relative(step)+'\n')
    return run

def complete_step(project,step):
    from .s0_schema import validate_artifact,contract
    project,lock=context(project);run=contained(project,run_relative(step),True)
    cfg=contract()
    for name in cfg['stages'][step]['scientific_files']:
        if name in cfg.get('optional_outputs',{}).get(step,[]) and not (run/name).exists():continue
        validate_artifact(step,name,load(contained(run,name,True)))
    summary=load(run/'audit_summary.json');sem=load(run/'semantic_output_digest.json')
    if (run/'OVERALL_STATUS.txt').read_text().strip()!=summary['OVERALL_STATUS']:raise ValueError('summary/status mismatch')
    next_key='next_action' if step in {'K0','K1'} else 'NEXT_ACTION'
    if (run/'NEXT_ACTION.txt').read_text().strip()!=summary[next_key]:raise ValueError('summary/next mismatch')
    files={}
    for p in sorted(run.rglob('*')):
        if p.is_symlink():raise ValueError('symlink in stage output')
        if p.is_file():files[p.relative_to(run).as_posix()]={'sha256':file_sha256(p),'bytes':p.stat().st_size}
    parents={s:load(project.parent/'receipts'/f'{s}.json')['receipt_digest'] for s in DEPS[step]}
    receipt={'schema':'P13_S0_STAGE_RECEIPT_V1','execution_id':lock['execution_id'],'step':step,'run_relative':run_relative(step),
       'source_commit':lock['source_commit'],'artifacts':files,'semantic_output_digest':sem['semantic_output_digest'],
       'computed_status':summary['OVERALL_STATUS'],'parent_receipt_digests':parents,'historical_expected_decision_used':False}
    receipt['receipt_digest']=digest(receipt);write(project.parent/'receipts'/f'{step}.json',receipt)
    return receipt

def run_child(project,step):
    env=os.environ.copy();env['PYTHONDONTWRITEBYTECODE']='1';env['PYTHONPATH']=str(project)
    for k in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS']:env[k]='1'
    # No staging-root/private locator is passed to a scientific process.
    return subprocess.run([sys.executable,'-B','-m','reproduce.s0_step','--project-root',str(project),'--step',step],cwd=project,env=env,check=False).returncode

def execute(root,staging_root,resume=False):
    import platform,numpy,scipy
    from threadpoolctl import threadpool_info
    if (platform.python_version(),numpy.__version__,scipy.__version__)!=('3.10.19','2.2.6','1.15.2'):
        raise RuntimeError('use the qualified Python/NumPy/SciPy candidate')
    if any(x['num_threads']!=1 for x in threadpool_info()):raise RuntimeError('BLAS thread count must be one')
    root=prepare(root,staging_root,resume);project=root/'project'
    # Only this function calls scientific child processes; R4 tests never call it.
    import fcntl
    with (root/'coordinator.lock').open('a') as guard:
        fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
        for i,step in enumerate(STEPS,1):
            verify_snapshot(root)
            if (root/'receipts'/f'{step}.json').is_file():parent(project,step);continue
            prepare_step(project,step);print(f'[S0 {i}/{len(STEPS)}] stage={step} execution_id={load(root/"execution_lock.json")["execution_id"]}',flush=True)
            rc=run_child(project,step)
            if rc:raise RuntimeError(f'{step} exited {rc}; preserve evidence and stop')
            complete_step(project,step)
        print('S0_COMPLETE; no S1 launch. Run the separate post-computation comparison command.',flush=True)

def main():
    p=argparse.ArgumentParser(description=__doc__);g=p.add_mutually_exclusive_group(required=True)
    g.add_argument('--dry-run',action='store_true');g.add_argument('--execute',action='store_true')
    p.add_argument('--execution-root',type=Path);p.add_argument('--staging-root',type=Path);p.add_argument('--resume',action='store_true')
    args=p.parse_args()
    if args.dry_run:print(json.dumps(plan(),indent=2));return
    if args.execution_root is None or args.staging_root is None:p.error('--execute requires explicit --execution-root and --staging-root')
    if not args.execution_root.is_absolute() or not args.staging_root.is_absolute():p.error('execution/staging paths must be absolute')
    execute(args.execution_root,args.staging_root,args.resume)

if __name__=='__main__':main()
