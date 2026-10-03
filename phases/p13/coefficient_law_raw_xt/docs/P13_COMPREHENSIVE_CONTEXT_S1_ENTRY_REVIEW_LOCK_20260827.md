# P13 Comprehensive Context — S1 Entry after S0 Freeze and Adversarial Review Lock

**Project:** P13 Constructive Coefficient-Law Discovery  
**Semantic milestone:** S0 frozen; S1 protocol converged after independent adversarial review  
**Date:** 2026-08-27  
**Artifact role:** **ACTIVE canonical S1-entry context**  
**Supersedes as ACTIVE:** `P13_COMPREHENSIVE_CONTEXT_S1_ENTRY_20260827.md` (retain that file as REFERENCE)  
**Immutable upstream design:** `P13_COMPREHENSIVE_CONTEXT_S0_ENTRY_20260826.md`  
**S0 formal status:** `PASS`  
**S0-K3 semantic digest:** `5f5c72825de4ec0f740471266d72247682669ab92bb2926d95bd8429a5ad4526`  
**Authorized next action:** `P13-S1-K0R_POST_S0_ADVERSARIAL_REVIEW_PROTOCOL_LOCK`  
**Formal S1-K1 search authorized now:** **NO**

---

## 0. Purpose and governing objective

This document freezes the updated S1-entry design after S0 formal freeze and two rounds of adversarial review. The current research objective is **route qualification first, paper-level completeness second**:

> At minimum additional cost, make every plausible S1 outcome lead to a predeclared **GO / REPAIR / STOP** interpretation, without response leakage, theory-witness steering, candidate-specific rescue, target survivor counts, or brute-force budget compensation.

S0 remains frozen. It established that the current finite-local representation/search machinery is scientifically qualified for a formal TRAIN-only discovery attempt. It did **not** discover the final coefficient law and did **not** establish unseen-coefficient or physical-response generalization.

A key methodological rule is now explicit:

\[
\boxed{\text{deferred execution} \neq \text{deferred preregistration}.}
\]

Diagnostics that may be useful only after S1 are preregistered now, before the S1 result is known. Their execution remains conditional and later. This prevents post-hoc failure-specific instrumentation while avoiding unnecessary computation in the first route-feasibility pass.

---

## 1. Frozen scientific target

P13 seeks a shared raw finite-local coefficient-functional coordinate law

\[
\Phi_\theta[a]=(X_\theta[a],T_\theta[a]),
\qquad
u(x,t)=w(X,T),\qquad A=1,
\]

with one shared AST and one shared theta vector across multiple coefficient fields.

The representation remains the S0-frozen L3 raw primitive grammar. FULL arms may access the coefficient only through the primitive coefficient terminal and compositional derivatives. There are no characteristic labels, path-integral primitives, numerical characteristic solvers, Claim-II skeleton dictionaries, theory-motif mutations, response information, or S0 capacity-witness seeds.

Claim II remains the theoretical boundary: exact arbitrary-coefficient finite-jet universality is not the target; constructive discovery is tested only in the declared weak/local finite-jet regime.

---

## 2. Frozen data roles and leakage boundary

- `CALIBRATION_COEF`: 5; open; calibration only.
- `TRAIN_OPERATOR`: 6; open; the only coefficient fields accessible to formal S1 search/objective/fitting.
- existing `OPENED_TRANSFER_DIAGNOSTIC`: 2 cross-family fields; diagnostic only.
- `DEVELOPMENT_COEF`: 4; committed and sealed until S2.
- `SEALED_FINAL_COEF`: 4; committed and sealed until S3.
- DEVELOPMENT response definitions: 32 committed pairs; sealed.
- SEALED_FINAL response definitions: 32 committed pairs; sealed.

S1-K0R additionally commits **two fresh within-family transfer diagnostic coefficient fields** generated from the same public TRAIN generator family (`oblique_plane_wave_3mode`) but with fresh hidden seeds/payload commitments. Their commitment hashes are frozen before K1; payload/seed disclosure occurs only after the complete response-blind S1 search horizon is irreversibly fixed. Role: `WITHIN_FAMILY_TRANSFER_DIAGNOSTIC`.

Neither existing nor new diagnostic outcomes may alter:

- S1 search budget;
- AST/theta;
- candidate membership;
- objective;
- thresholds;
- formal S2 candidate eligibility.

They are mechanism diagnostics only.

---

