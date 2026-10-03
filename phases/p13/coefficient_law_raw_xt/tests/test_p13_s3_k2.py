import json
import unittest
from pathlib import Path

from p13rawxt.s3_k2_operator_adjudication import (
    _adjudicate_row,
    _boundary_status,
    _component_consensus,
    _final_decision,
    _validate_partition,
    _verify_stored_measurement_row,
)


TAU = 0.005


def _statuses(family, fields):
    return [_boundary_status(family, 0.5, TAU)] + [
        _boundary_status(value, 1.0, TAU) for value in fields
    ]


def _row(family33=0.4, family65=0.4, fields33=None, fields65=None, formal=True):
    fields33 = fields33 or [0.8, 0.8, 0.8, 0.8]
    fields65 = fields65 or [0.8, 0.8, 0.8, 0.8]
    return {
        "source_order": 0,
        "membership_index": 0,
        "scientific_branch_id": "branch-0",
        "stratum": "FORMAL_1955" if formal else "DIAGNOSTIC_DEV_FAIL_348",
        "formal_membership_authority": formal,
        "arm": "FULL-V1",
        "paired_seed": 3,
        "proposal_index": 17,
        "structural_hash": "structure-0",
        "exact_equivalence_class": "equivalence-0",
        "same_AST_theta_gauge_zero_refit": True,
        "K1_final_decision_authority": False,
        "K1_measurement_status": "COMPLETE",
        "SEALED_G33": {
            "all_F0_F4_valid": True,
            "all_J_resolved": True,
            "family_ratio_to_identity": family33,
            "field_ratios_to_identity": fields33,
            "frozen_boundary_statuses": _statuses(family33, fields33),
        },
        "SEALED_G65": {
            "all_F0_F4_valid": True,
            "all_J_resolved": True,
            "family_ratio_to_identity": family65,
            "field_ratios_to_identity": fields65,
            "frozen_boundary_statuses": _statuses(family65, fields65),
        },
        "G33_G65_numerical_fidelity": {
            "family_relative_J_discrepancy": 0.0,
            "max_per_field_relative_J_discrepancy": 0.0,
        },
    }


