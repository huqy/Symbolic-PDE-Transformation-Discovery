"""Frozen PF0/PF1 applicability guard; no scientific decision authority."""
from pathlib import Path
from .s0_contract import load, write
from .s1_lineage import seal, unseal, verify_membership, verify_receipt

STATUS = 'BLOCKED_FRESH_CONTINUATION_TRUE_PF0_PROTOCOL_SCOPE_REQUIRES_CONFIRMATION'
EXIT_CODE = 3


class PF0ScopeBlocked(RuntimeError):
    def __init__(self):
        super().__init__(STATUS)


def require_pf0_scope(project):
    """Reject continuation-true before importing/executing the frozen addendum."""
    lock = verify_membership(project)
    authorized = lock['continuation']['authorized']
    if authorized is True:
        raise PF0ScopeBlocked()
    if authorized is not False:
        raise ValueError('invalid immutable continuation decision')


def stop_after_k3(project):
    """Coordinator-only, idempotent stop receipt outside the scientific run store."""
    k3 = verify_receipt(project, 'K3')
    try:
        require_pf0_scope(project)
    except PF0ScopeBlocked:
        lock = verify_membership(project)
        receipt = seal({
            'schema': 'P13_S1_PF0_SCOPE_GUARD_V1',
            'execution_id': lock['execution_id'],
            'status': STATUS,
            'last_completed_stage': 'K3',
            'parent_K3_receipt_digest': k3['digest'],
            'membership_lock_digest': lock['digest'],
            'continuation': lock['continuation'],
            'horizons': lock['horizons'],
            'formal_S1_TRAIN_through_K3_frozen': True,
            'PF0_PF1_authorized': False,
            'S2_opening_authorized_by_guard': False,
            'next_action': 'CONFIRM_PF0_PF1_SCOPE_FOR_CONTINUATION_TRUE_LINEAGE',
            'reason': 'Formal S1 TRAIN evidence through K3 is frozen; the historical '
                      'PF0/PF1 addendum does not authorize a continuation-true fresh lineage.'})
        path = Path(project).parent / 'pf0_scope_guard.json'
        if path.exists():
            if unseal(load(path)) != receipt:
                raise ValueError('refuse scope receipt overwrite')
        else:
            write(path, receipt)
        print(STATUS + "; Formal S1 TRAIN evidence is frozen through K3; PF0/PF1 does not authorize this continuation lineage. Preserve evidence and stop.", flush=True)
        return receipt
    return None
