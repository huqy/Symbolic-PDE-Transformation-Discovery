#!/usr/bin/env python3
from __future__ import annotations

import json
import multiprocessing as mp
import os
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np

from p11rawxt_ast import parameter_names
from p11rawxt_common import canonical_json_bytes, sha256_bytes, write_json
from p11rawxt_s1.k2_ast_runtime import crossover_pair, mutate_pair, random_pair
from p11rawxt_s1.k2_evaluator import evaluate_concrete_call
from p11rawxt_s1.k2_fitter import fit_skeleton, nondominated_indices, parameter_names_for_pair, record_dominates
from p11rawxt_validity import build_grid

_WORKER_CONTEXT: dict[str, Any] = {}


def _worker_init(context: dict[str, Any]) -> None:
    global _WORKER_CONTEXT
    _WORKER_CONTEXT = context
    os.environ["OMP_NUM_THREADS"] = "1"
    os.environ["MKL_NUM_THREADS"] = "1"
    os.environ["OPENBLAS_NUM_THREADS"] = "1"
    os.environ["NUMEXPR_NUM_THREADS"] = "1"


def _worker_fit(task: dict[str, Any]) -> dict[str, Any]:
    context = _WORKER_CONTEXT
    grid = build_grid(int(context["grid_points_x"]), int(context["grid_points_t"]))

    def evaluate(pair: dict[str, Any], theta: list[float]) -> dict[str, Any]:
        return evaluate_concrete_call(
            pair,
            theta,
            grid,
            context["validity_numerical"],
            float(context["inverse_roundtrip_tolerance"]),
            context["search_space"],
            context["operator_numerical"],
        )

    fitted = fit_skeleton(
        task["pair"],
        evaluate,
        context["fitter_protocol"],
        call_budget_override=int(task["planned_call_budget"]),
    )
    return {**task, "fit": fitted, "worker_pid": os.getpid()}


def scheduler_allocated_cpus(default: int = 1) -> int:
    for name in ["NSLOTS", "SLURM_CPUS_PER_TASK", "PBS_NP", "LSB_DJOB_NUMPROC"]:
        value = os.environ.get(name)
        if value:
            try:
                return max(1, int(value))
            except ValueError:
                pass
    return max(1, default)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _parent_key(record: dict[str, Any]) -> tuple[Any, ...]:
    # Complexity/hash are used only after an exact equality of all direct facts.
    return (int(record["pair"]["pair_stats"]["total_nodes"]), record["pair"]["structural_hash"])


def _direct_record_key(record: dict[str, Any]) -> tuple[Any, ...]:
    stage = int(record["stage_index"])
    if stage == 5 and record.get("J_princ") is not None:
        return (stage, float(record["J_princ"]), float(record["J_XT"]), float(record["J_XX"]))
    return (stage, *tuple(map(float, record.get("direct_margin_vector", []))))


def _select_parent(pool: list[dict[str, Any]], rng: np.random.Generator) -> dict[str, Any]:
    if len(pool) == 1:
        return pool[0]
    a, b = rng.choice(len(pool), size=2, replace=False)
    left, right = pool[int(a)], pool[int(b)]
    lr = record_dominates(left["representative"], right["representative"])
    rl = record_dominates(right["representative"], left["representative"])
    if lr and not rl:
        return left
    if rl and not lr:
        return right
    if _direct_record_key(left["representative"]) == _direct_record_key(right["representative"]):
        return min([left, right], key=_parent_key)
    # Incomparable direct outcomes have equal proposal rights; hash cannot act as a hidden quality score.
    return left if int(rng.integers(0, 2)) == 0 else right


def _branch_representative(fit: dict[str, Any]) -> dict[str, Any] | None:
    reps = fit.get("breeding_representatives", [])
    if not reps:
        return None
    best_stage = max(int(row["stage_index"]) for row in reps)
    stage_rows = [row for row in reps if int(row["stage_index"]) == best_stage]
    if best_stage == 5:
        return min(stage_rows, key=lambda row: (float(row["J_princ"]), float(row["J_XT"]), float(row["J_XX"]), row["candidate_branch_hash"]))
    return min(stage_rows, key=lambda row: row["candidate_branch_hash"])


