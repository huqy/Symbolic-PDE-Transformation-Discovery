from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .ast_runtime import FieldJetInterpolator, evaluate_pair_jet, grid_coefficient_derivatives

Array = np.ndarray
STAGE_ORDER = {"NONE": 0, "F0": 1, "F1": 2, "F2": 3, "F3": 4, "F4": 5}


def _p11_imports():
    from p11rawxt_operator import build_basis, evaluate_operator_norms, gauge_second_jet, tensor_trapezoid_weights
    from p11rawxt_validity import (
        _boundary_polygon, _point_winding_number, _polygon_signed_area,
        boundary_self_intersection_count, common_scale_translation_gauge,
        derivative_condition_numbers, mapped_cell_signed_areas,
    )
    return locals()


def _probe_source(grid: Any, per_axis: int, local_coordinates: tuple[float, float]) -> Array:
    cell_i = np.unique(np.linspace(0, len(grid.x) - 2, per_axis, dtype=int))
    cell_j = np.unique(np.linspace(0, len(grid.t) - 2, per_axis, dtype=int))
    ux, ut = local_coordinates
    rows = []
    for i in cell_i:
        for j in cell_j:
            rows.append((float(grid.x[i] + ux * (grid.x[i+1]-grid.x[i])), float(grid.t[j] + ut * (grid.t[j+1]-grid.t[j]))))
    return np.asarray(rows, dtype=np.float64)


def _cross2(a: Array, b: Array) -> Array:
    return a[...,0]*b[...,1]-a[...,1]*b[...,0]


def _triangles(gauged: dict[str, Array], grid: Any) -> tuple[Array,Array]:
    X,T=gauged["X"],gauged["T"]
    sx,st=np.meshgrid(grid.x,grid.t,indexing="ij")
    target=[]; source=[]
    for i in range(len(grid.x)-1):
        for j in range(len(grid.t)-1):
            tv=np.array([[X[i,j],T[i,j]],[X[i+1,j],T[i+1,j]],[X[i+1,j+1],T[i+1,j+1]],[X[i,j+1],T[i,j+1]]],float)
            sv=np.array([[sx[i,j],st[i,j]],[sx[i+1,j],st[i+1,j]],[sx[i+1,j+1],st[i+1,j+1]],[sx[i,j+1],st[i,j+1]]],float)
            target.extend([tv[[0,1,2]],tv[[0,2,3]]]); source.extend([sv[[0,1,2]],sv[[0,2,3]]])
    return np.stack(target),np.stack(source)


def vectorized_inverse_evidence(gauged: dict[str,Array], grid: Any, raw_probe_source: Array, raw_probe_target: Array, gauge_record: dict[str,Any], bary_tol: float, boundary_tol: float) -> dict[str,Any]:
    imp=_p11_imports(); boundary_polygon=imp["_boundary_polygon"]; winding_fn=imp["_point_winding_number"]
    scale=float(gauge_record["common_positive_scale"]); translation=np.asarray(gauge_record["raw_translation"],float)
    targets=(np.asarray(raw_probe_target,float)-translation[None,:])/scale
    target_tri,source_tri=_triangles(gauged,grid)
    a=target_tri[:,0,:]; v0=target_tri[:,1,:]-a; v1=target_tri[:,2,:]-a; denom=_cross2(v0,v1); valid=np.abs(denom)>bary_tol
    poly=boundary_polygon(gauged["X"],gauged["T"])
    unique=[]; winding=[]; errors=[]
    for point,true in zip(targets,raw_probe_source):
        winding.append(winding_fn(point,poly,boundary_tol))
        v2=point[None,:]-a; u=np.full(len(denom),np.nan); v=np.full(len(denom),np.nan)
        u[valid]=_cross2(v2[valid],v1[valid])/denom[valid]; v[valid]=_cross2(v0[valid],v2[valid])/denom[valid]
        inside=valid&(u>=-bary_tol)&(v>=-bary_tol)&(u+v<=1+bary_tol)
        reconstructed=[]
        for idx in np.flatnonzero(inside):
            w=np.array([1-u[idx]-v[idx],u[idx],v[idx]])
            reconstructed.append(w@source_tri[idx])
        clusters=[]
        for cand in reconstructed:
            if not any(np.linalg.norm(cand-old,ord=np.inf)<=10*bary_tol for old in clusters): clusters.append(cand)
        unique.append(len(clusters)); errors.append(float(np.linalg.norm(clusters[0]-true,ord=np.inf)) if len(clusters)==1 else float("inf"))
    return {
        "probe_count":len(raw_probe_source),"unique_preimage_count_min":min(unique) if unique else 0,"unique_preimage_count_max":max(unique) if unique else 0,
        "all_unique":bool(unique and all(x==1 for x in unique)),"winding_number_min":min(winding) if winding else 0,"winding_number_max":max(winding) if winding else 0,
        "degree_one_evidence":bool(winding and all(x==1 for x in winding)),"all_inside_boundary":bool(winding and all(x!=0 for x in winding)),
        "roundtrip_error_max":max(errors) if errors else float("inf"),
    }


