from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import sys
import tarfile
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import numpy as np

from .ast_runtime import FieldJetInterpolator, evaluate_pair_jet, grid_coefficient_derivatives
from .coefficients import canonical_json_bytes, search_object_semantic_digest, sha256_bytes
from .family_evaluator import (
    FieldView,
    _p11_imports,
    _probe_source,
    direct_margin_vector,
    evaluate_pair_on_field,
    evaluate_validity_variable,
    load_field_views,
    load_npz,
    operator_on_variable,
)

EXPECTED_ACTIVE_CONTEXT_SHA256 = "3db057955859653c35853b8b2dd2b281e1c4429312ac969e3b9a18b4e1d56db0"
EXPECTED_K2A_PROTOCOL_SHA256 = "c22178d8dba3fd1f08b9df6b012cfe50e2ba204f8771e7b41086d852fc0f3ec6"
EXPECTED_K2A_SOURCE_SHA256 = "5dc0c44cffa663971b70333018b8db42b4e61836a8b2bcca87c11da626f5e96a"
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


def _line_at(path: Path, offset: int) -> dict[str, Any]:
    with path.open("rb") as f:
        f.seek(int(offset))
        raw = f.readline()
    if not raw.strip():
        raise RuntimeError(f"empty locator row: {path}@{offset}")
    return json.loads(raw)


def _npz_from_bytes(data: bytes) -> dict[str, np.ndarray]:
    with np.load(io.BytesIO(data), allow_pickle=False) as z:
        return {k: np.asarray(z[k]) for k in z.files}


def _private_views(archive: Path, commitment: dict[str, Any], grid_n: int, probe_grid: int) -> list[FieldView]:
    if not archive.is_file():
        raise FileNotFoundError(archive)
    if _sha256_path(archive) != commitment["archive_sha256"]:
        raise RuntimeError("within-family private archive SHA mismatch")
    rows = {r["field_id"]: r for r in commitment["public_row_commitments"]}
    views: list[FieldView] = []
    with tarfile.open(archive, "r:xz") as tf:
        names = {m.name: m for m in tf.getmembers() if m.isfile()}
        for fid in sorted(rows):
            row = rows[fid]
            gname = f"{fid}_generator.json"
            if gname not in names:
                raise RuntimeError(f"missing committed generator {gname}")
            gen_data = tf.extractfile(names[gname]).read()
            gen = json.loads(gen_data)
            if sha256_bytes(canonical_json_bytes(gen)) != row["generator_semantic_digest"]:
                raise RuntimeError(f"generator semantic digest mismatch {fid}")
            arrays_by_grid: dict[int, dict[str, np.ndarray]] = {}
            for obj in row["search_object_commitments"]:
                g = int(obj["grid"])
                if g not in {int(grid_n), int(probe_grid)}:
                    continue
                name = f"{fid}_G{g}.npz"
                if name not in names:
                    raise RuntimeError(f"missing committed object {name}")
                data = tf.extractfile(names[name]).read()
                if hashlib.sha256(data).hexdigest() != obj["sha256"]:
                    raise RuntimeError(f"private object SHA mismatch {name}")
                arr = _npz_from_bytes(data)
                if search_object_semantic_digest(arr) != obj["semantic_digest"]:
                    raise RuntimeError(f"private semantic digest mismatch {name}")
                arrays_by_grid[g] = arr
            if int(grid_n) not in arrays_by_grid or int(probe_grid) not in arrays_by_grid:
                raise RuntimeError(f"missing requested grids for {fid}")
            views.append(FieldView(fid, row["role"], int(grid_n), arrays_by_grid[int(grid_n)], FieldJetInterpolator(arrays_by_grid[int(probe_grid)], 4)))
    return views


def _verify_cross_objects(k1_run: Path, commitments: list[dict[str, Any]], grids: set[int]) -> None:
    for row in commitments:
        if int(row["grid"]) not in grids:
            continue
        p = k1_run / row["path"]
        if not p.is_file() or _sha256_path(p) != row["sha256"]:
            raise RuntimeError(f"cross-family diagnostic object mismatch: {p}")
        arr = load_npz(p)
        if search_object_semantic_digest(arr) != row["semantic_digest"]:
            raise RuntimeError(f"cross-family semantic mismatch: {p}")


def _mean_train_arrays(fields: list[FieldView]) -> dict[str, np.ndarray]:
    if len(fields) != 6:
        raise ValueError("six TRAIN fields required for family-mean ablation")
    first = fields[0].arrays
    out = {"x": np.asarray(first["x"]), "t": np.asarray(first["t"]), "q": np.asarray(first["q"])}
    keys = sorted(k for k in first if k.startswith("a_d"))
    for k in keys:
        out[k] = np.mean(np.stack([np.asarray(f.arrays[k], dtype=np.float64) for f in fields], axis=0), axis=0)
    return out


