from p13rawxt.s2_k3_response_protocol_lock import (
    candidate_fidelity_action,
    classify_branch,
    classify_pair,
    control_first_action,
    reference_refinement_action,
    response_interval,
)


def test_interval_and_strict_response_semantics():
    iv = response_interval(0.10, 0.11, 0.01)
    assert abs(iv["radius"] - 0.02) < 1e-15
    assert classify_pair(0.10, 0.11, 0.01) == "CLEAR_PASS"
    assert classify_pair(0.17, 0.18, 0.01) == "CLEAR_FAIL"
    assert classify_pair(0.14, 0.15, 0.01) == "UNRESOLVED"
    pass32 = [{"lower": 0.08, "upper": 0.14}] * 32
    fail32 = pass32[:-1] + [{"lower": 0.151, "upper": 0.17}]
    unresolved32 = pass32[:-1] + [{"lower": 0.14, "upper": 0.16}]
    assert classify_branch(pass32) == "RESPONSE_PASS"
    assert classify_branch(fail32) == "RESPONSE_FAIL"
    assert classify_branch(unresolved32) == "RESPONSE_UNRESOLVED"


def test_reference_refinement_is_own_convergence_only_ladder():
    assert reference_refinement_action(0.019, (513, 1025)) == "REFERENCE_CERTIFIED"
    assert reference_refinement_action(0.021, (513, 1025)) == "REFINE_TO_G2049"
    assert reference_refinement_action(0.021, (1025, 2049)) == "REFINE_TO_G4097"
    assert reference_refinement_action(0.021, (2049, 4097)) == "REFERENCE_UNRESOLVED_AFTER_G2049_G4097"


def test_candidate_escalation_is_complete_cohort_not_candidate_rescue():
    assert candidate_fidelity_action(True, 257) == "ESCALATE_ALL_1955_PLUS_CONTROLS_TO_G513"
    assert candidate_fidelity_action(True, 513) == "ESCALATE_ALL_1955_PLUS_CONTROLS_TO_G1025"
    assert candidate_fidelity_action(True, 1025) == "PRESERVE_REMAINING_AS_UNRESOLVED"
    assert candidate_fidelity_action(False, 257) == "FREEZE_CURRENT_DECISIONS"


def test_control_first_nondiscrimination_stops_before_candidate_response():
    assert control_first_action("RESPONSE_PASS", 257) == "NONDISCRIMINATIVE_ABSOLUTE_GATE_STOP_BEFORE_K5"
    assert control_first_action("RESPONSE_FAIL", 257) == "DISCRIMINATIVE_ABSOLUTE_GATE_ALLOW_K4_COST_PREFLIGHT"
    assert control_first_action("RESPONSE_UNRESOLVED", 257) == "ESCALATE_IDENTITY_AND_NULL_TO_G513"
    assert control_first_action("RESPONSE_UNRESOLVED", 513) == "ESCALATE_IDENTITY_AND_NULL_TO_G1025"
    assert control_first_action("RESPONSE_UNRESOLVED", 1025) == "CONTROL_GATE_UNRESOLVED_STOP_BEFORE_K5"
