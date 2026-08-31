"""Digest and atomic persistence helpers for Kanban performance evidence."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

try:
    from scripts.performance.kanban_performance_validation import (
        SuiteValidationError,
        mapping,
        text,
    )
except ModuleNotFoundError:
    from kanban_performance_validation import (  # type: ignore
        SuiteValidationError,
        mapping,
        text,
    )

ROOT = Path(__file__).resolve().parents[2]


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_path(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def repo_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(ROOT))
    except ValueError:
        return str(resolved)


def load_json(path: Path) -> tuple[dict[str, Any], bytes]:
    payload = path.read_bytes()
    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise SuiteValidationError(f"json_invalid:{repo_path(path)}") from exc
    return mapping(decoded, f"json:{repo_path(path)}"), payload


def source_artifact(path: Path, payload: bytes, report: dict[str, Any]) -> dict[str, str]:
    return {
        "path": repo_path(path),
        "sha256": sha256_bytes(payload),
        "schema": text(report.get("schema"), "source_schema"),
    }


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
