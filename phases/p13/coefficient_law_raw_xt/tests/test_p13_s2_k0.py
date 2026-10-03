import hashlib
import io
import json
import tarfile
from pathlib import Path

import numpy as np

from p13rawxt.coefficients import canonical_json_bytes, search_object_semantic_digest, sha256_bytes, write_npz_exact
from p13rawxt.s2_k0_dev_open_protocol_lock import inspect_and_open_development_coefficient_archive
from p13rawxt.s1_search_primitives import boundary_status


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_frozen_membership_and_tau_protocol():
    root = Path(__file__).resolve().parents[4]
    cfg = json.loads((root / "phases/p13/coefficient_law_raw_xt/configs/p13_s2_k0_protocol.json").read_text())
    assert cfg["membership_lock"]["clear"]["count"] == 2307
    assert cfg["membership_lock"]["unresolved"]["count"] == 80
    assert cfg["membership_lock"]["exact_equivalence_role"] == "execution_sharing_only"
    assert cfg["operator_transfer_protocol"]["tau_num"] == 0.005
    assert cfg["operator_transfer_protocol"]["tau_num_role"] == "ambiguity_only"
    assert boundary_status(0.4974, 0.5, 0.005) == "CLEAR_PASS"
    assert boundary_status(0.5, 0.5, 0.005) == "UNRESOLVED_NUMERICAL_BOUNDARY"
    assert boundary_status(0.5026, 0.5, 0.005) == "CLEAR_FAIL"
    assert cfg["response_stage"]["blocked"] is True
    assert cfg["data_boundary"]["response_archive_stat_hash_or_extract_in_K0"] is False
    assert cfg["data_boundary"]["sealed_archive_stat_hash_or_extract_in_K0"] is False


def test_private_coeff_open_extracts_search_objects_only(tmp_path: Path):
    staging = tmp_path / "staging"
    (staging / "search_objects").mkdir(parents=True)
    (staging / "generators").mkdir(parents=True)
    arrays = {
        "x": np.linspace(0.0, 1.0, 3),
        "t": np.linspace(0.0, 1.0, 3),
        "q": np.ones((3, 3)),
        "a_d0_0": np.ones((3, 3)),
    }
    npz = staging / "search_objects/P13_DEVELOPMENT_COEF_01_G33.npz"
    write_npz_exact(npz, arrays)
    sem = search_object_semantic_digest(arrays)
    obj = {"grid": 33, "member_path": "search_objects/P13_DEVELOPMENT_COEF_01_G33.npz", "sha256": _sha(npz), "semantic_digest": sem}
    payload = {
        "schema": "P13_PRIVATE_COEFFICIENT_PAYLOAD_V1",
        "role": "DEVELOPMENT_COEF",
        "rows": [{
            "field_id": "P13_DEVELOPMENT_COEF_01",
            "role": "DEVELOPMENT_COEF",
            "family": "fixture",
            "generator_member_path": "generators/P13_DEVELOPMENT_COEF_01.json",
            "generator_semantic_digest": "fixture",
            "regime_certificate": {"status": "PASS"},
            "search_objects": [obj],
        }],
        "master_seed_hex": "SECRET_MUST_NOT_PERSIST",
    }
    (staging / "payload_manifest.json").write_text(json.dumps(payload))
    (staging / "generators/P13_DEVELOPMENT_COEF_01.json").write_text(json.dumps({"secret": "PRIVATE_GENERATOR"}))
    archive = tmp_path / "dev.tar.xz"
    with tarfile.open(archive, "w:xz") as tf:
        for p in sorted(staging.rglob("*")):
            tf.add(p, arcname=p.relative_to(staging).as_posix())
    semantic_obj = {k: v for k, v in payload.items() if k != "master_seed_hex"}
    commitment = {
        "archive_bytes": archive.stat().st_size,
        "archive_sha256": _sha(archive),
        "payload_semantic_digest": sha256_bytes(canonical_json_bytes(semantic_obj)),
        "row_count": 1,
    }
    public = [{
        "field_id": "P13_DEVELOPMENT_COEF_01",
        "family": "fixture",
        "search_object_commitments": [{"grid": 33, "sha256": _sha(npz), "semantic_digest": sem}],
    }]
    opened_root = tmp_path / "opened"
    out = inspect_and_open_development_coefficient_archive(archive, commitment, public, opened_root)
    assert out["status"] == "PASS"
    assert out["private_master_seed_persisted"] is False
    assert out["private_generator_json_persisted"] is False
    assert (opened_root / "P13_DEVELOPMENT_COEF_01/P13_DEVELOPMENT_COEF_01_G33.npz").is_file()
    assert not any(p.name == "payload_manifest.json" for p in opened_root.rglob("*"))
    assert not any("generators" in p.parts for p in opened_root.rglob("*"))
    assert "SECRET_MUST_NOT_PERSIST" not in json.dumps(out)


def test_archive_sha_mismatch_fails_closed(tmp_path: Path):
    archive = tmp_path / "bad.tar.xz"
    with tarfile.open(archive, "w:xz"):
        pass
    commitment = {"archive_bytes": archive.stat().st_size, "archive_sha256": "0" * 64, "payload_semantic_digest": "x", "row_count": 0}
    try:
        inspect_and_open_development_coefficient_archive(archive, commitment, [], tmp_path / "opened")
    except RuntimeError as exc:
        assert "SHA mismatch" in str(exc)
    else:
        raise AssertionError("SHA mismatch must fail closed")


def test_response_stage_never_authorized_by_k0():
    root = Path(__file__).resolve().parents[4]
    cfg = json.loads((root / "phases/p13/coefficient_law_raw_xt/configs/p13_s2_k0_protocol.json").read_text())
    assert cfg["response_stage"]["DEVELOPMENT_RESPONSE_may_open_in_K0"] is False
    assert cfg["response_stage"]["DEVELOPMENT_RESPONSE_may_open_in_K1"] is False
    assert cfg["response_stage"]["DEVELOPMENT_RESPONSE_may_open_in_K2"] is False
    forbidden = set(cfg["forbidden_membership_or_decision_rules"])
    assert {"proxy labels", "top-k", "Pareto narrowing", "percentile narrowing", "target survivor count"} <= forbidden
