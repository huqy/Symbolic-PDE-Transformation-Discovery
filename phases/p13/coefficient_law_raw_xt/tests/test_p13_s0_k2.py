from __future__ import annotations

import inspect
import json
from pathlib import Path

import numpy as np


def test_m2_runtime_uses_order4_support_exactly():
    from p11rawxt_ast import Var, Op
    from p13rawxt.coefficients import exponential_coefficient_derivatives
    from p13rawxt.ast_runtime import evaluate_ast_jet
    generator={
        "epsilon":0.2,
        "expanded_plane_wave_components":[{"amplitude":0.7,"kx":0.3,"kt":-0.4,"phase":0.2},{"amplitude":-0.2,"kx":-0.15,"kt":0.25,"phase":1.1}],
    }
    x1=np.linspace(0,1,9); t1=np.linspace(0,1,9); x,t=np.meshgrid(x1,t1,indexing="ij")
    d=exponential_coefficient_derivatives(generator,x,t,4)
    node=Op("Dx",Op("Dt",Var("a")))
    jet=evaluate_ast_jet(node,x,t,{},d,4)
    assert np.max(np.abs(jet.coeff[(0,0)]-d[(1,1)])) < 1e-13
    assert np.max(np.abs(jet.coeff[(2,0)]*2.0-d[(3,1)])) < 1e-13
    assert np.max(np.abs(jet.coeff[(1,1)]-d[(2,2)])) < 1e-13


def test_proposal_kernel_has_no_capacity_or_characteristic_dependency():
    import p13rawxt.proposal_geometry as pg
    source=inspect.getsource(pg)
    assert "build_full_capacity" not in source
    assert "characteristic" not in source.lower()
    assert "response" not in source.lower()
    assert "generator_family" not in source


def test_residual_graft_rejects_theta_saturated_parent():
    from p11rawxt_ast import Var,Theta,Op
    from p11rawxt_s1.k1_representation import canonicalize_pair
    from p13rawxt.proposal_geometry import residual_graft
    caps={"nodes_X_max":41,"nodes_T_max":41,"total_nodes_max":72,"depth_max":13,"unique_theta_total_max":10,"unique_theta_component_max":6,"theta_occurrences_total_max":12,"derivative_nesting_max":2}
    # 10 unique parameters spread across components but within component caps.
    X=Op("Add",Var("x"),*[Op("Mul",Theta(f"theta_{i}"),Var("t")) for i in range(1,6)])
    T=Op("Add",Var("t"),*[Op("Mul",Theta(f"theta_{i}"),Var("x")) for i in range(6,11)])
    parent=canonicalize_pair(X,T,caps)
    rng=np.random.default_rng(1)
    try:
        residual_graft(parent,rng,caps,9)
    except ValueError as exc:
        assert "THETA_SATURATED" in str(exc)
    else:
        raise AssertionError("saturated parent must reject theta_new residual graft")


def test_causal_zero_forcing_zero_error():
    from p13rawxt.causal_calibration import causal_solve
    n=17; x=np.linspace(0,1,n); t=np.linspace(0,1,n); shape=(n,n-1)
    sc={"A_tt":np.ones(shape),"A_xx":-np.ones(shape),"A_xt":np.zeros(shape),"b_x":np.zeros(shape),"b_t":np.zeros(shape),"c":np.ones(shape)}
    E,P,cert=causal_solve(x,t,sc,np.zeros(shape),{"source_A_tt_positive_floor":1e-10,"time_step_linear_residual_relative_tolerance":1e-9})
    assert np.max(np.abs(E)) == 0.0
    assert np.max(np.abs(P)) == 0.0
    assert cert["max_step_linear_residual_relative"] == 0.0


def test_protocol_forbids_search_and_holdout_opening():
    here=Path(__file__).resolve()
    root=here.parents[1]
    p=json.loads((root/"configs/p13_s0_k2_protocol.json").read_text())
    gov=p["scientific_governance"]
    assert gov["formal_candidate_search"] is False
    assert gov["development_or_sealed_open"] is False
    assert gov["opened_transfer_diagnostic_read"] is False
    assert gov["top_k_or_percentile_membership"] is False
    assert gov["candidate_specific_rescue"] is False
    assert p["proposal_geometry"]["proposal_attempts"] == 8192
    assert p["fitter"]["fresh_prior_skeleton_count"] == 128


