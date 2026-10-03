from __future__ import annotations
import argparse, hashlib, json, os
from pathlib import Path
from typing import Any

EXPECTED_K3 = "0862640a856eed7d0321d8fa0597b96005293c9058836d686914b31f52e01997"
EXPECTED_CLEAR_SHA = "564134db0bee502198729463c64ffe29408b30081ec1af63d86afff0f4da0c84"
EXPECTED_UNRESOLVED_SHA = "4177feffe4417e157f17eb90578b1e749ec06fe0db7935a7ade315c33cbc4f67"
EXPECTED_CLEAR_N = 2307
EXPECTED_UNRESOLVED_N = 80


def loadj(p: Path) -> Any:
    return json.loads(p.read_text())

def writej(p: Path, x: Any) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    t=p.with_suffix(p.suffix+'.tmp'); t.write_text(json.dumps(x,sort_keys=True,indent=2)+'\n'); os.replace(t,p)

def writet(p: Path, s: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    t=p.with_suffix(p.suffix+'.tmp'); t.write_text(s); os.replace(t,p)

def sha(p: Path, block: int=16*1024*1024) -> str:
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(block),b''): h.update(b)
    return h.hexdigest()

def line_count(p: Path) -> int:
    with p.open('rb') as f: return sum(1 for _ in f)

def marker(root: Path, rel: str) -> Path:
    m=root/rel
    if not m.is_file(): raise FileNotFoundError(m)
    q=Path(m.read_text().strip()); q=q if q.is_absolute() else root/q; q=q.resolve()
    if root.resolve() not in q.parents: raise RuntimeError(f'marker escapes project root: {rel}')
    if not q.is_dir(): raise FileNotFoundError(q)
    return q

def obj(root: Path, p: Path, role: str) -> dict[str,Any]:
    return {'path':str(p.relative_to(root)),'bytes':p.stat().st_size,'sha256':sha(p),'role':role}

