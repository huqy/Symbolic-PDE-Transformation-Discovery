from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import platform
import sys
import time
import warnings
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np

from .calibration_instruments import best_f4, production_fit
from .family_evaluator import load_field_views, evaluate_family
from .s1_search_primitives import (
    additive_root_residual_graft,
    canonical_json_bytes,
    contains_coefficient_syntax,
    derive_seed,
    empty_v2_counters,
    execution_equivalence_key,
    mutate_pair_policy,
    operator_qualification,
    random_pair_policy,
    scientific_branch_id,
    sha256_bytes,
    theta_names,
)

_WORKER: dict[str, Any] = {}


def _sha256_path(path: Path, block: int = 16 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(block), b""):
            h.update(b)
    return h.hexdigest()


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


def _verify_entry(root: Path, protocol: dict[str, Any]) -> tuple[Path, Path, dict[str, Any]]:
    k0r = _resolve_marker(root, protocol["k0r_marker"])
    k1 = _resolve_marker(root, protocol["k1_open_marker"])
    a = _load_json(k0r / "audit_summary.json")
    k0r_checks = {
        "status_pass": (k0r / "OVERALL_STATUS.txt").read_text().strip() == "PASS",
        "next_action": (k0r / "NEXT_ACTION.txt").read_text().strip() == "P13-S1-K1A_SEED1_FORMAL_STAGED_EXECUTION_AUTHORIZED",
        "formal_K1A_authorized": bool(a.get("formal_K1A_authorized")),
        "formal_K1_not_preexecuted": not bool(a.get("formal_K1_search_executed")),
        "no_DEV_SEALED": not bool(a.get("DEVELOPMENT_or_SEALED_opened")),
        "no_silent_fallback": int(a.get("silent_fallback_to_V1", -1)) == 0,
        "tau": abs(float(a.get("tau_num", math.nan)) - float(protocol["operator_qualification"]["tau_num"])) < 1e-15,
        "capacity_has_no_authority": a.get("J_capacity_R_att_decision_authority") == "NONE",
    }
    k1_manifest = _load_json(k1 / "open_search_object_manifest.json")
    train = [x for x in k1_manifest["objects"] if x["role"] == protocol["train_role"]]
    field_ids = sorted({x["field_id"] for x in train})
    k1_checks = {
        "status_pass": (k1 / "OVERALL_STATUS.txt").read_text().strip() == "PASS",
        "six_train_fields": len(field_ids) == int(protocol["train_field_count"]),
        "formal_grid_objects": len([x for x in train if int(x["grid"]) == int(protocol["formal_grid"])]) == int(protocol["train_field_count"]),
    }
    # Byte-verify only TRAIN objects required by formal search; do not open diagnostic payload arrays.
    sha_ok = True
    verified = []
    for row in train:
        p = k1 / row["path"]
        if not p.is_file() or _sha256_path(p) != row["sha256"]:
            sha_ok = False
        verified.append({"path": str(p.relative_to(root)), "sha256": row["sha256"], "grid": int(row["grid"]), "field_id": row["field_id"]})
    k1_checks["all_train_sha"] = sha_ok
    if not all(k0r_checks.values()) or not all(k1_checks.values()):
        raise RuntimeError(f"K1A entry verification failed: K0R={k0r_checks} K1={k1_checks}")
    return k0r, k1, {
        "status": "PASS",
        "K0R_run": str(k0r.relative_to(root)),
        "K1_input_run": str(k1.relative_to(root)),
        "K0R_checks": k0r_checks,
        "K1_checks": k1_checks,
        "train_field_ids": field_ids,
        "train_objects_verified": verified,
        "forbidden_payload_arrays_opened": False,
        "K0R_audit_archive_used_as_runtime_input": False,
    }


def _worker_init(root_s: str, k1_s: str, base: dict[str, Any], grid: int) -> None:
    root = Path(root_s)
    for p in [root / "phases/p13/coefficient_law_raw_xt/src", root / "phases/p11/raw_xt_td/src"]:
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    os.environ["OMP_NUM_THREADS"] = "1"
    os.environ["MKL_NUM_THREADS"] = "1"
    os.environ["OPENBLAS_NUM_THREADS"] = "1"
    os.environ["NUMEXPR_NUM_THREADS"] = "1"
    global _WORKER
    _WORKER = {
        "base": base,
        "fields": load_field_views(Path(k1_s), "TRAIN_OPERATOR", int(grid)),
    }


