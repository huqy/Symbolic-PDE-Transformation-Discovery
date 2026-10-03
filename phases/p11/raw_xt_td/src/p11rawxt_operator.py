#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Any, Iterable

import numpy as np
from numpy.polynomial.legendre import Legendre

from p11rawxt_validity import GridSpec, benchmark_a, common_scale_translation_gauge

Array = np.ndarray


@dataclass(frozen=True)
class OperatorEvaluation:
    J_princ: float
    J_XT: float
    J_XX: float
    mass_condition_number: float
    eigen_residual_princ: float
    eigen_residual_XT: float
    eigen_residual_XX: float
    dimension: int
    grid_shape: tuple[int, int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "J_princ": self.J_princ,
            "J_XT": self.J_XT,
            "J_XX": self.J_XX,
            "mass_condition_number": self.mass_condition_number,
            "eigen_residual_princ": self.eigen_residual_princ,
            "eigen_residual_XT": self.eigen_residual_XT,
            "eigen_residual_XX": self.eigen_residual_XX,
            "dimension": self.dimension,
            "grid_shape": list(self.grid_shape),
        }


def tensor_trapezoid_weights(x: Array, t: Array) -> Array:
    if x.ndim != 1 or t.ndim != 1 or len(x) < 2 or len(t) < 2:
        raise ValueError("x and t must be one-dimensional grids with at least two points")
    wx = np.empty_like(x, dtype=np.float64)
    wt = np.empty_like(t, dtype=np.float64)
    wx[0] = 0.5 * (x[1] - x[0])
    wx[-1] = 0.5 * (x[-1] - x[-2])
    wx[1:-1] = 0.5 * (x[2:] - x[:-2])
    wt[0] = 0.5 * (t[1] - t[0])
    wt[-1] = 0.5 * (t[-1] - t[-2])
    wt[1:-1] = 0.5 * (t[2:] - t[:-2])
    return wx[:, None] * wt[None, :]


def gauge_second_jet(raw: dict[str, Array]) -> tuple[dict[str, Array] | None, dict[str, Any]]:
    first = {key: raw[key] for key in ["X", "T", "Xx", "Xt", "Tx", "Tt"]}
    gauged_first, record = common_scale_translation_gauge(first)
    if gauged_first is None:
        return None, record
    scale = float(record["common_positive_scale"])
    gauged = dict(gauged_first)
    for key in ["Xxx", "Xxt", "Xtt", "Txx", "Txt", "Ttt"]:
        if key not in raw:
            raise KeyError(f"Missing second-jet array: {key}")
        gauged[key] = np.asarray(raw[key], dtype=np.float64) / scale
    return gauged, record


