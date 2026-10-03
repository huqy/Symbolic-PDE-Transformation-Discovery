#!/usr/bin/env bash
set -euo pipefail
ROOT="${1:-$(pwd)}"
cd "$ROOT"
MARKER="phases/p13/coefficient_law_raw_xt/runs/LATEST_P13_S1_K0R_RUN.txt"
[[ -f "$MARKER" ]] || { echo "Missing $MARKER" >&2; exit 2; }
RUN="$(cat "$MARKER")"
[[ -d "$RUN" ]] || { echo "Missing run directory $RUN" >&2; exit 2; }
STATUS="$(cat "$RUN/OVERALL_STATUS.txt")"
[[ "$STATUS" == PASS ]] || { echo "Refusing formal compact audit pack: OVERALL_STATUS=$STATUS" >&2; exit 2; }
STAMP="$(basename "$RUN" | sed 's/^p13_s1_k0r_protocol_lock_//')"
OUT="P13_S1_K0R_PROTOCOL_LOCK_AUDIT_${STAMP}.tar.xz"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
TOP="$TMP/P13_S1_K0R_PROTOCOL_LOCK_AUDIT_${STAMP}"
mkdir -p "$TOP/K0R"
FILES=(
  OVERALL_STATUS.txt NEXT_ACTION.txt audit_summary.json semantic_output_digest.json source_manifest.json runtime_environment.json
  01_active_protocol_lock.json 02_s0_freeze_provenance.json 03_k1_open_inputs.json
  04_p11_inheritance_and_theta_renumber.json 05_paired_initialization.json 06_shared_theta_family_objective.json
  07_v2_kernel_and_saturation.json 08_branch_ledger_equivalence.json 09_tau_continuation_frontiers.json
  10_within_family_private_commitment.json 11_governance_deferred_controls.json 12_data_boundary_no_forbidden_read.json
)
for f in "${FILES[@]}"; do [[ -f "$RUN/$f" ]] && cp "$RUN/$f" "$TOP/K0R/$f"; done
mkdir -p "$TOP/protocol"
cp phases/p13/coefficient_law_raw_xt/configs/p13_s1_k0r_protocol.json "$TOP/protocol/"
cp phases/p13/coefficient_law_raw_xt/docs/P13_S1_K0R_PROTOCOL_LOCK.md "$TOP/protocol/"
cat > "$TOP/README.md" <<EOF2
# P13-S1-K0R compact audit

Role: protocol/implementation qualification evidence only. It contains the ACTIVE protocol SHA lock, S0/K1/P11 provenance checks, own-grammar/common-RNG initialization regression, shared-theta objective test, V2 saturation/fallback ledger regression, branch/equivalence semantics, tau_num and TRAIN-only continuation regression, fresh within-family diagnostic commitment hashes, governance/data-boundary records, runtime/source provenance, and K0R protocol note.

It deliberately excludes K1 authoritative coefficient arrays, all within-family private payload bytes/seeds, OPENED_TRANSFER payload arrays/outcomes, DEVELOPMENT/SEALED payloads, S0 calibration/burn-in/fitter/V2 candidates, formal S1 proposal/candidate ledgers, caches/tmp/checkpoints, __pycache__, large arrays, sparse matrices/LU factors, and upstream audit archives.

No new ACTIVE scientific-input archive is created. Future S1 reads K1 coefficient objects in place through the authoritative marker/manifest and reads only this K0R protocol lock/commitment metadata as qualification provenance.
EOF2
(
  cd "$TOP"
  find . -type f ! -name SHA256SUMS.txt -print0 | LC_ALL=C sort -z | xargs -0 sha256sum > SHA256SUMS.txt
)
tar -C "$TMP" -cJf "$ROOT/$OUT" "$(basename "$TOP")"
sha256sum "$ROOT/$OUT"
echo "$ROOT/$OUT"
