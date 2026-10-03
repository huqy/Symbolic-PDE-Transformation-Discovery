#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable

import numpy as np


Array = np.ndarray
MapFunction = Callable[[Array, Array], dict[str, Array]]


@dataclass(frozen=True)
class GridSpec:
    x: Array
    t: Array

    @property
    def mesh(self) -> tuple[Array, Array]:
        return np.meshgrid(self.x, self.t, indexing="ij")


REJECTION_VOCABULARY: dict[str, dict[str, str]] = {
    "GAUGE_REFERENCE_J_NONPOSITIVE": {"level": "GAUGE", "meaning": "Raw reference Jacobian is not strictly positive."},
    "GAUGE_SCALE_NONFINITE": {"level": "GAUGE", "meaning": "Common positive gauge scale is nonfinite or zero."},
    "F0_SHAPE_MISMATCH": {"level": "F0", "meaning": "Map arrays do not share the declared grid shape."},
    "F0_NONFINITE_MAP_OR_DERIVATIVE": {"level": "F0", "meaning": "Map values or first derivatives contain nonfinite values."},
    "F1_JACOBIAN_ORIENTATION_MARGIN": {"level": "F1", "meaning": "Jacobian fails the fixed positive orientation margin."},
    "F1_FUTURE_ORIENTATION": {"level": "F1", "meaning": "T_t fails the fixed future-orientation margin."},
    "F2_CTT_NONPOSITIVE": {"level": "F2", "meaning": "C_TT=T_t^2-a*T_x^2 is not strictly positive."},
    "F2_INITIAL_CURVE_NOT_SPACELIKE": {"level": "F2", "meaning": "Mapped source initial line is not spacelike for the standard target principal metric."},
    "F2_LEFT_BOUNDARY_NOT_TIMELIKE": {"level": "F2", "meaning": "Mapped x=0 side boundary is not timelike."},
    "F2_RIGHT_BOUNDARY_NOT_TIMELIKE": {"level": "F2", "meaning": "Mapped x=1 side boundary is not timelike."},
    "F3_BOUNDARY_SELF_INTERSECTION": {"level": "F3", "meaning": "Mapped domain boundary has a nonadjacent segment intersection."},
    "F3_BOUNDARY_ORIENTATION": {"level": "F3", "meaning": "Mapped boundary does not have positive orientation."},
    "F3_CELL_ORIENTATION": {"level": "F3", "meaning": "At least one mapped grid cell fails positive oriented-area margin."},
    "F4_INVERSE_PROBE_OUTSIDE_BOUNDARY": {"level": "F4", "meaning": "A mapped interior inverse probe is not inside the mapped boundary."},
    "F4_INVERSE_NOT_UNIQUE": {"level": "F4", "meaning": "A mapped inverse probe has zero or multiple distinct numerical preimages."},
    "F4_ROUNDTRIP_TOLERANCE": {"level": "F4", "meaning": "Numerical inverse round-trip exceeds the calibrated tolerance."},
    "F4_DEGREE_NOT_ONE": {"level": "F4", "meaning": "Boundary winding/degree evidence is not one for all probes."},
    "F4_CONDITIONING_CEILING": {"level": "F4", "meaning": "Derivative-map condition number exceeds the frozen hard ceiling."}
}


def build_grid(nx: int, nt: int) -> GridSpec:
    return GridSpec(x=np.linspace(0.0, 1.0, nx, dtype=np.float64), t=np.linspace(0.0, 1.0, nt, dtype=np.float64))


def benchmark_a(x: Array, t: Array) -> Array:
    return 1.0 + 0.5 * x * t


