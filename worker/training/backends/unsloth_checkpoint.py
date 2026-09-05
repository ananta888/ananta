"""Atomic, provenance-bound checkpoint lifecycle shared by Unsloth backends."""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from worker.training.backends.base import (
    TrainingBackendError,
    TrainingContext,
    TrainingOutcome,
)
from worker.training.process_control import CancellationToken

CHECKPOINT_MANIFEST_NAME = "ananta-checkpoint-manifest.json"
CHECKPOINT_MANIFEST_SCHEMA = "ananta.unsloth-checkpoint-manifest.v1"
_MAX_MANIFEST_BYTES = 64 * 1024
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MANIFEST_FIELDS = frozenset(
    {
        "schema",
        "tenant_scope_digest",
        "job_id",
        "attempt_id",
        "fencing_token",
        "backend",
        "dataset_hash",
        "base_model_hash",
        "configuration_hash",
        "checkpoint_name",
        "checkpoint_payload_sha256",
    }
)


class UnslothCheckpointLifecycle:
    """Bind one backend run to a fresh checkpoint manager."""

    def __init__(self, *, backend_name: str) -> None:
        self._backend_name = str(backend_name)

    def bind(self, context: TrainingContext) -> "_CheckpointSession":
        if context.request.backend != self._backend_name:
            raise TrainingBackendError(
                "checkpoint_binding_mismatch",
                "checkpoint backend does not match the admitted job backend",
            )
        manager = UnslothCheckpointManager(
            context=context,
            backend_name=self._backend_name,
        )
        manager.admit_resume()
        original_emit = context.emit

        def emit(event_type: str, payload: Mapping[str, Any]) -> None:
            context.cancel.raise_if_cancelled()
            if event_type == "checkpoint":
                manager.seal_named(str(payload.get("name") or ""))
            original_emit(event_type, payload)

        return _CheckpointSession(
            context=replace(context, emit=emit),
            manager=manager,
        )

    def recover_latest(
        self,
        *,
        request: Any,
        state_root: Path,
        checkpoint_root: Path,
    ) -> dict[str, Any] | None:
        """Recover the newest valid checkpoint sealed before a hard crash."""

        if request.backend != self._backend_name:
            return None
        state = _absolute(state_root)
        root = _absolute(checkpoint_root)
        _assert_contained_symlink_free(state, root, require_directory=True)
        cancel = CancellationToken()
        recovered: list[tuple[int, Path, str]] = []
        for checkpoint in root.iterdir():
            match = re.fullmatch(r"checkpoint-([1-9][0-9]*)", checkpoint.name)
            if match is None:
                continue
            try:
                _assert_contained_symlink_free(root, checkpoint, require_directory=True)
                _validate_checkpoint_tree(checkpoint, cancel)
                manifest = _load_manifest(checkpoint)
                expected = {
                    "schema": CHECKPOINT_MANIFEST_SCHEMA,
                    "tenant_scope_digest": request.tenant_scope_digest,
                    "job_id": request.job_id,
                    "attempt_id": request.attempt_id,
                    "backend": self._backend_name,
                    "dataset_hash": request.dataset.identity_hash,
                    "base_model_hash": request.base_model.snapshot_hash,
                    "configuration_hash": request.configuration.identity_hash,
                    "checkpoint_name": checkpoint.name,
                    "fencing_token": request.fencing_token,
                }
                _assert_manifest_binding(manifest, expected)
                payload_digest = _checkpoint_tree_sha256(
                    checkpoint,
                    cancel=cancel,
                    include_manifest=False,
                )
                if manifest["checkpoint_payload_sha256"] != payload_digest:
                    continue
                complete_digest = _checkpoint_tree_sha256(
                    checkpoint,
                    cancel=cancel,
                    include_manifest=True,
                )
            except (OSError, TrainingBackendError):
                continue
            recovered.append((int(match.group(1)), checkpoint, complete_digest))
        if not recovered:
            return None
        _step, checkpoint, complete_digest = max(recovered, key=lambda item: item[0])
        return {
            "relative_path": checkpoint.relative_to(state).as_posix(),
            "binding": {
                "job_id": request.job_id,
                "source_attempt_id": request.attempt_id,
                "base_model_hash": request.base_model.snapshot_hash,
                "dataset_hash": request.dataset.identity_hash,
                "configuration_hash": request.configuration.identity_hash,
                "checkpoint_sha256": complete_digest,
            },
        }


