#!/usr/bin/env python3
from __future__ import annotations

import math
import time
from typing import Any

import numpy as np

from p11rawxt_operator import evaluate_map_on_space, gauge_second_jet
from p11rawxt_validity import (
    GridSpec,
    _boundary_polygon,
    _point_winding_number,
    _polygon_signed_area,
    benchmark_a,
    boundary_self_intersection_count,
    common_scale_translation_gauge,
    derivative_condition_numbers,
    mapped_cell_signed_areas,
)
from p11rawxt_s1.k2_ast_runtime import evaluate_pair_jet

Array = np.ndarray

STAGE_ORDER = {"NONE": 0, "F0": 1, "F1": 2, "F2": 3, "F3": 4, "F4": 5}


def _probe_source(grid: GridSpec, per_axis: int, local_coordinates: tuple[float, float]) -> Array:
    cell_i = np.unique(np.linspace(0, len(grid.x) - 2, per_axis, dtype=int))
    cell_j = np.unique(np.linspace(0, len(grid.t) - 2, per_axis, dtype=int))
    ux, ut = local_coordinates
    rows: list[tuple[float, float]] = []
    for i in cell_i:
        for j in cell_j:
            rows.append((
                float(grid.x[i] + ux * (grid.x[i + 1] - grid.x[i])),
                float(grid.t[j] + ut * (grid.t[j + 1] - grid.t[j])),
            ))
    return np.asarray(rows, dtype=np.float64)


def _triangles(gauged: dict[str, Array], grid: GridSpec) -> tuple[Array, Array]:
    X, T = gauged["X"], gauged["T"]
    sx, st = np.meshgrid(grid.x, grid.t, indexing="ij")
    target: list[Array] = []
    source: list[Array] = []
    for i in range(len(grid.x) - 1):
        for j in range(len(grid.t) - 1):
            tv = np.array([
                [X[i, j], T[i, j]], [X[i + 1, j], T[i + 1, j]],
                [X[i + 1, j + 1], T[i + 1, j + 1]], [X[i, j + 1], T[i, j + 1]],
            ], dtype=np.float64)
            sv = np.array([
                [sx[i, j], st[i, j]], [sx[i + 1, j], st[i + 1, j]],
                [sx[i + 1, j + 1], st[i + 1, j + 1]], [sx[i, j + 1], st[i, j + 1]],
            ], dtype=np.float64)
            target.extend([tv[[0, 1, 2]], tv[[0, 2, 3]]])
            source.extend([sv[[0, 1, 2]], sv[[0, 2, 3]]])
    return np.stack(target), np.stack(source)


def _cross2(a: Array, b: Array) -> Array:
    return a[..., 0] * b[..., 1] - a[..., 1] * b[..., 0]


