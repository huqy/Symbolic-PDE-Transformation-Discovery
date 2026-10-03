from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any


def _load_json(p: Path) -> Any:
    return json.loads(p.read_text())


def _write_json(p: Path, obj: Any) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, sort_keys=True, indent=2) + "\n")
    os.replace(tmp, p)


def _write_text(p: Path, s: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(s)
    os.replace(tmp, p)


def _sha(p: Path, block: int = 16 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(block), b""):
            h.update(b)
    return h.hexdigest()


def _line_count(p: Path) -> int | None:
    if p.suffix != ".jsonl":
        return None
    n = 0
    with p.open("rb") as f:
        for _ in f:
            n += 1
    return n


def _resolve_marker(root: Path, rel: str) -> Path:
    m = root / rel
    if not m.is_file():
        raise FileNotFoundError(m)
    q = Path(m.read_text().strip())
    if not q.is_absolute():
        q = root / q
    q = q.resolve()
    if root.resolve() not in q.parents:
        raise RuntimeError(f"marker escapes project root: {m} -> {q}")
    if not q.is_dir():
        raise FileNotFoundError(q)
    return q


def _object(root: Path, p: Path, role: str, *, line_count: bool = False) -> dict[str, Any]:
    return {
        "path": str(p.relative_to(root)),
        "bytes": p.stat().st_size,
        "sha256": _sha(p),
        "line_count": _line_count(p) if line_count else None,
        "role": role,
    }


def _semantic_digest(objs: list[Any]) -> str:
    payload = json.dumps(objs, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(payload).hexdigest()


def _find_s0_k2r3_null_control(root: Path, k2r4_rel: str) -> dict[str, Any]:
    # K2R4 froze the upstream K2R3 capacity-instrument source run.  The actual
    # frozen K2R3 schema is a *top-level* CALIBRATION_ONLY lock containing
    # instrument_hashes/theta dictionaries; it does not contain a nested
    # null_capacity object.  K3 must read that immutable schema exactly rather
    # than inventing a later representation for the S0 control.
    k2r4 = root / k2r4_rel
    if not k2r4.is_dir():
        raise FileNotFoundError(k2r4)
    reuse = _load_json(k2r4 / "capacity_reuse_lock.json")
    source = root / reuse["source_run"]
    lock = source / "calibration_only/capacity_instrument_lock_k2r3.json"
    if not lock.is_file():
        raise FileNotFoundError(lock)
    obj = _load_json(lock)

    if obj.get("role") != "CALIBRATION_ONLY" or obj.get("forbidden_from_search") is not True:
        raise RuntimeError("frozen S0 capacity-instrument lock is not CALIBRATION_ONLY/forbidden-from-search")
    hashes = obj.get("instrument_hashes")
    theta = obj.get("theta")
    if not isinstance(hashes, dict) or not isinstance(theta, dict):
        raise RuntimeError("frozen S0 capacity-instrument lock has unexpected schema")
    null_hash = hashes.get("null_capacity")
    null_theta = theta.get("null_capacity")
    if not isinstance(null_hash, str) or not null_hash or not isinstance(null_theta, list):
        raise RuntimeError("frozen S0 NULL calibration control provenance is incomplete")
    try:
        null_theta = [float(v) for v in null_theta]
    except (TypeError, ValueError) as exc:
        raise RuntimeError("frozen S0 NULL calibration theta is malformed") from exc

    builder = root / "phases/p13/coefficient_law_raw_xt/src/p13rawxt/calibration_instruments.py"
    if not builder.is_file():
        raise FileNotFoundError(builder)
    return {
        "source_run": str(source.relative_to(root)),
        "instrument_lock": _object(root, lock, "S2_RESPONSE_CONTROL_REFERENCE"),
        "source_role": obj["role"],
        "forbidden_from_search": True,
        "control_id": "S0_FROZEN_COEFFICIENT_BLIND_NULL_CALIBRATION",
        "structural_hash": null_hash,
        "theta": null_theta,
        "pair_builder": "p13rawxt.calibration_instruments.build_null_capacity_pair",
        "pair_builder_source": _object(root, builder, "S2_RESPONSE_CONTROL_BUILDER_REFERENCE"),
        "reconstruction_rule": "rebuild only with the frozen deterministic calibration-instrument builder, verify structural_hash, and use the frozen theta exactly; no refit",
        "candidate_membership_authority": "NONE",
        "response_control_only": True,
    }


def _update_rolling_context(root: Path, freeze: dict[str, Any]) -> None:
    p = root / "P13_S1_ROLLING_CONTEXT.md"
    old = p.read_text() if p.exists() else "# P13 S1 Rolling Execution Context\n"
    marker = "<!-- S1_K3_FORMAL_FREEZE -->"
    block = f'''{marker}\n## S1-K3 — formal S1 stage freeze\n\n- `OVERALL_STATUS`: **{freeze['OVERALL_STATUS']}**\n- S1 semantic digest: `{freeze['semantic_output_digest']}`\n- authoritative S1 store: `{freeze['authoritative_S1_run']}`\n- search horizon: `8192 proposals/seed/arm`; complete first rung `98,304` structural proposals; K1C not authorized\n- S2 ACTIVE candidate cohort: `{freeze['cohorts']['clear_count']}` clear `OPERATOR_QUALIFIED_TRAIN` branches\n- unresolved lineage: `{freeze['cohorts']['unresolved_count']}` `OPERATOR_QUALIFIED_UNRESOLVED` branches, not S2 eligible and not converted to FAIL\n- route conclusion: **Pattern A + Pattern B** — coefficient-aware raw discovery is supported on TRAIN; current additive-root V2 has no stable advantage over V1\n- K2B/K2C evidence is frozen as `REFERENCE/MECHANISM`; it has no candidate-membership authority\n- DEVELOPMENT and SEALED remained unopened through S1 freeze\n- exact S2 active-input manifest: `{freeze['s2_active_input_manifest_path']}`\n- new canonical S2-entry context: `P13_COMPREHENSIVE_CONTEXT_S2_ENTRY_20260829.md`\n- next authorized action: `{freeze['NEXT_ACTION']}`\n\nScientific claim boundary: S1 establishes constructive TRAIN operator discovery, coefficient-use evidence, and search/mechanism evidence only. It does not establish prospective DEVELOPMENT coefficient transfer or physical-response accuracy.\n'''
    if marker in old:
        old = old.split(marker)[0].rstrip() + "\n\n"
    _write_text(p, old.rstrip() + "\n\n" + block)


def _write_s2_context(root: Path, freeze: dict[str, Any], k2a: dict[str, Any], k2b: dict[str, Any], k2c: dict[str, Any]) -> None:
    p = root / "P13_COMPREHENSIVE_CONTEXT_S2_ENTRY_20260829.md"
    txt = f'''# P13 Comprehensive Context — S2 Entry after Formal S1 Freeze\n\n**Project:** P13 — Constructive Coefficient-Law Discovery  \n**Semantic milestone:** S1 formally frozen  \n**Artifact role:** **ACTIVE canonical S2-entry scientific context**  \n**Upstream ACTIVE S1 protocol:** `P13_COMPREHENSIVE_CONTEXT_S1_ENTRY_REVIEW_LOCK_20260827.md`  \n**S1 formal status:** `{freeze['OVERALL_STATUS']}`  \n**S1 semantic digest:** `{freeze['semantic_output_digest']}`  \n**Authorized next action:** `{freeze['NEXT_ACTION']}`\n\n## 1. What S1 established\n\nS1 completed a prospective operator-only raw-AST search on six TRAIN coefficient fields with one shared AST and one shared theta vector per branch. The response-blind search horizon was frozen at 8192 structural proposals per seed/arm after the preregistered continuation rule rejected further TRAIN optimization because clear FULL-qualified branches already existed.\n\nFormal first-rung budget: **98,304 structural proposals**.\n\nFrozen TRAIN scientific cohorts:\n\n- **S2 ACTIVE:** `{freeze['cohorts']['clear_count']}` clear `OPERATOR_QUALIFIED_TRAIN` branches.\n- **REFERENCE / UNRESOLVED lineage:** `{freeze['cohorts']['unresolved_count']}` numerical-boundary branches.\n- NULL clear/unresolved: `0/0`.\n\nNo top-k, percentile, Pareto, weighted score, target survivor count, diagnostic-transfer filter, theory-alignment filter, or response-aware rule was used to narrow the S2 cohort. Exact equivalence is execution sharing only.\n\n## 2. Search interpretation\n\nThe frozen route interpretation is **Pattern A + Pattern B**. FULL-V1 and FULL-V2 each showed 4/4 paired-seed clear advantage over NULL at equal structural proposals, and coefficient-dependent FULL frontiers beat coefficient-free frontiers 4/4 in both FULL arms. The current `additive_root_residual_graft` V2 intervention did not show stable superiority over FULL-V1 and is not redesigned inside this branch.\n\nThis supports constructive coefficient-aware operator discovery on TRAIN. It does **not** establish unseen-coefficient transfer or response accuracy.\n\n## 3. Frozen mechanism evidence\n\nK2B zero-refit diagnostics were opened only after the search horizon and TRAIN membership were frozen. Their role is permanently REFERENCE/mechanism evidence. They cannot alter S2 eligibility.\n\nK2C was post-membership and holdout-free. At epsilon=0.20 the clear-cohort median relative RMS-to-identity was `{k2c['aggregate']['clear_relative_RMS_eps_0p20']['median']:.12g}`. Claim-II functional alignment was resolved for `{k2c['aggregate']['alignment_resolved_clear']}/{freeze['cohorts']['clear_count']}` clear branches; median theory cosine was `{k2c['aggregate']['clear_theory_cosine']['median']:.12g}`. Empirical J-princ amplitude slopes are descriptive only and are not Claim-II coordinate-remainder theorems.\n\n## 4. S2 candidate governance\n\nFormal S2 candidate eligibility is **exactly** the frozen clear TRAIN cohort. Each branch carries the S1-frozen canonical skeleton, theta, deterministic gauge, and fit provenance. S2 may not reselect another theta branch of the same skeleton after DEVELOPMENT outcomes are seen.\n\nThe `{freeze['cohorts']['unresolved_count']}` TRAIN numerical-boundary branches remain UNRESOLVED and are not S2 eligible. They may not be rescued by K2B/K2C diagnostics.\n\n## 5. S2 data boundary\n\nS1 ended with DEVELOPMENT and SEALED unopened. S2-K0 is the first authorized opening of the already committed DEVELOPMENT coefficient payload. The SEALED_FINAL coefficient and response payloads remain sealed through S2.\n\nS2-K0 must evaluate every S2 ACTIVE branch on all four DEVELOPMENT coefficient fields using **same AST + same theta + same deterministic gauge + zero refit**. The operator-transfer hard effect-size rule remains the preregistered rule relative to DEVELOPMENT identity; failure is Claim III-B negative evidence and does not proceed to candidate response certification.\n\nDiagnostic-transfer, epsilon-scaling, theory-alignment, and observability evidence have no S2 membership authority.\n\n## 6. Physical-response boundary\n\nS2 response work remains causal/source-domain. At every formal response stage identity and the frozen S0 coefficient-blind NULL calibration control must be run on the same response bank. If absolute response gates do not discriminate candidates from controls in this intentionally weak/local regime, use the preregistered `NONDISCRIMINATIVE_ABSOLUTE_GATE` classification rather than redesigning the current DEVELOPMENT/SEALED branch post hoc.\n\n## 7. Authoritative objects\n\n- authoritative S1 run: `{freeze['authoritative_S1_run']}`\n- clear S2 membership locator: `{freeze['membership']['clear']['path']}`\n- unresolved lineage locator: `{freeze['membership']['unresolved']['path']}`\n- complete S1 registry manifest: `{freeze['registry_manifest_path']}`\n- exact S2 active-input manifest: `{freeze['s2_active_input_manifest_path']}`\n- S1 rolling context: `P13_S1_ROLLING_CONTEXT.md`\n\nCandidate/data objects are not duplicated for S2. S2 reads these authoritative paths in place.\n\n## 8. Claim hierarchy entering S2\n\n- **III-A constructive TRAIN operator discovery:** supported by S1.\n- **III-B zero-shot coefficient operator transfer:** untested until S2-K0.\n- **III-C prospective physical-response development:** untested until S2 response stages.\n- **III-D sealed joint generalization:** untested; SEALED remains unopened.\n\nPassing one level never upgrades a stronger claim automatically.\n'''
    _write_text(p, txt)


def run(root: Path) -> int:
    cfg_path = root / "phases/p13/coefficient_law_raw_xt/configs/p13_s1_k3_protocol.json"
    cfg = _load_json(cfg_path)
    active = root / cfg["active_protocol"]
    if _sha(active) != cfg["expected_active_protocol_sha256"]:
        raise RuntimeError("ACTIVE S1 protocol SHA mismatch")

    run = _resolve_marker(root, "phases/p13/coefficient_law_raw_xt/runs/LATEST_P13_S1_FORMAL_RUN.txt")
    k2c_run = _resolve_marker(root, "phases/p13/coefficient_law_raw_xt/runs/LATEST_P13_S1_K2C_RUN.txt")
    if run != k2c_run:
        raise RuntimeError("K2C marker does not resolve to authoritative S1 run")
    if (run / "K2C_OVERALL_STATUS.txt").read_text().strip() != "PASS":
        raise RuntimeError("K2C not PASS")
    if (run / "K2C_NEXT_ACTION.txt").read_text().strip() != "P13-S1-K3_FORMAL_S1_FREEZE":
        raise RuntimeError("K2C did not authorize K3")

    k2a = _load_json(run / "K2A_scientific_summary.json")
    k2b = _load_json(run / "K2B_diagnostics/K2B_scientific_summary.json")
    k2c = _load_json(run / "K2C_theory_bridge/K2C_scientific_summary.json")
    k2c_sem = _load_json(run / "K2C_theory_bridge/K2C_semantic_output_digest.json")["semantic_output_digest"]
    if k2c_sem != cfg["expected_K2C_semantic_digest"]:
        raise RuntimeError("K2C semantic digest mismatch")

    clear = run / "K2A_FROZEN_FULL_CLEAR_MEMBERSHIP.jsonl"
    unresolved = run / "K2A_FROZEN_FULL_UNRESOLVED_MEMBERSHIP.jsonl"
    clear_sha, unresolved_sha = _sha(clear), _sha(unresolved)
    clear_n, unresolved_n = _line_count(clear), _line_count(unresolved)
    if clear_sha != cfg["expected_clear_membership_sha256"] or clear_n != cfg["expected_clear_count"]:
        raise RuntimeError("clear S2 membership changed")
    if unresolved_sha != cfg["expected_unresolved_membership_sha256"] or unresolved_n != cfg["expected_unresolved_count"]:
        raise RuntimeError("unresolved membership changed")
    if not k2a.get("TRAIN_membership", {}).get("frozen"):
        raise RuntimeError("K2A TRAIN membership not frozen")
    if k2a.get("continuation_decision", {}).get("authorized"):
        raise RuntimeError("K1C was authorized; cannot freeze first rung as final")
    if int(k2a.get("first_rung_structural_proposals", -1)) != cfg["expected_first_rung_structural_proposals"]:
        raise RuntimeError("first-rung proposal count mismatch")
    if not k2b.get("membership_unchanged") or not k2c.get("membership_unchanged"):
        raise RuntimeError("post-search diagnostics changed membership")
    if k2c.get("DEVELOPMENT_or_SEALED_opened") or k2c.get("response_outcomes_opened"):
        raise RuntimeError("DEVELOPMENT/SEALED/response opened before S1 freeze")

    freeze_dir = run / "K3_freeze"
    freeze_dir.mkdir(exist_ok=True)
    registry_objects = []
    total_props = 0
    unit_total = len(cfg["paired_seeds"]) * len(cfg["arms"])
    unit_done = 0
    t0 = time.perf_counter()
    for seed in cfg["paired_seeds"]:
        for arm in cfg["arms"]:
            unit = run / "units" / f"seed_{seed:02d}" / arm
            us = _load_json(unit / "unit_summary.json")
            if us.get("status") != "PASS" or int(us.get("processed", -1)) != cfg["expected_search_horizon_per_seed_arm"]:
                raise RuntimeError(f"unit incomplete: seed={seed} arm={arm}")
            total_props += int(us["processed"])
            if int(us.get("counters", {}).get("silent_fallback_to_V1", 0)) != 0:
                raise RuntimeError(f"silent V2->V1 fallback: seed={seed} arm={arm}")
            for fname in cfg["registry_files_per_unit"]:
                p = unit / fname
                if not p.is_file():
                    raise FileNotFoundError(p)
                registry_objects.append(_object(root, p, "AUTHORITATIVE_S1_REGISTRY", line_count=fname.endswith(".jsonl")))
            unit_done += 1
            elapsed = max(time.perf_counter() - t0, 1e-9)
            rate = unit_done / elapsed
            eta = (unit_total - unit_done) / rate if rate > 0 else float("inf")
            print(f"[P13 S1 K3] registry_freeze processed={unit_done}/{unit_total} current=seed_{seed:02d}/{arm} elapsed={elapsed:.1f}s rate={rate:.3f} units/s ETA={eta:.1f}s", flush=True)
    if total_props != cfg["expected_first_rung_structural_proposals"]:
        raise RuntimeError("registry proposal total mismatch")

    registry_manifest = {
        "authoritative_S1_run": str(run.relative_to(root)),
        "scientific_membership_is_not_narrowed_by_this_manifest": True,
        "objects": registry_objects,
        "policy": "authoritative S1 proposal/skeleton/branch/equivalence/unit registries remain in place; K3 records only path/SHA/bytes/line-count and creates no duplicate candidate store",
    }
    reg_path = freeze_dir / "K3_COMPLETE_S1_REGISTRY_MANIFEST.json"
    _write_json(reg_path, registry_manifest)

    # Read only public/private commitments, never archive payloads.
    s0k1 = _resolve_marker(root, "phases/p13/coefficient_law_raw_xt/runs/LATEST_P13_S0_K1_RUN.txt")
    commitments_path = s0k1 / "private_payload_commitments.json"
    commitments = _load_json(commitments_path)
    dev_coef = commitments["coefficient:DEVELOPMENT_COEF"]
    dev_resp = commitments["response:DEVELOPMENT_COEF"]
    sealed_coef = commitments["coefficient:SEALED_FINAL_COEF"]
    sealed_resp = commitments["response:SEALED_FINAL_COEF"]
    null_control = _find_s0_k2r3_null_control(root, cfg["s0_k2r4_authoritative_run"])

    s2_manifest = {
        "stage": "P13-S2-ENTRY",
        "created_by": "P13-S1-K3_FORMAL_S1_FREEZE",
        "candidate_rule": cfg["s2_candidate_rule"],
        "clear_candidate_cohort": _object(root, clear, "ACTIVE_S2_CANDIDATE_MEMBERSHIP", line_count=True),
        "unresolved_lineage": _object(root, unresolved, "REFERENCE_UNRESOLVED_NOT_S2_ELIGIBLE", line_count=True),
        "candidate_source_registry_manifest": _object(root, reg_path, "ACTIVE_S2_PROVENANCE_MANIFEST"),
        "development_coefficient_commitment": dev_coef,
        "development_response_commitment": dev_resp,
        "sealed_final_coefficient_guard": sealed_coef,
        "sealed_final_response_guard": sealed_resp,
        "identity_control": {"definition": "deterministic identity map under frozen P13 grammar/gauge", "response_control_only": True},
        "null_control": null_control,
        "same_AST_theta_gauge_zero_refit": True,
        "branch_reselection_forbidden": True,
        "diagnostic_transfer_has_membership_authority": False,
        "epsilon_scaling_has_membership_authority": False,
        "theory_alignment_has_membership_authority": False,
        "DEVELOPMENT_payload_opened_by_K3": False,
        "SEALED_payload_opened_by_K3": False,
        "runtime_policy": "S2 reads authoritative S1 candidate locators/registries and S0 commitments in place; no S2 active-input archive is required",
    }
    s2_path = freeze_dir / "K3_S2_ACTIVE_INPUT_MANIFEST.json"
    _write_json(s2_path, s2_manifest)

    data_boundary = {
        "TRAIN_OPERATOR": "OPENED_FORMAL_SEARCH_EVIDENCE_FROZEN",
        "OPENED_TRANSFER_DIAGNOSTIC": "OPENED_DIAGNOSTIC_REFERENCE_ONLY",
        "WITHIN_FAMILY_TRANSFER_DIAGNOSTIC": "OPENED_DIAGNOSTIC_REFERENCE_ONLY",
        "DEVELOPMENT_COEF": "SEALED_COMMITTED_UNOPENED_AT_S1_FREEZE",
        "DEVELOPMENT_RESPONSE": "SEALED_COMMITTED_UNOPENED_AT_S1_FREEZE",
        "SEALED_FINAL_COEF": "SEALED_COMMITTED_UNOPENED",
        "SEALED_FINAL_RESPONSE": "SEALED_COMMITTED_UNOPENED",
        "historical_response_used_for_membership": False,
        "K2B_or_K2C_used_for_membership": False,
        "response_aware_refit": False,
        "topk_pareto_percentile_target_count_narrowing": False,
        "status": "PASS",
    }
    _write_json(freeze_dir / "K3_DATA_BOUNDARY_FREEZE.json", data_boundary)

    scientific = {
        "stage": "P13-S1-K3",
        "S1_status": "PASS",
        "search_horizon": {"proposals_per_seed_arm": 8192, "total_structural_proposals": total_props, "K1C_authorized": False},
        "cohorts": {"clear_count": clear_n, "unresolved_count": unresolved_n, "NULL_clear": 0, "NULL_unresolved": 0},
        "route_interpretation": "PATTERN_A_PLUS_B",
        "technical_conclusion": "Constructive coefficient-aware raw finite-local operator discovery is supported on TRAIN; coefficient access shows stable paired benefit over NULL; current additive-root V2 has no stable demonstrated advantage over FULL-V1.",
        "claim_boundary": "S1 establishes TRAIN operator discovery, coefficient-use/search evidence, and response-blind mechanism diagnostics only. DEVELOPMENT zero-shot operator transfer and physical response remain untested.",
        "diagnostics_role": "REFERENCE_MECHANISM_ONLY",
        "s2_membership_rule": cfg["s2_candidate_rule"],
        "unresolved_rule": cfg["s2_unresolved_rule"],
        "forbidden_narrowing": cfg["forbidden_narrowing"],
        "K2B_semantic_digest": k2b.get("semantic_output_digest"),
        "K2C_semantic_digest": k2c_sem,
        "S2_predictions_frozen": bool(k2c.get("S2_mechanistic_predictions_frozen")),
    }
    _write_json(freeze_dir / "K3_S1_SCIENTIFIC_FREEZE.json", scientific)

    lineage = {
        "ACTIVE": [
            str(clear.relative_to(root)),
            str(reg_path.relative_to(root)),
            str(s2_path.relative_to(root)),
            "P13_COMPREHENSIVE_CONTEXT_S2_ENTRY_20260829.md",
        ],
        "REFERENCE": [
            str(unresolved.relative_to(root)),
            str((run / "K2B_diagnostics").relative_to(root)),
            str((run / "K2C_theory_bridge").relative_to(root)),
            "P13_S1_ROLLING_CONTEXT.md",
        ],
        "ARCHIVE_OR_SUPERSEDED": ["per-K patch/audit archives after review; caches/restart work not required for reproduction"],
        "authoritative_S1_run": str(run.relative_to(root)),
    }
    _write_json(freeze_dir / "K3_ARTIFACT_REGISTRY.json", lineage)

    source_manifest = []
    for rel in [
        "phases/p13/coefficient_law_raw_xt/configs/p13_s1_k3_protocol.json",
        "phases/p13/coefficient_law_raw_xt/src/p13rawxt/s1_k3_formal_freeze.py",
        "phases/p13/coefficient_law_raw_xt/src/p13rawxt/calibration_instruments.py",
        "phases/p13/coefficient_law_raw_xt/scripts/run_p13_s1_k3.sh",
        "phases/p13/coefficient_law_raw_xt/scripts/package_p13_s1_k3_audit.sh",
        "phases/p13/coefficient_law_raw_xt/tests/test_p13_s1_k3.py",
        "phases/p13/coefficient_law_raw_xt/docs/P13_S1_K3_FORMAL_S1_FREEZE.md",
        "phases/p13/coefficient_law_raw_xt/docs/P13_S1_K3R_NULL_CONTROL_PROVENANCE_REPAIR.md",
        cfg["active_protocol"],
    ]:
        p = root / rel
        source_manifest.append(_object(root, p, "K3_SOURCE_PROTOCOL"))
    _write_json(freeze_dir / "K3_SOURCE_MANIFEST.json", {"files": source_manifest})

    pre_semantic = [scientific, data_boundary, s2_manifest, lineage, registry_manifest, {"source_manifest": source_manifest}]
    semantic = _semantic_digest(pre_semantic)
    freeze = {
        "stage": "P13-S1-K3",
        "OVERALL_STATUS": "PASS",
        "NEXT_ACTION": cfg["next_action_on_pass"],
        "semantic_output_digest": semantic,
        "authoritative_S1_run": str(run.relative_to(root)),
        "cohorts": {"clear_count": clear_n, "unresolved_count": unresolved_n},
        "membership": {
            "clear": {"path": str(clear.relative_to(root)), "sha256": clear_sha, "count": clear_n},
            "unresolved": {"path": str(unresolved.relative_to(root)), "sha256": unresolved_sha, "count": unresolved_n},
        },
        "registry_manifest_path": str(reg_path.relative_to(root)),
        "s2_active_input_manifest_path": str(s2_path.relative_to(root)),
        "DEVELOPMENT_opened": False,
        "SEALED_opened": False,
    }
    _write_json(freeze_dir / "K3_SEMANTIC_OUTPUT_DIGEST.json", {"semantic_output_digest": semantic})
    _write_json(freeze_dir / "K3_FREEZE_SUMMARY.json", freeze)
    _write_text(run / "K3_OVERALL_STATUS.txt", "PASS\n")
    _write_text(run / "K3_NEXT_ACTION.txt", cfg["next_action_on_pass"] + "\n")
    _write_text(root / "phases/p13/coefficient_law_raw_xt/runs/LATEST_P13_S1_K3_RUN.txt", str(run.relative_to(root)) + "\n")

    _write_s2_context(root, freeze, k2a, k2b, k2c)
    _update_rolling_context(root, freeze)
    # Include final context hashes in a final handoff manifest, without changing the semantic evidence digest.
    handoff = {
        "S1_semantic_output_digest": semantic,
        "S2_active_input_manifest": _object(root, s2_path, "ACTIVE_S2_INPUT_MANIFEST"),
        "S2_entry_context": _object(root, root / "P13_COMPREHENSIVE_CONTEXT_S2_ENTRY_20260829.md", "ACTIVE_CANONICAL_S2_CONTEXT"),
        "S1_rolling_context": _object(root, root / "P13_S1_ROLLING_CONTEXT.md", "REFERENCE_HANDOFF_CONTEXT"),
        "no_new_active_input_archive_required": True,
    }
    _write_json(freeze_dir / "K3_S2_HANDOFF_MANIFEST.json", handoff)

    print("OVERALL_STATUS=PASS", flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-root", required=True)
    args = ap.parse_args()
    root = Path(args.project_root).resolve()
    return run(root)


if __name__ == "__main__":
    raise SystemExit(main())
