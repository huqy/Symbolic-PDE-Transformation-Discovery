from __future__ import annotations

import argparse
import json
import math
import os
import platform
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from reproduce import s0_contract as s0
from typing import Any

import numpy as np

from .calibration_instruments import (
    build_full_capacity_pair,
    build_null_capacity_pair,
    parameter_names_for_pair,
)
from .family_evaluator import load_field_views
from .k2_qualification import (
    add_source_paths,
    canonical_json_bytes,
    causal_response_gate,
    derive_seed,
    json_load,
    run_parallel_tasks,
    sha256_bytes,
    sha256_path,
    write_json,
    write_text,
    _evaluator,
    _worker_init,
    _worker_proposal_task,
)
from .k2r2_attainment_repair import (
    EXPECTED_K2_SEMANTIC,
    _adjudicate_objects,
    _burnin_worker,
    _burnin_worker_init,
    _load_base_k2_protocol,
    _reference_tasks,
    _reference_worker_init,
    _truncate,
    _worker_reference_task,
    evaluator_fidelity_gate_r2,
)

EXPECTED_K2R3_SEMANTIC = "1bb3ae221034b2f5da1e1e76889a57df1ea945a784d82ed71f7828133788d6d4"
EXPECTED_K2R2_SEMANTIC = "165d6e41e6b9b25e09456e0733a410f073cca422ebf2544e5d6980461bf07acc"


def chronological_unique_f4_membership(rows: list[dict[str, Any]], max_count: int) -> list[dict[str, Any]]:
    """Pure membership rule used for tests/audit semantics.

    Membership depends only on F4 hard-gate attainment, structural uniqueness, and
    task chronology. Objective values never enter ordering or inclusion.
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in sorted(rows, key=lambda r: int(r["task_index"])):
        if row.get("best_f4") is None:
            continue
        h = str(row["pair"]["structural_hash"])
        if h in seen:
            continue
        seen.add(h)
        out.append(row)
        if len(out) >= int(max_count):
            break
    return out


def adjudicate_ratio_summary(ratios: list[float], minimum: int, median_max: float, p90_max: float) -> dict[str, Any]:
    if len(ratios) < int(minimum):
        return {"status": "FAIL", "failure": "PRODUCTION_FITTER_F4_ATTAINMENT_NOT_QUALIFIED", "R_fit_resolved": len(ratios)}
    arr = np.asarray(ratios, dtype=float)
    median = float(np.median(arr)); p90 = float(np.quantile(arr, 0.9))
    gates = {"R_fit_resolved_count": len(ratios) >= int(minimum), "median_R_fit": median <= float(median_max), "p90_R_fit": p90 <= float(p90_max)}
    return {"status": "PASS" if all(gates.values()) else "FAIL", "failure": None if all(gates.values()) else "PRODUCTION_FITTER_REGRET_NOT_QUALIFIED", "R_fit_resolved": len(ratios), "median_R_fit": median, "p90_R_fit": p90, "gates": gates}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _verify_parent_k2r3(root: Path, r4: dict[str, Any]) -> tuple[Path, Path, Path, Path, dict[str, Any]]:
    if s0.active(root): return s0.repair_parent(root, "K2R4")
    cfg = r4["parent_k2r3"]
    marker = root / cfg["marker"]
    if not marker.is_file():
        raise FileNotFoundError(marker)
    text = marker.read_text().strip()
    k2r3 = Path(text)
    if not k2r3.is_absolute():
        k2r3 = root / text
    if not k2r3.is_dir():
        raise FileNotFoundError(k2r3)

    summary = json_load(k2r3 / "audit_summary.json")
    sem = json_load(k2r3 / "semantic_output_digest.json")
    leakage = json_load(k2r3 / "no_leakage_guard.json")
    checks = {
        "semantic": sem.get("semantic_output_digest") == cfg["expected_semantic_output_digest"] == EXPECTED_K2R3_SEMANTIC,
        "status": (k2r3 / "OVERALL_STATUS.txt").read_text().strip() == cfg["expected_overall_status"],
        "next": (k2r3 / "NEXT_ACTION.txt").read_text().strip() == cfg["expected_next_action"],
        "failure": set(cfg["required_failure_classifications"]).issubset(set(summary.get("failure_classifications", []))),
        "capacity": summary.get("capacity_existence_status") == cfg["required_capacity_existence_status"],
        "full_optimum": summary.get("full_optimum_reference_status") == cfg["required_full_optimum_reference_status"],
        "no_leakage": leakage.get("status") == cfg["required_no_leakage_status"],
        "formal_S1_disabled": not bool(summary.get("formal_S1_search_authorized", True)),
    }
    if not all(checks.values()):
        raise RuntimeError(f"K2R4 parent K2R3 verification failed: {checks}")

    source = json_load(k2r3 / "source_manifest.json")
    source_by_path = {r["path"]: r["sha256"] for r in source.get("files", [])}
    required_current = [
        "phases/p13/coefficient_law_raw_xt/configs/p13_s0_k2r3_protocol.json",
        "phases/p13/coefficient_law_raw_xt/src/p13rawxt/k2r3_capacity_continuation.py",
        "phases/p13/coefficient_law_raw_xt/configs/p13_s0_k2r2_protocol.json",
        "phases/p13/coefficient_law_raw_xt/src/p13rawxt/k2r2_attainment_repair.py",
        "phases/p13/coefficient_law_raw_xt/src/p13rawxt/reference_optimizer_v2.py",
    ]
    source_checks = {}
    for rel in required_current:
        p = root / rel
        source_checks[rel] = p.is_file() and source_by_path.get(rel) == sha256_path(p)
    if not all(source_checks.values()):
        raise RuntimeError(f"K2R4 current source byte-lock failed: {source_checks}")

    def resolve_run(key: str) -> Path:
        p = Path(summary[key])
        if not p.is_absolute():
            p = root / p
        if not p.is_dir():
            raise FileNotFoundError(p)
        return p

    k2r2 = resolve_run("parent_K2R2_run")
    k2 = resolve_run("parent_K2_run")
    k1 = resolve_run("parent_K1_run")
    return k2r3, k2r2, k2, k1, {
        "status": "PASS",
        "checks": checks,
        "source_checks": source_checks,
        "K2R3_run": str(k2r3.relative_to(root)),
        "K2R3_semantic": sem["semantic_output_digest"],
        "K2R2_run": str(k2r2.relative_to(root)),
        "K2_run": str(k2.relative_to(root)),
        "K1_run": str(k1.relative_to(root)),
    }


def reproduce_parent_fitter_mechanism(root: Path, k2r3: Path, k2: Path, r4: dict[str, Any]) -> dict[str, Any]:
    cfg = r4["mechanism_lock"]
    production = _read_jsonl(k2 / "authoritative/fitter_results.jsonl")
    prod_f4 = sum(
        r.get("production", {}).get("best") is not None
        and r["production"]["best"].get("J_princ") is not None
        for r in production
    )
    levels: dict[str, Any] = {}
    all_match = len(production) == int(cfg["expected_naked_fitter_object_count"]) and prod_f4 == int(cfg["expected_production_F4_count"])
    for level in cfg["expected_reference_levels"]:
        rows = _read_jsonl(k2r3 / f"authoritative/reference_fitter_{level}.jsonl")
        by_obj: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            by_obj.setdefault(str(row["object_id"]), []).append(row)
        both_no_f4 = 0
        for rr in by_obj.values():
            if len(rr) == 2 and all(r.get("best") is None for r in rr):
                both_no_f4 += 1
        levels[level] = {
            "object_count": len(by_obj),
            "row_count": len(rows),
            "both_reference_launches_no_F4": both_no_f4,
        }
        all_match = all_match and len(by_obj) == int(cfg["expected_naked_fitter_object_count"])
        all_match = all_match and both_no_f4 == int(cfg["expected_both_reference_launches_no_F4_per_level"])
    out = {
        "status": "PASS" if all_match else "FAIL",
        "formal_K2R3_status_unchanged": True,
        "diagnostic_json_used_as_runtime_input": False,
        "production_object_count": len(production),
        "production_F4_count": prod_f4,
        "levels": levels,
        "interpretation": "the naked raw-prior 128-skeleton cohort contained no F4-attainable comparison objects under either production fitting or the frozen R0/R1/R2 reference launches; this diagnoses cohort infeasibility, not production-fitter regret",
    }
    if out["status"] != "PASS":
        raise RuntimeError(f"K2R4 mechanism lock did not reproduce the approved post-freeze diagnostic: {out}")
    return out


def _load_resolved_capacity_instruments(root: Path, k2r3: Path, base: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    cap = json_load(k2r3 / "capacity_evidence_repaired.json")
    polish = json_load(k2r3 / "full_capacity_optimum_polish.json")
    if cap.get("status") != "PASS" or polish.get("status") != "RESOLVED":
        raise RuntimeError("K2R4 requires K2R3 capacity PASS and resolved FULL optimum")
    lock = json_load(k2r3 / "calibration_only/capacity_instrument_lock_k2r3.json")
    stable_path = k2r3 / "calibration_only/stable_full_capacity_reference_k2r3.json"
    if not stable_path.is_file():
        raise FileNotFoundError(stable_path)
    stable = json_load(stable_path)
    instruments = {
        "null_capacity": {
            "pair": build_null_capacity_pair(base["caps"]),
            "theta": list(map(float, lock["theta"]["null_capacity"])),
            "role": "CALIBRATION_ONLY",
            "forbidden_from_search": True,
        },
        "full_capacity": {
            "pair": build_full_capacity_pair(base["caps"]),
            "theta": list(map(float, stable["theta"])),
            "role": "CALIBRATION_ONLY_STABLE_OPTIMUM_REFERENCE",
            "forbidden_from_search": True,
        },
    }
    return instruments, {
        "status": "PASS",
        "capacity_existence_status": cap["status"],
        "full_optimum_reference_status": polish["status"],
        "stable_J_capacity_G65": polish.get("stable_J_capacity_G65"),
        "source_run": str(k2r3.relative_to(root)),
        "rerun_capacity": False,
        "capacity_witness_used_as_search_information": False,
    }


def _operational_common_burnin(
    root: Path,
    k1: Path,
    base: dict[str, Any],
    r2: dict[str, Any],
    r4: dict[str, Any],
    workers: int,
    run: Path,
) -> dict[str, Any]:
    add_source_paths(root)
    from p11rawxt_s1.k2_search import _generate_task, _migrate_ring, _nondominance_view

    cfg = r2["common_burnin"]
    gp = cfg["gp"]
    total = int(r4["operational_burnin"]["structural_proposal_budget"])
    parent_count = int(r4["operational_burnin"]["parent_count"])
    fitter_max = int(r4["operational_burnin"]["operational_fitter_cohort_max"])
    batch_size = int(cfg["batch_size"])
    ledger = run / "authoritative/common_burnin_results.jsonl"
    checkpoint = run / "authoritative/common_burnin_checkpoint.json"
    pool_path = run / "authoritative/common_burnin_parent_pool.json"
    fitter_path = run / "authoritative/operational_fitter_F4_cohort.json"

    seed = derive_seed(cfg["seed_namespace"], 0)
    rng = np.random.default_rng(seed)
    islands = [[] for _ in range(int(gp["island_count"]))]
    duplicate_hashes: set[str] = set()
    gp_completed = [0 for _ in islands]
    next_task = 0
    completed = 0
    batches = 0
    operational_f4: list[dict[str, Any]] = []
    f4_hashes: set[str] = set()
    counters = Counter()
    committed_offset = 0
    resume_completed = 0

    if checkpoint.is_file():
        state = json_load(checkpoint)
        rng.bit_generator.state = state["rng_state"]
        islands = state["islands"]
        duplicate_hashes = set(state["duplicate_hashes"])
        gp_completed = list(map(int, state["gp_completed_by_island"]))
        next_task = int(state["next_task_index"])
        completed = int(state["completed_structural_proposals"])
        resume_completed = completed
        batches = int(state["completed_batches"])
        operational_f4 = state["operational_F4_cohort"]
        f4_hashes = set(state["F4_structural_hashes"])
        counters = Counter(state["counters"])
        committed_offset = int(state["ledger_byte_offset"])
        _truncate(ledger, committed_offset)

    ledger.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if completed else "w"
    start = time.perf_counter()
    last = start
    ctx = {
        "initial_complete_skeleton_target_per_island": int(gp["initial_complete_skeleton_target_per_island"]),
        "island_count": int(gp["island_count"]),
        "offspring_mix": gp["offspring_mix"],
        "migration": gp["migration"],
    }

    with ledger.open(mode, encoding="utf-8") as out, ProcessPoolExecutor(
        max_workers=workers,
        initializer=_burnin_worker_init,
        initargs=(str(root), str(k1), base),
    ) as ex:
        while completed < total:
            batch = []
            while len(batch) < batch_size and next_task < total:
                task, dup = _generate_task(rng, next_task, "gp", islands, base["caps"], ctx, duplicate_hashes, gp_completed)
                counters["duplicate_cache_hits"] += int(dup)
                counters["generator_rejections"] += int(task.get("generator_rejections", 0))
                batch.append(task)
                next_task += 1
            futures = [ex.submit(_burnin_worker, t) for t in batch]
            results = [f.result() for f in futures]
            for row in sorted(results, key=lambda x: int(x["task_index"])):
                completed += 1
                counters["completed_calls"] += int(row.get("completed_calls", 0))
                counters["worker_cpu_millis"] += int(round(1000 * float(row.get("worker_elapsed_seconds", 0.0))))
                if row.get("fatal_exception"):
                    counters["fatal_exception_count"] += 1
                else:
                    island = int(row["island"])
                    gp_completed[island] += 1
                    rep = row.get("representative")
                    if rep is not None:
                        islands[island].append({"pair": row["pair"], "representative": rep})
                        islands[island] = _nondominance_view(islands[island])
                    bf = row.get("best_f4")
                    if bf is not None:
                        h = row["pair"]["structural_hash"]
                        if h not in f4_hashes:
                            f4_hashes.add(h)
                            counters["F4_unique_skeletons"] += 1
                            if len(operational_f4) < fitter_max:
                                operational_f4.append({
                                    "operational_index": len(operational_f4),
                                    "task_index": int(row["task_index"]),
                                    "pair": row["pair"],
                                    "structural_hash": h,
                                    "production_best": bf,
                                    "production_completed_calls": int(row.get("completed_calls", 0)),
                                    "membership_reason": "chronologically_first_unique_production_F4_structural_skeleton",
                                })
                    counters["F4_branch_emissions"] += int(row.get("F4_branch_count", 0))
                    counters["warning_count"] += int(row.get("warning_count", 0))
                compact = {k: v for k, v in row.items() if k not in {"pair", "representative", "best_f4"}}
                compact["structural_hash"] = row.get("pair", {}).get("structural_hash")
                compact["F4"] = row.get("best_f4") is not None
                out.write(json.dumps(compact, sort_keys=True) + "\n")
            batches += 1
            mig = gp.get("migration", {})
            interval = int(mig.get("interval_completed_batches", 0))
            if interval > 0 and batches % interval == 0:
                events = _migrate_ring(islands, int(mig.get("records_per_island", 0)))
                counters["migration_events"] += 1 if events else 0
                counters["migration_records"] += len(events)
            out.flush(); os.fsync(out.fileno())
            if batches % int(r4["runtime"]["checkpoint_every_batches"]) == 0 or completed >= total:
                state = {
                    "schema": "P13_K2R4_OPERATIONAL_BURNIN_CHECKPOINT_V1",
                    "rng_state": rng.bit_generator.state,
                    "islands": islands,
                    "duplicate_hashes": sorted(duplicate_hashes),
                    "gp_completed_by_island": gp_completed,
                    "next_task_index": next_task,
                    "completed_structural_proposals": completed,
                    "completed_batches": batches,
                    "operational_F4_cohort": operational_f4,
                    "F4_structural_hashes": sorted(f4_hashes),
                    "counters": dict(counters),
                    "ledger_byte_offset": int(out.tell()),
                }
                write_json(checkpoint, state)
            now = time.perf_counter()
            if now - last >= float(r4["runtime"]["progress_every_seconds"]) or completed >= total:
                elapsed = now - start
                rate = (completed - resume_completed) / max(elapsed, 1e-12)
                eta = (total - completed) / rate if rate > 0 else math.inf
                print(
                    f"[K2R4 operational burn-in] processed={completed}/{total} "
                    f"F4_unique={len(f4_hashes)} V2_parents={min(len(operational_f4), parent_count)}/{parent_count} "
                    f"fitter_F4_cohort={len(operational_f4)}/{fitter_max} calls={counters['completed_calls']} "
                    f"elapsed={elapsed:.1f}s rate={rate:.3g}/s ETA={eta/3600:.2f}h",
                    flush=True,
                )
                last = now

    parent_rows = [
        {
            "parent_index": i,
            "task_index": int(row["task_index"]),
            "pair": row["pair"],
            "theta_vector": row["production_best"]["theta_vector"],
            "J_parent": row["production_best"]["J_princ"],
            "J_XT": row["production_best"].get("J_XT"),
            "J_XX": row["production_best"].get("J_XX"),
            "membership_reason": "chronologically_first_unique_F4_structural_skeleton",
        }
        for i, row in enumerate(operational_f4[:parent_count])
    ]
    write_json(pool_path, {
        "schema": "P13_K2R4_COMMON_BURNIN_PARENT_POOL_V1",
        "rows": parent_rows,
        "F4_unique_skeletons": len(f4_hashes),
        "completed_structural_proposals": completed,
        "membership_rule": r4["operational_burnin"]["V2_parent_membership_rule"],
        "formal_S1_seed_export": False,
    })
    write_json(fitter_path, {
        "schema": "P13_K2R4_OPERATIONAL_FITTER_F4_COHORT_V1",
        "rows": operational_f4,
        "cohort_count": len(operational_f4),
        "cohort_max": fitter_max,
        "membership_rule": r4["operational_burnin"]["operational_fitter_membership_rule"],
        "objective_ranking_used": False,
        "formal_S1_seed_export": False,
    })
    fatal = int(counters["fatal_exception_count"])
    enough = len(parent_rows) >= parent_count
    status = "PASS" if fatal == 0 and enough else "FAIL"
    failure = None if status == "PASS" else ("OPERATIONAL_COMMON_BURNIN_NUMERICAL_UNRESOLVED" if fatal else r4["operational_burnin"]["failure_if_fewer_than_64_unique_F4"])
    return {
        "status": status,
        "failure": failure,
        "structural_proposal_budget": total,
        "completed_structural_proposals": completed,
        "F4_unique_skeletons": len(f4_hashes),
        "F4_unique_rate": len(f4_hashes) / max(completed, 1),
        "frozen_V2_parent_count": len(parent_rows),
        "required_V2_parent_count": parent_count,
        "operational_fitter_F4_cohort_count": len(operational_f4),
        "operational_fitter_F4_cohort_max": fitter_max,
        "counters": dict(counters),
        "parent_pool": "authoritative/common_burnin_parent_pool.json",
        "operational_fitter_cohort": "authoritative/operational_fitter_F4_cohort.json",
        "result_ledger": "authoritative/common_burnin_results.jsonl",
        "checkpoint": "authoritative/common_burnin_checkpoint.json",
        "claim_boundary": "system-level ability of the current generic GP plus unchanged production fitter to form at least 64 unique F4 states within 8192 response-blind calibration proposals",
    }


def _operational_fitter_qualification(
    root: Path,
    k1: Path,
    base: dict[str, Any],
    r2: dict[str, Any],
    r4: dict[str, Any],
    workers: int,
    run: Path,
) -> dict[str, Any]:
    cohort = json_load(run / "authoritative/operational_fitter_F4_cohort.json")
    rows = cohort.get("rows", [])
    objects = [
        {
            "object_id": f"operational_F4_{int(row['operational_index']):03d}",
            "index": int(row["operational_index"]),
            "pair": row["pair"],
            "parameter_names": parameter_names_for_pair(row["pair"]),
            "production_best": row["production_best"],
            "progress_label": f"K2R4 operational fitter {int(row['operational_index']):03d}",
        }
        for row in rows
    ]
    minimum = int(r4["operational_fitter"]["minimum_R_fit_resolved"])
    if len(objects) < minimum:
        return {
            "status": "NOT_RUN",
            "failure": "OPERATIONAL_F4_COHORT_INSUFFICIENT_FOR_FITTER",
            "cohort_count": len(objects),
            "minimum_required": minimum,
            "production_fitter_adjudication": "NOT_ADJUDICATED",
        }

    levels = []
    selected = None
    for level in r2["reference_optimizer_v2"]["fidelity_ladder"]:
        tasks = _reference_tasks(objects, level, r2)
        out_path = run / f"authoritative/operational_reference_fitter_{level['level']}.jsonl"
        ref_rows = run_parallel_tasks(
            tasks,
            _worker_reference_task,
            _reference_worker_init,
            (str(root), str(k1), base, 33),
            workers,
            out_path,
            f"K2R4 operational ref fitter {level['level']}",
            int(r4["runtime"]["progress_every_seconds"]),
        )
        adj = _adjudicate_objects(objects, ref_rows, r2)
        reference_qualified = 0
        ratios: list[float] = []
        disagreements: list[float] = []
        for obj in objects:
            a = adj[obj["object_id"]]
            if a["qualified"]:
                reference_qualified += 1
                pb = obj["production_best"]
                if a.get("best") is not None and pb is not None and pb.get("J_princ") is not None:
                    ratios.append(float(pb["J_princ"]) / max(float(a["best"]["J_princ"]), 1e-15))
            if a.get("launch_relative_agreement") is not None:
                disagreements.append(float(a["launch_relative_agreement"]))
        ls = {
            "level": level["level"],
            "cohort_count": len(objects),
            "reference_qualified": reference_qualified,
            "R_fit_resolved": len(ratios),
            "minimum_required": minimum,
            "reference_agreement_median": None if not disagreements else float(np.median(disagreements)),
            "reference_agreement_p90": None if not disagreements else float(np.quantile(disagreements, 0.9)),
        }
        levels.append(ls)
        write_json(run / f"operational_reference_fitter_{level['level']}_summary.json", ls)
        if len(ratios) >= minimum:
            selected = (level, adj, ratios)
            break

    if selected is None:
        best_level = levels[-1] if levels else {}
        if int(best_level.get("reference_qualified", 0)) < minimum:
            failure = "OPERATIONAL_REFERENCE_NOT_QUALIFIED"
        else:
            failure = "PRODUCTION_FITTER_F4_ATTAINMENT_NOT_QUALIFIED"
        return {
            "status": "FAIL",
            "failure": failure,
            "cohort_membership": "chronologically first operational unique F4 skeletons; F4 hard gate plus chronology only",
            "cohort_count": len(objects),
            "levels": levels,
            "production_fitter_adjudication": "NOT_ADJUDICATED",
            "claim_boundary": r4["operational_fitter"]["claim_boundary"],
        }

    level, adj, ratios = selected
    ratio_adj = adjudicate_ratio_summary(
        ratios, minimum,
        float(r4["operational_fitter"]["median_R_fit_max"]),
        float(r4["operational_fitter"]["p90_R_fit_max"]),
    )
    median = ratio_adj["median_R_fit"]; p90 = ratio_adj["p90_R_fit"]; gates = ratio_adj["gates"]; status = ratio_adj["status"]
    return {
        "status": status,
        "failure": ratio_adj["failure"],
        "selected_reference_level": level["level"],
        "cohort_count": len(objects),
        "reference_qualified": sum(bool(adj[o["object_id"]]["qualified"]) for o in objects),
        "R_fit_resolved": len(ratios),
        "median_R_fit": median,
        "p90_R_fit": p90,
        "gates": gates,
        "levels": levels,
        "production_fitter_changed": False,
        "reference_optimizer_changed": False,
        "cohort_membership": "chronologically first operational unique F4 skeletons; F4 hard gate plus chronology only",
        "claim_boundary": r4["operational_fitter"]["claim_boundary"],
    }


def _residual_graft_retest_r4(root: Path, k1: Path, base: dict[str, Any], r2: dict[str, Any], workers: int, run: Path) -> dict[str, Any]:
    pool = json_load(run / "authoritative/common_burnin_parent_pool.json")["rows"]
    required = int(r2["common_burnin"]["parent_count"])
    if len(pool) < required:
        return {"status": "NOT_RUN", "failure": "COMMON_BURNIN_PARENT_POOL_INSUFFICIENT", "residual_graft_adjudication": "NOT_ADJUDICATED"}
    n = int(r2["residual_graft"]["proposal_attempts"])
    ns = r2["residual_graft"]["seed_namespace"]
    tasks = [{"index": i, "parent": pool[i % len(pool)], "seed": derive_seed(ns, i + 1)} for i in range(n)]
    rows = run_parallel_tasks(
        tasks,
        _worker_proposal_task,
        _worker_init,
        (str(root), str(k1), base, 33),
        workers,
        run / "authoritative/v2_proposal_results_k2r4.jsonl",
        "K2R4 V2 proposals",
        60,
    )
    f4 = [r for r in rows if r.get("proposal_status") == "F4_CHILD"]
    n5 = sum(float(r.get("relative_improvement", -math.inf)) >= 0.05 for r in f4)
    n20 = sum(float(r.get("relative_improvement", -math.inf)) >= 0.20 for r in f4)
    fatal = sum(bool(r.get("fatal_exception")) for r in rows)
    gates = {
        "improve_5pct": n5 >= int(r2["residual_graft"]["improve_5pct_min_count"]),
        "improve_20pct": n20 >= int(r2["residual_graft"]["improve_20pct_min_count"]),
        "fatal_count_zero": fatal == 0,
    }
    return {
        "status": "PASS" if all(gates.values()) else "FAIL",
        "failure": None if all(gates.values()) else ("V2_PROPOSAL_NUMERICAL_UNRESOLVED" if fatal else "PROPOSAL_GEOMETRY_NOT_QUALIFIED"),
        "gates": gates,
        "attempted_proposals": n,
        "F4_children": len(f4),
        "improve_5pct_count": n5,
        "improve_20pct_count": n20,
        "fatal_exception_count": fatal,
        "result_ledger": "authoritative/v2_proposal_results_k2r4.jsonl",
        "residual_graft_adjudication": "ADJUDICATED",
        "parent_source": "K2R4 common burn-in chronological first-64 unique F4",
    }


def _source_manifest(root: Path, paths: list[str]) -> dict[str, Any]:
    rows = []
    for rel in paths:
        p = root / rel
        rows.append({"path": rel, "sha256": sha256_path(p), "bytes": p.stat().st_size})
    return {"files": rows}


def _evidence_manifest(root: Path, run: Path) -> dict[str, Any]:
    names = [
        "authoritative/common_burnin_results.jsonl",
        "authoritative/common_burnin_checkpoint.json",
        "authoritative/common_burnin_parent_pool.json",
        "authoritative/operational_fitter_F4_cohort.json",
        "authoritative/operational_reference_fitter_R0.jsonl",
        "authoritative/operational_reference_fitter_R1.jsonl",
        "authoritative/operational_reference_fitter_R2.jsonl",
        "authoritative/v2_proposal_results_k2r4.jsonl",
    ]
    rows = []
    for name in names:
        p = run / name
        if p.is_file():
            rows.append({
                "path": str(p.relative_to(root)),
                "sha256": sha256_path(p),
                "bytes": p.stat().st_size,
                "line_count": sum(1 for _ in p.open()) if p.suffix == ".jsonl" else None,
            })
    return {"large_or_redundant_objects_copied_into_audit": False, "rows": rows}


def _update_context(root: Path, summary: dict[str, Any], semantic: str, run_rel: str) -> None:
    path = root / "P13_S0_ROLLING_CONTEXT.md"
    if not path.exists():
        return
    marker = "## K2R4 formal result"
    block = f'''{marker}\n\n- authoritative run: `{run_rel}`\n- `OVERALL_STATUS`: **{summary['OVERALL_STATUS']}**\n- semantic output digest: `{semantic}`\n- failure classifications: `{summary.get('failure_classifications', [])}`\n- operational burn-in: `{summary.get('operational_burnin_status')}`\n- operational fitter: `{summary.get('operational_fitter_status')}`\n- next action: `{summary['NEXT_ACTION']}`\n- fitter claim is conditional on operational F4 attainment; the burn-in gate separately adjudicates system-level F4-state formation.\n- formal S1 remains unauthorized; K3 is required before any S1 promotion.\n- DEV/SEALED and OPENED_TRANSFER_DIAGNOSTIC remain unread.\n'''
    text = path.read_text()
    if marker in text:
        text = text.split(marker)[0].rstrip() + "\n\n" + block
    else:
        text = text.rstrip() + "\n\n" + block
    write_text(path, text)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-root", default=".")
    ap.add_argument("--workers", type=int, default=None)
    args = ap.parse_args(argv)
    root = Path(args.project_root).resolve()
    add_source_paths(root)

    r4_path = root / "phases/p13/coefficient_law_raw_xt/configs/p13_s0_k2r4_protocol.json"
    r4 = json_load(r4_path)
    r4_sha = sha256_path(r4_path)
    r2_path = root / "phases/p13/coefficient_law_raw_xt/configs/p13_s0_k2r2_protocol.json"
    r2 = json_load(r2_path)
    k2r3, k2r2, k2, k1, parent = _verify_parent_k2r3(root, r4)
    base = _load_base_k2_protocol(root, k2)
    mechanism = reproduce_parent_fitter_mechanism(root, k2r3, k2, r4)
    instruments, capacity_reuse = _load_resolved_capacity_instruments(root, k2r3, base)

    workers = args.workers or max(1, int(os.environ.get("NSLOTS", "17")) - 1)
    workers = max(1, min(int(r4["runtime"]["default_workers"]), workers))
    marker = root / "phases/p13/coefficient_law_raw_xt/runs/LATEST_P13_S0_K2R4_RUN.txt"
    run = None
    if marker.is_file():
        p = Path(marker.read_text().strip())
        p = p if p.is_absolute() else root / p
        if p.is_dir() and not (p / "OVERALL_STATUS.txt").is_file():
            run = p
    if run is None:
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        run = root / f"phases/p13/coefficient_law_raw_xt/runs/p13_s0_k2r4_operational_fitter_{stamp}"
        run.mkdir(parents=True)
        write_text(marker, str(run.relative_to(root)) + "\n")
    (run / "authoritative").mkdir(exist_ok=True)
    write_json(run / "parent_k2r3_inplace_verification.json", parent)
    write_json(run / "parent_fitter_mechanism_verification.json", mechanism)
    write_json(run / "capacity_reuse_lock.json", capacity_reuse)

    failures: list[str] = []
    gates: dict[str, str] = {"parent_mechanism_lock": "PASS", "capacity_reuse": "PASS"}
    start = time.perf_counter()
    print(f"P13-S0-K2R4 run={run.relative_to(root)} workers={workers}", flush=True)

    print("[K2R4 1/5] operational production-matched common F4 burn-in", flush=True)
    burn = _operational_common_burnin(root, k1, base, r2, r4, workers, run)
    write_json(run / "operational_burnin_qualification.json", burn)
    gates["operational_burnin"] = burn["status"]
    if burn["status"] != "PASS":
        failures.append(str(burn.get("failure")))
        fit = {"status": "NOT_RUN", "reason": "OPERATIONAL_BURNIN_PRECONDITION_FAIL", "production_fitter_adjudication": "NOT_ADJUDICATED"}
        prop = {"status": "NOT_RUN", "residual_graft_adjudication": "NOT_ADJUDICATED"}
    else:
        print("[K2R4 2/5] operational F4-conditioned production/reference fitter qualification", flush=True)
        fit = _operational_fitter_qualification(root, k1, base, r2, r4, workers, run)
        write_json(run / "operational_fitter_qualification.json", fit)
        gates["operational_fitter"] = fit["status"]
        if fit["status"] != "PASS":
            failures.append(str(fit.get("failure")))
            prop = {"status": "NOT_RUN", "residual_graft_adjudication": "NOT_ADJUDICATED"}
        else:
            print("[K2R4 3/5] frozen V2 residual-graft partial-credit retest", flush=True)
            prop = _residual_graft_retest_r4(root, k1, base, r2, workers, run)
            write_json(run / "proposal_geometry_requalification.json", prop)
            gates["proposal_geometry"] = prop["status"]
            if prop["status"] != "PASS":
                failures.append(str(prop.get("failure")))

    if not (run / "operational_fitter_qualification.json").is_file():
        write_json(run / "operational_fitter_qualification.json", fit)
    if not (run / "proposal_geometry_requalification.json").is_file():
        write_json(run / "proposal_geometry_requalification.json", prop)

    if not failures:
        print("[K2R4 4/5] original K2 evaluator fidelity + lower-order diagnostics", flush=True)
        ef = evaluator_fidelity_gate_r2(root, k1, base, instruments, run)
        write_json(run / "evaluator_fidelity_cost.json", ef)
        gates["evaluator_fidelity"] = ef["status"]
        lod = {"status": "PASS", "diagnostic_only": True, "rows": [{"name": x["name"], "G65_lower_order": x.get("G65_lower_order")} for x in ef.get("rows", [])]}
        write_json(run / "lower_order_diagnostics.json", lod)
        gates["lower_order_diagnostics"] = "PASS"
        if ef["status"] != "PASS":
            failures.append("NUMERICAL_EVALUATOR_NOT_QUALIFIED")
    else:
        ef = {"status": "NOT_RUN", "reason": "K2R4_PRECONDITION_FAIL"}
        lod = {"status": "NOT_RUN", "reason": "K2R4_PRECONDITION_FAIL", "diagnostic_only": True}
        write_json(run / "evaluator_fidelity_cost.json", ef)
        write_json(run / "lower_order_diagnostics.json", lod)

    if not failures:
        print("[K2R4 5/5] original calibration-only causal response feasibility", flush=True)
        cr = causal_response_gate(root, k1, base, instruments)
        write_json(run / "causal_response_feasibility.json", cr)
        gates["causal_response"] = cr["status"]
        if cr["status"] != "PASS":
            failures.append("CALIBRATION_RESPONSE_SEPARATION_NOT_CLEAN")
    else:
        cr = {"status": "NOT_RUN", "reason": "K2R4_PRECONDITION_FAIL"}
        write_json(run / "causal_response_feasibility.json", cr)

    overall = "PASS" if not failures else "FAIL"
    if overall == "PASS":
        next_action = "P13-S0-K3_SCIENTIFIC_ADJUDICATION_AND_FREEZE"
    elif any(x in failures for x in ["OPERATIONAL_COMMON_BURNIN_F4_ATTAINMENT_FAIL", "OPERATIONAL_COMMON_BURNIN_NUMERICAL_UNRESOLVED"]):
        next_action = "P13-S0-K2R4_OPERATIONAL_ATTAINMENT_SCIENTIFIC_DECISION_REQUIRED"
    elif "OPERATIONAL_REFERENCE_NOT_QUALIFIED" in failures:
        next_action = "P13-S0-K2R4_OPERATIONAL_REFERENCE_SCIENTIFIC_DECISION_REQUIRED"
    elif any(x in failures for x in ["PRODUCTION_FITTER_F4_ATTAINMENT_NOT_QUALIFIED", "PRODUCTION_FITTER_REGRET_NOT_QUALIFIED"]):
        next_action = "P13-S0-K2R4_PRODUCTION_FITTER_SCIENTIFIC_DECISION_REQUIRED"
    elif any(x in failures for x in ["PROPOSAL_GEOMETRY_NOT_QUALIFIED", "V2_PROPOSAL_NUMERICAL_UNRESOLVED"]):
        next_action = "P13-S0-K2R4_PROPOSAL_GEOMETRY_SCIENTIFIC_DECISION_REQUIRED"
    elif "NUMERICAL_EVALUATOR_NOT_QUALIFIED" in failures:
        next_action = "P13-S0-K2R4_NUMERICAL_FIDELITY_SCIENTIFIC_DECISION_REQUIRED"
    elif "CALIBRATION_RESPONSE_SEPARATION_NOT_CLEAN" in failures:
        next_action = "P13-S0-K2R4_CAUSAL_CALIBRATION_SCIENTIFIC_DECISION_REQUIRED"
    else:
        next_action = "P13-S0-K3_FAIL_CLOSED_ADJUDICATION"

    leakage = {
        "status": "PASS",
        "formal_candidate_search_run": False,
        "burnin_is_calibration_only": True,
        "burnin_candidates_eligible_for_S1": False,
        "historical_response_outcomes_read": False,
        "opened_transfer_diagnostic_read": False,
        "development_or_sealed_opened": False,
        "capacity_instrument_used_as_search_information": False,
        "characteristic_formula_used_as_search_information": False,
        "objective_top_k_percentile_membership": False,
        "fitter_membership_uses_only_F4_hard_gate_and_chronology": True,
        "candidate_specific_reference_rescue": False,
        "response_aware_refit": False,
        "post_freeze_diagnostic_json_used_as_runtime_input": False,
    }
    write_json(run / "no_leakage_guard.json", leakage)
    summary = {
        "OVERALL_STATUS": overall,
        "NEXT_ACTION": next_action,
        "failure_classifications": failures,
        "gate_statuses": gates,
        "parent_K2R3_run": str(k2r3.relative_to(root)),
        "parent_K2R3_semantic": s0.semantic(root, "K2R3", EXPECTED_K2R3_SEMANTIC),
        "parent_K2R2_run": str(k2r2.relative_to(root)),
        "parent_K2_run": str(k2.relative_to(root)),
        "parent_K2_semantic": s0.semantic(root, "K2", EXPECTED_K2_SEMANTIC),
        "parent_K1_run": str(k1.relative_to(root)),
        "workers": workers,
        "capacity_reused_without_rerun": True,
        "J_capacity_hard_decision_status": "RESOLVED",
        "R_att_hard_decision_status": "RESOLVED",
        "operational_burnin_status": burn["status"],
        "operational_fitter_status": fit["status"],
        "formal_S1_search_authorized": False,
        "K3_required_before_S1": True,
        "authoritative_upstream_data_reused_in_place": True,
        "audit_archives_used_as_runtime_input": False,
        "elapsed_seconds": time.perf_counter() - start,
    }
    digest_payload = {
        "failure": failures,
        "gates": gates,
        "mechanism": {"production_F4_count": mechanism["production_F4_count"], "levels": mechanism["levels"]},
        "burnin": {k: burn.get(k) for k in ["F4_unique_skeletons", "F4_unique_rate", "frozen_V2_parent_count", "operational_fitter_F4_cohort_count"]},
        "fitter": {k: fit.get(k) for k in ["selected_reference_level", "reference_qualified", "R_fit_resolved", "median_R_fit", "p90_R_fit"]},
        "proposal": {k: prop.get(k) for k in ["F4_children", "improve_5pct_count", "improve_20pct_count"]},
        "evaluator": ef.get("maximum_relative_J_difference"),
        "causal": cr.get("gates"),
    }
    semantic = sha256_bytes(canonical_json_bytes({"parent_K2R3_semantic": s0.semantic(root, "K2R3", EXPECTED_K2R3_SEMANTIC), "protocol_sha256": r4_sha, "result": digest_payload}))
    summary["semantic_output_digest"] = semantic
    write_json(run / "audit_summary.json", summary)
    write_json(run / "semantic_output_digest.json", {"stage": "P13-S0-K2R4", "semantic_output_digest": semantic, "protocol_sha256": r4_sha, "parent_K2R3_semantic": s0.semantic(root, "K2R3", EXPECTED_K2R3_SEMANTIC)})
    write_text(run / "OVERALL_STATUS.txt", overall + "\n")
    write_text(run / "NEXT_ACTION.txt", next_action + "\n")
    write_json(run / "authoritative_evidence_manifest.json", _evidence_manifest(root, run))
    source_paths = [
        "phases/p13/coefficient_law_raw_xt/configs/p13_s0_k2r4_protocol.json",
        "phases/p13/coefficient_law_raw_xt/configs/p13_s0_k2r3_protocol.json",
        "phases/p13/coefficient_law_raw_xt/configs/p13_s0_k2r2_protocol.json",
        "phases/p13/coefficient_law_raw_xt/src/p13rawxt/k2r4_operational_fitter.py",
        "phases/p13/coefficient_law_raw_xt/src/p13rawxt/k2r3_capacity_continuation.py",
        "phases/p13/coefficient_law_raw_xt/src/p13rawxt/k2r2_attainment_repair.py",
        "phases/p13/coefficient_law_raw_xt/src/p13rawxt/reference_optimizer_v2.py",
        "phases/p13/coefficient_law_raw_xt/src/p13rawxt/proposal_geometry.py",
        "phases/p13/coefficient_law_raw_xt/src/p13rawxt/calibration_instruments.py",
        "phases/p11/raw_xt_td/src/p11rawxt_s1/k2_search.py",
        "phases/p11/raw_xt_td/src/p11rawxt_s1/k2_fitter.py",
    ]
    write_json(run / "source_manifest.json", _source_manifest(root, source_paths))
    write_json(run / "runtime_environment.json", {"python": sys.version, "numpy": np.__version__, "platform": platform.platform(), "workers": workers, "NSLOTS": os.environ.get("NSLOTS"), "OMP_NUM_THREADS": os.environ.get("OMP_NUM_THREADS"), "OPENBLAS_NUM_THREADS": os.environ.get("OPENBLAS_NUM_THREADS")})
    write_json(run / "k3_handoff_manifest.json", {
        "schema": "P13_S0_K2R4_K3_HANDOFF_V1",
        "K1_run": str(k1.relative_to(root)),
        "K2_run": str(k2.relative_to(root)),
        "K2R2_run": str(k2r2.relative_to(root)),
        "K2R3_run": str(k2r3.relative_to(root)),
        "K2R4_run": str(run.relative_to(root)),
        "K2R4_semantic_output_digest": semantic,
        "formal_S1_search_authorized": False,
        "K3_required": True,
        "immediate_next_action": next_action,
        "burnin_candidates_eligible_for_S1": False,
        "operational_fitter_claim_boundary": r4["operational_fitter"]["claim_boundary"],
        "J_capacity_hard_decision_status": "RESOLVED",
    })
    _update_context(root, summary, semantic, str(run.relative_to(root)))
    print(f"OVERALL_STATUS={overall}", flush=True)
    print(f"semantic_output_digest={semantic}", flush=True)
    print(f"NEXT_ACTION={next_action}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
