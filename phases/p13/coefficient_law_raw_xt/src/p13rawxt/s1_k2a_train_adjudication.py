from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import numpy as np

from .s1_search_primitives import continuation_decision, operator_qualification

EXPECTED_ACTIVE_CONTEXT_SHA256 = "3db057955859653c35853b8b2dd2b281e1c4429312ac969e3b9a18b4e1d56db0"
EXPECTED_K1_PROTOCOL_SHA256 = "9f71f3fccba5ea3beeb244ac64708c24a5bc298e2299331a122e0dba2c510ace"
EXPECTED_SEARCH_CORE_SHA256 = "bad3ccf5de568272a248283cad3e22c48bf373bb8c91c1c32829ce4ac2d44296"
EXPECTED_PRIMITIVES_SHA256 = "c3faa7e2f64bbfc6ef7fee997231d569d617b52a862ff465b9ecc055af594cd2"


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


def _float_hex_list(xs: list[float]) -> list[str]:
    return [float(x).hex() for x in xs]


def _effect(left: float | None, right: float | None, tau: float) -> dict[str, Any]:
    if left is None and right is None:
        return {"left": left, "right": right, "ratio_left_over_right": None, "direction": "NO_RESOLVED_F4_EITHER"}
    if left is None:
        return {"left": left, "right": right, "ratio_left_over_right": None, "direction": "RIGHT_ONLY_RESOLVED"}
    if right is None:
        return {"left": left, "right": right, "ratio_left_over_right": None, "direction": "LEFT_ONLY_RESOLVED"}
    if right <= 0:
        raise ValueError("best J must be positive")
    ratio = float(left) / float(right)
    if ratio < 1.0 - tau:
        direction = "LEFT_CLEAR_ADVANTAGE"
    elif ratio > 1.0 + tau:
        direction = "RIGHT_CLEAR_ADVANTAGE"
    else:
        direction = "TIE_UNRESOLVED"
    return {"left": float(left), "right": float(right), "ratio_left_over_right": ratio, "direction": direction}


def _direction_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    dirs = [x["direction"] for x in rows]
    left = sum(d == "LEFT_CLEAR_ADVANTAGE" for d in dirs)
    right = sum(d == "RIGHT_CLEAR_ADVANTAGE" for d in dirs)
    ties = sum(d == "TIE_UNRESOLVED" for d in dirs)
    missing = len(dirs) - left - right - ties
    mx = max(left, right)
    if mx == 4:
        stability = "CONSISTENT_ROUTE_FEASIBILITY_DIRECTION"
    elif mx == 3:
        stability = "DIRECTIONAL_BUT_UNSTABLE"
    else:
        stability = "NO_STABLE_PAIRED_ADVANTAGE"
    return {"left_clear": left, "right_clear": right, "ties_unresolved": ties, "missing_resolved_best": missing, "stability": stability}


def _jrel(ji: list[float] | None, identity_i: list[float]) -> float | None:
    if ji is None:
        return None
    return float(math.sqrt(sum((float(x) / float(y)) ** 2 for x, y in zip(ji, identity_i)) / 6.0))


