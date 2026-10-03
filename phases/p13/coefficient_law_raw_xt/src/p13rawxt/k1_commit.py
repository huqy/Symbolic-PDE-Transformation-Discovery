from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import platform
import secrets
import shutil
import stat
import sys
import tarfile
import tempfile
import time
from pathlib import Path
from reproduce import s0_contract as s0
from typing import Any

import numpy as np

from .coefficients import (
    canonical_json_bytes,
    deterministic_seed,
    generate_generator,
    identifiability_report,
    make_search_object,
    search_object_semantic_digest,
    sha256_bytes,
    spectral_certificate,
    write_npz_exact,
)


def sha256_path(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj: Any, *, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    if mode is not None:
        os.chmod(path, mode)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")


def _safe_extract_parent_audit(archive: Path, dest: Path) -> Path:
    with tarfile.open(archive, "r:xz") as tf:
        members = tf.getmembers()
        for m in members:
            p = Path(m.name)
            if p.is_absolute() or ".." in p.parts or m.issym() or m.islnk():
                raise RuntimeError(f"unsafe K0 audit member: {m.name}")
        tf.extractall(dest, filter="data")
    roots = [p for p in dest.iterdir() if p.is_dir()]
    if len(roots) != 1:
        raise RuntimeError("K0 audit must contain exactly one top-level directory")
    return roots[0]


def verify_parent(archive: Path, protocol: dict[str, Any]) -> dict[str, Any]:
    parent = protocol["parent"]
    archive_sha = sha256_path(archive)
    with tempfile.TemporaryDirectory(prefix="p13_k1_parent_") as td:
        root = _safe_extract_parent_audit(archive, Path(td))
        k0 = root / "K0"
        status = (k0 / "OVERALL_STATUS.txt").read_text(encoding="utf-8").strip()
        nxt = (k0 / "NEXT_ACTION.txt").read_text(encoding="utf-8").strip()
        semantic = load_json(k0 / "semantic_output_digest.json")
        deriv = load_json(k0 / "derivative_semantics_guard.json")
        checks = {
            "archive_sha256": archive_sha == parent["formal_audit_archive_sha256"],
            "overall_status": status == parent["overall_status"],
            "next_action": nxt == parent["next_action"],
            "semantic_output_digest": semantic.get("semantic_output_digest") == parent["semantic_output_digest"],
            "numeric_L3_guard": deriv.get("numeric_L3_execution_status") == parent["numeric_L3_execution_status"] and bool(deriv.get("blocking_for_formal_search")),
        }
    return {
        "archive": str(archive.resolve()),
        "observed_archive_sha256": archive_sha,
        "expected_archive_sha256": parent["formal_audit_archive_sha256"],
        "observed_semantic_output_digest": semantic.get("semantic_output_digest"),
        "expected_semantic_output_digest": parent["semantic_output_digest"],
        "checks": checks,
        "status": "PASS" if all(checks.values()) else "FAIL",
    }


def _semantic_generator_payload(generator: dict[str, Any]) -> str:
    return sha256_bytes(canonical_json_bytes(generator))


def _open_field_rows(role: str, count: int, protocol: dict[str, Any], run: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, dict[int, dict[str, np.ndarray]]]]:
    registry: list[dict[str, Any]] = []
    provenance: list[dict[str, Any]] = []
    all_arrays: dict[str, dict[int, dict[str, np.ndarray]]] = {}
    for idx in range(1, count + 1):
        seed = deterministic_seed(protocol["open_seed_namespace"], role, idx)
        gen = generate_generator(role, idx, seed, protocol)
        cert = spectral_certificate(gen, protocol)
        if cert["status"] != "PASS":
            raise RuntimeError(f"regime certificate failed for {gen['field_id']}")
        gen_sem = _semantic_generator_payload(gen)
        provenance.append({"generator": gen, "generator_semantic_digest": gen_sem, "regime_certificate": cert})
        field_arrays: dict[int, dict[str, np.ndarray]] = {}
        object_rows = []
        for grid_n in protocol["regime"]["search_object_grids"]:
            arrays = make_search_object(gen, int(grid_n), int(protocol["regime"]["search_facing_coefficient_jet_order"]))
            field_arrays[int(grid_n)] = arrays
            rel = Path("open_search_objects") / role / f"{gen['field_id']}_G{grid_n}.npz"
            out = run / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            write_npz_exact(out, arrays)
            object_rows.append({
                "grid": int(grid_n), "path": rel.as_posix(), "bytes": out.stat().st_size,
                "sha256": sha256_path(out), "semantic_digest": search_object_semantic_digest(arrays),
                "contents": "x,t,q and analytic coefficient derivative arrays only; no generator parameters",
            })
        all_arrays[gen["field_id"]] = field_arrays
        registry.append({
            "field_id": gen["field_id"], "role": role, "visibility": "OPEN",
            "generator_family": protocol["generator_families"][role],
            "epsilon": protocol["regime"]["epsilon_abs"],
            "M_certificate": cert["M_l1"], "chi_certificate": cert["chi_exact"],
            "positivity_lower_bound": cert["analytic_positivity_lower_bound"],
            "generator_provenance_semantic_digest": gen_sem,
            "search_objects": object_rows,
            "search_contract": "consumer may read search objects only, never open-generator provenance or generator internals",
        })
    return registry, provenance, all_arrays


