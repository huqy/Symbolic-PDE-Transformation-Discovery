from __future__ import annotations

import argparse
import json
import math
import os
import platform
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from reproduce import s0_contract as s0
from typing import Any

import numpy as np
from scipy.optimize import minimize

from .calibration_instruments import build_identity_pair, build_null_capacity_pair, build_full_capacity_pair
from .family_evaluator import load_field_views
from .k2_qualification import (
    add_source_paths, canonical_json_bytes, json_load, sha256_bytes, sha256_path,
    write_json, write_text, _evaluator, run_parallel_tasks,
)
from .k2r2_attainment_repair import (
    EXPECTED_K2_SEMANTIC,
    _load_base_k2_protocol,
    fitter_reference_repair,
    common_burnin,
    residual_graft_retest,
    evaluator_fidelity_gate_r2,
    _source_manifest,
)
from .k2_qualification import causal_response_gate

EXPECTED_K2R2_SEMANTIC = "165d6e41e6b9b25e09456e0733a410f073cca422ebf2544e5d6980461bf07acc"
_POLISH: dict[str, Any] = {}


def _find_level(cap: dict[str, Any], level_name: str) -> dict[str, Any] | None:
    for level in cap.get("levels", []):
        if level.get("level") == level_name:
            return level
    return None


def _best_record_from_launches(obj: dict[str, Any]) -> dict[str, Any] | None:
    bests = []
    for row in obj.get("launches", []):
        b = row.get("best")
        if b is not None and int(b.get("stage_index", -1)) == 5 and b.get("J_princ") is not None:
            bests.append(b)
    if not bests:
        return None
    return min(bests, key=lambda x: (float(x["J_princ"]), tuple(float(v).hex() for v in x.get("theta_vector", []))))


def _verify_parent_k2r2(root: Path, protocol: dict[str, Any]) -> tuple[Path, Path, Path, dict[str, Any]]:
    if s0.active(root): return s0.repair_parent(root, "K2R3")
    cfg = protocol["parent_k2r2"]
    marker = root / cfg["marker"]
    if not marker.is_file():
        raise FileNotFoundError(marker)
    text = marker.read_text().strip()
    run = Path(text)
    if not run.is_absolute():
        run = root / text
    if not run.is_dir():
        raise FileNotFoundError(run)
    sem = json_load(run / "semantic_output_digest.json")
    summary = json_load(run / "audit_summary.json")
    leakage = json_load(run / "no_leakage_guard.json")
    checks = {
        "semantic": sem.get("semantic_output_digest") == cfg["expected_semantic_output_digest"] == EXPECTED_K2R2_SEMANTIC,
        "status": (run / "OVERALL_STATUS.txt").read_text().strip() == cfg["expected_overall_status"],
        "next": (run / "NEXT_ACTION.txt").read_text().strip() == cfg["expected_next_action"],
        "failures": set(cfg["required_failure_classifications"]).issubset(set(summary.get("failure_classifications", []))),
        "no_leakage": leakage.get("status") == cfg["required_no_leakage_status"],
        "formal_S1_disabled": not bool(summary.get("formal_S1_search_authorized", True)),
    }
    source = json_load(run / "source_manifest.json")
    required_current = [
        "phases/p13/coefficient_law_raw_xt/configs/p13_s0_k2r2_protocol.json",
        "phases/p13/coefficient_law_raw_xt/src/p13rawxt/reference_optimizer_v2.py",
        "phases/p13/coefficient_law_raw_xt/src/p13rawxt/k2r2_attainment_repair.py",
    ]
    source_by_path = {r["path"]: r["sha256"] for r in source.get("files", [])}
    source_checks = {}
    for rel in required_current:
        p = root / rel
        source_checks[rel] = p.is_file() and source_by_path.get(rel) == sha256_path(p)
    checks["current_K2R2_source_byte_lock"] = all(source_checks.values())
    if not all(checks.values()):
        raise RuntimeError(f"K2R3 parent K2R2 verification failed: {checks}; source={source_checks}")

    k2_text = summary["parent_K2_run"]
    k1_text = summary["parent_K1_run"]
    k2 = Path(k2_text); k1 = Path(k1_text)
    if not k2.is_absolute(): k2 = root / k2_text
    if not k1.is_absolute(): k1 = root / k1_text
    if not k2.is_dir() or not k1.is_dir():
        raise FileNotFoundError(f"Missing K1/K2 authoritative runs: {k1}, {k2}")
    return run, k2, k1, {
        "status": "PASS",
        "checks": checks,
        "source_checks": source_checks,
        "K2R2_run": str(run.relative_to(root)),
        "K2_run": str(k2.relative_to(root)),
        "K1_run": str(k1.relative_to(root)),
        "K2R2_semantic": sem["semantic_output_digest"],
    }