def vectorized_inverse_evidence(
    gauged: dict[str, Array],
    grid: GridSpec,
    raw_probe_source: Array,
    raw_probe_target: Array,
    gauge_record: dict[str, Any],
    bary_tol: float,
    boundary_tol: float,
) -> dict[str, Any]:
    scale = float(gauge_record["common_positive_scale"])
    translation = np.asarray(gauge_record["raw_translation"], dtype=np.float64)
    targets = (raw_probe_target - translation[None, :]) / scale
    target_tri, source_tri = _triangles(gauged, grid)
    a = target_tri[:, 0, :]
    v0 = target_tri[:, 1, :] - a
    v1 = target_tri[:, 2, :] - a
    denom = _cross2(v0, v1)
    valid_tri = np.abs(denom) > bary_tol
    poly = _boundary_polygon(gauged["X"], gauged["T"])
    unique_counts: list[int] = []
    winding: list[int] = []
    errors: list[float] = []
    for point, source_true in zip(targets, raw_probe_source):
        winding.append(_point_winding_number(point, poly, boundary_tol))
        v2 = point[None, :] - a
        u = np.full(len(denom), np.nan)
        v = np.full(len(denom), np.nan)
        u[valid_tri] = _cross2(v2[valid_tri], v1[valid_tri]) / denom[valid_tri]
        v[valid_tri] = _cross2(v0[valid_tri], v2[valid_tri]) / denom[valid_tri]
        inside = valid_tri & (u >= -bary_tol) & (v >= -bary_tol) & (u + v <= 1.0 + bary_tol)
        ids = np.flatnonzero(inside)
        reconstructed: list[Array] = []
        for idx in ids:
            weights = np.array([1.0 - u[idx] - v[idx], u[idx], v[idx]])
            reconstructed.append(weights @ source_tri[idx])
        clusters: list[Array] = []
        for candidate in reconstructed:
            if not any(np.linalg.norm(candidate - old, ord=np.inf) <= 10.0 * bary_tol for old in clusters):
                clusters.append(candidate)
        unique_counts.append(len(clusters))
        errors.append(float(np.linalg.norm(clusters[0] - source_true, ord=np.inf)) if len(clusters) == 1 else float("inf"))
    return {
        "probe_count": int(len(raw_probe_source)),
        "unique_preimage_count_min": int(min(unique_counts)) if unique_counts else 0,
        "unique_preimage_count_max": int(max(unique_counts)) if unique_counts else 0,
        "all_unique": bool(unique_counts and all(value == 1 for value in unique_counts)),
        "winding_number_min": int(min(winding)) if winding else 0,
        "winding_number_max": int(max(winding)) if winding else 0,
        "degree_one_evidence": bool(winding and all(value == 1 for value in winding)),
        "all_inside_boundary": bool(winding and all(value != 0 for value in winding)),
        "roundtrip_error_max": float(max(errors)) if errors else float("inf"),
    }


def _result(highest: str, rejection: list[str], metrics: dict[str, Any], gauge: dict[str, Any] | None, gauged: dict[str, Array] | None) -> dict[str, Any]:
    ladder = {name: STAGE_ORDER[highest] >= index for name, index in [("F0", 1), ("F1", 2), ("F2", 3), ("F3", 4), ("F4", 5)]}
    return {
        "highest_feasibility_level": highest,
        "stage_index": STAGE_ORDER[highest],
        "feasibility_ladder": ladder,
        "overall_valid": highest == "F4",
        "rejection_codes": rejection,
        "metrics": metrics,
        "gauge_record": gauge or {},
        "gauged_map": gauged or {},
    }