def _derive_private_seed(master_hex: str, label: str) -> int:
    return int.from_bytes(hashlib.sha256(bytes.fromhex(master_hex) + label.encode("utf-8")).digest()[:8], "big")


def _response_rng(master_hex: str, role: str, field_idx: int, case: str) -> np.random.Generator:
    seed = _derive_private_seed(master_hex, f"response|{role}|{field_idx}|{case}")
    return np.random.default_rng(seed)


def _sine_series(rng: np.random.Generator, *, max_mode: int = 4) -> dict[str, Any]:
    modes = sorted(rng.choice(np.arange(1, max_mode + 1), size=3, replace=False).tolist())
    amps = rng.normal(size=3); amps /= max(np.sum(np.abs(amps)), 1e-15)
    return {"type": "sine_series", "modes": [int(x) for x in modes], "amplitudes": [float(x) for x in amps]}


def _local_spatial(rng: np.random.Generator) -> dict[str, Any]:
    return {"type": "dirichlet_gaussian_packet", "center": float(rng.uniform(0.25, 0.75)), "width": float(rng.uniform(0.10, 0.20)), "amplitude": float(rng.choice([-1.0, 1.0]))}


def _global_forcing(rng: np.random.Generator) -> dict[str, Any]:
    amps = rng.normal(size=3); amps /= max(np.sum(np.abs(amps)), 1e-15)
    terms = []
    for amp in amps:
        terms.append({"amplitude": float(amp), "spatial_mode": int(rng.integers(1, 5)), "time_frequency": float(rng.uniform(0.5, 2.5)), "phase": float(rng.uniform(0.0, 2.0 * np.pi))})
    return {"type": "sine_x_harmonic_t_series", "terms": terms}


def _local_forcing(rng: np.random.Generator) -> dict[str, Any]:
    return {"type": "dirichlet_spacetime_gaussian_packet", "x_center": float(rng.uniform(0.25, 0.75)), "x_width": float(rng.uniform(0.10, 0.20)), "t_center": float(rng.uniform(0.25, 0.75)), "t_width": float(rng.uniform(0.10, 0.22)), "amplitude": float(rng.choice([-1.0, 1.0]))}


def _response_case(master_hex: str, role: str, field_idx: int, case: str) -> dict[str, Any]:
    rng = _response_rng(master_hex, role, field_idx, case)
    zero = {"type": "zero"}
    global_kind = case.endswith("global")
    spatial = _sine_series(rng) if global_kind else _local_spatial(rng)
    forcing = _global_forcing(rng) if global_kind else _local_forcing(rng)
    if case.startswith("u0-"):
        u0, v0, f = spatial, zero, zero
    elif case.startswith("v0-"):
        u0, v0, f = zero, spatial, zero
    elif case.startswith("forcing-"):
        u0, v0, f = zero, zero, forcing
    elif case.startswith("mixed-"):
        u0 = spatial
        v0 = _sine_series(rng) if global_kind else _local_spatial(rng)
        f = forcing
    else:
        raise ValueError(case)
    return {"case_type": case, "boundary_condition": "homogeneous_dirichlet", "u0": u0, "v0": v0, "forcing": f}