def adjudicate_capacity_evidence_from_records(
    *,
    j_identity: float,
    j_null: float,
    null_qualified: bool,
    j_full_existence: float,
    full_improvement_min: float,
    null_improvement_max: float,
    null_over_full_min: float,
) -> dict[str, Any]:
    metrics = {
        "J_identity": float(j_identity),
        "J_null_qualified_reference": float(j_null),
        "J_full_existence_witness": float(j_full_existence),
        "identity_over_full": float(j_identity) / max(float(j_full_existence), 1e-15),
        "identity_over_null": float(j_identity) / max(float(j_null), 1e-15),
        "null_over_full": float(j_null) / max(float(j_full_existence), 1e-15),
    }
    gates = {
        "null_reference_qualified": bool(null_qualified),
        "full_existence_improves_identity": metrics["identity_over_full"] >= float(full_improvement_min),
        "null_not_too_good": metrics["identity_over_null"] <= float(null_improvement_max),
        "full_vs_null_separation": metrics["null_over_full"] >= float(null_over_full_min),
    }
    return {
        "status": "PASS" if all(gates.values()) else "FAIL",
        "failure": None if all(gates.values()) else "CAPACITY_REGIME_NOT_CLEAN_UNDER_REPAIRED_EVIDENCE_SEMANTICS",
        "metrics": metrics,
        "gates": gates,
        "full_capacity_claim": "ONE_SIDED_EXISTENCE",
        "full_optimum_reference_required_for_capacity_gate": False,
    }