def _nondominance_view(pool: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not pool:
        return []
    records = [item["representative"] for item in pool]
    return [pool[index] for index in nondominated_indices(records)]


def _generate_task(
    rng: np.random.Generator,
    task_index: int,
    arm: str,
    islands: list[list[dict[str, Any]]],
    caps: dict[str, int],
    gp_protocol: dict[str, Any],
    duplicate_hashes: set[str],
    gp_completed_by_island: list[int],
) -> tuple[dict[str, Any], int]:
    duplicate_hits = 0
    generator_rejections = 0
    for proposal_attempt in range(1, 2049):
        island = task_index % len(islands)
        operation = "de_novo"
        parents: list[str] = []
        lineage_meta: dict[str, Any] = {"component": "", "subtree_positions": [], "replacement_hash": ""}
        initialization_target = int(gp_protocol["initial_complete_skeleton_target_per_island"])
        gp_ready = gp_completed_by_island[island] >= initialization_target
        try:
            if arm == "gp" and gp_ready and islands[island]:
                mix = gp_protocol["offspring_mix"]
                draw = float(rng.random())
                if draw < float(mix["de_novo"]):
                    pair = random_pair(rng, caps)
                elif draw < float(mix["de_novo"]) + float(mix["subtree_mutation"]):
                    operation = "subtree_mutation"
                    parent = _select_parent(islands[island], rng)
                    pair, lineage_meta = mutate_pair(parent["pair"], rng, caps)
                    parents = [parent["representative"]["candidate_branch_hash"]]
                else:
                    operation = "subtree_crossover"
                    parent_a = _select_parent(islands[island], rng)
                    parent_b = _select_parent(islands[island], rng)
                    pair, lineage_meta = crossover_pair(parent_a["pair"], parent_b["pair"], rng, caps)
                    parents = [parent_a["representative"]["candidate_branch_hash"], parent_b["representative"]["candidate_branch_hash"]]
            else:
                pair = random_pair(rng, caps)
        except RuntimeError:
            # A generator that exhausts its bounded legal-offspring attempts is
            # an ordinary proposal rejection.  It must not terminate a long
            # scientific run or alter the concrete-call budget.
            generator_rejections += 1
            continue
        structural_hash = pair["structural_hash"]
        if structural_hash in duplicate_hashes:
            duplicate_hits += 1
            continue
        duplicate_hashes.add(structural_hash)
        lineage_payload = {
            "operation": operation,
            "parent_branch_hashes": parents,
            "component": lineage_meta.get("component", ""),
            "subtree_positions": lineage_meta.get("subtree_positions", []),
            "replacement_or_exchange_hashes": [lineage_meta.get("replacement_hash", "")] if lineage_meta.get("replacement_hash") else [],
            "offspring_structural_hash": structural_hash,
            "seed": int(rng.bit_generator.state["state"]["state"] if isinstance(rng.bit_generator.state.get("state"), dict) else 0),
            "island": island,
            "batch_index": -1,
            "task_index": task_index,
        }
        lineage_id = sha256_bytes(canonical_json_bytes(lineage_payload))
        lineage_payload["lineage_id"] = lineage_id
        return {
            "task_index": task_index,
            "arm": arm,
            "island": island,
            "pair": pair,
            "lineage": lineage_payload,
            "proposal_attempts": proposal_attempt,
            "generator_rejections": generator_rejections,
        }, duplicate_hits
    raise RuntimeError("Generator exhausted proposal attempts")


def _uniform_hash_spaced(pool: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    if count <= 0 or not pool:
        return []
    ordered = sorted(pool, key=lambda item: item["pair"]["structural_hash"])
    if len(ordered) <= count:
        return ordered
    indices = np.linspace(0, len(ordered) - 1, count, dtype=int)
    return [ordered[int(index)] for index in indices]


def _migrate_ring(
    islands: list[list[dict[str, Any]]],
    records_per_island: int,
) -> list[dict[str, Any]]:
    snapshots = [_uniform_hash_spaced(pool, records_per_island) for pool in islands]
    events: list[dict[str, Any]] = []
    for source, migrants in enumerate(snapshots):
        destination = (source + 1) % len(islands)
        known = {item["pair"]["structural_hash"] for item in islands[destination]}
        for migrant in migrants:
            structural_hash = migrant["pair"]["structural_hash"]
            if structural_hash in known:
                continue
            islands[destination].append(migrant)
            known.add(structural_hash)
            events.append({
                "operation": "island_migration",
                "source_island": source,
                "destination_island": destination,
                "offspring_structural_hash": structural_hash,
                "parent_branch_hashes": [migrant["representative"]["candidate_branch_hash"]],
                "migration_changes_export_membership": False,
            })
        islands[destination] = _nondominance_view(islands[destination])
    return events


def run_calibration(
    run_dir: Path,
    *,
    total_calls: int,
    requested_workers: int,
    worker_context: dict[str, Any],
    caps: dict[str, int],
    gp_protocol: dict[str, Any],
    checkpoint_protocol: dict[str, Any],
    seed: int,
    resume: bool = False,
) -> dict[str, Any]:
    run_dir.mkdir(parents=True, exist_ok=True)
    checkpoint = run_dir / "checkpoint_state.json"
    call_ledger = run_dir / "calibration_call_ledger.jsonl"
    proposal_ledger = run_dir / "calibration_proposal_ledger.jsonl"
    lineage_ledger = run_dir / "calibration_lineage_ledger.jsonl"
    invalid_ledger = run_dir / "calibration_invalid_ledger.jsonl"
    broad_archive_file = run_dir / "calibration_broad_F4_archive.jsonl"

    allocated = scheduler_allocated_cpus(requested_workers + 1)
    workers = min(int(checkpoint_protocol["worker_cap"]), requested_workers, max(1, allocated - 1))
    rng = np.random.default_rng(seed)
    islands: list[list[dict[str, Any]]] = [[] for _ in range(int(gp_protocol["island_count"]))]
    duplicate_hashes: set[str] = set()
    broad_hashes: set[str] = set()
    gp_completed_by_island = [0 for _ in islands]
    counters = {
        "completed_calls": 0, "attempted_proposals": 0, "F4_valid_count": 0, "invalid_count": 0,
        "duplicate_cache_hits": 0, "completed_skeletons": 0, "random_skeletons": 0, "gp_skeletons": 0,
        "all_F4_branch_emissions": 0, "all_F4_branch_duplicate_hits": 0,
        "completed_batches": 0, "migration_events": 0, "migration_records": 0,
        "generator_rejections": 0,
    }
    next_task_index = 0
    start = time.monotonic()
    last_progress_time = start
    last_progress_calls = 0
    last_checkpoint_time = start
    last_checkpoint_calls = 0

    if resume and checkpoint.is_file():
        state = json.loads(checkpoint.read_text(encoding="utf-8"))
        if state["source_config_active_input_digest"] != worker_context["source_config_active_input_digest"]:
            raise RuntimeError("Checkpoint source/config/active-input digest mismatch")
        rng.bit_generator.state = state["rng_state"]
        islands = state["islands"]
        gp_completed_by_island = list(map(int, state.get("gp_completed_by_island", [0 for _ in islands])))
        duplicate_hashes = set(state["duplicate_hashes"])
        broad_hashes = set(state["broad_hashes"])
        counters = state["counters"]
        counters.setdefault("generator_rejections", 0)
        next_task_index = int(state["next_task_index"])

    mode = "a" if counters["completed_calls"] else "w"
    files = [call_ledger, proposal_ledger, lineage_ledger, invalid_ledger, broad_archive_file]
    handles = [path.open(mode, encoding="utf-8") for path in files]
    call_handle, proposal_handle, lineage_handle, invalid_handle, broad_handle = handles

    def save_checkpoint() -> None:
        payload = {
            "program": "P11-RawXT", "stage": "P11-RawXT-S1-K2", "calibration_only": True,
            "source_config_active_input_digest": worker_context["source_config_active_input_digest"],
            "rng_state": rng.bit_generator.state, "islands": islands,
            "gp_completed_by_island": gp_completed_by_island,
            "duplicate_hashes": sorted(duplicate_hashes), "broad_hashes": sorted(broad_hashes),
            "counters": counters, "next_task_index": next_task_index,
            "pending_batch_semantics": "none; checkpoints are committed only after deterministic batch commit",
        }
        _atomic_write_json(checkpoint, payload)

    try:
        context = mp.get_context("spawn")
        with ProcessPoolExecutor(max_workers=workers, mp_context=context, initializer=_worker_init, initargs=(worker_context,)) as executor:
            while counters["completed_calls"] < total_calls:
                remaining = total_calls - counters["completed_calls"]
                batch: list[dict[str, Any]] = []
                batch_budget = 0
                batch_size = int(checkpoint_protocol["skeleton_batch_size"])
                while len(batch) < batch_size and batch_budget < remaining:
                    arm = "random" if next_task_index % 2 == 0 else "gp"
                    task, duplicate_hits = _generate_task(
                        rng, next_task_index, arm, islands, caps, gp_protocol, duplicate_hashes, gp_completed_by_island
                    )
                    counters["duplicate_cache_hits"] += duplicate_hits
                    counters["generator_rejections"] += int(task.get("generator_rejections", 0))
                    p = len(parameter_names_for_pair(task["pair"]))
                    max_calls = int(worker_context["fitter_protocol"]["maximum_calls_by_parameter_count"][str(p)])
                    planned = min(max_calls, remaining - batch_budget)
                    if planned <= 0:
                        break
                    task["planned_call_budget"] = planned
                    task["lineage"]["batch_index"] = counters["completed_skeletons"] // max(batch_size, 1)
                    batch.append(task)
                    batch_budget += planned
                    next_task_index += 1
                if not batch:
                    break
                results = list(executor.map(_worker_fit, batch))
                for result in sorted(results, key=lambda row: int(row["task_index"])):
                    counters["attempted_proposals"] += int(result["proposal_attempts"])
                    counters["completed_skeletons"] += 1
                    counters[f"{result['arm']}_skeletons"] += 1
                    if result["arm"] == "gp":
                        gp_completed_by_island[int(result["island"])] += 1
                    proposal_handle.write(json.dumps({
                        "task_index": result["task_index"], "arm": result["arm"], "island": result["island"],
                        "structural_hash": result["pair"]["structural_hash"], "proposal_attempts": result["proposal_attempts"],
                        "planned_call_budget": result["planned_call_budget"], "formal_candidate": False,
                        "generator_rejections_before_acceptance": int(result.get("generator_rejections", 0)),
                    }, sort_keys=True) + "\n")
                    lineage_handle.write(json.dumps({**result["lineage"], "formal_candidate": False}, sort_keys=True) + "\n")
                    fit = result["fit"]
                    for local_index, record in enumerate(fit["call_records"]):
                        global_index = counters["completed_calls"]
                        scientific_record = {key: value for key, value in record.items() if key not in {"elapsed_seconds", "operator_seconds"}}
                        record_out = {
                            "concrete_call_index": global_index, "task_index": result["task_index"], "local_call_index": local_index,
                            "structural_hash": result["pair"]["structural_hash"], "arm": result["arm"], "island": result["island"],
                            **scientific_record, "formal_candidate": False, "calibration_only": True,
                        }
                        call_handle.write(json.dumps(record_out, sort_keys=True) + "\n")
                        counters["completed_calls"] += 1
                        if int(record["stage_index"]) == 5 and record.get("J_princ") is not None:
                            counters["F4_valid_count"] += 1
                        else:
                            counters["invalid_count"] += 1
                            invalid_handle.write(json.dumps({
                                "concrete_call_index": global_index, "structural_hash": result["pair"]["structural_hash"],
                                "theta_vector": record["theta_vector"], "highest_feasibility_level": record["highest_feasibility_level"],
                                "rejection_codes": record.get("F0_F4_records", {}).get("rejection_codes", []),
                                "formal_candidate": False, "calibration_only": True,
                            }, sort_keys=True) + "\n")
                    for branch in fit["all_F4_branches"]:
                        counters["all_F4_branch_emissions"] += 1
                        branch_hash = branch["candidate_branch_hash"]
                        if branch_hash in broad_hashes:
                            counters["all_F4_branch_duplicate_hits"] += 1
                            continue
                        broad_hashes.add(branch_hash)
                        broad_handle.write(json.dumps({
                            "candidate_branch_hash": branch_hash, "structural_hash": result["pair"]["structural_hash"],
                            "raw_X_AST": result["pair"]["raw_X_AST"], "raw_T_AST": result["pair"]["raw_T_AST"],
                            "theta_vector": branch["theta_vector"], "J_princ": branch["J_princ"], "J_XT": branch["J_XT"], "J_XX": branch["J_XX"],
                            "arm": result["arm"], "island": result["island"], "lineage_id": result["lineage"]["lineage_id"],
                            "formal_candidate": False, "calibration_only": True, "eligible_for_K3_parent_pool": False,
                        }, sort_keys=True) + "\n")
                    representative = _branch_representative(fit)
                    if representative is not None:
                        representative = {key: value for key, value in representative.items() if key not in {"elapsed_seconds", "operator_seconds"}}
                    if representative is not None and result["arm"] == "gp":
                        island = int(result["island"])
                        islands[island].append({"pair": result["pair"], "representative": representative})
                        islands[island] = _nondominance_view(islands[island])
                    if counters["completed_calls"] >= total_calls:
                        break
                counters["completed_batches"] += 1
                migration = gp_protocol.get("migration", {})
                interval = int(migration.get("interval_completed_batches", 0))
                if interval > 0 and counters["completed_batches"] % interval == 0:
                    events = _migrate_ring(islands, int(migration.get("records_per_island", 0)))
                    if events:
                        counters["migration_events"] += 1
                        counters["migration_records"] += len(events)
                        for event in events:
                            lineage_payload = {
                                "operation": "island_migration",
                                "parent_branch_hashes": event["parent_branch_hashes"],
                                "component": "",
                                "subtree_positions": [],
                                "replacement_or_exchange_hashes": [event["offspring_structural_hash"]],
                                "offspring_structural_hash": event["offspring_structural_hash"],
                                "seed": seed,
                                "island": event["destination_island"],
                                "batch_index": counters["completed_batches"],
                                "task_index": -1,
                                "source_island": event["source_island"],
                                "destination_island": event["destination_island"],
                                "migration_changes_export_membership": False,
                            }
                            lineage_payload["lineage_id"] = sha256_bytes(canonical_json_bytes(lineage_payload))
                            lineage_handle.write(json.dumps({
                                **lineage_payload,
                                "formal_candidate": False,
                                "calibration_only": True,
                            }, sort_keys=True) + "\n")
                for handle in handles:
                    handle.flush()
                    os.fsync(handle.fileno())

                now = time.monotonic()
                progress_due = (
                    counters["completed_calls"] - last_progress_calls >= int(checkpoint_protocol["progress_every_completed_calls"])
                    or now - last_progress_time >= int(checkpoint_protocol["progress_every_seconds"])
                    or counters["completed_calls"] >= total_calls
                )
                if progress_due:
                    elapsed = now - start
                    print(
                        "[P11-RawXT-S1-K2] "
                        f"completed_calls={counters['completed_calls']}/{total_calls} "
                        f"attempted_proposals={counters['attempted_proposals']} F4_valid_count={counters['F4_valid_count']} "
                        f"invalid_count={counters['invalid_count']} duplicate_cache_hits={counters['duplicate_cache_hits']} "
                        f"elapsed_seconds={elapsed:.1f} workers={workers} checkpoint_path={checkpoint}",
                        flush=True,
                    )
                    last_progress_calls = counters["completed_calls"]
                    last_progress_time = now
                checkpoint_due = (
                    counters["completed_calls"] - last_checkpoint_calls >= int(checkpoint_protocol["checkpoint_every_completed_calls"])
                    or now - last_checkpoint_time >= int(checkpoint_protocol["checkpoint_every_seconds"])
                    or counters["completed_calls"] >= total_calls
                )
                if checkpoint_due:
                    save_checkpoint()
                    last_checkpoint_calls = counters["completed_calls"]
                    last_checkpoint_time = now
    finally:
        for handle in handles:
            handle.close()

    elapsed = time.monotonic() - start
    save_checkpoint()
    return {
        "calibration_only": True,
        "formal_K3_search_run": False,
        "workers": workers,
        "scheduler_allocated_cpus": allocated,
        "requested_workers": requested_workers,
        "elapsed_seconds": elapsed,
        "calls_per_second": counters["completed_calls"] / max(elapsed, 1e-12),
        "projected_seconds_1M": 1_000_000.0 * elapsed / max(counters["completed_calls"], 1),
        "counters": counters,
        "broad_archive_unique_count": len(broad_hashes),
        "island_breeding_view_sizes": [len(pool) for pool in islands],
        "gp_completed_skeletons_by_island": gp_completed_by_island,
        "gp_initialization_target_per_island": int(gp_protocol["initial_complete_skeleton_target_per_island"]),
        "checkpoint_path": str(checkpoint),
        "call_ledger_path": str(call_ledger),
        "proposal_ledger_path": str(proposal_ledger),
        "lineage_ledger_path": str(lineage_ledger),
        "invalid_ledger_path": str(invalid_ledger),
        "broad_archive_path": str(broad_archive_file),
        "calibration_results_eligible_for_K3_parent_pool": False,
    }
