from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from .coefficients import canonical_json_bytes, sha256_bytes


DECISIONS = (
    "SEALED_OPERATOR_PASS",
    "SEALED_OPERATOR_UNRESOLVED",
    "SEALED_OPERATOR_FAIL",
)
COMPONENT_ORDER = ("family", "field_1", "field_2", "field_3", "field_4")


def _sha(path: Path, block: int = 8 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(block), b""):
            h.update(chunk)
    return h.hexdigest()


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL {path}:{line_number}: {exc}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"non-object JSONL row {path}:{line_number}")
            rows.append(row)
    return rows


def _jsonl_count(path: Path) -> int:
    with path.open("rb") as handle:
        return sum(1 for line in handle if line.strip())


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n")


def _resolve_marker(root: Path, relative_marker: str) -> Path:
    marker = root / relative_marker
    if not marker.is_file():
        raise FileNotFoundError(f"missing marker: {marker}")
    relative_target = marker.read_text(encoding="utf-8").strip()
    if not relative_target or Path(relative_target).is_absolute():
        raise ValueError(f"invalid project-relative marker target: {relative_target!r}")
    target = (root / relative_target).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"marker target escapes project root: {target}") from exc
    if not target.is_dir():
        raise FileNotFoundError(f"marker target missing: {target}")
    return target


def _boundary_status(value: float, boundary: float, tau_num: float) -> str:
    value = float(value)
    boundary = float(boundary)
    tau_num = float(tau_num)
    if not math.isfinite(value) or not math.isfinite(boundary) or boundary <= 0.0:
        raise ValueError("boundary classification requires finite value and positive boundary")
    if value < boundary * (1.0 - tau_num):
        return "CLEAR_PASS"
    if value > boundary * (1.0 + tau_num):
        return "CLEAR_FAIL"
    return "UNRESOLVED_NUMERICAL_BOUNDARY"


def _component_consensus(status_g33: str, status_g65: str) -> str:
    if status_g33 == "CLEAR_PASS" and status_g65 == "CLEAR_PASS":
        return "CLEAR_PASS"
    if status_g33 == "CLEAR_FAIL" and status_g65 == "CLEAR_FAIL":
        return "CLEAR_FAIL"
    return "UNRESOLVED_NUMERICAL_BOUNDARY_OR_GRID_DISAGREEMENT"


def _final_decision(
    component_consensus: list[str],
    valid_g33: bool = True,
    valid_g65: bool = True,
    resolved_g33: bool = True,
    resolved_g65: bool = True,
) -> tuple[str, list[str]]:
    if not valid_g33 or not valid_g65:
        return "SEALED_OPERATOR_FAIL", ["REQUIRED_F0_F4_VALIDITY_GATE_FAILED"]
    if not resolved_g33 or not resolved_g65:
        return "SEALED_OPERATOR_UNRESOLVED", ["REQUIRED_OPERATOR_QUANTITY_UNRESOLVED"]
    if any(value == "CLEAR_FAIL" for value in component_consensus):
        return "SEALED_OPERATOR_FAIL", ["AT_LEAST_ONE_HARD_GATE_COMPONENT_CLEAR_FAIL_ON_BOTH_GRIDS"]
    if any(value != "CLEAR_PASS" for value in component_consensus):
        return "SEALED_OPERATOR_UNRESOLVED", [
            "HARD_GATE_COMPONENT_NUMERICAL_BOUNDARY_OR_GRID_DISAGREEMENT"
        ]
    return "SEALED_OPERATOR_PASS", ["ALL_FIVE_HARD_GATE_COMPONENTS_CLEAR_PASS_ON_G33_AND_G65"]


def _quantile(sorted_values: list[float], probability: float) -> float | None:
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    position = (len(sorted_values) - 1) * probability
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return float(sorted_values[lower])
    weight = position - lower
    return float(sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight)


def _quantiles(values: Iterable[float]) -> dict[str, float | None]:
    finite = sorted(float(value) for value in values if math.isfinite(float(value)))
    return {
        "min": finite[0] if finite else None,
        "p10": _quantile(finite, 0.10),
        "median": _quantile(finite, 0.50),
        "p90": _quantile(finite, 0.90),
        "p99": _quantile(finite, 0.99),
        "max": finite[-1] if finite else None,
    }


def _named_parent_file(config: dict[str, Any], name: str) -> dict[str, Any]:
    matches = [row for row in config["parent"]["files"] if row["name"] == name]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one parent file named {name!r}")
    return matches[0]


def _verify_stored_measurement_row(row: dict[str, Any], tau_num: float) -> None:
    branch_id = row.get("scientific_branch_id", "<missing>")
    if row.get("K1_measurement_status") != "COMPLETE":
        raise ValueError(f"K1 measurement is not complete: {branch_id}")
    if row.get("K1_final_decision_authority") is not False:
        raise ValueError(f"K1 incorrectly assigned final decision authority: {branch_id}")
    if row.get("same_AST_theta_gauge_zero_refit") is not True:
        raise ValueError(f"zero-refit inheritance missing: {branch_id}")
    for grid_label in ("SEALED_G33", "SEALED_G65"):
        grid = row.get(grid_label)
        if not isinstance(grid, dict):
            raise ValueError(f"missing {grid_label}: {branch_id}")
        if grid.get("all_F0_F4_valid") is not True or grid.get("all_J_resolved") is not True:
            raise ValueError(f"incomplete K1 operator record at {grid_label}: {branch_id}")
        field_ratios = grid.get("field_ratios_to_identity")
        if not isinstance(field_ratios, list) or len(field_ratios) != 4:
            raise ValueError(f"invalid field-ratio vector at {grid_label}: {branch_id}")
        family_ratio = float(grid["family_ratio_to_identity"])
        ratios = [float(value) for value in field_ratios]
        if not all(math.isfinite(value) and value >= 0.0 for value in [family_ratio, *ratios]):
            raise ValueError(f"nonfinite or negative operator ratio at {grid_label}: {branch_id}")
        recomputed = [_boundary_status(family_ratio, 0.5, tau_num)] + [
            _boundary_status(value, 1.0, tau_num) for value in ratios
        ]
        if grid.get("frozen_boundary_statuses") != recomputed:
            raise ValueError(f"stored boundary statuses do not recompute at {grid_label}: {branch_id}")


