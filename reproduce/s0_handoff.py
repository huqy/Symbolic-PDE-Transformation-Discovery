"""Read a computed fresh S0 freeze for future S1 wiring. Never launch S1."""
from pathlib import Path
from .s0_contract import context,parent,load,run_relative
from .common import contained

def s1_entry(execution_root):
    project,lock=context(Path(execution_root)/'project')
    k3=parent(project,'K3');entry=load(k3/'s1_entry_manifest.json');adjud=load(k3/'s0_gate_adjudication.json')
    if entry['status']!='AUTHORIZED' or adjud['status']!='PASS' or adjud['S1_K0_authorized'] is not True:
        return {'execution_id':lock['execution_id'],'status':'NOT_AUTHORIZED','formal_search_authorized':False,'S1_started':False}
    k1=parent(project,'K1')
    if entry['active_inputs']['K1_run']!=run_relative('K1'):raise ValueError('S1 entry references non-fresh K1')
    manifest=load(k1/'open_search_object_manifest.json')
    train=[]
    for row in manifest['objects']:
        if row['role']!='TRAIN_OPERATOR':continue
        p=contained(k1,row['path'],True)
        train.append({'field_id':row['field_id'],'grid':row['grid'],'execution_relative_path':str(p.relative_to(project.parent)),
                      'sha256':row['sha256'],'semantic_digest':row['semantic_digest']})
    return {'execution_id':lock['execution_id'],'status':entry['status'],'formal_search_authorized':False,
            'S1_started':False,'fresh_S0_freeze_relative':'project/'+run_relative('K3'),'TRAIN_objects':train,
            'private_payload_paths':[],'calibration_seed_objects':[],'entry_contract':entry}
