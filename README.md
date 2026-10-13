# Symbolic PDE Transformation Discovery

Reproduction software and frozen inputs for
**A Symbolic-Learning Framework for PDE Transformation Discovery**.

## Verified release and scope

Use **`v1.0.2-paper-submission`**, commit
`39da3f998de8653d7bdf314bfd0d6abb74d3c438`, to reproduce the paper-associated P13
study. A fresh public clone of this exact release completed the full S0-S3
workflow in October 2026, including a scheduler restart, with no source repair or
manual scientific-state change during the execution lineage. Post-run review
accepted its claim-level result under the frozen numerical criteria.

This README on `main` is updated documentation. The tested executable authority
is the immutable tag above, not an untested later `main` commit. Checkout the tag
before running. No private repository, author-specific HPC path, or historical
generated result is required.

The workflow studies constructive discovery of finite-local symbolic coordinate
transformations, zero-refit coefficient transfer, and solution-field accuracy.
The historical SEALED inputs are already public. Replaying them is **not a new
prospective holdout experiment or an independent statistical replication**.
Identical historical candidate counts are not acceptance targets.

Code is BSD-3-Clause; the five frozen input archives are CC BY 4.0.
Paper authors, preprint and DOI: see `CITATION.md` for current metadata.

## Requirements

- Linux x86_64, Bash, Git, and conda or mamba.
- The exact public conda-forge package lock:
  `environment/public-release-explicit.txt`.
- 17 allocated CPUs for stages using 16 workers and one coordinator.
  One BLAS/OpenMP thread per process is mandatory.
- Persistent storage shared across scheduler allocations. Do not use a
  node-local temporary directory for the environment, staged inputs or work root.

The validated numerical environment uses Python 3.10.19, NumPy 2.2.6,
SciPy 1.15.2, threadpoolctl 3.6.0 and OpenBLAS 0.3.30 (pthreads). The package
lock also records exact build identities. Other environments are not certified
by the completed run. Setting `NSLOTS` does not allocate CPUs; obtain resources
through your site's scheduler or use a suitably provisioned workstation.

This is a substantial scientific computation, not a quick demonstration.
The completed run used two coordinator invocations. Observed elapsed spans
include scheduler waits and are not portable CPU-time estimates.

## 1. Fresh clone, environment and checks

Run the following in a Bash session with the CPU resources described above.
Set `TD_RUN_PARENT` to an absolute persistent scratch/storage directory when
appropriate; the default is a new directory under your home folder.
Site-specific commands such as `module load mamba` belong before this block.

The block runs in a subshell: a failed check stops setup without closing an
interactive allocation. It creates a unique run directory and prints the exact
`RUN_INFO` path needed for execution and every later resume.

