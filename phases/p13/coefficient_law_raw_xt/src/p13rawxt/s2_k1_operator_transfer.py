from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import numpy as np

from .ast_runtime import FieldJetInterpolator
from .calibration_instruments import build_identity_pair
from .coefficients import canonical_json_bytes, search_object_semantic_digest, sha256_bytes
from .family_evaluator import FieldView, evaluate_pair_on_field, load_npz
from .s1_search_primitives import boundary_status

_WORKER: dict[str, Any] = {}


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


def _count_jsonl(path: Path) -> int:
    with path.open("rb") as f:
        return sum(1 for x in f if x.strip())


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    out = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                out.append(json.loads(line))
    return out


def _line_at(path: Path, offset: int) -> dict[str, Any]:
    with path.open("rb") as f:
        f.seek(int(offset))
        line = f.readline()
    if not line:
        raise RuntimeError(f"no JSONL line at offset {offset}: {path}")
    return json.loads(line)


def _resolve_marker(root: Path, rel_marker: str) -> Path:
    p = root / rel_marker
    if not p.is_file():
        raise FileNotFoundError(f"missing marker: {p}")
    rel = p.read_text(encoding="utf-8").strip()
    target = root / rel
    if not target.exists():
        raise FileNotFoundError(f"marker target missing: {target}")
    return target


def _verify_npz_semantic(path: Path, expected: str) -> bool:
    arrays = load_npz(path)
    return search_object_semantic_digest(arrays) == expected


def _load_dev_views(root: Path, manifest: dict[str, Any], grid: int, probe_grid: int = 65) -> list[FieldView]:
    rows = manifest["search_objects"]
    field_ids = sorted({r["field_id"] for r in rows})
    views: list[FieldView] = []
    for fid in field_ids:
        main = next(r for r in rows if r["field_id"] == fid and int(r["grid"]) == int(grid))
        probe = next(r for r in rows if r["field_id"] == fid and int(r["grid"]) == int(probe_grid))
        arrays = load_npz(root / main["path"])
        parr = load_npz(root / probe["path"])
        views.append(FieldView(fid, "DEVELOPMENT_COEF", int(grid), arrays, FieldJetInterpolator(parr, 4)))
    return views


