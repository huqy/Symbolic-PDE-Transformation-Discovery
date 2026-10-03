from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import platform
import shutil
import sys
import tarfile
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import numpy as np

from .ast_runtime import FieldJetInterpolator
from .calibration_instruments import build_identity_pair
from .coefficients import canonical_json_bytes, search_object_semantic_digest, sha256_bytes
from .family_evaluator import FieldView, evaluate_pair_on_field, load_npz
from .s1_search_primitives import boundary_status


_WORKER: dict[str, Any] = {}


def _sha(path: Path, block: int = 16 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(block), b""):
            h.update(chunk)
    return h.hexdigest()


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _jsonl_count(path: Path) -> int:
    with path.open("rb") as f:
        return sum(1 for line in f if line.strip())


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n")
    tmp.replace(path)


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(value, encoding="utf-8")
    tmp.replace(path)


def _line_at(path: Path, offset: int) -> dict[str, Any]:
    with path.open("rb") as f:
        f.seek(int(offset))
        line = f.readline()
    if not line:
        raise RuntimeError(f"no JSONL row at byte offset {offset}: {path}")
    return json.loads(line)


def _resolve_marker(root: Path, rel: str) -> Path:
    marker = root / rel
    if not marker.is_file():
        raise RuntimeError(f"missing authoritative marker: {rel}")
    raw = marker.read_text(encoding="utf-8").strip()
    target = Path(raw)
    if not target.is_absolute():
        target = root / target
    if not target.exists():
        raise RuntimeError(f"authoritative marker target missing: {raw}")
    return target.resolve()


def _path_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _check_tar_member(member: tarfile.TarInfo) -> None:
    p = Path(member.name)
    if p.is_absolute() or ".." in p.parts:
        raise RuntimeError(f"unsafe SEALED coefficient member path: {member.name}")
    if member.issym() or member.islnk() or member.isdev():
        raise RuntimeError(f"unsafe SEALED coefficient member type: {member.name}")


def _npz_semantic_from_bytes(data: bytes) -> str:
    with np.load(io.BytesIO(data), allow_pickle=False) as z:
        arrays = {key: np.asarray(z[key]) for key in z.files}
    return search_object_semantic_digest(arrays)


def _payload_semantic_digest(payload: dict[str, Any]) -> str:
    public_semantics = {key: value for key, value in payload.items() if key != "master_seed_hex"}
    return sha256_bytes(canonical_json_bytes(public_semantics))


def _validate_partition(
    strata: dict[str, list[dict[str, Any]]], expected_counts: dict[str, int]
) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    all_ids: list[str] = []
    for name, expected in expected_counts.items():
        rows = strata.get(name, [])
        ids = [str(row["scientific_branch_id"]) for row in rows]
        checks[f"{name}_count"] = len(rows) == int(expected)
        checks[f"{name}_unique_ids"] = len(ids) == len(set(ids))
        all_ids.extend(ids)
    checks["cross_stratum_disjoint"] = len(all_ids) == len(set(all_ids))
    checks["complete_2307_partition"] = len(all_ids) == sum(int(v) for v in expected_counts.values()) == 2307
    return {"status": "PASS" if all(checks.values()) else "FAIL", "checks": checks, "all_ids": all_ids}


