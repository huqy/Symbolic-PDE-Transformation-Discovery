from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np


def _json(path: Path) -> Any:
    return json.loads(path.read_text())


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, sort_keys=True, indent=2) + "\n")
    tmp.replace(path)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)


def _sha(path: Path, block: int = 16 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(block), b""):
            h.update(b)
    return h.hexdigest()


def _jsonl_count(path: Path) -> int:
    with path.open("rb") as f:
        return sum(1 for line in f if line.strip())


def _read_jsonl(path: Path):
    with path.open() as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def _line_at(path: Path, offset: int) -> dict[str, Any]:
    with path.open("rb") as f:
        f.seek(int(offset))
        line = f.readline()
    return json.loads(line)


def _resolve_marker(root: Path, rel: str) -> Path:
    marker = root / rel
    if not marker.is_file():
        raise RuntimeError(f"missing authoritative marker: {rel}")
    s = marker.read_text().strip()
    p = Path(s)
    if not p.is_absolute():
        p = root / p
    return p.resolve()


def _progress(stage: str, done: int, total: int, start: float, current: str = "") -> None:
    elapsed = max(time.perf_counter() - start, 1e-12)
    rate = done / elapsed
    eta = (total - done) / rate if rate > 0 else math.inf
    print(f"[{stage}] processed={done}/{total} current={current} elapsed={elapsed:.1f}s rate={rate:.3g}/s ETA={eta:.1f}s", flush=True)


def coefficient_derivative_order(node: Any, derivative_depth: int = 0) -> int:
    """Exact syntactic coefficient-derivative order along AST paths to Var('a').

    Dx/Dt increment derivative depth for all coefficient occurrences in their argument.
    The result is syntactic order only; it does not establish functional necessity.
    Returns -1 when no coefficient syntax occurs.
    """
    if not isinstance(node, dict):
        return -1
    op = node.get("op")
    if op == "Var" and node.get("name") == "a":
        return int(derivative_depth)
    next_depth = derivative_depth + 1 if op in {"Dx", "Dt"} else derivative_depth
    vals = [coefficient_derivative_order(c, next_depth) for c in node.get("args", [])]
    return max(vals) if vals else -1


def pair_derivative_order(pair: dict[str, Any]) -> dict[str, int]:
    ox = coefficient_derivative_order(pair["raw_X_AST"])
    ot = coefficient_derivative_order(pair["raw_T_AST"])
    return {"X": ox, "T": ot, "pair": max(ox, ot)}


def _setup_imports(root: Path) -> None:
    for p in [root / "phases/p13/coefficient_law_raw_xt/src", root / "phases/p11/raw_xt_td/src"]:
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    for k in ["OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"]:
        os.environ[k] = "1"


def _build_theory_pair(caps: dict[str, int], mode: str) -> dict[str, Any]:
    import copy
    from p11rawxt_ast import Var, Theta, Op
    from p11rawxt_s1.k1_representation import canonicalize_pair
    x, t, a = Var("x"), Var("t"), Var("a")
    loga = Op("Log", a)
    bx = Op("Dx", copy.deepcopy(loga))
    bt = Op("Dt", copy.deepcopy(loga))
    bxt = Op("Dt", Op("Dx", copy.deepcopy(loga)))
    bxx = Op("Dx", Op("Dx", copy.deepcopy(loga)))
    btt = Op("Dt", Op("Dt", copy.deepcopy(loga)))
    t2, t3 = Op("PowInt", t, exponent=2), Op("PowInt", t, exponent=3)
    if mode == "B3_ONLY_M0_POINT_VALUE":
        X = x
        T = Op("Add", t, Op("Mul", Theta("theta_1"), t, loga))
    elif mode == "THEORY_M_LE_1":
        X = Op("Add", x, Op("Mul", Theta("theta_1"), t2, bx))
        T = Op("Add", t,
               Op("Mul", Theta("theta_2"), t, loga),
               Op("Mul", Theta("theta_3"), t2, bt))
    elif mode == "FULL_FROZEN_M_LE_2":
        X = Op("Add", x,
               Op("Mul", Theta("theta_1"), copy.deepcopy(t2), bx),
               Op("Mul", Theta("theta_2"), copy.deepcopy(t3), bxt))
        T = Op("Add", t,
               Op("Mul", Theta("theta_3"), t, loga),
               Op("Mul", Theta("theta_4"), copy.deepcopy(t2), bt),
               Op("Mul", Theta("theta_5"), copy.deepcopy(t3), btt),
               Op("Mul", Theta("theta_6"), copy.deepcopy(t3), bxx))
    else:
        raise ValueError(mode)
    return canonicalize_pair(X, T, caps)