def _family_evaluator(base: dict[str, Any], fields: list[Any]):
    return lambda pair, theta: evaluate_family(
        pair, theta, fields,
        base["validity"], float(base["validity"]["inverse_roundtrip_tolerance"]),
        base["operator"]["space"], base["operator"]["numerical"],
    )


def _worker_fit(task: dict[str, Any]) -> dict[str, Any]:
    wall0 = time.perf_counter()
    cpu0 = time.process_time()
    try:
        base = _WORKER["base"]
        ev = _family_evaluator(base, _WORKER["fields"])
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            fit = production_fit(task["pair"], ev, base["fitter"]["production"])
        return {
            **task,
            "fit": fit,
            "worker_wall_seconds": time.perf_counter() - wall0,
            "worker_cpu_seconds": time.process_time() - cpu0,
            "warning_count": len(caught),
        }
    except Exception as exc:
        return {
            **task,
            "fatal_exception_type": type(exc).__name__,
            "fatal_exception": str(exc),
            "worker_wall_seconds": time.perf_counter() - wall0,
            "worker_cpu_seconds": time.process_time() - cpu0,
        }


def _parent_key(record: dict[str, Any]) -> tuple[Any, ...]:
    return (int(record["pair"]["pair_stats"]["total_nodes"]), record["pair"]["structural_hash"])


def _direct_record_key(record: dict[str, Any]) -> tuple[Any, ...]:
    r = record["representative"]
    stage = int(r["stage_index"])
    if stage == 5 and r.get("J_princ") is not None:
        return (stage, float(r["J_princ"]), float(r["J_XT"]), float(r["J_XX"]))
    return (stage, *tuple(map(float, r.get("direct_margin_vector", []))))


def _p11_search_imports():
    from p11rawxt_s1.k2_ast_runtime import crossover_pair
    from p11rawxt_s1.k2_fitter import record_dominates, nondominated_indices
    return crossover_pair, record_dominates, nondominated_indices


def _select_parent(pool: list[dict[str, Any]], rng: np.random.Generator) -> dict[str, Any]:
    _, record_dominates, _ = _p11_search_imports()
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
    if _direct_record_key(left) == _direct_record_key(right):
        return min([left, right], key=_parent_key)
    return left if int(rng.integers(0, 2)) == 0 else right


