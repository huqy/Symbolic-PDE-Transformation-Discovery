"""Verify and copy five opaque frozen archives to explicit external staging."""
import argparse
import os
import shutil
from pathlib import Path
from .common import SOURCE_ROOT
from .verify_public_assets import load_assets, verify_assets, no_symlinks

def stage(destination, source=SOURCE_ROOT/'public_assets', manifest=None):
    source=no_symlinks(Path(source)); destination=no_symlinks(Path(destination))
    if not destination.is_absolute(): raise ValueError('explicit absolute destination required')
    src=source.resolve(strict=True); dst=destination.resolve()
    for other in (src,SOURCE_ROOT.resolve()):
        if dst==other or other in dst.parents or dst in other.parents:
            raise ValueError('source/destination overlap refused')
    # A staging directory must not live inside any existing scientific work root.
    for p in (dst,*dst.parents):
        if any((p/n).exists() for n in ('public_execution.json','execution_lock.json','s1_execution.json','s2_execution.json','s3_execution.json')):
            raise ValueError('work/staging overlap refused')
    obj=load_assets() if manifest is None else manifest
    report=verify_assets(src,obj)
    if report['status']!='PASS': raise ValueError('source assets failed verification')
    existing=verify_assets(dst,obj)
    if any(r['status'] not in ('MISSING','PASS') for r in existing['assets']):
        raise ValueError('mismatched destination overwrite refused')
    dst.mkdir(parents=True,exist_ok=True)
    for row,observed in zip(obj['assets'],existing['assets']):
        if observed['status']=='PASS': continue
        target=dst/row['filename']; no_symlinks(target)
        with (src/row['filename']).open('rb') as inp, target.open('xb') as out:
            shutil.copyfileobj(inp,out)
    result=verify_assets(dst,obj)
    if result['status']!='PASS': raise ValueError('destination verification failed')
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--destination',type=Path,required=True)
    a=p.parse_args()
    import json
    print(json.dumps(stage(a.destination),indent=2))
if __name__=='__main__': main()
