from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import sys
from pathlib import Path
from typing import Any

from .coefficients import canonical_json_bytes, sha256_bytes


def _sha(path: Path, block: int = 8 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(block), b""):
            h.update(b)
    return h.hexdigest()


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                out.append(json.loads(line))
    return out


def _count_jsonl(path: Path) -> int:
    with path.open("rb") as f:
        return sum(1 for x in f if x.strip())


def _resolve_marker(root: Path, rel_marker: str) -> Path:
    p = root / rel_marker
    if not p.is_file():
        raise FileNotFoundError(f"missing marker: {p}")
    rel = p.read_text(encoding="utf-8").strip()
    target = root / rel
    if not target.exists():
        raise FileNotFoundError(f"marker target missing: {target}")
    return target


def _component_consensus(s33: str, s65: str) -> str:
    if s33 == "CLEAR_PASS" and s65 == "CLEAR_PASS":
        return "CLEAR_PASS"
    if s33 == "CLEAR_FAIL" and s65 == "CLEAR_FAIL":
        return "CLEAR_FAIL"
    return "UNRESOLVED_NUMERICAL_BOUNDARY_OR_GRID_DISAGREEMENT"


def _final_decision(component_statuses: list[str], valid33: bool = True, valid65: bool = True,
                    resolved33: bool = True, resolved65: bool = True) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if not valid33 or not valid65:
        reasons.append("DEV_F0_F4_INVALID_ON_PREREGISTERED_GRID")
        return "OPERATOR_TRANSFER_FAIL", reasons
    if not resolved33 or not resolved65:
        reasons.append("REQUIRED_OPERATOR_QUANTITY_UNRESOLVED")
        return "OPERATOR_TRANSFER_UNRESOLVED", reasons
    if any(x == "CLEAR_FAIL" for x in component_statuses):
        reasons.append("AT_LEAST_ONE_HARD_GATE_COMPONENT_CLEAR_FAIL_ON_BOTH_GRIDS")
        return "OPERATOR_TRANSFER_FAIL", reasons
    if any(x != "CLEAR_PASS" for x in component_statuses):
        reasons.append("HARD_GATE_COMPONENT_NUMERICAL_BOUNDARY_OR_GRID_DISAGREEMENT")
        return "OPERATOR_TRANSFER_UNRESOLVED", reasons
    reasons.append("ALL_FIVE_HARD_GATE_COMPONENTS_CLEAR_PASS_ON_G33_AND_G65")
    return "OPERATOR_TRANSFER_PASS", reasons


def _quantiles(vals: list[float]) -> dict[str, float | None]:
    if not vals:
        return {k: None for k in ["min", "p10", "median", "p90", "p99", "max"]}
    import numpy as np
    a = np.asarray(vals, dtype=float)
    return {
        "min": float(np.min(a)), "p10": float(np.quantile(a, 0.10)),
        "median": float(np.median(a)), "p90": float(np.quantile(a, 0.90)),
        "p99": float(np.quantile(a, 0.99)), "max": float(np.max(a)),
    }


