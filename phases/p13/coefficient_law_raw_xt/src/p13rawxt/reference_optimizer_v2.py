from __future__ import annotations

import math
import time
from typing import Any, Callable

import numpy as np
from scipy.stats import qmc


def _theta_key(theta:list[float])->tuple[str,...]:
    return tuple(float(x).hex() for x in theta)


def _sobol(p:int,count:int,lower:float,upper:float,seed:int)->list[list[float]]:
    if p==0 or count<=0:
        return []
    exp=int(math.ceil(math.log2(max(1,count))))
    sampler=qmc.Sobol(d=p,scramble=True,seed=int(seed)&0xffffffff)
    unit=sampler.random_base2(exp)[:count]
    return (lower+(upper-lower)*unit).tolist()


def _sort_key(record:dict[str,Any])->tuple[Any,...]:
    stage=int(record.get("stage_index",-1))
    if stage==5 and record.get("J_princ") is not None:
        return (-stage,float(record["J_princ"]),float(record.get("J_XT",math.inf)),float(record.get("J_XX",math.inf)),tuple(float(x).hex() for x in record.get("theta_vector",[])))
    margins=tuple(float(x) for x in record.get("direct_margin_vector",[]))
    return (-stage,)+tuple(-x for x in margins)+(tuple(float(x).hex() for x in record.get("theta_vector",[])),)


def best_f4_record(records:list[dict[str,Any]])->dict[str,Any]|None:
    rows=[r for r in records if int(r.get("stage_index",-1))==5 and r.get("J_princ") is not None]
    return min(rows,key=_sort_key) if rows else None


def reference_launch_v2(
    pair:dict[str,Any],
    evaluate:Callable[[dict[str,Any],list[float]],dict[str,Any]],
    *,
    parameter_names:list[str],
    production_best:dict[str,Any]|None,
    launch_seed:int,
    call_budget:int,
    protocol:dict[str,Any],
    progress_label:str|None=None,
    progress_every_seconds:float=60.0,
)->dict[str,Any]:
    """Generic response-blind calibration reference launch.

    The common incumbent is only a numerical starting point: zero theta and the
    unchanged production fitter's best F4 theta, if one exists. Independent
    launches then use different scrambled Sobol/global and local perturbations.
    No response, characteristic, capacity-witness motif, or candidate-specific
    rule enters this optimizer.
    """
    p=len(parameter_names)
    lower,upper=map(float,protocol["parameter_bounds"])
    budget=max(1,int(call_budget))
    seen:set[tuple[str,...]]=set()
    records:list[dict[str,Any]]=[]
    start=time.perf_counter(); last=start

    def report(force:bool=False)->None:
        nonlocal last
        if not progress_label:
            return
        now=time.perf_counter()
        if not force and now-last<float(progress_every_seconds):
            return
        rate=len(records)/max(now-start,1e-12)
        eta=(budget-len(records))/rate if rate>0 else math.inf
        print(f"[{progress_label}] calls={len(records)}/{budget} elapsed={now-start:.1f}s rate={rate:.4g}/s ETA={eta/60:.1f}m",flush=True)
        last=now

    def add(theta:list[float]|np.ndarray)->None:
        if len(records)>=budget:
            return
        th=[min(upper,max(lower,float(v))) for v in list(theta)]
        key=_theta_key(th)
        if key in seen:
            return
        seen.add(key)
        rec=evaluate(pair,th)
        records.append(rec)
        report(False)

    add([0.0]*p)
    if production_best is not None and production_best.get("theta_vector") is not None:
        add(list(map(float,production_best["theta_vector"])))
    if p==0:
        report(True)
        best=best_f4_record(records)
        return {"completed_calls":len(records),"best":best,"F4_count":sum(int(r.get("stage_index",-1))==5 and r.get("J_princ") is not None for r in records),"elapsed_seconds":time.perf_counter()-start}

    # Independent global exploration consumes a frozen fraction of the level budget.
    global_target=max(len(records),min(budget,int(math.ceil(float(protocol["global_fraction"])*budget))))
    for theta in _sobol(p,max(0,global_target-len(records)),lower,upper,launch_seed):
        add(theta)

    steps=[float(x) for x in protocol["coordinate_steps"]]
    frontier_width=int(protocol["frontier_width"])
    local_sobol=int(protocol["local_sobol_per_frontier"])
    round_index=0
    while len(records)<budget:
        frontier=sorted(records,key=_sort_key)[:frontier_width]
        step=steps[min(round_index,len(steps)-1)]
        before=len(records)
        for base in frontier:
            theta0=np.asarray(base.get("theta_vector",[0.0]*p),dtype=float)
            for axis in range(p):
                for sign in (-1.0,1.0):
                    trial=theta0.copy(); trial[axis]+=sign*step; add(trial)
                    if len(records)>=budget: break
                if len(records)>=budget: break
            if len(records)>=budget: break
        if len(records)<budget:
            for base_index,base in enumerate(frontier[:max(1,min(4,len(frontier))) ]):
                count=min(local_sobol,budget-len(records))
                if count<=0: break
                seed=(int(launch_seed)+0x9E3779B9*(round_index+1)+0x85EBCA6B*base_index)&0xffffffff
                exp=int(math.ceil(math.log2(max(1,count))))
                unit=qmc.Sobol(d=p,scramble=True,seed=seed).random_base2(exp)[:count]
                theta0=np.asarray(base.get("theta_vector",[0.0]*p),dtype=float)
                for delta in (2.0*unit-1.0):
                    add(theta0+step*delta)
                    if len(records)>=budget: break
                if len(records)>=budget: break
        if len(records)==before:
            count=min(64,budget-len(records))
            seed=(int(launch_seed)+0xC2B2AE35*(round_index+1))&0xffffffff
            for theta in _sobol(p,count,lower,upper,seed):
                add(theta)
        round_index+=1
        if round_index>10000:
            raise RuntimeError("reference optimizer v2 exhausted deterministic refinement rounds")
    report(True)
    best=best_f4_record(records)
    return {
        "completed_calls":len(records),
        "best":best,
        "F4_count":sum(int(r.get("stage_index",-1))==5 and r.get("J_princ") is not None for r in records),
        "elapsed_seconds":time.perf_counter()-start,
        "production_incumbent_supplied":production_best is not None,
    }


def adjudicate_independent_launches(rows:list[dict[str,Any]],agreement_max:float)->dict[str,Any]:
    rows=sorted(rows,key=lambda r:int(r["launch_index"]))
    valid=[r for r in rows if not r.get("fatal_exception") and r.get("best") is not None and int(r["best"].get("stage_index",-1))==5 and r["best"].get("J_princ") is not None]
    agreement=None; qualified=False
    if len(valid)==len(rows) and len(valid)>=2:
        js=[float(r["best"]["J_princ"]) for r in valid]
        agreement=(max(js)-min(js))/max(max(js),1e-15)
        qualified=agreement<=float(agreement_max)
    best=min([r["best"] for r in valid],key=_sort_key) if valid else None
    return {
        "qualified":qualified,
        "launch_relative_agreement":agreement,
        "best":best,
        "launches":[{"launch_index":int(r["launch_index"]),"completed_calls":int(r.get("completed_calls",0)),"best":r.get("best"),"F4_count":int(r.get("F4_count",0)),"fatal_exception":r.get("fatal_exception")} for r in rows],
    }
