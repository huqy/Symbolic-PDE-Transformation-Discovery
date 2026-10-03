from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import numpy as np

from .ast_runtime import FieldJetInterpolator, TaylorJet2D, evaluate_pair_jet, grid_coefficient_derivatives, indices
from .coefficients import (
    canonical_json_bytes,
    search_object_semantic_digest,
    sha256_bytes,
)
from .family_evaluator import (
    FieldView,
    _p11_imports,
    _probe_source,
    evaluate_family,
    evaluate_validity_variable,
    load_field_views,
)
from .s1_k2b_post_search_diagnostics import _candidate_from_locator, _load_membership

EXPECTED_ACTIVE_CONTEXT_SHA256 = "3db057955859653c35853b8b2dd2b281e1c4429312ac969e3b9a18b4e1d56db0"
EXPECTED_K2B_PROTOCOL_SHA256 = "c8785b70ef80c37b809be8b9ea7deb19f497222008109b8d2fb99570e9ff8046"
EXPECTED_K2B_SOURCE_SHA256 = "59c354de892237b54a177ebf9ce4eb17b52f4feb31b6f76412bc1c9e4416189e"
EXPECTED_K2B_SEMANTIC_DIGEST = "1dfbfbb520dd4ef943c7e7306583eef63fba9c605bfe332b312de07a96e7af8f"
EXPECTED_CLEAR_MEMBERSHIP_SHA256 = "564134db0bee502198729463c64ffe29408b30081ec1af63d86afff0f4da0c84"
EXPECTED_UNRESOLVED_MEMBERSHIP_SHA256 = "4177feffe4417e157f17eb90578b1e749ec06fe0db7935a7ade315c33cbc4f67"

_WORKER: dict[str, Any] = {}


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text())


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, sort_keys=True, indent=2) + "\n")
    os.replace(tmp, path)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def _sha256_path(path: Path, block: int = 16 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(block), b""):
            h.update(b)
    return h.hexdigest()


def _resolve_marker(root: Path, rel: str) -> Path:
    marker = root / rel
    if not marker.is_file():
        raise FileNotFoundError(marker)
    p = Path(marker.read_text().strip())
    if not p.is_absolute():
        p = root / p
    p = p.resolve()
    if root.resolve() not in p.parents:
        raise RuntimeError(f"marker escapes project root: {marker} -> {p}")
    if not p.is_dir():
        raise FileNotFoundError(p)
    return p


def _jet_from_arrays(arrays: dict[str, np.ndarray], order: int = 4) -> TaylorJet2D:
    import math as _math
    shape = np.asarray(arrays["a_d0_0"]).shape
    coeff = {}
    for i, j in indices(order):
        coeff[(i, j)] = np.asarray(arrays[f"a_d{i}_{j}"], dtype=np.float64) / (_math.factorial(i) * _math.factorial(j))
    return TaylorJet2D(coeff, order)


def _arrays_from_jet(template: dict[str, np.ndarray], jet: TaylorJet2D) -> dict[str, np.ndarray]:
    import math as _math
    out = {"x": np.asarray(template["x"]).copy(), "t": np.asarray(template["t"]).copy(), "q": np.asarray(template["q"]).copy()}
    for i, j in indices(jet.order):
        out[f"a_d{i}_{j}"] = np.asarray(jet.coeff[(i, j)] * (_math.factorial(i) * _math.factorial(j)), dtype=np.float64)
    return out


def _scaled_arrays_from_authoritative(arrays: dict[str, np.ndarray], epsilon: float, source_epsilon: float = 0.2) -> dict[str, np.ndarray]:
    """Same TRAIN coefficient shape at a new amplitude: a_eps = a_0.2 ** (epsilon/0.2).

    The construction uses only the authoritative search-facing coefficient jets. At ratio=1
    it returns byte-semantic copies of those arrays rather than regenerating from hidden
    generator parameters.
    """
    ratio = float(epsilon) / float(source_epsilon)
    if abs(ratio - 1.0) <= 1e-15:
        return {k: np.asarray(v).copy() for k, v in arrays.items()}
    base = _jet_from_arrays(arrays, 4)
    scaled = base.pow_scalar(ratio)
    return _arrays_from_jet(arrays, scaled)


def _derived_views(authoritative_train: list[FieldView], epsilon: float, source_epsilon: float = 0.2) -> list[FieldView]:
    views = []
    for f in authoritative_train:
        arr = _scaled_arrays_from_authoritative(f.arrays, float(epsilon), float(source_epsilon))
        views.append(FieldView(f.field_id, f"TRAIN_SHAPE_EPS_{epsilon:.2f}", f.grid_n, arr, FieldJetInterpolator(arr, 4)))
    return views


def _verify_train_scaling_identity(authoritative_train: list[FieldView]) -> dict[str, Any]:
    rows=[]
    for f in authoritative_train:
        arr=_scaled_arrays_from_authoritative(f.arrays,0.2,0.2)
        got=search_object_semantic_digest(arr); expected=search_object_semantic_digest(f.arrays)
        rows.append({"field_id":f.field_id,"scaled_epsilon_0p20_semantic_digest":got,"authoritative_semantic_digest":expected,"exact":got==expected})
    if not all(r["exact"] for r in rows):
        raise RuntimeError("ratio-one epsilon scaling changed authoritative TRAIN semantics")
    return {"grid":authoritative_train[0].grid_n if authoritative_train else None,"rows":rows,"all_exact":True,"construction":"authoritative a-jets raised locally to epsilon/0.2; ratio=1 exact-copy special case"}


