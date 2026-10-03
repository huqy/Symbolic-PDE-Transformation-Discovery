#!/usr/bin/env python3
from __future__ import annotations

import copy
import math
from dataclasses import dataclass
from typing import Any, Iterable

import numpy as np

from p11rawxt_ast import Const, Node, Op, Theta, Var, parameter_names
from p11rawxt_s1.k1_representation import canonicalize_pair

Array = np.ndarray
MultiIndex = tuple[int, int]


def _indices(order: int) -> list[MultiIndex]:
    return [(i, j) for total in range(order + 1) for i in range(total + 1) for j in [total - i]]


@dataclass
class TaylorJet2D:
    coeff: dict[MultiIndex, Array]
    order: int = 3

    @property
    def shape(self) -> tuple[int, ...]:
        return self.coeff[(0, 0)].shape

    def copy(self) -> "TaylorJet2D":
        return TaylorJet2D({key: value.copy() for key, value in self.coeff.items()}, self.order)

    @classmethod
    def constant(cls, value: float | Array, shape: tuple[int, ...], order: int = 3) -> "TaylorJet2D":
        base = np.broadcast_to(np.asarray(value, dtype=np.float64), shape).copy()
        coeff = {idx: np.zeros(shape, dtype=np.float64) for idx in _indices(order)}
        coeff[(0, 0)] = base
        return cls(coeff, order)

    def __add__(self, other: "TaylorJet2D") -> "TaylorJet2D":
        return TaylorJet2D({idx: self.coeff[idx] + other.coeff[idx] for idx in _indices(self.order)}, self.order)

    def __neg__(self) -> "TaylorJet2D":
        return TaylorJet2D({idx: -self.coeff[idx] for idx in _indices(self.order)}, self.order)

    def __sub__(self, other: "TaylorJet2D") -> "TaylorJet2D":
        return self + (-other)

    def __mul__(self, other: "TaylorJet2D") -> "TaylorJet2D":
        out = {idx: np.zeros(self.shape, dtype=np.float64) for idx in _indices(self.order)}
        for i, j in _indices(self.order):
            acc = out[(i, j)]
            for a in range(i + 1):
                for b in range(j + 1):
                    acc += self.coeff[(a, b)] * other.coeff[(i - a, j - b)]
        return TaylorJet2D(out, self.order)

    def scalar_mul(self, value: float) -> "TaylorJet2D":
        return TaylorJet2D({idx: value * self.coeff[idx] for idx in _indices(self.order)}, self.order)

    def inverse(self) -> "TaylorJet2D":
        a0 = self.coeff[(0, 0)]
        if not np.all(np.isfinite(a0)) or np.any(a0 == 0.0):
            raise FloatingPointError("inverse domain failure")
        out = {idx: np.zeros(self.shape, dtype=np.float64) for idx in _indices(self.order)}
        out[(0, 0)] = 1.0 / a0
        for total in range(1, self.order + 1):
            for i in range(total + 1):
                j = total - i
                acc = np.zeros(self.shape, dtype=np.float64)
                for a in range(i + 1):
                    for b in range(j + 1):
                        if a == 0 and b == 0:
                            continue
                        acc += self.coeff[(a, b)] * out[(i - a, j - b)]
                out[(i, j)] = -acc / a0
        return TaylorJet2D(out, self.order)

    def log(self) -> "TaylorJet2D":
        a0 = self.coeff[(0, 0)]
        if not np.all(np.isfinite(a0)) or np.any(a0 <= 0.0):
            raise FloatingPointError("log domain failure")
        base = TaylorJet2D.constant(a0, self.shape, self.order)
        u = (self - base) * base.inverse()
        out = TaylorJet2D.constant(np.log(a0), self.shape, self.order)
        power = TaylorJet2D.constant(1.0, self.shape, self.order)
        for k in range(1, self.order + 1):
            power = power * u
            out = out + power.scalar_mul((1.0 if k % 2 else -1.0) / float(k))
        return out

    def exp(self) -> "TaylorJet2D":
        a0 = self.coeff[(0, 0)]
        if not np.all(np.isfinite(a0)):
            raise FloatingPointError("exp nonfinite base")
        base = TaylorJet2D.constant(a0, self.shape, self.order)
        u = self - base
        series = TaylorJet2D.constant(1.0, self.shape, self.order)
        power = TaylorJet2D.constant(1.0, self.shape, self.order)
        factorial = 1.0
        for k in range(1, self.order + 1):
            power = power * u
            factorial *= k
            series = series + power.scalar_mul(1.0 / factorial)
        return series.scalar_mul(1.0) * TaylorJet2D.constant(np.exp(a0), self.shape, self.order)

    def pow_scalar(self, exponent: float) -> "TaylorJet2D":
        if exponent == 0.0:
            return TaylorJet2D.constant(1.0, self.shape, self.order)
        if float(exponent).is_integer():
            n = int(exponent)
            if n < 0:
                return self.pow_scalar(-n).inverse()
            result = TaylorJet2D.constant(1.0, self.shape, self.order)
            base = self
            while n:
                if n & 1:
                    result = result * base
                n >>= 1
                if n:
                    base = base * base
            return result
        return self.log().scalar_mul(float(exponent)).exp()

    def derivative(self, axis: str) -> "TaylorJet2D":
        out = {idx: np.zeros(self.shape, dtype=np.float64) for idx in _indices(self.order)}
        for i, j in _indices(self.order):
            if axis == "x" and i + 1 + j <= self.order:
                out[(i, j)] = (i + 1) * self.coeff[(i + 1, j)]
            elif axis == "t" and i + j + 1 <= self.order:
                out[(i, j)] = (j + 1) * self.coeff[(i, j + 1)]
        return TaylorJet2D(out, self.order)


