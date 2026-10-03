"""Release-local byte authentication; no results or private Git objects."""
import json
import subprocess
from pathlib import Path
from .common import SOURCE_ROOT, contained, file_sha256, digest

MANIFEST = 'reproduce/RELEASE_SOURCE_MANIFEST.json'
PROVENANCE = 'reproduce/RELEASE_PROVENANCE.json'

def verify(root=SOURCE_ROOT):
    root = Path(root).resolve(strict=True)
    obj = json.loads(contained(root, MANIFEST, True).read_text())
    provenance = json.loads(contained(root, PROVENANCE, True).read_text())
    rows = obj['files']
    if obj['schema'] != 'PUBLIC_RELEASE_SOURCE_V1' or len({r['path'] for r in rows}) != len(rows):
        raise ValueError('invalid release source manifest')
    actual_scientific = {p.relative_to(root).as_posix() for p in (root/'phases').rglob('*') if p.is_file()}
    scientific = [r for r in rows if r['role'] == 'scientific_frozen']
    if {r['path'] for r in scientific if r['path'].startswith('phases/')} != actual_scientific:
        raise ValueError('incomplete scientific source coverage')
    if digest(scientific) != provenance['protected_scientific_digest']:
        raise ValueError('scientific commitments differ from accepted provenance')
    required = {'reproduce/s%d_launcher.py' % n for n in range(4)} | {'reproduce/run_s%d.sh' % n for n in range(4)}
    if not required <= {r['path'] for r in scientific}:
        raise ValueError('stage launcher protection missing')
    for r in rows:
        if r['path'].startswith(('reference_results/', 'public_assets/')):
            raise ValueError('reference evidence or payload in runtime source manifest')
        p = contained(root, r['path'], True)
        if not p.is_file() or p.stat().st_size != r['bytes'] or file_sha256(p) != r['sha256']:
            raise ValueError('release source drift: '+r['path'])
        if (p.stat().st_mode & 0o777) != r['mode']:
            raise ValueError('release source mode drift: '+r['path'])
    return {'status':'PASS', 'verification_mode':'RELEASE_LOCAL_EXACT_SHA256',
            'protected_scientific_files':len(scientific), 'release_files':len(rows),
            'release_semantic_digest':digest(scientific), 'scientific_files_changed':0,
            'private_git_objects_required':False, 'reference_results_used':False}

def source_identity():
    if (SOURCE_ROOT/'.git').exists():
        return subprocess.check_output(['git','-C',str(SOURCE_ROOT),'rev-parse','HEAD'],text=True).strip()
    return 'UNCOMMITTED_EXPORT:'+verify()['release_semantic_digest']

def source_clean():
    if not (SOURCE_ROOT/'.git').exists():
        verify(); return True
    return not subprocess.check_output(['git','-C',str(SOURCE_ROOT),'status','--porcelain'],text=True).strip()

def check_source():
    result = verify()
    if not (SOURCE_ROOT/'.git').exists() or not source_clean():
        raise RuntimeError('execution requires a committed clean public source tree')
    obj = json.loads((SOURCE_ROOT/MANIFEST).read_text())
    paths = [r['path'] for r in obj['files']] + [MANIFEST]
    rows = {}
    for rel in paths:
        p = contained(SOURCE_ROOT,rel,True)
        rows[rel] = {'sha256':file_sha256(p),'bytes':p.stat().st_size,'mode':p.stat().st_mode & 0o777}
    return source_identity(), rows, result

def install():
    # Keep accepted stage launcher files exact. Replace only their shared source authentication.
    from . import s0_launcher
    s0_launcher.check_source = check_source
