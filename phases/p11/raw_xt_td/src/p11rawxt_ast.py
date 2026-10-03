#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
import re
from collections import Counter
from typing import Any, Iterable

Node = dict[str, Any]


def Var(name: str) -> Node:
    return {"op": "Var", "name": name}


def Const(value: int) -> Node:
    return {"op": "Const", "value": int(value)}


def Theta(name: str) -> Node:
    return {"op": "Theta", "name": name}


def Op(name: str, *args: Node, **attrs: Any) -> Node:
    node: Node = {"op": name, "args": [copy.deepcopy(arg) for arg in args]}
    node.update(attrs)
    return node


def masked_serialization(node: Node) -> str:
    def mask(obj: Any) -> Any:
        if isinstance(obj, dict):
            if obj.get("op") == "Theta":
                return {"op": "Theta", "name": "theta_*"}
            return {key: mask(value) for key, value in sorted(obj.items())}
        if isinstance(obj, list):
            return [mask(value) for value in obj]
        return obj
    return json.dumps(mask(node), sort_keys=True, separators=(",", ":"))


def canonical_serialization(node: Node) -> str:
    return json.dumps(node, sort_keys=True, separators=(",", ":"))


def _is_const(node: Node, value: int | None = None) -> bool:
    if node.get("op") != "Const":
        return False
    return value is None or int(node["value"]) == value


def _normalize_once(node: Node) -> Node:
    op = node.get("op")
    if op == "Var":
        if node.get("name") not in {"x", "t", "a", "q"}:
            raise ValueError(f"Unknown variable terminal: {node}")
        return {"op": "Var", "name": node["name"]}
    if op == "Const":
        value = int(node["value"])
        return Const(value)
    if op == "Theta":
        name = str(node["name"])
        if not re.fullmatch(r"theta_[1-9][0-9]*", name):
            raise ValueError(f"Invalid parameter terminal: {name}")
        return Theta(name)

    args = [_normalize_once(arg) for arg in node.get("args", [])]
    if op == "Sub":
        if len(args) != 2:
            raise ValueError("Sub requires two arguments")
        return _normalize_once(Op("Add", args[0], Op("Neg", args[1])))
    if op == "Div":
        if len(args) != 2:
            raise ValueError("Div requires two arguments")
        return _normalize_once(Op("Mul", args[0], Op("Inv", args[1])))
    if op == "Neg":
        if len(args) != 1:
            raise ValueError("Neg requires one argument")
        child = args[0]
        if _is_const(child):
            return Const(-int(child["value"]))
        if child.get("op") == "Neg":
            return child["args"][0]
        return {"op": "Neg", "args": [child]}
    if op == "Add":
        flat: list[Node] = []
        const_total = 0
        for child in args:
            if child.get("op") == "Add":
                flat.extend(child["args"])
            elif _is_const(child):
                const_total += int(child["value"])
            else:
                flat.append(child)
        if const_total:
            flat.append(Const(const_total))
        flat = [child for child in flat if not _is_const(child, 0)]
        if not flat:
            return Const(0)
        if len(flat) == 1:
            return flat[0]
        flat.sort(key=masked_serialization)
        return {"op": "Add", "args": flat}
    if op == "Mul":
        flat: list[Node] = []
        const_product = 1
        for child in args:
            if child.get("op") == "Mul":
                flat.extend(child["args"])
            elif _is_const(child):
                const_product *= int(child["value"])
            else:
                flat.append(child)
        if const_product == 0:
            return Const(0)
        if const_product != 1 or not flat:
            flat.append(Const(const_product))
        flat = [child for child in flat if not _is_const(child, 1)]
        if not flat:
            return Const(1)
        if len(flat) == 1:
            return flat[0]
        flat.sort(key=masked_serialization)
        return {"op": "Mul", "args": flat}
    if op == "Inv":
        if len(args) != 1:
            raise ValueError("Inv requires one argument")
        child = args[0]
        if _is_const(child, 1):
            return Const(1)
        if _is_const(child, -1):
            return Const(-1)
        if child.get("op") == "Inv":
            return child["args"][0]
        return {"op": "Inv", "args": [child]}
    if op in {"Sqrt", "Log"}:
        if len(args) != 1:
            raise ValueError(f"{op} requires one argument")
        return {"op": op, "args": args}
    if op == "PowInt":
        if len(args) != 1:
            raise ValueError("PowInt requires one argument")
        exponent = int(node["exponent"])
        if exponent == 0:
            return Const(1)
        if exponent == 1:
            return args[0]
        if exponent == -1:
            return _normalize_once(Op("Inv", args[0]))
        return {"op": "PowInt", "exponent": exponent, "args": args}
    if op == "PowParam":
        if len(args) != 2 or args[1].get("op") != "Theta":
            raise ValueError("PowParam requires base and parameter terminal")
        return {"op": "PowParam", "args": args}
    if op in {"Dx", "Dt"}:
        if len(args) != 1:
            raise ValueError(f"{op} requires one argument")
        child = args[0]
        if child.get("op") in {"Const", "Theta"}:
            return Const(0)
        if child.get("op") == "Var":
            name = child["name"]
            if op == "Dx" and name == "x":
                return Const(1)
            if op == "Dt" and name == "t":
                return Const(1)
            if name in {"x", "t"}:
                return Const(0)
        return {"op": op, "args": [child]}
    raise ValueError(f"Unknown AST operator: {op}")