def _terminal_jet(name: str, x: Array, t: Array, theta: dict[str, float], order: int) -> TaylorJet2D:
    shape = x.shape
    if name == "x":
        jet = TaylorJet2D.constant(x, shape, order)
        jet.coeff[(1, 0)] = np.ones(shape, dtype=np.float64)
        return jet
    if name == "t":
        jet = TaylorJet2D.constant(t, shape, order)
        jet.coeff[(0, 1)] = np.ones(shape, dtype=np.float64)
        return jet
    if name == "a":
        jet = TaylorJet2D.constant(1.0 + 0.5 * x * t, shape, order)
        jet.coeff[(1, 0)] = 0.5 * t
        jet.coeff[(0, 1)] = 0.5 * x
        jet.coeff[(1, 1)] = 0.5 * np.ones(shape, dtype=np.float64)
        return jet
    if name == "q":
        return TaylorJet2D.constant(1.0, shape, order)
    if name.startswith("theta_"):
        if name not in theta:
            raise KeyError(f"Missing parameter value: {name}")
        return TaylorJet2D.constant(theta[name], shape, order)
    raise ValueError(f"Unsupported terminal {name}")


def evaluate_ast_jet(node: Node, x: Array, t: Array, theta: dict[str, float], order: int = 3) -> TaylorJet2D:
    op = node.get("op")
    if op == "Var":
        return _terminal_jet(str(node["name"]), x, t, theta, order)
    if op == "Const":
        return TaylorJet2D.constant(float(node["value"]), x.shape, order)
    if op == "Theta":
        return _terminal_jet(str(node["name"]), x, t, theta, order)
    args = node.get("args", [])
    if op == "Neg":
        return -evaluate_ast_jet(args[0], x, t, theta, order)
    if op == "Add":
        result = TaylorJet2D.constant(0.0, x.shape, order)
        for child in args:
            result = result + evaluate_ast_jet(child, x, t, theta, order)
        return result
    if op == "Mul":
        result = TaylorJet2D.constant(1.0, x.shape, order)
        for child in args:
            result = result * evaluate_ast_jet(child, x, t, theta, order)
        return result
    if op == "Inv":
        return evaluate_ast_jet(args[0], x, t, theta, order).inverse()
    if op == "Sqrt":
        return evaluate_ast_jet(args[0], x, t, theta, order).pow_scalar(0.5)
    if op == "Log":
        return evaluate_ast_jet(args[0], x, t, theta, order).log()
    if op == "PowInt":
        return evaluate_ast_jet(args[0], x, t, theta, order).pow_scalar(float(node["exponent"]))
    if op == "PowParam":
        exponent_node = args[1]
        exponent_name = str(exponent_node["name"])
        if exponent_name not in theta:
            raise KeyError(exponent_name)
        return evaluate_ast_jet(args[0], x, t, theta, order).pow_scalar(float(theta[exponent_name]))
    if op == "Dx":
        return evaluate_ast_jet(args[0], x, t, theta, order).derivative("x")
    if op == "Dt":
        return evaluate_ast_jet(args[0], x, t, theta, order).derivative("t")
    raise ValueError(f"Unsupported normalized AST operator: {op}")