## 3. S0 evidence retained as frozen upstream evidence

S0-K3 froze the following:

- L3/m=2 numerical semantics: PASS;
- deterministic gauge equivalence: PASS;
- coefficient-family identifiability: PASS;
- rank = 6;
- `sigma_min/sigma_max = 0.014451934091790493`;
- minimum residualized coefficient-channel fraction = `0.8511250699071453`;
- stable FULL capacity reference at G65:
  `J_capacity = 0.0022936465892580485`;
- FULL/NULL capacity separation: PASS;
- generic 8192-proposal operational burn-in: 1314 unique F4;
- production fitter on operational F4: 72 resolved reference comparisons, median `R_fit=1.0`, p90 `R_fit=1.153892357658559`;
- S0 V2 calibration: 8192 attempts, 7103 F4 children, 443 >=5% improvements, 254 >=20% improvements;
- G33/G65 evaluator max relative J discrepancy = `0.0015882328348619504`;
- calibration-only causal response feasibility: PASS;
- no-leakage: PASS.

These facts qualify machinery. They are not S1 candidates and do not determine S1 membership or budget.

---

## 4. Adversarial-review adjudication

### 4.1 Accepted as pre-K1 protocol requirements

The following review points are accepted and incorporated:

1. each arm initializes from its **own declared grammar prior**; FULL is not forced to start on the NULL coordinate-only submanifold;
2. FULL-V1 and FULL-V2 remain byte-identical at initialization;
3. common random numbers couple NULL/FULL initialization wherever the sampled grammar choice does not invoke coefficient access;
4. `J_capacity` and `R_att` are removed from every S1 membership, continuation, budget, and arm-decision path;
5. continuation is based on internal TRAIN objective progress, not new-F4 diversity;
6. coefficient-dependent versus coordinate-only best-J frontier curves are recorded at every checkpoint;
7. candidate/branch identity and S2 no-branch-reselection semantics are explicit;
8. S2/S3 identity and coefficient-blind controls plus a non-discriminative absolute-gate classification are preregistered now;
9. two fresh within-family diagnostics are committed now and opened only after the search horizon is fixed;
10. evaluator calls become a third fairness axis in addition to structural proposals and CPU;
11. four seeds remain the primary route-feasibility cohort; future additional seeds are independent confirmation and may not retroactively rescue a failed/unstable first cohort;
12. deferred mechanism diagnostics are preregistered now even when executed only after a negative or ambiguous S1 result.

### 4.2 A0 theta-index concern — formally resolved by code inspection

Direct inspection of the authoritative inherited P11 code shows:

- `canonicalize_pair()` calls `renumber_parameter_pair()`;
- `renumber_parameter_pair()` normalizes both components and renumbers all theta parameters compactly as `theta_1,...,theta_k`;
- therefore on canonical parents the current residual-graft rule `max(existing_index)+1` is the next free index.

Hence the hypothesized theta-hole early-saturation bug is **not present** in the current canonical-parent path. No S0 replay/addendum is required for this issue.

The separate coverage issue remains: S0's chronological first-64 F4 parents did not establish behavior near the true cap `unique_theta_total_max=10`. S1 therefore records saturation explicitly rather than assuming it away.

### 4.3 V2 kernel naming and semantics

The formal name is:

`additive_root_residual_graft`.

Its structural prior is explicit: it adds a new scaled raw subtree at the root of X or T. It is **not** described as a theory-free or prior-free kernel; the minimum-prior claim is specifically absence of PDE/characteristic/answer-specific motifs.

No mixed root/subtree/multiplicative kernel is added in the first S1 route-feasibility pass. Such a change would be a new proposal-geometry branch.

### 4.4 Review items intentionally not converted into hard gates

- no arbitrary response-relative threshold such as 0.25 is added;
- no arbitrary 1% operator-membership tolerance is added;
- no “worst of three fairness axes” scalar is created;
- no immediate 4→8 seed expansion;
- no PLCP or theory-alignment result may affect current S1 candidate membership.

---

## 5. S1 formal arms

### NULL-V2

- coefficient-blind grammar;
- same caps and shared-theta family objective;
- same V2 mutation-slot schedule as FULL-V2;
- role: coefficient-blind control.

### FULL-V1

- full raw coefficient grammar;
- inherited generic P11-style GP search geometry;
- role: representation-enabled generic-search baseline.

### FULL-V2

