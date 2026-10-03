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
from concurrent.futures import ProcessPoolExecutor, as_completed
from multiprocessing import get_context
from pathlib import Path
from typing import Any

import numpy as np

from .ast_runtime import evaluate_pair_jet
from .calibration_instruments import build_identity_pair, build_null_capacity_pair
from .causal_calibration import apply_gauge, causal_solve, inverse_transform_second_derivatives, source_operator_coefficients
from .coefficients import canonical_json_bytes, exponential_coefficient_derivatives
from .family_evaluator import pushforward_variable
from .s2_k1_operator_transfer import _candidate_from_locator
from .s2_k3_response_protocol_lock import response_interval, classify_branch
from .s2_k4_response_reference_controls import (
    _a_at, _d1, _gauge_record, _midpoint_source_derivatives, _restrict_reference,
    _simpson_weights, load_coefficient_generators, physical_energy, run_control_tasks,
    _control_tasks, _control_decisions, sha256_file,
)

_WORKER: dict[str, Any] = {}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    out=[]
    with path.open("r",encoding="utf-8") as f:
        for line in f:
            if line.strip(): out.append(json.loads(line))
    return out


def count_jsonl(path: Path) -> int:
    with path.open("rb") as f: return sum(1 for x in f if x.strip())


def resolve_marker(root: Path, rel: str) -> Path:
    p=root/rel
    if not p.is_file(): raise FileNotFoundError(p)
    q=root/p.read_text(encoding="utf-8").strip()
    if not q.exists(): raise FileNotFoundError(q)
    return q


def line_at(path: Path, offset: int) -> dict[str, Any]:
    with path.open("rb") as f:
        f.seek(int(offset)); line=f.readline()
    if not line: raise RuntimeError(f"missing line at {offset}: {path}")
    return json.loads(line)


def _verify_entry(root: Path, cfg: dict[str,Any]) -> tuple[Path,Path,dict[str,Any],dict[str,Any],dict[str,Any]]:
    checks={}
    s2run=resolve_marker(root,cfg["k4_run_marker"])
    k4=s2run/"K4_development_response_reference_control_first"
    checks["K4_status"]=(s2run/"K4_OVERALL_STATUS.txt").is_file() and (s2run/"K4_OVERALL_STATUS.txt").read_text().strip()==cfg["expected_k4"]["overall_status"]
    checks["K4_next"]=(s2run/"K4_NEXT_ACTION.txt").is_file() and (s2run/"K4_NEXT_ACTION.txt").read_text().strip()==cfg["expected_k4"]["next_action"]
    summary=load_json(k4/"K4_SCIENTIFIC_SUMMARY.json")
    checks["K4_outcome"]=summary.get("scientific_outcome")==cfg["expected_k4"]["scientific_outcome"]
    checks["K4_semantic"]=summary.get("semantic_output_digest")==cfg["expected_k4"]["semantic_output_digest"]
    checks["K4R_semantic"]=summary.get("K4R_semantic_output_digest")==cfg["expected_k4"]["k4r_semantic_output_digest"]
    checks["references_32"]=summary.get("reference_certified_count")==32 and summary.get("reference_unresolved_count")==0
    checks["controls_fail"]=summary.get("identity_decision")=="RESPONSE_FAIL" and summary.get("frozen_null_decision")=="RESPONSE_FAIL"
    checks["response_opened"]=summary.get("DEVELOPMENT_response_opened") is True
    checks["sealed_unopened"]=summary.get("SEALED_opened") is False
    checks["candidate_responses_not_run_K4"]=summary.get("candidate_responses_executed") is False
    guard=load_json(k4/"K4_DATA_BOUNDARY_GUARD.json")
    checks["guard"]=(guard.get("DEVELOPMENT_RESPONSE")=="OPENED_K4" and guard.get("SEALED_FINAL_COEF")=="SEALED_COMMITTED_UNOPENED" and guard.get("SEALED_FINAL_RESPONSE")=="SEALED_COMMITTED_UNOPENED" and guard.get("candidate_response_solved") is False and guard.get("top_k_or_proxy_filter") is False)
    refcert=load_json(k4/"K4_REFERENCE_CERTIFICATION.json")
    checks["reference_certification"]=refcert.get("status")=="PASS" and refcert.get("certified_count")==32 and refcert.get("unresolved_count")==0
    controls=load_json(k4/"K4_CONTROL_FIRST_RESULTS.json")
    checks["control_pair"]=controls.get("coarse_grid")==129 and controls.get("fine_grid")==257 and controls.get("controls",{}).get("identity",{}).get("decision")=="RESPONSE_FAIL"
    eligible=root/cfg["response_eligible_membership"]["path"]
    checks["eligible_sha"]=eligible.is_file() and sha256_file(eligible)==cfg["response_eligible_membership"]["sha256"]
    checks["eligible_count"]=eligible.is_file() and count_jsonl(eligible)==1955
    clear=root/cfg["s1_clear_membership"]["path"]
    checks["S1_clear_sha"]=clear.is_file() and sha256_file(clear)==cfg["s1_clear_membership"]["sha256"]
    checks["S1_clear_count"]=clear.is_file() and count_jsonl(clear)==2307
    pf1=root/cfg["pf1_active_input_manifest"]["path"]
    checks["PF1_sha"]=pf1.is_file() and sha256_file(pf1)==cfg["pf1_active_input_manifest"]["sha256"]
    core=root/cfg["frozen_causal_core"]["path"]
    checks["causal_core_sha"]=core.is_file() and sha256_file(core)==cfg["frozen_causal_core"]["sha256"]
    response=load_json(k4/"K4_OPENED_DEVELOPMENT_RESPONSE.json")
    checks["response_rows_32"]=len(response.get("rows",[]))==32 and "master_seed_hex" not in response
    status="PASS" if all(bool(v) for v in checks.values()) else "FAIL"
    return s2run,k4,refcert,response,{"status":status,"checks":checks,"K4_semantic_output_digest":summary.get("semantic_output_digest"),"K4R_semantic_output_digest":summary.get("K4R_semantic_output_digest")}


