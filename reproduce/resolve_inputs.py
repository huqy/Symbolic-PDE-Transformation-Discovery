"""Stage-gated archive locator. No tar import, extraction or payload parsing."""
import argparse
import json
from pathlib import Path

from .common import SOURCE_ROOT, STAGES, contained, file_sha256, relative_path

# Capabilities are deliberately exact consumers; later stages are not implicitly
# granted all earlier inputs. Search and membership stages have no capability.
CAPABILITIES = {
    'within_family_diagnostic': ('S1-K2B',),
    'development_coefficient': ('S2-K0', 'S2-K4R', 'S2-K5'),
    'development_response': ('S2-K4R',),
    'sealed_coefficient': ('S3-K1', 'S3-K3', 'S3-K4'),
    'sealed_response': ('S3-K3',),
}

def load_manifest():
    path = SOURCE_ROOT / 'inputs/P13_REPRO_INPUT_MANIFEST.json'
    obj = json.loads(path.read_text())
    if obj.get('schema') != 'P13_REPRO_INPUT_MANIFEST_V1':
        raise ValueError('unsupported input manifest')
    rows = obj['historical_random_realizations']
    if len(rows) != 5 or {r['id'] for r in rows} != set(CAPABILITIES):
        raise ValueError('five unique committed input IDs required')
    for row in rows:
        name = row['staged_basename']
        if len(relative_path(name).parts) != 1:
            raise ValueError('staged name must be a basename')
        if tuple(row['allowed_consumers']) != CAPABILITIES[row['id']]:
            raise ValueError('visibility policy drift')
        if row['first_stage_allowed'] != CAPABILITIES[row['id']][0]:
            raise ValueError('first visibility stage drift')
        if row['id'] == 'within_family_diagnostic' and row['membership_authority'] != 'NONE':
            raise ValueError('diagnostic membership authority forbidden')
    return obj

def _verified_local(row, staging_root):
    # Only the explicit staging root is consulted. historical_locator is never used.
    path = contained(staging_root, row['staged_basename'], must_exist=True)
    if not path.is_file() or path.stat().st_size != row['bytes']:
        raise ValueError('archive size mismatch: ' + row['id'])
    if file_sha256(path) != row['sha256']:
        raise ValueError('archive SHA mismatch: ' + row['id'])
    return path

def resolve_input(input_id, stage, staging_root):
    # Deny before touching staging_root or archive bytes.
    if stage not in STAGES or stage not in CAPABILITIES.get(input_id, ()):
        raise PermissionError('private input is not visible to this exact stage')
    obj = load_manifest()
    # Only the committed manifest supplies expected hashes and visibility.
    rows = [r for r in obj['historical_random_realizations'] if r['id'] == input_id]
    if len(rows) != 1 or tuple(rows[0]['allowed_consumers']) != CAPABILITIES[input_id]:
        raise ValueError('invalid input capability row')
    return _verified_local(rows[0], staging_root)

def stage_view(stage, staging_root):
    if stage not in STAGES:
        raise ValueError('unknown stage')
    return {ident: str(resolve_input(ident, stage, staging_root))
            for ident, allowed in CAPABILITIES.items() if stage in allowed}

def audit_inventory(staging_root):
    """Administrative integrity check: opaque compressed bytes only; no paths out."""
    rows = []
    for row in load_manifest()['historical_random_realizations']:
        _verified_local(row, staging_root)
        rows.append({'id': row['id'], 'sha256': row['sha256'], 'bytes': row['bytes'], 'verified': True})
    return {'status': 'PASS', 'kind': 'SHA_ONLY_TRANSPORT_AUDIT', 'payloads_inspected': False, 'archives': rows}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--staging-root', type=Path, required=True)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('audit')
    request = sub.add_parser('resolve')
    request.add_argument('--stage', choices=STAGES, required=True)
    request.add_argument('--input-id', choices=tuple(CAPABILITIES), required=True)
    args = parser.parse_args()
    if args.command == 'audit':
        print(json.dumps(audit_inventory(args.staging_root), indent=2))
    else:
        print(resolve_input(args.input_id, args.stage, args.staging_root))

if __name__ == '__main__':
    main()
