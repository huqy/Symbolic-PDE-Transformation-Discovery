from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import numpy as np

from .ast_runtime import evaluate_pair_jet, grid_coefficient_derivatives
from .calibration_instruments import build_full_capacity_pair, build_identity_pair, reference_launch
from .coefficients import canonical_json_bytes, search_object_semantic_digest, sha256_bytes
from .family_evaluator import (
    _grid_from_arrays,
    _probe_source,
    _p11_imports,
    evaluate_family,
    evaluate_validity_variable,
    load_field_views,
    operator_on_variable,
)
from .s1_k2b_post_search_diagnostics import _candidate_from_locator, _load_membership
from .s1_k2c_theory_bridge import _alignment_basis, _derived_views, _log_slope
from .s1_search_primitives import operator_qualification

EXPECTED_K3_SEMANTIC = "0862640a856eed7d0321d8fa0597b96005293c9058836d686914b31f52e01997"
EXPECTED_K2C_SEMANTIC = "910c1337fef9395610f809daff2de3b7f64deca691f0d1a6ca240e3ce3d9597f"
EXPECTED_CLEAR_SHA = "564134db0bee502198729463c64ffe29408b30081ec1af63d86afff0f4da0c84"
EXPECTED_UNRESOLVED_SHA = "4177feffe4417e157f17eb90578b1e749ec06fe0db7935a7ade315c33cbc4f67"
EXPECTED_CLEAR_COUNT = 2307
EXPECTED_UNRESOLVED_COUNT = 80
PF0_V1_ORIGINAL_SOURCE_SHA256 = "8a6741df11ab10cc42473cfebee9da0fc74a4cb5e70b3c0befe2f4c2ea6b78d0"
ASP_DIAGNOSTIC_NUMERIC_EXCEPTIONS = (FloatingPointError, OverflowError, ZeroDivisionError)

_ASP: dict[str, Any] = {}


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text())


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, sort_keys=True, indent=2) + "\n")
    os.replace(tmp, path)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def _sha256_path(path: Path, block: int = 16 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(block), b""):
            h.update(b)
    return h.hexdigest()


def _resolve_marker(root: Path, rel: str) -> Path:
    marker = root / rel
    if not marker.is_file():
        raise FileNotFoundError(marker)
    p = Path(marker.read_text().strip())
    if not p.is_absolute():
        p = root / p
    p = p.resolve()
    if root.resolve() not in p.parents:
        raise RuntimeError(f"marker escapes project root: {marker} -> {p}")
    if not p.is_dir():
        raise FileNotFoundError(p)
    return p


def _stats(xs: list[float]) -> dict[str, Any]:
    if not xs:
        return {"n": 0}
    a = np.asarray(xs, dtype=float)
    return {
        "n": int(a.size),
        "min": float(np.min(a)),
        "p10": float(np.quantile(a, 0.10)),
        "median": float(np.median(a)),
        "p90": float(np.quantile(a, 0.90)),
        "max": float(np.max(a)),
    }


def _stats_extended(xs: list[float]) -> dict[str, Any]:
    d = _stats(xs)
    if not xs:
        return d
    a = np.asarray(xs, dtype=float)
    d.update({"p01": float(np.quantile(a, 0.01)), "p25": float(np.quantile(a, 0.25)), "p75": float(np.quantile(a, 0.75)), "p99": float(np.quantile(a, 0.99))})
    return d


def _identity_family(fields: list[Any], base: dict[str, Any]) -> dict[str, Any]:
    pair = build_identity_pair(base["caps"])
    fam = evaluate_family(pair, [], fields, base["validity"], float(base["validity"]["inverse_roundtrip_tolerance"]), base["operator"]["space"], base["operator"]["numerical"])
    if int(fam.get("stage_index", 0)) != 5 or fam.get("J_family") is None:
        raise RuntimeError("identity family unresolved")
    return {"J_i": [float(r["J_princ"]) for r in fam["per_field"]], "J_family": float(fam["J_family"]), "J_max": float(fam["J_max"]), "structural_hash": pair["structural_hash"]}


def _verify_entry(root: Path, cfg: dict[str, Any]) -> tuple[Path, Path, dict[str, Any]]:
    p = cfg["parent_freeze"]
    run = _resolve_marker(root, p["s1_run_marker"])
    k3run = _resolve_marker(root, p["k3_marker"])
    k1 = _resolve_marker(root, p["k1_open_marker"])
    if run != k3run:
        raise RuntimeError("K3 marker must point to the authoritative S1 run")
    k3dir = run / "K3_freeze"
    if not k3dir.is_dir():
        raise FileNotFoundError(k3dir)
    k3sem = _load_json(k3dir / "K3_SEMANTIC_OUTPUT_DIGEST.json").get("semantic_output_digest")
    k3sum = _load_json(k3dir / "K3_FREEZE_SUMMARY.json")
    k3boundary = _load_json(k3dir / "K3_DATA_BOUNDARY_FREEZE.json")
    k2csum = _load_json(run / "K2C_theory_bridge/K2C_scientific_summary.json")
    mem = _load_json(run / "K2A_membership_lock.json")
    clearp = root / mem["clear_membership_index"]
    unrp = root / mem["unresolved_membership_index"]
    k2c_branch = run / "K2C_theory_bridge/K2C_branch_results.jsonl"
    k2b_branch = run / "K2B_diagnostics/K2B_diagnostic_branch_results.jsonl"
    k2b_identity = run / "K2B_diagnostics/K2B_diagnostic_identity_baselines.json"
    k2c_source = root / "phases/p13/coefficient_law_raw_xt/src/p13rawxt/s1_k2c_theory_bridge.py"
    k2c_protocol = root / "phases/p13/coefficient_law_raw_xt/configs/p13_s1_k2c_protocol.json"
    cal_source = root / "phases/p13/coefficient_law_raw_xt/src/p13rawxt/calibration_instruments.py"
    s0k2_protocol = root / "phases/p13/coefficient_law_raw_xt/configs/p13_s0_k2_protocol.json"
    gates = {
        "K3_PASS": (run / "K3_OVERALL_STATUS.txt").read_text().strip() == "PASS",
        "K3_semantic_exact": k3sem == p["expected_K3_semantic_digest"] == EXPECTED_K3_SEMANTIC,
        "K2C_semantic_exact": k2csum.get("semantic_output_digest") == p["expected_K2C_semantic_digest"] == EXPECTED_K2C_SEMANTIC,
        "clear_membership_sha_exact": clearp.is_file() and _sha256_path(clearp) == p["expected_clear_membership_sha256"] == EXPECTED_CLEAR_SHA,
        "unresolved_membership_sha_exact": unrp.is_file() and _sha256_path(unrp) == p["expected_unresolved_membership_sha256"] == EXPECTED_UNRESOLVED_SHA,
        "clear_count_exact": int(mem["clear_FULL_branch_count"]) == int(p["expected_clear_count"]) == EXPECTED_CLEAR_COUNT,
        "unresolved_count_exact": int(mem["unresolved_FULL_branch_count"]) == int(p["expected_unresolved_count"]) == EXPECTED_UNRESOLVED_COUNT,
        "K3_DEV_unopened": not bool(k3sum.get("DEVELOPMENT_opened")) and not bool(k3boundary.get("DEVELOPMENT_opened", False)),
        "K3_SEALED_unopened": not bool(k3sum.get("SEALED_opened")) and not bool(k3boundary.get("SEALED_opened", False)),
        "K3_boundary_DEV_coef_unopened": "UNOPENED" in str(k3boundary.get("DEVELOPMENT_COEF", "")),
        "K3_boundary_DEV_response_unopened": "UNOPENED" in str(k3boundary.get("DEVELOPMENT_RESPONSE", "")),
        "K3_boundary_SEALED_coef_unopened": "UNOPENED" in str(k3boundary.get("SEALED_FINAL_COEF", "")),
        "K3_boundary_SEALED_response_unopened": "UNOPENED" in str(k3boundary.get("SEALED_FINAL_RESPONSE", "")),
        "K2C_branch_results_exact": k2c_branch.is_file() and _sha256_path(k2c_branch) == p["expected_K2C_branch_results_sha256"],
        "K2B_branch_results_exact": k2b_branch.is_file() and _sha256_path(k2b_branch) == p["expected_K2B_branch_results_sha256"],
        "K2B_identity_baselines_exact": k2b_identity.is_file() and _sha256_path(k2b_identity) == p["expected_K2B_identity_baselines_sha256"],
        "K2C_source_exact": k2c_source.is_file() and _sha256_path(k2c_source) == p["expected_K2C_source_sha256"],
        "K2C_protocol_exact": k2c_protocol.is_file() and _sha256_path(k2c_protocol) == p["expected_K2C_protocol_sha256"],
        "calibration_instruments_source_exact": cal_source.is_file() and _sha256_path(cal_source) == p["expected_calibration_instruments_sha256"],
        "S0_K2_protocol_exact": s0k2_protocol.is_file() and _sha256_path(s0k2_protocol) == p["expected_S0_K2_protocol_sha256"],
        "K3_membership_clear_exact": int(k3sum["cohorts"]["clear_count"]) == EXPECTED_CLEAR_COUNT,
        "K3_membership_unresolved_exact": int(k3sum["cohorts"]["unresolved_count"]) == EXPECTED_UNRESOLVED_COUNT,
    }
    if not all(gates.values()):
        raise RuntimeError(f"PF0 entry gate failed: {gates}")
    return run, k1, {
        "gates": gates,
        "authoritative_S1_run": str(run.relative_to(root)),
        "K3_semantic_digest": k3sem,
        "K2C_semantic_digest": k2csum.get("semantic_output_digest"),
        "membership": mem,
        "K3_freeze_remains_immutable": True,
        "audit_archives_used_as_runtime_input": False,
    }


