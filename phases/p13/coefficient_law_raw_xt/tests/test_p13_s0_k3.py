from p13rawxt.k3_s0_freeze import adjudicate_s0


def _all():
    return {
        "regime_identifiability": "PASS",
        "L3_m2_semantics": "PASS",
        "gauge_equivalence": "PASS",
        "capacity_null_separation": "PASS",
        "operational_fitter": "PASS",
        "proposal_geometry": "PASS",
        "evaluator_fidelity": "PASS",
        "causal_response_calibration": "PASS",
    }


def test_all_hard_gates_authorize_only_s1_k0():
    out = adjudicate_s0(_all(), True, True)
    assert out["status"] == "PASS"
    assert out["S1_K0_authorized"] is True
    assert out["S1_K1_formal_search_authorized"] is False
    assert out["failures"] == []


def test_fitter_failure_blocks_s1():
    g = _all(); g["operational_fitter"] = "FAIL"
    out = adjudicate_s0(g, True, True)
    assert out["status"] == "FAIL"
    assert "operational_fitter" in out["failures"]
    assert not out["S1_K0_authorized"]


def test_leakage_failure_blocks_s1():
    out = adjudicate_s0(_all(), False, True)
    assert out["status"] == "FAIL"
    assert "no_leakage" in out["failures"]


def test_capacity_reference_unresolved_blocks_freeze():
    out = adjudicate_s0(_all(), True, False)
    assert out["status"] == "FAIL"
    assert "capacity_reference" in out["failures"]


def test_causal_calibration_is_required_by_s0_protocol():
    g = _all(); g["causal_response_calibration"] = "FAIL"
    out = adjudicate_s0(g, True, True)
    assert out["status"] == "FAIL"
    assert "causal_response_calibration" in out["failures"]
