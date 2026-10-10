"""Complete authenticated coordinator metadata fixtures; no scientific execution."""
import shutil
from pathlib import Path
from .common import SOURCE_ROOT, digest, file_sha256
from .s0_contract import load, write, P
from . import s0_contract as s0, s1_lineage as s1, s2_contract as s2, s3_contract as s3
from .boundary_fixture import s1_fixture, s2_fixture
from .release_integrity import MANIFEST

SOURCE='fixture-source'

def snapshot(project):
    manifest=load(SOURCE_ROOT/MANIFEST)
    rows={}
    for rel in [r['path'] for r in manifest['files']]+[MANIFEST]:
        src=SOURCE_ROOT/rel; dst=project/rel; dst.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(src,dst); dst.chmod(src.stat().st_mode & 0o777)
        rows[rel]={'sha256':file_sha256(dst),'bytes':dst.stat().st_size,'mode':dst.stat().st_mode & 0o777}
    return rows

def prepare(base, formal_count=2, unresolved_count=1, fail_count=1):
    base=Path(base);total=formal_count+unresolved_count+fail_count
    root1=s1_fixture(base,total); p0=base/'S0/project'; p1=root1/'project'
    source0=snapshot(p0); source1=snapshot(p1)
    assets=load(SOURCE_ROOT/'reproduce/PUBLIC_RELEASE_ASSETS.json')['assets']
    c0={'schema':'P13_S0_EXECUTION_LOCK_V1','execution_id':'fixture-S0','source_commit':SOURCE,'source_files':source0,
        'input_transport_verification':{'status':'PASS','payloads_inspected':False,'archives':[{'id':a['id'],'bytes':a['bytes'],'sha256':a['sha256'],'verified':True} for a in assets]}}
    write(base/'S0/execution_lock.json',c0);write(p0/'.s0_context.json',{'schema':'P13_S0_CONTEXT_V1','execution_id':c0['execution_id']})
    receipts={}
    for step in s0.STEPS:
        d=p0/s0.run_relative(step);write(d/'semantic_output_digest.json',{'semantic_output_digest':digest({'fixture':step})})
        write(d/'audit_summary.json',{'OVERALL_STATUS':'PASS','S1_K0_authorized':step=='K3'})
        paths=list(d.iterdir());artifacts={p.name:{'sha256':file_sha256(p),'bytes':p.stat().st_size} for p in paths if p.is_file()}
        rec={'schema':'P13_S0_STAGE_RECEIPT_V1','execution_id':c0['execution_id'],'step':step,'run_relative':s0.run_relative(step),'computed_status':'PASS','source_commit':SOURCE,'semantic_output_digest':digest({'fixture':step}),'parent_receipt_digests':{s:r['receipt_digest'] for s,r in receipts.items()},'artifacts':artifacts}
        rec['receipt_digest']=digest(rec);write(base/'S0/receipts'/(step+'.json'),rec);receipts[step]=rec
    c1=load(root1/'s1_execution.json');c1.update(schema='P13_S1_EXECUTION_V1',source_files=source1,staging_root=str(base.parent/'assets'),s0={'root':str(base/'S0'),'execution_id':c0['execution_id'],'source_commit':SOURCE,'K1_receipt':receipts['K1'],'K3_receipt':receipts['K3']})
    write(root1/'s1_execution.json',c1)
    root2=s2_fixture(base,formal_count);p2=root2/'project';source2=snapshot(p2)
    header,*_=s2.handoff(root1,verify_registries=True)
    c2={'schema':'P13_S2_EXECUTION_V1','execution_id':'fixture-S2','source_commit':SOURCE,'source_files':source2,'s1':header,'scientific_budget_changed':False,'public_contract':s2.record(SOURCE_ROOT/'PUBLIC_REPRODUCTION_CONTRACT.md'),'staging_root':c1['staging_root']}
    write(root2/'s2_execution.json',s1.seal(c2))
    op=[{'scientific_branch_id':'synthetic-'+str(i),'III_B_decision':s2.OP[0] if i<formal_count else s2.OP[1] if i<formal_count+unresolved_count else s2.OP[2],'same_AST_theta_gauge_zero_refit':True} for i in range(total)]
    s2.write_rows(s2.stage_dir(p2,'K2')/'K2_III_B_DECISION_MAP.jsonl',op)
    controls={'identity':{'decision':s2.RESP[2]},'frozen_null':{'decision':s2.RESP[2]}}
    k4=load(s2.stage_dir(p2,'K4')/'K4_SCIENTIFIC_SUMMARY.json');k4.update(reference_status='PASS',reference_unresolved_count=0,scientific_outcome='DISCRIMINATIVE_ABSOLUTE_GATE')
    write(s2.stage_dir(p2,'K4')/'K4_SCIENTIFIC_SUMMARY.json',k4)
    write(s2.stage_dir(p2,'K4')/'K4_REFERENCE_CERTIFICATION.json',{'status':'PASS','candidate_independent':True,'certified_count':32,'unresolved_count':0,'reference_uncertainty_ceiling':load(p2/P/'configs/p13_s2_k4_protocol.json')['reference_uncertainty_ceiling']})
    write(s2.stage_dir(p2,'K4')/'K4_CONTROL_FIRST_RESULTS.json',{'controls':controls})
    rc=dict(zip(s2.RESP,(formal_count,0,0))); oc=dict(zip(s2.OP,(formal_count,unresolved_count,fail_count)))
    write(s2.stage_dir(p2,'K5')/'K5_SCIENTIFIC_SUMMARY.json',{'complete_response_cohort':formal_count,'decision_counts':rc,'candidate_specific_rescue':False,'top_k_or_proxy_filter':False})
    k7=load(s2.stage_dir(p2,'K7')/'K7_SCIENTIFIC_SUMMARY.json');k7.update(operator_counts=oc,claim_contract_evidence=s2.claim_evidence(oc,rc,controls));write(s2.stage_dir(p2,'K7')/'K7_SCIENTIFIC_SUMMARY.json',k7)
    decision=load(s2.stage_dir(p2,'K7')/'K7_S3_DECISION_LOCK.json');decision.update(explicit_S3_authorization_required=True,shortlist_forbidden=True);write(s2.stage_dir(p2,'K7')/'K7_S3_DECISION_LOCK.json',decision)
    reseal_s2(base)
    return base