def _strip_runtime_fields(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {
            key: _strip_runtime_fields(value)
            for key, value in obj.items()
            if key not in {"elapsed_seconds", "worker_wall_seconds", "worker_cpu_seconds"}
        }
    if isinstance(obj, list):
        return [_strip_runtime_fields(value) for value in obj]
    return obj


def _verify_opened_manifest(root: Path, k1: Path, manifest: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    commitment = cfg["sealed_final_coefficient_commitment"]
    opened_root = k1 / cfg["outputs"]["opened_coefficient_subdir"]
    checks["status"] = manifest.get("status") == "PASS"
    checks["role"] = manifest.get("opened_role") == "SEALED_FINAL_COEF"
    checks["field_count"] = int(manifest.get("field_count", -1)) == int(commitment["field_count"])
    checks["field_ids"] = sorted(manifest.get("field_ids", [])) == sorted(commitment["field_ids"])
    checks["object_count"] = int(manifest.get("search_object_count", -1)) == 12
    checks["source_archive"] = (
        manifest.get("source_archive", {}).get("absolute_path") == commitment["archive_absolute_path"]
        and int(manifest.get("source_archive", {}).get("bytes", -1)) == int(commitment["archive_bytes"])
        and manifest.get("source_archive", {}).get("sha256") == commitment["archive_sha256"]
        and manifest.get("source_archive", {}).get("payload_semantic_digest") == commitment["payload_semantic_digest"]
    )
    checks["private_material_not_persisted"] = (
        manifest.get("private_master_seed_persisted") is False
        and manifest.get("private_generator_json_persisted") is False
    )
    checks["response_untouched"] = manifest.get("SEALED_FINAL_RESPONSE") == "SEALED_COMMITTED_UNOPENED"
    object_ok = True
    seen: set[tuple[str, int]] = set()
    for row in manifest.get("search_objects", []):
        key = (str(row.get("field_id")), int(row.get("grid", -1)))
        seen.add(key)
        path = root / str(row.get("path", ""))
        if not _path_within(path, opened_root):
            object_ok = False
            break
        if not path.is_file() or _sha(path) != row.get("sha256"):
            object_ok = False
            break
        arrays = load_npz(path)
        if search_object_semantic_digest(arrays) != row.get("semantic_digest"):
            object_ok = False
            break
    expected_keys = {(fid, grid) for fid in commitment["field_ids"] for grid in commitment["expected_grids"]}
    checks["opened_objects_SHA_semantic"] = object_ok and seen == expected_keys
    return {"status": "PASS" if all(checks.values()) else "FAIL", "checks": checks}


def _open_sealed_coefficient(
    root: Path, k1: Path, public_guard: dict[str, Any], cfg: dict[str, Any]
) -> dict[str, Any]:
    commitment = cfg["sealed_final_coefficient_commitment"]
    manifest_path = k1 / "K1_SEALED_FINAL_COEF_INPUT_MANIFEST.json"
    opened_root = k1 / cfg["outputs"]["opened_coefficient_subdir"]
    event_path = k1 / "K1_COEFFICIENT_OPENING_EVENT.json"

    if manifest_path.is_file():
        manifest = _json(manifest_path)
        review = _verify_opened_manifest(root, k1, manifest, cfg)
        if review["status"] != "PASS":
            raise RuntimeError("existing opened SEALED coefficient manifest failed resume verification")
        return manifest
    if opened_root.exists():
        raise RuntimeError("opened SEALED coefficient directory exists without a PASS manifest; stop for audit")

    _write_json(event_path, {
        "stage": "P13-S3-K1",
        "authorization": cfg["authorization"],
        "event": "AUTHORIZED_SEALED_FINAL_COEF_OPENING_STARTED",
        "SEALED_FINAL_COEF": "OPENING_IN_PROGRESS",
        "SEALED_FINAL_RESPONSE": "SEALED_COMMITTED_UNOPENED",
        "response_archive_access": False,
    })

    archive = Path(commitment["archive_absolute_path"])
    staging_parent = k1 / "opening_work"
    staging_parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="opened_sealed_final_coef.", dir=str(staging_parent)))
    try:
        if not archive.is_file():
            raise RuntimeError(f"missing committed SEALED coefficient archive: {archive}")
        if archive.stat().st_size != int(commitment["archive_bytes"]):
            raise RuntimeError("SEALED coefficient archive byte-count mismatch")
        if _sha(archive) != commitment["archive_sha256"]:
            raise RuntimeError("SEALED coefficient archive SHA-256 mismatch")

        public_rows = public_guard.get("public_row_commitments", [])
        if sha256_bytes(canonical_json_bytes(public_rows)) != commitment["public_row_commitments_digest"]:
            raise RuntimeError("public SEALED coefficient commitment digest mismatch")
        public_by_id = {str(row["field_id"]): row for row in public_rows}
        if sorted(public_by_id) != sorted(commitment["field_ids"]):
            raise RuntimeError("public SEALED coefficient field-ID mismatch")

        object_rows: list[dict[str, Any]] = []
        with tarfile.open(archive, "r:xz") as tf:
            members = tf.getmembers()
            for member in members:
                _check_tar_member(member)
            by_name = {member.name: member for member in members}
            if "payload_manifest.json" not in by_name:
                raise RuntimeError("SEALED coefficient payload missing payload_manifest.json")
            payload_file = tf.extractfile(by_name["payload_manifest.json"])
            if payload_file is None:
                raise RuntimeError("cannot read SEALED coefficient payload manifest")
            payload = json.loads(payload_file.read().decode("utf-8"))
            if payload.get("schema") != "P13_PRIVATE_COEFFICIENT_PAYLOAD_V1":
                raise RuntimeError("unexpected SEALED coefficient payload schema")
            if payload.get("role") != "SEALED_FINAL_COEF":
                raise RuntimeError("SEALED coefficient payload role mismatch")
            if _payload_semantic_digest(payload) != commitment["payload_semantic_digest"]:
                raise RuntimeError("SEALED coefficient payload semantic-digest mismatch")
            private_by_id = {str(row["field_id"]): row for row in payload.get("rows", [])}
            if sorted(private_by_id) != sorted(commitment["field_ids"]):
                raise RuntimeError("private/public SEALED coefficient field-ID mismatch")

            for field_id in sorted(commitment["field_ids"]):
                public = public_by_id[field_id]
                private = private_by_id[field_id]
                if public.get("role") != "SEALED_FINAL_COEF" or public.get("family") != commitment["family"]:
                    raise RuntimeError(f"public role/family mismatch for {field_id}")
                public_objects = {int(row["grid"]): row for row in public.get("search_object_commitments", [])}
                private_objects = {int(row["grid"]): row for row in private.get("search_objects", [])}
                if sorted(public_objects) != commitment["expected_grids"] or sorted(private_objects) != commitment["expected_grids"]:
                    raise RuntimeError(f"grid commitment mismatch for {field_id}")
                for grid in commitment["expected_grids"]:
                    pub = public_objects[int(grid)]
                    pvt = private_objects[int(grid)]
                    member_name = str(pvt["member_path"])
                    member_path = Path(member_name)
                    if (
                        member_name not in by_name
                        or not member_name.startswith("search_objects/")
                        or member_path.suffix != ".npz"
                    ):
                        raise RuntimeError(f"missing or invalid search-object member: {member_name}")
                    extracted = tf.extractfile(by_name[member_name])
                    if extracted is None:
                        raise RuntimeError(f"cannot read search-object member: {member_name}")
                    data = extracted.read()
                    observed_sha = hashlib.sha256(data).hexdigest()
                    observed_semantic = _npz_semantic_from_bytes(data)
                    if observed_sha != pub["sha256"] or observed_sha != pvt["sha256"]:
                        raise RuntimeError(f"search-object SHA mismatch: {field_id} G{grid}")
                    if observed_semantic != pub["semantic_digest"] or observed_semantic != pvt["semantic_digest"]:
                        raise RuntimeError(f"search-object semantic mismatch: {field_id} G{grid}")
                    staged = staging / field_id / f"{field_id}_G{grid}.npz"
                    staged.parent.mkdir(parents=True, exist_ok=True)
                    staged.write_bytes(data)
                    final = opened_root / field_id / staged.name
                    object_rows.append({
                        "field_id": field_id,
                        "role": "SEALED_FINAL_COEF",
                        "family": commitment["family"],
                        "grid": int(grid),
                        "path": final.relative_to(root).as_posix(),
                        "bytes": len(data),
                        "sha256": observed_sha,
                        "semantic_digest": observed_semantic,
                    })

        os.replace(staging, opened_root)
        manifest = {
            "status": "PASS",
            "opened_role": "SEALED_FINAL_COEF",
            "source_archive": {
                "absolute_path": commitment["archive_absolute_path"],
                "bytes": int(commitment["archive_bytes"]),
                "sha256": commitment["archive_sha256"],
                "payload_semantic_digest": commitment["payload_semantic_digest"],
                "role_after_opening": "REFERENCE_COMMITMENT_SOURCE",
            },
            "field_count": len(commitment["field_ids"]),
            "field_ids": list(commitment["field_ids"]),
            "search_object_count": len(object_rows),
            "search_objects": object_rows,
            "private_master_seed_persisted": False,
            "private_generator_json_persisted": False,
            "authoritative_after_opening": "opened search objects under the S3-K1 run; later S3 steps read them in place",
            "SEALED_FINAL_RESPONSE": "SEALED_COMMITTED_UNOPENED",
            "response_archive_access": False,
        }
        _write_json(manifest_path, manifest)
        review = _verify_opened_manifest(root, k1, manifest, cfg)
        if review["status"] != "PASS":
            raise RuntimeError("newly opened SEALED coefficient objects failed post-opening verification")
        _write_json(event_path, {
            "stage": "P13-S3-K1",
            "authorization": cfg["authorization"],
            "event": "AUTHORIZED_SEALED_FINAL_COEF_OPENING_COMPLETED",
            "SEALED_FINAL_COEF": "OPENED_K1_SEARCH_OBJECTS_ONLY",
            "SEALED_FINAL_RESPONSE": "SEALED_COMMITTED_UNOPENED",
            "response_archive_access": False,
            "input_manifest": manifest_path.relative_to(root).as_posix(),
            "input_manifest_sha256": _sha(manifest_path),
        })
        _write_text(
            root / "phases/p13/coefficient_law_raw_xt/runs/LATEST_P13_S3_SEALED_FINAL_COEF_INPUT.txt",
            manifest_path.relative_to(root).as_posix() + "\n",
        )
        return manifest
    except Exception as exc:
        _write_json(event_path, {
            "stage": "P13-S3-K1",
            "authorization": cfg["authorization"],
            "event": "AUTHORIZED_SEALED_FINAL_COEF_OPENING_FAILED_AND_REQUIRES_AUDIT",
            "SEALED_FINAL_COEF": "OPENING_ATTEMPTED_STATE_REQUIRES_AUDIT",
            "SEALED_FINAL_RESPONSE": "SEALED_COMMITTED_UNOPENED",
            "response_archive_access": False,
            "exception_type": type(exc).__name__,
            "exception": str(exc),
        })
        raise