def _membership_readout(root: Path, run: Path, k1: Path, base: dict[str, Any], mem: dict[str, Any], tau: float) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    train33 = load_field_views(k1, "TRAIN_OPERATOR", 33, 65)
    train65 = load_field_views(k1, "TRAIN_OPERATOR", 65, 65)
    id33 = _identity_family(train33, base)
    id65 = _identity_family(train65, base)
    frozen_id = _load_json(run / "identity_train_G33.json")
    if [float(x).hex() for x in id33["J_i"]] != [float(x).hex() for x in frozen_id["J_i"]]:
        raise RuntimeError("recomputed TRAIN G33 identity baseline differs from frozen S1 identity")

    locators = _load_membership(root, run, mem)
    clear_candidates: list[dict[str, Any]] = []
    clear_rows: list[dict[str, Any]] = []
    unresolved_rows: list[dict[str, Any]] = []
    for loc in locators:
        cand = _candidate_from_locator(root, loc)
        b = cand["branch"]
        ji = [float(x) for x in b["J_i"]]
        q = operator_qualification(ji, id33["J_i"], True, True, tau)
        expected = loc["_membership_status"]
        if q["status"] != expected:
            raise RuntimeError(f"PF0 qualification mismatch {b['scientific_branch_id']}: {q['status']} != {expected}")
        fam_ratio = float(q["family_ratio"])
        field_ratios = [float(x) for x in q["field_ratios"]]
        raw_margin = min(0.5 - fam_ratio, min(1.0 - x for x in field_ratios))
        clear_margin = min(0.5 * (1.0 - tau) - fam_ratio, min((1.0 - tau) - x for x in field_ratios))
        row = {
            "scientific_branch_id": b["scientific_branch_id"],
            "arm": b["arm"],
            "paired_seed": int(b["paired_seed"]),
            "proposal_index": int(b["proposal_index"]),
            "membership_status": expected,
            "J_family_G33": float(b["J_family"]),
            "formal_family_ratio_G33": fam_ratio,
            "field_ratios_G33": field_ratios,
            "raw_hard_gate_margin": float(raw_margin),
            "clear_margin_to_tau_boundary": float(clear_margin),
        }
        if expected == "OPERATOR_QUALIFIED_TRAIN":
            clear_rows.append(row)
            clear_candidates.append(cand)
        else:
            unresolved_rows.append(row)
    if len(clear_rows) != EXPECTED_CLEAR_COUNT or len(unresolved_rows) != EXPECTED_UNRESOLVED_COUNT:
        raise RuntimeError("PF0 membership readout count mismatch")

    k2a = _load_json(run / "K2A_scientific_summary.json")
    unit_rows = []
    global_best = None
    global_best_full = None
    for key, u in sorted(k2a["unit_summaries"].items()):
        seed = int(key.split("/")[0].split("_")[1])
        arm = key.split("/")[1]
        p8192 = u["proposal_checkpoints"]["8192"]
        b8192 = u["branch_checkpoints"]["8192"]
        best = u["best_branch_records"]["any"]
        formal_ratio = None if best is None else float(best["J_family"]) / float(id33["J_family"])
        rec = {
            "paired_seed": seed,
            "arm": arm,
            "structural_proposals": 8192,
            "F4_scientific_branches": int(b8192["branches"]),
            "F4_scientific_branches_per_proposal": float(b8192["branches"]) / 8192.0,
            "evaluator_calls": int(p8192["evaluator_calls"]),
            "worker_cpu_seconds": float(p8192["worker_cpu_seconds"]),
            "best_F4_J_family": None if best is None else float(best["J_family"]),
            "best_F4_formal_ratio": formal_ratio,
            "best_F4_branch_id": None if best is None else best["scientific_branch_id"],
            "clear_count": int(b8192["clear"]),
            "unresolved_count": int(b8192["unresolved"]),
        }
        unit_rows.append(rec)
        if rec["best_F4_J_family"] is not None and (global_best is None or rec["best_F4_J_family"] < global_best["J_family"]):
            global_best = {"J_family": rec["best_F4_J_family"], "formal_ratio": formal_ratio, "scientific_branch_id": rec["best_F4_branch_id"], "paired_seed": seed, "arm": arm}
        if arm in {"FULL-V1", "FULL-V2"} and rec["best_F4_J_family"] is not None and (global_best_full is None or rec["best_F4_J_family"] < global_best_full["J_family"]):
            global_best_full = {"J_family": rec["best_F4_J_family"], "formal_ratio": formal_ratio, "scientific_branch_id": rec["best_F4_branch_id"], "paired_seed": seed, "arm": arm}
    best_clear = min(clear_rows, key=lambda r: r["J_family_G33"])

    def arm_totals(arm: str) -> dict[str, Any]:
        xs = [r for r in unit_rows if r["arm"] == arm]
        return {
            "arm": arm,
            "structural_proposals": sum(r["structural_proposals"] for r in xs),
            "F4_scientific_branches": sum(r["F4_scientific_branches"] for r in xs),
            "F4_scientific_branches_per_proposal": sum(r["F4_scientific_branches"] for r in xs) / max(sum(r["structural_proposals"] for r in xs), 1),
            "evaluator_calls": sum(r["evaluator_calls"] for r in xs),
            "worker_cpu_seconds": sum(r["worker_cpu_seconds"] for r in xs),
            "clear_count": sum(r["clear_count"] for r in xs),
            "unresolved_count": sum(r["unresolved_count"] for r in xs),
            "best_F4_J_family": min((r["best_F4_J_family"] for r in xs if r["best_F4_J_family"] is not None), default=None),
        }
    arm_summary = {arm: arm_totals(arm) for arm in ["NULL-V2", "FULL-V1", "FULL-V2"]}
    v1 = arm_summary["FULL-V1"]; v2 = arm_summary["FULL-V2"]
    throughput_ratio = float(v2["F4_scientific_branches_per_proposal"] / v1["F4_scientific_branches_per_proposal"])

    k2b_id_path = run / "K2B_diagnostics/K2B_diagnostic_identity_baselines.json"
    k2b_results = run / "K2B_diagnostics/K2B_diagnostic_branch_results.jsonl"
    if not k2b_id_path.is_file() or not k2b_results.is_file():
        raise FileNotFoundError("K2B frozen diagnostic identity/results missing")
    k2b_identity = _load_json(k2b_id_path)
    within_abs: list[float] = []
    cross_abs: list[float] = []
    with k2b_results.open() as f:
        for line in f:
            if not line.strip():
                continue
            x = json.loads(line)
            if x.get("TRAIN_membership_status") != "OPERATOR_QUALIFIED_TRAIN":
                continue
            w = x["transfer"]["within_family_G65"].get("J_RMS")
            c = x["transfer"]["cross_family_G65"].get("J_RMS")
            if w is not None and math.isfinite(float(w)): within_abs.append(float(w))
            if c is not None and math.isfinite(float(c)): cross_abs.append(float(c))

    summary = {
        "TRAIN_identity_G33": id33,
        "TRAIN_identity_G65": id65,
        "clear_formal_ratio": _stats_extended([r["formal_family_ratio_G33"] for r in clear_rows]),
        "unresolved_formal_ratio": _stats_extended([r["formal_family_ratio_G33"] for r in unresolved_rows]),
        "clear_raw_hard_gate_margin": _stats_extended([r["raw_hard_gate_margin"] for r in clear_rows]),
        "clear_margin_to_tau_boundary": _stats_extended([r["clear_margin_to_tau_boundary"] for r in clear_rows]),
        "global_best_F4_all_arms": global_best,
        "global_best_FULL_F4": global_best_full,
        "global_best_clear": {k: best_clear[k] for k in ["scientific_branch_id", "arm", "paired_seed", "proposal_index", "J_family_G33", "formal_family_ratio_G33"]},
        "per_unit": unit_rows,
        "per_arm": arm_summary,
        "FULL_V2_over_FULL_V1_F4_branch_throughput_ratio": throughput_ratio,
        "K2B_absolute_identity_baselines": k2b_identity,
        "K2B_clear_candidate_absolute_within_G65_J_RMS": _stats_extended(within_abs),
        "K2B_clear_candidate_absolute_cross_G65_J_RMS": _stats_extended(cross_abs),
        "interpretation_boundary": "F4 scientific-branch throughput is not independent-solution count and has no candidate-membership authority",
    }
    return summary, clear_rows, unresolved_rows, {"clear_candidates": clear_candidates, "train33": train33, "train65": train65, "id33": id33, "id65": id65}


