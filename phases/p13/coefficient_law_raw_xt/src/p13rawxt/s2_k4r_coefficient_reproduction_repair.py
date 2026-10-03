from __future__ import annotations

import argparse
import json
import math
import traceback
from pathlib import Path
from typing import Any

import numpy as np

from . import s2_k4_response_reference_controls as base

EXPECTED_COEFFICIENTS_PY_SHA256 = "78f4b703d6ebe6ec6a153c4793169588cba94ce1068a6fc772461ecbb0b9a8cf"
EXPECTED_S0_K1_PROTOCOL_SHA256 = "71c9aa38e5c53133a09baff88e9319362cd5a9b7e6fcfe9bb1d641702f39f528"
EXPECTED_S0_K1_SEARCH_JET_ORDER = 4
# Engineering-only cross-node/libm reproducibility tolerance. This is not a
# scientific candidate/response threshold. 4096 eps ~= 9.09e-13 for float64.
FLOAT_REPRO_SCALED_INF_TOL = float(4096.0 * np.finfo(np.float64).eps)


def _actual_object_lock(path: Path, entry: dict[str, Any]) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    actual = base.load_npz(path)
    sha_ok = base.sha256_file(path) == entry["sha256"]
    sem = base.search_object_semantic_digest(actual)
    sem_ok = sem == entry["semantic_digest"]
    return actual, {
        "path": str(path),
        "file_sha256_exact": sha_ok,
        "semantic_digest_exact": sem_ok,
        "observed_semantic_digest": sem,
        "expected_semantic_digest": entry["semantic_digest"],
    }


def validate_regenerated_coefficients_roundoff(
    root: Path,
    generators: dict[str, dict[str, Any]],
    k0_manifest: dict[str, Any],
    order: int = EXPECTED_S0_K1_SEARCH_JET_ORDER,
) -> dict[str, Any]:
    """Validate analytic regeneration against K0 authoritative objects.

    Exact identity is required for generator/archive commitments, current K0 object
    SHA/semantic identity, key sets, shapes, dtypes, and non-transcendental x/t/q
    arrays. Coefficient derivative arrays are compared at a candidate-independent
    machine-roundoff scale because sin/cos/exp regeneration is not guaranteed to
    be bit-identical across CPU/libm implementations.
    """
    rows: list[dict[str, Any]] = []
    ok = True
    tol = FLOAT_REPRO_SCALED_INF_TOL
    for entry in k0_manifest["search_objects"]:
        grid = int(entry["grid"])
        if grid not in (17, 33, 65):
            continue
        fid = entry["field_id"]
        actual_path = root / entry["path"]
        actual, alock = _actual_object_lock(actual_path, entry)
        regenerated = base.make_search_object(generators[fid], grid, order)

        keys_ok = sorted(regenerated) == sorted(actual)
        comparisons = []
        arrays_ok = keys_ok
        if keys_ok:
            for key in sorted(actual):
                aa = np.asarray(actual[key])
                rr = np.asarray(regenerated[key])
                shape_ok = aa.shape == rr.shape
                dtype_ok = aa.dtype == rr.dtype
                finite_ok = bool(np.all(np.isfinite(aa)) and np.all(np.isfinite(rr)))
                if shape_ok and finite_ok:
                    diff = float(np.max(np.abs(aa.astype(np.float64) - rr.astype(np.float64)))) if aa.size else 0.0
                    scale = max(1.0, float(np.max(np.abs(aa))) if aa.size else 0.0, float(np.max(np.abs(rr))) if rr.size else 0.0)
                    scaled = diff / scale
                else:
                    diff = math.inf
                    scale = math.nan
                    scaled = math.inf
                exact_required = key in {"x", "t", "q"}
                exact_equal = bool(shape_ok and dtype_ok and np.array_equal(aa, rr))
                pass_key = bool(shape_ok and dtype_ok and finite_ok and (exact_equal if exact_required else scaled <= tol))
                arrays_ok = arrays_ok and pass_key
                comparisons.append({
                    "key": key,
                    "shape_ok": shape_ok,
                    "dtype_ok": dtype_ok,
                    "finite_ok": finite_ok,
                    "exact_required": exact_required,
                    "exact_equal": exact_equal,
                    "max_abs_difference": diff,
                    "scale_floor_one": scale,
                    "scaled_inf_difference": scaled,
                    "tolerance": 0.0 if exact_required else tol,
                    "pass": pass_key,
                })

        regen_sem = base.search_object_semantic_digest(regenerated)
        row_pass = bool(alock["file_sha256_exact"] and alock["semantic_digest_exact"] and keys_ok and arrays_ok)
        row = {
            "field_id": fid,
            "grid": grid,
            "K0_authoritative_object_lock": alock,
            "keys_exact": keys_ok,
            "regenerated_semantic_digest": regen_sem,
            "regenerated_semantic_digest_byte_identity_required": False,
            "comparison_policy": "x/t/q exact; a derivative arrays scaled-Linf <= 4096*float64_eps",
            "scaled_inf_tolerance": tol,
            "comparisons": comparisons,
            "max_scaled_inf_difference": max((c["scaled_inf_difference"] for c in comparisons if not c["exact_required"]), default=0.0),
            "pass": row_pass,
        }
        rows.append(row)
        ok = ok and row_pass
    return {
        "status": "PASS" if ok and len(rows) == 12 else "FAIL",
        "rows": rows,
        "count": len(rows),
        "interpolation_from_G65_used": False,
        "scientific_threshold_changed": False,
        "candidate_membership_authority": "NONE",
        "response_outcome_authority": "NONE",
        "repair_reason": "cross-node transcendental regeneration is not required to be byte-identical; exact committed generator/code/object identity plus machine-roundoff numerical reproduction is required",
        "scaled_inf_tolerance": tol,
    }