def _b_derivatives_from_authoritative(arrays: dict[str, np.ndarray], source_epsilon: float = 0.2) -> dict[tuple[int,int], np.ndarray]:
    import math as _math
    bj = _jet_from_arrays(arrays, 4).log().scalar_mul(1.0 / float(source_epsilon))
    return {(i,j): np.asarray(bj.coeff[(i,j)] * (_math.factorial(i) * _math.factorial(j)), dtype=np.float64) for i,j in indices(2)}

def _identity_baselines(views_by_eps: dict[float, list[FieldView]], base: dict[str, Any]) -> dict[str, Any]:
    from .calibration_instruments import build_identity_pair
    pair = build_identity_pair(base["caps"])
    val = base["validity"]
    inv = float(val["inverse_roundtrip_tolerance"])
    space, num = base["operator"]["space"], base["operator"]["numerical"]
    out: dict[str, Any] = {}
    for eps, views in sorted(views_by_eps.items()):
        fam = evaluate_family(pair, [], views, val, inv, space, num)
        if fam.get("J_family") is None or int(fam.get("stage_index", 0)) != 5:
            raise RuntimeError(f"identity unresolved at epsilon={eps}")
        out[f"{eps:.2f}"] = {"J_i": [float(r["J_princ"]) for r in fam["per_field"]], "J_family": float(fam["J_family"]), "J_max": float(fam["J_max"])}
    return out


def _canonical_map(pair: dict[str, Any], theta: list[float], field: FieldView, base: dict[str, Any]) -> tuple[dict[str, np.ndarray] | None, dict[str, Any]]:
    from p11rawxt_validity import GridSpec
    arrays = field.arrays
    grid = GridSpec(np.asarray(arrays["x"], float), np.asarray(arrays["t"], float))
    xm, tm = grid.mesh
    try:
        raw = evaluate_pair_jet(pair["raw_X_AST"], pair["raw_T_AST"], theta, xm, tm, grid_coefficient_derivatives(arrays, 4), 4)
        source = _probe_source(grid, int(base["validity"]["inverse_probe_grid_per_axis"]), tuple(base["validity"]["inverse_probe_local_coordinates"]))
        pd = field.probe_interpolator.derivatives(source[:, 0], source[:, 1])
        probe_raw = evaluate_pair_jet(pair["raw_X_AST"], pair["raw_T_AST"], theta, source[:, 0], source[:, 1], pd, 4)
        target = np.column_stack([probe_raw["X"], probe_raw["T"]])
        validity = evaluate_validity_variable(raw, grid, arrays["a_d0_0"], source, target, base["validity"], float(base["validity"]["inverse_roundtrip_tolerance"]))
        if not validity["overall_valid"]:
            return None, {"status": "UNRESOLVED_NOT_F4", "highest": validity["highest_feasibility_level"], "rejection_codes": validity.get("rejection_codes", [])}
        gauged, grec = _p11_imports()["gauge_second_jet"](raw)
        if gauged is None:
            return None, {"status": "UNRESOLVED_GAUGE", "gauge_record": grec}
        return {"X": np.asarray(gauged["X"], float), "T": np.asarray(gauged["T"], float)}, {"status": "RESOLVED", "gauge_record": grec}
    except Exception as exc:
        return None, {"status": "UNRESOLVED_EXCEPTION", "exception": f"{type(exc).__name__}:{exc}"}


def _trap_weights(x: np.ndarray, t: np.ndarray) -> np.ndarray:
    wx = np.empty_like(x, dtype=float); wt = np.empty_like(t, dtype=float)
    wx[0] = 0.5 * (x[1] - x[0]); wx[-1] = 0.5 * (x[-1] - x[-2]); wx[1:-1] = 0.5 * (x[2:] - x[:-2])
    wt[0] = 0.5 * (t[1] - t[0]); wt[-1] = 0.5 * (t[-1] - t[-2]); wt[1:-1] = 0.5 * (t[2:] - t[:-2])
    return wx[:, None] * wt[None, :]