def pushforward_coefficients(
    gauged: dict[str, Array],
    x_mesh: Array,
    t_mesh: Array,
    *,
    medium: str = "fixed",
    ctt_floor: float = 1e-12,
) -> dict[str, Array]:
    required = ["X", "T", "Xx", "Xt", "Tx", "Tt", "Xxx", "Xtt", "Txx", "Ttt"]
    missing = [key for key in required if key not in gauged]
    if missing:
        raise KeyError(f"Missing map arrays: {missing}")
    if medium == "fixed":
        a = benchmark_a(x_mesh, t_mesh)
        ax = 0.5 * t_mesh
        q = np.ones_like(x_mesh)
    elif medium == "constant":
        a = np.ones_like(x_mesh)
        ax = np.zeros_like(x_mesh)
        q = np.ones_like(x_mesh)
    else:
        raise ValueError(f"Unsupported medium: {medium}")

    Xx, Xt = gauged["Xx"], gauged["Xt"]
    Tx, Tt = gauged["Tx"], gauged["Tt"]
    C_TT = Tt * Tt - a * Tx * Tx
    C_XT = 2.0 * (Xt * Tt - a * Xx * Tx)
    C_XX = Xt * Xt - a * Xx * Xx
    L_T = gauged["Ttt"] - a * gauged["Txx"] - ax * Tx
    L_X = gauged["Xtt"] - a * gauged["Xxx"] - ax * Xx
    if not np.all(np.isfinite(C_TT)) or float(np.min(C_TT)) <= ctt_floor:
        raise ValueError(f"C_TT fails positive floor {ctt_floor}")
    m = C_XT / C_TT
    r = (C_TT + C_XX) / C_TT
    normalized = {
        "w_TT": np.ones_like(C_TT),
        "w_XT": m,
        "w_XX": C_XX / C_TT,
        "w_T": L_T / C_TT,
        "w_X": L_X / C_TT,
        "w": q / C_TT,
        "forcing_factor": 1.0 / C_TT,
    }
    reduced = {
        "w_TT": np.ones_like(C_TT),
        "w_XT": np.zeros_like(C_TT),
        "w_XX": -np.ones_like(C_TT),
        "w_T": L_T / C_TT,
        "w_X": L_X / C_TT,
        "w": q / C_TT,
        "forcing_factor": 1.0 / C_TT,
    }
    return {
        "a": a,
        "a_x": ax,
        "q": q,
        "J_Phi": Xx * Tt - Xt * Tx,
        "C_TT": C_TT,
        "C_XT": C_XT,
        "C_XX": C_XX,
        "L_T": L_T,
        "L_X": L_X,
        "m": m,
        "r": r,
        "full_normalized": normalized,
        "reduced": reduced,
    }


def _legendre_values_and_derivatives(n: int, T: Array) -> tuple[Array, Array]:
    polynomial = Legendre.basis(n)
    z = 2.0 * T - 1.0
    scale = np.sqrt(2.0 * n + 1.0)
    value = scale * polynomial(z)
    derivative = scale * 2.0 * polynomial.deriv(1)(z) if n > 0 else np.zeros_like(T)
    return value, derivative


def build_sine_legendre_basis(X: Array, T: Array, m_values: Iterable[int], n_values: Iterable[int]) -> dict[str, Array]:
    fields: dict[str, list[Array]] = {name: [] for name in ["w", "wX", "wT", "wXT", "wXX"]}
    for m in m_values:
        k = float(m) * np.pi
        sinx = np.sin(k * X)
        cosx = np.cos(k * X)
        for n in n_values:
            lt, dlt = _legendre_values_and_derivatives(int(n), T)
            fields["w"].append(sinx * lt)
            fields["wX"].append(k * cosx * lt)
            fields["wT"].append(sinx * dlt)
            fields["wXT"].append(k * cosx * dlt)
            fields["wXX"].append(-(k * k) * sinx * lt)
    return {name: np.stack(values, axis=0) for name, values in fields.items()}


def build_gaussian_basis(X: Array, T: Array, centers_x: Iterable[float], centers_t: Iterable[float], sigma: float) -> dict[str, Array]:
    if sigma <= 0.0:
        raise ValueError("sigma must be positive")
    inv_s2 = 1.0 / (sigma * sigma)
    inv_s4 = inv_s2 * inv_s2
    fields: dict[str, list[Array]] = {name: [] for name in ["w", "wX", "wT", "wXT", "wXX"]}
    for cx in centers_x:
        for ct in centers_t:
            dx = X - float(cx)
            dt = T - float(ct)
            phi = np.exp(-0.5 * (dx * dx + dt * dt) * inv_s2)
            fields["w"].append(phi)
            fields["wX"].append(-dx * inv_s2 * phi)
            fields["wT"].append(-dt * inv_s2 * phi)
            fields["wXT"].append(dx * dt * inv_s4 * phi)
            fields["wXX"].append((dx * dx * inv_s4 - inv_s2) * phi)
    return {name: np.stack(values, axis=0) for name, values in fields.items()}


