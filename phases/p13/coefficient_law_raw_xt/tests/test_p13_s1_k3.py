from __future__ import annotations

import json
from pathlib import Path

from p13rawxt.s1_k3_formal_freeze import _find_s0_k2r3_null_control, _semantic_digest


def test_k3_semantic_digest_is_order_stable():
    a = [{"b": 2, "a": 1}, {"x": [3, 4]}]
    b = [{"a": 1, "b": 2}, {"x": [3, 4]}]
    assert _semantic_digest(a) == _semantic_digest(b)


def test_k3_protocol_is_freeze_only():
    p = Path(__file__).parents[1] / "configs/p13_s1_k3_protocol.json"
    c = json.loads(p.read_text())
    assert c["K3_may_open_DEVELOPMENT"] is False
    assert c["K3_may_open_SEALED"] is False
    assert c["K3_may_run_new_search"] is False
    assert c["expected_clear_count"] == 2307
    assert c["expected_unresolved_count"] == 80
    assert "top-k" in c["forbidden_narrowing"]
    assert c["next_action_on_pass"] == "P13-S2-K0_DEVELOPMENT_ZERO_SHOT_OPERATOR_TRANSFER"


def test_k3_s2_membership_rule_is_train_only():
    p = Path(__file__).parents[1] / "configs/p13_s1_k3_protocol.json"
    c = json.loads(p.read_text())
    text = c["s2_candidate_rule"]
    assert "clear OPERATOR_QUALIFIED_TRAIN" in text
    assert "exact equivalence" in text
    assert "diagnostic" not in text.lower()



def test_k3_reads_actual_frozen_k2r3_null_control_schema(tmp_path):
    root = tmp_path
    k2r4_rel = "phases/p13/coefficient_law_raw_xt/runs/k2r4"
    k2r4 = root / k2r4_rel
    k2r4.mkdir(parents=True)
    source_rel = "phases/p13/coefficient_law_raw_xt/runs/k2r3"
    source = root / source_rel
    (source / "calibration_only").mkdir(parents=True)
    (root / "phases/p13/coefficient_law_raw_xt/src/p13rawxt").mkdir(parents=True, exist_ok=True)
    (root / "phases/p13/coefficient_law_raw_xt/src/p13rawxt/calibration_instruments.py").write_text("# frozen builder\n")
    (k2r4 / "capacity_reuse_lock.json").write_text(json.dumps({"source_run": source_rel}))
    lock = {
        "role": "CALIBRATION_ONLY",
        "forbidden_from_search": True,
        "instrument_hashes": {"null_capacity": "null-structural-hash", "full_capacity": "full-hash"},
        "theta": {"null_capacity": [0.1, -0.2], "full_capacity": [0.3]},
    }
    (source / "calibration_only/capacity_instrument_lock_k2r3.json").write_text(json.dumps(lock))

    control = _find_s0_k2r3_null_control(root, k2r4_rel)
    assert control["source_role"] == "CALIBRATION_ONLY"
    assert control["forbidden_from_search"] is True
    assert control["structural_hash"] == "null-structural-hash"
    assert control["theta"] == [0.1, -0.2]
    assert control["candidate_membership_authority"] == "NONE"
    assert control["response_control_only"] is True
    assert control["pair_builder"].endswith("build_null_capacity_pair")


def test_k3_rejects_non_calibration_null_control_schema(tmp_path):
    root = tmp_path
    k2r4_rel = "phases/p13/coefficient_law_raw_xt/runs/k2r4"
    k2r4 = root / k2r4_rel
    k2r4.mkdir(parents=True)
    source_rel = "phases/p13/coefficient_law_raw_xt/runs/k2r3"
    source = root / source_rel
    (source / "calibration_only").mkdir(parents=True)
    (root / "phases/p13/coefficient_law_raw_xt/src/p13rawxt").mkdir(parents=True, exist_ok=True)
    (root / "phases/p13/coefficient_law_raw_xt/src/p13rawxt/calibration_instruments.py").write_text("# frozen builder\n")
    (k2r4 / "capacity_reuse_lock.json").write_text(json.dumps({"source_run": source_rel}))
    bad = {
        "role": "SEARCH_INPUT",
        "forbidden_from_search": False,
        "instrument_hashes": {"null_capacity": "h"},
        "theta": {"null_capacity": []},
    }
    (source / "calibration_only/capacity_instrument_lock_k2r3.json").write_text(json.dumps(bad))
    import pytest
    with pytest.raises(RuntimeError):
        _find_s0_k2r3_null_control(root, k2r4_rel)