def _load_candidates(root: Path, cfg: dict[str,Any]) -> list[dict[str,Any]]:
    clear=read_jsonl(root/cfg["s1_clear_membership"]["path"])
    eligible=read_jsonl(root/cfg["response_eligible_membership"]["path"])
    if len(clear)!=2307 or len(eligible)!=1955: raise RuntimeError("membership count mismatch")
    out=[]
    seen=set()
    for erow in eligible:
        idx=int(erow["membership_index"])
        if idx<0 or idx>=len(clear): raise RuntimeError("eligible membership index out of range")
        c=_candidate_from_locator(root,clear[idx],idx)
        if c["scientific_branch_id"]!=erow["scientific_branch_id"]: raise RuntimeError("eligible/S1 ID mismatch")
        if c["scientific_branch_id"] in seen: raise RuntimeError("duplicate response eligible branch")
        seen.add(c["scientific_branch_id"]); out.append(c)
    return out


def _ref_map(root: Path, refcert: dict[str,Any]) -> dict[str,dict[str,Any]]:
    out={}
    for r in refcert["records"]:
        if r.get("status")!="REFERENCE_CERTIFIED": raise RuntimeError("uncertified K4 reference in K5")
        rr=dict(r); rr["final_reference_path_abs"]=str(root/r["final_reference_path"])
        out[f"{r['field_id']}::{r['case_type']}"]=rr
    if len(out)!=32: raise RuntimeError("reference map must contain 32 cases")
    return out


def _worker_init(root_s: str, response_rows: list[dict[str,Any]], generators: dict[str,dict[str,Any]], refmap: dict[str,dict[str,Any]], causal_cfg: dict[str,Any]) -> None:
    os.environ.update({k:"1" for k in ["OMP_NUM_THREADS","MKL_NUM_THREADS","OPENBLAS_NUM_THREADS","NUMEXPR_NUM_THREADS"]})
    global _WORKER
    byfield={}
    for row in response_rows: byfield.setdefault(row["field_id"],[]).append(row)
    for fid in byfield: byfield[fid]=sorted(byfield[fid],key=lambda r:r["case_type"])
    _WORKER={"root":Path(root_s),"rows":byfield,"generators":generators,"refmap":refmap,"causal_cfg":causal_cfg}


