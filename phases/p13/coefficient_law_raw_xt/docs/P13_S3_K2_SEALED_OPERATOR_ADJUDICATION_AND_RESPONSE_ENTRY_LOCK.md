# P13-S3-K2 — SEALED operator adjudication and response-entry lock

K2 is deterministic postprocessing of the complete, frozen S3-K1 operator ledgers. It performs no coefficient evaluation, no response evaluation, and no candidate refit.

In plain language, K1 finished marking two copies of the same coefficient-only exam, at G33 and G65. K2 now applies the already declared marking rule to every candidate. It does not rank candidates or choose a desired cohort size. Every formal candidate that clearly satisfies all five gate components on both grids is retained; every robust clear failure is failed; every boundary or grid disagreement remains unresolved.

The technical correspondence is:

- “same exam rule” = family ratio boundary `0.5`, four per-field ratio boundaries `1.0`, and `tau_num=0.005` ambiguity-only semantics;
- “both copies agree clearly” = G33/G65 component consensus;
- “all five components pass” = one family component plus four per-field components all `CLEAR_PASS` on both grids;
- “kept for the next exam” = every and only formal `SEALED_OPERATOR_PASS` branch enters the response-eligible membership;
- “not allowed to rescue” = the 348 DEVELOPMENT clear-FAIL and 4 DEVELOPMENT-unresolved strata are reported separately and have no promotion or membership authority.

K2 independently verifies the K1 semantic digest, exact ledger hashes and counts, the 1955/348/4 disjoint partition, stored boundary-status arithmetic, numerical-fidelity census, zero-refit inheritance, and the unopened response boundary before adjudicating.

K2 adds no threshold, proxy label, score, top-k rule, Pareto rule, percentile, representative shortlist, target survivor count, or candidate-specific numerical repair. It does not read opened SEALED coefficient NPZ objects; it reads only the frozen K1 ledgers. It does not read, stat, hash, extract, or open `SEALED_FINAL_RESPONSE`.

If the formal clear-PASS count is zero, K2 stops the formal response path. If it is nonzero, the complete clear-PASS membership is frozen as response eligible, but K3 remains blocked. K3 may first open `SEALED_FINAL_RESPONSE` only after K2 audit and explicit user authorization; K3 must then certify references and run identity plus frozen NULL controls before any expensive candidate response campaign.
