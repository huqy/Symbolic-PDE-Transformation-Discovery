from __future__ import annotations

import json
from pathlib import Path

from p13rawxt.reference_optimizer_v2 import reference_launch_v2, adjudicate_independent_launches


def _fake_eval(pair,theta):
    # Smooth response-blind fake F4 objective with a known optimum.
    val=sum((float(x)-0.25)**2 for x in theta)
    return {
        "stage_index":5,"highest_feasibility_level":"F4","J_princ":val,
        "J_XT":0.5*val,"J_XX":0.75*val,"theta_vector":list(map(float,theta)),
        "direct_margin_vector":[],
    }


def test_reference_v2_common_incumbent_is_not_degraded():
    production={"stage_index":5,"highest_feasibility_level":"F4","J_princ":0.25,"J_XT":0.125,"J_XX":0.1875,"theta_vector":[0.0,0.0],"direct_margin_vector":[]}
    proto={
        "parameter_bounds":[-3.0,3.0],"global_fraction":0.45,"frontier_width":4,
        "local_sobol_per_frontier":4,"coordinate_steps":[1.0,0.5,0.25,0.125]
    }
    out=reference_launch_v2({},_fake_eval,parameter_names=["theta_1","theta_2"],production_best=production,launch_seed=123,call_budget=128,protocol=proto)
    assert out["best"] is not None
    assert float(out["best"]["J_princ"]) <= float(production["J_princ"])
    assert out["completed_calls"] == 128


def test_reference_launch_adjudication_uses_agreement_not_best_only():
    base={"stage_index":5,"J_princ":1.0,"J_XT":0.5,"J_XX":0.5,"theta_vector":[0.0]}
    rows=[{"launch_index":0,"completed_calls":10,"best":base,"F4_count":1},{"launch_index":1,"completed_calls":10,"best":{**base,"J_princ":1.01},"F4_count":1}]
    good=adjudicate_independent_launches(rows,0.02)
    bad=adjudicate_independent_launches(rows,0.005)
    assert good["qualified"] is True
    assert bad["qualified"] is False


def test_k2r2_protocol_keeps_burnin_calibration_only_and_response_blind():
    root=Path(__file__).resolve().parents[1]
    p=json.loads((root/"configs/p13_s0_k2r2_protocol.json").read_text())
    assert p["common_burnin"]["structural_proposal_budget"] == 8192
    assert p["common_burnin"]["formal_S1_seed_export"] is False
    assert p["common_burnin"]["gp"]["residual_graft_used"] is False
    assert p["common_burnin"]["gp"]["characteristic_or_witness_seed"] is False
    assert p["scientific_governance"]["development_or_sealed_open"] is False
    assert p["scientific_governance"]["opened_transfer_diagnostic_read"] is False
    assert p["scientific_governance"]["burnin_candidates_eligible_for_S1"] is False


def test_common_burnin_inherits_generic_p11_mix_exactly():
    root=Path(__file__).resolve().parents[1]
    p=json.loads((root/"configs/p13_s0_k2r2_protocol.json").read_text())
    gp=p["common_burnin"]["gp"]
    assert gp["island_count"] == 8
    assert gp["initial_complete_skeleton_target_per_island"] == 128
    assert gp["offspring_mix"] == {"de_novo":0.2,"subtree_mutation":0.4,"subtree_crossover":0.4}
    assert gp["migration"]["records_per_island"] == 2
    assert gp["migration"]["interval_completed_batches"] == 20


def test_reference_escalation_is_cohort_wide_not_candidate_specific():
    root=Path(__file__).resolve().parents[1]
    p=json.loads((root/"configs/p13_s0_k2r2_protocol.json").read_text())
    ref=p["reference_optimizer_v2"]
    assert ref["candidate_specific_reference_rescue"] is False
    assert "all 128 skeletons" in ref["fitter_cohort_rule"]
    assert "both NULL and FULL" in ref["capacity_cohort_rule"]
    levels=ref["fidelity_ladder"]
    assert [x["level"] for x in levels] == ["R0","R1","R2"]
    for param in range(1,11):
        vals=[int(x["calls_by_parameter_count"][str(param)]) for x in levels]
        assert vals[0] <= vals[1] <= vals[2]


def test_parent_membership_rule_is_chronological_not_quality_ranked():
    root=Path(__file__).resolve().parents[1]
    p=json.loads((root/"configs/p13_s0_k2r2_protocol.json").read_text())
    rule=p["common_burnin"]["parent_membership_rule"].lower()
    assert "chronologically first 64" in rule
    assert "no objective threshold or top-k" in rule