def score_candidate_field(pair: dict[str,Any], theta: list[float], field_id: str, n: int, cases: list[dict[str,Any]], generator: dict[str,Any], refmap: dict[str,dict[str,Any]], causal_cfg: dict[str,Any]) -> dict[str,Any]:
    x=np.linspace(0.0,1.0,n); t=np.linspace(0.0,1.0,n); tm=0.5*(t[:-1]+t[1:]); xm,tt=np.meshgrid(x,tm,indexing="ij")
    dmid=exponential_coefficient_derivatives(generator,xm,tt,4)
    gauge=_gauge_record(pair,theta,generator)
    raw=evaluate_pair_jet(pair["raw_X_AST"],pair["raw_T_AST"],theta,xm,tt,dmid,4)
    g=apply_gauge(raw,gauge)
    coeff=pushforward_variable(g,dmid[(0,0)],dmid[(1,0)],np.ones_like(xm),1e-12)
    sc=source_operator_coefficients(g,coeff)
    w=_simpson_weights(n); W=w[:,None]*w[None,:]; xn,tn=np.meshgrid(x,t,indexing="ij"); an=_a_at(generator,xn,tn)
    records=[]
    max_lin=0.0; min_att=math.inf; factor_s=0.0; solve_s=0.0
    for case in cases:
        key=f"{field_id}::{case['case_type']}"; ref=np.asarray(np.load(refmap[key]["final_reference_path_abs"],allow_pickle=False),dtype=np.float64); u=_restrict_reference(ref,n)
        source,_,_=_midpoint_source_derivatives(u,generator,case); wxx,wxt=inverse_transform_second_derivatives(source,g)
        forcing=coeff["m"]*wxt+coeff["r"]*wxx
        E,P,cert=causal_solve(x,t,sc,forcing,causal_cfg)
        Ex=_d1(E,1.0/(n-1),0); err=math.sqrt(max(float(np.sum(W*(P*P+an*Ex*Ex+E*E))),0.0)); full=physical_energy(u,generator)
        records.append({"field_id":field_id,"case_type":case["case_type"],"grid":n,"relative_energy_error":float(err/max(full,1e-14)),"max_step_linear_residual_relative":float(cert["max_step_linear_residual_relative"]),"minimum_source_A_tt":float(cert["minimum_source_A_tt"])})
        max_lin=max(max_lin,float(cert["max_step_linear_residual_relative"])); min_att=min(min_att,float(cert["minimum_source_A_tt"])); factor_s+=float(cert.get("factor_seconds",0.0)); solve_s+=float(cert.get("solve_seconds",0.0))
    return {"field_id":field_id,"grid":n,"records":records,"max_step_linear_residual_relative":max_lin,"minimum_source_A_tt":float(min_att),"factor_seconds_8cases":factor_s,"solve_seconds_8cases":solve_s}


def _worker(task: dict[str,Any]) -> dict[str,Any]:
    t0=time.perf_counter(); c0=time.process_time()
    try:
        fields=[]
        for fid in sorted(_WORKER["rows"]):
            fields.append(score_candidate_field(task["pair"],task["theta"],fid,int(task["grid"]),_WORKER["rows"][fid],_WORKER["generators"][fid],_WORKER["refmap"],_WORKER["causal_cfg"]))
        return {"scientific_branch_id":task["scientific_branch_id"],"membership_index":task["membership_index"],"arm":task["arm"],"paired_seed":task["paired_seed"],"proposal_index":task["proposal_index"],"structural_hash":task["structural_hash"],"theta_hex":task["theta_hex"],"grid":int(task["grid"]),"fields":fields,"worker_wall_seconds":time.perf_counter()-t0,"worker_cpu_seconds":time.process_time()-c0,"same_AST_theta_gauge_zero_refit":True}
    except (FloatingPointError, np.linalg.LinAlgError) as exc:
        return {"scientific_branch_id":task["scientific_branch_id"],"membership_index":task["membership_index"],"arm":task["arm"],"grid":int(task["grid"]),"numerical_unresolved":True,"exception_type":type(exc).__name__,"exception":str(exc),"worker_wall_seconds":time.perf_counter()-t0,"worker_cpu_seconds":time.process_time()-c0,"same_AST_theta_gauge_zero_refit":True}
    except RuntimeError as exc:
        # Sparse factorization singularity is numerical unresolved; unrelated RuntimeError is an implementation failure and must fail the stage.
        if "singular" not in str(exc).lower():
            raise
        return {"scientific_branch_id":task["scientific_branch_id"],"membership_index":task["membership_index"],"arm":task["arm"],"grid":int(task["grid"]),"numerical_unresolved":True,"exception_type":type(exc).__name__,"exception":str(exc),"worker_wall_seconds":time.perf_counter()-t0,"worker_cpu_seconds":time.process_time()-c0,"same_AST_theta_gauge_zero_refit":True}