def _verify_entry(root: Path, cfg: dict[str, Any]) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    checks: dict[str, Any] = {}
    active = root / cfg["active_context"]
    checks["active_context_sha"] = active.is_file() and _sha(active) == cfg["active_context_sha256"]

    k0run = _resolve_marker(root, cfg["k0_run_marker"])
    checks["K0_status"] = (k0run / "K0_OVERALL_STATUS.txt").is_file() and (k0run / "K0_OVERALL_STATUS.txt").read_text().strip() == "PASS"
    checks["K0_next"] = (k0run / "K0_NEXT_ACTION.txt").is_file() and (k0run / "K0_NEXT_ACTION.txt").read_text().strip() == "P13-S2-K1_COMPLETE_ZERO_SHOT_OPERATOR_TRANSFER"
    k0dir = k0run / "K0_development_open_lock"
    k0summary = _load_json(k0dir / "K0_SCIENTIFIC_SUMMARY.json")
    checks["K0_summary_zero_refit"] = k0summary.get("same_AST_theta_gauge_zero_refit") is True
    checks["K0_response_unopened"] = k0summary.get("DEVELOPMENT_response_opened") is False
    checks["K0_sealed_unopened"] = k0summary.get("SEALED_opened") is False
    checks["K0_tau"] = abs(float(k0summary.get("tau_num", math.nan)) - 0.005) < 1e-15
    k0guard = _load_json(k0dir / "K0_DATA_BOUNDARY_GUARD.json")
    checks["K0_guard"] = (
        k0guard.get("DEVELOPMENT_RESPONSE") == "SEALED_COMMITTED_UNOPENED" and
        k0guard.get("SEALED_FINAL_COEF") == "SEALED_COMMITTED_UNOPENED" and
        k0guard.get("SEALED_FINAL_RESPONSE") == "SEALED_COMMITTED_UNOPENED"
    )

    dev_manifest_path = _resolve_marker(root, cfg["k0_dev_input_marker"])
    checks["DEV_manifest_is_inside_K0"] = k0run.resolve() in dev_manifest_path.resolve().parents
    dev = _load_json(dev_manifest_path)
    checks["DEV_manifest_status"] = dev.get("status") == "PASS"
    checks["DEV_4_fields"] = int(dev.get("field_count", -1)) == 4 and len(dev.get("field_ids", [])) == 4
    checks["DEV_12_objects"] = int(dev.get("search_object_count", -1)) == 12
    needed = [r for r in dev.get("search_objects", []) if int(r["grid"]) in (33, 65)]
    checks["DEV_8_required_objects"] = len(needed) == 8
    sha_sem_ok = True
    for row in needed:
        p = root / row["path"]
        if not p.is_file() or _sha(p) != row["sha256"] or not _verify_npz_semantic(p, row["semantic_digest"]):
            sha_sem_ok = False
            break
    checks["DEV_required_SHA_semantic"] = sha_sem_ok

    for key in ("clear_membership", "unresolved_reference"):
        lock = cfg[key]
        p = root / lock["path"]
        checks[f"{key}_sha"] = p.is_file() and _sha(p) == lock["sha256"]
        checks[f"{key}_count"] = p.is_file() and _count_jsonl(p) == int(lock["count"])
    checks["complete_2307"] = int(cfg["clear_membership"]["count"]) == 2307
    checks["80_not_S2_eligible"] = cfg["unresolved_reference"].get("s2_eligible") is False
    checks["zero_refit_contract"] = all([
        cfg["zero_shot_contract"]["same_raw_AST"], cfg["zero_shot_contract"]["same_theta"],
        cfg["zero_shot_contract"]["same_deterministic_gauge"], not cfg["zero_shot_contract"]["AST_refit"],
        not cfg["zero_shot_contract"]["theta_refit"], not cfg["zero_shot_contract"]["branch_reselection"],
        not cfg["zero_shot_contract"]["amplitude_compensation"],
    ])
    checks["no_shortlist"] = all([
        cfg["candidate_governance"]["complete_2307_required"], cfg["candidate_governance"]["top_k_forbidden"],
        cfg["candidate_governance"]["pareto_forbidden"], cfg["candidate_governance"]["percentile_forbidden"],
        cfg["candidate_governance"]["target_survivor_count_forbidden"], cfg["candidate_governance"]["PF0_K2B_K2C_filter_forbidden"],
        cfg["candidate_governance"]["historical_response_information_forbidden"],
    ])
    status = "PASS" if all(bool(v) for v in checks.values()) else "FAIL"
    return k0run, dev, {"status": status, "checks": checks}


def _candidate_from_locator(root: Path, loc: dict[str, Any], membership_index: int) -> dict[str, Any]:
    b = _line_at(root / loc["branch_registry_path"], int(loc["branch_registry_byte_offset"]))
    s = _line_at(root / loc["skeleton_registry_path"], int(loc["skeleton_registry_byte_offset"]))
    if b["scientific_branch_id"] != loc["scientific_branch_id"]:
        raise RuntimeError("branch locator scientific ID mismatch")
    if b["structural_hash"] != loc["structural_hash"] or s["structural_hash"] != loc["structural_hash"]:
        raise RuntimeError("structural hash mismatch")
    if int(b["proposal_index"]) != int(s["proposal_index"]):
        raise RuntimeError("branch/skeleton proposal mismatch")
    if b.get("operator_qualification", {}).get("status") != "OPERATOR_QUALIFIED_TRAIN":
        raise RuntimeError("K1 membership contains non-clear TRAIN branch")
    gauge = b.get("deterministic_gauge", {})
    if gauge != {"application":"deterministic_per_field","optimized":False,"rule":"S0_fixed_translation_common_positive_scale"}:
        raise RuntimeError("unexpected frozen gauge semantics")
    return {
        "membership_index": int(membership_index),
        "scientific_branch_id": b["scientific_branch_id"],
        "arm": b["arm"],
        "paired_seed": int(b["paired_seed"]),
        "proposal_index": int(b["proposal_index"]),
        "structural_hash": b["structural_hash"],
        "exact_equivalence_class": b["exact_equivalence_class"],
        "pair": s["pair"],
        "theta": [float(x) for x in b["theta_vector"]],
        "theta_hex": list(b["theta_hex"]),
        "fit_provenance": b["fit_provenance"],
        "deterministic_gauge": gauge,
        "TRAIN_J_i_G33": [float(x) for x in b["J_i"]],
        "TRAIN_J_family_G33": float(b["J_family"]),
        "TRAIN_family_ratio_G33": float(b["operator_qualification"]["family_ratio"]),
    }