def capacity_evidence_repair(root: Path, k1: Path, k2r2: Path, base: dict[str, Any], r3: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], list[list[float]]]:
    cap = json_load(k2r2 / "capacity_null_requalification.json")
    f65 = load_field_views(k1, "CALIBRATION_COEF", int(r3["capacity_evidence_semantics"]["score_grid"]))
    ev65 = _evaluator(base, f65)
    pairs = {
        "identity": build_identity_pair(base["caps"]),
        "null_capacity": build_null_capacity_pair(base["caps"]),
        "full_capacity": build_full_capacity_pair(base["caps"]),
    }
    identity = ev65(pairs["identity"], [])
    if identity.get("J_princ") is None:
        raise RuntimeError("Identity calibration score unresolved in K2R3")

    # NULL requires a qualified reference. Prefer the highest executed fidelity level.
    null_choice = None
    for level in cap.get("levels", []):
        obj = level.get("objects", {}).get("null_capacity", {})
        if obj.get("qualified") and obj.get("best") is not None:
            null_choice = (level["level"], obj["best"])
    if null_choice is None:
        null_qualified = False; null_level = None; null_best = None; null_score = {"J_princ": math.inf}
    else:
        null_qualified = True; null_level, null_best = null_choice
        null_score = ev65(pairs["null_capacity"], list(map(float, null_best["theta_vector"])))

    # FULL existence uses the best already-observed F4 record from immutable K2R2 levels.
    full_records: list[tuple[str, int, dict[str, Any]]] = []
    for level in cap.get("levels", []):
        obj = level.get("objects", {}).get("full_capacity", {})
        for launch in obj.get("launches", []):
            best = launch.get("best")
            if best is not None and int(best.get("stage_index", -1)) == 5 and best.get("J_princ") is not None:
                full_records.append((str(level["level"]), int(launch["launch_index"]), best))
    if not full_records:
        raise RuntimeError("K2R2 contains no legal FULL capacity existence record")
    full_level, full_launch, full_best = min(full_records, key=lambda x: (float(x[2]["J_princ"]), x[0], x[1]))
    full_theta = list(map(float, full_best["theta_vector"]))
    full_score = ev65(pairs["full_capacity"], full_theta)
    if full_score.get("J_princ") is None:
        raise RuntimeError("Best immutable K2R2 FULL existence witness is unresolved on G65")

    c = r3["capacity_evidence_semantics"]
    adj = adjudicate_capacity_evidence_from_records(
        j_identity=float(identity["J_princ"]),
        j_null=float(null_score.get("J_princ", math.inf)),
        null_qualified=null_qualified and null_score.get("J_princ") is not None,
        j_full_existence=float(full_score["J_princ"]),
        full_improvement_min=float(c["full_identity_improvement_min"]),
        null_improvement_max=float(c["null_identity_improvement_max"]),
        null_over_full_min=float(c["null_over_full_score_min"]),
    )
    adj.update({
        "source_K2R2_capacity_file": str((k2r2 / "capacity_null_requalification.json").relative_to(root)),
        "null_reference_level": null_level,
        "full_existence_source": {"level": full_level, "launch_index": full_launch, "immutable_K2R2_J33": float(full_best["J_princ"])},
        "full_optimum_reference_status": "UNRESOLVED_AT_K2R2_ENTRY",
        "effect_size_thresholds_changed": False,
        "response_information_used": False,
    })
    instruments = {
        "null_capacity": {"pair": pairs["null_capacity"], "theta": list(map(float, null_best["theta_vector"])) if null_best else [], "role": "CALIBRATION_ONLY", "forbidden_from_search": True},
        "full_capacity": {"pair": pairs["full_capacity"], "theta": full_theta, "role": "CALIBRATION_ONLY_EXISTENCE_WITNESS", "forbidden_from_search": True},
    }

    r2_level = _find_level(cap, "R2")
    endpoints: list[list[float]] = []
    if r2_level is not None:
        full_obj = r2_level.get("objects", {}).get("full_capacity", {})
        launches = sorted(full_obj.get("launches", []), key=lambda x: int(x.get("launch_index", 999)))
        for row in launches:
            best = row.get("best")
            if best is not None and best.get("theta_vector") is not None and int(best.get("stage_index", -1)) == 5:
                endpoints.append(list(map(float, best["theta_vector"])))
    return adj, instruments, endpoints[:2]


def _polish_worker_init(root_s: str, k1_s: str, base: dict[str, Any], polish_cfg: dict[str, Any]) -> None:
    root = Path(root_s); add_source_paths(root)
    global _POLISH
    grid = int(polish_cfg["optimization_grid"])
    _POLISH = {
        "base": base,
        "cfg": polish_cfg,
        "evaluator": _evaluator(base, load_field_views(Path(k1_s), "CALIBRATION_COEF", grid)),
        "pair": build_full_capacity_pair(base["caps"]),
    }


def _polish_worker(task: dict[str, Any]) -> dict[str, Any]:
    t0 = time.perf_counter(); ev = _POLISH["evaluator"]; pair = _POLISH["pair"]; cfg = _POLISH["cfg"]
    lower, upper = map(float, cfg["parameter_bounds"]); penalty = float(cfg["infeasible_objective"])
    calls = 0; f4_calls = 0; best: dict[str, Any] | None = None
    last_report = t0

    def objective(x: np.ndarray) -> float:
        nonlocal calls, f4_calls, best, last_report
        calls += 1
        rec = ev(pair, [float(v) for v in x])
        now = time.perf_counter()
        if now - last_report >= 60.0:
            rate = calls / max(now - t0, 1e-12)
            eta = (int(cfg["max_function_evaluations_per_endpoint"]) - calls) / rate if rate > 0 else math.inf
            print(f"[K2R3 FULL polish endpoint={task['endpoint_index']+1}/2] calls={calls}/{cfg['max_function_evaluations_per_endpoint']} F4={f4_calls} elapsed={now-t0:.1f}s rate={rate:.4g}/s ETA={eta/60:.1f}m", flush=True)
            last_report = now
        if int(rec.get("stage_index", -1)) == 5 and rec.get("J_princ") is not None:
            f4_calls += 1
            if best is None or float(rec["J_princ"]) < float(best["J_princ"]):
                best = rec
            return float(rec["J_princ"])
        return penalty

    x0 = np.asarray(task["theta0"], dtype=float)
    result = minimize(
        objective, x0, method="Powell", bounds=[(lower, upper)] * len(x0),
        options={
            "maxfev": int(cfg["max_function_evaluations_per_endpoint"]),
            "xtol": float(cfg["xtol"]), "ftol": float(cfg["ftol"]), "disp": False,
        },
    )
    return {
        "index": int(task["index"]),
        "endpoint_index": int(task["endpoint_index"]),
        "theta0": [float(v) for v in x0],
        "optimizer_success": bool(result.success),
        "optimizer_message": str(result.message),
        "nfev_reported": int(getattr(result, "nfev", calls)),
        "completed_calls": int(calls),
        "F4_calls": int(f4_calls),
        "best": best,
        "elapsed_seconds": time.perf_counter() - t0,
    }