def _completed_grid(path: Path) -> dict[str,dict[str,Any]]:
    out={}
    if path.is_file():
        for r in read_jsonl(path): out[r["scientific_branch_id"]]=r
    return out


def run_candidate_grid(root: Path, k5: Path, candidates: list[dict[str,Any]], grid: int, workers: int, response_rows: list[dict[str,Any]], generators: dict[str,dict[str,Any]], refmap: dict[str,dict[str,Any]], causal_cfg: dict[str,Any], progress_seconds: float, checkpoint_every: int) -> dict[str,dict[str,Any]]:
    work=k5/"work"; work.mkdir(exist_ok=True); partial=work/f"G{grid}_candidate_results.jsonl"; completed=_completed_grid(partial)
    remaining=[dict(c,grid=grid) for c in candidates if c["scientific_branch_id"] not in completed]
    start=time.monotonic(); done0=len(completed); last=start; total=len(candidates)
    print(f"[P13-S2-K5] stage=candidate_G{grid} processed={done0}/{total} current=resume_lock workers={workers} elapsed=0.0s rate=0/s ETA=NA",flush=True)
    with partial.open("a",encoding="utf-8") as fh:
        with ProcessPoolExecutor(max_workers=workers,mp_context=get_context("spawn"),initializer=_worker_init,initargs=(str(root),response_rows,generators,refmap,causal_cfg)) as ex:
            futs={ex.submit(_worker,t):t for t in remaining}
            for fut in as_completed(futs):
                r=fut.result(); completed[r["scientific_branch_id"]]=r; fh.write(json.dumps(r,sort_keys=True,separators=(",",":"))+"\n"); fh.flush()
                n=len(completed); now=time.monotonic()
                if n%checkpoint_every==0 or now-last>=progress_seconds or n==total:
                    elapsed=now-start; rate=max(n-done0,0)/max(elapsed,1e-9); eta=(total-n)/max(rate,1e-12); cur=r["scientific_branch_id"][:12]
                    print(f"[P13-S2-K5] stage=candidate_G{grid} processed={n}/{total} current_branch={cur} cases=32 elapsed={elapsed:.1f}s rate={rate:.3f}/s ETA={eta/60:.1f}m",flush=True)
                    write_json(work/f"G{grid}_checkpoint.json",{"processed":n,"total":total,"grid":grid,"elapsed_this_job_seconds":elapsed,"completed_ids_digest":hashlib.sha256(canonical_json_bytes(sorted(completed))).hexdigest()}); last=now
    if len(completed)!=total: raise RuntimeError(f"incomplete G{grid} cohort")
    return completed


def _flatten_errors(rec: dict[str,Any]) -> dict[str,float] | None:
    if rec.get("numerical_unresolved"): return None
    out={}
    for field in rec["fields"]:
        for r in field["records"]: out[f"{r['field_id']}::{r['case_type']}"]=float(r["relative_energy_error"])
    return out if len(out)==32 else None


def adjudicate_pair(coarse_rec: dict[str,Any], fine_rec: dict[str,Any], refmap: dict[str,dict[str,Any]], threshold: float) -> dict[str,Any]:
    ca=_flatten_errors(coarse_rec); fi=_flatten_errors(fine_rec)
    if ca is None or fi is None:
        return {"decision":"RESPONSE_UNRESOLVED","numerical_unresolved":True,"intervals":[],"worst_upper":None,"worst_lower":None}
    intervals=[]
    for key in sorted(refmap):
        iv=response_interval(ca[key],fi[key],float(refmap[key]["reference_uncertainty"])); fid,case=key.split("::",1); intervals.append({"field_id":fid,"case_type":case,"coarse_error":ca[key],"fine_error":fi[key],"reference_uncertainty":float(refmap[key]["reference_uncertainty"]),**iv})
    return {"decision":classify_branch(intervals,threshold),"numerical_unresolved":False,"intervals":intervals,"worst_upper":max(x["upper"] for x in intervals),"worst_lower":max(x["lower"] for x in intervals)}