def _scan_proposal_ledger(path: Path, checkpoints: list[int]) -> dict[str, Any]:
    cps = set(checkpoints)
    h = hashlib.sha256()
    cumulative_calls = 0
    cumulative_cpu = 0.0
    best_any = None
    best_dep = None
    best_free = None
    f4_proposals = 0
    coefficient_dep_proposals = 0
    legal_structures = 0
    rows_out = []
    prefix = {}
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            h.update(line.encode("utf-8"))
            if not line.strip():
                continue
            x = json.loads(line)
            idx = int(x["proposal_index"])
            if idx != line_no:
                raise RuntimeError(f"proposal ledger index/line mismatch {path}: idx={idx} line={line_no}")
            cumulative_calls += int(x.get("evaluator_calls", 0) or 0)
            cumulative_cpu += float(x.get("worker_cpu_seconds", 0.0) or 0.0)
            if x.get("structural_hash") is not None:
                legal_structures += 1
            if x.get("coefficient_dependent_syntax") is True:
                coefficient_dep_proposals += 1
            if x.get("F0_F4_status") == "F4":
                f4_proposals += 1
            j = x.get("J_family")
            if j is not None and math.isfinite(float(j)):
                jf = float(j)
                best_any = jf if best_any is None else min(best_any, jf)
                if x.get("coefficient_dependent_syntax") is True:
                    best_dep = jf if best_dep is None else min(best_dep, jf)
                elif x.get("coefficient_dependent_syntax") is False:
                    best_free = jf if best_free is None else min(best_free, jf)
            rows_out.append((idx, cumulative_calls, cumulative_cpu, best_any, best_dep, best_free))
            if idx in cps:
                prefix[str(idx)] = {
                    "proposals": idx,
                    "evaluator_calls": cumulative_calls,
                    "worker_cpu_seconds": cumulative_cpu,
                    "best_J_family": best_any,
                    "best_J_coefficient_dependent": best_dep,
                    "best_J_coefficient_free": best_free,
                    "F4_proposals": f4_proposals,
                    "F4_proposal_rate": f4_proposals / idx,
                    "legal_structures": legal_structures,
                    "coefficient_dependent_proposals": coefficient_dep_proposals,
                    "coefficient_dependent_proposal_rate": coefficient_dep_proposals / idx,
                }
    return {
        "row_count": len(rows_out),
        "total_evaluator_calls": cumulative_calls,
        "total_worker_cpu_seconds": cumulative_cpu,
        "terminal_best_J_family": best_any,
        "terminal_best_J_coefficient_dependent": best_dep,
        "terminal_best_J_coefficient_free": best_free,
        "prefix": prefix,
        "curve": rows_out,
        "sha256": h.hexdigest(),
        "bytes": path.stat().st_size,
    }


def _source_stat(path: Path) -> dict[str, int]:
    st = path.stat()
    return {"size": int(st.st_size), "mtime_ns": int(st.st_mtime_ns), "inode": int(st.st_ino), "device": int(st.st_dev)}


def _scan_branch_registry(
    branch_path_s: str,
    membership_path_s: str,
    identity_i: list[float],
    checkpoints: list[int],
    tau: float,
    write_membership_candidates: bool,
    result_path_s: str | None = None,
) -> dict[str, Any]:
    branch_path = Path(branch_path_s)
    membership_path = Path(membership_path_s)
    result_path = Path(result_path_s) if result_path_s else None
    source_stat = _source_stat(branch_path)
    h = hashlib.sha256()
    if write_membership_candidates:
        membership_path.parent.mkdir(parents=True, exist_ok=True)
        mout = membership_path.open("w", encoding="utf-8")
    else:
        mout = None
    counts = Counter()
    cp = {str(n): {"branches": 0, "clear": 0, "unresolved": 0, "not_qualified": 0,
                   "best_any": None, "best_dep": None, "best_free": None} for n in checkpoints}
    best_records = {"any": None, "dependent": None, "free": None}
    mismatch = 0
    bytes_total = branch_path.stat().st_size
    bytes_read = 0
    start = time.perf_counter()
    last_report = start
    try:
        with branch_path.open("rb") as f:
            line_no = 0
            while True:
                offset = f.tell()
                raw = f.readline()
                if not raw:
                    break
                h.update(raw)
                bytes_read = f.tell()
                if not raw.strip():
                    continue
                line_no += 1
                x = json.loads(raw)
                idx = int(x["proposal_index"])
                ji = [float(v) for v in x["J_i"]]
                recomputed = operator_qualification(ji, identity_i, True, True, tau)
                stored = x["operator_qualification"]
                if recomputed["status"] != stored.get("status"):
                    mismatch += 1
                status = recomputed["status"]
                counts["branches"] += 1
                if status == "OPERATOR_QUALIFIED_TRAIN":
                    counts["clear"] += 1
                elif status == "OPERATOR_QUALIFIED_UNRESOLVED":
                    counts["unresolved"] += 1
                else:
                    counts["not_qualified"] += 1
                dep = bool(x.get("coefficient_dependent_syntax"))
                jf = float(x["J_family"])
                key = "dependent" if dep else "free"
                if best_records["any"] is None or jf < best_records["any"]["J_family"]:
                    best_records["any"] = {"J_family": jf, "J_family_rel": _jrel(ji, identity_i), "scientific_branch_id": x["scientific_branch_id"], "proposal_index": idx, "coefficient_dependent_syntax": dep}
                if best_records[key] is None or jf < best_records[key]["J_family"]:
                    best_records[key] = {"J_family": jf, "J_family_rel": _jrel(ji, identity_i), "scientific_branch_id": x["scientific_branch_id"], "proposal_index": idx, "coefficient_dependent_syntax": dep}
                for n in checkpoints:
                    if idx <= n:
                        c = cp[str(n)]
                        c["branches"] += 1
                        if status == "OPERATOR_QUALIFIED_TRAIN": c["clear"] += 1
                        elif status == "OPERATOR_QUALIFIED_UNRESOLVED": c["unresolved"] += 1
                        else: c["not_qualified"] += 1
                        c["best_any"] = jf if c["best_any"] is None else min(c["best_any"], jf)
                        if dep: c["best_dep"] = jf if c["best_dep"] is None else min(c["best_dep"], jf)
                        else: c["best_free"] = jf if c["best_free"] is None else min(c["best_free"], jf)
                if mout is not None and status in {"OPERATOR_QUALIFIED_TRAIN", "OPERATOR_QUALIFIED_UNRESOLVED"}:
                    loc = {
                        "scientific_branch_id": x["scientific_branch_id"],
                        "exact_equivalence_class": x["exact_equivalence_class"],
                        "operator_qualification_status": status,
                        "proposal_index": idx,
                        "structural_hash": x["structural_hash"],
                        "branch_registry_path": branch_path_s,
                        "branch_registry_byte_offset": offset,
                    }
                    mout.write(json.dumps(loc, sort_keys=True, separators=(",", ":")) + "\n")
                now = time.perf_counter()
                if now - last_report >= 60.0:
                    frac = bytes_read / max(bytes_total, 1)
                    elapsed = now - start
                    eta = elapsed * (1.0 - frac) / frac if frac > 0 else math.inf
                    print(f"[P13 S1 K2A branch-scan] file={branch_path.name} bytes={bytes_read}/{bytes_total} branches={counts['branches']} elapsed={elapsed:.1f}s ETA={eta/60:.1f}m", flush=True)
                    last_report = now
    finally:
        if mout is not None:
            mout.close()
    res = {
        "branch_registry": branch_path_s,
        "branch_line_count": int(counts["branches"]),
        "qualification_counts": {k: int(counts[k]) for k in ["clear", "unresolved", "not_qualified"]},
        "qualification_recompute_mismatch_count": mismatch,
        "checkpoint_summary": cp,
        "best_records": best_records,
        "membership_candidate_path": membership_path_s if write_membership_candidates else None,
        "branch_sha256": h.hexdigest(),
        "branch_bytes": bytes_total,
        "source_stat": source_stat,
    }
    if result_path is not None:
        _write_json(result_path, res)
    return res