def _result(highest: str,rejection:list[str],metrics:dict[str,Any],gauge:dict[str,Any]|None,gauged:dict[str,Array]|None)->dict[str,Any]:
    ladder={name:STAGE_ORDER[highest]>=idx for name,idx in [("F0",1),("F1",2),("F2",3),("F3",4),("F4",5)]}
    return {"highest_feasibility_level":highest,"stage_index":STAGE_ORDER[highest],"feasibility_ladder":ladder,"overall_valid":highest=="F4","rejection_codes":rejection,"metrics":metrics,"gauge_record":gauge or {},"gauged_map":gauged or {}}


def direct_margin_vector(validity:dict[str,Any])->tuple[float,...]:
    stage=validity["highest_feasibility_level"]; m=validity.get("metrics",{})
    if stage=="NONE": return (float(m.get("finite_fraction",0.0)),)
    if stage=="F0": return (float(m.get("J_min",-math.inf)),float(m.get("Tt_min",-math.inf)))
    if stage=="F1": return tuple(float(m.get(k,-math.inf)) for k in ["CTT_min","initial_spacelike_margin_min","left_boundary_timelike_margin_min","right_boundary_timelike_margin_min"])
    if stage=="F2": return (-float(m.get("boundary_self_intersection_count",math.inf)),float(m.get("boundary_signed_area",-math.inf)),float(m.get("mapped_cell_signed_area_min",-math.inf)))
    if stage=="F3": return (-float(m.get("roundtrip_error_max",math.inf)),-float(m.get("map_condition_number_max",math.inf)))
    return ()