def decision_census(candidates: list[dict[str,Any]], coarse: dict[str,dict[str,Any]], fine: dict[str,dict[str,Any]], refmap: dict[str,dict[str,Any]], threshold: float) -> tuple[list[dict[str,Any]],dict[str,int]]:
    rows=[]; counts={"RESPONSE_PASS":0,"RESPONSE_UNRESOLVED":0,"RESPONSE_FAIL":0}
    for c in candidates:
        sid=c["scientific_branch_id"]; a=adjudicate_pair(coarse[sid],fine[sid],refmap,threshold); counts[a["decision"]]+=1
        fail_witnesses=[x for x in a["intervals"] if x["lower"]>threshold]
        overlap=[x for x in a["intervals"] if x["lower"]<=threshold<=x["upper"]]
        rows.append({"membership_index":c["membership_index"],"scientific_branch_id":sid,"arm":c["arm"],"paired_seed":c["paired_seed"],"proposal_index":c["proposal_index"],"structural_hash":c["structural_hash"],"decision":a["decision"],"numerical_unresolved":a["numerical_unresolved"],"worst_upper":a["worst_upper"],"worst_lower":a["worst_lower"],"clear_fail_witness_count":len(fail_witnesses),"threshold_overlap_count":len(overlap),"first_clear_fail_witness":fail_witnesses[0] if fail_witnesses else None})
    return rows,counts


def _run_escalated_controls(root:Path,k4:Path,k5:Path,grid:int,response_rows:list[dict[str,Any]],generators:dict[str,dict[str,Any]],refmap:dict[str,dict[str,Any]],cfg:dict[str,Any])->dict[str,Any]:
    k4cfg=load_json(root/"phases/p13/coefficient_law_raw_xt/configs/p13_s2_k4_protocol.json")
    caps=load_json(root/"phases/p13/coefficient_law_raw_xt/configs/p13_s0_k2_protocol.json")["caps"]
    idpair=build_identity_pair(caps); nullpair=build_null_capacity_pair(caps); lock=k4cfg["null_control_lock"]
    if nullpair["structural_hash"]!=lock["structural_hash"]: raise RuntimeError("NULL structural hash mismatch")
    controls={"identity":(idpair,[]),"frozen_null":(nullpair,[float(x) for x in lock["theta"]])}
    tasks=[]
    for name,(pair,theta) in controls.items(): tasks+=_control_tasks(name,pair,theta,grid,response_rows,generators,refmap,k4cfg)
    recs=run_control_tasks(tasks,int(cfg["runtime"]["workers"][str(grid)]),f"K5_controls_G{grid}")
    write_json(k5/f"K5_CONTROL_ESCALATION_G{grid}.json",{"grid":grid,"records":recs})
    return {"grid":grid,"records":recs}


def _control_final(k4:Path,k5:Path,coarse_grid:int,fine_grid:int,threshold:float)->dict[str,Any]:
    base=load_json(k4/"K4_CONTROL_EXECUTION_RECORDS.json")["records"]
    extra=[]
    for g in (513,1025):
        p=k5/f"K5_CONTROL_ESCALATION_G{g}.json"
        if p.is_file(): extra+=load_json(p)["records"]
    allrecs=base+extra
    # ref map not required here except uncertainty; caller uses helper with actual map separately
    return {"records":allrecs,"coarse_grid":coarse_grid,"fine_grid":fine_grid}


def _quant(vals:list[float])->dict[str,float]|None:
    if not vals:return None
    a=np.asarray(vals,float); return {"min":float(np.min(a)),"p10":float(np.quantile(a,.1)),"median":float(np.median(a)),"p90":float(np.quantile(a,.9)),"p99":float(np.quantile(a,.99)),"max":float(np.max(a))}


