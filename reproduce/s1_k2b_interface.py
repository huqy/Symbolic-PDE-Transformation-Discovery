"""Exact projected K0/K2B metadata interface. Never reads diagnostic payloads."""
import re
from contextlib import contextmanager
from pathlib import Path
from .common import contained, relative_path
from .s0_contract import P, load
from .s1_lineage import K0, K1, config, context, check_record, diagnostic_capability, verify_receipt

KEY = 'existing_transfer_commitments_metadata_only'


def cross_commitment_metadata(project):
    """Historical field projection, manifest order, protocol shape, metadata only."""
    project = Path(project).resolve()
    rel = K1 + '/open_search_object_manifest.json'
    ctx = context(project)
    if rel not in ctx['input_files']: raise ValueError('projected manifest is not an authoritative input')
    manifest = load(check_record(project, ctx['input_files'][rel]))
    diag = [r for r in manifest['objects'] if r['role'] == 'OPENED_TRANSFER_DIAGNOSTIC']
    count = config(project, 'k0r')['k1_open']['required_opened_transfer_diagnostic_count']
    k2b = config(project, 'k2b')['transfer_diagnostics']
    s0 = load(project / P / 'configs/p13_s0_k1_protocol.json')
    grids = set(s0['regime']['search_object_grids'])
    if count != k2b['cross_family_count'] or count != s0['role_counts']['OPENED_TRANSFER_DIAGNOSTIC']:
        raise ValueError('cross-family protocol count mismatch')
    if not set(k2b['grids']).issubset(grids): raise ValueError('cross-family grid protocols disagree')
    ids = {r['field_id'] for r in diag}
    if len(ids) != count or any(not isinstance(fid, str) or not fid for fid in ids):
        raise ValueError('cross-family field count/IDs mismatch')
    pairs = []
    for row in diag:
        grid = row['grid']
        if type(grid) is not int or grid not in grids: raise ValueError('cross-family grid mismatch')
        path = relative_path(row['path'])
        if path.parts[:2] != ('open_search_objects', 'OPENED_TRANSFER_DIAGNOSTIC') or path.suffix != '.npz':
            raise ValueError('cross-family object path outside diagnostic role')
        # Reject aliases/escapes using path metadata only, never open the npz.
        contained(project / K1, row['path'])
        for key in ('sha256', 'semantic_digest'):
            if not isinstance(row.get(key), str) or not re.fullmatch('[0-9a-f]{64}', row[key]):
                raise ValueError('invalid cross-family ' + key)
        pairs.append((row['field_id'], grid))
    if len(pairs) != count * len(grids) or set(pairs) != {(fid, grid) for fid in ids for grid in grids}:
        raise ValueError('cross-family missing or duplicate field/grid object')
    if len({r['path'] for r in diag}) != len(diag): raise ValueError('duplicate cross-family object path')
    return [{'field_id': r['field_id'], 'grid': r['grid'], 'path': r['path'],
             'sha256': r['sha256'], 'semantic_digest': r.get('semantic_digest')} for r in diag]


@contextmanager
def k2b_metadata(project, module):
    """Augment just the receipt-frozen K0 stage-3 read, in memory after FINAL."""
    project = Path(project).resolve()
    from .s1_io import ACTIVE_POLICY
    if ACTIVE_POLICY is not None and ACTIVE_POLICY != (str(project), 'K2B'):
        raise PermissionError('K0 metadata compatibility is K2B-only')
    if module.__name__ != 'p13rawxt.s1_k2b_post_search_diagnostics':
        raise PermissionError('compatibility requires the frozen K2B module')
    diagnostic_capability(project, 'K2B')
    target = contained(project, K0 + '/03_k1_open_inputs.json', True)
    receipt = verify_receipt(project, 'K0')
    if not any(r['path'] == target.relative_to(project).as_posix() for r in receipt['outputs']):
        raise ValueError('K0 input file is not receipt-frozen')
    expected = cross_commitment_metadata(project)
    original = module._load_json
    def reading(path):
        obj = original(path)
        if Path(path) != target: return obj
        if obj.get('existing_transfer_field_ids') != sorted({r['field_id'] for r in expected}):
            raise ValueError('K0 field IDs disagree with authoritative manifest')
        if KEY in obj:
            if obj[KEY] != expected: raise ValueError('K0 cross commitment metadata mismatch')
            return obj
        return dict(obj, **{KEY: expected})
    module._load_json = reading
    try: yield
    finally: module._load_json = original
