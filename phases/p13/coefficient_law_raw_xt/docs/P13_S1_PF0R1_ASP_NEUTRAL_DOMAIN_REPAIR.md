# P13-S1-PF0R1 — ASP neutral-domain repair

## Status

Implementation/provenance repair only. The immutable S1-K3 freeze, the 2307 clear / 80 unresolved membership, the PF0 objective, lambda grid, hard gates, data boundary, and next-stage authorization are unchanged.

## Failure observed

A frozen TRAIN-qualified raw AST can contain primitives such as `Inv` or noninteger powers that are valid on all six TRAIN coefficient fields but undefined on the synthetic neutral medium `a=1`. PF0-E originally evaluated the neutral map eagerly before the lambda loop. A neutral-domain `FloatingPointError` therefore aborted the whole PF0 process, even though lambda=1 is the original frozen TRAIN map and remains well defined.

## Repaired semantics

For every branch/field:

1. The frozen TRAIN map is evaluated first. Any TRAIN-side failure remains fatal because lambda=1 must reproduce the frozen K2C epsilon=0.20 result.
2. The neutral baseline `Phi[a=1]` is evaluated separately.
3. If the neutral baseline has a numerical/domain failure, all lambda != 1 radial points for that field are marked `ASP_NEUTRAL_BASELINE_DOMAIN_FAILURE`; the branch is retained and ASP is `ASP_UNRESOLVED_NEUTRAL_BASELINE_DOMAIN`.
4. Lambda=1 is still evaluated directly from the frozen TRAIN map and must satisfy the existing relative integrity tolerance `1e-10`.
5. No candidate-specific rescue, alternate neutral baseline, theta refit, lambda-grid extension, membership change, or threshold change is allowed.
6. Unexpected schema/programming errors remain fatal; only numerical/domain exceptions (`FloatingPointError`, `OverflowError`, `ZeroDivisionError`) are converted to diagnostic UNRESOLVED outcomes.

## Restart migration

The original PF0 source SHA was:

`8a6741df11ab10cc42473cfebee9da0fc74a4cb5e70b3c0befe2f4c2ea6b78d0`

PF0R1 permits a one-time restart migration from that exact source only when every other restart-lock field is byte-semantically unchanged. Existing partial ASP rows are reused only after validating branch membership, lambda grid, zero-refit flag, supported status, and lambda=1 integrity `<=1e-10`. Otherwise restart remains fail-closed.

## Claim boundary

Neutral-domain ASP unresolved is a property of this post-membership radial diagnostic, not a failure of the already-frozen TRAIN qualification. It has no S2 membership or search authority.
