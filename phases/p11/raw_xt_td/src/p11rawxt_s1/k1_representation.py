#!/usr/bin/env python3
from __future__ import annotations

import copy
import itertools
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from p11rawxt_ast import (
    Const,
    Node,
    Op,
    Theta,
    Var,
    canonical_serialization,
    check_pair_caps,
    depth,
    derivative_nesting,
    node_count,
    normalize,
    operator_multiset,
    pair_stats,
    parameter_names,
    renumber_parameter_pair,
    walk_preorder,
)
from p11rawxt_common import canonical_json_bytes, sha256_bytes


FROZEN_VARIABLES = {"x", "t", "a", "q"}
FROZEN_TERMINAL_CONSTANTS = {-1, 0, 1}
FROZEN_BINARY_OPERATORS = {"Add", "Sub", "Mul", "Div"}
FROZEN_UNARY_OPERATORS = {"Inv", "Sqrt", "Log", "Dx", "Dt"}
FROZEN_POWINT_EXPONENTS = {-3, -2, -1, 2, 3}


@dataclass
class ExactPairDuplicateCache:
    first_fixture_by_hash: dict[str, str] = field(default_factory=dict)
    duplicate_count: int = 0

    def register(self, structural_hash: str, fixture_id: str) -> tuple[bool, str]:
        first = self.first_fixture_by_hash.get(structural_hash)
        if first is not None:
            self.duplicate_count += 1
            return True, first
        self.first_fixture_by_hash[structural_hash] = fixture_id
        return False, fixture_id


def _raw_terminal_nodes() -> list[Node]:
    return [Var("x"), Var("t"), Var("a"), Var("q"), Const(-1), Const(0), Const(1), Theta("theta_1")]


def validate_raw_ast(node: Node, derivative_nesting_max: int | None = None) -> list[str]:
    failures: list[str] = []

    def visit(current: Node, derivative_level: int) -> None:
        op = current.get("op")
        args = current.get("args", [])
        if op == "Var":
            if current.get("name") not in FROZEN_VARIABLES:
                failures.append(f"unknown_variable:{current.get('name')}")
            if args:
                failures.append("variable_has_args")
            return
        if op == "Const":
            if int(current.get("value", 999999)) not in FROZEN_TERMINAL_CONSTANTS:
                failures.append(f"raw_constant_not_terminal:{current.get('value')}")
            if args:
                failures.append("constant_has_args")
            return
        if op == "Theta":
            name = str(current.get("name", ""))
            if not name.startswith("theta_") or not name[6:].isdigit() or int(name[6:]) <= 0:
                failures.append(f"invalid_theta:{name}")
            if args:
                failures.append("theta_has_args")
            return
        if op in FROZEN_BINARY_OPERATORS:
            if len(args) != 2:
                failures.append(f"wrong_arity:{op}:{len(args)}")
        elif op in FROZEN_UNARY_OPERATORS:
            if len(args) != 1:
                failures.append(f"wrong_arity:{op}:{len(args)}")
        elif op == "PowInt":
            if len(args) != 1:
                failures.append(f"wrong_arity:PowInt:{len(args)}")
            if int(current.get("exponent", 0)) not in FROZEN_POWINT_EXPONENTS:
                failures.append(f"invalid_powint_exponent:{current.get('exponent')}")
        elif op == "PowParam":
            if len(args) != 2:
                failures.append(f"wrong_arity:PowParam:{len(args)}")
            elif args[1].get("op") != "Theta":
                failures.append("powparam_second_argument_not_theta")
        else:
            failures.append(f"unknown_operator:{op}")
        next_level = derivative_level + 1 if op in {"Dx", "Dt"} else derivative_level
        if derivative_nesting_max is not None and next_level > derivative_nesting_max:
            failures.append(f"derivative_nesting>{derivative_nesting_max}")
        for child in args:
            visit(child, next_level)

    visit(node, 0)
    return sorted(set(failures))