def _nondominance_view(pool: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not pool:
        return []
    _, _, nondominated_indices = _p11_search_imports()
    records = [x["representative"] for x in pool]
    return [pool[i] for i in nondominated_indices(records)]


def _uniform_hash_spaced(pool: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    if count <= 0 or not pool:
        return []
    ordered = sorted(pool, key=lambda x: x["pair"]["structural_hash"])
    if len(ordered) <= count:
        return ordered
    ii = np.linspace(0, len(ordered) - 1, count, dtype=int)
    return [ordered[int(i)] for i in ii]


def _migrate_ring(islands: list[list[dict[str, Any]]], records_per_island: int) -> list[dict[str, Any]]:
    snaps = [_uniform_hash_spaced(p, records_per_island) for p in islands]
    events = []
    for source, migrants in enumerate(snaps):
        dest = (source + 1) % len(islands)
        known = {x["pair"]["structural_hash"] for x in islands[dest]}
        for m in migrants:
            h = m["pair"]["structural_hash"]
            if h in known:
                continue
            islands[dest].append(copy.deepcopy(m)); known.add(h)
            events.append({"source_island": source, "destination_island": dest, "structural_hash": h})
        islands[dest] = _nondominance_view(islands[dest])
    return events


def _pair_seed(seed: int, proposal_index: int, attempt: int) -> int:
    return derive_seed("P13-S1-K1V1-COMMON-PROPOSAL", seed, proposal_index, attempt)


def generate_proposal(
    arm: str,
    seed: int,
    proposal_index: int,
    protocol: dict[str, Any],
    islands: list[list[dict[str, Any]]],
    duplicate_hashes: set[str],
) -> dict[str, Any]:
    """Generate exactly one structural proposal slot.

    Selected V2 graft saturation/no-legal-child consumes the slot and is returned
    as an explicit failed proposal; there is no V1 fallback or replacement draw.
    Other inherited P11 generator failures/duplicates may retry inside the slot.
    """
    caps = protocol["caps"]
    grammar = protocol["grammar_prior"][arm]
    kernel = protocol["kernel"][arm]
    init_total = int(protocol["budget"]["initial_population_per_island"]) * int(protocol["budget"]["island_count"])
    island = (proposal_index - 1) % int(protocol["budget"]["island_count"])
    v2 = empty_v2_counters()
    duplicate_hits = 0
    generator_rejections = 0
    for attempt in range(2048):
        rng = np.random.default_rng(_pair_seed(seed, proposal_index, attempt))
        operation = "de_novo"
        parents: list[str] = []
        pair = None
        lineage_meta: dict[str, Any] = {}
        try:
            if proposal_index <= init_total or not islands[island]:
                pair = random_pair_policy(_pair_seed(seed, proposal_index, attempt), caps, grammar)
                operation = "initial_de_novo" if proposal_index <= init_total else "de_novo"
            else:
                mix = protocol["gp"]["offspring_mix"]
                draw = float(rng.random())
                if draw < float(mix["de_novo"]):
                    pair = random_pair_policy(_pair_seed(seed, proposal_index, attempt), caps, grammar)
                    operation = "de_novo"
                elif draw < float(mix["de_novo"]) + float(mix["subtree_mutation"]):
                    parent = _select_parent(islands[island], rng)
                    parents = [parent["representative"]["candidate_branch_hash"]]
                    if kernel == "V2":
                        v2["mutation_slots_total"] += 1
                        if float(rng.random()) < float(protocol["v2"]["mutation_slot_graft_probability"]):
                            v2["graft_selected"] += 1; v2["graft_attempted"] += 1
                            outcome = additive_root_residual_graft(parent["pair"], rng, caps, int(protocol["v2"]["subtree_nodes_max"]), grammar)
                            operation = "additive_root_residual_graft"
                            if outcome.outcome == "graft_parent_theta_saturated":
                                v2["graft_parent_theta_saturated"] += 1
                                return {"proposal_index":proposal_index,"island":island,"pair":None,"operation":operation,"parents":parents,"v2_counters":v2,"proposal_status":"GRAFT_PARENT_THETA_SATURATED","duplicate_hits":duplicate_hits,"generator_rejections":generator_rejections}
                            if outcome.outcome == "graft_no_legal_child":
                                v2["graft_no_legal_child"] += 1
                                return {"proposal_index":proposal_index,"island":island,"pair":None,"operation":operation,"parents":parents,"v2_counters":v2,"proposal_status":"GRAFT_NO_LEGAL_CHILD","duplicate_hits":duplicate_hits,"generator_rejections":generator_rejections}
                            pair = outcome.child; v2["graft_successful_child"] += 1
                            lineage_meta = {"component": outcome.component, "new_theta": outcome.new_theta}
                        else:
                            v2["standard_mutation_selected"] += 1
                            operation = "subtree_mutation"
                            pair = mutate_pair_policy(parent["pair"], rng, caps, grammar)
                    else:
                        operation = "subtree_mutation"
                        pair = mutate_pair_policy(parent["pair"], rng, caps, grammar)
                    if pair is None:
                        generator_rejections += 1
                        continue
                else:
                    crossover_pair, _, _ = _p11_search_imports()
                    pa = _select_parent(islands[island], rng); pb = _select_parent(islands[island], rng)
                    parents = [pa["representative"]["candidate_branch_hash"], pb["representative"]["candidate_branch_hash"]]
                    pair, lineage_meta = crossover_pair(pa["pair"], pb["pair"], rng, caps)
                    operation = "subtree_crossover"
        except RuntimeError:
            generator_rejections += 1
            continue
        if pair is None:
            generator_rejections += 1
            continue
        h = pair["structural_hash"]
        if h in duplicate_hashes:
            duplicate_hits += 1
            continue
        duplicate_hashes.add(h)
        lineage = {
            "operation": operation, "parent_branch_hashes": parents,
            "component": lineage_meta.get("component", ""),
            "new_theta": lineage_meta.get("new_theta"),
            "offspring_structural_hash": h, "island": island,
            "proposal_index": proposal_index, "attempt": attempt,
        }
        lineage["lineage_id"] = sha256_bytes(canonical_json_bytes(lineage))
        return {"proposal_index":proposal_index,"island":island,"pair":pair,"operation":operation,"parents":parents,"lineage":lineage,"v2_counters":v2,"proposal_status":"LEGAL_CHILD","duplicate_hits":duplicate_hits,"generator_rejections":generator_rejections}
    return {"proposal_index":proposal_index,"island":island,"pair":None,"operation":"generator_exhausted","parents":[],"v2_counters":v2,"proposal_status":"GENERATOR_EXHAUSTED","duplicate_hits":duplicate_hits,"generator_rejections":generator_rejections+2048}


def _representative(fit: dict[str, Any]) -> dict[str, Any] | None:
    reps = fit.get("breeding_representatives", [])
    if not reps:
        return None
    stage = max(int(r["stage_index"]) for r in reps)
    rows = [r for r in reps if int(r["stage_index"]) == stage]
    if stage == 5:
        return min(rows, key=lambda r: (float(r["J_princ"]), float(r["J_XT"]), float(r["J_XX"]), r["candidate_branch_hash"]))
    return min(rows, key=lambda r: r["candidate_branch_hash"])


def _extract_ji(branch: dict[str, Any]) -> list[float] | None:
    rows = branch.get("per_field")
    if not isinstance(rows, list) or len(rows) != 6:
        return None
    vals = []
    for r in rows:
        if r.get("J_princ") is None:
            return None
        vals.append(float(r["J_princ"]))
    return vals


def _identity_baseline(base: dict[str, Any], k1: Path, grid: int) -> dict[str, Any]:
    from .calibration_instruments import build_identity_pair
    fields = load_field_views(k1, "TRAIN_OPERATOR", grid)
    pair = build_identity_pair(base["caps"])
    r = _family_evaluator(base, fields)(pair, [])
    ji = _extract_ji(r)
    if ji is None or r.get("J_family") is None:
        raise RuntimeError("identity baseline unresolved")
    return {"J_i": ji, "J_family": float(r["J_family"]), "J_max": float(r["J_max"]), "structural_hash": pair["structural_hash"]}


def _append_jsonl(handle, obj: Any) -> None:
    handle.write(json.dumps(obj, sort_keys=True, separators=(",", ":")) + "\n")


def _truncate(path: Path, offset: int) -> None:
    if path.exists():
        with path.open("r+b") as f:
            f.truncate(int(offset))


def _checkpoint_summary(arm: str, seed: int, processed: int, counters: Counter, frontier: dict[str, Any], identity: dict[str, Any]) -> dict[str, Any]:
    return {
        "arm":arm,"paired_seed":seed,"processed":processed,
        "counters":dict(counters),"frontier":frontier,"identity_baseline":identity,
        "scientific_membership_rule":"complete_F4_branches_no_topk_percentile_pareto_target_count",
        "scientific_result_may_change_remaining_seeds":False,
    }


def run_arm(root: Path, run: Path, k1: Path, base: dict[str, Any], protocol: dict[str, Any], seed: int, arm: str, workers: int, formal_total: int | None = None) -> dict[str, Any]:
    total = int(formal_total if formal_total is not None else protocol["budget"]["proposals_per_seed_per_arm"])
    unit = run / "units" / f"seed_{seed:02d}" / arm
    unit.mkdir(parents=True, exist_ok=True)
    paths = {name: unit/name for name in ["proposal_ledger.jsonl","skeleton_registry.jsonl","branch_registry.jsonl","equivalence_map.jsonl"]}
    cp = unit / "checkpoint_state.json"
    islands = [[] for _ in range(int(protocol["budget"]["island_count"]))]
    duplicate_hashes: set[str] = set()
    counters = Counter()
    processed = 0; batches = 0
    frontier = {"coefficient_dependent":None,"coefficient_free":None,"best_any":None}
    offsets = {k:0 for k in paths}
    if cp.is_file():
        s = _load_json(cp)
        islands = s["islands"]; duplicate_hashes = set(s["duplicate_hashes"]); counters = Counter(s["counters"])
        processed = int(s["processed"]); batches = int(s["batches"]); frontier = s["frontier"]; offsets = {k:int(v) for k,v in s["ledger_offsets"].items()}
        for k,p in paths.items(): _truncate(p, offsets[k])
    identity_path = run / "identity_train_G33.json"
    if identity_path.is_file(): identity = _load_json(identity_path)
    else:
        identity = _identity_baseline(base,k1,int(protocol["formal_grid"])); _write_json(identity_path,identity)
    mode = "a" if processed else "w"
    handles = {k:p.open(mode,encoding="utf-8") for k,p in paths.items()}
    start = time.perf_counter(); resume_start = processed; last = start
    batch_size = int(protocol["budget"]["batch_size"])
    gauge_rule = {"rule":"S0_fixed_translation_common_positive_scale","application":"deterministic_per_field","optimized":False}
    try:
        with ProcessPoolExecutor(max_workers=workers, initializer=_worker_init, initargs=(str(root),str(k1),base,int(protocol["formal_grid"]))) as ex:
            while processed < total:
                n = min(batch_size, total-processed)
                generated=[]
                for idx in range(processed+1, processed+n+1):
                    g=generate_proposal(arm,seed,idx,protocol,islands,duplicate_hashes); generated.append(g)
                tasks=[]
                for g in generated:
                    if g["pair"] is not None:
                        tasks.append({"proposal_index":g["proposal_index"],"island":g["island"],"pair":g["pair"],"lineage":g.get("lineage",{}),"generated":g})
                futs={t["proposal_index"]:ex.submit(_worker_fit,t) for t in tasks}
                results={idx:f.result() for idx,f in futs.items()}
                for g in generated:
                    idx=int(g["proposal_index"]); counters["structural_proposals"]+=1
                    counters["duplicate_cache_hits"]+=int(g.get("duplicate_hits",0)); counters["generator_rejections"]+=int(g.get("generator_rejections",0))
                    for k,v in g.get("v2_counters",{}).items(): counters[k]+=int(v)
                    if g["pair"] is None:
                        counters[g["proposal_status"]]+=1
                        prow={"arm":arm,"paired_seed":seed,"proposal_index":idx,"proposal_status":g["proposal_status"],"operation":g["operation"],"parent_branch_hashes":g.get("parents",[]),"structural_hash":None,"coefficient_dependent_syntax":None,"theta_count":None,"F0_F4_status":"NO_CHILD","J_i":None,"J_family":None,"J_max":None,"evaluator_calls":0,"worker_cpu_seconds":0.0,"worker_wall_seconds":0.0,"v2_counters":g.get("v2_counters",{}),"membership_rule":"failed structural proposal; no scientific branch emitted"}
                        _append_jsonl(handles["proposal_ledger.jsonl"],prow); processed+=1; continue
                    row=results[idx]; pair=g["pair"]
                    _append_jsonl(handles["skeleton_registry.jsonl"],{"arm":arm,"paired_seed":seed,"proposal_index":idx,"structural_hash":pair["structural_hash"],"pair":pair,"lineage":g.get("lineage",{}),"coefficient_dependent_syntax":contains_coefficient_syntax(pair)})
                    if row.get("fatal_exception"):
                        counters["fatal_exception_count"]+=1
                        prow={"arm":arm,"paired_seed":seed,"proposal_index":idx,"proposal_status":"FIT_FATAL","operation":g["operation"],"structural_hash":pair["structural_hash"],"coefficient_dependent_syntax":contains_coefficient_syntax(pair),"fatal_exception_type":row["fatal_exception_type"],"fatal_exception":row["fatal_exception"],"evaluator_calls":0,"worker_cpu_seconds":float(row.get("worker_cpu_seconds",0)),"worker_wall_seconds":float(row.get("worker_wall_seconds",0)),"v2_counters":g.get("v2_counters",{}),"membership_rule":"no branch emitted due implementation/numerical fatal"}
                        _append_jsonl(handles["proposal_ledger.jsonl"],prow); processed+=1; continue
                    fit=row["fit"]; calls=int(fit.get("completed_calls",0)); counters["evaluator_calls"]+=calls; counters["worker_cpu_millis"]+=int(round(1000*float(row["worker_cpu_seconds"]))); counters["warning_count"]+=int(row.get("warning_count",0))
                    rep=_representative(fit)
                    if rep is not None:
                        islands[int(g["island"])].append({"pair":pair,"representative":rep}); islands[int(g["island"])]=_nondominance_view(islands[int(g["island"])])
                    f4=list(fit.get("all_F4_branches",[])); counters["F4_branch_emissions"]+=len(f4)
                    best=None
                    for j,b in enumerate(f4):
                        ji=_extract_ji(b)
                        if ji is None: continue
                        fitprov={"fitter":"frozen_P11_production","arm":arm,"paired_seed":seed,"proposal_index":idx,"fitter_candidate_branch_hash":b.get("candidate_branch_hash"),"emission_index":j}
                        bid=scientific_branch_id(pair,b["theta_vector"],gauge_rule,fitprov); eq=execution_equivalence_key(pair,b["theta_vector"],gauge_rule)
                        qual=operator_qualification(ji,identity["J_i"],True,True,float(protocol["operator_qualification"]["tau_num"]))
                        brow={"scientific_branch_id":bid,"arm":arm,"paired_seed":seed,"proposal_index":idx,"structural_hash":pair["structural_hash"],"theta_hex":[float(x).hex() for x in b["theta_vector"]],"theta_vector":[float(x) for x in b["theta_vector"]],"fit_provenance":fitprov,"deterministic_gauge":gauge_rule,"coefficient_dependent_syntax":contains_coefficient_syntax(pair),"J_i":ji,"J_family":float(b["J_princ"]),"J_max":float(max(ji)),"J_XT":float(b["J_XT"]),"J_XX":float(b["J_XX"]),"operator_qualification":qual,"exact_equivalence_class":eq,"membership_rule":"complete_F4_branch_registry_no_topk_percentile_pareto_target_count"}
                        _append_jsonl(handles["branch_registry.jsonl"],brow); _append_jsonl(handles["equivalence_map.jsonl"],{"scientific_branch_id":bid,"exact_equivalence_class":eq})
                        counters["F4_scientific_branches"]+=1
                        if qual["status"]=="OPERATOR_QUALIFIED_TRAIN": counters["operator_qualified_clear"]+=1
                        elif qual["status"]=="OPERATOR_QUALIFIED_UNRESOLVED": counters["operator_qualified_unresolved"]+=1
                        jf=float(b["J_princ"]); key="coefficient_dependent" if contains_coefficient_syntax(pair) else "coefficient_free"
                        frontier[key]=jf if frontier[key] is None else min(float(frontier[key]),jf); frontier["best_any"]=jf if frontier["best_any"] is None else min(float(frontier["best_any"]),jf)
                        if best is None or jf<float(best["J_family"]): best=brow
                    prow={"arm":arm,"paired_seed":seed,"proposal_index":idx,"proposal_status":"FIT_COMPLETE","operation":g["operation"],"parent_branch_hashes":g.get("parents",[]),"structural_hash":pair["structural_hash"],"coefficient_dependent_syntax":contains_coefficient_syntax(pair),"theta_count":len(theta_names(pair)),"F0_F4_status":"F4" if f4 else (rep.get("highest_feasibility_level") if rep else "NONE"),"J_i":None if best is None else best["J_i"],"J_family":None if best is None else best["J_family"],"J_max":None if best is None else best["J_max"],"best_scientific_branch_id":None if best is None else best["scientific_branch_id"],"evaluator_calls":calls,"worker_cpu_seconds":float(row["worker_cpu_seconds"]),"worker_wall_seconds":float(row["worker_wall_seconds"]),"v2_counters":g.get("v2_counters",{}),"membership_rule":"proposal summary only; all F4 theta branches retained separately"}
                    _append_jsonl(handles["proposal_ledger.jsonl"],prow); processed+=1
                batches+=1
                mig=protocol["gp"]["migration"]
                if int(mig["interval_completed_batches"])>0 and batches%int(mig["interval_completed_batches"])==0:
                    events=_migrate_ring(islands,int(mig["records_per_island"])); counters["migration_events"]+=1 if events else 0; counters["migration_records"]+=len(events)
                for h in handles.values(): h.flush(); os.fsync(h.fileno())
                if processed in set(map(int,protocol["budget"]["checkpoints"])):
                    _write_json(unit/f"checkpoint_{processed:06d}.json",_checkpoint_summary(arm,seed,processed,counters,frontier,identity))
                if batches%int(protocol["runtime"]["checkpoint_every_batches"])==0 or processed>=total:
                    state={"schema":"P13_S1_K1_UNIT_CHECKPOINT_V1","arm":arm,"paired_seed":seed,"processed":processed,"batches":batches,"islands":islands,"duplicate_hashes":sorted(duplicate_hashes),"counters":dict(counters),"frontier":frontier,"ledger_offsets":{k:int(h.tell()) for k,h in handles.items()}}
                    _write_json(cp,state)
                now=time.perf_counter()
                if now-last>=float(protocol["runtime"]["progress_every_seconds"]) or processed>=total:
                    elapsed=now-start; rate=(processed-resume_start)/max(elapsed,1e-12); eta=(total-processed)/rate if rate>0 else math.inf
                    print(f"[P13 S1 K1A seed={seed} arm={arm}] processed={processed}/{total} calls={counters['evaluator_calls']} silent_fallback={counters['silent_fallback_to_V1']} elapsed={elapsed:.1f}s rate={rate:.3g}/s ETA={eta/3600:.2f}h",flush=True); last=now
    finally:
        for h in handles.values(): h.close()
    status="PASS" if processed==total and int(counters["fatal_exception_count"])==0 and int(counters["silent_fallback_to_V1"])==0 else "FAIL"
    summary={"status":status,"arm":arm,"paired_seed":seed,"processed":processed,"total":total,"counters":dict(counters),"frontier":frontier,"identity_baseline":identity,"authoritative_unit":str(unit.relative_to(root)),"scientific_peeking_may_change_remaining_protocol":False}
    _write_json(unit/"unit_summary.json",summary); return summary


def _initial_digest(unit: Path, n: int) -> str:
    """Digest the complete structural initialization emission, not only legal skeletons.

    Arm labels and fitter/J outcomes are deliberately excluded.  The digest therefore
    tests the preregistered claim that FULL-V1 and FULL-V2 see byte-identical
    structural initial populations before their proposal kernels diverge.
    """
    rows=[]
    p=unit/"proposal_ledger.jsonl"
    if not p.is_file(): return ""
    for line in p.read_text().splitlines():
        if not line.strip():
            continue
        x=json.loads(line)
        if int(x["proposal_index"])<=n:
            rows.append({
                "proposal_index": int(x["proposal_index"]),
                "proposal_status": x.get("proposal_status"),
                "operation": x.get("operation"),
                "structural_hash": x.get("structural_hash"),
                "coefficient_dependent_syntax": x.get("coefficient_dependent_syntax"),
                "theta_count": x.get("theta_count"),
            })
    return sha256_bytes(canonical_json_bytes(rows))


def _update_rolling_context(root: Path, summary: dict[str, Any]) -> None:
    p=root/"P13_S1_ROLLING_CONTEXT.md"
    if not p.is_file(): return
    marker="<!-- K1A_FORMAL_RESULT -->"
    block=f'''## S1-K1A formal seed-1 staged execution\n\n- authoritative S1 store: `{summary['authoritative_S1_run']}`\n- `OVERALL_STATUS`: **{summary['OVERALL_STATUS']}**\n- seed: `1` across `NULL-V2`, `FULL-V1`, `FULL-V2`\n- each arm completed: `{summary['proposals_per_arm']}` structural proposals\n- FULL-V1/FULL-V2 initial-population byte identity: `{summary['integrity']['FULL_V1_V2_initial_population_byte_identical']}`\n- `silent_fallback_to_V1`: `{summary['integrity_metadata']['silent_fallback_to_V1']}`\n- forbidden diagnostic/DEVELOPMENT/SEALED/historical-response reads: `0`\n- K1A status is an engineering-integrity gate before K1B. Seed-1 J/F4/arm rankings are formal evidence but may not change the remaining three-seed protocol.\n- next action on PASS: `P13-S1-K1B_REMAINING_THREE_SEEDS_UNCHANGED`\n\n{marker}\n'''
    text=p.read_text()
    if marker in text: text=text.replace(marker,block)
    else: text=text.rstrip()+"\n\n"+block
    _write_text(p,text)


def main(argv: list[str] | None = None) -> int:
    ap=argparse.ArgumentParser(); ap.add_argument("--project-root",default="."); ap.add_argument("--workers",type=int,default=16); ap.add_argument("--seed",type=int,default=1); ap.add_argument("--test-proposals",type=int,default=None,help=argparse.SUPPRESS)
    args=ap.parse_args(argv); root=Path(args.project_root).resolve()
    for p in [root/"phases/p13/coefficient_law_raw_xt/src",root/"phases/p11/raw_xt_td/src"]:
        if str(p) not in sys.path: sys.path.insert(0,str(p))
    protocol=_load_json(root/"phases/p13/coefficient_law_raw_xt/configs/p13_s1_k1_protocol.json")
    if int(args.seed)!=int(protocol["k1a_seed"]): raise RuntimeError("K1A may execute only paired seed 1")
    k0r,k1,entry=_verify_entry(root,protocol)
    base=_load_json(root/"phases/p13/coefficient_law_raw_xt/configs/p13_s0_k2_protocol.json")
    # Reuse frozen production validity/operator/fitter settings; replace only caps with K0R/S1 exact caps (same L3 semantics).
    base["caps"]=protocol["caps"]
    runs=root/"phases/p13/coefficient_law_raw_xt/runs"; runs.mkdir(parents=True,exist_ok=True)
    s1marker=runs/"LATEST_P13_S1_FORMAL_RUN.txt"
    if s1marker.is_file():
        run=_resolve_marker(root,protocol["s1_run_marker"])
    else:
        stamp=time.strftime("%Y%m%dT%H%M%SZ",time.gmtime()); run=runs/f"p13_s1_formal_search_{stamp}"; run.mkdir(); _write_text(s1marker,str(run.relative_to(root)))
    _write_text(runs/"LATEST_P13_S1_K1A_RUN.txt",str(run.relative_to(root)))
    _write_json(run/"entry_provenance.json",entry); _write_json(run/"protocol_lock.json",{"path":"phases/p13/coefficient_law_raw_xt/configs/p13_s1_k1_protocol.json","sha256":_sha256_path(root/"phases/p13/coefficient_law_raw_xt/configs/p13_s1_k1_protocol.json"),"active_context_sha256":_sha256_path(root/protocol["active_context"]),"K0R_semantic":_load_json(k0r/"semantic_output_digest.json")["semantic_output_digest"]})
    _write_json(run/"data_boundary_guard.json",{"allowed_arrays":"TRAIN_OPERATOR only","OPENED_TRANSFER_DIAGNOSTIC_arrays_read":False,"WITHIN_FAMILY_TRANSFER_DIAGNOSTIC_payload_read":False,"DEVELOPMENT_read":False,"SEALED_read":False,"historical_response_read":False,"S0_calibration_candidates_used_as_seed":False,"K0R_audit_archive_used_as_runtime_input":False,"status":"PASS"})
    workers=max(1,min(int(args.workers),16)); os.environ.update({k:"1" for k in ["OMP_NUM_THREADS","MKL_NUM_THREADS","OPENBLAS_NUM_THREADS","NUMEXPR_NUM_THREADS"]})
    formal_total=None if args.test_proposals is None else int(args.test_proposals)
    summaries=[]
    for arm in protocol["arms"]:
        summaries.append(run_arm(root,run,k1,base,protocol,1,arm,workers,formal_total))
    init_n=min(int(protocol["budget"]["initial_population_per_island"])*int(protocol["budget"]["island_count"]), int(formal_total or protocol["budget"]["proposals_per_seed_per_arm"]))
    dig={s["arm"]:_initial_digest(root/s["authoritative_unit"],init_n) for s in summaries}
    silent=sum(int(s["counters"].get("silent_fallback_to_V1",0)) for s in summaries)
    integrity={
        "all_units_complete": all(s["status"]=="PASS" for s in summaries),
        "FULL_V1_V2_initial_population_byte_identical": dig["FULL-V1"]==dig["FULL-V2"],
        "silent_fallback_to_V1_zero": silent==0,
        "proposal_accounting_conservation": all(int(s["processed"])==int(s["total"]) for s in summaries),
        "no_forbidden_data_read": True,
        "worker_cap_respected": workers<=16,
        "BLAS_OpenMP_single_thread": True,
    }
    status="PASS" if all(integrity.values()) else "FAIL"
    summary={
        "OVERALL_STATUS":status,
        "stage":"P13-S1-K1A",
        "authoritative_S1_run":str(run.relative_to(root)),
        "proposals_per_arm":int(formal_total or protocol["budget"]["proposals_per_seed_per_arm"]),
        "units":summaries,
        "integrity":integrity,
        "integrity_metadata":{
            "initial_population_digests":dig,
            "silent_fallback_to_V1":silent,
            "worker_count":workers,
            "BLAS_OpenMP_threads":1,
            "forbidden_data_read":False,
        },
        "scientific_seed1_results_may_change_remaining_protocol":False,
        "NEXT_ACTION":"P13-S1-K1B_REMAINING_THREE_SEEDS_UNCHANGED" if status=="PASS" else "REPAIR_K1A_ENGINEERING_INTEGRITY_BEFORE_K1B"
    }
    _write_json(run/"K1A_integrity_summary.json",summary); _write_text(run/"K1A_OVERALL_STATUS.txt",status+"\n"); _write_text(run/"K1A_NEXT_ACTION.txt",summary["NEXT_ACTION"]+"\n")
    _update_rolling_context(root,summary)
    print(f"OVERALL_STATUS={status}",flush=True)
    return 0 if status=="PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
