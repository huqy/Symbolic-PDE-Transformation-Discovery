# P13-S1-K3R — NULL-control provenance reader repair

## Scope

Pure implementation/provenance-reader repair after K3 stopped after the 12/12 registry scan. No S0/S1 scientific object is regenerated and no DEVELOPMENT/SEALED payload is opened.

## Root cause

The frozen S0-K2R3 `capacity_instrument_lock_k2r3.json` uses the authoritative top-level schema:

- `role = CALIBRATION_ONLY`;
- `forbidden_from_search = true`;
- `instrument_hashes[null_capacity]`;
- `theta[null_capacity]`.

The original K3 reader incorrectly expected a nonexistent nested `null_capacity.role` record.

## Repair

K3 now validates the actual immutable K2R3 schema and carries the NULL response-control structural hash, frozen theta, deterministic pair-builder provenance and no-refit reconstruction rule into the S2 active-input manifest.

## Scientific invariants

Unchanged: grammar, objective, candidate membership, 2307 clear / 80 unresolved cohorts, thresholds, search horizon, diagnostic roles, DEVELOPMENT/SEALED boundary, response-control identity, and S2 compute scale. The repair does not refit or rebuild the S0 NULL calibration object during K3.
