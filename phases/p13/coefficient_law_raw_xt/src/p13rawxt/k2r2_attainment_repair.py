from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import sys
import time
import warnings
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from reproduce import s0_contract as s0
from typing import Any

import numpy as np

from .calibration_instruments import (
    build_identity_pair, build_null_capacity_pair, build_full_capacity_pair,
    production_fit, best_f4, parameter_names_for_pair,
)
from .family_evaluator import load_field_views
from .reference_optimizer_v2 import reference_launch_v2, adjudicate_independent_launches
from .k2_qualification import (
    add_source_paths, canonical_json_bytes, sha256_bytes, sha256_path, write_json, write_text,
    json_load, derive_seed, _evaluator, _worker_init, run_parallel_tasks,
    evaluator_fidelity_gate, causal_response_gate, build_authoritative_evidence_manifest,
    _worker_proposal_task,
)

EXPECTED_K2_SEMANTIC="ab229cfe44bb7d8cc58cdc4b804a2ef9ab9dcf897888dbbc99bf1b6ffe398d53"
_WORKER:dict[str,Any]={}


def _read_jsonl(path:Path)->list[dict[str,Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _atomic_json(path:Path,obj:Any)->None:
    write_json(path,obj)


def _verify_parent_k2(root:Path,r2:dict[str,Any])->tuple[Path,Path,dict[str,Any],dict[str,Any]]:
    if s0.active(root): return s0.repair_parent(root, "K2R2")
    marker=root/r2["parent_k2"]["marker"]
    if not marker.is_file(): raise FileNotFoundError(marker)
    text=marker.read_text().strip(); k2=Path(text)
    if not k2.is_absolute(): k2=root/text
    if not k2.is_dir(): raise FileNotFoundError(k2)
    sem=json_load(k2/"semantic_output_digest.json")
    summary=json_load(k2/"audit_summary.json")
    checks={
        "semantic":sem.get("semantic_output_digest")==r2["parent_k2"]["expected_semantic_output_digest"]==EXPECTED_K2_SEMANTIC,
        "status":(k2/"OVERALL_STATUS.txt").read_text().strip()==r2["parent_k2"]["expected_overall_status"],
        "next":(k2/"NEXT_ACTION.txt").read_text().strip()==r2["parent_k2"]["expected_next_action"],
        "failures":set(r2["parent_k2"]["required_failure_classifications"]).issubset(set(summary.get("failure_classifications",[]))),
        "leakage":json_load(k2/"no_leakage_guard.json").get("status")=="PASS",
        "formal_search_disabled":not bool(summary.get("formal_S1_search_authorized",True)),
    }
    if not all(checks.values()): raise RuntimeError(f"K2R2 parent K2 verification failed: {checks}")
    k1_text=summary["parent_K1_run"]; k1=Path(k1_text)
    if not k1.is_absolute(): k1=root/k1_text
    if not k1.is_dir(): raise FileNotFoundError(k1)
    return k2,k1,summary,{"status":"PASS","checks":checks,"K2_run":str(k2.relative_to(root)),"K1_run":str(k1.relative_to(root)),"K2_semantic":sem["semantic_output_digest"]}


def _load_base_k2_protocol(root:Path,k2:Path)->dict[str,Any]:
    path=root/"phases/p13/coefficient_law_raw_xt/configs/p13_s0_k2_protocol.json"
    source=json_load(k2/"source_manifest.json")
    expected=next((r["sha256"] for r in source.get("files",[]) if r["path"]=="phases/p13/coefficient_law_raw_xt/configs/p13_s0_k2_protocol.json"),None)
    if expected is None or sha256_path(path)!=expected:
        raise RuntimeError("Base K2 protocol SHA mismatch against authoritative K2 source manifest")
    return json_load(path)


def _reference_worker_init(root_s:str,k1_s:str,base_protocol:dict[str,Any],grid:int):
    root=Path(root_s); add_source_paths(root)
    global _WORKER
    _WORKER={"root":root,"k1":Path(k1_s),"base":base_protocol,"fields":load_field_views(Path(k1_s),"CALIBRATION_COEF",grid)}


def _worker_reference_task(task:dict[str,Any])->dict[str,Any]:
    t0=time.perf_counter(); base=_WORKER["base"]; ev=_evaluator(base,_WORKER["fields"])
    try:
        raw=reference_launch_v2(
            task["pair"],ev,parameter_names=task["parameter_names"],production_best=task.get("production_best"),
            launch_seed=int(task["seed"]),call_budget=int(task["call_budget"]),protocol=task["reference_protocol"],
            progress_label=task.get("progress_label"),progress_every_seconds=float(task.get("progress_every_seconds",60.0)),
        )
        return {"index":int(task["index"]),"object_id":task["object_id"],"launch_index":int(task["launch_index"]),"level":task["level"],"completed_calls":int(raw["completed_calls"]),"best":raw.get("best"),"F4_count":int(raw.get("F4_count",0)),"worker_elapsed_seconds":time.perf_counter()-t0}
    except Exception as exc:
        return {"index":int(task["index"]),"object_id":task["object_id"],"launch_index":int(task["launch_index"]),"level":task["level"],"fatal_exception_type":type(exc).__name__,"fatal_exception":str(exc),"worker_elapsed_seconds":time.perf_counter()-t0}


def _reference_tasks(objects:list[dict[str,Any]],level:dict[str,Any],r2:dict[str,Any])->list[dict[str,Any]]:
    ref=r2["reference_optimizer_v2"]; launches=int(ref["independent_launches"]); tasks=[]; idx=0
    for obj_index,obj in enumerate(objects):
        p=len(obj["parameter_names"]); budget=int(level["calls_by_parameter_count"][str(p)])
        for li in range(launches):
            tasks.append({
                "index":idx,"object_id":obj["object_id"],"pair":obj["pair"],"parameter_names":obj["parameter_names"],
                "production_best":obj.get("production_best"),"launch_index":li,"level":level["level"],"call_budget":budget,
                "seed":derive_seed(f"P13-S0-K2R2-REFV2-{level['level']}-{obj['object_id']}",li),
                "reference_protocol":ref,"progress_label":obj.get("progress_label"),
                "progress_every_seconds":float(r2["runtime"]["progress_every_seconds"]),
            }); idx+=1
    return tasks


def _adjudicate_objects(objects:list[dict[str,Any]],rows:list[dict[str,Any]],r2:dict[str,Any])->dict[str,dict[str,Any]]:
    out={}; agreement=float(r2["reference_optimizer_v2"]["launch_relative_agreement_max"])
    for obj in objects:
        rr=[r for r in rows if r.get("object_id")==obj["object_id"]]
        adj=adjudicate_independent_launches(rr,agreement)
        out[obj["object_id"]]=adj
    return out


def capacity_reference_repair(root:Path,k1:Path,base:dict[str,Any],r2:dict[str,Any],workers:int,run:Path)->tuple[dict[str,Any],dict[str,Any]]:
    f33=load_field_views(k1,"CALIBRATION_COEF",33); f65=load_field_views(k1,"CALIBRATION_COEF",65)
    ev33=_evaluator(base,f33); ev65=_evaluator(base,f65); caps=base["caps"]
    pairs={"identity":build_identity_pair(caps),"null_capacity":build_null_capacity_pair(caps),"full_capacity":build_full_capacity_pair(caps)}
    prod={}
    for name in ["null_capacity","full_capacity"]:
        fit=production_fit(pairs[name],ev33,base["fitter"]["production"]); prod[name]=best_f4(fit)
    objects=[{"object_id":name,"pair":pairs[name],"parameter_names":parameter_names_for_pair(pairs[name]),"production_best":prod[name],"progress_label":f"K2R2 capacity {name}"} for name in ["null_capacity","full_capacity"]]
    selected=None; level_summaries=[]
    for level in r2["reference_optimizer_v2"]["fidelity_ladder"]:
        tasks=_reference_tasks(objects,level,r2)
        rows=run_parallel_tasks(tasks,_worker_reference_task,_reference_worker_init,(str(root),str(k1),base,33),min(workers,len(tasks)),run/f"authoritative/reference_capacity_{level['level']}.jsonl",f"K2R2 ref capacity {level['level']}",int(r2["runtime"]["progress_every_seconds"]))
        adj=_adjudicate_objects(objects,rows,r2); level_summary={"level":level["level"],"objects":adj,"all_qualified":all(adj[x]["qualified"] for x in adj)}; level_summaries.append(level_summary); write_json(run/f"reference_capacity_{level['level']}.json",level_summary)
        if level_summary["all_qualified"]:
            selected=(level,adj); break
    if selected is None:
        return {"status":"FAIL","failure":"REFERENCE_OPTIMIZER_NOT_QUALIFIED_CAPACITY","levels":level_summaries,"capacity_effect_size_adjudication":"NOT_ADJUDICATED"},{}
    level,adj=selected; identity=ev65(pairs["identity"],[]); instruments={}; scores={"identity":identity}
    for name in ["null_capacity","full_capacity"]:
        best=adj[name]["best"]; theta=list(map(float,best["theta_vector"])); score=ev65(pairs[name],theta); scores[name]=score; instruments[name]={"pair":pairs[name],"theta":theta,"role":"CALIBRATION_ONLY","forbidden_from_search":True}
    ji=float(identity["J_princ"]); jn=float(scores["null_capacity"]["J_princ"]); jf=float(scores["full_capacity"]["J_princ"])
    metrics={"J_identity":ji,"J_null":jn,"J_full":jf,"identity_over_full":ji/max(jf,1e-15),"identity_over_null":ji/max(jn,1e-15),"null_over_full":jn/max(jf,1e-15)}
    c=r2["capacity_null"]; gates={"full_improvement":metrics["identity_over_full"]>=float(c["full_identity_improvement_min"]),"null_not_too_good":metrics["identity_over_null"]<=float(c["null_identity_improvement_max"]),"full_vs_null":metrics["null_over_full"]>=float(c["null_over_full_score_min"])}
    return {"status":"PASS" if all(gates.values()) else "FAIL","failure":None if all(gates.values()) else "REGIME_OR_CAPACITY_NOT_CLEAN_AFTER_REFERENCE_REPAIR","selected_reference_level":level["level"],"levels":level_summaries,"metrics":metrics,"gates":gates,"scores":{k:{"J_princ":v.get("J_princ"),"highest_feasibility_level":v.get("highest_feasibility_level")} for k,v in scores.items()},"reference_optimizer_role":"CALIBRATION_ONLY"},instruments


def _reconstruct_fitter_objects(root:Path,k1:Path,k2:Path,base:dict[str,Any])->tuple[list[dict[str,Any]],dict[str,Any]]:
    add_source_paths(root)
    from p11rawxt_s1.k2_ast_runtime import random_pair
    membership=json_load(k2/"authoritative/fitter_fresh_prior_membership.json")
    old_rows={int(r["index"]):r for r in _read_jsonl(k2/"authoritative/fitter_results.jsonl")}
    rng=np.random.default_rng(derive_seed(base["fitter"]["seed_namespace"],0)); objects=[]; hash_checks=[]
    for i in range(int(base["fitter"]["fresh_prior_skeleton_count"])):
        pair=random_pair(rng,base["caps"]); expected=membership["rows"][i]["structural_hash"]; hash_checks.append(pair["structural_hash"]==expected)
        old=old_rows[i]; objects.append({"object_id":f"skeleton_{i:03d}","index":i,"pair":pair,"parameter_names":parameter_names_for_pair(pair),"production_best":old.get("production",{}).get("best"),"production":old.get("production",{})})
    if not all(hash_checks): raise RuntimeError("Frozen K2 fitter membership could not be reproduced by deterministic raw grammar sequence")
    return objects,{"membership_count":len(objects),"all_structural_hashes_match":True,"source_membership":"K2 authoritative/fitter_fresh_prior_membership.json","source_production":"K2 authoritative/fitter_results.jsonl"}


def fitter_reference_repair(root:Path,k1:Path,k2:Path,base:dict[str,Any],r2:dict[str,Any],workers:int,run:Path)->dict[str,Any]:
    objects,membership=_reconstruct_fitter_objects(root,k1,k2,base); selected=None; levels=[]
    for level in r2["reference_optimizer_v2"]["fidelity_ladder"]:
        tasks=_reference_tasks(objects,level,r2)
        rows=run_parallel_tasks(tasks,_worker_reference_task,_reference_worker_init,(str(root),str(k1),base,33),workers,run/f"authoritative/reference_fitter_{level['level']}.jsonl",f"K2R2 ref fitter {level['level']}",int(r2["runtime"]["progress_every_seconds"]))
        adj=_adjudicate_objects(objects,rows,r2)
        reference_qualified=sum(bool(adj[o["object_id"]]["qualified"]) for o in objects)
        both=[]
        for o in objects:
            a=adj[o["object_id"]]; pb=o.get("production_best")
            if a["qualified"] and a.get("best") is not None and pb is not None and pb.get("J_princ") is not None:
                both.append(float(pb["J_princ"])/max(float(a["best"]["J_princ"]),1e-15))
        ls={"level":level["level"],"reference_qualified":reference_qualified,"R_fit_resolved":len(both),"minimum_required":int(r2["fitter"]["minimum_reference_qualified"])}; levels.append(ls); write_json(run/f"reference_fitter_{level['level']}_summary.json",ls)
        if len(both)>=int(r2["fitter"]["minimum_reference_qualified"]): selected=(level,adj,both); break
    if selected is None:
        return {"status":"FAIL","failure":"REFERENCE_OPTIMIZER_NOT_QUALIFIED_FITTER_COHORT","membership":membership,"levels":levels,"production_fitter_adjudication":"NOT_ADJUDICATED"}
    level,adj,ratios=selected; ratios_arr=np.asarray(ratios,float); median=float(np.median(ratios_arr)); p90=float(np.quantile(ratios_arr,0.9))
    refq=sum(bool(adj[o["object_id"]]["qualified"]) for o in objects); prod_missing=sum(bool(adj[o["object_id"]]["qualified"]) and o.get("production_best") is None for o in objects)
    gates={"R_fit_resolved_count":len(ratios)>=int(r2["fitter"]["minimum_reference_qualified"]),"median_R_fit":median<=float(r2["fitter"]["median_R_fit_max"]),"p90_R_fit":p90<=float(r2["fitter"]["p90_R_fit_max"])}
    return {"status":"PASS" if all(gates.values()) else "FAIL","failure":None if all(gates.values()) else "FITTER_NOT_QUALIFIED_AFTER_REFERENCE_REPAIR","selected_reference_level":level["level"],"membership":membership,"levels":levels,"reference_qualified":refq,"R_fit_resolved":len(ratios),"production_no_F4_given_reference_F4":prod_missing,"median_R_fit":median,"p90_R_fit":p90,"gates":gates,"production_fitter_changed":False}


def _burnin_worker_init(root_s:str,k1_s:str,base:dict[str,Any]):
    root=Path(root_s); add_source_paths(root)
    global _WORKER
    _WORKER={"root":root,"k1":Path(k1_s),"base":base,"fields":load_field_views(Path(k1_s),"CALIBRATION_COEF",33)}


def _burnin_worker(task:dict[str,Any])->dict[str,Any]:
    t0=time.perf_counter(); base=_WORKER["base"]; ev=_evaluator(base,_WORKER["fields"])
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            fit=production_fit(task["pair"],ev,base["fitter"]["production"])
        from p11rawxt_s1.k2_search import _branch_representative
        rep=_branch_representative(fit); bf=best_f4(fit)
        return {"task_index":int(task["task_index"]),"island":int(task["island"]),"pair":task["pair"],"lineage":task["lineage"],"proposal_attempts":int(task.get("proposal_attempts",1)),"generator_rejections":int(task.get("generator_rejections",0)),"completed_calls":int(fit.get("completed_calls",0)),"representative":rep,"best_f4":bf,"F4_branch_count":int(fit.get("all_F4_probe_count",0)),"warning_count":len(caught),"worker_elapsed_seconds":time.perf_counter()-t0}
    except Exception as exc:
        return {"task_index":int(task["task_index"]),"island":int(task["island"]),"fatal_exception_type":type(exc).__name__,"fatal_exception":str(exc),"worker_elapsed_seconds":time.perf_counter()-t0}


def _truncate(path:Path,offset:int)->None:
    if not path.exists(): return
    with path.open("r+b") as f:f.truncate(int(offset))


def common_burnin(root:Path,k1:Path,base:dict[str,Any],r2:dict[str,Any],workers:int,run:Path)->dict[str,Any]:
    add_source_paths(root)
    from p11rawxt_s1.k2_search import _generate_task,_nondominance_view,_migrate_ring
    cfg=r2["common_burnin"]; gp=cfg["gp"]; total=int(cfg["structural_proposal_budget"]); batch_size=int(cfg["batch_size"])
    ledger=run/"authoritative/common_burnin_results.jsonl"; checkpoint=run/"authoritative/common_burnin_checkpoint.json"; pool_path=run/"authoritative/common_burnin_parent_pool.json"
    seed=derive_seed(cfg["seed_namespace"],0); rng=np.random.default_rng(seed); islands=[[] for _ in range(int(gp["island_count"]))]; duplicate_hashes=set(); gp_completed=[0 for _ in islands]; next_task=0; completed=0; batches=0; first_f4=[]; f4_hashes=set(); counters=Counter(); committed_offset=0
    resume_completed=0
    if checkpoint.is_file():
        state=json_load(checkpoint); rng.bit_generator.state=state["rng_state"]; islands=state["islands"]; duplicate_hashes=set(state["duplicate_hashes"]); gp_completed=list(map(int,state["gp_completed_by_island"])); next_task=int(state["next_task_index"]); completed=int(state["completed_structural_proposals"]); resume_completed=completed; batches=int(state["completed_batches"]); first_f4=state["first_F4_parents"]; f4_hashes=set(state["F4_structural_hashes"]); counters=Counter(state["counters"]); committed_offset=int(state["ledger_byte_offset"]); _truncate(ledger,committed_offset)
    ledger.parent.mkdir(parents=True,exist_ok=True); mode="a" if completed else "w"; start=time.perf_counter(); last=start
    ctx={"initial_complete_skeleton_target_per_island":int(gp["initial_complete_skeleton_target_per_island"]),"island_count":int(gp["island_count"]),"offspring_mix":gp["offspring_mix"],"migration":gp["migration"]}
    with ledger.open(mode,encoding="utf-8") as out, ProcessPoolExecutor(max_workers=workers,initializer=_burnin_worker_init,initargs=(str(root),str(k1),base)) as ex:
        while completed<total:
            batch=[]
            while len(batch)<batch_size and next_task<total:
                task,dup=_generate_task(rng,next_task,"gp",islands,base["caps"],ctx,duplicate_hashes,gp_completed); counters["duplicate_cache_hits"]+=int(dup); counters["generator_rejections"]+=int(task.get("generator_rejections",0)); batch.append(task); next_task+=1
            futures=[ex.submit(_burnin_worker,t) for t in batch]; results=[f.result() for f in futures]
            for row in sorted(results,key=lambda x:int(x["task_index"])):
                completed+=1; counters["completed_calls"]+=int(row.get("completed_calls",0)); counters["worker_cpu_millis"]+=int(round(1000*float(row.get("worker_elapsed_seconds",0.0))))
                if row.get("fatal_exception"):
                    counters["fatal_exception_count"]+=1
                else:
                    island=int(row["island"]); gp_completed[island]+=1
                    rep=row.get("representative")
                    if rep is not None:
                        islands[island].append({"pair":row["pair"],"representative":rep}); islands[island]=_nondominance_view(islands[island])
                    bf=row.get("best_f4")
                    if bf is not None:
                        h=row["pair"]["structural_hash"]
                        if h not in f4_hashes:
                            f4_hashes.add(h); counters["F4_unique_skeletons"]+=1
                            if len(first_f4)<int(cfg["parent_count"]):
                                first_f4.append({"parent_index":len(first_f4),"task_index":int(row["task_index"]),"pair":row["pair"],"theta_vector":bf["theta_vector"],"J_parent":bf["J_princ"],"J_XT":bf.get("J_XT"),"J_XX":bf.get("J_XX"),"membership_reason":"chronologically_first_unique_F4_structural_skeleton"})
                    counters["F4_branch_emissions"]+=int(row.get("F4_branch_count",0)); counters["warning_count"]+=int(row.get("warning_count",0))
                compact={k:v for k,v in row.items() if k not in {"pair","representative","best_f4"}}; compact["structural_hash"]=row.get("pair",{}).get("structural_hash"); compact["F4"]=row.get("best_f4") is not None; out.write(json.dumps(compact,sort_keys=True)+"\n")
            batches+=1
            mig=gp.get("migration",{}); interval=int(mig.get("interval_completed_batches",0))
            if interval>0 and batches%interval==0:
                events=_migrate_ring(islands,int(mig.get("records_per_island",0))); counters["migration_events"]+=1 if events else 0; counters["migration_records"]+=len(events)
            out.flush(); os.fsync(out.fileno())
            if batches%int(r2["runtime"]["checkpoint_every_batches"])==0 or completed>=total:
                state={"schema":"P13_K2R2_COMMON_BURNIN_CHECKPOINT_V1","rng_state":rng.bit_generator.state,"islands":islands,"duplicate_hashes":sorted(duplicate_hashes),"gp_completed_by_island":gp_completed,"next_task_index":next_task,"completed_structural_proposals":completed,"completed_batches":batches,"first_F4_parents":first_f4,"F4_structural_hashes":sorted(f4_hashes),"counters":dict(counters),"ledger_byte_offset":int(out.tell())}; write_json(checkpoint,state)
            now=time.perf_counter()
            if now-last>=float(r2["runtime"]["progress_every_seconds"]) or completed>=total:
                elapsed=now-start; rate=(completed-resume_completed)/max(elapsed,1e-12); eta=(total-completed)/rate if rate>0 else math.inf
                print(f"[K2R2 common burn-in] processed={completed}/{total} F4_unique={len(f4_hashes)} parents={len(first_f4)}/{cfg['parent_count']} calls={counters['completed_calls']} elapsed={elapsed:.1f}s rate={rate:.3g}/s ETA={eta/3600:.2f}h",flush=True); last=now
    write_json(pool_path,{"schema":"P13_K2R2_COMMON_BURNIN_PARENT_POOL_V1","rows":first_f4,"F4_unique_skeletons":len(f4_hashes),"completed_structural_proposals":completed,"membership_rule":cfg["parent_membership_rule"],"formal_S1_seed_export":False})
    fatal=int(counters["fatal_exception_count"]); enough=len(first_f4)>=int(cfg["parent_count"])
    status="PASS" if fatal==0 and enough else "FAIL"; failure=None if status=="PASS" else ("COMMON_BURNIN_NUMERICAL_UNRESOLVED" if fatal else cfg["failure_if_fewer_than_64"])
    return {"status":status,"failure":failure,"structural_proposal_budget":total,"completed_structural_proposals":completed,"F4_unique_skeletons":len(f4_hashes),"frozen_parent_count":len(first_f4),"required_parent_count":int(cfg["parent_count"]),"counters":dict(counters),"parent_pool":"authoritative/common_burnin_parent_pool.json","result_ledger":"authoritative/common_burnin_results.jsonl","checkpoint":"authoritative/common_burnin_checkpoint.json","scientific_interpretation":"generic production-matched feasibility burn-in; parents are chronological first-F4, never objective top-k; burn-in candidates are calibration-only and ineligible for S1"}


def residual_graft_retest(root:Path,k1:Path,base:dict[str,Any],r2:dict[str,Any],workers:int,run:Path)->dict[str,Any]:
    pool=json_load(run/"authoritative/common_burnin_parent_pool.json")["rows"]; required=int(r2["common_burnin"]["parent_count"])
    if len(pool)<required:return {"status":"NOT_RUN","failure":"COMMON_BURNIN_PARENT_POOL_INSUFFICIENT","residual_graft_adjudication":"NOT_ADJUDICATED"}
    n=int(r2["residual_graft"]["proposal_attempts"]); ns=r2["residual_graft"]["seed_namespace"]
    tasks=[{"index":i,"parent":pool[i%len(pool)],"seed":derive_seed(ns,i+1)} for i in range(n)]
    rows=run_parallel_tasks(tasks,_worker_proposal_task,_worker_init,(str(root),str(k1),base,33),workers,run/"authoritative/v2_proposal_results_k2r2.jsonl","K2R2 V2 proposals",int(r2["runtime"]["progress_every_seconds"]))
    f4=[r for r in rows if r.get("proposal_status")=="F4_CHILD"]; n5=sum(float(r.get("relative_improvement",-math.inf))>=0.05 for r in f4); n20=sum(float(r.get("relative_improvement",-math.inf))>=0.20 for r in f4); fatal=sum(bool(r.get("fatal_exception")) for r in rows)
    gates={"improve_5pct":n5>=int(r2["residual_graft"]["improve_5pct_min_count"]),"improve_20pct":n20>=int(r2["residual_graft"]["improve_20pct_min_count"]),"fatal_count_zero":fatal==0}
    return {"status":"PASS" if all(gates.values()) else "FAIL","failure":None if all(gates.values()) else ("V2_PROPOSAL_NUMERICAL_UNRESOLVED" if fatal else "PROPOSAL_GEOMETRY_NOT_QUALIFIED"),"gates":gates,"attempted_proposals":n,"F4_children":len(f4),"improve_5pct_count":n5,"improve_20pct_count":n20,"fatal_exception_count":fatal,"result_ledger":"authoritative/v2_proposal_results_k2r2.jsonl","residual_graft_adjudication":"ADJUDICATED","parent_source":"K2R2 common burn-in chronological first-64 unique F4"}



def evaluator_fidelity_gate_r2(root:Path,k1:Path,base:dict[str,Any],instruments:dict[str,Any],run:Path)->dict[str,Any]:
    """Original K2 G33/G65 operator-fidelity gate using the K2R2 burn-in F4 cohort.

    This is the same numerical gate as K2; only the source path of the 16 fresh
    calibration maps changes from the failed naked-raw parent sampler to the
    newly qualified common-burnin parent cohort. No objective ranking is used.
    """
    f33=load_field_views(k1,"CALIBRATION_COEF",33); f65=load_field_views(k1,"CALIBRATION_COEF",65)
    ev33=_evaluator(base,f33); ev65=_evaluator(base,f65)
    pairs=[("identity",build_identity_pair(base["caps"]),[])]
    for name in ["null_capacity","full_capacity"]:
        if name in instruments:pairs.append((name,instruments[name]["pair"],instruments[name]["theta"]))
    pool_path=run/"authoritative/common_burnin_parent_pool.json"
    if pool_path.is_file():
        pool=json_load(pool_path)["rows"]
        for row in pool[:int(base["evaluator_fidelity"]["additional_fresh_F4_map_count"])]:
            pairs.append((f"burnin_parent_{row['parent_index']}",row["pair"],row["theta_vector"]))
    rows=[]
    for name,pair,theta in pairs:
        a=ev33(pair,theta); b=ev65(pair,theta); rel=None
        if a.get("J_princ") is not None and b.get("J_princ") is not None:
            rel=abs(float(a["J_princ"])-float(b["J_princ"]))/max(abs(float(b["J_princ"])),1e-14)
        rows.append({"name":name,"G33_stage":a["highest_feasibility_level"],"G65_stage":b["highest_feasibility_level"],"G33_J":a.get("J_princ"),"G65_J":b.get("J_princ"),"relative_J_difference":rel,"G33_elapsed_seconds":a.get("elapsed_seconds"),"G65_elapsed_seconds":b.get("elapsed_seconds"),"G65_lower_order":[x.get("lower_order_diagnostics") for x in b.get("per_field",[])]})
    resolved=[r for r in rows if r["relative_J_difference"] is not None]; maxrel=max([r["relative_J_difference"] for r in resolved],default=math.inf); ceiling=float(base["evaluator_fidelity"]["maximum_relative_J_difference"]); ok=len(resolved)==len(rows) and maxrel<=ceiling
    return {"status":"PASS" if ok else "FAIL","resolved_count":len(resolved),"map_count":len(rows),"maximum_relative_J_difference":maxrel,"ceiling":ceiling,"fresh_map_source":"K2R2 common-burnin chronological F4 cohort","rows":rows}

def _source_manifest(root:Path,paths:list[str])->dict[str,Any]:
    rows=[]
    for rel in paths:
        p=root/rel; rows.append({"path":rel,"sha256":sha256_path(p),"bytes":p.stat().st_size})
    return {"files":rows}


def _evidence_manifest(root:Path,run:Path)->dict[str,Any]:
    names=["authoritative/reference_capacity_R0.jsonl","authoritative/reference_capacity_R1.jsonl","authoritative/reference_capacity_R2.jsonl","authoritative/reference_fitter_R0.jsonl","authoritative/reference_fitter_R1.jsonl","authoritative/reference_fitter_R2.jsonl","authoritative/common_burnin_results.jsonl","authoritative/common_burnin_parent_pool.json","authoritative/v2_proposal_results_k2r2.jsonl"]
    rows=[]
    for name in names:
        p=run/name
        if p.is_file(): rows.append({"path":str(p.relative_to(root)),"sha256":sha256_path(p),"bytes":p.stat().st_size,"line_count":sum(1 for _ in p.open()) if p.suffix==".jsonl" else None})
    return {"large_or_redundant_objects_copied_into_audit":False,"rows":rows}


def _update_context(root:Path,summary:dict[str,Any],semantic:str,run_rel:str)->None:
    path=root/"P13_S0_ROLLING_CONTEXT.md"
    if not path.exists(): return
    marker="## K2R2 formal result"
    block=f'''{marker}\n\n- authoritative run: `{run_rel}`\n- `OVERALL_STATUS`: **{summary['OVERALL_STATUS']}**\n- semantic output digest: `{semantic}`\n- failure classifications: `{summary.get('failure_classifications',[])}`\n- next action: `{summary['NEXT_ACTION']}`\n- formal S1 remains unauthorized; K3 is required before any S1 promotion.\n- DEV/SEALED and OPENED_TRANSFER_DIAGNOSTIC remain unread.\n'''
    text=path.read_text()
    if marker in text:text=text.split(marker)[0].rstrip()+"\n\n"+block
    else:text=text.rstrip()+"\n\n"+block
    write_text(path,text)


def main(argv:list[str]|None=None)->int:
    ap=argparse.ArgumentParser(); ap.add_argument("--project-root",default="."); ap.add_argument("--workers",type=int,default=None); args=ap.parse_args(argv)
    root=Path(args.project_root).resolve(); add_source_paths(root)
    r2_path=root/"phases/p13/coefficient_law_raw_xt/configs/p13_s0_k2r2_protocol.json"; r2=json_load(r2_path); r2_sha=sha256_path(r2_path)
    k2,k1,k2_summary,parent=_verify_parent_k2(root,r2); base=_load_base_k2_protocol(root,k2)
    workers=args.workers or max(1,int(os.environ.get("NSLOTS","17"))-1); workers=max(1,min(int(r2["runtime"]["default_workers"]),workers))
    marker=root/"phases/p13/coefficient_law_raw_xt/runs/LATEST_P13_S0_K2R2_RUN.txt"; run=None
    if marker.is_file():
        p=Path(marker.read_text().strip()); p=p if p.is_absolute() else root/p
        if p.is_dir() and not (p/"OVERALL_STATUS.txt").is_file():run=p
    if run is None:
        stamp=time.strftime("%Y%m%dT%H%M%SZ",time.gmtime()); run=root/f"phases/p13/coefficient_law_raw_xt/runs/p13_s0_k2r2_attainment_repair_{stamp}"; run.mkdir(parents=True); write_text(marker,str(run.relative_to(root))+"\n")
    write_json(run/"parent_k2_inplace_verification.json",parent); start=time.perf_counter(); failure=[]; gates={}
    print(f"P13-S0-K2R2 run={run.relative_to(root)} workers={workers}",flush=True)

    print("[K2R2 1/6] reference optimizer v2 + capacity/null requalification",flush=True)
    cap,instruments=capacity_reference_repair(root,k1,base,r2,workers,run); write_json(run/"capacity_null_requalification.json",cap); gates["capacity_reference_and_separation"]=cap["status"]
    (run/"calibration_only").mkdir(exist_ok=True)
    if instruments: write_json(run/"calibration_only/capacity_instrument_lock_k2r2.json",{"role":"CALIBRATION_ONLY","forbidden_from_search":True,"instrument_hashes":{k:v["pair"]["structural_hash"] for k,v in instruments.items()},"theta":{k:v["theta"] for k,v in instruments.items()}})
    if cap["status"]!="PASS": failure.append(str(cap.get("failure"))); fit={"status":"NOT_RUN","reason":"CAPACITY_REFERENCE_NOT_QUALIFIED"}; burn={"status":"NOT_RUN"}; prop={"status":"NOT_RUN"}
    else:
        print("[K2R2 2/6] frozen 128-skeleton fitter requalification",flush=True)
        fit=fitter_reference_repair(root,k1,k2,base,r2,workers,run); write_json(run/"fitter_requalification.json",fit); gates["fitter"]=fit["status"]
        if fit["status"]!="PASS": failure.append(str(fit.get("failure"))); burn={"status":"NOT_RUN"}; prop={"status":"NOT_RUN"}
        else:
            print("[K2R2 3/6] production-matched common F4 burn-in",flush=True)
            burn=common_burnin(root,k1,base,r2,workers,run); write_json(run/"common_burnin_qualification.json",burn); gates["common_burnin"]=burn["status"]
            if burn["status"]!="PASS": failure.append(str(burn.get("failure"))); prop={"status":"NOT_RUN","residual_graft_adjudication":"NOT_ADJUDICATED"}
            else:
                print("[K2R2 4/6] frozen V2 residual-graft partial-credit retest",flush=True)
                prop=residual_graft_retest(root,k1,base,r2,workers,run); write_json(run/"proposal_geometry_requalification.json",prop); gates["proposal_geometry"]=prop["status"]
                if prop["status"]!="PASS": failure.append(str(prop.get("failure")))
    if not (run/"fitter_requalification.json").is_file(): write_json(run/"fitter_requalification.json",fit)
    if not (run/"common_burnin_qualification.json").is_file(): write_json(run/"common_burnin_qualification.json",burn)
    if not (run/"proposal_geometry_requalification.json").is_file(): write_json(run/"proposal_geometry_requalification.json",prop)

    if not failure:
        print("[K2R2 5/6] original K2 evaluator fidelity + lower-order diagnostics",flush=True)
        ef=evaluator_fidelity_gate_r2(root,k1,base,instruments,run); write_json(run/"evaluator_fidelity_cost.json",ef); gates["evaluator_fidelity"]=ef["status"]
        lod={"status":"PASS","diagnostic_only":True,"rows":[{"name":x["name"],"G65_lower_order":x.get("G65_lower_order")} for x in ef.get("rows",[])]}; write_json(run/"lower_order_diagnostics.json",lod); gates["lower_order_diagnostics"]="PASS"
        if ef["status"]!="PASS": failure.append("NUMERICAL_EVALUATOR_NOT_QUALIFIED")
    else:
        ef={"status":"NOT_RUN","reason":"K2R2_PRECONDITION_FAIL"}; lod={"status":"NOT_RUN","reason":"K2R2_PRECONDITION_FAIL","diagnostic_only":True}; write_json(run/"evaluator_fidelity_cost.json",ef); write_json(run/"lower_order_diagnostics.json",lod)
    if not failure:
        print("[K2R2 6/6] original calibration-only causal response feasibility",flush=True)
        cr=causal_response_gate(root,k1,base,instruments); write_json(run/"causal_response_feasibility.json",cr); gates["causal_response"]=cr["status"]
        if cr["status"]!="PASS":failure.append("CALIBRATION_RESPONSE_SEPARATION_NOT_CLEAN")
    else:
        cr={"status":"NOT_RUN","reason":"K2R2_PRECONDITION_FAIL"}; write_json(run/"causal_response_feasibility.json",cr)

    overall="PASS" if not failure else "FAIL"
    if overall=="PASS":next_action="P13-S0-K3_SCIENTIFIC_ADJUDICATION_AND_FREEZE"
    elif any(x in failure for x in ["REFERENCE_OPTIMIZER_NOT_QUALIFIED_CAPACITY","REFERENCE_OPTIMIZER_NOT_QUALIFIED_FITTER_COHORT","FITTER_NOT_QUALIFIED_AFTER_REFERENCE_REPAIR"]):next_action="P13-S0-K2R2_REFERENCE_FITTER_SCIENTIFIC_DECISION_REQUIRED"
    elif any(x in failure for x in ["COMMON_BURNIN_F4_ATTAINMENT_FAIL","COMMON_BURNIN_NUMERICAL_UNRESOLVED"]):next_action="P13-S0-K2R2_COMMON_BURNIN_SCIENTIFIC_DECISION_REQUIRED"
    else:next_action="P13-S0-K3_FAIL_CLOSED_ADJUDICATION"
    leakage={"status":"PASS","formal_candidate_search_run":False,"burnin_is_calibration_only":True,"burnin_candidates_eligible_for_S1":False,"historical_response_outcomes_read":False,"opened_transfer_diagnostic_read":False,"development_or_sealed_opened":False,"capacity_instrument_used_as_search_information":False,"characteristic_formula_used_as_search_information":False,"top_k_percentile_target_membership":False,"candidate_specific_reference_rescue":False,"response_aware_refit":False}
    write_json(run/"no_leakage_guard.json",leakage)
    summary={"OVERALL_STATUS":overall,"NEXT_ACTION":next_action,"failure_classifications":failure,"gate_statuses":gates,"parent_K2_run":str(k2.relative_to(root)),"parent_K2_semantic":s0.semantic(root, "K2", EXPECTED_K2_SEMANTIC),"parent_K1_run":str(k1.relative_to(root)),"workers":workers,"formal_S1_search_authorized":False,"K3_required_before_S1":True,"authoritative_upstream_data_reused_in_place":True,"audit_archives_used_as_runtime_input":False,"elapsed_seconds":time.perf_counter()-start}
    digest_payload={"failure":failure,"gates":gates,"capacity":cap.get("metrics",{}),"fitter":{k:fit.get(k) for k in ["selected_reference_level","reference_qualified","R_fit_resolved","median_R_fit","p90_R_fit"]},"burnin":{k:burn.get(k) for k in ["F4_unique_skeletons","frozen_parent_count"]},"proposal":{k:prop.get(k) for k in ["F4_children","improve_5pct_count","improve_20pct_count"]},"evaluator":ef.get("maximum_relative_J_difference"),"causal":cr.get("gates")}
    semantic=sha256_bytes(canonical_json_bytes({"parent_K2_semantic":s0.semantic(root, "K2", EXPECTED_K2_SEMANTIC),"protocol_sha256":r2_sha,"result":digest_payload})); summary["semantic_output_digest"]=semantic
    write_json(run/"audit_summary.json",summary); write_json(run/"semantic_output_digest.json",{"stage":"P13-S0-K2R2","semantic_output_digest":semantic,"protocol_sha256":r2_sha,"parent_K2_semantic":s0.semantic(root, "K2", EXPECTED_K2_SEMANTIC)}); write_text(run/"OVERALL_STATUS.txt",overall+"\n"); write_text(run/"NEXT_ACTION.txt",next_action+"\n")
    write_json(run/"authoritative_evidence_manifest.json",_evidence_manifest(root,run))
    source_paths=["phases/p13/coefficient_law_raw_xt/configs/p13_s0_k2r2_protocol.json","phases/p13/coefficient_law_raw_xt/src/p13rawxt/reference_optimizer_v2.py","phases/p13/coefficient_law_raw_xt/src/p13rawxt/k2r2_attainment_repair.py","phases/p13/coefficient_law_raw_xt/src/p13rawxt/proposal_geometry.py","phases/p13/coefficient_law_raw_xt/src/p13rawxt/calibration_instruments.py","phases/p11/raw_xt_td/src/p11rawxt_s1/k2_search.py","phases/p11/raw_xt_td/src/p11rawxt_s1/k2_fitter.py"]
    write_json(run/"source_manifest.json",_source_manifest(root,source_paths)); write_json(run/"runtime_environment.json",{"python":sys.version,"numpy":np.__version__,"platform":platform.platform(),"workers":workers,"NSLOTS":os.environ.get("NSLOTS"),"OMP_NUM_THREADS":os.environ.get("OMP_NUM_THREADS"),"OPENBLAS_NUM_THREADS":os.environ.get("OPENBLAS_NUM_THREADS")})
    write_json(run/"k3_handoff_manifest.json",{"schema":"P13_S0_K2R2_K3_HANDOFF_V1","K1_run":str(k1.relative_to(root)),"K2_run":str(k2.relative_to(root)),"K2R2_run":str(run.relative_to(root)),"K2R2_semantic_output_digest":semantic,"formal_S1_search_authorized":False,"K3_required":True,"immediate_next_action":next_action,"burnin_candidates_eligible_for_S1":False,"calibration_only_capacity_artifact_excluded_from_future_search_inputs":True})
    _update_context(root,summary,semantic,str(run.relative_to(root)))
    print(f"OVERALL_STATUS={overall}",flush=True); print(f"semantic_output_digest={semantic}",flush=True); print(f"NEXT_ACTION={next_action}",flush=True); return 0

if __name__=="__main__": raise SystemExit(main())
