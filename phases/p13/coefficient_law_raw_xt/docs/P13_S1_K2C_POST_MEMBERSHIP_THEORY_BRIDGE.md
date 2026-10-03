# P13-S1-K2C — Post-membership theory bridge

K2C is executed only after the complete TRAIN search horizon, hard TRAIN membership, and K2B post-search diagnostics are frozen. It is a holdout-free mechanism stage. It cannot add, remove, rescue, rank-select, or refit S2 candidates.

## Epsilon scaling

For each frozen FULL clear branch and each separately retained numerical-boundary-unresolved branch, K2C evaluates the same AST/theta/gauge on the six TRAIN coefficient *shapes* at `epsilon={0.05,0.10,0.20}`. The `epsilon=0` neutral medium is generated only as an auxiliary baseline for the functional derivative/alignment calculation. Derived coefficient arrays are constructed **only from the authoritative TRAIN search-facing coefficient jets**, using the exact shape identity `a_epsilon = a_0.2^(epsilon/0.2)` through local Taylor-jet algebra. Generator parameters are not read. The ratio-one (`epsilon=0.20`) construction is an exact copy of the authoritative G65 object and must preserve its semantic digest. No scaled coefficient array is persisted as a new scientific object.

K2C reports `J_princ` family scaling and consecutive log slopes. It does not interpret a slope as a Claim-II remainder theorem. Claim II bounds characteristic-coordinate approximation; no operator-defect Lipschitz bridge has been proved here.

## Gauge-aware Claim-II m=2 functional alignment

The Claim-II first-order local characteristic-jet tangent uses six raw basis channels:

- `B1=(t^2 b_x,0)`
- `B2=(t^3 b_xt,0)`
- `B3=(0,t b)`
- `B4=(0,t^2 b_t)`
- `B5=(0,t^3 b_tt)`
- `B6=(0,t^3 b_xx)`

with theory coefficients

`(1/4,-1/6,1/2,-1/4,1/12,1/12)`.

Every basis vector is passed through the linearization of the same deterministic common-translation/common-positive-scale gauge used by the discovered maps. The discovered map tangent is estimated from canonical maps by the fixed one-sided second-order formula

`(-3 Phi(0)+4 Phi(0.05)-Phi(0.10))/(2*0.05)`.

The six TRAIN shapes and both coordinate components are stacked using source-domain tensor-trapezoid quadrature with equal field weight. The projection rank tolerance is `1e-8*sigma_max`. Rank deficiency or invalid/non-F4 candidate maps at one of the derivative amplitudes produces `ALIGNMENT_UNRESOLVED`; no favorable subspace is hand-selected.

Alignment coefficients, residuals, and cosine quantities are descriptive only. There is no alignment PASS/FAIL threshold and no K2C result can modify S2 eligibility.

## Deferred diagnostics

PLCP and reverse-fitter regret are not triggered because K2A produced Pattern A+B with 2307 clear TRAIN-qualified branches rather than structural under-attainment. Observability is not assigned a new post-hoc K2B divergence threshold; it remains preregistered for a later S2 operator-transfer failure if needed. J-vs-response calibration remains an S2 diagnostic.

## S1-to-S2 prediction freeze

Before DEVELOPMENT is opened, K2C freezes directional mechanism predictions about population-level operator transfer, expected transfer heterogeneity, descriptive theory-alignment association, and the already-preregistered possibility of response non-discrimination in the weak/local regime. These predictions are not gates and cannot select candidates.