- identical representation/caps to FULL-V1;
- byte-identical initial population to FULL-V1;
- 50% of standard **mutation slots** are routed to `additive_root_residual_graft`;
- role: search-geometry intervention at fixed representation.

The NULL-vs-FULL comparison tests coefficient access. FULL-V1-vs-FULL-V2 tests proposal geometry.

---

## 6. S1-K0R initialization fairness

Each arm samples from its own declared grammar prior.

- NULL uses the coordinate-only terminal alphabet.
- FULL-V1/FULL-V2 use the full coefficient-aware raw terminal alphabet.
- sampler form, caps, initial population size, structural budget, and paired seed are matched.
- common random numbers are used so shared grammar choices are coupled wherever possible.
- FULL-V1 and FULL-V2 initial populations must be byte-identical.

Required initial-population audit fields:

- coefficient-terminal syntax present: yes/no;
- F4 status;
- nodes/depth/theta count;
- structural hash;
- paired-seed provenance.

No initial member may come from S0 burn-in, S0 fitter cohort, S0 capacity instruments, S0 V2 children, or historical P11/P12 outcome-selected candidates.

---

## 7. V2 arm realization and saturation semantics

For every mutation opportunity, the mutation-type choice is frozen by the deterministic paired RNG stream.

For FULL-V2/NULL-V2 record:

- `mutation_slots_total`;
- `standard_mutation_selected`;
- `graft_selected`;
- `graft_attempted`;
- `graft_successful_child`;
- `graft_parent_theta_saturated`;
- `graft_no_legal_child`;
- `silent_fallback_to_V1`.

**Hard implementation invariant:** `silent_fallback_to_V1 == 0`.

If a graft-selected slot cannot produce a legal child because of true theta-cap saturation or no legal subtree, that event remains a failed V2 proposal attempt; it is **not replaced by a V1 mutation**. Thus state-dependent saturation is measured as part of V2 kernel performance rather than silently changing the arm definition.

The nominal 50% intervention refers to **selected mutation slots**, not successful legal children. No arbitrary successful-graft fraction such as 0.40 is introduced as a scientific threshold.

---

## 8. Objective and reporting quantities

For TRAIN field i:

\[
J_i=J_{\rm princ}(\Phi;a_i).
\]

Primary search objective:

\[
J_{\rm family}=\sqrt{\frac{1}{6}\sum_i J_i^2}.
\]

Also always report:

\[
J_{\max}=\max_i J_i,
\]

and the descriptive relative-family score

\[
J_{\rm family}^{rel}
=
\sqrt{\frac{1}{6}\sum_i\left(\frac{J_i}{J_{{\rm identity},i}}\right)^2}.
\]

`J_family^rel` never replaces the formal objective and never changes membership.

`R_att = J_search,best/J_capacity` may be reported **only after the search horizon is frozen**, as a cross-cohort calibration diagnostic. It has no role in continuation, candidate membership, budget, or S2 eligibility.

---

## 9. Candidate and branch semantics

A scientific search emission is defined by

\[
(\text{canonical skeleton},\hat\theta,\text{deterministic gauge},\text{fit provenance}).
\]

Different theta branches of the same skeleton are distinct scientific branches unless exact symbolic/numerical equivalence certifies them identical. No branch is deleted merely because another branch of the same skeleton has lower TRAIN J.

All emitted branches remain in the frozen execution/scientific registry. Structural summaries may additionally report the TRAIN-best branch per skeleton, but this is descriptive compression only.

S2 receives the entire clear `OPERATOR_QUALIFIED_TRAIN` branch cohort modulo exact-equivalence execution sharing.

**S2 branch reselection is forbidden.** Each branch carries exactly its S1-frozen AST/theta/gauge. DEV outcomes may not replace a failing branch by another branch of the same skeleton. Any later branch-sensitivity analysis is descriptive, cohort-wide, and cannot change PASS/FAIL assignments.

---

## 10. Numerical ambiguity semantics for operator qualification

The S0 evaluator certificate found

`max_G33_G65_relative_J_difference = 0.0015882328348619504`.

S1-K0R freezes a conservative search-resolution numerical ambiguity width

\[
\tau_{num}=0.005,
\]

chosen prospectively from that certificate by upward rounding, not from S1 outcomes.

This is **not** a relaxed scientific threshold and never converts a worse-than-baseline result into PASS.

For any hard ratio boundary q:

- clear PASS: value < `q*(1-tau_num)`;
- numerical-boundary UNRESOLVED: value within `q*(1±tau_num)`;
- clear FAIL: value > `q*(1+tau_num)`.

Apply this ambiguity semantics to the family 0.5×identity boundary and each per-field identity boundary. Candidates in the ambiguity band remain `OPERATOR_QUALIFIED_UNRESOLVED`, not FAIL and not S2-eligible clear PASS.

Formal effect-size gate itself remains unchanged:

- F0-F4 valid on all six TRAIN fields;
- all `J_i` resolved;
- `J_family <= 0.5 J_identity,family`;
- no TRAIN field clearly worse than identity.

All unique F4 branches remain recorded regardless of qualification.

---

## 11. Primary seed policy

Primary S1 route-feasibility cohort:

`4 paired seeds`.

Report all four paired seed effects individually. No aggregate significance claim is made.

For each comparison, a paired seed is counted as a clear advantage only when the terminal best-J ratio differs by more than `tau_num`; differences inside the ambiguity band are ties/UNRESOLVED.

Interpretation:

- 4/4 same clear direction: consistent route-feasibility evidence;
- 3/4: directional but unstable;
- <=2/4: no stable paired advantage.

Any future extra seeds constitute a separately preregistered independent confirmatory replication. They may strengthen a positive first-cohort result but may **not** retroactively rescue a failed or unstable primary four-seed result.

---

## 12. Staged execution without adaptive scientific peeking

S1-K1 may execute the first paired seed across all three arms before launching the remaining three seeds to verify HPC mechanics.

Allowed interim checks are engineering/integrity only:

- process/file integrity;
- restart/resume equality;
- proposal/accounting conservation;
- mutation-slot schedule integrity;
- no forbidden data read;
- worker/BLAS configuration;
- no silent V2→V1 fallback.

The remaining three seeds must run unchanged regardless of seed-1 objective values, F4 yield, arm ranking, or apparent scientific success/failure, unless a genuine implementation/integrity failure voids the affected run. Scientific peeking may not alter settings or budgets.

---

## 13. S1-K1V1 first formal rung

After K0R PASS:

\[
4\text{ paired seeds}\times8192\text{ structural proposals/seed/arm}\times3\text{ arms}.
\]

Total = 98,304 structural proposals.

Frozen checkpoints per seed/arm:

`2048, 4096, 6144, 8192`.

One authoritative S1 run store; restart/resume across HPC jobs; 17 CPUs = 16 workers + 1 coordinator; BLAS/OpenMP threads = 1.

Every proposal ledger records at least:

- arm / paired seed / proposal index;
- parent/proposal provenance;
- structural hash;
- coefficient-dependent-syntax flag;
- theta count and branch identity;
- F0-F4 status;
- `J_i`, `J_family`, `J_max`;
- evaluator calls;
- worker CPU and wall clock;
- exact-equivalence class;
- V2 counters where applicable.

At every checkpoint report two separate FULL frontier curves:

1. best `J_family` among coefficient-dependent syntax candidates;
2. best `J_family` among coefficient-free coordinate-only candidates.

These curves are descriptive and never affect candidate membership.

---

## 14. Fairness axes

Pre-register and report three distinct estimands; do not combine them into a weighted/worst-axis score.

1. **Primary:** equal structural proposals.
2. **Secondary:** equal evaluator calls, reconstructed from frozen ledgers.
3. **Tertiary:** equal accumulated CPU, reconstructed from frozen ledgers.

If conclusions differ across axes, the disagreement is itself reported as a result.

---

## 15. Exact optional continuation rule

Only NULL-V2 and FULL-V2 may extend beyond 8192. FULL-V1 remains frozen at 8192.

For FULL-V2 seed s, define `B_s(n)` as the cumulative best resolved `J_family` among F4-valid proposals through proposal n.

Define progress from 4096 to 8192:

- if no resolved F4 exists by 8192: `r_s = 0`;
- if no resolved F4 exists by 4096 but one exists by 8192: `r_s = 1`;
- otherwise `r_s = 1 - B_s(8192)/B_s(4096)`.

Continuation to 32768/seed/arm is authorized iff all are true:

1. **no clear OPERATOR_QUALIFIED_TRAIN branch exists in either FULL arm at the completed 8192 horizon**;
2. `median_s r_s >= 0.10` over the four primary FULL-V2 seeds;
3. all implementation/restart/accounting invariants PASS.

