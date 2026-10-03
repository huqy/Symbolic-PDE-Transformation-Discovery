from __future__ import annotations
import argparse, hashlib, json, os, platform, sys, time
from pathlib import Path
from typing import Any


def loadj(p:Path)->Any: return json.loads(p.read_text(encoding='utf-8'))
def writej(p:Path,x:Any): p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(x,sort_keys=True,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
def writet(p:Path,s:str): p.parent.mkdir(parents=True,exist_ok=True); p.write_text(s,encoding='utf-8')
def sha(p:Path)->str:
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1<<20),b''): h.update(b)
    return h.hexdigest()
def canon(x:Any)->bytes: return json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
def count_jsonl(p:Path)->int:
    with p.open('rb') as f:return sum(1 for l in f if l.strip())
def ids_jsonl(p:Path)->list[str]:
    out=[]
    with p.open(encoding='utf-8') as f:
        for line in f:
            if line.strip(): out.append(json.loads(line)['scientific_branch_id'])
    return out
def resolve_marker(root:Path,rel:str)->Path:
    m=root/rel
    if not m.is_file(): raise FileNotFoundError(m)
    p=root/m.read_text().strip()
    if not p.exists(): raise FileNotFoundError(p)
    return p

def verify(root:Path,cfg:dict[str,Any]):
    checks={}; s2=resolve_marker(root,cfg['k6_run_marker']); k6=s2/'K6_post_response_diagnostics'; k5=s2/'K5_complete_candidate_response_certification'; k2=s2/'K2_III_B_adjudication_response_entry_lock'
    checks['K6_status']=(s2/'K6_OVERALL_STATUS.txt').is_file() and (s2/'K6_OVERALL_STATUS.txt').read_text().strip()=='PASS'
    checks['K6_next']=(s2/'K6_NEXT_ACTION.txt').is_file() and (s2/'K6_NEXT_ACTION.txt').read_text().strip()==cfg['expected_k6_next_action']
    ks=loadj(k6/'K6_SCIENTIFIC_SUMMARY.json'); kg=loadj(k6/'K6_DATA_BOUNDARY_GUARD.json'); kd=loadj(k6/'K6_SEMANTIC_OUTPUT_DIGEST.json')
    checks['K6_summary']=ks.get('OVERALL_STATUS')=='PASS' and ks.get('diagnostic_rows')==1955 and ks.get('K5_decisions_immutable') is True and ks.get('K5_response_pass_membership_immutable') is True and ks.get('all_K5_RESPONSE_PASS') is True
    checks['K6_guard']=kg.get('SEALED_FINAL_COEF')=='SEALED_COMMITTED_UNOPENED' and kg.get('SEALED_FINAL_RESPONSE')=='SEALED_COMMITTED_UNOPENED' and kg.get('K5_decision_map_modified') is False and kg.get('K5_pass_membership_modified') is False and kg.get('top_k_or_proxy_filter') is False and kg.get('weighted_composite_score') is False and kg.get('new_response_threshold') is False and kg.get('diagnostics_have_membership_authority') is False
    checks['K6_semantic_internal']=kd.get('semantic_output_digest')==ks.get('semantic_output_digest')
    bp=root/ks['branch_diagnostics']; checks['K6_branch_diag_sha']=bp.is_file() and sha(bp)==ks.get('branch_diagnostics_sha256') and count_jsonl(bp)==1955
    k5s=loadj(k5/'K5_SCIENTIFIC_SUMMARY.json'); checks['K5_counts']=k5s.get('decision_counts')==cfg['expected_k5_counts'] and k5s.get('complete_response_cohort')==1955 and k5s.get('final_fidelity_pair')==cfg['expected_k5_final_pair']
    k5pass=root/k5s['response_pass_membership']; checks['K5_pass_sha']=k5pass.is_file() and sha(k5pass)==cfg['expected_k5_pass_membership_sha256'] and count_jsonl(k5pass)==1955
    controls=loadj(k5/'K5_FINAL_CONTROL_RESULTS.json').get('controls',{}); checks['discriminative_controls']=controls.get('identity',{}).get('decision')=='RESPONSE_FAIL' and controls.get('frozen_null',{}).get('decision')=='RESPONSE_FAIL'
    k2s=loadj(k2/'K2_SCIENTIFIC_SUMMARY.json'); checks['K2_counts']=k2s.get('complete_census')==2307 and k2s.get('decision_counts')==cfg['expected_k2_counts']
    k2pass=root/k2s['response_eligible_membership']; checks['K2_K5_same_ids']=k2pass.is_file() and count_jsonl(k2pass)==1955 and ids_jsonl(k2pass)==ids_jsonl(k5pass)
    s1=root/'phases/p13/coefficient_law_raw_xt/runs/p13_s1_formal_search_20260827T200612Z'; clear=s1/'K2A_FROZEN_FULL_CLEAR_MEMBERSHIP.jsonl'; unr=s1/'K2A_FROZEN_FULL_UNRESOLVED_MEMBERSHIP.jsonl'; checks['S1_counts']=count_jsonl(clear)==cfg['expected_s1_clear'] and count_jsonl(unr)==cfg['expected_s1_unresolved']
    return s2,k6,k5,k2,ks,k5s,k2s,{'status':'PASS' if all(checks.values()) else 'FAIL','checks':checks}