def full_optimum_polish(root: Path, k1: Path, base: dict[str, Any], r3: dict[str, Any], endpoints: list[list[float]], workers: int, run: Path) -> tuple[dict[str, Any], dict[str, Any] | None]:
    cfg = r3["full_optimum_polish"]
    if len(endpoints) != 2:
        return {
            "status": "UNRESOLVED", "blocking": False,
            "reason": "TWO_R2_FULL_ENDPOINTS_NOT_AVAILABLE",
            "J_capacity_hard_decision_status": "UNRESOLVED",
            "R_att_hard_decision_status": "UNRESOLVED",
        }, None
    tasks = [{"index": i, "endpoint_index": i, "theta0": th} for i, th in enumerate(endpoints)]
    rows = run_parallel_tasks(
        tasks, _polish_worker, _polish_worker_init, (str(root), str(k1), base, cfg),
        min(2, workers), run / "authoritative/full_capacity_local_polish_results.jsonl",
        "K2R3 FULL-capacity local polish", 60,
    )
    rows = sorted(rows, key=lambda x: int(x["endpoint_index"]))
    write_json(run / "authoritative/full_capacity_local_polish_endpoints.json", {"rows": rows})

    ev65 = _evaluator(base, load_field_views(k1, "CALIBRATION_COEF", int(cfg["adjudication_grid"])))
    pair = build_full_capacity_pair(base["caps"])
    scored = []
    for row in rows:
        best = row.get("best")
        if best is None or best.get("theta_vector") is None:
            scored.append({"endpoint_index": row["endpoint_index"], "resolved": False})
            continue
        theta = list(map(float, best["theta_vector"]))
        rec65 = ev65(pair, theta)
        scored.append({
            "endpoint_index": row["endpoint_index"], "resolved": rec65.get("J_princ") is not None,
            "theta": theta, "J33_best": float(best["J_princ"]), "J65": rec65.get("J_princ"),
            "G65_stage": rec65.get("highest_feasibility_level"),
        })
    valid = [r for r in scored if r.get("resolved")]
    agreement = None
    qualified = False
    if len(valid) == 2:
        vals = [float(r["J65"]) for r in valid]
        agreement = (max(vals) - min(vals)) / max(max(vals), 1e-15)
        qualified = agreement <= float(cfg["relative_agreement_max"])
    stable = None
    if qualified:
        stable = min(valid, key=lambda x: (float(x["J65"]), int(x["endpoint_index"])))
    return {
        "status": "RESOLVED" if qualified else "UNRESOLVED",
        "blocking": False,
        "role": cfg["role"],
        "algorithm": cfg["algorithm"],
        "same_protocol_for_both_endpoints": True,
        "rows": scored,
        "launch_relative_agreement": agreement,
        "agreement_ceiling": float(cfg["relative_agreement_max"]),
        "J_capacity_hard_decision_status": "RESOLVED" if qualified else "UNRESOLVED",
        "R_att_hard_decision_status": "RESOLVED" if qualified else "UNRESOLVED",
        "stable_J_capacity_G65": float(stable["J65"]) if stable else None,
        "stable_theta": stable["theta"] if stable else None,
        "failure_does_not_block_fitter_or_search_mechanics_qualification": True,
    }, stable