def _known_sign(node: Node) -> str:
    op = node.get("op")
    if op == "Const":
        value = int(node["value"])
        return "positive" if value > 0 else "negative" if value < 0 else "zero"
    if op == "Var" and node.get("name") == "a":
        return "positive"
    if op == "Neg":
        child = _known_sign(node["args"][0])
        return {"positive": "negative", "negative": "positive", "zero": "zero"}.get(child, "unknown")
    if op == "Mul":
        signs = [_known_sign(child) for child in node.get("args", [])]
        if "zero" in signs:
            return "zero"
        if all(sign in {"positive", "negative"} for sign in signs):
            negatives = sum(sign == "negative" for sign in signs)
            return "negative" if negatives % 2 else "positive"
    if op == "Inv":
        child = _known_sign(node["args"][0])
        return child if child in {"positive", "negative"} else "unknown"
    if op == "Sqrt":
        child = _known_sign(node["args"][0])
        return "positive" if child == "positive" else "zero" if child == "zero" else "unknown"
    if op == "PowInt":
        child = _known_sign(node["args"][0])
        exponent = int(node["exponent"])
        if child == "zero" and exponent > 0:
            return "zero"
        if child in {"positive", "negative"}:
            if exponent % 2 == 0:
                return "positive"
            return child
    return "unknown"


def domain_audit(node: Node, component: str) -> dict[str, Any]:
    impossible: list[str] = []
    obligations: list[dict[str, Any]] = []
    for position, current in enumerate(walk_preorder(node)):
        op = current.get("op")
        args = current.get("args", [])
        if op == "Inv":
            sign = _known_sign(args[0])
            if sign == "zero":
                impossible.append(f"{component}:preorder_{position}:Inv_zero")
            elif sign == "unknown":
                obligations.append({"component": component, "preorder_position": position, "condition": "argument_nonzero", "operator": op})
        elif op in {"Sqrt", "Log"}:
            sign = _known_sign(args[0])
            if sign in {"zero", "negative"}:
                impossible.append(f"{component}:preorder_{position}:{op}_nonpositive")
            elif sign == "unknown":
                obligations.append({"component": component, "preorder_position": position, "condition": "argument_positive", "operator": op})
        elif op == "PowInt" and int(current["exponent"]) < 0:
            sign = _known_sign(args[0])
            if sign == "zero":
                impossible.append(f"{component}:preorder_{position}:PowInt_negative_zero_base")
            elif sign == "unknown":
                obligations.append({"component": component, "preorder_position": position, "condition": "base_nonzero", "operator": op})
        elif op == "PowParam":
            sign = _known_sign(args[0])
            if sign in {"zero", "negative"}:
                impossible.append(f"{component}:preorder_{position}:PowParam_nonpositive_base")
            elif sign == "unknown":
                obligations.append({"component": component, "preorder_position": position, "condition": "base_positive", "operator": op})
    return {
        "static_domain_status": "IMPOSSIBLE" if impossible else "UNRESOLVED_OR_PROVEN",
        "static_impossibilities": impossible,
        "domain_obligations": obligations,
        "unresolved_obligation_count": len(obligations),
    }


def l0_pair_caps(l0_cfg: dict[str, Any]) -> dict[str, int]:
    return {
        "nodes_X_max": int(l0_cfg["nodes_component_max"]),
        "nodes_T_max": int(l0_cfg["nodes_component_max"]),
        "total_nodes_max": int(l0_cfg["total_nodes_max"]),
        "depth_max": int(l0_cfg["depth_max"]),
        "unique_theta_total_max": int(l0_cfg["unique_theta_total_max"]),
        "unique_theta_component_max": int(l0_cfg["unique_theta_total_max"]),
        "theta_occurrences_total_max": 12,
        "derivative_nesting_max": int(l0_cfg["derivative_nesting_max"]),
    }