def _verify_entry(
    root: Path, cfg: dict[str, Any]
) -> tuple[Path, dict[str, Any], dict[str, list[dict[str, Any]]], list[dict[str, Any]], dict[str, Any]]:
    checks: dict[str, Any] = {}
    parent = cfg["parent"]
    freeze = root / parent["portable_freeze_filename"]
    checks["portable_freeze"] = freeze.is_file() and _sha(freeze) == parent["portable_freeze_sha256"]

    k0run = _resolve_marker(root, parent["k0_run_marker"])
    checks["K0_marker_target"] = k0run == (root / parent["expected_k0_run"]).resolve()
    checks["K0_status"] = (k0run / "K0_presealed_lock/OVERALL_STATUS.txt").read_text().strip() == "PASS"
    checks["K0_next_action"] = (
        (k0run / "K0_presealed_lock/NEXT_ACTION.txt").read_text().strip()
        == "STOP_AFTER_K0_AWAIT_EXPLICIT_USER_AUTHORIZATION_FOR_S3_K1_SEALED_FINAL_COEF_OPENING"
    )
    for row in parent["k0_files"]:
        path = root / row["path"]
        checks[f"K0_file_{Path(row['path']).name}"] = path.is_file() and _sha(path) == row["sha256"]

    k0out = root / parent["k0_output_dir"]
    k0summary = _json(k0out / "K0_SCIENTIFIC_SUMMARY.json")
    k0semantic = _json(k0out / "K0_SEMANTIC_OUTPUT_DIGEST.json")
    k0parent = _json(k0out / "K0A_PARENT_REPRODUCIBILITY_LOCK.json")
    k0guard = _json(k0out / "K0_NO_SEALED_OPENING_GUARD.json")
    checks["K0_summary"] = (
        k0summary.get("OVERALL_STATUS") == "PASS"
        and int(k0summary.get("formal_membership_count", -1)) == 1955
        and k0summary.get("SEALED_opened") is False
        and k0summary.get("K1_authorized") is False
    )
    checks["K0_semantic"] = k0semantic.get("semantic_output_digest") == parent["k0_semantic_output_digest"]
    checks["K7_lineage"] = k0parent.get("K7_summary", {}).get("semantic_output_digest") == parent["k7_semantic_output_digest"]
    checks["K0_no_SEALED"] = (
        k0guard.get("SEALED_FINAL_COEF_opened") is False
        and k0guard.get("SEALED_FINAL_RESPONSE_opened") is False
        and k0guard.get("private_SEALED_payload_traversed") is False
    )

    strata: dict[str, list[dict[str, Any]]] = {}
    expected_counts: dict[str, int] = {}
    for row in parent["k0_files"]:
        if "stratum" in row:
            strata[row["stratum"]] = _read_jsonl(root / row["path"])
            expected_counts[row["stratum"]] = int(row["count"])
    partition = _validate_partition(strata, expected_counts)
    checks["K0_partition"] = partition["status"] == "PASS"
    checks["K0_membership_authority"] = (
        all(row.get("membership_authority") is True for row in strata["FORMAL_1955"])
        and all(row.get("membership_authority") is False for row in strata["DIAGNOSTIC_DEV_FAIL_348"])
        and all(row.get("membership_authority") is False for row in strata["DIAGNOSTIC_DEV_UNRESOLVED_4"])
    )

    locator_lock = parent["s1_clear_locator"]
    locator_path = root / locator_lock["path"]
    checks["S1_locator_SHA_count"] = (
        locator_path.is_file()
        and _sha(locator_path) == locator_lock["sha256"]
        and _jsonl_count(locator_path) == int(locator_lock["count"])
    )
    locators = _read_jsonl(locator_path)
    locator_ids = [str(row["scientific_branch_id"]) for row in locators]
    checks["S1_locator_unique"] = len(locator_ids) == len(set(locator_ids)) == 2307
    checks["K0_partition_equals_S1_locator"] = set(partition["all_ids"]) == set(locator_ids)

    formal_lock = parent["s2_formal_membership"]
    formal_path = root / formal_lock["path"]
    checks["S2_formal_SHA_count"] = (
        formal_path.is_file()
        and _sha(formal_path) == formal_lock["sha256"]
        and _jsonl_count(formal_path) == int(formal_lock["count"])
    )
    formal_membership = _read_jsonl(formal_path)
    formal_map = {str(row["scientific_branch_id"]): int(row["membership_index"]) for row in formal_membership}
    k0_formal_map = {
        str(row["scientific_branch_id"]): int(row["membership_index"])
        for row in strata["FORMAL_1955"]
    }
    checks["K0_formal_equals_K5_formal"] = formal_map == k0_formal_map and len(formal_map) == 1955

    source_ok = True
    for row in cfg["inherited_live_source_locks"]:
        path = root / row["path"]
        if not path.is_file() or _sha(path) != row["sha256"]:
            source_ok = False
            break
    checks["inherited_live_source_locks"] = source_ok

    s1_manifest_lock = parent["s1_k3_active_input_manifest"]
    s1_manifest_path = root / s1_manifest_lock["path"]
    checks["S1_K3_manifest_SHA"] = s1_manifest_path.is_file() and _sha(s1_manifest_path) == s1_manifest_lock["sha256"]
    s1_manifest = _json(s1_manifest_path)
    public_guard = s1_manifest[s1_manifest_lock["sealed_guard_key"]]
    commitment = cfg["sealed_final_coefficient_commitment"]
    checks["sealed_commitment_role"] = public_guard.get("role") == commitment["role"] and public_guard.get("kind") == commitment["kind"]
    checks["sealed_commitment_archive"] = (
        public_guard.get("archive_absolute_path") == commitment["archive_absolute_path"]
        and int(public_guard.get("archive_bytes", -1)) == int(commitment["archive_bytes"])
        and public_guard.get("archive_sha256") == commitment["archive_sha256"]
        and public_guard.get("payload_semantic_digest") == commitment["payload_semantic_digest"]
    )
    checks["sealed_commitment_public_rows"] = (
        int(public_guard.get("row_count", -1)) == int(commitment["field_count"])
        and sha256_bytes(canonical_json_bytes(public_guard.get("public_row_commitments", [])))
        == commitment["public_row_commitments_digest"]
    )
    checks["sealed_private_not_preexposed"] = (
        public_guard.get("private_payload_copied_into_active_tree") is False
        and public_guard.get("private_seed_material_exposed") is False
    )

    z = cfg["zero_shot_contract"]
    checks["zero_refit_contract"] = all([
        z["same_raw_AST"], z["same_theta"], z["same_deterministic_gauge"], z["zero_refit"],
        not z["AST_refit"], not z["theta_refit"], not z["branch_reselection"], not z["amplitude_compensation"],
    ])
    checks["frozen_operator_contract"] = (
        float(cfg["operator_protocol"]["family_ratio_boundary"]) == 0.5
        and float(cfg["operator_protocol"]["per_field_ratio_boundary"]) == 1.0
        and float(cfg["operator_protocol"]["tau_num"]) == 0.005
        and cfg["operator_protocol"]["evaluation_grids"] == [33, 65]
    )
    checks["authorization"] = cfg.get("authorization") == "EXPLICIT_USER_AUTHORIZATION_AFTER_K0_AUDIT_20260901"
    checks["no_ranking_or_shortlist"] = cfg["cohort_contract"]["execution_order"] == "ORIGINAL_S1_K2A_LOCATOR_ORDER_NOT_DEVELOPMENT_RANKED"
    checks["response_stays_unopened"] = cfg["data_boundary"]["SEALED_FINAL_RESPONSE"] == "SEALED_COMMITTED_UNOPENED"

    status = "PASS" if all(bool(value) for value in checks.values()) else "FAIL"
    return k0run, public_guard, strata, locators, {"status": status, "checks": checks}


