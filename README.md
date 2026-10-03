# Symbolic PDE Transformation Discovery

Reproduction software and exact frozen inputs associated with
**A Symbolic-Learning Framework for PDE Transformation Discovery**.
Paper/preprint/DOI: pending final metadata.

The framework discovers symbolic finite-local PDE transformations and tests
operator transfer and causal numerical responses. This first release contains the
frozen P13 first-branch study. It preserves the accepted scientific implementation
and uses independent public Git history with local SHA-256 authentication.

## Quick start

Use Linux x86_64, Git, Bash, and the canonical conda-forge environment. The accepted
staged campaign used Python 3.10.19, NumPy 2.2.6, SciPy 1.15.2, threadpoolctl 3.6.0,
and OpenBLAS 0.3.30 (pthreads). Other numerical environments have not been validated.
The explicit package lock includes build identifiers and hashes.

```bash
git clone https://github.com/huqy/Symbolic-PDE-Transformation-Discovery.git
cd Symbolic-PDE-Transformation-Discovery
git checkout v1.0.0-paper-submission
conda create -n td-repro --file environment/public-release-explicit.txt
conda activate td-repro
export PYTHONDONTWRITEBYTECODE=1
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
python -B -m reproduce.verify_baseline
python -B -m unittest reproduce.test_public_release reproduce.test_release_integrity -v
python -B -m reproduce.frozen_unit_tests
```

Choose absolute paths outside the cloned source tree. Assets, source, and work must
be separate. Replace the example `/tmp` paths with persistent storage for execution.

```bash
python -B -m reproduce.stage_public_assets --destination /tmp/td-inputs
python -B -m reproduce.verify_public_assets --staging-root /tmp/td-inputs
bash reproduce/run_public_reproduction.sh --dry-run --work-root /tmp/td-work --staging-root /tmp/td-inputs
```

Staging copies opaque exact archives without parsing or extraction. Their legacy
`PRIVATE` basename token records the original preregistration role; it is not a
current access restriction. Runtime capabilities still enforce their data roles.

## Execute and resume

Execution is substantial scientific computation. Allocate 17 CPUs (16 workers and
one coordinator where applicable) with one BLAS/OpenMP thread per process. The
accepted S0 alone took 19,667 seconds; later stages include complete cohort and
32-case response evaluations. Plan persistent storage and scheduler time accordingly.

```bash
bash reproduce/run_public_reproduction.sh --execute --work-root /tmp/td-work --staging-root /tmp/td-inputs
bash reproduce/run_public_reproduction.sh --execute --resume --work-root /tmp/td-work --staging-root /tmp/td-inputs
```

The coordinator invokes the accepted stage launchers, preserves failure evidence,
and stops on protocol ambiguity. Resume requires the same source, environment,
staging identity, and authenticated parent receipts. Do not substitute historical
outputs as parents. Use the shell entry points or public coordinator; direct
`python -m reproduce.s0_launcher` execution is disabled at its obsolete ancestry
check. `python -m reproduce.release_entry` provides the equivalent public S0 entry.

Stages: S0 qualification and commitments; S1 TRAIN discovery; S2 DEVELOPMENT
operator transfer and complete response certification; S3 historical SEALED replay.
Progress includes processed/total, stage/candidate/case, elapsed time, rate, and ETA
where supported by the accepted campaign. Frozen worker policies remain unchanged.

## Scientific scope

III-A: constructive TRAIN discovery in the frozen finite-local primitive grammar.
III-B: same AST, shared parameters, deterministic gauge, and zero-refit DEVELOPMENT
operator transfer. III-C: independent complete DEVELOPMENT response certification.
III-D: zero-refit historical SEALED operator and complete response reproduction.
The full claim is constructive feasibility within the declared representation.

This is not a search completeness theorem, capacity attainment claim, broad
population reliability estimate, or new untouched sealed-holdout validation.
Historical SEALED data had already been opened. Diagnostic branches never acquire
formal membership authority. Preserve UNRESOLVED; there is no response-aware
refit, target survivor count, candidate rescue, or diagnostic promotion.

## Descriptive frozen evidence

| Evidence | Fresh accepted census |
| --- | --- |
| S1 TRAIN FULL clear / unresolved | 2444 / 89 |
| S1 TRAIN NULL clear / unresolved | 0 / 0 |
| S2 DEVELOPMENT operator PASS / UNRESOLVED / FAIL | 2038 / 6 / 400 |
| S2 DEVELOPMENT response PASS / UNRESOLVED / FAIL | 2038 / 0 / 0 |
| S3 historical SEALED operator PASS / UNRESOLVED / FAIL | 451 / 26 / 1561 |
| S3 historical SEALED response PASS / UNRESOLVED / FAIL | 4 / 0 / 447 |

These observations are descriptive, never expected counts, tolerances, or runtime
targets. `reference_results/` is excluded from runtime source snapshots and integrity
inputs. No candidate arrays or historical generated stores are published.

The accepted staged campaign includes documented engineering repairs: S0 dispatcher
compatibility; S1 atomic administrative markers, scoped cleanup, and projected K0
interface; S3 atomic marker write permissions. See
`docs/reproducibility/ENGINEERING_LINEAGE.md`. Local reproduction passed with this
lineage; byte/mode integrity passed; external claim review passed as CLAIM_COMPATIBLE.
An independent heavy integrated run from this new public commit has not been
performed. Integrated orchestration is tested with synthetic control flow only.

## Licenses and citation

Software: BSD-3-Clause (`LICENSE`). Five frozen input archives: CC BY 4.0
(`public_assets/DATA_LICENSE.md`). Manuscript licensing is separate.
Please cite the associated paper and tagged release (`CITATION.md`). Archive this
immutable tag later as described in `docs/ARCHIVAL_RELEASE.md`.