def _cached_branch_result(branch_path: Path, membership_path: Path, result_path: Path, require_membership: bool) -> dict[str, Any] | None:
    if not result_path.is_file():
        return None
    try:
        res = _load_json(result_path)
    except Exception:
        return None
    if res.get("source_stat") != _source_stat(branch_path):
        return None
    if require_membership and not membership_path.is_file():
        return None
    return res


def _best_at_resource(curve: list[tuple[int, int, float, float | None, float | None, float | None]], axis: str, cap: float) -> dict[str, Any]:
    best = None
    best_dep = None
    best_free = None
    proposals = 0
    calls = 0
    cpu = 0.0
    for idx, ccalls, ccpu, bany, bdep, bfree in curve:
        value = ccalls if axis == "calls" else ccpu
        if value > cap:
            break
        proposals = idx; calls = ccalls; cpu = ccpu; best = bany; best_dep = bdep; best_free = bfree
    return {"proposals_reached": proposals, "evaluator_calls": calls, "worker_cpu_seconds": cpu,
            "best_J_family": best, "best_J_coefficient_dependent": best_dep, "best_J_coefficient_free": best_free}


def _pairwise_seed_effects(unit: dict[tuple[int, str], dict[str, Any]], comparisons: list[list[str]], tau: float, axis: str) -> dict[str, Any]:
    out = {}
    for left, right in comparisons:
        rows = []
        for seed in (1, 2, 3, 4):
            if axis == "proposals":
                lv = unit[(seed, left)]["proposal"]["terminal_best_J_family"]
                rv = unit[(seed, right)]["proposal"]["terminal_best_J_family"]
                cap = 8192
                detail = {}
            else:
                totals = [unit[(seed, arm)]["proposal"]["total_evaluator_calls" if axis == "calls" else "total_worker_cpu_seconds"] for arm in ("NULL-V2", "FULL-V1", "FULL-V2")]
                cap = min(totals)
                la = _best_at_resource(unit[(seed, left)]["proposal"]["curve"], axis, cap)
                ra = _best_at_resource(unit[(seed, right)]["proposal"]["curve"], axis, cap)
                lv, rv = la["best_J_family"], ra["best_J_family"]
                detail = {"left_prefix": la, "right_prefix": ra}
            e = _effect(lv, rv, tau)
            rows.append({"paired_seed": seed, "common_budget": cap, **e, **detail})
        out[f"{left}__vs__{right}"] = {"per_seed": rows, "summary": _direction_summary(rows)}
    return out


