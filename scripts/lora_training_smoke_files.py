"""Deterministic filesystem evidence helpers for the LoRA smoke gate."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_sha256(path: Path) -> str:
    if path.is_symlink():
        raise ValueError("tree hash does not admit symbolic links")
    if path.is_file():
        return file_sha256(path)
    if not path.is_dir():
        raise ValueError("tree hash requires a regular file or directory")
    entries = list(path.rglob("*"))
    if any(item.is_symlink() for item in entries):
        raise ValueError("tree hash does not admit symbolic links")
    if any(not item.is_file() and not item.is_dir() for item in entries):
        raise ValueError("tree hash does not admit special filesystem entries")
    children = sorted(item for item in entries if item.is_file())
    if not children:
        raise ValueError("tree hash requires a non-empty file tree")
    digest = hashlib.sha256()
    for child in children:
        digest.update(child.relative_to(path).as_posix().encode("utf-8"))
        digest.update(b"\x00")
        digest.update(file_sha256(child).encode("ascii"))
        digest.update(b"\x00")
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