def common_scale_translation_gauge(raw: dict[str, Array], reference_index: tuple[int, int] = (0, 0)) -> tuple[dict[str, Array] | None, dict[str, Any]]:
    i0, j0 = reference_index
    j_ref = float(raw["Xx"][i0, j0] * raw["Tt"][i0, j0] - raw["Xt"][i0, j0] * raw["Tx"][i0, j0])
    if not np.isfinite(j_ref) or j_ref <= 0.0:
        return None, {"status": "FAIL", "rejection_code": "GAUGE_REFERENCE_J_NONPOSITIVE", "raw_reference_J": j_ref}
    scale = float(np.sqrt(j_ref))
    if not np.isfinite(scale) or scale <= 0.0:
        return None, {"status": "FAIL", "rejection_code": "GAUGE_SCALE_NONFINITE", "raw_reference_J": j_ref, "scale": scale}
    x_ref = float(raw["X"][i0, j0])
    t_ref = float(raw["T"][i0, j0])
    gauged = {
        "X": (raw["X"] - x_ref) / scale,
        "T": (raw["T"] - t_ref) / scale,
        "Xx": raw["Xx"] / scale,
        "Xt": raw["Xt"] / scale,
        "Tx": raw["Tx"] / scale,
        "Tt": raw["Tt"] / scale,
    }
    return gauged, {
        "status": "PASS",
        "reference_index": [i0, j0],
        "raw_translation": [x_ref, t_ref],
        "common_positive_scale": scale,
        "raw_reference_J": j_ref,
        "gauged_reference_J": float(gauged["Xx"][i0, j0] * gauged["Tt"][i0, j0] - gauged["Xt"][i0, j0] * gauged["Tx"][i0, j0]),
        "automatic_orientation_flip_applied": False,
        "anisotropic_scale_or_linear_mixing_applied": False,
    }


def _boundary_polygon(X: Array, T: Array) -> Array:
    bottom = np.column_stack([X[:, 0], T[:, 0]])
    right = np.column_stack([X[-1, 1:], T[-1, 1:]])
    top = np.column_stack([X[-2::-1, -1], T[-2::-1, -1]])
    left = np.column_stack([X[0, -2:0:-1], T[0, -2:0:-1]])
    return np.vstack([bottom, right, top, left])


def _polygon_signed_area(poly: Array) -> float:
    x = poly[:, 0]
    y = poly[:, 1]
    return 0.5 * float(np.sum(x * np.roll(y, -1) - y * np.roll(x, -1)))


def _orientation(a: Array, b: Array, c: Array) -> float:
    return float((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]))


def _on_segment(a: Array, b: Array, p: Array, tol: float) -> bool:
    return (
        min(a[0], b[0]) - tol <= p[0] <= max(a[0], b[0]) + tol
        and min(a[1], b[1]) - tol <= p[1] <= max(a[1], b[1]) + tol
        and abs(_orientation(a, b, p)) <= tol
    )


def _segments_intersect(a: Array, b: Array, c: Array, d: Array, tol: float) -> bool:
    o1 = _orientation(a, b, c)
    o2 = _orientation(a, b, d)
    o3 = _orientation(c, d, a)
    o4 = _orientation(c, d, b)
    if ((o1 > tol and o2 < -tol) or (o1 < -tol and o2 > tol)) and ((o3 > tol and o4 < -tol) or (o3 < -tol and o4 > tol)):
        return True
    return (
        (abs(o1) <= tol and _on_segment(a, b, c, tol))
        or (abs(o2) <= tol and _on_segment(a, b, d, tol))
        or (abs(o3) <= tol and _on_segment(c, d, a, tol))
        or (abs(o4) <= tol and _on_segment(c, d, b, tol))
    )


def boundary_self_intersection_count(poly: Array, tol: float) -> int:
    n = len(poly)
    count = 0
    for i in range(n):
        a = poly[i]
        b = poly[(i + 1) % n]
        for j in range(i + 1, n):
            if j == i or (j + 1) % n == i or (i + 1) % n == j:
                continue
            if i == 0 and j == n - 1:
                continue
            c = poly[j]
            d = poly[(j + 1) % n]
            if _segments_intersect(a, b, c, d, tol):
                count += 1
    return count