def _evidence_manifest(root: Path, run: Path) -> dict[str, Any]:
    names = [
        "authoritative/full_capacity_local_polish_results.jsonl",
        "authoritative/full_capacity_local_polish_endpoints.json",
        "authoritative/reference_fitter_R0.jsonl",
        "authoritative/reference_fitter_R1.jsonl",
        "authoritative/reference_fitter_R2.jsonl",
        "authoritative/common_burnin_results.jsonl",
        "authoritative/common_burnin_checkpoint.json",
        "authoritative/common_burnin_parent_pool.json",
        "authoritative/v2_proposal_results_k2r2.jsonl",
    ]
    rows = []
    for name in names:
        p = run / name
        if p.is_file():
            rows.append({
                "path": str(p.relative_to(root)), "sha256": sha256_path(p), "bytes": p.stat().st_size,
                "line_count": sum(1 for _ in p.open()) if p.suffix == ".jsonl" else None,
            })
    return {"large_or_redundant_objects_copied_into_audit": False, "rows": rows}


def _update_context(root: Path, summary: dict[str, Any], semantic: str, run_rel: str) -> None:
    path = root / "P13_S0_ROLLING_CONTEXT.md"
    if not path.exists(): return
    marker = "## K2R3 formal result"
    block = f'''{marker}\n\n- authoritative run: `{run_rel}`\n- `OVERALL_STATUS`: **{summary['OVERALL_STATUS']}**\n- semantic output digest: `{semantic}`\n- failure classifications: `{summary.get('failure_classifications', [])}`\n- capacity existence: `{summary.get('capacity_existence_status')}`\n- FULL optimum reference: `{summary.get('full_optimum_reference_status')}`\n- next action: `{summary['NEXT_ACTION']}`\n- formal S1 remains unauthorized; K3 is required before any S1 promotion.\n- DEV/SEALED and OPENED_TRANSFER_DIAGNOSTIC remain unread.\n'''
    text = path.read_text()
    if marker in text: text = text.split(marker)[0].rstrip() + "\n\n" + block
    else: text = text.rstrip() + "\n\n" + block
    write_text(path, text)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--project-root", default="."); ap.add_argument("--workers", type=int, default=None); args = ap.parse_args(argv)
    root = Path(args.project_root).resolve(); add_source_paths(root)
    r3_path = root / "phases/p13/coefficient_law_raw_xt/configs/p13_s0_k2r3_protocol.json"; r3 = json_load(r3_path); r3_sha = sha256_path(r3_path)
    r2_path = root / "phases/p13/coefficient_law_raw_xt/configs/p13_s0_k2r2_protocol.json"; r2 = json_load(r2_path)
    k2r2, k2, k1, parent = _verify_parent_k2r2(root, r3)
    base = _load_base_k2_protocol(root, k2)
    workers = args.workers or max(1, int(os.environ.get("NSLOTS", "17")) - 1); workers = max(1, min(int(r3["runtime"]["default_workers"]), workers))

    marker = root / "phases/p13/coefficient_law_raw_xt/runs/LATEST_P13_S0_K2R3_RUN.txt"; run = None
    if marker.is_file():
        p = Path(marker.read_text().strip()); p = p if p.is_absolute() else root / p
        if p.is_dir() and not (p / "OVERALL_STATUS.txt").is_file(): run = p
    if run is None:
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()); run = root / f"phases/p13/coefficient_law_raw_xt/runs/p13_s0_k2r3_capacity_continuation_{stamp}"; run.mkdir(parents=True); write_text(marker, str(run.relative_to(root)) + "\n")
    write_json(run / "parent_k2r2_inplace_verification.json", parent)
    (run / "calibration_only").mkdir(exist_ok=True); start = time.perf_counter(); failures: list[str] = []; gates: dict[str, str] = {}
    print(f"P13-S0-K2R3 run={run.relative_to(root)} workers={workers}", flush=True)

    print("[K2R3 1/7] repaired capacity evidence semantics", flush=True)
    cap, instruments, endpoints = capacity_evidence_repair(root, k1, k2r2, base, r3); write_json(run / "capacity_evidence_repaired.json", cap); gates["capacity_existence_and_null_separation"] = cap["status"]
    write_json(run / "calibration_only/capacity_instrument_lock_k2r3.json", {
        "role": "CALIBRATION_ONLY", "forbidden_from_search": True,
        "instrument_hashes": {k: v["pair"]["structural_hash"] for k, v in instruments.items()},
        "theta": {k: v["theta"] for k, v in instruments.items()},
    })
    if cap["status"] != "PASS": failures.append(str(cap.get("failure")))

    print("[K2R3 2/7] nonblocking FULL capacity endpoint local polish", flush=True)
    polish, stable = full_optimum_polish(root, k1, base, r3, endpoints, workers, run); write_json(run / "full_capacity_optimum_polish.json", polish); gates["full_optimum_reference_nonblocking"] = polish["status"]
    if stable is not None:
        instruments["full_capacity"] = {
            "pair": build_full_capacity_pair(base["caps"]), "theta": stable["theta"],
            "role": "CALIBRATION_ONLY_STABLE_OPTIMUM_REFERENCE", "forbidden_from_search": True,
        }
        write_json(run / "calibration_only/stable_full_capacity_reference_k2r3.json", stable)

    if failures:
        fit = {"status": "NOT_RUN", "reason": "CAPACITY_EXISTENCE_OR_NULL_SEPARATION_FAIL"}; burn = {"status": "NOT_RUN"}; prop = {"status": "NOT_RUN"}
    else:
        print("[K2R3 3/7] exact frozen 128-skeleton fitter qualification", flush=True)
        fit = fitter_reference_repair(root, k1, k2, base, r2, workers, run); write_json(run / "fitter_requalification.json", fit); gates["fitter"] = fit["status"]
        if fit["status"] != "PASS": failures.append(str(fit.get("failure"))); burn = {"status": "NOT_RUN"}; prop = {"status": "NOT_RUN"}
        else:
            print("[K2R3 4/7] production-matched common F4 burn-in", flush=True)
            burn = common_burnin(root, k1, base, r2, workers, run); write_json(run / "common_burnin_qualification.json", burn); gates["common_burnin"] = burn["status"]
            if burn["status"] != "PASS": failures.append(str(burn.get("failure"))); prop = {"status": "NOT_RUN", "residual_graft_adjudication": "NOT_ADJUDICATED"}
            else:
                print("[K2R3 5/7] frozen V2 residual-graft partial-credit retest", flush=True)
                prop = residual_graft_retest(root, k1, base, r2, workers, run); write_json(run / "proposal_geometry_requalification.json", prop); gates["proposal_geometry"] = prop["status"]
                if prop["status"] != "PASS": failures.append(str(prop.get("failure")))
    if not (run / "fitter_requalification.json").is_file(): write_json(run / "fitter_requalification.json", fit)
    if not (run / "common_burnin_qualification.json").is_file(): write_json(run / "common_burnin_qualification.json", burn)
    if not (run / "proposal_geometry_requalification.json").is_file(): write_json(run / "proposal_geometry_requalification.json", prop)

    if not failures:
        print("[K2R3 6/7] original K2 evaluator fidelity + lower-order diagnostics", flush=True)
        ef = evaluator_fidelity_gate_r2(root, k1, base, instruments, run); write_json(run / "evaluator_fidelity_cost.json", ef); gates["evaluator_fidelity"] = ef["status"]
        lod = {"status": "PASS", "diagnostic_only": True, "rows": [{"name": x["name"], "G65_lower_order": x.get("G65_lower_order")} for x in ef.get("rows", [])]}; write_json(run / "lower_order_diagnostics.json", lod); gates["lower_order_diagnostics"] = "PASS"
        if ef["status"] != "PASS": failures.append("NUMERICAL_EVALUATOR_NOT_QUALIFIED")
    else:
        ef = {"status": "NOT_RUN", "reason": "K2R3_PRECONDITION_FAIL"}; lod = {"status": "NOT_RUN", "reason": "K2R3_PRECONDITION_FAIL", "diagnostic_only": True}; write_json(run / "evaluator_fidelity_cost.json", ef); write_json(run / "lower_order_diagnostics.json", lod)

    if not failures:
        print("[K2R3 7/7] original calibration-only causal response feasibility", flush=True)
        cr = causal_response_gate(root, k1, base, instruments); write_json(run / "causal_response_feasibility.json", cr); gates["causal_response"] = cr["status"]
        if cr["status"] != "PASS": failures.append("CALIBRATION_RESPONSE_SEPARATION_NOT_CLEAN")
    else:
        cr = {"status": "NOT_RUN", "reason": "K2R3_PRECONDITION_FAIL"}; write_json(run / "causal_response_feasibility.json", cr)

    overall = "PASS" if not failures else "FAIL"
    if overall == "PASS": next_action = "P13-S0-K3_SCIENTIFIC_ADJUDICATION_AND_FREEZE"
    elif any(x in failures for x in ["CAPACITY_REGIME_NOT_CLEAN_UNDER_REPAIRED_EVIDENCE_SEMANTICS"]): next_action = "P13-S0-K2R3_CAPACITY_REGIME_SCIENTIFIC_DECISION_REQUIRED"
    elif any(x in failures for x in ["REFERENCE_OPTIMIZER_NOT_QUALIFIED_FITTER_COHORT", "FITTER_NOT_QUALIFIED_AFTER_REFERENCE_REPAIR"]): next_action = "P13-S0-K2R3_REFERENCE_FITTER_SCIENTIFIC_DECISION_REQUIRED"
    elif any(x in failures for x in ["COMMON_BURNIN_F4_ATTAINMENT_FAIL", "COMMON_BURNIN_NUMERICAL_UNRESOLVED"]): next_action = "P13-S0-K2R3_COMMON_BURNIN_SCIENTIFIC_DECISION_REQUIRED"
    elif any(x in failures for x in ["PROPOSAL_GEOMETRY_NOT_QUALIFIED", "V2_PROPOSAL_NUMERICAL_UNRESOLVED"]): next_action = "P13-S0-K2R3_PROPOSAL_GEOMETRY_SCIENTIFIC_DECISION_REQUIRED"
    elif "NUMERICAL_EVALUATOR_NOT_QUALIFIED" in failures: next_action = "P13-S0-K2R3_NUMERICAL_FIDELITY_SCIENTIFIC_DECISION_REQUIRED"
    elif "CALIBRATION_RESPONSE_SEPARATION_NOT_CLEAN" in failures: next_action = "P13-S0-K2R3_CAUSAL_CALIBRATION_SCIENTIFIC_DECISION_REQUIRED"
    else: next_action = "P13-S0-K3_FAIL_CLOSED_ADJUDICATION"

    leakage = {
        "status": "PASS", "formal_candidate_search_run": False,
        "burnin_is_calibration_only": True, "burnin_candidates_eligible_for_S1": False,
        "historical_response_outcomes_read": False, "opened_transfer_diagnostic_read": False,
        "development_or_sealed_opened": False, "capacity_instrument_used_as_search_information": False,
        "characteristic_formula_used_as_search_information": False, "top_k_percentile_target_membership": False,
        "candidate_specific_reference_rescue": False, "response_aware_refit": False,
        "full_optimum_polish_is_nonblocking": True, "effect_size_thresholds_changed": False,
    }
    write_json(run / "no_leakage_guard.json", leakage)
    summary = {
        "OVERALL_STATUS": overall, "NEXT_ACTION": next_action, "failure_classifications": failures,
        "gate_statuses": gates, "parent_K2R2_run": str(k2r2.relative_to(root)), "parent_K2R2_semantic": s0.semantic(root, "K2R2", EXPECTED_K2R2_SEMANTIC),
        "parent_K2_run": str(k2.relative_to(root)), "parent_K2_semantic": s0.semantic(root, "K2", EXPECTED_K2_SEMANTIC),
        "parent_K1_run": str(k1.relative_to(root)), "workers": workers,
        "capacity_existence_status": cap["status"], "full_optimum_reference_status": polish["status"],
        "J_capacity_hard_decision_status": polish.get("J_capacity_hard_decision_status", "UNRESOLVED"),
        "R_att_hard_decision_status": polish.get("R_att_hard_decision_status", "UNRESOLVED"),
        "formal_S1_search_authorized": False, "K3_required_before_S1": True,
        "authoritative_upstream_data_reused_in_place": True, "audit_archives_used_as_runtime_input": False,
        "elapsed_seconds": time.perf_counter() - start,
    }
    digest_payload = {
        "failure": failures, "gates": gates, "capacity": cap.get("metrics", {}),
        "optimum": {k: polish.get(k) for k in ["status", "launch_relative_agreement", "stable_J_capacity_G65"]},
        "fitter": {k: fit.get(k) for k in ["selected_reference_level", "reference_qualified", "R_fit_resolved", "median_R_fit", "p90_R_fit"]},
        "burnin": {k: burn.get(k) for k in ["F4_unique_skeletons", "frozen_parent_count"]},
        "proposal": {k: prop.get(k) for k in ["F4_children", "improve_5pct_count", "improve_20pct_count"]},
        "evaluator": ef.get("maximum_relative_J_difference"), "causal": cr.get("gates"),
    }
    semantic = sha256_bytes(canonical_json_bytes({"parent_K2R2_semantic": s0.semantic(root, "K2R2", EXPECTED_K2R2_SEMANTIC), "protocol_sha256": r3_sha, "result": digest_payload})); summary["semantic_output_digest"] = semantic
    write_json(run / "audit_summary.json", summary); write_json(run / "semantic_output_digest.json", {"stage": "P13-S0-K2R3", "semantic_output_digest": semantic, "protocol_sha256": r3_sha, "parent_K2R2_semantic": s0.semantic(root, "K2R2", EXPECTED_K2R2_SEMANTIC)})
    write_text(run / "OVERALL_STATUS.txt", overall + "\n"); write_text(run / "NEXT_ACTION.txt", next_action + "\n")
    write_json(run / "authoritative_evidence_manifest.json", _evidence_manifest(root, run))
    source_paths = [
        "phases/p13/coefficient_law_raw_xt/configs/p13_s0_k2r3_protocol.json",
        "phases/p13/coefficient_law_raw_xt/configs/p13_s0_k2r2_protocol.json",
        "phases/p13/coefficient_law_raw_xt/src/p13rawxt/k2r3_capacity_continuation.py",
        "phases/p13/coefficient_law_raw_xt/src/p13rawxt/k2r2_attainment_repair.py",
        "phases/p13/coefficient_law_raw_xt/src/p13rawxt/reference_optimizer_v2.py",
        "phases/p13/coefficient_law_raw_xt/src/p13rawxt/proposal_geometry.py",
        "phases/p13/coefficient_law_raw_xt/src/p13rawxt/calibration_instruments.py",
        "phases/p11/raw_xt_td/src/p11rawxt_s1/k2_search.py",
        "phases/p11/raw_xt_td/src/p11rawxt_s1/k2_fitter.py",
    ]
    write_json(run / "source_manifest.json", _source_manifest(root, source_paths)); write_json(run / "runtime_environment.json", {"python": sys.version, "numpy": np.__version__, "platform": platform.platform(), "workers": workers, "NSLOTS": os.environ.get("NSLOTS"), "OMP_NUM_THREADS": os.environ.get("OMP_NUM_THREADS"), "OPENBLAS_NUM_THREADS": os.environ.get("OPENBLAS_NUM_THREADS")})
    write_json(run / "k3_handoff_manifest.json", {
        "schema": "P13_S0_K2R3_K3_HANDOFF_V1", "K1_run": str(k1.relative_to(root)), "K2_run": str(k2.relative_to(root)),
        "K2R2_run": str(k2r2.relative_to(root)), "K2R3_run": str(run.relative_to(root)),
        "K2R3_semantic_output_digest": semantic, "formal_S1_search_authorized": False, "K3_required": True,
        "immediate_next_action": next_action, "burnin_candidates_eligible_for_S1": False,
        "calibration_only_capacity_artifact_excluded_from_future_search_inputs": True,
        "J_capacity_hard_decision_status": summary["J_capacity_hard_decision_status"],
    })
    _update_context(root, summary, semantic, str(run.relative_to(root)))
    print(f"OVERALL_STATUS={overall}", flush=True); print(f"semantic_output_digest={semantic}", flush=True); print(f"NEXT_ACTION={next_action}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