def _source_manifest(root:Path,cfgp:Path)->dict[str,Any]:
    rels=["phases/p13/coefficient_law_raw_xt/src/p13rawxt/s2_k5_candidate_response_certification.py","phases/p13/coefficient_law_raw_xt/src/p13rawxt/s2_k4_response_reference_controls.py","phases/p13/coefficient_law_raw_xt/src/p13rawxt/s2_k3_response_protocol_lock.py","phases/p13/coefficient_law_raw_xt/src/p13rawxt/causal_calibration.py","phases/p13/coefficient_law_raw_xt/src/p13rawxt/s2_k1_operator_transfer.py",str(cfgp.relative_to(root))]
    return {"files":[{"path":r,"bytes":(root/r).stat().st_size,"sha256":sha256_file(root/r)} for r in rels]}


def _update_context(root:Path,summary:dict[str,Any])->None:
    p=root/"P13_S2_ROLLING_CONTEXT.md"
    if not p.is_file(): return
    marker="<!-- S2_K5_FORMAL_RESULT -->"; c=summary["decision_counts"]
    block=f'''{marker}\n## S2-K5 — complete DEVELOPMENT response certification\n\n- `OVERALL_STATUS`: **{summary['OVERALL_STATUS']}**\n- complete response-eligible cohort: `1955/1955`\n- final fidelity pair: `{summary['final_fidelity_pair']}`\n- `RESPONSE_PASS`: `{c['RESPONSE_PASS']}`\n- `RESPONSE_UNRESOLVED`: `{c['RESPONSE_UNRESOLVED']}`\n- `RESPONSE_FAIL`: `{c['RESPONSE_FAIL']}`\n- top-k/Pareto/proxy narrowing: `False`\n- candidate-specific fidelity rescue: `False`\n- DEVELOPMENT response: `OPENED`; SEALED: `UNOPENED`\n- semantic output digest: `{summary['semantic_output_digest']}`\n- next action: `{summary['NEXT_ACTION']}`\n'''
    text=p.read_text(encoding="utf-8"); text=text.split(marker)[0].rstrip()+"\n\n"+block if marker in text else text.rstrip()+"\n\n"+block; write_text(p,text)