def _worker_init(root_s: str, dev_manifest: dict[str, Any], base: dict[str, Any]) -> None:
    root = Path(root_s)
    for p in [root / "phases/p13/coefficient_law_raw_xt/src", root / "phases/p11/raw_xt_td/src"]:
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    os.environ.update({k: "1" for k in ["OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"]})
    global _WORKER
    _WORKER = {
        "base": base,
        "G33": _load_dev_views(root, dev_manifest, 33, 65),
        "G65": _load_dev_views(root, dev_manifest, 65, 65),
    }


def _eval_grid(task: dict[str, Any], fields: list[FieldView], base: dict[str, Any]) -> dict[str, Any]:
    rows = [evaluate_pair_on_field(
        task["pair"], task["theta"], f,
        base["validity"], float(base["validity"]["inverse_roundtrip_tolerance"]),
        base["operator"]["space"], base["operator"]["numerical"],
    ) for f in fields]
    ji = [None if r.get("J_princ") is None else float(r["J_princ"]) for r in rows]
    all_f4 = all(int(r.get("stage_index", 0)) == 5 for r in rows)
    all_j = all(x is not None and math.isfinite(float(x)) for x in ji)
    jf = float(math.sqrt(sum(float(x)**2 for x in ji) / len(ji))) if all_j else None
    return {
        "all_F0_F4_valid": all_f4,
        "all_J_resolved": all_j,
        "J_i": ji,
        "J_family": jf,
        "J_max": max(float(x) for x in ji) if all_j else None,
        "per_field": rows,
    }


def _worker_eval(task: dict[str, Any]) -> dict[str, Any]:
    t0 = time.perf_counter(); c0 = time.process_time()
    try:
        base = _WORKER["base"]
        g33 = _eval_grid(task, _WORKER["G33"], base)
        g65 = _eval_grid(task, _WORKER["G65"], base)
        return {
            "membership_index": task["membership_index"],
            "scientific_branch_id": task["scientific_branch_id"],
            "arm": task["arm"], "paired_seed": task["paired_seed"], "proposal_index": task["proposal_index"],
            "structural_hash": task["structural_hash"], "exact_equivalence_class": task["exact_equivalence_class"],
            "theta_hex": task["theta_hex"], "fit_provenance": task["fit_provenance"], "deterministic_gauge": task["deterministic_gauge"],
            "TRAIN_J_i_G33": task["TRAIN_J_i_G33"], "TRAIN_J_family_G33": task["TRAIN_J_family_G33"],
            "TRAIN_family_ratio_G33": task["TRAIN_family_ratio_G33"],
            "DEV_G33": g33, "DEV_G65": g65,
            "worker_wall_seconds": time.perf_counter()-t0,
            "worker_cpu_seconds": time.process_time()-c0,
            "same_AST_theta_gauge_zero_refit": True,
        }
    except Exception as exc:
        return {
            "membership_index": task["membership_index"], "scientific_branch_id": task["scientific_branch_id"],
            "fatal_exception_type": type(exc).__name__, "fatal_exception": str(exc),
            "worker_wall_seconds": time.perf_counter()-t0, "worker_cpu_seconds": time.process_time()-c0,
            "same_AST_theta_gauge_zero_refit": True,
        }


def _identity(fields: list[FieldView], base: dict[str, Any]) -> dict[str, Any]:
    pair = build_identity_pair(base["caps"])
    rows = [evaluate_pair_on_field(pair, [], f, base["validity"], float(base["validity"]["inverse_roundtrip_tolerance"]), base["operator"]["space"], base["operator"]["numerical"]) for f in fields]
    ji = [r.get("J_princ") for r in rows]
    if not all(int(r.get("stage_index", 0)) == 5 and x is not None and math.isfinite(float(x)) for r, x in zip(rows, ji)):
        raise RuntimeError("DEVELOPMENT identity baseline unresolved")
    vals = [float(x) for x in ji]
    return {"J_i": vals, "J_family": float(math.sqrt(sum(x*x for x in vals)/len(vals))), "J_max": max(vals), "per_field": rows}


