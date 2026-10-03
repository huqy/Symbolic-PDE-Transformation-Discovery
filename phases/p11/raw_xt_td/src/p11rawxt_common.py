#!/usr/bin/env python3
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import json
import os
import re
import shutil
import tarfile
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

REPRODUCIBLE_TAR_MTIME = 946684800  # 2000-01-01T00:00:00Z


def utc_stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_path(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_json_bytes(obj: Any) -> bytes:
    return (json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8"))


def write_tsv(path: Path, rows: Sequence[Mapping[str, Any]], fields: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fields})


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), sort_keys=True, ensure_ascii=False) + "\n")


def parse_sha256sums(text: str) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        match = re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?(.+)", line)
        if not match:
            raise ValueError(f"Invalid SHA256SUMS line: {line!r}")
        digest, rel = match.groups()
        rel = rel.strip()
        if rel.startswith("./"):
            rel = rel[2:]
        if rel.startswith("/") or ".." in Path(rel).parts:
            raise ValueError(f"Unsafe checksum path: {rel}")
        rows.append((digest.lower(), rel))
    return rows


def _member_suffix_index(tf: tarfile.TarFile) -> dict[str, list[tarfile.TarInfo]]:
    index: dict[str, list[tarfile.TarInfo]] = {}
    for member in tf.getmembers():
        if not member.isfile():
            continue
        parts = member.name.split("/", 1)
        rel = parts[1] if len(parts) == 2 else parts[0]
        index.setdefault(rel, []).append(member)
    return index


def verify_tar_internal_checksums(tar_path: Path) -> dict[str, Any]:
    with tarfile.open(tar_path, "r:xz") as tf:
        checksum_members = [m for m in tf.getmembers() if m.isfile() and m.name.endswith("/SHA256SUMS.txt")]
        if not checksum_members:
            checksum_members = [m for m in tf.getmembers() if m.isfile() and m.name == "SHA256SUMS.txt"]
        if len(checksum_members) != 1:
            return {"status": "FAIL", "errors": [f"Expected one SHA256SUMS.txt, found {len(checksum_members)}"], "checked_file_count": 0}
        raw = tf.extractfile(checksum_members[0])
        if raw is None:
            return {"status": "FAIL", "errors": ["Could not read SHA256SUMS.txt"], "checked_file_count": 0}
        rows = parse_sha256sums(raw.read().decode("utf-8"))
        index = _member_suffix_index(tf)
        errors: list[str] = []
        checked = 0
        for expected, rel in rows:
            hits = index.get(rel, [])
            if len(hits) != 1:
                errors.append(f"Checksum member resolution failed for {rel}: {len(hits)} matches")
                continue
            extracted = tf.extractfile(hits[0])
            if extracted is None:
                errors.append(f"Could not extract {rel}")
                continue
            actual = sha256_bytes(extracted.read())
            if actual != expected:
                errors.append(f"Checksum mismatch for {rel}: expected {expected}, got {actual}")
            checked += 1
        return {
            "status": "PASS" if not errors else "FAIL",
            "errors": errors,
            "checked_file_count": checked,
            "declared_file_count": len(rows),
            "checksum_member": checksum_members[0].name,
        }


def read_tar_member_bytes(tar_path: Path, suffix: str) -> bytes:
    suffix = suffix.lstrip("/")
    with tarfile.open(tar_path, "r:xz") as tf:
        hits = []
        for member in tf.getmembers():
            if not member.isfile():
                continue
            parts = member.name.split("/", 1)
            rel = parts[1] if len(parts) == 2 else parts[0]
            if rel == suffix:
                hits.append(member)
        if len(hits) != 1:
            raise FileNotFoundError(f"Expected one member {suffix!r} in {tar_path}, found {len(hits)}")
        handle = tf.extractfile(hits[0])
        if handle is None:
            raise OSError(f"Could not extract {hits[0].name}")
        return handle.read()


def read_tar_json(tar_path: Path, suffix: str) -> Any:
    return json.loads(read_tar_member_bytes(tar_path, suffix).decode("utf-8"))


