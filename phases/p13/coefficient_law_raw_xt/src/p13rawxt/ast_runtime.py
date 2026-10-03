from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import numpy as np
from scipy.interpolate import RegularGridInterpolator

Array = np.ndarray
MultiIndex = tuple[int, int]
Node = dict[str, Any]


def indices(order: int) -> list[MultiIndex]:
    return [(i, total - i) for total in range(order + 1) for i in range(total + 1)]


@dataclass
class TaylorJet2D:
    # Normalized Taylor coefficients: coeff[(i,j)] = d_x^i d_t^j f / (i! j!).
    coeff: dict[MultiIndex, Array]
    order: int

    @property
    def shape(self) -> tuple[int, ...]:
        return self.coeff[(0, 0)].shape

    @classmethod
    def constant(cls, value: float | Array, shape: tuple[int, ...], order: int) -> "TaylorJet2D":
        base = np.broadcast_to(np.asarray(value, dtype=np.float64), shape).copy()
        c = {idx: np.zeros(shape, dtype=np.float64) for idx in indices(order)}
        c[(0, 0)] = base
        return cls(c, order)

    def __add__(self, other: "TaylorJet2D") -> "TaylorJet2D":
        return TaylorJet2D({idx: self.coeff[idx] + other.coeff[idx] for idx in indices(self.order)}, self.order)

    def __neg__(self) -> "TaylorJet2D":
        return TaylorJet2D({idx: -self.coeff[idx] for idx in indices(self.order)}, self.order)

    def __sub__(self, other: "TaylorJet2D") -> "TaylorJet2D":
        return self + (-other)

    def __mul__(self, other: "TaylorJet2D") -> "TaylorJet2D":
        out = {idx: np.zeros(self.shape, dtype=np.float64) for idx in indices(self.order)}
        for i, j in indices(self.order):
            acc = out[(i, j)]
            for a in range(i + 1):
                for b in range(j + 1):
                    acc += self.coeff[(a, b)] * other.coeff[(i - a, j - b)]
        return TaylorJet2D(out, self.order)

    def scalar_mul(self, value: float) -> "TaylorJet2D":
        return TaylorJet2D({idx: float(value) * self.coeff[idx] for idx in indices(self.order)}, self.order)

    def inverse(self) -> "TaylorJet2D":
        a0 = self.coeff[(0, 0)]
        if np.any(~np.isfinite(a0)) or np.any(a0 == 0.0):
            raise FloatingPointError("inverse domain failure")
        out = {idx: np.zeros(self.shape, dtype=np.float64) for idx in indices(self.order)}
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
        if np.any(~np.isfinite(a0)) or np.any(a0 <= 0.0):
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
        if np.any(~np.isfinite(a0)):
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
        return series * TaylorJet2D.constant(np.exp(a0), self.shape, self.order)

    def pow_scalar(self, exponent: float) -> "TaylorJet2D":
        exponent = float(exponent)
        if exponent == 0.0:
            return TaylorJet2D.constant(1.0, self.shape, self.order)
        if exponent.is_integer():
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
        return self.log().scalar_mul(exponent).exp()

    def derivative(self, axis: str) -> "TaylorJet2D":
        out = {idx: np.zeros(self.shape, dtype=np.float64) for idx in indices(self.order)}
        for i, j in indices(self.order):
            if axis == "x" and i + j + 1 <= self.order:
                out[(i, j)] = (i + 1) * self.coeff[(i + 1, j)]
            elif axis == "t" and i + j + 1 <= self.order:
                out[(i, j)] = (j + 1) * self.coeff[(i, j + 1)]
        return TaylorJet2D(out, self.order)


class FieldJetInterpolator:
    """Interpolation-only production view of K1 search-facing coefficient jets.

    Formal search/evaluation sees only x,t,q and derivative arrays. Generator parameters
    never enter this object. G65 is used for off-grid inverse probes.
    """

    def __init__(self, arrays: dict[str, Array], order: int = 4):
        self.x = np.asarray(arrays["x"], dtype=np.float64)
        self.t = np.asarray(arrays["t"], dtype=np.float64)
        self.order = int(order)
        self._interpolators: dict[MultiIndex, RegularGridInterpolator] = {}
        for i, j in indices(self.order):
            key = f"a_d{i}_{j}"
            if key not in arrays:
                raise KeyError(f"search object missing {key}")
            self._interpolators[(i, j)] = RegularGridInterpolator(
                (self.x, self.t), np.asarray(arrays[key], dtype=np.float64),
                method="linear", bounds_error=True,
            )

    def derivatives(self, x: Array, t: Array) -> dict[MultiIndex, Array]:
        xx = np.asarray(x, dtype=np.float64)
        tt = np.asarray(t, dtype=np.float64)
        shape = np.broadcast_shapes(xx.shape, tt.shape)
        xx = np.broadcast_to(xx, shape); tt = np.broadcast_to(tt, shape)
        pts = np.column_stack([xx.ravel(), tt.ravel()])
        return {idx: interp(pts).reshape(shape) for idx, interp in self._interpolators.items()}