def canon_digest(x: Any) -> str:
    return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def verify_pf0_outputs(root: Path, run: Path, pf0: Path, summary: dict[str,Any]) -> dict[str,Any]:
    sem=loadj(pf0/'PF0_semantic_output_digest.json')
    gates={}
    gates['PF0_status_PASS']=(run/'PF0_OVERALL_STATUS.txt').read_text().strip()=='PASS'
    gates['PF0_next_action_exact']=(run/'PF0_NEXT_ACTION.txt').read_text().strip()=='P13-S1-PF1_FINAL_STAGE_FREEZE_AND_S2_HANDOFF'
    gates['summary_PASS']=summary.get('OVERALL_STATUS')=='PASS'
    gates['summary_next_exact']=summary.get('NEXT_ACTION')=='P13-S1-PF1_FINAL_STAGE_FREEZE_AND_S2_HANDOFF'
    gates['K3_parent_exact']=summary.get('parent_K3_semantic_digest')==EXPECTED_K3
    gates['membership_counts_exact']=(int(summary.get('membership_clear_count',-1))==EXPECTED_CLEAR_N and int(summary.get('membership_unresolved_count',-1))==EXPECTED_UNRESOLVED_N)
    gates['membership_unchanged']=summary.get('membership_unchanged') is True
    gates['DEV_SEALED_unopened']=summary.get('DEVELOPMENT_or_SEALED_opened') is False
    gates['response_unopened']=summary.get('response_outcomes_opened') is False
    gates['no_search_refit']=summary.get('new_search_or_refit') is False
    gates['K3_immutable']=summary.get('K3_freeze_remains_immutable') is True
    gates['semantic_summary_match']=summary.get('PF0_semantic_output_digest')==sem.get('semantic_output_digest')
    output_ok=True
    for rec in sem.get('outputs',[]):
        p=root/rec['path']
        if not p.is_file() or p.stat().st_size!=int(rec['bytes']) or sha(p)!=rec['sha256']:
            output_ok=False; break
    gates['semantic_output_files_exact']=output_ok
    src=loadj(pf0/'PF0_source_manifest.json'); src_ok=True
    for rec in src.get('files',[]):
        p=root/rec['path']
        if not p.is_file() or p.stat().st_size!=int(rec['bytes']) or sha(p)!=rec['sha256']:
            src_ok=False; break
    gates['PF0_source_manifest_exact']=src_ok
    b=loadj(pf0/'PF0_data_boundary_guard.json')
    gates['boundary_status_PASS']=b.get('status')=='PASS'
    for k in ['K3_base_freeze_mutated','clear_membership_changed','unresolved_membership_changed','DEVELOPMENT_read','SEALED_read','response_outcomes_read','historical_response_read','new_formal_search','PLCP_run','continuation_32768_run','candidate_refit','ASP_or_theory_or_witness_has_membership_authority','audit_archive_used_as_runtime_input']:
        gates[f'boundary_{k}_false']=b.get(k) is False
    m=loadj(pf0/'PF0_membership_immutability.json')
    gates['clear_SHA_count_exact']=m.get('clear_sha256')==EXPECTED_CLEAR_SHA and int(m.get('clear_count',-1))==EXPECTED_CLEAR_N
    gates['unresolved_SHA_count_exact']=m.get('unresolved_sha256')==EXPECTED_UNRESOLVED_SHA and int(m.get('unresolved_count',-1))==EXPECTED_UNRESOLVED_N
    asp=loadj(pf0/'PF0_ASP_aggregate.json')
    gates['ASP_complete_rows']=int(asp.get('rows',-1))==EXPECTED_CLEAR_N
    li=asp.get('lambda_1_integrity_error',{})
    gates['ASP_lambda1_integrity']=int(li.get('n',-1))==EXPECTED_CLEAR_N and float(li.get('max',1.0))<=1e-10
    pred=loadj(pf0/'PF0_PRE_DEV_PREDICTIONS.json')
    gates['predictions_frozen_pre_DEV']=pred.get('frozen_before_DEVELOPMENT') is True and pred.get('membership_unchanged') is True and pred.get('predictions_are_descriptive_not_gates') is True
    if not all(gates.values()):
        bad=[k for k,v in gates.items() if not v]
        raise RuntimeError('PF1 refuses freeze: PF0 audit gates failed: '+','.join(bad))
    return {'gates':gates,'PF0_semantic_digest':sem['semantic_output_digest'],'witness_status':summary.get('matched_TRAIN_witness_status'),'ASP_rows':asp['rows']}

def fmt(x: Any, digits: int=6) -> str:
    if x is None: return 'NA'
    try: return f'{float(x):.{digits}g}'
    except Exception: return str(x)