class TestP13S3K2(unittest.TestCase):
    def test_boundary_semantics_are_ambiguity_only(self):
        self.assertEqual(_boundary_status(0.4974, 0.5, TAU), "CLEAR_PASS")
        self.assertEqual(_boundary_status(0.5, 0.5, TAU), "UNRESOLVED_NUMERICAL_BOUNDARY")
        self.assertEqual(_boundary_status(0.5026, 0.5, TAU), "CLEAR_FAIL")
        self.assertEqual(_boundary_status(0.9949, 1.0, TAU), "CLEAR_PASS")
        self.assertEqual(_boundary_status(1.0, 1.0, TAU), "UNRESOLVED_NUMERICAL_BOUNDARY")
        self.assertEqual(_boundary_status(1.0051, 1.0, TAU), "CLEAR_FAIL")

    def test_component_consensus_requires_both_grids(self):
        self.assertEqual(_component_consensus("CLEAR_PASS", "CLEAR_PASS"), "CLEAR_PASS")
        self.assertEqual(_component_consensus("CLEAR_FAIL", "CLEAR_FAIL"), "CLEAR_FAIL")
        self.assertTrue(_component_consensus("CLEAR_PASS", "CLEAR_FAIL").startswith("UNRESOLVED"))
        self.assertTrue(
            _component_consensus("CLEAR_PASS", "UNRESOLVED_NUMERICAL_BOUNDARY").startswith(
                "UNRESOLVED"
            )
        )

    def test_final_decision_precedence(self):
        passed, _ = _final_decision(["CLEAR_PASS"] * 5)
        unresolved, _ = _final_decision(["CLEAR_PASS"] * 4 + ["UNRESOLVED_X"])
        failed, _ = _final_decision(["CLEAR_PASS"] * 4 + ["CLEAR_FAIL"])
        invalid, _ = _final_decision(["CLEAR_PASS"] * 5, valid_g33=False)
        self.assertEqual(passed, "SEALED_OPERATOR_PASS")
        self.assertEqual(unresolved, "SEALED_OPERATOR_UNRESOLVED")
        self.assertEqual(failed, "SEALED_OPERATOR_FAIL")
        self.assertEqual(invalid, "SEALED_OPERATOR_FAIL")

    def test_adjudication_is_complete_and_deterministic(self):
        passed = _row()
        _verify_stored_measurement_row(passed, TAU)
        self.assertEqual(
            _adjudicate_row(passed, "CLEAR_PASS")["SEALED_operator_decision"],
            "SEALED_OPERATOR_PASS",
        )
        boundary = _row(family33=0.5, family65=0.5)
        self.assertEqual(
            _adjudicate_row(boundary, "CLEAR_PASS")["SEALED_operator_decision"],
            "SEALED_OPERATOR_UNRESOLVED",
        )
        failed = _row(fields33=[0.8, 1.1, 0.8, 0.8], fields65=[0.8, 1.1, 0.8, 0.8])
        self.assertEqual(
            _adjudicate_row(failed, "CLEAR_PASS")["SEALED_operator_decision"],
            "SEALED_OPERATOR_FAIL",
        )

    def test_stored_boundary_status_tampering_is_rejected(self):
        row = _row()
        row["SEALED_G65"]["frozen_boundary_statuses"][0] = "CLEAR_FAIL"
        with self.assertRaises(ValueError):
            _verify_stored_measurement_row(row, TAU)

    def test_diagnostic_clear_pass_cannot_gain_membership_authority(self):
        row = _row(formal=False)
        decision = _adjudicate_row(row, "CLEAR_FAIL")
        self.assertEqual(decision["SEALED_operator_decision"], "SEALED_OPERATOR_PASS")
        self.assertFalse(decision["formal_membership_authority"])
        self.assertFalse(decision["response_membership_authority"])
        self.assertFalse(decision["diagnostic_promotion_or_rescue_authority"])

    def test_partition_rejects_cross_stratum_duplicate(self):
        formal = _row()
        diagnostic = _row(formal=False)
        expected = {
            "FORMAL_1955": {"count": 1, "membership_authority": True},
            "DIAGNOSTIC_DEV_FAIL_348": {"count": 1, "membership_authority": False},
        }
        review = _validate_partition(
            {"FORMAL_1955": [formal], "DIAGNOSTIC_DEV_FAIL_348": [diagnostic]}, expected
        )
        self.assertEqual(review["status"], "FAIL")
        diagnostic["scientific_branch_id"] = "branch-1"
        review = _validate_partition(
            {"FORMAL_1955": [formal], "DIAGNOSTIC_DEV_FAIL_348": [diagnostic]}, expected
        )
        self.assertEqual(review["status"], "PASS")

    def test_protocol_forbids_shortcuts_and_keeps_response_sealed(self):
        config_path = Path(__file__).resolve().parents[1] / "configs/p13_s3_k2_protocol.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(config["frozen_hard_gate"]["tau_num"], TAU)
        self.assertTrue(config["adjudication"]["every_clear_formal_PASS_enters_response_eligibility"])
        self.assertTrue(config["adjudication"]["top_k_after_PASS_forbidden"])
        self.assertTrue(config["adjudication"]["target_survivor_count_forbidden"])
        self.assertTrue(config["adjudication"]["diagnostic_promotion_or_rescue_forbidden"])
        self.assertEqual(config["data_boundary"]["K2_response_archive_access"], "NONE")
        self.assertEqual(config["data_boundary"]["SEALED_FINAL_RESPONSE"], "SEALED_COMMITTED_UNOPENED")
        self.assertTrue(config["data_boundary"]["K3_blocked_until_K2_audit_and_explicit_user_authorization"])


if __name__ == "__main__":
    unittest.main()