def evaluate_pair_jet(raw_x: Node, raw_t: Node, theta_vector: Iterable[float], x: Array, t: Array) -> dict[str, Array]:
    names = sorted(parameter_names(raw_x) | parameter_names(raw_t), key=lambda value: int(value.split("_")[1]))
    values = list(theta_vector)
    if len(names) != len(values):
        raise ValueError(f"Parameter length mismatch names={names} values={values}")
    theta = dict(zip(names, (float(value) for value in values)))
    xjet = evaluate_ast_jet(raw_x, x, t, theta, order=3)
    tjet = evaluate_ast_jet(raw_t, x, t, theta, order=3)
    factor = {(0, 0): 1.0, (1, 0): 1.0, (0, 1): 1.0, (2, 0): 2.0, (1, 1): 1.0, (0, 2): 2.0}
    def derivative(jet: TaylorJet2D, idx: MultiIndex) -> Array:
        return factor[idx] * jet.coeff[idx]
    return {
        "X": derivative(xjet, (0, 0)), "T": derivative(tjet, (0, 0)),
        "Xx": derivative(xjet, (1, 0)), "Xt": derivative(xjet, (0, 1)),
        "Tx": derivative(tjet, (1, 0)), "Tt": derivative(tjet, (0, 1)),
        "Xxx": derivative(xjet, (2, 0)), "Xxt": derivative(xjet, (1, 1)), "Xtt": derivative(xjet, (0, 2)),
        "Txx": derivative(tjet, (2, 0)), "Txt": derivative(tjet, (1, 1)), "Ttt": derivative(tjet, (0, 2)),
    }


def parameter_count(pair: dict[str, Any]) -> int:
    return len(parameter_names(pair["raw_X_AST"]) | parameter_names(pair["raw_T_AST"]))


def _terminal(rng: np.random.Generator, theta_cap: int) -> Node:
    choices: list[Node] = [Var("x"), Var("t"), Var("a"), Var("q"), Const(-1), Const(0), Const(1)]
    if theta_cap > 0:
        choices.extend(Theta(f"theta_{index}") for index in range(1, theta_cap + 1))
    return copy.deepcopy(choices[int(rng.integers(0, len(choices)))])


def random_ast(rng: np.random.Generator, max_nodes: int, max_depth: int, theta_cap: int, derivative_nesting_max: int, derivative_level: int = 0) -> Node:
    if max_nodes <= 1 or max_depth <= 1:
        return _terminal(rng, theta_cap)
    categories = ["terminal", "unary", "powint"]
    if max_nodes >= 3:
        categories.extend(["binary", "powparam"])
    if derivative_level < derivative_nesting_max:
        categories.append("derivative")
    category = categories[int(rng.integers(0, len(categories)))]
    if category == "terminal":
        return _terminal(rng, theta_cap)
    if category in {"unary", "powint", "derivative"}:
        child = random_ast(rng, max_nodes - 1, max_depth - 1, theta_cap, derivative_nesting_max, derivative_level + (category == "derivative"))
        if category == "unary":
            return Op(["Inv", "Sqrt", "Log"][int(rng.integers(0, 3))], child)
        if category == "powint":
            exponent = [-3, -2, -1, 2, 3][int(rng.integers(0, 5))]
            return Op("PowInt", child, exponent=exponent)
        return Op("Dx" if int(rng.integers(0, 2)) == 0 else "Dt", child)
    if category == "powparam":
        if theta_cap <= 0:
            return _terminal(rng, theta_cap)
        base = random_ast(rng, max_nodes - 2, max_depth - 1, theta_cap, derivative_nesting_max, derivative_level)
        return Op("PowParam", base, Theta(f"theta_{int(rng.integers(1, theta_cap + 1))}"))
    left_budget = int(rng.integers(1, max_nodes - 1))
    right_budget = max(1, max_nodes - 1 - left_budget)
    left = random_ast(rng, left_budget, max_depth - 1, theta_cap, derivative_nesting_max, derivative_level)
    right = random_ast(rng, right_budget, max_depth - 1, theta_cap, derivative_nesting_max, derivative_level)
    return Op(["Add", "Sub", "Mul", "Div"][int(rng.integers(0, 4))], left, right)


