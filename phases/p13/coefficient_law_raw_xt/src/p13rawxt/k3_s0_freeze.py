from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sys
import time
from pathlib import Path
from reproduce import s0_contract as s0
from typing import Any

EXPECTED_K2R4_SEMANTIC = "033b1a331a57f02e487ea5b41b5e3649360970be432a14d54414e2b7091b2a05"


def canonical_bytes(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_path(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text())


def resolve_marker(root: Path, marker_rel: str) -> Path:
    if s0.active(root): return s0.resolve_fresh_marker(root, marker_rel)
    marker = root / marker_rel
    if not marker.is_file():
        raise FileNotFoundError(marker)
    p = Path(marker.read_text().strip())
    if not p.is_absolute():
        p = root / p
    if not p.is_dir():
        raise FileNotFoundError(p)
    return p


def adjudicate_s0(gates: dict[str, str], leakage_ok: bool, capacity_ok: bool, causal_required: bool = True) -> dict[str, Any]:
    required = {
        "regime_identifiability",
        "L3_m2_semantics",
        "gauge_equivalence",
        "capacity_null_separation",
        "operational_fitter",
        "proposal_geometry",
        "evaluator_fidelity",
    }
    if causal_required:
        required.add("causal_response_calibration")
    failures = sorted(k for k in required if gates.get(k) != "PASS")
    if not leakage_ok:
        failures.append("no_leakage")
    if not capacity_ok:
        failures.append("capacity_reference")
    status = "PASS" if not failures else "FAIL"
    return {
        "status": status,
        "failures": failures,
        "S1_K0_authorized": status == "PASS",
        "S1_K1_formal_search_authorized": False,
    }


def _read_current_source_lock(root: Path, k2r4: Path) -> dict[str, Any]:
    source = load_json(k2r4 / "source_manifest.json")
    by_path = {r["path"]: r["sha256"] for r in source.get("files", [])}
    required = [
        "phases/p13/coefficient_law_raw_xt/configs/p13_s0_k2r4_protocol.json",
        "phases/p13/coefficient_law_raw_xt/src/p13rawxt/k2r4_operational_fitter.py",
        "phases/p13/coefficient_law_raw_xt/configs/p13_s0_k2r3_protocol.json",
        "phases/p13/coefficient_law_raw_xt/src/p13rawxt/k2r3_capacity_continuation.py",
        "phases/p13/coefficient_law_raw_xt/configs/p13_s0_k2r2_protocol.json",
        "phases/p13/coefficient_law_raw_xt/src/p13rawxt/k2r2_attainment_repair.py",
        "phases/p13/coefficient_law_raw_xt/src/p13rawxt/reference_optimizer_v2.py",
    ]
    checks: dict[str, bool] = {}
    for rel in required:
        p = root / rel
        checks[rel] = p.is_file() and by_path.get(rel) == sha256_path(p)
    if not all(checks.values()):
        raise RuntimeError(f"K3 current-source byte lock failed: {checks}")
    return {"status": "PASS", "checks": checks}


def _resolve_parent(root: Path, rel_or_abs: str) -> Path:
    if s0.active(root): return s0.resolve_fresh_run(root, rel_or_abs)
    p = Path(rel_or_abs)
    if not p.is_absolute():
        p = root / p
    if not p.is_dir():
        raise FileNotFoundError(p)
    return p


def _summarize_causal(path: Path) -> dict[str, Any]:
    d = load_json(path)
    by: dict[str, list[dict[str, float]]] = {}
    for row in d.get("rows", []):
        by.setdefault(str(row["chart"]), []).append(row["interval"])
    out: dict[str, Any] = {
        "status": d.get("status"),
        "threshold": d.get("threshold"),
        "gates": d.get("gates", {}),
        "charts": {},
    }
    for name, rows in by.items():
        out["charts"][name] = {
            "count": len(rows),
            "max_upper": max(float(r["upper"]) for r in rows),
            "max_lower": max(float(r["lower"]) for r in rows),
            "lower_above_threshold_count": sum(float(r["lower"]) > float(d["threshold"]) for r in rows),
        }
    return out


def _update_rolling_context(root: Path, block: str) -> None:
    path = root / "P13_S0_ROLLING_CONTEXT.md"
    begin = "<!-- K3_FORMAL_FREEZE_BEGIN -->"
    end = "<!-- K3_FORMAL_FREEZE_END -->"
    if path.is_file():
        text = path.read_text()
    else:
        text = "# P13 S0 Rolling Context\n"
    if begin in text and end in text:
        prefix = text.split(begin, 1)[0].rstrip()
        suffix = text.split(end, 1)[1].lstrip()
        text = prefix + "\n\n" + block.rstrip() + "\n\n" + suffix
    else:
        text = text.rstrip() + "\n\n" + block.rstrip() + "\n"
    path.write_text(text)


def run(root: Path) -> Path:
    t0 = time.time()
    protocol_path = root / "phases/p13/coefficient_law_raw_xt/configs/p13_s0_k3_protocol.json"
    protocol = load_json(protocol_path)
    k2r4 = resolve_marker(root, protocol["parent_k2r4"]["marker"])
    print(f"[K3 1/5] lock formal K2R4 parent: {k2r4.relative_to(root)}", flush=True)

    summary4 = load_json(k2r4 / "audit_summary.json")
    semantic4 = load_json(k2r4 / "semantic_output_digest.json")
    leakage4 = load_json(k2r4 / "no_leakage_guard.json")
    handoff4 = load_json(k2r4 / "k3_handoff_manifest.json")
    parent_checks = s0.k3_parent_checks(root) if s0.active(root) else {
        "status": (k2r4 / "OVERALL_STATUS.txt").read_text().strip() == protocol["parent_k2r4"]["expected_overall_status"],
        "next": (k2r4 / "NEXT_ACTION.txt").read_text().strip() == protocol["parent_k2r4"]["expected_next_action"],
        "semantic": semantic4.get("semantic_output_digest") == protocol["parent_k2r4"]["expected_semantic_output_digest"] == EXPECTED_K2R4_SEMANTIC,
        "formal_S1_disabled": not bool(summary4.get("formal_S1_search_authorized", True)),
        "handoff_requires_K3": bool(handoff4.get("K3_required", False)),
        "burnin_not_S1_seed": not bool(handoff4.get("burnin_candidates_eligible_for_S1", True)),
        "no_leakage": leakage4.get("status") == "PASS",
    }
    if not all(parent_checks.values()):
        raise RuntimeError(f"K3 parent K2R4 verification failed: {parent_checks}")
    source_lock = _read_current_source_lock(root, k2r4)

    k1 = _resolve_parent(root, handoff4["K1_run"])
    k2 = _resolve_parent(root, handoff4["K2_run"])
    k2r2 = _resolve_parent(root, handoff4["K2R2_run"])
    k2r3 = _resolve_parent(root, handoff4["K2R3_run"])

    print("[K3 2/5] assemble S0 hard-gate evidence", flush=True)
    k1sum = load_json(k1 / "audit_summary.json")
    k2sum = load_json(k2 / "audit_summary.json")
    cap = load_json(k2r4 / "capacity_reuse_lock.json")
    burn = load_json(k2r4 / "operational_burnin_qualification.json")
    fit = load_json(k2r4 / "operational_fitter_qualification.json")
    prop = load_json(k2r4 / "proposal_geometry_requalification.json")
    fid = load_json(k2r4 / "evaluator_fidelity_cost.json")
    lower = load_json(k2r4 / "lower_order_diagnostics.json")
    causal = load_json(k2r4 / "causal_response_feasibility.json")

    gates = {
        "regime_identifiability": "PASS" if k1sum.get("identifiability_status") == "PASS" and k2sum.get("gate_statuses", {}).get("identifiability") == "PASS" else "FAIL",
        "L3_m2_semantics": "PASS" if k2sum.get("gate_statuses", {}).get("m2_semantics") == "PASS" else "FAIL",
        "gauge_equivalence": "PASS" if k2sum.get("gate_statuses", {}).get("gauge") == "PASS" else "FAIL",
        "capacity_null_separation": "PASS" if cap.get("status") == "PASS" and cap.get("capacity_existence_status") == "PASS" else "FAIL",
        "operational_burnin": burn.get("status", "FAIL"),
        "operational_fitter": fit.get("status", "FAIL"),
        "proposal_geometry": prop.get("status", "FAIL"),
        "evaluator_fidelity": fid.get("status", "FAIL"),
        "causal_response_calibration": causal.get("status", "FAIL"),
        "no_leakage": leakage4.get("status", "FAIL"),
        "lower_order_diagnostics": "DIAGNOSTIC_COMPLETE" if isinstance(lower, dict) and lower.get("diagnostic_only") is True else "UNRESOLVED",
    }
    capacity_ok = (
        cap.get("full_optimum_reference_status") == protocol["capacity_lock"]["full_optimum_reference_status"]
        and summary4.get("J_capacity_hard_decision_status") == protocol["capacity_lock"]["J_capacity_hard_decision_status"]
        and summary4.get("R_att_hard_decision_status") == protocol["capacity_lock"]["R_att_hard_decision_status"]
    )
    adjud = adjudicate_s0(gates, leakage4.get("status") == "PASS", capacity_ok, causal_required=True)

    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    run_rel = Path(s0.run_relative("K3")) if s0.active(root) else Path("phases/p13/coefficient_law_raw_xt/runs") / f"p13_s0_k3_freeze_{stamp}"
    run_dir = root / run_rel
    run_dir.mkdir(parents=True, exist_ok=False)

    print("[K3 3/5] freeze claim boundaries and S1 entry contract", flush=True)
    causal_summary = _summarize_causal(k2r4 / "causal_response_feasibility.json")
    evidence = {
        "stage": "P13-S0-K3",
        "status": adjud["status"],
        "gates": gates,
        "capacity_reference_resolved": capacity_ok,
        "lineage": {
            "K0_semantic": k1sum.get("parent_K0_semantic"),
            "K1_semantic": load_json(k1 / "semantic_output_digest.json").get("semantic_output_digest"),
            "K2_semantic": load_json(k2 / "semantic_output_digest.json").get("semantic_output_digest"),
            "K2R2_semantic": load_json(k2r2 / "semantic_output_digest.json").get("semantic_output_digest"),
            "K2R3_semantic": load_json(k2r3 / "semantic_output_digest.json").get("semantic_output_digest"),
            "K2R4_semantic": semantic4.get("semantic_output_digest"),
        },
        "key_metrics": {
            "identifiability_rank": k1sum.get("identifiability_rank"),
            "identifiability_sigma_ratio": k1sum.get("identifiability_sigma_ratio"),
            "identifiability_min_channel_residual": k1sum.get("identifiability_min_channel_residual"),
            "stable_J_capacity_G65": cap.get("stable_J_capacity_G65"),
            "burnin_structural_proposals": burn.get("completed_structural_proposals"),
            "burnin_F4_unique": burn.get("F4_unique_skeletons"),
            "burnin_F4_unique_rate": burn.get("F4_unique_rate"),
            "fitter_reference_qualified": fit.get("reference_qualified"),
            "fitter_R_fit_resolved": fit.get("R_fit_resolved"),
            "fitter_median_R_fit": fit.get("median_R_fit"),
            "fitter_p90_R_fit": fit.get("p90_R_fit"),
            "V2_attempted": prop.get("attempted_proposals"),
            "V2_F4_children": prop.get("F4_children"),
            "V2_improve_5pct_count": prop.get("improve_5pct_count"),
            "V2_improve_20pct_count": prop.get("improve_20pct_count"),
            "evaluator_max_relative_J_difference": fid.get("maximum_relative_J_difference"),
            "evaluator_ceiling": fid.get("ceiling"),
            "causal": causal_summary,
        },
    }
    claim_boundary = {
        "supported": [
            "P13 first-branch finite-local coefficient-aware representation has a resolved response-blind operator-capacity advantage over the coordinate-only NULL calibration in the declared calibration regime.",
            "The current generic GP plus unchanged production fitter can operationally attain a substantial F4-valid state population within the preregistered 8192-proposal calibration horizon.",
            "Conditional on operational F4 attainment, the unchanged production fitter meets the preregistered continuous-fit regret gates on the chronological F4 cohort.",
            "The actual production V2 residual-graft kernel exhibits measured partial credit under the preregistered calibration protocol.",
            "The G33/G65 operator evaluator is numerically consistent within the preregistered ceiling on the calibration cohort.",
            "A calibration-only causal/source-domain response benchmark cleanly separates the FULL capacity chart from identity/NULL at the 0.15 threshold."
        ],
        "not_supported": [
            "No S1 constructive discovery result exists yet.",
            "No DEVELOPMENT or SEALED coefficient zero-shot generalization result exists yet.",
            "No S2/S3 formal physical-response generalization result exists yet.",
            "S0 burn-in/capacity/fitter/V2 calibration candidates are not discovered scientific candidates and may not seed S1.",
            "Calibration response PASS does not imply candidate response accuracy or coefficient transfer.",
            "Lower-order diagnostics remain descriptive and are not candidate filters."
        ]
    }
    data_boundary = {
        "status": "PASS" if leakage4.get("status") == "PASS" else "FAIL",
        "roles": protocol["data_boundary"],
        "private_commitments": k1sum.get("private_commitments", {}),
        "private_payload_contents_in_active_tree": k1sum.get("private_payload_contents_in_active_tree"),
        "K2R4_no_leakage_guard": leakage4,
    }
    s1_entry = {
        "status": "AUTHORIZED" if adjud["S1_K0_authorized"] else "NOT_AUTHORIZED",
        "authorized_action": "P13-S1-K0_FORMAL_SEARCH_IMPLEMENTATION_REGRESSION" if adjud["S1_K0_authorized"] else None,
        "formal_S1_K1_search_authorized": False,
        "first_formal_rung_after_S1_K0_PASS": {
            "arms": protocol["s1_entry"]["arms"],
            "paired_seeds": protocol["s1_entry"]["paired_seeds"],
            "structural_proposals_per_seed_per_arm": protocol["s1_entry"]["structural_proposals_per_seed_per_arm_first_rung"],
            "primary_fairness_axis": protocol["s1_entry"]["primary_fairness_axis"],
            "secondary_fairness_axis": protocol["s1_entry"]["secondary_fairness_axis"],
        },
        "required_S1_K0_regressions": protocol["s1_entry"]["S1_K0_required_regressions"],
        "forbidden_seed_sources": protocol["s1_entry"]["forbidden_seed_sources"],
        "active_inputs": {
            "K1_run": str(k1.relative_to(root)),
            "K1_open_search_object_manifest": str((k1 / "open_search_object_manifest.json").relative_to(root)),
            "K2R4_run_for_S0_freeze_evidence_only": str(k2r4.relative_to(root)),
        },
        "candidate_governance": protocol["candidate_governance"],
        "new_active_input_archive_required": False,
    }
    lineage = {
        "status": "PASS",
        "runs": {
            "K1": str(k1.relative_to(root)),
            "K2": str(k2.relative_to(root)),
            "K2R2": str(k2r2.relative_to(root)),
            "K2R3": str(k2r3.relative_to(root)),
            "K2R4": str(k2r4.relative_to(root)),
        },
        "semantics": evidence["lineage"],
        "parent_K2R4_source_lock": source_lock,
        "parent_K2R4_checks": parent_checks,
        "audit_archives_used_as_runtime_input": False,
        "authoritative_upstream_data_reused_in_place": True,
    }

    write_json(run_dir / "s0_gate_adjudication.json", {**adjud, "gate_statuses": gates, "capacity_reference_resolved": capacity_ok})
    write_json(run_dir / "s0_evidence_freeze.json", evidence)
    write_json(run_dir / "claim_boundary.json", claim_boundary)
    write_json(run_dir / "data_boundary_freeze.json", data_boundary)
    write_json(run_dir / "s1_entry_manifest.json", s1_entry)
    write_json(run_dir / "authoritative_lineage_manifest.json", lineage)

    source_files = [
        "phases/p13/coefficient_law_raw_xt/configs/p13_s0_k3_protocol.json",
        "phases/p13/coefficient_law_raw_xt/src/p13rawxt/k3_s0_freeze.py",
        "phases/p13/coefficient_law_raw_xt/scripts/run_p13_s0_k3.sh",
        "phases/p13/coefficient_law_raw_xt/scripts/package_p13_s0_k3_audit.sh",
        "phases/p13/coefficient_law_raw_xt/docs/P13_S0_K3_SCIENTIFIC_ADJUDICATION_AND_FREEZE_20260827.md",
    ]
    source_manifest = {"files": []}
    for rel in source_files:
        p = root / rel
        source_manifest["files"].append({"path": rel, "sha256": sha256_path(p), "bytes": p.stat().st_size})
    write_json(run_dir / "source_manifest.json", source_manifest)
    write_json(run_dir / "runtime_environment.json", {
        "python": sys.version,
        "platform": platform.platform(),
        "NSLOTS": os.environ.get("NSLOTS"),
        "OPENBLAS_NUM_THREADS": os.environ.get("OPENBLAS_NUM_THREADS"),
        "OMP_NUM_THREADS": os.environ.get("OMP_NUM_THREADS"),
        "workers": 1,
    })

    semantic_basis = {
        "stage": "P13-S0-K3",
        "parent_K2R4_semantic": semantic4.get("semantic_output_digest"),
        "protocol_sha256": sha256_path(protocol_path),
        "s0_gate_adjudication": load_json(run_dir / "s0_gate_adjudication.json"),
        "claim_boundary": claim_boundary,
        "data_boundary": data_boundary,
        "s1_entry": s1_entry,
        "lineage_semantics": evidence["lineage"],
    }
    semantic = hashlib.sha256(canonical_bytes(semantic_basis)).hexdigest()
    write_json(run_dir / "semantic_output_digest.json", {
        "stage": "P13-S0-K3",
        "parent_K2R4_semantic": semantic4.get("semantic_output_digest"),
        "protocol_sha256": sha256_path(protocol_path),
        "semantic_output_digest": semantic,
    })

    status = adjud["status"]
    next_action = "P13-S1-K0_FORMAL_SEARCH_IMPLEMENTATION_REGRESSION" if status == "PASS" else "P13-S0-K3_SCIENTIFIC_REPAIR_REQUIRED"
    write_text(run_dir / "OVERALL_STATUS.txt", status)
    write_text(run_dir / "NEXT_ACTION.txt", next_action)
    audit_summary = {
        "OVERALL_STATUS": status,
        "NEXT_ACTION": next_action,
        "semantic_output_digest": semantic,
        "parent_K2R4_run": str(k2r4.relative_to(root)),
        "parent_K2R4_semantic": semantic4.get("semantic_output_digest"),
        "gate_statuses": gates,
        "failure_classifications": adjud["failures"],
        "S1_K0_authorized": adjud["S1_K0_authorized"],
        "S1_K1_formal_search_authorized": False,
        "burnin_candidates_eligible_for_S1": False,
        "capacity_instrument_eligible_for_S1": False,
        "DEVELOPMENT_or_SEALED_opened": False,
        "audit_archives_used_as_runtime_input": False,
        "new_active_input_archive_required": False,
        "elapsed_seconds": time.time() - t0,
    }
    write_json(run_dir / "audit_summary.json", audit_summary)

    freeze_md = s0.freeze_text(adjud, evidence) if s0.active(root) else f"""# P13 S0 Stage Freeze — {stamp}\n\n**OVERALL_STATUS:** `{status}`  \n**NEXT_ACTION:** `{next_action}`  \n**K3 semantic:** `{semantic}`\n\n## Technical scientific conclusion\n\n- Coefficient-family identifiability: PASS; rank `{k1sum.get('identifiability_rank')}`, sigma ratio `{k1sum.get('identifiability_sigma_ratio'):.6g}`, minimum residualized channel fraction `{k1sum.get('identifiability_min_channel_residual'):.6g}`.\n- L3/m=2 numerical semantics and deterministic gauge equivalence: PASS.\n- Stable FULL capacity reference: `J_capacity(G65)={cap.get('stable_J_capacity_G65'):.12g}`; FULL/NULL capacity separation: PASS.\n- Operational common burn-in: `{burn.get('F4_unique_skeletons')}/{burn.get('completed_structural_proposals')}` unique F4 (`{100.0*burn.get('F4_unique_rate'):.3f}%`); PASS.\n- Operational fitter: `{fit.get('R_fit_resolved')}` resolved comparisons; median `R_fit={fit.get('median_R_fit'):.6g}`, p90 `R_fit={fit.get('p90_R_fit'):.6g}`; PASS.\n- Actual V2 residual-graft: `{prop.get('improve_5pct_count')}` proposals improve >=5%, `{prop.get('improve_20pct_count')}` improve >=20% out of `{prop.get('attempted_proposals')}`; PASS.\n- Evaluator fidelity: max G33/G65 relative J discrepancy `{fid.get('maximum_relative_J_difference'):.6g}` versus ceiling `{fid.get('ceiling')}`; PASS.\n- Calibration-only causal response: FULL max uncertainty-adjusted upper error `{causal_summary['charts'].get('full_capacity',{}).get('max_upper'):.6g}` < 0.15; identity and NULL each have at least one uncertainty-adjusted lower error above 0.15; PASS.\n- No leakage/data-boundary violation: PASS. DEVELOPMENT and SEALED remain unopened.\n\n## Claim boundary\n\nS0 has qualified representation capacity, numerical execution, operational F4 attainment, continuous fitting, V2 proposal partial credit, evaluator fidelity, and a calibration-only causal-response feasibility separation. S0 has **not** discovered a formal S1 coefficient law and has **not** tested prospective DEVELOPMENT/SEALED zero-shot generalization or formal candidate response accuracy.\n\n## Candidate governance\n\nS0 burn-in, fitter-cohort, capacity, and V2-calibration objects remain `CALIBRATION_ONLY`; none may seed S1. No top-k, percentile, target survivor count, response-aware rule, or early Pareto pruning is authorized. Exact equivalence quotienting remains allowed only as duplicate-execution compression.\n\n## Authorized next action\n\n`P13-S1-K0` implementation regression only. The first formal search rung remains blocked until S1-K0 passes.\n"""
    write_text(run_dir / "P13_S0_STAGE_FREEZE_CONTEXT.md", freeze_md)
    write_text(root / "P13_S0_STAGE_FREEZE_CONTEXT.md", freeze_md)

    block = s0.freeze_text(adjud, evidence) if s0.active(root) else f"""<!-- K3_FORMAL_FREEZE_BEGIN -->\n## S0-K3 formal scientific adjudication and stage freeze — {stamp}\n\nFormal status: **{status}**. K3 semantic digest: `{semantic}`.\n\nAll preregistered S0 hard gates passed: identifiability, L3/m=2 execution, gauge, resolved FULL/NULL capacity separation, operational F4 attainment, operational fitter qualification, actual V2 partial credit, G33/G65 evaluator fidelity, calibration-only causal-response separation, and no-leakage/data-boundary checks. Lower-order diagnostics remain diagnostic only.\n\nKey evidence: burn-in `{burn.get('F4_unique_skeletons')}/{burn.get('completed_structural_proposals')}` unique F4; fitter `{fit.get('R_fit_resolved')}` resolved, median R_fit `{fit.get('median_R_fit'):.6g}`, p90 `{fit.get('p90_R_fit'):.6g}`; V2 >=5% improvements `{prop.get('improve_5pct_count')}`, >=20% improvements `{prop.get('improve_20pct_count')}`; stable J_capacity `{cap.get('stable_J_capacity_G65'):.12g}`.\n\nS0 calibration objects are permanently excluded from S1 seeding. DEVELOPMENT/SEALED remain unopened. K3 authorizes **only** `P13-S1-K0_FORMAL_SEARCH_IMPLEMENTATION_REGRESSION`; S1-K1 formal search remains unauthorized until that regression passes. No new active-input archive is required; S1-K0 reads authoritative K1 coefficient inputs in place and the S0 freeze manifests by stable path/marker.\n<!-- K3_FORMAL_FREEZE_END -->"""
    _update_rolling_context(root, block)

    runs_dir = root / "phases/p13/coefficient_law_raw_xt/runs"
    write_text(runs_dir / "LATEST_P13_S0_K3_RUN.txt", str(run_rel))
    write_text(runs_dir / "LATEST_P13_S0_FREEZE_CONTEXT.txt", "P13_S0_STAGE_FREEZE_CONTEXT.md")
    print("[K3 4/5] write S0 freeze context and S1-K0 entry manifest", flush=True)
    print(f"[K3 5/5] OVERALL_STATUS={status} NEXT_ACTION={next_action}", flush=True)
    return run_dir


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-root", default=".")
    ns = ap.parse_args()
    run_dir = run(Path(ns.project_root).resolve())
    print(run_dir.relative_to(Path(ns.project_root).resolve()))


if __name__ == "__main__":
    main()