def build_basis(space: dict[str, Any], X: Array, T: Array) -> dict[str, Array]:
    if space["name"].startswith("sine_shifted_legendre"):
        return build_sine_legendre_basis(X, T, space["m_values"], space["n_values"])
    if space["name"].startswith("fixed_gaussian_rbf"):
        return build_gaussian_basis(X, T, space["centers_X"], space["centers_T"], float(space["sigma"]))
    raise ValueError(f"Unknown test space: {space['name']}")


def weighted_gram(fields_left: Array, fields_right: Array, weights: Array) -> Array:
    return np.einsum("kij,lij,ij->kl", fields_left, fields_right, weights, optimize=True)


def graph_mass_matrix(basis: dict[str, Array], weights: Array) -> Array:
    B = np.zeros((basis["w"].shape[0], basis["w"].shape[0]), dtype=np.float64)
    for key in ["w", "wX", "wT", "wXT", "wXX"]:
        B += weighted_gram(basis[key], basis[key], weights)
    return 0.5 * (B + B.T)


def _generalized_largest_eigenvalue(N: Array, B: Array, relative_floor: float) -> tuple[float, float, float]:
    N = 0.5 * (N + N.T)
    B = 0.5 * (B + B.T)
    b_values = np.linalg.eigvalsh(B)
    b_max = float(np.max(b_values))
    b_min = float(np.min(b_values))
    if not np.isfinite(b_min) or b_min <= relative_floor * max(b_max, 1.0):
        raise np.linalg.LinAlgError(f"Graph mass matrix is not numerically positive definite: min={b_min}, max={b_max}")
    L = np.linalg.cholesky(B)
    transformed = np.linalg.solve(L, N)
    transformed = np.linalg.solve(L, transformed.T).T
    transformed = 0.5 * (transformed + transformed.T)
    values, vectors = np.linalg.eigh(transformed)
    eigenvalue = max(float(values[-1]), 0.0)
    y = vectors[:, -1]
    c = np.linalg.solve(L.T, y)
    residual = N @ c - eigenvalue * (B @ c)
    denom = max(float(np.linalg.norm(N @ c)), abs(eigenvalue) * float(np.linalg.norm(B @ c)), 1e-300)
    relative_residual = float(np.linalg.norm(residual) / denom)
    condition = float(b_max / b_min)
    return float(np.sqrt(eigenvalue)), relative_residual, condition


def evaluate_operator_norms(
    basis: dict[str, Array],
    m: Array,
    r: Array,
    measure_weights: Array,
    *,
    mass_relative_floor: float = 1e-12,
) -> OperatorEvaluation:
    B = graph_mass_matrix(basis, measure_weights)
    defect_joint = m[None, :, :] * basis["wXT"] + r[None, :, :] * basis["wXX"]
    defect_xt = m[None, :, :] * basis["wXT"]
    defect_xx = r[None, :, :] * basis["wXX"]
    N_joint = weighted_gram(defect_joint, defect_joint, measure_weights)
    N_xt = weighted_gram(defect_xt, defect_xt, measure_weights)
    N_xx = weighted_gram(defect_xx, defect_xx, measure_weights)
    J_princ, residual_princ, condition = _generalized_largest_eigenvalue(N_joint, B, mass_relative_floor)
    J_xt, residual_xt, condition_xt = _generalized_largest_eigenvalue(N_xt, B, mass_relative_floor)
    J_xx, residual_xx, condition_xx = _generalized_largest_eigenvalue(N_xx, B, mass_relative_floor)
    if max(abs(condition - condition_xt), abs(condition - condition_xx)) > 1e-8 * max(condition, 1.0):
        raise RuntimeError("Inconsistent graph mass condition number across direct channels")
    return OperatorEvaluation(
        J_princ=J_princ,
        J_XT=J_xt,
        J_XX=J_xx,
        mass_condition_number=condition,
        eigen_residual_princ=residual_princ,
        eigen_residual_XT=residual_xt,
        eigen_residual_XX=residual_xx,
        dimension=int(B.shape[0]),
        grid_shape=tuple(int(v) for v in m.shape),
    )


