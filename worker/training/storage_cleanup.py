"""Worker-side execution of one Hub-delegated contained cleanup task."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
from typing import Any, Mapping

from ananta_contracts.unsloth_task import (
    build_unsloth_cleanup_result,
    normalize_unsloth_cleanup_payload,
    unsloth_payload_sha256,
)

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,191}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_KINDS = frozenset({"workspace", "checkpoint", "export"})


class WorkerStorageCleanupError(RuntimeError):
    def __init__(
        self,
        reason_code: str,
        message: str,
        *,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.code = reason_code
        self.http_status = 422
        self.retryable = retryable


class WorkerStorageCleanupExecutor:
    """Deletes only exact tenant/job/attempt paths delegated by the Hub."""

    def __init__(
        self,
        *,
        state_root: Path,
        workspace_root: Path,
    ) -> None:
        self._state_root = _root(state_root, "state_root")
        self._workspace_root = _root(workspace_root, "workspace_root")
        self._receipt_root = self._state_root / ".cleanup-receipts"
        self._receipt_root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def execute(self, envelope: Mapping[str, Any]) -> dict[str, Any]:
        try:
            payload = normalize_unsloth_cleanup_payload(envelope)
        except ValueError as exc:
            raise WorkerStorageCleanupError(
                str(exc) or "cleanup_contract_invalid",
                "Cleanup task contract is invalid.",
            ) from exc
        task_id = str(payload["task_id"])
        scope = str(payload["tenant_scope_digest"])
        plan_sha256 = str(payload["plan_sha256"])
        artifacts = payload["artifacts"]
        assert isinstance(artifacts, list)
        request_sha256 = unsloth_payload_sha256(payload)
        receipt = self._receipt_path(scope, task_id)
        if receipt.exists():
            stored = _read_receipt(receipt)
            if not hmac.compare_digest(
                str(stored.get("request_sha256") or ""),
                request_sha256,
            ):
                raise WorkerStorageCleanupError(
                    "cleanup_task_idempotency_conflict",
                    "Cleanup task ID is already bound to another payload.",
                )
            result = stored.get("result")
            if not isinstance(result, Mapping):
                raise WorkerStorageCleanupError(
                    "cleanup_receipt_invalid",
                    "Cleanup receipt is invalid.",
                    retryable=True,
                )
            return {**dict(result), "replayed": True}

        admitted = [
            self._admit_artifact(scope, item)
            for item in artifacts
        ]
        results: list[dict[str, Any]] = []
        for item, target in admitted:
            existed = target.exists()
            if existed:
                if target.is_symlink():
                    raise WorkerStorageCleanupError(
                        "cleanup_symlink_forbidden",
                        "Cleanup target is a symbolic link.",
                    )
                if target.is_dir():
                    shutil.rmtree(target)
                elif target.is_file():
                    target.unlink()
                else:
                    raise WorkerStorageCleanupError(
                        "cleanup_special_file_forbidden",
                        "Cleanup target is not a regular file or directory.",
                    )
            results.append(
                {
                    "artifact_id": item["artifact_id"],
                    "kind": item["kind"],
                    "status": "deleted" if existed else "already_absent",
                    "sha256": item["sha256"],
                }
            )
        result = build_unsloth_cleanup_result(
            task_id=task_id,
            tenant_scope_digest=scope,
            plan_sha256=plan_sha256,
            artifacts=results,
        )
        _atomic_json(
            receipt,
            {
                "request_sha256": request_sha256,
                "result": result,
            },
        )
        return result

    def _admit_artifact(
        self,
        scope: str,
        raw: Any,
    ) -> tuple[dict[str, str], Path]:
        if not isinstance(raw, Mapping):
            raise WorkerStorageCleanupError(
                "cleanup_artifact_contract_invalid",
                "Cleanup artifact must be an object.",
            )
        allowed = {
            "artifact_id",
            "kind",
            "relative_ref",
            "job_id",
            "attempt_id",
            "sha256",
            "size_bytes",
        }
        if set(raw) != allowed:
            raise WorkerStorageCleanupError(
                "cleanup_artifact_contract_invalid",
                "Cleanup artifact fields are invalid.",
            )
        artifact_id = _identifier(
            raw.get("artifact_id"),
            "cleanup_artifact_id_invalid",
        )
        job_id = _identifier(raw.get("job_id"), "cleanup_job_id_invalid")
        attempt_id = _identifier(
            raw.get("attempt_id"),
            "cleanup_attempt_id_invalid",
        )
        kind = str(raw.get("kind") or "").strip().lower()
        if kind not in _KINDS:
            raise WorkerStorageCleanupError(
                "cleanup_kind_not_supported_by_training_worker",
                "Training worker may clean only workspace, checkpoint, or export storage.",
            )
        expected_sha256 = _digest(
            raw.get("sha256"),
            "cleanup_artifact_hash_invalid",
        )
        size_bytes = raw.get("size_bytes")
        if (
            isinstance(size_bytes, bool)
            or not isinstance(size_bytes, int)
            or not 0 <= size_bytes <= 2**63 - 1
        ):
            raise WorkerStorageCleanupError(
                "cleanup_artifact_size_invalid",
                "Cleanup artifact size is invalid.",
            )
        relative = _relative(raw.get("relative_ref"))
        expected_prefix = (
            "tenants",
            scope,
            "jobs",
            job_id,
            "attempts",
            attempt_id,
        )
        suffix = relative.parts[len(expected_prefix) :]
        if (
            len(relative.parts) <= len(expected_prefix)
            or relative.parts[: len(expected_prefix)] != expected_prefix
            or (
                kind == "workspace"
                and suffix[0] != "workspace"
            )
            or (
                kind == "checkpoint"
                and suffix[0] != "checkpoints"
            )
            or (
                kind == "export"
                and suffix[0] not in {"adapter", "artifacts", "exports"}
            )
        ):
            raise WorkerStorageCleanupError(
                "cleanup_scope_binding_mismatch",
                "Cleanup path is not bound to its tenant, job, attempt, and kind.",
            )
        root = self._workspace_root if kind == "workspace" else self._state_root
        target = _resolve_contained(root, relative)
        if target.exists():
            actual_size = _path_size(target)
            if actual_size != size_bytes:
                raise WorkerStorageCleanupError(
                    "cleanup_artifact_size_mismatch",
                    "Cleanup target size differs from Hub admission.",
                )
            actual_sha256 = _path_sha256(target)
            if not hmac.compare_digest(actual_sha256, expected_sha256):
                raise WorkerStorageCleanupError(
                    "cleanup_artifact_hash_mismatch",
                    "Cleanup target hash differs from Hub admission.",
                )
        return (
            {
                "artifact_id": artifact_id,
                "kind": kind,
                "sha256": expected_sha256,
            },
            target,
        )

    def _receipt_path(self, scope: str, task_id: str) -> Path:
        path = self._receipt_root / scope / f"{task_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        return _resolve_contained(
            self._state_root,
            path.relative_to(self._state_root),
        )


def _root(value: Path, name: str) -> Path:
    path = Path(value).resolve()
    if not path.is_dir() or path.is_symlink():
        raise ValueError(f"{name} must be an existing non-symlink directory")
    return path


def _relative(value: Any) -> PurePosixPath:
    raw = str(value or "")
    path = PurePosixPath(raw)
    if (
        not raw
        or "\x00" in raw
        or "\\" in raw
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise WorkerStorageCleanupError(
            "cleanup_path_invalid",
            "Cleanup path must be a clean relative path.",
        )
    return path


def _resolve_contained(root: Path, relative: Path | PurePosixPath) -> Path:
    current = root
    for part in relative.parts:
        current = current / part
        if current.exists() and current.is_symlink():
            raise WorkerStorageCleanupError(
                "cleanup_symlink_forbidden",
                "Cleanup path contains a symbolic link.",
            )
    candidate = current.resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise WorkerStorageCleanupError(
            "cleanup_path_escape",
            "Cleanup path escapes its configured root.",
        ) from exc
    return candidate


def _identifier(value: Any, reason_code: str) -> str:
    normalized = str(value or "").strip()
    if _IDENTIFIER.fullmatch(normalized) is None:
        raise WorkerStorageCleanupError(
            reason_code,
            "Cleanup identifier is invalid.",
        )
    return normalized


def _digest(value: Any, reason_code: str) -> str:
    normalized = str(value or "").strip().lower()
    if _SHA256.fullmatch(normalized) is None:
        raise WorkerStorageCleanupError(
            reason_code,
            "Cleanup digest is invalid.",
        )
    return normalized


def _path_size(path: Path) -> int:
    if path.is_symlink():
        raise WorkerStorageCleanupError(
            "cleanup_symlink_forbidden",
            "Cleanup target is a symbolic link.",
        )
    if path.is_file():
        return path.stat().st_size
    if not path.is_dir():
        raise WorkerStorageCleanupError(
            "cleanup_special_file_forbidden",
            "Cleanup target is not a regular file or directory.",
        )
    total = 0
    for child in path.rglob("*"):
        if child.is_symlink():
            raise WorkerStorageCleanupError(
                "cleanup_symlink_forbidden",
                "Cleanup tree contains a symbolic link.",
            )
        if child.is_file():
            total += child.stat().st_size
        elif not child.is_dir():
            raise WorkerStorageCleanupError(
                "cleanup_special_file_forbidden",
                "Cleanup tree contains a special file.",
            )
    return total


def _path_sha256(path: Path) -> str:
    if path.is_file():
        return _file_sha256(path)
    digest = hashlib.sha256()
    files = sorted(child for child in path.rglob("*") if child.is_file())
    for child in files:
        digest.update(child.relative_to(path).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(_file_sha256(child).encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_receipt(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise WorkerStorageCleanupError(
            "cleanup_receipt_invalid",
            "Cleanup receipt cannot be read.",
            retryable=True,
        ) from exc
    if not isinstance(value, dict):
        raise WorkerStorageCleanupError(
            "cleanup_receipt_invalid",
            "Cleanup receipt is invalid.",
            retryable=True,
        )
    return value


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            dict(value),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ),
        encoding="utf-8",
    )
    with temporary.open("rb") as handle:
        os.fsync(handle.fileno())
    temporary.replace(path)


__all__ = ["WorkerStorageCleanupError", "WorkerStorageCleanupExecutor"]
