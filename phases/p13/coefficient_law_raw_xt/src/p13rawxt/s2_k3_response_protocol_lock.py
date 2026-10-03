from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sys
from pathlib import Path
from typing import Any


def sha256_file(path: Path, block: int = 8 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(block), b""):
            h.update(b)
    return h.hexdigest()


def canonical_json_bytes(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def count_jsonl(path: Path) -> int:
    with path.open("rb") as f:
        return sum(1 for line in f if line.strip())


def resolve_marker(root: Path, rel_marker: str) -> Path:
    marker = root / rel_marker
    if not marker.is_file():
        raise FileNotFoundError(f"missing marker: {marker}")
    rel = marker.read_text(encoding="utf-8").strip()
    target = root / rel
    if not target.exists():
        raise FileNotFoundError(f"marker target missing: {target}")
    return target


def response_interval(e_coarse: float, e_fine: float, reference_uncertainty: float) -> dict[str, float]:
    radius = abs(float(e_fine) - float(e_coarse)) + float(reference_uncertainty)
    nominal = float(e_fine)
    return {
        "nominal": nominal,
        "radius": radius,
        "lower": max(0.0, nominal - radius),
        "upper": nominal + radius,
    }


def classify_pair(e_coarse: float, e_fine: float, reference_uncertainty: float, threshold: float = 0.15) -> str:
    iv = response_interval(e_coarse, e_fine, reference_uncertainty)
    if iv["upper"] < threshold:
        return "CLEAR_PASS"
    if iv["lower"] > threshold:
        return "CLEAR_FAIL"
    return "UNRESOLVED"


def classify_branch(pair_intervals: list[dict[str, float]], threshold: float = 0.15) -> str:
    if len(pair_intervals) != 32:
        raise ValueError("formal branch classification requires all 32 response pairs")
    if any(float(x["lower"]) > threshold for x in pair_intervals):
        return "RESPONSE_FAIL"
    if all(float(x["upper"]) < threshold for x in pair_intervals):
        return "RESPONSE_PASS"
    return "RESPONSE_UNRESOLVED"


def reference_refinement_action(uncertainty: float, current_pair: tuple[int, int], ceiling: float = 0.02) -> str:
    if uncertainty <= ceiling:
        return "REFERENCE_CERTIFIED"
    ladder = {(513, 1025): "REFINE_TO_G2049", (1025, 2049): "REFINE_TO_G4097"}
    return ladder.get(tuple(current_pair), "REFERENCE_UNRESOLVED_AFTER_G2049_G4097")


def candidate_fidelity_action(any_unresolved: bool, current_fine_grid: int) -> str:
    if not any_unresolved:
        return "FREEZE_CURRENT_DECISIONS"
    if current_fine_grid == 257:
        return "ESCALATE_ALL_1955_PLUS_CONTROLS_TO_G513"
    if current_fine_grid == 513:
        return "ESCALATE_ALL_1955_PLUS_CONTROLS_TO_G1025"
    if current_fine_grid == 1025:
        return "PRESERVE_REMAINING_AS_UNRESOLVED"
    raise ValueError(f"unexpected candidate fine grid {current_fine_grid}")


def control_first_action(identity_decision: str, current_fine_grid: int) -> str:
    if identity_decision == "RESPONSE_PASS":
        return "NONDISCRIMINATIVE_ABSOLUTE_GATE_STOP_BEFORE_K5"
    if identity_decision == "RESPONSE_FAIL":
        return "DISCRIMINATIVE_ABSOLUTE_GATE_ALLOW_K4_COST_PREFLIGHT"
    if identity_decision != "RESPONSE_UNRESOLVED":
        raise ValueError(identity_decision)
    if current_fine_grid == 257:
        return "ESCALATE_IDENTITY_AND_NULL_TO_G513"
    if current_fine_grid == 513:
        return "ESCALATE_IDENTITY_AND_NULL_TO_G1025"
    if current_fine_grid == 1025:
        return "CONTROL_GATE_UNRESOLVED_STOP_BEFORE_K5"
    raise ValueError(f"unexpected control fine grid {current_fine_grid}")


def _check_file(root: Path, spec: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    p = root / spec["path"]
    ok = p.is_file() and sha256_file(p) == spec["sha256"]
    return ok, {"path": spec["path"], "sha256_expected": spec["sha256"], "sha256_actual": sha256_file(p) if p.is_file() else None, "bytes": p.stat().st_size if p.is_file() else None}


def _verify_public_commitments(pf1: dict[str, Any], cfg: dict[str, Any]) -> dict[str, bool]:
    out: dict[str, bool] = {}
    dev = pf1.get("DEVELOPMENT_response_commitment", {})
    expd = cfg["expected_public_commitments"]["development_response"]
    out["DEV_response_public_commitment_sha"] = dev.get("archive_sha256") == expd["archive_sha256"]
    out["DEV_response_public_commitment_bytes"] = dev.get("archive_bytes") == expd["archive_bytes"]
    out["DEV_response_public_semantic_digest"] = dev.get("payload_semantic_digest") == expd["payload_semantic_digest"]
    out["DEV_response_public_row_count"] = dev.get("row_count") == expd["row_count"] == 32
    out["DEV_response_private_not_copied"] = dev.get("private_payload_copied_into_active_tree") is False
    out["DEV_response_seed_not_exposed"] = dev.get("private_seed_material_exposed") is False
    sealed = pf1.get("SEALED_response_guard", {})
    exps = cfg["expected_public_commitments"]["sealed_response"]
    out["SEALED_response_public_commitment_sha"] = sealed.get("archive_sha256") == exps["archive_sha256"]
    out["SEALED_response_public_semantic_digest"] = sealed.get("payload_semantic_digest") == exps["payload_semantic_digest"]
    out["SEALED_response_public_row_count"] = sealed.get("row_count") == exps["row_count"] == 32
    out["SEALED_response_private_not_copied"] = sealed.get("private_payload_copied_into_active_tree") is False
    out["response_stage_blocked_at_entry"] = pf1.get("response_stage_blocked_at_entry") is True
    return out


def _verify_contract(contract: dict[str, Any]) -> dict[str, bool]:
    r = contract.get("response_contract", {})
    return {
        "contract_causal_source_domain": r.get("formal_solver") == "causal/source-domain",
        "contract_32_pairs": r.get("pairs_per_formal_stage") == 32 and r.get("cases_per_coefficient") == 8 and r.get("coefficients_per_formal_stage") == 4,
        "contract_threshold_0p15": abs(float(r.get("relative_error_threshold", -1.0)) - 0.15) < 1e-15,
        "contract_candidate_independent_reference": r.get("reference_refinement") == "candidate-independent",
        "contract_cohort_wide_candidate_escalation": r.get("candidate_fidelity_escalation") == "cohort-wide only",
        "contract_no_candidate_rescue": r.get("candidate_specific_rescue") is False,
    }


def run(project_root: Path) -> int:
    root = project_root.resolve()
    home = root / "phases/p13/coefficient_law_raw_xt"
    cfg_path = home / "configs/p13_s2_k3_protocol.json"
    cfg = load_json(cfg_path)
    checks: dict[str, bool] = {}
    provenance: dict[str, Any] = {}

    active = root / cfg["active_context"]
    checks["active_context_sha"] = active.is_file() and sha256_file(active) == cfg["active_context_sha256"]

    s2run = resolve_marker(root, cfg["k2_run_marker"])
    k2 = s2run / "K2_III_B_adjudication_response_entry_lock"
    exp = cfg["expected_k2"]
    checks["K2_status"] = (s2run / "K2_OVERALL_STATUS.txt").is_file() and (s2run / "K2_OVERALL_STATUS.txt").read_text().strip() == exp["overall_status"]
    checks["K2_next"] = (s2run / "K2_NEXT_ACTION.txt").is_file() and (s2run / "K2_NEXT_ACTION.txt").read_text().strip() == exp["next_action"]
    k2sem = load_json(k2 / "K2_SEMANTIC_OUTPUT_DIGEST.json")
    k2sum = load_json(k2 / "K2_SCIENTIFIC_SUMMARY.json")
    k2dec = load_json(k2 / "K2_III_B_DECISION_SUMMARY.json")
    k2guard = load_json(k2 / "K2_DATA_BOUNDARY_GUARD.json")
    k2manifest = load_json(k2 / "K2_RESPONSE_ELIGIBLE_INPUT_MANIFEST.json")
    checks["K2_semantic_digest"] = k2sem.get("semantic_output_digest") == exp["semantic_output_digest"] == k2sum.get("semantic_output_digest")
    checks["K2_complete_2307"] = k2dec.get("complete_census") == exp["complete_census"]
    counts = k2dec.get("decision_counts", {})
    checks["K2_counts"] = counts.get("OPERATOR_TRANSFER_PASS") == exp["pass_count"] and counts.get("OPERATOR_TRANSFER_UNRESOLVED") == exp["unresolved_count"] and counts.get("OPERATOR_TRANSFER_FAIL") == exp["fail_count"]
    checks["K2_no_proxy_narrowing"] = k2dec.get("top_k_or_Pareto_used") is False and k2dec.get("target_survivor_count_used") is False and k2dec.get("PF0_or_historical_response_filter_used") is False and k2dec.get("new_numeric_thresholds") == []
    checks["K2_all_clear_pass_eligible"] = k2manifest.get("all_clear_PASS_included") is True and k2manifest.get("response_eligible_count") == exp["pass_count"]
    dmap = root / k2manifest["complete_K2_decision_map_path"]
    eligible = root / k2manifest["response_eligible_membership_path"]
    checks["K2_decision_map_sha"] = dmap.is_file() and sha256_file(dmap) == exp["decision_map_sha256"] == k2manifest.get("complete_K2_decision_map_sha256")
    checks["K2_decision_map_count"] = dmap.is_file() and count_jsonl(dmap) == 2307
    checks["K2_eligible_sha"] = eligible.is_file() and sha256_file(eligible) == exp["response_eligible_membership_sha256"] == k2manifest.get("response_eligible_membership_sha256")
    checks["K2_eligible_count"] = eligible.is_file() and count_jsonl(eligible) == 1955
    checks["K2_response_unopened"] = k2guard.get("DEVELOPMENT_RESPONSE") == "SEALED_COMMITTED_UNOPENED" and k2manifest.get("DEVELOPMENT_RESPONSE") == "SEALED_COMMITTED_UNOPENED"
    checks["K2_sealed_unopened"] = k2guard.get("SEALED_FINAL_COEF") == "SEALED_COMMITTED_UNOPENED" and k2guard.get("SEALED_FINAL_RESPONSE") == "SEALED_COMMITTED_UNOPENED"
    checks["K2_zero_refit_no_reselection"] = k2guard.get("candidate_refit") is False and k2guard.get("branch_reselection") is False and k2guard.get("shortlist") is False

    for key in ("pf1_active_input_manifest", "response_contract_lock", "causal_solver_core", "null_control_builder", "null_control_instrument_lock"):
        ok, rec = _check_file(root, cfg[key])
        checks[f"{key}_sha"] = ok
        provenance[key] = rec

    pf1 = load_json(root / cfg["pf1_active_input_manifest"]["path"])
    checks.update(_verify_public_commitments(pf1, cfg))
    contract = load_json(root / cfg["response_contract_lock"]["path"])
    checks.update(_verify_contract(contract))

    null = pf1.get("null_control", {})
    identity = pf1.get("identity_control", {})
    checks["identity_control_frozen"] = identity.get("definition") == "deterministic identity map under frozen P13 grammar/gauge" and identity.get("response_control_only") is True
    checks["null_control_frozen"] = null.get("control_id") == "S0_FROZEN_COEFFICIENT_BLIND_NULL_CALIBRATION" and null.get("candidate_membership_authority") == "NONE" and null.get("forbidden_from_search") is True and null.get("response_control_only") is True
    checks["null_control_no_refit"] = "no refit" in str(null.get("reconstruction_rule", "")).lower()
    checks["null_control_builder_sha_in_manifest"] = null.get("pair_builder_source", {}).get("sha256") == cfg["null_control_builder"]["sha256"]
    checks["null_control_lock_sha_in_manifest"] = null.get("instrument_lock", {}).get("sha256") == cfg["null_control_instrument_lock"]["sha256"]

    # K3 intentionally does not stat/hash/open the private DEVELOPMENT/SEALED response archive paths.
    data_boundary = {
        "DEVELOPMENT_COEF": "OPENED_K0_READ_IN_PLACE",
        "DEVELOPMENT_RESPONSE": "SEALED_COMMITTED_UNOPENED",
        "SEALED_FINAL_COEF": "SEALED_COMMITTED_UNOPENED",
        "SEALED_FINAL_RESPONSE": "SEALED_COMMITTED_UNOPENED",
        "private_DEVELOPMENT_response_archive_stat_hash_open_extract": False,
        "private_SEALED_response_archive_stat_hash_open_extract": False,
        "response_payload_rows_read": False,
        "response_outcomes_read": False,
        "K3_is_protocol_lock_only": True,
        "K4_requires_post_K3_user_review": True,
    }

    k3 = s2run / "K3_response_protocol_fidelity_control_lock"
    k3.mkdir(parents=True, exist_ok=True)
    entry = {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "K2_semantic_output_digest": exp["semantic_output_digest"],
        "K2_decision_counts": counts,
        "response_eligible_count": 1955,
        "response_eligible_membership_sha256": exp["response_eligible_membership_sha256"],
        "provenance": provenance,
        "scientific_interpretation": "K2 provenance and full response-eligible membership are closed; K3 freezes response numerics and control semantics without opening response data."
    }
    write_json(k3 / "K3_ENTRY_AND_PROVENANCE_AUDIT.json", entry)
    write_json(k3 / "K3_DATA_BOUNDARY_GUARD.json", data_boundary)

    if entry["status"] != "PASS":
        write_text(s2run / "K3_OVERALL_STATUS.txt", "FAIL\n")
        write_text(s2run / "K3_NEXT_ACTION.txt", "BLOCK_P13_S2_RESPONSE_AND_REPAIR_K3_ENTRY_PROVENANCE\n")
        print("[P13-S2-K3] OVERALL_STATUS=FAIL", flush=True)
        return 2

    response_lock = {
        "stage": "P13-S2-K3",
        "status": "PASS",
        "response_data_opened": False,
        "complete_response_eligible_scientific_cohort": 1955,
        "scientific_response_contract": cfg["scientific_response_contract"],
        "reference_protocol": cfg["reference_protocol"],
        "candidate_control_fidelity_protocol": cfg["candidate_control_fidelity_protocol"],
        "exact_execution_sharing": cfg["exact_execution_sharing"],
        "causal_solver_core": cfg["causal_solver_core"],
        "threshold_changed": False,
        "candidate_membership_changed": False,
        "proxy_or_score_filter_added": False,
        "candidate_specific_rescue_added": False,
    }
    control_lock = {
        "stage": "P13-S2-K3",
        "status": "PASS",
        "response_data_opened": False,
        "identity_control": identity,
        "null_control": null,
        "control_first_protocol": cfg["control_first_protocol"],
        "nondiscrimination_and_sealed_policy": cfg["nondiscrimination_and_sealed_policy"],
        "new_relative_hard_gate": False,
        "absolute_gate": 0.15,
        "membership_authority": "NONE_FOR_CONTROLS"
    }
    write_json(k3 / "K3_RESPONSE_FIDELITY_PROTOCOL_LOCK.json", response_lock)
    write_json(k3 / "K3_CONTROL_FIRST_AND_NONDISCRIMINATION_LOCK.json", control_lock)

    source_paths = [cfg_path, Path(__file__), home / "scripts/run_p13_s2_k3.sh", home / "scripts/package_p13_s2_k3_audit.sh", home / "tests/test_p13_s2_k3.py", home / "docs/P13_S2_K3_RESPONSE_PROTOCOL_FIDELITY_AND_CONTROL_LOCK.md"]
    source_manifest = {"files": []}
    for p in source_paths:
        source_manifest["files"].append({"path": str(p.relative_to(root)), "bytes": p.stat().st_size, "sha256": sha256_file(p)})
    write_json(k3 / "K3_SOURCE_MANIFEST.json", source_manifest)
    runtime = {
        "python": sys.version,
        "platform": platform.platform(),
        "parallelism": "single_coordinator_protocol_lock",
        "OMP_NUM_THREADS": os.environ.get("OMP_NUM_THREADS"),
        "MKL_NUM_THREADS": os.environ.get("MKL_NUM_THREADS"),
        "OPENBLAS_NUM_THREADS": os.environ.get("OPENBLAS_NUM_THREADS"),
        "NUMEXPR_NUM_THREADS": os.environ.get("NUMEXPR_NUM_THREADS"),
        "heavy_future_default": cfg["runtime_policy"]["heavy_default"],
    }
    write_json(k3 / "K3_RUNTIME_ENVIRONMENT.json", runtime)

    semantic_basis = {
        "stage": "P13-S2-K3",
        "K2_semantic_output_digest": exp["semantic_output_digest"],
        "K2_response_eligible_membership_sha256": exp["response_eligible_membership_sha256"],
        "entry": entry,
        "response_lock": response_lock,
        "control_lock": control_lock,
        "data_boundary": data_boundary,
        "source_manifest": source_manifest,
    }
    sem_digest = hashlib.sha256(canonical_json_bytes(semantic_basis)).hexdigest()
    write_json(k3 / "K3_SEMANTIC_OUTPUT_DIGEST.json", {"semantic_output_digest": sem_digest, "basis": semantic_basis})
    next_action = cfg["next_action_on_pass"]
    summary = {
        "OVERALL_STATUS": "PASS",
        "K2_embedded_entry_review": "PASS",
        "K2_decision_counts": counts,
        "response_eligible_count": 1955,
        "DEVELOPMENT_response_opened": False,
        "SEALED_opened": False,
        "absolute_response_gate": 0.15,
        "reference_initial_pair": [513, 1025],
        "reference_uncertainty_ceiling": 0.02,
        "candidate_initial_pair": [129, 257],
        "candidate_uniform_escalation": [513, 1025],
        "candidate_specific_rescue": False,
        "control_first": True,
        "NONDISCRIMINATIVE_ABSOLUTE_GATE_policy_frozen": True,
        "current_SEALED_preserved_if_nondiscriminative": True,
        "top_k_or_Pareto": False,
        "semantic_output_digest": sem_digest,
        "NEXT_ACTION": next_action,
        "K4_response_opening_requires_user_review": True,
        "authoritative_S2_run": str(s2run.relative_to(root)),
    }
    write_json(k3 / "K3_SCIENTIFIC_SUMMARY.json", summary)
    write_text(s2run / "K3_OVERALL_STATUS.txt", "PASS\n")
    write_text(s2run / "K3_NEXT_ACTION.txt", next_action + "\n")
    write_text(root / "phases/p13/coefficient_law_raw_xt/runs/LATEST_P13_S2_K3_RUN.txt", str(s2run.relative_to(root)) + "\n")

    rolling = root / "P13_S2_ROLLING_CONTEXT.md"
    if not rolling.exists():
        write_text(rolling, "# P13 S2 Rolling Execution Context\n\n**Role:** REFERENCE / HANDOFF living record. ACTIVE protocol remains `P13_COMPREHENSIVE_CONTEXT_S2_ENTRY_PF0_LOCK_20260831.md`.\n")
    text = rolling.read_text(encoding="utf-8")
    marker = "<!-- S2_K3_FORMAL_RESULT -->"
    block = f'''{marker}\n## S2-K3 — response protocol/fidelity/control lock\n\n- `OVERALL_STATUS`: **PASS**\n- K2 frozen III-B census: `1955 PASS / 4 UNRESOLVED / 348 FAIL`\n- complete response-eligible cohort: `1955`; no top-k/Pareto narrowing\n- DEVELOPMENT response opened: `False`\n- SEALED opened: `False`\n- causal/source-domain response contract: frozen\n- original-PDE reference ladder: `G513/G1025`, candidate-independent self-refinement to `G2049/G4097` only if reference uncertainty exceeds `2%`\n- candidate/control ladder: `G129/G257 -> cohort-wide G513 -> cohort-wide G1025`; no candidate-specific rescue\n- response hard gate: unchanged `E_rel < 0.15` under certified interval semantics\n- control-first: identity + frozen S0 NULL; identity clear PASS triggers `NONDISCRIMINATIVE_ABSOLUTE_GATE` and stops before candidate response campaign\n- if nondiscriminative: current SEALED remains unopened; no new relative hard gate\n- semantic output digest: `{sem_digest}`\n- next action: `{next_action}`\n- K4 remains blocked pending post-K3 user review/confirmation.\n'''
    if marker in text:
        text = text.split(marker)[0].rstrip() + "\n\n" + block
    else:
        text = text.rstrip() + "\n\n" + block
    write_text(rolling, text)

    print(f"[P13-S2-K3] response_eligible=1955 response_opened=false semantic={sem_digest}", flush=True)
    print("[P13-S2-K3] OVERALL_STATUS=PASS", flush=True)
    print(f"[P13-S2-K3] NEXT_ACTION={next_action}", flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-root", required=True)
    args = ap.parse_args()
    return run(Path(args.project_root))


if __name__ == "__main__":
    raise SystemExit(main())
