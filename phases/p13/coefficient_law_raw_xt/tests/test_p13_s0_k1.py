from __future__ import annotations
import json, math
from pathlib import Path
import numpy as np

from p13rawxt.coefficients import (
    deterministic_seed, generate_generator, spectral_certificate,
    make_search_object, exponential_coefficient_derivatives,
    b_derivatives, identifiability_report,
)


def protocol():
    here = Path(__file__).resolve()
    p = here.parents[1] / "configs" / "p13_s0_k1_protocol.json"
    return json.loads(p.read_text())


def test_deterministic_open_generator_and_regime():
    p = protocol(); seed = deterministic_seed(p["open_seed_namespace"], "TRAIN_OPERATOR", 1)
    g1 = generate_generator("TRAIN_OPERATOR", 1, seed, p)
    g2 = generate_generator("TRAIN_OPERATOR", 1, seed, p)
    assert g1 == g2
    cert = spectral_certificate(g1, p)
    assert cert["status"] == "PASS"
    assert cert["M_l1"] <= 1.0 and cert["chi_exact"] <= 1.0
    assert cert["zero_frequency_component_count"] == 0


def test_exponential_derivative_first_second_chain_rule():
    p = protocol(); seed = deterministic_seed(p["open_seed_namespace"], "TRAIN_OPERATOR", 2)
    g = generate_generator("TRAIN_OPERATOR", 2, seed, p)
    x1 = np.linspace(0, 1, 9); t1 = np.linspace(0, 1, 9); x,t=np.meshgrid(x1,t1,indexing="ij")
    bd = b_derivatives(g,x,t,2); ad = exponential_coefficient_derivatives(g,x,t,2); eps=g["epsilon"]
    a=ad[(0,0)]
    assert np.allclose(ad[(1,0)], eps*bd[(1,0)]*a, rtol=2e-13, atol=2e-13)
    assert np.allclose(ad[(0,1)], eps*bd[(0,1)]*a, rtol=2e-13, atol=2e-13)
    assert np.allclose(ad[(2,0)], (eps*bd[(2,0)]+eps**2*bd[(1,0)]**2)*a, rtol=2e-13, atol=2e-13)
    assert np.allclose(ad[(1,1)], (eps*bd[(1,1)]+eps**2*bd[(1,0)]*bd[(0,1)])*a, rtol=2e-13, atol=2e-13)


def test_search_object_contains_only_coefficient_jets_and_q():
    p=protocol(); g=generate_generator("CALIBRATION_COEF",1,deterministic_seed(p["open_seed_namespace"],"CALIBRATION_COEF",1),p)
    obj=make_search_object(g,17,4)
    assert "x" in obj and "t" in obj and "q" in obj and "a_d0_0" in obj and "a_d4_0" in obj
    assert not any(k in obj for k in ("epsilon","chi","kx","kt","phase","amplitude","family"))
    assert np.min(obj["a_d0_0"]) > 0.0


def test_formal_train_family_passes_frozen_identifiability_gate():
    p=protocol(); arrays=[]
    for idx in range(1,p["role_counts"]["TRAIN_OPERATOR"]+1):
        g=generate_generator("TRAIN_OPERATOR",idx,deterministic_seed(p["open_seed_namespace"],"TRAIN_OPERATOR",idx),p)
        arrays.append(make_search_object(g,33,4))
    rep=identifiability_report(arrays,p)
    assert rep["status"] == "PASS", rep
    assert rep["numerical_rank"] == 6
    assert rep["sigma_min_over_sigma_max"] >= 1e-4
    assert min(rep["relative_residuals"].values()) >= 0.05


def test_role_generator_families_are_structurally_separated():
    p=protocol(); fam=p["generator_families"]
    assert fam["DEVELOPMENT_COEF"] != fam["TRAIN_OPERATOR"]
    assert fam["SEALED_FINAL_COEF"] != fam["TRAIN_OPERATOR"]
    assert p["response_contract"]["pairs_per_formal_stage"] == 32
    assert p["response_contract"]["relative_error_threshold"] == 0.15
