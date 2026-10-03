# P13-S1-PF0 — Post-freeze attainment and radial diagnostics

## Status / role

`REFERENCE / DESCRIPTIVE / POST-MEMBERSHIP`.

This is an explicitly authorized post-K3 continuation inside the S1 stage. It does **not** reopen or overwrite the immutable K3 discovery freeze. The K3 semantic digest remains the authoritative freeze of formal S1 search/membership evidence. PF0 appends response-blind descriptive evidence before any DEVELOPMENT opening; after PF0 audit, a new S0+S1 final stage-freeze/handoff may be created for S2.

## Immutable parent facts

- S1-K3: PASS.
- K3 semantic digest: `0862640a856eed7d0321d8fa0597b96005293c9058836d686914b31f52e01997`.
- clear S2-eligible TRAIN cohort: 2307 branches, SHA `564134db0bee502198729463c64ffe29408b30081ec1af63d86afff0f4da0c84`.
- numerical-boundary unresolved lineage: 80 branches, SHA `4177feffe4417e157f17eb90578b1e749ec06fe0db7935a7ade315c33cbc4f67`.
- search horizon: 8192 proposals/seed/arm; no 32768 continuation.
- DEVELOPMENT and SEALED remain unopened.

PF0 cannot modify any of these facts.

## Scientific purpose

PF0 resolves interpretation/reporting questions exposed by the completed S1 evidence without converting them into new discovery gates. It addresses:

1. formal hard-gate margin and best-attained objective values;
2. branchwise angular versus radial mismatch in the frozen Claim-II local-jet space;
3. a matched-TRAIN post-freeze theory-informed attainable witness reference;
4. a zero-refit epsilon comparator for that witness;
5. a full-cohort amplitude-sensitivity probe (ASP) along each discovered coefficient-induced functional correction;
6. prospective descriptive mechanism predictions to be checked after DEVELOPMENT opening.

## PF0-A — frozen-ledger attainment readout

Use only frozen S1 records. Report the actual formal quantity

\[
R_{\rm formal}=J_{\rm family}/J_{{\rm identity},\,\rm family},
\]

not the K2C descriptive RMS of per-field ratios. Recompute the frozen qualification semantics with `tau_num=0.005` and require exact agreement with the 2307 clear / 80 unresolved membership labels.

Report:

- formal-ratio distributions;
- clear hard-gate margins;
- per-seed/per-arm/global best F4 objective values;
- best clear branch;
- absolute TRAIN identity baselines;
- already-frozen K2B within/cross identity baselines and absolute candidate diagnostic distributions;
- V1/V2 F4 **scientific-branch throughput**, evaluator calls and CPU separately from objective quality.

No PF0-A statistic has membership, continuation, or search authority.

## PF0-B — theory-space geometry

For every clear branch with resolved K2C alignment, use its frozen branchwise values

\[
s=\|u\|/\|v\|,\qquad c=\cos(u,v),\qquad r=\|u-v\|/\|v\|
\]

and the exact identity

\[
r^2=(1-c^2)+(s-c)^2.
\]

Compute this branch-by-branch before population aggregation. The terms are called **angular** and **radial functional mismatch**. Radial mismatch must not be silently relabeled as theta-fitter regret.

Reconstruct the frozen Claim-II Gram matrix

\[
G=B^T B
\]

from the same six TRAIN fields and gauge-aware weighted basis used by K2C. Report six coefficient distributions, G-metric coefficient norm/cosine/residual, and theory leave-one-channel-out G-metric effects.

No theory-space metric may add, delete, rescue, or rank-filter candidates.

## PF0-C — matched post-freeze TRAIN witness

Build the frozen Claim-II-informed `full_capacity` skeleton only after formal S1 search/membership has already been irrevocably frozen. Fit one shared theta vector on the six TRAIN fields using the exact frozen S0 reference optimizer protocol (`fitter.reference` from `p13_s0_k2_protocol.json`), with two independent deterministic launches and no discovered-candidate initialization.

This object is named:

`POSTFREEZE_MATCHED_TRAIN_CAPACITY_REFERENCE`.

