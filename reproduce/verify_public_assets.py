"""Opaque transport verification only; no extraction, seeds or payload parsing."""
import argparse
import json
import re
from pathlib import Path
from .common import SOURCE_ROOT, file_sha256

MANIFEST = SOURCE_ROOT / 'reproduce/PUBLIC_RELEASE_ASSETS.json'

def no_symlinks(path):
    path = Path(path)
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('symlink namespace refused: ' + str(path))
    return path

def load_assets():
    obj = json.loads(MANIFEST.read_text())
    frozen = json.loads((SOURCE_ROOT/'inputs/P13_REPRO_INPUT_MANIFEST.json').read_text())['historical_random_realizations']
    actual = [(r['id'], r['filename'], r['bytes'], r['sha256']) for r in obj['assets']]
    expected = [(r['id'], r['staged_basename'], r['bytes'], r['sha256']) for r in frozen]
    if obj['schema'] != 'P13_PUBLIC_RELEASE_ASSETS_V1' or actual != expected:
        raise ValueError('release asset commitments differ from frozen input manifest')
    return obj

def verify_assets(staging_root, manifest=None):
    obj = load_assets() if manifest is None else manifest
    rows = obj['assets']
    if len(rows) != 5 or len({r['id'] for r in rows}) != 5 or len({r['filename'] for r in rows}) != 5:
        raise ValueError('exactly five unique assets required')
    root = no_symlinks(Path(staging_root))
    if not root.is_absolute(): raise ValueError('explicit absolute staging root required')
    results = []
    for row in rows:
        name = row['filename']
        if Path(name).name != name or name in ('.', '..') or '/' in name or '\\' in name:
            raise ValueError('asset filename must be an exact basename')
        if not re.fullmatch('[0-9a-f]{64}', row['sha256']) or type(row['bytes']) is not int or row['bytes'] < 0:
            raise ValueError('invalid asset commitment')
        p = root / name
        result = {'id': row['id'], 'filename': name, 'expected_bytes': row['bytes'], 'expected_sha256': row['sha256']}
        if p.is_symlink(): result['status'] = 'SYMLINK_REFUSED'
        elif not p.exists(): result['status'] = 'MISSING'
        elif not p.is_file(): result['status'] = 'NOT_REGULAR_FILE'
        else:
            result['actual_bytes'] = p.stat().st_size
            if result['actual_bytes'] != row['bytes']: result['status'] = 'BYTES_MISMATCH'
            else:
                result['actual_sha256'] = file_sha256(p)
                result['status'] = 'PASS' if result['actual_sha256'] == row['sha256'] else 'SHA256_MISMATCH'
        results.append(result)
    return {'schema': 'P13_PUBLIC_ASSET_VERIFICATION_V1', 'status': 'PASS' if all(r['status']=='PASS' for r in results) else 'BLOCKED',
            'staging_root': str(root), 'verification': 'EXACT_FILENAME_BYTES_SHA256_OPAQUE_ONLY',
            'payloads_parsed': False, 'archives_unpacked': False, 'download_or_regeneration_fallback': False, 'assets': results}

def main():
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--staging-root',type=Path,required=True)
    a = p.parse_args(); report = verify_assets(a.staging_root)
    print(json.dumps(report,indent=2,sort_keys=True)); return 0 if report['status']=='PASS' else 2

if __name__ == '__main__': raise SystemExit(main())