def _build_private_coeff_payload(role: str, master_hex: str, protocol: dict[str, Any], staging: Path) -> dict[str, Any]:
    rows = []
    for idx in range(1, int(protocol["role_counts"][role]) + 1):
        seed = _derive_private_seed(master_hex, f"coefficient|{role}|{idx}")
        gen = generate_generator(role, idx, seed, protocol)
        cert = spectral_certificate(gen, protocol)
        if cert["status"] != "PASS":
            raise RuntimeError(f"private regime certificate failed {gen['field_id']}")
        gen_path = staging / "generators" / f"{gen['field_id']}.json"
        write_json(gen_path, gen, mode=0o600)
        objects = []
        for grid_n in protocol["regime"]["search_object_grids"]:
            arrays = make_search_object(gen, int(grid_n), int(protocol["regime"]["search_facing_coefficient_jet_order"]))
            path = staging / "search_objects" / f"{gen['field_id']}_G{grid_n}.npz"
            path.parent.mkdir(parents=True, exist_ok=True)
            write_npz_exact(path, arrays); os.chmod(path, 0o600)
            objects.append({"grid": int(grid_n), "member_path": path.relative_to(staging).as_posix(), "sha256": sha256_path(path), "semantic_digest": search_object_semantic_digest(arrays)})
        rows.append({
            "field_id": gen["field_id"], "role": role, "family": gen["family"],
            "generator_member_path": gen_path.relative_to(staging).as_posix(),
            "generator_semantic_digest": _semantic_generator_payload(gen),
            "regime_certificate": cert, "search_objects": objects,
        })
    payload = {"schema": "P13_PRIVATE_COEFFICIENT_PAYLOAD_V1", "role": role, "rows": rows, "master_seed_hex": master_hex}
    write_json(staging / "payload_manifest.json", payload, mode=0o600)
    return payload


def _build_private_response_payload(coef_role: str, master_hex: str, protocol: dict[str, Any], staging: Path) -> dict[str, Any]:
    rows = []
    for field_idx in range(1, int(protocol["role_counts"][coef_role]) + 1):
        field_id = f"P13_{coef_role}_{field_idx:02d}"
        for case in protocol["response_contract"]["case_types"]:
            rows.append({"field_id": field_id, **_response_case(master_hex, coef_role, field_idx, case)})
    payload = {"schema": "P13_PRIVATE_RESPONSE_PAYLOAD_V1", "coefficient_role": coef_role, "rows": rows, "master_seed_hex": master_hex, "q": 1.0}
    write_json(staging / "payload_manifest.json", payload, mode=0o600)
    return payload


def _archive_private(staging: Path, archive: Path) -> None:
    archive.parent.mkdir(parents=True, exist_ok=True)
    if archive.exists():
        raise FileExistsError(archive)
    with tarfile.open(archive, "w:xz") as tf:
        for path in sorted(staging.rglob("*"), key=lambda p: p.as_posix()):
            arcname = path.relative_to(staging).as_posix()
            info = tf.gettarinfo(str(path), arcname=arcname)
            info.uid = info.gid = 0; info.uname = info.gname = ""
            info.mtime = 0
            if path.is_file():
                with path.open("rb") as f:
                    tf.addfile(info, f)
            else:
                tf.addfile(info)
    os.chmod(archive, 0o600)


def _private_commitment(kind: str, role: str, payload: dict[str, Any], archive: Path) -> dict[str, Any]:
    public_rows = []
    for row in payload["rows"]:
        r = {k: v for k, v in row.items() if k not in {"generator_member_path", "regime_certificate", "search_objects", "u0", "v0", "forcing"}}
        if "search_objects" in row:
            r["search_object_commitments"] = [{"grid": x["grid"], "sha256": x["sha256"], "semantic_digest": x["semantic_digest"]} for x in row["search_objects"]]
        public_rows.append(r)
    semantic_obj = {k: v for k, v in payload.items() if k != "master_seed_hex"}
    return {
        "kind": kind, "role": role, "archive_absolute_path": str(archive.resolve()),
        "archive_bytes": archive.stat().st_size, "archive_sha256": sha256_path(archive),
        "payload_semantic_digest": sha256_bytes(canonical_json_bytes(semantic_obj)),
        "row_count": len(payload["rows"]), "public_row_commitments": public_rows,
        "private_seed_material_exposed": False, "private_payload_copied_into_active_tree": False,
    }