def _candidate_task(
    root: Path,
    locator: dict[str, Any],
    source_order: int,
    stratum_row: dict[str, Any],
) -> dict[str, Any]:
    branch = _line_at(root / locator["branch_registry_path"], int(locator["branch_registry_byte_offset"]))
    skeleton = _line_at(root / locator["skeleton_registry_path"], int(locator["skeleton_registry_byte_offset"]))
    sid = str(locator["scientific_branch_id"])
    if branch.get("scientific_branch_id") != sid or stratum_row.get("scientific_branch_id") != sid:
        raise RuntimeError("candidate scientific-branch identity mismatch")
    for obj in (branch, skeleton, stratum_row):
        if obj.get("structural_hash") != locator["structural_hash"]:
            raise RuntimeError(f"candidate structural-hash mismatch: {sid}")
    if int(branch["proposal_index"]) != int(skeleton["proposal_index"]) or int(branch["proposal_index"]) != int(stratum_row["proposal_index"]):
        raise RuntimeError(f"candidate proposal-index mismatch: {sid}")
    if branch.get("arm") != stratum_row.get("arm") or int(branch["paired_seed"]) != int(stratum_row["paired_seed"]):
        raise RuntimeError(f"candidate arm/seed mismatch: {sid}")
    if branch.get("exact_equivalence_class") != stratum_row.get("exact_equivalence_class"):
        raise RuntimeError(f"candidate exact-equivalence mismatch: {sid}")
    if branch.get("operator_qualification", {}).get("status") != "OPERATOR_QUALIFIED_TRAIN":
        raise RuntimeError(f"non-clear TRAIN branch entered K1: {sid}")
    gauge = branch.get("deterministic_gauge", {})
    expected_gauge = {
        "application": "deterministic_per_field",
        "optimized": False,
        "rule": "S0_fixed_translation_common_positive_scale",
    }
    if gauge != expected_gauge:
        raise RuntimeError(f"unexpected frozen gauge semantics: {sid}")
    return {
        "source_order": int(source_order),
        "membership_index": int(stratum_row["membership_index"]),
        "scientific_branch_id": sid,
        "stratum": stratum_row["stratum"],
        "formal_membership_authority": bool(stratum_row["formal_membership_authority"]),
        "arm": branch["arm"],
        "paired_seed": int(branch["paired_seed"]),
        "proposal_index": int(branch["proposal_index"]),
        "structural_hash": branch["structural_hash"],
        "exact_equivalence_class": branch["exact_equivalence_class"],
        "pair": skeleton["pair"],
        "theta": [float(value) for value in branch["theta_vector"]],
        "theta_hex": list(branch["theta_hex"]),
        "fit_provenance": branch["fit_provenance"],
        "deterministic_gauge": gauge,
    }


def _load_views(root: Path, manifest: dict[str, Any], grid: int, probe_grid: int) -> list[FieldView]:
    rows = manifest["search_objects"]
    field_ids = sorted({str(row["field_id"]) for row in rows})
    views: list[FieldView] = []
    for field_id in field_ids:
        main = next(row for row in rows if row["field_id"] == field_id and int(row["grid"]) == int(grid))
        probe = next(row for row in rows if row["field_id"] == field_id and int(row["grid"]) == int(probe_grid))
        arrays = load_npz(root / main["path"])
        probe_arrays = load_npz(root / probe["path"])
        views.append(FieldView(field_id, "SEALED_FINAL_COEF", int(grid), arrays, FieldJetInterpolator(probe_arrays, 4)))
    return views


def _worker_init(root_s: str, manifest: dict[str, Any], base: dict[str, Any]) -> None:
    root = Path(root_s)
    for path in [root / "phases/p13/coefficient_law_raw_xt/src", root / "phases/p11/raw_xt_td/src"]:
        value = str(path)
        if value not in sys.path:
            sys.path.insert(0, value)
    for key in ["OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"]:
        os.environ[key] = "1"
    global _WORKER
    _WORKER = {
        "base": base,
        "G33": _load_views(root, manifest, 33, 65),
        "G65": _load_views(root, manifest, 65, 65),
    }


def _evaluate_grid(task: dict[str, Any], fields: list[FieldView], base: dict[str, Any]) -> dict[str, Any]:
    per_field = [
        evaluate_pair_on_field(
            task["pair"], task["theta"], field,
            base["validity"], float(base["validity"]["inverse_roundtrip_tolerance"]),
            base["operator"]["space"], base["operator"]["numerical"],
        )
        for field in fields
    ]
    values = [None if row.get("J_princ") is None else float(row["J_princ"]) for row in per_field]
    all_f4 = all(int(row.get("stage_index", 0)) == 5 for row in per_field)
    all_resolved = all(value is not None and math.isfinite(float(value)) for value in values)
    family = float(math.sqrt(sum(float(value) ** 2 for value in values) / len(values))) if all_resolved else None
    return {
        "all_F0_F4_valid": all_f4,
        "all_J_resolved": all_resolved,
        "J_i": values,
        "J_family": family,
        "J_max": max(float(value) for value in values) if all_resolved else None,
        "per_field": per_field,
    }


def _worker_eval(task: dict[str, Any]) -> dict[str, Any]:
    wall_start = time.perf_counter()
    cpu_start = time.process_time()
    try:
        base = _WORKER["base"]
        return {
            "source_order": task["source_order"],
            "membership_index": task["membership_index"],
            "scientific_branch_id": task["scientific_branch_id"],
            "stratum": task["stratum"],
            "formal_membership_authority": task["formal_membership_authority"],
            "arm": task["arm"],
            "paired_seed": task["paired_seed"],
            "proposal_index": task["proposal_index"],
            "structural_hash": task["structural_hash"],
            "exact_equivalence_class": task["exact_equivalence_class"],
            "theta_hex": task["theta_hex"],
            "fit_provenance": task["fit_provenance"],
            "deterministic_gauge": task["deterministic_gauge"],
            "SEALED_G33": _evaluate_grid(task, _WORKER["G33"], base),
            "SEALED_G65": _evaluate_grid(task, _WORKER["G65"], base),
            "same_AST_theta_gauge_zero_refit": True,
            "K1_final_decision_authority": False,
            "worker_wall_seconds": time.perf_counter() - wall_start,
            "worker_cpu_seconds": time.process_time() - cpu_start,
        }
    except Exception as exc:
        return {
            "source_order": task["source_order"],
            "membership_index": task["membership_index"],
            "scientific_branch_id": task["scientific_branch_id"],
            "stratum": task["stratum"],
            "formal_membership_authority": task["formal_membership_authority"],
            "fatal_exception_type": type(exc).__name__,
            "fatal_exception": str(exc),
            "same_AST_theta_gauge_zero_refit": True,
            "K1_final_decision_authority": False,
            "worker_wall_seconds": time.perf_counter() - wall_start,
            "worker_cpu_seconds": time.process_time() - cpu_start,
        }


def _identity(fields: list[FieldView], base: dict[str, Any]) -> dict[str, Any]:
    pair = build_identity_pair(base["caps"])
    rows = [
        evaluate_pair_on_field(
            pair, [], field,
            base["validity"], float(base["validity"]["inverse_roundtrip_tolerance"]),
            base["operator"]["space"], base["operator"]["numerical"],
        )
        for field in fields
    ]
    values = [row.get("J_princ") for row in rows]
    if not all(int(row.get("stage_index", 0)) == 5 and value is not None and math.isfinite(float(value)) for row, value in zip(rows, values)):
        raise RuntimeError("SEALED identity baseline is unresolved; stop for cohort-wide numerical audit")
    resolved = [float(value) for value in values]
    return {
        "J_i": resolved,
        "J_family": float(math.sqrt(sum(value * value for value in resolved) / len(resolved))),
        "J_max": max(resolved),
        "per_field": rows,
    }


