from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
import os
import platform
import sys
import time
from pathlib import Path
from typing import Any, Iterable


CONFIG_REL = "phases/p13/coefficient_law_raw_xt/configs/p13_s3_k6_protocol.json"
K6_SUBDIR = "K6_final_claim_III_first_branch_freeze"
CANONICAL_CONTEXT = "P13_COMPREHENSIVE_CONTEXT_FINAL_FIRST_BRANCH_FREEZE_20260902.md"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def atomic_write_text(path: Path, value: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    write_text(tmp, value)
    os.replace(tmp, path)


def canonical_bytes(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def count_jsonl(path: Path) -> int:
    with path.open("rb") as handle:
        return sum(1 for line in handle if line.strip())


def scientific_ids(path: Path) -> list[str]:
    values = [str(row["scientific_branch_id"]) for row in read_jsonl(path)]
    if len(values) != len(set(values)):
        raise RuntimeError(f"duplicate scientific_branch_id in {path}")
    return values


def classify_response_intervals(intervals: Iterable[dict[str, Any]], threshold: float = 0.15) -> str:
    rows = list(intervals)
    if not rows:
        return "RESPONSE_UNRESOLVED"
    if any(float(row["lower"]) > threshold for row in rows):
        return "RESPONSE_FAIL"
    if all(float(row["upper"]) < threshold for row in rows):
        return "RESPONSE_PASS"
    return "RESPONSE_UNRESOLVED"


def _authority_true_paths(obj: Any, prefix: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            here = f"{prefix}/{key}"
            if key == "membership_authority" and value is True:
                found.append(here)
            found.extend(_authority_true_paths(value, here))
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            found.extend(_authority_true_paths(value, f"{prefix}/{index}"))
    return found


def _resolve_marker(root: Path, marker_rel: str) -> Path:
    marker = root / marker_rel
    if not marker.is_file():
        raise FileNotFoundError(marker)
    run = root / marker.read_text(encoding="utf-8").strip()
    if not run.is_dir():
        raise FileNotFoundError(run)
    return run


def _resolve_input(root: Path, s3run: Path, spec: dict[str, Any]) -> Path:
    base = root if spec["scope"] == "root" else s3run
    return base / spec["path"]


def _verify_k5(root: Path, cfg: dict[str, Any]) -> tuple[Path, Path, list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    checks: dict[str, bool] = {}
    s3run = _resolve_marker(root, cfg["k5_run_marker"])
    checks["authoritative_s3_run"] = str(s3run.relative_to(root)) == cfg["expected_s3_run"]
    k5 = s3run / "K5_post_sealed_diagnostics"
    expected = cfg["expected_k5"]
    checks["K5_status"] = (s3run / "K5_OVERALL_STATUS.txt").is_file() and (s3run / "K5_OVERALL_STATUS.txt").read_text().strip() == expected["overall_status"]
    checks["K5_next"] = (s3run / "K5_NEXT_ACTION.txt").is_file() and (s3run / "K5_NEXT_ACTION.txt").read_text().strip() == expected["next_action"]
    summary = load_json(k5 / "K5_SCIENTIFIC_SUMMARY.json")
    semantic = load_json(k5 / "K5_SEMANTIC_OUTPUT_DIGEST.json")
    checks["K5_semantic_expected"] = summary.get("semantic_output_digest") == semantic.get("semantic_output_digest") == expected["semantic_output_digest"]
    checks["K5_semantic_internal"] = hashlib.sha256(canonical_bytes(semantic["basis"])).hexdigest() == semantic["semantic_output_digest"]
    branch_path = root / summary["branch_diagnostics"]
    checks["K5_branch_path"] = branch_path == k5 / "K5_COMPLETE_423_BRANCH_DIAGNOSTICS.jsonl"
    checks["K5_branch_sha"] = branch_path.is_file() and sha256_file(branch_path) == expected["branch_diagnostics_sha256"] == summary["branch_diagnostics_sha256"]
    rows = read_jsonl(branch_path)
    checks["K5_row_count"] = len(rows) == expected["complete_cohort"]
    checks["K5_unique_ids"] = len({row["scientific_branch_id"] for row in rows}) == len(rows)
    checks["K5_unique_membership_index"] = len({int(row["membership_index"]) for row in rows}) == len(rows)
    recount: collections.Counter[str] = collections.Counter()
    interval_integrity = True
    all_authority_false = True
    for row in rows:
        intervals = row.get("SEALED_response_intervals", [])
        decision = classify_response_intervals(intervals)
        recount[decision] += 1
        witnesses = sorted(f"{item['field_id']}::{item['case_type']}" for item in intervals if float(item["lower"]) > 0.15)
        overlaps = sorted(f"{item['field_id']}::{item['case_type']}" for item in intervals if float(item["lower"]) <= 0.15 <= float(item["upper"]))
        values = [float(item["nominal"]) for item in intervals]
        aggregates = bool(values) and math.isclose(max(values), float(row["SEALED_response_worst_nominal"]), rel_tol=0.0, abs_tol=1e-14)
        aggregates = aggregates and math.isclose(max(float(item["upper"]) for item in intervals), float(row["SEALED_response_worst_upper"]), rel_tol=0.0, abs_tol=1e-14)
        interval_integrity = interval_integrity and len(intervals) == 32 and decision == row.get("K4_decision") and witnesses == row.get("failure_witness_keys") and overlaps == row.get("threshold_overlap_keys") and aggregates
        all_authority_false = all_authority_false and row.get("membership_authority") is False and row.get("K4_decision_immutable") is True
    expected_counts = collections.Counter({"RESPONSE_PASS": expected["response_pass"], "RESPONSE_UNRESOLVED": expected["response_unresolved"], "RESPONSE_FAIL": expected["response_fail"]})
    checks["K5_complete_interval_reproduction"] = interval_integrity and recount == expected_counts
    checks["K5_rows_zero_membership_authority"] = all_authority_false
    pass_ids = {row["scientific_branch_id"] for row in rows if row["K4_decision"] == "RESPONSE_PASS"}
    checks["K5_pass_ids_exact"] = pass_ids == set(expected["pass_scientific_branch_ids"])
    immutable = load_json(k5 / "K5_K4_DECISION_MEMBERSHIP_IMMUTABILITY_AUDIT.json")
    checks["K5_immutability"] = immutable.get("status") == "PASS" and immutable.get("decision_map_modified") is False and immutable.get("pass_membership_modified") is False and immutable.get("K5_reproduced_counts") == {"RESPONSE_PASS": 3, "RESPONSE_UNRESOLVED": 0, "RESPONSE_FAIL": 420}
    guard = load_json(k5 / "K5_DATA_BOUNDARY_GUARD.json")
    checks["K5_guard"] = all(guard.get(key) is False for key in (
        "K4_decision_map_modified", "K4_pass_membership_modified", "candidate_refit", "branch_reselection", "proxy_filter", "top_k_or_Pareto_formal_selection", "presentation_set_membership_authority", "weighted_composite_score", "new_response_threshold", "new_fidelity_escalation", "amplitude_compensation"
    ))
    k5cfg = load_json(root / "phases/p13/coefficient_law_raw_xt/configs/p13_s3_k5_protocol.json")
    output_hashes_ok = True
    descriptive_authority_paths: list[str] = []
    for key, filename in k5cfg["outputs"].items():
        path = k5 / filename
        output_hashes_ok = output_hashes_ok and path.is_file() and sha256_file(path) == semantic["basis"]["output_sha256"][key]
        if path.suffix == ".json":
            descriptive_authority_paths.extend(_authority_true_paths(load_json(path), filename))
    checks["K5_output_hashes"] = output_hashes_ok
    checks["K5_descriptive_zero_membership_authority"] = not descriptive_authority_paths
    structures = load_json(k5 / "K5_STRUCTURE_AND_EQUIVALENCE_CLASSES.json")
    signatures = load_json(k5 / "K5_FAILURE_SIGNATURE_CLASSES.json")
    pareto = load_json(k5 / "K5_DESCRIPTIVE_PARETO_VIEW.json")
    checks["K5_descriptive_shapes"] = structures.get("structural_hash_class_count") == 217 and structures.get("exact_equivalence_class_count") == 423 and signatures.get("class_count") == 5 and pareto.get("nondominated_count") == 1
    exemplars = load_json(k5 / "K5_PAPER_FACING_EXEMPLARS.json")
    pass_dossiers = [row for row in exemplars["dossiers"] if row["K4_decision"] == "RESPONSE_PASS"]
    checks["K5_all_three_pass_dossiers"] = len(pass_dossiers) == 3 and {row["scientific_branch_id"] for row in pass_dossiers} == pass_ids and exemplars.get("membership_authority") is False and exemplars.get("changes_K4_membership") is False
    status = "PASS" if all(checks.values()) else "FAIL"
    entry = {
        "status": status,
        "checks": checks,
        "K5_semantic_output_digest": semantic["semantic_output_digest"],
        "K5_recomputed_counts": dict(recount),
        "K5_recomputed_interval_count": sum(len(row["SEALED_response_intervals"]) for row in rows),
        "K5_pass_scientific_branch_ids": sorted(pass_ids),
        "K5_descriptive_membership_authority_true_paths": descriptive_authority_paths,
    }
    return s3run, k5, rows, exemplars, entry


def _verify_lineage(root: Path, s3run: Path, k5_rows: list[dict[str, Any]], cfg: dict[str, Any]) -> tuple[dict[str, Path], dict[str, Any]]:
    checks: dict[str, bool] = {}
    paths: dict[str, Path] = {}
    for name, spec in cfg["frozen_lineage"].items():
        path = _resolve_input(root, s3run, spec)
        paths[name] = path
        checks[f"{name}_exists"] = path.is_file()
        checks[f"{name}_sha"] = path.is_file() and sha256_file(path) == spec["sha256"]
        if "count" in spec:
            checks[f"{name}_count"] = path.is_file() and count_jsonl(path) == int(spec["count"])

    s1_clear = set(scientific_ids(paths["s1_clear_membership"]))
    s1_unresolved = set(scientific_ids(paths["s1_unresolved_membership"]))
    s2_formal = set(scientific_ids(paths["s2_formal_membership"]))
    k2_eligible = set(scientific_ids(paths["k2_response_eligible_membership"]))
    k4_pass = set(scientific_ids(paths["k4_pass_membership"]))
    k5_ids = {row["scientific_branch_id"] for row in k5_rows}
    checks["S1_clear_unresolved_disjoint"] = not (s1_clear & s1_unresolved)
    checks["S2_subset_S1_clear"] = s2_formal < s1_clear
    checks["K2_eligible_subset_S2"] = k2_eligible < s2_formal
    checks["K5_complete_423_identity"] = k5_ids == k2_eligible
    checks["K4_pass_subset_K2_eligible"] = k4_pass < k2_eligible and len(k4_pass) == 3
    checks["K4_K5_pass_identity"] = k4_pass == {row["scientific_branch_id"] for row in k5_rows if row["K4_decision"] == "RESPONSE_PASS"}

    s2k7 = load_json(paths["s2_k7_summary"])
    lineage = cfg["expected_semantic_lineage"]
    checks["S2_K7_claims"] = s2k7.get("OVERALL_STATUS") == "PASS" and s2k7.get("semantic_output_digest") == lineage["s2_k7"] and s2k7.get("K6_semantic_output_digest") == lineage["s2_k6_diagnostic"] and s2k7.get("III_A") == "SUPPORTED" and s2k7.get("III_B") == "SUPPORTED_DEVELOPMENT" and s2k7.get("III_C") == "SUPPORTED_DEVELOPMENT"
    k0 = load_json(paths["k0_summary"])
    checks["K0_semantic_and_boundary"] = k0.get("OVERALL_STATUS") == "PASS" and k0.get("semantic_output_digest") == lineage["s3_k0"] and k0.get("formal_membership_count") == 1955

    k2dir = s3run / "K2_sealed_operator_adjudication_response_entry_lock"
    k2summary = load_json(k2dir / "K2_SCIENTIFIC_SUMMARY.json")
    checks["K2_semantic_counts"] = k2summary.get("semantic_output_digest") == lineage["s3_k2"] and k2summary.get("formal_operator_decision_counts") == {"SEALED_OPERATOR_PASS": 423, "SEALED_OPERATOR_UNRESOLVED": 27, "SEALED_OPERATOR_FAIL": 1505} and k2summary.get("diagnostic_promotion_or_rescue") is False
    k2rows = read_jsonl(paths["k2_formal_decision_map"])
    checks["K2_map_identity"] = {row["scientific_branch_id"] for row in k2rows} == s2_formal and collections.Counter(row["SEALED_operator_decision"] for row in k2rows) == collections.Counter({"SEALED_OPERATOR_PASS": 423, "SEALED_OPERATOR_UNRESOLVED": 27, "SEALED_OPERATOR_FAIL": 1505})

    k3dir = s3run / "K3_sealed_response_reference_control_first"
    k3summary = load_json(k3dir / "K3_SCIENTIFIC_SUMMARY.json")
    reference = load_json(paths["k3_reference_certification"])
    controls = load_json(paths["k3_control_results"])
    checks["K3_reference_control"] = k3summary.get("semantic_output_digest") == lineage["s3_k3"] and reference.get("status") == "PASS" and reference.get("certified_count") == 32 and reference.get("unresolved_count") == 0 and controls.get("status") == "PASS" and controls["controls"]["identity"]["decision"] == "RESPONSE_FAIL" and controls["controls"]["frozen_null"]["decision"] == "RESPONSE_FAIL"

    k4dir = s3run / "K4_complete_candidate_response_certification"
    k4summary = load_json(k4dir / "K4_SCIENTIFIC_SUMMARY.json")
    k4rows = read_jsonl(paths["k4_decision_map"])
    checks["K4_semantic_counts"] = k4summary.get("semantic_output_digest") == lineage["s3_k4"] and k4summary.get("decision_counts") == {"RESPONSE_PASS": 3, "RESPONSE_UNRESOLVED": 0, "RESPONSE_FAIL": 420}
    checks["K4_map_identity"] = {row["scientific_branch_id"] for row in k4rows} == k2_eligible and collections.Counter(row["decision"] for row in k4rows) == collections.Counter({"RESPONSE_PASS": 3, "RESPONSE_FAIL": 420})
    status = "PASS" if all(checks.values()) else "FAIL"
    return paths, {"status": status, "checks": checks}


def build_claim_adjudication(cfg: dict[str, Any]) -> dict[str, Any]:
    decision = cfg["frozen_claim_decisions"]
    return {
        "status": "PASS",
        "current_branch": cfg["claim_boundary"]["current_branch"],
        "claims": {
            "III-A": {"decision": decision["III-A"], "domain": "TRAIN", "evidence": "2307 S1 clear scientific branches; 80 numerical-boundary branches remain REFERENCE/UNRESOLVED", "scope": "constructive discovery under the frozen first-branch grammar"},
            "III-B": {"decision": decision["III-B"], "domain": "DEVELOPMENT coefficient/operator", "evidence": "1955 PASS / 4 UNRESOLVED / 348 FAIL with same AST, theta, deterministic gauge, and zero refit", "scope": "zero-shot operator transfer"},
            "III-C": {"decision": decision["III-C"], "domain": "DEVELOPMENT response", "evidence": "1955 PASS / 0 UNRESOLVED / 0 FAIL on the complete III-B PASS cohort; identity and frozen NULL fail", "scope": "discriminative 15% uncertainty-adjusted response gate"},
            "III-D-OP": {"decision": decision["III-D-OP"], "domain": "SEALED_FINAL coefficient/operator", "evidence": "423 PASS / 27 UNRESOLVED / 1505 FAIL on the complete 1955 formal S2 cohort", "scope": cfg["claim_boundary"]["III_D_OP_scope"]},
            "III-D-R": {"decision": decision["III-D-R"], "domain": "SEALED_FINAL response", "evidence": "3 PASS / 0 UNRESOLVED / 420 FAIL on the complete 423 III-D-OP PASS cohort; identity and frozen NULL fail", "scope": cfg["claim_boundary"]["III_D_R_scope"]},
        },
        "first_branch_conclusion": "CONSTRUCTIVE_ROUTE_FEASIBILITY_SUPPORTED",
        "not_claimed": ["search saturation", "near-capacity attainment", "coefficient-population completeness", "universal response reliability", "1955 or 423 independent transformation laws"],
    }


def build_cohort_lineage(cfg: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "PASS",
        "scientific_branch_identity_preserved": True,
        "stages": [
            {"stage": "S1_TRAIN", "starting": 2387, "PASS": 2307, "UNRESOLVED_REFERENCE": 80, "FAIL": None, "formal_membership_rule": "frozen S1 clear membership"},
            {"stage": "S2_DEVELOPMENT_OPERATOR", "starting": 2307, "PASS": 1955, "UNRESOLVED": 4, "FAIL": 348, "formal_membership_rule": "frozen operator hard gate"},
            {"stage": "S2_DEVELOPMENT_RESPONSE", "starting": 1955, "PASS": 1955, "UNRESOLVED": 0, "FAIL": 0, "formal_membership_rule": "complete 15% response gate"},
            {"stage": "S3_SEALED_OPERATOR", "starting": 1955, "PASS": 423, "UNRESOLVED": 27, "FAIL": 1505, "formal_membership_rule": "same frozen operator hard gate"},
            {"stage": "S3_SEALED_RESPONSE", "starting": 423, "PASS": 3, "UNRESOLVED": 0, "FAIL": 420, "formal_membership_rule": "complete 32-case 15% uncertainty-adjusted response gate"},
        ],
        "final_formal_cohort": {
            "count": 3,
            "scientific_branch_ids": list(cfg["expected_k5"]["pass_scientific_branch_ids"]),
            "source": cfg["frozen_lineage"]["k4_pass_membership"]["path"],
            "source_sha256": cfg["frozen_lineage"]["k4_pass_membership"]["sha256"],
            "membership_rule_changed_in_K5_or_K6": False,
        },
        "compression_descriptive": {
            "S1_clear_to_final_ratio": 2307 / 3,
            "S2_formal_to_final_ratio": 1955 / 3,
            "S3_operator_eligible_to_final_ratio": 423 / 3,
            "final_fraction_of_S1_clear": 3 / 2307,
            "final_fraction_of_S2_formal": 3 / 1955,
            "final_fraction_of_S3_operator_eligible": 3 / 423,
            "ranking_or_target_count_used": False,
        },
    }


def _final_dossiers(exemplars: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    expected = set(cfg["expected_k5"]["pass_scientific_branch_ids"])
    dossiers = []
    for row in exemplars["dossiers"]:
        if row["scientific_branch_id"] not in expected:
            continue
        item = dict(row)
        item.pop("presentation_role", None)
        item.pop("presentation_only", None)
        item.pop("membership_authority", None)
        item["formal_role"] = "ALL_K4_RESPONSE_PASS_NO_FURTHER_SELECTION"
        item["membership_authority_provenance"] = "INHERITED_BYTE_EXACT_FROM_K4_RESPONSE_PASS_MEMBERSHIP"
        dossiers.append(item)
    dossiers.sort(key=lambda row: int(row["membership_index"]))
    if len(dossiers) != 3 or {row["scientific_branch_id"] for row in dossiers} != expected:
        raise RuntimeError("K6 requires exact dossiers for all three K4 PASS identities")
    return {
        "status": "PASS",
        "formal_cohort_count": 3,
        "distinct_structural_hashes": len({row["structural_hash"] for row in dossiers}),
        "source_K4_pass_membership_sha256": cfg["frozen_lineage"]["k4_pass_membership"]["sha256"],
        "new_selection_performed": False,
        "dossiers": dossiers,
    }


def _input_manifest(root: Path, s3run: Path, paths: dict[str, Path], k5: Path, cfg: dict[str, Any]) -> dict[str, Any]:
    records = []
    for name, spec in cfg["frozen_lineage"].items():
        path = paths[name]
        record = {"name": name, "path": str(path.relative_to(root)), "bytes": path.stat().st_size, "sha256": sha256_file(path), "role": "AUTHORITATIVE_FROZEN_INPUT"}
        if "count" in spec:
            record["jsonl_count"] = count_jsonl(path)
        records.append(record)
    for name in (
        "K2_SCIENTIFIC_SUMMARY.json", "K3_SCIENTIFIC_SUMMARY.json", "K4_SCIENTIFIC_SUMMARY.json",
    ):
        if name.startswith("K2"):
            path = s3run / "K2_sealed_operator_adjudication_response_entry_lock" / name
        elif name.startswith("K3"):
            path = s3run / "K3_sealed_response_reference_control_first" / name
        else:
            path = s3run / "K4_complete_candidate_response_certification" / name
        records.append({"name": name[:-5].lower(), "path": str(path.relative_to(root)), "bytes": path.stat().st_size, "sha256": sha256_file(path), "role": "AUTHORITATIVE_FROZEN_SUMMARY"})
    for name in (
        "K5_SCIENTIFIC_SUMMARY.json", "K5_SEMANTIC_OUTPUT_DIGEST.json", "K5_COMPLETE_423_BRANCH_DIAGNOSTICS.jsonl",
        "K5_STRUCTURE_AND_EQUIVALENCE_CLASSES.json", "K5_FAILURE_SIGNATURE_CLASSES.json", "K5_SEED_RELIABILITY_DESCRIPTIVE.json",
        "K5_COEFFICIENT_ABLATION_DESCRIPTIVE.json", "K5_OPERATOR_RESPONSE_ASSOCIATIONS.json", "K5_FAILURE_WITNESS_CENSUS.json",
        "K5_PAPER_FACING_EXEMPLARS.json", "K5_K4_DECISION_MEMBERSHIP_IMMUTABILITY_AUDIT.json", "K5_DATA_BOUNDARY_GUARD.json",
    ):
        path = k5 / name
        record = {"name": name[:-5].lower(), "path": str(path.relative_to(root)), "bytes": path.stat().st_size, "sha256": sha256_file(path), "role": "AUTHORITATIVE_K5_FROZEN_OR_DESCRIPTIVE_INPUT"}
        if path.suffix == ".jsonl":
            record["jsonl_count"] = count_jsonl(path)
        records.append(record)
    return {"status": "PASS", "records": sorted(records, key=lambda row: row["path"]), "large_objects_copied": False}


def _source_manifest(root: Path, cfg_path: Path) -> dict[str, Any]:
    relatives = [
        str(cfg_path.relative_to(root)),
        "phases/p13/coefficient_law_raw_xt/src/p13rawxt/s3_k6_final_first_branch_freeze.py",
        "phases/p13/coefficient_law_raw_xt/tests/test_p13_s3_k6.py",
        "phases/p13/coefficient_law_raw_xt/scripts/run_p13_s3_k6.sh",
        "phases/p13/coefficient_law_raw_xt/scripts/verify_p13_s3_k6.sh",
        "phases/p13/coefficient_law_raw_xt/scripts/package_p13_s3_k6_audit.sh",
        "phases/p13/coefficient_law_raw_xt/docs/P13_S3_K6_FINAL_CLAIM_III_P13_FIRST_BRANCH_FREEZE.md",
        "phases/p13/coefficient_law_raw_xt/patch_manifests/P13_S3_K6_FINAL_CLAIM_III_P13_FIRST_BRANCH_FREEZE_PATCH_20260902.json",
    ]
    return {"files": [{"path": rel, "bytes": (root / rel).stat().st_size, "sha256": sha256_file(root / rel)} for rel in relatives]}


def _render_context(claims: dict[str, Any], cohorts: dict[str, Any], dossiers: dict[str, Any], semantic: str) -> str:
    claim_lines = []
    for name in ("III-A", "III-B", "III-C", "III-D-OP", "III-D-R"):
        row = claims["claims"][name]
        claim_lines.append(f"- **{name}: {row['decision']}** — {row['evidence']}. Scope: {row['scope']}")
    dossier_blocks = []
    for index, row in enumerate(dossiers["dossiers"], 1):
        theta = ", ".join(f"{item['name']}={item['value']:.17g} [{item['hex']}]" for item in row["theta"])
        dossier_blocks.append(
            f"### Certified branch {index}\n\n"
            f"- scientific ID: `{row['scientific_branch_id']}`\n"
            f"- structural hash: `{row['structural_hash']}`\n"
            f"- arm / paired seed: `{row['arm']}` / `{row['paired_seed']}`\n"
            f"- raw X: `{row['raw_X_formula']}`\n"
            f"- raw T: `{row['raw_T_formula']}`\n"
            f"- theta: `{theta or 'none'}`\n"
            f"- SEALED operator family ratio G65: `{row['SEALED_operator_family_ratio_G65']:.17g}`\n"
            f"- SEALED response worst upper: `{row['SEALED_response_worst_upper']:.17g}`\n"
            f"- gauge: frozen deterministic per-field translation/common-positive-scale; no optimized compensation.\n"
        )
    return f"""# P13 Comprehensive Context — Final First-Branch Freeze

**Project:** P13 Constructive Coefficient-Law Discovery  
**Artifact role:** ACTIVE CANONICAL FINAL FIRST-BRANCH FREEZE  
**K6 semantic digest:** `{semantic}`  
**Branch interpretation:** `{claims['current_branch']}`  
**Next action:** future completeness requires a new prospective protocol and explicit authorization.

## Plain-language conclusion

The project did not rank candidates after seeing the final answers. It carried every branch through the previously frozen gates. Starting from 2307 TRAIN-clear scientific branches, 1955 passed DEVELOPMENT operator transfer, 423 passed SEALED operator transfer, and exactly 3 passed every uncertainty-adjusted SEALED response case. Those three are the complete final cohort, not a hand-picked top three.

This establishes that the constructive route is feasible on independent SEALED coefficient and response families. It does not establish that the search was exhaustive, near its theoretical capacity, or universally reliable across coefficient populations.

## Frozen Claim-III hierarchy

{chr(10).join(claim_lines)}

## Frozen cohort lineage

| Stage | Starting | PASS | UNRESOLVED | FAIL |
|---|---:|---:|---:|---:|
| S1 TRAIN | 2387 | 2307 | 80 REFERENCE | — |
| S2 DEVELOPMENT operator | 2307 | 1955 | 4 | 348 |
| S2 DEVELOPMENT response | 1955 | 1955 | 0 | 0 |
| S3 SEALED operator | 1955 | 423 | 27 | 1505 |
| S3 SEALED response | 423 | 3 | 0 | 420 |

Descriptive compression is `769×` from S1 clear to the final cohort, `651.6666666666666×` from the formal S2 cohort, and `141×` from the S3 operator-eligible cohort. No ranking, target-survivor count, proxy rule, refit, rescue, or engineering compensation produced these counts.

## Exact final certified transformation dossiers

{chr(10).join(dossier_blocks)}

## Controls, diagnostics, and interpretation limits

- All 32 SEALED references are certified; identity and frozen NULL both fail the same 15% uncertainty-adjusted response gate, so the gate is discriminative.
- K0 syntactic derivative-order census and nested capacity/kappa results remain TRAIN-only descriptive interpretation. Syntactic order is not functional necessity.
- K5 failure witnesses, seed summaries, coefficient-ablation association, structural/equivalence classes, medoids, Pareto view, and exemplars have zero membership authority.
- The concentration of failures in one SEALED coefficient field is a scientific observation, not permission to filter that field, design a proxy, or compensate candidates.

## Future completeness boundary

The current first branch is closed. Any larger-grammar, larger-`m`, altered-`chi`, stricter-threshold, new coefficient-generator, new SEALED-population, or completeness/attainment experiment is a separate prospective program. It must make fresh commitments before opening new outcomes and may not rewrite the memberships, decisions, thresholds, or claims frozen here.
"""


def _render_handoff(claims: dict[str, Any], cohorts: dict[str, Any], dossiers: dict[str, Any], interpretation: dict[str, Any]) -> str:
    return f"""# P13 First-Branch Paper / Reviewer Handoff

## Headline

Prospective constructive discovery yielded a nonempty independently SEALED certified cohort without response-aware refitting or post-outcome membership rules.

## Reportable complete-census results

- DEVELOPMENT operator: 1955 / 2307 PASS; 4 UNRESOLVED; 348 FAIL.
- DEVELOPMENT response: 1955 / 1955 PASS; identity and frozen NULL FAIL.
- SEALED operator: 423 / 1955 PASS; 27 UNRESOLVED; 1505 FAIL.
- SEALED response: 3 / 423 PASS; 0 UNRESOLVED; 420 FAIL; identity and frozen NULL FAIL.
- Final cohort: all and only the three K4 clear-PASS scientific branches; two structural hashes.

## Required claim wording

`CONSTRUCTIVE_ROUTE_FEASIBILITY_SUPPORTED`, not search saturation, near-capacity attainment, coefficient-population completeness, or universal reliability.

## Diagnostic wording

K0/K5 associations and representative views are descriptive, conditional on their frozen cohorts, and have zero membership authority. Do not describe the K5 Pareto point, medoids, seed result, coefficient-ablation association, or field-03 failure concentration as a formal selector or causal rule.
"""


def run(root: Path, config_path: Path | None = None) -> int:
    started = time.perf_counter()
    cfg_path = config_path or (root / CONFIG_REL)
    cfg = load_json(cfg_path)
    s3run, k5, k5_rows, exemplars, k5_entry = _verify_k5(root, cfg)
    paths, lineage_entry = _verify_lineage(root, s3run, k5_rows, cfg)
    entry = {"status": "PASS" if k5_entry["status"] == lineage_entry["status"] == "PASS" else "FAIL", "K5_review": k5_entry, "lineage_review": lineage_entry}
    k6 = s3run / K6_SUBDIR
    k6.mkdir(parents=True, exist_ok=True)
    write_json(k6 / "K6_ENTRY_AND_K5_REVIEW.json", entry)
    if entry["status"] != "PASS":
        write_text(s3run / "K6_OVERALL_STATUS.txt", "FAIL\n")
        write_text(s3run / "K6_NEXT_ACTION.txt", "BLOCK_P13_S3_K6_PARENT_OR_LINEAGE_REPAIR\n")
        raise RuntimeError("K6 entry/lineage verification failed")

    claims = build_claim_adjudication(cfg)
    cohorts = build_cohort_lineage(cfg)
    controls = {
        "status": "PASS",
        "unresolved_counts": {"S1_TRAIN_REFERENCE": 80, "S2_DEVELOPMENT_OPERATOR": 4, "S2_DEVELOPMENT_RESPONSE": 0, "S3_SEALED_OPERATOR": 27, "S3_SEALED_RESPONSE": 0},
        "SEALED_reference_certified": 32,
        "SEALED_reference_unresolved": 0,
        "identity_control": "RESPONSE_FAIL",
        "frozen_NULL_control": "RESPONSE_FAIL",
        "scientific_outcome": "DISCRIMINATIVE_ABSOLUTE_GATE",
        "response_threshold": 0.15,
        "uncertainty_semantics": "radius=abs(E_fine-E_coarse)+reference_uncertainty; PASS iff all 32 upper bounds < 0.15; FAIL iff any lower bound > 0.15",
    }
    dossiers = _final_dossiers(exemplars, cfg)
    k0_order = load_json(paths["k0_order_census"])
    k0_capacity = load_json(paths["k0_capacity_kappa"])
    transitions = load_json(k5 / "K5_DEVELOPMENT_TO_SEALED_TRANSITIONS.json")
    witness = load_json(k5 / "K5_FAILURE_WITNESS_CENSUS.json")
    structures = load_json(k5 / "K5_STRUCTURE_AND_EQUIVALENCE_CLASSES.json")
    signatures = load_json(k5 / "K5_FAILURE_SIGNATURE_CLASSES.json")
    seed = load_json(k5 / "K5_SEED_RELIABILITY_DESCRIPTIVE.json")
    ablation = load_json(k5 / "K5_COEFFICIENT_ABLATION_DESCRIPTIVE.json")
    associations = load_json(k5 / "K5_OPERATOR_RESPONSE_ASSOCIATIONS.json")
    interpretation = {
        "status": "DESCRIPTIVE_POST_FREEZE_ONLY",
        "membership_authority": False,
        "K0_syntactic_order_census": k0_order,
        "K0_nested_capacity": {
            "B3_only_J_G65": k0_capacity["nested_subspaces"]["B3_ONLY_M0_POINT_VALUE"]["J_G65"],
            "m_le_1_J_G65": k0_capacity["nested_subspaces"]["THEORY_M_LE_1"]["J_G65"],
            "full_m_le_2_J_G65": k0_capacity["nested_subspaces"]["FULL_FROZEN_M_LE_2"]["J_G65"],
            "ratios_to_full_m2": {
                "B3_only": k0_capacity["nested_subspaces"]["B3_ONLY_M0_POINT_VALUE"]["J_G65_over_full_m2_capacity"],
                "m_le_1": k0_capacity["nested_subspaces"]["THEORY_M_LE_1"]["J_G65_over_full_m2_capacity"],
                "full_m_le_2": 1.0,
            },
            "kappa_objective_region": [0.4019231032580137, 0.44931400939822197],
            "claim_boundary": k0_capacity["kappa_obj"]["claim_boundary"],
        },
        "K5_DEVELOPMENT_to_SEALED": transitions,
        "K5_failure_witness_census": witness,
        "K5_structure_summary": {"structural_hash_class_count": structures["structural_hash_class_count"], "exact_equivalence_class_count": structures["exact_equivalence_class_count"], "structural_classes_with_mixed_K4_decisions": structures["structural_classes_with_mixed_K4_decisions"]},
        "K5_failure_signature_summary": {"class_count": signatures["class_count"], "modal_count": signatures["modal_class"]["count"], "clustering_hyperparameter": signatures["clustering_hyperparameter"]},
        "K5_seed_reliability": seed,
        "K5_coefficient_ablation": ablation,
        "K5_operator_response_associations": associations,
        "claim_boundary": cfg["claim_boundary"]["diagnostic_role"],
    }
    future = {"status": "FROZEN_BOUNDARY", **cfg["future_completeness"]}
    guard = {"status": "PASS", **cfg["governance"], "K5_diagnostics_membership_authority": False, "K6_creates_new_membership": False, "first_branch_closed": True}
    input_manifest = _input_manifest(root, s3run, paths, k5, cfg)
    source_manifest = _source_manifest(root, cfg_path)

    write_json(k6 / "K6_CLAIM_III_ADJUDICATION.json", claims)
    write_json(k6 / "K6_FORMAL_COHORT_LINEAGE.json", cohorts)
    write_json(k6 / "K6_UNRESOLVED_AND_CONTROL_STATE.json", controls)
    write_json(k6 / "K6_FINAL_CERTIFIED_TRANSFORMATION_DOSSIERS.json", dossiers)
    write_json(k6 / "K6_POST_FREEZE_INTERPRETATION.json", interpretation)
    write_json(k6 / "K6_FUTURE_COMPLETENESS_BOUNDARY.json", future)
    write_json(k6 / "K6_DATA_BOUNDARY_GUARD.json", guard)
    write_json(k6 / "K6_AUTHORITATIVE_PATH_MANIFEST.json", input_manifest)
    write_json(k6 / "K6_SOURCE_MANIFEST.json", source_manifest)

    placeholder = "PENDING_SEMANTIC_DIGEST"
    context = _render_context(claims, cohorts, dossiers, placeholder)
    handoff = _render_handoff(claims, cohorts, dossiers, interpretation)
    write_text(k6 / CANONICAL_CONTEXT, context)
    write_text(k6 / "P13_S3_K6_PAPER_REVIEWER_HANDOFF.md", handoff)
    core_names = [
        "K6_ENTRY_AND_K5_REVIEW.json", "K6_CLAIM_III_ADJUDICATION.json", "K6_FORMAL_COHORT_LINEAGE.json",
        "K6_UNRESOLVED_AND_CONTROL_STATE.json", "K6_FINAL_CERTIFIED_TRANSFORMATION_DOSSIERS.json",
        "K6_POST_FREEZE_INTERPRETATION.json", "K6_FUTURE_COMPLETENESS_BOUNDARY.json", "K6_DATA_BOUNDARY_GUARD.json",
        "K6_AUTHORITATIVE_PATH_MANIFEST.json", "K6_SOURCE_MANIFEST.json", "P13_S3_K6_PAPER_REVIEWER_HANDOFF.md",
    ]
    registry = {
        "status": "PASS",
        "scope": "SEMANTIC_CORE_ARTIFACTS_BEFORE_DERIVED_CONTEXT_SUMMARY_AND_DIGEST",
        "derived_outputs_verified_separately": [CANONICAL_CONTEXT, "K6_SEMANTIC_OUTPUT_DIGEST.json", "K6_SCIENTIFIC_SUMMARY.json"],
        "artifacts": [{"path": name, "bytes": (k6 / name).stat().st_size, "sha256": sha256_file(k6 / name)} for name in core_names],
    }
    write_json(k6 / "K6_GENERATED_ARTIFACT_REGISTRY.json", registry)
    basis = {
        "semantic_lineage": cfg["expected_semantic_lineage"],
        "K5_branch_diagnostics_sha256": cfg["expected_k5"]["branch_diagnostics_sha256"],
        "K4_pass_membership_sha256": cfg["frozen_lineage"]["k4_pass_membership"]["sha256"],
        "claims": claims,
        "cohort_lineage": cohorts,
        "controls_and_unresolved": controls,
        "future_completeness_boundary": future,
        "guard": guard,
        "authoritative_path_manifest_sha256": sha256_file(k6 / "K6_AUTHORITATIVE_PATH_MANIFEST.json"),
        "generated_artifact_registry": registry,
    }
    semantic = hashlib.sha256(canonical_bytes(basis)).hexdigest()
    write_json(k6 / "K6_SEMANTIC_OUTPUT_DIGEST.json", {"semantic_output_digest": semantic, "basis": basis})
    context = _render_context(claims, cohorts, dossiers, semantic)
    write_text(k6 / CANONICAL_CONTEXT, context)
    atomic_write_text(root / CANONICAL_CONTEXT, context)
    summary = {
        "OVERALL_STATUS": "PASS",
        "NEXT_ACTION": cfg["next_action_on_pass"],
        "semantic_output_digest": semantic,
        "first_branch_conclusion": claims["first_branch_conclusion"],
        "claim_decisions": cfg["frozen_claim_decisions"],
        "final_formal_cohort_count": 3,
        "final_formal_scientific_branch_ids": list(cfg["expected_k5"]["pass_scientific_branch_ids"]),
        "final_formal_structural_hash_count": dossiers["distinct_structural_hashes"],
        "K4_decisions_immutable": True,
        "K4_pass_membership_immutable": True,
        "K5_diagnostics_membership_authority": False,
        "new_search_or_candidate_evaluation": False,
        "new_threshold_refit_rescue_proxy_or_compensation": False,
        "canonical_context": CANONICAL_CONTEXT,
        "canonical_context_sha256": sha256_file(root / CANONICAL_CONTEXT),
        "authoritative_S3_run": str(s3run.relative_to(root)),
    }
    write_json(k6 / "K6_SCIENTIFIC_SUMMARY.json", summary)
    write_text(s3run / "K6_OVERALL_STATUS.txt", "PASS\n")
    write_text(s3run / "K6_NEXT_ACTION.txt", cfg["next_action_on_pass"] + "\n")
    write_text(root / "phases/p13/coefficient_law_raw_xt/runs/LATEST_P13_S3_K6_RUN.txt", str(s3run.relative_to(root)) + "\n")
    print(f"[P13-S3-K6] OVERALL_STATUS=PASS final_cohort=3 semantic={semantic} elapsed={time.perf_counter()-started:.1f}s", flush=True)
    print(f"[P13-S3-K6] NEXT_ACTION={cfg['next_action_on_pass']}", flush=True)
    return 0


def verify(root: Path, config_path: Path | None = None) -> int:
    cfg = load_json(config_path or (root / CONFIG_REL))
    s3run = _resolve_marker(root, "phases/p13/coefficient_law_raw_xt/runs/LATEST_P13_S3_K6_RUN.txt")
    if str(s3run.relative_to(root)) != cfg["expected_s3_run"]:
        raise RuntimeError("K6 marker run mismatch")
    k6 = s3run / K6_SUBDIR
    if (s3run / "K6_OVERALL_STATUS.txt").read_text().strip() != "PASS":
        raise RuntimeError("K6 status is not PASS")
    if (s3run / "K6_NEXT_ACTION.txt").read_text().strip() != cfg["next_action_on_pass"]:
        raise RuntimeError("K6 next action mismatch")
    semantic = load_json(k6 / "K6_SEMANTIC_OUTPUT_DIGEST.json")
    if hashlib.sha256(canonical_bytes(semantic["basis"])).hexdigest() != semantic["semantic_output_digest"]:
        raise RuntimeError("K6 semantic digest mismatch")
    registry = load_json(k6 / "K6_GENERATED_ARTIFACT_REGISTRY.json")
    for item in registry["artifacts"]:
        path = k6 / item["path"]
        if not path.is_file() or path.stat().st_size != item["bytes"] or sha256_file(path) != item["sha256"]:
            raise RuntimeError(f"K6 artifact mismatch: {path}")
    summary = load_json(k6 / "K6_SCIENTIFIC_SUMMARY.json")
    if summary.get("semantic_output_digest") != semantic["semantic_output_digest"] or summary.get("final_formal_cohort_count") != 3:
        raise RuntimeError("K6 scientific summary mismatch")
    claims = load_json(k6 / "K6_CLAIM_III_ADJUDICATION.json")
    if {name: row["decision"] for name, row in claims["claims"].items()} != cfg["frozen_claim_decisions"]:
        raise RuntimeError("K6 Claim-III decision mismatch")
    dossiers = load_json(k6 / "K6_FINAL_CERTIFIED_TRANSFORMATION_DOSSIERS.json")
    if {row["scientific_branch_id"] for row in dossiers["dossiers"]} != set(cfg["expected_k5"]["pass_scientific_branch_ids"]):
        raise RuntimeError("K6 final dossiers mismatch")
    guard = load_json(k6 / "K6_DATA_BOUNDARY_GUARD.json")
    if any(guard.get(key) is not False for key, value in cfg["governance"].items() if value is False):
        raise RuntimeError("K6 governance guard mismatch")
    context = root / CANONICAL_CONTEXT
    if not context.is_file() or sha256_file(context) != summary["canonical_context_sha256"] or context.read_bytes() != (k6 / CANONICAL_CONTEXT).read_bytes():
        raise RuntimeError("K6 canonical context mismatch")
    print("P13_S3_K6_VERIFY=PASS")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--config")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    root = Path(args.project_root).resolve()
    config_path = Path(args.config).resolve() if args.config else None
    return verify(root, config_path) if args.verify_only else run(root, config_path)


if __name__ == "__main__":
    raise SystemExit(main())
