import json
from pathlib import Path

from p13rawxt import s1_pf1_final_freeze as pf1


def test_pf1_constants_and_s2_roadmap_config():
    root = Path(__file__).resolve().parents[4]
    cfg = json.loads((root / 'phases/p13/coefficient_law_raw_xt/configs/p13_s1_pf1_protocol.json').read_text())
    assert cfg['parent_K3_semantic_digest'] == pf1.EXPECTED_K3
    assert cfg['clear_membership_sha256'] == pf1.EXPECTED_CLEAR_SHA
    assert cfg['unresolved_membership_sha256'] == pf1.EXPECTED_UNRESOLVED_SHA
    assert cfg['clear_count'] == 2307
    assert cfg['unresolved_count'] == 80
    steps = cfg['S2_top_level_roadmap']
    assert len(steps) == 8
    assert steps[0].startswith('S2-K0_')
    assert steps[-1].startswith('S2-K7_')
    assert cfg['response_stage_blocked_at_S2_entry'] is True


def test_s2_final_roadmap_explicitly_separates_operator_and_response():
    root = Path(__file__).resolve().parents[4]
    text = (root / 'phases/p13/coefficient_law_raw_xt/docs/P13_S2_FINAL_ROADMAP_AFTER_PF0_20260831.md').read_text()
    assert 'S2-K0 — DEVELOPMENT coefficient opening' in text
    assert 'S2-K1 — complete zero-shot operator transfer' in text
    assert 'S2-K2 — III-B adjudication' in text
    assert 'S2-K3 — response protocol/fidelity/control lock' in text
    assert 'blocked at S2 entry' in text
    assert 'no top-k/Pareto/score narrowing' in text


def test_pf1_document_preserves_k3_and_pf0_roles():
    root = Path(__file__).resolve().parents[4]
    text = (root / 'phases/p13/coefficient_law_raw_xt/docs/P13_S1_PF1_FINAL_STAGE_FREEZE_AND_S2_HANDOFF.md').read_text()
    assert 'K3 remains the immutable formal-discovery freeze' in text
    assert 'PF0 is a post-membership REFERENCE/DESCRIPTIVE addendum' in text
    assert '2307 clear' in text
    assert '80 TRAIN numerical-boundary' in text
    assert 'K0A/K0B/K0C were substeps' in text


def test_pf1_packager_is_recovery_not_runtime_input():
    root = Path(__file__).resolve().parents[4]
    text = (root / 'phases/p13/coefficient_law_raw_xt/src/p13rawxt/s0_s1_pf1_final_packager.py').read_text()
    assert 'RECOVERY_REVIEW_COPY_NOT_AUTHORITATIVE' in text
    assert 'portable_candidate_snapshot_role' in text
    assert 'DEVELOPMENT and SEALED private payloads' in text