def _point_winding_number(point: Array, poly: Array, tol: float) -> int:
    winding = 0
    px, py = float(point[0]), float(point[1])
    n = len(poly)
    for i in range(n):
        a = poly[i]
        b = poly[(i + 1) % n]
        if _on_segment(a, b, point, tol):
            return 1
        if a[1] <= py:
            if b[1] > py and _orientation(a, b, point) > tol:
                winding += 1
        else:
            if b[1] <= py and _orientation(a, b, point) < -tol:
                winding -= 1
    return winding


def mapped_cell_signed_areas(X: Array, T: Array) -> Array:
    x00, y00 = X[:-1, :-1], T[:-1, :-1]
    x10, y10 = X[1:, :-1], T[1:, :-1]
    x11, y11 = X[1:, 1:], T[1:, 1:]
    x01, y01 = X[:-1, 1:], T[:-1, 1:]
    return 0.5 * (
        x00 * y10 - y00 * x10
        + x10 * y11 - y10 * x11
        + x11 * y01 - y11 * x01
        + x01 * y00 - y01 * x00
    )


def derivative_condition_numbers(Xx: Array, Xt: Array, Tx: Array, Tt: Array) -> Array:
    a = Xx * Xx + Tx * Tx
    b = Xx * Xt + Tx * Tt
    d = Xt * Xt + Tt * Tt
    trace = a + d
    disc = np.sqrt(np.maximum((a - d) ** 2 + 4.0 * b * b, 0.0))
    lam_max = np.maximum(0.5 * (trace + disc), 0.0)
    lam_min = np.maximum(0.5 * (trace - disc), 0.0)
    smax = np.sqrt(lam_max)
    smin = np.sqrt(lam_min)
    return np.divide(smax, smin, out=np.full_like(smax, np.inf), where=smin > 0.0)


def _barycentric(point: Array, tri: Array, tol: float) -> tuple[bool, Array]:
    v0 = tri[1] - tri[0]
    v1 = tri[2] - tri[0]
    v2 = point - tri[0]
    det = v0[0] * v1[1] - v0[1] * v1[0]
    if abs(det) <= tol:
        return False, np.zeros(3, dtype=np.float64)
    u = (v2[0] * v1[1] - v2[1] * v1[0]) / det
    v = (v0[0] * v2[1] - v0[1] * v2[0]) / det
    w = 1.0 - u - v
    weights = np.array([w, u, v], dtype=np.float64)
    return bool(np.all(weights >= -tol) and np.all(weights <= 1.0 + tol)), weights


def build_inverse_probes(map_function: MapFunction, grid: GridSpec, per_axis: int, local_coordinates: tuple[float, float]) -> dict[str, Array]:
    cell_i = np.unique(np.linspace(0, len(grid.x) - 2, per_axis, dtype=int))
    cell_j = np.unique(np.linspace(0, len(grid.t) - 2, per_axis, dtype=int))
    ux, ut = local_coordinates
    source_rows = []
    for i in cell_i:
        for j in cell_j:
            x = grid.x[i] + ux * (grid.x[i + 1] - grid.x[i])
            t = grid.t[j] + ut * (grid.t[j + 1] - grid.t[j])
            source_rows.append((x, t))
    source = np.asarray(source_rows, dtype=np.float64)
    mapped = map_function(source[:, 0], source[:, 1])
    target = np.column_stack([mapped["X"], mapped["T"]])
    return {"source": source, "target": target}


