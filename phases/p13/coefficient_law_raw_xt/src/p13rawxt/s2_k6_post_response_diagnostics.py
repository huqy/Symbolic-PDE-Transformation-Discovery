from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import sys
import time
from pathlib import Path
from typing import Any, Iterable

import numpy as np


def load_json(p: Path) -> Any:
    return json.loads(p.read_text(encoding="utf-8"))


def write_json(p: Path, x: Any) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(x, sort_keys=True, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_text(p: Path, s: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(s, encoding="utf-8")


def read_jsonl(p: Path) -> list[dict[str, Any]]:
    out=[]
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip(): out.append(json.loads(line))
    return out


def iter_jsonl(p: Path) -> Iterable[dict[str, Any]]:
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip(): yield json.loads(line)


def sha256_file(p: Path) -> str:
    h=hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda:f.read(1<<20), b""): h.update(b)
    return h.hexdigest()


def canonical_bytes(x: Any) -> bytes:
    return json.dumps(x, sort_keys=True, separators=(",",":"), ensure_ascii=False).encode("utf-8")


def count_jsonl(p: Path) -> int:
    with p.open("rb") as f: return sum(1 for x in f if x.strip())


def resolve_marker(root: Path, rel: str) -> Path:
    p=root/rel
    if not p.is_file(): raise FileNotFoundError(p)
    q=root/p.read_text(encoding="utf-8").strip()
    if not q.exists(): raise FileNotFoundError(q)
    return q


def qstats(vals: Iterable[float]) -> dict[str,float] | None:
    a=np.asarray([float(v) for v in vals if v is not None and math.isfinite(float(v))], dtype=float)
    if a.size==0: return None
    return {"n":int(a.size),"min":float(np.min(a)),"p10":float(np.quantile(a,.10)),"median":float(np.median(a)),"p90":float(np.quantile(a,.90)),"p99":float(np.quantile(a,.99)),"max":float(np.max(a))}


def _avg_ranks(x: np.ndarray) -> np.ndarray:
    order=np.argsort(x,kind="mergesort"); ranks=np.empty(len(x),dtype=float); i=0
    while i<len(x):
        j=i+1
        while j<len(x) and x[order[j]]==x[order[i]]: j+=1
        ranks[order[i:j]]=0.5*((i+1)+j); i=j
    return ranks


def spearman_pairs(rows: list[dict[str,Any]], xkey: str, ykey: str) -> dict[str,Any]:
    pairs=[]
    for r in rows:
        x=r.get(xkey); y=r.get(ykey)
        if x is None or y is None: continue
        x=float(x); y=float(y)
        if math.isfinite(x) and math.isfinite(y): pairs.append((x,y))
    if len(pairs)<3: return {"n":len(pairs),"spearman":None,"p_value":None}
    x=np.asarray([p[0] for p in pairs],float); y=np.asarray([p[1] for p in pairs],float)
    rx=_avg_ranks(x); ry=_avg_ranks(y); sx=float(np.std(rx)); sy=float(np.std(ry))
    rho=None if sx==0 or sy==0 else float(np.corrcoef(rx,ry)[0,1])
    return {"n":len(pairs),"spearman":rho,"p_value":None}


def _nested_semantic(summary_path: Path, key: str) -> str | None:
    if not summary_path.is_file(): return None
    x=load_json(summary_path)
    return x.get(key) or x.get("semantic_output_digest") or x.get("PF0_semantic_output_digest")


def _verify_entry(root: Path, cfg: dict[str,Any]) -> tuple[Path,Path,Path,dict[str,Any]]:
    checks={}; s2run=resolve_marker(root,cfg["k5_run_marker"]); k5=s2run/"K5_complete_candidate_response_certification"
    checks["K5_status"]=(s2run/"K5_OVERALL_STATUS.txt").is_file() and (s2run/"K5_OVERALL_STATUS.txt").read_text().strip()==cfg["expected_k5"]["overall_status"]
    checks["K5_next"]=(s2run/"K5_NEXT_ACTION.txt").is_file() and (s2run/"K5_NEXT_ACTION.txt").read_text().strip()==cfg["expected_k5"]["next_action"]
    summ=load_json(k5/"K5_SCIENTIFIC_SUMMARY.json")
    ek=cfg["expected_k5"]
    checks["K5_semantic"]=summ.get("semantic_output_digest")==ek["semantic_output_digest"]
    checks["K5_counts"]=summ.get("complete_response_cohort")==ek["complete_response_cohort"] and summ.get("decision_counts")=={"RESPONSE_PASS":ek["response_pass"],"RESPONSE_UNRESOLVED":ek["response_unresolved"],"RESPONSE_FAIL":ek["response_fail"]}
    checks["K5_pair"]=summ.get("final_fidelity_pair")==ek["final_fidelity_pair"]
    mp=root/summ["measurements"]; dp=root/summ["decision_map"]; pp=root/summ["response_pass_membership"]
    checks["measurements_sha"]=mp.is_file() and sha256_file(mp)==ek["measurements_sha256"]
    checks["decision_map_sha"]=dp.is_file() and sha256_file(dp)==ek["decision_map_sha256"]
    checks["pass_membership_sha"]=pp.is_file() and sha256_file(pp)==ek["pass_membership_sha256"]
    checks["counts_1955"]=all(count_jsonl(p)==1955 for p in [mp,dp,pp])
    drows=read_jsonl(dp); checks["all_response_pass"]=len(drows)==1955 and all(r.get("decision")=="RESPONSE_PASS" for r in drows)
    guard=load_json(k5/"K5_DATA_BOUNDARY_GUARD.json")
    checks["guard"]=guard.get("SEALED_FINAL_COEF")=="SEALED_COMMITTED_UNOPENED" and guard.get("SEALED_FINAL_RESPONSE")=="SEALED_COMMITTED_UNOPENED" and guard.get("candidate_refit") is False and guard.get("branch_reselection") is False and guard.get("top_k_or_proxy_filter") is False and guard.get("complete_1955_evaluated") is True
    esc=load_json(k5/"K5_FIDELITY_ESCALATION_SUMMARY.json"); checks["uniform_fidelity"]=esc.get("uniform_complete_cohort") is True and esc.get("candidate_specific_rescue") is False and esc.get("final_pair")==[129,257]
    s1run=root/cfg["parent_evidence"]["s1_run"]; checks["S1_run_exists"]=s1run.exists()
    k1=s2run/cfg["parent_evidence"]["k1_measurements_rel_to_s2run"]; checks["K1_sha"]=k1.is_file() and sha256_file(k1)==cfg["parent_evidence"]["k1_measurements_sha256"]
    k2bs=s1run/"K2B_diagnostics/K2B_scientific_summary.json"; k2cs=s1run/"K2C_theory_bridge/K2C_scientific_summary.json"; pf0s=s1run/"PF0_postfreeze/PF0_scientific_summary.json"
    checks["K2B_semantic"]=_nested_semantic(k2bs,"semantic_output_digest")==cfg["parent_evidence"]["k2b_semantic_output_digest"]
    checks["K2C_semantic"]=_nested_semantic(k2cs,"semantic_output_digest")==cfg["parent_evidence"]["k2c_semantic_output_digest"]
    checks["PF0_semantic"]=_nested_semantic(pf0s,"PF0_semantic_output_digest")==cfg["parent_evidence"]["pf0_semantic_output_digest"]
    for nm in ["k2b_branch_results","k2c_branch_results","pf0_geometry_rows","pf0_asp_rows"]: checks[nm]=(s1run/cfg["parent_evidence"][nm]).is_file()
    return s2run,k5,s1run,{"status":"PASS" if all(checks.values()) else "FAIL","checks":checks,"K5_semantic_output_digest":summ.get("semantic_output_digest")}


def _final_grid_response(m: dict[str,Any], fine_grid: int) -> tuple[dict[str,float], dict[str,float]]:
    g=m.get("grids",{}).get(str(fine_grid));
    if not g or g.get("numerical_unresolved"): raise RuntimeError(f"missing resolved final-grid measurement {m.get('scientific_branch_id')}")
    cases={}; byfield={}
    for field in g["fields"]:
        fid=field["field_id"]
        for rec in field["records"]:
            key=f"{fid}::{rec['case_type']}"; v=float(rec["relative_energy_error"]); cases[key]=v; byfield.setdefault(fid,[]).append(v)
    if len(cases)!=32: raise RuntimeError("final-grid response must contain 32 cases")
    return cases,{fid:max(v) for fid,v in byfield.items()}


def _control_nominals(k5: Path) -> dict[str,Any]:
    x=load_json(k5/"K5_FINAL_CONTROL_RESULTS.json"); out={}
    for name,c in x.get("controls",{}).items():
        iv=c.get("pair_intervals",[]); vals={f"{r['field_id']}::{r['case_type']}":float(r["nominal"]) for r in iv}
        out[name]={"decision":c.get("decision"),"nominal_by_case":vals,"worst_nominal":max(vals.values()) if vals else None,"worst_upper":c.get("worst_upper"),"worst_lower":c.get("worst_lower")}
    return out


def _dict_by_id(p: Path) -> dict[str,dict[str,Any]]:
    return {r["scientific_branch_id"]:r for r in iter_jsonl(p)}


def _extract_lower(k2b: dict[str,Any]) -> dict[str,Any]:
    lo=(k2b.get("lower_order") or {}).get("TRAIN_G65") or {}
    abl=k2b.get("coefficient_ablation") or {}
    return {
      "coefficient_ablation_delta_rel":abl.get("Delta_coef_rel"),
      "lower_max_abs_L_T_over_C_TT":lo.get("max_abs_L_T_over_C_TT"),
      "lower_max_abs_L_X_over_C_TT":lo.get("max_abs_L_X_over_C_TT"),
      "lower_min_C_TT":lo.get("min_C_TT"),
      "lower_max_abs_q_over_C_TT":lo.get("max_abs_q_over_C_TT"),
      "lower_max_abs_inv_C_TT":lo.get("max_abs_inv_C_TT")
    }


def _extract_pf0(geom: dict[str,Any], asp: dict[str,Any]) -> dict[str,Any]:
    lam=asp.get("lambda_star_on_grid"); abslog=None
    if lam is not None and float(lam)>0: abslog=abs(math.log(float(lam)))
    return {
      "pf0_geometry_status":geom.get("geometry_status"),
      "pf0_s_norm_ratio":geom.get("s_norm_ratio"),
      "pf0_theory_cosine":geom.get("c_theory_cosine"),
      "pf0_tangent_relative_residual":geom.get("r_theory_relative_residual"),
      "pf0_radial_fraction":geom.get("radial_fraction_of_r_squared"),
      "pf0_signed_radial_offset":geom.get("signed_radial_offset_s_minus_c"),
      "pf0_projection_relative_residual":geom.get("projection_relative_residual"),
      "pf0_G_metric_coefficient_cosine":geom.get("G_metric_coefficient_cosine_to_theory"),
      "pf0_G_metric_coefficient_relative_residual":geom.get("G_metric_coefficient_relative_residual"),
      "asp_status":asp.get("status"),
      "asp_lambda_star":lam,
      "asp_abs_log_lambda_star":abslog,
      "asp_radial_slack":asp.get("radial_slack"),
      "asp_minimum_at_grid_boundary":asp.get("minimum_at_grid_boundary")
    }


def _case_census(rows: list[dict[str,Any]], controls: dict[str,Any]) -> dict[str,Any]:
    keys=sorted(rows[0]["response_cases"]); out=[]
    for key in keys:
        vals=[float(r["response_cases"][key]) for r in rows]; fid,case=key.split("::",1)
        rec={"field_id":fid,"case_type":case,"candidate_response_error":qstats(vals)}
        for cname in ["identity","frozen_null"]:
            cv=controls.get(cname,{}).get("nominal_by_case",{}).get(key); rec[f"{cname}_nominal_error"]=cv
            rec[f"fraction_candidates_below_{cname}"]=None if cv is None else float(np.mean(np.asarray(vals)<float(cv)))
        out.append(rec)
    return {"rows":out,"hardest_by_candidate_median":sorted(out,key=lambda r:r["candidate_response_error"]["median"],reverse=True)[:8],"membership_authority":False}


def _arm_seed(rows: list[dict[str,Any]]) -> dict[str,Any]:
    def agg(rr:list[dict[str,Any]])->dict[str,Any]: return {"n":len(rr),"worst_nominal":qstats([r["response_worst_nominal"] for r in rr]),"worst_upper":qstats([r["response_worst_upper"] for r in rr]),"response_margin":qstats([r["response_margin_to_0p15"] for r in rr])}
    byarm={a:agg([r for r in rows if r["arm"]==a]) for a in sorted({r["arm"] for r in rows})}
    byseed={str(s):agg([r for r in rows if int(r["paired_seed"])==s]) for s in sorted({int(r["paired_seed"]) for r in rows})}
    return {"status":"EXPLORATORY_DESCRIPTIVE","by_arm":byarm,"by_paired_seed":byseed,"membership_authority":False,"formal_arm_selection":False}


def _source_manifest(root: Path, cfgp: Path) -> dict[str,Any]:
    rels=[str(cfgp.relative_to(root)),"phases/p13/coefficient_law_raw_xt/src/p13rawxt/s2_k6_post_response_diagnostics.py","phases/p13/coefficient_law_raw_xt/scripts/run_p13_s2_k6.sh","phases/p13/coefficient_law_raw_xt/scripts/package_p13_s2_k6_audit.sh","phases/p13/coefficient_law_raw_xt/tests/test_p13_s2_k6.py","phases/p13/coefficient_law_raw_xt/docs/P13_S2_K6_POST_RESPONSE_DIAGNOSTICS.md"]
    return {"files":[{"path":r,"bytes":(root/r).stat().st_size,"sha256":sha256_file(root/r)} for r in rels if (root/r).is_file()]}


def _update_context(root: Path, summary: dict[str,Any]) -> None:
    p=root/"P13_S2_ROLLING_CONTEXT.md"; marker="<!-- S2_K6_FORMAL_RESULT -->"
    if not p.is_file(): return
    block=f'''{marker}\n## S2-K6 — post-response diagnostics\n\n- `OVERALL_STATUS`: **{summary['OVERALL_STATUS']}**\n- frozen K5 response cohort/decisions changed: `False`\n- diagnostic rows: `{summary['diagnostic_rows']}`\n- DEVELOPMENT response: `OPENED`; SEALED: `UNOPENED`\n- top-k/Pareto/proxy/new threshold/refit: `False`\n- semantic output digest: `{summary['semantic_output_digest']}`\n- next action: `{summary['NEXT_ACTION']}`\n'''
    text=p.read_text(encoding="utf-8"); text=text.split(marker)[0].rstrip()+"\n\n"+block if marker in text else text.rstrip()+"\n\n"+block; write_text(p,text)


def run(root: Path) -> int:
    t0=time.perf_counter(); root=root.resolve(); home=root/"phases/p13/coefficient_law_raw_xt"; cfgp=home/"configs/p13_s2_k6_protocol.json"; cfg=load_json(cfgp)
    s2run,k5,s1run,entry=_verify_entry(root,cfg); k6=s2run/"K6_post_response_diagnostics"; k6.mkdir(exist_ok=True); write_json(k6/"K6_ENTRY_AND_K5_REVIEW.json",entry)
    if entry["status"]!="PASS":
        write_text(s2run/"K6_OVERALL_STATUS.txt","FAIL\n"); write_text(s2run/"K6_NEXT_ACTION.txt","BLOCK_P13_S2_K6_PARENT_EVIDENCE_REPAIR\n"); return 2
    k1=_dict_by_id(s2run/cfg["parent_evidence"]["k1_measurements_rel_to_s2run"])
    k2b=_dict_by_id(s1run/cfg["parent_evidence"]["k2b_branch_results"])
    geom=_dict_by_id(s1run/cfg["parent_evidence"]["pf0_geometry_rows"])
    asp=_dict_by_id(s1run/cfg["parent_evidence"]["pf0_asp_rows"])
    dmap=_dict_by_id(k5/"K5_RESPONSE_DECISION_MAP.jsonl"); controls=_control_nominals(k5)
    fine=int(cfg["expected_k5"]["final_fidelity_pair"][1]); rows=[]; measurements=k5/"K5_CANDIDATE_RESPONSE_MEASUREMENTS.jsonl"
    for i,m in enumerate(iter_jsonl(measurements),1):
        sid=m["scientific_branch_id"]
        if sid not in dmap or dmap[sid].get("decision")!="RESPONSE_PASS": raise RuntimeError(f"K5 pass/measurement mismatch {sid}")
        if sid not in k1 or sid not in k2b or sid not in geom or sid not in asp: raise RuntimeError(f"missing parent diagnostic row {sid}")
        cases,byfield=_final_grid_response(m,fine); vals=list(cases.values()); kd=dmap[sid]; kr=k1[sid]
        worst_nom=max(vals); rms=float(math.sqrt(sum(v*v for v in vals)/len(vals))); med=float(np.median(np.asarray(vals,float)))
        r={"scientific_branch_id":sid,"membership_index":m["membership_index"],"arm":m["arm"],"paired_seed":m["paired_seed"],"proposal_index":m["proposal_index"],"structural_hash":m["structural_hash"],"K5_decision":"RESPONSE_PASS","K5_decision_immutable":True,"membership_authority":False,
           "TRAIN_family_ratio_G33":kr.get("TRAIN_family_ratio_G33"),"DEV_family_ratio_G33":kr.get("DEV_G33",{}).get("family_ratio_to_identity"),"DEV_family_ratio_G65":kr.get("DEV_G65",{}).get("family_ratio_to_identity"),"rho_transfer_G33":kr.get("rho_transfer"),"rho_transfer_G65":kr.get("rho_transfer_G65"),
           "response_worst_nominal":worst_nom,"response_RMS_nominal":rms,"response_median_nominal":med,"response_worst_upper":kd.get("worst_upper"),"response_worst_lower":kd.get("worst_lower"),"response_margin_to_0p15":0.15-float(kd["worst_upper"]),"response_cases":cases,"response_worst_by_field":byfield}
        r.update(_extract_lower(k2b[sid])); r.update(_extract_pf0(geom[sid],asp[sid])); rows.append(r)
        if i%int(cfg["runtime"]["progress_every_rows"])==0 or i==1955: print(f"[P13-S2-K6] stage=join processed={i}/1955 current={sid[:12]} elapsed={time.perf_counter()-t0:.1f}s",flush=True)
    if len(rows)!=1955 or len({r["scientific_branch_id"] for r in rows})!=1955: raise RuntimeError("K6 complete diagnostic cohort mismatch")
    outp=k6/cfg["outputs"]["branch_diagnostics"]
    with outp.open("w",encoding="utf-8") as f:
        for r in rows: f.write(json.dumps(r,sort_keys=True,separators=(",",":"))+"\n")
    resp={"n":1955,"all_K5_RESPONSE_PASS":True,"final_fidelity_pair":[129,257],"worst_nominal":qstats([r["response_worst_nominal"] for r in rows]),"RMS_nominal":qstats([r["response_RMS_nominal"] for r in rows]),"median_nominal":qstats([r["response_median_nominal"] for r in rows]),"worst_upper":qstats([r["response_worst_upper"] for r in rows]),"response_margin_to_0p15":qstats([r["response_margin_to_0p15"] for r in rows]),"formal_membership_change":False}; write_json(k6/cfg["outputs"]["response_distributions"],resp)
    case=_case_census(rows,controls); write_json(k6/cfg["outputs"]["case_census"],case)
    xkeys=["TRAIN_family_ratio_G33","DEV_family_ratio_G33","DEV_family_ratio_G65","rho_transfer_G33","rho_transfer_G65","coefficient_ablation_delta_rel","lower_max_abs_L_T_over_C_TT","lower_max_abs_L_X_over_C_TT","lower_min_C_TT","lower_max_abs_q_over_C_TT","lower_max_abs_inv_C_TT"]
    ykeys=["response_worst_nominal","response_RMS_nominal","response_worst_upper","response_margin_to_0p15"]
    assoc={x:{y:spearman_pairs(rows,x,y) for y in ykeys} for x in xkeys}; write_json(k6/cfg["outputs"]["operator_response_associations"],{"status":"DESCRIPTIVE_ONLY","p_values_reported":False,"membership_authority":False,"associations":assoc})
    pfkeys=["pf0_s_norm_ratio","pf0_theory_cosine","pf0_tangent_relative_residual","pf0_radial_fraction","pf0_signed_radial_offset","pf0_projection_relative_residual","pf0_G_metric_coefficient_cosine","pf0_G_metric_coefficient_relative_residual","asp_abs_log_lambda_star","asp_radial_slack"]
    pfa={x:{y:spearman_pairs(rows,x,y) for y in ykeys} for x in pfkeys}; write_json(k6/cfg["outputs"]["pf0_response_associations"],{"status":"EXPLORATORY_POST_RESPONSE_DESCRIPTIVE","p_values_reported":False,"membership_authority":False,"K1_preDEV_prediction_was_about_transfer_not_response":True,"associations":pfa})
    ar=_arm_seed(rows); write_json(k6/cfg["outputs"]["arm_seed_descriptive"],ar)
    cc={"status":"DESCRIPTIVE_ONLY","controls":controls,"candidate_to_identity_worst_nominal_ratio":qstats([r["response_worst_nominal"]/controls["identity"]["worst_nominal"] for r in rows]),"candidate_to_null_worst_nominal_ratio":qstats([r["response_worst_nominal"]/controls["frozen_null"]["worst_nominal"] for r in rows]),"fraction_candidates_below_identity_worst_nominal":float(np.mean([r["response_worst_nominal"]<controls["identity"]["worst_nominal"] for r in rows])),"fraction_candidates_below_null_worst_nominal":float(np.mean([r["response_worst_nominal"]<controls["frozen_null"]["worst_nominal"] for r in rows])),"membership_authority":False}; write_json(k6/cfg["outputs"]["control_comparisons"],cc)
    guard={"DEVELOPMENT_COEF":"OPENED_K0_READ_ONLY","DEVELOPMENT_RESPONSE":"OPENED_K4_K5_READ_ONLY","SEALED_FINAL_COEF":"SEALED_COMMITTED_UNOPENED","SEALED_FINAL_RESPONSE":"SEALED_COMMITTED_UNOPENED","K5_decision_map_modified":False,"K5_pass_membership_modified":False,"candidate_refit":False,"branch_reselection":False,"top_k_or_proxy_filter":False,"weighted_composite_score":False,"new_response_threshold":False,"diagnostics_have_membership_authority":False}; write_json(k6/"K6_DATA_BOUNDARY_GUARD.json",guard)
    runtime={"python":sys.version,"platform":platform.platform(),"mode":cfg["runtime"]["mode"],"OMP_NUM_THREADS":os.environ.get("OMP_NUM_THREADS"),"MKL_NUM_THREADS":os.environ.get("MKL_NUM_THREADS"),"OPENBLAS_NUM_THREADS":os.environ.get("OPENBLAS_NUM_THREADS"),"NUMEXPR_NUM_THREADS":os.environ.get("NUMEXPR_NUM_THREADS"),"elapsed_seconds":time.perf_counter()-t0}; write_json(k6/"K6_RUNTIME_ENVIRONMENT.json",runtime)
    src=_source_manifest(root,cfgp); write_json(k6/"K6_SOURCE_MANIFEST.json",src)
    basis={"K5_semantic_output_digest":cfg["expected_k5"]["semantic_output_digest"],"K5_decision_map_sha256":cfg["expected_k5"]["decision_map_sha256"],"K5_pass_membership_sha256":cfg["expected_k5"]["pass_membership_sha256"],"branch_diagnostics_sha256":sha256_file(outp),"response_distributions":resp,"case_census_sha256":sha256_file(k6/cfg["outputs"]["case_census"]),"operator_associations_sha256":sha256_file(k6/cfg["outputs"]["operator_response_associations"]),"pf0_associations_sha256":sha256_file(k6/cfg["outputs"]["pf0_response_associations"]),"arm_seed_sha256":sha256_file(k6/cfg["outputs"]["arm_seed_descriptive"]),"control_comparisons_sha256":sha256_file(k6/cfg["outputs"]["control_comparisons"]),"guard":guard,"source_manifest":src}
    sem=hashlib.sha256(canonical_bytes(basis)).hexdigest(); write_json(k6/"K6_SEMANTIC_OUTPUT_DIGEST.json",{"semantic_output_digest":sem,"basis":basis})
    summary={"OVERALL_STATUS":"PASS","NEXT_ACTION":cfg["next_action_on_pass"],"diagnostic_rows":1955,"K5_decisions_immutable":True,"K5_response_pass_membership_immutable":True,"all_K5_RESPONSE_PASS":True,"DEVELOPMENT_response":"OPENED_READ_ONLY","SEALED_opened":False,"top_k_or_proxy_filter":False,"candidate_refit":False,"new_response_threshold":False,"branch_diagnostics":str(outp.relative_to(root)),"branch_diagnostics_sha256":sha256_file(outp),"semantic_output_digest":sem,"authoritative_S2_run":str(s2run.relative_to(root))}; write_json(k6/"K6_SCIENTIFIC_SUMMARY.json",summary)
    write_text(s2run/"K6_OVERALL_STATUS.txt","PASS\n"); write_text(s2run/"K6_NEXT_ACTION.txt",cfg["next_action_on_pass"]+"\n"); write_text(home/"runs/LATEST_P13_S2_K6_RUN.txt",str(s2run.relative_to(root))+"\n"); _update_context(root,summary)
    print(f"[P13-S2-K6] OVERALL_STATUS=PASS rows=1955 elapsed={time.perf_counter()-t0:.1f}s",flush=True); print(f"[P13-S2-K6] NEXT_ACTION={cfg['next_action_on_pass']}",flush=True); return 0


def main()->int:
    ap=argparse.ArgumentParser(); ap.add_argument("--project-root",required=True); args=ap.parse_args(); return run(Path(args.project_root))

if __name__=="__main__": raise SystemExit(main())