def evaluate_map_on_space(
    map_jet: dict[str, Array],
    grid: GridSpec,
    space: dict[str, Any],
    numerical: dict[str, Any],
    *,
    medium: str = "fixed",
) -> tuple[dict[str, Array], OperatorEvaluation, float]:
    start = perf_counter()
    x_mesh, t_mesh = grid.mesh
    coefficients = pushforward_coefficients(
        map_jet,
        x_mesh,
        t_mesh,
        medium=medium,
        ctt_floor=float(numerical["C_TT_absolute_floor"]),
    )
    if float(np.min(coefficients["J_Phi"])) <= 0.0:
        raise ValueError("Map Jacobian must remain positive before operator evaluation")
    basis = build_basis(space, map_jet["X"], map_jet["T"])
    source_weights = tensor_trapezoid_weights(grid.x, grid.t)
    transformed_measure = source_weights * coefficients["J_Phi"]
    result = evaluate_operator_norms(
        basis,
        coefficients["m"],
        coefficients["r"],
        transformed_measure,
        mass_relative_floor=float(numerical["mass_matrix_relative_eigen_floor"]),
    )
    return coefficients, result, perf_counter() - start


def relative_difference(a: float, b: float) -> float:
    return abs(float(a) - float(b)) / max(abs(float(a)), abs(float(b)), 1e-15)


def synthetic_operator_map_registry(x: Array, t: Array) -> dict[str, dict[str, Any]]:
    zero = np.zeros_like(x)
    one = np.ones_like(x)

    identity = {
        "X": x, "T": t,
        "Xx": one, "Xt": zero, "Tx": zero, "Tt": one,
        "Xxx": zero, "Xxt": zero, "Xtt": zero,
        "Txx": zero, "Txt": zero, "Ttt": zero,
    }

    translated_common_scale = {
        "X": 3.0 + 2.5 * x, "T": -4.0 + 2.5 * t,
        "Xx": 2.5 * one, "Xt": zero, "Tx": zero, "Tt": 2.5 * one,
        "Xxx": zero, "Xxt": zero, "Xtt": zero,
        "Txx": zero, "Txt": zero, "Ttt": zero,
    }

    beta = 0.2
    curved_initial = {
        "X": x,
        "T": t + beta * x * (1.0 - x),
        "Xx": one, "Xt": zero,
        "Tx": beta * (1.0 - 2.0 * x), "Tt": one,
        "Xxx": zero, "Xxt": zero, "Xtt": zero,
        "Txx": -2.0 * beta * one, "Txt": zero, "Ttt": zero,
    }

    alpha = 0.03
    gamma = 0.04
    nonlinear = {
        "X": x + alpha * np.sin(2.0 * np.pi * x) * np.sin(np.pi * t),
        "T": t + gamma * x * (1.0 - x),
        "Xx": 1.0 + 2.0 * np.pi * alpha * np.cos(2.0 * np.pi * x) * np.sin(np.pi * t),
        "Xt": np.pi * alpha * np.sin(2.0 * np.pi * x) * np.cos(np.pi * t),
        "Tx": gamma * (1.0 - 2.0 * x),
        "Tt": one,
        "Xxx": -(2.0 * np.pi) ** 2 * alpha * np.sin(2.0 * np.pi * x) * np.sin(np.pi * t),
        "Xxt": 2.0 * np.pi * np.pi * alpha * np.cos(2.0 * np.pi * x) * np.cos(np.pi * t),
        "Xtt": -(np.pi ** 2) * alpha * np.sin(2.0 * np.pi * x) * np.sin(np.pi * t),
        "Txx": -2.0 * gamma * one,
        "Txt": zero,
        "Ttt": zero,
    }

    return {
        "identity": {"raw": identity, "role": "fixed_medium_identity"},
        "translated_common_scale": {"raw": translated_common_scale, "role": "gauge_equivalence"},
        "curved_initial": {"raw": curved_initial, "role": "curved_Cauchy_operator_regression"},
        "nonlinear": {"raw": nonlinear, "role": "general_second_jet_regression"},
    }