def _repair_lock(root: Path) -> dict[str, Any]:
    coeff = root / "phases/p13/coefficient_law_raw_xt/src/p13rawxt/coefficients.py"
    s0k1 = root / "phases/p13/coefficient_law_raw_xt/configs/p13_s0_k1_protocol.json"
    k1cfg = base.load_json(s0k1)
    checks = {
        "coefficients_py_sha_exact_to_S0_K1": coeff.is_file() and base.sha256_file(coeff) == EXPECTED_COEFFICIENTS_PY_SHA256,
        "s0_k1_protocol_sha_exact": s0k1.is_file() and base.sha256_file(s0k1) == EXPECTED_S0_K1_PROTOCOL_SHA256,
        "search_facing_coefficient_jet_order_still_4": int(k1cfg["regime"]["search_facing_coefficient_jet_order"]) == EXPECTED_S0_K1_SEARCH_JET_ORDER,
    }
    return {
        "stage": "P13-S2-K4R_COEFFICIENT_REPRODUCIBILITY_ENGINEERING_REPAIR",
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "failure_classification": "ENGINEERING_FLOAT_REPRODUCIBILITY_PRECHECK" if all(checks.values()) else "SOURCE_PROVENANCE_MISMATCH",
        "scientific_protocol_changed": False,
        "candidate_membership_changed": False,
        "response_threshold_changed": False,
        "reference_fidelity_policy_changed": False,
        "solver_semantics_changed": False,
        "data_boundary_changed": False,
        "new_proxy_or_filter": False,
        "candidate_specific_rescue": False,
        "comparison_policy": {
            "generator_commitment": "exact SHA/semantic verification remains in K4 coefficient reopen",
            "S0_K1_coefficient_generation_code": "exact SHA lock",
            "K0_authoritative_search_objects": "exact file SHA and semantic digest lock",
            "regenerated_x_t_q": "bit exact",
            "regenerated_coefficient_derivative_arrays": "scaled Linf <= 4096*float64_eps",
            "scaled_inf_tolerance": FLOAT_REPRO_SCALED_INF_TOL,
        },
    }


def _repair_dir(root: Path) -> Path:
    cfg = base.load_json(root / "phases/p13/coefficient_law_raw_xt/configs/p13_s2_k4_protocol.json")
    k3run = base.resolve_marker(root, cfg["k3_run_marker"])
    d = k3run / "K4_development_response_reference_control_first"
    d.mkdir(exist_ok=True)
    return d


def run(root: Path) -> int:
    root = root.resolve()
    k4 = _repair_dir(root)
    lock = _repair_lock(root)
    base.write_json(k4 / "K4R_ENGINEERING_REPAIR_LOCK.json", lock)
    if lock["status"] != "PASS":
        return 2

    # Explicit repair: only replace the invalid bitwise regeneration gate.
    original_validator = base.validate_regenerated_coefficients
    base.validate_regenerated_coefficients = validate_regenerated_coefficients_roundoff
    try:
        rc = base.run(root)
    except Exception as exc:
        base.write_json(k4 / "K4R_ENGINEERING_REPAIR_EXECUTION.json", {
            "status": "FAIL",
            "exception_type": type(exc).__name__,
            "exception": str(exc),
            "traceback": traceback.format_exc(),
        })
        raise
    finally:
        base.validate_regenerated_coefficients = original_validator

    base.write_json(k4 / "K4R_ENGINEERING_REPAIR_EXECUTION.json", {
        "status": "PASS" if rc == 0 else "FAIL",
        "base_K4_return_code": int(rc),
        "same_authoritative_K4_directory_reused": True,
        "DEVELOPMENT_response_was_already_legally_opened_before_repair": True,
        "candidate_response_executed_by_repair": False,
    })

    if rc == 0:
        overlay_basis = {
            "base_K4_semantic_output_digest": base.load_json(k4 / "K4_SEMANTIC_OUTPUT_DIGEST.json")["semantic_output_digest"],
            "K4R_engineering_repair_lock_sha256": base.sha256_file(k4 / "K4R_ENGINEERING_REPAIR_LOCK.json"),
            "K4R_engineering_repair_execution_sha256": base.sha256_file(k4 / "K4R_ENGINEERING_REPAIR_EXECUTION.json"),
            "coefficient_reproduction_precheck_sha256": base.sha256_file(k4 / "K4_HIGH_GRID_COEFFICIENT_REPRODUCTION_PRECHECK.json"),
        }
        sem = base.sha256_bytes(base.canonical_json_bytes(overlay_basis))
        base.write_json(k4 / "K4R_SEMANTIC_OUTPUT_DIGEST.json", {"semantic_output_digest": sem, "basis": overlay_basis})
        summary = base.load_json(k4 / "K4_SCIENTIFIC_SUMMARY.json")
        summary["K4R_engineering_repair"] = "COEFFICIENT_REPRODUCIBILITY_PRECHECK_ROUNDOFF_REPAIR"
        summary["K4R_semantic_output_digest"] = sem
        base.write_json(k4 / "K4_SCIENTIFIC_SUMMARY.json", summary)
    return rc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-root", required=True)
    args = ap.parse_args()
    return run(Path(args.project_root))


if __name__ == "__main__":
    raise SystemExit(main())
