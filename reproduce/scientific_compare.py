"""Exact, outcome-blind comparison of explicit normalized scientific records.

This API consumes independently produced records and reports differences only.
Legacy-stage output schemas still need explicit adapters before orchestration.
No tolerance, gate evaluation, candidate filtering or automatic normalization.
"""
import math
from .common import digest

FIELDS = {'scientific_branch_id', 'raw_X_AST', 'raw_T_AST', 'theta_hex',
          'deterministic_gauge', 'fit_provenance', 'decision', 'protocol_invariants'}
TRANSPORT_FIELDS = {'execution_id', 'run_relative', 'created_utc', 'elapsed_seconds',
                    'branch_registry_path', 'branch_registry_byte_offset',
                    'skeleton_registry_path', 'skeleton_registry_byte_offset'}

def exact_tree(obj):
    # Tag every JSON type so bool/int equality and user dicts resembling a float
    # encoding cannot accidentally erase scientific differences.
    if obj is None:
        return ['null']
    if isinstance(obj, bool):
        return ['bool', obj]
    if isinstance(obj, int):
        return ['int', str(obj)]
    if isinstance(obj, str):
        return ['str', obj]
    if isinstance(obj, float):
        if not math.isfinite(obj):
            raise ValueError('nonfinite scientific value')
        return ['float64', obj.hex()]
    if isinstance(obj, dict):
        if any(not isinstance(k, str) for k in obj):
            raise ValueError('scientific JSON object requires string keys')
        return ['object', [[k, exact_tree(obj[k])] for k in sorted(obj)]]
    if isinstance(obj, list):
        return ['array', [exact_tree(v) for v in obj]]
    raise ValueError('unsupported scientific value type')

def _index(records):
    out = {}
    for row in records:
        if not FIELDS <= row.keys() or not isinstance(row['scientific_branch_id'], str) or not row['scientific_branch_id']:
            raise ValueError('incomplete scientific record')
        ident = row['scientific_branch_id']
        if ident in out:
            raise ValueError('duplicate scientific identity')
        if not isinstance(row['theta_hex'], list) or any(not isinstance(x, str) for x in row['theta_hex']):
            raise ValueError('theta must retain explicit float hex strings')
        if not isinstance(row['decision'], str) or not row['decision']:
            raise ValueError('explicit decision semantics required')
        execution = row.get('execution', {})
        if not isinstance(execution, dict) or not set(execution) <= TRANSPORT_FIELDS:
            raise ValueError('unrecognized field in transport namespace')
        # All other scientific/metric keys, including unfamiliar ones, remain.
        scientific = {k: v for k, v in row.items() if k != 'execution'}
        out[ident] = {k: exact_tree(v) for k, v in scientific.items()}
    return out

def compare_records(fresh_records, reference_records, fresh_invariants, reference_invariants):
    if not fresh_invariants or not reference_invariants:
        raise ValueError('nonempty protocol invariants required')
    fresh, reference = _index(fresh_records), _index(reference_records)
    missing = sorted(set(reference) - set(fresh))
    added = sorted(set(fresh) - set(reference))
    changed = {key: sorted(k for k in set(fresh[key]) | set(reference[key])
                           if fresh[key].get(k) != reference[key].get(k)
                           or (k in fresh[key]) != (k in reference[key]))
               for key in sorted(set(fresh) & set(reference)) if fresh[key] != reference[key]}
    invariants_match = exact_tree(fresh_invariants) == exact_tree(reference_invariants)
    match = not missing and not added and not changed and invariants_match
    return {'comparison': 'EXACT_MATCH' if match else 'MISMATCH',
            'fresh_scientific_digest': digest(fresh), 'reference_scientific_digest': digest(reference),
            'missing_ids': missing, 'added_ids': added, 'changed_fields': changed,
            'protocol_invariants_match': invariants_match, 'scientific_gate_evaluated': False,
            'candidate_membership_modified': False, 'authorizes_stage_execution': False}
