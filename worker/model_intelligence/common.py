"""Shared bounded file utilities for model-intelligence analyzers."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
from typing import Mapping


class ModelAnalysisError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        relative_path: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.relative_path = relative_path


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def canonical_digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def safe_relative_path(relative_path: str) -> PurePosixPath:
    candidate = PurePosixPath(relative_path)
    if (
        not relative_path
        or candidate.is_absolute()
        or ".." in candidate.parts
        or "." in candidate.parts
    ):
        raise ModelAnalysisError(
            "analysis_path_invalid",
            "Analysis files must use safe relative paths.",
            relative_path=relative_path,
        )
    return candidate


def open_bounded_file(
    snapshot_root: str | Path,
    relative_path: str,
    *,
    max_bytes: int,
) -> tuple[int, int]:
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    root = Path(snapshot_root).resolve(strict=True)
    relative = safe_relative_path(relative_path)
    current = root
    for part in relative.parts:
        current = current / part
        try:
            info = current.lstat()
        except OSError as exc:
            raise ModelAnalysisError(
                "analysis_file_missing",
                "Analysis input file is missing.",
                relative_path=relative_path,
            ) from exc
        if stat.S_ISLNK(info.st_mode):
            raise ModelAnalysisError(
                "analysis_symlink_forbidden",
                "Analysis input must not contain symbolic links.",
                relative_path=relative_path,
            )
    resolved = current.resolve(strict=True)
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ModelAnalysisError(
            "analysis_path_invalid",
            "Analysis input is not an in-snapshot regular file.",
            relative_path=relative_path,
        )
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(resolved, flags)
    except OSError as exc:
        raise ModelAnalysisError(
            "analysis_file_unreadable",
            "Analysis input cannot be opened.",
            relative_path=relative_path,
        ) from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise ModelAnalysisError(
                "analysis_file_type_invalid",
                "Analysis input must be a regular file.",
                relative_path=relative_path,
            )
        if info.st_size > max_bytes:
            raise ModelAnalysisError(
                "analysis_file_too_large",
                "Analysis input exceeds its bounded read limit.",
                relative_path=relative_path,
            )
        return descriptor, info.st_size
    except Exception:
        os.close(descriptor)
        raise


def read_bounded_bytes(
    snapshot_root: str | Path,
    relative_path: str,
    *,
    max_bytes: int,
) -> bytes:
    descriptor, expected_size = open_bounded_file(
        snapshot_root,
        relative_path,
        max_bytes=max_bytes,
    )
    chunks: list[bytes] = []
    remaining = expected_size
    try:
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
    finally:
        os.close(descriptor)
    content = b"".join(chunks)
    if len(content) != expected_size:
        raise ModelAnalysisError(
            "analysis_file_changed",
            "Analysis input changed while it was being read.",
            relative_path=relative_path,
        )
    return content


def read_bounded_json(
    snapshot_root: str | Path,
    relative_path: str,
    *,
    max_bytes: int,
) -> Mapping[str, object]:
    content = read_bounded_bytes(
        snapshot_root,
        relative_path,
        max_bytes=max_bytes,
    )
    try:
        decoded = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ModelAnalysisError(
            "analysis_json_invalid",
            "Analysis JSON input is invalid.",
            relative_path=relative_path,
        ) from exc
    if not isinstance(decoded, Mapping):
        raise ModelAnalysisError(
            "analysis_json_type_invalid",
            "Analysis JSON input must be an object.",
            relative_path=relative_path,
        )
    return decoded