def test_parallel_capacity_launch_reconstruction_matches_sequential_reference_semantics():
    from p11rawxt_ast import Var, Theta, Op
    from p11rawxt_s1.k1_representation import canonicalize_pair
    from p13rawxt.calibration_instruments import reference_fit, reference_launch
    from p13rawxt.k2_qualification import _capacity_reference_from_launch_rows

    caps={"nodes_X_max":41,"nodes_T_max":41,"total_nodes_max":72,"depth_max":13,
          "unique_theta_total_max":10,"unique_theta_component_max":6,
          "theta_occurrences_total_max":12,"derivative_nesting_max":2}
    pair=canonicalize_pair(Op("Add",Var("x"),Op("Mul",Theta("theta_1"),Var("t"))),Var("t"),caps)
    proto={"parameter_bounds":[-1.0,1.0],"independent_launches":2,"sobol_base":4,"sobol_per_parameter":2,
           "sobol_refresh":2,"frontier_width":2,"coordinate_steps":[0.5,0.25],
           "launch_relative_agreement_max":1.0,"calls_by_parameter_count":{"1":12}}

    def evaluate(_pair,theta):
        j=(float(theta[0])-0.125)**2+0.01
        return {"stage_index":5,"highest_feasibility_level":"F4","theta_vector":[float(theta[0])],
                "direct_margin_vector":[1.0],"J_princ":j,"J_XT":j+0.1,"J_XX":j+0.2}

    base_seed=12345
    seq=reference_fit(pair,evaluate,proto,base_seed)
    rows=[]
    for li in range(2):
        seed=base_seed if li==0 else (base_seed+li*0x85EBCA6B)&0xffffffff
        r=reference_launch(pair,evaluate,proto,seed)
        rows.append({"launch_index":li,"completed_calls":r["completed_calls"],"best":r["best"]})
    par=_capacity_reference_from_launch_rows(rows,proto)
    assert par["qualified"] == seq["qualified"]
    assert par["launch_relative_agreement"] == seq["launch_relative_agreement"]
    assert par["best"]["J_princ"] == seq["best"]["J_princ"]
    assert par["best"]["theta_vector"] == seq["best"]["theta_vector"]
    assert [x["completed_calls"] for x in par["launches"]] == [x["completed_calls"] for x in seq["launches"]]


def test_k2r_parent_pool_selection_is_attempt_order_not_objective_order():
    from p13rawxt.k2_qualification import _parent_pool_from_census
    protocol={"proposal_geometry":{"parent_count":3,"proposal_attempts":8}}
    rows=[]
    for i in range(8):
        f4=i in {1,3,6,7}
        row={"index":i,"attempt":i+1,"F4":f4,"highest_feasibility_level":"F4" if f4 else "F1",
             "warning_counts":{},"attempt_status":"F4" if f4 else "NO_F4"}
        if f4:
            # Deliberately make later attempts have better J so selection-by-J would differ.
            row["structural_hash"]=f"hash-{i}"
            row["parent_candidate"]={"pair":{"structural_hash":f"hash-{i}"},"theta_vector":[float(i)],
                                     "J_parent":1.0/(i+1),"parameter_count":1}
        rows.append(row)
    out=_parent_pool_from_census(rows,protocol)
    assert out["F4_count_total"] == 4
    assert [r["source_attempt"] for r in out["rows"]] == [2,4,7]
    assert [r["J_parent"] for r in out["rows"]] == [0.5,0.25,1.0/7.0]
    assert out["selection"].startswith("first 64 production-F4-valid")


def test_k2r_parent_attainment_failure_semantics_are_not_proposal_failure():
    import inspect
    from p13rawxt import k2_qualification as kq
    source=inspect.getsource(kq.proposal_gate)
    assert "RAW_PRIOR_F4_PARENT_ATTAINMENT_FAIL" in source
    assert '"residual_graft_adjudication":"NOT_ADJUDICATED"' in source
    main_source=inspect.getsource(kq.main)
    assert "P13-S0-K2R_PARENT_SOURCE_SCIENTIFIC_DECISION_REQUIRED" in main_source
    assert "PROPOSAL_GEOMETRY_NOT_QUALIFIED" in main_source