```bash
(
set -euo pipefail
umask 022

RUN_PARENT="${TD_RUN_PARENT:-$HOME/td-reproduction-runs}"
case "$RUN_PARENT" in
  /*) ;;
  *) echo "TD_RUN_PARENT must be absolute" >&2; exit 2 ;;
esac
mkdir -p "$RUN_PARENT"
BASE="$(mktemp -d "$RUN_PARENT/p13-v102-XXXXXXXX")"

SRC="$BASE/Symbolic-PDE-Transformation-Discovery"
ENV_DIR="$BASE/td-repro-env"
ASSET_STAGE="$BASE/public-inputs"
WORK_ROOT="$BASE/public-work"
LOG="$BASE/public-e2e.log"
RUN_INFO="$BASE/P13_PUBLIC_V102_RUN_INFO.env"
PUBLIC_TAG="v1.0.2-paper-submission"
PUBLIC_COMMIT="39da3f998de8653d7bdf314bfd0d6abb74d3c438"

git clone https://github.com/huqy/Symbolic-PDE-Transformation-Discovery.git "$SRC"
git -C "$SRC" checkout --detach "$PUBLIC_TAG"
test "$(git -C "$SRC" rev-parse HEAD)" = "$PUBLIC_COMMIT"
test -z "$(git -C "$SRC" status --porcelain)"

if command -v mamba >/dev/null 2>&1; then
  mamba create -y -p "$ENV_DIR" --file "$SRC/environment/public-release-explicit.txt"
elif command -v conda >/dev/null 2>&1; then
  conda create -y -p "$ENV_DIR" --file "$SRC/environment/public-release-explicit.txt"
else
  echo "Load or install conda/mamba before setup" >&2
  exit 2
fi

export P13_PYTHON="$ENV_DIR/bin/python"
export PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
export NSLOTS="${SLURM_CPUS_PER_TASK:-${NSLOTS:-17}}"

{
  for var in BASE SRC ENV_DIR ASSET_STAGE WORK_ROOT LOG PUBLIC_TAG PUBLIC_COMMIT; do
    printf 'export %s=%q\n' "$var" "${!var}"
  done
} > "$RUN_INFO"

cd "$SRC"
"$P13_PYTHON" -B -m reproduce.verify_baseline
"$P13_PYTHON" -B -m unittest discover -s reproduce -t . -p 'test*.py' -v \
  2>&1 | tee "$BASE/public-tests.log"
"$P13_PYTHON" -B -m reproduce.frozen_unit_tests \
  2>&1 | tee "$BASE/frozen-tests.log"
"$P13_PYTHON" -B -m reproduce.stage_public_assets --destination "$ASSET_STAGE"
"$P13_PYTHON" -B -m reproduce.verify_public_assets --staging-root "$ASSET_STAGE"
test -z "$(git status --porcelain)"

bash reproduce/run_public_reproduction.sh \
  --dry-run --work-root "$WORK_ROOT" --staging-root "$ASSET_STAGE" \
  2>&1 | tee "$BASE/public-dry-run.log"

printf '\nSETUP=PASS\nRUN_INFO=%s\n' "$RUN_INFO"
)
```

The release has 118 public engineering tests and 62 frozen pure tests.
All must pass. Setup and dry-run do not perform the full scientific computation.
The five archives are copied and hashed as opaque bytes, not extracted by staging.
Their legacy `PRIVATE` filename token describes their original experiment role,
not their present access restrictions.

**Source, environment, input staging and generated work are separate directories.**
Do not pre-create `public-work`, substitute an old stage output, or reuse a work
root from a previous release.

## 2. Execute S0-S3

Set `RUN_INFO` to the absolute filename printed by successful setup. Keep it
outside the source repository and retain it until all review is complete.

Read `docs/reproducibility/HISTORICAL_REPLAY_BRANCH_B.md` in the tagged clone.
The explicit opt-in below authorizes historical SEALED replay before S0 begins.
It is stored in immutable execution identity. It does not assign a scientific
claim PASS, and it cannot be added or changed later.

```bash
RUN_INFO="/absolute/path/printed/by/setup/P13_PUBLIC_V102_RUN_INFO.env"

(
set -euo pipefail
source "$RUN_INFO"
export P13_PYTHON="$ENV_DIR/bin/python"
export PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
export NSLOTS="${SLURM_CPUS_PER_TASK:-${NSLOTS:-17}}"

test "$(git -C "$SRC" rev-parse HEAD)" = "$PUBLIC_COMMIT"
test -z "$(git -C "$SRC" status --porcelain)"
test ! -e "$WORK_ROOT"
cd "$SRC"

{
  echo "START_UTC=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "PUBLIC_TAG=$PUBLIC_TAG"
  echo "PUBLIC_COMMIT=$PUBLIC_COMMIT"
  echo "HOST=$(hostname)"
  echo "NSLOTS=$NSLOTS"
  echo "WORK_ROOT=$WORK_ROOT"
} | tee "$LOG"

set +e
bash reproduce/run_public_reproduction.sh \
  --execute --authorize-historical-sealed-replay \
  --work-root "$WORK_ROOT" --staging-root "$ASSET_STAGE" \
  2>&1 | tee -a "$LOG"
rc=${PIPESTATUS[0]}
printf 'RUN_RC=%s\n' "$rc" | tee -a "$LOG"
exit "$rc"
)
```

The coordinator runs S0 qualification, S1 TRAIN discovery, S2 DEVELOPMENT
operator/response certification, then S3 historical SEALED replay.