def _compact_best(r: dict[str, Any] | None) -> dict[str, Any] | None:
    if r is None:
        return None
    return {k: r.get(k) for k in ["stage_index", "highest_feasibility_level", "J_princ", "J_XT", "J_XX", "J_max", "theta_vector"]}


def _fit_subspace(pair: dict[str, Any], evaluate, ref_protocol: dict[str, Any], seed: int, label: str) -> dict[str, Any]:
    from p13rawxt.calibration_instruments import reference_launch
    launches = []
    n = int(ref_protocol["independent_launches"])
    for i in range(n):
        s = (int(seed) + i * 0x85EBCA6B) & 0xFFFFFFFF
        print(f"[K0-C {label}] optimizer_launch={i+1}/{n} seed={s}", flush=True)
        launches.append(reference_launch(pair, evaluate, ref_protocol, s,
                                         progress_label=f"S3-K0 {label} launch {i+1}/{n}",
                                         progress_every_calls=25, progress_every_seconds=30.0))
    bests = [x["best"] for x in launches if x.get("best") and int(x["best"].get("stage_index", -1)) == 5 and x["best"].get("J_princ") is not None]
    agreement = None
    qualified = False
    if len(bests) == n and n >= 2:
        js = [float(x["J_princ"]) for x in bests]
        agreement = (max(js) - min(js)) / max(max(js), 1e-15)
        qualified = agreement <= float(ref_protocol["launch_relative_agreement_max"])
    best = min(bests, key=lambda r: float(r["J_princ"])) if bests else None
    return {
        "qualified": qualified,
        "launch_relative_agreement": agreement,
        "best": _compact_best(best),
        "launches": [{"completed_calls": int(x["completed_calls"]), "best": _compact_best(x.get("best"))} for x in launches],
    }


def _parent_lock(root: Path, cfg: dict[str, Any]) -> dict[str, Any]:
    p = cfg["parent"]
    checks: dict[str, Any] = {}
    freeze = root / p["portable_freeze_filename"]
    checks["portable_freeze_exists"] = freeze.is_file()
    checks["portable_freeze_sha"] = freeze.is_file() and _sha(freeze) == p["portable_freeze_sha256"]
    s2run = _resolve_marker(root, "phases/p13/coefficient_law_raw_xt/runs/LATEST_P13_S2_K7_RUN.txt")
    k7 = s2run / "K7_formal_s2_freeze"
    summary = _json(k7 / "K7_S2_FINAL_SCIENTIFIC_SUMMARY.json")
    digest = _json(k7 / "K7_SEMANTIC_OUTPUT_DIGEST.json")
    checks["K7_PASS"] = summary.get("OVERALL_STATUS") == "PASS"
    checks["K7_semantic"] = digest.get("semantic_output_digest") == p["K7_semantic_output_digest"]
    checks["K6_semantic"] = summary.get("K6_semantic_output_digest") == p["K6_semantic_output_digest"]
    checks["claim_hierarchy"] = all(summary.get(k) == v for k, v in cfg["frozen_claim_hierarchy"].items())
    checks["SEALED_unopened"] = summary.get("SEALED_opened") is False
    checks["cohort_count"] = int(summary.get("formal_S2_certified_cohort", -1)) == int(p["formal_S2_certified_count"])
    membership = root / p["K5_membership_path"]
    checks["membership_exists"] = membership.is_file()
    checks["membership_count"] = membership.is_file() and _jsonl_count(membership) == int(p["formal_S2_certified_count"])
    checks["membership_sha"] = membership.is_file() and _sha(membership) == p["formal_S2_membership_sha256"]
    checks["summary_membership_sha"] = summary.get("formal_S2_certified_membership_sha256") == p["formal_S2_membership_sha256"]

    k0cfg = _json(root / "phases/p13/coefficient_law_raw_xt/configs/p13_s2_k0_protocol.json")
    k2cfg = _json(root / "phases/p13/coefficient_law_raw_xt/configs/p13_s2_k2_protocol.json")
    k3cfg = _json(root / "phases/p13/coefficient_law_raw_xt/configs/p13_s2_k3_protocol.json")
    z = k0cfg["zero_shot_contract"]
    checks["same_AST_theta_gauge_zero_refit"] = all([
        z.get("same_raw_AST"), z.get("same_theta"), z.get("same_deterministic_gauge"),
        not z.get("AST_refit"), not z.get("theta_refit"), not z.get("branch_reselection"), not z.get("amplitude_compensation")
    ])
    checks["operator_boundary_0p5"] = float(k0cfg["operator_transfer_protocol"]["hard_gate"]["family_ratio_boundary"]) == 0.5
    checks["tau_num_0p005"] = float(k2cfg["frozen_hard_gate"]["tau_num"]) == 0.005 and k2cfg["k1_fidelity_review_lock"].get("tau_num_changed") is False
    checks["response_gate_0p15"] = float(k3cfg["scientific_response_contract"]["relative_error_threshold"]) == 0.15
    checks["reference_ladder"] = k3cfg["reference_protocol"]["initial_nested_pair"] == [513, 1025] and k3cfg["reference_protocol"]["refinement_pairs_if_needed"] == [[1025, 2049], [2049, 4097]] and float(k3cfg["reference_protocol"]["reference_uncertainty_ceiling_relative_energy"]) == 0.02
    checks["candidate_ladder"] = k3cfg["candidate_control_fidelity_protocol"]["initial_nested_pair"] == [129, 257] and int(k3cfg["candidate_control_fidelity_protocol"]["first_uniform_escalation_grid"]) == 513 and int(k3cfg["candidate_control_fidelity_protocol"]["second_uniform_escalation_grid"]) == 1025
    checks["no_candidate_specific_rescue"] = k3cfg["candidate_control_fidelity_protocol"].get("candidate_specific_rescue") is False

    status = "PASS" if all(bool(v) for v in checks.values()) else "FAIL"
    return {"status": status, "checks": checks, "authoritative_S2_run": str(s2run), "K7_summary": summary}


