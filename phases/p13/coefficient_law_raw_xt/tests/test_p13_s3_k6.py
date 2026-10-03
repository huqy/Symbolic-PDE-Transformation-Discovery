import json
import unittest
from pathlib import Path

from p13rawxt.s3_k6_final_first_branch_freeze import (
    _authority_true_paths,
    _final_dossiers,
    _render_context,
    build_claim_adjudication,
    build_cohort_lineage,
    canonical_bytes,
    classify_response_intervals,
)


ROOT = Path(__file__).parents[1]
CFG = json.loads((ROOT / "configs/p13_s3_k6_protocol.json").read_text())


class S3K6Tests(unittest.TestCase):
    def test_interval_adjudication_is_strict_and_uncertainty_aware(self):
        self.assertEqual(classify_response_intervals([{"lower": 0.10, "upper": 0.14}]), "RESPONSE_PASS")
        self.assertEqual(classify_response_intervals([{"lower": 0.16, "upper": 0.17}]), "RESPONSE_FAIL")
        self.assertEqual(classify_response_intervals([{"lower": 0.14, "upper": 0.16}]), "RESPONSE_UNRESOLVED")
        self.assertEqual(classify_response_intervals([]), "RESPONSE_UNRESOLVED")

    def test_claim_hierarchy_is_separate(self):
        claims = build_claim_adjudication(CFG)
        self.assertEqual(set(claims["claims"]), {"III-A", "III-B", "III-C", "III-D-OP", "III-D-R"})
        self.assertEqual(claims["claims"]["III-D-OP"]["decision"], "SUPPORTED_SEALED")
        self.assertEqual(claims["claims"]["III-D-R"]["decision"], "SUPPORTED_SEALED")
        self.assertEqual(claims["first_branch_conclusion"], "CONSTRUCTIVE_ROUTE_FEASIBILITY_SUPPORTED")

    def test_claim_boundary_forbids_saturation_overclaim(self):
        claims = build_claim_adjudication(CFG)
        self.assertIn("search saturation", claims["not_claimed"])
        self.assertIn("near-capacity attainment", claims["not_claimed"])
        self.assertIn("coefficient-population completeness", claims["not_claimed"])

    def test_complete_cohort_lineage_and_compression(self):
        cohort = build_cohort_lineage(CFG)
        self.assertEqual([(row["PASS"], row.get("UNRESOLVED", row.get("UNRESOLVED_REFERENCE"))) for row in cohort["stages"]], [(2307, 80), (1955, 4), (1955, 0), (423, 27), (3, 0)])
        self.assertEqual(cohort["final_formal_cohort"]["count"], 3)
        self.assertEqual(cohort["compression_descriptive"]["S1_clear_to_final_ratio"], 769.0)
        self.assertEqual(cohort["compression_descriptive"]["S3_operator_eligible_to_final_ratio"], 141.0)
        self.assertIs(cohort["compression_descriptive"]["ranking_or_target_count_used"], False)

    def test_governance_forbids_shortcuts(self):
        forbidden_false = [
            "new_search", "new_candidate_evaluation", "new_threshold", "tau_num_changed",
            "response_gate_changed", "candidate_refit", "candidate_specific_rescue",
            "engineering_compensation", "proxy_label_or_rule",
            "top_k_pareto_percentile_or_weighted_membership", "target_survivor_count",
            "diagnostic_promotion", "K4_decision_map_modified", "K4_pass_membership_modified",
        ]
        self.assertTrue(all(CFG["governance"][key] is False for key in forbidden_false))

    def test_future_completeness_is_new_prospective_branch(self):
        future = CFG["future_completeness"]
        self.assertIs(future["inside_current_branch"], False)
        self.assertIs(future["requires_new_prospective_commitments"], True)
        self.assertIs(future["requires_explicit_user_authorization"], True)
        self.assertGreaterEqual(len(future["must_not_modify"]), 5)

    def test_membership_authority_scanner(self):
        self.assertEqual(_authority_true_paths({"membership_authority": False}), [])
        self.assertEqual(_authority_true_paths({"x": [{"membership_authority": True}]}), ["/x/0/membership_authority"])

    def test_final_dossiers_are_all_and_only_k4_pass(self):
        dossiers = []
        for index, sid in enumerate(CFG["expected_k5"]["pass_scientific_branch_ids"]):
            dossiers.append({
                "scientific_branch_id": sid, "K4_decision": "RESPONSE_PASS", "membership_index": index,
                "structural_hash": "s" + str(index % 2), "presentation_role": "ALL_FORMAL_K4_RESPONSE_PASS",
                "presentation_only": True, "membership_authority": False,
            })
        dossiers.append({"scientific_branch_id": "fail", "K4_decision": "RESPONSE_FAIL", "membership_index": 9, "structural_hash": "sf"})
        result = _final_dossiers({"dossiers": dossiers}, CFG)
        self.assertEqual(result["formal_cohort_count"], 3)
        self.assertEqual(result["distinct_structural_hashes"], 2)
        self.assertIs(result["new_selection_performed"], False)
        self.assertTrue(all(row["formal_role"] == "ALL_K4_RESPONSE_PASS_NO_FURTHER_SELECTION" for row in result["dossiers"]))

    def test_context_maps_plain_language_to_exact_claims(self):
        claims = build_claim_adjudication(CFG)
        cohort = build_cohort_lineage(CFG)
        dossier = {
            "dossiers": [{
                "scientific_branch_id": "id", "structural_hash": "s", "arm": "FULL-V1", "paired_seed": 4,
                "raw_X_formula": "x", "raw_T_formula": "t", "theta": [],
                "SEALED_operator_family_ratio_G65": 0.2, "SEALED_response_worst_upper": 0.1,
            }]
        }
        text = _render_context(claims, cohort, dossier, "digest")
        self.assertIn("complete final cohort, not a hand-picked top three", text)
        self.assertIn("III-D-OP: SUPPORTED_SEALED", text)
        self.assertIn("III-D-R: SUPPORTED_SEALED", text)
        self.assertIn("769×", text)
        self.assertIn("future completeness requires a new prospective protocol", text)

    def test_canonical_bytes_are_deterministic(self):
        self.assertEqual(canonical_bytes({"b": 2, "a": 1}), canonical_bytes({"a": 1, "b": 2}))


if __name__ == "__main__":
    unittest.main()
