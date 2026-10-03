# P13-S2-K0 — DEVELOPMENT Coefficient Opening and Protocol Lock

**Role:** formal S2 entry/opening stage.  
**Authorization:** operator-transfer path only. Candidate response remains blocked.

## Scientific action

K0 opens exactly the four precommitted `DEVELOPMENT_COEF` coefficient fields and freezes the operator-transfer execution contract. It does not evaluate candidate quality and does not open DEVELOPMENT response or any SEALED payload.

The formal S2 cohort remains exactly:

- 2307 clear `OPERATOR_QUALIFIED_TRAIN` branches -> all enter K1;
- 80 S1 numerical-boundary branches -> `UNRESOLVED / REFERENCE`, not FAIL and not S2 eligible.

Exact equivalence may share later execution only; it never deletes scientific branch identities.

## Opening discipline

The committed coefficient archive is verified by bytes, SHA-256, payload semantic digest, field IDs, grids and per-object SHA/semantic digest. Only `search_objects/*.npz` are extracted into the K0 authoritative run. Private master seed material and generator JSON are read only as needed for in-memory commitment verification and are not persisted into the active tree.

K0 deliberately does **not stat, hash, open or extract**:

- `DEVELOPMENT_RESPONSE` archive;
- `SEALED_FINAL_COEF` archive;
- `SEALED_FINAL_RESPONSE` archive.

Their commitments remain metadata in the frozen PF1 S2 input manifest.

## Numerical ambiguity

Inherit `tau_num=0.005` unchanged. For any frozen hard ratio boundary `q`:

- clear PASS: `value < q*(1-tau_num)`;
- numerical-boundary UNRESOLVED: `q*(1-tau_num) <= value <= q*(1+tau_num)`;
- clear FAIL: `value > q*(1+tau_num)`.

`tau_num` is ambiguity-only and never relaxes the scientific boundary. K1 must measure DEV G33/G65 discrepancy prospectively. K0 does not invent a post-hoc fraction of unresolved candidates at which fidelity would be declared inadequate. If an explicit fidelity repair is later required, it must be a new preregistered repair with unchanged `tau_num` and the same added fidelity treatment for the complete relevant cohort; candidate-specific rescue is forbidden.

## Frozen K1/K2 contract

K1 evaluates all 2307 branches on all four DEV fields with same raw AST, same theta, same deterministic gauge and zero refit. III-B remains:

1. F0-F4 valid on every DEV field;
2. all required operator quantities numerically resolved;
3. DEV family RMS passes the unchanged `0.5 x identity-family` boundary under inherited ambiguity semantics;
4. no DEV field is clearly worse than its own identity.

`rho_transfer`, absolute J, TRAIN-to-DEV degradation and PF0 radial/ASP associations are descriptive only.

No proxy/top-k/Pareto/percentile/target-survivor/diagnostic-filter rule is permitted.

K2 freezes PASS/UNRESOLVED/FAIL for every branch. Zero clear PASS stops the response path. If clear PASS is nonempty, the entire clear PASS cohort becomes response eligible; no narrowing.

## Runtime/data policy

K0 is a small single-coordinator transactional stage. Heavy S2 work inherits 17 CPUs = 16 workers + 1 coordinator, BLAS/OpenMP threads = 1, progress/ETA and restart/resume. Opened DEV coefficient search objects are extracted once at K0 and reused in place by K1/K2; compact audits contain only manifests and hashes, never duplicated arrays.