def _eval_substituted(pair: dict[str, Any], theta: list[float], actual: FieldView, mean_main: dict[str, np.ndarray], mean_probe: FieldJetInterpolator, base: dict[str, Any]) -> dict[str, Any]:
    start = time.perf_counter()
    arrays = actual.arrays
    from p11rawxt_validity import GridSpec
    grid = GridSpec(np.asarray(arrays["x"], float), np.asarray(arrays["t"], float))
    xm, tm = grid.mesh
    try:
        raw = evaluate_pair_jet(pair["raw_X_AST"], pair["raw_T_AST"], theta, xm, tm, grid_coefficient_derivatives(mean_main, 4), 4)
        validity_num = base["validity"]
        source = _probe_source(grid, int(validity_num["inverse_probe_grid_per_axis"]), tuple(validity_num["inverse_probe_local_coordinates"]))
        pd = mean_probe.derivatives(source[:, 0], source[:, 1])
        probe_raw = evaluate_pair_jet(pair["raw_X_AST"], pair["raw_T_AST"], theta, source[:, 0], source[:, 1], pd, 4)
        target = np.column_stack([probe_raw["X"], probe_raw["T"]])
        validity = evaluate_validity_variable(raw, grid, arrays["a_d0_0"], source, target, validity_num, float(validity_num["inverse_roundtrip_tolerance"]))
    except Exception as exc:
        return {"field_id": actual.field_id, "stage_index": 0, "highest_feasibility_level": "NONE", "J_princ": None, "exception": f"{type(exc).__name__}:{exc}", "elapsed_seconds": time.perf_counter() - start}
    rec: dict[str, Any] = {"field_id": actual.field_id, "stage_index": validity["stage_index"], "highest_feasibility_level": validity["highest_feasibility_level"], "J_princ": None, "direct_margin_vector": list(direct_margin_vector(validity))}
    if validity["overall_valid"]:
        try:
            gauged, grec = _p11_imports()["gauge_second_jet"](raw)
            if gauged is None:
                raise ValueError(f"gauge second jet failed {grec}")
            _, op = operator_on_variable(gauged, grid, arrays, base["operator"]["space"], base["operator"]["numerical"])
            rec.update(op)
        except Exception as exc:
            rec["stage_index"] = 4
            rec["highest_feasibility_level"] = "F3"
            rec["operator_exception"] = f"{type(exc).__name__}:{exc}"
    rec["elapsed_seconds"] = time.perf_counter() - start
    return rec


def _summary(records: list[dict[str, Any]], identity_j: list[float] | None = None) -> dict[str, Any]:
    js = [None if r.get("J_princ") is None else float(r["J_princ"]) for r in records]
    resolved = all(x is not None and math.isfinite(x) for x in js)
    out: dict[str, Any] = {
        "field_ids": [r["field_id"] for r in records],
        "highest_levels": [r.get("highest_feasibility_level") for r in records],
        "J_i": js,
        "all_F4_and_J_resolved": bool(resolved and all(int(r.get("stage_index", 0)) == 5 for r in records)),
    }
    if resolved:
        vals = np.asarray(js, dtype=float)
        out["J_RMS"] = float(np.sqrt(np.mean(vals * vals)))
        out["J_max"] = float(np.max(vals))
        if identity_j is not None:
            ids = np.asarray(identity_j, dtype=float)
            out["identity_J_i"] = [float(x) for x in ids]
            out["per_field_ratio_to_identity"] = [float(x / y) for x, y in zip(vals, ids)]
            out["relative_RMS_to_identity"] = float(np.sqrt(np.mean((vals / ids) ** 2)))
    return out


