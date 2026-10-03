#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Callable, Iterable

import numpy as np
from scipy.stats import qmc

from p11rawxt_ast import parameter_names
from p11rawxt_common import canonical_json_bytes, sha256_bytes


def _theta_key(theta: Iterable[float]) -> tuple[str, ...]:
    return tuple(float(value).hex() for value in theta)


def candidate_branch_hash(structural_hash: str, theta: Iterable[float]) -> str:
    payload = {"structural_hash": structural_hash, "theta_hex": list(_theta_key(theta))}
    return sha256_bytes(canonical_json_bytes(payload))


def _maximize_dominates(a: tuple[float, ...], b: tuple[float, ...]) -> bool:
    if len(a) != len(b):
        return False
    return all(x >= y for x, y in zip(a, b)) and any(x > y for x, y in zip(a, b))


def _minimize_dominates(a: tuple[float, ...], b: tuple[float, ...]) -> bool:
    return all(x <= y for x, y in zip(a, b)) and any(x < y for x, y in zip(a, b))


def record_dominates(a: dict[str, Any], b: dict[str, Any]) -> bool:
    if int(a["stage_index"]) != int(b["stage_index"]):
        return int(a["stage_index"]) > int(b["stage_index"])
    if int(a["stage_index"]) == 5 and a.get("J_princ") is not None and b.get("J_princ") is not None:
        return _minimize_dominates(
            (float(a["J_princ"]), float(a["J_XT"]), float(a["J_XX"])),
            (float(b["J_princ"]), float(b["J_XT"]), float(b["J_XX"])),
        )
    return _maximize_dominates(tuple(map(float, a.get("direct_margin_vector", []))), tuple(map(float, b.get("direct_margin_vector", []))))


def nondominated_indices(records: list[dict[str, Any]]) -> list[int]:
    out: list[int] = []
    for i, candidate in enumerate(records):
        dominated = False
        remove: list[int] = []
        for existing_index in out:
            existing = records[existing_index]
            if record_dominates(existing, candidate):
                dominated = True
                break
            if record_dominates(candidate, existing):
                remove.append(existing_index)
        if dominated:
            continue
        out = [index for index in out if index not in remove]
        out.append(i)
    return out


def parameter_names_for_pair(pair: dict[str, Any]) -> list[str]:
    names = parameter_names(pair["raw_X_AST"]) | parameter_names(pair["raw_T_AST"])
    return sorted(names, key=lambda name: int(name.split("_")[1]))


def _sobol_points(parameter_count: int, count: int, lower: float, upper: float, seed: int) -> list[list[float]]:
    if parameter_count == 0 or count <= 0:
        return []
    exponent = int(math.ceil(math.log2(max(count, 1))))
    sampler = qmc.Sobol(d=parameter_count, scramble=True, seed=seed)
    unit = sampler.random_base2(exponent)[:count]
    return (lower + (upper - lower) * unit).tolist()


def fit_skeleton(
    pair: dict[str, Any],
    evaluate: Callable[[dict[str, Any], list[float]], dict[str, Any]],
    fitter_protocol: dict[str, Any],
    *,
    call_budget_override: int | None = None,
) -> dict[str, Any]:
    names = parameter_names_for_pair(pair)
    p = len(names)
    maximum = int(fitter_protocol["maximum_calls_by_parameter_count"][str(p)])
    budget = min(maximum, int(call_budget_override)) if call_budget_override is not None else maximum
    if budget <= 0:
        return {"parameter_names": names, "call_records": [], "retained_branches": [], "breeding_representatives": []}
    lower, upper = map(float, fitter_protocol["parameter_bounds"])
    seed = int(pair["structural_hash"][:16], 16) & 0xFFFFFFFF
    proposed: list[list[float]] = []
    seen: set[tuple[str, ...]] = set()

    def add(theta: list[float]) -> None:
        clipped = [min(upper, max(lower, float(value))) for value in theta]
        key = _theta_key(clipped)
        if key not in seen and len(proposed) < budget:
            seen.add(key)
            proposed.append(clipped)

    add([0.0] * p)
    phase1_target = min(budget, max(1, 1 + 8 * p))
    for theta in _sobol_points(p, max(phase1_target - 1, 0), lower, upper, seed):
        add(theta)

    records: list[dict[str, Any]] = []
    for theta in proposed:
        record = evaluate(pair, theta)
        record["parameter_names"] = names
        record["candidate_branch_hash"] = candidate_branch_hash(pair["structural_hash"], theta)
        records.append(record)

    steps = [1.5, 0.75, 0.375, 0.1875, 0.09375, 0.046875]
    step_index = 0
    while len(records) < budget:
        frontier = [records[index] for index in nondominated_indices(records)]
        made = False
        step = steps[min(step_index, len(steps) - 1)]
        for base in frontier:
            theta0 = list(map(float, base["theta_vector"]))
            for axis in range(p):
                for sign in (-1.0, 1.0):
                    theta = theta0.copy()
                    theta[axis] += sign * step
                    before = len(proposed)
                    add(theta)
                    if len(proposed) == before:
                        continue
                    record = evaluate(pair, proposed[-1])
                    record["parameter_names"] = names
                    record["candidate_branch_hash"] = candidate_branch_hash(pair["structural_hash"], proposed[-1])
                    records.append(record)
                    made = True
                    if len(records) >= budget:
                        break
                if len(records) >= budget:
                    break
            if len(records) >= budget:
                break
        if not made:
            # Deterministic additional Sobol block prevents fitter deadlock without a discrete answer table.
            extra_seed = (seed + 0x9E3779B9 * (step_index + 1)) & 0xFFFFFFFF
            for theta in _sobol_points(p, max(8 * max(p, 1), 1), lower, upper, extra_seed):
                before = len(proposed)
                add(theta)
                if len(proposed) == before:
                    continue
                record = evaluate(pair, proposed[-1])
                record["parameter_names"] = names
                record["candidate_branch_hash"] = candidate_branch_hash(pair["structural_hash"], proposed[-1])
                records.append(record)
                made = True
                if len(records) >= budget:
                    break
        step_index += 1
        if not made:
            break

    f4 = [record for record in records if int(record["stage_index"]) == 5 and record.get("J_princ") is not None]
    breeding_f4 = [f4[index] for index in nondominated_indices(f4)] if f4 else []
    breeding = [records[index] for index in nondominated_indices(records)]
    return {
        "parameter_names": names,
        "maximum_protocol_calls": maximum,
        "completed_calls": len(records),
        "call_records": records,
        # Scientific archive membership is every exact-hash-unique F4 branch.
        # Nondominance is restricted to the breeding proposal view.
        "all_F4_branches": f4,
        "breeding_F4_representatives": breeding_f4,
        "breeding_representatives": breeding,
        "all_F4_probe_count": len(f4),
        "breeding_F4_nondominated_branch_count": len(breeding_f4),
        "broad_archive_uses_all_F4_branches": True,
        "response_guided_fitting": False,
        "weighted_constraint_penalty": False,
        "weighted_operator_score": False,
    }
