"""Real S0 artifact adapters; reference comparison never authorizes execution."""
import argparse
import copy
import hashlib
import json
import tarfile
from pathlib import Path
from .common import SOURCE_ROOT,contained
from .s0_contract import load,STEPS,parent
from .scientific_compare import exact_tree

def contract():return load(SOURCE_ROOT/'provenance/S0_REAL_OUTPUT_SCHEMA.json')

def validate_summary(step,obj):
    spec=contract()['stages'][step]
    if not isinstance(obj,dict) or set(obj)!=set(spec['required_summary_fields']):
        raise ValueError('unknown/missing real S0 summary fields: '+step)
    if obj['OVERALL_STATUS'] not in {'PASS','FAIL','UNRESOLVED'}:raise ValueError('invalid computed decision')
    if 'gate_statuses' in obj:
        gates=obj['gate_statuses']
        if not isinstance(gates,dict) or not gates or set(gates)-set(spec['gate_names']):raise ValueError('unknown/missing gate fields')
    for key in ['fatal','failure_classifications']:
        if key in obj and not isinstance(obj[key],list):raise ValueError('failure field must be a list')
    if 'private_commitments' in obj:
        rows=obj['private_commitments']
        keys={'coefficient:DEVELOPMENT_COEF','coefficient:SEALED_FINAL_COEF','response:DEVELOPMENT_COEF','response:SEALED_FINAL_COEF'}
        if set(rows)!=keys:raise ValueError('commitment membership mismatch')
        for v in rows.values():
            if set(v)!={'archive_sha256','row_count','payload_semantic_digest'}:raise ValueError('unknown/missing commitment summary field')
    shape=spec['scientific_files']['audit_summary.json']['reference_shape']
    for key,value in obj.items():
        if key not in {'gate_statuses','private_commitments','fatal','failure_classifications'}:
            validate_fixed_shape(shape['fields'][key],value)
    return obj

def validate_fixed_shape(shape,obj,path=''):
    if shape['type']=='object':
        flexible=path.endswith('/causal/charts')
        if not isinstance(obj,dict) or (set(obj)-set(shape['fields']) if flexible else set(obj)!=set(shape['fields'])):raise ValueError('unknown/missing nested scientific field')
        for k,v in obj.items():validate_fixed_shape(shape['fields'][k],v,path+'/'+k)
    elif shape['type']=='array':
        if not isinstance(obj,list):raise ValueError('expected scientific array')
        for item in obj:
            if not shape['items']:continue
            for variant in shape['items']:
                try:validate_fixed_shape(variant,item,path+'/*');break
                except ValueError:pass
            else:raise ValueError('unknown/missing scientific array record fields')
    elif isinstance(obj,(dict,list)):raise ValueError('expected scalar scientific value')

def validate_artifact(step,name,obj):
    if name=='audit_summary.json':return validate_summary(step,obj)
    spec=contract()['stages'][step]['scientific_files'][name]['reference_shape']
    if not isinstance(obj,dict):raise ValueError('S0 gate evidence must be an object')
    # These shapes are emitted explicitly by the source on prerequisite failure.
    if obj.get('status')=='NOT_RUN':
        if set(obj)-{'status','reason','diagnostic_only','residual_graft_adjudication','production_fitter_adjudication'}:raise ValueError('unknown NOT_RUN field')
        return obj
    record=contract()['stages'][step]['scientific_files'][name]
    variants=record['source_top_level_variants']
    if not any(set(obj)==set(keys) for keys in variants):raise ValueError('unknown/missing scientific fields in '+step+'/'+name)
    # Freeze/contract schemas are fixed; gate-result alternatives retain full
    # dictionaries for comparison and always require their decision field.
    fixed=step in {'K0','K3'} or name in {'objective_response_contract_lock.json','role_family_separation_certificate.json','no_leakage_guard.json'}
    required=set(spec['fields']) if fixed else ({'status'} if 'status' in spec['fields'] else set(spec['fields']))
    if not required<=obj.keys():raise ValueError('missing scientific fields in '+step+'/'+name)
    if fixed:validate_fixed_shape(spec,obj)
    return obj

def scientific_projection(step,name,obj):
    validate_artifact(step,name,obj);out=copy.deepcopy(obj);cfg=contract()
    removed={}
    if name=='audit_summary.json':
        for key in cfg['stages'][step]['transport_summary_fields']:removed['/'+key]=out.pop(key)
    for pointer in cfg.get('transport_file_paths',{}).get(step,{}).get(name,[]):
        keys=pointer.strip('/').split('/');node=out
        for k in keys[:-1]:node=node[k]
        removed[pointer]=node.pop(keys[-1])
    return out,removed

def compare(step,name,fresh,reference):
    a,at=scientific_projection(step,name,fresh);b,bt=scientific_projection(step,name,reference)
    # json exact encoding includes nonfinite values in legacy numerical evidence;
    # compare symbolic NaN/Infinity directly, never convert to a finite value.
    def exact(value):
        if isinstance(value,float):return ['float',value.hex()]
        if isinstance(value,dict):return ['object',[(k,exact(v)) for k,v in sorted(value.items())]]
        if isinstance(value,list):return ['array',[exact(v) for v in value]]
        return [type(value).__name__,value]
    return {'stage':step,'artifact':name,'scientific_equal':exact(a)==exact(b),
            'transport_differences':{k:{'fresh':at.get(k),'reference':bt.get(k)} for k in set(at)|set(bt) if at.get(k)!=bt.get(k)},
            'authorizes_execution':False,'tolerance_added':False}

def compare_capsule(execution_root,capsule):
    """Called separately AFTER computation. No launcher imports reference bytes."""
    root=Path(execution_root)/'project';parent(root,'K3')
    expected='307a454173dfcb79d1150187d622053f42df5317d0688e6f0e4a22126fce2fa4'
    from .common import file_sha256
    if file_sha256(capsule)!=expected:raise ValueError('reference capsule SHA mismatch')
    results=[]
    with tarfile.open(capsule) as tf:
        members={m.name.split('/',1)[1]:m for m in tf if m.isfile() and '/' in m.name}
        for step in STEPS:
            run=parent(root,step)
            for name,row in contract()['stages'][step]['scientific_files'].items():
                raw=tf.extractfile(members[row['reference_member']]).read()
                if hashlib.sha256(raw).hexdigest()!=row['reference_sha256']:raise ValueError('reference artifact SHA mismatch')
                if name in contract().get('optional_outputs',{}).get(step,[]) and not (run/name).exists():
                    results.append({'stage':step,'artifact':name,'scientific_equal':False,'difference':'FRESH_OPTIONAL_OUTPUT_ABSENT'});continue
                results.append(compare(step,name,load(contained(run,name,True)),json.loads(raw)))
    return {'role':'POST_COMPUTATION_COMPARISON_ONLY','comparison_scope':contract()['comparison_scope'],'records':results,'all_compact_scientific_evidence_equal':all(x['scientific_equal'] for x in results),'authorizes_execution':False}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--execution-root',type=Path,required=True);p.add_argument('--reference-capsule',type=Path,required=True)
    args=p.parse_args();print(json.dumps(compare_capsule(args.execution_root,args.reference_capsule),indent=2))
