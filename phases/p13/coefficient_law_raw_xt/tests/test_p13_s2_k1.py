import json
import math
from pathlib import Path

from p13rawxt.s2_k1_operator_transfer import _avg_ranks, _rel_disc, _spearman
from p13rawxt.s1_search_primitives import boundary_status


def test_frozen_boundary_semantics():
    tau=0.005
    assert boundary_status(0.49,0.5,tau)=="CLEAR_PASS"
    assert boundary_status(0.5,0.5,tau)=="UNRESOLVED_NUMERICAL_BOUNDARY"
    assert boundary_status(0.51,0.5,tau)=="CLEAR_FAIL"
    assert boundary_status(0.99,1.0,tau)=="CLEAR_PASS"
    assert boundary_status(1.0,1.0,tau)=="UNRESOLVED_NUMERICAL_BOUNDARY"
    assert boundary_status(1.01,1.0,tau)=="CLEAR_FAIL"


def test_grid_discrepancy_definition():
    assert abs(_rel_disc(1.01,1.0)-0.01)<1e-15
    assert _rel_disc(None,1.0) is None


def test_spearman_no_pvalue_helper():
    assert abs(_spearman([1,2,3,4],[2,4,6,8])-1.0)<1e-15
    assert abs(_spearman([1,2,3,4],[8,6,4,2])+1.0)<1e-15
    assert _spearman([1,1,1],[1,2,3]) is None


def test_protocol_has_no_shortlist_or_response_opening():
    p=Path(__file__).parents[1]/"configs/p13_s2_k1_protocol.json"
    cfg=json.loads(p.read_text())
    assert cfg["candidate_governance"]["complete_2307_required"] is True
    assert cfg["candidate_governance"]["top_k_forbidden"] is True
    assert cfg["candidate_governance"]["pareto_forbidden"] is True
    assert cfg["data_boundary"]["DEVELOPMENT_RESPONSE"]=="SEALED_COMMITTED_UNOPENED"
    assert cfg["response_stage_still_blocked"] is True
    assert cfg["hard_gate_frozen_for_K2"]["K1_assigns_final_PASS_FAIL"] is False
    assert cfg["numerical_fidelity"]["posthoc_unresolved_fraction_trigger_forbidden"] is True