def _verify_entry(root: Path, cfg: dict[str, Any]) -> tuple[Path, Path, list[dict[str, Any]], dict[str, Any]]:
    checks: dict[str, Any] = {}
    active = root / cfg["active_context"]
    checks["active_context_sha"] = active.is_file() and _sha(active) == cfg["active_context_sha256"]

    k1run = _resolve_marker(root, cfg["k1_run_marker"])
    k1 = k1run / "K1_zero_shot_operator_transfer"
    exp = cfg["expected_k1"]
    checks["K1_status"] = (k1run / "K1_OVERALL_STATUS.txt").is_file() and (k1run / "K1_OVERALL_STATUS.txt").read_text().strip() == exp["overall_status"]
    checks["K1_next"] = (k1run / "K1_NEXT_ACTION.txt").is_file() and (k1run / "K1_NEXT_ACTION.txt").read_text().strip() == exp["next_action"]

    summary = _load_json(k1 / "K1_SCIENTIFIC_SUMMARY.json")
    aggregate = _load_json(k1 / "K1_OPERATOR_TRANSFER_AGGREGATE.json")
    guard = _load_json(k1 / "K1_DATA_BOUNDARY_GUARD.json")
    handoff = _load_json(k1 / "K1_ADJUDICATION_HANDOFF_LOCK.json")
    sem = _load_json(k1 / "K1_SEMANTIC_OUTPUT_DIGEST.json")
    measurements = k1 / "K1_OPERATOR_TRANSFER_MEASUREMENTS.jsonl"

    checks["K1_semantic_digest"] = sem.get("semantic_output_digest") == exp["semantic_output_digest"] == summary.get("semantic_output_digest")
    checks["K0_semantic_chain"] = sem.get("basis", {}).get("K0_semantic_output_digest") == exp["k0_semantic_output_digest"]
    checks["K1_measurement_sha"] = measurements.is_file() and _sha(measurements) == exp["measurements_sha256"] == summary.get("K1_measurements_sha256")
    checks["K1_measurement_count"] = measurements.is_file() and _count_jsonl(measurements) == int(exp["measurement_count"])
    checks["K1_complete_2307"] = aggregate.get("complete_measurements") == 2307 and handoff.get("complete_2307") is True
    checks["K1_no_measurement_unresolved"] = aggregate.get("numerical_or_operator_unresolved") == exp["numerical_or_operator_unresolved"]
    checks["K1_all_F4_G33"] = aggregate.get("all_DEV_fields_F4_G33_count") == exp["all_DEV_fields_F4_G33_count"]
    checks["K1_all_F4_G65"] = aggregate.get("all_DEV_fields_F4_G65_count") == exp["all_DEV_fields_F4_G65_count"]
    checks["K1_family_discrepancy_census"] = aggregate.get("branches_family_discrepancy_gt_tau_num") == exp["family_discrepancy_gt_tau_num_count"]
    checks["K1_per_field_discrepancy_census"] = aggregate.get("branches_max_per_field_discrepancy_gt_tau_num") == exp["max_per_field_discrepancy_gt_tau_num_count"]
    checks["K1_tau_ambiguity_only"] = abs(float(handoff.get("tau_num", math.nan)) - 0.005) < 1e-15 and handoff.get("tau_role") == "ambiguity_only"
    checks["K1_zero_refit"] = handoff.get("same_AST_theta_gauge_zero_refit") is True and guard.get("candidate_refit") is False and guard.get("branch_reselection") is False
    checks["K1_no_shortlist"] = guard.get("shortlist") is False
    checks["K1_response_unopened"] = guard.get("DEVELOPMENT_RESPONSE") == "SEALED_COMMITTED_UNOPENED" and guard.get("response_stage_blocked") is True
    checks["K1_sealed_unopened"] = guard.get("SEALED_FINAL_COEF") == "SEALED_COMMITTED_UNOPENED" and guard.get("SEALED_FINAL_RESPONSE") == "SEALED_COMMITTED_UNOPENED"
    checks["K1_no_historical_response"] = guard.get("historical_response_information_read") is False
    checks["K1_final_decision_not_preassigned"] = summary.get("K1_final_III_B_decision_frozen") is False and handoff.get("K1_assigns_final_III_B_decision") is False

    for key in ("clear_membership", "unresolved_reference"):
        lock = cfg[key]
        p = root / lock["path"]
        checks[f"{key}_sha"] = p.is_file() and _sha(p) == lock["sha256"]
        checks[f"{key}_count"] = p.is_file() and _count_jsonl(p) == int(lock["count"])
    checks["80_not_S2_eligible"] = cfg["unresolved_reference"]["s2_eligible"] is False

    rows = _read_jsonl(measurements) if measurements.is_file() else []
    checks["K1_unique_scientific_ids"] = len(rows) == 2307 and len({r.get("scientific_branch_id") for r in rows}) == 2307
    checks["K1_all_zero_refit_rows"] = len(rows) == 2307 and all(r.get("same_AST_theta_gauge_zero_refit") is True for r in rows)
    checks["K1_no_K2_authority_rows"] = len(rows) == 2307 and all(r.get("K2_final_decision_authority") is False for r in rows)

    fam_gt = sum(1 for r in rows if float(r["G33_G65_numerical_fidelity"]["family_relative_J_discrepancy"]) > 0.005)
    field_gt = sum(1 for r in rows if float(r["G33_G65_numerical_fidelity"]["max_per_field_relative_J_discrepancy"]) > 0.005)
    checks["recomputed_family_discrepancy_count"] = fam_gt == exp["family_discrepancy_gt_tau_num_count"]
    checks["recomputed_per_field_discrepancy_count"] = field_gt == exp["max_per_field_discrepancy_gt_tau_num_count"]

    status = "PASS" if all(bool(v) for v in checks.values()) else "FAIL"
    review = {
        "status": status,
        "checks": checks,
        "K1_semantic_output_digest": exp["semantic_output_digest"],
        "K1_measurements_sha256": exp["measurements_sha256"],
        "complete_measurements": len(rows),
        "family_discrepancy_gt_tau_num_count": fam_gt,
        "max_per_field_discrepancy_gt_tau_num_count": field_gt,
        "fidelity_conclusion": cfg["k1_fidelity_review_lock"],
        "scientific_interpretation": "K1 shows no material cohort-wide G33/G65 fidelity inconsistency requiring a new repair before K2. No candidate-specific rescue, new fraction trigger, or tau widening is introduced."
    }
    return k1run, k1, rows, review