def _skeleton_offsets(skeleton_path: Path, needed: set[int]) -> dict[int, int]:
    found = {}
    with skeleton_path.open("rb") as f:
        while needed - found.keys():
            off = f.tell(); raw = f.readline()
            if not raw: break
            if not raw.strip(): continue
            x = json.loads(raw)
            idx = int(x["proposal_index"])
            if idx in needed:
                found[idx] = off
    missing = needed - found.keys()
    if missing:
        raise RuntimeError(f"missing skeleton locators {skeleton_path}: {sorted(missing)[:10]} (n={len(missing)})")
    return found


def _finalize_membership(root: Path, run: Path, candidate_paths: list[Path]) -> dict[str, Any]:
    clear_path = run / "K2A_FROZEN_FULL_CLEAR_MEMBERSHIP.jsonl"
    unr_path = run / "K2A_FROZEN_FULL_UNRESOLVED_MEMBERSHIP.jsonl"
    clear = clear_path.open("w", encoding="utf-8")
    unr = unr_path.open("w", encoding="utf-8")
    clear_count = unr_count = 0
    eq_clear = set()
    try:
        for cand in candidate_paths:
            parts = cand.parts
            seed = int(cand.parent.name.split("_")[1])
            arm = cand.stem.replace("_membership_candidates", "")
            unit = run / "units" / f"seed_{seed:02d}" / arm
            rows = [json.loads(line) for line in cand.read_text().splitlines() if line.strip()]
            needed = {int(x["proposal_index"]) for x in rows}
            offsets = _skeleton_offsets(unit / "skeleton_registry.jsonl", needed) if needed else {}
            for x in rows:
                idx = int(x["proposal_index"])
                x["skeleton_registry_path"] = str((unit / "skeleton_registry.jsonl").relative_to(root))
                x["skeleton_registry_byte_offset"] = int(offsets[idx])
                x["branch_registry_path"] = str(Path(x["branch_registry_path"]).relative_to(root)) if Path(x["branch_registry_path"]).is_absolute() else x["branch_registry_path"]
                target = clear if x["operator_qualification_status"] == "OPERATOR_QUALIFIED_TRAIN" else unr
                target.write(json.dumps(x, sort_keys=True, separators=(",", ":")) + "\n")
                if target is clear:
                    clear_count += 1; eq_clear.add(x["exact_equivalence_class"])
                else:
                    unr_count += 1
    finally:
        clear.close(); unr.close()
    return {
        "clear_FULL_branch_count": clear_count,
        "clear_FULL_exact_execution_class_count": len(eq_clear),
        "unresolved_FULL_branch_count": unr_count,
        "clear_membership_index": str(clear_path.relative_to(root)),
        "unresolved_membership_index": str(unr_path.relative_to(root)),
        "scientific_identity_preserved": True,
        "exact_equivalence_role": "execution_sharing_only",
        "S2_branch_reselection_forbidden": True,
    }


