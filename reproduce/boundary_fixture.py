"""Small authenticated stage interfaces; no science, archives or result targets."""
from pathlib import Path
from .common import digest, file_sha256, SOURCE_ROOT
from .s0_contract import P, load, write
from . import s1_lineage as s1, s2_contract as s2

def s1_fixture(base, count=2):
    root=Path(base)/'S1'; project=root/'project'; project.mkdir(parents=True)
    s0=Path(base)/'S0'; contract=s0/'project'/P/'runs/fresh_s0_k1/objective_response_contract_lock.json';write(contract,{'fixture':True})
    rec={'artifacts':{'objective_response_contract_lock.json':{'sha256':file_sha256(contract),'bytes':contract.stat().st_size}}};rec['receipt_digest']=digest(rec);write(s0/'receipts/K1.json',rec)
    write(root/'s1_execution.json',{'execution_id':'fixture-S1','source_commit':'fixture-source','s0':{'root':str(s0),'K1_receipt':rec}})
    def obj(rel,value):
        path=project/rel;write(path,value);return s1.record(project,path)
    def rows(rel,values):
        path=project/rel;s2.write_rows(path,values);return s1.record(project,path,True)
    clear=rows(s1.RUN+'/clear.jsonl',[{'scientific_branch_id':'synthetic-'+str(i)} for i in range(count)]);unresolved=rows(s1.RUN+'/unresolved.jsonl',[])
    final=obj(s1.RUN+'/FINAL_TRAIN_membership.json',{'clear_FULL_exact_execution_class_count':count})
    desc={step:[rows(s1.RUN+'/'+name,[])] for step,name in [('K2B','K2B_diagnostics/K2B_diagnostic_branch_results.jsonl'),('PF0','PF0_postfreeze/PF0_theory_geometry_rows.jsonl')]}
    desc['PF0'].append(rows(s1.RUN+'/PF0_postfreeze/PF0_ASP_branch_results.jsonl',[]))
    null={k:obj(s1.RUN+'/'+k+'.json',{'fixture':True}) for k in ('instrument_lock','pair_builder_source')}
    metadata=load(SOURCE_ROOT/'reproduce/s2_public_commitments.json')['commitments']
    active={'role':'ACTIVE_S2_INPUT_MANIFEST_AFTER_PF0','same_AST_theta_gauge_zero_refit':True,'branch_reselection_forbidden':True,'PF0_has_membership_authority':False,'response_stage_blocked_at_entry':True,'clear_membership':clear,'unresolved_lineage':dict(unresolved,S2_eligible=False),'null_control':null,'DEVELOPMENT_coefficient_commitment':dict(metadata['coefficient:DEVELOPMENT_COEF'],input_id='development_coefficient'),'DEVELOPMENT_response_commitment':dict(metadata['response:DEVELOPMENT_COEF'],input_id='development_response')}
    bound=obj(s1.RUN+'/PF1_final_freeze/PF1_S2_ACTIVE_INPUT_MANIFEST.json',active)
    receipts={}
    for step in ('K0','K1A','K1B','K2A','FINAL','K2B','K2C','K3','PF0','PF1'):
        out={'FINAL':[final],'PF1':[bound],**desc}.get(step,[])
        r=s1.seal({'execution_id':'fixture-S1','step':step,'parents':{s:r['digest'] for s,r in receipts.items()},'outputs':out});write(root/'receipts'/(step+'.json'),r);receipts[step]=r
    write(root/'membership_lock.json',s1.seal({'execution_id':'fixture-S1','parents':{'FINAL':receipts['FINAL']['digest']},'continuation':{'authorized':False},'membership':{'clear':clear,'unresolved':unresolved},'registries':[]}))
    return root

def s2_fixture(base, count=2):
    root=Path(base)/'S2'; project=root/'project'; project.mkdir(parents=True)
    write(root/'s2_execution.json',s1.seal({'execution_id':'fixture-S2','source_commit':'fixture-source','s1':{'PF1_receipt_digest':'fixture-parent'},'source_files':{}}))
    formal=[{'scientific_branch_id':'synthetic-'+str(i)} for i in range(count)]
    op=[dict(r,III_B_decision=s2.OP[0]) for r in formal];resp=[dict(r,decision=s2.RESP[0]) for r in formal]
    files={}
    def add(step,name,value,isrows=False):
        path=s2.stage_dir(project,step)/name
        (s2.write_rows if isrows else write)(path,value);files.setdefault(step,[]).append(s2.record(path));return path
    membership=add('K5','K5_RESPONSE_PASS_MEMBERSHIP.jsonl',formal,True)
    add('K5','K5_RESPONSE_DECISION_MAP.jsonl',resp,True);add('K2','K2_III_B_DECISION_MAP.jsonl',op,True)
    add('K4','K4_SCIENTIFIC_SUMMARY.json',{'reference_certified_count':32,'identity_decision':s2.RESP[2],'frozen_null_decision':s2.RESP[2]})
    add('K7','K7_SCIENTIFIC_SUMMARY.json',{'OVERALL_STATUS':'PASS','SEALED_opened':False,'operator_counts':dict(zip(s2.OP,(count,0,0))),'response_counts':dict(zip(s2.RESP,(count,0,0)))})
    add('K7','K7_S3_DECISION_LOCK.json',{'opening_performed':False,'eligible_count_if_authorized':count,'eligible_membership_sha256':file_sha256(membership)})
    receipts={}
    for step in s2.STEPS:
        r=s1.seal({'execution_id':'fixture-S2','step':step,'parents':{s:r['digest'] for s,r in receipts.items()},'S1_parent':'fixture-parent','outputs':files.get(step,[])})
        write(root/'receipts'/(step+'.json'),r);receipts[step]=r
    return root