def _adjudicate_row(row: dict[str, Any]) -> dict[str, Any]:
    g33 = row["DEV_G33"]
    g65 = row["DEV_G65"]
    s33 = g33.get("frozen_boundary_statuses")
    s65 = g65.get("frozen_boundary_statuses")
    valid33 = bool(g33.get("all_F0_F4_valid"))
    valid65 = bool(g65.get("all_F0_F4_valid"))
    resolved33 = bool(g33.get("all_J_resolved")) and isinstance(s33, list) and len(s33) == 5
    resolved65 = bool(g65.get("all_J_resolved")) and isinstance(s65, list) and len(s65) == 5
    if resolved33 and resolved65:
        comp = [_component_consensus(a, b) for a, b in zip(s33, s65)]
    else:
        comp = ["UNRESOLVED_NUMERICAL_BOUNDARY_OR_GRID_DISAGREEMENT"] * 5
    final, reasons = _final_decision(comp, valid33, valid65, resolved33, resolved65)
    return {
        "membership_index": int(row["membership_index"]),
        "scientific_branch_id": row["scientific_branch_id"],
        "arm": row["arm"],
        "paired_seed": int(row["paired_seed"]),
        "proposal_index": int(row["proposal_index"]),
        "structural_hash": row["structural_hash"],
        "exact_equivalence_class": row["exact_equivalence_class"],
        "same_AST_theta_gauge_zero_refit": bool(row.get("same_AST_theta_gauge_zero_refit")),
        "G33_boundary_statuses": s33,
        "G65_boundary_statuses": s65,
        "component_order": ["family", "field_1", "field_2", "field_3", "field_4"],
        "component_consensus": comp,
        "III_B_decision": final,
        "decision_reasons": reasons,
        "DEV_G33_family_ratio_to_identity": g33.get("family_ratio_to_identity"),
        "DEV_G65_family_ratio_to_identity": g65.get("family_ratio_to_identity"),
        "DEV_G33_field_ratios_to_identity": g33.get("field_ratios_to_identity"),
        "DEV_G65_field_ratios_to_identity": g65.get("field_ratios_to_identity"),
        "rho_transfer_G33_descriptive": row.get("rho_transfer"),
        "rho_transfer_membership_authority": False,
        "K2_final_decision_authority": True
    }