def canonicalize_pair(raw_x: Node, raw_t: Node, caps: dict[str, int]) -> dict[str, Any]:
    raw_failures = validate_raw_ast(raw_x, caps.get("derivative_nesting_max")) + validate_raw_ast(raw_t, caps.get("derivative_nesting_max"))
    x_can, t_can, parameter_mapping = renumber_parameter_pair(raw_x, raw_t)
    cap_ok, cap_failures, stats = check_pair_caps(x_can, t_can, caps)
    x_domain = domain_audit(x_can, "X")
    t_domain = domain_audit(t_can, "T")
    pair_payload = {"raw_X_AST": x_can, "raw_T_AST": t_can}
    pair_serialization = canonical_json_bytes(pair_payload).decode("utf-8").strip()
    structural_hash = sha256_bytes(canonical_json_bytes(pair_payload))
    static_domain_impossible = bool(x_domain["static_impossibilities"] or t_domain["static_impossibilities"])
    return {
        "raw_X_AST": x_can,
        "raw_T_AST": t_can,
        "canonical_pair_serialization": pair_serialization,
        "structural_hash": structural_hash,
        "parameter_identity_mapping": parameter_mapping,
        "pair_stats": stats,
        "operator_multiset_X": dict(sorted(operator_multiset(x_can).items())),
        "operator_multiset_T": dict(sorted(operator_multiset(t_can).items())),
        "raw_grammar_valid": not raw_failures,
        "raw_grammar_failures": sorted(set(raw_failures)),
        "caps_valid": cap_ok,
        "cap_failures": cap_failures,
        "static_domain_impossible": static_domain_impossible,
        "domain_obligations": x_domain["domain_obligations"] + t_domain["domain_obligations"],
        "static_domain_failures": x_domain["static_impossibilities"] + t_domain["static_impossibilities"],
    }


def _l0_raw_component_productions(grammar_cfg: dict[str, Any]) -> Iterable[tuple[str, Node]]:
    terminals = _raw_terminal_nodes()
    for index, terminal in enumerate(terminals):
        yield f"terminal:{index}", copy.deepcopy(terminal)
    for op in ["Inv", "Sqrt", "Log"]:
        for index, terminal in enumerate(terminals):
            yield f"unary:{op}:{index}", Op(op, terminal)
    for exponent in [-3, -2, -1, 2, 3]:
        for index, terminal in enumerate(terminals):
            yield f"powint:{exponent}:{index}", Op("PowInt", terminal, exponent=exponent)
    for index, terminal in enumerate(terminals):
        yield f"powparam:{index}", Op("PowParam", terminal, Theta("theta_1"))
    for op in ["Add", "Sub", "Mul", "Div"]:
        for left_index, right_index in itertools.product(range(len(terminals)), repeat=2):
            yield f"binary:{op}:{left_index}:{right_index}", Op(op, terminals[left_index], terminals[right_index])