def inverse_probe_evidence(gauged: dict[str, Array], grid: GridSpec, raw_probe_target: Array, raw_probe_source: Array, gauge_record: dict[str, Any], bary_tol: float, boundary_tol: float) -> dict[str, Any]:
    scale = float(gauge_record["common_positive_scale"])
    translation = np.asarray(gauge_record["raw_translation"], dtype=np.float64)
    probe_target = (raw_probe_target - translation[None, :]) / scale
    poly = _boundary_polygon(gauged["X"], gauged["T"])
    nx, nt = gauged["X"].shape
    source_mesh_x, source_mesh_t = np.meshgrid(grid.x, grid.t, indexing="ij")
    unique_counts: list[int] = []
    winding_numbers: list[int] = []
    errors: list[float] = []
    for point, source_true in zip(probe_target, raw_probe_source):
        winding_numbers.append(_point_winding_number(point, poly, boundary_tol))
        reconstructed: list[Array] = []
        for i in range(nx - 1):
            for j in range(nt - 1):
                target_vertices = np.array([
                    [gauged["X"][i, j], gauged["T"][i, j]],
                    [gauged["X"][i + 1, j], gauged["T"][i + 1, j]],
                    [gauged["X"][i + 1, j + 1], gauged["T"][i + 1, j + 1]],
                    [gauged["X"][i, j + 1], gauged["T"][i, j + 1]],
                ], dtype=np.float64)
                if point[0] < target_vertices[:, 0].min() - bary_tol or point[0] > target_vertices[:, 0].max() + bary_tol:
                    continue
                if point[1] < target_vertices[:, 1].min() - bary_tol or point[1] > target_vertices[:, 1].max() + bary_tol:
                    continue
                source_vertices = np.array([
                    [source_mesh_x[i, j], source_mesh_t[i, j]],
                    [source_mesh_x[i + 1, j], source_mesh_t[i + 1, j]],
                    [source_mesh_x[i + 1, j + 1], source_mesh_t[i + 1, j + 1]],
                    [source_mesh_x[i, j + 1], source_mesh_t[i, j + 1]],
                ], dtype=np.float64)
                for ids in ((0, 1, 2), (0, 2, 3)):
                    inside, weights = _barycentric(point, target_vertices[list(ids)], bary_tol)
                    if inside:
                        reconstructed.append(weights @ source_vertices[list(ids)])
        clusters: list[Array] = []
        for candidate in reconstructed:
            if not any(np.linalg.norm(candidate - existing, ord=np.inf) <= 10.0 * bary_tol for existing in clusters):
                clusters.append(candidate)
        unique_counts.append(len(clusters))
        if len(clusters) == 1:
            errors.append(float(np.linalg.norm(clusters[0] - source_true, ord=np.inf)))
        else:
            errors.append(float("inf"))
    return {
        "probe_count": int(len(raw_probe_source)),
        "unique_preimage_count_min": int(min(unique_counts)) if unique_counts else 0,
        "unique_preimage_count_max": int(max(unique_counts)) if unique_counts else 0,
        "all_unique": bool(unique_counts and all(value == 1 for value in unique_counts)),
        "winding_number_min": int(min(winding_numbers)) if winding_numbers else 0,
        "winding_number_max": int(max(winding_numbers)) if winding_numbers else 0,
        "degree_one_evidence": bool(winding_numbers and all(value == 1 for value in winding_numbers)),
        "all_inside_boundary": bool(winding_numbers and all(value != 0 for value in winding_numbers)),
        "roundtrip_error_max": float(max(errors)) if errors else float("inf"),
    }