def _update_context(root: Path, summary: dict[str, Any]) -> None:
    p = root / "P13_S2_ROLLING_CONTEXT.md"
    if not p.exists():
        _write_text(p, "# P13 S2 Rolling Execution Context\n\n**Role:** REFERENCE / HANDOFF living record. The unique ACTIVE protocol remains `P13_COMPREHENSIVE_CONTEXT_S2_ENTRY_PF0_LOCK_20260831.md`.\n\n")
    text = p.read_text(encoding="utf-8")
    marker = "<!-- S2_K2_FORMAL_RESULT -->"
    c = summary["decision_counts"]
    block = f'''{marker}\n## S2-K2 — III-B adjudication and response-entry lock\n\n- `OVERALL_STATUS`: **{summary['OVERALL_STATUS']}**\n- K1 embedded evidence review: `PASS`\n- complete III-B census: `{summary['complete_census']}/2307`\n- clear `OPERATOR_TRANSFER_PASS`: `{c['OPERATOR_TRANSFER_PASS']}`\n- `OPERATOR_TRANSFER_UNRESOLVED`: `{c['OPERATOR_TRANSFER_UNRESOLVED']}`\n- `OPERATOR_TRANSFER_FAIL`: `{c['OPERATOR_TRANSFER_FAIL']}`\n- all clear PASS branches enter response eligibility: `True`\n- top-k/Pareto/percentile/target-survivor narrowing: `False`\n- DEVELOPMENT response opened: `False`\n- SEALED coefficient/response opened: `False`\n- response stage remains blocked pending explicit K3 protocol confirmation: `{summary['response_stage_blocked']}`\n- semantic output digest: `{summary['semantic_output_digest']}`\n- next action: `{summary['NEXT_ACTION']}`\n'''
    if marker in text:
        text = text.split(marker)[0].rstrip() + "\n\n" + block
    else:
        text = text.rstrip() + "\n\n" + block
    _write_text(p, text)


