from __future__ import annotations

import json
from pathlib import Path

from p13rawxt.k2r4_operational_fitter import (
    adjudicate_ratio_summary,
    chronological_unique_f4_membership,
)


def _root() -> Path:
    return Path(__file__).resolve().parents[4]


def _protocol() -> dict:
    return json.loads((_root() / "phases/p13/coefficient_law_raw_xt/configs/p13_s0_k2r4_protocol.json").read_text())


def test_protocol_keeps_scientific_gates_and_boundaries():
    p = _protocol()
    assert p["operational_burnin"]["structural_proposal_budget"] == 8192
    assert p["operational_burnin"]["parent_count"] == 64
    assert p["operational_burnin"]["operational_fitter_cohort_max"] == 128
    assert p["operational_fitter"]["minimum_R_fit_resolved"] == 64
    assert p["operational_fitter"]["median_R_fit_max"] == 1.25
    assert p["operational_fitter"]["p90_R_fit_max"] == 3.0
    gov = p["scientific_governance"]
    assert gov["objective_changed"] is False
    assert gov["grammar_changed"] is False
    assert gov["F4_hard_gate_changed"] is False
    assert gov["development_or_sealed_open"] is False
    assert gov["opened_transfer_diagnostic_read"] is False
    assert gov["objective_top_k_or_percentile_membership"] is False
    assert gov["burnin_candidates_eligible_for_S1"] is False
    assert gov["formal_S1_search_authorized"] is False


def test_operational_membership_is_F4_chronology_not_J_rank():
    rows = [
        {"task_index": 4, "pair": {"structural_hash": "d"}, "best_f4": {"J_princ": 1e-9}},
        {"task_index": 1, "pair": {"structural_hash": "a"}, "best_f4": {"J_princ": 10.0}},
        {"task_index": 2, "pair": {"structural_hash": "b"}, "best_f4": None},
        {"task_index": 3, "pair": {"structural_hash": "c"}, "best_f4": {"J_princ": 9.0}},
    ]
    got = chronological_unique_f4_membership(rows, 2)
    assert [r["pair"]["structural_hash"] for r in got] == ["a", "c"]


def test_operational_membership_deduplicates_before_counting():
    rows = [
        {"task_index": 1, "pair": {"structural_hash": "a"}, "best_f4": {"J_princ": 4.0}},
        {"task_index": 2, "pair": {"structural_hash": "a"}, "best_f4": {"J_princ": 1.0}},
        {"task_index": 3, "pair": {"structural_hash": "b"}, "best_f4": {"J_princ": 5.0}},
    ]
    got = chronological_unique_f4_membership(rows, 2)
    assert [r["task_index"] for r in got] == [1, 3]


def test_ratio_gate_preserves_original_thresholds():
    ratios = [1.1] * 64
    out = adjudicate_ratio_summary(ratios, 64, 1.25, 3.0)
    assert out["status"] == "PASS"
    assert out["median_R_fit"] == 1.1


def test_ratio_gate_fails_if_fewer_than_64_resolved():
    out = adjudicate_ratio_summary([1.0] * 63, 64, 1.25, 3.0)
    assert out["status"] == "FAIL"
    assert out["failure"] == "PRODUCTION_FITTER_F4_ATTAINMENT_NOT_QUALIFIED"


def test_ratio_gate_distinguishes_regret_failure():
    ratios = [1.0] * 32 + [4.0] * 32
    out = adjudicate_ratio_summary(ratios, 64, 1.25, 3.0)
    assert out["status"] == "FAIL"
    assert out["failure"] == "PRODUCTION_FITTER_REGRET_NOT_QUALIFIED"


def test_parent_mechanism_lock_records_zero_F4_naked_cohort():
    p = _protocol()
    m = p["mechanism_lock"]
    assert m["expected_naked_fitter_object_count"] == 128
    assert m["expected_production_F4_count"] == 0
    assert m["expected_both_reference_launches_no_F4_per_level"] == 128
    assert m["formal_K2R3_status_unchanged"] is True