def _adjudicate_row(row: dict[str, Any], development_status: str) -> dict[str, Any]:
    g33 = row["SEALED_G33"]
    g65 = row["SEALED_G65"]
    statuses_g33 = g33.get("frozen_boundary_statuses")
    statuses_g65 = g65.get("frozen_boundary_statuses")
    valid_g33 = bool(g33.get("all_F0_F4_valid"))
    valid_g65 = bool(g65.get("all_F0_F4_valid"))
    resolved_g33 = (
        bool(g33.get("all_J_resolved"))
        and isinstance(statuses_g33, list)
        and len(statuses_g33) == 5
    )
    resolved_g65 = (
        bool(g65.get("all_J_resolved"))
        and isinstance(statuses_g65, list)
        and len(statuses_g65) == 5
    )
    if resolved_g33 and resolved_g65:
        component_consensus = [
            _component_consensus(first, second)
            for first, second in zip(statuses_g33, statuses_g65)
        ]
    else:
        component_consensus = [
            "UNRESOLVED_NUMERICAL_BOUNDARY_OR_GRID_DISAGREEMENT"
        ] * 5
    decision, reasons = _final_decision(
        component_consensus,
        valid_g33,
        valid_g65,
        resolved_g33,
        resolved_g65,
    )
    formal_authority = bool(row["formal_membership_authority"])
    return {
        "source_order": int(row["source_order"]),
        "membership_index": int(row["membership_index"]),
        "scientific_branch_id": row["scientific_branch_id"],
        "stratum": row["stratum"],
        "development_operator_status": development_status,
        "formal_membership_authority": formal_authority,
        "diagnostic_promotion_or_rescue_authority": False if not formal_authority else None,
        "arm": row["arm"],
        "paired_seed": int(row["paired_seed"]),
        "proposal_index": int(row["proposal_index"]),
        "structural_hash": row["structural_hash"],
        "exact_equivalence_class": row["exact_equivalence_class"],
        "same_AST_theta_gauge_zero_refit": bool(row["same_AST_theta_gauge_zero_refit"]),
        "component_order": list(COMPONENT_ORDER),
        "G33_boundary_statuses": statuses_g33,
        "G65_boundary_statuses": statuses_g65,
        "component_consensus": component_consensus,
        "SEALED_operator_decision": decision,
        "decision_reasons": reasons,
        "SEALED_G33_family_ratio_to_identity": g33.get("family_ratio_to_identity"),
        "SEALED_G65_family_ratio_to_identity": g65.get("family_ratio_to_identity"),
        "SEALED_G33_field_ratios_to_identity": g33.get("field_ratios_to_identity"),
        "SEALED_G65_field_ratios_to_identity": g65.get("field_ratios_to_identity"),
        "G33_G65_numerical_fidelity": row.get("G33_G65_numerical_fidelity"),
        "K2_final_operator_decision_authority": True,
        "response_membership_authority": formal_authority and decision == "SEALED_OPERATOR_PASS",
        "proxy_label_or_ranking_used": False,
    }