def evaluate_validity(raw: dict[str, Array], grid: GridSpec, raw_probe_source: Array, raw_probe_target: Array, protocol: dict[str, Any], inverse_roundtrip_tolerance: float) -> dict[str, Any]:
    required = ["X", "T", "Xx", "Xt", "Tx", "Tt"]
    rejection_codes: list[str] = []
    shapes = {key: tuple(np.asarray(raw[key]).shape) for key in required if key in raw}
    expected_shape = (len(grid.x), len(grid.t))
    if set(shapes) != set(required) or any(shape != expected_shape for shape in shapes.values()):
        rejection_codes.append("F0_SHAPE_MISMATCH")
        return _finalize_ladder(rejection_codes, {}, {}, {})
    if not all(np.isfinite(raw[key]).all() for key in required):
        rejection_codes.append("F0_NONFINITE_MAP_OR_DERIVATIVE")
        return _finalize_ladder(rejection_codes, {}, {}, {})

    gauged, gauge_record = common_scale_translation_gauge(raw)
    if gauged is None:
        rejection_codes.append(str(gauge_record["rejection_code"]))
        return _finalize_ladder(rejection_codes, {}, gauge_record, {})

    eps_orientation = float(protocol["absolute_orientation_margin"])
    eps_causal = float(protocol["absolute_causal_margin"])
    Xx, Xt, Tx, Tt = gauged["Xx"], gauged["Xt"], gauged["Tx"], gauged["Tt"]
    x_mesh, t_mesh = grid.mesh
    a = benchmark_a(x_mesh, t_mesh)
    J = Xx * Tt - Xt * Tx
    CTT = Tt * Tt - a * Tx * Tx
    initial_spacelike = Xx[:, 0] ** 2 - Tx[:, 0] ** 2
    left_timelike = Tt[0, :] ** 2 - Xt[0, :] ** 2
    right_timelike = Tt[-1, :] ** 2 - Xt[-1, :] ** 2
    if float(np.min(J)) <= eps_orientation:
        rejection_codes.append("F1_JACOBIAN_ORIENTATION_MARGIN")
    if float(np.min(Tt)) <= eps_orientation:
        rejection_codes.append("F1_FUTURE_ORIENTATION")
    if float(np.min(CTT)) <= eps_causal:
        rejection_codes.append("F2_CTT_NONPOSITIVE")
    if float(np.min(initial_spacelike)) <= eps_causal:
        rejection_codes.append("F2_INITIAL_CURVE_NOT_SPACELIKE")
    if float(np.min(left_timelike)) <= eps_causal:
        rejection_codes.append("F2_LEFT_BOUNDARY_NOT_TIMELIKE")
    if float(np.min(right_timelike)) <= eps_causal:
        rejection_codes.append("F2_RIGHT_BOUNDARY_NOT_TIMELIKE")

    poly = _boundary_polygon(gauged["X"], gauged["T"])
    boundary_area = _polygon_signed_area(poly)
    intersection_count = boundary_self_intersection_count(poly, float(protocol["segment_intersection_tolerance"]))
    cell_areas = mapped_cell_signed_areas(gauged["X"], gauged["T"])
    source_cell_area = float((grid.x[1] - grid.x[0]) * (grid.t[1] - grid.t[0]))
    cell_margin = float(protocol["cell_area_relative_margin"]) * source_cell_area
    if intersection_count > 0:
        rejection_codes.append("F3_BOUNDARY_SELF_INTERSECTION")
    if boundary_area <= 0.0:
        rejection_codes.append("F3_BOUNDARY_ORIENTATION")
    if float(np.min(cell_areas)) <= cell_margin:
        rejection_codes.append("F3_CELL_ORIENTATION")

    inverse = inverse_probe_evidence(
        gauged,
        grid,
        raw_probe_target,
        raw_probe_source,
        gauge_record,
        float(protocol["barycentric_tolerance"]),
        float(protocol["segment_intersection_tolerance"]),
    )
    cond = derivative_condition_numbers(Xx, Xt, Tx, Tt)
    condition_max = float(np.max(cond))
    if not inverse["all_inside_boundary"]:
        rejection_codes.append("F4_INVERSE_PROBE_OUTSIDE_BOUNDARY")
    if not inverse["all_unique"]:
        rejection_codes.append("F4_INVERSE_NOT_UNIQUE")
    if not inverse["degree_one_evidence"]:
        rejection_codes.append("F4_DEGREE_NOT_ONE")
    if inverse["roundtrip_error_max"] > inverse_roundtrip_tolerance:
        rejection_codes.append("F4_ROUNDTRIP_TOLERANCE")
    if condition_max > float(protocol["condition_number_hard_ceiling"]):
        rejection_codes.append("F4_CONDITIONING_CEILING")

    metrics = {
        "J_min": float(np.min(J)),
        "J_max": float(np.max(J)),
        "Tt_min": float(np.min(Tt)),
        "CTT_min": float(np.min(CTT)),
        "initial_spacelike_margin_min": float(np.min(initial_spacelike)),
        "left_boundary_timelike_margin_min": float(np.min(left_timelike)),
        "right_boundary_timelike_margin_min": float(np.min(right_timelike)),
        "boundary_signed_area": boundary_area,
        "boundary_self_intersection_count": int(intersection_count),
        "mapped_cell_signed_area_min": float(np.min(cell_areas)),
        "mapped_cell_signed_area_max": float(np.max(cell_areas)),
        "map_condition_number_max": condition_max,
        "map_condition_number_median": float(np.median(cond)),
        **inverse,
    }
    return _finalize_ladder(rejection_codes, metrics, gauge_record, {key: gauged[key] for key in required})