def _relative_discrepancy(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or not math.isfinite(float(a)) or not math.isfinite(float(b)):
        return None
    return abs(float(a) - float(b)) / max(abs(float(b)), 1e-15)


def _enrich(row: dict[str, Any], identity33: dict[str, Any], identity65: dict[str, Any], tau: float) -> dict[str, Any]:
    out = json.loads(json.dumps(row))
    if "fatal_exception" in out:
        out["K1_measurement_status"] = "IMPLEMENTATION_EXCEPTION_BLOCKER"
        return _strip_runtime_fields(out)
    for label, identity in [("SEALED_G33", identity33), ("SEALED_G65", identity65)]:
        record = out[label]
        record["identity_J_i"] = list(identity["J_i"])
        record["identity_J_family"] = float(identity["J_family"])
        if record["all_F0_F4_valid"] and record["all_J_resolved"]:
            ratios = [float(a) / float(b) for a, b in zip(record["J_i"], identity["J_i"])]
            family_ratio = float(record["J_family"]) / float(identity["J_family"])
            record["field_ratios_to_identity"] = ratios
            record["family_ratio_to_identity"] = family_ratio
            record["frozen_boundary_statuses"] = [boundary_status(family_ratio, 0.5, tau)] + [
                boundary_status(value, 1.0, tau) for value in ratios
            ]
        else:
            record["field_ratios_to_identity"] = None
            record["family_ratio_to_identity"] = None
            record["frozen_boundary_statuses"] = None
    per_field = [
        _relative_discrepancy(a, b)
        for a, b in zip(out["SEALED_G33"]["J_i"], out["SEALED_G65"]["J_i"])
    ]
    family = _relative_discrepancy(out["SEALED_G33"]["J_family"], out["SEALED_G65"]["J_family"])
    valid = [value for value in per_field if value is not None]
    out["G33_G65_numerical_fidelity"] = {
        "per_field_relative_J_discrepancy": per_field,
        "family_relative_J_discrepancy": family,
        "max_per_field_relative_J_discrepancy": max(valid) if len(valid) == 4 else None,
        "tau_num_reference": tau,
        "count_per_field_gt_tau_num": sum(1 for value in valid if value > tau),
        "automatic_candidate_rescue_or_repair_trigger": False,
    }
    out["K1_measurement_status"] = (
        "COMPLETE"
        if out["SEALED_G33"]["all_F0_F4_valid"]
        and out["SEALED_G65"]["all_F0_F4_valid"]
        and out["SEALED_G33"]["all_J_resolved"]
        and out["SEALED_G65"]["all_J_resolved"]
        else "OPERATOR_OR_NUMERICAL_UNRESOLVED_FOR_K2"
    )
    return _strip_runtime_fields(out)


def _quantiles(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    array = np.asarray(values, dtype=float)
    return {
        "min": float(np.min(array)),
        "p10": float(np.quantile(array, 0.10)),
        "median": float(np.median(array)),
        "p90": float(np.quantile(array, 0.90)),
        "p99": float(np.quantile(array, 0.99)),
        "max": float(np.max(array)),
    }


def _aggregate_rows(rows: list[dict[str, Any]], tau: float) -> dict[str, Any]:
    complete = [row for row in rows if row.get("K1_measurement_status") == "COMPLETE"]
    fatal = [row for row in rows if row.get("K1_measurement_status") == "IMPLEMENTATION_EXCEPTION_BLOCKER"]
    family_discrepancy = [
        float(row["G33_G65_numerical_fidelity"]["family_relative_J_discrepancy"])
        for row in complete
        if row["G33_G65_numerical_fidelity"]["family_relative_J_discrepancy"] is not None
    ]
    max_field_discrepancy = [
        float(row["G33_G65_numerical_fidelity"]["max_per_field_relative_J_discrepancy"])
        for row in complete
        if row["G33_G65_numerical_fidelity"]["max_per_field_relative_J_discrepancy"] is not None
    ]
    sealed_ratio65 = [
        float(row["SEALED_G65"]["family_ratio_to_identity"])
        for row in complete
        if row["SEALED_G65"].get("family_ratio_to_identity") is not None
    ]
    return {
        "total_scientific_branches": len(rows),
        "complete_measurements": len(complete),
        "operator_or_numerical_unresolved": len(rows) - len(complete) - len(fatal),
        "fatal_implementation_exceptions": len(fatal),
        "all_SEALED_fields_F4_G33_count": sum(
            1 for row in rows if "SEALED_G33" in row and row["SEALED_G33"]["all_F0_F4_valid"]
        ),
        "all_SEALED_fields_F4_G65_count": sum(
            1 for row in rows if "SEALED_G65" in row and row["SEALED_G65"]["all_F0_F4_valid"]
        ),
        "G33_G65_family_discrepancy": _quantiles(family_discrepancy),
        "G33_G65_max_per_field_discrepancy": _quantiles(max_field_discrepancy),
        "branches_family_discrepancy_gt_tau_num": sum(value > tau for value in family_discrepancy),
        "branches_max_per_field_discrepancy_gt_tau_num": sum(value > tau for value in max_field_discrepancy),
        "SEALED_G65_family_ratio_to_identity": _quantiles(sealed_ratio65),
        "tau_num_reference": tau,
        "count_gt_tau_num_is_not_an_automatic_repair_trigger": True,
        "K1_final_III_D_operator_decisions_frozen": False,
        "K2_adjudication_required": True,
    }


def _source_manifest(root: Path, paths: list[Path]) -> dict[str, Any]:
    rows = []
    seen: set[str] = set()
    for path in paths:
        if not path.is_file():
            continue
        rel = path.resolve().relative_to(root.resolve()).as_posix()
        if rel in seen:
            continue
        seen.add(rel)
        rows.append({"path": rel, "bytes": path.stat().st_size, "sha256": _sha(path)})
    return {"files": rows}


def _shard_path(work: Path, task: dict[str, Any]) -> Path:
    return work / "results" / f"{int(task['source_order']):04d}_{task['scientific_branch_id']}.json"


def _safe_cleanup_work(k1: Path, work: Path) -> None:
    if work.name != "work" or work.parent.resolve() != k1.resolve():
        raise RuntimeError("refusing unsafe K1 work cleanup target")
    if work.exists():
        shutil.rmtree(work)
    opening_work = k1 / "opening_work"
    if opening_work.exists() and opening_work.parent.resolve() == k1.resolve():
        shutil.rmtree(opening_work)


def run(root: Path, workers: int) -> int:
    root = root.resolve()
    home = root / "phases/p13/coefficient_law_raw_xt"
    cfg_path = home / "configs/p13_s3_k1_protocol.json"
    cfg = _json(cfg_path)
    k0run, public_guard, strata, locators, entry = _verify_entry(root, cfg)
    k1 = k0run / cfg["outputs"]["run_subdir"]
    k1.mkdir(parents=True, exist_ok=True)
    _write_json(k1 / "K1_ENTRY_AND_AUTHORIZATION_AUDIT.json", entry)
    if entry["status"] != "PASS":
        _write_text(k0run / "K1_OVERALL_STATUS.txt", "FAIL\n")
        _write_text(k0run / "K1_NEXT_ACTION.txt", cfg["next_on_failure"] + "\n")
        print("P13_S3_K1_FINAL=FAIL", flush=True)
        return 2

    opened_manifest = _open_sealed_coefficient(root, k1, public_guard, cfg)
    opened_review = _verify_opened_manifest(root, k1, opened_manifest, cfg)
    _write_json(k1 / "K1_OPENED_COEFFICIENT_REPRODUCIBILITY_AUDIT.json", opened_review)
    if opened_review["status"] != "PASS":
        raise RuntimeError("opened SEALED coefficient reproducibility audit failed")

    stratum_by_id: dict[str, dict[str, Any]] = {}
    authority = {"FORMAL_1955": True, "DIAGNOSTIC_DEV_FAIL_348": False, "DIAGNOSTIC_DEV_UNRESOLVED_4": False}
    for name, rows in strata.items():
        for row in rows:
            item = dict(row)
            item["stratum"] = name
            item["formal_membership_authority"] = authority[name]
            stratum_by_id[str(item["scientific_branch_id"])] = item
    tasks = [
        _candidate_task(root, locator, index, stratum_by_id[str(locator["scientific_branch_id"])])
        for index, locator in enumerate(locators)
    ]
    if len(tasks) != 2307 or len({task["scientific_branch_id"] for task in tasks}) != 2307:
        raise RuntimeError("K1 requires the complete unique 2307 partition")

    base = _json(root / cfg["operator_protocol"]["base_protocol_path"])
    sealed33 = _load_views(root, opened_manifest, 33, 65)
    sealed65 = _load_views(root, opened_manifest, 65, 65)
    identity33 = _strip_runtime_fields(_identity(sealed33, base))
    identity65 = _strip_runtime_fields(_identity(sealed65, base))
    identity_fidelity = {
        "per_field_relative_J_discrepancy": [
            _relative_discrepancy(a, b) for a, b in zip(identity33["J_i"], identity65["J_i"])
        ],
        "family_relative_J_discrepancy": _relative_discrepancy(identity33["J_family"], identity65["J_family"]),
        "tau_num_reference": float(cfg["operator_protocol"]["tau_num"]),
        "membership_authority": False,
    }
    identity_record = {"G33": identity33, "G65": identity65, "G33_G65_fidelity": identity_fidelity}
    _write_json(k1 / "K1_SEALED_IDENTITY_BASELINES.json", identity_record)

    work = k1 / "work"
    results_dir = work / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    expected_shards = {_shard_path(work, task).name: task for task in tasks}
    unexpected = [path.name for path in results_dir.glob("*.json") if path.name not in expected_shards]
    if unexpected:
        raise RuntimeError(f"unexpected K1 resume shards: {unexpected[:3]}")
    completed: dict[str, dict[str, Any]] = {}
    for name, task in expected_shards.items():
        path = results_dir / name
        if not path.is_file():
            continue
        row = _json(path)
        if row.get("scientific_branch_id") != task["scientific_branch_id"] or int(row.get("source_order", -1)) != int(task["source_order"]):
            raise RuntimeError(f"resume-shard identity mismatch: {path}")
        completed[task["scientific_branch_id"]] = row

    remaining = [task for task in tasks if task["scientific_branch_id"] not in completed]
    start = time.monotonic()
    done0 = len(completed)
    last = start
    print(
        f"[P13-S3-K1] stage=sealed_operator_transfer processed={done0}/2307 "
        "current=resume_lock fields=4 grids=33,65 elapsed=0.0s rate=0/s ETA=NA",
        flush=True,
    )
    if remaining:
        with ProcessPoolExecutor(
            max_workers=workers,
            initializer=_worker_init,
            initargs=(str(root), opened_manifest, base),
        ) as executor:
            futures = {executor.submit(_worker_eval, task): task for task in remaining}
            for future in as_completed(futures):
                task = futures[future]
                row = future.result()
                _write_json(_shard_path(work, task), row)
                completed[task["scientific_branch_id"]] = row
                now = time.monotonic()
                count = len(completed)
                if (
                    count % int(cfg["runtime"]["progress_every_completed"]) == 0
                    or now - last >= float(cfg["runtime"]["progress_every_seconds"])
                    or count == 2307
                ):
                    elapsed = now - start
                    rate = max(count - done0, 0) / max(elapsed, 1e-12)
                    eta = (2307 - count) / max(rate, 1e-12)
                    print(
                        f"[P13-S3-K1] stage=sealed_operator_transfer processed={count}/2307 "
                        f"current_branch={task['scientific_branch_id'][:12]} stratum={task['stratum']} "
                        f"fields=4 grids=33,65 elapsed={elapsed:.1f}s rate={rate:.3f}/s ETA={eta/60:.1f}m",
                        flush=True,
                    )
                    _write_json(work / "checkpoint.json", {
                        "processed": count,
                        "total": 2307,
                        "completed_ids_sha256": sha256_bytes(canonical_json_bytes(sorted(completed))),
                        "elapsed_this_job_seconds": elapsed,
                    })
                    last = now
    if len(completed) != 2307:
        raise RuntimeError(f"incomplete K1 cohort: {len(completed)}/2307")

    tau = float(cfg["operator_protocol"]["tau_num"])
    enriched = [
        _enrich(completed[task["scientific_branch_id"]], identity33, identity65, tau)
        for task in tasks
    ]
    by_stratum = {
        name: [row for row in enriched if row["stratum"] == name]
        for name in ["FORMAL_1955", "DIAGNOSTIC_DEV_FAIL_348", "DIAGNOSTIC_DEV_UNRESOLVED_4"]
    }
    output_names = {
        "FORMAL_1955": cfg["outputs"]["formal_ledger"],
        "DIAGNOSTIC_DEV_FAIL_348": cfg["outputs"]["diagnostic_fail_ledger"],
        "DIAGNOSTIC_DEV_UNRESOLVED_4": cfg["outputs"]["diagnostic_unresolved_ledger"],
    }
    ledger_manifest: dict[str, Any] = {}
    for name, rows in by_stratum.items():
        path = k1 / output_names[name]
        _write_jsonl(path, rows)
        ledger_manifest[name] = {
            "path": path.relative_to(root).as_posix(),
            "count": len(rows),
            "sha256": _sha(path),
            "membership_authority": name == "FORMAL_1955",
            "promotion_or_rescue_authority": False if name != "FORMAL_1955" else None,
        }

    aggregate = {
        "complete_execution_count": len(enriched),
        "execution_order": cfg["cohort_contract"]["execution_order"],
        "strata": {name: _aggregate_rows(rows, tau) for name, rows in by_stratum.items()},
        "ledger_manifest": ledger_manifest,
        "formal_membership_changed": False,
        "diagnostic_promotion_or_rescue": False,
        "K1_final_adjudication_performed": False,
    }
    _write_json(k1 / "K1_OPERATOR_MEASUREMENT_AGGREGATE.json", aggregate)

    fidelity = {
        "identity": identity_fidelity,
        "strata": {
            name: {
                "G33_G65_family_discrepancy": aggregate["strata"][name]["G33_G65_family_discrepancy"],
                "G33_G65_max_per_field_discrepancy": aggregate["strata"][name]["G33_G65_max_per_field_discrepancy"],
                "branches_family_discrepancy_gt_tau_num": aggregate["strata"][name]["branches_family_discrepancy_gt_tau_num"],
                "branches_max_per_field_discrepancy_gt_tau_num": aggregate["strata"][name]["branches_max_per_field_discrepancy_gt_tau_num"],
                "complete_measurements": aggregate["strata"][name]["complete_measurements"],
                "operator_or_numerical_unresolved": aggregate["strata"][name]["operator_or_numerical_unresolved"],
                "fatal_implementation_exceptions": aggregate["strata"][name]["fatal_implementation_exceptions"],
            }
            for name in aggregate["strata"]
        },
        "tau_num_reference": tau,
        "tau_num_role": "NUMERICAL_AMBIGUITY_ONLY_NOT_THRESHOLD_RELAXATION",
        "posthoc_fraction_trigger": False,
        "candidate_specific_rescue": False,
        "repair_if_needed": "COHORT_WIDE_EXPLICIT_REPAIR_BEFORE_K2",
    }
    _write_json(k1 / "K1_NUMERICAL_FIDELITY_CENSUS.json", fidelity)

    guard = {
        "status": "PASS",
        "TRAIN_OPERATOR": "READ_ONLY_FOR_FROZEN_CANDIDATE_PROVENANCE",
        "DEVELOPMENT_COEF": "PRELOCKED_STRATUM_LABELS_ONLY",
        "DEVELOPMENT_RESPONSE_read_by_K1": False,
        "SEALED_FINAL_COEF": "OPENED_K1_SEARCH_OBJECTS_ONLY",
        "SEALED_FINAL_RESPONSE": "SEALED_COMMITTED_UNOPENED",
        "response_archive_access": False,
        "same_AST_theta_gauge_zero_refit": True,
        "candidate_refit": False,
        "amplitude_compensation": False,
        "branch_reselection": False,
        "top_k_or_proxy_filter": False,
        "development_ranking_priority": False,
        "diagnostic_promotion_or_rescue": False,
        "new_threshold": False,
        "tau_num_widened": False,
    }
    _write_json(k1 / "K1_DATA_BOUNDARY_GUARD.json", guard)
    handoff = {
        "status": "PASS",
        "formal_cohort_count": 1955,
        "formal_cohort_membership_authority": True,
        "diagnostic_strata_counts": {"DEV_FAIL": 348, "DEV_UNRESOLVED": 4},
        "diagnostic_membership_or_promotion_authority": False,
        "K1_assigns_final_III_D_operator_decision": False,
        "K2_must_adjudicate_all_1955_formal_branches": True,
        "K2_must_keep_348_and_4_separate": True,
        "every_clear_formal_PASS_enters_later_response_eligibility": True,
        "top_k_or_representative_narrowing": False,
        "candidate_specific_rescue": False,
        "if_fidelity_insufficient": "STOP_FOR_EXPLICIT_COHORT_WIDE_REPAIR_DO_NOT_WIDEN_TAU",
        "SEALED_FINAL_RESPONSE_opening_authorized": False,
    }
    _write_json(k1 / "K1_ADJUDICATION_HANDOFF_LOCK.json", handoff)

    patch_paths = [
        cfg_path,
        Path(__file__).resolve(),
        home / "docs/P13_S3_K1_SEALED_COEFFICIENT_OPERATOR_TRANSFER.md",
        home / "scripts/run_p13_s3_k1.sh",
        home / "scripts/verify_p13_s3_k1.sh",
        home / "scripts/package_p13_s3_k1_audit.sh",
        home / "tests/test_p13_s3_k1.py",
        home / "patch_manifests/P13_S3_K1_SEALED_COEFFICIENT_OPERATOR_TRANSFER_PATCH_20260901.json",
    ]
    inherited_paths = [root / row["path"] for row in cfg["inherited_live_source_locks"]]
    parent_paths = [root / row["path"] for row in cfg["parent"]["k0_files"]]
    parent_paths.extend([
        root / cfg["parent"]["s1_clear_locator"]["path"],
        root / cfg["parent"]["s2_formal_membership"]["path"],
        root / cfg["parent"]["s1_k3_active_input_manifest"]["path"],
        root / cfg["parent"]["portable_freeze_filename"],
    ])
    sources = _source_manifest(root, patch_paths + inherited_paths + parent_paths)
    _write_json(k1 / "K1_SOURCE_AND_PARENT_MANIFEST.json", sources)
    runtime = {
        "python": sys.version,
        "numpy": np.__version__,
        "platform": platform.platform(),
        "workers": workers,
        "coordinator_cpus": 1,
        "OMP_NUM_THREADS": os.environ.get("OMP_NUM_THREADS"),
        "MKL_NUM_THREADS": os.environ.get("MKL_NUM_THREADS"),
        "OPENBLAS_NUM_THREADS": os.environ.get("OPENBLAS_NUM_THREADS"),
        "NUMEXPR_NUM_THREADS": os.environ.get("NUMEXPR_NUM_THREADS"),
        "restart_resume": True,
    }
    _write_json(k1 / "K1_RUNTIME_ENVIRONMENT.json", runtime)

    semantic_basis = {
        "stage": "P13-S3-K1",
        "K0_semantic_output_digest": cfg["parent"]["k0_semantic_output_digest"],
        "formal_S2_membership_sha256": cfg["parent"]["s2_formal_membership"]["sha256"],
        "opened_coefficient_manifest_sha256": _sha(k1 / "K1_SEALED_FINAL_COEF_INPUT_MANIFEST.json"),
        "identity": identity_record,
        "ledger_manifest": ledger_manifest,
        "aggregate": aggregate,
        "numerical_fidelity": fidelity,
        "data_boundary": guard,
        "adjudication_handoff": handoff,
        "source_manifest_sha256": _sha(k1 / "K1_SOURCE_AND_PARENT_MANIFEST.json"),
    }
    semantic = sha256_bytes(canonical_json_bytes(semantic_basis))
    _write_json(k1 / "K1_SEMANTIC_OUTPUT_DIGEST.json", {
        "semantic_output_digest": semantic,
        "basis": semantic_basis,
    })

    fatal_count = sum(value["fatal_implementation_exceptions"] for value in aggregate["strata"].values())
    overall = "PASS" if fatal_count == 0 else "FAIL"
    next_action = cfg["next_on_pass"] if overall == "PASS" else cfg["next_on_failure"]
    summary = {
        "OVERALL_STATUS": overall,
        "NEXT_ACTION": next_action,
        "authorization": cfg["authorization"],
        "authoritative_S3_run": k0run.relative_to(root).as_posix(),
        "K0_semantic_output_digest": cfg["parent"]["k0_semantic_output_digest"],
        "semantic_output_digest": semantic,
        "SEALED_FINAL_COEF_opened": True,
        "SEALED_FINAL_RESPONSE_opened": False,
        "formal_cohort_count": 1955,
        "diagnostic_DEV_FAIL_count": 348,
        "diagnostic_DEV_UNRESOLVED_count": 4,
        "complete_execution_count": 2307,
        "same_AST_theta_gauge_zero_refit": True,
        "formal_membership_changed": False,
        "diagnostic_promotion_or_rescue": False,
        "top_k_or_proxy_filter": False,
        "K1_final_III_D_operator_decision_frozen": False,
        "K2_required": True,
        "ledger_manifest": ledger_manifest,
        "aggregate": aggregate,
        "numerical_fidelity": fidelity,
    }
    _write_json(k1 / "K1_SCIENTIFIC_SUMMARY.json", summary)
    _write_text(k0run / "K1_OVERALL_STATUS.txt", overall + "\n")
    _write_text(k0run / "K1_NEXT_ACTION.txt", next_action + "\n")
    _write_text(home / "runs/LATEST_P13_S3_K1_RUN.txt", k0run.relative_to(root).as_posix() + "\n")
    if overall == "PASS":
        _safe_cleanup_work(k1, work)
    print(f"P13_S3_K1_FINAL={overall}", flush=True)
    print(f"P13_S3_K1_SEMANTIC_DIGEST={semantic}", flush=True)
    print(f"P13_S3_K1_NEXT_ACTION={next_action}", flush=True)
    return 0 if overall == "PASS" else 2


def verify(root: Path) -> dict[str, Any]:
    root = root.resolve()
    home = root / "phases/p13/coefficient_law_raw_xt"
    cfg = _json(home / "configs/p13_s3_k1_protocol.json")
    checks: dict[str, Any] = {}
    run = _resolve_marker(root, "phases/p13/coefficient_law_raw_xt/runs/LATEST_P13_S3_K1_RUN.txt")
    checks["run_target"] = run == (root / cfg["parent"]["expected_k0_run"]).resolve()
    k1 = run / cfg["outputs"]["run_subdir"]
    summary = _json(k1 / "K1_SCIENTIFIC_SUMMARY.json")
    entry = _json(k1 / "K1_ENTRY_AND_AUTHORIZATION_AUDIT.json")
    opened_review = _json(k1 / "K1_OPENED_COEFFICIENT_REPRODUCIBILITY_AUDIT.json")
    checks["entry_and_opening_audits"] = entry.get("status") == "PASS" and opened_review.get("status") == "PASS"
    checks["status"] = summary.get("OVERALL_STATUS") == "PASS" and (run / "K1_OVERALL_STATUS.txt").read_text().strip() == "PASS"
    checks["next_action"] = summary.get("NEXT_ACTION") == cfg["next_on_pass"] and (run / "K1_NEXT_ACTION.txt").read_text().strip() == cfg["next_on_pass"]
    checks["sealed_boundary"] = summary.get("SEALED_FINAL_COEF_opened") is True and summary.get("SEALED_FINAL_RESPONSE_opened") is False
    checks["governance"] = (
        summary.get("same_AST_theta_gauge_zero_refit") is True
        and summary.get("formal_membership_changed") is False
        and summary.get("diagnostic_promotion_or_rescue") is False
        and summary.get("top_k_or_proxy_filter") is False
        and summary.get("K1_final_III_D_operator_decision_frozen") is False
    )

    rows_by_stratum: dict[str, list[dict[str, Any]]] = {}
    for name, rec in summary["ledger_manifest"].items():
        path = root / rec["path"]
        checks[f"{name}_ledger_SHA_count"] = (
            path.is_file() and _sha(path) == rec["sha256"] and _jsonl_count(path) == int(rec["count"])
        )
        rows_by_stratum[name] = _read_jsonl(path)
    partition = _validate_partition(rows_by_stratum, {
        "FORMAL_1955": 1955,
        "DIAGNOSTIC_DEV_FAIL_348": 348,
        "DIAGNOSTIC_DEV_UNRESOLVED_4": 4,
    })
    checks["complete_disjoint_partition"] = partition["status"] == "PASS"
    all_rows = [row for rows in rows_by_stratum.values() for row in rows]
    checks["original_locator_order_census"] = (
        len(all_rows) == 2307
        and {int(row.get("source_order", -1)) for row in all_rows} == set(range(2307))
    )
    checks["membership_authority"] = (
        all(row.get("formal_membership_authority") is True for row in rows_by_stratum["FORMAL_1955"])
        and all(row.get("formal_membership_authority") is False for row in rows_by_stratum["DIAGNOSTIC_DEV_FAIL_348"])
        and all(row.get("formal_membership_authority") is False for row in rows_by_stratum["DIAGNOSTIC_DEV_UNRESOLVED_4"])
    )
    checks["zero_refit_rows"] = all(
        row.get("same_AST_theta_gauge_zero_refit") is True and row.get("K1_final_decision_authority") is False
        for rows in rows_by_stratum.values() for row in rows
    )

    opened_manifest = _json(k1 / "K1_SEALED_FINAL_COEF_INPUT_MANIFEST.json")
    checks["opened_objects"] = _verify_opened_manifest(root, k1, opened_manifest, cfg)["status"] == "PASS"
    guard = _json(k1 / "K1_DATA_BOUNDARY_GUARD.json")
    checks["response_unopened"] = (
        guard.get("SEALED_FINAL_RESPONSE") == "SEALED_COMMITTED_UNOPENED"
        and guard.get("response_archive_access") is False
        and guard.get("DEVELOPMENT_RESPONSE_read_by_K1") is False
    )
    checks["no_shortcut"] = (
        guard.get("candidate_refit") is False
        and guard.get("amplitude_compensation") is False
        and guard.get("branch_reselection") is False
        and guard.get("top_k_or_proxy_filter") is False
        and guard.get("development_ranking_priority") is False
        and guard.get("diagnostic_promotion_or_rescue") is False
        and guard.get("new_threshold") is False
        and guard.get("tau_num_widened") is False
    )
    source_manifest = _json(k1 / "K1_SOURCE_AND_PARENT_MANIFEST.json")
    source_ok = True
    for record in source_manifest.get("files", []):
        path = root / record["path"]
        if (
            not path.is_file()
            or path.stat().st_size != int(record["bytes"])
            or _sha(path) != record["sha256"]
        ):
            source_ok = False
            break
    checks["source_and_parent_manifest"] = source_ok and bool(source_manifest.get("files"))
    semantic_record = _json(k1 / "K1_SEMANTIC_OUTPUT_DIGEST.json")
    calculated = sha256_bytes(canonical_json_bytes(semantic_record["basis"]))
    checks["semantic_digest"] = (
        calculated == semantic_record.get("semantic_output_digest") == summary.get("semantic_output_digest")
    )
    return {"status": "PASS" if all(bool(value) for value in checks.values()) else "FAIL", "checks": checks}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--workers", type=int, default=int(os.environ.get("P13_WORKERS", "16")))
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args(argv)
    if args.workers < 1:
        raise SystemExit("workers must be >= 1")
    if args.verify_only:
        try:
            review = verify(args.project_root)
        except Exception as exc:
            print(f"P13_S3_K1_VERIFY=FAIL type={type(exc).__name__} message={exc}", flush=True)
            return 2
        print(f"P13_S3_K1_VERIFY={review['status']}", flush=True)
        return 0 if review["status"] == "PASS" else 2
    try:
        return run(args.project_root, args.workers)
    except Exception as exc:
        root = args.project_root.resolve()
        home = root / "phases/p13/coefficient_law_raw_xt"
        try:
            cfg = _json(home / "configs/p13_s3_k1_protocol.json")
            run_dir = root / cfg["parent"]["expected_k0_run"]
            k1 = run_dir / cfg["outputs"]["run_subdir"]
            k1.mkdir(parents=True, exist_ok=True)
            _write_json(k1 / "K1_FATAL_ERROR.json", {
                "status": "FAIL",
                "exception_type": type(exc).__name__,
                "exception": str(exc),
                "SEALED_FINAL_RESPONSE_opened": False,
                "response_archive_access": False,
            })
            _write_text(run_dir / "K1_OVERALL_STATUS.txt", "FAIL\n")
            _write_text(run_dir / "K1_NEXT_ACTION.txt", cfg["next_on_failure"] + "\n")
        except Exception:
            pass
        print(f"P13_S3_K1_FINAL=FAIL type={type(exc).__name__} message={exc}", flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
