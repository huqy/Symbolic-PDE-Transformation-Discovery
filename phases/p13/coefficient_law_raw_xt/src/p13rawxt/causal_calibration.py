from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any

import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spla

from .ast_runtime import evaluate_pair_jet
from .family_evaluator import pushforward_variable


def _spatial_matrices(n: int, dx: float) -> tuple[sp.csc_matrix, sp.csc_matrix]:
    m = n - 2
    e = np.ones(m, dtype=float)
    d1 = sp.diags([-e[:-1], e[:-1]], [-1, 1], shape=(m, m), format="csc") / (2.0 * dx)
    d2 = sp.diags([e[:-1], -2.0 * e, e[:-1]], [-1, 0, 1], shape=(m, m), format="csc") / (dx * dx)
    return d1, d2


def source_operator_coefficients(gauged: dict[str, np.ndarray], coeff: dict[str, Any]) -> dict[str, np.ndarray]:
    Xx = np.asarray(gauged["Xx"], float); Xt = np.asarray(gauged["Xt"], float)
    Tx = np.asarray(gauged["Tx"], float); Tt = np.asarray(gauged["Tt"], float)
    J = Xx * Tt - Xt * Tx
    ia = Tt / J; ib = -Tx / J; ic = -Xt / J; id_ = Xx / J
    A_xx = ic * ic - ia * ia
    A_xt = 2.0 * (ic * id_ - ia * ib)
    A_tt = id_ * id_ - ib * ib
    curv_X = A_xx * gauged["Xxx"] + A_xt * gauged["Xxt"] + A_tt * gauged["Xtt"]
    curv_T = A_xx * gauged["Txx"] + A_xt * gauged["Txt"] + A_tt * gauged["Ttt"]
    red = coeff["reduced"]
    low_X = red["w_X"] - curv_X
    low_T = red["w_T"] - curv_T
    b_x = low_X * ia + low_T * ic
    b_t = low_X * ib + low_T * id_
    return {
        "A_xx": A_xx, "A_xt": A_xt, "A_tt": A_tt,
        "b_x": b_x, "b_t": b_t, "c": np.asarray(red["w"], float),
    }