def run(root:Path)->int:
    root=root.resolve(); home=root/"phases/p13/coefficient_law_raw_xt"; cfgp=home/"configs/p13_s2_k5_protocol.json"; cfg=load_json(cfgp)
    s2run,k4,refcert,response,entry=_verify_entry(root,cfg); k5=s2run/"K5_complete_candidate_response_certification"; k5.mkdir(exist_ok=True); write_json(k5/"K5_ENTRY_AND_K4_REVIEW.json",entry)
    if entry["status"]!="PASS":
        write_text(s2run/"K5_OVERALL_STATUS.txt","FAIL\n"); write_text(s2run/"K5_NEXT_ACTION.txt","BLOCK_P13_S2_REPAIR_K4_K5_ENTRY\n"); print("[P13-S2-K5] OVERALL_STATUS=FAIL",flush=True); return 2
    candidates=_load_candidates(root,cfg); refmap=_ref_map(root,refcert)
    pf1=load_json(root/cfg["pf1_active_input_manifest"]["path"]); coeff_commit=pf1["DEVELOPMENT_coefficient_commitment"]; generators,copen=load_coefficient_generators(Path(coeff_commit["archive_absolute_path"]),coeff_commit); write_json(k5/"K5_COEFFICIENT_REOPEN_AUDIT.json",copen)
    if copen["status"]!="PASS": raise RuntimeError("K5 DEVELOPMENT coefficient reopen failed")
    response_rows=response["rows"]; causal_cfg=load_json(root/"phases/p13/coefficient_law_raw_xt/configs/p13_s2_k4_protocol.json")["causal_solver_numerical"]
    workers=cfg["runtime"]["workers"]; prog=float(cfg["runtime"]["progress_every_seconds"]); ck=int(cfg["runtime"]["checkpoint_every_completed_field_tasks"])
    grids:dict[int,dict[str,dict[str,Any]]]={}
    for g in (129,257): grids[g]=run_candidate_grid(root,k5,candidates,g,int(workers[str(g)]),response_rows,generators,refmap,causal_cfg,prog,ck)
    rows,counts=decision_census(candidates,grids[129],grids[257],refmap,float(cfg["response_contract"]["threshold"])); escalation=[{"pair":[129,257],"counts":counts}]; final_pair=[129,257]
    if counts["RESPONSE_UNRESOLVED"]>0:
        _run_escalated_controls(root,k4,k5,513,response_rows,generators,refmap,cfg); grids[513]=run_candidate_grid(root,k5,candidates,513,int(workers["513"]),response_rows,generators,refmap,causal_cfg,prog,ck)
        rows,counts=decision_census(candidates,grids[257],grids[513],refmap,float(cfg["response_contract"]["threshold"])); escalation.append({"pair":[257,513],"counts":counts}); final_pair=[257,513]
    if counts["RESPONSE_UNRESOLVED"]>0:
        _run_escalated_controls(root,k4,k5,1025,response_rows,generators,refmap,cfg); grids[1025]=run_candidate_grid(root,k5,candidates,1025,int(workers["1025"]),response_rows,generators,refmap,causal_cfg,prog,ck)
        rows,counts=decision_census(candidates,grids[513],grids[1025],refmap,float(cfg["response_contract"]["threshold"])); escalation.append({"pair":[513,1025],"counts":counts}); final_pair=[513,1025]
    # Controls must exist at every escalated fine grid; compute final control decision descriptively.
    control_final=load_json(k4/"K4_CONTROL_FIRST_RESULTS.json")
    if final_pair!=[129,257]:
        allrecs=load_json(k4/"K4_CONTROL_EXECUTION_RECORDS.json")["records"]
        for g in (513,1025):
            p=k5/f"K5_CONTROL_ESCALATION_G{g}.json"
            if p.is_file(): allrecs+=load_json(p)["records"]
        control_final={"status":"PASS","coarse_grid":final_pair[0],"fine_grid":final_pair[1],"controls":_control_decisions(allrecs,refmap,final_pair[0],final_pair[1],float(cfg["response_contract"]["threshold"])),"membership_authority":"NONE"}
    write_json(k5/"K5_FINAL_CONTROL_RESULTS.json",control_final)
    # Authoritative full measurement ledger.
    measurements=k5/cfg["outputs"]["measurements"]
    with measurements.open("w",encoding="utf-8") as f:
        for c in candidates:
            sid=c["scientific_branch_id"]; obj={"membership_index":c["membership_index"],"scientific_branch_id":sid,"arm":c["arm"],"paired_seed":c["paired_seed"],"proposal_index":c["proposal_index"],"structural_hash":c["structural_hash"],"theta_hex":c["theta_hex"],"same_AST_theta_gauge_zero_refit":True,"grids":{str(g):grids[g][sid] for g in sorted(grids)}}; f.write(json.dumps(obj,sort_keys=True,separators=(",",":"))+"\n")
    dmap=k5/cfg["outputs"]["decision_map"]
    with dmap.open("w",encoding="utf-8") as f:
        for r in rows: r["final_fidelity_pair"]=final_pair; f.write(json.dumps(r,sort_keys=True,separators=(",",":"))+"\n")
    ppass=k5/cfg["outputs"]["pass_membership"]
    with ppass.open("w",encoding="utf-8") as f:
        for r in rows:
            if r["decision"]=="RESPONSE_PASS": f.write(json.dumps({"membership_index":r["membership_index"],"scientific_branch_id":r["scientific_branch_id"],"K5_decision":"RESPONSE_PASS","K5_decision_map_path":str(dmap.relative_to(root))},sort_keys=True,separators=(",",":"))+"\n")
    write_json(k5/cfg["outputs"]["escalation_summary"],{"status":"PASS","uniform_complete_cohort":True,"candidate_specific_rescue":False,"steps":escalation,"final_pair":final_pair,"controls_escalated_with_candidates":True})
    worst_upper=[float(r["worst_upper"]) for r in rows if r["worst_upper"] is not None]; worst_lower=[float(r["worst_lower"]) for r in rows if r["worst_lower"] is not None]
    summary_dec={"complete_census":1955,"decision_counts":counts,"final_fidelity_pair":final_pair,"worst_upper_distribution":_quant(worst_upper),"worst_lower_distribution":_quant(worst_lower),"response_pass_membership_count":counts["RESPONSE_PASS"],"top_k_or_Pareto_used":False,"proxy_filter_used":False,"candidate_specific_rescue":False,"all_scientific_identities_preserved":True}
    write_json(k5/cfg["outputs"]["decision_summary"],summary_dec)
    guard={"DEVELOPMENT_COEF":"OPENED_K0_READ_IN_PLACE","DEVELOPMENT_RESPONSE":"OPENED_K4_USED_K5","SEALED_FINAL_COEF":"SEALED_COMMITTED_UNOPENED","SEALED_FINAL_RESPONSE":"SEALED_COMMITTED_UNOPENED","candidate_refit":False,"branch_reselection":False,"top_k_or_proxy_filter":False,"amplitude_compensation":False,"complete_1955_evaluated":True}; write_json(k5/"K5_DATA_BOUNDARY_GUARD.json",guard)
    runtime={"python":sys.version,"platform":platform.platform(),"allocation":cfg["runtime"]["allocation"],"workers":workers,"OMP_NUM_THREADS":os.environ.get("OMP_NUM_THREADS"),"MKL_NUM_THREADS":os.environ.get("MKL_NUM_THREADS"),"OPENBLAS_NUM_THREADS":os.environ.get("OPENBLAS_NUM_THREADS"),"NUMEXPR_NUM_THREADS":os.environ.get("NUMEXPR_NUM_THREADS"),"restart_resume":True}; write_json(k5/"K5_RUNTIME_ENVIRONMENT.json",runtime)
    src=_source_manifest(root,cfgp); write_json(k5/"K5_SOURCE_MANIFEST.json",src)
    basis={"K4_semantic_output_digest":cfg["expected_k4"]["semantic_output_digest"],"K4R_semantic_output_digest":cfg["expected_k4"]["k4r_semantic_output_digest"],"response_eligible_membership_sha256":cfg["response_eligible_membership"]["sha256"],"reference_certification_sha256":sha256_file(k4/"K4_REFERENCE_CERTIFICATION.json"),"measurements_sha256":sha256_file(measurements),"decision_map_sha256":sha256_file(dmap),"response_pass_membership_sha256":sha256_file(ppass),"decision_summary":summary_dec,"escalation":escalation,"data_boundary":guard,"source_manifest":src}
    sem=hashlib.sha256(canonical_json_bytes(basis)).hexdigest(); write_json(k5/"K5_SEMANTIC_OUTPUT_DIGEST.json",{"semantic_output_digest":sem,"basis":basis})
    summary={"OVERALL_STATUS":"PASS","NEXT_ACTION":cfg["next_action_on_pass"],"complete_response_cohort":1955,"decision_counts":counts,"final_fidelity_pair":final_pair,"measurements":str(measurements.relative_to(root)),"measurements_sha256":sha256_file(measurements),"decision_map":str(dmap.relative_to(root)),"decision_map_sha256":sha256_file(dmap),"response_pass_membership":str(ppass.relative_to(root)),"response_pass_membership_sha256":sha256_file(ppass),"DEVELOPMENT_response_opened":True,"SEALED_opened":False,"candidate_specific_rescue":False,"top_k_or_proxy_filter":False,"semantic_output_digest":sem,"authoritative_S2_run":str(s2run.relative_to(root))}; write_json(k5/"K5_SCIENTIFIC_SUMMARY.json",summary)
    write_text(s2run/"K5_OVERALL_STATUS.txt","PASS\n"); write_text(s2run/"K5_NEXT_ACTION.txt",cfg["next_action_on_pass"]+"\n"); write_text(home/"runs/LATEST_P13_S2_K5_RUN.txt",str(s2run.relative_to(root))+"\n"); _update_context(root,summary)
    # Successful freeze supersedes restart shards/checkpoints; authoritative measurements remain.
    work=k5/"work"
    if work.exists(): shutil.rmtree(work)
    print(f"[P13-S2-K5] OVERALL_STATUS=PASS PASS={counts['RESPONSE_PASS']} UNRESOLVED={counts['RESPONSE_UNRESOLVED']} FAIL={counts['RESPONSE_FAIL']} final_pair={final_pair}",flush=True); print(f"[P13-S2-K5] NEXT_ACTION={cfg['next_action_on_pass']}",flush=True); return 0


def main()->int:
    ap=argparse.ArgumentParser(); ap.add_argument("--project-root",required=True); args=ap.parse_args(); return run(Path(args.project_root))

if __name__=="__main__": raise SystemExit(main())