def evaluate_validity_staged(
    raw: dict[str, Array],
    grid: GridSpec,
    raw_probe_source: Array,
    raw_probe_target: Array,
    numerical: dict[str, Any],
    inverse_roundtrip_tolerance: float,
) -> dict[str, Any]:
    required = ["X", "T", "Xx", "Xt", "Tx", "Tt"]
    expected_shape = (len(grid.x), len(grid.t))
    if any(key not in raw or np.asarray(raw[key]).shape != expected_shape for key in required):
        return _result("NONE", ["F0_SHAPE_MISMATCH"], {}, None, None)
    finite_count = sum(int(np.isfinite(raw[key]).sum()) for key in required)
    total_count = sum(int(np.asarray(raw[key]).size) for key in required)
    if finite_count != total_count:
        return _result("NONE", ["F0_NONFINITE_MAP_OR_DERIVATIVE"], {"finite_fraction": finite_count / max(total_count, 1)}, None, None)
    gauged, gauge_record = common_scale_translation_gauge({key: raw[key] for key in required})
    if gauged is None:
        return _result("NONE", [str(gauge_record.get("rejection_code", "GAUGE_FAILURE"))], {"finite_fraction": 1.0}, gauge_record, None)

    Xx, Xt, Tx, Tt = gauged["Xx"], gauged["Xt"], gauged["Tx"], gauged["Tt"]
    x_mesh, t_mesh = grid.mesh
    a = benchmark_a(x_mesh, t_mesh)
    J = Xx * Tt - Xt * Tx
    eps_o = float(numerical["absolute_orientation_margin"])
    metrics: dict[str, Any] = {"finite_fraction": 1.0, "J_min": float(np.min(J)), "Tt_min": float(np.min(Tt))}
    reject: list[str] = []
    if metrics["J_min"] <= eps_o:
        reject.append("F1_JACOBIAN_ORIENTATION_MARGIN")
    if metrics["Tt_min"] <= eps_o:
        reject.append("F1_FUTURE_ORIENTATION")
    if reject:
        return _result("F0", reject, metrics, gauge_record, gauged)

    eps_c = float(numerical["absolute_causal_margin"])
    CTT = Tt * Tt - a * Tx * Tx
    initial = Xx[:, 0] ** 2 - Tx[:, 0] ** 2
    left = Tt[0, :] ** 2 - Xt[0, :] ** 2
    right = Tt[-1, :] ** 2 - Xt[-1, :] ** 2
    metrics.update({
        "CTT_min": float(np.min(CTT)), "initial_spacelike_margin_min": float(np.min(initial)),
        "left_boundary_timelike_margin_min": float(np.min(left)), "right_boundary_timelike_margin_min": float(np.min(right)),
    })
    if metrics["CTT_min"] <= eps_c:
        reject.append("F2_CTT_NONPOSITIVE")
    if metrics["initial_spacelike_margin_min"] <= eps_c:
        reject.append("F2_INITIAL_CURVE_NOT_SPACELIKE")
    if metrics["left_boundary_timelike_margin_min"] <= eps_c:
        reject.append("F2_LEFT_BOUNDARY_NOT_TIMELIKE")
    if metrics["right_boundary_timelike_margin_min"] <= eps_c:
        reject.append("F2_RIGHT_BOUNDARY_NOT_TIMELIKE")
    if reject:
        return _result("F1", reject, metrics, gauge_record, gauged)

    poly = _boundary_polygon(gauged["X"], gauged["T"])
    boundary_area = _polygon_signed_area(poly)
    intersections = boundary_self_intersection_count(poly, float(numerical["segment_intersection_tolerance"]))
    cell_areas = mapped_cell_signed_areas(gauged["X"], gauged["T"])
    source_cell_area = float((grid.x[1] - grid.x[0]) * (grid.t[1] - grid.t[0]))
    cell_floor = float(numerical["cell_area_relative_margin"]) * source_cell_area
    metrics.update({
        "boundary_signed_area": float(boundary_area), "boundary_self_intersection_count": int(intersections),
        "mapped_cell_signed_area_min": float(np.min(cell_areas)), "mapped_cell_signed_area_max": float(np.max(cell_areas)),
    })
    if intersections > 0:
        reject.append("F3_BOUNDARY_SELF_INTERSECTION")
    if boundary_area <= 0.0:
        reject.append("F3_BOUNDARY_ORIENTATION")
    if metrics["mapped_cell_signed_area_min"] <= cell_floor:
        reject.append("F3_CELL_ORIENTATION")
    if reject:
        return _result("F2", reject, metrics, gauge_record, gauged)

    inverse = vectorized_inverse_evidence(
        gauged, grid, raw_probe_source, raw_probe_target, gauge_record,
        float(numerical["barycentric_tolerance"]), float(numerical["segment_intersection_tolerance"]),
    )
    cond = derivative_condition_numbers(Xx, Xt, Tx, Tt)
    metrics.update(inverse)
    metrics.update({"map_condition_number_max": float(np.max(cond)), "map_condition_number_median": float(np.median(cond))})
    if not inverse["all_inside_boundary"]:
        reject.append("F4_INVERSE_PROBE_OUTSIDE_BOUNDARY")
    if not inverse["all_unique"]:
        reject.append("F4_INVERSE_NOT_UNIQUE")
    if not inverse["degree_one_evidence"]:
        reject.append("F4_DEGREE_NOT_ONE")
    if inverse["roundtrip_error_max"] > inverse_roundtrip_tolerance:
        reject.append("F4_ROUNDTRIP_TOLERANCE")
    if metrics["map_condition_number_max"] > float(numerical["condition_number_hard_ceiling"]):
        reject.append("F4_CONDITIONING_CEILING")
    if reject:
        return _result("F3", reject, metrics, gauge_record, gauged)
    return _result("F4", [], metrics, gauge_record, gauged)