No `J_capacity`, `R_att`, diagnostic field, F4-diversity rate, response result, or target survivor count enters this decision.

If continuation is authorized, NULL-V2 and FULL-V2 both extend by exactly 24,576 additional proposals per seed, preserving the matched comparison. If not authorized, record `NOT_AUTHORIZED_BY_PREREGISTERED_TRAIN_ONLY_RULE`.

This rule is deliberately tuned to the current route-feasibility objective: once a clear FULL operator-qualified law already exists, the next scientific question is prospective transfer, not spending more TRAIN budget to improve an already adequate discovery.

---

## 16. S1 route-level GO / REPAIR / STOP interpretation table

This table does not change membership. It determines interpretation and the predeclared next branch after the formal search horizon is frozen.

### Pattern A — constructive route supported

Conditions:

- at least one clear FULL `OPERATOR_QUALIFIED_TRAIN` branch exists; and
- FULL shows a stable paired advantage over NULL or the best-J frontier is demonstrably coefficient-dependent.

Interpretation: constructive coefficient-law route is alive. Proceed to frozen S1 diagnostics and then prospective S2 using the entire clear TRAIN-qualified cohort.

V2 need not be positive for the coefficient-law route to survive.

### Pattern B — coefficient access works; V2 is neutral/negative

FULL clearly/stably outperforms NULL, but FULL-V2 does not clearly outperform FULL-V1.

Interpretation: coefficient information is useful; current additive-root V2 intervention has no stable advantage. Preserve the FULL discovery claim, report V2 as neutral/negative, and do not redesign V2 before evaluating frozen FULL candidates prospectively.

### Pattern C — FULL-V2 improves attainment but no FULL candidate qualifies

FULL-V2 clearly outperforms NULL/V1 but no clear operator-qualified FULL branch exists after the authorized horizon.

Interpretation: representation capacity is known to exist from S0; current formal search under-attains. Use hit-rate-vs-J and preregistered PLCP/reverse-fitter diagnostics to distinguish proposal-geometry from fitter accessibility. Do not add search budget post hoc.

### Pattern D — FULL approximately equals NULL and coefficient-dependent frontier never dominates

Interpretation: coefficient-enabled region was not effectively accessed. Do **not** conclude “coefficient information is useless.” Enter an accessibility/initialization/proposal-geometry repair branch.

### Pattern E — FULL approximately equals NULL despite substantial coefficient-dependent frontier exposure

Interpretation: coefficient access is available but gives no stable TRAIN objective benefit. Investigate objective alignment, TRAIN family informativeness/observability, and representation-in-regime limitations. Do not rescue by changing objective or grammar inside the same branch.

### Pattern F — all arms fail to form a healthy F4 population

Interpretation: execution/search accessibility failure. No coefficient-law scientific conclusion is permitted.

For Patterns C–E, the diagnostic capacity ratio `J_best/J_capacity` may be reported as context only; it does not itself determine the branch.

---

## 17. Post-search diagnostic opening — S1-K2B

Only after the complete authorized response-blind search horizon and continuation decision are immutable:

1. evaluate frozen AST/theta/gauge with zero refit on:
   - 2 fresh `WITHIN_FAMILY_TRANSFER_DIAGNOSTIC` fields;
   - 2 existing cross-family `OPENED_TRANSFER_DIAGNOSTIC` fields;
2. coefficient-channel ablation/substitution diagnostics;
3. lower-order transformed-coefficient diagnostics;
4. hit-rate-vs-parent-J curves;
5. absolute and relative family objectives;
6. the three fairness axes.

Diagnostic outcomes may classify mechanisms but do not alter TRAIN membership or formal S2 candidate eligibility.

Surrogate observability/SVD profiles for public generator families may be computed descriptively. They are not proxy labels and cannot become candidate filters or formal transfer-failure classifiers.

---

## 18. Theory-bridge diagnostics preregistered now, executed later

### 18.1 Epsilon-scaling diagnostic — highest priority holdout-free mechanism test

After S1 TRAIN candidate membership is frozen and before opening DEVELOPMENT, evaluate frozen AST/theta with no refit on TRAIN-derived coefficient shapes at

`epsilon' = {0.05, 0.10, 0.20}`.

Report empirical scaling of `J_princ` and optionally fit a descriptive two-term model such as `A*epsilon^2 + B*|epsilon|` when numerically identifiable.