def write_contexts(root: Path, cfg: dict[str,Any], run: Path, pf1dir: Path, pf0sem: str, final_digest: str) -> None:
    pf0=run/'PF0_postfreeze'
    att=loadj(pf0/'PF0_attainment_summary.json')
    geom=loadj(pf0/'PF0_theory_geometry_aggregate.json')
    wit=loadj(pf0/'PF0_matched_TRAIN_witness.json')
    weps=loadj(pf0/'PF0_witness_epsilon_comparator.json')
    asp=loadj(pf0/'PF0_ASP_aggregate.json')
    pred=loadj(pf0/'PF0_PRE_DEV_PREDICTIONS.json')
    k2a=loadj(run/'K2A_scientific_summary.json')
    k2b=loadj(run/'K2B_diagnostics/K2B_scientific_summary.json')
    k2c=loadj(run/'K2C_theory_bridge/K2C_scientific_summary.json')
    clear=att['clear_formal_ratio']; margin=att['clear_margin_to_tau_boundary']
    rb=geom.get('radial_fraction_of_r_squared',{})
    sratio=geom.get('s_norm_ratio',{})
    gcos=geom.get('G_metric_coefficient_cosine_to_theory',{})
    best=att.get('global_best_FULL_F4') or {}
    bestclear=att.get('global_best_clear') or {}
    v2ratio=att.get('FULL_V2_over_FULL_V1_F4_branch_throughput_ratio')
    witness_ratio=wit.get('best_search_F4_over_witness_G33')
    s1path=root/cfg['contexts']['S1_final']
    s1=f'''# P13 Comprehensive Context — Final S1 Freeze after PF0\n\n**Project:** P13 Constructive Coefficient-Law Discovery  \n**Semantic milestone:** S0+S1 complete; K3 formal-discovery freeze retained; PF0 post-membership addendum frozen  \n**Artifact role:** **FINAL S1 scientific context / REFERENCE after S2 begins**  \n**K3 immutable formal-discovery digest:** `{EXPECTED_K3}`  \n**PF0 semantic digest:** `{pf0sem}`  \n**Final S1/PF1 handoff digest:** `{final_digest}`  \n**S2 ACTIVE cohort:** **2307** clear TRAIN-qualified scientific branches  \n**S1 unresolved lineage:** **80** numerical-boundary branches  \n**DEVELOPMENT/SEALED/response state through S1:** **UNOPENED**  \n**Next authorized action:** `{cfg['next_on_pass']}`\n\n## 1. Final S1 technical conclusion\n\nP13-S1 completed a de-novo response-blind raw finite-local GP-style search using one shared AST and one shared theta vector over six TRAIN coefficient fields. The first formal horizon was 4 paired seeds × 3 arms × 8192 structural proposals = 98,304 proposals. Candidate membership was determined only by the preregistered F0-F4 and TRAIN operator hard gates; no top-k/Pareto/percentile/target-count/response-aware narrowing was used.\n\nThe frozen route remains **Pattern A + Pattern B**: coefficient-enabled FULL search has stable paired benefit over NULL, while the present `additive_root_residual_graft` V2 intervention has no stable demonstrated quality advantage over FULL-V1. V2 nevertheless increases F4 scientific-branch throughput by about `{fmt(v2ratio)}`× relative to V1; that throughput increase did not produce stable best-objective superiority.\n\n## 2. PF0 post-freeze attainment readout\n\nPF0 did not alter membership. For the 2307 clear branches the true formal family-ratio distribution `J_family/J_identity_family` is: min `{fmt(clear.get('min'))}`, p10 `{fmt(clear.get('p10'))}`, median `{fmt(clear.get('median'))}`, p90 `{fmt(clear.get('p90'))}`, max `{fmt(clear.get('max'))}`. The median clear margin to the frozen tau boundary is `{fmt(margin.get('median'))}`. The global best FULL F4 branch has `J_family={fmt(best.get('J_family'))}` and formal ratio `{fmt(best.get('formal_ratio'))}`; the best clear branch formal ratio is `{fmt(bestclear.get('formal_family_ratio_G33'))}`.\n\nThe matched post-freeze theory-informed TRAIN witness has status **`{wit.get('status')}`**. When resolved, `J_best/J_witness` is reported only as **witness-relative attainment**, never as global raw-AST regret. Current value: `{fmt(witness_ratio)}`.\n\n## 3. PF0 theory-space geometry\n\nPF0 evaluated the radial/angular decomposition branch-by-branch rather than combining population medians. Alignment geometry is resolved for `{geom.get('resolved_count')}/{geom.get('clear_count')}` clear branches. Median theory-space norm ratio `s` is `{fmt(sratio.get('median'))}`; median radial fraction of squared tangent mismatch is `{fmt(rb.get('median'))}`, with fraction radial-dominant (`>0.5`) `{fmt(geom.get('fraction_radial_fraction_gt_0p5'))}`. Median Gram-metric coefficient cosine to the Claim-II theory vector is `{fmt(gcos.get('median'))}`.\n\nThis supports only functional-geometry language. Radial mismatch is **not** automatically fitter regret; radial flatness is **not** automatically global identifiability failure.\n\n## 4. PF0 amplitude-sensitivity probe\n\nASP evaluated all 2307 clear branches on the common preregistered lambda grid. Full-grid resolved: `{asp.get('resolved_full_grid')}`; neutral-baseline-domain unresolved: `{asp.get('unresolved_neutral_baseline_domain')}`; other partial-grid unresolved: `{asp.get('unresolved_other_partial_grid')}`. Median radial slack among fully resolved ASP branches is `{fmt((asp.get('radial_slack') or {}).get('median'))}`; fraction whose grid minimum lies at a lambda boundary is `{fmt(asp.get('fraction_minimum_at_grid_boundary'))}`. Domain-unresolved ASP branches remain fully valid S2 members because ASP is diagnostic only.\n\n## 5. Claim hierarchy at final S1 freeze\n\n- **III-A / constructive TRAIN operator discovery:** supported inside the declared local finite-jet regime.\n- **III-B / unseen DEVELOPMENT zero-shot operator transfer:** untested.\n- **III-C / physical response accuracy:** untested.\n- **III-D / SEALED generalization:** untested.\n\nK2B/K2C/PF0 are REFERENCE mechanism evidence only. None can add/remove/rescue S2 candidates.\n\n## 6. Frozen pre-DEVELOPMENT predictions\n\nPF0 predictions remain descriptive, not gates: `{pred.get('H2_transfer_association')}`\n\n## 7. Authoritative artifact policy\n\nScientific authority remains the single S1 run store `{run.relative_to(root)}`. PF1 adds compact manifests and portable recovery copies only. Full proposal/branch/skeleton/equivalence ledgers are not duplicated.\n'''
    writet(s1path,s1)
    s2path=root/cfg['contexts']['S2_entry']
    s2=f'''# P13 Comprehensive Context — S2 Entry after Final S1/PF0 Freeze\n\n**Project:** P13 Constructive Coefficient-Law Discovery  \n**Artifact role:** **ACTIVE canonical S2-entry context**  \n**Final S1/PF1 semantic digest:** `{final_digest}`  \n**Immutable K3 discovery digest:** `{EXPECTED_K3}`  \n**PF0 descriptive digest:** `{pf0sem}`  \n**S2 ACTIVE cohort:** **2307** clear branches; **80** TRAIN unresolved branches remain REFERENCE/not S2 eligible.  \n**DEVELOPMENT coefficient/response:** committed and unopened at entry.  \n**SEALED_FINAL:** committed and unopened.  \n**Authorized next action:** `{cfg['next_on_pass']}`\n\n## 1. Scientific target of S2\n\nS2 asks two stronger prospective questions without modifying the S1 laws: (III-B) zero-refit operator transfer to four previously sealed DEVELOPMENT coefficient fields; and, only if III-B yields a nonempty clear-pass cohort and a separate response protocol is frozen, (III-C) certified physical-response accuracy on the committed DEVELOPMENT response bank.\n\nEvery S2 branch uses the same S1-frozen canonical skeleton, theta, deterministic gauge and fit provenance. DEVELOPMENT outcomes may not reselect a theta branch.\n\n## 2. Top-level S2 K-roadmap\n\nThe former K0A/K0B/K0C notation described only the operator-transfer phase; K0C did **not** complete S2. PF1 promotes the roadmap to sequential top-level K steps for clarity:\n\n### S2-K0 — DEVELOPMENT coefficient opening and protocol lock\nOpen only the four committed DEVELOPMENT coefficient fields. Verify archive SHA/bytes/semantic digest and membership chain; keep DEVELOPMENT response and all SEALED payloads unopened. Explicitly inherit `tau_num=0.005`. Prospectively state that cohort-wide G33/G65 discrepancy beyond the inherited ambiguity semantics triggers a numerical-fidelity repair, never a wider tau. No candidate score is required before the opening lock is frozen.\n\n### S2-K1 — complete zero-shot operator transfer\nEvaluate **all 2307** branches on all four DEVELOPMENT coefficient fields with same AST + same theta + same gauge + zero refit. Frozen III-B hard gate is unchanged: all fields F0-F4 valid/resolved, `J_family_DEV <= 0.5 * J_identity_DEV_family`, and no field clearly worse than its own identity. Report `rho_transfer=(R_DEV/R_TRAIN)` and PF0 radial-metric associations descriptively only.\n\n### S2-K2 — Claim III-B adjudication and response-entry lock\nFreeze PASS / UNRESOLVED / FAIL for every branch. If zero clear PASS, response remains unopened and III-B is negative/unresolved according to the census. If nonempty, the **entire clear PASS cohort** becomes response-eligible; no rank narrowing.\n\n### S2-K3 — response protocol, fidelity and control lock\n**Currently blocked pending explicit protocol confirmation before candidate response.** Freeze the exact causal/source-domain solver ladder, reference uncertainty rule, cohort-wide candidate fidelity escalation, identity/NULL control-first semantics, `NONDISCRIMINATIVE_ABSOLUTE_GATE`, and the consequence that a nondiscriminative DEVELOPMENT response gate keeps the current SEALED holdout unopened for a strong transformation-improvement claim.\n\n### S2-K4 — response reference bank and control-first precheck\nOnly after K3 PASS: open the committed DEVELOPMENT response definitions, build/refine the 32 original-PDE references using candidate-independent self-convergence, then run identity and frozen NULL on the full bank before launching candidate response. If controls make the absolute gate nondiscriminative, freeze that result and pause for compute/claim governance; do not alter the 0.15 gate. Exact execution-equivalence/cost census may be included here and may share execution only.\n\n### S2-K5 — complete candidate response certification\nIf authorized after K4, certify every K2 clear operator-transfer branch on the complete 32-case bank under the K3-frozen fidelity policy. Numerical escalation is cohort-wide; no candidate-specific rescue or score shortlist.\n\n### S2-K6 — post-response mechanism diagnostics\nAfter the K5 decision map is frozen, report TRAIN J, DEVELOPMENT operator J, response error, PF0 radial metrics, theory alignment, lower-order diagnostics, identity and NULL comparisons. No refit or decision rewrite.\n\n### S2-K7 — formal S2 freeze and S3 decision\nFreeze III-A/III-B/III-C separately. SEALED remains unopened unless S3 is scientifically authorized under the K3-frozen non-discrimination policy.\n\n## 3. S2-K0/K1 numerical semantics\n\nInherit `tau_num=0.005`; do not retune it on DEVELOPMENT. Measure actual DEV G33/G65 discrepancy prospectively. If fidelity is cohort-wide insufficient, stop for an explicit numerical repair branch rather than candidate-specific rescue. Large UNRESOLVED counts are a scientific/numerical outcome, not permission to loosen the gate.\n\n## 4. PF0 quantities allowed in S2\n\nPF0 formal TRAIN ratio, theory geometry, witness and ASP may be joined to S2 results only for descriptive association. In particular `rho_transfer` is a secondary estimand; it never replaces the frozen III-B hard gate. PF0 has zero membership authority.\n\n## 5. Response-stage block at S2 entry\n\nS2-K0 through K2 are authorized by this handoff. Candidate physical-response evaluation is **not** authorized until S2-K3 explicitly freezes response fidelity/control semantics. This preserves the agreed `ALLOW OPERATOR TRANSFER, BLOCK RESPONSE UNTIL LOCK` governance.\n\n## 6. Data boundary\n\n- DEVELOPMENT_COEF: may first open in S2-K0.\n- DEVELOPMENT_RESPONSE: stays sealed until response K3 is locked and K4 begins.\n- SEALED_FINAL coefficient/response: remains sealed throughout S2.\n- opened diagnostic/K2B/K2C/PF0 evidence: REFERENCE only.\n'''
    writet(s2path,s2)

