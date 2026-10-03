from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import sys
import tarfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from multiprocessing import get_context
from pathlib import Path
from typing import Any

import numpy as np
from scipy.integrate import solve_ivp

from .ast_runtime import evaluate_pair_jet
from .calibration_instruments import build_identity_pair, build_null_capacity_pair
from .causal_calibration import (
    apply_gauge,
    causal_solve,
    inverse_transform_second_derivatives,
    source_operator_coefficients,
)
from .coefficients import (
    canonical_json_bytes,
    exponential_coefficient_derivatives,
    make_search_object,
    search_object_semantic_digest,
)
from .family_evaluator import pushforward_variable, load_npz
from .s2_k3_response_protocol_lock import response_interval, classify_branch, control_first_action


def sha256_file(path: Path, block: int = 8 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(block), b""):
            h.update(b)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def count_jsonl(path: Path) -> int:
    with path.open("rb") as f:
        return sum(1 for line in f if line.strip())


def resolve_marker(root: Path, rel_marker: str) -> Path:
    marker = root / rel_marker
    if not marker.is_file():
        raise FileNotFoundError(f"missing marker {marker}")
    rel = marker.read_text(encoding="utf-8").strip()
    target = root / rel
    if not target.exists():
        raise FileNotFoundError(f"marker target missing {target}")
    return target


def _safe_member_names(tf: tarfile.TarFile) -> list[str]:
    names = []
    for m in tf.getmembers():
        name = m.name
        p = Path(name)
        if p.is_absolute() or ".." in p.parts:
            raise RuntimeError(f"unsafe tar member: {name}")
        names.append(name)
    return names


def _read_tar_json(tf: tarfile.TarFile, member: str) -> Any:
    f = tf.extractfile(member)
    if f is None:
        raise FileNotFoundError(member)
    return json.loads(f.read().decode("utf-8"))


def _semantic_payload_digest(payload: dict[str, Any]) -> str:
    obj = {k: v for k, v in payload.items() if k != "master_seed_hex"}
    return sha256_bytes(canonical_json_bytes(obj))