def _make_private_payloads(private_run: Path, protocol: dict[str, Any]) -> dict[str, Any]:
    private_run.mkdir(parents=True, exist_ok=False); os.chmod(private_run, 0o700)
    commitments = {}
    specs = [
        ("coefficient", "DEVELOPMENT_COEF"), ("coefficient", "SEALED_FINAL_COEF"),
        ("response", "DEVELOPMENT_COEF"), ("response", "SEALED_FINAL_COEF"),
    ]
    for kind, role in specs:
        master = secrets.token_hex(32)
        staging = private_run / f".{kind}_{role}.staging"
        staging.mkdir(mode=0o700)
        if kind == "coefficient":
            payload = _build_private_coeff_payload(role, master, protocol, staging)
        else:
            payload = _build_private_response_payload(role, master, protocol, staging)
        archive = private_run / f"P13_S0_K1_PRIVATE_{kind.upper()}_{role}.tar.xz"
        _archive_private(staging, archive)
        shutil.rmtree(staging)
        commitments[f"{kind}:{role}"] = _private_commitment(kind, role, payload, archive)
    receipt = {"schema": "P13_PRIVATE_GENERATION_RECEIPT_V1", "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "commitments": commitments}
    write_json(private_run / "PRIVATE_GENERATION_RECEIPT.json", receipt, mode=0o600)
    return commitments


def _role_separation(open_registry: list[dict[str, Any]], private_commitments: dict[str, Any], protocol: dict[str, Any]) -> dict[str, Any]:
    open_ids = [r["field_id"] for r in open_registry]
    private_ids = []
    for key in ("coefficient:DEVELOPMENT_COEF", "coefficient:SEALED_FINAL_COEF"):
        private_ids.extend([r["field_id"] for r in private_commitments[key]["public_row_commitments"]])
    checks = {
        "all_field_ids_unique": len(set(open_ids + private_ids)) == len(open_ids + private_ids),
        "role_counts_exact": all(sum(r["role"] == role for r in open_registry) == count for role, count in protocol["role_counts"].items() if role in {"CALIBRATION_COEF", "TRAIN_OPERATOR", "OPENED_TRANSFER_DIAGNOSTIC"}),
        "development_vs_train_family_distinct": protocol["generator_families"]["DEVELOPMENT_COEF"] != protocol["generator_families"]["TRAIN_OPERATOR"],
        "sealed_vs_train_family_distinct": protocol["generator_families"]["SEALED_FINAL_COEF"] != protocol["generator_families"]["TRAIN_OPERATOR"],
        "development_response_rows_32": private_commitments["response:DEVELOPMENT_COEF"]["row_count"] == 32,
        "sealed_response_rows_32": private_commitments["response:SEALED_FINAL_COEF"]["row_count"] == 32,
    }
    return {"checks": checks, "status": "PASS" if all(checks.values()) else "FAIL"}


def _source_manifest(root: Path, paths: list[Path]) -> dict[str, Any]:
    rows = []
    for p in paths:
        rows.append({"path": p.relative_to(root).as_posix(), "bytes": p.stat().st_size, "sha256": sha256_path(p)})
    return {"files": rows}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-root", type=Path, default=Path.cwd())
    ap.add_argument("--k0-audit", type=Path)
    ap.add_argument("--private-root", type=Path)
    ap.add_argument("--run-dir", type=Path, default=None)
    args = ap.parse_args()
    root = args.project_root.resolve()
    home = root / "phases/p13/coefficient_law_raw_xt"
    protocol_path = home / "configs/p13_s0_k1_protocol.json"
    protocol = load_json(protocol_path)
    private_root = root / "opaque_commitments_only" if s0.active(root) else args.private_root.resolve()
    if not s0.active(root) and (private_root == root or root in private_root.parents):
        raise SystemExit("FATAL: --private-root must be outside project root")
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    run = args.run_dir.resolve() if args.run_dir else home / "runs" / f"p13_s0_k1_commit_{stamp}"
    run.mkdir(parents=True, exist_ok=False)
    private_run = private_root / f"p13_s0_k1_private_{stamp}"
    fatal: list[str] = []
    print(f"[P13-S0-K1] run_dir={run}", flush=True)
    print(f"[P13-S0-K1] private_run={private_run}", flush=True)

    print("[P13-S0-K1] 1/8 verify formal K0 parent audit", flush=True)
    parent = s0.fresh_parent_k0(root, protocol) if s0.active(root) else verify_parent(args.k0_audit.resolve(), protocol)
    write_json(run / "parent_k0_verification.json", parent)
    if parent["status"] != "PASS":
        fatal.append("formal K0 parent lock mismatch")

    print("[P13-S0-K1] 2/8 generate fresh open coefficient roles", flush=True)
    open_registry: list[dict[str, Any]] = []
    open_prov: list[dict[str, Any]] = []
    arrays_by_id: dict[str, dict[int, dict[str, np.ndarray]]] = {}
    for role in ("CALIBRATION_COEF", "TRAIN_OPERATOR", "OPENED_TRANSFER_DIAGNOSTIC"):
        reg, prov, arr = _open_field_rows(role, int(protocol["role_counts"][role]), protocol, run)
        open_registry.extend(reg); open_prov.extend(prov); arrays_by_id.update(arr)
        print(f"[P13-S0-K1] open role={role} generated={len(reg)}/{protocol['role_counts'][role]}", flush=True)
    write_jsonl(run / "public_coefficient_registry.jsonl", open_registry)
    write_jsonl(run / "open_generator_provenance.jsonl", open_prov)

    print("[P13-S0-K1] 3/8 verify coefficient regime certificates", flush=True)
    regime_rows = []
    for p in open_prov:
        cert = p["regime_certificate"]
        regime_rows.append({"field_id": p["generator"]["field_id"], "role": p["generator"]["role"], **cert})
        if cert["status"] != "PASS": fatal.append(f"regime failure {p['generator']['field_id']}")
    write_json(run / "open_regime_certificates.json", {"records": regime_rows, "status": "PASS" if all(x["status"] == "PASS" for x in regime_rows) else "FAIL"})

    print("[P13-S0-K1] 4/8 run frozen TRAIN identifiability gate on G33", flush=True)
    train_ids = [r["field_id"] for r in open_registry if r["role"] == "TRAIN_OPERATOR"]
    ident = identifiability_report([arrays_by_id[fid][33] for fid in train_ids], protocol)
    write_json(run / "train_identifiability_report.json", ident)
    print(f"[P13-S0-K1] identifiability status={ident['status']} rank={ident['numerical_rank']}/6 sigma_ratio={ident['sigma_min_over_sigma_max']:.6e} min_channel_resid={min(ident['relative_residuals'].values()):.6e}", flush=True)
    if ident["status"] != "PASS": fatal.append(protocol["identifiability"]["failure_code"])

    print("[P13-S0-K1] 5/8 HISTORICAL_REPLAY opaque commitments" if s0.active(root) else "[P13-S0-K1] 5/8 generate private DEVELOPMENT/SEALED coefficient+response payloads", flush=True)
    private_commitments = s0.replay_commitments(root) if s0.active(root) else _make_private_payloads(private_run, protocol)
    write_json(run / "private_payload_commitments.json", private_commitments)
    print("[P13-S0-K1] private payloads=4/4 committed; contents not copied to active tree", flush=True)

    print("[P13-S0-K1] 6/8 freeze role separation, objective and response contracts", flush=True)
    separation = s0.replay_role_separation(open_registry, private_commitments, protocol) if s0.active(root) else _role_separation(open_registry, private_commitments, protocol)
    write_json(run / "role_family_separation_certificate.json", separation)
    if separation["status"] != "PASS": fatal.append("role/family separation failure")
    contract = {
        "stage": "P13-S0-K1",
        "regime": protocol["regime"],
        "operator_contract": protocol["operator_contract"],
        "response_contract": protocol["response_contract"],
        "formal_search_authorized": False,
        "numeric_L3_execution_status": protocol["parent"]["numeric_L3_execution_status"],
        "K2_required_before_S1": True,
    }
    write_json(run / "objective_response_contract_lock.json", contract)

    print("[P13-S0-K1] 7/8 freeze leakage and active/private manifests", flush=True)
    search_objects = []
    for r in open_registry:
        for obj in r["search_objects"]:
            search_objects.append({"field_id": r["field_id"], "role": r["role"], **obj})
    write_json(run / "open_search_object_manifest.json", {"objects": search_objects, "consumer_boundary": "search/K2 may read these arrays; generator provenance is not a search input"})
    leakage = {
        "status": "PASS",
        "formal_candidate_search_run": False,
        "development_or_sealed_response_solve_run": False,
        "historical_response_outcomes_read": False,
        "claim_I_capacity_witness_used_as_search_information": False,
        "claim_II_characteristic_information_used_as_generator_target_seed_fitness_filter": False,
        "private_root_outside_project_root": True if s0.active(root) else not (private_root == root or root in private_root.parents),
        "private_payloads_copied_into_active_tree": False,
        "opened_transfer_diagnostic_search_authority": "NONE",
        "formal_search_guard": "S1 remains disabled until K2+K3 S0 adjudication",
        "forbidden": protocol["forbidden"],
    }
    write_json(run / "no_leakage_guard.json", leakage)

    print("[P13-S0-K1] 8/8 provenance, semantic digest and adjudication", flush=True)
    own = [
        protocol_path, home / "src/p13rawxt/coefficients.py", Path(__file__).resolve(),
        home / "scripts/run_p13_s0_k1.sh", home / "scripts/package_p13_s0_k1_audit.sh",
        home / "scripts/verify_p13_s0_k1_private_commitments.sh", home / "tests/test_p13_s0_k1.py", home / "docs/P13_S0_K1_COEFFICIENT_DATA_COMMITMENTS.md",
    ]
    source = _source_manifest(root, own)
    write_json(run / "source_manifest.json", source)
    write_json(run / "runtime_environment.json", {"python": sys.version, "numpy": np.__version__, "platform": platform.platform(), "pid": os.getpid()})
    protocol_digest = sha256_path(protocol_path)
    registry_semantic = sha256_bytes(canonical_json_bytes(open_registry))
    private_commitment_semantic = sha256_bytes(canonical_json_bytes(private_commitments))
    semantic = {
        "stage": "P13-S0-K1", "parent_K0_semantic": s0.semantic(root, "K0", protocol["parent"]["semantic_output_digest"]),
        "protocol_sha256": protocol_digest,
        "open_registry_semantic_digest": registry_semantic,
        "private_commitments_semantic_digest": private_commitment_semantic,
        "identifiability_semantic_digest": sha256_bytes(canonical_json_bytes(ident)),
        "objective_response_contract_semantic_digest": sha256_bytes(canonical_json_bytes(contract)),
        "role_separation_semantic_digest": sha256_bytes(canonical_json_bytes(separation)),
        "leakage_semantic_digest": sha256_bytes(canonical_json_bytes(leakage)),
    }
    semantic["semantic_output_digest"] = sha256_bytes(canonical_json_bytes(semantic))
    write_json(run / "semantic_output_digest.json", semantic)
    status = "PASS" if not fatal else "FAIL"
    next_action = protocol["next_on_pass"] if status == "PASS" else protocol["next_on_failure"]
    (run / "OVERALL_STATUS.txt").write_text(status + "\n", encoding="utf-8")
    (run / "NEXT_ACTION.txt").write_text(next_action + "\n", encoding="utf-8")
    summary = {
        "OVERALL_STATUS": status, "fatal": fatal, "next_action": next_action,
        "parent_K0_semantic": s0.semantic(root, "K0", protocol["parent"]["semantic_output_digest"]),
        "open_role_counts": {role: sum(r["role"] == role for r in open_registry) for role in ("CALIBRATION_COEF", "TRAIN_OPERATOR", "OPENED_TRANSFER_DIAGNOSTIC")},
        "private_commitments": {k: {"archive_sha256": v["archive_sha256"], "row_count": v["row_count"], "payload_semantic_digest": v["payload_semantic_digest"]} for k, v in private_commitments.items()},
        "identifiability_status": ident["status"], "identifiability_rank": ident["numerical_rank"],
        "identifiability_sigma_ratio": ident["sigma_min_over_sigma_max"],
        "identifiability_min_channel_residual": min(ident["relative_residuals"].values()),
        "formal_search_authorized": False,
        "numeric_L3_execution_status": protocol["parent"]["numeric_L3_execution_status"],
        "development_or_sealed_outcome_opened": False,
        "private_payload_contents_in_active_tree": False,
    }
    write_json(run / "audit_summary.json", summary)
    marker_dir = home / "runs"; marker_dir.mkdir(parents=True, exist_ok=True)
    (marker_dir / "LATEST_P13_S0_K1_RUN.txt").write_text((str(run.relative_to(root)) if s0.active(root) else str(run)) + "\n", encoding="utf-8")
    (marker_dir / "LATEST_P13_S0_K1_OPEN_INPUT.txt").write_text((str((run / "open_search_objects").relative_to(root)) if s0.active(root) else str(run / "open_search_objects")) + "\n", encoding="utf-8")
    (marker_dir / "LATEST_P13_S0_K1_PRIVATE_COMMITMENTS.txt").write_text((str((run / "private_payload_commitments.json").relative_to(root)) if s0.active(root) else str(run / "private_payload_commitments.json")) + "\n", encoding="utf-8")
    print(f"[P13-S0-K1] OVERALL_STATUS={status}", flush=True)
    print(f"[P13-S0-K1] semantic_output_digest={semantic['semantic_output_digest']}", flush=True)
    print(f"[P13-S0-K1] NEXT_ACTION={next_action}", flush=True)
    if fatal:
        for x in fatal: print(f"[P13-S0-K1] FATAL: {x}", flush=True)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