def update_rolling(root: Path, cfg: dict[str,Any], digest: str, pf0sem: str) -> None:
    p=root/cfg['contexts']['rolling']
    old=p.read_text() if p.exists() else '# P13 S1 Rolling Execution Context\n'
    mark='<!-- PF1_FINAL_S1_FREEZE -->'
    block=f'''{mark}\n## S1-PF1 — final S0+S1 stage freeze and S2 handoff\n\n- `OVERALL_STATUS`: **PASS**\n- immutable K3 discovery digest: `{EXPECTED_K3}`\n- PF0 descriptive digest: `{pf0sem}`\n- final PF1/S1 handoff digest: `{digest}`\n- final S2 ACTIVE cohort: `2307` clear branches\n- unresolved lineage: `80`\n- DEVELOPMENT/SEALED/response opened through S1: `False`\n- canonical final S1 context: `{cfg['contexts']['S1_final']}`\n- canonical S2-entry context: `{cfg['contexts']['S2_entry']}`\n- next authorized action: `{cfg['next_on_pass']}`\n'''
    if mark in old: old=old.split(mark)[0].rstrip()+'\n\n'
    writet(p,old.rstrip()+'\n\n'+block+'\n')

def run(root: Path) -> int:
    cfgp=root/'phases/p13/coefficient_law_raw_xt/configs/p13_s1_pf1_protocol.json'; cfg=loadj(cfgp)
    runp=marker(root,'phases/p13/coefficient_law_raw_xt/runs/LATEST_P13_S1_PF0_RUN.txt')
    k3run=marker(root,'phases/p13/coefficient_law_raw_xt/runs/LATEST_P13_S1_K3_RUN.txt')
    if runp!=k3run: raise RuntimeError('PF0/K3 markers do not point to same authoritative S1 run')
    run=runp; pf0=run/'PF0_postfreeze'; k3=run/'K3_freeze'
    if (run/'K3_OVERALL_STATUS.txt').read_text().strip()!='PASS': raise RuntimeError('K3 not PASS')
    if loadj(k3/'K3_SEMANTIC_OUTPUT_DIGEST.json').get('semantic_output_digest')!=EXPECTED_K3: raise RuntimeError('K3 semantic mismatch')
    pf0sum=loadj(pf0/'PF0_scientific_summary.json')
    audit=verify_pf0_outputs(root,run,pf0,pf0sum)
    out=run/'PF1_final_freeze'; out.mkdir(exist_ok=True)
    mem=loadj(run/'K2A_membership_lock.json')
    clear=root/mem['clear_membership_index']; unr=root/mem['unresolved_membership_index']
    if sha(clear)!=EXPECTED_CLEAR_SHA or line_count(clear)!=EXPECTED_CLEAR_N: raise RuntimeError('clear membership drift')
    if sha(unr)!=EXPECTED_UNRESOLVED_SHA or line_count(unr)!=EXPECTED_UNRESOLVED_N: raise RuntimeError('unresolved membership drift')
    pf0files=[]
    for p in sorted(pf0.iterdir()):
        if p.is_file(): pf0files.append(obj(root,p,'S1_POSTFREEZE_REFERENCE_EVIDENCE'))
    final_science={
      'stage':'P13-S1-PF1','OVERALL_STATUS':'PASS','K3_formal_discovery_digest':EXPECTED_K3,'PF0_semantic_digest':audit['PF0_semantic_digest'],
      'cohorts':{'S2_ACTIVE_clear':EXPECTED_CLEAR_N,'REFERENCE_unresolved':EXPECTED_UNRESOLVED_N},
      'route_interpretation':'PATTERN_A_PLUS_B_WITH_POSTFREEZE_ATTAINMENT_AND_RADIAL_CHARACTERIZATION',
      'claim_III_A':'SUPPORTED_ON_FROZEN_TRAIN_OPERATOR_EXPERIMENT',
      'claim_III_B':'UNTESTED','claim_III_C':'UNTESTED','claim_III_D':'UNTESTED',
      'PF0_role':'REFERENCE_DESCRIPTIVE_POST_MEMBERSHIP','PF0_membership_authority':'NONE',
      'technical_conclusion':'Raw finite-local coefficient-dependent discovery is supported on TRAIN with stable NULL/FULL separation. PF0 characterizes attainment/theory-space/radial objective geometry without changing membership. Prospective DEVELOPMENT zero-shot transfer remains the next decisive test.',
      'forbidden_retroactive_changes':['candidate membership','TRAIN threshold','search horizon','continuation','response-aware refit','top-k/Pareto/percentile/target-count narrowing']
    }
    writej(out/'PF1_FINAL_S1_SCIENTIFIC_FREEZE.json',final_science)
    boundary={'status':'PASS','DEVELOPMENT_COEF':'SEALED_COMMITTED_UNOPENED_THROUGH_S1','DEVELOPMENT_RESPONSE':'SEALED_COMMITTED_UNOPENED_THROUGH_S1','SEALED_FINAL_COEF':'SEALED_COMMITTED_UNOPENED','SEALED_FINAL_RESPONSE':'SEALED_COMMITTED_UNOPENED','PF0_opened_DEV':False,'PF0_opened_response':False,'PF0_changed_membership':False}
    writej(out/'PF1_DATA_BOUNDARY_FREEZE.json',boundary)
    k3s2=loadj(k3/'K3_S2_ACTIVE_INPUT_MANIFEST.json')
    s2manifest={
      'role':'ACTIVE_S2_INPUT_MANIFEST_AFTER_PF0','authoritative_S1_run':str(run.relative_to(root)),
      'clear_membership':{'path':str(clear.relative_to(root)),'sha256':EXPECTED_CLEAR_SHA,'count':EXPECTED_CLEAR_N},
      'unresolved_lineage':{'path':str(unr.relative_to(root)),'sha256':EXPECTED_UNRESOLVED_SHA,'count':EXPECTED_UNRESOLVED_N,'S2_eligible':False},
      'K3_S2_manifest_upstream':obj(root,k3/'K3_S2_ACTIVE_INPUT_MANIFEST.json','IMMUTABLE_UPSTREAM_S2_INPUT_LOCK'),
      'DEVELOPMENT_coefficient_commitment':k3s2.get('development_coefficient_commitment'),
      'DEVELOPMENT_response_commitment':k3s2.get('development_response_commitment'),
      'SEALED_coefficient_guard':k3s2.get('sealed_final_coefficient_guard'),
      'SEALED_response_guard':k3s2.get('sealed_final_response_guard'),
      'identity_control':k3s2.get('identity_control'),'null_control':k3s2.get('null_control'),
      'same_AST_theta_gauge_zero_refit':True,'branch_reselection_forbidden':True,
      'PF0_reference_evidence':pf0files,'PF0_has_membership_authority':False,
      'next_action':cfg['next_on_pass'],'response_stage_blocked_at_entry':True,
      'runtime_policy':'S2 reads authoritative S1 locators/registries and private commitments in place; portable freeze is review/recovery only.'
    }
    writej(out/'PF1_S2_ACTIVE_INPUT_MANIFEST.json',s2manifest)
    writej(out/'PF1_S2_PROTOCOL_ROADMAP.json',{'top_level_K_steps':cfg['S2_top_level_roadmap'],'response_stage_blocked_at_S2_entry':True,'response_blockers':cfg['response_blockers_to_freeze_before_candidate_response'],'renumbering_changes_scientific_semantics':False})
    # Source manifest
    rels=[
      'phases/p13/coefficient_law_raw_xt/configs/p13_s1_pf1_protocol.json','phases/p13/coefficient_law_raw_xt/src/p13rawxt/s1_pf1_final_freeze.py',
      'phases/p13/coefficient_law_raw_xt/src/p13rawxt/s0_s1_pf1_final_packager.py','phases/p13/coefficient_law_raw_xt/scripts/run_p13_s1_pf1.sh',
      'phases/p13/coefficient_law_raw_xt/scripts/package_p13_s1_pf1_audit.sh','phases/p13/coefficient_law_raw_xt/scripts/package_p13_s0_s1_pf1_final_freeze.sh',
      'phases/p13/coefficient_law_raw_xt/tests/test_p13_s1_pf1.py','phases/p13/coefficient_law_raw_xt/docs/P13_S1_PF1_FINAL_STAGE_FREEZE_AND_S2_HANDOFF.md']
    src=[]
    for rel in rels:
        p=root/rel
        if p.is_file(): src.append(obj(root,p,'PF1_SOURCE_PROTOCOL'))
    writej(out/'PF1_SOURCE_MANIFEST.json',{'files':src})
    # The PF1 scientific/handoff digest intentionally excludes rendered context bytes to avoid
    # a circular self-hash. Context SHA values are frozen separately in PF1_HANDOFF_MANIFEST.
    base={'stage':'P13-S1-PF1','K3':EXPECTED_K3,'PF0':audit['PF0_semantic_digest'],'membership':[EXPECTED_CLEAR_SHA,EXPECTED_UNRESOLVED_SHA,EXPECTED_CLEAR_N,EXPECTED_UNRESOLVED_N],'science':final_science,'boundary':boundary,'S2_manifest':s2manifest,'S2_roadmap':cfg['S2_top_level_roadmap'],'source_manifest':src}
    final_digest=canon_digest(base)
    write_contexts(root,cfg,run,out,audit['PF0_semantic_digest'],final_digest)
    context_objs=[obj(root,root/cfg['contexts']['S1_final'],'FINAL_S1_CONTEXT'),obj(root,root/cfg['contexts']['S2_entry'],'ACTIVE_S2_ENTRY_CONTEXT')]
    update_rolling(root,cfg,final_digest,audit['PF0_semantic_digest'])
    handoff={'final_S1_PF1_semantic_digest':final_digest,'K3_formal_discovery_digest':EXPECTED_K3,'PF0_semantic_digest':audit['PF0_semantic_digest'],'contexts':context_objs+[obj(root,root/cfg['contexts']['rolling'],'ROLLING_REFERENCE_CONTEXT')],'S2_active_input_manifest':obj(root,out/'PF1_S2_ACTIVE_INPUT_MANIFEST.json','ACTIVE_S2_INPUT_MANIFEST'),'portable_freeze_runtime_input':False,'next_action':cfg['next_on_pass']}
    writej(out/'PF1_HANDOFF_MANIFEST.json',handoff)
    writej(out/'PF1_SEMANTIC_OUTPUT_DIGEST.json',{'semantic_output_digest':final_digest,'semantic_basis_note':'K3 immutable discovery + PF0 post-freeze evidence + final S2 handoff; does not replace K3 historical digest'})
    freeze={'stage':'P13-S1-PF1','OVERALL_STATUS':'PASS','NEXT_ACTION':cfg['next_on_pass'],'semantic_output_digest':final_digest,'K3_formal_discovery_digest':EXPECTED_K3,'PF0_semantic_digest':audit['PF0_semantic_digest'],'authoritative_S1_run':str(run.relative_to(root)),'cohorts':{'clear_count':EXPECTED_CLEAR_N,'unresolved_count':EXPECTED_UNRESOLVED_N},'S1_final_context':cfg['contexts']['S1_final'],'S2_entry_context':cfg['contexts']['S2_entry'],'response_stage_blocked':True}
    writej(out/'PF1_FREEZE_SUMMARY.json',freeze)
    writet(run/'PF1_OVERALL_STATUS.txt','PASS\n'); writet(run/'PF1_NEXT_ACTION.txt',cfg['next_on_pass']+'\n')
    writet(root/'phases/p13/coefficient_law_raw_xt/runs/LATEST_P13_S1_PF1_RUN.txt',str(run.relative_to(root))+'\n')
    writet(root/'phases/p13/coefficient_law_raw_xt/runs/LATEST_P13_S1_FINAL_FREEZE_RUN.txt',str(run.relative_to(root))+'\n')
    print('OVERALL_STATUS=PASS')
    return 0

def main() -> int:
    ap=argparse.ArgumentParser(); ap.add_argument('--project-root',required=True); a=ap.parse_args(); return run(Path(a.project_root).resolve())
if __name__=='__main__': raise SystemExit(main())