def find_archive(
    search_root: Path,
    canonical_name: str,
    expected_sha256: str | None = None,
) -> tuple[Path | None, list[dict[str, Any]], list[str]]:
    exact = search_root / canonical_name
    if canonical_name.endswith(".tar.xz"):
        stem = canonical_name[:-7]
        patterns = [stem + "*.tar.xz", stem + "*.tar(*).xz"]
    else:
        stem = canonical_name
        patterns = [stem + "*.xz"]
    candidates = [exact] if exact.exists() else []
    for pattern in patterns:
        candidates.extend(p for p in sorted(search_root.glob(pattern)) if p not in candidates)
    records = [{"path": p.name, "sha256": sha256_path(p), "bytes": p.stat().st_size} for p in candidates]
    warnings: list[str] = []
    if not candidates:
        return None, records, warnings
    if expected_sha256 is not None:
        matching = [p for p, row in zip(candidates, records) if row["sha256"] == expected_sha256]
        if not matching:
            warnings.append(
                f"No archive matching the frozen SHA-256 was found for {canonical_name}; "
                f"observed={[r['path'] for r in records]}"
            )
            return None, records, warnings
        selected = exact if exact in matching else matching[0]
        nonmatching = [row["path"] for row in records if row["sha256"] != expected_sha256]
        if nonmatching:
            warnings.append(
                f"Ignored nonidentical stale copies for {canonical_name}: {nonmatching}; "
                f"selected {selected.name} by frozen SHA-256"
            )
        elif len(matching) > 1:
            warnings.append(f"Multiple byte-identical copies found for {canonical_name}; selected {selected.name}")
        return selected, records, warnings

    unique_hashes = {row["sha256"] for row in records}
    if len(unique_hashes) > 1:
        return None, records, [f"Multiple nonidentical archives match {canonical_name}: {[r['path'] for r in records]}"]
    selected = exact if exact.exists() else candidates[0]
    if len(candidates) > 1:
        warnings.append(f"Multiple byte-identical copies found for {canonical_name}; selected {selected.name}")
    return selected, records, warnings


def source_manifest(project_root: Path, paths: Iterable[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted({p.resolve() for p in paths if p.exists()}):
        rows.append({
            "path": path.relative_to(project_root.resolve()).as_posix(),
            "sha256": sha256_path(path),
            "bytes": path.stat().st_size,
        })
    return rows


def create_checksum_file(root: Path, relative_files: Sequence[str]) -> Path:
    checksum_path = root / "SHA256SUMS.txt"
    lines = []
    for rel in sorted(relative_files):
        path = root / rel
        lines.append(f"{sha256_path(path)}  ./{rel}\n")
    checksum_path.write_text("".join(lines), encoding="utf-8")
    return checksum_path


def create_tar_from_directory(source_dir: Path, output_path: Path, top_name: str) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output_path, "w:xz", preset=6) as tf:
        for path in sorted(source_dir.rglob("*")):
            if path.is_dir():
                continue
            arcname = f"{top_name}/{path.relative_to(source_dir).as_posix()}"
            info = tf.gettarinfo(str(path), arcname=arcname)
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mtime = REPRODUCIBLE_TAR_MTIME
            with path.open("rb") as handle:
                tf.addfile(info, handle)


def safe_extract_selected(tar_path: Path, destination: Path, allowed_relative_paths: Sequence[str]) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    allowed = set(allowed_relative_paths)
    with tarfile.open(tar_path, "r:xz") as tf:
        index = _member_suffix_index(tf)
        for rel in allowed:
            hits = index.get(rel, [])
            if len(hits) != 1:
                raise FileNotFoundError(f"Expected one active-input member {rel}, found {len(hits)}")
            handle = tf.extractfile(hits[0])
            if handle is None:
                raise OSError(f"Could not extract {rel}")
            target = destination / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(handle.read())


def latest_path(root: Path, pattern: str) -> Path | None:
    hits = sorted(root.glob(pattern))
    return hits[-1] if hits else None


def copy_files(base: Path, relative_files: Sequence[str], destination: Path) -> None:
    for rel in relative_files:
        src = base / rel
        if not src.exists():
            raise FileNotFoundError(src)
        dst = destination / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