def _coefficient_terminal(derivatives: dict[MultiIndex, Array], order: int) -> TaylorJet2D:
    shape = derivatives[(0, 0)].shape
    out = {idx: np.zeros(shape, dtype=np.float64) for idx in indices(order)}
    import math
    for i, j in indices(order):
        if (i, j) not in derivatives:
            raise KeyError(f"coefficient derivative {(i,j)} unavailable")
        out[(i, j)] = np.asarray(derivatives[(i, j)], dtype=np.float64) / (math.factorial(i) * math.factorial(j))
    return TaylorJet2D(out, order)


def _terminal(name: str, x: Array, t: Array, theta: dict[str, float], coefficient_derivatives: dict[MultiIndex, Array], order: int) -> TaylorJet2D:
    shape = x.shape
    if name == "x":
        j = TaylorJet2D.constant(x, shape, order); j.coeff[(1, 0)] = np.ones(shape); return j
    if name == "t":
        j = TaylorJet2D.constant(t, shape, order); j.coeff[(0, 1)] = np.ones(shape); return j
    if name == "a":
        return _coefficient_terminal(coefficient_derivatives, order)
    if name == "q":
        return TaylorJet2D.constant(1.0, shape, order)
    if name.startswith("theta_"):
        return TaylorJet2D.constant(float(theta[name]), shape, order)
    raise ValueError(name)


def evaluate_ast_jet(node: Node, x: Array, t: Array, theta: dict[str, float], coefficient_derivatives: dict[MultiIndex, Array], order: int = 4) -> TaylorJet2D:
    op = node.get("op")
    if op == "Var":
        return _terminal(str(node["name"]), x, t, theta, coefficient_derivatives, order)
    if op == "Const":
        return TaylorJet2D.constant(float(node["value"]), x.shape, order)
    if op == "Theta":
        return _terminal(str(node["name"]), x, t, theta, coefficient_derivatives, order)
    args = node.get("args", [])
    if op == "Neg": return -evaluate_ast_jet(args[0], x, t, theta, coefficient_derivatives, order)
    if op == "Add":
        out = TaylorJet2D.constant(0.0, x.shape, order)
        for child in args: out = out + evaluate_ast_jet(child, x, t, theta, coefficient_derivatives, order)
        return out
    if op == "Mul":
        out = TaylorJet2D.constant(1.0, x.shape, order)
        for child in args: out = out * evaluate_ast_jet(child, x, t, theta, coefficient_derivatives, order)
        return out
    if op == "Inv": return evaluate_ast_jet(args[0], x, t, theta, coefficient_derivatives, order).inverse()
    if op == "Sqrt": return evaluate_ast_jet(args[0], x, t, theta, coefficient_derivatives, order).pow_scalar(0.5)
    if op == "Log": return evaluate_ast_jet(args[0], x, t, theta, coefficient_derivatives, order).log()
    if op == "PowInt": return evaluate_ast_jet(args[0], x, t, theta, coefficient_derivatives, order).pow_scalar(float(node["exponent"]))
    if op == "PowParam":
        name = str(args[1]["name"])
        return evaluate_ast_jet(args[0], x, t, theta, coefficient_derivatives, order).pow_scalar(float(theta[name]))
    if op == "Dx": return evaluate_ast_jet(args[0], x, t, theta, coefficient_derivatives, order).derivative("x")
    if op == "Dt": return evaluate_ast_jet(args[0], x, t, theta, coefficient_derivatives, order).derivative("t")
    raise ValueError(f"unsupported AST op {op}")


def _parameter_names(node: Node) -> set[str]:
    out: set[str] = set()
    stack = [node]
    while stack:
        cur = stack.pop()
        if cur.get("op") == "Theta": out.add(str(cur["name"]))
        stack.extend(cur.get("args", []))
    return out


def evaluate_pair_jet(raw_x: Node, raw_t: Node, theta_vector: Iterable[float], x: Array, t: Array, coefficient_derivatives: dict[MultiIndex, Array], order: int = 4) -> dict[str, Array]:
    names = sorted(_parameter_names(raw_x) | _parameter_names(raw_t), key=lambda v: int(v.split("_")[1]))
    values = [float(v) for v in theta_vector]
    if len(names) != len(values):
        raise ValueError(f"theta mismatch names={names} values={values}")
    theta = dict(zip(names, values))
    xj = evaluate_ast_jet(raw_x, x, t, theta, coefficient_derivatives, order)
    tj = evaluate_ast_jet(raw_t, x, t, theta, coefficient_derivatives, order)
    factor = {(0,0):1.0,(1,0):1.0,(0,1):1.0,(2,0):2.0,(1,1):1.0,(0,2):2.0}
    def d(jet: TaylorJet2D, idx: MultiIndex) -> Array:
        return factor[idx] * jet.coeff[idx]
    return {
        "X":d(xj,(0,0)),"T":d(tj,(0,0)),"Xx":d(xj,(1,0)),"Xt":d(xj,(0,1)),"Tx":d(tj,(1,0)),"Tt":d(tj,(0,1)),
        "Xxx":d(xj,(2,0)),"Xxt":d(xj,(1,1)),"Xtt":d(xj,(0,2)),"Txx":d(tj,(2,0)),"Txt":d(tj,(1,1)),"Ttt":d(tj,(0,2)),
    }


def grid_coefficient_derivatives(arrays: dict[str, Array], order: int = 4) -> dict[MultiIndex, Array]:
    return {(i,j): np.asarray(arrays[f"a_d{i}_{j}"], dtype=np.float64) for i,j in indices(order)}