def _verify_entry(root: Path, cfg: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    active = root / cfg["active_context"]
    k1cfg = root / "phases/p13/coefficient_law_raw_xt/configs/p13_s1_k1_protocol.json"
    core = root / "phases/p13/coefficient_law_raw_xt/src/p13rawxt/s1_k1_formal_search.py"
    prim = root / "phases/p13/coefficient_law_raw_xt/src/p13rawxt/s1_search_primitives.py"
    locks = {
        "active_context_exact": active.is_file() and _sha256_path(active) == EXPECTED_ACTIVE_CONTEXT_SHA256,
        "K1_protocol_exact": k1cfg.is_file() and _sha256_path(k1cfg) == EXPECTED_K1_PROTOCOL_SHA256,
        "search_core_exact": core.is_file() and _sha256_path(core) == EXPECTED_SEARCH_CORE_SHA256,
        "qualification_continuation_primitives_exact": prim.is_file() and _sha256_path(prim) == EXPECTED_PRIMITIVES_SHA256,
    }
    if not all(locks.values()):
        raise RuntimeError(f"K2A source/protocol lock failed: {locks}")
    run = _resolve_marker(root, cfg["s1_run_marker"])
    k1a = _resolve_marker(root, cfg["k1a_marker"])
    k1b = _resolve_marker(root, cfg["k1b_marker"])
    if not (run == k1a == k1b):
        raise RuntimeError(f"authoritative S1 marker disagreement: run={run}, K1A={k1a}, K1B={k1b}")
    k1a_sum = _load_json(run / "K1A_integrity_summary.json")
    k1b_sum = _load_json(run / "K1B_integrity_summary.json")
    guards = [_load_json(run / "data_boundary_guard.json"), _load_json(run / "K1B_data_boundary_guard.json")]
    gate = {
        "K1A_PASS": (run / "K1A_OVERALL_STATUS.txt").read_text().strip() == "PASS",
        "K1B_PASS": (run / "K1B_OVERALL_STATUS.txt").read_text().strip() == "PASS",
        "K1B_next_action_exact": (run / "K1B_NEXT_ACTION.txt").read_text().strip() == "P13-S1-K2A_TRAIN_ONLY_RUNG_ADJUDICATION_AND_CONTINUATION_LOCK",
        "K1A_integrity_all": all(bool(v) for v in k1a_sum["integrity"].values()),
        "K1B_integrity_all": all(bool(v) for v in k1b_sum["integrity"].values()),
        "no_forbidden_reads": all(g.get("OPENED_TRANSFER_DIAGNOSTIC_arrays_read") is False and g.get("WITHIN_FAMILY_TRANSFER_DIAGNOSTIC_payload_read") is False and g.get("DEVELOPMENT_read") is False and g.get("SEALED_read") is False and g.get("historical_response_read") is False for g in guards),
        "first_rung_expected": int(k1b_sum["K1_first_rung_structural_proposals_expected_after_PASS"]) == 98304,
    }
    if not all(gate.values()):
        raise RuntimeError(f"K2A entry gate failed: {gate}")
    return run, {"locks": locks, "gate": gate, "authoritative_S1_run": str(run.relative_to(root)), "K1A_K1B_audit_archives_used_as_runtime_input": False}


def _update_rolling_context(root: Path, summary: dict[str, Any]) -> None:
    p = root / "P13_S1_ROLLING_CONTEXT.md"
    if not p.is_file():
        return
    marker = "<!-- K2A_FORMAL_RESULT -->"
    dec = summary["continuation_decision"]
    block = f'''## S1-K2A — TRAIN-only first-rung adjudication / continuation lock\n\n- authoritative S1 store: `{summary['authoritative_S1_run']}`\n- `OVERALL_STATUS`: **{summary['OVERALL_STATUS']}**\n- complete first-rung horizon adjudicated: `4 paired seeds x 3 arms x 8192 = 98,304` structural proposals\n- analysis inputs: authoritative S1 proposal/branch/skeleton ledgers only; transfer diagnostics / DEVELOPMENT / SEALED / historical response remain unread\n- qualification-ledger consistency: `{summary['integrity']['qualification_recompute_consistent']}`\n- complete-F4 branch-ledger consistency: `{summary['integrity']['F4_emissions_fully_represented_in_branch_registry']}`\n- clear FULL `OPERATOR_QUALIFIED_TRAIN` branch count at first rung: `{summary['cohort_counts']['FULL_clear']}`\n- FULL `OPERATOR_QUALIFIED_UNRESOLVED` branch count: `{summary['cohort_counts']['FULL_unresolved']}`\n- FULL-V2 4096->8192 per-seed progress: `{dec['r_s']}`; median: `{dec['median_r_s']}`\n- continuation decision: **{dec['reason']}**\n- search horizon state: `{summary['search_horizon_state']}`\n- next action: `{summary['NEXT_ACTION']}`\n\n{marker}\n'''
    text = p.read_text()
    if marker in text:
        text = text.replace(marker, block)
    else:
        text = text.rstrip() + "\n\n" + block
    _write_text(p, text)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-root", default=".")
    ap.add_argument("--workers", type=int, default=16)
    args = ap.parse_args(argv)
    root = Path(args.project_root).resolve()
    for p in [root / "phases/p13/coefficient_law_raw_xt/src", root / "phases/p11/raw_xt_td/src"]:
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    cfg_path = root / "phases/p13/coefficient_law_raw_xt/configs/p13_s1_k2a_protocol.json"
    cfg = _load_json(cfg_path)
    run, entry = _verify_entry(root, cfg)
    _write_text(root / "phases/p13/coefficient_law_raw_xt/runs/LATEST_P13_S1_K2A_RUN.txt", str(run.relative_to(root)))
    _write_json(run / "K2A_entry_provenance.json", entry)
    _write_json(run / "K2A_data_boundary_guard.json", {
        "allowed_inputs": cfg["data_boundary"]["allowed"],
        "forbidden_inputs": cfg["data_boundary"]["forbidden"],
        "OPENED_TRANSFER_DIAGNOSTIC_read": False,
        "WITHIN_FAMILY_TRANSFER_DIAGNOSTIC_read": False,
        "DEVELOPMENT_read": False,
        "SEALED_read": False,
        "historical_response_read": False,
        "J_capacity_or_R_att_used_for_continuation": False,
        "status": "PASS",
    })

    checkpoints = list(map(int, cfg["checkpoints"]))
    tau = float(cfg["tau_num"])
    units: dict[tuple[int, str], dict[str, Any]] = {}
    branch_jobs = []
    work = run / "K2A_work"
    work.mkdir(exist_ok=True)
    identity_hex = None
    integrity = {
        "all_12_units_present_and_PASS": True,
        "proposal_ledgers_complete_8192": True,
        "silent_fallback_zero_all_units": True,
        "F4_emissions_fully_represented_in_branch_registry": True,
        "qualification_recompute_consistent": True,
        "identity_baseline_consistent": True,
        "no_forbidden_data_read": True,
    }
    inputs = []
    start = time.perf_counter()
    for seed in cfg["paired_seeds"]:
        for arm in cfg["arms"]:
            unit = run / "units" / f"seed_{int(seed):02d}" / arm
            us = _load_json(unit / "unit_summary.json")
            if us.get("status") != "PASS" or int(us.get("processed", -1)) != 8192 or int(us.get("total", -1)) != 8192:
                integrity["all_12_units_present_and_PASS"] = False
            c = us.get("counters", {})
            if int(c.get("silent_fallback_to_V1", 0)) != 0:
                integrity["silent_fallback_zero_all_units"] = False
            if int(c.get("F4_branch_emissions", 0)) != int(c.get("F4_scientific_branches", 0)):
                integrity["F4_emissions_fully_represented_in_branch_registry"] = False
            ids = [float(x) for x in us["identity_baseline"]["J_i"]]
            hx = _float_hex_list(ids)
            if identity_hex is None: identity_hex = hx
            elif hx != identity_hex: integrity["identity_baseline_consistent"] = False
            pp = unit / "proposal_ledger.jsonl"
            bp = unit / "branch_registry.jsonl"
            sp = unit / "skeleton_registry.jsonl"
            prop = _scan_proposal_ledger(pp, checkpoints)
            if prop["row_count"] != 8192:
                integrity["proposal_ledgers_complete_8192"] = False
            units[(int(seed), arm)] = {"unit": str(unit.relative_to(root)), "unit_summary": us, "proposal": prop}
            inputs.append({"path": str(pp.relative_to(root)), "bytes": prop["bytes"], "sha256": prop["sha256"]})
            usp = unit / "unit_summary.json"
            inputs.append({"path": str(usp.relative_to(root)), "bytes": usp.stat().st_size, "sha256": _sha256_path(usp)})
            cand = work / f"seed_{int(seed):02d}" / f"{arm}_membership_candidates.jsonl"
            result_cache = work / f"seed_{int(seed):02d}" / f"{arm}_branch_scan_result.json"
            branch_jobs.append((int(seed), arm, str(bp), str(cand), str(result_cache), ids))
    print(f"[P13 S1 K2A] proposal ledgers scanned units=12/12 elapsed={time.perf_counter()-start:.1f}s", flush=True)

    # Branch registries are the large scientific evidence. Stream them in parallel; each worker writes only compact membership locators.
    max_workers = max(1, min(int(args.workers), len(branch_jobs), 16))
    branch_results = {}
    pending = []
    for seed, arm, bp, cand, result_cache, ids in branch_jobs:
        cached = _cached_branch_result(Path(bp), Path(cand), Path(result_cache), arm in cfg["full_arms"])
        if cached is not None:
            branch_results[(seed, arm)] = cached
            print(f"[P13 S1 K2A] resume-cache hit seed={seed} arm={arm}", flush=True)
        else:
            pending.append((seed, arm, bp, cand, result_cache, ids))
    with ProcessPoolExecutor(max_workers=max_workers) as ex:
        futs = {
            ex.submit(_scan_branch_registry, bp, cand, ids, checkpoints, tau, arm in cfg["full_arms"], result_cache): (seed, arm)
            for seed, arm, bp, cand, result_cache, ids in pending
        }
        done = len(branch_results)
        while futs:
            for fut in list(futs):
                if fut.done():
                    key = futs.pop(fut)
                    branch_results[key] = fut.result(); done += 1
                    print(f"[P13 S1 K2A] branch units processed={done}/12 current=seed_{key[0]:02d}/{key[1]} elapsed={time.perf_counter()-start:.1f}s", flush=True)
            if futs:
                time.sleep(2.0)
    for key, br in branch_results.items():
        bp = Path(br["branch_registry"])
        if not bp.is_absolute():
            bp = root / bp
        inputs.append({"path": str(bp.relative_to(root)), "bytes": int(br["branch_bytes"]), "sha256": br["branch_sha256"]})
        br = dict(br)
        br["branch_registry"] = str(bp.relative_to(root))
        units[key]["branch"] = br
        if int(br["qualification_recompute_mismatch_count"]) != 0:
            integrity["qualification_recompute_consistent"] = False
        expected = int(units[key]["unit_summary"].get("counters", {}).get("F4_scientific_branches", 0))
        if int(br["branch_line_count"]) != expected:
            integrity["F4_emissions_fully_represented_in_branch_registry"] = False

    # Scientific summaries from frozen TRAIN-only evidence.
    cohort = {"NULL_clear": 0, "NULL_unresolved": 0, "FULL_clear": 0, "FULL_unresolved": 0}
    unit_summaries = {}
    for (seed, arm), u in sorted(units.items()):
        q = u["branch"]["qualification_counts"]
        if arm == "NULL-V2":
            cohort["NULL_clear"] += q["clear"]; cohort["NULL_unresolved"] += q["unresolved"]
        else:
            cohort["FULL_clear"] += q["clear"]; cohort["FULL_unresolved"] += q["unresolved"]
        unit_summaries[f"seed_{seed:02d}/{arm}"] = {
            "proposal_checkpoints": u["proposal"]["prefix"],
            "branch_checkpoints": u["branch"]["checkpoint_summary"],
            "best_branch_records": u["branch"]["best_records"],
            "qualification_counts": q,
            "V2_counters": {k: int(v) for k, v in u["unit_summary"].get("counters", {}).items() if k in {"mutation_slots_total","standard_mutation_selected","graft_selected","graft_attempted","graft_successful_child","graft_parent_theta_saturated","graft_no_legal_child","silent_fallback_to_V1"}},
        }

    pairwise = {
        "equal_structural_proposals": _pairwise_seed_effects(units, cfg["pairwise_comparisons"], tau, "proposals"),
        "equal_evaluator_calls": _pairwise_seed_effects(units, cfg["pairwise_comparisons"], tau, "calls"),
        "equal_accumulated_CPU": _pairwise_seed_effects(units, cfg["pairwise_comparisons"], tau, "cpu"),
    }
    frontier_effects = {}
    for arm in cfg["full_arms"]:
        rows = []
        for seed in cfg["paired_seeds"]:
            br = units[(int(seed), arm)]["branch"]["checkpoint_summary"]["8192"]
            rows.append({"paired_seed": int(seed), **_effect(br["best_dep"], br["best_free"], tau)})
        frontier_effects[arm] = {"per_seed": rows, "summary": _direction_summary(rows)}

    per_seed_prog = []
    for seed in cfg["paired_seeds"]:
        br = units[(int(seed), "FULL-V2")]["branch"]["checkpoint_summary"]
        per_seed_prog.append((br["4096"]["best_any"], br["8192"]["best_any"]))
    clear_full_exists = cohort["FULL_clear"] > 0
    invariants_pass = all(integrity.values())
    cont = continuation_decision(per_seed_prog, clear_full_exists, invariants_pass)
    cont.update({
        "clear_FULL_operator_qualified_exists": clear_full_exists,
        "integrity_pass": invariants_pass,
        "decision_inputs": ["FULL clear qualification existence", "FULL-V2 B_s(4096)", "FULL-V2 B_s(8192)", "implementation/restart/accounting invariants"],
        "forbidden_inputs_used": [],
        "eligible_extension": "NULL-V2 and FULL-V2 only, +24576 proposals/seed/arm to 32768 total" if cont["authorized"] else None,
    })

    if not invariants_pass:
        status = "FAIL"
        next_action = "REPAIR_K2A_LEDGER_OR_INTEGRITY_INCONSISTENCY_BEFORE_ANY_CONTINUATION_OR_DIAGNOSTIC_OPENING"
        horizon_state = "NOT_FROZEN_INTEGRITY_FAILURE"
        # membership candidates are not authoritative on a failed K2A.
        for p in work.glob("seed_*/*_membership_candidates.jsonl"):
            p.unlink(missing_ok=True)
        membership = {"frozen": False, "reason": "K2A_INTEGRITY_FAILURE"}
    elif cont["authorized"]:
        status = "PASS"
        next_action = "P13-S1-K1C_OPTIONAL_CONTINUATION_NULL_V2_FULL_V2_TO_32768"
        horizon_state = "CONTINUATION_AUTHORIZED_FIRST_RUNG_NOT_FINAL"
        for p in work.glob("seed_*/*_membership_candidates.jsonl"):
            p.unlink(missing_ok=True)
        membership = {"frozen": False, "reason": "AUTHORIZED_CONTINUATION_MUST_COMPLETE_BEFORE_FINAL_TRAIN_MEMBERSHIP_FREEZE"}
    else:
        status = "PASS"
        next_action = "P13-S1-K2B_POST_SEARCH_DIAGNOSTICS"
        horizon_state = "FROZEN_AT_8192_BY_PREREGISTERED_TRAIN_ONLY_RULE"
        candidate_paths = [work / f"seed_{int(seed):02d}" / f"{arm}_membership_candidates.jsonl" for seed in cfg["paired_seeds"] for arm in cfg["full_arms"]]
        membership = {"frozen": True, **_finalize_membership(root, run, candidate_paths)}
        for seed in cfg["paired_seeds"]:
            for arm in cfg["full_arms"]:
                sp = run / "units" / f"seed_{int(seed):02d}" / arm / "skeleton_registry.jsonl"
                inputs.append({"path": str(sp.relative_to(root)), "bytes": sp.stat().st_size, "sha256": _sha256_path(sp)})
        for p in candidate_paths:
            p.unlink(missing_ok=True)

    # No J_capacity/R_att is loaded or calculated here; the search horizon must be frozen first.
    summary = {
        "stage": "P13-S1-K2A",
        "OVERALL_STATUS": status,
        "authoritative_S1_run": str(run.relative_to(root)),
        "first_rung_structural_proposals": 98304,
        "cohort_counts": cohort,
        "integrity": integrity,
        "unit_summaries": unit_summaries,
        "paired_arm_effects": pairwise,
        "coefficient_dependent_vs_free_frontier_effects": frontier_effects,
        "continuation_decision": cont,
        "search_horizon_state": horizon_state,
        "TRAIN_membership": membership,
        "route_pattern_interpretation": "DEFERRED: K2A records preregistered route evidence but does not invent a new scalar/threshold for Patterns A-F; if horizon is final, K2B may add only preregistered diagnostics without changing membership",
        "J_capacity_R_att_used_for_membership_or_continuation": False,
        "diagnostic_or_response_data_used": False,
        "NEXT_ACTION": next_action,
    }
    _write_json(run / "K2A_scientific_summary.json", summary)
    _write_json(run / "K2A_continuation_decision.json", cont)
    _write_json(run / "K2A_authoritative_input_manifest.json", {"objects": inputs, "policy": "read authoritative S1 files in place; no audit tar or duplicate scientific store"})
    _write_json(run / "K2A_membership_lock.json", membership)
    _write_text(run / "K2A_OVERALL_STATUS.txt", status + "\n")
    _write_text(run / "K2A_CONTINUATION_STATUS.txt", cont["reason"] + "\n")
    _write_text(run / "K2A_NEXT_ACTION.txt", next_action + "\n")
    _update_rolling_context(root, summary)
    # K2A_work is restart/checkpoint state only. Preserve it on failure/interruption; remove it after successful adjudication.
    if status == "PASS" and work.exists():
        import shutil
        shutil.rmtree(work)
    print(f"OVERALL_STATUS={status}", flush=True)
    return 0 if status == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