At S2-to-S3 it verifies frozen readiness and records
`HISTORICAL_SEALED_REPLAY_ENTRY_AUTHORIZED`. Legitimate diagnostic
operator-UNRESOLVED branches remain unpromoted; uncertainty in mandatory global
readiness still stops entry. Without initial opt-in, S3 is deliberately blocked.

Stage-local messages such as `S1_COMPLETE; STOP before S2` describe the isolated
stage launcher. The public coordinator handles the authorized transitions.

## 3. Resume after a scheduler time limit

Obtain another suitable allocation. Use the same `RUN_INFO`, source, environment,
input staging and work root. Do not clone again, reinstall, change code, or copy
outputs from another run. Compatible Linux x86_64 nodes can have different
OpenBLAS CPU architecture observations; package/build/thread and input identity
remain locked and full observations are recorded.

**Dry-run must not include the opt-in flag. Every execute/resume must include it.**

```bash
RUN_INFO="/absolute/path/from/your/original/setup/P13_PUBLIC_V102_RUN_INFO.env"

(
set -euo pipefail
source "$RUN_INFO"
export P13_PYTHON="$ENV_DIR/bin/python"
export PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
export NSLOTS="${SLURM_CPUS_PER_TASK:-${NSLOTS:-17}}"

test "$(git -C "$SRC" rev-parse HEAD)" = "$PUBLIC_COMMIT"
test -z "$(git -C "$SRC" status --porcelain)"
cd "$SRC"
bash reproduce/run_public_reproduction.sh \
  --dry-run --resume --work-root "$WORK_ROOT" --staging-root "$ASSET_STAGE"

{
  echo "RESUME_UTC=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "HOST=$(hostname)"
  echo "NSLOTS=$NSLOTS"
} | tee -a "$LOG"

set +e
bash reproduce/run_public_reproduction.sh \
  --execute --authorize-historical-sealed-replay --resume \
  --work-root "$WORK_ROOT" --staging-root "$ASSET_STAGE" \
  2>&1 | tee -a "$LOG"
rc=${PIPESTATUS[0]}
printf 'RUN_RC=%s\n' "$rc" | tee -a "$LOG"
exit "$rc"
)
```

A scheduler interruption is not a scientific FAIL. A missing receipt, source or
input drift, failed numerical prerequisite, or unresolved mandatory gate is
different: preserve the log and work root and inspect the stated reason.
Do not edit locks, relax thresholds, force membership, or patch a running release.

## 4. Inspect completion and preserve evidence

A successful coordinator traversal prints
`COMPLETE_PENDING_EXTERNAL_CLAIM_AUDIT`. Check the recorded chain:

```bash
(
set -euo pipefail
source "$RUN_INFO"
"$ENV_DIR/bin/python" -B - <<'PY'
import json
import os
from pathlib import Path

state = json.loads(
    (Path(os.environ["WORK_ROOT"]) / "public_execution.json").read_text()
)
assert state["schema"] == "P13_PUBLIC_EXECUTION_V3"
assert state["identity"]["source_commit"] == os.environ["PUBLIC_COMMIT"]
assert state["identity"]["historical_replay_authorization"]["opt_in"] is True
assert list(state["completed"]) == ["S0", "S1", "S2", "S3"]
assert any(
    e.get("decision") == "HISTORICAL_SEALED_REPLAY_ENTRY_AUTHORIZED"
    for e in state["events"]
)
print("PUBLIC_E2E_CHAIN=COMPLETE")
print("R_C=REQUIRES_POST_RUN_SCIENTIFIC_REVIEW")
PY
)
```

This is a completion check, not a substitute for scientific adjudication.
Review the receipt-bound operator/response decision maps, complete eligible
cohort, all 32 cases, reference/control certification, uncertainty intervals and
diagnostic roles. The frozen response criterion uses a numerical error envelope,
not a rigorous analytic error bound. Preserve PASS, FAIL and UNRESOLVED.

Retain `public_execution.json`, all stage receipts, source identities, result
manifests and the log. Large generated stores need not be copied into Git.
Any published compact result pack is reference evidence only and must never be
used by the runner as a membership target or historical-output fallback.

## Observed results and interpretation