def inverse_transform_second_derivatives(source: dict[str, np.ndarray], map_jet: dict[str, np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    Xx=np.asarray(map_jet["Xx"]); Xt=np.asarray(map_jet["Xt"]); Tx=np.asarray(map_jet["Tx"]); Tt=np.asarray(map_jet["Tt"])
    J=Xx*Tt-Xt*Tx
    if np.min(J) <= 0.0: raise ValueError("positive Jacobian required")
    ux=source["ux"]; ut=source["ut"]
    ia=Tt/J; ib=-Tx/J; ic=-Xt/J; id_=Xx/J
    wX=ia*ux+ib*ut; wT=ic*ux+id_*ut
    r0=source["uxx"]-wX*map_jet["Xxx"]-wT*map_jet["Txx"]
    r1=source["uxt"]-wX*map_jet["Xxt"]-wT*map_jet["Txt"]
    r2=source["utt"]-wX*map_jet["Xtt"]-wT*map_jet["Ttt"]
    wXX=ia*ia*r0 + 2.0*ia*ib*r1 + ib*ib*r2
    wXT=ia*ic*r0 + (ia*id_+ib*ic)*r1 + ib*id_*r2
    return wXX,wXT


def apply_gauge(raw: dict[str, np.ndarray], gauge_record: dict[str, Any]) -> dict[str, np.ndarray]:
    scale=float(gauge_record["common_positive_scale"]); xref,tref=map(float,gauge_record["raw_translation"])
    out={}
    for k,v in raw.items():
        arr=np.asarray(v,float)
        if k=="X": out[k]=(arr-xref)/scale
        elif k=="T": out[k]=(arr-tref)/scale
        else: out[k]=arr/scale
    return out


def causal_solve(x: np.ndarray, t: np.ndarray, sc_mid: dict[str,np.ndarray], forcing_mid: np.ndarray, cfg: dict[str,Any]) -> tuple[np.ndarray,np.ndarray,dict[str,Any]]:
    n=len(x); m=n-2; dx=float(x[1]-x[0]); dt=float(t[1]-t[0])
    d1,d2=_spatial_matrices(n,dx); I=sp.eye(m,format="csc"); Z=sp.csc_matrix((m,m)); I2=sp.eye(2*m,format="csc")
    E=np.zeros((n,n),float); P=np.zeros((n,n),float)
    max_res=0.0; factor_s=0.0; solve_s=0.0; min_att=math.inf
    floor=float(cfg["source_A_tt_positive_floor"]); tol=float(cfg["time_step_linear_residual_relative_tolerance"])
    for j in range(n-1):
        sl=(slice(1,-1),j)
        Att=np.asarray(sc_mid["A_tt"][sl],float); min_att=min(min_att,float(np.min(Att)))
        if np.any(~np.isfinite(Att)) or float(np.min(Att))<=floor: raise FloatingPointError(f"source A_tt floor fail {float(np.min(Att))}")
        inv=1.0/Att
        Axx=np.asarray(sc_mid["A_xx"][sl],float); Axt=np.asarray(sc_mid["A_xt"][sl],float)
        bx=np.asarray(sc_mid["b_x"][sl],float); bt=np.asarray(sc_mid["b_t"][sl],float); c=np.asarray(sc_mid["c"][sl],float)
        Le=sp.diags(-Axx*inv)@d2 + sp.diags(-bx*inv)@d1 + sp.diags(-c*inv)
        Lp=sp.diags(-Axt*inv)@d1 + sp.diags(-bt*inv)
        M=sp.bmat([[Z,I],[Le,Lp]],format="csc")
        Astep=I2-0.5*dt*M; Bstep=I2+0.5*dt*M
        Y=np.concatenate([E[1:-1,j],P[1:-1,j]])
        F=np.concatenate([np.zeros(m),np.asarray(forcing_mid[1:-1,j],float)*inv])
        rhs=np.asarray(Bstep@Y+dt*F,float)
        q0=time.perf_counter(); lu=spla.splu(Astep,permc_spec="COLAMD"); factor_s+=time.perf_counter()-q0
        q0=time.perf_counter(); Yn=np.asarray(lu.solve(rhs),float); solve_s+=time.perf_counter()-q0
        rel=float(np.linalg.norm(Astep@Yn-rhs)/max(np.linalg.norm(rhs),1e-14)); max_res=max(max_res,rel)
        if not np.isfinite(rel) or rel>tol: raise FloatingPointError(f"time-step residual {rel}")
        E[1:-1,j+1]=Yn[:m]; P[1:-1,j+1]=Yn[m:]
    return E,P,{"max_step_linear_residual_relative":max_res,"minimum_source_A_tt":float(min_att),"factor_seconds":factor_s,"solve_seconds":solve_s}


def _spatial_global(x: np.ndarray, mode: int) -> tuple[np.ndarray,np.ndarray,np.ndarray]:
    k=mode*math.pi
    return np.sin(k*x), k*np.cos(k*x), -(k*k)*np.sin(k*x)


def _spatial_local(x: np.ndarray, c: float, s: float) -> tuple[np.ndarray,np.ndarray,np.ndarray]:
    # sin(pi x) times Gaussian keeps homogeneous Dirichlet values exactly.
    g=np.exp(-0.5*((x-c)/s)**2); gp=-(x-c)/(s*s)*g; gpp=(((x-c)**2)/(s**4)-1/(s*s))*g
    h=np.sin(math.pi*x); hp=math.pi*np.cos(math.pi*x); hpp=-(math.pi**2)*h
    return h*g, hp*g+h*gp, hpp*g+2*hp*gp+h*gpp


def _time_harmonic(t: np.ndarray, omega: float, phase: float=0.0) -> tuple[np.ndarray,np.ndarray,np.ndarray]:
    z=omega*t+phase
    return np.cos(z), -omega*np.sin(z), -(omega**2)*np.cos(z)


def _time_local(t: np.ndarray,c:float,s:float) -> tuple[np.ndarray,np.ndarray,np.ndarray]:
    g=np.exp(-0.5*((t-c)/s)**2); gp=-(t-c)/(s*s)*g; gpp=(((t-c)**2)/(s**4)-1/(s*s))*g
    return g,gp,gpp


def manufactured_case(case_index:int,xm:np.ndarray,tm:np.ndarray)->dict[str,np.ndarray]:
    if case_index==0: sx,sxx1,sxx2=_spatial_global(xm,1); ht,ht1,ht2=_time_harmonic(tm,1.35*math.pi,0.2)
    elif case_index==1: sx,sxx1,sxx2=_spatial_global(xm,2); ht,ht1,ht2=_time_harmonic(tm,0.85*math.pi,0.65)
    elif case_index==2: sx,sxx1,sxx2=_spatial_local(xm,0.34,0.16); ht,ht1,ht2=_time_local(tm,0.58,0.20)
    elif case_index==3: sx,sxx1,sxx2=_spatial_local(xm,0.69,0.13); ht,ht1,ht2=_time_harmonic(tm,1.75*math.pi,0.4)
    else: raise ValueError(case_index)
    return {"u":sx*ht,"ux":sxx1*ht,"ut":sx*ht1,"uxx":sxx2*ht,"uxt":sxx1*ht1,"utt":sx*ht2}


def _energy_norm(x:np.ndarray,t:np.ndarray,u:np.ndarray,ux:np.ndarray,ut:np.ndarray,a:np.ndarray,q:np.ndarray)->float:
    # Tensor trapezoid; normalization cancels in relative error.
    wx=np.ones(len(x)); wt=np.ones(len(t)); wx[[0,-1]]=0.5; wt[[0,-1]]=0.5
    W=np.outer(wx,wt)*float(x[1]-x[0])*float(t[1]-t[0])
    val=np.sum(W*(ut*ut+a*ux*ux+q*u*u))
    return math.sqrt(max(float(val),0.0))


def _numerical_ex(E:np.ndarray,x:np.ndarray)->np.ndarray:
    dx=float(x[1]-x[0]); out=np.empty_like(E)
    out[1:-1]=(E[2:]-E[:-2])/(2*dx); out[0]=(E[1]-E[0])/dx; out[-1]=(E[-1]-E[-2])/dx
    return out


def load_calibration_generator(k1_run:Path,field_id:str)->dict[str,Any]:
    for line in open(k1_run/"open_generator_provenance.jsonl"):
        if not line.strip(): continue
        row=json.loads(line)
        if row.get("generator",{}).get("field_id")==field_id:
            return row["generator"]
    raise KeyError(field_id)


def calibration_pair_score(project_root:Path,k1_run:Path,pair:dict[str,Any],theta:list[float],field_id:str,case_index:int,n:int,cfg:dict[str,Any])->dict[str,Any]:
    # Generator provenance is used only here for exact CALIBRATION coefficient truth at G129/midpoints.
    from p13rawxt.coefficients import exponential_coefficient_derivatives
    from p11rawxt_operator import gauge_second_jet
    generator=load_calibration_generator(k1_run,field_id)
    x=np.linspace(0,1,n); t=np.linspace(0,1,n); tmids=0.5*(t[:-1]+t[1:])
    xm,tt=np.meshgrid(x,t,indexing="ij"); xmid,tmid=np.meshgrid(x,tmids,indexing="ij")
    dgrid=exponential_coefficient_derivatives(generator,xm,tt,4)
    dmid=exponential_coefficient_derivatives(generator,xmid,tmid,4)
    raw0=evaluate_pair_jet(pair["raw_X_AST"],pair["raw_T_AST"],theta,np.asarray([[0.0]]),np.asarray([[0.0]]),
                           {idx:np.asarray([[dgrid[idx][0,0]]]) for idx in dgrid},4)
    _,grec=gauge_second_jet(raw0)
    if grec.get("status")!="PASS": raise ValueError(f"gauge reference failed {grec}")
    rawmid=evaluate_pair_jet(pair["raw_X_AST"],pair["raw_T_AST"],theta,xmid,tmid,dmid,4); gmid=apply_gauge(rawmid,grec)
    amid=dmid[(0,0)]; axmid=dmid[(1,0)]; qmid=np.ones_like(amid)
    coeffmid=pushforward_variable(gmid,amid,axmid,qmid,1e-12); sc=source_operator_coefficients(gmid,coeffmid)
    source=manufactured_case(case_index,xmid,tmid); wxx,wxt=inverse_transform_second_derivatives(source,gmid)
    forcing=coeffmid["m"]*wxt+coeffmid["r"]*wxx
    E,P,cert=causal_solve(x,t,sc,forcing,cfg)
    Ex=_numerical_ex(E,x)
    # Fine source full-state energy uses exact manufactured solution on full time nodes.
    source_full=manufactured_case(case_index,xm,tt); agrid=dgrid[(0,0)]; qgrid=np.ones_like(agrid)
    err=_energy_norm(x,t,E,Ex,P,agrid,qgrid); full=_energy_norm(x,t,source_full["u"],source_full["ux"],source_full["ut"],agrid,qgrid)
    return {"field_id":field_id,"case_index":case_index,"grid":n,"relative_energy_error":float(err/max(full,1e-14)),"linear_certificate":cert}


def bounded_interval(coarse:float,fine:float)->dict[str,float]:
    uncertainty=abs(float(fine)-float(coarse))
    return {"nominal":float(fine),"uncertainty":uncertainty,"lower":max(0.0,float(fine)-uncertainty),"upper":float(fine)+uncertainty}