def _claim_lock(cfg: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "PASS",
        "role": "POST_OPUS_CLAIM_BOUNDARY_LOCK",
        "locks": cfg["claim_boundary_lock"],
        "opus_permission": {
            "postfreeze_attainment_quantities": "DESCRIPTIVE_INTERPRETATION_ONLY",
            "structural_order_and_kappa_tests": "TRAIN_ONLY_DIAGNOSTIC_CALIBRATION_ONLY",
            "retroactive_membership_change": False,
            "retroactive_threshold_change": False,
            "retroactive_grammar_change": False,
            "retroactive_fidelity_change": False,
            "SEALED_use": False
        }
    }


def _strata_lock(root: Path, cfg: dict[str, Any]) -> dict[str, Any]:
    path = root / cfg["parent"]["K2_decision_map_path"]
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in _read_jsonl(path):
        groups[r["III_B_decision"]].append({
            "membership_index": int(r["membership_index"]),
            "scientific_branch_id": r["scientific_branch_id"],
            "arm": r["arm"], "paired_seed": int(r["paired_seed"]), "proposal_index": int(r["proposal_index"]),
            "structural_hash": r["structural_hash"], "exact_equivalence_class": r["exact_equivalence_class"],
            "membership_authority": r["III_B_decision"] == "OPERATOR_TRANSFER_PASS"
        })
    expected = {"OPERATOR_TRANSFER_PASS": 1955, "OPERATOR_TRANSFER_FAIL": 348, "OPERATOR_TRANSFER_UNRESOLVED": 4}
    counts = {k: len(groups.get(k, [])) for k in expected}
    status = "PASS" if counts == expected else "FAIL"
    return {"status": status, "counts": counts, "expected": expected, "reporting_lock": cfg["diagnostic_strata"], "groups": groups}


