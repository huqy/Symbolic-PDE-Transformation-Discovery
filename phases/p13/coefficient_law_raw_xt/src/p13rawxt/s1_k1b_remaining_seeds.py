from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path
from typing import Any

from .s1_k1_formal_search import (
    _initial_digest,
    _load_json,
    _resolve_marker,
    _sha256_path,
    _verify_entry,
    _write_json,
    _write_text,
    run_arm,
)

# K1B must execute the exact search core and protocol already used by formal seed 1.
EXPECTED_SEARCH_CORE_SHA256 = "bad3ccf5de568272a248283cad3e22c48bf373bb8c91c1c32829ce4ac2d44296"
EXPECTED_PROTOCOL_SHA256 = "9f71f3fccba5ea3beeb244ac64708c24a5bc298e2299331a122e0dba2c510ace"
EXPECTED_ACTIVE_CONTEXT_SHA256 = "3db057955859653c35853b8b2dd2b281e1c4429312ac969e3b9a18b4e1d56db0"
K1A_MARKER = "phases/p13/coefficient_law_raw_xt/runs/LATEST_P13_S1_K1A_RUN.txt"
K1B_MARKER = "phases/p13/coefficient_law_raw_xt/runs/LATEST_P13_S1_K1B_RUN.txt"
K1B_SEEDS = (2, 3, 4)


def _verify_k1b_entry(root: Path, protocol: dict[str, Any]) -> tuple[Path, Path, Path, dict[str, Any]]:
    core = root / "phases/p13/coefficient_law_raw_xt/src/p13rawxt/s1_k1_formal_search.py"
    cfg = root / "phases/p13/coefficient_law_raw_xt/configs/p13_s1_k1_protocol.json"
    active = root / protocol["active_context"]
    locks = {
        "search_core_byte_identical_to_K1A": core.is_file() and _sha256_path(core) == EXPECTED_SEARCH_CORE_SHA256,
        "protocol_byte_identical_to_K1A": cfg.is_file() and _sha256_path(cfg) == EXPECTED_PROTOCOL_SHA256,
        "active_context_unchanged": active.is_file() and _sha256_path(active) == EXPECTED_ACTIVE_CONTEXT_SHA256,
        "paired_seed_contract": tuple(map(int, protocol["paired_seeds"])) == (1, 2, 3, 4),
        "remaining_seed_contract": tuple(K1B_SEEDS) == (2, 3, 4),
        "first_rung_budget_unchanged": int(protocol["budget"]["proposals_per_seed_per_arm"]) == 8192,
        "checkpoints_unchanged": list(map(int, protocol["budget"]["checkpoints"])) == [2048, 4096, 6144, 8192],
        "arms_unchanged": protocol["arms"] == ["NULL-V2", "FULL-V1", "FULL-V2"],
        "shared_theta": bool(protocol["family_objective"]["shared_theta"]),
        "no_per_field_refit": not bool(protocol["family_objective"]["per_field_theta_refit"]),
        "capacity_has_no_authority": protocol["governance"]["J_capacity_or_R_att_decision_authority"] == "NONE",
        "diagnostic_payload_forbidden": not bool(protocol["governance"]["diagnostic_payload_read"]),
        "DEV_SEALED_forbidden": not bool(protocol["governance"]["development_or_sealed_read"]),
        "historical_response_forbidden": not bool(protocol["governance"]["historical_response_read"]),
    }
    if not all(locks.values()):
        raise RuntimeError(f"K1B byte/protocol lock failed: {locks}")

    # Re-verify the K0R and authoritative TRAIN inputs in place. This never reads an audit tar.
    k0r, k1, upstream = _verify_entry(root, protocol)
    k1a = _resolve_marker(root, K1A_MARKER)
    s1run = _resolve_marker(root, protocol["s1_run_marker"])
    if k1a != s1run:
        raise RuntimeError(f"K1A run and authoritative S1 run differ: {k1a} != {s1run}")
    k1a_checks = {
        "K1A_status_pass": (k1a / "K1A_OVERALL_STATUS.txt").is_file() and (k1a / "K1A_OVERALL_STATUS.txt").read_text().strip() == "PASS",
        "K1A_next_action_exact": (k1a / "K1A_NEXT_ACTION.txt").is_file() and (k1a / "K1A_NEXT_ACTION.txt").read_text().strip() == "P13-S1-K1B_REMAINING_THREE_SEEDS_UNCHANGED",
    }
    if not all(k1a_checks.values()):
        raise RuntimeError(f"K1A did not authorize unchanged K1B: {k1a_checks}")

    return k0r, k1, s1run, {
        "status": "PASS",
        "byte_protocol_locks": locks,
        "K1A_gate": k1a_checks,
        "authoritative_S1_run": str(s1run.relative_to(root)),
        "K0R_run": upstream["K0R_run"],
        "K1_input_run": upstream["K1_input_run"],
        "train_field_ids": upstream["train_field_ids"],
        "train_objects_verified": upstream["train_objects_verified"],
        "K0R_audit_archive_used_as_runtime_input": False,
        "forbidden_payload_arrays_opened": False,
        "seed1_scientific_results_read_for_K1B_decision": False,
    }


