from __future__ import annotations

import copy
import math
import time
from typing import Any, Callable

import numpy as np
from scipy.stats import qmc


def build_identity_pair(caps: dict[str,int]) -> dict[str,Any]:
    from p11rawxt_ast import Var
    from p11rawxt_s1.k1_representation import canonicalize_pair
    return canonicalize_pair(Var("x"),Var("t"),caps)


def build_null_capacity_pair(caps:dict[str,int])->dict[str,Any]:
    from p11rawxt_ast import Var,Theta,Op
    from p11rawxt_s1.k1_representation import canonicalize_pair
    x,t=Var("x"),Var("t")
    # Calibration-only coordinate polynomial residual chart. No coefficient access.
    X=Op("Add",x,
         Op("Mul",Theta("theta_1"),t),
         Op("Mul",Theta("theta_2"),x,t),
         Op("Mul",Theta("theta_3"),Op("PowInt",t,exponent=2)),
         Op("Mul",Theta("theta_4"),Op("PowInt",x,exponent=2)),
         Op("Mul",Theta("theta_5"),x,Op("PowInt",t,exponent=2)))
    T=Op("Add",t,
         Op("Mul",Theta("theta_6"),x),
         Op("Mul",Theta("theta_7"),x,t),
         Op("Mul",Theta("theta_8"),Op("PowInt",x,exponent=2)),
         Op("Mul",Theta("theta_9"),Op("PowInt",t,exponent=2)),
         Op("Mul",Theta("theta_10"),Op("PowInt",x,exponent=2),t))
    return canonicalize_pair(X,T,caps)


def build_full_capacity_pair(caps:dict[str,int])->dict[str,Any]:
    """Claim-II-informed m=2 finite-local calibration instrument only.

    This exact skeleton is permanently forbidden from formal search seeds/parents/subtree libraries.
    It is written only into K2 CALIBRATION evidence.
    """
    from p11rawxt_ast import Var,Theta,Op
    from p11rawxt_s1.k1_representation import canonicalize_pair
    x,t,a=Var("x"),Var("t"),Var("a")
    loga=Op("Log",a); bx=Op("Dx",copy.deepcopy(loga)); bt=Op("Dt",copy.deepcopy(loga))
    bxt=Op("Dt",Op("Dx",copy.deepcopy(loga))); bxx=Op("Dx",Op("Dx",copy.deepcopy(loga))); btt=Op("Dt",Op("Dt",copy.deepcopy(loga)))
    t2=Op("PowInt",t,exponent=2); t3=Op("PowInt",t,exponent=3)
    X=Op("Add",x,Op("Mul",Theta("theta_1"),copy.deepcopy(t2),bx),Op("Mul",Theta("theta_2"),copy.deepcopy(t3),bxt))
    T=Op("Add",t,Op("Mul",Theta("theta_3"),t,loga),Op("Mul",Theta("theta_4"),copy.deepcopy(t2),bt),Op("Mul",Theta("theta_5"),copy.deepcopy(t3),btt),Op("Mul",Theta("theta_6"),copy.deepcopy(t3),bxx))
    return canonicalize_pair(X,T,caps)


def parameter_names_for_pair(pair:dict[str,Any])->list[str]:
    from p11rawxt_ast import parameter_names
    names=parameter_names(pair["raw_X_AST"])|parameter_names(pair["raw_T_AST"])
    return sorted(names,key=lambda n:int(n.split("_")[1]))


def production_fit(pair:dict[str,Any],evaluate:Callable[[dict[str,Any],list[float]],dict[str,Any]],fitter_protocol:dict[str,Any])->dict[str,Any]:
    from p11rawxt_s1.k2_fitter import fit_skeleton
    return fit_skeleton(pair,evaluate,fitter_protocol)


def best_f4(fit:dict[str,Any])->dict[str,Any]|None:
    rows=[r for r in fit.get("all_F4_branches",[]) if r.get("J_princ") is not None]
    return min(rows,key=lambda r:(float(r["J_princ"]),float(r["J_XT"]),float(r["J_XX"]),r.get("candidate_branch_hash",""))) if rows else None


def _sobol(p:int,count:int,lower:float,upper:float,seed:int)->list[list[float]]:
    if p==0:return [[]]
    exp=int(math.ceil(math.log2(max(count,1)))); sampler=qmc.Sobol(d=p,scramble=True,seed=seed); u=sampler.random_base2(exp)[:count]
    return (lower+(upper-lower)*u).tolist()


def _record_better(a:dict[str,Any]|None,b:dict[str,Any]|None)->dict[str,Any]|None:
    if a is None:return b
    if b is None:return a
    sa,sb=int(a["stage_index"]),int(b["stage_index"])
    if sa!=sb:return a if sa>sb else b
    if sa==5 and a.get("J_princ") is not None and b.get("J_princ") is not None:
        ka=(float(a["J_princ"]),float(a["J_XT"]),float(a["J_XX"])); kb=(float(b["J_princ"]),float(b["J_XT"]),float(b["J_XX"]))
        return a if ka<=kb else b
    ma=tuple(map(float,a.get("direct_margin_vector",[]))); mb=tuple(map(float,b.get("direct_margin_vector",[])))
    # Direct lexicographic feasibility facts are used only as deterministic optimizer mechanics,
    # never as a scientific score or membership rule.
    return a if ma>=mb else b


def reference_launch(
    pair:dict[str,Any],
    evaluate:Callable[[dict[str,Any],list[float]],dict[str,Any]],
    protocol:dict[str,Any],
    seed:int,
    *,
    progress_label:str|None=None,
    progress_every_calls:int=50,
    progress_every_seconds:float=60.0,
)->dict[str,Any]:
    """Run one deterministic calibration-reference optimizer launch.

    Progress reporting is observational only: it does not alter the proposal sequence,
    seed, objective, call ceiling, or qualification semantics.
    """
    names=parameter_names_for_pair(pair); p=len(names); lower,upper=map(float,protocol["parameter_bounds"])
    max_calls=int(protocol["calls_by_parameter_count"][str(p)])
    initial=min(max_calls,int(protocol["sobol_base"])+int(protocol["sobol_per_parameter"])*p)
    points=_sobol(p,initial,lower,upper,seed); records=[]; seen=set()
    start=time.perf_counter(); last=start; last_calls=0

    def report(force:bool=False)->None:
        nonlocal last,last_calls
        if progress_label is None:return
        now=time.perf_counter(); calls=len(records)
        if not force and calls-last_calls<int(progress_every_calls) and now-last<float(progress_every_seconds):return
        elapsed=now-start; rate=calls/max(elapsed,1e-12); eta=(max_calls-calls)/rate if rate>0 else math.inf
        print(f"[K2 capacity {progress_label}] calls={calls}/{max_calls} elapsed={elapsed:.1f}s rate={rate:.3g}/s ETA={eta/60:.1f}m",flush=True)
        last=now; last_calls=calls

    def ev(theta:list[float]):
        if len(records)>=max_calls:return None
        th=[min(upper,max(lower,float(v))) for v in theta]; key=tuple(float(v).hex() for v in th)
        if key in seen:return None
        seen.add(key); r=evaluate(pair,th); records.append(r); report(); return r
    best=None
    for th in points: best=_record_better(best,ev(th))
    if p==0:
        report(True)
        return {"best":best,"completed_calls":len(records),"records":records}
    steps=[float(x) for x in protocol["coordinate_steps"]]
    for step in steps:
        if len(records)>=max_calls:break
        bases=sorted([r for r in records if int(r["stage_index"])==5 and r.get("J_princ") is not None],key=lambda r:float(r["J_princ"]))[:int(protocol["frontier_width"])]
        if not bases and best is not None:bases=[best]
        improved=False
        for base in bases:
            theta0=list(map(float,base["theta_vector"]))
            for axis in range(p):
                for sign in (-1.0,1.0):
                    if len(records)>=max_calls:break
                    th=theta0.copy(); th[axis]+=sign*step; r=ev(th); old=best; best=_record_better(best,r); improved=improved or (best is not old)
        if not improved and len(records)<max_calls:
            extra=_sobol(p,min(int(protocol["sobol_refresh"]),max_calls-len(records)),lower,upper,(seed+0x9E3779B9+len(records))&0xffffffff)
            for th in extra: best=_record_better(best,ev(th))
    report(True)
    return {"best":best,"completed_calls":len(records),"records":records}


def reference_fit(pair:dict[str,Any],evaluate:Callable[[dict[str,Any],list[float]],dict[str,Any]],protocol:dict[str,Any],seed:int)->dict[str,Any]:
    launches=[reference_launch(pair,evaluate,protocol,(seed+i*0x85EBCA6B)&0xffffffff) for i in range(int(protocol["independent_launches"]))]
    bests=[x["best"] for x in launches if x.get("best") and int(x["best"]["stage_index"])==5 and x["best"].get("J_princ") is not None]
    qualified=False; agreement=None
    if len(bests)==len(launches) and len(bests)>=2:
        js=[float(x["J_princ"]) for x in bests]; agreement=max(js)-min(js); agreement/=max(max(js),1e-15)
        qualified=agreement<=float(protocol["launch_relative_agreement_max"])
    best=min(bests,key=lambda r:float(r["J_princ"])) if bests else None
    return {"qualified":qualified,"launch_relative_agreement":agreement,"best":best,"launches":[{"completed_calls":x["completed_calls"],"best":x["best"]} for x in launches]}