def _linearized_gauge(hx: np.ndarray, ht: np.ndarray, hxx_anchor: float, htt_anchor: float, x: np.ndarray, t: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Linearization of the frozen common translation/common positive scale gauge around identity."""
    s = 0.5 * (float(hxx_anchor) + float(htt_anchor))
    return hx - float(hx[0, 0]) - s * x, ht - float(ht[0, 0]) - s * t


def _alignment_basis(authoritative_train: list[FieldView], grid: int = 65, rank_rel_tol: float = 1e-8) -> dict[str, Any]:
    theory_coeff = np.asarray([0.25, -1.0 / 6.0, 0.5, -0.25, 1.0 / 12.0, 1.0 / 12.0], dtype=float)
    columns: list[list[np.ndarray]] = [[] for _ in range(6)]
    weight_parts: list[np.ndarray] = []
    field_meta = []
    for field in authoritative_train:
        if int(field.grid_n) != int(grid):
            raise RuntimeError("alignment basis requires authoritative TRAIN view at configured grid")
        x1 = np.asarray(field.arrays["x"], dtype=float); t1 = np.asarray(field.arrays["t"], dtype=float)
        x, t = np.meshgrid(x1, t1, indexing="ij")
        bd = _b_derivatives_from_authoritative(field.arrays, 0.2)
        b, bx, bt, bxx, bxt, btt = bd[(0, 0)], bd[(1, 0)], bd[(0, 1)], bd[(2, 0)], bd[(1, 1)], bd[(0, 2)]
        raw = [
            (t * t * bx, np.zeros_like(x), 0.0, 0.0),
            (t ** 3 * bxt, np.zeros_like(x), 0.0, 0.0),
            (np.zeros_like(x), t * b, 0.0, float(b[0, 0])),
            (np.zeros_like(x), t * t * bt, 0.0, 0.0),
            (np.zeros_like(x), t ** 3 * btt, 0.0, 0.0),
            (np.zeros_like(x), t ** 3 * bxx, 0.0, 0.0),
        ]
        w = _trap_weights(x1, t1)
        sw = np.sqrt(w / float(len(authoritative_train)))
        weight_parts.extend([sw.ravel(), sw.ravel()])
        for j, (hx, ht, hxx0, htt0) in enumerate(raw):
            gx, gt = _linearized_gauge(hx, ht, hxx0, htt0, x, t)
            columns[j].extend([gx.ravel(), gt.ravel()])
        field_meta.append({"field_id": field.field_id, "authoritative_search_object_semantic_digest": search_object_semantic_digest(field.arrays)})
    weights = np.concatenate(weight_parts)
    B = np.column_stack([np.concatenate(parts) for parts in columns])
    BW = B * weights[:, None]
    svals = np.linalg.svd(BW, compute_uv=False)
    tol = float(rank_rel_tol) * float(svals[0]) if len(svals) else 0.0
    rank = int(np.sum(svals > tol))
    cond = float(svals[0] / svals[-1]) if rank == 6 and svals[-1] > 0 else float("inf")
    theory = BW @ theory_coeff
    return {
        "B_weighted": BW,
        "weights": weights,
        "theory_coefficients": theory_coeff,
        "theory_tangent_weighted": theory,
        "metadata": {
            "basis": ["t^2*b_x in X", "t^3*b_xt in X", "t*b in T", "t^2*b_t in T", "t^3*b_tt in T", "t^3*b_xx in T"],
            "theory_coefficients": [float(x) for x in theory_coeff],
            "grid": int(grid),
            "rank_relative_tolerance": float(rank_rel_tol),
            "singular_values": [float(x) for x in svals],
            "rank": rank,
            "condition_number": cond,
            "field_provenance": field_meta,
            "gauge_linearization": "G(h)_X=h_X-h_X00-s*x; G(h)_T=h_T-h_T00-s*t; s=0.5*(h_Xx00+h_Tt00)",
        },
    }


def _family_summary(fam: dict[str, Any], identity: dict[str, Any]) -> dict[str, Any]:
    out = {"stage_index": int(fam.get("stage_index", 0)), "highest_feasibility_level": fam.get("highest_feasibility_level"), "J_family": None, "J_max": None, "J_i": [r.get("J_princ") for r in fam.get("per_field", [])]}
    if fam.get("J_family") is not None:
        vals = np.asarray(out["J_i"], dtype=float); ids = np.asarray(identity["J_i"], dtype=float)
        out["J_family"] = float(fam["J_family"]); out["J_max"] = float(fam["J_max"])
        if np.all(np.abs(ids) > 1e-14):
            out["relative_RMS_to_identity"] = float(np.sqrt(np.mean((vals / ids) ** 2)))
            out["identity_baseline_zero_or_near_zero"] = False
        else:
            out["relative_RMS_to_identity"] = None
            out["identity_baseline_zero_or_near_zero"] = True
    return out


def _log_slope(e1: float, j1: float | None, e2: float, j2: float | None) -> float | None:
    if j1 is None or j2 is None or j1 <= 0 or j2 <= 0:
        return None
    return float(math.log(j2 / j1) / math.log(e2 / e1))


def _worker_init(root_s: str, k1_s: str, base: dict[str, Any], cfg: dict[str, Any]) -> None:
    root = Path(root_s)
    for p in [root / "phases/p13/coefficient_law_raw_xt/src", root / "phases/p11/raw_xt_td/src"]:
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    os.environ.update({k: "1" for k in ["OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"]})
    authoritative_train = load_field_views(Path(k1_s), "TRAIN_OPERATOR", int(cfg["epsilon_scaling"]["grid"]), int(cfg["epsilon_scaling"]["grid"]))
    eps_all = [float(cfg["epsilon_scaling"]["neutral_epsilon_auxiliary"])] + [float(x) for x in cfg["epsilon_scaling"]["epsilon_values"]]
    views = {eps: _derived_views(authoritative_train, eps, 0.2) for eps in eps_all}
    identities = _identity_baselines(views, base)
    basis = _alignment_basis(authoritative_train, int(cfg["functional_alignment"]["grid"]), 1e-8)
    global _WORKER
    _WORKER = {"views": views, "identity": identities, "basis": basis, "base": base, "cfg": cfg}


def _alignment_for_candidate(pair: dict[str, Any], theta: list[float]) -> dict[str, Any]:
    w = _WORKER; views = w["views"]; base = w["base"]; basis = w["basis"]
    eps0, h, e2 = 0.0, 0.05, 0.10
    deriv_parts = []
    status_rows = []
    for i in range(6):
        maps = {}
        okay = True
        for eps in [eps0, h, e2]:
            m, rec = _canonical_map(pair, theta, views[eps][i], base)
            status_rows.append({"field_id": views[eps][i].field_id, "epsilon": eps, **rec})
            if m is None:
                okay = False
            else:
                maps[eps] = m
        if not okay:
            return {"status": "ALIGNMENT_UNRESOLVED", "reason": "candidate map not F4/gauge-resolved at all derivative amplitudes", "map_status": status_rows}
        dx = (-3.0 * maps[eps0]["X"] + 4.0 * maps[h]["X"] - maps[e2]["X"]) / (2.0 * h)
        dt = (-3.0 * maps[eps0]["T"] + 4.0 * maps[h]["T"] - maps[e2]["T"]) / (2.0 * h)
        deriv_parts.extend([dx.ravel(), dt.ravel()])
    y = np.concatenate(deriv_parts) * basis["weights"]
    B = basis["B_weighted"]; c = basis["theory_coefficients"]; th = basis["theory_tangent_weighted"]
    meta = basis["metadata"]
    if int(meta["rank"]) < 6:
        return {"status": "ALIGNMENT_UNRESOLVED", "reason": "frozen gauge-aware basis rank deficient", "basis_rank": int(meta["rank"]), "basis_condition_number": meta["condition_number"]}
    yn = float(np.linalg.norm(y)); tn = float(np.linalg.norm(th))
    if not math.isfinite(yn) or yn <= 1e-14 or not math.isfinite(tn) or tn <= 1e-14:
        return {"status": "ALIGNMENT_UNRESOLVED", "reason": "degenerate tangent norm", "candidate_tangent_norm": yn, "theory_tangent_norm": tn}
    beta, *_ = np.linalg.lstsq(B, y, rcond=1e-8)
    proj = B @ beta
    pn = float(np.linalg.norm(y - proj) / yn)
    cosine = float(np.dot(y, th) / (yn * tn))
    tr = float(np.linalg.norm(y - th) / tn)
    amp = float(np.dot(y, th) / max(float(np.dot(th, th)), 1e-30))
    cr = float(np.linalg.norm(beta - c) / max(float(np.linalg.norm(c)), 1e-30))
    return {
        "status": "RESOLVED",
        "fitted_basis_coefficients": [float(x) for x in beta],
        "theory_coefficients": [float(x) for x in c],
        "projection_relative_residual": pn,
        "theory_cosine": cosine,
        "theory_tangent_relative_residual": tr,
        "theory_amplitude_coefficient": amp,
        "relative_coefficient_error": cr,
        "candidate_tangent_norm": yn,
        "theory_tangent_norm": tn,
        "basis_rank": int(meta["rank"]),
        "basis_condition_number": float(meta["condition_number"]),
        "map_status": status_rows,
        "alignment_has_membership_authority": False,
    }


def _eval_task(task: dict[str, Any]) -> dict[str, Any]:
    w = _WORKER; base = w["base"]; cfg = w["cfg"]
    pair, theta = task["pair"], task["theta"]
    val = base["validity"]; inv = float(val["inverse_roundtrip_tolerance"]); space, num = base["operator"]["space"], base["operator"]["numerical"]
    scaling: dict[str, Any] = {}
    for eps in [0.0, 0.05, 0.10, 0.20]:
        fam = evaluate_family(pair, theta, w["views"][eps], val, inv, space, num)
        scaling[f"{eps:.2f}"] = _family_summary(fam, w["identity"][f"{eps:.2f}"])
    j05, j10, j20 = scaling["0.05"]["J_family"], scaling["0.10"]["J_family"], scaling["0.20"]["J_family"]
    scaling["log_slope_0p05_to_0p10"] = _log_slope(0.05, j05, 0.10, j10)
    scaling["log_slope_0p10_to_0p20"] = _log_slope(0.10, j10, 0.20, j20)
    scaling["all_preregistered_positive_eps_resolved"] = all(scaling[f"{e:.2f}"]["J_family"] is not None for e in [0.05, 0.10, 0.20])
    scaling["epsilon_scaling_has_membership_authority"] = False
    alignment = _alignment_for_candidate(pair, theta)
    b = task["branch"]
    return {
        "scientific_branch_id": b["scientific_branch_id"],
        "arm": b["arm"],
        "paired_seed": int(b["paired_seed"]),
        "proposal_index": int(b["proposal_index"]),
        "TRAIN_membership_status": task["membership_status"],
        "S2_eligible_from_TRAIN": task["membership_status"] == "OPERATOR_QUALIFIED_TRAIN",
        "coefficient_dependent_syntax": bool(b.get("coefficient_dependent_syntax")),
        "same_AST_theta_gauge_zero_refit": True,
        "epsilon_scaling": scaling,
        "functional_alignment": alignment,
    }


def _stats(xs: list[float]) -> dict[str, Any]:
    if not xs:
        return {"n": 0}
    a = np.asarray(xs, dtype=float)
    return {"n": len(xs), "min": float(np.min(a)), "median": float(np.median(a)), "p10": float(np.quantile(a, 0.1)), "p90": float(np.quantile(a, 0.9)), "max": float(np.max(a))}


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def vals(fn, subset=None):
        out=[]
        for r in rows:
            if subset is not None and not subset(r):
                continue
            x=fn(r)
            if isinstance(x,(int,float)) and math.isfinite(float(x)):
                out.append(float(x))
        return out
    clear=lambda r:r["TRAIN_membership_status"]=="OPERATOR_QUALIFIED_TRAIN"
    return {
        "rows": len(rows),
        "clear_rows": sum(clear(r) for r in rows),
        "unresolved_rows": sum(not clear(r) for r in rows),
        "positive_epsilon_scaling_resolved_all_rows": sum(bool(r["epsilon_scaling"]["all_preregistered_positive_eps_resolved"]) for r in rows),
        "clear_slope_0p05_to_0p10": _stats(vals(lambda r:r["epsilon_scaling"]["log_slope_0p05_to_0p10"],clear)),
        "clear_slope_0p10_to_0p20": _stats(vals(lambda r:r["epsilon_scaling"]["log_slope_0p10_to_0p20"],clear)),
        "clear_relative_RMS_eps_0p05": _stats(vals(lambda r:r["epsilon_scaling"]["0.05"].get("relative_RMS_to_identity"),clear)),
        "clear_relative_RMS_eps_0p10": _stats(vals(lambda r:r["epsilon_scaling"]["0.10"].get("relative_RMS_to_identity"),clear)),
        "clear_relative_RMS_eps_0p20": _stats(vals(lambda r:r["epsilon_scaling"]["0.20"].get("relative_RMS_to_identity"),clear)),
        "alignment_resolved_all": sum(r["functional_alignment"]["status"]=="RESOLVED" for r in rows),
        "alignment_resolved_clear": sum(clear(r) and r["functional_alignment"]["status"]=="RESOLVED" for r in rows),
        "clear_theory_cosine": _stats(vals(lambda r:r["functional_alignment"].get("theory_cosine"),lambda r:clear(r) and r["functional_alignment"]["status"]=="RESOLVED")),
        "clear_projection_relative_residual": _stats(vals(lambda r:r["functional_alignment"].get("projection_relative_residual"),lambda r:clear(r) and r["functional_alignment"]["status"]=="RESOLVED")),
        "clear_theory_tangent_relative_residual": _stats(vals(lambda r:r["functional_alignment"].get("theory_tangent_relative_residual"),lambda r:clear(r) and r["functional_alignment"]["status"]=="RESOLVED")),
        "clear_relative_coefficient_error": _stats(vals(lambda r:r["functional_alignment"].get("relative_coefficient_error"),lambda r:clear(r) and r["functional_alignment"]["status"]=="RESOLVED")),
    }


def _verify_entry(root: Path, cfg: dict[str, Any]) -> tuple[Path, Path, dict[str, Any], list[FieldView], dict[str, Any]]:
    active = root / cfg["active_context"]
    k2bcfg = root / "phases/p13/coefficient_law_raw_xt/configs/p13_s1_k2b_protocol.json"
    k2bsrc = root / "phases/p13/coefficient_law_raw_xt/src/p13rawxt/s1_k2b_post_search_diagnostics.py"
    locks = {
        "active_context_exact": active.is_file() and _sha256_path(active) == EXPECTED_ACTIVE_CONTEXT_SHA256,
        "K2B_protocol_exact": k2bcfg.is_file() and _sha256_path(k2bcfg) == EXPECTED_K2B_PROTOCOL_SHA256,
        "K2B_source_exact": k2bsrc.is_file() and _sha256_path(k2bsrc) == EXPECTED_K2B_SOURCE_SHA256,
    }
    run = _resolve_marker(root, cfg["s1_run_marker"]); k2b = _resolve_marker(root, cfg["k2b_marker"]); k1 = _resolve_marker(root, cfg["k1_open_marker"])
    if run != k2b:
        raise RuntimeError("K2B marker must point to authoritative S1 run")
    k2bsum = _load_json(run / "K2B_diagnostics/K2B_scientific_summary.json")
    mem = _load_json(run / "K2A_membership_lock.json")
    clearp = root / mem["clear_membership_index"]; unrp = root / mem["unresolved_membership_index"]
    gates = {
        "K2B_PASS": (run / "K2B_OVERALL_STATUS.txt").read_text().strip() == cfg["entry_requires"]["K2B_status"],
        "K2B_next_action_exact": (run / "K2B_NEXT_ACTION.txt").read_text().strip() == cfg["entry_requires"]["K2B_next_action"],
        "K2B_semantic_exact": k2bsum.get("semantic_output_digest") == EXPECTED_K2B_SEMANTIC_DIGEST,
        "clear_membership_sha_exact": clearp.is_file() and _sha256_path(clearp) == EXPECTED_CLEAR_MEMBERSHIP_SHA256,
        "unresolved_membership_sha_exact": unrp.is_file() and _sha256_path(unrp) == EXPECTED_UNRESOLVED_MEMBERSHIP_SHA256,
        "search_horizon_exact": int(k2bsum.get("search_horizon", -1)) == int(cfg["entry_requires"]["K2A_search_horizon"]),
        "membership_unchanged_by_K2B": bool(k2bsum.get("membership_unchanged")),
        "DEVELOPMENT_or_SEALED_unopened": not bool(k2bsum.get("DEVELOPMENT_or_SEALED_opened")),
    }
    if not all(locks.values()) or not all(gates.values()):
        raise RuntimeError(f"K2C entry lock failed locks={locks} gates={gates}")
    authoritative_train = load_field_views(k1, "TRAIN_OPERATOR", int(cfg["epsilon_scaling"]["grid"]), int(cfg["epsilon_scaling"]["grid"]))
    scale_identity = _verify_train_scaling_identity(authoritative_train)
    return run, k1, mem, authoritative_train, {"locks": locks, "gates": gates, "K2B_summary_sha256": _sha256_path(run / "K2B_diagnostics/K2B_scientific_summary.json"), "TRAIN_scaling_identity": scale_identity}


def _predictions(k2b: dict[str, Any], agg: dict[str, Any]) -> dict[str, Any]:
    return {
        "frozen_before_DEVELOPMENT": True,
        "predictions_are_directional_mechanism_predictions_not_gates": True,
        "S2_candidate_membership_unchanged": True,
        "P1_population_operator_advantage": {
            "prediction": "On DEVELOPMENT coefficients, the complete 2307-member clear TRAIN cohort is expected to retain a population-level operator advantage over identity under same AST/theta/gauge and zero refit.",
            "operational_descriptive_check": "report complete-cohort distribution and median relative-to-identity operator score; this prediction is not a candidate gate",
            "basis": {"K2B_within_median": k2b["diagnostic_aggregate"]["within_family_relative_RMS_to_identity"].get("median"), "K2B_cross_median": k2b["diagnostic_aggregate"]["cross_family_relative_RMS_to_identity"].get("median"), "K2C_eps0p20_clear_median": agg["clear_relative_RMS_eps_0p20"].get("median")},
        },
        "P2_transfer_heterogeneity": {
            "prediction": "Transfer will be heterogeneous; TRAIN qualification is not predicted to guarantee better-than-identity performance for every branch on every unseen coefficient.",
            "basis": {"K2B_within_max": k2b["diagnostic_aggregate"]["within_family_relative_RMS_to_identity"].get("max"), "K2B_cross_max": k2b["diagnostic_aggregate"]["cross_family_relative_RMS_to_identity"].get("max")},
        },
        "P3_theory_bridge_association": {
            "prediction": "Small-epsilon scaling and Claim-II functional-alignment metrics may associate descriptively with DEVELOPMENT operator transfer, but no K2C metric may select, rank-delete, or rescue candidates.",
            "alignment_resolved_clear": agg["alignment_resolved_clear"],
        },
        "P4_response_dynamic_range": {
            "prediction": "Because the declared epsilon=0.2, chi<=1, m=2 regime is weak/local, identity and NULL controls may already pass absolute response gates; if candidate/control discrimination is absent, use the preregistered NONDISCRIMINATIVE_ABSOLUTE_GATE classification rather than strengthening the regime post hoc.",
            "forbidden_use": "no redesign of current DEV/SEALED branch after seeing S2 response outcomes",
        },
    }


def _update_rolling_context(root: Path, summary: dict[str, Any]) -> None:
    p = root / "P13_S1_ROLLING_CONTEXT.md"
    if not p.is_file():
        return
    marker = "<!-- K2C_FORMAL_RESULT -->"
    agg = summary["aggregate"]
    block = f'''## S1-K2C — post-membership theory bridge\n\n- `OVERALL_STATUS`: **{summary['OVERALL_STATUS']}**\n- authoritative S1 store remains: `{summary['authoritative_S1_run']}`\n- cohort evaluated without refit: `{agg['rows']}` = `{agg['clear_rows']}` clear + `{agg['unresolved_rows']}` numerical-boundary unresolved\n- epsilon-scaling amplitudes: `0.05, 0.10, 0.20`; neutral `epsilon=0` used only as auxiliary derivative baseline\n- TRAIN-derived scaled coefficient arrays were constructed in memory from authoritative search-facing jets and not persisted; epsilon=0.20 uses an exact-copy path and matched authoritative G65 semantics exactly\n- Claim-II m=2 alignment used the frozen six-channel gauge-linearized basis and no alignment PASS/FAIL threshold\n- K2C outcomes changed S2 membership: `False`\n- DEVELOPMENT / SEALED / response outcomes opened: `False`\n- S1->S2 mechanistic predictions frozen before DEVELOPMENT opening: `True`\n- next action on PASS: `P13-S1-K3_FORMAL_S1_FREEZE`\n\n{marker}\n'''
    text = p.read_text()
    if marker in text:
        text = text.replace(marker, block)
    else:
        text = text.rstrip() + "\n\n" + block
    _write_text(p, text)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--project-root", default="."); ap.add_argument("--workers", type=int, default=16); ap.add_argument("--test-limit", type=int, default=None, help=argparse.SUPPRESS)
    args = ap.parse_args(argv); root = Path(args.project_root).resolve()
    for p in [root / "phases/p13/coefficient_law_raw_xt/src", root / "phases/p11/raw_xt_td/src"]:
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    cfgp = root / "phases/p13/coefficient_law_raw_xt/configs/p13_s1_k2c_protocol.json"; cfg = _load_json(cfgp)
    run, k1, membership, authoritative_train, entry = _verify_entry(root, cfg)
    locators = _load_membership(root, run, membership)
    if args.test_limit is not None:
        locators = locators[:int(args.test_limit)]
    base = _load_json(root / "phases/p13/coefficient_law_raw_xt/configs/p13_s0_k2_protocol.json")
    base["caps"] = _load_json(root / "phases/p13/coefficient_law_raw_xt/configs/p13_s1_k1_protocol.json")["caps"]
    outdir = run / "K2C_theory_bridge"; outdir.mkdir(exist_ok=True)
    # A completed PASS is authoritative and must not be silently recomputed.
    if (run / "K2C_OVERALL_STATUS.txt").is_file() and (run / "K2C_OVERALL_STATUS.txt").read_text().strip() == "PASS":
        if not (outdir / "K2C_scientific_summary.json").is_file():
            raise RuntimeError("K2C PASS marker exists without scientific summary")
        print("[P13 S1 K2C] OVERALL_STATUS=PASS (already frozen; no recomputation)", flush=True)
        return 0
    work = run / "K2C_work"; work.mkdir(exist_ok=True)
    restart_lock_path = work / "restart_lock.json"
    restart_lock = {
        "K2C_config_sha256": _sha256_path(cfgp),
        "K2C_source_sha256": _sha256_path(Path(__file__).resolve()),
        "active_context_sha256": _sha256_path(root / cfg["active_context"]),
        "K2B_semantic_digest": EXPECTED_K2B_SEMANTIC_DIGEST,
        "clear_membership_sha256": _sha256_path(root / membership["clear_membership_index"]),
        "unresolved_membership_sha256": _sha256_path(root / membership["unresolved_membership_index"]),
        "TRAIN_G65_semantic_digests": [search_object_semantic_digest(f.arrays) for f in authoritative_train],
    }
    if restart_lock_path.is_file():
        if _load_json(restart_lock_path) != restart_lock:
            raise RuntimeError("K2C restart lock mismatch; refusing to reuse stale partial results")
    else:
        _write_json(restart_lock_path, restart_lock)
    partial = work / "partial_results.jsonl"
    completed: dict[str, Any] = {}
    if partial.is_file():
        good=[]
        for line in partial.read_text().splitlines():
            if not line.strip(): continue
            try: r=json.loads(line)
            except json.JSONDecodeError: continue
            completed[r["scientific_branch_id"]]=r; good.append(json.dumps(r,sort_keys=True,separators=(",",":")))
        partial.write_text("\n".join(good)+("\n" if good else ""))
    tasks=[]
    for loc in locators:
        if loc["scientific_branch_id"] in completed: continue
        c=_candidate_from_locator(root,loc); c["membership_status"]=loc["_membership_status"]; tasks.append(c)
    workers=max(1,min(int(args.workers),16)); os.environ.update({k:"1" for k in ["OMP_NUM_THREADS","MKL_NUM_THREADS","OPENBLAS_NUM_THREADS","NUMEXPR_NUM_THREADS"]})
    start=time.perf_counter(); last=start; done=len(completed); total=len(locators)
    if tasks:
        with partial.open("a",encoding="utf-8") as f, ProcessPoolExecutor(max_workers=workers,initializer=_worker_init,initargs=(str(root),str(k1),base,cfg)) as ex:
            futs={ex.submit(_eval_task,t):t["branch"]["scientific_branch_id"] for t in tasks}
            for fut in as_completed(futs):
                r=fut.result(); completed[r["scientific_branch_id"]]=r; f.write(json.dumps(r,sort_keys=True,separators=(",",":"))+"\n"); f.flush(); done+=1
                now=time.perf_counter()
                if done==total or now-last>=20:
                    elapsed=now-start; rate=max(done-(total-len(tasks)),1)/max(elapsed,1e-9); eta=(total-done)/max(rate,1e-12)
                    print(f"[P13 S1 K2C] processed={done}/{total} current_branch={r['scientific_branch_id'][:12]} elapsed={elapsed:.1f}s rate={rate:.3f}/s ETA={eta/60:.1f}m",flush=True); last=now
    missing=[x["scientific_branch_id"] for x in locators if x["scientific_branch_id"] not in completed]
    if missing: raise RuntimeError(f"missing K2C results n={len(missing)}")
    final=outdir/"K2C_branch_results.jsonl"
    rows=[completed[x["scientific_branch_id"]] for x in locators]
    with final.open("w",encoding="utf-8") as f:
        for r in rows: f.write(json.dumps(r,sort_keys=True,separators=(",",":"))+"\n")
    agg=_aggregate(rows); _write_json(outdir/"K2C_aggregate.json",agg)
    basis=_alignment_basis(authoritative_train,int(cfg["functional_alignment"]["grid"]),1e-8); basis_meta=basis["metadata"]; _write_json(outdir/"K2C_alignment_basis_lock.json",basis_meta)
    eps_manifest={"persistent_scaled_arrays":False,"grid":int(cfg["epsilon_scaling"]["grid"]),"TRAIN_ratio_one_identity_check":entry["TRAIN_scaling_identity"],"derived_epsilons":[0.0,0.05,0.10,0.20],"construction":"in-memory local Taylor-jet power a_eps = a_authoritative^(epsilon/0.2); no generator parameters are read","source_fields":[{"field_id":f.field_id,"authoritative_search_object_semantic_digest":search_object_semantic_digest(f.arrays)} for f in authoritative_train]}; _write_json(outdir/"K2C_epsilon_shape_manifest.json",eps_manifest)
    k2bsum=_load_json(run/"K2B_diagnostics/K2B_scientific_summary.json"); predictions=_predictions(k2bsum,agg); _write_json(outdir/"K2C_S2_MECHANISTIC_PREDICTIONS.json",predictions)
    triggers={"PLCP":{"triggered":False,"reason":"K2A route Pattern A+B; not Pattern C structural under-attainment"},"reverse_fitter_regret":{"triggered":False,"reason":"2307 clear TRAIN-qualified branches; no fitter-accessibility blocker"},"observability":{"triggered":False,"reason":"No preregistered numeric within-vs-cross divergence threshold exists; do not invent one post hoc. Remains triggered by later S2 operator-transfer failure."},"S2_J_vs_response":{"triggered":False,"reason":"belongs to S2 after prospective response opening"}}; _write_json(outdir/"K2C_deferred_trigger_adjudication.json",triggers)
    memlock={"clear_path":membership["clear_membership_index"],"clear_sha256":_sha256_path(root/membership["clear_membership_index"]),"clear_count":membership["clear_FULL_branch_count"],"unresolved_path":membership["unresolved_membership_index"],"unresolved_sha256":_sha256_path(root/membership["unresolved_membership_index"]),"unresolved_count":membership["unresolved_FULL_branch_count"],"K2C_may_modify_membership":False}; _write_json(outdir/"K2C_membership_immutability.json",memlock)
    boundary={"status":"PASS","TRAIN_generator_provenance_read":False,"scaled_shapes_derived_only_from_authoritative_TRAIN_search_objects":True,"scaled_coefficient_arrays_persisted":False,"candidate_refit":False,"branch_reselection":False,"epsilon_or_alignment_used_for_membership":False,"DEVELOPMENT_read":False,"SEALED_read":False,"response_outcomes_read":False,"historical_response_read":False,"new_search_or_budget":False}; _write_json(outdir/"K2C_data_boundary_guard.json",boundary)
    _write_json(outdir/"K2C_entry_provenance.json",entry)
    source_files=[cfgp,Path(__file__).resolve(),root/"phases/p13/coefficient_law_raw_xt/scripts/run_p13_s1_k2c.sh",root/"phases/p13/coefficient_law_raw_xt/scripts/package_p13_s1_k2c_audit.sh",root/"phases/p13/coefficient_law_raw_xt/tests/test_p13_s1_k2c.py",root/"phases/p13/coefficient_law_raw_xt/docs/P13_S1_K2C_POST_MEMBERSHIP_THEORY_BRIDGE.md",root/cfg["active_context"]]
    src=[{"path":str(p.relative_to(root)),"bytes":p.stat().st_size,"sha256":_sha256_path(p)} for p in source_files]; _write_json(outdir/"K2C_source_manifest.json",{"files":src})
    summary={"OVERALL_STATUS":"PASS","NEXT_ACTION":cfg["next_on_pass"],"authoritative_S1_run":str(run.relative_to(root)),"aggregate":agg,"membership_unchanged":True,"S2_clear_branch_count":membership["clear_FULL_branch_count"],"S2_unresolved_not_eligible_count":membership["unresolved_FULL_branch_count"],"same_AST_theta_gauge_zero_refit":True,"DEVELOPMENT_or_SEALED_opened":False,"response_outcomes_opened":False,"scaled_coefficient_arrays_persisted":False,"S2_mechanistic_predictions_frozen":True,"semantic_output_digest":None}
    sem_basis={"stage":"P13-S1-K2C","config_sha256":_sha256_path(cfgp),"membership":memlock,"branch_results_sha256":_sha256_path(final),"aggregate":agg,"basis":basis_meta,"epsilon_manifest":eps_manifest,"predictions":predictions,"triggers":triggers,"data_boundary":boundary,"source_manifest":src}; sem=sha256_bytes(canonical_json_bytes(sem_basis)); summary["semantic_output_digest"]=sem
    _write_json(outdir/"K2C_semantic_output_digest.json",{**sem_basis,"semantic_output_digest":sem}); _write_json(outdir/"K2C_scientific_summary.json",summary)
    _write_text(run/"K2C_OVERALL_STATUS.txt","PASS\n"); _write_text(run/"K2C_NEXT_ACTION.txt",cfg["next_on_pass"]+"\n")
    runs=root/"phases/p13/coefficient_law_raw_xt/runs"; _write_text(runs/"LATEST_P13_S1_K2C_RUN.txt",str(run.relative_to(root)))
    _update_rolling_context(root,summary)
    for p in work.iterdir(): p.unlink()
    work.rmdir()
    print(f"[P13 S1 K2C] processed={len(rows)}/{len(locators)} elapsed={time.perf_counter()-start:.1f}s OVERALL_STATUS=PASS NEXT_ACTION={cfg['next_on_pass']}",flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
