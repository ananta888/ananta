"""Filesystem safety and atomic IO for development workflow keyrings."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

from ananta_contracts.file_credentials import read_file_managed_bytes
from scripts.dev_workflow_keyring_contract import (
    _ALL_DOCUMENTS,
    _DISPATCH_FILENAME,
    _HUB_SERVICE_TOKEN_FILENAME,
    _HUB_SESSION_KEY_FILENAME,
    _MAX_KEYRING_BYTES,
    _REGISTRATION_KEYRING_FILENAME,
    _SIGNING_FILENAME,
    _SOURCE_ACCESS_KEYRING_FILENAME,
    _STAGING_PREFIX,
    _TRANSACTION_FILENAME,
    _VERIFICATION_FILENAME,
    _WORKER_REGISTRATION_TOKEN_FILENAME,
    _WORKER_SERVICE_TOKEN_FILENAME,
    _WORKER_SESSION_KEY_FILENAME,
    DevWorkflowKeyringBootstrapError,
)


def _paths(root: Path) -> dict[str, Path]:
    if not root.is_absolute():
        raise DevWorkflowKeyringBootstrapError("keyring root must be absolute")
    if root.is_symlink():
        raise DevWorkflowKeyringBootstrapError("keyring root must not be a symlink")
    normalized = Path(os.path.abspath(os.fspath(root)))
    if normalized == Path("/"):
        raise DevWorkflowKeyringBootstrapError("keyring root must not be filesystem root")
    return {
        "root": normalized,
        "hub_dir": normalized / "hub",
        "worker_dir": normalized / "worker",
        "alpha_dir": normalized / "alpha",
        "beta_dir": normalized / "beta",
        "transaction": normalized / _TRANSACTION_FILENAME,
        "signing": normalized / "hub" / _SIGNING_FILENAME,
        "verification": normalized / "worker" / _VERIFICATION_FILENAME,
        "dispatch": normalized / "hub" / _DISPATCH_FILENAME,
        "registration_keyring": (normalized / "hub" / _REGISTRATION_KEYRING_FILENAME),
        "hub_service_token": (normalized / "hub" / _HUB_SERVICE_TOKEN_FILENAME),
        "hub_session_key": (normalized / "hub" / _HUB_SESSION_KEY_FILENAME),
        "alpha_service_token": (normalized / "alpha" / _WORKER_SERVICE_TOKEN_FILENAME),
        "alpha_registration_token": (normalized / "alpha" / _WORKER_REGISTRATION_TOKEN_FILENAME),
        "alpha_session_key": (normalized / "alpha" / _WORKER_SESSION_KEY_FILENAME),
        "beta_service_token": (normalized / "beta" / _WORKER_SERVICE_TOKEN_FILENAME),
        "beta_registration_token": (normalized / "beta" / _WORKER_REGISTRATION_TOKEN_FILENAME),
        "beta_session_key": (normalized / "beta" / _WORKER_SESSION_KEY_FILENAME),
        "source_access_keyring": (normalized / "worker" / _SOURCE_ACCESS_KEYRING_FILENAME),
    }


def _prepare_directories(paths: dict[str, Path]) -> None:
    for name in (
        "root",
        "hub_dir",
        "worker_dir",
        "alpha_dir",
        "beta_dir",
    ):
        path = paths[name]
        if path.is_symlink():
            raise DevWorkflowKeyringBootstrapError(
                f"development workflow keyring directory must not be a symlink: {name}"
            )
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not path.is_dir():
            raise DevWorkflowKeyringBootstrapError(f"development workflow keyring path is not a directory: {name}")
        path.chmod(0o700)


def _assert_expected_entries(paths: dict[str, Path]) -> None:
    expected_root_entries = {
        paths["hub_dir"].name,
        paths["worker_dir"].name,
        paths["alpha_dir"].name,
        paths["beta_dir"].name,
    }
    unexpected_root = sorted(path.name for path in paths["root"].iterdir() if path.name not in expected_root_entries)
    if unexpected_root:
        raise DevWorkflowKeyringBootstrapError("unexpected entry in development workflow root")

    allowed = {
        "hub_dir": {
            _SIGNING_FILENAME,
            _DISPATCH_FILENAME,
            _REGISTRATION_KEYRING_FILENAME,
            _HUB_SERVICE_TOKEN_FILENAME,
            _HUB_SESSION_KEY_FILENAME,
        },
        "worker_dir": {
            _VERIFICATION_FILENAME,
            _SOURCE_ACCESS_KEYRING_FILENAME,
        },
        "alpha_dir": {
            _WORKER_SERVICE_TOKEN_FILENAME,
            _WORKER_REGISTRATION_TOKEN_FILENAME,
            _WORKER_SESSION_KEY_FILENAME,
        },
        "beta_dir": {
            _WORKER_SERVICE_TOKEN_FILENAME,
            _WORKER_REGISTRATION_TOKEN_FILENAME,
            _WORKER_SESSION_KEY_FILENAME,
        },
    }
    for directory_name, allowed_names in allowed.items():
        unexpected = sorted(path.name for path in paths[directory_name].iterdir() if path.name not in allowed_names)
        if unexpected:
            raise DevWorkflowKeyringBootstrapError(f"unexpected entry in development workflow {directory_name}")


def _assert_expected_file_types(paths: dict[str, Path]) -> None:
    for name in _ALL_DOCUMENTS:
        path = paths[name]
        if not os.path.lexists(path):
            continue
        if path.is_symlink() or not path.is_file():
            raise DevWorkflowKeyringBootstrapError(f"development workflow credential path is unsafe: {name}")


def _assign_host_ownership(
    paths: dict[str, Path],
    *,
    owner_uid: int,
    owner_gid: int,
) -> None:
    """Make the bind-mounted credentials backupable by the WSL host user."""

    if owner_uid < 0 or owner_gid < 0 or owner_uid > 2_147_483_647 or owner_gid > 2_147_483_647:
        raise DevWorkflowKeyringBootstrapError("development workflow credential owner is invalid")
    ordered_paths = [
        *(paths[name] for name in sorted(_ALL_DOCUMENTS)),
        paths["hub_dir"],
        paths["worker_dir"],
        paths["alpha_dir"],
        paths["beta_dir"],
        paths["root"],
    ]
    try:
        for path in ordered_paths:
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                raise DevWorkflowKeyringBootstrapError("development workflow credential ownership target is unsafe")
            os.chown(
                path,
                owner_uid,
                owner_gid,
                follow_symlinks=False,
            )
    except OSError as exc:
        raise DevWorkflowKeyringBootstrapError(
            "development workflow credential ownership could not be assigned"
        ) from exc


def _read_json(path: Path, *, description: str) -> dict[str, Any]:
    try:
        raw = read_file_managed_bytes(
            str(path),
            description=description,
            max_bytes=_MAX_KEYRING_BYTES,
        )
        decoded = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise DevWorkflowKeyringBootstrapError(f"{description} cannot be trusted") from exc
    if not isinstance(decoded, dict):
        raise DevWorkflowKeyringBootstrapError(f"{description} must be an object")
    return {str(key): value for key, value in decoded.items()}


def _atomic_write_json(path: Path, document: dict[str, Any], *, mode: int) -> None:
    payload = (
        json.dumps(
            document,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    _atomic_write_bytes(path, payload, mode=mode)


def _atomic_write_bytes(path: Path, payload: bytes, *, mode: int) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(mode)
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(8192):
                digest.update(chunk)
    except OSError as exc:
        raise DevWorkflowKeyringBootstrapError("development workflow credential cannot be hashed") from exc
    return digest.hexdigest()


def _read_secret(path: Path, *, description: str) -> str:
    try:
        value = (
            read_file_managed_bytes(
                str(path),
                description=description,
                max_bytes=_MAX_KEYRING_BYTES,
            )
            .decode("utf-8")
            .strip()
        )
    except (OSError, UnicodeError, ValueError) as exc:
        raise DevWorkflowKeyringBootstrapError(f"{description} cannot be trusted") from exc
    if len(value.encode("utf-8")) < 32:
        raise DevWorkflowKeyringBootstrapError(f"{description} is too short")
    if "\x00" in value or any(character.isspace() for character in value):
        raise DevWorkflowKeyringBootstrapError(f"{description} value is invalid")
    return value


def _remove_staging_tree(*, root: Path, staging: Path) -> None:
    normalized_root = Path(os.path.abspath(os.fspath(root)))
    normalized_staging = Path(os.path.abspath(os.fspath(staging)))
    if (
        normalized_staging.parent != normalized_root
        or not normalized_staging.name.startswith(_STAGING_PREFIX)
        or normalized_staging.is_symlink()
    ):
        raise DevWorkflowKeyringBootstrapError("development workflow staging path is unsafe")
    if not normalized_staging.exists():
        return

    def remove_directory(directory: Path) -> None:
        for entry in directory.iterdir():
            if entry.is_symlink():
                raise DevWorkflowKeyringBootstrapError("development workflow staging entry is unsafe")
            if entry.is_dir():
                remove_directory(entry)
                entry.rmdir()
            elif entry.is_file():
                entry.unlink()
            else:
                raise DevWorkflowKeyringBootstrapError("development workflow staging entry is unsafe")

    remove_directory(normalized_staging)
    normalized_staging.rmdir()
    _fsync_directory(normalized_root)