These are frozen observations, not required output counts:

| Evidence | Original prospective study | Staged reconstruction | Clean public v1.0.2 |
| --- | --- | --- | --- |
| TRAIN FULL clear / unresolved | 2307 / 80 | 2444 / 89 | 2444 / 89 |
| TRAIN NULL clear / unresolved | 0 / 0 | 0 / 0 | 0 / 0 |
| DEVELOPMENT operator P/U/F | 1955 / 4 / 348 | 2038 / 6 / 400 | 2038 / 6 / 400 |
| DEVELOPMENT response P/U/F | 1955 / 0 / 0 | 2038 / 0 / 0 | 2038 / 0 / 0 |
| Historical SEALED operator P/U/F | 423 / 27 / 1505 | 451 / 26 / 1561 | 451 / 26 / 1561 |
| Historical SEALED response P/U/F | 3 / 0 / 420 | 4 / 0 / 447 | 4 / 0 / 447 |

The clean public run recovered the same four certified branches as the staged
reconstruction, including the three originally certified branches. The fourth
has a different structure from the original point-power family. The four
branches are not four independent statistical discoveries.

The original prospective study remains the primary scientific evidence.
Subsequent runs test computational reproducibility on the same historical
populations, seeds and numerical criteria. They cannot be pooled as independent
holdout samples. Exact equality of all generated artifacts has not been
established; specific matching censuses and branch identities do not imply it.

The claim is constructive feasibility under the declared finite-local grammar
and coefficient regime. It is not search completeness, near-capacity attainment,
arbitrary-coefficient universality, broad population reliability, or a demonstrated
speedup from an independently implemented reduced solver.

## Versions, data and citation

- `v1.0.2-paper-submission` is the completed public E2E execution authority.
- `v1.0.0` has a known S1-to-S2 dependency blocker.
- `v1.0.1` repairs dependency/resume issues but stops at S2-to-S3 on a missing
  claim-status field. These earlier immutable tags remain historical references.
- Documentation on `main` may be updated without changing any existing tag.
  Always record the exact tag and commit actually executed.

The [v1.0.2 Release](https://github.com/huqy/Symbolic-PDE-Transformation-Discovery/releases/tag/v1.0.2-paper-submission) provides the
[complete compact scientific evidence capsule](https://github.com/huqy/Symbolic-PDE-Transformation-Discovery/releases/download/v1.0.2-paper-submission/P13_V102_VALIDATED_E2E_COMPACT_EVIDENCE_20261012.tar.xz),
[member manifest](https://github.com/huqy/Symbolic-PDE-Transformation-Discovery/releases/download/v1.0.2-paper-submission/P13_V102_VALIDATED_E2E_COMPACT_EVIDENCE_20261012.manifest.json), and
[SHA-256 sidecar](https://github.com/huqy/Symbolic-PDE-Transformation-Discovery/releases/download/v1.0.2-paper-submission/P13_V102_VALIDATED_E2E_COMPACT_EVIDENCE_20261012.sha256).
The capsule is 12,937,836 bytes and contains 436 files. Its SHA-256 is
`10ec4f6d5abec9d997eb5912b91d5c9c9b8a12f88e353ede16e1b278bcb63ba9`.

It includes complete formal and diagnostic decisions, retained clear/unresolved
identities and AST/theta values, all eligible response scalar measurements,
reference/control certificates and four final dossiers. Private paths are mapped
to logical identifiers with separate original/projected hashes. Projected receipts
are reference derivatives, never resume inputs or runtime membership targets.
Large arrays and bulk unselected registries remain outside this compact capsule.
User-owned result data are CC BY 4.0; included notices preserve existing attribution.

Authenticated and anonymous downloads were verified against all member hashes.
A tag and a GitHub Release attachment are distinct: the tested code tag remains
fixed when evidence is added. The five exact inputs are already in
`public_assets/`; no private download is needed.

Software: [BSD-3-Clause](LICENSE).
Input archives: [CC BY 4.0](public_assets/DATA_LICENSE.md).
Citation instructions: [CITATION.md](CITATION.md).
The manuscript is licensed separately. An archival DOI may be added later;
none is implied here.
