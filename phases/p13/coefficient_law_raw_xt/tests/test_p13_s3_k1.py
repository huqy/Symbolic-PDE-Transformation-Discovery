import hashlib
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

import numpy as np

from p13rawxt.coefficients import canonical_json_bytes, search_object_semantic_digest, sha256_bytes
from p13rawxt.s3_k1_sealed_operator_transfer import (
    _check_tar_member,
    _open_sealed_coefficient,
    _payload_semantic_digest,
    _relative_discrepancy,
    _strip_runtime_fields,
    _validate_partition,
)


class TestP13S3K1(unittest.TestCase):
    def test_partition_is_complete_and_disjoint(self):
        strata = {
            "FORMAL_1955": [{"scientific_branch_id": f"f{i}"} for i in range(1955)],
            "DIAGNOSTIC_DEV_FAIL_348": [{"scientific_branch_id": f"n{i}"} for i in range(348)],
            "DIAGNOSTIC_DEV_UNRESOLVED_4": [{"scientific_branch_id": f"u{i}"} for i in range(4)],
        }
        result = _validate_partition(strata, {
            "FORMAL_1955": 1955,
            "DIAGNOSTIC_DEV_FAIL_348": 348,
            "DIAGNOSTIC_DEV_UNRESOLVED_4": 4,
        })
        self.assertEqual(result["status"], "PASS")

    def test_partition_rejects_cross_stratum_duplicate(self):
        strata = {
            "FORMAL_1955": [{"scientific_branch_id": "same"}] * 1955,
            "DIAGNOSTIC_DEV_FAIL_348": [{"scientific_branch_id": f"n{i}"} for i in range(348)],
            "DIAGNOSTIC_DEV_UNRESOLVED_4": [{"scientific_branch_id": f"u{i}"} for i in range(4)],
        }
        result = _validate_partition(strata, {
            "FORMAL_1955": 1955,
            "DIAGNOSTIC_DEV_FAIL_348": 348,
            "DIAGNOSTIC_DEV_UNRESOLVED_4": 4,
        })
        self.assertEqual(result["status"], "FAIL")

    def test_tar_path_guard(self):
        _check_tar_member(tarfile.TarInfo("search_objects/field/G33.npz"))
        with self.assertRaises(RuntimeError):
            _check_tar_member(tarfile.TarInfo("../SEALED_FINAL_RESPONSE.json"))

    def test_runtime_fields_are_not_scientific_digest_inputs(self):
        value = {"elapsed_seconds": 4.0, "nested": [{"worker_cpu_seconds": 2.0, "J": 0.1}]}
        self.assertEqual(_strip_runtime_fields(value), {"nested": [{"J": 0.1}]})

    def test_relative_discrepancy(self):
        self.assertAlmostEqual(_relative_discrepancy(1.01, 1.0), 0.01)
        self.assertIsNone(_relative_discrepancy(None, 1.0))

    def test_protocol_keeps_response_unopened_and_no_refit(self):
        root = Path(__file__).resolve().parents[1]
        cfg = json.loads((root / "configs/p13_s3_k1_protocol.json").read_text())
        self.assertEqual(cfg["authorization"], "EXPLICIT_USER_AUTHORIZATION_AFTER_K0_AUDIT_20260901")
        self.assertEqual(cfg["data_boundary"]["SEALED_FINAL_RESPONSE"], "SEALED_COMMITTED_UNOPENED")
        self.assertEqual(cfg["cohort_contract"]["complete_execution_count"], 2307)
        self.assertEqual(cfg["cohort_contract"]["formal"]["count"], 1955)
        self.assertFalse(cfg["zero_shot_contract"]["theta_refit"])
        self.assertFalse(cfg["zero_shot_contract"]["AST_refit"])
        self.assertFalse(cfg["zero_shot_contract"]["amplitude_compensation"])
        self.assertFalse(cfg["operator_protocol"]["K1_assigns_final_III_D_operator_decision"])

    def test_transactional_coefficient_opening_and_resume(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            k1 = root / "phases/p13/coefficient_law_raw_xt/runs/s3/K1"
            k1.mkdir(parents=True)
            archive = root / "private_coefficients.tar.xz"
            field_ids = [f"F{i}" for i in range(4)]
            grids = [17, 33, 65]
            public_rows = []
            private_rows = []
            payload_bytes = {}
            for field_index, field_id in enumerate(field_ids):
                public_objects = []
                private_objects = []
                for grid in grids:
                    buffer = io.BytesIO()
                    np.savez_compressed(buffer, x=np.asarray([field_index, grid], dtype=np.float64))
                    data = buffer.getvalue()
                    with np.load(io.BytesIO(data), allow_pickle=False) as z:
                        arrays = {key: np.asarray(z[key]) for key in z.files}
                    sha = hashlib.sha256(data).hexdigest()
                    semantic = search_object_semantic_digest(arrays)
                    member = f"search_objects/{field_id}/{field_id}_G{grid}.npz"
                    payload_bytes[member] = data
                    public_objects.append({"grid": grid, "sha256": sha, "semantic_digest": semantic})
                    private_objects.append({"grid": grid, "sha256": sha, "semantic_digest": semantic, "member_path": member})
                public_rows.append({
                    "field_id": field_id,
                    "role": "SEALED_FINAL_COEF",
                    "family": "fixture_family",
                    "search_object_commitments": public_objects,
                })
                private_rows.append({"field_id": field_id, "search_objects": private_objects})
            payload = {
                "schema": "P13_PRIVATE_COEFFICIENT_PAYLOAD_V1",
                "role": "SEALED_FINAL_COEF",
                "master_seed_hex": "not-persisted",
                "rows": private_rows,
            }
            with tarfile.open(archive, "w:xz") as tf:
                manifest_data = json.dumps(payload, sort_keys=True).encode()
                info = tarfile.TarInfo("payload_manifest.json")
                info.size = len(manifest_data)
                tf.addfile(info, io.BytesIO(manifest_data))
                for member, data in payload_bytes.items():
                    info = tarfile.TarInfo(member)
                    info.size = len(data)
                    tf.addfile(info, io.BytesIO(data))
            cfg = {
                "authorization": "fixture",
                "sealed_final_coefficient_commitment": {
                    "role": "SEALED_FINAL_COEF",
                    "kind": "coefficient",
                    "archive_absolute_path": str(archive),
                    "archive_bytes": archive.stat().st_size,
                    "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                    "payload_semantic_digest": _payload_semantic_digest(payload),
                    "public_row_commitments_digest": sha256_bytes(canonical_json_bytes(public_rows)),
                    "field_count": 4,
                    "field_ids": field_ids,
                    "family": "fixture_family",
                    "expected_grids": grids,
                },
                "outputs": {"opened_coefficient_subdir": "opened_sealed_final_coef"},
            }
            manifest = _open_sealed_coefficient(root, k1, {"public_row_commitments": public_rows}, cfg)
            self.assertEqual(manifest["status"], "PASS")
            self.assertEqual(manifest["search_object_count"], 12)
            self.assertEqual(manifest["SEALED_FINAL_RESPONSE"], "SEALED_COMMITTED_UNOPENED")
            self.assertFalse(manifest["private_master_seed_persisted"])
            resumed = _open_sealed_coefficient(root, k1, {"public_row_commitments": public_rows}, cfg)
            self.assertEqual(resumed, manifest)


if __name__ == "__main__":
    unittest.main()