@dataclass(frozen=True)
class _CheckpointSession:
    context: TrainingContext
    manager: "UnslothCheckpointManager"

    def finalize(self, outcome: TrainingOutcome) -> TrainingOutcome:
        self.context.cancel.raise_if_cancelled()
        if outcome.best_checkpoint is not None:
            self.manager.seal(outcome.best_checkpoint)
        return outcome


class UnslothCheckpointManager:
    def __init__(self, *, context: TrainingContext, backend_name: str) -> None:
        self._context = context
        self._backend_name = str(backend_name)
        self._state_root = _absolute(context.checkpoint_state_root or context.checkpoint_root.parent)
        self._checkpoint_root = _absolute(context.checkpoint_root)
        _assert_contained_symlink_free(
            self._state_root,
            self._checkpoint_root,
            require_directory=True,
        )
        if context.dataset.dataset_hash != context.request.dataset.identity_hash:
            raise TrainingBackendError(
                "checkpoint_binding_mismatch",
                "verified dataset hash does not match the admitted request",
            )

    def admit_resume(self) -> None:
        self._context.cancel.raise_if_cancelled()
        resume_path = self._context.resume_path
        resume = self._context.request.resume_checkpoint
        if resume_path is None and resume is None:
            return
        if resume_path is None or resume is None:
            raise TrainingBackendError(
                "checkpoint_binding_mismatch",
                "resume path and checkpoint binding must be supplied together",
            )
        path = _absolute(resume_path)
        declared = _absolute(self._state_root / resume.relative_path)
        if path != declared:
            raise TrainingBackendError(
                "checkpoint_binding_mismatch",
                "resume path does not match its admitted relative path",
            )
        _assert_contained_symlink_free(
            self._state_root,
            path,
            require_directory=True,
        )
        _validate_checkpoint_tree(path, self._context.cancel)
        manifest = _load_manifest(path)
        binding = resume.binding
        expected = self._expected_manifest(
            job_id=binding.job_id,
            attempt_id=binding.source_attempt_id,
            checkpoint_name=path.name,
        )
        _assert_manifest_binding(manifest, expected)
        if not isinstance(manifest.get("fencing_token"), int) or int(manifest["fencing_token"]) < 1:
            raise TrainingBackendError(
                "checkpoint_manifest_invalid",
                "checkpoint manifest has no valid source fencing token",
            )
        payload_digest = _checkpoint_tree_sha256(
            path,
            cancel=self._context.cancel,
            include_manifest=False,
        )
        if manifest["checkpoint_payload_sha256"] != payload_digest:
            raise TrainingBackendError(
                "checkpoint_hash_mismatch",
                "checkpoint payload does not match its atomic manifest",
            )
        complete_digest = _checkpoint_tree_sha256(
            path,
            cancel=self._context.cancel,
            include_manifest=True,
        )
        if binding.checkpoint_sha256 != complete_digest:
            raise TrainingBackendError(
                "checkpoint_hash_mismatch",
                "checkpoint tree does not match its admitted binding",
            )
        self._context.cancel.raise_if_cancelled()

    def seal_named(self, name: str) -> Path:
        raw = Path(str(name or ""))
        if not str(name) or raw.is_absolute() or len(raw.parts) != 1 or raw.name in {".", ".."}:
            raise TrainingBackendError(
                "checkpoint_boundary_violation",
                "checkpoint event name must identify one direct child directory",
            )
        return self.seal(self._checkpoint_root / raw.name)

    def seal(self, checkpoint: Path) -> Path:
        self._context.cancel.raise_if_cancelled()
        path = _absolute(checkpoint)
        _assert_contained_symlink_free(
            self._checkpoint_root,
            path,
            require_directory=True,
        )
        try:
            relative = path.relative_to(self._checkpoint_root)
        except ValueError as exc:
            raise TrainingBackendError(
                "checkpoint_boundary_violation",
                "checkpoint escaped its attempt root",
            ) from exc
        if len(relative.parts) != 1:
            raise TrainingBackendError(
                "checkpoint_boundary_violation",
                "checkpoint must be a direct child of its attempt root",
            )
        _validate_checkpoint_tree(path, self._context.cancel)
        payload_digest = _checkpoint_tree_sha256(
            path,
            cancel=self._context.cancel,
            include_manifest=False,
        )
        expected = self._expected_manifest(
            job_id=self._context.request.job_id,
            attempt_id=self._context.request.attempt_id,
            checkpoint_name=path.name,
        )
        manifest = {
            **expected,
            "fencing_token": self._context.request.fencing_token,
            "checkpoint_payload_sha256": payload_digest,
        }
        manifest_path = path / CHECKPOINT_MANIFEST_NAME
        if manifest_path.exists() or manifest_path.is_symlink():
            existing = _load_manifest(path)
            if existing != manifest:
                raise TrainingBackendError(
                    "checkpoint_manifest_mismatch",
                    "existing checkpoint manifest does not match this attempt",
                )
            return manifest_path
        self._context.cancel.raise_if_cancelled()
        _write_manifest_atomic(
            manifest_path,
            manifest,
            context=self._context,
        )
        return manifest_path

    def _expected_manifest(
        self,
        *,
        job_id: str,
        attempt_id: str,
        checkpoint_name: str,
    ) -> dict[str, Any]:
        request = self._context.request
        return {
            "schema": CHECKPOINT_MANIFEST_SCHEMA,
            "tenant_scope_digest": request.tenant_scope_digest,
            "job_id": str(job_id),
            "attempt_id": str(attempt_id),
            "backend": self._backend_name,
            "dataset_hash": request.dataset.identity_hash,
            "base_model_hash": request.base_model.snapshot_hash,
            "configuration_hash": request.configuration.identity_hash,
            "checkpoint_name": str(checkpoint_name),
        }