def _lower_order_aggregate(records: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [r.get("lower_order_diagnostics") for r in records if r.get("lower_order_diagnostics")]
    if len(rows) != len(records):
        return {"resolved_all_fields": False, "resolved_fields": len(rows), "total_fields": len(records)}
    return {
        "resolved_all_fields": True,
        "max_abs_L_T_over_C_TT": float(max(r["max_abs_L_T_over_C_TT"] for r in rows)),
        "max_abs_L_X_over_C_TT": float(max(r["max_abs_L_X_over_C_TT"] for r in rows)),
        "min_C_TT": float(min(r["min_C_TT"] for r in rows)),
        "max_abs_q_over_C_TT": float(max(r["max_abs_q_over_C_TT"] for r in rows)),
        "max_abs_inv_C_TT": float(max(r["max_abs_inv_C_TT"] for r in rows)),
    }


def _rel_disc(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or not math.isfinite(a) or not math.isfinite(b):
        return None
    return abs(float(a) - float(b)) / max(abs(float(b)), 1e-15)


def _candidate_from_locator(root: Path, loc: dict[str, Any]) -> dict[str, Any]:
    bpath = root / loc["branch_registry_path"]
    spath = root / loc["skeleton_registry_path"]
    b = _line_at(bpath, int(loc["branch_registry_byte_offset"]))
    s = _line_at(spath, int(loc["skeleton_registry_byte_offset"]))
    if b["scientific_branch_id"] != loc["scientific_branch_id"]:
        raise RuntimeError("branch locator scientific ID mismatch")
    if int(b["proposal_index"]) != int(s["proposal_index"]):
        raise RuntimeError("branch/skeleton proposal mismatch")
    if b["structural_hash"] != s["structural_hash"]:
        raise RuntimeError("branch/skeleton structural hash mismatch")
    return {"locator": loc, "branch": b, "pair": s["pair"], "theta": [float(x) for x in b["theta_vector"]]}


def _identity_J(fields: list[FieldView], base: dict[str, Any]) -> list[float]:
    from .calibration_instruments import build_identity_pair
    pair = build_identity_pair(base["caps"])
    rows = [evaluate_pair_on_field(pair, [], f, base["validity"], float(base["validity"]["inverse_roundtrip_tolerance"]), base["operator"]["space"], base["operator"]["numerical"]) for f in fields]
    if not all(r.get("J_princ") is not None and int(r.get("stage_index", 0)) == 5 for r in rows):
        raise RuntimeError("identity diagnostic baseline unresolved")
    return [float(r["J_princ"]) for r in rows]


def _worker_init(root_s: str, k1_s: str, private_archive_s: str, commitment: dict[str, Any], base: dict[str, Any]) -> None:
    root = Path(root_s)
    for p in [root / "phases/p13/coefficient_law_raw_xt/src", root / "phases/p11/raw_xt_td/src"]:
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    os.environ.update({k: "1" for k in ["OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"]})
    k1 = Path(k1_s)
    train33 = load_field_views(k1, "TRAIN_OPERATOR", 33)
    train65 = load_field_views(k1, "TRAIN_OPERATOR", 65)
    cross33 = load_field_views(k1, "OPENED_TRANSFER_DIAGNOSTIC", 33)
    cross65 = load_field_views(k1, "OPENED_TRANSFER_DIAGNOSTIC", 65)
    within33 = _private_views(Path(private_archive_s), commitment, 33, 65)
    within65 = _private_views(Path(private_archive_s), commitment, 65, 65)
    mean33 = _mean_train_arrays(train33)
    mean65 = _mean_train_arrays(train65)
    global _WORKER
    _WORKER = {
        "base": base,
        "train33": train33,
        "train65": train65,
        "cross33": cross33,
        "cross65": cross65,
        "within33": within33,
        "within65": within65,
        "mean33": mean33,
        "mean_probe": FieldJetInterpolator(mean65, 4),
    }


def _eval_task(task: dict[str, Any]) -> dict[str, Any]:
    w = _WORKER
    base = w["base"]
    pair, theta = task["pair"], task["theta"]
    val = base["validity"]
    inv = float(val["inverse_roundtrip_tolerance"])
    space, num = base["operator"]["space"], base["operator"]["numerical"]

    train65_rows = [evaluate_pair_on_field(pair, theta, f, val, inv, space, num) for f in w["train65"]]
    cross33_rows = [evaluate_pair_on_field(pair, theta, f, val, inv, space, num) for f in w["cross33"]]
    cross65_rows = [evaluate_pair_on_field(pair, theta, f, val, inv, space, num) for f in w["cross65"]]
    within33_rows = [evaluate_pair_on_field(pair, theta, f, val, inv, space, num) for f in w["within33"]]
    within65_rows = [evaluate_pair_on_field(pair, theta, f, val, inv, space, num) for f in w["within65"]]
    abl_rows = [_eval_substituted(pair, theta, f, w["mean33"], w["mean_probe"], base) for f in w["train33"]]

    orig_j = float(task["branch"]["J_family"])
    abl = _summary(abl_rows)
    if abl.get("all_F4_and_J_resolved"):
        ja = float(abl["J_RMS"])
        abl.update({"J_family_original_G33": orig_j, "Delta_coef_abs": ja - orig_j, "Delta_coef_rel": ja / orig_j - 1.0, "ablation_valid_all_fields": True})
    else:
        abl.update({"J_family_original_G33": orig_j, "Delta_coef_abs": None, "Delta_coef_rel": None, "ablation_valid_all_fields": False})

    c33, c65 = _summary(cross33_rows), _summary(cross65_rows)
    w33, w65 = _summary(within33_rows), _summary(within65_rows)
    discs = []
    for a, b in zip(c33["J_i"], c65["J_i"]):
        d = _rel_disc(a, b)
        if d is not None: discs.append(d)
    for a, b in zip(w33["J_i"], w65["J_i"]):
        d = _rel_disc(a, b)
        if d is not None: discs.append(d)
    max_disc = max(discs) if discs else None

    return {
        "scientific_branch_id": task["branch"]["scientific_branch_id"],
        "arm": task["branch"]["arm"],
        "paired_seed": int(task["branch"]["paired_seed"]),
        "proposal_index": int(task["branch"]["proposal_index"]),
        "TRAIN_membership_status": task["membership_status"],
        "S2_eligible_from_TRAIN": task["membership_status"] == "OPERATOR_QUALIFIED_TRAIN",
        "coefficient_dependent_syntax": bool(task["branch"].get("coefficient_dependent_syntax")),
        "zero_refit": True,
        "transfer": {"within_family_G33": w33, "within_family_G65": w65, "cross_family_G33": c33, "cross_family_G65": c65, "max_G33_G65_relative_J_discrepancy": max_disc, "numerical_sensitivity_flag": "STABLE_WITHIN_TAU" if max_disc is not None and max_disc <= 0.005 else "DIAGNOSTIC_NUMERICAL_SENSITIVITY_OR_UNRESOLVED"},
        "coefficient_ablation": abl,
        "lower_order": {"TRAIN_G65": _lower_order_aggregate(train65_rows), "WITHIN_FAMILY_G65": _lower_order_aggregate(within65_rows), "CROSS_FAMILY_G65": _lower_order_aggregate(cross65_rows)},
    }


def _aggregate_candidate_results(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def vals(path: tuple[str, ...]) -> list[float]:
        out=[]
        for r in rows:
            x: Any = r
            for k in path:
                if not isinstance(x, dict) or k not in x:
                    x = None; break
                x = x[k]
            if x is not None and isinstance(x, (int, float)) and math.isfinite(float(x)):
                out.append(float(x))
        return out
    def stats(xs: list[float]) -> dict[str, Any]:
        if not xs: return {"n":0}
        a=np.asarray(xs,float)
        return {"n":len(xs),"min":float(np.min(a)),"median":float(np.median(a)),"p90":float(np.quantile(a,0.9)),"max":float(np.max(a))}
    return {
        "rows": len(rows),
        "clear_rows": sum(r["TRAIN_membership_status"] == "OPERATOR_QUALIFIED_TRAIN" for r in rows),
        "unresolved_rows": sum(r["TRAIN_membership_status"] == "OPERATOR_QUALIFIED_UNRESOLVED" for r in rows),
        "coefficient_dependent_rows": sum(bool(r["coefficient_dependent_syntax"]) for r in rows),
        "within_family_relative_RMS_to_identity": stats(vals(("transfer","within_family_G65","relative_RMS_to_identity"))),
        "cross_family_relative_RMS_to_identity": stats(vals(("transfer","cross_family_G65","relative_RMS_to_identity"))),
        "G33_G65_max_relative_discrepancy": stats(vals(("transfer","max_G33_G65_relative_J_discrepancy"))),
        "Delta_coef_rel": stats(vals(("coefficient_ablation","Delta_coef_rel"))),
        "ablation_invalid_or_unresolved": sum(not bool(r["coefficient_ablation"].get("ablation_valid_all_fields")) for r in rows),
        "TRAIN_lower_order_max_L_T": stats(vals(("lower_order","TRAIN_G65","max_abs_L_T_over_C_TT"))),
        "TRAIN_lower_order_max_L_X": stats(vals(("lower_order","TRAIN_G65","max_abs_L_X_over_C_TT"))),
        "TRAIN_lower_order_min_C_TT": stats(vals(("lower_order","TRAIN_G65","min_C_TT"))),
    }


def _hit_rate_summary(run: Path, identity_j: float, tau: float, bins: list[Any]) -> dict[str, Any]:
    edges = [float("inf") if x == "inf" else float(x) for x in bins]
    out: dict[str, Any] = {}
    for arm in ["FULL-V1", "FULL-V2"]:
        rows=[]
        for seed in [1,2,3,4]:
            unit=run/"units"/f"seed_{seed:02d}"/arm
            props=[]; needed=set()
            with (unit/"proposal_ledger.jsonl").open() as f:
                for line in f:
                    if not line.strip(): continue
                    x=json.loads(line); parents=list(x.get("parent_branch_hashes") or [])
                    if not parents: continue
                    needed.update(parents)
                    props.append((x,parents))
            pmap={}
            with (unit/"branch_registry.jsonl").open() as f:
                for line in f:
                    if not line.strip(): continue
                    b=json.loads(line); h=b.get("fit_provenance",{}).get("fitter_candidate_branch_hash")
                    if h in needed:
                        j=float(b["J_family"]); pmap[h]=j if h not in pmap else min(pmap[h],j)
            for x,parents in props:
                known=[pmap[p] for p in parents if p in pmap]
                if not known: continue
                parent=min(known); child=x.get("J_family")
                clear=child is not None and math.isfinite(float(child)) and float(child) < parent*(1.0-tau)
                anyhit=child is not None and math.isfinite(float(child)) and float(child) < parent
                rows.append({"seed":seed,"operation":x.get("operation"),"parent_norm":parent/identity_j,"clear_hit":bool(clear),"any_hit":bool(anyhit),"child_resolved":child is not None})
        summary={}
        for op in sorted({r["operation"] for r in rows} | {"ALL"}):
            rr=rows if op=="ALL" else [r for r in rows if r["operation"]==op]
            br=[]
            for lo,hi in zip(edges[:-1],edges[1:]):
                z=[r for r in rr if r["parent_norm"]>=lo and r["parent_norm"]<hi]
                br.append({"lo":lo,"hi":"inf" if math.isinf(hi) else hi,"n":len(z),"child_resolved":sum(r["child_resolved"] for r in z),"clear_hits":sum(r["clear_hit"] for r in z),"any_hits":sum(r["any_hit"] for r in z),"clear_hit_rate":None if not z else sum(r["clear_hit"] for r in z)/len(z)})
            summary[op]={"rows":len(rr),"bins":br}
        out[arm]=summary
    return out



def _route_interpretation_from_k2a(k2a: dict[str, Any]) -> dict[str, Any]:
    counts=k2a.get("cohort_counts",{})
    effects=k2a.get("paired_arm_effects",{}).get("equal_structural_proposals",{})
    front=k2a.get("coefficient_dependent_vs_free_frontier_effects",{})
    clear_full=int(counts.get("FULL_clear",0))>0
    def consistent(key: str) -> bool:
        return effects.get(key,{}).get("summary",{}).get("stability")=="CONSISTENT_ROUTE_FEASIBILITY_DIRECTION" and int(effects.get(key,{}).get("summary",{}).get("left_clear",0))==4
    coeff_access=consistent("FULL-V1__vs__NULL-V2") or consistent("FULL-V2__vs__NULL-V2")
    frontier_dep=any(v.get("summary",{}).get("stability")=="CONSISTENT_ROUTE_FEASIBILITY_DIRECTION" and int(v.get("summary",{}).get("left_clear",0))==4 for v in front.values())
    pattern_a=clear_full and (coeff_access or frontier_dep)
    v2_vs_v1=effects.get("FULL-V2__vs__FULL-V1",{}).get("summary",{})
    v2_stable_adv=v2_vs_v1.get("stability")=="CONSISTENT_ROUTE_FEASIBILITY_DIRECTION" and int(v2_vs_v1.get("left_clear",0))==4
    pattern_b=pattern_a and not v2_stable_adv
    patterns=[]
    if pattern_a: patterns.append("A")
    if pattern_b: patterns.append("B")
    return {
        "K2A_route_pattern":"+".join(patterns) if patterns else "NOT_AUTO_CLASSIFIED",
        "Pattern_A_supported":pattern_a,
        "Pattern_B_supported":pattern_b,
        "basis":{
            "clear_FULL_exists":clear_full,
            "stable_FULL_vs_NULL_equal_proposals":coeff_access,
            "stable_coefficient_dependent_frontier":frontier_dep,
            "stable_FULL_V2_vs_FULL_V1_equal_proposals":v2_stable_adv
        },
        "interpretation":"constructive coefficient-access route supported; additive-root V2 is neutral/negative in the primary cohort; do not redesign V2 before prospective evaluation of the frozen FULL cohort" if pattern_a and pattern_b else ("constructive coefficient-access route supported" if pattern_a else "route interpretation requires the ACTIVE Pattern A-F table; K2B diagnostics have no authority to repair the completed search"),
        "diagnostic_results_may_change_route_or_membership":False
    }

def _verify_entry(root: Path, cfg: dict[str, Any]) -> tuple[Path, Path, Path, dict[str, Any], dict[str, Any]]:
    active=root/cfg["active_context"]
    k2acfg=root/"phases/p13/coefficient_law_raw_xt/configs/p13_s1_k2a_protocol.json"
    k2asrc=root/"phases/p13/coefficient_law_raw_xt/src/p13rawxt/s1_k2a_train_adjudication.py"
    locks={
        "active_context_exact": active.is_file() and _sha256_path(active)==EXPECTED_ACTIVE_CONTEXT_SHA256,
        "K2A_protocol_exact": k2acfg.is_file() and _sha256_path(k2acfg)==EXPECTED_K2A_PROTOCOL_SHA256,
        "K2A_source_exact": k2asrc.is_file() and _sha256_path(k2asrc)==EXPECTED_K2A_SOURCE_SHA256,
    }
    run=_resolve_marker(root,cfg["s1_run_marker"]); k2a=_resolve_marker(root,cfg["k2a_marker"]); k0r=_resolve_marker(root,cfg["k0r_marker"]); k1=_resolve_marker(root,cfg["k1_open_marker"])
    if run != k2a:
        raise RuntimeError("K2A marker must point to authoritative S1 run")
    gates={
        "K2A_PASS": (run/"K2A_OVERALL_STATUS.txt").read_text().strip()==cfg["entry_requires"]["K2A_status"],
        "continuation_exact": (run/"K2A_CONTINUATION_STATUS.txt").read_text().strip()==cfg["entry_requires"]["K2A_continuation_status"],
        "next_action_exact": (run/"K2A_NEXT_ACTION.txt").read_text().strip()==cfg["entry_requires"]["K2A_next_action"],
        "membership_frozen": bool(_load_json(run/"K2A_membership_lock.json").get("frozen")),
    }
    if not all(locks.values()) or not all(gates.values()):
        raise RuntimeError(f"K2B entry lock failed locks={locks} gates={gates}")
    commitment=_load_json(k0r/"10_within_family_private_commitment.json")
    archive=Path(commitment["archive_absolute_path"])
    if not archive.is_file() or _sha256_path(archive)!=commitment["archive_sha256"]:
        raise RuntimeError("within-family commitment archive unavailable or changed")
    k0inputs=_load_json(k0r/"03_k1_open_inputs.json")
    _verify_cross_objects(k1,k0inputs["existing_transfer_commitments_metadata_only"],{33,65})
    membership=_load_json(run/"K2A_membership_lock.json")
    entry={"locks":locks,"gates":gates,"authoritative_S1_run":str(run.relative_to(root)),"K0R_run":str(k0r.relative_to(root)),"K1_open_run":str(k1.relative_to(root)),"K1A_K1B_K2A_audit_archives_used_as_runtime_input":False,"within_family_archive_sha256":commitment["archive_sha256"],"within_family_archive_bytes":commitment["archive_bytes"],"within_family_archive_absolute_path":str(archive),"cross_family_objects_verified":True}
    return run,k0r,k1,commitment,{"entry":entry,"membership":membership}


def _load_membership(root: Path, run: Path, membership: dict[str, Any]) -> list[dict[str, Any]]:
    out=[]
    for key,status in [("clear_membership_index","OPERATOR_QUALIFIED_TRAIN"),("unresolved_membership_index","OPERATOR_QUALIFIED_UNRESOLVED")]:
        p=root/membership[key]
        if not p.is_file(): raise FileNotFoundError(p)
        with p.open() as f:
            for line in f:
                if line.strip():
                    x=json.loads(line); x["_membership_status"]=status; out.append(x)
    if len(out) != int(membership["clear_FULL_branch_count"])+int(membership["unresolved_FULL_branch_count"]):
        raise RuntimeError("membership locator count mismatch")
    return out


def _update_rolling_context(root: Path, summary: dict[str, Any]) -> None:
    p=root/"P13_S1_ROLLING_CONTEXT.md"
    if not p.is_file(): return
    marker="<!-- K2B_FORMAL_RESULT -->"
    agg=summary["diagnostic_aggregate"]
    block=f'''## S1-K2B — post-search diagnostics\n\n- `OVERALL_STATUS`: **{summary['OVERALL_STATUS']}**\n- authoritative S1 store remains: `{summary['authoritative_S1_run']}`\n- diagnostic cohort: `{agg['rows']}` frozen FULL branches = `{agg['clear_rows']}` clear TRAIN-qualified + `{agg['unresolved_rows']}` TRAIN numerical-boundary unresolved\n- same AST/theta/gauge, zero refit: `True`\n- opened only the 2 committed within-family and 2 existing cross-family diagnostic coefficient fields\n- DEVELOPMENT / SEALED / response outcomes opened: `False`\n- K2A TRAIN membership changed by K2B: `False`\n- diagnostic branch results remain in the authoritative S1 store under `K2B_diagnostics/`; private within-family payload was read directly from its committed archive and was not copied into the active tree\n- next action on PASS: `P13-S1-K2C_POST_MEMBERSHIP_THEORY_BRIDGE`\n\n{marker}\n'''
    text=p.read_text()
    if marker in text: text=text.replace(marker,block)
    else: text=text.rstrip()+"\n\n"+block
    _write_text(p,text)


def main(argv: list[str] | None = None) -> int:
    ap=argparse.ArgumentParser(); ap.add_argument("--project-root",default="."); ap.add_argument("--workers",type=int,default=16); ap.add_argument("--test-limit",type=int,default=None,help=argparse.SUPPRESS)
    args=ap.parse_args(argv); root=Path(args.project_root).resolve()
    for p in [root/"phases/p13/coefficient_law_raw_xt/src",root/"phases/p11/raw_xt_td/src"]:
        if str(p) not in sys.path: sys.path.insert(0,str(p))
    cfg=_load_json(root/"phases/p13/coefficient_law_raw_xt/configs/p13_s1_k2b_protocol.json")
    run,k0r,k1,commitment,state=_verify_entry(root,cfg)
    membership=state["membership"]
    locators=_load_membership(root,run,membership)
    if args.test_limit is not None: locators=locators[:int(args.test_limit)]
    base=_load_json(root/"phases/p13/coefficient_law_raw_xt/configs/p13_s0_k2_protocol.json")
    base["caps"]=_load_json(root/"phases/p13/coefficient_law_raw_xt/configs/p13_s1_k1_protocol.json")["caps"]
    diag=run/"K2B_diagnostics"; diag.mkdir(exist_ok=True)
    work=run/"K2B_work"; work.mkdir(exist_ok=True)
    partial=work/"partial_results.jsonl"
    completed={}
    if partial.is_file():
        good=[]
        for line in partial.read_text().splitlines():
            if not line.strip(): continue
            try: x=json.loads(line)
            except json.JSONDecodeError: continue
            completed[x["scientific_branch_id"]]=x; good.append(json.dumps(x,sort_keys=True,separators=(",",":")))
        partial.write_text("\n".join(good)+("\n" if good else ""))
    tasks=[]
    for loc in locators:
        if loc["scientific_branch_id"] in completed: continue
        c=_candidate_from_locator(root,loc); c["membership_status"]=loc["_membership_status"]; tasks.append(c)

    workers=max(1,min(int(args.workers),16)); os.environ.update({k:"1" for k in ["OMP_NUM_THREADS","MKL_NUM_THREADS","OPENBLAS_NUM_THREADS","NUMEXPR_NUM_THREADS"]})
    start=time.perf_counter(); last=start; done=len(completed); total=len(locators)
    if tasks:
        with partial.open("a",encoding="utf-8") as out, ProcessPoolExecutor(max_workers=workers,initializer=_worker_init,initargs=(str(root),str(k1),commitment["archive_absolute_path"],commitment,base)) as ex:
            futs={ex.submit(_eval_task,t):t["branch"]["scientific_branch_id"] for t in tasks}
            for fut in as_completed(futs):
                r=fut.result(); completed[r["scientific_branch_id"]]=r; out.write(json.dumps(r,sort_keys=True,separators=(",",":"))+"\n"); out.flush(); done+=1
                now=time.perf_counter()
                if done==total or now-last>=20:
                    elapsed=now-start; rate=max(done-len(locators)+len(tasks),1)/max(elapsed,1e-9); remain=total-done; eta=remain/max(rate,1e-12)
                    print(f"[P13 S1 K2B] processed={done}/{total} current_branch={r['scientific_branch_id'][:12]} elapsed={elapsed:.1f}s rate={rate:.3f}/s ETA={eta/60:.1f}m",flush=True); last=now

    missing=[x["scientific_branch_id"] for x in locators if x["scientific_branch_id"] not in completed]
    if missing: raise RuntimeError(f"missing diagnostic results n={len(missing)}")
    final=diag/"K2B_diagnostic_branch_results.jsonl"
    with final.open("w",encoding="utf-8") as f:
        for loc in locators: f.write(json.dumps(completed[loc["scientific_branch_id"]],sort_keys=True,separators=(",",":"))+"\n")
    rows=[completed[x["scientific_branch_id"]] for x in locators]

    # Identity diagnostic baselines are computed once after the private/cross payload opening is authorized.
    cross33=load_field_views(k1,"OPENED_TRANSFER_DIAGNOSTIC",33); cross65=load_field_views(k1,"OPENED_TRANSFER_DIAGNOSTIC",65)
    within33=_private_views(Path(commitment["archive_absolute_path"]),commitment,33,65); within65=_private_views(Path(commitment["archive_absolute_path"]),commitment,65,65)
    identity={"within_G33":_identity_J(within33,base),"within_G65":_identity_J(within65,base),"cross_G33":_identity_J(cross33,base),"cross_G65":_identity_J(cross65,base)}
    _write_json(diag/"K2B_diagnostic_identity_baselines.json",identity)

    # Add relative transfer quantities deterministically without re-evaluating candidates.
    enriched=[]
    for r in rows:
        rr=json.loads(json.dumps(r))
        for key,ids in [("within_family_G33",identity["within_G33"]),("within_family_G65",identity["within_G65"]),("cross_family_G33",identity["cross_G33"]),("cross_family_G65",identity["cross_G65"])]:
            s=rr["transfer"][key]
            if s.get("J_i") and all(x is not None for x in s["J_i"]):
                a=np.asarray(s["J_i"],float); b=np.asarray(ids,float); s["identity_J_i"]=[float(x) for x in b]; s["per_field_ratio_to_identity"]=[float(x/y) for x,y in zip(a,b)]; s["relative_RMS_to_identity"]=float(np.sqrt(np.mean((a/b)**2)))
        enriched.append(rr)
    rows=enriched
    with final.open("w",encoding="utf-8") as f:
        for r in rows: f.write(json.dumps(r,sort_keys=True,separators=(",",":"))+"\n")

    agg=_aggregate_candidate_results(rows); _write_json(diag/"K2B_diagnostic_aggregate.json",agg)
    k2asummary=_load_json(run/"K2A_scientific_summary.json")
    identity_train=float(_load_json(run/"identity_train_G33.json")["J_family"])
    hit=_hit_rate_summary(run,identity_train,float(cfg["hit_rate_vs_parent_J"]["tau_num"]),cfg["hit_rate_vs_parent_J"]["normalized_parent_J_bins"]); _write_json(diag/"K2B_hit_rate_vs_parent_J.json",hit)
    route=_route_interpretation_from_k2a(k2asummary); _write_json(diag/"K2B_route_interpretation_lock.json",route)
    fairness={"source":"K2A_scientific_summary.json","source_sha256":_sha256_path(run/"K2A_scientific_summary.json"),"paired_arm_effects":k2asummary["paired_arm_effects"],"scalar_composite_created":False}; _write_json(diag/"K2B_fairness_carryforward.json",fairness)
    receipt={"within_family":{"archive_absolute_path":commitment["archive_absolute_path"],"archive_sha256":commitment["archive_sha256"],"archive_bytes":commitment["archive_bytes"],"payload_semantic_digest":commitment["payload_semantic_digest"],"payload_copied_into_active_tree":False,"private_seed_material_copied_into_active_tree":False},"cross_family":{"source_K1_run":str(k1.relative_to(root)),"read_in_place":True,"count":2},"opening_authorized_by":"immutable K2A horizon + continuation decision","DEVELOPMENT_read":False,"SEALED_read":False,"response_outcomes_read":False}; _write_json(diag/"K2B_diagnostic_opening_receipt.json",receipt)
    boundary={"status":"PASS","TRAIN_membership_immutable":True,"diagnostic_transfer_used_for_membership":False,"diagnostic_transfer_used_for_S2_eligibility":False,"candidate_refit":False,"branch_reselection":False,"DEVELOPMENT_read":False,"SEALED_read":False,"response_outcomes_read":False,"private_within_payload_copied_into_active_tree":False,"K1A_K1B_K2A_audit_archives_used_as_runtime_input":False}; _write_json(diag/"K2B_data_boundary_guard.json",boundary)

    # Membership immutability check after diagnostics.
    clearp=root/membership["clear_membership_index"]; unrp=root/membership["unresolved_membership_index"]
    memlock={"clear_path":membership["clear_membership_index"],"clear_sha256":_sha256_path(clearp),"clear_count":membership["clear_FULL_branch_count"],"unresolved_path":membership["unresolved_membership_index"],"unresolved_sha256":_sha256_path(unrp),"unresolved_count":membership["unresolved_FULL_branch_count"],"diagnostics_may_modify_membership":False}; _write_json(diag/"K2B_membership_immutability.json",memlock)
    entry=state["entry"]; _write_json(diag/"K2B_entry_provenance.json",entry)
    source_files=[root/"phases/p13/coefficient_law_raw_xt/configs/p13_s1_k2b_protocol.json",Path(__file__).resolve(),root/"phases/p13/coefficient_law_raw_xt/scripts/run_p13_s1_k2b.sh",root/"phases/p13/coefficient_law_raw_xt/scripts/package_p13_s1_k2b_audit.sh",root/"phases/p13/coefficient_law_raw_xt/tests/test_p13_s1_k2b.py",root/"phases/p13/coefficient_law_raw_xt/docs/P13_S1_K2B_POST_SEARCH_DIAGNOSTICS.md",root/cfg["active_context"]]
    src=[]
    for p in source_files: src.append({"path":str(p.relative_to(root)),"bytes":p.stat().st_size,"sha256":_sha256_path(p)})
    _write_json(diag/"K2B_source_manifest.json",{"files":src})
    status="PASS"; next_action=cfg["next_on_pass"]
    summary={"OVERALL_STATUS":status,"NEXT_ACTION":next_action,"authoritative_S1_run":str(run.relative_to(root)),"K2A_continuation_status":"NOT_AUTHORIZED_BY_PREREGISTERED_TRAIN_ONLY_RULE","search_horizon":8192,"diagnostic_aggregate":agg,"route_interpretation":route,"membership_unchanged":True,"S2_clear_branch_count":membership["clear_FULL_branch_count"],"S2_unresolved_not_eligible_count":membership["unresolved_FULL_branch_count"],"zero_refit":True,"DEVELOPMENT_or_SEALED_opened":False,"response_outcomes_opened":False,"diagnostic_transfer_has_membership_authority":False,"diagnostic_results_path":str(final.relative_to(root)),"diagnostic_results_sha256":_sha256_path(final),"hit_rate_path":str((diag/"K2B_hit_rate_vs_parent_J.json").relative_to(root)),"semantic_output_digest":None}
    sem_basis={"stage":"P13-S1-K2B","config_sha256":_sha256_path(root/"phases/p13/coefficient_law_raw_xt/configs/p13_s1_k2b_protocol.json"),"membership":memlock,"diagnostic_results_sha256":summary["diagnostic_results_sha256"],"aggregate":agg,"route":route,"opening_receipt":receipt,"source_manifest":src}
    sem=sha256_bytes(canonical_json_bytes(sem_basis)); summary["semantic_output_digest"]=sem
    _write_json(diag/"K2B_semantic_output_digest.json",{**sem_basis,"semantic_output_digest":sem}); _write_json(diag/"K2B_scientific_summary.json",summary)
    _write_text(run/"K2B_OVERALL_STATUS.txt",status+"\n"); _write_text(run/"K2B_NEXT_ACTION.txt",next_action+"\n")
    runs=root/"phases/p13/coefficient_law_raw_xt/runs"; _write_text(runs/"LATEST_P13_S1_K2B_RUN.txt",str(run.relative_to(root)))
    _update_rolling_context(root,summary)
    # Work state is not an authoritative scientific object after successful deterministic finalization.
    for p in work.iterdir(): p.unlink()
    work.rmdir()
    print(f"[P13 S1 K2B] processed={len(rows)}/{len(locators)} elapsed={time.perf_counter()-start:.1f}s OVERALL_STATUS={status} NEXT_ACTION={next_action}",flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