def _rel_disc(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or not math.isfinite(float(a)) or not math.isfinite(float(b)):
        return None
    return abs(float(a)-float(b))/max(abs(float(b)), 1e-15)


def _enrich(row: dict[str, Any], identity33: dict[str, Any], identity65: dict[str, Any], tau: float) -> dict[str, Any]:
    out = json.loads(json.dumps(row))
    if "fatal_exception" in out:
        out["K1_measurement_status"] = "NUMERICAL_OR_IMPLEMENTATION_UNRESOLVED"
        out["K2_final_decision_authority"] = False
        return out
    for label, identity in [("DEV_G33", identity33), ("DEV_G65", identity65)]:
        rec = out[label]
        if rec["all_F0_F4_valid"] and rec["all_J_resolved"]:
            ratios = [float(a)/float(b) for a,b in zip(rec["J_i"], identity["J_i"])]
            fam_ratio = float(rec["J_family"])/float(identity["J_family"])
            rec["identity_J_i"] = list(identity["J_i"])
            rec["identity_J_family"] = float(identity["J_family"])
            rec["field_ratios_to_identity"] = ratios
            rec["family_ratio_to_identity"] = fam_ratio
            rec["frozen_boundary_statuses"] = [boundary_status(fam_ratio,0.5,tau)] + [boundary_status(x,1.0,tau) for x in ratios]
        else:
            rec["identity_J_i"] = list(identity["J_i"])
            rec["identity_J_family"] = float(identity["J_family"])
            rec["field_ratios_to_identity"] = None
            rec["family_ratio_to_identity"] = None
            rec["frozen_boundary_statuses"] = None
    per = [_rel_disc(a,b) for a,b in zip(out["DEV_G33"]["J_i"],out["DEV_G65"]["J_i"])]
    fam = _rel_disc(out["DEV_G33"]["J_family"],out["DEV_G65"]["J_family"])
    valid_discs = [x for x in per if x is not None]
    out["G33_G65_numerical_fidelity"] = {
        "per_field_relative_J_discrepancy": per,
        "family_relative_J_discrepancy": fam,
        "max_per_field_relative_J_discrepancy": max(valid_discs) if len(valid_discs)==4 else None,
        "tau_num_reference": tau,
        "count_per_field_gt_tau_num": sum(1 for x in valid_discs if x > tau),
        "automatic_candidate_rescue_or_repair_trigger": False,
    }
    rt = float(out["TRAIN_family_ratio_G33"])
    r33 = out["DEV_G33"].get("family_ratio_to_identity")
    r65 = out["DEV_G65"].get("family_ratio_to_identity")
    out["rho_transfer"] = (float(r33)/rt) if r33 is not None and rt > 0 else None
    out["rho_transfer_G65"] = (float(r65)/rt) if r65 is not None and rt > 0 else None
    out["rho_transfer_membership_authority"] = False
    out["K1_measurement_status"] = "COMPLETE" if out["DEV_G33"]["all_J_resolved"] and out["DEV_G65"]["all_J_resolved"] else "NUMERICAL_UNRESOLVED"
    out["K2_final_decision_authority"] = False
    return out


def _avg_ranks(x: np.ndarray) -> np.ndarray:
    order = np.argsort(x, kind="mergesort")
    ranks = np.empty(len(x), dtype=float)
    i = 0
    while i < len(x):
        j = i + 1
        while j < len(x) and x[order[j]] == x[order[i]]:
            j += 1
        rank = 0.5*((i+1)+j)
        ranks[order[i:j]] = rank
        i = j
    return ranks


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    x = np.asarray(xs,float); y=np.asarray(ys,float)
    rx=_avg_ranks(x); ry=_avg_ranks(y)
    sx=float(np.std(rx)); sy=float(np.std(ry))
    if sx == 0 or sy == 0:
        return None
    return float(np.corrcoef(rx,ry)[0,1])


def _pf0_associations(s1run: Path, rows: list[dict[str, Any]]) -> dict[str, Any]:
    geom_path = s1run / "PF0_postfreeze/PF0_theory_geometry_rows.jsonl"
    asp_path = s1run / "PF0_postfreeze/PF0_ASP_branch_results.jsonl"
    if not geom_path.is_file() or not asp_path.is_file():
        return {"status":"PF0_REFERENCE_NOT_AVAILABLE", "membership_authority":False}
    asp = {r["scientific_branch_id"]: r for r in _read_jsonl(asp_path)}
    rmap = {r["scientific_branch_id"]:r for r in rows if r.get("rho_transfer") is not None and float(r["rho_transfer"])>0}
    xs1=[]; xs2=[]; ys=[]
    n_slack=0; n_lambda=0
    for sid, rr in rmap.items():
        a=asp.get(sid)
        if not a: continue
        y=math.log(float(rr["rho_transfer"]))
        slack=a.get("radial_slack")
        if slack is not None and math.isfinite(float(slack)):
            xs1.append(float(slack)); ys.append(y); n_slack += 1
    y2=[]
    for sid, rr in rmap.items():
        a=asp.get(sid)
        if not a: continue
        lam=a.get("lambda_star_on_grid")
        if lam is not None and float(lam)>0 and math.isfinite(float(lam)):
            xs2.append(abs(math.log(float(lam)))); y2.append(math.log(float(rr["rho_transfer"]))); n_lambda += 1
    return {
        "status":"DESCRIPTIVE_ONLY",
        "rho_transfer_definition":"DEV_G33_family_ratio_to_identity / frozen_TRAIN_G33_family_ratio",
        "radial_slack_vs_log_rho_transfer":{"n":n_slack,"spearman":_spearman(xs1,ys)},
        "abs_log_lambda_star_vs_log_rho_transfer":{"n":n_lambda,"spearman":_spearman(xs2,y2)},
        "p_values_reported":False,
        "membership_authority":False,
    }


def _quantiles(vals: list[float]) -> dict[str, float] | None:
    if not vals: return None
    a=np.asarray(vals,float)
    return {"min":float(np.min(a)),"p10":float(np.quantile(a,0.10)),"median":float(np.median(a)),"p90":float(np.quantile(a,0.90)),"p99":float(np.quantile(a,0.99)),"max":float(np.max(a))}


def _aggregate(rows: list[dict[str, Any]], tau: float) -> dict[str, Any]:
    complete=[r for r in rows if r.get("K1_measurement_status")=="COMPLETE"]
    fatal=[r for r in rows if "fatal_exception" in r]
    famdisc=[float(r["G33_G65_numerical_fidelity"]["family_relative_J_discrepancy"]) for r in complete if r["G33_G65_numerical_fidelity"]["family_relative_J_discrepancy"] is not None]
    maxdisc=[float(r["G33_G65_numerical_fidelity"]["max_per_field_relative_J_discrepancy"]) for r in complete if r["G33_G65_numerical_fidelity"]["max_per_field_relative_J_discrepancy"] is not None]
    rho=[float(r["rho_transfer"]) for r in complete if r.get("rho_transfer") is not None and math.isfinite(float(r["rho_transfer"]))]
    r65=[float(r["DEV_G65"]["family_ratio_to_identity"]) for r in complete if r["DEV_G65"].get("family_ratio_to_identity") is not None]
    f4_33=sum(1 for r in rows if "DEV_G33" in r and r["DEV_G33"]["all_F0_F4_valid"])
    f4_65=sum(1 for r in rows if "DEV_G65" in r and r["DEV_G65"]["all_F0_F4_valid"])
    return {
        "total_scientific_branches":len(rows),
        "complete_measurements":len(complete),
        "numerical_or_operator_unresolved":len(rows)-len(complete),
        "fatal_worker_exceptions":len(fatal),
        "all_DEV_fields_F4_G33_count":f4_33,
        "all_DEV_fields_F4_G65_count":f4_65,
        "G33_G65_family_discrepancy":_quantiles(famdisc),
        "G33_G65_max_per_field_discrepancy":_quantiles(maxdisc),
        "branches_family_discrepancy_gt_tau_num":sum(1 for x in famdisc if x>tau),
        "branches_max_per_field_discrepancy_gt_tau_num":sum(1 for x in maxdisc if x>tau),
        "tau_num_reference":tau,
        "count_gt_tau_num_is_not_an_automatic_repair_threshold":True,
        "DEV_G65_family_ratio_to_identity":_quantiles(r65),
        "rho_transfer_G33":_quantiles(rho),
        "K1_final_III_B_decisions_frozen":False,
        "K2_adjudication_required":True,
    }


def _source_manifest(root: Path, paths: list[Path]) -> dict[str, Any]:
    return {"files":[{"path":str(p.resolve().relative_to(root.resolve())),"bytes":p.stat().st_size,"sha256":_sha(p)} for p in paths if p.is_file()]}


def _update_context(root: Path, summary: dict[str, Any]) -> None:
    p=root/"P13_S2_ROLLING_CONTEXT.md"
    if not p.exists():
        _write_text(p,"# P13 S2 Rolling Execution Context\n\n**Role:** REFERENCE / HANDOFF living record. The unique ACTIVE protocol remains `P13_COMPREHENSIVE_CONTEXT_S2_ENTRY_PF0_LOCK_20260831.md`.\n\n")
    text=p.read_text(encoding="utf-8")
    marker="<!-- S2_K1_FORMAL_RESULT -->"
    a=summary["aggregate"]
    block=f'''{marker}\n## S2-K1 — complete zero-shot operator transfer measurements\n\n- `OVERALL_STATUS`: **{summary['OVERALL_STATUS']}**\n- complete S1 clear cohort attempted: `2307/2307`\n- same AST + same theta + same deterministic gauge + zero refit: `True`\n- DEVELOPMENT fields: `4`; grids: `G33/G65`\n- K1 final III-B PASS/FAIL authority: `False`; K2 adjudication required\n- complete operator measurements: `{a['complete_measurements']}`\n- numerical/operator unresolved measurements: `{a['numerical_or_operator_unresolved']}`\n- branches with max per-field G33/G65 discrepancy above inherited tau: `{a['branches_max_per_field_discrepancy_gt_tau_num']}` (descriptive; not an automatic rescue/repair trigger)\n- response / SEALED opened: `False`\n- semantic output digest: `{summary['semantic_output_digest']}`\n- next action after audit review: `{summary['NEXT_ACTION_AFTER_AUDIT_REVIEW']}`\n'''
    if marker in text: text=text.split(marker)[0].rstrip()+"\n\n"+block
    else: text=text.rstrip()+"\n\n"+block
    _write_text(p,text)


def run(root: Path, workers: int) -> int:
    root=root.resolve(); home=root/"phases/p13/coefficient_law_raw_xt"
    cfgp=home/"configs/p13_s2_k1_protocol.json"; cfg=_load_json(cfgp)
    k0run, dev, entry = _verify_entry(root,cfg)
    k1=k0run/"K1_zero_shot_operator_transfer"; k1.mkdir(exist_ok=True)
    _write_json(k1/"K1_ENTRY_AUDIT.json",entry)
    if entry["status"]!="PASS":
        _write_text(k0run/"K1_OVERALL_STATUS.txt","FAIL\n"); _write_text(k0run/"K1_NEXT_ACTION.txt",cfg["next_on_failure"]+"\n")
        print("[P13-S2-K1] OVERALL_STATUS=FAIL",flush=True); return 2

    base=_load_json(root/cfg["base_operator_protocol"])
    membership=_read_jsonl(root/cfg["clear_membership"]["path"])
    if len(membership)!=2307: raise RuntimeError("complete 2307 membership required")
    tasks=[_candidate_from_locator(root,loc,i) for i,loc in enumerate(membership)]
    if len({t["scientific_branch_id"] for t in tasks})!=2307: raise RuntimeError("scientific branch IDs not unique")

    dev33=_load_dev_views(root,dev,33,65); dev65=_load_dev_views(root,dev,65,65)
    identity33=_identity(dev33,base); identity65=_identity(dev65,base)
    _write_json(k1/"K1_DEVELOPMENT_IDENTITY_BASELINES.json",{"G33":identity33,"G65":identity65,"membership_authority":False})

    work=k1/"work"; work.mkdir(exist_ok=True)
    partial=work/"partial_results.jsonl"
    completed: dict[str,dict[str,Any]]={}
    if partial.is_file():
        for r in _read_jsonl(partial): completed[r["scientific_branch_id"]]=r
    remaining=[t for t in tasks if t["scientific_branch_id"] not in completed]
    start=time.monotonic(); done0=len(completed); last=start
    print(f"[P13-S2-K1] stage=operator_transfer processed={done0}/2307 current=resume_lock elapsed=0.0s rate=0/s ETA=NA",flush=True)
    with partial.open("a",encoding="utf-8") as fh:
        with ProcessPoolExecutor(max_workers=workers,initializer=_worker_init,initargs=(str(root),dev,base)) as ex:
            futs={ex.submit(_worker_eval,t):t for t in remaining}
            for fut in as_completed(futs):
                r=fut.result(); completed[r["scientific_branch_id"]]=r
                fh.write(json.dumps(r,sort_keys=True,separators=(",",":"))+"\n"); fh.flush()
                n=len(completed); now=time.monotonic()
                if n%int(cfg["runtime"]["checkpoint_every_completed"])==0 or now-last>=float(cfg["runtime"]["progress_every_seconds"]) or n==2307:
                    elapsed=now-start; rate=max(n-done0,0)/max(elapsed,1e-9); rem=2307-n; eta=rem/max(rate,1e-12)
                    current=r["scientific_branch_id"][:12]
                    print(f"[P13-S2-K1] stage=operator_transfer processed={n}/2307 current_branch={current} fields=4 grids=33,65 elapsed={elapsed:.1f}s rate={rate:.3f}/s ETA={eta/60:.1f}m",flush=True)
                    _write_json(work/"checkpoint.json",{"processed":n,"total":2307,"completed_ids_sha256":sha256_bytes(canonical_json_bytes(sorted(completed))),"elapsed_this_job_seconds":elapsed})
                    last=now
    if len(completed)!=2307: raise RuntimeError("K1 incomplete cohort")

    tau=float(cfg["hard_gate_frozen_for_K2"]["tau_num"])
    rows=[_enrich(completed[t["scientific_branch_id"]],identity33,identity65,tau) for t in tasks]
    final=k1/"K1_OPERATOR_TRANSFER_MEASUREMENTS.jsonl"
    with final.open("w",encoding="utf-8") as f:
        for r in rows: f.write(json.dumps(r,sort_keys=True,separators=(",",":"))+"\n")
    agg=_aggregate(rows,tau); _write_json(k1/"K1_OPERATOR_TRANSFER_AGGREGATE.json",agg)
    pf0=_pf0_associations(root/cfg["authoritative_s1_run"],rows); _write_json(k1/"K1_PF0_TRANSFER_ASSOCIATIONS_DESCRIPTIVE.json",pf0)
    boundary={"status":"PASS","DEVELOPMENT_COEF":"OPENED_K0_READ_IN_PLACE","DEVELOPMENT_RESPONSE":"SEALED_COMMITTED_UNOPENED","SEALED_FINAL_COEF":"SEALED_COMMITTED_UNOPENED","SEALED_FINAL_RESPONSE":"SEALED_COMMITTED_UNOPENED","historical_response_information_read":False,"candidate_refit":False,"branch_reselection":False,"shortlist":False,"PF0_membership_authority":False,"response_stage_blocked":True}; _write_json(k1/"K1_DATA_BOUNDARY_GUARD.json",boundary)
    policy={"same_AST_theta_gauge_zero_refit":True,"complete_2307":True,"K1_assigns_final_III_B_decision":False,"K2_must_adjudicate_complete_census":True,"tau_num":tau,"tau_role":"ambiguity_only","no_posthoc_fidelity_fraction_trigger":True,"candidate_specific_rescue":False,"cohort_wide_repair_only_if_explicitly_authorized_after_audit":True}; _write_json(k1/"K1_ADJUDICATION_HANDOFF_LOCK.json",policy)

    sources=[cfgp,Path(__file__).resolve(),home/"scripts/run_p13_s2_k1.sh",home/"scripts/package_p13_s2_k1_audit.sh",home/"tests/test_p13_s2_k1.py",home/"docs/P13_S2_K1_COMPLETE_ZERO_SHOT_OPERATOR_TRANSFER.md",root/cfg["active_context"]]
    src=_source_manifest(root,sources); _write_json(k1/"K1_SOURCE_MANIFEST.json",src)
    runtime={"python":sys.version,"numpy":np.__version__,"platform":platform.platform(),"workers":workers,"coordinator_cpus":1,"OMP_NUM_THREADS":os.environ.get("OMP_NUM_THREADS"),"MKL_NUM_THREADS":os.environ.get("MKL_NUM_THREADS"),"OPENBLAS_NUM_THREADS":os.environ.get("OPENBLAS_NUM_THREADS"),"NUMEXPR_NUM_THREADS":os.environ.get("NUMEXPR_NUM_THREADS"),"restart_resume":True}; _write_json(k1/"K1_RUNTIME_ENVIRONMENT.json",runtime)
    sem_basis={"stage":"P13-S2-K1","K0_semantic_output_digest":_load_json(k0run/"K0_development_open_lock/K0_SEMANTIC_OUTPUT_DIGEST.json")["semantic_output_digest"],"clear_membership_sha256":cfg["clear_membership"]["sha256"],"measurements_sha256":_sha(final),"aggregate":agg,"identity_G33":{"J_i":identity33["J_i"],"J_family":identity33["J_family"]},"identity_G65":{"J_i":identity65["J_i"],"J_family":identity65["J_family"]},"PF0_associations":pf0,"data_boundary":boundary,"handoff_lock":policy,"source_manifest":src}
    sem=sha256_bytes(canonical_json_bytes(sem_basis)); _write_json(k1/"K1_SEMANTIC_OUTPUT_DIGEST.json",{"semantic_output_digest":sem,"basis":sem_basis})
    summary={"OVERALL_STATUS":"PASS","NEXT_ACTION_AFTER_AUDIT_REVIEW":cfg["next_on_pass_after_audit_review"],"authoritative_S2_run":str(k0run.relative_to(root)),"K1_measurements":str(final.relative_to(root)),"K1_measurements_sha256":_sha(final),"semantic_output_digest":sem,"complete_cohort":2307,"unresolved_S1_reference_not_evaluated":80,"aggregate":agg,"PF0_associations":pf0,"DEVELOPMENT_response_opened":False,"SEALED_opened":False,"K1_final_III_B_decision_frozen":False,"response_stage_blocked":True}
    _write_json(k1/"K1_SCIENTIFIC_SUMMARY.json",summary); _write_text(k0run/"K1_OVERALL_STATUS.txt","PASS\n"); _write_text(k0run/"K1_NEXT_ACTION.txt",cfg["next_on_pass_after_audit_review"]+"\n")
    _write_text(home/"runs/LATEST_P13_S2_K1_RUN.txt",str(k0run.relative_to(root))+"\n")
    _update_context(root,summary)
    if (work/"checkpoint.json").exists(): (work/"checkpoint.json").unlink()
    if partial.exists(): partial.unlink()
    try: work.rmdir()
    except OSError: pass
    print("[P13-S2-K1] OVERALL_STATUS=PASS",flush=True); print(f"[P13-S2-K1] semantic_output_digest={sem}",flush=True); print(f"[P13-S2-K1] NEXT_ACTION_AFTER_AUDIT_REVIEW={cfg['next_on_pass_after_audit_review']}",flush=True)
    return 0


def main() -> int:
    ap=argparse.ArgumentParser(); ap.add_argument("--project-root",type=Path,default=Path.cwd()); ap.add_argument("--workers",type=int,default=int(os.environ.get("P13_WORKERS","16")))
    args=ap.parse_args()
    if args.workers < 1: raise SystemExit("workers must be >=1")
    return run(args.project_root,args.workers)

if __name__=="__main__": raise SystemExit(main())
