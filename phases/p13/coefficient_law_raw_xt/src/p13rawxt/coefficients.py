from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable

import numpy as np

MultiIndex = tuple[int, int]


def canonical_json_bytes(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def deterministic_seed(namespace: str, role: str, index: int) -> int:
    digest = hashlib.sha256(f"{namespace}|{role}|{index:04d}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big", signed=False)


def _random_frequency(rng: np.random.Generator, spec: dict[str, Any], *, oblique: bool) -> tuple[float, float]:
    lo = float(spec["frequency_l1_min"])
    hi = float(spec["frequency_l1_max"])
    component_min = float(spec["oblique_component_abs_min"])
    for _ in range(10000):
        total = float(rng.uniform(lo, hi))
        frac = float(rng.uniform(0.15, 0.85))
        ax, at = total * frac, total * (1.0 - frac)
        if oblique and min(ax, at) < component_min:
            continue
        sx = -1.0 if int(rng.integers(0, 2)) else 1.0
        st = -1.0 if int(rng.integers(0, 2)) else 1.0
        return sx * ax, st * at
    raise RuntimeError("unable to draw legal frequency")


def _normalized_amplitudes(rng: np.random.Generator, n: int, l1_target: float) -> np.ndarray:
    raw = rng.normal(size=n)
    tiny = np.abs(raw) < 0.15
    raw[tiny] = np.where(raw[tiny] >= 0.0, 0.15, -0.15)
    return raw * (float(l1_target) / float(np.sum(np.abs(raw))))


def _plane_wave_components(rng: np.random.Generator, n: int, spec: dict[str, Any]) -> list[dict[str, float]]:
    amps = _normalized_amplitudes(rng, n, float(spec["amplitude_l1_target"]))
    out: list[dict[str, float]] = []
    for amp in amps:
        kx, kt = _random_frequency(rng, spec, oblique=True)
        phase = float(rng.uniform(*spec["phase_range"]))
        out.append({"amplitude": float(amp), "kx": kx, "kt": kt, "phase": phase})
    return out


def _separable_components(rng: np.random.Generator, spec: dict[str, Any]) -> list[dict[str, float]]:
    amps = _normalized_amplitudes(rng, 4, float(spec["amplitude_l1_target"]))
    out: list[dict[str, float]] = []
    for idx, amp in enumerate(amps):
        freq = float(rng.uniform(spec["frequency_l1_min"], spec["frequency_l1_max"]))
        if int(rng.integers(0, 2)):
            freq *= -1.0
        phase = float(rng.uniform(*spec["phase_range"]))
        if idx < 2:
            out.append({"amplitude": float(amp), "kx": freq, "kt": 0.0, "phase": phase})
        else:
            out.append({"amplitude": float(amp), "kx": 0.0, "kt": freq, "phase": phase})
    return out


def _tensor_components(rng: np.random.Generator, n: int, spec: dict[str, Any], l1_budget: float) -> tuple[list[dict[str, float]], list[dict[str, float]]]:
    amps = _normalized_amplitudes(rng, n, l1_budget)
    primitive: list[dict[str, float]] = []
    expanded: list[dict[str, float]] = []
    for amp in amps:
        kx, kt = _random_frequency(rng, spec, oblique=True)
        kx, kt = abs(kx), abs(kt)
        phix = float(rng.uniform(*spec["phase_range"]))
        phit = float(rng.uniform(*spec["phase_range"]))
        primitive.append({"amplitude": float(amp), "kx": kx, "kt": kt, "phase_x": phix, "phase_t": phit})
        expanded.append({"amplitude": float(amp) / 2.0, "kx": kx, "kt": kt, "phase": phix + phit})
        expanded.append({"amplitude": float(amp) / 2.0, "kx": kx, "kt": -kt, "phase": phix - phit})
    return primitive, expanded


def generate_generator(role: str, index: int, seed: int, protocol: dict[str, Any]) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    spec = protocol["spectral_sampling"]
    family = protocol["generator_families"][role]
    primitive: list[dict[str, float]] = []
    if family == "oblique_plane_wave_3mode":
        expanded = _plane_wave_components(rng, 3, spec)
        primitive = [dict(x) for x in expanded]
    elif family == "separable_additive_x_t_4mode":
        expanded = _separable_components(rng, spec)
        primitive = [dict(x) for x in expanded]
    elif family == "tensor_product_trig_3term":
        primitive, expanded = _tensor_components(rng, 3, spec, float(spec["amplitude_l1_target"]))
    elif family == "mixed_plane_tensor_4term":
        # Two plane-wave terms and two tensor-product terms share one fixed L1 budget.
        plane_budget = 0.46
        tensor_budget = float(spec["amplitude_l1_target"]) - plane_budget
        plane_spec = dict(spec); plane_spec["amplitude_l1_target"] = plane_budget
        plane = _plane_wave_components(rng, 2, plane_spec)
        tensor_primitive, tensor_expanded = _tensor_components(rng, 2, spec, tensor_budget)
        primitive = [{"kind": "plane", **x} for x in plane] + [{"kind": "tensor", **x} for x in tensor_primitive]
        expanded = plane + tensor_expanded
    else:
        raise ValueError(f"unknown family {family}")
    field_id = f"P13_{role}_{index:02d}"
    return {
        "schema": "P13_COEFFICIENT_GENERATOR_V1",
        "field_id": field_id,
        "role": role,
        "family": family,
        "seed": int(seed),
        "epsilon": float(protocol["regime"]["epsilon_abs"]),
        "primitive_terms": primitive,
        "expanded_plane_wave_components": expanded,
    }


def spectral_certificate(generator: dict[str, Any], protocol: dict[str, Any]) -> dict[str, Any]:
    comps = generator["expanded_plane_wave_components"]
    l1 = float(sum(abs(float(c["amplitude"])) for c in comps))
    chis = [max(abs(float(c["kt"]) + float(c["kx"])), abs(float(c["kt"]) - float(c["kx"]))) * float(protocol["regime"]["ell_max"]) for c in comps]
    chi = float(max(chis)) if chis else 0.0
    zero = [c for c in comps if abs(float(c["kx"])) + abs(float(c["kt"])) <= 1e-15]
    eps = float(protocol["regime"]["epsilon_abs"])
    positivity_lower = float(math.exp(-eps * l1))
    positivity_upper = float(math.exp(eps * l1))
    passed = l1 <= float(protocol["regime"]["M_max"]) + 1e-14 and chi <= float(protocol["regime"]["chi_max"]) + 1e-14 and not zero
    return {
        "M_l1": l1,
        "M_max": float(protocol["regime"]["M_max"]),
        "chi_exact": chi,
        "chi_max": float(protocol["regime"]["chi_max"]),
        "zero_frequency_component_count": len(zero),
        "analytic_positivity_lower_bound": positivity_lower,
        "analytic_positivity_upper_bound": positivity_upper,
        "status": "PASS" if passed else "FAIL",
    }


def multi_indices(order: int) -> list[MultiIndex]:
    return [(i, total - i) for total in range(order + 1) for i in range(total + 1)]


def b_derivatives(generator: dict[str, Any], x: np.ndarray, t: np.ndarray, order: int) -> dict[MultiIndex, np.ndarray]:
    out = {idx: np.zeros_like(x, dtype=np.float64) for idx in multi_indices(order)}
    for comp in generator["expanded_plane_wave_components"]:
        amp, kx, kt, phase = (float(comp[k]) for k in ("amplitude", "kx", "kt", "phase"))
        base = kx * x + kt * t + phase
        for i, j in out:
            factor = (kx ** i) * (kt ** j)
            out[(i, j)] += amp * factor * np.cos(base + 0.5 * math.pi * (i + j))
    return out


def exponential_coefficient_derivatives(generator: dict[str, Any], x: np.ndarray, t: np.ndarray, order: int) -> dict[MultiIndex, np.ndarray]:
    """Exact gridwise derivatives of a=exp(epsilon*b) via normalized multivariate series."""
    b = b_derivatives(generator, x, t, order)
    eps = float(generator["epsilon"])
    # normalized Taylor coefficients g_alpha = derivative / alpha!
    g: dict[MultiIndex, np.ndarray] = {}
    for i, j in multi_indices(order):
        g[(i, j)] = eps * b[(i, j)] / (math.factorial(i) * math.factorial(j))
    c: dict[MultiIndex, np.ndarray] = {(0, 0): np.exp(g[(0, 0)])}
    for total in range(1, order + 1):
        for i in range(total + 1):
            j = total - i
            acc = np.zeros_like(x, dtype=np.float64)
            for bi in range(i + 1):
                for bj in range(j + 1):
                    if bi == 0 and bj == 0:
                        continue
                    if bi + bj > total:
                        continue
                    rem = (i - bi, j - bj)
                    if rem not in c:
                        continue
                    acc += (bi + bj) * g[(bi, bj)] * c[rem]
            c[(i, j)] = acc / float(total)
    return {(i, j): c[(i, j)] * math.factorial(i) * math.factorial(j) for i, j in multi_indices(order)}


def make_search_object(generator: dict[str, Any], grid_n: int, order: int) -> dict[str, np.ndarray]:
    x1 = np.linspace(0.0, 1.0, grid_n, dtype=np.float64)
    t1 = np.linspace(0.0, 1.0, grid_n, dtype=np.float64)
    x, t = np.meshgrid(x1, t1, indexing="ij")
    deriv = exponential_coefficient_derivatives(generator, x, t, order)
    arrays: dict[str, np.ndarray] = {"x": x1, "t": t1, "q": np.ones_like(x)}
    for (i, j), value in deriv.items():
        arrays[f"a_d{i}_{j}"] = value
    return arrays


def write_npz_exact(path: Path, arrays: dict[str, np.ndarray]) -> None:
    # NPZ metadata is not used as semantic identity; the SHA is still recorded for the concrete object.
    np.savez_compressed(path, **arrays)


def search_object_semantic_digest(arrays: dict[str, np.ndarray]) -> str:
    h = hashlib.sha256()
    for key in sorted(arrays):
        arr = np.ascontiguousarray(arrays[key])
        h.update(key.encode("utf-8") + b"\0")
        h.update(str(arr.dtype).encode("ascii") + b"\0")
        h.update(canonical_json_bytes(list(arr.shape)) + b"\0")
        h.update(arr.tobytes(order="C"))
    return h.hexdigest()


def coordinate_dictionary(x: np.ndarray, t: np.ndarray, degree: int) -> np.ndarray:
    cols = []
    for total in range(degree + 1):
        for i in range(total + 1):
            j = total - i
            cols.append((x ** i) * (t ** j))
    return np.column_stack([c.ravel() for c in cols])


def identifiability_report(train_arrays: Iterable[dict[str, np.ndarray]], protocol: dict[str, Any]) -> dict[str, Any]:
    cfg = protocol["identifiability"]
    arrays = list(train_arrays)
    if len(arrays) != int(protocol["role_counts"]["TRAIN_OPERATOR"]):
        raise ValueError("TRAIN array count mismatch")
    n = int(cfg["grid"])
    x1, t1 = arrays[0]["x"], arrays[0]["t"]
    if len(x1) != n or len(t1) != n:
        raise ValueError("identifiability grid mismatch")
    x, t = np.meshgrid(x1, t1, indexing="ij")
    D_one = coordinate_dictionary(x, t, int(cfg["coordinate_dictionary_total_degree"]))
    D = np.tile(D_one, (len(arrays), 1))
    channel_keys = {
        "a_minus_1": None,
        "a_x": "a_d1_0",
        "a_t": "a_d0_1",
        "a_xx": "a_d2_0",
        "a_xt": "a_d1_1",
        "a_tt": "a_d0_2",
    }
    residual_cols = []
    residual_ratios: dict[str, float] = {}
    for name in cfg["channels"]:
        if name == "a_minus_1":
            y = np.concatenate([(a["a_d0_0"] - 1.0).ravel() for a in arrays])
        else:
            y = np.concatenate([a[channel_keys[name]].ravel() for a in arrays])
        beta, *_ = np.linalg.lstsq(D, y, rcond=None)
        r = y - D @ beta
        ratio = float(np.linalg.norm(r) / max(np.linalg.norm(y), 1e-300))
        residual_ratios[name] = ratio
        residual_cols.append(r)
    R = np.column_stack(residual_cols)
    s = np.linalg.svd(R, compute_uv=False)
    tol = float(cfg["svd_relative_tolerance"]) * float(s[0])
    rank = int(np.sum(s > tol))
    cond_ratio = float(s[-1] / s[0]) if s[0] > 0 else 0.0
    gates = {
        "rank": rank == int(cfg["required_rank"]),
        "sigma_ratio": cond_ratio >= float(cfg["sigma_min_over_sigma_max_min"]),
        "channel_residuals": all(v >= float(cfg["channel_relative_residual_min"]) for v in residual_ratios.values()),
    }
    return {
        "grid": n,
        "stacked_rows": int(R.shape[0]),
        "coordinate_dictionary_columns": int(D.shape[1]),
        "channels": list(cfg["channels"]),
        "relative_residuals": residual_ratios,
        "singular_values": [float(v) for v in s],
        "svd_tolerance": tol,
        "numerical_rank": rank,
        "required_rank": int(cfg["required_rank"]),
        "sigma_min_over_sigma_max": cond_ratio,
        "sigma_min_over_sigma_max_required": float(cfg["sigma_min_over_sigma_max_min"]),
        "channel_relative_residual_required": float(cfg["channel_relative_residual_min"]),
        "gates": gates,
        "status": "PASS" if all(gates.values()) else "FAIL",
        "failure_code": None if all(gates.values()) else cfg["failure_code"],
    }