def evaluate_validity_variable(raw:dict[str,Array],grid:Any,a:Array,raw_probe_source:Array,raw_probe_target:Array,numerical:dict[str,Any],inverse_roundtrip_tolerance:float)->dict[str,Any]:
    imp=_p11_imports(); gauge_fn=imp["common_scale_translation_gauge"]
    required=["X","T","Xx","Xt","Tx","Tt"]; shape=(len(grid.x),len(grid.t))
    if any(k not in raw or np.asarray(raw[k]).shape!=shape for k in required): return _result("NONE",["F0_SHAPE_MISMATCH"],{},None,None)
    finite=sum(int(np.isfinite(raw[k]).sum()) for k in required); total=sum(np.asarray(raw[k]).size for k in required)
    if finite!=total: return _result("NONE",["F0_NONFINITE_MAP_OR_DERIVATIVE"],{"finite_fraction":finite/max(total,1)},None,None)
    gauged,gauge_record=gauge_fn({k:raw[k] for k in required})
    if gauged is None: return _result("NONE",[str(gauge_record.get("rejection_code","GAUGE_FAILURE"))],{"finite_fraction":1.0},gauge_record,None)
    Xx,Xt,Tx,Tt=(gauged[k] for k in ["Xx","Xt","Tx","Tt"]); J=Xx*Tt-Xt*Tx; eps_o=float(numerical["absolute_orientation_margin"])
    metrics={"finite_fraction":1.0,"J_min":float(np.min(J)),"Tt_min":float(np.min(Tt))}; reject=[]
    if metrics["J_min"]<=eps_o: reject.append("F1_JACOBIAN_ORIENTATION_MARGIN")
    if metrics["Tt_min"]<=eps_o: reject.append("F1_FUTURE_ORIENTATION")
    if reject:return _result("F0",reject,metrics,gauge_record,gauged)
    eps_c=float(numerical["absolute_causal_margin"]); CTT=Tt*Tt-np.asarray(a)*Tx*Tx
    initial=Xx[:,0]**2-Tx[:,0]**2; left=Tt[0,:]**2-Xt[0,:]**2; right=Tt[-1,:]**2-Xt[-1,:]**2
    metrics.update({"CTT_min":float(np.min(CTT)),"initial_spacelike_margin_min":float(np.min(initial)),"left_boundary_timelike_margin_min":float(np.min(left)),"right_boundary_timelike_margin_min":float(np.min(right))})
    if metrics["CTT_min"]<=eps_c:reject.append("F2_CTT_NONPOSITIVE")
    if metrics["initial_spacelike_margin_min"]<=eps_c:reject.append("F2_INITIAL_CURVE_NOT_SPACELIKE")
    if metrics["left_boundary_timelike_margin_min"]<=eps_c:reject.append("F2_LEFT_BOUNDARY_NOT_TIMELIKE")
    if metrics["right_boundary_timelike_margin_min"]<=eps_c:reject.append("F2_RIGHT_BOUNDARY_NOT_TIMELIKE")
    if reject:return _result("F1",reject,metrics,gauge_record,gauged)
    poly=imp["_boundary_polygon"](gauged["X"],gauged["T"]); area=imp["_polygon_signed_area"](poly)
    intersections=imp["boundary_self_intersection_count"](poly,float(numerical["segment_intersection_tolerance"])); cell=imp["mapped_cell_signed_areas"](gauged["X"],gauged["T"])
    floor=float(numerical["cell_area_relative_margin"])*float((grid.x[1]-grid.x[0])*(grid.t[1]-grid.t[0]))
    metrics.update({"boundary_signed_area":float(area),"boundary_self_intersection_count":int(intersections),"mapped_cell_signed_area_min":float(np.min(cell)),"mapped_cell_signed_area_max":float(np.max(cell))})
    if intersections>0:reject.append("F3_BOUNDARY_SELF_INTERSECTION")
    if area<=0:reject.append("F3_BOUNDARY_ORIENTATION")
    if metrics["mapped_cell_signed_area_min"]<=floor:reject.append("F3_CELL_ORIENTATION")
    if reject:return _result("F2",reject,metrics,gauge_record,gauged)
    inv=vectorized_inverse_evidence(gauged,grid,raw_probe_source,raw_probe_target,gauge_record,float(numerical["barycentric_tolerance"]),float(numerical["segment_intersection_tolerance"]))
    cond=imp["derivative_condition_numbers"](Xx,Xt,Tx,Tt); metrics.update(inv); metrics.update({"map_condition_number_max":float(np.max(cond)),"map_condition_number_median":float(np.median(cond))})
    if not inv["all_inside_boundary"]:reject.append("F4_INVERSE_PROBE_OUTSIDE_BOUNDARY")
    if not inv["all_unique"]:reject.append("F4_INVERSE_NOT_UNIQUE")
    if not inv["degree_one_evidence"]:reject.append("F4_DEGREE_NOT_ONE")
    if inv["roundtrip_error_max"]>inverse_roundtrip_tolerance:reject.append("F4_ROUNDTRIP_TOLERANCE")
    if metrics["map_condition_number_max"]>float(numerical["condition_number_hard_ceiling"]):reject.append("F4_CONDITIONING_CEILING")
    if reject:return _result("F3",reject,metrics,gauge_record,gauged)
    return _result("F4",[],metrics,gauge_record,gauged)