def build_l0_component_census(grammar_cfg: dict[str, Any], l0_cfg: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    canonical: dict[str, dict[str, Any]] = {}
    raw_production_count = 0
    raw_grammar_failure_count = 0
    normalization_failure_count = 0
    for production_id, raw in _l0_raw_component_productions(grammar_cfg):
        raw_production_count += 1
        raw_failures = validate_raw_ast(raw, int(l0_cfg["derivative_nesting_max"]))
        if raw_failures:
            raw_grammar_failure_count += 1
            continue
        try:
            normalized = normalize(raw)
        except Exception:
            normalization_failure_count += 1
            continue
        if node_count(normalized) > int(l0_cfg["nodes_component_max"]):
            continue
        if depth(normalized) > int(l0_cfg["depth_max"]):
            continue
        if derivative_nesting(normalized) > int(l0_cfg["derivative_nesting_max"]):
            continue
        if len(parameter_names(normalized)) > int(l0_cfg["unique_theta_total_max"]):
            continue
        serialization = canonical_serialization(normalized)
        row = canonical.get(serialization)
        if row is None:
            audit = domain_audit(normalized, "component")
            canonical[serialization] = {
                "component_hash": sha256_bytes(serialization.encode("utf-8")),
                "representative_raw_AST": copy.deepcopy(raw),
                "canonical_AST": normalized,
                "canonical_serialization": serialization,
                "nodes": node_count(normalized),
                "depth": depth(normalized),
                "unique_theta": len(parameter_names(normalized)),
                "derivative_nesting": derivative_nesting(normalized),
                "static_domain_impossible": audit["static_domain_status"] == "IMPOSSIBLE",
                "static_domain_failures": audit["static_impossibilities"],
                "domain_obligations": audit["domain_obligations"],
                "raw_production_ids": [production_id],
            }
        else:
            row["raw_production_ids"].append(production_id)
    rows = []
    for index, serialization in enumerate(sorted(canonical)):
        row = canonical[serialization]
        row["component_fixture_id"] = f"L0C{index:04d}"
        row["raw_production_multiplicity"] = len(row["raw_production_ids"])
        rows.append(row)
    summary = {
        "raw_component_production_count": raw_production_count,
        "raw_grammar_failure_count": raw_grammar_failure_count,
        "normalization_failure_count": normalization_failure_count,
        "canonical_component_count": len(rows),
        "statically_impossible_component_count": sum(row["static_domain_impossible"] for row in rows),
        "domain_usable_or_unresolved_component_count": sum(not row["static_domain_impossible"] for row in rows),
        "component_count_by_nodes": dict(sorted(Counter(str(row["nodes"]) for row in rows).items())),
        "component_count_by_depth": dict(sorted(Counter(str(row["depth"]) for row in rows).items())),
    }
    return rows, summary


def build_l0_paired_census(
    grammar_cfg: dict[str, Any],
    l0_cfg: dict[str, Any],
    progress: Callable[[int, int], None] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    components, component_summary = build_l0_component_census(grammar_cfg, l0_cfg)
    caps = l0_pair_caps(l0_cfg)
    total_cartesian = len(components) * len(components)
    cache = ExactPairDuplicateCache()
    records: list[dict[str, Any]] = []
    attempted_pairs = 0
    cap_rejected = 0
    static_domain_rejected = 0
    grammar_rejected = 0
    duplicate_hits = 0
    obligation_histogram: Counter[str] = Counter()
    stats_histogram: Counter[tuple[int, int, int]] = Counter()

    processed = 0
    for x_row in components:
        for t_row in components:
            processed += 1
            if progress is not None:
                progress(processed, total_cartesian)
            if int(x_row["nodes"]) + int(t_row["nodes"]) > int(l0_cfg["total_nodes_max"]):
                continue
            attempted_pairs += 1
            pair = canonicalize_pair(x_row["representative_raw_AST"], t_row["representative_raw_AST"], caps)
            if not pair["raw_grammar_valid"]:
                grammar_rejected += 1
                continue
            if not pair["caps_valid"]:
                cap_rejected += 1
                continue
            if pair["static_domain_impossible"]:
                static_domain_rejected += 1
                continue
            fixture_id = f"L0P{len(records):05d}"
            duplicate, first_fixture = cache.register(pair["structural_hash"], fixture_id)
            if duplicate:
                duplicate_hits += 1
                continue
            for obligation in pair["domain_obligations"]:
                obligation_histogram[f"{obligation['operator']}:{obligation['condition']}"] += 1
            stats = pair["pair_stats"]
            stats_histogram[(stats["nodes_X"], stats["nodes_T"], stats["unique_theta_total"])] += 1
            records.append({
                "fixture_id": fixture_id,
                "fixture_scope": "complete_L0_paired_raw_XT_AST_census",
                "formal_candidate": False,
                "eligible_for_parent_pool": False,
                "quality_objective_evaluated": False,
                "parameter_fitting_run": False,
                "source_component_fixture_ids": [x_row["component_fixture_id"], t_row["component_fixture_id"]],
                **pair,
                "duplicate": False,
                "first_fixture_for_hash": first_fixture,
            })

    pair_digest = sha256_bytes(b"".join(canonical_json_bytes(row) for row in records))
    histogram_rows = [
        {
            "nodes_X": key[0],
            "nodes_T": key[1],
            "unique_theta_total": key[2],
            "pair_count": count,
        }
        for key, count in sorted(stats_histogram.items())
    ]
    summary = {
        "component_summary": component_summary,
        "component_cartesian_product_count": total_cartesian,
        "pair_attempt_count_after_total_node_prefilter": attempted_pairs,
        "grammar_rejected_pair_count": grammar_rejected,
        "cap_rejected_pair_count": cap_rejected,
        "static_domain_rejected_pair_count": static_domain_rejected,
        "exact_structural_duplicate_pair_count": duplicate_hits,
        "accepted_unique_pair_fixture_count": len(records),
        "formal_candidate_count": 0,
        "eligible_parent_pool_count": 0,
        "quality_objective_evaluation_count": 0,
        "parameter_fitting_count": 0,
        "pair_records_digest": pair_digest,
        "domain_obligation_histogram": dict(sorted(obligation_histogram.items())),
    }
    return records, summary, {"rows": histogram_rows}


def paired_representation_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "p11rawxt://s1/paired-map-proposal",
        "title": "P11 RawXT shared paired-map proposal record",
        "type": "object",
        "required": [
            "raw_X_AST",
            "raw_T_AST",
            "canonical_pair_serialization",
            "structural_hash",
            "parameter_identity_mapping",
            "pair_stats",
            "raw_grammar_valid",
            "caps_valid",
            "static_domain_impossible",
            "domain_obligations"
        ],
        "scientific_semantics": {
            "pair_order_is_semantic": True,
            "X_T_swap_is_not_equivalence": True,
            "translation_or_common_scale_is_not_removed_in_K1": True,
            "exact_structural_hash_is_the_only_online_quotient": True,
            "numerical_map_equivalence_is_deferred_to_K5": True
        }
    }


def canonicalization_invariance_fixtures(active_caps: dict[str, int]) -> list[dict[str, Any]]:
    fixtures: list[dict[str, Any]] = []

    def add(fixture_id: str, left_x: Node, left_t: Node, right_x: Node, right_t: Node, expect_equal: bool) -> None:
        left = canonicalize_pair(left_x, left_t, active_caps)
        right = canonicalize_pair(right_x, right_t, active_caps)
        observed = left["structural_hash"] == right["structural_hash"]
        fixtures.append({
            "fixture_id": fixture_id,
            "expect_equal": expect_equal,
            "observed_equal": observed,
            "pass": observed == expect_equal,
            "left_hash": left["structural_hash"],
            "right_hash": right["structural_hash"],
        })

    add("add_commutativity", Op("Add", Var("x"), Var("a")), Var("t"), Op("Add", Var("a"), Var("x")), Var("t"), True)
    add("sub_normalization_deterministic", Op("Sub", Var("x"), Var("a")), Var("t"), Op("Sub", Var("x"), Var("a")), Var("t"), True)
    add("div_rewrite", Op("Div", Var("x"), Var("a")), Var("t"), Op("Mul", Var("x"), Op("Inv", Var("a"))), Var("t"), True)
    add(
        "parameter_pair_renumber",
        Op("Add", Var("x"), Theta("theta_9")),
        Op("Add", Var("t"), Theta("theta_2")),
        Op("Add", Var("x"), Theta("theta_1")),
        Op("Add", Var("t"), Theta("theta_2")),
        True,
    )
    add("translation_not_gauge", Var("x"), Var("t"), Op("Add", Var("x"), Const(1)), Var("t"), False)
    add("common_scale_not_gauge", Var("x"), Var("t"), Op("Mul", Const(-1), Var("x")), Op("Mul", Const(-1), Var("t")), False)
    add("X_T_order_is_semantic", Var("x"), Var("t"), Var("t"), Var("x"), False)
    return fixtures


def duplicate_cache_self_test(active_caps: dict[str, int]) -> dict[str, Any]:
    first = canonicalize_pair(Op("Add", Var("x"), Var("a")), Var("t"), active_caps)
    same = canonicalize_pair(Op("Add", Var("a"), Var("x")), Var("t"), active_caps)
    different = canonicalize_pair(Var("x"), Op("Add", Var("t"), Var("a")), active_caps)
    cache = ExactPairDuplicateCache()
    a = cache.register(first["structural_hash"], "fixture_a")
    b = cache.register(same["structural_hash"], "fixture_b")
    c = cache.register(different["structural_hash"], "fixture_c")
    return {
        "first_is_duplicate": a[0],
        "commutative_equivalent_is_duplicate": b[0],
        "different_pair_is_duplicate": c[0],
        "duplicate_count": cache.duplicate_count,
        "pass": (not a[0]) and b[0] and (not c[0]) and cache.duplicate_count == 1,
    }
