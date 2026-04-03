from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path


def build_file_manifest(files: list[Path]) -> dict[str, object]:
    entries: dict[str, dict[str, object]] = {}
    digest = hashlib.sha256()
    for path in sorted(files):
        rel = str(path)
        try:
            stat = path.stat()
        except OSError:
            continue
        entries[rel] = {"mtime": stat.st_mtime, "size": stat.st_size}
        digest.update(f"{rel}|{stat.st_mtime}|{stat.st_size}".encode("utf-8", errors="ignore"))
    return {"files": entries, "fingerprint": digest.hexdigest()}


def read_manifest(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logging.warning(f"Failed to read semantic manifest '{path}': {e}")
        return {}


def write_manifest(path: Path, manifest: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def manifest_needs_reingest(*, files: list[Path], manifest_path: Path) -> bool:
    current = build_file_manifest(files)
    existing = read_manifest(manifest_path)
    return not existing or existing.get("fingerprint") != current.get("fingerprint")


def redact_sensitive_text(text: str, patterns: list) -> str:
    redacted = text
    for pattern in patterns:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted
