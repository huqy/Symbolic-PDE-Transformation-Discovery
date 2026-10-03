from __future__ import annotations

import json
from pathlib import Path

from p13rawxt.k2r3_capacity_continuation import adjudicate_capacity_evidence_from_records


def _protocol():
    root = Path(__file__).resolve().parents[1]
    return json.loads((root / "configs/p13_s0_k2r3_protocol.json").read_text())


def test_capacity_existence_is_one_sided_and_does_not_require_full_optimum():
    out = adjudicate_capacity_evidence_from_records(
        j_identity=0.09, j_null=0.089, null_qualified=True, j_full_existence=0.02,
        full_improvement_min=3.0, null_improvement_max=2.0, null_over_full_min=1.67,
    )
    assert out["status"] == "PASS"
    assert out["full_capacity_claim"] == "ONE_SIDED_EXISTENCE"
    assert out["full_optimum_reference_required_for_capacity_gate"] is False


def test_capacity_gate_still_fails_if_null_reference_is_not_qualified():
    out = adjudicate_capacity_evidence_from_records(
        j_identity=0.09, j_null=0.089, null_qualified=False, j_full_existence=0.001,
        full_improvement_min=3.0, null_improvement_max=2.0, null_over_full_min=1.67,
    )
    assert out["status"] == "FAIL"
    assert out["gates"]["null_reference_qualified"] is False


def test_capacity_thresholds_are_unchanged_from_approved_k2r2_design():
    p = _protocol()
    c = p["capacity_evidence_semantics"]
    assert c["full_identity_improvement_min"] == 3.0
    assert c["null_identity_improvement_max"] == 2.0
    assert c["null_over_full_score_min"] == 1.67
    assert c["thresholds_changed"] is False


def test_full_optimum_polish_is_nonblocking_and_symmetric():
    p = _protocol()["full_optimum_polish"]
    assert p["role"] == "NONBLOCKING_CALIBRATION_DIAGNOSTIC"
    assert p["same_protocol_for_both_endpoints"] is True
    assert p["candidate_specific_budget"] is False
    assert p["relative_agreement_max"] == 0.02
    assert p["max_function_evaluations_per_endpoint"] == 2048
    assert "UNRESOLVED" in p["if_agreement_fails"]


def test_fitter_continuation_reuses_frozen_membership_and_cohort_wide_reference_ladder():
    f = _protocol()["fitter_continuation"]
    assert f["reuse_exact_k2r2_reference_optimizer_v2"] is True
    assert f["reuse_exact_frozen_k2_128_skeleton_membership"] is True
    assert f["reuse_exact_k2_production_fitter_results"] is True
    assert f["minimum_reference_qualified"] == 64
    assert f["median_R_fit_max"] == 1.25
    assert f["p90_R_fit_max"] == 3.0
    assert f["cohort_wide_escalation"] is True
    assert f["candidate_specific_reference_rescue"] is False


def test_governance_keeps_search_and_holdouts_untouched():
    g = _protocol()["scientific_governance"]
    assert g["objective_changed"] is False
    assert g["grammar_changed"] is False
    assert g["candidate_membership_changed"] is False
    assert g["response_threshold_changed"] is False
    assert g["data_boundary_changed"] is False
    assert g["large_search_budget_changed"] is False
    assert g["development_or_sealed_open"] is False
    assert g["opened_transfer_diagnostic_read"] is False
    assert g["capacity_witness_search_use"] is False
    assert g["characteristic_formula_search_use"] is False
    assert g["formal_S1_search_authorized"] is False


def test_powell_polish_worker_improves_a_smooth_fake_f4_objective():
    import p13rawxt.k2r3_capacity_continuation as mod
    def fake_eval(pair, theta):
        j = sum((float(x) - 0.2) ** 2 for x in theta)
        return {"stage_index": 5, "highest_feasibility_level": "F4", "J_princ": j, "theta_vector": list(map(float, theta))}
    mod._POLISH = {
        "evaluator": fake_eval,
        "pair": {},
        "cfg": {
            "parameter_bounds": [-3.0, 3.0], "infeasible_objective": 1e6,
            "max_function_evaluations_per_endpoint": 128, "xtol": 1e-6, "ftol": 1e-8,
        },
    }
    out = mod._polish_worker({"index": 0, "endpoint_index": 0, "theta0": [1.0, -1.0]})
    assert out["best"] is not None
    assert float(out["best"]["J_princ"]) < 1e-6
    assert out["completed_calls"] <= 128