def run(root: Path) -> int:
    root = root.resolve()
    home = root / "phases/p13/coefficient_law_raw_xt"
    cfgp = home / "configs/p13_s2_k2_protocol.json"
    cfg = _load_json(cfgp)
    k1run, k1, rows, review = _verify_entry(root, cfg)
    k2 = k1run / "K2_III_B_adjudication_response_entry_lock"
    k2.mkdir(exist_ok=True)
    _write_json(k2 / "K2_ENTRY_AND_K1_EVIDENCE_REVIEW.json", review)
    if review["status"] != "PASS":
        _write_text(k1run / "K2_OVERALL_STATUS.txt", "FAIL\n")
        _write_text(k1run / "K2_NEXT_ACTION.txt", "BLOCK_P13_S2_AND_REPAIR_K1_K2_PROVENANCE\n")
        print("[P13-S2-K2] OVERALL_STATUS=FAIL", flush=True)
        return 2

    decisions = [_adjudicate_row(r) for r in rows]
    if len(decisions) != 2307 or len({r["scientific_branch_id"] for r in decisions}) != 2307:
        raise RuntimeError("K2 complete 2307 decision census required")

    dmap = k2 / cfg["outputs"]["complete_decision_map"]
    with dmap.open("w", encoding="utf-8") as f:
        for r in decisions:
            f.write(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n")

    counts = {x: 0 for x in ["OPERATOR_TRANSFER_PASS", "OPERATOR_TRANSFER_UNRESOLVED", "OPERATOR_TRANSFER_FAIL"]}
    for r in decisions:
        counts[r["III_B_decision"]] += 1

    pass_rows = [r for r in decisions if r["III_B_decision"] == "OPERATOR_TRANSFER_PASS"]
    pass_path = k2 / cfg["outputs"]["response_eligible_membership"]
    with pass_path.open("w", encoding="utf-8") as f:
        for r in pass_rows:
            f.write(json.dumps({
                "membership_index": r["membership_index"],
                "scientific_branch_id": r["scientific_branch_id"],
                "S1_clear_membership_path": cfg["clear_membership"]["path"],
                "K2_decision_map_path": str(dmap.relative_to(root)),
                "K2_decision": "OPERATOR_TRANSFER_PASS"
            }, sort_keys=True, separators=(",", ":")) + "\n")

    unresolved = [r for r in decisions if r["III_B_decision"] == "OPERATOR_TRANSFER_UNRESOLVED"]
    fail = [r for r in decisions if r["III_B_decision"] == "OPERATOR_TRANSFER_FAIL"]
    grid_disagreement = sum(1 for r in decisions if any(x == "UNRESOLVED_NUMERICAL_BOUNDARY_OR_GRID_DISAGREEMENT" for x in r["component_consensus"]))
    family_g65 = [float(r["DEV_G65_family_ratio_to_identity"]) for r in decisions if r["DEV_G65_family_ratio_to_identity"] is not None]
    pass_family_g65 = [float(r["DEV_G65_family_ratio_to_identity"]) for r in pass_rows if r["DEV_G65_family_ratio_to_identity"] is not None]

    per_arm: dict[str, dict[str, int]] = {}
    for r in decisions:
        a = r["arm"]
        per_arm.setdefault(a, {x: 0 for x in counts})
        per_arm[a][r["III_B_decision"]] += 1

    decision_summary = {
        "complete_census": 2307,
        "decision_counts": counts,
        "per_arm_counts_descriptive_only": per_arm,
        "branches_with_any_component_grid_boundary_or_disagreement": grid_disagreement,
        "DEV_G65_family_ratio_all": _quantiles(family_g65),
        "DEV_G65_family_ratio_clear_PASS": _quantiles(pass_family_g65),
        "hard_gate_components": 5,
        "new_numeric_thresholds": [],
        "top_k_or_Pareto_used": False,
        "target_survivor_count_used": False,
        "PF0_or_historical_response_filter_used": False,
        "all_clear_PASS_enter_response_eligibility": True
    }
    _write_json(k2 / "K2_III_B_DECISION_SUMMARY.json", decision_summary)

    response_blocked = True
    if counts["OPERATOR_TRANSFER_PASS"] > 0:
        next_action = cfg["next_if_nonempty_PASS"]
        response_path = "NONEMPTY_CLEAR_PASS_COHORT_RESPONSE_ELIGIBLE_BUT_BLOCKED_PENDING_K3"
    else:
        next_action = cfg["next_if_zero_PASS"]
        response_path = "ZERO_CLEAR_PASS_STOP_RESPONSE_PATH"

    response_manifest = {
        "status": "PASS",
        "response_eligible_count": counts["OPERATOR_TRANSFER_PASS"],
        "response_eligible_membership_path": str(pass_path.relative_to(root)),
        "response_eligible_membership_sha256": _sha(pass_path),
        "complete_K2_decision_map_path": str(dmap.relative_to(root)),
        "complete_K2_decision_map_sha256": _sha(dmap),
        "S1_clear_membership_path": cfg["clear_membership"]["path"],
        "S1_clear_membership_sha256": cfg["clear_membership"]["sha256"],
        "scientific_branch_identity_preserved": True,
        "exact_equivalence_execution_sharing_only": True,
        "all_clear_PASS_included": True,
        "response_path": response_path,
        "DEVELOPMENT_RESPONSE": "SEALED_COMMITTED_UNOPENED",
        "response_stage_blocked_pending_K3": response_blocked
    }
    _write_json(k2 / cfg["outputs"]["response_eligible_manifest"], response_manifest)

    guard = {
        "status": "PASS",
        "DEVELOPMENT_COEF": "OPENED_K0_READ_IN_PLACE",
        "DEVELOPMENT_RESPONSE": "SEALED_COMMITTED_UNOPENED",
        "SEALED_FINAL_COEF": "SEALED_COMMITTED_UNOPENED",
        "SEALED_FINAL_RESPONSE": "SEALED_COMMITTED_UNOPENED",
        "candidate_refit": False,
        "branch_reselection": False,
        "historical_response_information_read": False,
        "PF0_membership_authority": False,
        "shortlist": False,
        "response_stage_blocked": True
    }
    _write_json(k2 / "K2_DATA_BOUNDARY_GUARD.json", guard)

    response_lock = {
        "status": "PASS",
        "III_B_decision_map_frozen": True,
        "clear_PASS_count": counts["OPERATOR_TRANSFER_PASS"],
        "entire_clear_PASS_cohort_response_eligible": True,
        "top_k_after_III_B_forbidden": True,
        "DEVELOPMENT_RESPONSE_opened": False,
        "K3_currently_blocked_pending_explicit_user_confirmation": True,
        "K3_must_freeze": [
            "exact causal/source-domain response fidelity ladder",
            "candidate-independent reference refinement/uncertainty semantics",
            "cohort-wide candidate fidelity escalation",
            "identity + frozen NULL control-first execution and stopping semantics",
            "NONDISCRIMINATIVE_ABSOLUTE_GATE",
            "SEALED preservation policy if DEVELOPMENT absolute response gate is nondiscriminative"
        ],
        "NEXT_ACTION": next_action
    }
    _write_json(k2 / "K2_RESPONSE_ENTRY_LOCK.json", response_lock)

    sources = [cfgp, Path(__file__).resolve(), home / "scripts/run_p13_s2_k2.sh", home / "scripts/package_p13_s2_k2_audit.sh", home / "tests/test_p13_s2_k2.py", home / "docs/P13_S2_K2_III_B_ADJUDICATION_AND_RESPONSE_ENTRY_LOCK.md", root / cfg["active_context"]]
    source_manifest = {"files": [{"path": str(p.resolve().relative_to(root.resolve())), "bytes": p.stat().st_size, "sha256": _sha(p)} for p in sources if p.is_file()]}
    _write_json(k2 / "K2_SOURCE_MANIFEST.json", source_manifest)
    runtime = {
        "python": sys.version,
        "platform": platform.platform(),
        "parallelism": "single_coordinator",
        "OMP_NUM_THREADS": os.environ.get("OMP_NUM_THREADS"),
        "MKL_NUM_THREADS": os.environ.get("MKL_NUM_THREADS"),
        "OPENBLAS_NUM_THREADS": os.environ.get("OPENBLAS_NUM_THREADS"),
        "NUMEXPR_NUM_THREADS": os.environ.get("NUMEXPR_NUM_THREADS")
    }
    _write_json(k2 / "K2_RUNTIME_ENVIRONMENT.json", runtime)

    sem_basis = {
        "stage": "P13-S2-K2",
        "K1_semantic_output_digest": cfg["expected_k1"]["semantic_output_digest"],
        "K1_measurements_sha256": cfg["expected_k1"]["measurements_sha256"],
        "K1_evidence_review": review,
        "decision_map_sha256": _sha(dmap),
        "response_eligible_membership_sha256": _sha(pass_path),
        "decision_summary": decision_summary,
        "response_manifest": response_manifest,
        "data_boundary": guard,
        "response_entry_lock": response_lock,
        "source_manifest": source_manifest
    }
    sem = sha256_bytes(canonical_json_bytes(sem_basis))
    _write_json(k2 / "K2_SEMANTIC_OUTPUT_DIGEST.json", {"semantic_output_digest": sem, "basis": sem_basis})

    scientific_summary = {
        "OVERALL_STATUS": "PASS",
        "NEXT_ACTION": next_action,
        "authoritative_S2_run": str(k1run.relative_to(root)),
        "complete_census": 2307,
        "decision_counts": counts,
        "K1_embedded_evidence_review": "PASS",
        "K1_fidelity_repair_required_before_K2": False,
        "K2_decision_map": str(dmap.relative_to(root)),
        "K2_decision_map_sha256": _sha(dmap),
        "response_eligible_membership": str(pass_path.relative_to(root)),
        "response_eligible_membership_sha256": _sha(pass_path),
        "response_stage_blocked": True,
        "DEVELOPMENT_response_opened": False,
        "SEALED_opened": False,
        "semantic_output_digest": sem
    }
    _write_json(k2 / "K2_SCIENTIFIC_SUMMARY.json", scientific_summary)
    _write_text(k1run / "K2_OVERALL_STATUS.txt", "PASS\n")
    _write_text(k1run / "K2_NEXT_ACTION.txt", next_action + "\n")
    _write_text(home / "runs/LATEST_P13_S2_K2_RUN.txt", str(k1run.relative_to(root)) + "\n")
    _write_text(home / "runs/LATEST_P13_S2_RESPONSE_ELIGIBLE_INPUT.txt", str((k2 / cfg["outputs"]["response_eligible_manifest"]).relative_to(root)) + "\n")
    _update_context(root, scientific_summary)

    print("[P13-S2-K2] OVERALL_STATUS=PASS", flush=True)
    print(f"[P13-S2-K2] III_B counts PASS={counts['OPERATOR_TRANSFER_PASS']} UNRESOLVED={counts['OPERATOR_TRANSFER_UNRESOLVED']} FAIL={counts['OPERATOR_TRANSFER_FAIL']}", flush=True)
    print(f"[P13-S2-K2] semantic_output_digest={sem}", flush=True)
    print(f"[P13-S2-K2] NEXT_ACTION={next_action}", flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-root", type=Path, default=Path.cwd())
    args = ap.parse_args()
    return run(args.project_root)


if __name__ == "__main__":
    raise SystemExit(main())