def direct_margin_vector(validity: dict[str, Any]) -> tuple[float, ...]:
    stage = validity["highest_feasibility_level"]
    m = validity.get("metrics", {})
    if stage == "NONE":
        return (float(m.get("finite_fraction", 0.0)),)
    if stage == "F0":
        return (float(m.get("J_min", -math.inf)), float(m.get("Tt_min", -math.inf)))
    if stage == "F1":
        return tuple(float(m.get(key, -math.inf)) for key in [
            "CTT_min", "initial_spacelike_margin_min", "left_boundary_timelike_margin_min", "right_boundary_timelike_margin_min"
        ])
    if stage == "F2":
        return (-float(m.get("boundary_self_intersection_count", math.inf)), float(m.get("boundary_signed_area", -math.inf)), float(m.get("mapped_cell_signed_area_min", -math.inf)))
    if stage == "F3":
        return (-float(m.get("roundtrip_error_max", math.inf)), -float(m.get("map_condition_number_max", math.inf)))
    return ()


def evaluate_concrete_call(
    pair: dict[str, Any],
    theta_vector: list[float],
    grid: GridSpec,
    validity_numerical: dict[str, Any],
    inverse_roundtrip_tolerance: float,
    search_space: dict[str, Any],
    operator_numerical: dict[str, Any],
) -> dict[str, Any]:
    start = time.perf_counter()
    x_mesh, t_mesh = grid.mesh
    try:
        raw = evaluate_pair_jet(pair["raw_X_AST"], pair["raw_T_AST"], theta_vector, x_mesh, t_mesh)
        source = _probe_source(grid, int(validity_numerical["inverse_probe_grid_per_axis"]), tuple(validity_numerical["inverse_probe_local_coordinates"]))
        probe_raw = evaluate_pair_jet(pair["raw_X_AST"], pair["raw_T_AST"], theta_vector, source[:, 0], source[:, 1])
        target = np.column_stack([probe_raw["X"], probe_raw["T"]])
        validity = evaluate_validity_staged(raw, grid, source, target, validity_numerical, inverse_roundtrip_tolerance)
    except Exception as exc:
        return {
            "theta_vector": [float(value) for value in theta_vector], "highest_feasibility_level": "NONE", "stage_index": 0,
            "F0_F4_records": {"exception_type": type(exc).__name__, "exception": str(exc)}, "direct_margin_vector": [0.0],
            "J_princ": None, "J_XT": None, "J_XX": None, "elapsed_seconds": time.perf_counter() - start,
        }
    record: dict[str, Any] = {
        "theta_vector": [float(value) for value in theta_vector],
        "highest_feasibility_level": validity["highest_feasibility_level"], "stage_index": validity["stage_index"],
        "F0_F4_records": {key: value for key, value in validity.items() if key != "gauged_map"},
        "direct_margin_vector": list(direct_margin_vector(validity)),
        "J_princ": None, "J_XT": None, "J_XX": None,
    }
    if validity["overall_valid"]:
        try:
            gauged_second, gauge_record = gauge_second_jet(raw)
            if gauged_second is None:
                raise ValueError(f"second-jet gauge failed: {gauge_record}")
            _, operator, operator_seconds = evaluate_map_on_space(gauged_second, grid, search_space, operator_numerical, medium="fixed")
            record.update(operator.as_dict())
            record["operator_seconds"] = operator_seconds
        except Exception as exc:
            record["highest_feasibility_level"] = "F3"
            record["stage_index"] = 4
            record["F0_F4_records"]["operator_exception_type"] = type(exc).__name__
            record["F0_F4_records"]["operator_exception"] = str(exc)
    record["elapsed_seconds"] = time.perf_counter() - start
    return record
