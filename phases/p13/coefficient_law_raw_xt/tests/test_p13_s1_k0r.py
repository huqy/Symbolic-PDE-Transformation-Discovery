import numpy as np

from p13rawxt.s1_search_primitives import (
    aggregate_family_objective,
    boundary_status,
    continuation_decision,
    empty_v2_counters,
    operator_qualification,
    route_v2_mutation_slot,
    scientific_branch_id,
    execution_equivalence_key,
)

CAPS = {
    "nodes_X_max": 41, "nodes_T_max": 41, "total_nodes_max": 72, "depth_max": 13,
    "unique_theta_total_max": 10, "unique_theta_component_max": 6,
    "theta_occurrences_total_max": 12, "derivative_nesting_max": 2,
}


def _pair_with_theta():
    from p11rawxt_ast import Op, Theta, Var
    from p11rawxt_s1.k1_representation import canonicalize_pair
    return canonicalize_pair(Op("Add", Var("x"), Op("Mul", Theta("theta_8"), Var("t"))), Var("t"), CAPS)


def test_family_objective_is_six_field_rms():
    out = aggregate_family_objective([1, 2, 3, 4, 5, 6])
    assert np.isclose(out["J_family"], np.sqrt(91 / 6))
    assert out["J_max"] == 6


def test_tau_is_ambiguity_not_relaxed_pass():
    assert boundary_status(0.4974, 0.5, 0.005) == "CLEAR_PASS"
    assert boundary_status(0.5, 0.5, 0.005) == "UNRESOLVED_NUMERICAL_BOUNDARY"
    assert boundary_status(0.5026, 0.5, 0.005) == "CLEAR_FAIL"
    q = operator_qualification([0.5] * 6, [1.0] * 6, True, True, 0.005)
    assert q["status"] == "OPERATOR_QUALIFIED_UNRESOLVED"
    assert q["tau_num_role"] == "ambiguity_only"


def test_continuation_uses_train_progress_and_four_seeds_only():
    out = continuation_decision([(1, .85), (1, .89), (1, .90), (1, .88)], False, True)
    assert out["authorized"]
    assert out["median_r_s"] >= 0.10
    assert out["forbidden_inputs_used"] == []
    blocked = continuation_decision([(1, .85), (1, .89), (1, .90), (1, .88)], True, True)
    assert not blocked["authorized"]


def test_scientific_branch_keeps_theta_and_fit_provenance():
    p = _pair_with_theta(); gauge = {"rule": "fixed"}
    a = scientific_branch_id(p, [0.1], gauge, {"launch": "a"})
    b = scientific_branch_id(p, [0.2], gauge, {"launch": "a"})
    c = scientific_branch_id(p, [0.1], gauge, {"launch": "b"})
    assert len({a, b, c}) == 3
    assert execution_equivalence_key(p, [0.1], gauge) == execution_equivalence_key(p, [0.1], gauge)


def test_v2_selected_graft_never_silently_falls_back():
    from p11rawxt_ast import Var
    from p11rawxt_s1.k1_representation import canonicalize_pair
    parent = canonicalize_pair(Var("x"), Var("t"), CAPS)
    total = empty_v2_counters()
    for i in range(64):
        _, counters, _ = route_v2_mutation_slot(parent, np.random.default_rng(i), CAPS, "full_raw_coefficient", 0.5, 9)
        for k, v in counters.items(): total[k] += v
    assert total["silent_fallback_to_V1"] == 0
    assert total["mutation_slots_total"] == 64
    assert total["standard_mutation_selected"] + total["graft_selected"] == 64
    assert total["graft_selected"] == total["graft_attempted"]
