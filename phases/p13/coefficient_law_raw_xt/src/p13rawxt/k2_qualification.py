from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import shutil
import sys
import time
import warnings
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from reproduce import s0_contract as s0
from typing import Any, Callable

import numpy as np

from .ast_runtime import evaluate_ast_jet, grid_coefficient_derivatives
from .family_evaluator import load_field_views, evaluate_family, evaluate_pair_on_field
from .calibration_instruments import (
    build_identity_pair, build_null_capacity_pair, build_full_capacity_pair,
    production_fit, reference_fit, reference_launch, best_f4, parameter_names_for_pair,
)
from .proposal_geometry import residual_graft
from .causal_calibration import calibration_pair_score, bounded_interval

EXPECTED_K1_SEMANTIC = "3b3e4dbc151d2c4c70241da4450d86cfdc69e573d4df27bee2fa7cc20e021015"


def canonical_json_bytes(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_path(path: Path, block: int = 16 * 1024 * 1024) -> str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b=f.read(block)
            if not b: break
            h.update(b)
    return h.hexdigest()


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp=path.with_suffix(path.suffix+".tmp")
    tmp.write_text(json.dumps(obj,sort_keys=True,indent=2)+"\n")
    os.replace(tmp,path)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp=path.with_suffix(path.suffix+".tmp"); tmp.write_text(text); os.replace(tmp,path)


def json_load(path: Path) -> Any:
    return json.loads(path.read_text())


def derive_seed(namespace: str, index: int) -> int:
    return int.from_bytes(hashlib.sha256(f"{namespace}|{index:08d}".encode()).digest()[:8],"big") & ((1<<63)-1)


def add_source_paths(root: Path) -> None:
    for p in [root/"phases/p13/coefficient_law_raw_xt/src", root/"phases/p11/raw_xt_td/src"]:
        s=str(p)
        if s not in sys.path: sys.path.insert(0,s)


def verify_k1(root: Path, protocol: dict[str, Any]) -> tuple[Path,dict[str,Any]]:
    if s0.active(root): return s0.fresh_verify_k1(root, protocol)
    marker=root/protocol["parent_k1"]["marker"]
    if not marker.is_file(): raise FileNotFoundError(marker)
    text=marker.read_text().strip(); run=Path(text)
    if not run.is_absolute(): run=root/text
    run=run.resolve()
    if root.resolve() not in run.parents: raise RuntimeError("K1 marker escapes project root")
    checks={}
    checks["overall_status"]=(run/"OVERALL_STATUS.txt").read_text().strip()==protocol["parent_k1"]["expected_overall_status"]
    checks["next_action"]=(run/"NEXT_ACTION.txt").read_text().strip()==protocol["parent_k1"]["expected_next_action"]
    sem=json_load(run/"semantic_output_digest.json")
    checks["semantic"] = sem.get("semantic_output_digest")==protocol["parent_k1"]["expected_semantic_output_digest"]==EXPECTED_K1_SEMANTIC
    checks["protocol_sha"] = sem.get("protocol_sha256")==protocol["parent_k1"]["expected_k1_protocol_sha256"]
    leakage=json_load(run/"no_leakage_guard.json")
    checks["k1_leakage_pass"] = leakage.get("status")=="PASS" and not leakage.get("historical_response_outcomes_read") and not leakage.get("formal_candidate_search_run") and not leakage.get("development_or_sealed_response_solve_run")
    summary=json_load(run/"audit_summary.json")
    checks["formal_search_still_disabled"] = summary.get("formal_search_authorized") is False
    checks["L3_guard_carried"] = summary.get("numeric_L3_execution_status")=="NOT_YET_QUALIFIED"
    checks["identifiability_k1_pass"] = summary.get("identifiability_status")=="PASS"
    private=json_load(run/"private_payload_commitments.json")
    checks["private_commitments_present"] = len(private)==4
    checks["private_not_copied"] = all(not x.get("private_payload_copied_into_active_tree",True) for x in private.values())
    if not all(checks.values()): raise RuntimeError(f"K1 in-place entry verification failed {checks}")
    return run,{"status":"PASS","k1_run":str(run.relative_to(root)),"checks":checks,"semantic":sem.get("semantic_output_digest"),"protocol_sha256":sem.get("protocol_sha256")}


def _evaluator(protocol:dict[str,Any],fields):
    return lambda pair,theta: evaluate_family(pair,theta,fields,protocol["validity"],float(protocol["validity"]["inverse_roundtrip_tolerance"]),protocol["operator"]["space"],protocol["operator"]["numerical"])


def _fit_summary(fit:dict[str,Any])->dict[str,Any]:
    best=best_f4(fit)
    return {"completed_calls":int(fit.get("completed_calls",0)),"all_F4_probe_count":int(fit.get("all_F4_probe_count",0)),"best":best}


def m2_semantics_gate(k1_run:Path,protocol:dict[str,Any])->dict[str,Any]:
    from p11rawxt_ast import Var,Op
    manifest=json_load(k1_run/"open_search_object_manifest.json")
    row=next(x for x in manifest["objects"] if x["role"]=="CALIBRATION_COEF" and int(x["grid"])==65)
    with np.load(k1_run/row["path"],allow_pickle=False) as z: arrays={k:np.asarray(z[k]) for k in z.files}
    x,t=np.meshgrid(arrays["x"],arrays["t"],indexing="ij"); d=grid_coefficient_derivatives(arrays,4); a=Var("a")
    fixtures={"DxDt_a":Op("Dx",Op("Dt",a)),"DxDx_a":Op("Dx",Op("Dx",a)),"DtDt_a":Op("Dt",Op("Dt",a))}
    expected_indices={"DxDt_a":(1,1),"DxDx_a":(2,0),"DtDt_a":(0,2)}
    # Value + two further source derivatives: this is exactly why coefficient support through order 4 is required.
    derivative_offsets={"value":(0,0),"x":(1,0),"t":(0,1),"xx":(2,0),"xt":(1,1),"tt":(0,2)}
    import math as _m
    worst=0.0; rows=[]
    for name,node in fixtures.items():
        jet=evaluate_ast_jet(node,x,t,{},d,4); base=expected_indices[name]
        one={}
        for lab,off in derivative_offsets.items():
            idx=(base[0]+off[0],base[1]+off[1])
            # evaluate_ast_jet derivatives are normalized Taylor coefficients.
            got=jet.coeff[off]*_m.factorial(off[0])*_m.factorial(off[1])
            truth=d[idx]
            rel=float(np.max(np.abs(got-truth))/max(float(np.max(np.abs(truth))),1e-14)); one[lab]=rel; worst=max(worst,rel)
        rows.append({"fixture":name,"relative_discrepancies":one})
    tol=float(protocol["m2_semantics"]["relative_tolerance"])
    return {"status":"PASS" if worst<=tol else "FAIL","worst_relative_discrepancy":worst,"tolerance":tol,"fixtures":rows,"representation_m_max":2,"coefficient_support_jet_order":4}


def recompute_identifiability(root:Path,k1_run:Path)->dict[str,Any]:
    from p13rawxt.coefficients import identifiability_report
    k1_protocol=json_load(root/"phases/p13/coefficient_law_raw_xt/configs/p13_s0_k1_protocol.json")
    manifest=json_load(k1_run/"open_search_object_manifest.json")
    arrays=[]
    for fid in sorted({x["field_id"] for x in manifest["objects"] if x["role"]=="TRAIN_OPERATOR"}):
        row=next(x for x in manifest["objects"] if x["field_id"]==fid and int(x["grid"])==33)
        with np.load(k1_run/row["path"],allow_pickle=False) as z: arrays.append({k:np.asarray(z[k]) for k in z.files})
    r=identifiability_report(arrays,k1_protocol)
    parent=json_load(k1_run/"train_identifiability_report.json")
    r["exact_parent_status_match"]=r["status"]==parent["status"]
    r["parent_rank_match"]=r["numerical_rank"]==parent["numerical_rank"]
    r["sigma_ratio_abs_difference"]=abs(float(r["sigma_min_over_sigma_max"])-float(parent["sigma_min_over_sigma_max"]))
    if not (r["status"]=="PASS" and r["exact_parent_status_match"] and r["parent_rank_match"] and r["sigma_ratio_abs_difference"]<=1e-12): r["status"]="FAIL"
    return r


def gauge_gate(protocol:dict[str,Any],fields)->dict[str,Any]:
    from p11rawxt_ast import Var,Theta,Op
    from p11rawxt_s1.k1_representation import canonicalize_pair
    from p11rawxt_operator import gauge_second_jet
    from p13rawxt.family_evaluator import _grid_from_arrays, _probe_source, evaluate_validity_variable, operator_on_variable
    from p13rawxt.ast_runtime import evaluate_pair_jet, grid_coefficient_derivatives
    caps=protocol["caps"]
    fixtures=[]
    for idx in range(int(protocol["gauge"]["fresh_coefficient_dependent_fixture_count"])):
        x,t,a=Var("x"),Var("t"),Var("a")
        if idx==0:
            X=Op("Add",x,Op("Mul",Theta("theta_1"),t,a)); T=Op("Add",t,Op("Mul",Theta("theta_2"),x,a)); theta=[0.035,-0.025]
        elif idx==1:
            X=Op("Add",x,Op("Mul",Theta("theta_1"),Op("PowInt",t,exponent=2),Op("Dx",a))); T=Op("Add",t,Op("Mul",Theta("theta_2"),x,Op("Dt",a))); theta=[0.06,0.05]
        else:
            X=Op("Add",x,Op("Mul",Theta("theta_1"),t,Op("Dt",Op("Dx",a)))); T=Op("Add",t,Op("Mul",Theta("theta_2"),Op("PowInt",t,exponent=2),Op("Dx",Op("Dx",a)))); theta=[0.02,-0.018]
        base=canonicalize_pair(X,T,caps)
        for field in fields:
            arrays=field.arrays; grid=_grid_from_arrays(arrays); xm,tm=grid.mesh
            d=grid_coefficient_derivatives(arrays,4)
            raw=evaluate_pair_jet(base["raw_X_AST"],base["raw_T_AST"],theta,xm,tm,d,4)
            source=_probe_source(grid,int(protocol["validity"]["inverse_probe_grid_per_axis"]),tuple(protocol["validity"]["inverse_probe_local_coordinates"]))
            pd=field.probe_interpolator.derivatives(source[:,0],source[:,1])
            probe=evaluate_pair_jet(base["raw_X_AST"],base["raw_T_AST"],theta,source[:,0],source[:,1],pd,4)
            target0=np.column_stack([probe["X"],probe["T"]])
            vb=evaluate_validity_variable(raw,grid,arrays["a_d0_0"],source,target0,protocol["validity"],float(protocol["validity"]["inverse_roundtrip_tolerance"]))
            gb,_=gauge_second_jet(raw)
            opb=None
            if vb["overall_valid"] and gb is not None: opb=operator_on_variable(gb,grid,arrays,protocol["operator"]["space"],protocol["operator"]["numerical"])[1]
            for scale,(tx,tt) in zip(protocol["gauge"]["common_positive_scales"],protocol["gauge"]["translations"]):
                acted={}
                for k,v in raw.items():
                    arr=np.asarray(v,float)
                    if k=="X": acted[k]=float(scale)*arr+float(tx)
                    elif k=="T": acted[k]=float(scale)*arr+float(tt)
                    else: acted[k]=float(scale)*arr
                target=np.empty_like(target0); target[:,0]=float(scale)*target0[:,0]+float(tx); target[:,1]=float(scale)*target0[:,1]+float(tt)
                vg=evaluate_validity_variable(acted,grid,arrays["a_d0_0"],source,target,protocol["validity"],float(protocol["validity"]["inverse_roundtrip_tolerance"]))
                gg,_=gauge_second_jet(acted)
                opg=None
                if vg["overall_valid"] and gg is not None: opg=operator_on_variable(gg,grid,arrays,protocol["operator"]["space"],protocol["operator"]["numerical"])[1]
                stage_equal=int(vb["stage_index"])==int(vg["stage_index"])
                if opb is not None and opg is not None:
                    rel=abs(float(opb["J_princ"])-float(opg["J_princ"]))/max(abs(float(opb["J_princ"])),1e-14)
                    cmap=max(float(np.max(np.abs(gb[k]-gg[k]))) for k in ["X","T","Xx","Xt","Tx","Tt","Xxx","Xxt","Xtt","Txx","Txt","Ttt"])
                else:
                    rel=0.0 if opb is None and opg is None else math.inf; cmap=0.0 if gb is None and gg is None else math.inf
                fixtures.append({"fixture":idx,"field_id":field.field_id,"scale":scale,"translation":[tx,tt],"stage_equal":stage_equal,"J_relative_discrepancy":rel,"canonical_map_max_abs_discrepancy":cmap})
    tol=float(protocol["gauge"]["relative_tolerance"]); worst=max([x["J_relative_discrepancy"] for x in fixtures],default=0.0); worst_map=max([x["canonical_map_max_abs_discrepancy"] for x in fixtures],default=0.0)
    ok=all(x["stage_equal"] for x in fixtures) and worst<=tol and worst_map<=tol
    return {"status":"PASS" if ok else "FAIL","tolerance":tol,"worst_J_relative_discrepancy":worst,"worst_canonical_map_abs_discrepancy":worst_map,"fixtures":fixtures}

def _capacity_reference_from_launch_rows(rows:list[dict[str,Any]],protocol:dict[str,Any])->dict[str,Any]:
    """Reconstruct the exact reference_fit adjudication from independent launches.

    The launch seeds and each launch's deterministic adaptive trajectory are unchanged;
    only the scheduling of mutually independent launches is parallelized.
    """
    rows=sorted(rows,key=lambda r:int(r["launch_index"]))
    bests=[r["best"] for r in rows if r.get("best") and int(r["best"]["stage_index"])==5 and r["best"].get("J_princ") is not None]
    qualified=False; agreement=None
    if len(bests)==len(rows) and len(bests)>=2:
        js=[float(x["J_princ"]) for x in bests]
        agreement=(max(js)-min(js))/max(max(js),1e-15)
        qualified=agreement<=float(protocol["launch_relative_agreement_max"])
    best=min(bests,key=lambda r:float(r["J_princ"])) if bests else None
    return {
        "qualified":qualified,
        "launch_relative_agreement":agreement,
        "best":best,
        "launches":[{"completed_calls":int(r["completed_calls"]),"best":r.get("best")} for r in rows],
    }


def _worker_capacity_launch_task(task:dict[str,Any])->dict[str,Any]:
    t0=time.perf_counter()
    p=_WORKER["protocol"]; ev=_evaluator(p,_WORKER["fields"])
    raw=reference_launch(
        task["pair"],ev,p["fitter"]["reference"],int(task["seed"]),
        progress_label=f"{task['instrument']} launch={int(task['launch_index'])+1}/{int(task['launch_count'])}",
        progress_every_calls=50,
        progress_every_seconds=float(p["runtime"]["progress_every_seconds"]),
    )
    return {
        "index":int(task["index"]),
        "instrument":task["instrument"],
        "launch_index":int(task["launch_index"]),
        "completed_calls":int(raw["completed_calls"]),
        "best":raw.get("best"),
        "worker_elapsed_seconds":time.perf_counter()-t0,
    }


def capacity_null_gate(root:Path,k1_run:Path,protocol:dict[str,Any],fields65,workers:int,run:Path)->tuple[dict[str,Any],dict[str,Any]]:
    caps=protocol["caps"]; ev65=_evaluator(protocol,fields65)
    pairs={"identity":build_identity_pair(caps),"null_capacity":build_null_capacity_pair(caps),"full_capacity":build_full_capacity_pair(caps)}
    result={}; instruments={}
    identity=ev65(pairs["identity"],[]); result["identity"]={"G65":identity}

    # Four independent deterministic launches (2 instruments x 2 launches) are
    # parallelized. This is execution-only: seeds, call ceilings, adaptive launch
    # trajectories, objective, and scientific gates are byte-semantically unchanged.
    launch_count=int(protocol["fitter"]["reference"]["independent_launches"])
    tasks=[]; idx=0
    for instrument_index,name in enumerate(["null_capacity","full_capacity"]):
        base_seed=derive_seed("P13-S0-K2-CAPACITY",instrument_index)
        for launch_index in range(launch_count):
            seed=base_seed if launch_index==0 else (base_seed+launch_index*0x85EBCA6B)&0xffffffff
            tasks.append({
                "index":idx,"instrument":name,"launch_index":launch_index,"launch_count":launch_count,
                "seed":seed,"pair":pairs[name],
            })
            idx+=1
    launch_rows=run_parallel_tasks(
        tasks,_worker_capacity_launch_task,_worker_init,
        (str(root),str(k1_run),protocol,33),
        max(1,min(int(workers),len(tasks))),
        run/"authoritative/capacity_reference_launches.jsonl",
        "K2 capacity launches",int(protocol["runtime"]["progress_every_seconds"]),
    )

    for name in ["null_capacity","full_capacity"]:
        rows=[r for r in launch_rows if r.get("instrument")==name and "fatal_exception" not in r]
        ref=_capacity_reference_from_launch_rows(rows,protocol["fitter"]["reference"]) if len(rows)==launch_count else {"qualified":False,"launch_relative_agreement":None,"best":None,"launches":[]}
        best=ref.get("best")
        if not ref.get("qualified") or best is None:
            result[name]={"reference_qualified":False,"reference":ref}; continue
        theta=list(map(float,best["theta_vector"])); score65=ev65(pairs[name],theta)
        result[name]={
            "reference_qualified":True,
            "reference_calls":sum(int(x["completed_calls"]) for x in ref["launches"]),
            "launch_relative_agreement":ref["launch_relative_agreement"],
            "theta":theta,"G33_best_J":best["J_princ"],"G65":score65,
        }
        instruments[name]={"pair":pairs[name],"theta":theta,"role":"CALIBRATION_ONLY","forbidden_from_search":True}

    ok=all(result.get(k,{}).get("reference_qualified") for k in ["null_capacity","full_capacity"]) and identity.get("J_princ") is not None
    metrics={}
    if ok:
        ji=float(identity["J_princ"]); jn=float(result["null_capacity"]["G65"]["J_princ"]); jf=float(result["full_capacity"]["G65"]["J_princ"])
        metrics={"J_identity":ji,"J_null":jn,"J_full":jf,"identity_over_full":ji/max(jf,1e-15),"identity_over_null":ji/max(jn,1e-15),"null_over_full":jn/max(jf,1e-15)}
        c=protocol["capacity_null"]
        gates={"full_improvement":metrics["identity_over_full"]>=float(c["full_identity_improvement_min"]),"null_not_too_good":metrics["identity_over_null"]<=float(c["null_identity_improvement_max"]),"full_vs_null":metrics["null_over_full"]>=float(c["null_over_full_score_min"])}
        ok=all(gates.values())
    else:gates={"reference_qualification":False}
    result["metrics"]=metrics; result["gates"]=gates; result["status"]="PASS" if ok else "FAIL"
    result["execution"]={
        "parallel_independent_launches":True,
        "launch_count_total":len(tasks),
        "max_parallel_launch_workers":max(1,min(int(workers),len(tasks))),
        "checkpoint_ledger":"authoritative/capacity_reference_launches.jsonl",
        "scientific_semantics_changed":False,
    }
    return result,instruments


_WORKER={}
def _worker_init(root_s:str,k1_s:str,protocol:dict[str,Any],grid:int):
    root=Path(root_s); add_source_paths(root)
    global _WORKER
    _WORKER={"root":root,"k1":Path(k1_s),"protocol":protocol,"fields":load_field_views(Path(k1_s),"CALIBRATION_COEF",grid)}


def _worker_fit_task(task:dict[str,Any])->dict[str,Any]:
    t0=time.perf_counter()
    p=_WORKER["protocol"]; ev=_evaluator(p,_WORKER["fields"]); pair=task["pair"]
    prod=production_fit(pair,ev,p["fitter"]["production"]); ps=_fit_summary(prod)
    ref=reference_fit(pair,ev,p["fitter"]["reference"],int(task["reference_seed"])); rb=ref.get("best")
    ratio=None
    if ps["best"] is not None and ref.get("qualified") and rb is not None: ratio=float(ps["best"]["J_princ"])/max(float(rb["J_princ"]),1e-15)
    return {"index":task["index"],"structural_hash":pair["structural_hash"],"parameter_count":len(parameter_names_for_pair(pair)),"production":ps,"reference":{"qualified":bool(ref.get("qualified")),"launch_relative_agreement":ref.get("launch_relative_agreement"),"best":rb,"total_calls":sum(x["completed_calls"] for x in ref["launches"])},"R_fit":ratio,"worker_elapsed_seconds":time.perf_counter()-t0}


def run_parallel_tasks(tasks:list[dict[str,Any]],worker:Callable[[dict[str,Any]],dict[str,Any]],initializer:Callable,initargs:tuple,workers:int,ledger:Path,stage:str,progress_seconds:int=60)->list[dict[str,Any]]:
    done={}
    if ledger.is_file():
        for line in ledger.read_text().splitlines():
            if line.strip():
                r=json.loads(line); done[int(r["index"])]=r
    pending=[t for t in tasks if int(t["index"]) not in done]
    start=time.perf_counter(); last=start; ledger.parent.mkdir(parents=True,exist_ok=True)
    with ledger.open("a") as out, ProcessPoolExecutor(max_workers=workers,initializer=initializer,initargs=initargs) as ex:
        futs={ex.submit(worker,t):t for t in pending}
        for fut in as_completed(futs):
            t=futs[fut]
            try:r=fut.result()
            except Exception as exc:r={"index":t["index"],"fatal_exception_type":type(exc).__name__,"fatal_exception":str(exc)}
            out.write(json.dumps(r,sort_keys=True)+"\n"); out.flush(); os.fsync(out.fileno()); done[int(t["index"])]=r
            now=time.perf_counter()
            if now-last>=progress_seconds or len(done)==len(tasks):
                elapsed=now-start; completed=len(done); rate=max((len(tasks)-len(pending)+len([x for x in done if x not in []]))/max(elapsed,1e-9),0.0)
                # ETA based only on this invocation's pending completion count.
                newdone=len(tasks)-len(pending)+sum(1 for t0 in pending if int(t0["index"]) in done)
                local_done=sum(1 for t0 in pending if int(t0["index"]) in done); local_rate=local_done/max(elapsed,1e-9); eta=(len(pending)-local_done)/local_rate if local_rate>0 else math.inf
                print(f"[{stage}] processed={completed}/{len(tasks)} elapsed={elapsed:.1f}s rate={local_rate:.4g}/s ETA={eta/3600:.2f}h",flush=True); last=now
    return [done[i] for i in sorted(done) if i < len(tasks)]


def fitter_gate(root:Path,k1_run:Path,protocol:dict[str,Any],workers:int,run:Path)->dict[str,Any]:
    from p11rawxt_s1.k2_ast_runtime import random_pair
    rng=np.random.default_rng(derive_seed(protocol["fitter"]["seed_namespace"],0)); tasks=[]
    for i in range(int(protocol["fitter"]["fresh_prior_skeleton_count"])):
        pair=random_pair(rng,protocol["caps"]); tasks.append({"index":i,"pair":pair,"reference_seed":derive_seed(protocol["fitter"]["seed_namespace"],i+1)})
    write_json(run/"authoritative/fitter_fresh_prior_membership.json",{"schema":"P13_K2_FITTER_MEMBERSHIP_V1","rows":[{"index":x["index"],"structural_hash":x["pair"]["structural_hash"]} for x in tasks]})
    rows=run_parallel_tasks(tasks,_worker_fit_task,_worker_init,(str(root),str(k1_run),protocol,33),workers,run/"authoritative/fitter_results.jsonl","K2 fitter",int(protocol["runtime"]["progress_every_seconds"]))
    qualified=[r for r in rows if r.get("R_fit") is not None and not r.get("fatal_exception")]
    ratios=np.asarray([float(r["R_fit"]) for r in qualified],float)
    median=float(np.median(ratios)) if len(ratios) else None; p90=float(np.quantile(ratios,0.9)) if len(ratios) else None
    gates={"reference_qualified_count":len(qualified)>=int(protocol["fitter"]["minimum_reference_qualified"]),"median_R_fit":median is not None and median<=float(protocol["fitter"]["median_R_fit_max"]),"p90_R_fit":p90 is not None and p90<=float(protocol["fitter"]["p90_R_fit_max"]),"fatal_count_zero":sum("fatal_exception" in r for r in rows)==0}
    return {"status":"PASS" if all(gates.values()) else "FAIL","gates":gates,"total_skeletons":len(tasks),"reference_qualified":len(qualified),"median_R_fit":median,"p90_R_fit":p90,"production_calls_total":sum(int(r.get("production",{}).get("completed_calls",0)) for r in rows),"reference_calls_total":sum(int(r.get("reference",{}).get("total_calls",0)) for r in rows),"worker_cpu_seconds_total":sum(float(r.get("worker_elapsed_seconds",0.0)) for r in rows),"result_ledger":"authoritative/fitter_results.jsonl"}


def _warning_bucket(message:str)->str:
    text=str(message).lower()
    if "overflow encountered in exp" in text:return "overflow_exp"
    if "overflow encountered" in text:return "overflow_numeric"
    if "invalid value encountered" in text:return "invalid_numeric"
    if "divide by zero" in text:return "divide_by_zero"
    return "other_runtime_warning"


def _worker_parent_attempt(task:dict[str,Any])->dict[str,Any]:
    """Evaluate one frozen grammar-prior parent attempt.

    Scientific semantics are unchanged from the preregistered serial implementation:
    each already-generated raw pair receives the same production fitter on the same
    CALIBRATION coefficient family.  Warning capture is observational only.
    """
    t0=time.perf_counter(); p=_WORKER["protocol"]
    if task.get("generation_failure") is not None:
        return {
            "index":int(task["index"]),"attempt":int(task["index"])+1,
            "structural_hash":None,"attempt_status":"GRAMMAR_PRIOR_GENERATION_FAIL",
            "generation_failure":task["generation_failure"],"F4":False,
            "completed_calls":0,"highest_stage_index":None,"highest_feasibility_level":"NONE",
            "stage_call_counts":{},"warning_counts":{},"worker_elapsed_seconds":time.perf_counter()-t0,
        }
    pair=task["pair"]; ev=_evaluator(p,_WORKER["fields"]); warning_counts=Counter()
    old_showwarning=warnings.showwarning
    def _capture(message,category,filename,lineno,file=None,line=None):
        if issubclass(category,RuntimeWarning): warning_counts[_warning_bucket(str(message))]+=1
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("always",RuntimeWarning)
            warnings.showwarning=_capture
            fit=production_fit(pair,ev,p["fitter"]["production"])
    finally:
        warnings.showwarning=old_showwarning
    best=best_f4(fit); records=fit.get("call_records",[])
    stage_counts=Counter(int(r.get("stage_index",0)) for r in records)
    max_stage=max(stage_counts,default=0)
    stage_name={0:"NONE",1:"F0",2:"F1",3:"F2",4:"F3",5:"F4"}.get(max_stage,f"STAGE_{max_stage}")
    row={
        "index":int(task["index"]),"attempt":int(task["index"])+1,
        "structural_hash":pair["structural_hash"],"attempt_status":"F4" if best is not None else "NO_F4",
        "F4":best is not None,"completed_calls":int(fit.get("completed_calls",0)),
        "highest_stage_index":int(max_stage),"highest_feasibility_level":stage_name,
        "stage_call_counts":{str(k):int(v) for k,v in sorted(stage_counts.items())},
        "warning_counts":{k:int(v) for k,v in sorted(warning_counts.items())},
        "parameter_count":len(parameter_names_for_pair(pair)),
        "worker_elapsed_seconds":time.perf_counter()-t0,
    }
    if best is not None:
        row["parent_candidate"]={
            "pair":pair,"theta_vector":best["theta_vector"],"J_parent":best["J_princ"],
            "parameter_count":len(parameter_names_for_pair(pair)),
        }
    return row


def _load_indexed_jsonl(path:Path)->dict[int,dict[str,Any]]:
    done={}
    if not path.is_file():return done
    for line in path.read_text().splitlines():
        if not line.strip():continue
        row=json.loads(line)
        if "index" not in row:raise RuntimeError(f"Incompatible parent-census ledger schema: {path}")
        idx=int(row["index"])
        if idx in done and done[idx]!=row:raise RuntimeError(f"Conflicting duplicate parent-census row index={idx}")
        done[idx]=row
    return done


def _prepare_parent_attempt_tasks(protocol:dict[str,Any])->list[dict[str,Any]]:
    """Reproduce the exact deterministic grammar-prior pair sequence in the coordinator.

    Pair generation remains serial because it consumes one frozen RNG stream.  The expensive
    production fits are parallelized.  Regenerating this inexpensive membership sequence on
    restart avoids any duplicate candidate-data archive while preserving exact attempt order.
    """
    from p11rawxt_s1.k2_ast_runtime import random_pair
    n=int(protocol["proposal_geometry"]["proposal_attempts"])
    rng=np.random.default_rng(derive_seed(protocol["proposal_geometry"]["seed_namespace"],0))
    tasks=[]; generation_failures=0; start=time.perf_counter()
    for i in range(n):
        try: tasks.append({"index":i,"pair":random_pair(rng,protocol["caps"])})
        except Exception as exc:
            generation_failures+=1; tasks.append({"index":i,"generation_failure":{"type":type(exc).__name__,"message":str(exc)}})
        if (i+1)%1024==0 or i+1==n:
            elapsed=time.perf_counter()-start; rate=(i+1)/max(elapsed,1e-12); eta=(n-i-1)/rate if rate>0 else math.inf
            print(f"[K2 V2 parent membership] generated={i+1}/{n} generation_failures={generation_failures} elapsed={elapsed:.1f}s rate={rate:.3g}/s ETA={eta/60:.1f}m",flush=True)
    return tasks


def _parent_census_progress(done:dict[int,dict[str,Any]],total:int,start:float,initial_completed:int)->str:
    completed=len(done); elapsed=time.perf_counter()-start
    local_completed=max(completed-initial_completed,0); rate=local_completed/max(elapsed,1e-12); eta=(total-completed)/rate if rate>0 else math.inf
    f4=sum(bool(r.get("F4")) for r in done.values())
    stage=Counter(r.get("highest_feasibility_level","NONE") for r in done.values())
    warn=Counter()
    for r in done.values(): warn.update(r.get("warning_counts",{}))
    stage_s=",".join(f"{k}:{stage[k]}" for k in ["NONE","F0","F1","F2","F3","F4"] if stage[k]) or "none"
    warn_s=",".join(f"{k}:{v}" for k,v in sorted(warn.items())) or "none"
    return f"[K2 V2 parent census] processed={completed}/{total} F4={f4} stages={stage_s} warnings={warn_s} elapsed={elapsed:.1f}s rate={rate:.3g}/s ETA={eta/3600:.2f}h"


def _run_parent_census(root:Path,k1_run:Path,protocol:dict[str,Any],workers:int,run:Path)->list[dict[str,Any]]:
    ledger=run/"authoritative/proposal_parent_sampling.jsonl"; ledger.parent.mkdir(parents=True,exist_ok=True)
    legacy=run/"authoritative/proposal_parent_sampling_pre_k2r_incomplete.jsonl"
    # The pre-repair ledger used {attempt,...} without a stable task index and was not restart-safe.
    # Preserve it by rename only; never merge it into formal K2R evidence.
    if ledger.is_file():
        first=next((json.loads(x) for x in ledger.read_text().splitlines() if x.strip()),None)
        if first is not None and "index" not in first:
            if legacy.exists(): raise RuntimeError(f"Both legacy and active parent ledgers exist: {legacy}")
            os.replace(ledger,legacy)
    done=_load_indexed_jsonl(ledger)
    tasks=_prepare_parent_attempt_tasks(protocol); total=len(tasks)
    pending=[t for t in tasks if int(t["index"]) not in done]
    if not pending:return [done[i] for i in range(total)]
    start=time.perf_counter(); last_print=start; initial_completed=len(done)
    max_inflight=max(1,2*int(workers))
    with ledger.open("a") as out, ProcessPoolExecutor(max_workers=workers,initializer=_worker_init,initargs=(str(root),str(k1_run),protocol,33)) as ex:
        iterator=iter(pending); futures={}
        for _ in range(min(max_inflight,len(pending))):
            try:t=next(iterator)
            except StopIteration:break
            futures[ex.submit(_worker_parent_attempt,t)]=t
        while futures:
            fut=next(as_completed(futures)); task=futures.pop(fut)
            try:row=fut.result()
            except Exception as exc:
                row={"index":int(task["index"]),"attempt":int(task["index"])+1,"attempt_status":"FATAL_EXCEPTION","F4":False,"fatal_exception_type":type(exc).__name__,"fatal_exception":str(exc),"completed_calls":0,"highest_stage_index":None,"highest_feasibility_level":"NONE","stage_call_counts":{},"warning_counts":{},"worker_elapsed_seconds":0.0}
            out.write(json.dumps(row,sort_keys=True)+"\n"); out.flush(); os.fsync(out.fileno()); done[int(row["index"])]=row
            try:nxt=next(iterator); futures[ex.submit(_worker_parent_attempt,nxt)]=nxt
            except StopIteration:pass
            now=time.perf_counter()
            if len(done)%25==0 or now-last_print>=float(protocol["runtime"]["progress_every_seconds"]) or len(done)==total:
                print(_parent_census_progress(done,total,start,initial_completed),flush=True); last_print=now
    if len(done)!=total:raise RuntimeError(f"Parent census incomplete {len(done)}/{total}")
    return [done[i] for i in range(total)]


def _parent_pool_from_census(rows:list[dict[str,Any]],protocol:dict[str,Any])->dict[str,Any]:
    n_parent=int(protocol["proposal_geometry"]["parent_count"]); ordered=sorted(rows,key=lambda r:int(r["index"]))
    f4=[r for r in ordered if r.get("F4") and r.get("parent_candidate") is not None]
    selected=[]
    for src in f4[:n_parent]:
        p=dict(src["parent_candidate"]); p["parent_index"]=len(selected); p["source_attempt"]=int(src["attempt"]); p["source_structural_hash"]=src.get("structural_hash"); selected.append(p)
    stage=Counter(r.get("highest_feasibility_level","NONE") for r in ordered); warn=Counter()
    for r in ordered:warn.update(r.get("warning_counts",{}))
    fatal=sum(r.get("attempt_status")=="FATAL_EXCEPTION" for r in ordered)
    return {
        "schema":"P13_K2R_V2_PARENT_POOL_V2",
        "sampling_attempts":len(ordered),"sampling_budget":int(protocol["proposal_geometry"]["proposal_attempts"]),
        "F4_count_total":len(f4),"selected_parent_count":len(selected),"required_parent_count":n_parent,
        "rows":selected,"selection":"first 64 production-F4-valid grammar-prior parents by frozen deterministic attempt index; no objective-quality threshold",
        "full_8192_census":len(ordered)==int(protocol["proposal_geometry"]["proposal_attempts"]),
        "attempt_highest_stage_census":{k:int(v) for k,v in sorted(stage.items())},
        "runtime_warning_census":{k:int(v) for k,v in sorted(warn.items())},
        "fatal_exception_count":int(fatal),
        "status":"PASS" if len(selected)>=n_parent and fatal==0 else "FAIL",
    }


def build_parent_pool(root:Path,k1_run:Path,protocol:dict[str,Any],workers:int,run:Path)->dict[str,Any]:
    summary_path=run/"authoritative/proposal_parent_pool.json"
    if summary_path.is_file():
        old=json_load(summary_path)
        if old.get("schema")=="P13_K2R_V2_PARENT_POOL_V2":return old
        legacy=run/"authoritative/proposal_parent_pool_pre_k2r.json"
        if not legacy.exists():os.replace(summary_path,legacy)
        else:summary_path.unlink()
    rows=_run_parent_census(root,k1_run,protocol,workers,run)
    summary=_parent_pool_from_census(rows,protocol); write_json(summary_path,summary); return summary

def _worker_proposal_task(task:dict[str,Any])->dict[str,Any]:
    t0=time.perf_counter()
    p=_WORKER["protocol"]; ev=_evaluator(p,_WORKER["fields"]); parent=task["parent"]
    rng=np.random.default_rng(int(task["seed"]))
    try:g=residual_graft(parent["pair"],rng,p["caps"],int(p["proposal_geometry"]["residual_graft_subtree_nodes_max"]))
    except Exception as exc:return {"index":task["index"],"parent_index":parent["parent_index"],"proposal_status":"GRAMMAR_OR_CAP_REJECT","reason":str(exc),"worker_elapsed_seconds":time.perf_counter()-t0}
    child=g["pair"]; fit=production_fit(child,ev,p["fitter"]["production"]); best=best_f4(fit)
    if best is None:return {"index":task["index"],"parent_index":parent["parent_index"],"proposal_status":"VALID_SKELETON_NO_F4_FIT","child_structural_hash":child["structural_hash"],"completed_calls":fit.get("completed_calls",0),"worker_elapsed_seconds":time.perf_counter()-t0}
    improvement=(float(parent["J_parent"])-float(best["J_princ"]))/max(float(parent["J_parent"]),1e-15)
    return {"index":task["index"],"parent_index":parent["parent_index"],"proposal_status":"F4_CHILD","child_structural_hash":child["structural_hash"],"completed_calls":fit.get("completed_calls",0),"parent_J":parent["J_parent"],"child_J":best["J_princ"],"relative_improvement":improvement,"new_theta":g["new_theta"],"component":g["component"],"worker_elapsed_seconds":time.perf_counter()-t0}


def proposal_gate(root:Path,k1_run:Path,protocol:dict[str,Any],workers:int,run:Path)->dict[str,Any]:
    parent_summary=build_parent_pool(root,k1_run,protocol,workers,run); pool=parent_summary["rows"]
    required=int(protocol["proposal_geometry"]["parent_count"])
    fatal_parent=int(parent_summary.get("fatal_exception_count",0))
    if fatal_parent>0 or len(pool)<required:
        parent_failure="RAW_PRIOR_PARENT_CENSUS_NUMERICAL_UNRESOLVED" if fatal_parent>0 else "RAW_PRIOR_F4_PARENT_ATTAINMENT_FAIL"
        interpretation=("raw-prior parent census contains fatal numerical/software exceptions; residual-graft cannot be adjudicated" if fatal_parent>0
                        else "raw-prior initialization/F4-parent attainment failure; this is not evidence that residual-graft partial credit failed")
        return {
            "status":"FAIL","failure":parent_failure,
            "raw_prior_parent_census_status":"UNRESOLVED_NUMERICAL" if fatal_parent>0 else "FAIL","parent_count":len(pool),"required_parent_count":required,
            "sampling_attempts":parent_summary.get("sampling_attempts"),"F4_count_total":parent_summary.get("F4_count_total"),
            "attempt_highest_stage_census":parent_summary.get("attempt_highest_stage_census",{}),
            "runtime_warning_census":parent_summary.get("runtime_warning_census",{}),
            "fatal_exception_count":fatal_parent,
            "residual_graft_adjudication":"NOT_ADJUDICATED",
            "scientific_interpretation":interpretation,
            "parent_census_ledger":"authoritative/proposal_parent_sampling.jsonl",
        }
    n=int(protocol["proposal_geometry"]["proposal_attempts"]); ns=protocol["proposal_geometry"]["seed_namespace"]
    tasks=[{"index":i,"parent":pool[i%len(pool)],"seed":derive_seed(ns,i+1)} for i in range(n)]
    rows=run_parallel_tasks(tasks,_worker_proposal_task,_worker_init,(str(root),str(k1_run),protocol,33),workers,run/"authoritative/v2_proposal_results.jsonl","K2 V2 proposals",int(protocol["runtime"]["progress_every_seconds"]))
    f4=[r for r in rows if r.get("proposal_status")=="F4_CHILD"]; n5=sum(float(r.get("relative_improvement",-math.inf))>=0.05 for r in f4); n20=sum(float(r.get("relative_improvement",-math.inf))>=0.20 for r in f4)
    gates={"parent_pool":len(pool)>=required,"improve_5pct":n5>=int(protocol["proposal_geometry"]["improve_5pct_min_count"]),"improve_20pct":n20>=int(protocol["proposal_geometry"]["improve_20pct_min_count"]),"no_fatal":sum("fatal_exception" in r for r in rows)==0}
    total_calls=sum(int(r.get("completed_calls",0)) for r in rows); cpu=sum(float(r.get("worker_elapsed_seconds",0.0)) for r in rows)
    return {"status":"PASS" if all(gates.values()) else "FAIL","failure":None if all(gates.values()) else "PROPOSAL_GEOMETRY_NOT_QUALIFIED","gates":gates,"raw_prior_parent_census_status":"PASS","parent_count":len(pool),"raw_prior_F4_count_total":parent_summary.get("F4_count_total"),"attempted_proposals":n,"F4_children":len(f4),"improve_5pct_count":n5,"improve_20pct_count":n20,"grammar_or_cap_rejections":sum(r.get("proposal_status")=="GRAMMAR_OR_CAP_REJECT" for r in rows),"concrete_evaluator_calls_total":total_calls,"mean_concrete_calls_per_attempt":total_calls/max(n,1),"worker_cpu_seconds_total":cpu,"mean_worker_cpu_seconds_per_attempt":cpu/max(n,1),"ideal_16_worker_hours_for_8192_attempts":cpu/16.0/3600.0,"result_ledger":"authoritative/v2_proposal_results.jsonl","residual_graft_adjudication":"ADJUDICATED"}

def evaluator_fidelity_gate(root:Path,k1_run:Path,protocol:dict[str,Any],instruments:dict[str,Any],run:Path)->dict[str,Any]:
    f33=load_field_views(k1_run,"CALIBRATION_COEF",33); f65=load_field_views(k1_run,"CALIBRATION_COEF",65); ev33=_evaluator(protocol,f33); ev65=_evaluator(protocol,f65)
    rows=[]
    pairs=[("identity",build_identity_pair(protocol["caps"]),[])]
    for name in ["null_capacity","full_capacity"]:
        if name in instruments:pairs.append((name,instruments[name]["pair"],instruments[name]["theta"]))
    # Reuse the first F4 parent-pool records, which are response-blind and already authoritative.
    pp=run/"authoritative/proposal_parent_pool.json"
    if pp.is_file():
        for r in json_load(pp)["rows"][:int(protocol["evaluator_fidelity"]["additional_fresh_F4_map_count"])]: pairs.append((f"fresh_parent_{r['parent_index']}",r["pair"],r["theta_vector"]))
    for name,pair,theta in pairs:
        a=ev33(pair,theta); b=ev65(pair,theta); rel=None
        if a.get("J_princ") is not None and b.get("J_princ") is not None: rel=abs(float(a["J_princ"])-float(b["J_princ"]))/max(abs(float(b["J_princ"])),1e-14)
        rows.append({"name":name,"G33_stage":a["highest_feasibility_level"],"G65_stage":b["highest_feasibility_level"],"G33_J":a.get("J_princ"),"G65_J":b.get("J_princ"),"relative_J_difference":rel,"G33_elapsed_seconds":a.get("elapsed_seconds"),"G65_elapsed_seconds":b.get("elapsed_seconds"),"G65_lower_order":[x.get("lower_order_diagnostics") for x in b.get("per_field",[])]})
    resolved=[r for r in rows if r["relative_J_difference"] is not None]; maxrel=max([r["relative_J_difference"] for r in resolved],default=math.inf); ceiling=float(protocol["evaluator_fidelity"]["maximum_relative_J_difference"])
    ok=len(resolved)==len(rows) and maxrel<=ceiling
    return {"status":"PASS" if ok else "FAIL","resolved_count":len(resolved),"map_count":len(rows),"maximum_relative_J_difference":maxrel,"ceiling":ceiling,"rows":rows}


def causal_response_gate(root:Path,k1_run:Path,protocol:dict[str,Any],instruments:dict[str,Any])->dict[str,Any]:
    pairs={"identity":(build_identity_pair(protocol["caps"]),[])}
    for name in ["null_capacity","full_capacity"]:
        if name not in instruments:return {"status":"FAIL","failure":"CAPACITY_INSTRUMENT_UNAVAILABLE"}
        pairs[name]=(instruments[name]["pair"],instruments[name]["theta"])
    fields=[f"P13_CALIBRATION_COEF_{i:02d}" for i in range(1,int(protocol["causal_response_calibration"]["coefficient_count"])+1)]
    rows=[]
    for name,(pair,theta) in pairs.items():
        for fid in fields:
            for case in range(int(protocol["causal_response_calibration"]["case_count_per_coefficient"])):
                scores=[]
                for n in protocol["causal_response_calibration"]["grids"]:
                    scores.append(calibration_pair_score(root,k1_run,pair,theta,fid,case,int(n),protocol["causal_response_calibration"]))
                interval=bounded_interval(scores[0]["relative_energy_error"],scores[1]["relative_energy_error"])
                rows.append({"chart":name,"field_id":fid,"case_index":case,"coarse":scores[0],"fine":scores[1],"interval":interval})
                print(f"[K2 causal] chart={name} field={fid} case={case} fine={interval['nominal']:.6g} unc={interval['uncertainty']:.3g}",flush=True)
    threshold=float(protocol["causal_response_calibration"]["relative_response_threshold"])
    full=[r for r in rows if r["chart"]=="full_capacity"]; identity=[r for r in rows if r["chart"]=="identity"]; null=[r for r in rows if r["chart"]=="null_capacity"]
    full_gate=len(full)==8 and all(float(r["interval"]["upper"])<threshold for r in full)
    contrast_gate=any(float(r["interval"]["lower"])>threshold for r in identity+null)
    gates={"full_capacity_all_upper_below_threshold":full_gate,"identity_or_null_some_lower_above_threshold":contrast_gate}
    return {"status":"PASS" if all(gates.values()) else "FAIL","gates":gates,"threshold":threshold,"rows":rows,"calibration_only":True,"generator_provenance_used_only_for_exact_calibration_truth":True}



def build_source_manifest(root:Path,protocol_path:Path)->dict[str,Any]:
    rels=[
      "phases/p13/coefficient_law_raw_xt/configs/p13_s0_k2_protocol.json",
      "phases/p13/coefficient_law_raw_xt/src/p13rawxt/ast_runtime.py",
      "phases/p13/coefficient_law_raw_xt/src/p13rawxt/family_evaluator.py",
      "phases/p13/coefficient_law_raw_xt/src/p13rawxt/calibration_instruments.py",
      "phases/p13/coefficient_law_raw_xt/src/p13rawxt/proposal_geometry.py",
      "phases/p13/coefficient_law_raw_xt/src/p13rawxt/causal_calibration.py",
      "phases/p13/coefficient_law_raw_xt/src/p13rawxt/k2_qualification.py",
      "phases/p13/coefficient_law_raw_xt/scripts/run_p13_s0_k2.sh",
      "phases/p13/coefficient_law_raw_xt/scripts/package_p13_s0_k2_audit.sh"
    ]
    rows=[]
    for rel in rels:
        path=root/rel
        if path.is_file(): rows.append({"path":rel,"bytes":path.stat().st_size,"sha256":sha256_path(path)})
    return {"files":rows}

def build_authoritative_evidence_manifest(root:Path,run:Path)->dict[str,Any]:
    rels=[
      "authoritative/fitter_fresh_prior_membership.json",
      "authoritative/fitter_results.jsonl",
      "authoritative/proposal_parent_sampling.jsonl",
      "authoritative/proposal_parent_sampling_pre_k2r_incomplete.jsonl",
      "authoritative/proposal_parent_pool.json",
      "authoritative/proposal_parent_pool_pre_k2r.json",
      "authoritative/v2_proposal_results.jsonl"
    ]
    rows=[]
    for rel in rels:
        p=run/rel
        if p.is_file():
            line_count=None
            if p.suffix==".jsonl": line_count=sum(1 for line in p.open() if line.strip())
            rows.append({"path":str(p.relative_to(root)),"bytes":p.stat().st_size,"sha256":sha256_path(p),"line_count":line_count})
    return {"schema":"P13_S0_K2_AUTHORITATIVE_EVIDENCE_MANIFEST_V1","rows":rows,"large_or_redundant_objects_copied_into_audit":False}

def _semantic_digest(summary_parts:dict[str,Any],protocol_sha:str,parent_semantic:str)->str:
    payload={"stage":"P13-S0-K2","protocol_sha256":protocol_sha,"parent_K1_semantic":parent_semantic,"results":summary_parts}
    return sha256_bytes(canonical_json_bytes(payload))


def update_rolling_context(root:Path,summary:dict[str,Any],semantic:str,run_rel:str)->None:
    path=root/"P13_S0_ROLLING_CONTEXT.md"
    text=path.read_text() if path.is_file() else "# P13 S0 Rolling Context\n"
    marker="<!-- K2_FORMAL_RESULT -->"
    block=f'''{marker}\n\n## S0-K2 formal result\n\n- authoritative run: `{run_rel}`\n- `OVERALL_STATUS`: **{summary['OVERALL_STATUS']}**\n- semantic output digest: `{semantic}`\n- failure classifications: `{summary.get('failure_classifications',[])}`\n- next action: `{summary['NEXT_ACTION']}`\n- formal S1 search remains **not authorized**; S0-K3 adjudication is still required.\n- no DEVELOPMENT/SEALED payload was opened; no OPENED_TRANSFER_DIAGNOSTIC result was read.\n\nK2 gates: `{json.dumps(summary['gate_statuses'],sort_keys=True)}`\n'''
    if marker in text:text=text.split(marker)[0].rstrip()+"\n\n"+block
    else:text=text.rstrip()+"\n\n"+block
    write_text(path,text)


def main(argv:list[str]|None=None)->int:
    ap=argparse.ArgumentParser(); ap.add_argument("--project-root",default="."); ap.add_argument("--workers",type=int,default=None); ap.add_argument("--resume",action="store_true",default=True); args=ap.parse_args(argv)
    root=Path(args.project_root).resolve(); add_source_paths(root)
    protocol_path=root/"phases/p13/coefficient_law_raw_xt/configs/p13_s0_k2_protocol.json"; protocol=json_load(protocol_path); protocol_sha=sha256_path(protocol_path)
    k1_run,parent=verify_k1(root,protocol)
    workers=args.workers or int(os.environ.get("NSLOTS","17"))-1; workers=max(1,min(int(protocol["runtime"]["default_workers"]),workers))
    marker=root/"phases/p13/coefficient_law_raw_xt/runs/LATEST_P13_S0_K2_RUN.txt"
    run=None
    if args.resume and marker.is_file():
        cand=root/marker.read_text().strip() if not Path(marker.read_text().strip()).is_absolute() else Path(marker.read_text().strip())
        if cand.is_dir() and not (cand/"OVERALL_STATUS.txt").is_file(): run=cand
    if run is None:
        stamp=time.strftime("%Y%m%dT%H%M%SZ",time.gmtime()); run=root/f"phases/p13/coefficient_law_raw_xt/runs/p13_s0_k2_qualification_{stamp}"; run.mkdir(parents=True)
        write_text(marker,str(run.relative_to(root))+"\n")
    start=time.perf_counter(); print(f"P13-S0-K2 run={run.relative_to(root)} workers={workers}",flush=True)
    write_json(run/"parent_k1_inplace_verification.json",parent)
    failure=[]; results={}
    stages=[
        ("m2_semantics",lambda: m2_semantics_gate(k1_run,protocol),"L3_M2_EXECUTION_NOT_QUALIFIED"),
        ("identifiability",lambda: recompute_identifiability(root,k1_run),"COEFFICIENT_FAMILY_NONIDENTIFYING"),
    ]
    for i,(name,fn,code) in enumerate(stages,1):
        print(f"[K2 {i}/9] {name}",flush=True); r=fn(); results[name]=r; write_json(run/f"{name}.json",r)
        if r.get("status")!="PASS": failure.append(code)
    print("[K2 3/9] gauge equivalence",flush=True); cal33=load_field_views(k1_run,"CALIBRATION_COEF",33); g=gauge_gate(protocol,cal33[:3]); results["gauge"]=g; write_json(run/"gauge_equivalence.json",g)
    if g["status"]!="PASS":failure.append("GAUGE_SEMANTICS_NOT_QUALIFIED")
    print("[K2 4/9] capacity/null separation",flush=True); cal65=load_field_views(k1_run,"CALIBRATION_COEF",65); cap,instruments=capacity_null_gate(root,k1_run,protocol,cal65,workers,run); results["capacity_null"]=cap; write_json(run/"capacity_null_separation.json",cap)
    # Exact calibration instruments are deliberately isolated outside S1 handoff manifests.
    (run/"calibration_only").mkdir(exist_ok=True); write_json(run/"calibration_only/capacity_instrument_lock.json",{"role":"CALIBRATION_ONLY","forbidden_from_search":True,"instrument_hashes":{k:v["pair"]["structural_hash"] for k,v in instruments.items()},"theta":{k:v["theta"] for k,v in instruments.items()}})
    if cap["status"]!="PASS":failure.append("REGIME_OR_CAPACITY_NOT_CLEAN")
    print("[K2 5/9] fresh-prior fitter qualification",flush=True); fit=fitter_gate(root,k1_run,protocol,workers,run); results["fitter"]=fit; write_json(run/"fitter_qualification.json",fit)
    if fit["status"]!="PASS":failure.append("FITTER_NOT_QUALIFIED")
    print("[K2 6/9] actual V2 residual-graft partial credit",flush=True); prop=proposal_gate(root,k1_run,protocol,workers,run); results["proposal_geometry"]=prop; write_json(run/"proposal_geometry_qualification.json",prop)
    parent_precondition_fail=prop.get("residual_graft_adjudication")=="NOT_ADJUDICATED"
    parent_attainment_fail=prop.get("failure")=="RAW_PRIOR_F4_PARENT_ATTAINMENT_FAIL"
    if parent_precondition_fail:
        failure.append(str(prop.get("failure")))
        print(f"[K2 6/9] FAIL-CLOSED: {prop.get('failure')}; residual-graft NOT_ADJUDICATED; stages 7-9 not run",flush=True)
        ef={"status":"NOT_RUN","reason":"RAW_PRIOR_F4_PARENT_ATTAINMENT_FAIL"}; results["evaluator_fidelity"]=ef; write_json(run/"evaluator_fidelity_cost.json",ef)
        lod={"status":"NOT_RUN","reason":"RAW_PRIOR_F4_PARENT_ATTAINMENT_FAIL","diagnostic_only":True}; results["lower_order_diagnostics"]=lod; write_json(run/"lower_order_diagnostics.json",lod)
        cr={"status":"NOT_RUN","reason":"RAW_PRIOR_F4_PARENT_ATTAINMENT_FAIL"}; results["causal_response"]=cr; write_json(run/"causal_response_feasibility.json",cr)
    else:
        if prop["status"]!="PASS":failure.append("PROPOSAL_GEOMETRY_NOT_QUALIFIED")
        print("[K2 7/9] evaluator fidelity/cost",flush=True); ef=evaluator_fidelity_gate(root,k1_run,protocol,instruments,run); results["evaluator_fidelity"]=ef; write_json(run/"evaluator_fidelity_cost.json",ef)
        if ef["status"]!="PASS":failure.append("NUMERICAL_EVALUATOR_NOT_QUALIFIED")
        print("[K2 8/9] lower-order diagnostics",flush=True)
        lod={"status":"PASS","diagnostic_only":True,"rows":[{"name":x["name"],"G65_lower_order":x.get("G65_lower_order")} for x in ef.get("rows",[])]}; results["lower_order_diagnostics"]=lod; write_json(run/"lower_order_diagnostics.json",lod)
        print("[K2 9/9] calibration-only causal response feasibility",flush=True); cr=causal_response_gate(root,k1_run,protocol,instruments); results["causal_response"]=cr; write_json(run/"causal_response_feasibility.json",cr)
        if cr["status"]!="PASS":failure.append("CALIBRATION_RESPONSE_SEPARATION_NOT_CLEAN")
    gate_statuses={k:v.get("status") for k,v in results.items()}; overall="PASS" if not failure else "FAIL"
    if overall=="PASS":next_action="P13-S0-K3_SCIENTIFIC_ADJUDICATION_AND_FREEZE"
    elif parent_attainment_fail:next_action="P13-S0-K2R_PARENT_SOURCE_SCIENTIFIC_DECISION_REQUIRED"
    elif parent_precondition_fail:next_action="P13-S0-K2R_PARENT_CENSUS_NUMERICAL_REPAIR_REQUIRED"
    else:next_action="P13-S0-K3_FAIL_CLOSED_ADJUDICATION"
    leakage={"status":"PASS","formal_candidate_search_run":False,"historical_response_outcomes_read":False,"opened_transfer_diagnostic_read":False,"development_or_sealed_opened":False,"capacity_instrument_used_as_search_information":False,"characteristic_formula_used_as_search_information":False,"top_k_percentile_target_membership":False,"candidate_specific_rescue":False,"response_aware_refit":False,"generator_provenance_scope":"CALIBRATION causal numerical truth only; not passed to production evaluator/fitter/proposal kernel"}
    write_json(run/"no_leakage_guard.json",leakage)
    summary={"OVERALL_STATUS":overall,"NEXT_ACTION":next_action,"failure_classifications":failure,"gate_statuses":gate_statuses,"parent_K1_run":str(k1_run.relative_to(root)),"parent_K1_semantic":parent["semantic"],"formal_S1_search_authorized":False,"K3_required_before_S1":True,"K2R_parent_source_decision_required":bool(parent_attainment_fail),"K2R_parent_census_numerical_repair_required":bool(parent_precondition_fail and not parent_attainment_fail),"elapsed_seconds":time.perf_counter()-start,"workers":workers,"authoritative_K1_data_reused_in_place":True,"K1_audit_runtime_input":False}
    semantic=_semantic_digest({"failure_classifications":failure,"gate_statuses":gate_statuses,"capacity_metrics":cap.get("metrics",{}),"fitter":{k:fit.get(k) for k in ["reference_qualified","median_R_fit","p90_R_fit"]},"proposal":{k:prop.get(k) for k in ["F4_children","improve_5pct_count","improve_20pct_count"]},"evaluator_max_rel":ef.get("maximum_relative_J_difference"),"causal_gates":cr.get("gates")},protocol_sha,parent["semantic"])
    summary["semantic_output_digest"]=semantic; write_json(run/"audit_summary.json",summary); write_json(run/"semantic_output_digest.json",{"stage":"P13-S0-K2","semantic_output_digest":semantic,"protocol_sha256":protocol_sha,"parent_K1_semantic":parent["semantic"]}); write_text(run/"OVERALL_STATUS.txt",overall+"\n"); write_text(run/"NEXT_ACTION.txt",next_action+"\n")
    # K3 handoff is metadata only; it points to K1/K2 authoritative paths and copies no data.
    write_json(run/"k3_handoff_manifest.json",{"schema":"P13_S0_K2_K3_HANDOFF_V1","K1_run":str(k1_run.relative_to(root)),"K2_run":str(run.relative_to(root)),"K1_open_search_object_manifest":str((k1_run/"open_search_object_manifest.json").relative_to(root)),"K1_private_commitments_manifest":str((k1_run/"private_payload_commitments.json").relative_to(root)),"K2_semantic_output_digest":semantic,"formal_S1_search_authorized":False,"K3_required":True,"immediate_next_action":next_action,"calibration_only_capacity_artifact_excluded_from_future_search_inputs":True})
    write_json(run/"authoritative_evidence_manifest.json",build_authoritative_evidence_manifest(root,run))
    write_json(run/"source_manifest.json",build_source_manifest(root,protocol_path))
    write_json(run/"runtime_environment.json",{"python":sys.version,"numpy":np.__version__,"platform":platform.platform(),"workers":workers,"NSLOTS":os.environ.get("NSLOTS"),"OMP_NUM_THREADS":os.environ.get("OMP_NUM_THREADS"),"OPENBLAS_NUM_THREADS":os.environ.get("OPENBLAS_NUM_THREADS")})
    update_rolling_context(root,summary,semantic,str(run.relative_to(root)))
    print(f"OVERALL_STATUS={overall}",flush=True); print(f"semantic_output_digest={semantic}",flush=True); print(f"NEXT_ACTION={next_action}",flush=True)
    # Scientific FAIL is a completed adjudication state, not a software crash.
    # Return 0 after a complete run so the audit can be packaged; exceptions still exit nonzero.
    return 0

if __name__=="__main__": raise SystemExit(main())