def open_response_payload(archive: Path, commitment: dict[str, Any], expected_cases: list[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    checks: dict[str, bool] = {
        "archive_exists": archive.is_file(),
        "archive_bytes": archive.is_file() and archive.stat().st_size == int(commitment["archive_bytes"]),
        "archive_sha256": archive.is_file() and sha256_file(archive) == commitment["archive_sha256"],
    }
    if not all(checks.values()):
        return {}, {"status": "FAIL", "checks": checks}
    with tarfile.open(archive, "r:xz") as tf:
        names = _safe_member_names(tf)
        checks["payload_manifest_only_required"] = "payload_manifest.json" in names
        payload = _read_tar_json(tf, "payload_manifest.json")
    checks["schema"] = payload.get("schema") == "P13_PRIVATE_RESPONSE_PAYLOAD_V1"
    checks["role"] = payload.get("coefficient_role") == "DEVELOPMENT_COEF"
    checks["q"] = abs(float(payload.get("q", math.nan)) - 1.0) < 1e-15
    checks["semantic_digest"] = _semantic_payload_digest(payload) == commitment["payload_semantic_digest"]
    rows = payload.get("rows", [])
    checks["row_count_32"] = len(rows) == int(commitment["row_count"]) == 32
    expected_fields = [f"P13_DEVELOPMENT_COEF_{i:02d}" for i in range(1, 5)]
    checks["field_case_product"] = sorted((r.get("field_id"), r.get("case_type")) for r in rows) == sorted((f, c) for f in expected_fields for c in expected_cases)
    checks["homogeneous_dirichlet"] = all(r.get("boundary_condition") == "homogeneous_dirichlet" for r in rows)
    public = [{k: r[k] for k in ("field_id", "case_type", "boundary_condition")} for r in rows]
    checks["public_commitments_match"] = sorted(public, key=lambda r: (r["field_id"], r["case_type"])) == sorted(commitment["public_row_commitments"], key=lambda r: (r["field_id"], r["case_type"]))
    sanitized = {k: v for k, v in payload.items() if k != "master_seed_hex"}
    checks["master_seed_not_persisted"] = "master_seed_hex" not in sanitized
    return sanitized, {"status": "PASS" if all(checks.values()) else "FAIL", "checks": checks, "archive_sha256": commitment["archive_sha256"], "payload_semantic_digest": commitment["payload_semantic_digest"], "row_count": len(rows)}


def load_coefficient_generators(archive: Path, commitment: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    checks = {
        "archive_exists": archive.is_file(),
        "archive_bytes": archive.is_file() and archive.stat().st_size == int(commitment["archive_bytes"]),
        "archive_sha256": archive.is_file() and sha256_file(archive) == commitment["archive_sha256"],
    }
    if not all(checks.values()):
        return {}, {"status": "FAIL", "checks": checks}
    generators: dict[str, dict[str, Any]] = {}
    with tarfile.open(archive, "r:xz") as tf:
        _safe_member_names(tf)
        payload = _read_tar_json(tf, "payload_manifest.json")
        checks["schema"] = payload.get("schema") == "P13_PRIVATE_COEFFICIENT_PAYLOAD_V1"
        checks["role"] = payload.get("role") == "DEVELOPMENT_COEF"
        checks["semantic_digest"] = _semantic_payload_digest(payload) == commitment["payload_semantic_digest"]
        checks["row_count_4"] = len(payload.get("rows", [])) == 4
        gen_sem_ok = True
        for row in payload.get("rows", []):
            gen = _read_tar_json(tf, row["generator_member_path"])
            if sha256_bytes(canonical_json_bytes(gen)) != row["generator_semantic_digest"]:
                gen_sem_ok = False
            generators[row["field_id"]] = gen
        checks["generator_semantic_digests"] = gen_sem_ok
    return generators, {"status": "PASS" if all(checks.values()) else "FAIL", "checks": checks}


def validate_regenerated_coefficients(root: Path, generators: dict[str, dict[str, Any]], k0_manifest: dict[str, Any], order: int = 4) -> dict[str, Any]:
    rows = []
    ok = True
    for entry in k0_manifest["search_objects"]:
        grid = int(entry["grid"])
        if grid not in (17, 33, 65):
            continue
        fid = entry["field_id"]
        regenerated = make_search_object(generators[fid], grid, order)
        actual = load_npz(root / entry["path"])
        keys_ok = sorted(regenerated) == sorted(actual)
        arrays_equal = keys_ok and all(np.array_equal(np.asarray(regenerated[k]), np.asarray(actual[k])) for k in regenerated)
        sem = search_object_semantic_digest(regenerated)
        row = {"field_id": fid, "grid": grid, "semantic_digest": sem, "expected_semantic_digest": entry["semantic_digest"], "arrays_byte_equal_to_K0_open_object": arrays_equal}
        row["pass"] = arrays_equal and sem == entry["semantic_digest"]
        ok = ok and row["pass"]
        rows.append(row)
    return {"status": "PASS" if ok and len(rows) == 12 else "FAIL", "rows": rows, "count": len(rows), "interpolation_from_G65_used": False}


def _spatial_value(spec: dict[str, Any], x: np.ndarray) -> np.ndarray:
    typ = spec.get("type")
    if typ == "zero":
        return np.zeros_like(x, dtype=np.float64)
    if typ == "sine_series":
        y = np.zeros_like(x, dtype=np.float64)
        for m, a in zip(spec["modes"], spec["amplitudes"]):
            y += float(a) * np.sin(int(m) * math.pi * x)
        return y
    if typ == "dirichlet_gaussian_packet":
        c = float(spec["center"]); s = float(spec["width"]); a = float(spec["amplitude"])
        return a * np.sin(math.pi * x) * np.exp(-0.5 * ((x - c) / s) ** 2)
    raise ValueError(f"unsupported spatial spec {typ}")


def _forcing_at(spec: dict[str, Any], x: np.ndarray, t: float) -> np.ndarray:
    typ = spec.get("type")
    if typ == "zero":
        return np.zeros_like(x, dtype=np.float64)
    if typ == "sine_x_harmonic_t_series":
        y = np.zeros_like(x, dtype=np.float64)
        for term in spec["terms"]:
            y += float(term["amplitude"]) * np.sin(int(term["spatial_mode"]) * math.pi * x) * math.cos(float(term["time_frequency"]) * float(t) + float(term["phase"]))
        return y
    if typ == "dirichlet_spacetime_gaussian_packet":
        xc = float(spec["x_center"]); xs = float(spec["x_width"]); tc = float(spec["t_center"]); ts = float(spec["t_width"]); a = float(spec["amplitude"])
        return a * np.sin(math.pi * x) * np.exp(-0.5 * ((x - xc) / xs) ** 2) * math.exp(-0.5 * ((float(t) - tc) / ts) ** 2)
    raise ValueError(f"unsupported forcing spec {typ}")


def _a_at(generator: dict[str, Any], x: np.ndarray, t: np.ndarray | float) -> np.ndarray:
    xx = np.asarray(x, dtype=np.float64)
    tt = np.asarray(t, dtype=np.float64)
    shape = np.broadcast_shapes(xx.shape, tt.shape)
    xx = np.broadcast_to(xx, shape); tt = np.broadcast_to(tt, shape)
    return exponential_coefficient_derivatives(generator, xx, tt, 0)[(0, 0)]


def _reference_output_path(store: Path, grid: int, field_id: str, case_type: str) -> Path:
    return store / f"G{grid}" / field_id / f"{case_type}.npy"


def solve_original_reference(task: dict[str, Any]) -> dict[str, Any]:
    for k in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[k] = "1"
    generator = task["generator"]; case = task["case"]; n = int(task["grid"]); out = Path(task["output"])
    if out.is_file():
        try:
            arr = np.load(out, mmap_mode="r", allow_pickle=False)
            if arr.shape == (n, n) and np.all(np.isfinite(arr)):
                return {"field_id": case["field_id"], "case_type": case["case_type"], "grid": n, "path": task["relpath"], "bytes": out.stat().st_size, "sha256": sha256_file(out), "status": "PASS", "resumed": True, "elapsed_seconds": 0.0}
        except Exception:
            pass
    started = time.perf_counter()
    x = np.linspace(0.0, 1.0, n); t_eval = np.linspace(0.0, 1.0, n); dx = float(x[1] - x[0]); interior = slice(1, n - 1)
    u0 = _spatial_value(case["u0"], x); v0 = _spatial_value(case["v0"], x)
    y0 = np.concatenate([u0[interior], v0[interior]])
    xhalf = 0.5 * (x[:-1] + x[1:])
    def rhs(time_value: float, state: np.ndarray) -> np.ndarray:
        u = np.zeros(n, dtype=np.float64); v = np.zeros(n, dtype=np.float64)
        u[interior] = state[:n-2]; v[interior] = state[n-2:]
        ah = _a_at(generator, xhalf, float(time_value))
        flux = ah * (u[1:] - u[:-1]) / dx
        div = (flux[1:] - flux[:-1]) / dx
        forcing = _forcing_at(case["forcing"], x, float(time_value))
        acc = div - u[interior] + forcing[interior]
        return np.concatenate([v[interior], acc])
    sol = solve_ivp(rhs, (0.0, 1.0), y0, method=task["solver"]["method"], t_eval=t_eval, rtol=float(task["solver"]["rtol"]), atol=float(task["solver"]["atol"]), max_step=float(task["solver"]["max_step"]))
    if not sol.success:
        raise RuntimeError(sol.message)
    arr = np.zeros((n, n), dtype=np.float64); arr[1:-1, :] = sol.y[:n-2]
    if np.any(~np.isfinite(arr)):
        raise FloatingPointError("nonfinite original-PDE reference")
    out.parent.mkdir(parents=True, exist_ok=True); tmp = out.with_suffix(out.suffix + ".partial")
    with tmp.open("wb") as f:
        np.save(f, arr, allow_pickle=False); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, out)
    return {"field_id": case["field_id"], "case_type": case["case_type"], "grid": n, "path": task["relpath"], "bytes": out.stat().st_size, "sha256": sha256_file(out), "status": "PASS", "resumed": False, "nfev": int(sol.nfev), "boundary_max_abs": float(max(np.max(np.abs(arr[0])), np.max(np.abs(arr[-1])))), "initial_max_abs": float(np.max(np.abs(arr[:,0] - u0))), "elapsed_seconds": float(time.perf_counter() - started)}


def _simpson_weights(n: int) -> np.ndarray:
    if n < 3 or n % 2 == 0:
        raise ValueError("odd grid required")
    h = 1.0 / (n - 1); w = np.ones(n); w[1:-1:2] = 4.0; w[2:-1:2] = 2.0
    return w * h / 3.0


def _d1(v: np.ndarray, h: float, axis: int) -> np.ndarray:
    a = np.asarray(v, float); out = np.empty_like(a)
    if axis == 0:
        out[1:-1] = (a[2:] - a[:-2])/(2*h); out[0] = (-3*a[0] + 4*a[1] - a[2])/(2*h); out[-1] = (3*a[-1] - 4*a[-2] + a[-3])/(2*h)
    else:
        out[:,1:-1] = (a[:,2:] - a[:,:-2])/(2*h); out[:,0] = (-3*a[:,0] + 4*a[:,1] - a[:,2])/(2*h); out[:,-1] = (3*a[:,-1] - 4*a[:,-2] + a[:,-3])/(2*h)
    return out


def _d2x(v: np.ndarray, h: float) -> np.ndarray:
    a = np.asarray(v, float); out = np.empty_like(a)
    out[1:-1] = (a[2:] - 2*a[1:-1] + a[:-2])/(h*h)
    out[0] = (2*a[0] - 5*a[1] + 4*a[2] - a[3])/(h*h)
    out[-1] = (2*a[-1] - 5*a[-2] + 4*a[-3] - a[-4])/(h*h)
    return out


def physical_energy(values: np.ndarray, generator: dict[str, Any]) -> float:
    n = values.shape[0]
    if values.shape != (n,n) or n % 2 == 0:
        raise ValueError(values.shape)
    h = 1.0/(n-1); ux = _d1(values,h,0); ut = _d1(values,h,1)
    x = np.linspace(0,1,n); t = np.linspace(0,1,n); xm,tm=np.meshgrid(x,t,indexing="ij")
    a = _a_at(generator,xm,tm); w = _simpson_weights(n); W=w[:,None]*w[None,:]
    val=float(np.sum(W*(ut*ut+a*ux*ux+values*values)))
    return math.sqrt(max(val,0.0))


def nested_reference_uncertainty(coarse: np.ndarray, fine: np.ndarray, generator: dict[str, Any]) -> float:
    if fine.shape[0] != 2*coarse.shape[0]-1:
        raise ValueError("non-nested reference pair")
    restricted = fine[::2,::2]
    den = physical_energy(restricted, generator)
    if den <= 1e-14:
        raise FloatingPointError("zero reference energy")
    return physical_energy(coarse-restricted, generator)/den


def generate_reference_tasks(tasks: list[dict[str, Any]], workers: int, label: str) -> list[dict[str, Any]]:
    if not tasks:
        return []
    records=[]; started=time.perf_counter(); done=0
    print(f"[P13-S2-K4] stage={label} processed=0/{len(tasks)} workers={workers}", flush=True)
    with ProcessPoolExecutor(max_workers=max(1,int(workers)), mp_context=get_context("spawn")) as pool:
        futs={pool.submit(solve_original_reference,t):t for t in tasks}
        for fut in as_completed(futs):
            rec=fut.result(); records.append(rec); done+=1
            elapsed=max(time.perf_counter()-started,1e-9); rate=done/elapsed; eta=(len(tasks)-done)/max(rate,1e-12)
            print(f"[P13-S2-K4] stage={label} processed={done}/{len(tasks)} current={rec['field_id']}:{rec['case_type']}:G{rec['grid']} elapsed={elapsed:.1f}s rate={rate:.3f}/s ETA={eta:.1f}s", flush=True)
    return records


def _restrict_reference(arr: np.ndarray, n: int) -> np.ndarray:
    N = arr.shape[0]
    if arr.shape != (N,N) or (N-1) % (n-1) != 0:
        raise ValueError(f"cannot nested-restrict G{N} to G{n}")
    step=(N-1)//(n-1)
    return np.asarray(arr[::step,::step], dtype=np.float64)


def _midpoint_source_derivatives(u: np.ndarray, generator: dict[str, Any], case: dict[str, Any]) -> tuple[dict[str,np.ndarray], np.ndarray, np.ndarray]:
    n=u.shape[0]; h=1.0/(n-1); x=np.linspace(0,1,n); t=np.linspace(0,1,n); tm=0.5*(t[:-1]+t[1:])
    um=0.5*(u[:,:-1]+u[:,1:]); ut=(u[:,1:]-u[:,:-1])/h
    ux=_d1(um,h,0); uxx=_d2x(um,h); uxt=_d1(ut,h,0)
    xm,tt=np.meshgrid(x,tm,indexing="ij"); a=_a_at(generator,xm,tt)
    xhalf=0.5*(x[:-1]+x[1:]); xhm,thm=np.meshgrid(xhalf,tm,indexing="ij"); ah=_a_at(generator,xhm,thm)
    flux=ah*(um[1:]-um[:-1])/h; utt=np.zeros_like(um); utt[1:-1]=(flux[1:]-flux[:-1])/h - um[1:-1]
    for j,tv in enumerate(tm):
        utt[1:-1,j] += _forcing_at(case["forcing"],x,float(tv))[1:-1]
    source={"ux":ux,"ut":ut,"uxx":uxx,"uxt":uxt,"utt":utt}
    return source, xm, tt


def _gauge_record(pair: dict[str,Any], theta: list[float], generator: dict[str,Any]) -> dict[str,Any]:
    from p11rawxt_operator import gauge_second_jet
    x=np.asarray([[0.0]]); t=np.asarray([[0.0]]); d=exponential_coefficient_derivatives(generator,x,t,4)
    raw=evaluate_pair_jet(pair["raw_X_AST"],pair["raw_T_AST"],theta,x,t,d,4)
    _,rec=gauge_second_jet(raw)
    if rec.get("status")!="PASS": raise ValueError(f"gauge reference failed {rec}")
    return rec


def control_pair_score(task: dict[str,Any]) -> dict[str,Any]:
    for k in ("OMP_NUM_THREADS","MKL_NUM_THREADS","OPENBLAS_NUM_THREADS","NUMEXPR_NUM_THREADS"): os.environ[k]="1"
    started=time.perf_counter(); n=int(task["grid"]); pair=task["pair"]; theta=task["theta"]; gen=task["generator"]; case=task["case"]
    ref=np.asarray(np.load(task["reference_path"],allow_pickle=False),dtype=np.float64); u=_restrict_reference(ref,n)
    x=np.linspace(0,1,n); t=np.linspace(0,1,n); tm=0.5*(t[:-1]+t[1:]); xm,tt=np.meshgrid(x,tm,indexing="ij")
    dmid=exponential_coefficient_derivatives(gen,xm,tt,4); gauge=_gauge_record(pair,theta,gen)
    raw=evaluate_pair_jet(pair["raw_X_AST"],pair["raw_T_AST"],theta,xm,tt,dmid,4); g=apply_gauge(raw,gauge)
    coeff=pushforward_variable(g,dmid[(0,0)],dmid[(1,0)],np.ones_like(xm),1e-12); sc=source_operator_coefficients(g,coeff)
    source,_,_=_midpoint_source_derivatives(u,gen,case); wxx,wxt=inverse_transform_second_derivatives(source,g)
    forcing=coeff["m"]*wxt+coeff["r"]*wxx
    E,P,cert=causal_solve(x,t,sc,forcing,task["causal_cfg"])
    Ex=_d1(E,1.0/(n-1),0); xn,tn=np.meshgrid(x,t,indexing="ij"); an=_a_at(gen,xn,tn); w=_simpson_weights(n); W=w[:,None]*w[None,:]
    err=math.sqrt(max(float(np.sum(W*(P*P+an*Ex*Ex+E*E))),0.0)); full=physical_energy(u,gen)
    return {"control":task["control"],"field_id":case["field_id"],"case_type":case["case_type"],"grid":n,"relative_energy_error":float(err/max(full,1e-14)),"linear_certificate":cert,"elapsed_seconds":float(time.perf_counter()-started)}


def _control_tasks(control: str, pair: dict[str,Any], theta: list[float], grid: int, response_rows: list[dict[str,Any]], generators: dict[str,dict[str,Any]], ref_cert: dict[str,dict[str,Any]], cfg: dict[str,Any]) -> list[dict[str,Any]]:
    out=[]
    for case in response_rows:
        key=f"{case['field_id']}::{case['case_type']}"; rc=ref_cert[key]
        out.append({"control":control,"pair":pair,"theta":theta,"grid":grid,"case":case,"generator":generators[case["field_id"]],"reference_path":rc["final_reference_path_abs"],"causal_cfg":cfg["causal_solver_numerical"]})
    return out


def run_control_tasks(tasks:list[dict[str,Any]],workers:int,label:str)->list[dict[str,Any]]:
    records=[]; started=time.perf_counter(); done=0
    print(f"[P13-S2-K4] stage={label} processed=0/{len(tasks)} workers={workers}",flush=True)
    with ProcessPoolExecutor(max_workers=max(1,int(workers)),mp_context=get_context("spawn")) as pool:
        futs={pool.submit(control_pair_score,t):t for t in tasks}
        for fut in as_completed(futs):
            r=fut.result(); records.append(r); done+=1; elapsed=max(time.perf_counter()-started,1e-9); rate=done/elapsed; eta=(len(tasks)-done)/max(rate,1e-12)
            print(f"[P13-S2-K4] stage={label} processed={done}/{len(tasks)} current={r['control']}:{r['field_id']}:{r['case_type']}:G{r['grid']} elapsed={elapsed:.1f}s rate={rate:.3f}/s ETA={eta:.1f}s",flush=True)
    return records


def _control_decisions(records:list[dict[str,Any]],ref_cert:dict[str,dict[str,Any]],coarse:int,fine:int,threshold:float)->dict[str,Any]:
    by={(r["control"],r["field_id"],r["case_type"],r["grid"]):r for r in records}
    controls=sorted({r["control"] for r in records}); out={}
    for control in controls:
        pairs=[]
        for key,rc in sorted(ref_cert.items()):
            fid,case=key.split("::",1); a=by[(control,fid,case,coarse)]["relative_energy_error"]; b=by[(control,fid,case,fine)]["relative_energy_error"]
            iv=response_interval(a,b,float(rc["reference_uncertainty"])); pairs.append({"field_id":fid,"case_type":case,"coarse_grid":coarse,"fine_grid":fine,"coarse_error":a,"fine_error":b,"reference_uncertainty":float(rc["reference_uncertainty"]),**iv})
        out[control]={"decision":classify_branch(pairs,threshold),"pair_intervals":pairs,"worst_upper":max(x["upper"] for x in pairs),"worst_lower":max(x["lower"] for x in pairs)}
    return out


def _verify_k3_entry(root:Path,cfg:dict[str,Any])->tuple[Path,dict[str,Any],dict[str,Any]]:
    checks={}; k3run=resolve_marker(root,cfg["k3_run_marker"]); k3=k3run/"K3_response_protocol_fidelity_control_lock"
    checks["K3_status"]=(k3run/"K3_OVERALL_STATUS.txt").is_file() and (k3run/"K3_OVERALL_STATUS.txt").read_text().strip()=="PASS"
    sem=load_json(k3/"K3_SEMANTIC_OUTPUT_DIGEST.json"); summary=load_json(k3/"K3_SCIENTIFIC_SUMMARY.json"); guard=load_json(k3/"K3_DATA_BOUNDARY_GUARD.json"); response_lock=load_json(k3/"K3_RESPONSE_FIDELITY_PROTOCOL_LOCK.json"); control_lock=load_json(k3/"K3_CONTROL_FIRST_AND_NONDISCRIMINATION_LOCK.json")
    checks["K3_semantic_digest"]=sem.get("semantic_output_digest")==cfg["expected_k3_semantic_output_digest"]==summary.get("semantic_output_digest")
    checks["K3_response_unopened"]=guard.get("DEVELOPMENT_RESPONSE")=="SEALED_COMMITTED_UNOPENED" and response_lock.get("response_data_opened") is False
    checks["K3_sealed_unopened"]=guard.get("SEALED_FINAL_COEF")=="SEALED_COMMITTED_UNOPENED" and guard.get("SEALED_FINAL_RESPONSE")=="SEALED_COMMITTED_UNOPENED"
    checks["K3_1955"]=summary.get("response_eligible_count")==1955 and response_lock.get("complete_response_eligible_scientific_cohort")==1955
    checks["K3_reference_ladder"]=response_lock["reference_protocol"]["initial_nested_pair"]==[513,1025] and response_lock["reference_protocol"]["refinement_pairs_if_needed"]==[[1025,2049],[2049,4097]] and abs(float(response_lock["reference_protocol"]["reference_uncertainty_ceiling_relative_energy"])-0.02)<1e-15
    checks["K3_candidate_ladder"]=response_lock["candidate_control_fidelity_protocol"]["initial_nested_pair"]==[129,257] and response_lock["candidate_control_fidelity_protocol"]["candidate_specific_rescue"] is False
    checks["K3_control_first"]=control_lock.get("status")=="PASS" and control_lock.get("absolute_gate")==0.15 and control_lock.get("membership_authority")=="NONE_FOR_CONTROLS"
    checks["K3_next"]=(k3run/"K3_NEXT_ACTION.txt").read_text().strip()==cfg["expected_k3_next_action"]
    eligible=root/cfg["response_eligible_membership"]["path"]
    checks["eligible_sha"]=eligible.is_file() and sha256_file(eligible)==cfg["response_eligible_membership"]["sha256"]
    checks["eligible_count"]=eligible.is_file() and count_jsonl(eligible)==1955
    pf1=root/cfg["pf1_active_input_manifest"]["path"]
    checks["PF1_manifest_sha"]=pf1.is_file() and sha256_file(pf1)==cfg["pf1_active_input_manifest"]["sha256"]
    return k3run, load_json(pf1), {"status":"PASS" if all(checks.values()) else "FAIL","checks":checks,"K3_semantic_output_digest":cfg["expected_k3_semantic_output_digest"]}


def _source_manifest(root: Path, cfgp: Path) -> dict[str, Any]:
    rels = [
        "phases/p13/coefficient_law_raw_xt/src/p13rawxt/s2_k4_response_reference_controls.py",
        "phases/p13/coefficient_law_raw_xt/src/p13rawxt/s2_k3_response_protocol_lock.py",
        "phases/p13/coefficient_law_raw_xt/src/p13rawxt/causal_calibration.py",
        "phases/p13/coefficient_law_raw_xt/src/p13rawxt/calibration_instruments.py",
        "phases/p13/coefficient_law_raw_xt/src/p13rawxt/coefficients.py",
        "phases/p13/coefficient_law_raw_xt/src/p13rawxt/ast_runtime.py",
        "phases/p13/coefficient_law_raw_xt/src/p13rawxt/family_evaluator.py",
        "phases/p13/coefficient_law_raw_xt/configs/p13_s2_k4_protocol.json",
    ]
    rows=[]
    for rel in rels:
        q=root/rel
        rows.append({"path":rel,"bytes":q.stat().st_size,"sha256":sha256_file(q)})
    return {"files":rows,"candidate_response_implementation_present_but_not_executed_in_K4":False}


def _runtime_environment(cfg: dict[str, Any]) -> dict[str, Any]:
    return {
        "python":sys.version,
        "platform":platform.platform(),
        "numpy":np.__version__,
        "workers":cfg["runtime_workers"],
        "allocation":cfg["runtime_policy"]["allocation"],
        "OMP_NUM_THREADS":os.environ.get("OMP_NUM_THREADS"),
        "MKL_NUM_THREADS":os.environ.get("MKL_NUM_THREADS"),
        "OPENBLAS_NUM_THREADS":os.environ.get("OPENBLAS_NUM_THREADS"),
        "NUMEXPR_NUM_THREADS":os.environ.get("NUMEXPR_NUM_THREADS"),
        "restart_resume":True,
    }


def _update_context(root:Path,summary:dict[str,Any])->None:
    p=root/"P13_S2_ROLLING_CONTEXT.md"
    if not p.exists(): write_text(p,"# P13 S2 Rolling Execution Context\n\n**Role:** REFERENCE / HANDOFF living record.\n\n")
    text=p.read_text(encoding="utf-8"); marker="<!-- S2_K4_FORMAL_RESULT -->"
    block=f'''{marker}\n## S2-K4 — DEVELOPMENT response opening, reference bank, control-first precheck\n\n- `OVERALL_STATUS`: **{summary['OVERALL_STATUS']}**\n- DEVELOPMENT response opened: `True` (32 committed pairs)\n- SEALED coefficient/response opened: `False`\n- reference status: `{summary['reference_status']}`\n- certified references: `{summary['reference_certified_count']}/32`\n- identity control decision: `{summary.get('identity_decision')}`\n- NULL control role: descriptive/control only; membership authority `NONE`\n- K4 scientific outcome: `{summary['scientific_outcome']}`\n- 1955 candidate responses executed in K4: `False`\n- semantic output digest: `{summary['semantic_output_digest']}`\n- next action: `{summary['NEXT_ACTION']}`\n'''
    text=(text.split(marker)[0].rstrip()+"\n\n"+block) if marker in text else text.rstrip()+"\n\n"+block
    write_text(p,text)


def run(root:Path)->int:
    root=root.resolve(); home=root/"phases/p13/coefficient_law_raw_xt"; cfgp=home/"configs/p13_s2_k4_protocol.json"; cfg=load_json(cfgp)
    k3run,pf1,entry=_verify_k3_entry(root,cfg); k4=k3run/"K4_development_response_reference_control_first"; k4.mkdir(exist_ok=True)
    write_json(k4/"K4_ENTRY_AND_K3_REVIEW.json",entry)
    write_json(k4/"K4_SOURCE_MANIFEST.json",_source_manifest(root,cfgp))
    write_json(k4/"K4_RUNTIME_ENVIRONMENT.json",_runtime_environment(cfg))
    if entry["status"]!="PASS":
        write_text(k3run/"K4_OVERALL_STATUS.txt","FAIL\n"); write_text(k3run/"K4_NEXT_ACTION.txt","BLOCK_P13_S2_REPAIR_K3_K4_PROVENANCE\n"); return 2

    print("[P13-S2-K4] stage=response_open processed=0/4 current=DEVELOPMENT_response",flush=True)
    response_commit=pf1["DEVELOPMENT_response_commitment"]; response,ropen=open_response_payload(Path(response_commit["archive_absolute_path"]),response_commit,cfg["case_types"]); write_json(k4/"K4_RESPONSE_OPENING_AUDIT.json",ropen)
    if ropen["status"]!="PASS": raise RuntimeError("DEVELOPMENT response opening verification failed")
    response_path=k4/"K4_OPENED_DEVELOPMENT_RESPONSE.json"; write_json(response_path,response)

    coeff_commit=pf1["DEVELOPMENT_coefficient_commitment"]; generators,copen=load_coefficient_generators(Path(coeff_commit["archive_absolute_path"]),coeff_commit); write_json(k4/"K4_COEFFICIENT_REOPEN_AUDIT.json",copen)
    k0_manifest=load_json(resolve_marker(root,cfg["k0_dev_input_marker"])); coeff_repro=validate_regenerated_coefficients(root,generators,k0_manifest); write_json(k4/"K4_HIGH_GRID_COEFFICIENT_REPRODUCTION_PRECHECK.json",coeff_repro)
    if copen["status"]!="PASS" or coeff_repro["status"]!="PASS": raise RuntimeError("DEVELOPMENT coefficient high-grid reproduction precheck failed")

    rows=response["rows"]; store=k4/"reference_bank"; solver=cfg["reference_solver"]
    def tasks_for(grid:int, selected:list[dict[str,Any]]|None=None):
        rr=rows if selected is None else selected; out=[]
        for case in rr:
            p=_reference_output_path(store,grid,case["field_id"],case["case_type"])
            out.append({"generator":generators[case["field_id"]],"case":case,"grid":grid,"output":str(p),"relpath":str(p.relative_to(root)),"solver":solver})
        return out
    runtime=[]
    for grid in (513,1025): runtime += generate_reference_tasks(tasks_for(grid),int(cfg["runtime_workers"][str(grid)]),f"reference_G{grid}")

    cert_rows=[]; need2049=[]
    for case in rows:
        c=np.asarray(np.load(_reference_output_path(store,513,case["field_id"],case["case_type"]),allow_pickle=False),float); f=np.asarray(np.load(_reference_output_path(store,1025,case["field_id"],case["case_type"]),allow_pickle=False),float)
        u=nested_reference_uncertainty(c,f,generators[case["field_id"]]); rec={"field_id":case["field_id"],"case_type":case["case_type"],"coarse_grid":513,"fine_grid":1025,"reference_uncertainty":u}
        if u<=cfg["reference_uncertainty_ceiling"]: rec.update({"status":"REFERENCE_CERTIFIED","final_grid":1025})
        else: rec["status"]="REFINE_TO_G2049"; need2049.append(case)
        cert_rows.append(rec)
    runtime += generate_reference_tasks(tasks_for(2049,need2049),int(cfg["runtime_workers"]["2049"]),"reference_G2049")
    need4097=[]
    for rec in cert_rows:
        if rec["status"]!="REFINE_TO_G2049": continue
        case=next(r for r in rows if r["field_id"]==rec["field_id"] and r["case_type"]==rec["case_type"]); c=np.asarray(np.load(_reference_output_path(store,1025,rec["field_id"],rec["case_type"]),allow_pickle=False),float); f=np.asarray(np.load(_reference_output_path(store,2049,rec["field_id"],rec["case_type"]),allow_pickle=False),float); u=nested_reference_uncertainty(c,f,generators[rec["field_id"]]); rec.update({"coarse_grid":1025,"fine_grid":2049,"reference_uncertainty":u})
        if u<=cfg["reference_uncertainty_ceiling"]: rec.update({"status":"REFERENCE_CERTIFIED","final_grid":2049})
        else: rec["status"]="REFINE_TO_G4097"; need4097.append(case)
    runtime += generate_reference_tasks(tasks_for(4097,need4097),int(cfg["runtime_workers"]["4097"]),"reference_G4097")
    for rec in cert_rows:
        if rec["status"]!="REFINE_TO_G4097": continue
        c=np.asarray(np.load(_reference_output_path(store,2049,rec["field_id"],rec["case_type"]),allow_pickle=False),float); f=np.asarray(np.load(_reference_output_path(store,4097,rec["field_id"],rec["case_type"]),allow_pickle=False),float); u=nested_reference_uncertainty(c,f,generators[rec["field_id"]]); rec.update({"coarse_grid":2049,"fine_grid":4097,"reference_uncertainty":u,"final_grid":4097,"status":"REFERENCE_CERTIFIED" if u<=cfg["reference_uncertainty_ceiling"] else "REFERENCE_UNRESOLVED_AFTER_G2049_G4097"})
    cert_map={}
    for rec in cert_rows:
        key=f"{rec['field_id']}::{rec['case_type']}"; p=_reference_output_path(store,int(rec["final_grid"]),rec["field_id"],rec["case_type"]); rec["final_reference_path"]=str(p.relative_to(root)); rec["final_reference_path_abs"]=str(p); rec["final_reference_sha256"]=sha256_file(p); cert_map[key]=rec
    ref_status="PASS" if all(r["status"]=="REFERENCE_CERTIFIED" for r in cert_rows) else "REFERENCE_UNRESOLVED"
    refcert={"status":ref_status,"candidate_independent":True,"reference_uncertainty_ceiling":cfg["reference_uncertainty_ceiling"],"certified_count":sum(r["status"]=="REFERENCE_CERTIFIED" for r in cert_rows),"unresolved_count":sum(r["status"]!="REFERENCE_CERTIFIED" for r in cert_rows),"final_grid_counts":{str(g):sum(r.get("final_grid")==g and r["status"]=="REFERENCE_CERTIFIED" for r in cert_rows) for g in (1025,2049,4097)},"records":cert_rows}
    write_json(k4/"K4_REFERENCE_CERTIFICATION.json",refcert); write_json(k4/"K4_REFERENCE_RUNTIME_MANIFEST.json",{"records":runtime})

    control_results={}; identity_decision=None; outcome="REFERENCE_UNRESOLVED_STOP" if ref_status!="PASS" else None
    if ref_status=="PASS":
        caps=load_json(home/"configs/p13_s0_k2_protocol.json")["caps"]; idpair=build_identity_pair(caps); nullpair=build_null_capacity_pair(caps); null_lock=cfg["null_control_lock"]
        if nullpair["structural_hash"]!=null_lock["structural_hash"]: raise RuntimeError("NULL structural hash mismatch")
        controls={"identity":(idpair,[]),"frozen_null":(nullpair,[float(x) for x in null_lock["theta"]])}
        allrecs=[]
        for grid in (129,257):
            tasks=[]
            for name,(pair,theta) in controls.items(): tasks += _control_tasks(name,pair,theta,grid,rows,generators,cert_map,cfg)
            allrecs += run_control_tasks(tasks,int(cfg["runtime_workers"][str(grid)]),f"controls_G{grid}")
        control_results=_control_decisions(allrecs,cert_map,129,257,cfg["absolute_response_gate"]); identity_decision=control_results["identity"]["decision"]; action=control_first_action(identity_decision,257)
        current_coarse,current_fine=129,257
        if identity_decision=="RESPONSE_UNRESOLVED":
            tasks=[]
            for name,(pair,theta) in controls.items(): tasks += _control_tasks(name,pair,theta,513,rows,generators,cert_map,cfg)
            allrecs += run_control_tasks(tasks,int(cfg["runtime_workers"]["513"]),"controls_G513"); control_results=_control_decisions(allrecs,cert_map,257,513,cfg["absolute_response_gate"]); identity_decision=control_results["identity"]["decision"]; action=control_first_action(identity_decision,513); current_coarse,current_fine=257,513
        if identity_decision=="RESPONSE_UNRESOLVED":
            tasks=[]
            for name,(pair,theta) in controls.items(): tasks += _control_tasks(name,pair,theta,1025,rows,generators,cert_map,cfg)
            allrecs += run_control_tasks(tasks,int(cfg["runtime_workers"]["1025"]),"controls_G1025"); control_results=_control_decisions(allrecs,cert_map,513,1025,cfg["absolute_response_gate"]); identity_decision=control_results["identity"]["decision"]; action=control_first_action(identity_decision,1025); current_coarse,current_fine=513,1025
        write_json(k4/"K4_CONTROL_EXECUTION_RECORDS.json",{"records":allrecs}); write_json(k4/"K4_CONTROL_FIRST_RESULTS.json",{"status":"PASS","coarse_grid":current_coarse,"fine_grid":current_fine,"controls":control_results,"identity_action":action,"null_membership_authority":"NONE"})
        if identity_decision=="RESPONSE_PASS": outcome="NONDISCRIMINATIVE_ABSOLUTE_GATE_STOP_BEFORE_K5"
        elif identity_decision=="RESPONSE_FAIL": outcome="DISCRIMINATIVE_ABSOLUTE_GATE"
        else: outcome="CONTROL_GATE_UNRESOLVED_STOP_BEFORE_K5"

    guard={"DEVELOPMENT_COEF":"OPENED_K0_READ_IN_PLACE","DEVELOPMENT_RESPONSE":"OPENED_K4","SEALED_FINAL_COEF":"SEALED_COMMITTED_UNOPENED","SEALED_FINAL_RESPONSE":"SEALED_COMMITTED_UNOPENED","candidate_response_rows_read":False,"candidate_response_solved":False,"candidate_membership_changed":False,"top_k_or_proxy_filter":False,"response_refit":False,"amplitude_compensation":False}
    write_json(k4/"K4_DATA_BOUNDARY_GUARD.json",guard)
    next_action={"REFERENCE_UNRESOLVED_STOP":"STOP_P13_S2_RESPONSE_PATH_REFERENCE_UNRESOLVED","NONDISCRIMINATIVE_ABSOLUTE_GATE_STOP_BEFORE_K5":"STOP_P13_S2_BEFORE_K5_NONDISCRIMINATIVE_ABSOLUTE_GATE","CONTROL_GATE_UNRESOLVED_STOP_BEFORE_K5":"STOP_P13_S2_BEFORE_K5_CONTROL_GATE_UNRESOLVED","DISCRIMINATIVE_ABSOLUTE_GATE":"P13-S2-K5_COMPLETE_CANDIDATE_RESPONSE_CERTIFICATION_REQUIRES_POST_K4_USER_REVIEW"}[outcome]
    basis={"K3_semantic_output_digest":cfg["expected_k3_semantic_output_digest"],"response_commitment_sha256":response_commit["archive_sha256"],"response_payload_semantic_digest":response_commit["payload_semantic_digest"],"response_eligible_membership_sha256":cfg["response_eligible_membership"]["sha256"],"reference_certification_sha256":sha256_file(k4/"K4_REFERENCE_CERTIFICATION.json"),"control_results_sha256":sha256_file(k4/"K4_CONTROL_FIRST_RESULTS.json") if (k4/"K4_CONTROL_FIRST_RESULTS.json").is_file() else None,"scientific_outcome":outcome,"next_action":next_action}
    sem=sha256_bytes(canonical_json_bytes(basis)); write_json(k4/"K4_SEMANTIC_OUTPUT_DIGEST.json",{"semantic_output_digest":sem,"basis":basis})
    ref_unc=[float(r["reference_uncertainty"]) for r in cert_rows]
    summary={
        "OVERALL_STATUS":"PASS",
        "reference_status":ref_status,
        "reference_certified_count":refcert["certified_count"],
        "reference_unresolved_count":refcert["unresolved_count"],
        "reference_final_grid_counts":refcert["final_grid_counts"],
        "reference_uncertainty_max":max(ref_unc) if ref_unc else None,
        "reference_uncertainty_median":float(np.median(ref_unc)) if ref_unc else None,
        "identity_decision":identity_decision,
        "frozen_null_decision":control_results.get("frozen_null",{}).get("decision") if control_results else None,
        "control_final_fine_grid":(load_json(k4/"K4_CONTROL_FIRST_RESULTS.json").get("fine_grid") if (k4/"K4_CONTROL_FIRST_RESULTS.json").is_file() else None),
        "scientific_outcome":outcome,
        "response_eligible_count":1955,
        "candidate_responses_executed":False,
        "DEVELOPMENT_response_opened":True,
        "SEALED_opened":False,
        "semantic_output_digest":sem,
        "NEXT_ACTION":next_action,
        "authoritative_K4_dir":str(k4.relative_to(root)),
        "reference_store":str(store.relative_to(root)),
    }
    write_json(k4/"K4_SCIENTIFIC_SUMMARY.json",summary); write_text(k3run/"K4_OVERALL_STATUS.txt","PASS\n"); write_text(k3run/"K4_NEXT_ACTION.txt",next_action+"\n"); write_text(home/"runs/LATEST_P13_S2_K4_RUN.txt",str(k3run.relative_to(root))+"\n"); _update_context(root,summary)
    print(f"[P13-S2-K4] OVERALL_STATUS=PASS outcome={outcome} reference={ref_status} identity={identity_decision}",flush=True); print(f"[P13-S2-K4] NEXT_ACTION={next_action}",flush=True); return 0


def main()->int:
    ap=argparse.ArgumentParser(); ap.add_argument("--project-root",required=True); args=ap.parse_args(); return run(Path(args.project_root))


if __name__=="__main__":
    raise SystemExit(main())
