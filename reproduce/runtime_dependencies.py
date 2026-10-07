"""Source-file closure verification, independent of scientific execution."""
import ast
import json
from pathlib import Path
from .common import SOURCE_ROOT, contained

CATALOG='reproduce/RUNTIME_DEPENDENCIES.json'

def discovered_sidecars(root):
    found={}
    for p in [*(root/'reproduce').glob('*.py'),*(root/'phases').rglob('src/*.py')]:
        if p.name.startswith(('test_','runtime_dependencies')) or p.stem.endswith('_fixture'): continue
        tree=ast.parse(p.read_text()); consumer=p.relative_to(root).as_posix()
        for n in ast.walk(tree):
            path=None
            if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='with_name' and n.args and isinstance(n.args[0],ast.Constant) and isinstance(n.args[0].value,str):
                path=(p.parent/n.args[0].value).relative_to(root).as_posix()
            elif isinstance(n,ast.Constant) and isinstance(n.value,str) and n.value.startswith(('reproduce/','inputs/','environment/','provenance/')) and Path(n.value).suffix in ('.json','.md','.txt','.sh'):
                path=n.value
            # Read denials are not inputs. Test-only fixtures are excluded above.
            if path and path not in {'reproduce/s1_reference.json'}:
                paths=[path % i for i in range(4)] if '%d' in path else [path]
                for resolved in paths: found.setdefault(resolved,set()).add(consumer)
    return found

def audit(root=SOURCE_ROOT):
    root=Path(root).resolve(); manifest=json.loads(contained(root,'reproduce/RELEASE_SOURCE_MANIFEST.json',True).read_text())
    listed={r['path']:r for r in manifest['files']}
    catalog=json.loads(contained(root,CATALOG,True).read_text())['dependencies']
    rows={r['required_path']:dict(r) for r in catalog}
    for path,consumers in discovered_sidecars(root).items():
        if path not in rows:
            rows[path]={'required_path':path,'consumer_module_function':sorted(consumers),'first_stage_used':'source-discovered','role':'publication_administration','test_coverage':'test_runtime_closure'}
    for path,row in rows.items():
        try: row['file_present']=contained(root,path,True).is_file()
        except (ValueError,FileNotFoundError): row['file_present']=False
        row['included_in_RELEASE_SOURCE_MANIFEST']=path in listed or path=='reproduce/RELEASE_SOURCE_MANIFEST.json'
    return {'schema':'P13_RUNTIME_DEPENDENCY_CLOSURE_V1','status':'PASS' if all(r['file_present'] and r['included_in_RELEASE_SOURCE_MANIFEST'] for r in rows.values()) else 'FAIL',
            'dependencies':[rows[k] for k in sorted(rows)],'payloads_opened':False,'heavy_science':False}

if __name__=='__main__':
    report=audit();print(json.dumps(report,indent=2,sort_keys=True));raise SystemExit(report['status']!='PASS')