def random_pair(rng: np.random.Generator, caps: dict[str, int], max_attempts: int = 256) -> dict[str, Any]:
    for _ in range(max_attempts):
        total_target = int(rng.integers(2, int(caps["total_nodes_max"]) + 1))
        x_budget = int(rng.integers(1, min(int(caps["nodes_X_max"]), total_target - 1) + 1))
        t_budget = max(1, min(int(caps["nodes_T_max"]), total_target - x_budget))
        raw_x = random_ast(rng, x_budget, int(caps["depth_max"]), int(caps["unique_theta_total_max"]), int(caps["derivative_nesting_max"]))
        raw_t = random_ast(rng, t_budget, int(caps["depth_max"]), int(caps["unique_theta_total_max"]), int(caps["derivative_nesting_max"]))
        pair = canonicalize_pair(raw_x, raw_t, caps)
        if pair["raw_grammar_valid"] and pair["caps_valid"] and not pair["static_domain_impossible"]:
            return pair
    raise RuntimeError("Could not generate a legal primitive pair within max_attempts")


def subtree_positions(node: Node) -> list[tuple[int, ...]]:
    return [site["position"] for site in typed_subtree_sites(node)]


def _raw_grammar_expansion(node: Node) -> Node:
    """Return a deterministic raw-grammar tree for a canonical AST.

    K1 stores normalized ASTs.  Normalization may introduce ``Neg`` and
    variadic ``Add``/``Mul`` nodes, while the frozen proposal grammar is binary
    and contains ``Sub`` rather than ``Neg``.  Genetic operators must therefore
    expand the canonical tree back to an equivalent raw-grammar tree before
    editing it.  Re-canonicalization then recovers the same scientific
    representation and structural hash.
    """
    op = node.get("op")
    if op in {"Var", "Const", "Theta"}:
        return copy.deepcopy(node)
    args = [_raw_grammar_expansion(child) for child in node.get("args", [])]
    if op == "Neg":
        if len(args) != 1:
            raise ValueError("Canonical Neg requires one argument")
        return Op("Sub", Const(0), args[0])
    if op in {"Add", "Mul"}:
        if not args:
            return Const(0 if op == "Add" else 1)
        current = args[0]
        for child in args[1:]:
            current = Op(op, current, child)
        return current
    if op in {"Inv", "Sqrt", "Log", "Dx", "Dt"}:
        if len(args) != 1:
            raise ValueError(f"Canonical {op} requires one argument")
        return Op(op, args[0])
    if op == "PowInt":
        if len(args) != 1:
            raise ValueError("Canonical PowInt requires one argument")
        return Op("PowInt", args[0], exponent=int(node["exponent"]))
    if op == "PowParam":
        if len(args) != 2 or args[1].get("op") != "Theta":
            raise ValueError("Canonical PowParam requires base and parameter terminal")
        return Op("PowParam", args[0], args[1])
    raise ValueError(f"Unsupported canonical AST operator during raw expansion: {op}")


def typed_subtree_sites(node: Node) -> list[dict[str, Any]]:
    """Enumerate subtree sites with the grammar role required at each site.

    Most child slots accept any scalar expression.  The second child of
    ``PowParam`` is different: it must remain a ``Theta`` terminal.  Treating
    these slots as untyped was the cause of the 50,000-call K2 crash.
    """
    sites: list[dict[str, Any]] = []

    def visit(current: Node, position: tuple[int, ...], required_kind: str) -> None:
        sites.append({"position": position, "required_kind": required_kind})
        args = current.get("args", [])
        if current.get("op") == "PowParam":
            if len(args) != 2:
                raise ValueError("PowParam requires two children while enumerating subtree sites")
            visit(args[0], position + (0,), "expression")
            visit(args[1], position + (1,), "theta_terminal")
            return
        for index, child in enumerate(args):
            visit(child, position + (index,), "expression")

    visit(node, (), "expression")
    return sites


def subtree_at(node: Node, position: tuple[int, ...]) -> Node:
    current = node
    for index in position:
        current = current["args"][index]
    return copy.deepcopy(current)


