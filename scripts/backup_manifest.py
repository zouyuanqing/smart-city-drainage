#!/usr/bin/env python3
"""Deterministic manifest and SHA-256 helpers for drainage backup archives."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath
from typing import Any

MANIFEST_SCHEMA = "smart-city-drainage-backup-v2"
MANIFEST_NAME = "manifest.json"
MANIFEST_HASH_NAME = "manifest.sha256"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ManifestError(ValueError):
    """Raised when a backup manifest or payload is invalid."""


def canonical_json_bytes(value: Any) -> bytes:
    """Return stable UTF-8 JSON bytes for hashes and evidence."""

    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def pretty_json_bytes(value: Any) -> bytes:
    """Return deterministic, human-readable JSON with a trailing newline."""

    return (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonicalize_pg_dump(payload: bytes | str, *, sort_lines: bool = False) -> bytes:
    """Normalize volatile pg_dump tokens; optionally compare data as a line multiset."""

    if isinstance(payload, bytes):
        text = payload.decode("utf-8", errors="surrogateescape")
    else:
        text = payload
    normalized: list[str] = []
    for line in text.splitlines(keepends=True):
        if line.startswith("\\restrict "):
            normalized.append("\\restrict <normalized>\n")
        elif line.startswith("\\unrestrict "):
            normalized.append("\\unrestrict <normalized>\n")
        else:
            normalized.append(line)
    if sort_lines:
        normalized.sort()
    return "".join(normalized).encode("utf-8", errors="surrogateescape")


def validate_sha256(value: str, *, field: str = "sha256") -> str:
    if not _SHA256_RE.fullmatch(value):
        raise ManifestError(f"{field} must be 64 lowercase hexadecimal characters")
    return value


def safe_relative_path(value: str) -> PurePosixPath:
    """Validate a portable relative path used inside a backup manifest."""

    if not value or "\\" in value:
        raise ManifestError(f"unsafe manifest path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ManifestError(f"unsafe manifest path: {value!r}")
    return path


def _resolve_payload(root: Path, relative: str) -> Path:
    path = root.joinpath(*safe_relative_path(relative).parts)
    root_resolved = root.resolve()
    resolved = path.resolve(strict=True)
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise ManifestError(f"payload escapes backup root: {relative!r}") from exc
    return resolved


def file_component(relative: str, path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise ManifestError(f"component is not a regular file: {path}")
    return {
        "bytes": path.stat().st_size,
        "path": str(safe_relative_path(relative)),
        "sha256": sha256_file(path),
    }


def tree_component(relative: str, path: Path) -> dict[str, Any]:
    if not path.is_dir() or path.is_symlink():
        raise ManifestError(f"component is not a regular directory: {path}")
    files: list[dict[str, Any]] = []
    for candidate in sorted(path.rglob("*"), key=lambda item: item.as_posix()):
        if candidate.is_symlink():
            raise ManifestError(f"symlinks are not allowed in backup payloads: {candidate}")
        if candidate.is_file():
            files.append(
                {
                    "bytes": candidate.stat().st_size,
                    "path": candidate.relative_to(path).as_posix(),
                    "sha256": sha256_file(candidate),
                }
            )
    if not files:
        raise ManifestError(f"component directory is empty: {path}")
    return {
        "files": files,
        "path": str(safe_relative_path(relative)),
        "tree_sha256": sha256_bytes(canonical_json_bytes(files)),
    }


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pretty_json_bytes(value))


def write_manifest(root: Path, manifest: dict[str, Any]) -> tuple[Path, str]:
    if manifest.get("schema") != MANIFEST_SCHEMA:
        raise ManifestError(
            f"manifest schema must be {MANIFEST_SCHEMA!r}, got {manifest.get('schema')!r}"
        )
    backup_id = manifest.get("backup_id")
    if not isinstance(backup_id, str) or not backup_id:
        raise ManifestError("manifest backup_id must be a non-empty string")
    root.mkdir(parents=True, exist_ok=True)
    payload = pretty_json_bytes(manifest)
    manifest_path = root / MANIFEST_NAME
    manifest_hash_path = root / MANIFEST_HASH_NAME
    manifest_path.write_bytes(payload)
    manifest_hash = sha256_bytes(payload)
    manifest_hash_path.write_text(f"{manifest_hash}  {MANIFEST_NAME}\n", encoding="ascii")
    return manifest_path, manifest_hash


def _verify_hashed_paths(
    root: Path,
    entries: dict[str, Any] | list[Any],
    *,
    tree: bool,
) -> None:
    values = entries.values() if isinstance(entries, dict) else entries
    for entry in values:
        if not isinstance(entry, dict):
            raise ManifestError("manifest hash entries must be objects")
        relative = entry.get("path")
        if not isinstance(relative, str):
            raise ManifestError("manifest hash entry is missing path")
        payload = _resolve_payload(root, relative)
        if tree:
            actual = tree_component(relative, payload)
            expected = entry.get("tree_sha256")
            if actual["tree_sha256"] != expected:
                raise ManifestError(f"tree hash mismatch: {relative}")
            if actual["files"] != entry.get("files"):
                raise ManifestError(f"tree file manifest mismatch: {relative}")
            continue
        actual_hash = sha256_file(payload)
        if actual_hash != entry.get("sha256"):
            raise ManifestError(f"file hash mismatch: {relative}")
        if payload.stat().st_size != entry.get("bytes"):
            raise ManifestError(f"file size mismatch: {relative}")


def load_verified_manifest(root: Path) -> dict[str, Any]:
    """Load a manifest and verify itself plus every declared payload hash."""

    manifest_path = root / MANIFEST_NAME
    manifest_hash_path = root / MANIFEST_HASH_NAME
    if not manifest_path.is_file() or not manifest_hash_path.is_file():
        raise ManifestError("archive is missing manifest.json or manifest.sha256")
    payload = manifest_path.read_bytes()
    expected_manifest_hash = manifest_hash_path.read_text(encoding="ascii").split()[0]
    validate_sha256(expected_manifest_hash, field="manifest.sha256")
    actual_manifest_hash = sha256_bytes(payload)
    if actual_manifest_hash != expected_manifest_hash:
        raise ManifestError("manifest.sha256 does not match manifest.json")
    try:
        manifest = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ManifestError(f"manifest.json is not valid JSON: {exc}") from exc
    if not isinstance(manifest, dict) or manifest.get("schema") != MANIFEST_SCHEMA:
        raise ManifestError("unsupported or malformed backup manifest")

    components = manifest.get("components")
    artifacts = manifest.get("artifacts", {})
    if not isinstance(components, dict) or not isinstance(artifacts, dict):
        raise ManifestError("manifest components and artifacts must be objects")
    _verify_hashed_paths(root, artifacts, tree=False)
    for name, component in components.items():
        if not isinstance(component, dict):
            raise ManifestError(f"component {name!r} must be an object")
        tree = "tree_sha256" in component
        _verify_hashed_paths(root, {name: component}, tree=tree)
    return manifest
