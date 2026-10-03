from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import platform
import sys
import tarfile
import time
from pathlib import Path
from typing import Any

import numpy as np

from .coefficients import canonical_json_bytes, search_object_semantic_digest, sha256_bytes
from .s1_search_primitives import boundary_status


def sha256_path(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def jsonl_count(path: Path) -> int:
    n = 0
    with path.open("rb") as f:
        for line in f:
            if line.strip():
                n += 1
    return n


def rel(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _safe_tar_members(tf: tarfile.TarFile) -> list[tarfile.TarInfo]:
    out = []
    for member in tf.getmembers():
        p = Path(member.name)
        if p.is_absolute() or ".." in p.parts or member.issym() or member.islnk() or member.isdev():
            raise RuntimeError(f"unsafe private coefficient archive member: {member.name}")
        out.append(member)
    return out


def _npz_semantic_from_bytes(data: bytes) -> str:
    with np.load(io.BytesIO(data), allow_pickle=False) as z:
        arrays = {k: np.asarray(z[k]) for k in z.files}
    return search_object_semantic_digest(arrays)


def _payload_semantic_digest(payload: dict[str, Any]) -> str:
    semantic_obj = {k: v for k, v in payload.items() if k != "master_seed_hex"}
    return sha256_bytes(canonical_json_bytes(semantic_obj))


def inspect_and_open_development_coefficient_archive(
    archive: Path,
    commitment: dict[str, Any],
    public_rows: list[dict[str, Any]],
    opened_root: Path,
) -> dict[str, Any]:
    """Open only committed DEVELOPMENT coefficient search objects.

    The sealed coefficient archive is verified before opening. payload_manifest.json is read
    in memory to verify the committed semantic payload, but private master seed material and
    generator JSON are never persisted in the active project tree. Only search_objects/*.npz
    are extracted once for K1/K2 runtime use.
    """
    archive = archive.resolve()
    if not archive.is_file():
        raise FileNotFoundError(f"missing DEVELOPMENT coefficient archive: {archive}")
    observed_bytes = archive.stat().st_size
    observed_sha = sha256_path(archive)
    if observed_bytes != int(commitment["archive_bytes"]):
        raise RuntimeError(f"DEVELOPMENT coefficient archive byte mismatch: {observed_bytes}")
    if observed_sha != commitment["archive_sha256"]:
        raise RuntimeError("DEVELOPMENT coefficient archive SHA mismatch")

    pub_by_id = {r["field_id"]: r for r in public_rows}
    expected_fields = sorted(pub_by_id)
    if len(expected_fields) != int(commitment["row_count"]):
        raise RuntimeError("public DEVELOPMENT coefficient row count mismatch")

    with tarfile.open(archive, "r:xz") as tf:
        members = _safe_tar_members(tf)
        names = {m.name for m in members}
        if "payload_manifest.json" not in names:
            raise RuntimeError("private coefficient payload missing payload_manifest.json")
        f = tf.extractfile("payload_manifest.json")
        if f is None:
            raise RuntimeError("cannot read payload_manifest.json")
        payload = json.loads(f.read().decode("utf-8"))
        if payload.get("schema") != "P13_PRIVATE_COEFFICIENT_PAYLOAD_V1":
            raise RuntimeError("unexpected private coefficient payload schema")
        if payload.get("role") != "DEVELOPMENT_COEF":
            raise RuntimeError("private coefficient payload role mismatch")
        if len(payload.get("rows", [])) != int(commitment["row_count"]):
            raise RuntimeError("private coefficient payload row count mismatch")
        sem = _payload_semantic_digest(payload)
        if sem != commitment["payload_semantic_digest"]:
            raise RuntimeError("private coefficient payload semantic digest mismatch")

        opened_root.mkdir(parents=True, exist_ok=False)
        object_rows: list[dict[str, Any]] = []
        private_manifest_by_id = {r["field_id"]: r for r in payload["rows"]}
        if sorted(private_manifest_by_id) != expected_fields:
            raise RuntimeError("private/public DEVELOPMENT field IDs mismatch")

        for field_id in expected_fields:
            prow = pub_by_id[field_id]
            irow = private_manifest_by_id[field_id]
            private_objs = {int(o["grid"]): o for o in irow["search_objects"]}
            public_objs = {int(o["grid"]): o for o in prow["search_object_commitments"]}
            if sorted(private_objs) != sorted(public_objs):
                raise RuntimeError(f"grid commitment mismatch for {field_id}")
            for grid in sorted(public_objs):
                pvt = private_objs[grid]
                pub = public_objs[grid]
                member_name = pvt["member_path"]
                if member_name not in names or not member_name.startswith("search_objects/"):
                    raise RuntimeError(f"missing/invalid search object member {member_name}")
                extracted = tf.extractfile(member_name)
                if extracted is None:
                    raise RuntimeError(f"cannot read {member_name}")
                data = extracted.read()
                got_sha = hashlib.sha256(data).hexdigest()
                got_sem = _npz_semantic_from_bytes(data)
                for expected_sha in (pvt["sha256"], pub["sha256"]):
                    if got_sha != expected_sha:
                        raise RuntimeError(f"search object SHA mismatch {field_id} G{grid}")
                for expected_sem in (pvt["semantic_digest"], pub["semantic_digest"]):
                    if got_sem != expected_sem:
                        raise RuntimeError(f"search object semantic mismatch {field_id} G{grid}")
                dest = opened_root / field_id / f"{field_id}_G{grid}.npz"
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                object_rows.append({
                    "field_id": field_id,
                    "role": "DEVELOPMENT_COEF",
                    "family": prow.get("family"),
                    "grid": grid,
                    "path": dest.as_posix(),
                    "bytes": len(data),
                    "sha256": got_sha,
                    "semantic_digest": got_sem,
                })

    return {
        "status": "PASS",
        "source_archive": {
            "absolute_path": archive.as_posix(),
            "bytes": observed_bytes,
            "sha256": observed_sha,
            "payload_semantic_digest": commitment["payload_semantic_digest"],
            "role_after_opening": "REFERENCE_COMMITMENT_SOURCE",
        },
        "opened_role": "DEVELOPMENT_COEF",
        "field_count": len(expected_fields),
        "field_ids": expected_fields,
        "search_object_count": len(object_rows),
        "search_objects": object_rows,
        "private_master_seed_persisted": False,
        "private_generator_json_persisted": False,
        "authoritative_after_opening": "opened search objects under this K0 run; K1/K2 read them in place",
    }


def _verify_entry(root: Path, cfg: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    checks: dict[str, Any] = {}
    context = root / cfg["active_context"]
    checks["active_context_exists"] = context.is_file()
    checks["active_context_sha256"] = checks["active_context_exists"] and sha256_path(context) == cfg["active_context_sha256"]

    pf1 = root / cfg["pf1_s2_active_input_manifest"]["path"]
    checks["PF1_S2_manifest_exists"] = pf1.is_file()
    checks["PF1_S2_manifest_sha256"] = checks["PF1_S2_manifest_exists"] and sha256_path(pf1) == cfg["pf1_s2_active_input_manifest"]["sha256"]
    manifest = load_json(pf1) if checks["PF1_S2_manifest_exists"] else {}
    checks["PF1_role"] = manifest.get("role") == "ACTIVE_S2_INPUT_MANIFEST_AFTER_PF0"
    checks["PF1_authoritative_S1_run"] = manifest.get("authoritative_S1_run") == cfg["authoritative_s1_run"]
    checks["same_AST_theta_gauge_zero_refit"] = manifest.get("same_AST_theta_gauge_zero_refit") is True
    checks["branch_reselection_forbidden"] = manifest.get("branch_reselection_forbidden") is True
    checks["PF0_no_membership_authority"] = manifest.get("PF0_has_membership_authority") is False
    checks["response_stage_blocked_at_entry"] = manifest.get("response_stage_blocked_at_entry") is True

    for name in ("clear", "unresolved"):
        lock = cfg["membership_lock"][name]
        path = root / lock["path"]
        checks[f"{name}_membership_exists"] = path.is_file()
        checks[f"{name}_membership_sha256"] = path.is_file() and sha256_path(path) == lock["sha256"]
        checks[f"{name}_membership_count"] = path.is_file() and jsonl_count(path) == int(lock["count"])
        mkey = "clear_membership" if name == "clear" else "unresolved_lineage"
        mrow = manifest.get(mkey, {})
        checks[f"{name}_PF1_manifest_chain"] = (
            mrow.get("path") == lock["path"] and
            mrow.get("sha256") == lock["sha256"] and
            int(mrow.get("count", -1)) == int(lock["count"])
        )

    dev = manifest.get("DEVELOPMENT_coefficient_commitment", {})
    configured = cfg["development_coefficient_commitment"]
    checks["DEV_commitment_role_kind"] = dev.get("role") == "DEVELOPMENT_COEF" and dev.get("kind") == "coefficient"
    checks["DEV_commitment_archive"] = dev.get("archive_absolute_path") == configured["archive_absolute_path"]
    checks["DEV_commitment_bytes_sha_semantic_count"] = (
        int(dev.get("archive_bytes", -1)) == int(configured["archive_bytes"]) and
        dev.get("archive_sha256") == configured["archive_sha256"] and
        dev.get("payload_semantic_digest") == configured["payload_semantic_digest"] and
        int(dev.get("row_count", -1)) == int(configured["row_count"])
    )

    # Deliberately do not touch the response or SEALED private archive paths. Only verify that
    # PF1 carries commitments and that K0's data-boundary declaration forbids access.
    checks["DEV_response_commitment_present_but_unopened"] = manifest.get("DEVELOPMENT_response_commitment", {}).get("kind") == "response"
    checks["SEALED_coef_commitment_present_but_unopened"] = manifest.get("SEALED_coefficient_guard", {}).get("kind") == "coefficient"
    checks["SEALED_response_commitment_present_but_unopened"] = manifest.get("SEALED_response_guard", {}).get("kind") == "response"
    checks["data_boundary_forbids_response_and_sealed_access"] = (
        cfg["data_boundary"]["response_archive_stat_hash_or_extract_in_K0"] is False and
        cfg["data_boundary"]["sealed_archive_stat_hash_or_extract_in_K0"] is False
    )

    checks["tau_num_exact"] = math.isclose(float(cfg["operator_transfer_protocol"]["tau_num"]), 0.005, rel_tol=0.0, abs_tol=0.0)
    checks["tau_is_ambiguity_only"] = cfg["operator_transfer_protocol"]["tau_num_role"] == "ambiguity_only"
    checks["boundary_examples"] = (
        boundary_status(0.4974, 0.5, 0.005) == "CLEAR_PASS" and
        boundary_status(0.5, 0.5, 0.005) == "UNRESOLVED_NUMERICAL_BOUNDARY" and
        boundary_status(0.5026, 0.5, 0.005) == "CLEAR_FAIL"
    )
    checks["no_shortlist_rules"] = all(
        x in cfg["forbidden_membership_or_decision_rules"]
        for x in ["top-k", "Pareto narrowing", "percentile narrowing", "target survivor count"]
    )
    checks["response_still_blocked"] = cfg["response_stage"]["blocked"] is True

    status = "PASS" if all(bool(v) for v in checks.values()) else "FAIL"
    return {
        "stage": "P13-S2-K0_ENTRY_AUDIT",
        "status": status,
        "checks": checks,
        "scientific_blocker": None if status == "PASS" else "ENTRY_PROVENANCE_OR_DATA_BOUNDARY_MISMATCH",
    }, manifest


def _relative_open_manifest(root: Path, obj: dict[str, Any]) -> dict[str, Any]:
    out = json.loads(json.dumps(obj))
    for row in out.get("search_objects", []):
        p = Path(row["path"])
        row["path"] = rel(root, p)
    return out


def _source_manifest(root: Path, paths: list[Path]) -> dict[str, Any]:
    rows = []
    for p in paths:
        rows.append({"path": rel(root, p), "bytes": p.stat().st_size, "sha256": sha256_path(p)})
    return {"files": rows}


def _update_rolling_context(root: Path, record: dict[str, Any]) -> None:
    path = root / "P13_S2_ROLLING_CONTEXT.md"
    if not path.exists():
        write_text(path, "# P13 S2 Rolling Execution Context\n\n**Role:** REFERENCE / HANDOFF living record. The unique ACTIVE protocol remains `P13_COMPREHENSIVE_CONTEXT_S2_ENTRY_PF0_LOCK_20260831.md`.\n\n")
    text = path.read_text(encoding="utf-8")
    marker = "<!-- S2_K0_FORMAL_RESULT -->"
    block = f'''{marker}\n## S2-K0 — DEVELOPMENT coefficient opening + protocol lock\n\n- `OVERALL_STATUS`: **{record['OVERALL_STATUS']}**\n- entry audit: `{record['entry_status']}`\n- active S2 cohort remains: `2307` clear branches\n- S1 unresolved lineage remains: `80` REFERENCE / not S2 eligible\n- opened data: `4 DEVELOPMENT_COEF` coefficient fields only, grids `17/33/65`\n- DEVELOPMENT response opened: `False`\n- SEALED coefficient/response opened: `False`\n- `tau_num=0.005`, role: numerical ambiguity only\n- same AST + same theta + same deterministic gauge + zero refit: `True`\n- exact-equivalence role: execution sharing only\n- response stage remains blocked through K2\n- semantic output digest: `{record['semantic_output_digest']}`\n- authoritative K0 run: `{record['authoritative_K0_run']}`\n- authoritative DEVELOPMENT coefficient manifest: `{record['development_manifest']}`\n- next action: `{record['NEXT_ACTION']}`\n'''
    if marker in text:
        text = text.split(marker)[0].rstrip() + "\n\n" + block
    else:
        text = text.rstrip() + "\n\n" + block
    write_text(path, text)


def run(project_root: Path) -> int:
    root = project_root.resolve()
    home = root / "phases/p13/coefficient_law_raw_xt"
    cfg_path = home / "configs/p13_s2_k0_protocol.json"
    cfg = load_json(cfg_path)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    run_dir = home / "runs" / f"p13_s2_k0_development_open_{stamp}"
    if run_dir.exists():
        raise RuntimeError(f"run directory already exists: {run_dir}")
    k0 = run_dir / "K0_development_open_lock"
    k0.mkdir(parents=True)

    start = time.monotonic()
    print(f"[P13-S2-K0] stage=entry_audit processed=0/4 current=ACTIVE_context elapsed=0.0s", flush=True)
    entry, pf1 = _verify_entry(root, cfg)
    write_json(k0 / "K0_ENTRY_AUDIT.json", entry)
    if entry["status"] != "PASS":
        write_text(run_dir / "K0_OVERALL_STATUS.txt", "FAIL\n")
        write_text(run_dir / "K0_NEXT_ACTION.txt", cfg["next_on_failure"] + "\n")
        print("[P13-S2-K0] OVERALL_STATUS=FAIL", flush=True)
        return 2

    elapsed = time.monotonic() - start
    print(f"[P13-S2-K0] stage=membership_lock processed=1/4 current=2307_clear+80_unresolved elapsed={elapsed:.2f}s", flush=True)
    membership = {
        "status": "PASS",
        "clear": cfg["membership_lock"]["clear"],
        "unresolved": cfg["membership_lock"]["unresolved"],
        "exact_equivalence_role": "execution_sharing_only",
        "scientific_branch_identity_preserved": True,
        "PF0_K2B_K2C_membership_authority": "NONE",
        "S1_rerun_or_reselection": False,
    }
    write_json(k0 / "K0_MEMBERSHIP_PROVENANCE_LOCK.json", membership)

    elapsed = time.monotonic() - start
    print(f"[P13-S2-K0] stage=development_coefficient_open processed=2/4 current=private_committed_archive elapsed={elapsed:.2f}s", flush=True)
    dev_commit = pf1["DEVELOPMENT_coefficient_commitment"]
    opened_abs = k0 / "opened_development_coefficients"
    opened = inspect_and_open_development_coefficient_archive(
        Path(dev_commit["archive_absolute_path"]),
        dev_commit,
        dev_commit["public_row_commitments"],
        opened_abs,
    )
    opened_rel = _relative_open_manifest(root, opened)
    write_json(k0 / "K0_DEVELOPMENT_COEFFICIENT_OPEN_MANIFEST.json", opened_rel)

    elapsed = time.monotonic() - start
    print(f"[P13-S2-K0] stage=protocol_lock processed=3/4 current=tau+zero_refit+data_boundary elapsed={elapsed:.2f}s", flush=True)
    boundary = {
        "status": "PASS",
        "DEVELOPMENT_COEF": "OPENED_SEARCH_OBJECTS_ONLY",
        "DEVELOPMENT_RESPONSE": "SEALED_COMMITTED_UNOPENED",
        "SEALED_FINAL_COEF": "SEALED_COMMITTED_UNOPENED",
        "SEALED_FINAL_RESPONSE": "SEALED_COMMITTED_UNOPENED",
        "response_archive_access_in_K0": "NONE_NOT_STAT_NOT_HASH_NOT_OPEN_NOT_EXTRACT",
        "sealed_archive_access_in_K0": "NONE_NOT_STAT_NOT_HASH_NOT_OPEN_NOT_EXTRACT",
        "private_generator_seed_persisted": False,
        "private_generator_json_persisted": False,
        "response_stage_blocked": True,
    }
    write_json(k0 / "K0_DATA_BOUNDARY_GUARD.json", boundary)

    protocol_lock = {
        "status": "PASS",
        "S2_active_cohort": 2307,
        "S1_unresolved_reference": 80,
        "same_AST_theta_gauge_zero_refit": cfg["zero_shot_contract"],
        "operator_transfer_protocol": cfg["operator_transfer_protocol"],
        "descriptive_only": cfg["descriptive_only"],
        "forbidden_membership_or_decision_rules": cfg["forbidden_membership_or_decision_rules"],
        "response_stage": cfg["response_stage"],
        "runtime_policy": cfg["runtime_policy"],
        "K1_complete_cohort_required": True,
        "K1_shortlist_forbidden": True,
        "K2_zero_clear_PASS_stops_response_path": True,
        "K2_nonempty_clear_PASS_entire_cohort_response_eligible": True,
    }
    write_json(k0 / "K0_S2_OPERATOR_TRANSFER_PROTOCOL_LOCK.json", protocol_lock)

    sources = [
        cfg_path,
        Path(__file__).resolve(),
        home / "scripts/run_p13_s2_k0.sh",
        home / "scripts/package_p13_s2_k0_audit.sh",
        home / "tests/test_p13_s2_k0.py",
        home / "docs/P13_S2_K0_DEVELOPMENT_COEFFICIENT_OPENING_AND_PROTOCOL_LOCK.md",
        root / cfg["active_context"],
    ]
    source_manifest = _source_manifest(root, [p for p in sources if p.is_file()])
    write_json(k0 / "K0_SOURCE_MANIFEST.json", source_manifest)
    runtime = {
        "python": sys.version,
        "numpy": np.__version__,
        "platform": platform.platform(),
        "pid": os.getpid(),
        "OMP_NUM_THREADS": os.environ.get("OMP_NUM_THREADS"),
        "MKL_NUM_THREADS": os.environ.get("MKL_NUM_THREADS"),
        "OPENBLAS_NUM_THREADS": os.environ.get("OPENBLAS_NUM_THREADS"),
        "NUMEXPR_NUM_THREADS": os.environ.get("NUMEXPR_NUM_THREADS"),
        "K0_parallelism": "single coordinator transactional stage",
        "heavy_S2_policy": cfg["runtime_policy"],
    }
    write_json(k0 / "K0_RUNTIME_ENVIRONMENT.json", runtime)

    semantic_basis = {
        "stage": "P13-S2-K0",
        "final_s1_pf1_semantic_digest": cfg["final_s1_pf1_semantic_digest"],
        "membership": membership,
        "opened_development_coefficients": opened_rel,
        "data_boundary": boundary,
        "protocol_lock": protocol_lock,
        "source_manifest": source_manifest,
    }
    sem = sha256_bytes(canonical_json_bytes(semantic_basis))
    write_json(k0 / "K0_SEMANTIC_OUTPUT_DIGEST.json", {"semantic_output_digest": sem, "basis": semantic_basis})

    elapsed = time.monotonic() - start
    next_action = cfg["next_on_pass"]
    summary = {
        "OVERALL_STATUS": "PASS",
        "NEXT_ACTION": next_action,
        "entry_status": entry["status"],
        "semantic_output_digest": sem,
        "authoritative_K0_run": rel(root, run_dir),
        "development_manifest": rel(root, k0 / "K0_DEVELOPMENT_COEFFICIENT_OPEN_MANIFEST.json"),
        "clear_count": 2307,
        "unresolved_count": 80,
        "opened_DEVELOPMENT_coefficient_fields": 4,
        "DEVELOPMENT_response_opened": False,
        "SEALED_opened": False,
        "tau_num": 0.005,
        "same_AST_theta_gauge_zero_refit": True,
        "response_stage_blocked": True,
        "elapsed_seconds": elapsed,
    }
    write_json(k0 / "K0_SCIENTIFIC_SUMMARY.json", summary)
    write_text(run_dir / "K0_OVERALL_STATUS.txt", "PASS\n")
    write_text(run_dir / "K0_NEXT_ACTION.txt", next_action + "\n")
    write_text(home / "runs/LATEST_P13_S2_K0_RUN.txt", rel(root, run_dir) + "\n")
    write_text(home / "runs/LATEST_P13_S2_DEVELOPMENT_COEF_INPUT.txt", rel(root, k0 / "K0_DEVELOPMENT_COEFFICIENT_OPEN_MANIFEST.json") + "\n")
    _update_rolling_context(root, summary)
    print(f"[P13-S2-K0] stage=freeze processed=4/4 current=complete elapsed={elapsed:.2f}s rate=transactional ETA=0s", flush=True)
    print("[P13-S2-K0] OVERALL_STATUS=PASS", flush=True)
    print(f"[P13-S2-K0] semantic_output_digest={sem}", flush=True)
    print(f"[P13-S2-K0] NEXT_ACTION={next_action}", flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-root", type=Path, default=Path.cwd())
    args = ap.parse_args()
    return run(args.project_root)


if __name__ == "__main__":
    raise SystemExit(main())