def final_context(summary:dict[str,Any], k6sem:str)->str:
    return f'''# P13 Comprehensive Context — S2 Final Freeze\n\n**Project:** P13 Constructive Coefficient-Law Discovery  \n**Artifact role:** FINAL S2 SCIENTIFIC FREEZE / ACTIVE until S3 is explicitly authorized  \n**S2 K7 semantic digest:** `{summary['semantic_output_digest']}`  \n**K6 diagnostic digest:** `{k6sem}`  \n**SEALED_FINAL coefficient/response:** UNOPENED  \n\n## Frozen claim hierarchy\n\n- **III-A TRAIN constructive operator discovery:** SUPPORTED. S1 clear cohort = 2307; 80 S1 numerical-boundary branches remain REFERENCE/UNRESOLVED.\n- **III-B zero-shot DEVELOPMENT operator transfer:** SUPPORTED. 1955 PASS / 4 UNRESOLVED / 348 FAIL, same AST + same theta + same deterministic gauge + zero refit.\n- **III-C DEVELOPMENT physical response:** SUPPORTED. The complete 1955 III-B PASS cohort is 1955 RESPONSE_PASS / 0 UNRESOLVED / 0 FAIL under the frozen 15% uncertainty semantics. Identity and frozen NULL are RESPONSE_FAIL, so the DEVELOPMENT absolute gate is discriminative.\n- **III-D SEALED joint generalization:** UNTESTED / SEALED.\n\n## Membership governance\n\nThe formal S2 certified scientific cohort is all 1955 K5 RESPONSE_PASS branches. K6 diagnostics have zero membership authority. No top-k, Pareto, percentile, response-rank, PF0, operator-score, or target-survivor rule may narrow this frozen set inside the current branch. Exact equivalence may share execution only.\n\n## S3 boundary\n\nS3 is scientifically recommended because III-C is supported under a discriminative DEVELOPMENT gate, but opening SEALED_FINAL is a separate data-boundary action and remains BLOCKED pending explicit user authorization. If S3 is authorized, the current protocol requires the same frozen laws and every formally eligible branch; DEVELOPMENT outcomes may not be used to create a new shortlist.\n'''

def s3_handoff(summary:dict[str,Any])->str:
    return f'''# P13 S3 Decision Handoff after S2 Freeze\n\n**Decision:** `{summary['s3_decision']}`  \n**Formal S3-eligible cohort if authorized:** 1955 frozen S2 RESPONSE_PASS scientific branches.  \n**SEALED state:** coefficient and response remain unopened.\n\n## Non-negotiable entry rules\n\n1. No top-k/Pareto/percentile/DEVELOPMENT-response ranking may narrow the 1955-member formal cohort.\n2. Same S1 AST + same theta + same deterministic gauge + zero refit.\n3. Identity + frozen NULL + every formally eligible candidate branch use the complete SEALED bank.\n4. The 15% response criterion remains the historical preregistered gate for this branch; it may not be tightened after DEVELOPMENT results.\n5. Any future 10% threshold, stronger heterogeneity, larger chi, larger grammar/search budget, or response-relative hard gate is a new preregistered branch with fresh DEVELOPMENT/SEALED commitments.\n6. SEALED opening requires explicit user authorization after review of the K7 freeze.\n'''