def _write_manifest_atomic(
    manifest_path: Path,
    manifest: Mapping[str, Any],
    *,
    context: TrainingContext,
) -> None:
    encoded = json.dumps(
        dict(manifest),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > _MAX_MANIFEST_BYTES:
        raise TrainingBackendError(
            "checkpoint_manifest_invalid",
            "checkpoint manifest exceeds its byte limit",
        )
    temporary = manifest_path.parent / (f".{CHECKPOINT_MANIFEST_NAME}.{uuid.uuid4().hex}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor: int | None = None
    try:
        descriptor = os.open(temporary, flags, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = None
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        context.cancel.raise_if_cancelled()
        os.replace(temporary, manifest_path)
        directory_flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            directory_flags |= os.O_DIRECTORY
        directory_descriptor = os.open(manifest_path.parent, directory_flags)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except TrainingBackendError:
        raise
    except OSError as exc:
        raise TrainingBackendError(
            "checkpoint_manifest_write_failed",
            "checkpoint manifest could not be committed atomically",
        ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _load_manifest(checkpoint: Path) -> dict[str, Any]:
    manifest_path = checkpoint / CHECKPOINT_MANIFEST_NAME
    if not manifest_path.is_file() or manifest_path.is_symlink() or manifest_path.stat().st_size > _MAX_MANIFEST_BYTES:
        raise TrainingBackendError(
            "checkpoint_manifest_missing",
            "checkpoint has no bounded regular atomic manifest",
        )
    try:
        raw = json.loads(
            manifest_path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_members,
        )
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise TrainingBackendError(
            "checkpoint_manifest_invalid",
            "checkpoint manifest is not valid JSON",
        ) from exc
    if not isinstance(raw, dict) or frozenset(raw) != _MANIFEST_FIELDS:
        raise TrainingBackendError(
            "checkpoint_manifest_invalid",
            "checkpoint manifest fields do not match the closed contract",
        )
    if (
        raw.get("schema") != CHECKPOINT_MANIFEST_SCHEMA
        or not _SHA256_RE.fullmatch(str(raw.get("tenant_scope_digest") or ""))
        or not _SHA256_RE.fullmatch(str(raw.get("dataset_hash") or ""))
        or not _SHA256_RE.fullmatch(str(raw.get("base_model_hash") or ""))
        or not _SHA256_RE.fullmatch(str(raw.get("configuration_hash") or ""))
        or not _SHA256_RE.fullmatch(str(raw.get("checkpoint_payload_sha256") or ""))
    ):
        raise TrainingBackendError(
            "checkpoint_manifest_invalid",
            "checkpoint manifest contains an invalid schema or digest",
        )
    return raw


def _assert_manifest_binding(
    manifest: Mapping[str, Any],
    expected: Mapping[str, Any],
) -> None:
    if any(manifest.get(key) != value for key, value in expected.items()):
        raise TrainingBackendError(
            "checkpoint_binding_mismatch",
            "checkpoint manifest provenance does not match the resume request",
        )


def _validate_checkpoint_tree(
    checkpoint: Path,
    cancel: CancellationToken,
) -> None:
    entries = list(checkpoint.rglob("*"))
    if not entries:
        raise TrainingBackendError(
            "checkpoint_missing",
            "checkpoint directory is empty",
        )
    for entry in entries:
        cancel.raise_if_cancelled()
        if entry.is_symlink():
            raise TrainingBackendError(
                "checkpoint_symlink_forbidden",
                "checkpoint tree contains a symbolic link",
            )
        if not entry.is_file() and not entry.is_dir():
            raise TrainingBackendError(
                "checkpoint_entry_forbidden",
                "checkpoint tree contains a non-regular entry",
            )


def _checkpoint_tree_sha256(
    checkpoint: Path,
    *,
    cancel: CancellationToken,
    include_manifest: bool,
) -> str:
    children = sorted(
        entry
        for entry in checkpoint.rglob("*")
        if entry.is_file()
        and (include_manifest or entry.relative_to(checkpoint).as_posix() != CHECKPOINT_MANIFEST_NAME)
    )
    if not children:
        raise TrainingBackendError(
            "checkpoint_missing",
            "checkpoint has no regular payload files",
        )
    digest = hashlib.sha256()
    for child in children:
        cancel.raise_if_cancelled()
        if child.is_symlink():
            raise TrainingBackendError(
                "checkpoint_symlink_forbidden",
                "checkpoint payload contains a symbolic link",
            )
        digest.update(child.relative_to(checkpoint).as_posix().encode("utf-8"))
        digest.update(b"\x00")
        digest.update(_file_sha256(child, cancel).encode("ascii"))
        digest.update(b"\x00")
    return digest.hexdigest()


def _file_sha256(path: Path, cancel: CancellationToken) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                cancel.raise_if_cancelled()
                digest.update(chunk)
    except OSError as exc:
        raise TrainingBackendError(
            "checkpoint_hash_failed",
            "checkpoint payload could not be hashed",
        ) from exc
    return digest.hexdigest()


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _assert_contained_symlink_free(
    root: Path,
    candidate: Path,
    *,
    require_directory: bool,
) -> None:
    admitted_root = _absolute(root)
    admitted_candidate = _absolute(candidate)
    try:
        relative = admitted_candidate.relative_to(admitted_root)
    except ValueError as exc:
        raise TrainingBackendError(
            "checkpoint_boundary_violation",
            "checkpoint path escapes its admitted state root",
        ) from exc
    if admitted_root.is_symlink() or not admitted_root.is_dir():
        raise TrainingBackendError(
            "checkpoint_boundary_violation",
            "checkpoint state root must be a regular directory",
        )
    current = admitted_root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise TrainingBackendError(
                "checkpoint_symlink_forbidden",
                "checkpoint path contains a symbolic-link component",
            )
    if require_directory and not admitted_candidate.is_dir():
        raise TrainingBackendError(
            "checkpoint_missing",
            "checkpoint path is not an existing directory",
        )


def _reject_duplicate_members(values: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in values:
        if key in result:
            raise ValueError("duplicate_json_member")
        result[key] = value
    return result


__all__ = [
    "CHECKPOINT_MANIFEST_NAME",
    "CHECKPOINT_MANIFEST_SCHEMA",
    "UnslothCheckpointLifecycle",
    "UnslothCheckpointManager",
]
