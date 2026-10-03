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

import numpy as np

from .s2_k1_operator_transfer import _candidate_from_locator
from .s2_k3_response_protocol_lock import classify_branch, response_interval


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return list(iter_jsonl(path))


def count_jsonl(path: Path) -> int:
    with path.open("rb") as handle:
        return sum(1 for line in handle if line.strip())


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_bytes(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def resolve_marker(root: Path, relative: str) -> Path:
    marker = root / relative
    if not marker.is_file():
        raise FileNotFoundError(marker)
    run = root / marker.read_text(encoding="utf-8").strip()
    if not run.exists():
        raise FileNotFoundError(run)
    return run


def qstats(values: Iterable[float | None]) -> dict[str, float | int] | None:
    vals = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    if not vals:
        return None
    arr = np.asarray(vals, dtype=float)
    return {
        "n": int(arr.size), "min": float(np.min(arr)), "p10": float(np.quantile(arr, 0.10)),
        "median": float(np.median(arr)), "p90": float(np.quantile(arr, 0.90)),
        "p99": float(np.quantile(arr, 0.99)), "max": float(np.max(arr)),
    }


def _average_ranks(values: np.ndarray) -> np.ndarray:
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(len(values), dtype=float)
    i = 0
    while i < len(values):
        j = i + 1
        while j < len(values) and values[order[j]] == values[order[i]]:
            j += 1
        ranks[order[i:j]] = 0.5 * ((i + 1) + j)
        i = j
    return ranks


def spearman(rows: list[dict[str, Any]], xkey: str, ykey: str) -> dict[str, Any]:
    pairs = []
    for row in rows:
        x, y = row.get(xkey), row.get(ykey)
        if x is None or y is None:
            continue
        x, y = float(x), float(y)
        if math.isfinite(x) and math.isfinite(y):
            pairs.append((x, y))
    if len(pairs) < 3:
        return {"n": len(pairs), "spearman": None, "p_value": None}
    x = np.asarray([pair[0] for pair in pairs], dtype=float)
    y = np.asarray([pair[1] for pair in pairs], dtype=float)
    rx, ry = _average_ranks(x), _average_ranks(y)
    rho = None if float(np.std(rx)) == 0.0 or float(np.std(ry)) == 0.0 else float(np.corrcoef(rx, ry)[0, 1])
    return {"n": len(pairs), "spearman": rho, "p_value": None}


def _by_id(path: Path) -> dict[str, dict[str, Any]]:
    rows = read_jsonl(path)
    out = {row["scientific_branch_id"]: row for row in rows}
    if len(out) != len(rows):
        raise RuntimeError(f"duplicate scientific ID in {path}")
    return out


def _verify_entry(root: Path, cfg: dict[str, Any]) -> tuple[Path, Path, dict[str, Path], dict[str, Any]]:
    checks: dict[str, bool] = {}
    s3run = resolve_marker(root, cfg["k4_run_marker"])
    k4 = s3run / "K4_complete_candidate_response_certification"
    expected = cfg["expected_k4"]
    checks["K4_status"] = (s3run / "K4_OVERALL_STATUS.txt").is_file() and (s3run / "K4_OVERALL_STATUS.txt").read_text().strip() == expected["overall_status"]
    checks["K4_next"] = (s3run / "K4_NEXT_ACTION.txt").is_file() and (s3run / "K4_NEXT_ACTION.txt").read_text().strip() == expected["next_action"]
    summary = load_json(k4 / "K4_SCIENTIFIC_SUMMARY.json")
    checks["K4_semantic"] = summary.get("semantic_output_digest") == expected["semantic_output_digest"]
    counts = {"RESPONSE_PASS": expected["response_pass"], "RESPONSE_UNRESOLVED": expected["response_unresolved"], "RESPONSE_FAIL": expected["response_fail"]}
    checks["K4_counts"] = summary.get("complete_response_cohort") == expected["complete_response_cohort"] and summary.get("decision_counts") == counts
    checks["K4_pair"] = summary.get("final_fidelity_pair") == expected["final_fidelity_pair"]
    paths = {
        "measurements": root / summary["measurements"], "decision_map": root / summary["decision_map"],
        "pass_membership": root / summary["response_pass_membership"],
        "k0": s3run / cfg["parent_evidence"]["k0_derivative_census_rel_to_s3run"],
        "k1": s3run / cfg["parent_evidence"]["k1_operator_ledger_rel_to_s3run"],
        "k2": s3run / cfg["parent_evidence"]["k2_decision_map_rel_to_s3run"],
        "k3ref": s3run / cfg["parent_evidence"]["k3_reference_certification_rel_to_s3run"],
        "s2k6": root / cfg["parent_evidence"]["s2_k6_branch_diagnostics"],
        "s1clear": root / cfg["parent_evidence"]["s1_clear_membership"],
    }
    expected_hashes = {
        "measurements": expected["measurements_sha256"], "decision_map": expected["decision_map_sha256"],
        "pass_membership": expected["pass_membership_sha256"],
        "k0": cfg["parent_evidence"]["k0_derivative_census_sha256"],
        "k1": cfg["parent_evidence"]["k1_operator_ledger_sha256"],
        "k2": cfg["parent_evidence"]["k2_decision_map_sha256"],
        "k3ref": cfg["parent_evidence"]["k3_reference_certification_sha256"],
        "s2k6": cfg["parent_evidence"]["s2_k6_branch_diagnostics_sha256"],
        "s1clear": cfg["parent_evidence"]["s1_clear_membership_sha256"],
    }
    for name, path in paths.items():
        checks[f"{name}_sha"] = path.is_file() and sha256_file(path) == expected_hashes[name]
    checks["line_counts"] = all(count_jsonl(paths[name]) == count for name, count in {"measurements": 423, "decision_map": 423, "pass_membership": 3, "k0": 1955, "k1": 1955, "k2": 1955, "s2k6": 1955, "s1clear": 2307}.items())
    drows = read_jsonl(paths["decision_map"])
    prows = read_jsonl(paths["pass_membership"])
    dcounts = collections.Counter(row["decision"] for row in drows)
    pass_ids = {row["scientific_branch_id"] for row in drows if row["decision"] == "RESPONSE_PASS"}
    checks["decision_recount"] = dict(dcounts) == {"RESPONSE_PASS": 3, "RESPONSE_FAIL": 420}
    checks["pass_membership_exact"] = len(prows) == 3 and {row["scientific_branch_id"] for row in prows} == pass_ids
    guard = load_json(k4 / "K4_DATA_BOUNDARY_GUARD.json")
    checks["K4_guard"] = guard.get("complete_423_evaluated") is True and guard.get("candidate_refit") is False and guard.get("branch_reselection") is False and guard.get("top_k_or_proxy_filter") is False and guard.get("amplitude_compensation") is False
    escalation = load_json(k4 / "K4_FIDELITY_ESCALATION_SUMMARY.json")
    checks["uniform_fidelity"] = escalation.get("uniform_complete_cohort") is True and escalation.get("candidate_specific_rescue") is False and escalation.get("final_pair") == [129, 257]
    reference = load_json(paths["k3ref"])
    checks["references_32"] = reference.get("status") == "PASS" and reference.get("certified_count") == 32 and reference.get("unresolved_count") == 0
    status = "PASS" if all(checks.values()) else "FAIL"
    return s3run, k4, paths, {"status": status, "checks": checks, "K4_semantic_output_digest": summary.get("semantic_output_digest")}


def _reference_map(reference: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out = {f"{row['field_id']}::{row['case_type']}": row for row in reference["records"]}
    if len(out) != 32 or any(row.get("status") != "REFERENCE_CERTIFIED" for row in out.values()):
        raise RuntimeError("K5 requires 32 certified references")
    return out


def final_response_record(measurement: dict[str, Any], fine_grid: int, refmap: dict[str, dict[str, Any]], threshold: float) -> dict[str, Any]:
    grid = measurement.get("grids", {}).get(str(fine_grid))
    if not grid or grid.get("numerical_unresolved"):
        return {"decision": "RESPONSE_UNRESOLVED", "numerical_unresolved": True, "cases": {}, "intervals": [], "witness_keys": []}
    cases: dict[str, float] = {}
    by_field: dict[str, list[float]] = {}
    for field in grid["fields"]:
        for rec in field["records"]:
            key = f"{rec['field_id']}::{rec['case_type']}"
            cases[key] = float(rec["relative_energy_error"])
            by_field.setdefault(rec["field_id"], []).append(cases[key])
    if set(cases) != set(refmap):
        raise RuntimeError("K4 measurement/reference case mismatch")
    coarse_grid = fine_grid // 2 + 1
    coarse = measurement.get("grids", {}).get(str(coarse_grid))
    if not coarse or coarse.get("numerical_unresolved"):
        return {"decision": "RESPONSE_UNRESOLVED", "numerical_unresolved": True, "cases": cases, "intervals": [], "witness_keys": []}
    coarse_cases = {}
    for field in coarse["fields"]:
        for rec in field["records"]:
            coarse_cases[f"{rec['field_id']}::{rec['case_type']}"] = float(rec["relative_energy_error"])
    intervals = []
    for key in sorted(cases):
        field_id, case_type = key.split("::", 1)
        interval = response_interval(coarse_cases[key], cases[key], float(refmap[key]["reference_uncertainty"]))
        intervals.append({"field_id": field_id, "case_type": case_type, "coarse_grid": coarse_grid, "fine_grid": fine_grid, "coarse_error": coarse_cases[key], "fine_error": cases[key], "reference_uncertainty": float(refmap[key]["reference_uncertainty"]), **interval})
    witnesses = sorted(f"{row['field_id']}::{row['case_type']}" for row in intervals if float(row["lower"]) > threshold)
    overlaps = sorted(f"{row['field_id']}::{row['case_type']}" for row in intervals if float(row["lower"]) <= threshold <= float(row["upper"]))
    return {
        "decision": classify_branch(intervals, threshold), "numerical_unresolved": False, "cases": cases, "intervals": intervals,
        "witness_keys": witnesses, "threshold_overlap_keys": overlaps,
        "worst_nominal": max(cases.values()), "RMS_nominal": math.sqrt(sum(value * value for value in cases.values()) / len(cases)),
        "median_nominal": float(np.median(np.asarray(list(cases.values()), dtype=float))),
        "worst_upper": max(float(row["upper"]) for row in intervals), "worst_lower": max(float(row["lower"]) for row in intervals),
        "worst_by_field": {field: max(values) for field, values in by_field.items()},
    }


def _control_map(k4: Path) -> dict[str, Any]:
    raw = load_json(k4 / "K4_FINAL_CONTROL_RESULTS.json")
    out = {}
    for name, control in raw["controls"].items():
        vals = {f"{row['field_id']}::{row['case_type']}": float(row["nominal"]) for row in control["pair_intervals"]}
        out[name] = {"decision": control["decision"], "nominal_by_case": vals, "worst_nominal": max(vals.values()), "worst_upper": control["worst_upper"], "worst_lower": control["worst_lower"]}
    return out


def _ast_formula(node: dict[str, Any]) -> str:
    op, args = node.get("op"), node.get("args", [])
    if op == "Var": return str(node["name"])
    if op == "Const": return format(float(node["value"]), ".17g")
    if op == "Theta": return str(node["name"])
    if op == "Neg": return f"(-{_ast_formula(args[0])})"
    if op == "Add": return "(" + " + ".join(_ast_formula(arg) for arg in args) + ")"
    if op == "Mul": return "(" + " * ".join(_ast_formula(arg) for arg in args) + ")"
    if op == "Inv": return f"(1 / {_ast_formula(args[0])})"
    if op == "Sqrt": return f"sqrt({_ast_formula(args[0])})"
    if op == "Log": return f"log({_ast_formula(args[0])})"
    if op == "PowInt": return f"({_ast_formula(args[0])} ** {int(node['exponent'])})"
    if op == "PowParam": return f"({_ast_formula(args[0])} ** {_ast_formula(args[1])})"
    if op == "Dx": return f"Dx({_ast_formula(args[0])})"
    if op == "Dt": return f"Dt({_ast_formula(args[0])})"
    raise ValueError(f"unsupported AST op {op}")


def _theta_names(node: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    stack = [node]
    while stack:
        current = stack.pop()
        if current.get("op") == "Theta": out.add(str(current["name"]))
        stack.extend(current.get("args", []))
    return out


def _structure_classes(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def build(key: str) -> list[dict[str, Any]]:
        groups: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
        for row in rows: groups[str(row[key])].append(row)
        result = []
        for value, group in groups.items():
            result.append({key: value, "count": len(group), "decision_counts": dict(collections.Counter(row["K4_decision"] for row in group)), "scientific_branch_ids": sorted(row["scientific_branch_id"] for row in group), "membership_authority": False})
        return sorted(result, key=lambda item: item[key])
    structural, exact = build("structural_hash"), build("exact_equivalence_class")
    return {
        "status": "DESCRIPTIVE_ONLY", "structural_hash_class_count": len(structural), "exact_equivalence_class_count": len(exact),
        "structural_classes_with_mixed_K4_decisions": sum(len(row["decision_counts"]) > 1 for row in structural),
        "exact_classes_with_mixed_K4_decisions": sum(len(row["decision_counts"]) > 1 for row in exact),
        "structural_hash_classes": structural, "exact_equivalence_classes": exact, "membership_authority": False,
    }


def _failure_signature_classes(rows: list[dict[str, Any]], case_keys: list[str]) -> dict[str, Any]:
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = collections.defaultdict(list)
    for row in rows:
        if row["K4_decision"] == "RESPONSE_FAIL": groups[tuple(row["failure_witness_keys"])].append(row)
    classes = []
    for signature, group in groups.items():
        matrix = np.asarray([[float(row["SEALED_response_cases"][key]) for key in case_keys] for row in group], dtype=float)
        distances = np.sum(np.abs(matrix[:, None, :] - matrix[None, :, :]), axis=(1, 2))
        best = float(np.min(distances))
        candidates = [group[index] for index, value in enumerate(distances) if float(value) == best]
        medoid = min(candidates, key=lambda row: row["scientific_branch_id"])
        signature_hash = hashlib.sha256(canonical_bytes(list(signature))).hexdigest()
        classes.append({
            "failure_signature_sha256": signature_hash, "witness_keys": list(signature), "count": len(group),
            "medoid_scientific_branch_id": medoid["scientific_branch_id"], "medoid_total_L1": best,
            "scientific_branch_ids": sorted(row["scientific_branch_id"] for row in group), "membership_authority": False,
        })
    classes.sort(key=lambda item: item["failure_signature_sha256"])
    modal = min(classes, key=lambda item: (-int(item["count"]), item["failure_signature_sha256"])) if classes else None
    return {
        "status": "EXACT_FAILURE_SIGNATURE_DESCRIPTIVE_CLUSTERING", "clustering_hyperparameter": None,
        "signature_definition": "exact set of uncertainty-adjusted lower-bound > 0.15 cases",
        "medoid_definition": "minimum total L1 distance over the complete 32-case nominal response vector; scientific ID tie-break",
        "class_count": len(classes), "classes": classes, "modal_class": modal, "membership_authority": False,
    }


def _nondominated(rows: list[dict[str, Any]], axes: list[str]) -> list[str]:
    values = np.asarray([[float(row[axis]) for axis in axes] for row in rows], dtype=float)
    keep = np.ones(len(rows), dtype=bool)
    for i in range(len(rows)):
        for j in range(len(rows)):
            if i != j and np.all(values[j] <= values[i]) and np.any(values[j] < values[i]):
                keep[i] = False
                break
    return sorted(rows[index]["scientific_branch_id"] for index in range(len(rows)) if bool(keep[index]))


def _case_control_effects(rows: list[dict[str, Any]], controls: dict[str, Any], case_keys: list[str]) -> dict[str, Any]:
    output = []
    for key in case_keys:
        field_id, case_type = key.split("::", 1)
        all_values = [float(row["SEALED_response_cases"][key]) for row in rows]
        passed = [float(row["SEALED_response_cases"][key]) for row in rows if row["K4_decision"] == "RESPONSE_PASS"]
        failed = [float(row["SEALED_response_cases"][key]) for row in rows if row["K4_decision"] == "RESPONSE_FAIL"]
        record = {"field_id": field_id, "case_type": case_type, "all_423": qstats(all_values), "K4_PASS_3": qstats(passed), "K4_FAIL_420": qstats(failed)}
        for name in ("identity", "frozen_null"):
            value = float(controls[name]["nominal_by_case"][key])
            record[f"{name}_nominal"] = value
            record[f"fraction_all_423_below_{name}"] = float(np.mean(np.asarray(all_values) < value))
        output.append(record)
    return {"status": "PAIRED_CASE_DESCRIPTIVE_ONLY", "rows": output, "membership_authority": False}


def _dossier(root: Path, locators: list[dict[str, Any]], row: dict[str, Any], k1: dict[str, Any], role: str) -> dict[str, Any]:
    candidate = _candidate_from_locator(root, locators[int(row["membership_index"])], int(row["membership_index"]))
    if candidate["scientific_branch_id"] != row["scientific_branch_id"]:
        raise RuntimeError("exemplar locator mismatch")
    pair = candidate["pair"]
    names = sorted(_theta_names(pair["raw_X_AST"]) | _theta_names(pair["raw_T_AST"]), key=lambda value: int(value.split("_")[1]))
    gauges = {field["field_id"]: field["F0_F4_records"]["gauge_record"] for field in k1["SEALED_G65"]["per_field"]}
    return {
        "presentation_role": role, "presentation_only": True, "membership_authority": False,
        "scientific_branch_id": row["scientific_branch_id"], "K4_decision": row["K4_decision"],
        "membership_index": row["membership_index"], "arm": row["arm"], "paired_seed": row["paired_seed"],
        "proposal_index": row["proposal_index"], "structural_hash": row["structural_hash"],
        "exact_equivalence_class": row["exact_equivalence_class"], "raw_X_AST": pair["raw_X_AST"], "raw_T_AST": pair["raw_T_AST"],
        "raw_X_formula": _ast_formula(pair["raw_X_AST"]), "raw_T_formula": _ast_formula(pair["raw_T_AST"]),
        "theta": [{"name": name, "value": float(value), "hex": candidate["theta_hex"][index]} for index, (name, value) in enumerate(zip(names, candidate["theta"]))],
        "deterministic_gauge_semantics": candidate["deterministic_gauge"], "SEALED_G65_gauge_by_field": gauges,
        "SEALED_operator_family_ratio_G65": row["SEALED_operator_family_ratio_G65"],
        "SEALED_response_worst_upper": row["SEALED_response_worst_upper"], "SEALED_response_worst_lower": row["SEALED_response_worst_lower"],
        "failure_witness_keys": row["failure_witness_keys"],
    }


def _source_manifest(root: Path, cfg_path: Path) -> dict[str, Any]:
    relatives = [
        str(cfg_path.relative_to(root)), "phases/p13/coefficient_law_raw_xt/src/p13rawxt/s3_k5_post_sealed_diagnostics.py",
        "phases/p13/coefficient_law_raw_xt/scripts/run_p13_s3_k5.sh", "phases/p13/coefficient_law_raw_xt/scripts/verify_p13_s3_k5.sh",
        "phases/p13/coefficient_law_raw_xt/scripts/package_p13_s3_k5_audit.sh", "phases/p13/coefficient_law_raw_xt/tests/test_p13_s3_k5.py",
        "phases/p13/coefficient_law_raw_xt/docs/P13_S3_K5_POST_SEALED_DIAGNOSTICS_AND_REPRESENTATIVE_ANALYSIS.md",
    ]
    return {"files": [{"path": rel, "bytes": (root / rel).stat().st_size, "sha256": sha256_file(root / rel)} for rel in relatives]}


def _update_context(root: Path, summary: dict[str, Any]) -> None:
    path = root / "P13_S3_ROLLING_CONTEXT.md"
    if not path.is_file(): return
    marker = "<!-- S3_K5_FORMAL_RESULT -->"
    block = f'''{marker}\n## S3-K5 — post-SEALED diagnostics and representative analysis\n\n- `OVERALL_STATUS`: **{summary['OVERALL_STATUS']}**\n- frozen K4 decisions/membership changed: `False`\n- complete diagnostic cohort: `423`\n- K4 formal counts: `3 PASS / 0 UNRESOLVED / 420 FAIL`\n- representative set: presentation-only; zero membership authority\n- top-k/Pareto/proxy/new threshold/refit/compensation: `False` for formal selection\n- semantic output digest: `{summary['semantic_output_digest']}`\n- next action: `{summary['NEXT_ACTION']}`\n'''
    text = path.read_text(encoding="utf-8")
    text = text.split(marker)[0].rstrip() + "\n\n" + block if marker in text else text.rstrip() + "\n\n" + block
    write_text(path, text)


def run(root: Path) -> int:
    started = time.perf_counter(); root = root.resolve(); home = root / "phases/p13/coefficient_law_raw_xt"
    cfg_path = home / "configs/p13_s3_k5_protocol.json"; cfg = load_json(cfg_path)
    s3run, k4, paths, entry = _verify_entry(root, cfg)
    k5 = s3run / "K5_post_sealed_diagnostics"; k5.mkdir(exist_ok=True); write_json(k5 / "K5_ENTRY_AND_K4_REVIEW.json", entry)
    if entry["status"] != "PASS":
        write_text(s3run / "K5_OVERALL_STATUS.txt", "FAIL\n"); write_text(s3run / "K5_NEXT_ACTION.txt", "BLOCK_P13_S3_K5_PARENT_EVIDENCE_REPAIR\n"); return 2
    decision = _by_id(paths["decision_map"]); k1 = _by_id(paths["k1"]); k2 = _by_id(paths["k2"])
    k0 = _by_id(paths["k0"]); development = _by_id(paths["s2k6"])
    refmap = _reference_map(load_json(paths["k3ref"])); controls = _control_map(k4)
    threshold = float(cfg["diagnostic_contract"]["response_threshold_for_margin_only"]); fine = int(cfg["expected_k4"]["final_fidelity_pair"][1])
    rows = []
    for index, measurement in enumerate(iter_jsonl(paths["measurements"]), 1):
        sid = measurement["scientific_branch_id"]
        if any(sid not in source for source in (decision, k1, k2, k0, development)):
            raise RuntimeError(f"missing K5 parent evidence for {sid}")
        response = final_response_record(measurement, fine, refmap, threshold); drow = decision[sid]
        if response["decision"] != drow["decision"]:
            raise RuntimeError(f"independent K4 decision reproduction mismatch {sid}")
        sealed = k1[sid]["SEALED_G65"]; dev = development[sid]
        vector_hash = hashlib.sha256(canonical_bytes([response["cases"][key] for key in sorted(response["cases"])] )).hexdigest()
        row = {
            "scientific_branch_id": sid, "membership_index": measurement["membership_index"], "arm": measurement["arm"],
            "paired_seed": measurement["paired_seed"], "proposal_index": measurement["proposal_index"], "structural_hash": measurement["structural_hash"],
            "exact_equivalence_class": k1[sid]["exact_equivalence_class"], "theta_hex": measurement["theta_hex"],
            "K4_decision": response["decision"], "K4_decision_immutable": True, "membership_authority": False,
            "SEALED_operator_family_ratio_G33": k1[sid]["SEALED_G33"]["family_ratio_to_identity"],
            "SEALED_operator_family_ratio_G65": sealed["family_ratio_to_identity"],
            "SEALED_operator_field_ratios_G65": sealed["field_ratios_to_identity"],
            "DEVELOPMENT_operator_family_ratio_G33": dev.get("DEV_family_ratio_G33"), "DEVELOPMENT_operator_family_ratio_G65": dev.get("DEV_family_ratio_G65"),
            "DEVELOPMENT_response_worst_nominal": dev.get("response_worst_nominal"), "DEVELOPMENT_response_worst_upper": dev.get("response_worst_upper"),
            "DEVELOPMENT_response_RMS_nominal": dev.get("response_RMS_nominal"), "DEVELOPMENT_response_decision": dev.get("K5_decision"),
            "SEALED_response_worst_nominal": response["worst_nominal"], "SEALED_response_worst_upper": response["worst_upper"],
            "SEALED_response_worst_lower": response["worst_lower"], "SEALED_response_RMS_nominal": response["RMS_nominal"],
            "SEALED_response_median_nominal": response["median_nominal"], "SEALED_response_margin_to_0p15": threshold - response["worst_upper"],
            "SEALED_response_cases": response["cases"], "SEALED_response_intervals": response["intervals"],
            "SEALED_response_worst_by_field": response["worst_by_field"], "failure_witness_keys": response["witness_keys"],
            "threshold_overlap_keys": response["threshold_overlap_keys"], "exact_serialized_response_vector_sha256": vector_hash,
            "max_coefficient_derivative_order_pair": k0[sid]["max_coefficient_derivative_order_pair"],
            "coefficient_ablation_delta_rel": dev.get("coefficient_ablation_delta_rel"),
            "pf0_theory_cosine": dev.get("pf0_theory_cosine"), "pf0_s_norm_ratio": dev.get("pf0_s_norm_ratio"),
            "pf0_tangent_relative_residual": dev.get("pf0_tangent_relative_residual"), "pf0_projection_relative_residual": dev.get("pf0_projection_relative_residual"),
        }
        rows.append(row)
        if index % int(cfg["runtime"]["progress_every_rows"]) == 0 or index == 423:
            print(f"[P13-S3-K5] stage=complete_join processed={index}/423 current={sid[:12]} elapsed={time.perf_counter()-started:.1f}s", flush=True)
    if len(rows) != 423 or len({row["scientific_branch_id"] for row in rows}) != 423:
        raise RuntimeError("K5 complete diagnostic cohort mismatch")
    recount = collections.Counter(row["K4_decision"] for row in rows)
    if recount != collections.Counter({"RESPONSE_PASS": 3, "RESPONSE_FAIL": 420}):
        raise RuntimeError(f"K5 reproduced decision count mismatch {recount}")
    case_keys = sorted(refmap)
    branch_path = k5 / cfg["outputs"]["branch_diagnostics"]
    with branch_path.open("w", encoding="utf-8") as handle:
        for row in rows: handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
    transition = {
        "status": "DESCRIPTIVE_ONLY", "complete_423": True,
        "response_crosstab": {"DEVELOPMENT_RESPONSE_PASS_to_SEALED_RESPONSE_PASS": 3, "DEVELOPMENT_RESPONSE_PASS_to_SEALED_RESPONSE_FAIL": 420, "DEVELOPMENT_RESPONSE_PASS_to_SEALED_RESPONSE_UNRESOLVED": 0},
        "DEVELOPMENT_response_worst_upper": qstats(row["DEVELOPMENT_response_worst_upper"] for row in rows),
        "SEALED_response_worst_upper": qstats(row["SEALED_response_worst_upper"] for row in rows),
        "SEALED_minus_DEVELOPMENT_response_worst_upper": qstats(row["SEALED_response_worst_upper"] - row["DEVELOPMENT_response_worst_upper"] for row in rows),
        "DEV_SEALED_response_spearman": spearman(rows, "DEVELOPMENT_response_worst_upper", "SEALED_response_worst_upper"),
        "DEV_SEALED_operator_spearman": spearman(rows, "DEVELOPMENT_operator_family_ratio_G65", "SEALED_operator_family_ratio_G65"),
        "membership_authority": False,
    }; write_json(k5 / cfg["outputs"]["development_sealed_transitions"], transition)
    witness_counts = collections.Counter(key for row in rows for key in row["failure_witness_keys"])
    field_counts = collections.Counter(key.split("::", 1)[0] for row in rows for key in row["failure_witness_keys"])
    witness = {"status": "COMPLETE_420_FAIL_WITNESS_CENSUS", "clear_fail_branch_count": 420, "total_clear_fail_witnesses": sum(witness_counts.values()), "by_case": dict(sorted(witness_counts.items())), "by_field": dict(sorted(field_counts.items())), "branches_with_threshold_overlap_in_nondecisive_cases": sum(bool(row["threshold_overlap_keys"]) for row in rows), "membership_authority": False}; write_json(k5 / cfg["outputs"]["failure_witness_census"], witness)
    structures = _structure_classes(rows); write_json(k5 / cfg["outputs"]["structure_equivalence"], structures)
    signatures = _failure_signature_classes(rows, case_keys); write_json(k5 / cfg["outputs"]["failure_signature_classes"], signatures)
    def group_summary(group: list[dict[str, Any]]) -> dict[str, Any]:
        counts = collections.Counter(row["K4_decision"] for row in group)
        return {"n": len(group), "decision_counts": dict(counts), "K4_PASS_fraction": counts.get("RESPONSE_PASS", 0) / len(group), "SEALED_response_worst_upper": qstats(row["SEALED_response_worst_upper"] for row in group)}
    seed = {"status": "CONDITIONAL_DESCRIPTIVE_ONLY", "scope": cfg["diagnostic_contract"]["seed_reliability_scope"], "by_paired_seed": {str(value): group_summary([row for row in rows if int(row["paired_seed"]) == value]) for value in sorted({int(row["paired_seed"]) for row in rows})}, "by_arm": {value: group_summary([row for row in rows if row["arm"] == value]) for value in sorted({row["arm"] for row in rows})}, "p_values_reported": False, "membership_authority": False}; write_json(k5 / cfg["outputs"]["seed_reliability"], seed)
    ablation = {"status": "CONTINUOUS_DESCRIPTIVE_ONLY_NO_POST_RESPONSE_BINS", "all_423": qstats(row["coefficient_ablation_delta_rel"] for row in rows), "by_K4_decision": {value: qstats(row["coefficient_ablation_delta_rel"] for row in rows if row["K4_decision"] == value) for value in ("RESPONSE_PASS", "RESPONSE_FAIL")}, "association_with_SEALED_response_worst_upper": spearman(rows, "coefficient_ablation_delta_rel", "SEALED_response_worst_upper"), "p_values_reported": False, "membership_authority": False}; write_json(k5 / cfg["outputs"]["coefficient_ablation"], ablation)
    xkeys = ["DEVELOPMENT_operator_family_ratio_G65", "SEALED_operator_family_ratio_G65", "DEVELOPMENT_response_worst_upper", "coefficient_ablation_delta_rel", "pf0_theory_cosine", "pf0_s_norm_ratio", "pf0_tangent_relative_residual", "pf0_projection_relative_residual", "max_coefficient_derivative_order_pair"]
    ykeys = ["SEALED_response_worst_nominal", "SEALED_response_worst_upper", "SEALED_response_RMS_nominal", "SEALED_response_margin_to_0p15"]
    associations = {"status": "DESCRIPTIVE_ONLY", "associations": {x: {y: spearman(rows, x, y) for y in ykeys} for x in xkeys}, "p_values_reported": False, "weighted_composite_score": False, "membership_authority": False}; write_json(k5 / cfg["outputs"]["operator_response_associations"], associations)
    case_effects = _case_control_effects(rows, controls, case_keys); write_json(k5 / cfg["outputs"]["case_control_effects"], case_effects)
    axes = list(cfg["diagnostic_contract"]["descriptive_pareto_axes"]); pareto_ids = _nondominated(rows, axes)
    pareto = {"status": "POST_DECISION_PRESENTATION_ONLY", "axes": axes, "orientation": "lower_is_better_on_all_axes", "epsilon_or_weighted_score": None, "nondominated_count": len(pareto_ids), "nondominated_scientific_branch_ids": pareto_ids, "K4_decision_counts_on_view": dict(collections.Counter(row["K4_decision"] for row in rows if row["scientific_branch_id"] in set(pareto_ids))), "formal_selection": False, "membership_authority": False}; write_json(k5 / cfg["outputs"]["descriptive_pareto"], pareto)
    pass_rows = sorted((row for row in rows if row["K4_decision"] == "RESPONSE_PASS"), key=lambda row: int(row["membership_index"]))
    fail_rows = [row for row in rows if row["K4_decision"] == "RESPONSE_FAIL"]
    nearest_fail = min(fail_rows, key=lambda row: (float(row["SEALED_response_worst_lower"]), row["scientific_branch_id"]))
    modal_medoid_id = signatures["modal_class"]["medoid_scientific_branch_id"]
    selected: list[tuple[dict[str, Any], str]] = [(row, "ALL_FORMAL_K4_RESPONSE_PASS") for row in pass_rows]
    selected.append((nearest_fail, "BOUNDARY_CONTRAST_NEAREST_CLEAR_FAIL"))
    modal_row = next(row for row in rows if row["scientific_branch_id"] == modal_medoid_id)
    if modal_row["scientific_branch_id"] != nearest_fail["scientific_branch_id"]: selected.append((modal_row, "MODAL_FAILURE_SIGNATURE_MEDOID"))
    locators = read_jsonl(paths["s1clear"])
    dossiers = [_dossier(root, locators, row, k1[row["scientific_branch_id"]], role) for row, role in selected]
    exemplars = {"status": "POST_DECISION_PAPER_PRESENTATION_ONLY", "selection_rule": cfg["diagnostic_contract"]["paper_exemplars"], "all_formal_K4_RESPONSE_PASS_included": True, "formal_PASS_count": 3, "dossiers": dossiers, "changes_K4_membership": False, "membership_authority": False}; write_json(k5 / cfg["outputs"]["paper_exemplars"], exemplars)
    immutable = {"status": "PASS", "K4_decision_map_sha256": cfg["expected_k4"]["decision_map_sha256"], "K4_pass_membership_sha256": cfg["expected_k4"]["pass_membership_sha256"], "K4_counts": {"RESPONSE_PASS": 3, "RESPONSE_UNRESOLVED": 0, "RESPONSE_FAIL": 420}, "K5_reproduced_counts": {"RESPONSE_PASS": 3, "RESPONSE_UNRESOLVED": 0, "RESPONSE_FAIL": 420}, "decision_map_modified": False, "pass_membership_modified": False}; write_json(k5 / "K5_K4_DECISION_MEMBERSHIP_IMMUTABILITY_AUDIT.json", immutable)
    guard = {"SEALED_FINAL_COEF": "OPENED_K1_READ_ONLY", "SEALED_FINAL_RESPONSE": "OPENED_K3_K4_READ_ONLY", "K4_decision_map_modified": False, "K4_pass_membership_modified": False, "candidate_refit": False, "branch_reselection": False, "proxy_filter": False, "top_k_or_Pareto_formal_selection": False, "presentation_set_membership_authority": False, "weighted_composite_score": False, "new_response_threshold": False, "new_fidelity_escalation": False, "amplitude_compensation": False}; write_json(k5 / "K5_DATA_BOUNDARY_GUARD.json", guard)
    runtime = {"python": sys.version, "platform": platform.platform(), "mode": cfg["runtime"]["mode"], "OMP_NUM_THREADS": os.environ.get("OMP_NUM_THREADS"), "MKL_NUM_THREADS": os.environ.get("MKL_NUM_THREADS"), "OPENBLAS_NUM_THREADS": os.environ.get("OPENBLAS_NUM_THREADS"), "NUMEXPR_NUM_THREADS": os.environ.get("NUMEXPR_NUM_THREADS"), "elapsed_seconds": time.perf_counter() - started}; write_json(k5 / "K5_RUNTIME_ENVIRONMENT.json", runtime)
    source = _source_manifest(root, cfg_path); write_json(k5 / "K5_SOURCE_MANIFEST.json", source)
    output_hashes = {name: sha256_file(k5 / filename) for name, filename in cfg["outputs"].items()}
    basis = {"K4_semantic_output_digest": cfg["expected_k4"]["semantic_output_digest"], "K4_decision_map_sha256": cfg["expected_k4"]["decision_map_sha256"], "K4_pass_membership_sha256": cfg["expected_k4"]["pass_membership_sha256"], "complete_branch_diagnostics_sha256": sha256_file(branch_path), "output_sha256": output_hashes, "immutable": immutable, "guard": guard, "source_manifest": source}
    semantic = hashlib.sha256(canonical_bytes(basis)).hexdigest(); write_json(k5 / "K5_SEMANTIC_OUTPUT_DIGEST.json", {"semantic_output_digest": semantic, "basis": basis})
    summary = {"OVERALL_STATUS": "PASS", "NEXT_ACTION": cfg["next_action_on_pass"], "complete_diagnostic_cohort": 423, "frozen_K4_decision_counts": {"RESPONSE_PASS": 3, "RESPONSE_UNRESOLVED": 0, "RESPONSE_FAIL": 420}, "K4_decisions_immutable": True, "K4_pass_membership_immutable": True, "formal_response_pass_count": 3, "formal_response_pass_structural_hash_count": len({row["structural_hash"] for row in pass_rows}), "structural_hash_class_count": structures["structural_hash_class_count"], "exact_equivalence_class_count": structures["exact_equivalence_class_count"], "failure_signature_class_count": signatures["class_count"], "descriptive_pareto_count": len(pareto_ids), "paper_exemplar_count": len(dossiers), "presentation_set_membership_authority": False, "top_k_or_Pareto_formal_selection": False, "proxy_filter": False, "candidate_refit": False, "new_response_threshold": False, "branch_diagnostics": str(branch_path.relative_to(root)), "branch_diagnostics_sha256": sha256_file(branch_path), "semantic_output_digest": semantic, "authoritative_S3_run": str(s3run.relative_to(root))}; write_json(k5 / "K5_SCIENTIFIC_SUMMARY.json", summary)
    write_text(s3run / "K5_OVERALL_STATUS.txt", "PASS\n"); write_text(s3run / "K5_NEXT_ACTION.txt", cfg["next_action_on_pass"] + "\n")
    write_text(home / "runs/LATEST_P13_S3_K5_RUN.txt", str(s3run.relative_to(root)) + "\n"); _update_context(root, summary)
    print(f"[P13-S3-K5] OVERALL_STATUS=PASS rows=423 K4_PASS=3 K4_UNRESOLVED=0 K4_FAIL=420 elapsed={time.perf_counter()-started:.1f}s", flush=True)
    print(f"[P13-S3-K5] NEXT_ACTION={cfg['next_action_on_pass']}", flush=True); return 0


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--project-root", required=True); args = parser.parse_args(); return run(Path(args.project_root))


if __name__ == "__main__":
    raise SystemExit(main())