def _finalize_ladder(rejection_codes: Iterable[str], metrics: dict[str, Any], gauge_record: dict[str, Any], gauged: dict[str, Array]) -> dict[str, Any]:
    codes = list(dict.fromkeys(rejection_codes))
    levels = {code: REJECTION_VOCABULARY[code]["level"] for code in codes if code in REJECTION_VOCABULARY}
    gauge_fail = any(level == "GAUGE" for level in levels.values())
    f0 = not gauge_fail and not any(level == "F0" for level in levels.values())
    f1 = f0 and not any(level == "F1" for level in levels.values())
    f2 = f1 and not any(level == "F2" for level in levels.values())
    f3 = f2 and not any(level == "F3" for level in levels.values())
    f4 = f3 and not any(level == "F4" for level in levels.values())
    ladder = {"F0": f0, "F1": f1, "F2": f2, "F3": f3, "F4": f4}
    highest = "NONE"
    for level in ["F0", "F1", "F2", "F3", "F4"]:
        if ladder[level]:
            highest = level
        else:
            break
    return {
        "overall_valid": bool(f4),
        "highest_feasibility_level": highest,
        "feasibility_ladder": ladder,
        "rejection_codes": codes,
        "metrics": metrics,
        "gauge_record": gauge_record,
        "gauged_map": gauged,
        "weighted_score_present": False,
    }


def _affine_map(x: Array, t: Array, matrix: tuple[tuple[float, float], tuple[float, float]], offset: tuple[float, float]) -> dict[str, Array]:
    ax, at = matrix[0]
    bx, bt = matrix[1]
    return {
        "X": offset[0] + ax * x + at * t,
        "T": offset[1] + bx * x + bt * t,
        "Xx": np.full_like(x, ax),
        "Xt": np.full_like(x, at),
        "Tx": np.full_like(x, bx),
        "Tt": np.full_like(x, bt),
    }