def _load_k2c_rows(run: Path) -> dict[str, dict[str, Any]]:
    p = run / "K2C_theory_bridge/K2C_branch_results.jsonl"
    if not p.is_file():
        raise FileNotFoundError(p)
    out: dict[str, dict[str, Any]] = {}
    with p.open() as f:
        for line in f:
            if not line.strip(): continue
            x = json.loads(line)
            out[x["scientific_branch_id"]] = x
    return out


def _gram_geometry(train65: list[Any], k2c_rows: dict[str, dict[str, Any]], clear_rows: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    basis = _alignment_basis(train65, 65, 1e-8)
    B = np.asarray(basis["B_weighted"], float)
    G = B.T @ B
    cstar = np.asarray(basis["theory_coefficients"], float)
    theory_g2 = float(cstar @ G @ cstar)
    if int(basis["metadata"]["rank"]) != 6 or theory_g2 <= 0:
        raise RuntimeError("PF0 Claim-II Gram basis not full-rank/positive")
    theory_leave_one_out = []
    for j in range(6):
        v = cstar.copy(); v[j] = 0.0
        vg2 = float(v @ G @ v)
        dot = float(v @ G @ cstar)
        cosine = None if vg2 <= 0 else dot / math.sqrt(vg2 * theory_g2)
        resid = math.sqrt(max(float((v-cstar) @ G @ (v-cstar)), 0.0) / theory_g2)
        theory_leave_one_out.append({"channel_index": j+1, "channel": basis["metadata"]["basis"][j], "cosine_to_full_theory": cosine, "relative_G_residual": resid})

    rows: list[dict[str, Any]] = []
    decomposition_mismatch = 0
    for m in clear_rows:
        bid = m["scientific_branch_id"]
        x = k2c_rows.get(bid)
        if x is None:
            raise RuntimeError(f"missing K2C branch result {bid}")
        a = x["functional_alignment"]
        rec: dict[str, Any] = {"scientific_branch_id": bid, "arm": m["arm"], "paired_seed": m["paired_seed"], "alignment_status": a["status"]}
        if a["status"] != "RESOLVED":
            rec.update({"geometry_status": "ALIGNMENT_UNRESOLVED"})
            rows.append(rec); continue
        s = float(a["candidate_tangent_norm"]) / float(a["theory_tangent_norm"])
        c = float(a["theory_cosine"])
        r = float(a["theory_tangent_relative_residual"])
        angular_sq = max(0.0, 1.0 - c*c)
        radial_sq = (s-c)*(s-c)
        err = abs((angular_sq + radial_sq) - r*r)
        if err > 1e-8 * max(1.0, r*r):
            decomposition_mismatch += 1
        radial_fraction = None if r*r <= 1e-30 else radial_sq/(r*r)
        beta = np.asarray(a["fitted_basis_coefficients"], float)
        bg2 = float(beta @ G @ beta)
        dot = float(beta @ G @ cstar)
        gcos = None if bg2 <= 0 else dot / math.sqrt(bg2 * theory_g2)
        gres = math.sqrt(max(float((beta-cstar) @ G @ (beta-cstar)), 0.0) / theory_g2)
        rec.update({
            "geometry_status": "RESOLVED",
            "s_norm_ratio": s,
            "c_theory_cosine": c,
            "r_theory_relative_residual": r,
            "angular_squared": angular_sq,
            "radial_squared": radial_sq,
            "radial_fraction_of_r_squared": radial_fraction,
            "signed_radial_offset_s_minus_c": s-c,
            "projection_relative_residual": float(a["projection_relative_residual"]),
            "fitted_basis_coefficients": [float(v) for v in beta],
            "G_metric_coefficient_cosine_to_theory": gcos,
            "G_metric_coefficient_relative_residual": gres,
            "G_metric_norm_ratio": math.sqrt(bg2/theory_g2) if bg2 > 0 else 0.0,
        })
        rows.append(rec)
    if decomposition_mismatch:
        raise RuntimeError(f"radial/angular identity mismatch rows={decomposition_mismatch}")

    resolved = [r for r in rows if r.get("geometry_status") == "RESOLVED"]
    channel_stats = {}
    for j, name in enumerate(basis["metadata"]["basis"]):
        vals = [r["fitted_basis_coefficients"][j] for r in resolved]
        ratios = [v/float(cstar[j]) for v in vals]
        channel_stats[f"B{j+1}"] = {"name": name, "theory_coefficient": float(cstar[j]), "fitted_coefficient": _stats_extended(vals), "coefficient_over_theory": _stats_extended(ratios)}
    agg = {
        "clear_count": len(clear_rows),
        "resolved_count": len(resolved),
        "unresolved_count": len(clear_rows)-len(resolved),
        "s_norm_ratio": _stats_extended([r["s_norm_ratio"] for r in resolved]),
        "signed_radial_offset_s_minus_c": _stats_extended([r["signed_radial_offset_s_minus_c"] for r in resolved]),
        "radial_fraction_of_r_squared": _stats_extended([r["radial_fraction_of_r_squared"] for r in resolved if r["radial_fraction_of_r_squared"] is not None]),
        "fraction_radial_fraction_gt_0p5": None if not resolved else float(np.mean([float(r["radial_fraction_of_r_squared"] or 0.0)>0.5 for r in resolved])),
        "G_metric_coefficient_cosine_to_theory": _stats_extended([r["G_metric_coefficient_cosine_to_theory"] for r in resolved if r["G_metric_coefficient_cosine_to_theory"] is not None]),
        "G_metric_coefficient_relative_residual": _stats_extended([r["G_metric_coefficient_relative_residual"] for r in resolved]),
        "channel_statistics": channel_stats,
        "claim_boundary": "branchwise geometry may support radial-vs-angular mismatch description; it does not identify fitter regret or global objective identifiability",
    }
    gram = {
        "basis_metadata": basis["metadata"],
        "Gram_matrix": [[float(v) for v in row] for row in G],
        "theory_coefficients": [float(v) for v in cstar],
        "theory_G_norm_squared": theory_g2,
        "theory_leave_one_channel_out": theory_leave_one_out,
    }
    return {"aggregate": agg, "gram_lock": gram}, rows


def _reference_fit_with_progress(pair: dict[str, Any], evaluate, protocol: dict[str, Any], seed: int) -> dict[str, Any]:
    launches = []
    n = int(protocol["independent_launches"])
    for i in range(n):
        s = (int(seed) + i * 0x85EBCA6B) & 0xFFFFFFFF
        print(f"[P13 S1 PF0 witness] launch={i+1}/{n} seed={s}", flush=True)
        r = reference_launch(pair, evaluate, protocol, s, progress_label=f"PF0 TRAIN witness launch {i+1}/{n}", progress_every_calls=25, progress_every_seconds=30.0)
        launches.append(r)
    bests = [x["best"] for x in launches if x.get("best") and int(x["best"].get("stage_index", -1)) == 5 and x["best"].get("J_princ") is not None]
    agreement = None; qualified = False
    if len(bests) == len(launches) and len(bests) >= 2:
        js = [float(x["J_princ"]) for x in bests]
        agreement = (max(js)-min(js))/max(max(js), 1e-15)
        qualified = agreement <= float(protocol["launch_relative_agreement_max"])
    best = min(bests, key=lambda r: float(r["J_princ"])) if bests else None
    compact_launches = []
    for x in launches:
        b = x.get("best")
        compact_launches.append({
            "completed_calls": int(x["completed_calls"]),
            "best": None if b is None else {"stage_index": int(b["stage_index"]), "highest_feasibility_level": b.get("highest_feasibility_level"), "J_family": b.get("J_princ"), "J_XT": b.get("J_XT"), "J_XX": b.get("J_XX"), "J_max": b.get("J_max"), "theta_vector": b.get("theta_vector")},
        })
    return {"qualified": qualified, "launch_relative_agreement": agreement, "best": best, "launches": compact_launches}


def _matched_train_witness(train33: list[Any], train65: list[Any], base: dict[str, Any], cfg: dict[str, Any], best_search_J: float | None) -> dict[str, Any]:
    wc = cfg["PF0_C_matched_TRAIN_witness"]
    pair = build_full_capacity_pair(base["caps"])
    refp = base["fitter"]["reference"]
    def evaluate(pair_obj, theta):
        return evaluate_family(pair_obj, theta, train33, base["validity"], float(base["validity"]["inverse_roundtrip_tolerance"]), base["operator"]["space"], base["operator"]["numerical"])
    fit = _reference_fit_with_progress(pair, evaluate, refp, int(wc["seed"]))
    out: dict[str, Any] = {
        "name": wc["name"],
        "role": wc["role"],
        "structural_hash": pair["structural_hash"],
        "fit_grid": 33,
        "adjudication_grid": 65,
        "optimizer_protocol": refp,
        "candidate_seed_or_initialization": False,
        "qualified": bool(fit["qualified"]),
        "launch_relative_agreement": fit["launch_relative_agreement"],
        "launches": fit["launches"],
        "status": "RESOLVED" if fit["qualified"] else "WITNESS_UNRESOLVED",
        "claim_boundary": wc["claim_boundary"],
        "membership_authority": False,
    }
    if fit["qualified"] and fit["best"] is not None:
        best = fit["best"]
        theta = [float(v) for v in best["theta_vector"]]
        fam65 = evaluate_family(pair, theta, train65, base["validity"], float(base["validity"]["inverse_roundtrip_tolerance"]), base["operator"]["space"], base["operator"]["numerical"])
        out.update({
            "theta": theta,
            "J_witness_TRAIN_G33": float(best["J_princ"]),
            "J_witness_TRAIN_G65": None if fam65.get("J_family") is None else float(fam65["J_family"]),
            "G65_stage_index": int(fam65.get("stage_index", 0)),
            "best_search_F4_over_witness_G33": None if best_search_J is None else float(best_search_J)/float(best["J_princ"]),
            "attainment_ratio_label": "WITNESS_RELATIVE_ATTAINMENT_NOT_GLOBAL_REGRET",
        })
    return out


def _witness_epsilon_comparator(witness: dict[str, Any], train65: list[Any], base: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    dc = cfg["PF0_D_witness_epsilon_comparator"]
    if witness.get("status") != "RESOLVED":
        return {"status": "WITNESS_UNRESOLVED", "reason": "PF0-C matched TRAIN witness unresolved", "refit_by_epsilon": False}
    pair = build_full_capacity_pair(base["caps"])
    theta = [float(v) for v in witness["theta"]]
    rows = {}
    for eps in [float(x) for x in dc["epsilon_values"]]:
        views = _derived_views(train65, eps, float(dc["source_epsilon"]))
        identity = _identity_family(views, base)
        fam = evaluate_family(pair, theta, views, base["validity"], float(base["validity"]["inverse_roundtrip_tolerance"]), base["operator"]["space"], base["operator"]["numerical"])
        rows[f"{eps:.2f}"] = {
            "stage_index": int(fam.get("stage_index", 0)),
            "J_family": None if fam.get("J_family") is None else float(fam["J_family"]),
            "J_i": [r.get("J_princ") for r in fam.get("per_field", [])],
            "identity_J_family": float(identity["J_family"]),
            "relative_RMS_to_identity": None if fam.get("J_family") is None else float(np.sqrt(np.mean((np.asarray([r["J_princ"] for r in fam["per_field"]], float)/np.asarray(identity["J_i"], float))**2))),
        }
    j05, j10, j20 = rows["0.05"]["J_family"], rows["0.10"]["J_family"], rows["0.20"]["J_family"]
    return {
        "status": "RESOLVED" if all(rows[k]["J_family"] is not None for k in rows) else "UNRESOLVED",
        "same_frozen_theta": True,
        "refit_by_epsilon": False,
        "theta": theta,
        "rows": rows,
        "log_slope_0p05_to_0p10": _log_slope(0.05, j05, 0.10, j10),
        "log_slope_0p10_to_0p20": _log_slope(0.10, j10, 0.20, j20),
        "claim_boundary": dc["claim_boundary"],
    }


def _combine_raw(raw0: dict[str, Any], raw1: dict[str, Any], lam: float) -> dict[str, Any]:
    if abs(lam - 1.0) <= 1e-15:
        return {k: np.asarray(v).copy() for k, v in raw1.items()}
    if abs(lam) <= 1e-15:
        return {k: np.asarray(v).copy() for k, v in raw0.items()}
    out = {}
    for k in raw0:
        if k not in raw1:
            raise KeyError(k)
        out[k] = np.asarray(raw0[k], float) + float(lam) * (np.asarray(raw1[k], float) - np.asarray(raw0[k], float))
    return out


def _asp_worker_init(root_s: str, k1_s: str, base: dict[str, Any], cfg: dict[str, Any]) -> None:
    root = Path(root_s)
    for p in [root / "phases/p13/coefficient_law_raw_xt/src", root / "phases/p11/raw_xt_td/src"]:
        if str(p) not in sys.path: sys.path.insert(0, str(p))
    os.environ.update({k: "1" for k in ["OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"]})
    train65 = load_field_views(Path(k1_s), "TRAIN_OPERATOR", int(cfg["PF0_E_amplitude_sensitivity_probe"]["grid"]), 65)
    neutral65 = _derived_views(train65, 0.0, 0.2)
    global _ASP
    _ASP = {"train65": train65, "neutral65": neutral65, "base": base, "lambda_grid": [float(x) for x in cfg["PF0_E_amplitude_sensitivity_probe"]["lambda_grid"]]}


def _asp_unresolved_row(lam: float, code: str) -> dict[str, Any]:
    return {
        "lambda": float(lam),
        "stage_index": 0,
        "highest_feasibility_level": "NONE",
        "J_princ": None,
        "rejection_codes": [code],
    }


def _asp_field_curve(pair: dict[str, Any], theta: list[float], field: Any, neutral: Any, base: dict[str, Any], lambdas: list[float]) -> list[dict[str, Any]]:
    arrays = field.arrays; grid = _grid_from_arrays(arrays); xm, tm = grid.mesh
    source = _probe_source(grid, int(base["validity"]["inverse_probe_grid_per_axis"]), tuple(base["validity"]["inverse_probe_local_coordinates"]))

    # TRAIN evaluation is part of the already-frozen clear branch and lambda=1 must
    # remain an integrity check.  Any failure here is therefore fatal, not a
    # diagnostic UNRESOLVED outcome.
    raw1 = evaluate_pair_jet(pair["raw_X_AST"], pair["raw_T_AST"], theta, xm, tm, grid_coefficient_derivatives(arrays, 4), 4)
    pd1 = field.probe_interpolator.derivatives(source[:,0], source[:,1])
    pr1 = evaluate_pair_jet(pair["raw_X_AST"], pair["raw_T_AST"], theta, source[:,0], source[:,1], pd1, 4)

    # A formal TRAIN-qualified raw AST is not guaranteed to be defined on the
    # synthetic neutral medium a=1.  ASP is post-membership diagnostic evidence,
    # so neutral-baseline domain failure makes radial lambda != 1 evaluations
    # unresolved; it must not delete/rescue the branch and must not abort PF0.
    raw0 = None
    pr0 = None
    neutral_failure_code = None
    try:
        raw0 = evaluate_pair_jet(pair["raw_X_AST"], pair["raw_T_AST"], theta, xm, tm, grid_coefficient_derivatives(neutral.arrays, 4), 4)
        pd0 = neutral.probe_interpolator.derivatives(source[:,0], source[:,1])
        pr0 = evaluate_pair_jet(pair["raw_X_AST"], pair["raw_T_AST"], theta, source[:,0], source[:,1], pd0, 4)
    except ASP_DIAGNOSTIC_NUMERIC_EXCEPTIONS as exc:
        neutral_failure_code = f"ASP_NEUTRAL_BASELINE_DOMAIN_FAILURE:{type(exc).__name__}:{exc}"

    out = []
    for lam in lambdas:
        if abs(lam-1.0) <= 1e-15:
            raw = {k: np.asarray(v).copy() for k, v in raw1.items()}
            tx = np.asarray(pr1["X"], float); tt = np.asarray(pr1["T"], float)
        else:
            if neutral_failure_code is not None:
                out.append(_asp_unresolved_row(lam, neutral_failure_code))
                continue
            assert raw0 is not None and pr0 is not None
            try:
                raw = _combine_raw(raw0, raw1, lam)
                tx = np.asarray(pr0["X"], float) + lam*(np.asarray(pr1["X"], float)-np.asarray(pr0["X"], float))
                tt = np.asarray(pr0["T"], float) + lam*(np.asarray(pr1["T"], float)-np.asarray(pr0["T"], float))
            except ASP_DIAGNOSTIC_NUMERIC_EXCEPTIONS as exc:
                out.append(_asp_unresolved_row(lam, f"ASP_RADIAL_CONSTRUCTION_DOMAIN_FAILURE:{type(exc).__name__}:{exc}"))
                continue

        try:
            target = np.column_stack([tx, tt])
            validity = evaluate_validity_variable(raw, grid, arrays["a_d0_0"], source, target, base["validity"], float(base["validity"]["inverse_roundtrip_tolerance"]))
            if not validity["overall_valid"]:
                out.append({"lambda": lam, "stage_index": int(validity["stage_index"]), "highest_feasibility_level": validity["highest_feasibility_level"], "J_princ": None, "rejection_codes": validity.get("rejection_codes", [])})
                continue
            gauged, grec = _p11_imports()["gauge_second_jet"](raw)
            if gauged is None:
                out.append({"lambda": lam, "stage_index": 4, "highest_feasibility_level": "F3", "J_princ": None, "rejection_codes": ["GAUGE_SECOND_JET_FAILURE"]})
                continue
            _, op = operator_on_variable(gauged, grid, arrays, base["operator"]["space"], base["operator"]["numerical"])
            out.append({"lambda": lam, "stage_index": 5, "highest_feasibility_level": "F4", "J_princ": float(op["J_princ"]), "rejection_codes": []})
        except ASP_DIAGNOSTIC_NUMERIC_EXCEPTIONS as exc:
            if abs(lam-1.0) <= 1e-15:
                raise RuntimeError(f"ASP lambda=1 TRAIN-domain integrity failure: {type(exc).__name__}:{exc}") from exc
            out.append(_asp_unresolved_row(lam, f"ASP_RADIAL_EVALUATION_DOMAIN_FAILURE:{type(exc).__name__}:{exc}"))
    return out


def _asp_task(task: dict[str, Any]) -> dict[str, Any]:
    w = _ASP; lambdas = w["lambda_grid"]
    all_fields = []
    for field, neutral in zip(w["train65"], w["neutral65"]):
        all_fields.append({"field_id": field.field_id, "curve": _asp_field_curve(task["pair"], task["theta"], field, neutral, w["base"], lambdas)})
    fam_curve = []
    for j, lam in enumerate(lambdas):
        vals = [r["curve"][j]["J_princ"] for r in all_fields]
        if all(v is not None and math.isfinite(float(v)) for v in vals):
            a = np.asarray(vals, float); jf = float(np.sqrt(np.mean(a*a))); jmax = float(np.max(a)); resolved = True
        else:
            jf = None; jmax = None; resolved = False
        fam_curve.append({"lambda": lam, "J_i": vals, "J_family": jf, "J_max": jmax, "all_fields_resolved": resolved})
    one = next(r for r in fam_curve if abs(float(r["lambda"])-1.0) <= 1e-15)
    frozen = float(task["frozen_K2C_eps0p20_J_family"])
    if one["J_family"] is None:
        raise RuntimeError(f"ASP lambda=1 unresolved for clear branch {task['branch_id']}")
    rel = abs(float(one["J_family"])-frozen)/max(abs(frozen), 1e-15)
    if rel > 1e-10:
        raise RuntimeError(f"ASP lambda=1 integrity mismatch {task['branch_id']} rel={rel}")
    all_resolved = all(bool(r["all_fields_resolved"]) for r in fam_curve)
    resolved_rows = [r for r in fam_curve if r["J_family"] is not None]
    star = min(resolved_rows, key=lambda r: (float(r["J_family"]), float(r["lambda"]))) if resolved_rows else None
    if all_resolved and star is not None:
        radial_slack = 1.0 - float(star["J_family"])/float(one["J_family"])
        vals = [float(r["J_family"]) for r in fam_curve]
        span = (max(vals)-min(vals))/max(float(one["J_family"]), 1e-15)
        boundary = float(star["lambda"]) in {min(lambdas), max(lambdas)}
        status = "RESOLVED_FULL_GRID"
    else:
        radial_slack = None; span = None; boundary = None
        neutral_domain_unresolved = any(
            any(str(code).startswith("ASP_NEUTRAL_BASELINE_DOMAIN_FAILURE:") for code in cell.get("rejection_codes", []))
            for field_row in all_fields for cell in field_row["curve"]
        )
        status = "ASP_UNRESOLVED_NEUTRAL_BASELINE_DOMAIN" if neutral_domain_unresolved else "ASP_UNRESOLVED_PARTIAL_GRID"
    return {
        "scientific_branch_id": task["branch_id"],
        "arm": task["arm"],
        "paired_seed": task["paired_seed"],
        "status": status,
        "same_AST_theta_zero_refit": True,
        "lambda_grid": lambdas,
        "family_curve": fam_curve,
        "lambda_star_on_grid": None if star is None else float(star["lambda"]),
        "radial_slack": radial_slack,
        "relative_objective_span": span,
        "minimum_at_grid_boundary": boundary,
        "lambda_1_relative_integrity_error": rel,
        "membership_authority": False,
    }


def _validate_asp_partial_row_for_restart(row: dict[str, Any], valid_branch_ids: set[str], lambdas: list[float]) -> None:
    bid = row.get("scientific_branch_id")
    if bid not in valid_branch_ids:
        raise RuntimeError(f"PF0 ASP restart row has unknown branch id: {bid}")
    if row.get("same_AST_theta_zero_refit") is not True:
        raise RuntimeError(f"PF0 ASP restart row violates zero-refit lock: {bid}")
    if [float(x) for x in row.get("lambda_grid", [])] != [float(x) for x in lambdas]:
        raise RuntimeError(f"PF0 ASP restart lambda-grid mismatch: {bid}")
    rel = row.get("lambda_1_relative_integrity_error")
    if rel is None or not math.isfinite(float(rel)) or float(rel) > 1e-10:
        raise RuntimeError(f"PF0 ASP restart lambda=1 integrity invalid: {bid} rel={rel}")
    if row.get("status") not in {"RESOLVED_FULL_GRID", "ASP_UNRESOLVED_PARTIAL_GRID", "ASP_UNRESOLVED_NEUTRAL_BASELINE_DOMAIN"}:
        raise RuntimeError(f"PF0 ASP restart row status unsupported: {bid} status={row.get('status')}")


def _restart_lock_migration_allowed(old: dict[str, Any], new: dict[str, Any]) -> bool:
    if old.get("PF0_source_sha256") != PF0_V1_ORIGINAL_SOURCE_SHA256:
        return False
    old2 = dict(old); new2 = dict(new)
    old2.pop("PF0_source_sha256", None); new2.pop("PF0_source_sha256", None)
    return old2 == new2


def _asp_run(root: Path, run: Path, k1: Path, base: dict[str, Any], cfg: dict[str, Any], clear_candidates: list[dict[str, Any]], k2c_rows: dict[str, dict[str, Any]], workers: int) -> tuple[dict[str, Any], Path]:
    work = run / "PF0_work"; work.mkdir(exist_ok=True)
    lockp = work / "restart_lock.json"
    clearp = run / "K2A_FROZEN_FULL_CLEAR_MEMBERSHIP.jsonl"
    source = Path(__file__).resolve(); cfgp = root / "phases/p13/coefficient_law_raw_xt/configs/p13_s1_pf0_protocol.json"
    train65 = load_field_views(k1, "TRAIN_OPERATOR", 65, 65)
    lock = {
        "PF0_config_sha256": _sha256_path(cfgp),
        "PF0_source_sha256": _sha256_path(source),
        "K3_semantic_digest": EXPECTED_K3_SEMANTIC,
        "clear_membership_sha256": _sha256_path(clearp),
        "K2C_branch_results_sha256": _sha256_path(run / "K2C_theory_bridge/K2C_branch_results.jsonl"),
        "TRAIN_G65_semantic_digests": [search_object_semantic_digest(f.arrays) for f in train65],
        "lambda_grid": cfg["PF0_E_amplitude_sensitivity_probe"]["lambda_grid"],
    }
    restart_lock_migrated = False
    if lockp.is_file():
        prior_lock = _load_json(lockp)
        if prior_lock != lock:
            if _restart_lock_migration_allowed(prior_lock, lock):
                restart_lock_migrated = True
            else:
                raise RuntimeError("PF0 restart lock mismatch; refusing stale ASP partial results")
    else:
        _write_json(lockp, lock)
    partial = work / "asp_partial_results.jsonl"
    completed: dict[str, dict[str, Any]] = {}
    valid_branch_ids = {c["branch"]["scientific_branch_id"] for c in clear_candidates}
    lambdas = [float(x) for x in cfg["PF0_E_amplitude_sensitivity_probe"]["lambda_grid"]]
    if partial.is_file():
        good = []
        for line in partial.read_text().splitlines():
            if not line.strip(): continue
            try: x = json.loads(line)
            except json.JSONDecodeError: continue
            _validate_asp_partial_row_for_restart(x, valid_branch_ids, lambdas)
            completed[x["scientific_branch_id"]] = x; good.append(json.dumps(x, sort_keys=True, separators=(",", ":")))
        partial.write_text("\n".join(good) + ("\n" if good else ""))
    if restart_lock_migrated:
        _write_json(lockp, lock)
        print(f"[P13 S1 PF0 ASP] restart_migration=PASS inherited_rows={len(completed)} from_source_sha={PF0_V1_ORIGINAL_SOURCE_SHA256}", flush=True)
    tasks = []
    for c in clear_candidates:
        bid = c["branch"]["scientific_branch_id"]
        if bid in completed: continue
        k2 = k2c_rows[bid]
        jf = k2["epsilon_scaling"]["0.20"]["J_family"]
        if jf is None:
            raise RuntimeError(f"clear branch K2C epsilon=0.20 unresolved {bid}")
        tasks.append({"branch_id": bid, "arm": c["branch"]["arm"], "paired_seed": int(c["branch"]["paired_seed"]), "pair": c["pair"], "theta": c["theta"], "frozen_K2C_eps0p20_J_family": float(jf)})
    start = time.perf_counter(); done = len(completed); total = len(clear_candidates); last = start
    if tasks:
        with partial.open("a", encoding="utf-8") as f, ProcessPoolExecutor(max_workers=max(1, min(int(workers), 16)), initializer=_asp_worker_init, initargs=(str(root), str(k1), base, cfg)) as ex:
            futs = {ex.submit(_asp_task, t): t["branch_id"] for t in tasks}
            for fut in as_completed(futs):
                r = fut.result(); completed[r["scientific_branch_id"]] = r
                f.write(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n"); f.flush(); done += 1
                now = time.perf_counter()
                if done == total or now-last >= 20.0:
                    rate = max(done-(total-len(tasks)), 1)/max(now-start, 1e-9); eta = (total-done)/max(rate, 1e-12)
                    print(f"[P13 S1 PF0 ASP] processed={done}/{total} current_branch={r['scientific_branch_id'][:12]} elapsed={now-start:.1f}s rate={rate:.3f}/s ETA={eta/60:.1f}m", flush=True); last = now
    missing = [c["branch"]["scientific_branch_id"] for c in clear_candidates if c["branch"]["scientific_branch_id"] not in completed]
    if missing: raise RuntimeError(f"PF0 ASP missing results n={len(missing)}")
    final = run / "PF0_postfreeze/PF0_ASP_branch_results.jsonl"
    final.parent.mkdir(exist_ok=True)
    rows = [completed[c["branch"]["scientific_branch_id"]] for c in clear_candidates]
    with final.open("w", encoding="utf-8") as f:
        for r in rows: f.write(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n")
    resolved = [r for r in rows if r["status"] == "RESOLVED_FULL_GRID"]
    neutral_unresolved = [r for r in rows if r["status"] == "ASP_UNRESOLVED_NEUTRAL_BASELINE_DOMAIN"]
    partial_unresolved = [r for r in rows if r["status"] == "ASP_UNRESOLVED_PARTIAL_GRID"]
    agg = {
        "rows": len(rows),
        "resolved_full_grid": len(resolved),
        "unresolved_neutral_baseline_domain": len(neutral_unresolved),
        "unresolved_other_partial_grid": len(partial_unresolved),
        "unresolved_partial_grid": len(rows)-len(resolved),
        "lambda_star_on_grid": _stats_extended([float(r["lambda_star_on_grid"]) for r in resolved]),
        "radial_slack": _stats_extended([float(r["radial_slack"]) for r in resolved]),
        "relative_objective_span": _stats_extended([float(r["relative_objective_span"]) for r in resolved]),
        "fraction_minimum_at_grid_boundary": None if not resolved else float(np.mean([bool(r["minimum_at_grid_boundary"]) for r in resolved])),
        "lambda_1_integrity_error": _stats_extended([float(r["lambda_1_relative_integrity_error"]) for r in rows]),
        "claim_boundary": "ASP measures realized functional radial objective sensitivity only; it does not by itself prove theta-fitter regret or global identifiability failure",
    }
    return agg, final


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows: f.write(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n")


def _source_manifest(root: Path) -> dict[str, Any]:
    rels = [
        "phases/p13/coefficient_law_raw_xt/configs/p13_s1_pf0_protocol.json",
        "phases/p13/coefficient_law_raw_xt/src/p13rawxt/s1_pf0_postfreeze_diagnostics.py",
        "phases/p13/coefficient_law_raw_xt/scripts/run_p13_s1_pf0.sh",
        "phases/p13/coefficient_law_raw_xt/scripts/package_p13_s1_pf0_audit.sh",
        "phases/p13/coefficient_law_raw_xt/tests/test_p13_s1_pf0.py",
        "phases/p13/coefficient_law_raw_xt/docs/P13_S1_PF0_POSTFREEZE_ATTAINMENT_AND_RADIAL_DIAGNOSTICS.md",
        "phases/p13/coefficient_law_raw_xt/docs/P13_S1_PF0R1_ASP_NEUTRAL_DOMAIN_REPAIR.md",
        "phases/p13/coefficient_law_raw_xt/src/p13rawxt/s1_k2c_theory_bridge.py",
        "phases/p13/coefficient_law_raw_xt/src/p13rawxt/calibration_instruments.py",
    ]
    rows = []
    for rel in rels:
        p = root / rel
        rows.append({"path": rel, "bytes": p.stat().st_size, "sha256": _sha256_path(p)})
    return {"files": rows}


def _update_rolling_context(root: Path, summary: dict[str, Any]) -> None:
    p = root / "P13_S1_ROLLING_CONTEXT.md"
    if not p.is_file(): return
    marker = "<!-- PF0_FORMAL_RESULT -->"
    block = f'''## S1-PF0 — post-freeze attainment and radial diagnostics\n\n- role: `REFERENCE / DESCRIPTIVE / POST-MEMBERSHIP`\n- K3 base freeze semantic digest remains immutable: `{summary['parent_K3_semantic_digest']}`\n- `OVERALL_STATUS`: **{summary['OVERALL_STATUS']}**\n- clear/unresolved membership remains byte-identical: `{summary['membership_clear_count']} / {summary['membership_unresolved_count']}`\n- DEVELOPMENT / SEALED / response opened: `False`\n- new search / PLCP / 32768 continuation / candidate refit: `False`\n- PF0 matched TRAIN witness status: `{summary['matched_TRAIN_witness_status']}`\n- ASP complete cohort rows: `{summary['ASP_rows']}`\n- next action on PASS: `{summary['NEXT_ACTION']}`\n\n{marker}\n'''
    text = p.read_text()
    if marker in text: text = text.replace(marker, block)
    else: text = text.rstrip() + "\n\n" + block
    _write_text(p, text)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-root", default=".")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--test-limit", type=int, default=None, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    root = Path(args.project_root).resolve()
    for p in [root / "phases/p13/coefficient_law_raw_xt/src", root / "phases/p11/raw_xt_td/src"]:
        if str(p) not in sys.path: sys.path.insert(0, str(p))
    cfgp = root / "phases/p13/coefficient_law_raw_xt/configs/p13_s1_pf0_protocol.json"
    cfg = _load_json(cfgp)
    run, k1, entry = _verify_entry(root, cfg)
    outdir = run / "PF0_postfreeze"; outdir.mkdir(exist_ok=True)
    if (run / "PF0_OVERALL_STATUS.txt").is_file() and (run / "PF0_OVERALL_STATUS.txt").read_text().strip() == "PASS":
        print("[P13 S1 PF0] OVERALL_STATUS=PASS (already frozen; no recomputation)", flush=True)
        return 0

    base = _load_json(root / "phases/p13/coefficient_law_raw_xt/configs/p13_s0_k2_protocol.json")
    base["caps"] = _load_json(root / "phases/p13/coefficient_law_raw_xt/configs/p13_s1_k1_protocol.json")["caps"]
    tau = float(cfg["PF0_A_frozen_ledger_attainment"]["tau_num"])
    workers = max(1, min(int(args.workers), 16))
    os.environ.update({k: "1" for k in ["OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"]})
    start = time.perf_counter()
    print(f"[P13 S1 PF0] stage=entry_lock status=PASS authoritative_run={run.relative_to(root)}", flush=True)

    print("[P13 S1 PF0] stage=A frozen-ledger attainment readout", flush=True)
    attainment, clear_rows, unresolved_rows, aux = _membership_readout(root, run, k1, base, entry["membership"], tau)
    if args.test_limit is not None:
        n = int(args.test_limit); clear_rows = clear_rows[:n]; aux["clear_candidates"] = aux["clear_candidates"][:n]
    _write_jsonl(outdir / "PF0_membership_attainment_rows.jsonl", clear_rows + unresolved_rows)
    _write_json(outdir / "PF0_attainment_summary.json", attainment)

    print("[P13 S1 PF0] stage=B theory-space branchwise geometry", flush=True)
    k2c_rows = _load_k2c_rows(run)
    geometry, geom_rows = _gram_geometry(aux["train65"], k2c_rows, clear_rows)
    _write_jsonl(outdir / "PF0_theory_geometry_rows.jsonl", geom_rows)
    _write_json(outdir / "PF0_theory_geometry_aggregate.json", geometry["aggregate"])
    _write_json(outdir / "PF0_theory_gram_lock.json", geometry["gram_lock"])

    print("[P13 S1 PF0] stage=C matched post-freeze TRAIN witness", flush=True)
    best_search_J = attainment["global_best_FULL_F4"]["J_family"] if attainment.get("global_best_FULL_F4") else None
    witness = _matched_train_witness(aux["train33"], aux["train65"], base, cfg, best_search_J)
    _write_json(outdir / "PF0_matched_TRAIN_witness.json", witness)

    print("[P13 S1 PF0] stage=D witness epsilon comparator", flush=True)
    witness_eps = _witness_epsilon_comparator(witness, aux["train65"], base, cfg)
    _write_json(outdir / "PF0_witness_epsilon_comparator.json", witness_eps)

    print("[P13 S1 PF0] stage=E amplitude-sensitivity probe", flush=True)
    asp_agg, asp_final = _asp_run(root, run, k1, base, cfg, aux["clear_candidates"], k2c_rows, workers)
    _write_json(outdir / "PF0_ASP_aggregate.json", asp_agg)

    print("[P13 S1 PF0] stage=F pre-DEV prediction lock", flush=True)
    predictions = {
        "frozen_before_DEVELOPMENT": True,
        "predictions_are_descriptive_not_gates": True,
        "membership_unchanged": True,
        "H1_radial_geometry": "If PF0-B branchwise population evidence is predominantly radial, record that as a descriptive tangent-geometry result without uniquely attributing it to fitter or objective geometry.",
        "H2_transfer_association": "On future S2-K0B, report descriptive Spearman association (no p-value, no membership use) between PF0 radial_slack and log(rho_transfer), and between abs(log(lambda_star)) and log(rho_transfer) for branches with resolved quantities; expected direction is positive degradation association.",
        "H3_epsilon_comparator": "Compare candidate and matched-witness empirical epsilon curves qualitatively; no slope threshold or gate is authorized.",
        "H4_V2_throughput": "V2's extra F4 scientific-branch throughput is not predicted to imply superior DEVELOPMENT transfer.",
        "forbidden_use": ["candidate selection", "candidate deletion", "candidate rescue", "threshold change", "search continuation", "response decision"],
    }
    _write_json(outdir / "PF0_PRE_DEV_PREDICTIONS.json", predictions)

    boundary = {
        "status": "PASS",
        "K3_base_freeze_mutated": False,
        "clear_membership_changed": False,
        "unresolved_membership_changed": False,
        "DEVELOPMENT_read": False,
        "SEALED_read": False,
        "response_outcomes_read": False,
        "historical_response_read": False,
        "new_formal_search": False,
        "PLCP_run": False,
        "continuation_32768_run": False,
        "candidate_refit": False,
        "ASP_or_theory_or_witness_has_membership_authority": False,
        "audit_archive_used_as_runtime_input": False,
    }
    _write_json(outdir / "PF0_data_boundary_guard.json", boundary)
    memlock = {
        "clear_path": entry["membership"]["clear_membership_index"],
        "clear_sha256": _sha256_path(root / entry["membership"]["clear_membership_index"]),
        "clear_count": int(entry["membership"]["clear_FULL_branch_count"]),
        "unresolved_path": entry["membership"]["unresolved_membership_index"],
        "unresolved_sha256": _sha256_path(root / entry["membership"]["unresolved_membership_index"]),
        "unresolved_count": int(entry["membership"]["unresolved_FULL_branch_count"]),
        "PF0_may_modify_membership": False,
    }
    _write_json(outdir / "PF0_membership_immutability.json", memlock)
    _write_json(outdir / "PF0_entry_provenance.json", entry)
    src = _source_manifest(root); _write_json(outdir / "PF0_source_manifest.json", src)

    output_paths = [
        outdir / "PF0_membership_attainment_rows.jsonl",
        outdir / "PF0_attainment_summary.json",
        outdir / "PF0_theory_geometry_rows.jsonl",
        outdir / "PF0_theory_geometry_aggregate.json",
        outdir / "PF0_theory_gram_lock.json",
        outdir / "PF0_matched_TRAIN_witness.json",
        outdir / "PF0_witness_epsilon_comparator.json",
        asp_final,
        outdir / "PF0_ASP_aggregate.json",
        outdir / "PF0_PRE_DEV_PREDICTIONS.json",
        outdir / "PF0_data_boundary_guard.json",
        outdir / "PF0_membership_immutability.json",
    ]
    sem_basis = {
        "stage": cfg["stage"],
        "parent_K3_semantic_digest": EXPECTED_K3_SEMANTIC,
        "config_sha256": _sha256_path(cfgp),
        "membership": memlock,
        "outputs": [{"path": str(p.relative_to(root)), "sha256": _sha256_path(p), "bytes": p.stat().st_size} for p in output_paths],
        "data_boundary": boundary,
        "source_manifest": src,
        "next_action": cfg["next_on_pass"],
    }
    sem = sha256_bytes(canonical_json_bytes(sem_basis))
    _write_json(outdir / "PF0_semantic_output_digest.json", {**sem_basis, "semantic_output_digest": sem})
    summary = {
        "OVERALL_STATUS": "PASS",
        "NEXT_ACTION": cfg["next_on_pass"],
        "role": cfg["role"],
        "authoritative_S1_run": str(run.relative_to(root)),
        "parent_K3_semantic_digest": EXPECTED_K3_SEMANTIC,
        "PF0_semantic_output_digest": sem,
        "membership_clear_count": int(memlock["clear_count"]),
        "membership_unresolved_count": int(memlock["unresolved_count"]),
        "membership_unchanged": True,
        "matched_TRAIN_witness_status": witness["status"],
        "ASP_rows": int(asp_agg["rows"]),
        "DEVELOPMENT_or_SEALED_opened": False,
        "response_outcomes_opened": False,
        "new_search_or_refit": False,
        "K3_freeze_remains_immutable": True,
    }
    _write_json(outdir / "PF0_scientific_summary.json", summary)
    _write_text(run / "PF0_OVERALL_STATUS.txt", "PASS\n")
    _write_text(run / "PF0_NEXT_ACTION.txt", cfg["next_on_pass"] + "\n")
    _write_text(root / "phases/p13/coefficient_law_raw_xt/runs/LATEST_P13_S1_PF0_RUN.txt", str(run.relative_to(root)) + "\n")
    _update_rolling_context(root, summary)
    work = run / "PF0_work"
    if work.exists(): shutil.rmtree(work)
    print(f"[P13 S1 PF0] stage=complete processed_clear={len(clear_rows)}/{EXPECTED_CLEAR_COUNT if args.test_limit is None else len(clear_rows)} elapsed={time.perf_counter()-start:.1f}s OVERALL_STATUS=PASS NEXT_ACTION={cfg['next_on_pass']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
