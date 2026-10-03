import json
from pathlib import Path

from p13rawxt.s2_k2_adjudication import _component_consensus, _final_decision


def test_component_consensus_uses_no_new_threshold():
    assert _component_consensus("CLEAR_PASS", "CLEAR_PASS") == "CLEAR_PASS"
    assert _component_consensus("CLEAR_FAIL", "CLEAR_FAIL") == "CLEAR_FAIL"
    assert _component_consensus("CLEAR_PASS", "UNRESOLVED_NUMERICAL_BOUNDARY").startswith("UNRESOLVED")
    assert _component_consensus("CLEAR_PASS", "CLEAR_FAIL").startswith("UNRESOLVED")


def test_final_pass_requires_all_five_clear_pass():
    d, _ = _final_decision(["CLEAR_PASS"] * 5)
    assert d == "OPERATOR_TRANSFER_PASS"
    d, _ = _final_decision(["CLEAR_PASS"] * 4 + ["UNRESOLVED_NUMERICAL_BOUNDARY_OR_GRID_DISAGREEMENT"])
    assert d == "OPERATOR_TRANSFER_UNRESOLVED"


def test_robust_hard_fail_is_sufficient_even_with_other_unresolved_component():
    d, _ = _final_decision(["CLEAR_FAIL", "UNRESOLVED_NUMERICAL_BOUNDARY_OR_GRID_DISAGREEMENT", "CLEAR_PASS", "CLEAR_PASS", "CLEAR_PASS"])
    assert d == "OPERATOR_TRANSFER_FAIL"
    d, _ = _final_decision(["CLEAR_PASS"] * 5, valid33=False)
    assert d == "OPERATOR_TRANSFER_FAIL"


def test_protocol_preserves_response_block_and_no_proxy_narrowing():
    p = Path(__file__).parents[1] / "configs/p13_s2_k2_protocol.json"
    cfg = json.loads(p.read_text())
    assert cfg["k1_fidelity_review_lock"]["new_discrepancy_threshold"] is None
    assert cfg["k1_fidelity_review_lock"]["candidate_specific_rescue"] is False
    assert cfg["adjudication"]["new_numeric_thresholds"] == []
    assert cfg["adjudication"]["top_k_after_PASS_forbidden"] is True
    assert cfg["adjudication"]["target_survivor_count_forbidden"] is True
    assert cfg["data_boundary"]["DEVELOPMENT_RESPONSE"] == "SEALED_COMMITTED_UNOPENED"
    assert cfg["data_boundary"]["response_stage_blocked_after_K2"] is True