def pushforward_variable(gauged:dict[str,Array],a:Array,ax:Array,q:Array,ctt_floor:float)->dict[str,Any]:
    Xx,Xt,Tx,Tt=(np.asarray(gauged[k],float) for k in ["Xx","Xt","Tx","Tt"])
    a=np.asarray(a,float); ax=np.asarray(ax,float); q=np.asarray(q,float)
    CTT=Tt*Tt-a*Tx*Tx; CXT=2*(Xt*Tt-a*Xx*Tx); CXX=Xt*Xt-a*Xx*Xx
    LT=np.asarray(gauged["Ttt"])-a*np.asarray(gauged["Txx"])-ax*Tx; LX=np.asarray(gauged["Xtt"])-a*np.asarray(gauged["Xxx"])-ax*Xx
    if np.any(~np.isfinite(CTT)) or float(np.min(CTT))<=ctt_floor: raise ValueError("C_TT floor")
    m=CXT/CTT; r=(CTT+CXX)/CTT; J=Xx*Tt-Xt*Tx
    red={"w_TT":np.ones_like(CTT),"w_XT":np.zeros_like(CTT),"w_XX":-np.ones_like(CTT),"w_T":LT/CTT,"w_X":LX/CTT,"w":q/CTT,"forcing_factor":1/CTT}
    return {"a":a,"a_x":ax,"q":q,"J_Phi":J,"C_TT":CTT,"C_XT":CXT,"C_XX":CXX,"L_T":LT,"L_X":LX,"m":m,"r":r,"reduced":red}


def operator_on_variable(gauged:dict[str,Array],grid:Any,field_arrays:dict[str,Array],space:dict[str,Any],numerical:dict[str,Any])->tuple[dict[str,Any],dict[str,Any]]:
    imp=_p11_imports(); coeff=pushforward_variable(gauged,field_arrays["a_d0_0"],field_arrays["a_d1_0"],field_arrays["q"],float(numerical["C_TT_absolute_floor"]))
    if float(np.min(coeff["J_Phi"]))<=0: raise ValueError("positive Jacobian required")
    basis=imp["build_basis"](space,gauged["X"],gauged["T"]); sw=imp["tensor_trapezoid_weights"](grid.x,grid.t)*coeff["J_Phi"]
    op=imp["evaluate_operator_norms"](basis,coeff["m"],coeff["r"],sw,mass_relative_floor=float(numerical["mass_matrix_relative_eigen_floor"]))
    return coeff,op.as_dict()


@dataclass
class FieldView:
    field_id: str
    role: str
    grid_n: int
    arrays: dict[str,Array]
    probe_interpolator: FieldJetInterpolator


def load_npz(path:Path)->dict[str,Array]:
    with np.load(path,allow_pickle=False) as z:return {k:np.asarray(z[k]) for k in z.files}


def load_field_views(k1_run:Path,role:str,grid_n:int,probe_grid:int=65)->list[FieldView]:
    manifest=json.load(open(k1_run/"open_search_object_manifest.json")); objs=manifest["objects"]
    ids=sorted({o["field_id"] for o in objs if o["role"]==role})
    views=[]
    for fid in ids:
        main=next(o for o in objs if o["field_id"]==fid and int(o["grid"])==grid_n); probe=next(o for o in objs if o["field_id"]==fid and int(o["grid"])==probe_grid)
        arrays=load_npz(k1_run/main["path"]); parr=load_npz(k1_run/probe["path"])
        views.append(FieldView(fid,role,grid_n,arrays,FieldJetInterpolator(parr,4)))
    return views


def _grid_from_arrays(arrays:dict[str,Array])->Any:
    from p11rawxt_validity import GridSpec
    return GridSpec(np.asarray(arrays["x"],float),np.asarray(arrays["t"],float))