def _validate_partition(
    rows_by_stratum: dict[str, list[dict[str, Any]]],
    expected: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    checks: dict[str, bool] = {}
    sets: dict[str, set[str]] = {}
    for stratum, contract in expected.items():
        rows = rows_by_stratum.get(stratum, [])
        ids = [str(row.get("scientific_branch_id")) for row in rows]
        sets[stratum] = set(ids)
        checks[f"{stratum}_count"] = len(rows) == int(contract["count"])
        checks[f"{stratum}_unique"] = len(sets[stratum]) == len(rows)
        checks[f"{stratum}_label"] = all(row.get("stratum") == stratum for row in rows)
        checks[f"{stratum}_authority"] = all(
            row.get("formal_membership_authority") is bool(contract["membership_authority"])
            for row in rows
        )
    names = list(sets)
    checks["cross_stratum_disjoint"] = all(
        sets[names[first]].isdisjoint(sets[names[second]])
        for first in range(len(names))
        for second in range(first + 1, len(names))
    )
    union = set().union(*sets.values()) if sets else set()
    checks["complete_partition"] = len(union) == sum(int(row["count"]) for row in expected.values())
    return {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "union_count": len(union),
    }


def _source_manifest(root: Path, paths: Iterable[Path]) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for source in paths:
        path = source.resolve()
        if path in seen:
            continue
        seen.add(path)
        if not path.is_file():
            raise FileNotFoundError(f"source-manifest input missing: {path}")
        records.append(
            {
                "path": path.relative_to(root.resolve()).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": _sha(path),
            }
        )
    return {"files": records}


def _verify_entry(
    root: Path, config: dict[str, Any]
) -> tuple[Path, Path, dict[str, list[dict[str, Any]]], dict[str, Any]]:
    checks: dict[str, bool] = {}
    run = _resolve_marker(root, config["parent"]["k1_run_marker"])
    expected_run = (root / config["parent"]["expected_run"]).resolve()
    checks["K1_run_marker"] = run == expected_run
    k1 = run / config["parent"]["k1_output_subdir"]
    checks["K1_output_directory"] = k1.is_dir()

    for record in config["parent"]["files"]:
        path = k1 / record["path"]
        checks[f"parent_file_{record['name']}"] = path.is_file() and _sha(path) == record["sha256"]
        if "count" in record:
            checks[f"parent_count_{record['name']}"] = path.is_file() and _jsonl_count(path) == int(record["count"])

    summary = _json(k1 / _named_parent_file(config, "scientific_summary")["path"])
    semantic_record = _json(k1 / _named_parent_file(config, "semantic_digest")["path"])
    entry_audit = _json(k1 / _named_parent_file(config, "entry_audit")["path"])
    opening_audit = _json(k1 / _named_parent_file(config, "opened_reproducibility_audit")["path"])
    input_manifest = _json(k1 / _named_parent_file(config, "opened_input_manifest")["path"])
    aggregate = _json(k1 / _named_parent_file(config, "measurement_aggregate")["path"])
    fidelity = _json(k1 / _named_parent_file(config, "numerical_fidelity_census")["path"])
    guard = _json(k1 / _named_parent_file(config, "data_boundary_guard")["path"])
    handoff = _json(k1 / _named_parent_file(config, "adjudication_handoff")["path"])

    checks["K1_status"] = (
        summary.get("OVERALL_STATUS") == config["parent"]["expected_overall_status"]
        and (run / "K1_OVERALL_STATUS.txt").read_text(encoding="utf-8").strip()
        == config["parent"]["expected_overall_status"]
    )
    checks["K1_next_action"] = (
        summary.get("NEXT_ACTION") == config["parent"]["expected_next_action"]
        and (run / "K1_NEXT_ACTION.txt").read_text(encoding="utf-8").strip()
        == config["parent"]["expected_next_action"]
    )
    calculated_semantic = sha256_bytes(canonical_json_bytes(semantic_record["basis"]))
    checks["K1_semantic_digest"] = (
        calculated_semantic
        == semantic_record.get("semantic_output_digest")
        == summary.get("semantic_output_digest")
        == config["parent"]["k1_semantic_output_digest"]
    )
    checks["K0_semantic_lineage"] = (
        summary.get("K0_semantic_output_digest") == config["parent"]["k0_semantic_output_digest"]
    )
    checks["K1_entry_and_opening_audits"] = (
        entry_audit.get("status") == "PASS" and opening_audit.get("status") == "PASS"
    )
    checks["K1_governance"] = (
        summary.get("same_AST_theta_gauge_zero_refit") is True
        and summary.get("formal_membership_changed") is False
        and summary.get("diagnostic_promotion_or_rescue") is False
        and summary.get("top_k_or_proxy_filter") is False
        and summary.get("K1_final_III_D_operator_decision_frozen") is False
    )
    checks["sealed_data_boundary"] = (
        summary.get("SEALED_FINAL_COEF_opened") is True
        and summary.get("SEALED_FINAL_RESPONSE_opened") is False
        and input_manifest.get("SEALED_FINAL_RESPONSE") == "SEALED_COMMITTED_UNOPENED"
        and input_manifest.get("response_archive_access") is False
        and guard.get("SEALED_FINAL_RESPONSE") == "SEALED_COMMITTED_UNOPENED"
        and guard.get("response_archive_access") is False
        and guard.get("candidate_refit") is False
        and guard.get("amplitude_compensation") is False
        and guard.get("top_k_or_proxy_filter") is False
        and guard.get("diagnostic_promotion_or_rescue") is False
        and guard.get("tau_num_widened") is False
    )
    checks["K1_handoff"] = (
        handoff.get("status") == "PASS"
        and handoff.get("K2_must_adjudicate_all_1955_formal_branches") is True
        and handoff.get("K2_must_keep_348_and_4_separate") is True
        and handoff.get("every_clear_formal_PASS_enters_later_response_eligibility") is True
        and handoff.get("top_k_or_representative_narrowing") is False
        and handoff.get("SEALED_FINAL_RESPONSE_opening_authorized") is False
    )

    cohort = config["cohort_contract"]
    expected = {
        cohort["formal"]["stratum"]: cohort["formal"],
        cohort["diagnostic_dev_fail"]["stratum"]: cohort["diagnostic_dev_fail"],
        cohort["diagnostic_dev_unresolved"]["stratum"]: cohort["diagnostic_dev_unresolved"],
    }
    rows_by_stratum: dict[str, list[dict[str, Any]]] = {}
    for record_name in ("formal_ledger", "diagnostic_dev_fail_ledger", "diagnostic_dev_unresolved_ledger"):
        record = _named_parent_file(config, record_name)
        rows_by_stratum[record["stratum"]] = _jsonl(k1 / record["path"])
    partition = _validate_partition(rows_by_stratum, expected)
    checks["complete_disjoint_partition"] = partition["status"] == "PASS"
    all_rows = [row for rows in rows_by_stratum.values() for row in rows]
    checks["original_source_order"] = (
        len(all_rows) == int(cohort["complete_partition_count"])
        and {int(row.get("source_order", -1)) for row in all_rows}
        == set(range(int(cohort["complete_partition_count"])))
    )
    tau_num = float(config["frozen_hard_gate"]["tau_num"])
    integrity_ok = True
    try:
        for row in all_rows:
            _verify_stored_measurement_row(row, tau_num)
    except (KeyError, TypeError, ValueError):
        integrity_ok = False
    checks["row_measurement_and_boundary_integrity"] = integrity_ok

    formal_rows = rows_by_stratum[cohort["formal"]["stratum"]]
    formal_family_gt_tau = sum(
        float(row["G33_G65_numerical_fidelity"]["family_relative_J_discrepancy"]) > tau_num
        for row in formal_rows
    )
    formal_field_gt_tau = sum(
        float(row["G33_G65_numerical_fidelity"]["max_per_field_relative_J_discrepancy"]) > tau_num
        for row in formal_rows
    )
    formal_measurement_unresolved = sum(row.get("K1_measurement_status") != "COMPLETE" for row in formal_rows)
    checks["formal_fidelity_observation"] = (
        formal_family_gt_tau == int(config["fidelity_review"]["observed_formal_family_discrepancy_gt_tau_num"])
        and formal_field_gt_tau
        == int(config["fidelity_review"]["observed_formal_max_per_field_discrepancy_gt_tau_num"])
        and formal_measurement_unresolved
        == int(config["fidelity_review"]["observed_formal_operator_or_numerical_unresolved"])
    )
    identity_discrepancies = [
        float(fidelity["identity"]["family_relative_J_discrepancy"]),
        *[float(value) for value in fidelity["identity"]["per_field_relative_J_discrepancy"]],
    ]
    checks["identity_fidelity"] = max(identity_discrepancies) < tau_num
    checks["K1_aggregate_complete"] = (
        int(aggregate.get("complete_execution_count", -1)) == int(cohort["complete_partition_count"])
        and aggregate.get("formal_membership_changed") is False
        and aggregate.get("diagnostic_promotion_or_rescue") is False
        and aggregate.get("K1_final_adjudication_performed") is False
    )

    review = {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "K0_semantic_output_digest": config["parent"]["k0_semantic_output_digest"],
        "K1_semantic_output_digest": config["parent"]["k1_semantic_output_digest"],
        "formal_S2_membership_sha256": config["parent"]["formal_s2_membership_sha256"],
        "partition": partition,
        "formal_fidelity": {
            "tau_num": tau_num,
            "family_discrepancy_gt_tau_num": formal_family_gt_tau,
            "max_per_field_discrepancy_gt_tau_num": formal_field_gt_tau,
            "operator_or_numerical_unresolved": formal_measurement_unresolved,
            "identity_max_relative_discrepancy": max(identity_discrepancies),
            "conclusion": config["fidelity_review"]["conclusion"],
            "tau_num_changed": False,
            "candidate_specific_rescue": False,
            "posthoc_fraction_trigger": False,
        },
        "data_boundary": {
            "K2_reads_K1_scalar_ledgers_only": True,
            "SEALED_FINAL_coefficient_objects_read_by_K2": False,
            "SEALED_FINAL_RESPONSE_opened_or_read_by_K2": False,
        },
    }
    return run, k1, rows_by_stratum, review


def _decision_counts(rows: Iterable[dict[str, Any]]) -> dict[str, int]:
    counter = Counter(row["SEALED_operator_decision"] for row in rows)
    return {decision: int(counter[decision]) for decision in DECISIONS}


def _descriptive_counts(
    decisions: list[dict[str, Any]], key: str
) -> dict[str, dict[str, int]]:
    grouped: dict[str, Counter[str]] = defaultdict(Counter)
    for row in decisions:
        grouped[str(row[key])][row["SEALED_operator_decision"]] += 1
    return {
        label: {decision: int(counts[decision]) for decision in DECISIONS}
        for label, counts in sorted(grouped.items())
    }


def _formal_summary(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    counts = _decision_counts(decisions)
    pass_rows = [row for row in decisions if row["SEALED_operator_decision"] == "SEALED_OPERATOR_PASS"]
    component_counts: list[dict[str, int]] = []
    for index in range(5):
        counter = Counter(row["component_consensus"][index] for row in decisions)
        component_counts.append({key: int(counter[key]) for key in sorted(counter)})
    structural: dict[str, int] = {}
    equivalence: dict[str, int] = {}
    for decision in DECISIONS:
        selected = [row for row in decisions if row["SEALED_operator_decision"] == decision]
        structural[decision] = len({row["structural_hash"] for row in selected})
        equivalence[decision] = len({row["exact_equivalence_class"] for row in selected})
    g65_all = [float(row["SEALED_G65_family_ratio_to_identity"]) for row in decisions]
    g65_pass = [float(row["SEALED_G65_family_ratio_to_identity"]) for row in pass_rows]
    total = len(decisions)
    return {
        "formal_starting_count": total,
        "decision_counts": counts,
        "count_identity": sum(counts.values()) == total,
        "per_seed_counts_descriptive_only": _descriptive_counts(decisions, "paired_seed"),
        "per_arm_counts_descriptive_only": _descriptive_counts(decisions, "arm"),
        "distinct_structural_hashes_by_decision_descriptive_only": structural,
        "distinct_exact_equivalence_classes_by_decision_descriptive_only": equivalence,
        "component_consensus_counts": dict(zip(COMPONENT_ORDER, component_counts)),
        "branches_with_any_unresolved_component": sum(
            any(value.startswith("UNRESOLVED") for value in row["component_consensus"])
            for row in decisions
        ),
        "SEALED_G65_family_ratio_all": _quantiles(g65_all),
        "SEALED_G65_family_ratio_clear_PASS": _quantiles(g65_pass),
        "fixed_gate_compression_descriptive": {
            "starting_count": total,
            "clear_PASS_count": counts["SEALED_OPERATOR_PASS"],
            "retained_fraction": counts["SEALED_OPERATOR_PASS"] / total if total else None,
            "starting_to_PASS_ratio": total / counts["SEALED_OPERATOR_PASS"]
            if counts["SEALED_OPERATOR_PASS"]
            else None,
            "membership_rule": "UNCHANGED_FROZEN_OPERATOR_HARD_GATE",
            "ranking_or_target_count_used": False,
        },
        "new_numeric_thresholds": [],
        "top_k_or_Pareto_used": False,
        "representative_narrowing_used": False,
        "target_survivor_count_used": False,
        "proxy_or_response_filter_used": False,
        "all_clear_PASS_enter_response_eligibility": True,
    }


def _manifest_record(root: Path, path: Path, count: int | None = None) -> dict[str, Any]:
    record: dict[str, Any] = {
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": _sha(path),
    }
    if count is not None:
        record["count"] = count
    return record


def run(root: Path) -> int:
    root = root.resolve()
    home = root / "phases/p13/coefficient_law_raw_xt"
    config_path = home / "configs/p13_s3_k2_protocol.json"
    config = _json(config_path)
    run_dir, k1, rows_by_stratum, entry_review = _verify_entry(root, config)
    k2 = run_dir / config["outputs"]["run_subdir"]
    k2.mkdir(parents=True, exist_ok=True)
    _write_json(k2 / "K2_ENTRY_AND_K1_EVIDENCE_REVIEW.json", entry_review)
    if entry_review["status"] != "PASS":
        _write_text(run_dir / "K2_OVERALL_STATUS.txt", "FAIL\n")
        _write_text(run_dir / "K2_NEXT_ACTION.txt", config["next_on_failure"] + "\n")
        print("P13_S3_K2_FINAL=FAIL", flush=True)
        return 2

    cohort = config["cohort_contract"]
    contracts = (
        (cohort["formal"], config["outputs"]["formal_decision_map"]),
        (cohort["diagnostic_dev_fail"], config["outputs"]["diagnostic_dev_fail_map"]),
        (cohort["diagnostic_dev_unresolved"], config["outputs"]["diagnostic_dev_unresolved_map"]),
    )
    decisions_by_stratum: dict[str, list[dict[str, Any]]] = {}
    map_manifest: dict[str, dict[str, Any]] = {}
    for contract, filename in contracts:
        stratum = contract["stratum"]
        decisions = [
            _adjudicate_row(row, contract["development_status"])
            for row in rows_by_stratum[stratum]
        ]
        if len(decisions) != int(contract["count"]):
            raise RuntimeError(f"incomplete K2 decision map for {stratum}")
        path = k2 / filename
        _write_jsonl(path, decisions)
        decisions_by_stratum[stratum] = decisions
        map_manifest[stratum] = {
            **_manifest_record(root, path, len(decisions)),
            "formal_membership_authority": bool(contract["membership_authority"]),
            "promotion_or_rescue_authority": None
            if bool(contract["membership_authority"])
            else False,
        }

    formal_decisions = decisions_by_stratum[cohort["formal"]["stratum"]]
    formal_summary = _formal_summary(formal_decisions)
    formal_counts = formal_summary["decision_counts"]
    if sum(formal_counts.values()) != int(cohort["formal"]["count"]):
        raise RuntimeError("formal PASS + UNRESOLVED + FAIL census is incomplete")
    pass_decisions = [
        row for row in formal_decisions if row["SEALED_operator_decision"] == "SEALED_OPERATOR_PASS"
    ]
    formal_map_path = k2 / config["outputs"]["formal_decision_map"]
    pass_membership_path = k2 / config["outputs"]["response_eligible_membership"]
    pass_membership = [
        {
            "membership_index": row["membership_index"],
            "source_order": row["source_order"],
            "scientific_branch_id": row["scientific_branch_id"],
            "arm": row["arm"],
            "paired_seed": row["paired_seed"],
            "proposal_index": row["proposal_index"],
            "structural_hash": row["structural_hash"],
            "exact_equivalence_class": row["exact_equivalence_class"],
            "K2_formal_decision_map_path": formal_map_path.relative_to(root).as_posix(),
            "K2_decision": "SEALED_OPERATOR_PASS",
            "formal_response_eligible": True,
            "same_AST_theta_gauge_zero_refit": True,
        }
        for row in pass_decisions
    ]
    _write_jsonl(pass_membership_path, pass_membership)

    _write_json(k2 / "K2_FORMAL_OPERATOR_DECISION_SUMMARY.json", formal_summary)
    diagnostic_counts = {
        stratum: _decision_counts(decisions)
        for stratum, decisions in decisions_by_stratum.items()
        if stratum != cohort["formal"]["stratum"]
    }
    cross_tab = {
        "formal_DEVELOPMENT_CLEAR_PASS_to_SEALED": formal_counts,
        "diagnostic_DEVELOPMENT_CLEAR_FAIL_to_SEALED": diagnostic_counts[
            cohort["diagnostic_dev_fail"]["stratum"]
        ],
        "diagnostic_DEVELOPMENT_UNRESOLVED_to_SEALED": diagnostic_counts[
            cohort["diagnostic_dev_unresolved"]["stratum"]
        ],
        "diagnostic_membership_or_promotion_authority": False,
        "selection_population_interpretation": (
            "Conditional transport outcomes for DEVELOPMENT-selected strata; not an unbiased full-search-population estimate."
        ),
    }
    _write_json(k2 / "K2_DEVELOPMENT_TO_SEALED_OPERATOR_CROSSTAB.json", cross_tab)

    fidelity_review = {
        "status": "PASS",
        **entry_review["formal_fidelity"],
        "repair_required_before_K2": False,
        "repair_branch_opened": False,
        "tau_num_widened": False,
        "candidate_specific_rescue": False,
        "new_fraction_or_discrepancy_trigger": False,
    }
    _write_json(k2 / "K2_NUMERICAL_FIDELITY_REVIEW.json", fidelity_review)

    response_eligible_manifest = {
        "status": "PASS",
        "formal_starting_count": int(cohort["formal"]["count"]),
        "response_eligible_count": len(pass_membership),
        "response_eligible_membership": _manifest_record(
            root, pass_membership_path, len(pass_membership)
        ),
        "complete_formal_decision_map": _manifest_record(
            root, formal_map_path, len(formal_decisions)
        ),
        "formal_S2_membership_sha256": config["parent"]["formal_s2_membership_sha256"],
        "scientific_branch_identity_preserved": True,
        "every_and_only_clear_formal_PASS_included": True,
        "exact_equivalence_execution_sharing_only": True,
        "top_k_or_representative_narrowing": False,
        "diagnostic_promotion_or_rescue": False,
        "SEALED_FINAL_RESPONSE": "SEALED_COMMITTED_UNOPENED",
        "K3_currently_blocked_pending_K2_audit_and_explicit_user_authorization": True,
    }
    _write_json(k2 / config["outputs"]["response_eligible_manifest"], response_eligible_manifest)

    nonempty = formal_counts["SEALED_OPERATOR_PASS"] > 0
    next_action = config["next_if_nonempty_PASS"] if nonempty else config["next_if_zero_PASS"]
    claim_lock = {
        "status": "PASS",
        "III_D_operator_decision_map_frozen": True,
        "formal_operator_counts": formal_counts,
        "formal_clear_PASS_cohort_nonempty": nonempty,
        "formal_response_path": "ELIGIBLE_BUT_BLOCKED_PENDING_K3"
        if nonempty
        else "STOPPED_ZERO_CLEAR_PASS",
        "every_clear_formal_PASS_response_eligible": True,
        "final_global_Claim_III_hierarchy_assigned_here": False,
        "III_D_response_status": "UNTESTED_SEALED_RESPONSE",
        "top_k_after_operator_PASS_forbidden": True,
        "representative_narrowing_after_operator_PASS_forbidden": True,
        "diagnostic_membership_or_promotion_authority": False,
        "SEALED_FINAL_RESPONSE_opened": False,
        "K3_opening_authorized_by_K2_execution_alone": False,
        "NEXT_ACTION": next_action,
    }
    _write_json(k2 / "K2_CLAIM_AND_RESPONSE_ENTRY_LOCK.json", claim_lock)

    data_guard = {
        "status": "PASS",
        "K2_input": "FROZEN_K1_SCALAR_OPERATOR_LEDGERS_ONLY",
        "TRAIN_OPERATOR_read_by_K2": False,
        "DEVELOPMENT_RESPONSE_read_by_K2": False,
        "SEALED_FINAL_COEF_objects_read_by_K2": False,
        "SEALED_FINAL_RESPONSE": "SEALED_COMMITTED_UNOPENED",
        "response_archive_access": False,
        "candidate_refit": False,
        "branch_reselection": False,
        "amplitude_compensation": False,
        "proxy_label_or_rule": False,
        "top_k_or_Pareto_or_percentile": False,
        "representative_narrowing": False,
        "target_survivor_count": False,
        "diagnostic_promotion_or_rescue": False,
        "new_threshold": False,
        "tau_num_widened": False,
    }
    _write_json(k2 / "K2_DATA_BOUNDARY_GUARD.json", data_guard)

    patch_paths = [
        config_path,
        Path(__file__).resolve(),
        home / "docs/P13_S3_K2_SEALED_OPERATOR_ADJUDICATION_AND_RESPONSE_ENTRY_LOCK.md",
        home / "scripts/run_p13_s3_k2.sh",
        home / "scripts/verify_p13_s3_k2.sh",
        home / "scripts/package_p13_s3_k2_audit.sh",
        home / "tests/test_p13_s3_k2.py",
        home
        / "patch_manifests/P13_S3_K2_SEALED_OPERATOR_ADJUDICATION_RESPONSE_ENTRY_LOCK_PATCH_20260901.json",
    ]
    parent_paths = [k1 / row["path"] for row in config["parent"]["files"]]
    parent_paths.extend(
        [
            root / config["parent"]["k1_run_marker"],
            run_dir / "K1_OVERALL_STATUS.txt",
            run_dir / "K1_NEXT_ACTION.txt",
        ]
    )
    source_manifest = _source_manifest(root, [*patch_paths, *parent_paths])
    _write_json(k2 / "K2_SOURCE_AND_PARENT_MANIFEST.json", source_manifest)
    runtime = {
        "python": sys.version,
        "platform": platform.platform(),
        "parallelism": "single_coordinator_deterministic_postprocessing",
        "OMP_NUM_THREADS": os.environ.get("OMP_NUM_THREADS"),
        "MKL_NUM_THREADS": os.environ.get("MKL_NUM_THREADS"),
        "OPENBLAS_NUM_THREADS": os.environ.get("OPENBLAS_NUM_THREADS"),
        "NUMEXPR_NUM_THREADS": os.environ.get("NUMEXPR_NUM_THREADS"),
    }
    _write_json(k2 / "K2_RUNTIME_ENVIRONMENT.json", runtime)

    semantic_basis = {
        "stage": "P13-S3-K2",
        "K0_semantic_output_digest": config["parent"]["k0_semantic_output_digest"],
        "K1_semantic_output_digest": config["parent"]["k1_semantic_output_digest"],
        "formal_S2_membership_sha256": config["parent"]["formal_s2_membership_sha256"],
        "entry_review": entry_review,
        "decision_map_manifest": map_manifest,
        "formal_decision_summary": formal_summary,
        "diagnostic_cross_tab": cross_tab,
        "numerical_fidelity_review": fidelity_review,
        "response_eligible_manifest": response_eligible_manifest,
        "claim_and_response_entry_lock": claim_lock,
        "data_boundary": data_guard,
        "source_manifest_sha256": _sha(k2 / "K2_SOURCE_AND_PARENT_MANIFEST.json"),
    }
    semantic_digest = sha256_bytes(canonical_json_bytes(semantic_basis))
    _write_json(
        k2 / "K2_SEMANTIC_OUTPUT_DIGEST.json",
        {"semantic_output_digest": semantic_digest, "basis": semantic_basis},
    )

    scientific_summary = {
        "OVERALL_STATUS": "PASS",
        "NEXT_ACTION": next_action,
        "authorization": config["authorization"],
        "authoritative_S3_run": run_dir.relative_to(root).as_posix(),
        "K0_semantic_output_digest": config["parent"]["k0_semantic_output_digest"],
        "K1_semantic_output_digest": config["parent"]["k1_semantic_output_digest"],
        "semantic_output_digest": semantic_digest,
        "formal_starting_count": int(cohort["formal"]["count"]),
        "formal_operator_decision_counts": formal_counts,
        "formal_decision_count_identity": sum(formal_counts.values())
        == int(cohort["formal"]["count"]),
        "response_eligible_count": len(pass_membership),
        "formal_decision_map": map_manifest[cohort["formal"]["stratum"]],
        "response_eligible_membership": response_eligible_manifest["response_eligible_membership"],
        "diagnostic_outcome_counts": diagnostic_counts,
        "K1_fidelity_repair_required_before_K2": False,
        "same_AST_theta_gauge_zero_refit": True,
        "formal_membership_rule_changed": False,
        "diagnostic_promotion_or_rescue": False,
        "top_k_or_proxy_or_representative_narrowing": False,
        "SEALED_FINAL_COEF_objects_read_by_K2": False,
        "SEALED_FINAL_RESPONSE_opened": False,
        "K3_authorized_by_this_run": False,
        "III_D_operator_decision_map_frozen": True,
        "III_D_response_status": "UNTESTED_SEALED_RESPONSE",
    }
    _write_json(k2 / "K2_SCIENTIFIC_SUMMARY.json", scientific_summary)
    _write_text(run_dir / "K2_OVERALL_STATUS.txt", "PASS\n")
    _write_text(run_dir / "K2_NEXT_ACTION.txt", next_action + "\n")
    _write_text(
        home / "runs/LATEST_P13_S3_K2_RUN.txt",
        run_dir.relative_to(root).as_posix() + "\n",
    )
    _write_text(
        home / "runs/LATEST_P13_S3_RESPONSE_ELIGIBLE_INPUT.txt",
        (k2 / config["outputs"]["response_eligible_manifest"]).relative_to(root).as_posix()
        + "\n",
    )
    print("P13_S3_K2_FINAL=PASS", flush=True)
    print(
        "P13_S3_K2_FORMAL_COUNTS="
        f"PASS:{formal_counts['SEALED_OPERATOR_PASS']} "
        f"UNRESOLVED:{formal_counts['SEALED_OPERATOR_UNRESOLVED']} "
        f"FAIL:{formal_counts['SEALED_OPERATOR_FAIL']}",
        flush=True,
    )
    print(f"P13_S3_K2_SEMANTIC_DIGEST={semantic_digest}", flush=True)
    print(f"P13_S3_K2_NEXT_ACTION={next_action}", flush=True)
    return 0


def verify(root: Path) -> dict[str, Any]:
    root = root.resolve()
    home = root / "phases/p13/coefficient_law_raw_xt"
    config = _json(home / "configs/p13_s3_k2_protocol.json")
    checks: dict[str, bool] = {}
    run_dir, _, rows_by_stratum, entry_review = _verify_entry(root, config)
    checks["entry_review"] = entry_review["status"] == "PASS"
    marker_run = _resolve_marker(root, "phases/p13/coefficient_law_raw_xt/runs/LATEST_P13_S3_K2_RUN.txt")
    checks["K2_run_marker"] = marker_run == run_dir
    k2 = run_dir / config["outputs"]["run_subdir"]
    summary = _json(k2 / "K2_SCIENTIFIC_SUMMARY.json")
    checks["status"] = (
        summary.get("OVERALL_STATUS") == "PASS"
        and (run_dir / "K2_OVERALL_STATUS.txt").read_text(encoding="utf-8").strip() == "PASS"
    )
    checks["next_action"] = (
        summary.get("NEXT_ACTION") == (run_dir / "K2_NEXT_ACTION.txt").read_text(encoding="utf-8").strip()
        and summary.get("NEXT_ACTION")
        in (config["next_if_nonempty_PASS"], config["next_if_zero_PASS"])
    )

    cohort = config["cohort_contract"]
    specifications = (
        (cohort["formal"], config["outputs"]["formal_decision_map"]),
        (cohort["diagnostic_dev_fail"], config["outputs"]["diagnostic_dev_fail_map"]),
        (cohort["diagnostic_dev_unresolved"], config["outputs"]["diagnostic_dev_unresolved_map"]),
    )
    decisions_by_stratum: dict[str, list[dict[str, Any]]] = {}
    expected_by_stratum: dict[str, list[dict[str, Any]]] = {}
    for contract, filename in specifications:
        path = k2 / filename
        decisions = _jsonl(path)
        decisions_by_stratum[contract["stratum"]] = decisions
        expected = [
            _adjudicate_row(row, contract["development_status"])
            for row in rows_by_stratum[contract["stratum"]]
        ]
        expected_by_stratum[contract["stratum"]] = expected
        checks[f"{contract['stratum']}_map_exact"] = decisions == expected
        checks[f"{contract['stratum']}_count"] = len(decisions) == int(contract["count"])

    formal_decisions = decisions_by_stratum[cohort["formal"]["stratum"]]
    counts = _decision_counts(formal_decisions)
    checks["formal_count_identity"] = sum(counts.values()) == int(cohort["formal"]["count"])
    checks["summary_counts"] = summary.get("formal_operator_decision_counts") == counts
    pass_ids = {
        row["scientific_branch_id"]
        for row in formal_decisions
        if row["SEALED_operator_decision"] == "SEALED_OPERATOR_PASS"
    }
    pass_path = k2 / config["outputs"]["response_eligible_membership"]
    pass_rows = _jsonl(pass_path)
    pass_membership_ids = {row["scientific_branch_id"] for row in pass_rows}
    diagnostic_ids = {
        row["scientific_branch_id"]
        for stratum, decisions in decisions_by_stratum.items()
        if stratum != cohort["formal"]["stratum"]
        for row in decisions
    }
    checks["all_and_only_formal_PASS_response_eligible"] = (
        len(pass_rows) == len(pass_ids)
        and pass_membership_ids == pass_ids
        and pass_ids.isdisjoint(diagnostic_ids)
        and all(row.get("formal_response_eligible") is True for row in pass_rows)
    )
    response_manifest = _json(k2 / config["outputs"]["response_eligible_manifest"])
    checks["response_manifest"] = (
        response_manifest.get("status") == "PASS"
        and int(response_manifest.get("response_eligible_count", -1)) == len(pass_rows)
        and response_manifest.get("every_and_only_clear_formal_PASS_included") is True
        and response_manifest.get("top_k_or_representative_narrowing") is False
        and response_manifest.get("diagnostic_promotion_or_rescue") is False
        and response_manifest.get("SEALED_FINAL_RESPONSE") == "SEALED_COMMITTED_UNOPENED"
        and response_manifest["response_eligible_membership"]["sha256"] == _sha(pass_path)
        and response_manifest["complete_formal_decision_map"]["sha256"]
        == _sha(k2 / config["outputs"]["formal_decision_map"])
    )
    guard = _json(k2 / "K2_DATA_BOUNDARY_GUARD.json")
    checks["data_boundary_and_no_shortcut"] = (
        guard.get("status") == "PASS"
        and guard.get("DEVELOPMENT_RESPONSE_read_by_K2") is False
        and guard.get("SEALED_FINAL_COEF_objects_read_by_K2") is False
        and guard.get("SEALED_FINAL_RESPONSE") == "SEALED_COMMITTED_UNOPENED"
        and guard.get("response_archive_access") is False
        and guard.get("candidate_refit") is False
        and guard.get("proxy_label_or_rule") is False
        and guard.get("top_k_or_Pareto_or_percentile") is False
        and guard.get("representative_narrowing") is False
        and guard.get("target_survivor_count") is False
        and guard.get("diagnostic_promotion_or_rescue") is False
        and guard.get("new_threshold") is False
        and guard.get("tau_num_widened") is False
    )
    claim_lock = _json(k2 / "K2_CLAIM_AND_RESPONSE_ENTRY_LOCK.json")
    checks["claim_and_K3_stop_lock"] = (
        claim_lock.get("III_D_operator_decision_map_frozen") is True
        and claim_lock.get("formal_operator_counts") == counts
        and claim_lock.get("every_clear_formal_PASS_response_eligible") is True
        and claim_lock.get("final_global_Claim_III_hierarchy_assigned_here") is False
        and claim_lock.get("III_D_response_status") == "UNTESTED_SEALED_RESPONSE"
        and claim_lock.get("SEALED_FINAL_RESPONSE_opened") is False
        and claim_lock.get("K3_opening_authorized_by_K2_execution_alone") is False
    )
    source_manifest = _json(k2 / "K2_SOURCE_AND_PARENT_MANIFEST.json")
    source_ok = bool(source_manifest.get("files"))
    for record in source_manifest.get("files", []):
        path = root / record["path"]
        if (
            not path.is_file()
            or path.stat().st_size != int(record["bytes"])
            or _sha(path) != record["sha256"]
        ):
            source_ok = False
            break
    checks["source_and_parent_manifest"] = source_ok
    semantic_record = _json(k2 / "K2_SEMANTIC_OUTPUT_DIGEST.json")
    semantic = sha256_bytes(canonical_json_bytes(semantic_record["basis"]))
    checks["semantic_digest"] = (
        semantic
        == semantic_record.get("semantic_output_digest")
        == summary.get("semantic_output_digest")
    )
    response_marker = root / "phases/p13/coefficient_law_raw_xt/runs/LATEST_P13_S3_RESPONSE_ELIGIBLE_INPUT.txt"
    checks["response_input_marker"] = (
        response_marker.is_file()
        and (root / response_marker.read_text(encoding="utf-8").strip()).resolve()
        == (k2 / config["outputs"]["response_eligible_manifest"]).resolve()
    )
    return {"status": "PASS" if all(checks.values()) else "FAIL", "checks": checks}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args(argv)
    if args.verify_only:
        try:
            review = verify(args.project_root)
        except Exception as exc:
            print(
                f"P13_S3_K2_VERIFY=FAIL type={type(exc).__name__} message={exc}",
                flush=True,
            )
            return 2
        print(f"P13_S3_K2_VERIFY={review['status']}", flush=True)
        return 0 if review["status"] == "PASS" else 2
    try:
        return run(args.project_root)
    except Exception as exc:
        root = args.project_root.resolve()
        home = root / "phases/p13/coefficient_law_raw_xt"
        try:
            config = _json(home / "configs/p13_s3_k2_protocol.json")
            run_dir = root / config["parent"]["expected_run"]
            k2 = run_dir / config["outputs"]["run_subdir"]
            k2.mkdir(parents=True, exist_ok=True)
            _write_json(
                k2 / "K2_FATAL_ERROR.json",
                {
                    "status": "FAIL",
                    "exception_type": type(exc).__name__,
                    "exception": str(exc),
                    "SEALED_FINAL_COEF_objects_read_by_K2": False,
                    "SEALED_FINAL_RESPONSE_opened": False,
                    "response_archive_access": False,
                },
            )
            _write_text(run_dir / "K2_OVERALL_STATUS.txt", "FAIL\n")
            _write_text(run_dir / "K2_NEXT_ACTION.txt", config["next_on_failure"] + "\n")
        except Exception:
            pass
        print(
            f"P13_S3_K2_FINAL=FAIL type={type(exc).__name__} message={exc}",
            flush=True,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
