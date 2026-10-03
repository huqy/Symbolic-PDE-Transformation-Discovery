# P13-S0-K1 — Coefficient/Data Commitments + Objective/Response Contract

K1 is a data-boundary and contract stage. It generates the fresh open P13 coefficient roles, seals DEVELOPMENT and FINAL coefficient/response payloads outside the active project tree, checks the frozen TRAIN identifiability gate, and freezes the operator/response decision rules.

It performs no symbolic candidate search and no PDE-response solve.

## Search-facing boundary

Search-facing coefficient objects contain only source grids, `q=1`, and analytic coefficient derivative arrays. They deliberately exclude generator family labels, amplitudes, phases, frequencies, `b`, `epsilon`, `M`, and `chi`. Open generator provenance is retained for audit but is not a search input.

DEV/SEALED generator payloads and response definitions live only in the external private root. The active tree stores cryptographic commitments and public schema/count metadata.

## Important continuing guard

K0 froze `numeric_L3_execution_status=NOT_YET_QUALIFIED`. K1 does not change that status. Even after K1 PASS, S1 remains unauthorized until K2 qualifies the m=2/L3 evaluator, gauge, capacity/null separation, fitter, proposal partial credit, evaluator cost/fidelity, and calibration-only response feasibility, followed by K3 S0 adjudication.

## Private commitment verification

`verify_p13_s0_k1_private_commitments.sh` verifies only existence, mode and archive SHA against the public commitments. It does not extract or expose private payload content.