def evaluate_pair_on_field(pair:dict[str,Any],theta:list[float],field:FieldView,validity_num:dict[str,Any],inverse_tol:float,space:dict[str,Any],operator_num:dict[str,Any])->dict[str,Any]:
    start=time.perf_counter(); arrays=field.arrays; grid=_grid_from_arrays(arrays); xm,tm=grid.mesh
    try:
        raw=evaluate_pair_jet(pair["raw_X_AST"],pair["raw_T_AST"],theta,xm,tm,grid_coefficient_derivatives(arrays,4),4)
        source=_probe_source(grid,int(validity_num["inverse_probe_grid_per_axis"]),tuple(validity_num["inverse_probe_local_coordinates"]))
        pd=field.probe_interpolator.derivatives(source[:,0],source[:,1]); probe_raw=evaluate_pair_jet(pair["raw_X_AST"],pair["raw_T_AST"],theta,source[:,0],source[:,1],pd,4)
        target=np.column_stack([probe_raw["X"],probe_raw["T"]]); validity=evaluate_validity_variable(raw,grid,arrays["a_d0_0"],source,target,validity_num,inverse_tol)
    except Exception as exc:
        return {"field_id":field.field_id,"theta_vector":[float(v) for v in theta],"highest_feasibility_level":"NONE","stage_index":0,"F0_F4_records":{"exception_type":type(exc).__name__,"exception":str(exc)},"direct_margin_vector":[0.0],"J_princ":None,"J_XT":None,"J_XX":None,"elapsed_seconds":time.perf_counter()-start}
    rec={"field_id":field.field_id,"theta_vector":[float(v) for v in theta],"highest_feasibility_level":validity["highest_feasibility_level"],"stage_index":validity["stage_index"],"F0_F4_records":{k:v for k,v in validity.items() if k!="gauged_map"},"direct_margin_vector":list(direct_margin_vector(validity)),"J_princ":None,"J_XT":None,"J_XX":None}
    if validity["overall_valid"]:
        try:
            gauged,grec=_p11_imports()["gauge_second_jet"](raw)
            if gauged is None: raise ValueError(f"gauge second jet failed {grec}")
            coeff,op=operator_on_variable(gauged,grid,arrays,space,operator_num); rec.update(op)
            rec["lower_order_diagnostics"]={"max_abs_L_T_over_C_TT":float(np.max(np.abs(coeff["L_T"]/coeff["C_TT"]))),"max_abs_L_X_over_C_TT":float(np.max(np.abs(coeff["L_X"]/coeff["C_TT"]))),"min_C_TT":float(np.min(coeff["C_TT"])),"max_abs_q_over_C_TT":float(np.max(np.abs(coeff["q"]/coeff["C_TT"]))),"max_abs_inv_C_TT":float(np.max(np.abs(1/coeff["C_TT"])))}
        except Exception as exc:
            rec["highest_feasibility_level"]="F3"; rec["stage_index"]=4; rec["F0_F4_records"]["operator_exception_type"]=type(exc).__name__; rec["F0_F4_records"]["operator_exception"]=str(exc)
    rec["elapsed_seconds"]=time.perf_counter()-start; return rec


def _agg_margin(records:list[dict[str,Any]],stage:int)->list[float]:
    rows=[r.get("direct_margin_vector",[]) for r in records if int(r["stage_index"])==stage]
    if not rows:return [0.0]
    n=min(len(x) for x in rows); return [float(min(x[i] for x in rows)) for i in range(n)]


def evaluate_family(pair:dict[str,Any],theta:list[float],fields:list[FieldView],validity_num:dict[str,Any],inverse_tol:float,space:dict[str,Any],operator_num:dict[str,Any])->dict[str,Any]:
    records=[evaluate_pair_on_field(pair,theta,f,validity_num,inverse_tol,space,operator_num) for f in fields]
    stage=min(int(r["stage_index"]) for r in records); stage_name={v:k for k,v in STAGE_ORDER.items()}[stage]
    out={"theta_vector":[float(v) for v in theta],"highest_feasibility_level":stage_name,"stage_index":stage,"direct_margin_vector":_agg_margin(records,stage),"J_princ":None,"J_XT":None,"J_XX":None,"per_field":records,"elapsed_seconds":float(sum(r.get("elapsed_seconds",0.0) for r in records))}
    if stage==5 and all(r.get("J_princ") is not None for r in records):
        for key,outkey in [("J_princ","J_princ"),("J_XT","J_XT"),("J_XX","J_XX")]:
            vals=np.asarray([float(r[key]) for r in records]); out[outkey]=float(np.sqrt(np.mean(vals*vals)))
        out["J_family"]=out["J_princ"]; out["J_max"]=float(max(float(r["J_princ"]) for r in records))
    return out