Its interpretation is an achievable matched-TRAIN theory-informed witness. It is **not** a global raw-AST optimum and the ratio `J_best / J_witness` is **not** formal global search regret.

If the two reference launches fail the frozen agreement condition, record `WITNESS_UNRESOLVED`; do not rescue or increase budget candidate-specifically. PF0 itself may remain PASS because this is descriptive evidence.

## PF0-D — witness epsilon comparator

If PF0-C resolves a witness, freeze the same witness theta and evaluate at

\[
\epsilon\in\{0.05,0.10,0.20\}
\]

on the same TRAIN coefficient shapes using the existing authoritative-jet scaling rule

\[
a_\epsilon=a_{0.2}^{\epsilon/0.2}.
\]

No refit by epsilon. No persistent scaled coefficient arrays. This comparator only contextualizes empirical `J_princ` scaling.

## PF0-E — amplitude-sensitivity probe (ASP)

Apply ASP to **all 2307 clear branches** on G65. For each TRAIN field `i`, let

\[
\Phi_0=\Phi_{\hat\theta}[a\equiv1],\qquad
\Phi_1=\Phi_{\hat\theta}[a_i].
\]

Probe the raw functional correction using

\[
\Phi_{\lambda,i}
=
\mathcal G\!\left[
\Phi_0+\lambda(\Phi_1-\Phi_0)
\right],
\]

with the same deterministic gauge, validity ladder and operator evaluator as the frozen pipeline.

The cohort-wide frozen grid is

`{0.6, 0.8, 0.9, 1.0, 1.1, 1.2, 1.4, 1.6}`.

No candidate-specific extension is allowed. If an extreme lambda becomes invalid, retain `ASP_UNRESOLVED_PARTIAL_GRID`; do not rescue it. At lambda=1, ASP must reproduce the frozen K2C epsilon=0.20 G65 `J_family` to relative tolerance `1e-10`, otherwise PF0 fails as an implementation/provenance inconsistency.

Report:

- `J_i(lambda)` and `J_family(lambda)`;
- grid `lambda_star`;
- `radial_slack = 1 - min_lambda J_family(lambda)/J_family(1)`;
- relative objective span across the grid;
- whether the minimum is at a preregistered grid boundary.

ASP measures realized functional radial objective sensitivity. `lambda_star != 1` does not alone prove theta-fitter regret; a flat curve does not alone prove global identifiability failure.

## PF0-F — pre-DEVELOPMENT prediction lock

Before DEVELOPMENT opening, freeze only descriptive hypotheses:

- if PF0-B shows predominantly radial mismatch, report that geometry without uniquely attributing it to fitter or objective geometry;
- larger TRAIN radial slack / larger `|log(lambda_star)|` is hypothesized to associate with larger future `rho_transfer`; this will be a descriptive association without p-values or membership use;
- candidate and matched-witness epsilon curves may be compared qualitatively, with no slope gate;
- V2 extra F4 scientific-branch throughput is not predicted to guarantee better DEVELOPMENT transfer.

## Forbidden actions

PF0 must not:

- open DEVELOPMENT or SEALED payloads;
- read response outcomes;
- run new search, PLCP, or 32768 continuation;
- refit any discovered candidate;
- alter 2307/80 membership;
- create a top-k/Pareto/percentile/weighted-score candidate subset;
- create a new hard threshold;
- use theory, ASP, or capacity-witness results for S2 eligibility.

## Artifact policy

All PF0 outputs are appended under the existing authoritative S1 run:

`PF0_postfreeze/`.

The large K1 proposal/branch/skeleton/equivalence ledgers remain the only authoritative copies. PF0 does not create a new candidate store. `PF0_work/` is restart-only state and is removed on PASS.

## PASS / next action

PF0 PASS means implementation/provenance/data-boundary integrity is closed and all available descriptive components are frozen; optional witness evidence may remain explicitly `WITNESS_UNRESOLVED`.

On PASS:

`P13-S1-PF1_FINAL_STAGE_FREEZE_AND_S2_HANDOFF`

PF1 will create the new S0+S1 final portable freeze and revised S2-entry handoff. It will not rerun S1 search or modify membership.