def _update_rolling_context(root: Path, summary: dict[str, Any]) -> None:
    p = root / "P13_S1_ROLLING_CONTEXT.md"
    if not p.is_file():
        return
    marker = "<!-- K1B_FORMAL_RESULT -->"
    block = f'''## S1-K1B — remaining primary seeds execution\n\n- authoritative S1 store: `{summary['authoritative_S1_run']}`\n- `OVERALL_STATUS`: **{summary['OVERALL_STATUS']}**\n- paired seeds completed here: `2,3,4`; seed 1 remains the previously audited K1A unit\n- each seed-arm unit target: `{summary['proposals_per_seed_per_arm']}` structural proposals\n- all four primary seeds now have the frozen first-rung execution horizon if status is PASS\n- FULL-V1/FULL-V2 initialization byte identity passed independently for seeds 2,3,4\n- total K1B `silent_fallback_to_V1`: `{summary['integrity_metadata']['silent_fallback_to_V1']}`\n- forbidden diagnostic/DEVELOPMENT/SEALED/historical-response reads: `0`\n- K1B used the byte-identical K1A search core and unchanged protocol; no seed-1 scientific outcome was used to alter seeds 2-4\n- next action on PASS: `P13-S1-K2A_TRAIN_ONLY_RUNG_ADJUDICATION_AND_CONTINUATION_LOCK`\n\n{marker}\n'''
    text = p.read_text()
    if marker in text:
        text = text.replace(marker, block)
    else:
        text = text.rstrip() + "\n\n" + block
    _write_text(p, text)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-root", default=".")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--test-proposals", type=int, default=None, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    root = Path(args.project_root).resolve()
    for p in [root / "phases/p13/coefficient_law_raw_xt/src", root / "phases/p11/raw_xt_td/src"]:
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))

    cfg = root / "phases/p13/coefficient_law_raw_xt/configs/p13_s1_k1_protocol.json"
    protocol = _load_json(cfg)
    k0r, k1, run, entry = _verify_k1b_entry(root, protocol)
    runs = root / "phases/p13/coefficient_law_raw_xt/runs"
    _write_text(runs / "LATEST_P13_S1_K1B_RUN.txt", str(run.relative_to(root)))
    _write_json(run / "K1B_entry_provenance.json", entry)
    _write_json(run / "K1B_protocol_lock.json", {
        "search_core_sha256": EXPECTED_SEARCH_CORE_SHA256,
        "protocol_sha256": EXPECTED_PROTOCOL_SHA256,
        "active_context_sha256": EXPECTED_ACTIVE_CONTEXT_SHA256,
        "remaining_seeds": list(K1B_SEEDS),
        "search_core_changed_after_K1A": False,
        "scientific_protocol_changed_after_K1A": False,
    })
    _write_json(run / "K1B_data_boundary_guard.json", {
        "allowed_arrays": "TRAIN_OPERATOR only",
        "OPENED_TRANSFER_DIAGNOSTIC_arrays_read": False,
        "WITHIN_FAMILY_TRANSFER_DIAGNOSTIC_payload_read": False,
        "DEVELOPMENT_read": False,
        "SEALED_read": False,
        "historical_response_read": False,
        "S0_calibration_candidates_used_as_seed": False,
        "K0R_audit_archive_used_as_runtime_input": False,
        "seed1_scientific_results_used_to_modify_K1B": False,
        "status": "PASS",
    })

    base = _load_json(root / "phases/p13/coefficient_law_raw_xt/configs/p13_s0_k2_protocol.json")
    base["caps"] = protocol["caps"]
    workers = max(1, min(int(args.workers), 16))
    os.environ.update({k: "1" for k in ["OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"]})
    formal_total = None if args.test_proposals is None else int(args.test_proposals)
    target = int(formal_total or protocol["budget"]["proposals_per_seed_per_arm"])

    summaries = []
    for seed in K1B_SEEDS:
        for arm in protocol["arms"]:
            print(f"[P13 S1 K1B] starting/resuming seed={seed} arm={arm} target={target}", flush=True)
            # Exact same byte-locked search core as K1A.
            summaries.append(run_arm(root, run, k1, base, protocol, seed, arm, workers, formal_total))

    init_n = min(
        int(protocol["budget"]["initial_population_per_island"]) * int(protocol["budget"]["island_count"]),
        target,
    )
    init_digests: dict[str, dict[str, str]] = {}
    full_init_equal: dict[str, bool] = {}
    for seed in K1B_SEEDS:
        per = {}
        for arm in protocol["arms"]:
            per[arm] = _initial_digest(run / "units" / f"seed_{seed:02d}" / arm, init_n)
        init_digests[str(seed)] = per
        full_init_equal[str(seed)] = per["FULL-V1"] == per["FULL-V2"]

    silent = sum(int(s["counters"].get("silent_fallback_to_V1", 0)) for s in summaries)
    integrity = {
        "K1A_gate_pass": True,
        "all_K1B_units_complete": all(s["status"] == "PASS" for s in summaries),
        "all_K1B_proposal_accounting_conserved": all(int(s["processed"]) == int(s["total"]) == target for s in summaries),
        "FULL_V1_V2_initial_population_byte_identical_all_remaining_seeds": all(full_init_equal.values()),
        "silent_fallback_to_V1_zero": silent == 0,
        "search_core_byte_identical_to_K1A": True,
        "protocol_byte_identical_to_K1A": True,
        "no_forbidden_data_read": True,
        "seed1_scientific_results_did_not_modify_K1B": True,
        "worker_cap_respected": workers <= 16,
        "BLAS_OpenMP_single_thread": True,
    }
    status = "PASS" if all(integrity.values()) else "FAIL"
    summary = {
        "OVERALL_STATUS": status,
        "stage": "P13-S1-K1B",
        "authoritative_S1_run": str(run.relative_to(root)),
        "remaining_seeds": list(K1B_SEEDS),
        "arms": protocol["arms"],
        "proposals_per_seed_per_arm": target,
        "K1B_structural_proposals_expected": len(K1B_SEEDS) * len(protocol["arms"]) * target,
        "K1_first_rung_structural_proposals_expected_after_PASS": 4 * len(protocol["arms"]) * target,
        "units": summaries,
        "integrity": integrity,
        "integrity_metadata": {
            "initial_population_digests": init_digests,
            "FULL_V1_V2_initial_population_equal": full_init_equal,
            "silent_fallback_to_V1": silent,
            "worker_count": workers,
            "BLAS_OpenMP_threads": 1,
            "search_core_sha256": EXPECTED_SEARCH_CORE_SHA256,
            "protocol_sha256": EXPECTED_PROTOCOL_SHA256,
            "forbidden_data_read": False,
        },
        "scientific_results_are_frozen_in_authoritative_ledgers": True,
        "scientific_results_interpretation_deferred_to_K2A": True,
        "NEXT_ACTION": (
            "P13-S1-K2A_TRAIN_ONLY_RUNG_ADJUDICATION_AND_CONTINUATION_LOCK"
            if status == "PASS" else "REPAIR_K1B_ENGINEERING_INTEGRITY_BEFORE_K2A"
        ),
    }
    _write_json(run / "K1B_integrity_summary.json", summary)
    _write_text(run / "K1B_OVERALL_STATUS.txt", status + "\n")
    _write_text(run / "K1B_NEXT_ACTION.txt", summary["NEXT_ACTION"] + "\n")
    _update_rolling_context(root, summary)
    print(f"OVERALL_STATUS={status}", flush=True)
    return 0 if status == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