def run(root:Path)->int:
    t0=time.perf_counter(); root=root.resolve(); home=root/'phases/p13/coefficient_law_raw_xt'; cfgp=home/'configs/p13_s2_k7_protocol.json'; cfg=loadj(cfgp)
    s2,k6,k5,k2,k6s,k5s,k2s,entry=verify(root,cfg); k7=s2/'K7_formal_s2_freeze'; k7.mkdir(exist_ok=True); writej(k7/'K7_ENTRY_AND_K6_REVIEW.json',entry)
    if entry['status']!='PASS':
        writet(s2/'K7_OVERALL_STATUS.txt','FAIL\n'); writet(s2/'K7_NEXT_ACTION.txt','BLOCK_P13_S2_K7_PARENT_EVIDENCE_REPAIR\n'); return 2
    frozen={'III_A':{'status':'SUPPORTED','S1_clear':2307,'S1_unresolved_reference':80},'III_B':{'status':'SUPPORTED_DEVELOPMENT','PASS':1955,'UNRESOLVED':4,'FAIL':348},'III_C':{'status':'SUPPORTED_DEVELOPMENT','RESPONSE_PASS':1955,'RESPONSE_UNRESOLVED':0,'RESPONSE_FAIL':0,'absolute_gate':'DISCRIMINATIVE'},'III_D':{'status':'UNTESTED_SEALED'}}; writej(k7/'K7_CLAIM_HIERARCHY_FREEZE.json',frozen)
    s3={'decision':cfg['s3_decision_on_clean_s2'],'eligible_count_if_authorized':1955,'eligible_membership':k5s['response_pass_membership'],'eligible_membership_sha256':k5s['response_pass_membership_sha256'],'SEALED_FINAL_COEF':'SEALED_COMMITTED_UNOPENED','SEALED_FINAL_RESPONSE':'SEALED_COMMITTED_UNOPENED','development_based_shortlist_forbidden':True,'top_k_forbidden_as_formal_membership':True,'opening_performed':False}; writej(k7/'K7_S3_DECISION_LOCK.json',s3)
    guard={'K5_decision_map_modified':False,'K5_pass_membership_modified':False,'K6_diagnostics_membership_authority':False,'top_k_or_pareto_formal_selection':False,'new_threshold':False,'response_aware_refit':False,'SEALED_FINAL_COEF':'SEALED_COMMITTED_UNOPENED','SEALED_FINAL_RESPONSE':'SEALED_COMMITTED_UNOPENED','S3_opening_performed':False}; writej(k7/'K7_DATA_BOUNDARY_GUARD.json',guard)
    basis={'K6_semantic_output_digest':k6s['semantic_output_digest'],'K5_semantic_output_digest':k5s['semantic_output_digest'],'K5_pass_membership_sha256':k5s['response_pass_membership_sha256'],'K2_semantic_output_digest':k2s['semantic_output_digest'],'claim_hierarchy':frozen,'s3_decision':s3,'guard':guard}
    sem=hashlib.sha256(canon(basis)).hexdigest(); writej(k7/'K7_SEMANTIC_OUTPUT_DIGEST.json',{'semantic_output_digest':sem,'basis':basis})
    summary={'OVERALL_STATUS':'PASS','NEXT_ACTION':cfg['next_action_on_pass'],'semantic_output_digest':sem,'K6_semantic_output_digest':k6s['semantic_output_digest'],'formal_S2_certified_cohort':1955,'formal_S2_certified_membership':k5s['response_pass_membership'],'formal_S2_certified_membership_sha256':k5s['response_pass_membership_sha256'],'III_A':'SUPPORTED','III_B':'SUPPORTED_DEVELOPMENT','III_C':'SUPPORTED_DEVELOPMENT','III_D':'UNTESTED_SEALED','s3_decision':cfg['s3_decision_on_clean_s2'],'SEALED_opened':False}; writej(k7/'K7_S2_FINAL_SCIENTIFIC_SUMMARY.json',summary)
    writet(root/'P13_COMPREHENSIVE_CONTEXT_S2_FINAL_FREEZE_20260831.md',final_context(summary,k6s['semantic_output_digest']))
    writet(root/'P13_S3_ENTRY_DECISION_AFTER_S2_20260831.md',s3_handoff(summary))
    rp=root/'P13_S2_ROLLING_CONTEXT.md'
    if rp.is_file():
        marker='<!-- S2_K7_FORMAL_FREEZE -->'; block=f"{marker}\n## S2-K7 — formal S2 freeze\n\n- `OVERALL_STATUS`: **PASS**\n- formal S2 certified cohort: `1955`\n- III-A/B/C: `SUPPORTED / SUPPORTED_DEVELOPMENT / SUPPORTED_DEVELOPMENT`\n- III-D: `UNTESTED_SEALED`\n- S3 decision: `{cfg['s3_decision_on_clean_s2']}`\n- SEALED opened: `False`\n- semantic output digest: `{sem}`\n- next action: `{cfg['next_action_on_pass']}`\n"
        txt=rp.read_text(); txt=txt.split(marker)[0].rstrip()+'\n\n'+block if marker in txt else txt.rstrip()+'\n\n'+block; writet(rp,txt)
    writet(s2/'K7_OVERALL_STATUS.txt','PASS\n'); writet(s2/'K7_NEXT_ACTION.txt',cfg['next_action_on_pass']+'\n'); writet(home/'runs/LATEST_P13_S2_K7_RUN.txt',str(s2.relative_to(root))+'\n')
    print(f"[P13-S2-K7] OVERALL_STATUS=PASS certified=1955 semantic={sem}",flush=True); print(f"[P13-S2-K7] S3_DECISION={cfg['s3_decision_on_clean_s2']}",flush=True); print(f"[P13-S2-K7] NEXT_ACTION={cfg['next_action_on_pass']}",flush=True); return 0

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--project-root',required=True); a=ap.parse_args(); return run(Path(a.project_root))
if __name__=='__main__': raise SystemExit(main())