**Important claim boundary:** Claim II directly bounds characteristic-coordinate approximation error, not `J_princ`. Therefore the fitted B coefficient must **not** be declared to satisfy the Claim-II coordinate remainder bound unless a separate operator-defect stability/Lipschitz connection is derived. Without that derivation, epsilon scaling tests perturbative order and amplitude transfer only.

No refit; no candidate filtering; no DEV/SEALED use.

### 18.2 Discovery–theory functional alignment

After membership freeze, compare the frozen discovered law's small-epsilon functional response with the Claim-II m=2 local characteristic-jet basis. Use a gauge-aware, preregistered projection and report alignment/correct-coefficient evidence descriptively.

This may support a mechanism claim that raw search rediscovered the expected first-order local law. It may not retroactively define search fitness or membership.

If the projection is non-identifiable/nonunique under the frozen gauge/basis, record `ALIGNMENT_UNRESOLVED` rather than hand-selecting a favorable decomposition.

### 18.3 PLCP — preregistered negative-result diagnostic

Trigger only if the authorized S1 horizon ends with structural under-attainment (Pattern C or a closely related geometry ambiguity).

Use the CALIBRATION_ONLY theory capacity law to construct predeclared partial-law completion probes by deleting one admissible first-order component at a time. Measure the frozen V2 kernel's completion probability under functional equivalence, not syntax matching.

PLCP measures the kernel, not candidates. Its outputs cannot enter the existing S1 registry. Any kernel redesign motivated by PLCP requires a new preregistered search branch; it cannot rescue the completed S1 run.

### 18.4 Reverse fitter-regret diagnostic

Trigger only if S1 shows low structural accessibility or evidence suggesting production fitter obstruction. Select production-non-F4 skeletons by a fixed chronology/hash rule, run the frozen reference optimizer cohort-wide, and estimate the fraction that reference fitting can make F4.

Reference-rescued objects remain diagnostic-only and cannot enter the completed S1 candidate registry.

### 18.5 Observability diagnostic

If within-family/cross-family transfer behavior diverges or later S2 operator transfer fails, report public-family jet-channel observability/SVD profiles, including the weak singular direction. This is descriptive mechanism evidence only.

### 18.6 S2 J-vs-response calibration

In S2, report the empirical relationship between frozen TRAIN/operator-transfer `J_family` and causal physical response error for the complete formally evaluated cohort and controls. This is a mechanism/certification result, not a response-aware refit rule.

---

## 19. S2/S3 response non-discrimination controls preregistered now

At every formal S2/S3 response stage, run the same complete response bank for:

1. the identity map;
2. the frozen S0 coefficient-blind NULL calibration control;
3. every formally eligible candidate branch.

The historical absolute response criterion remains unchanged (`E_rel < 0.15` under the existing uncertainty semantics).

If identity passes the complete formal response bank at a stage, classify that stage's absolute threshold as:

`NONDISCRIMINATIVE_ABSOLUTE_GATE`.

Candidate absolute PASS may still be reported as accuracy, but it cannot by itself support a strong transformation-improvement claim. Always report per-case candidate/identity and candidate/NULL error ratios descriptively. No new relative hard threshold is introduced in this branch.

---

## 20. Known regime limitation and future escalation branch

The current `epsilon=0.2, chi<=1, m=2` regime is intentionally weak/local and theoretically clean. It may have limited physical-response dynamic range because identity itself can already be reasonably accurate.

This is a declared claim boundary, not a reason to alter the current branch after seeing S2 outcomes.

If S2/S3 is scientifically non-discriminating despite successful operator discovery, any stronger-heterogeneity / larger-chi regime becomes a **new preregistered branch** with fresh coefficient/data commitments and fresh prospective holdouts. Current DEVELOPMENT/SEALED evidence may not be repackaged as new sealed evidence after such redesign.

---

## 21. Updated S1 K-step roadmap

### S1-K0R — Post-S0 adversarial-review protocol lock and implementation regression

No discovery evidence.

Freeze/test:

- own-grammar paired initialization;
- FULL-V1/FULL-V2 byte-identical starts;
- common-RNG coupling;
- coefficient-dependence flags/frontier accounting;
- shared-theta family objective;
- explicit `additive_root_residual_graft` semantics;
- canonical theta-renumber regression and true-cap saturation behavior;
- no silent V2→V1 fallback;
- complete proposal/branch/equivalence ledgers;
- `tau_num=0.005` ambiguity semantics;
- exact continuation formula;
- candidate/branch/S2 no-reselection semantics;
- 4-seed primary policy and future confirmatory-seed rule;
- within-family diagnostic commitments;
- deferred diagnostic interpretation rules;
- S2/S3 response controls/non-discrimination classification;
- restart/resume and 17-CPU execution accounting;
- no response/diagnostic/DEV/SEALED read.

K0R PASS is required before formal proposals.

### S1-K1A — first paired-seed staged execution

Run seed 1 across all three arms under final locked protocol. It is part of the formal four-seed cohort.

Only integrity checks may occur before the remaining seeds; scientific results cannot change settings or budget.

### S1-K1B — complete first formal rung

Run remaining three paired seeds unchanged. Freeze 2048/4096/6144/8192 checkpoints and complete registries.

### S1-K2A — TRAIN-only adjudication and continuation lock

Before reading any transfer diagnostic:

- arm comparisons;
- coefficient-dependent vs coordinate-only frontier curves;
- hit-rate/attainment curves;
- operator-qualified clear/UNRESOLVED cohort;
- equal-proposal/evaluator-call/CPU analyses;
- apply the exact Section 15 continuation rule;
- commit immutable continuation decision.

### S1-K1C — optional continuation

Only if K2A authorizes it, continue NULL-V2 and FULL-V2 to 32768 proposals/seed/arm total. FULL-V1 remains at 8192. No setting changes.

### S1-K2B — post-search diagnostics

Only after the search horizon is irrevocably complete: open within-family and cross-family diagnostic fields; perform zero-refit transfer, ablations, lower-order diagnostics, hit-rate-vs-J, and fairness reporting.

### S1-K2C — post-membership theory bridge

Freeze TRAIN-derived candidate membership first. Then execute the preregistered holdout-free epsilon-scaling and discovery–theory alignment diagnostics as scientifically appropriate. Freeze any S1→S2 mechanistic predictions before DEVELOPMENT is opened.

No K2C outcome may alter S2 candidate eligibility.

### S1-K3 — formal S1 freeze

Freeze:

- complete branch/proposal/F4 registries;
- exact-equivalence classes and execution-sharing map;
- complete clear `OPERATOR_QUALIFIED_TRAIN` cohort;
- `OPERATOR_QUALIFIED_UNRESOLVED` cohort separately;
- arm/fairness/attainment evidence;
- diagnostic evidence in separate REFERENCE namespace;
- preregistered S2 predictions;
- exact S2 active-input manifest.

No top-k, percentile, Pareto, weighted score, target survivor count, response-aware filter, or diagnostic-transfer filter may narrow the formal candidate cohort.

---

## 22. S1-to-S2 boundary

S1 can establish only:

- constructive TRAIN operator discovery;
- coefficient-access evidence;
- search-geometry evidence;
- response-blind mechanism diagnostics.

S2 is the first prospective DEVELOPMENT coefficient-transfer and response stage.

Formal S2 candidate eligibility is determined only by frozen TRAIN clear `OPERATOR_QUALIFIED_TRAIN` membership. Same AST, same theta, same deterministic gauge, zero refit.

Diagnostic transfer, epsilon scaling, theory alignment, or observability may inform interpretation but cannot add/remove candidates.

---

## 23. Artifact / authoritative-data policy for S1

- one authoritative S1 run store;
- read K1 S0 coefficient objects in place via authoritative manifest/path;
- do not create per-K copies of coefficient arrays or candidate stores;
- checkpoints/ledgers live in the S1 authoritative run and support resume;
- compact audits contain semantic/config locks, counts, decision maps, metrics, SHA/provenance, and authoritative paths only;
- do not package large candidate ledgers, caches, shards, temporary arrays, LU factors, or upstream archives into every audit;
- update canonical context only at semantic milestones: K0R protocol lock, formal S1 freeze, or an explicit scientific branch change.

---

## 24. Immediate authorized action

\[
\boxed{
\texttt{P13-S1-K0R_POST_S0_ADVERSARIAL_REVIEW_PROTOCOL_LOCK}
}
\]

K0R should produce code/config/tests and a compact audit proving the protocol above is exactly realized. Formal S1-K1A/K1B search remains blocked until K0R PASS.

No remaining scientific decision in this document requires reopening S0.