def _structural_census(root: Path, cfg: dict[str, Any], pass_ids: set[str]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    locator_path = root / cfg["parent"]["K2A_clear_locator_path"]
    locators = {r["scientific_branch_id"]: r for r in _read_jsonl(locator_path) if r["scientific_branch_id"] in pass_ids}
    if len(locators) != len(pass_ids):
        raise RuntimeError(f"missing S1 locators for K5 pass cohort: {len(locators)} vs {len(pass_ids)}")
    rows = []
    start = time.perf_counter(); total = len(pass_ids)
    for i, sid in enumerate(sorted(pass_ids), 1):
        loc = locators[sid]
        s = _line_at(root / loc["skeleton_registry_path"], int(loc["skeleton_registry_byte_offset"]))
        if s["structural_hash"] != loc["structural_hash"]:
            raise RuntimeError("skeleton locator hash mismatch")
        order = pair_derivative_order(s["pair"])
        rows.append({
            "scientific_branch_id": sid,
            "arm": s["arm"], "paired_seed": int(s["paired_seed"]), "proposal_index": int(s["proposal_index"]),
            "structural_hash": s["structural_hash"],
            "max_coefficient_derivative_order_X": order["X"],
            "max_coefficient_derivative_order_T": order["T"],
            "max_coefficient_derivative_order_pair": order["pair"],
            "interpretation": "SYNTACTIC_ONLY_NOT_FUNCTIONAL_NECESSITY",
            "membership_authority": False
        })
        if i == 1 or i % int(cfg["runtime"]["progress_every_items"]) == 0 or i == total:
            _progress("K0-C structural-census", i, total, start, sid[:12])
    cnt = Counter(r["max_coefficient_derivative_order_pair"] for r in rows)
    by_struct: dict[str, int] = {}
    for r in rows:
        by_struct[r["structural_hash"]] = r["max_coefficient_derivative_order_pair"]
    return {
        "status": "PASS", "branch_count": len(rows), "distinct_structural_hashes": len(by_struct),
        "branch_order_counts": {str(k): int(v) for k, v in sorted(cnt.items())},
        "structural_hash_order_counts": {str(k): int(v) for k, v in sorted(Counter(by_struct.values()).items())},
        "claim_boundary": "exact syntactic order census; does not establish effective/necessary functional jet order",
        "membership_authority": False
    }, rows


def _capacity_and_kappa(root: Path, cfg: dict[str, Any], pass_ids: set[str]) -> dict[str, Any]:
    from p13rawxt.family_evaluator import load_field_views, evaluate_family
    k1run = _resolve_marker(root, "phases/p13/coefficient_law_raw_xt/runs/LATEST_P13_S0_K1_RUN.txt")
    base = _json(root / "phases/p13/coefficient_law_raw_xt/configs/p13_s0_k2_protocol.json")
    train33 = load_field_views(k1run, "TRAIN_OPERATOR", 33, 65)
    train65 = load_field_views(k1run, "TRAIN_OPERATOR", 65, 65)
    def evaluator(fields):
        return lambda pair, theta: evaluate_family(pair, theta, fields, base["validity"], float(base["validity"]["inverse_roundtrip_tolerance"]), base["operator"]["space"], base["operator"]["numerical"])
    ev33, ev65 = evaluator(train33), evaluator(train65)
    refp = base["fitter"]["reference"]
    subspaces = {}
    for j, mode in enumerate(["B3_ONLY_M0_POINT_VALUE", "THEORY_M_LE_1"]):
        pair = _build_theory_pair(base["caps"], mode)
        fit = _fit_subspace(pair, ev33, refp, 0x533300 + j * 977, mode)
        rec = {"mode": mode, "role": "CALIBRATION_DIAGNOSTIC_ONLY", "structural_hash": pair["structural_hash"], "fit": fit, "membership_authority": False}
        if fit["qualified"] and fit["best"] is not None:
            theta = [float(x) for x in fit["best"]["theta_vector"]]
            g65 = ev65(pair, theta)
            rec.update({"theta": theta, "J_G33": float(fit["best"]["J_princ"]), "J_G65": None if g65.get("J_family") is None else float(g65["J_family"])})
        subspaces[mode] = rec
    fullJ = float(cfg["diagnostics"]["capacity_full_m2_frozen_J_G65"])
    subspaces["FULL_FROZEN_M_LE_2"] = {
        "mode": "FULL_FROZEN_M_LE_2", "role": "CALIBRATION_DIAGNOSTIC_ONLY_FROZEN_REUSE",
        "J_G65": fullJ, "source": cfg["diagnostics"]["capacity_full_m2_source"], "rerun": False,
        "membership_authority": False
    }
    for rec in subspaces.values():
        if rec.get("J_G65") is not None:
            rec["J_G65_over_full_m2_capacity"] = float(rec["J_G65"]) / fullJ

    # PF0 effective B3 coefficient restricted to the final 1955 cohort, using frozen K2C TRAIN-only results.
    k2c = root / cfg["parent"]["K2C_branch_results_path"]
    if _sha(k2c) != cfg["parent"]["K2C_branch_results_sha256"]:
        raise RuntimeError("K2C branch-results SHA mismatch")
    vals = []
    for r in _read_jsonl(k2c):
        if r.get("scientific_branch_id") not in pass_ids:
            continue
        fa = r.get("functional_alignment", {})
        coeff = fa.get("fitted_basis_coefficients")
        if fa.get("status") == "RESOLVED" and isinstance(coeff, list) and len(coeff) >= 3:
            vals.append(float(coeff[int(cfg["diagnostics"]["PF0_B3_channel_index_zero_based"])]))
    pf0 = {
        "resolved_count": len(vals),
        "median_effective_B3_coefficient_in_b_basis": None if not vals else float(np.median(vals)),
        "p10": None if not vals else float(np.quantile(vals, 0.1)),
        "p90": None if not vals else float(np.quantile(vals, 0.9)),
        "source": cfg["parent"]["K2C_branch_results_path"],
        "membership_authority": False
    }
    b3 = subspaces["B3_ONLY_M0_POINT_VALUE"]
    kappa = None if not b3.get("theta") else float(b3["theta"][0])
    return {
        "status": "PASS" if b3["fit"]["qualified"] and subspaces["THEORY_M_LE_1"]["fit"]["qualified"] else "UNRESOLVED_DIAGNOSTIC_OPTIMIZATION",
        "nested_subspaces": subspaces,
        "kappa_obj": {
            "family": "T=t+kappa* t*log(a), X=x",
            "kappa_obj": kappa,
            "objective": "frozen TRAIN family J_princ; response-blind",
            "PF0_final1955_effective_B3": pf0,
            "theory_B3_coefficient_in_b_basis": float(cfg["diagnostics"]["B3_theory_coefficient_in_b_basis"]),
            "full_capacity_fitted_log_a_coefficient": 0.06926249765820103,
            "claim_boundary": "diagnoses objective preference versus attainment hypothesis only; no historical candidate is refit",
            "membership_authority": False
        },
        "membership_authority": False
    }


def _semantic_digest(summary: dict[str, Any]) -> str:
    x = json.loads(json.dumps(summary))
    x.pop("semantic_output_digest", None)
    return hashlib.sha256(json.dumps(x, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("project_root")
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args(argv)
    root = Path(args.project_root).resolve()
    _setup_imports(root)
    cfg_path = root / "phases/p13/coefficient_law_raw_xt/configs/p13_s3_k0_protocol.json"
    cfg = _json(cfg_path)
    runs = root / "phases/p13/coefficient_law_raw_xt/runs"
    run = runs / "p13_s3_k0_presealed_lock"
    run.mkdir(parents=True, exist_ok=True)
    out = run / "K0_presealed_lock"
    out.mkdir(parents=True, exist_ok=True)

    print("[K0-A] parent freeze / reproducibility lock", flush=True)
    parent = _parent_lock(root, cfg)
    _write_json(out / "K0A_PARENT_REPRODUCIBILITY_LOCK.json", parent)
    if parent["status"] != "PASS":
        _write_text(out / "OVERALL_STATUS.txt", "FAIL\n")
        _write_text(out / "NEXT_ACTION.txt", "STOP_BLOCKER_PARENT_REPRODUCIBILITY_INCONSISTENCY\n")
        print("P13_S3_K0_FINAL=FAIL", flush=True)
        return 2

    print("[K0-B] post-Opus claim-boundary lock", flush=True)
    claim = _claim_lock(cfg)
    _write_json(out / "K0B_POST_OPUS_CLAIM_BOUNDARY_LOCK.json", claim)

    print("[K0-D] freeze formal/negative-control/unresolved diagnostic strata before any SEALED opening", flush=True)
    strata = _strata_lock(root, cfg)
    for decision, rows in strata.pop("groups").items():
        name = {"OPERATOR_TRANSFER_PASS": "FORMAL_1955", "OPERATOR_TRANSFER_FAIL": "DIAGNOSTIC_DEV_FAIL_348", "OPERATOR_TRANSFER_UNRESOLVED": "DIAGNOSTIC_DEV_UNRESOLVED_4"}[decision]
        with (out / f"K0D_{name}.jsonl").open("w") as f:
            for r in rows:
                f.write(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n")
    _write_json(out / "K0D_S3_REPORTING_DIAGNOSTIC_STRATA_LOCK.json", strata)
    if strata["status"] != "PASS":
        _write_text(out / "OVERALL_STATUS.txt", "FAIL\n")
        _write_text(out / "NEXT_ACTION.txt", "STOP_BLOCKER_DIAGNOSTIC_STRATA_ACCOUNTING\n")
        print("P13_S3_K0_FINAL=FAIL", flush=True)
        return 2

    pass_ids = {r["scientific_branch_id"] for r in _read_jsonl(root / cfg["parent"]["K5_membership_path"])}
    print("[K0-C1] frozen 1955 AST structural derivative-order census", flush=True)
    census, census_rows = _structural_census(root, cfg, pass_ids)
    _write_json(out / "K0C1_STRUCTURAL_DERIVATIVE_ORDER_CENSUS_SUMMARY.json", census)
    with (out / "K0C1_STRUCTURAL_DERIVATIVE_ORDER_CENSUS.jsonl").open("w") as f:
        for r in census_rows:
            f.write(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n")

    print("[K0-C2/C3] nested TRAIN-only theory subspaces + scalar kappa_obj objective experiment", flush=True)
    cap = _capacity_and_kappa(root, cfg, pass_ids)
    _write_json(out / "K0C2_C3_TRAIN_ONLY_CAPACITY_AND_KAPPA_DIAGNOSTIC.json", cap)

    # Diagnostic optimization non-convergence does not rewrite membership, but K0 is fail-closed for an implementation/numerical defect.
    diagnostic_impl_ok = cap["status"] in {"PASS", "UNRESOLVED_DIAGNOSTIC_OPTIMIZATION"}
    sealed_guard = {
        "SEALED_FINAL_COEF_opened": False,
        "SEALED_FINAL_RESPONSE_opened": False,
        "private_SEALED_payload_traversed": False,
        "development_response_used_for_K0_C": False,
        "historical_candidate_refit": False,
        "membership_modified": False,
        "threshold_modified": False,
        "grammar_modified": False,
        "fidelity_policy_modified": False,
        "top_k_proxy_target_survivor_rescue": False,
        "status": "PASS"
    }
    _write_json(out / "K0_NO_SEALED_OPENING_GUARD.json", sealed_guard)
    status = "PASS" if parent["status"] == "PASS" and claim["status"] == "PASS" and strata["status"] == "PASS" and census["status"] == "PASS" and diagnostic_impl_ok else "FAIL"
    summary = {
        "OVERALL_STATUS": status,
        "NEXT_ACTION": cfg["next_on_pass"] if status == "PASS" else "STOP_AND_AUDIT_K0_BLOCKER",
        "K0A_parent_lock": parent["status"],
        "K0B_claim_boundary_lock": claim["status"],
        "K0C_structural_census": census,
        "K0C_capacity_kappa_status": cap["status"],
        "K0D_strata": strata,
        "SEALED_opened": False,
        "formal_membership_count": 1955,
        "formal_membership_sha256": cfg["parent"]["formal_S2_membership_sha256"],
        "diagnostic_membership_authority": False,
        "K1_authorized": False,
        "semantic_output_digest": None
    }
    summary["semantic_output_digest"] = _semantic_digest(summary)
    _write_json(out / "K0_SCIENTIFIC_SUMMARY.json", summary)
    _write_json(out / "K0_SEMANTIC_OUTPUT_DIGEST.json", {"semantic_output_digest": summary["semantic_output_digest"]})
    _write_text(out / "OVERALL_STATUS.txt", status + "\n")
    _write_text(out / "NEXT_ACTION.txt", summary["NEXT_ACTION"] + "\n")
    _write_text(runs / "LATEST_P13_S3_K0_RUN.txt", str(run.relative_to(root)) + "\n")
    print(f"P13_S3_K0_FINAL={status}", flush=True)
    return 0 if status == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