def synthetic_map_registry() -> dict[str, dict[str, Any]]:
    def identity(x: Array, t: Array) -> dict[str, Array]:
        return _affine_map(x, t, ((1.0, 0.0), (0.0, 1.0)), (0.0, 0.0))

    def translated_common_scale(x: Array, t: Array) -> dict[str, Array]:
        return _affine_map(x, t, ((2.5, 0.0), (0.0, 2.5)), (3.0, -4.0))

    def shear_valid(x: Array, t: Array) -> dict[str, Array]:
        return _affine_map(x, t, ((1.0, 0.2), (0.0, 1.0)), (0.0, 0.0))

    def anisotropic_valid(x: Array, t: Array) -> dict[str, Array]:
        return _affine_map(x, t, ((2.0, 0.0), (0.0, 1.0)), (0.0, 0.0))

    def curved_initial_valid(x: Array, t: Array) -> dict[str, Array]:
        beta = 0.2
        return {
            "X": x,
            "T": t + beta * x * (1.0 - x),
            "Xx": np.ones_like(x),
            "Xt": np.zeros_like(x),
            "Tx": beta * (1.0 - 2.0 * x),
            "Tt": np.ones_like(x),
        }

    def nonlinear_valid(x: Array, t: Array) -> dict[str, Array]:
        alpha = 0.03
        beta = 0.04
        return {
            "X": x + alpha * np.sin(2.0 * np.pi * x) * np.sin(np.pi * t),
            "T": t + beta * x * (1.0 - x),
            "Xx": 1.0 + 2.0 * np.pi * alpha * np.cos(2.0 * np.pi * x) * np.sin(np.pi * t),
            "Xt": np.pi * alpha * np.sin(2.0 * np.pi * x) * np.cos(np.pi * t),
            "Tx": beta * (1.0 - 2.0 * x),
            "Tt": np.ones_like(x),
        }

    def reversed_orientation(x: Array, t: Array) -> dict[str, Array]:
        return _affine_map(x, t, ((-1.0, 0.0), (0.0, 1.0)), (1.0, 0.0))

    def initial_nonspacelike(x: Array, t: Array) -> dict[str, Array]:
        return _affine_map(x, t, ((1.0, 0.0), (1.1, 1.0)), (0.0, 0.0))

    def side_nontimelike(x: Array, t: Array) -> dict[str, Array]:
        return _affine_map(x, t, ((1.0, 1.1), (0.0, 1.0)), (0.0, 0.0))

    def local_fold(x: Array, t: Array) -> dict[str, Array]:
        amp = 0.2
        return {
            "X": x + amp * np.sin(2.0 * np.pi * x),
            "T": t,
            "Xx": 1.0 + 2.0 * np.pi * amp * np.cos(2.0 * np.pi * x),
            "Xt": np.zeros_like(x),
            "Tx": np.zeros_like(x),
            "Tt": np.ones_like(x),
        }

    def extreme_conditioning(x: Array, t: Array) -> dict[str, Array]:
        epsilon = 1.0e-9
        return _affine_map(x, t, ((epsilon, 0.0), (0.0, 1.0)), (0.0, 0.0))

    return {
        "identity": {"function": identity, "expected_F4": True, "role": "answer_neutral_valid_calibration"},
        "translated_common_scale": {"function": translated_common_scale, "expected_F4": True, "role": "gauge_equivalence_fixture"},
        "shear_valid_not_gauge": {"function": shear_valid, "expected_F4": True, "role": "non_gauge_candidate_structure_fixture"},
        "anisotropic_valid_not_gauge": {"function": anisotropic_valid, "expected_F4": True, "role": "non_gauge_candidate_structure_fixture"},
        "curved_initial_valid": {"function": curved_initial_valid, "expected_F4": True, "role": "curved_Cauchy_fixture"},
        "nonlinear_valid": {"function": nonlinear_valid, "expected_F4": True, "role": "answer_neutral_inverse_calibration"},
        "reversed_orientation": {"function": reversed_orientation, "expected_F4": False, "expected_rejection": "GAUGE_REFERENCE_J_NONPOSITIVE", "role": "invalid_regression"},
        "initial_nonspacelike": {"function": initial_nonspacelike, "expected_F4": False, "expected_rejection": "F2_CTT_NONPOSITIVE", "role": "invalid_regression"},
        "side_nontimelike": {"function": side_nontimelike, "expected_F4": False, "expected_rejection": "F2_LEFT_BOUNDARY_NOT_TIMELIKE", "role": "invalid_regression"},
        "local_fold": {"function": local_fold, "expected_F4": False, "expected_rejection": "F1_JACOBIAN_ORIENTATION_MARGIN", "role": "invalid_regression"},
        "extreme_conditioning": {"function": extreme_conditioning, "expected_F4": False, "expected_rejection": "F4_CONDITIONING_CEILING", "role": "invalid_regression"},
    }


def strip_arrays(record: dict[str, Any]) -> dict[str, Any]:
    out = dict(record)
    out.pop("gauged_map", None)
    return out
