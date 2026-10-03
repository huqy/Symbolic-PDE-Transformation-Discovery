import json
import unittest
from pathlib import Path

from p13rawxt.s3_k5_post_sealed_diagnostics import (
    _ast_formula, _failure_signature_classes, _nondominated, _structure_classes,
    final_response_record, qstats, spearman,
)


def _measurement(coarse, fine):
    fields=[]
    for index in range(1,5):
        records=[]
        for case in ["forcing-global","forcing-local","mixed-global","mixed-local","u0-global","u0-local","v0-global","v0-local"]:
            records.append({"field_id":f"F{index}","case_type":case,"relative_energy_error":fine})
        fields.append({"field_id":f"F{index}","records":records})
    cfields=json.loads(json.dumps(fields))
    for field in cfields:
        for row in field["records"]: row["relative_energy_error"]=coarse
    return {"grids":{"129":{"fields":cfields},"257":{"fields":fields}}}


def _refmap(uncertainty=0.001):
    return {f"F{i}::{case}":{"reference_uncertainty":uncertainty} for i in range(1,5) for case in ["forcing-global","forcing-local","mixed-global","mixed-local","u0-global","u0-local","v0-global","v0-local"]}


class S3K5Tests(unittest.TestCase):
    def test_response_reproduction_strict_gate(self):
        passed=final_response_record(_measurement(.100,.101),257,_refmap(),.15)
        failed=final_response_record(_measurement(.180,.181),257,_refmap(),.15)
        unresolved=final_response_record(_measurement(.149,.150),257,_refmap(),.15)
        self.assertEqual(passed["decision"],"RESPONSE_PASS")
        self.assertEqual(failed["decision"],"RESPONSE_FAIL")
        self.assertEqual(unresolved["decision"],"RESPONSE_UNRESOLVED")

    def test_spearman_has_no_pvalue(self):
        result=spearman([{"x":i,"y":2*i} for i in range(8)],"x","y")
        self.assertAlmostEqual(result["spearman"],1.0)
        self.assertIsNone(result["p_value"])

    def test_qstats(self):
        result=qstats([1,2,3,4,5])
        self.assertEqual((result["n"],result["min"],result["median"],result["max"]),(5,1.0,3.0,5.0))

    def test_structure_classes_preserve_mixed_outcomes(self):
        rows=[{"scientific_branch_id":"a","structural_hash":"s","exact_equivalence_class":"e1","K4_decision":"RESPONSE_PASS"},{"scientific_branch_id":"b","structural_hash":"s","exact_equivalence_class":"e2","K4_decision":"RESPONSE_FAIL"}]
        result=_structure_classes(rows)
        self.assertEqual(result["structural_classes_with_mixed_K4_decisions"],1)
        self.assertIs(result["membership_authority"],False)

    def test_failure_signature_medoid_is_deterministic(self):
        keys=["F::a","F::b"]
        rows=[
          {"scientific_branch_id":"a","K4_decision":"RESPONSE_FAIL","failure_witness_keys":["F::a"],"SEALED_response_cases":{"F::a":.2,"F::b":.1}},
          {"scientific_branch_id":"b","K4_decision":"RESPONSE_FAIL","failure_witness_keys":["F::a"],"SEALED_response_cases":{"F::a":.21,"F::b":.1}},
          {"scientific_branch_id":"c","K4_decision":"RESPONSE_FAIL","failure_witness_keys":["F::a"],"SEALED_response_cases":{"F::a":.5,"F::b":.1}},
        ]
        result=_failure_signature_classes(rows,keys)
        self.assertEqual(result["class_count"],1)
        self.assertEqual(result["classes"][0]["medoid_scientific_branch_id"],"b")
        self.assertIs(result["membership_authority"],False)

    def test_pareto_view_returns_all_exact_nondominated(self):
        rows=[{"scientific_branch_id":"a","x":1.,"y":2.},{"scientific_branch_id":"b","x":2.,"y":1.},{"scientific_branch_id":"c","x":3.,"y":3.}]
        self.assertEqual(_nondominated(rows,["x","y"]),["a","b"])

    def test_ast_formula(self):
        ast={"op":"Add","args":[{"op":"Var","name":"x"},{"op":"Mul","args":[{"op":"Theta","name":"theta_0"},{"op":"Log","args":[{"op":"Var","name":"a"}]}]}]}
        self.assertEqual(_ast_formula(ast),"(x + (theta_0 * log(a)))")

    def test_config_forbids_formal_proxy_selection(self):
        cfg=json.loads((Path(__file__).parents[1]/"configs/p13_s3_k5_protocol.json").read_text())
        gov=cfg["governance"]
        self.assertIs(gov["k4_decision_map_immutable"],True)
        self.assertIs(gov["diagnostics_may_modify_membership"],False)
        self.assertIs(gov["top_k_or_pareto_formal_selection"],False)
        self.assertIs(gov["presentation_set_has_membership_authority"],False)
        self.assertIs(gov["proxy_filter"],False)
        self.assertIs(gov["candidate_refit"],False)


if __name__ == "__main__":
    unittest.main()