def normalize(node: Node) -> Node:
    previous = None
    current = copy.deepcopy(node)
    for _ in range(20):
        current = _normalize_once(current)
        serial = canonical_serialization(current)
        if serial == previous:
            return current
        previous = serial
    raise RuntimeError("AST normalization did not converge")


def walk_preorder(node: Node) -> Iterable[Node]:
    yield node
    for child in node.get("args", []):
        yield from walk_preorder(child)


def renumber_parameter_pair(x_node: Node, t_node: Node) -> tuple[Node, Node, dict[str, str]]:
    x_norm = normalize(x_node)
    t_norm = normalize(t_node)
    mapping: dict[str, str] = {}
    next_id = 1

    def replace(node: Node) -> Node:
        nonlocal next_id
        if node.get("op") == "Theta":
            old = str(node["name"])
            if old not in mapping:
                mapping[old] = f"theta_{next_id}"
                next_id += 1
            return Theta(mapping[old])
        out = {key: copy.deepcopy(value) for key, value in node.items() if key != "args"}
        if "args" in node:
            out["args"] = [replace(child) for child in node["args"]]
        return out

    return normalize(replace(x_norm)), normalize(replace(t_norm)), mapping


def node_count(node: Node) -> int:
    return sum(1 for _ in walk_preorder(node))


def depth(node: Node) -> int:
    args = node.get("args", [])
    return 1 if not args else 1 + max(depth(child) for child in args)


def parameter_names(node: Node) -> set[str]:
    return {str(n["name"]) for n in walk_preorder(node) if n.get("op") == "Theta"}


def parameter_occurrences(node: Node) -> int:
    return sum(1 for n in walk_preorder(node) if n.get("op") == "Theta")


def derivative_nesting(node: Node, current: int = 0) -> int:
    op = node.get("op")
    next_current = current + 1 if op in {"Dx", "Dt"} else current
    best = next_current
    for child in node.get("args", []):
        best = max(best, derivative_nesting(child, next_current))
    return best


def pair_stats(x_node: Node, t_node: Node) -> dict[str, int]:
    px = parameter_names(x_node)
    pt = parameter_names(t_node)
    return {
        "nodes_X": node_count(x_node),
        "nodes_T": node_count(t_node),
        "total_nodes": node_count(x_node) + node_count(t_node),
        "depth_X": depth(x_node),
        "depth_T": depth(t_node),
        "depth_max": max(depth(x_node), depth(t_node)),
        "unique_theta_X": len(px),
        "unique_theta_T": len(pt),
        "unique_theta_total": len(px | pt),
        "theta_occurrences_total": parameter_occurrences(x_node) + parameter_occurrences(t_node),
        "derivative_nesting_max": max(derivative_nesting(x_node), derivative_nesting(t_node)),
    }


def check_pair_caps(x_node: Node, t_node: Node, caps: dict[str, int]) -> tuple[bool, list[str], dict[str, int]]:
    stats = pair_stats(x_node, t_node)
    checks = {
        "nodes_X": "nodes_X_max",
        "nodes_T": "nodes_T_max",
        "total_nodes": "total_nodes_max",
        "depth_max": "depth_max",
        "unique_theta_total": "unique_theta_total_max",
        "theta_occurrences_total": "theta_occurrences_total_max",
        "derivative_nesting_max": "derivative_nesting_max",
    }
    failures = [f"{stat}>{cap}" for stat, cap in checks.items() if stats[stat] > int(caps[cap])]
    if stats["unique_theta_X"] > int(caps["unique_theta_component_max"]):
        failures.append("unique_theta_X>unique_theta_component_max")
    if stats["unique_theta_T"] > int(caps["unique_theta_component_max"]):
        failures.append("unique_theta_T>unique_theta_component_max")
    return not failures, failures, stats


def operator_multiset(node: Node) -> Counter[str]:
    return Counter(str(n.get("op")) for n in walk_preorder(node))


def build_low_order_scalar_census() -> list[dict[str, Any]]:
    terminals = [Var("x"), Var("t"), Var("a"), Var("q"), Const(-1), Const(0), Const(1), Theta("theta_1")]
    raw: list[Node] = [copy.deepcopy(node) for node in terminals]
    for unary in ["Inv", "Sqrt", "Log"]:
        raw.extend(Op(unary, term) for term in terminals)
    for binary in ["Add", "Sub", "Mul", "Div"]:
        raw.extend(Op(binary, left, right) for left in terminals for right in terminals)
    for exponent in [-1, 2, 3]:
        raw.extend(Op("PowInt", term, exponent=exponent) for term in terminals)
    raw.extend(Op("PowParam", term, Theta("theta_1")) for term in terminals if term.get("op") != "Theta")

    unique: dict[str, Node] = {}
    for node in raw:
        normalized = normalize(node)
        serial = canonical_serialization(normalized)
        unique.setdefault(serial, normalized)
    rows: list[dict[str, Any]] = []
    for index, serial in enumerate(sorted(unique)):
        node = unique[serial]
        rows.append({
            "fixture_id": f"L0S{index:04d}",
            "fixture_scope": "single_scalar_AST_only_not_XT_pair",
            "formal_candidate": False,
            "eligible_for_parent_pool": False,
            "canonical_ast": node,
            "canonical_serialization": serial,
            "nodes": node_count(node),
            "depth": depth(node),
            "unique_theta": len(parameter_names(node)),
            "derivative_nesting": derivative_nesting(node),
        })
    return rows