def replace_subtree(node: Node, position: tuple[int, ...], replacement: Node) -> Node:
    if not position:
        return copy.deepcopy(replacement)
    out = copy.deepcopy(node)
    current = out
    for index in position[:-1]:
        current = current["args"][index]
    current["args"][position[-1]] = copy.deepcopy(replacement)
    return out


def mutate_pair(parent: dict[str, Any], rng: np.random.Generator, caps: dict[str, int], max_attempts: int = 128) -> tuple[dict[str, Any], dict[str, Any]]:
    for _ in range(max_attempts):
        component = "X" if int(rng.integers(0, 2)) == 0 else "T"
        key = "raw_X_AST" if component == "X" else "raw_T_AST"
        base_x = _raw_grammar_expansion(parent["raw_X_AST"])
        base_t = _raw_grammar_expansion(parent["raw_T_AST"])
        source = base_x if component == "X" else base_t
        sites = typed_subtree_sites(source)
        site = sites[int(rng.integers(0, len(sites)))]
        position = site["position"]
        if site["required_kind"] == "theta_terminal":
            replacement = Theta(f"theta_{int(rng.integers(1, int(caps['unique_theta_total_max']) + 1))}")
        else:
            replacement_budget = int(rng.integers(1, 1 + (int(caps["nodes_X_max"]) if component == "X" else int(caps["nodes_T_max"]))))
            replacement = random_ast(rng, replacement_budget, int(caps["depth_max"]), int(caps["unique_theta_total_max"]), int(caps["derivative_nesting_max"]))
        raw_x = replace_subtree(base_x, position, replacement) if component == "X" else base_x
        raw_t = replace_subtree(base_t, position, replacement) if component == "T" else base_t
        try:
            child = canonicalize_pair(raw_x, raw_t, caps)
        except (ValueError, RuntimeError):
            continue
        if child["raw_grammar_valid"] and child["caps_valid"] and not child["static_domain_impossible"]:
            return child, {
                "component": component,
                "subtree_positions": [list(position)],
                "replacement_hash": child["structural_hash"],
                "recipient_site_kind": site["required_kind"],
            }
    raise RuntimeError("Mutation failed to produce a legal child")


def crossover_pair(parent_a: dict[str, Any], parent_b: dict[str, Any], rng: np.random.Generator, caps: dict[str, int], max_attempts: int = 128) -> tuple[dict[str, Any], dict[str, Any]]:
    for _ in range(max_attempts):
        component = "X" if int(rng.integers(0, 2)) == 0 else "T"
        key = "raw_X_AST" if component == "X" else "raw_T_AST"
        base_a_x = _raw_grammar_expansion(parent_a["raw_X_AST"])
        base_a_t = _raw_grammar_expansion(parent_a["raw_T_AST"])
        base_b_x = _raw_grammar_expansion(parent_b["raw_X_AST"])
        base_b_t = _raw_grammar_expansion(parent_b["raw_T_AST"])
        source_a = base_a_x if component == "X" else base_a_t
        source_b = base_b_x if component == "X" else base_b_t
        sites_a = typed_subtree_sites(source_a)
        site_a = sites_a[int(rng.integers(0, len(sites_a)))]
        donor_sites = typed_subtree_sites(source_b)
        if site_a["required_kind"] == "theta_terminal":
            donor_sites = [site for site in donor_sites if subtree_at(source_b, site["position"]).get("op") == "Theta"]
            if not donor_sites:
                continue
        site_b = donor_sites[int(rng.integers(0, len(donor_sites)))]
        pos_a = site_a["position"]
        pos_b = site_b["position"]
        replacement = subtree_at(source_b, pos_b)
        raw_x = replace_subtree(base_a_x, pos_a, replacement) if component == "X" else base_a_x
        raw_t = replace_subtree(base_a_t, pos_a, replacement) if component == "T" else base_a_t
        try:
            child = canonicalize_pair(raw_x, raw_t, caps)
        except (ValueError, RuntimeError):
            continue
        if child["raw_grammar_valid"] and child["caps_valid"] and not child["static_domain_impossible"]:
            return child, {
                "component": component,
                "subtree_positions": [list(pos_a), list(pos_b)],
                "replacement_hash": parent_b["structural_hash"],
                "recipient_site_kind": site_a["required_kind"],
                "donor_site_kind": site_b["required_kind"],
            }
    raise RuntimeError("Crossover failed to produce a legal child")