def reseal_s2(base):
    """Reseal intentionally changed synthetic fixture evidence for gate tests."""
    root=Path(base)/'S2';project=root/'project';ctx=s2.state(project);receipts={}
    for step in s2.STEPS:
        d=s2.stage_dir(project,step);sp=d/(step+'_SCIENTIFIC_SUMMARY.json')
        summary=load(sp) if sp.exists() else {};summary.setdefault('OVERALL_STATUS','PASS');summary.setdefault('SEALED_opened',False)
        sem={'basis':{'fixture':step,'summary':{k:v for k,v in summary.items() if k!='semantic_output_digest'}}};sem['semantic_output_digest']=digest(sem['basis']);summary['semantic_output_digest']=sem['semantic_output_digest']
        write(sp,summary);write(d/(step+'_SEMANTIC_OUTPUT_DIGEST.json'),sem)
        write(d/'REPRODUCTION_STAGE_COMPLETE.json',{'schema':'P13_S2_COMPLETE_MARKER_V1','execution_id':ctx['execution_id'],'stage':step,'semantic_output_digest':sem['semantic_output_digest'],'parents':{s:r['digest'] for s,r in receipts.items()}})
        rec=s1.seal({'schema':'P13_S2_RECEIPT_V1','execution_id':ctx['execution_id'],'step':step,'parents':{s:r['digest'] for s,r in receipts.items()},'S1_parent':ctx['s1']['PF1_receipt_digest'],'semantic_digest':sem['semantic_output_digest'],'outputs':[s2.record(p) for p in sorted(d.rglob('*')) if p.is_file()]})
        write(root/'receipts'/(step+'.json'),rec);receipts[step]=rec

def prepare_s3(base):
    base=Path(base);root=base/'S3';project=root/'project';project.mkdir(parents=True)
    header,groups=s3.handoff(base/'S2',full=True)
    ctx={'schema':'P13_S3_EXECUTION_V1','execution_id':'fixture-S3','source_commit':SOURCE,'s2':header,'source_files':snapshot(project)}
    write(root/'s3_execution.json',s1.seal(ctx));receipts={}
    for step in s3.STEPS:
        d=s3.stage_dir(project,step);summary={'OVERALL_STATUS':'PASS','operator_UNRESOLVED_count':1}
        if step=='K6':summary['claim_contract_evidence']={'status':'CLAIM_COMPATIBLE_CANDIDATE','R_C':'NOT_AUTOMATICALLY_ASSIGNED'}
        write(d/(step+'_SCIENTIFIC_SUMMARY.json'),summary)
        rec=s1.seal({'execution_id':ctx['execution_id'],'step':step,'parents':{s:r['digest'] for s,r in receipts.items()},'S2_parent':header['K7_receipt_digest'],'outputs':[s2.record(d/(step+'_SCIENTIFIC_SUMMARY.json'))]})
        write(root/'receipts'/(step+'.json'),rec);receipts[step]=rec
