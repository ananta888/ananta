#!/usr/bin/env python3
"""Create the local Compose workflow keyrings without exposing Hub secrets.

This bootstrap is intentionally limited to development.  It creates a private
Hub directory, one public verification directory, and a private identity
directory for each Worker. Existing complete credentials are validated and
reused; known additive capability-only legacy documents are upgraded without
rotating credentials. An incomplete set fails closed instead of rotating keys
behind active workflow runs.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import stat
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

# The bootstrap is also invoked directly from a source checkout.  In that
# mode Python adds ``scripts/`` rather than the repository root to sys.path,
# so resolve the adjacent, version-matched contracts explicitly.
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from cryptography.fernet import Fernet  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: E402
    Ed25519PrivateKey,
)

from ananta_contracts.file_credentials import read_file_managed_bytes  # noqa: E402
from ananta_contracts.runtime_authorization_crypto import (  # noqa: E402
    ED25519_ALGORITHM,
    ED25519_SIGNING_KEYRING_SCHEMA,
    Ed25519SigningKeyRing,
    Ed25519VerificationKeyRing,
)
from scripts import dev_workflow_identity_documents as _identity_documents  # noqa: E402

_WORKER_CAPABILITIES = _identity_documents.WORKER_CAPABILITIES
_UPGRADABLE_WORKER_CAPABILITY_SETS = (
    _identity_documents.UPGRADABLE_WORKER_CAPABILITY_SETS
)
WorkerRegistrationSpec = _identity_documents.WorkerRegistrationSpec
_registration_document = _identity_documents.registration_document
_sha256_text = _identity_documents._sha256_text

_AUTH_KEY_ID = "dev-workflow-auth-v1"
_DISPATCH_KEY_ID = "dev-workflow-dispatch-v1"
_SIGNING_FILENAME = "workflow-auth-signing-keyring.json"
_VERIFICATION_FILENAME = "workflow-auth-verification-keyring.json"
_DISPATCH_FILENAME = "workflow-dispatch-keyring.json"
_REGISTRATION_KEYRING_FILENAME = "worker-registration-keyring.json"
_HUB_SERVICE_TOKEN_FILENAME = "hub-service-token"
_HUB_SESSION_KEY_FILENAME = "hub-session-signing-key"
_WORKER_SERVICE_TOKEN_FILENAME = "worker-service-token"
_WORKER_REGISTRATION_TOKEN_FILENAME = "worker-registration-token"
_WORKER_SESSION_KEY_FILENAME = "worker-session-signing-key"
_TRANSACTION_FILENAME = ".bootstrap-transaction.json"
_TRANSACTION_SCHEMA = "ananta.dev-workflow-bootstrap-transaction.v1"
_STAGING_PREFIX = ".bootstrap-staging-"
_MAX_KEYRING_BYTES = 65_536
_AUTHORIZATION_DOCUMENTS = frozenset(
    {"signing", "verification", "dispatch"}
)
_IDENTITY_DOCUMENTS = frozenset(
    {
        "registration_keyring",
        "hub_service_token",
        "hub_session_key",
        "alpha_service_token",
        "alpha_registration_token",
        "alpha_session_key",
        "beta_service_token",
        "beta_registration_token",
        "beta_session_key",
    }
)
_ALL_DOCUMENTS = _AUTHORIZATION_DOCUMENTS | _IDENTITY_DOCUMENTS


class DevWorkflowKeyringBootstrapError(RuntimeError):
    """Raised when a local keyring set cannot be trusted or completed."""


def bootstrap(
    root: Path,
    *,
    alpha_worker_id: str = "ananta-worker-1",
    beta_worker_id: str = "ananta-worker-2",
) -> str:
    worker_specs = _worker_specs(
        alpha_worker_id=alpha_worker_id,
        beta_worker_id=beta_worker_id,
    )
    paths = _paths(root)
    _prepare_directories(paths)
    _recover_interrupted_transaction(
        paths,
        worker_specs=worker_specs,
    )
    _assert_expected_entries(paths)
    _assert_expected_file_types(paths)
    existing = {
        name
        for name in _ALL_DOCUMENTS
        if paths[name].exists()
    }

    if existing == _ALL_DOCUMENTS:
        registration_upgrade_required = _validate(
            paths,
            worker_specs=worker_specs,
            allow_legacy_registration=True,
        )
        if registration_upgrade_required:
            secrets_by_name = _read_identity_secrets(paths)
            _atomic_write_json(
                paths["registration_keyring"],
                _registration_document(
                    secrets_by_name,
                    worker_specs=worker_specs,
                ),
                mode=0o600,
            )
            _validate(paths, worker_specs=worker_specs)
            return "upgraded"
        return "reused"
    if existing == _AUTHORIZATION_DOCUMENTS:
        documents = _generate_worker_identity_documents(
            worker_specs=worker_specs
        )
        _stage_validate_and_publish(
            paths,
            documents=documents,
            target_names=_IDENTITY_DOCUMENTS,
            mode="legacy_upgrade",
            worker_specs=worker_specs,
        )
        return "upgraded"
    if existing:
        missing = ", ".join(sorted(_ALL_DOCUMENTS - existing))
        raise DevWorkflowKeyringBootstrapError(
            f"incomplete development workflow keyring set; missing: {missing}"
        )

    documents = _generate_documents(worker_specs=worker_specs)
    _stage_validate_and_publish(
        paths,
        documents=documents,
        target_names=_ALL_DOCUMENTS,
        mode="create",
        worker_specs=worker_specs,
    )
    return "created"


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
        "registration_keyring": (
            normalized / "hub" / _REGISTRATION_KEYRING_FILENAME
        ),
        "hub_service_token": (
            normalized / "hub" / _HUB_SERVICE_TOKEN_FILENAME
        ),
        "hub_session_key": (
            normalized / "hub" / _HUB_SESSION_KEY_FILENAME
        ),
        "alpha_service_token": (
            normalized / "alpha" / _WORKER_SERVICE_TOKEN_FILENAME
        ),
        "alpha_registration_token": (
            normalized
            / "alpha"
            / _WORKER_REGISTRATION_TOKEN_FILENAME
        ),
        "alpha_session_key": (
            normalized / "alpha" / _WORKER_SESSION_KEY_FILENAME
        ),
        "beta_service_token": (
            normalized / "beta" / _WORKER_SERVICE_TOKEN_FILENAME
        ),
        "beta_registration_token": (
            normalized
            / "beta"
            / _WORKER_REGISTRATION_TOKEN_FILENAME
        ),
        "beta_session_key": (
            normalized / "beta" / _WORKER_SESSION_KEY_FILENAME
        ),
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
            raise DevWorkflowKeyringBootstrapError(
                f"development workflow keyring path is not a directory: {name}"
            )
        path.chmod(0o700)


def _assert_expected_entries(paths: dict[str, Path]) -> None:
    expected_root_entries = {
        paths["hub_dir"].name,
        paths["worker_dir"].name,
        paths["alpha_dir"].name,
        paths["beta_dir"].name,
    }
    unexpected_root = sorted(
        path.name
        for path in paths["root"].iterdir()
        if path.name not in expected_root_entries
    )
    if unexpected_root:
        raise DevWorkflowKeyringBootstrapError(
            "unexpected entry in development workflow root"
        )

    allowed = {
        "hub_dir": {
            _SIGNING_FILENAME,
            _DISPATCH_FILENAME,
            _REGISTRATION_KEYRING_FILENAME,
            _HUB_SERVICE_TOKEN_FILENAME,
            _HUB_SESSION_KEY_FILENAME,
        },
        "worker_dir": {_VERIFICATION_FILENAME},
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
        unexpected = sorted(
            path.name
            for path in paths[directory_name].iterdir()
            if path.name not in allowed_names
        )
        if unexpected:
            raise DevWorkflowKeyringBootstrapError(
                f"unexpected entry in development workflow {directory_name}"
            )


def _assert_expected_file_types(paths: dict[str, Path]) -> None:
    for name in _ALL_DOCUMENTS:
        path = paths[name]
        if not os.path.lexists(path):
            continue
        if path.is_symlink() or not path.is_file():
            raise DevWorkflowKeyringBootstrapError(
                f"development workflow credential path is unsafe: {name}"
            )


def _generate_documents(
    *,
    worker_specs: tuple[WorkerRegistrationSpec, ...],
) -> dict[str, Any]:
    private = Ed25519PrivateKey.generate()
    private_seed = private.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    encoded_private_seed = _encode_base64(private_seed)
    signer = Ed25519SigningKeyRing(
        {_AUTH_KEY_ID: encoded_private_seed},
        active_key_id=_AUTH_KEY_ID,
    )
    return {
        "signing": {
            "schema": ED25519_SIGNING_KEYRING_SCHEMA,
            "algorithm": ED25519_ALGORITHM,
            "active_key_id": _AUTH_KEY_ID,
            "private_keys": {
                _AUTH_KEY_ID: encoded_private_seed,
            },
        },
        "verification": signer.verification_mapping(),
        "dispatch": {
            "active_key_id": _DISPATCH_KEY_ID,
            "keys": {
                _DISPATCH_KEY_ID: Fernet.generate_key().decode("ascii"),
            },
        },
        **_generate_worker_identity_documents(
            worker_specs=worker_specs
        ),
    }


def _generate_worker_identity_documents(
    *,
    worker_specs: tuple[WorkerRegistrationSpec, ...],
) -> dict[str, Any]:
    tokens = {
        "hub_service_token": secrets.token_urlsafe(48),
        "hub_session_key": secrets.token_urlsafe(48),
        "alpha_service_token": secrets.token_urlsafe(48),
        "alpha_registration_token": secrets.token_urlsafe(48),
        "alpha_session_key": secrets.token_urlsafe(48),
        "beta_service_token": secrets.token_urlsafe(48),
        "beta_registration_token": secrets.token_urlsafe(48),
        "beta_session_key": secrets.token_urlsafe(48),
    }
    tokens["registration_keyring"] = _registration_document(
        tokens,
        worker_specs=worker_specs,
    )
    return tokens


def _write_documents(
    paths: dict[str, Path],
    documents: dict[str, Any],
    *,
    target_names: frozenset[str],
) -> None:
    json_documents = {
        "signing",
        "verification",
        "dispatch",
        "registration_keyring",
    }
    for name in sorted(target_names):
        if name not in documents:
            raise DevWorkflowKeyringBootstrapError(
                f"development workflow document missing: {name}"
            )
        if name in json_documents:
            _atomic_write_json(
                paths[name],
                documents[name],
                mode=0o444 if name == "verification" else 0o600,
            )
        else:
            _atomic_write_bytes(
                paths[name],
                (str(documents[name]) + "\n").encode("utf-8"),
                mode=0o600,
            )


def _stage_validate_and_publish(
    paths: dict[str, Path],
    *,
    documents: dict[str, Any],
    target_names: frozenset[str],
    mode: str,
    worker_specs: tuple[WorkerRegistrationSpec, ...],
) -> None:
    if mode not in {"create", "legacy_upgrade"}:
        raise DevWorkflowKeyringBootstrapError(
            "development workflow transaction mode is invalid"
        )
    expected_targets = (
        _ALL_DOCUMENTS if mode == "create" else _IDENTITY_DOCUMENTS
    )
    if target_names != expected_targets:
        raise DevWorkflowKeyringBootstrapError(
            "development workflow transaction target set is invalid"
        )
    for name in target_names:
        if os.path.lexists(paths[name]):
            raise DevWorkflowKeyringBootstrapError(
                "development workflow transaction refuses to overwrite credentials"
            )

    staging_root = (
        paths["root"]
        / f"{_STAGING_PREFIX}{uuid.uuid4().hex}"
    )
    staging_paths = _paths(staging_root)
    journal_written = False
    try:
        _prepare_directories(staging_paths)
        _write_documents(
            staging_paths,
            documents,
            target_names=target_names,
        )
        candidate_paths = dict(paths)
        for name in target_names:
            candidate_paths[name] = staging_paths[name]
        _validate(
            candidate_paths,
            worker_specs=worker_specs,
        )

        target_hashes = {
            name: _sha256_path(staging_paths[name])
            for name in sorted(target_names)
        }
        _atomic_write_json(
            paths["transaction"],
            {
                "schema": _TRANSACTION_SCHEMA,
                "mode": mode,
                "staging_name": staging_root.name,
                "target_hashes": target_hashes,
            },
            mode=0o600,
        )
        journal_written = True

        for name in sorted(target_names):
            os.replace(staging_paths[name], paths[name])
            _fsync_directory(paths[name].parent)
        _validate(paths, worker_specs=worker_specs)
    except DevWorkflowKeyringBootstrapError:
        if journal_written:
            _recover_interrupted_transaction(
                paths,
                worker_specs=worker_specs,
            )
        raise
    except OSError as exc:
        if journal_written:
            try:
                _recover_interrupted_transaction(
                    paths,
                    worker_specs=worker_specs,
                )
            except DevWorkflowKeyringBootstrapError as recovery_exc:
                raise recovery_exc from exc
        raise DevWorkflowKeyringBootstrapError(
            "development workflow transaction could not be published"
        ) from exc
    finally:
        if not journal_written and staging_root.exists():
            _remove_staging_tree(
                root=paths["root"],
                staging=staging_root,
            )

    _remove_staging_tree(
        root=paths["root"],
        staging=staging_root,
    )
    paths["transaction"].unlink()
    _fsync_directory(paths["root"])


def _recover_interrupted_transaction(
    paths: dict[str, Path],
    *,
    worker_specs: tuple[WorkerRegistrationSpec, ...],
) -> None:
    journal_path = paths["transaction"]
    if not os.path.lexists(journal_path):
        return
    journal = _read_json(
        journal_path,
        description="development workflow transaction journal",
    )
    mode = str(journal.get("mode") or "")
    expected_targets = (
        _ALL_DOCUMENTS
        if mode == "create"
        else _IDENTITY_DOCUMENTS
        if mode == "legacy_upgrade"
        else frozenset()
    )
    target_hashes = journal.get("target_hashes")
    staging_name = str(journal.get("staging_name") or "")
    if (
        journal.get("schema") != _TRANSACTION_SCHEMA
        or not expected_targets
        or not isinstance(target_hashes, dict)
        or set(target_hashes) != expected_targets
        or not staging_name.startswith(_STAGING_PREFIX)
        or Path(staging_name).name != staging_name
    ):
        raise DevWorkflowKeyringBootstrapError(
            "development workflow transaction journal cannot be trusted"
        )
    for value in target_hashes.values():
        digest = str(value or "")
        if (
            len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise DevWorkflowKeyringBootstrapError(
                "development workflow transaction journal cannot be trusted"
            )

    published: list[str] = []
    for name in sorted(expected_targets):
        target = paths[name]
        if not os.path.lexists(target):
            continue
        if target.is_symlink() or not target.is_file():
            raise DevWorkflowKeyringBootstrapError(
                "development workflow transaction target is unsafe"
            )
        if not secrets.compare_digest(
            _sha256_path(target),
            str(target_hashes[name]),
        ):
            raise DevWorkflowKeyringBootstrapError(
                "development workflow transaction target was modified"
            )
        published.append(name)

    staging_root = paths["root"] / staging_name
    if len(published) == len(expected_targets):
        _validate(paths, worker_specs=worker_specs)
    else:
        for name in published:
            paths[name].unlink()
            _fsync_directory(paths[name].parent)

    if os.path.lexists(staging_root):
        _remove_staging_tree(
            root=paths["root"],
            staging=staging_root,
        )
    journal_path.unlink()
    _fsync_directory(paths["root"])


def _validate(
    paths: dict[str, Path],
    *,
    worker_specs: tuple[WorkerRegistrationSpec, ...],
    allow_legacy_registration: bool = False,
) -> bool:
    _assert_credential_modes(paths)
    signing = _read_json(paths["signing"], description="development signing keyring")
    verification = _read_json(
        paths["verification"],
        description="development verification keyring",
    )
    dispatch = _read_json(paths["dispatch"], description="development dispatch keyring")

    try:
        signer = Ed25519SigningKeyRing.from_mapping(signing)
        Ed25519VerificationKeyRing.from_mapping(verification)
    except ValueError as exc:
        raise DevWorkflowKeyringBootstrapError(
            "development workflow authorization keyring is invalid"
        ) from exc
    if verification != signer.verification_mapping():
        raise DevWorkflowKeyringBootstrapError(
            "development workflow signing and verification keyrings do not match"
        )

    active_key_id = str(dispatch.get("active_key_id") or "")
    keys = dispatch.get("keys")
    if not isinstance(keys, dict) or active_key_id not in keys:
        raise DevWorkflowKeyringBootstrapError(
            "development workflow dispatch keyring is incomplete"
        )
    try:
        Fernet(str(keys[active_key_id]).encode("ascii"))
    except (TypeError, ValueError) as exc:
        raise DevWorkflowKeyringBootstrapError(
            "development workflow dispatch keyring is invalid"
        ) from exc

    secrets_by_name = _read_identity_secrets(paths)
    if len(set(secrets_by_name.values())) != len(secrets_by_name):
        raise DevWorkflowKeyringBootstrapError(
            "development workflow credentials must be disjoint"
        )
    registration = _read_json(
        paths["registration_keyring"],
        description="development Worker registration keyring",
    )
    expected_registration = _registration_document(
        secrets_by_name,
        worker_specs=worker_specs,
    )
    if registration == expected_registration:
        return False
    if allow_legacy_registration:
        for capabilities in _UPGRADABLE_WORKER_CAPABILITY_SETS:
            if registration == _registration_document(
                secrets_by_name,
                worker_specs=worker_specs,
                capabilities=list(capabilities),
            ):
                return True
    raise DevWorkflowKeyringBootstrapError(
        "development Worker registration keyring does not match credentials"
    )


def _read_identity_secrets(
    paths: dict[str, Path],
) -> dict[str, str]:
    secrets_by_name = {
        name: _read_secret(paths[name], description=name)
        for name in (
            "hub_service_token",
            "hub_session_key",
            "alpha_service_token",
            "alpha_registration_token",
            "alpha_session_key",
            "beta_service_token",
            "beta_registration_token",
            "beta_session_key",
        )
    }
    return secrets_by_name


def _assert_credential_modes(paths: dict[str, Path]) -> None:
    for name in _ALL_DOCUMENTS:
        path = paths[name]
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise DevWorkflowKeyringBootstrapError(
                f"development workflow credential is unavailable: {name}"
            ) from exc
        expected_mode = 0o444 if name == "verification" else 0o600
        if (
            not stat.S_ISREG(metadata.st_mode)
            or int(metadata.st_nlink) != 1
            or stat.S_IMODE(metadata.st_mode) != expected_mode
        ):
            raise DevWorkflowKeyringBootstrapError(
                f"development workflow credential mode is invalid: {name}"
            )


def _assign_host_ownership(
    paths: dict[str, Path],
    *,
    owner_uid: int,
    owner_gid: int,
) -> None:
    """Make the bind-mounted credentials backupable by the WSL host user."""

    if (
        owner_uid < 0
        or owner_gid < 0
        or owner_uid > 2_147_483_647
        or owner_gid > 2_147_483_647
    ):
        raise DevWorkflowKeyringBootstrapError(
            "development workflow credential owner is invalid"
        )
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
                raise DevWorkflowKeyringBootstrapError(
                    "development workflow credential ownership target is unsafe"
                )
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


def _encode_base64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(8192):
                digest.update(chunk)
    except OSError as exc:
        raise DevWorkflowKeyringBootstrapError(
            "development workflow credential cannot be hashed"
        ) from exc
    return digest.hexdigest()


def _read_secret(path: Path, *, description: str) -> str:
    try:
        value = read_file_managed_bytes(
            str(path),
            description=description,
            max_bytes=_MAX_KEYRING_BYTES,
        ).decode("utf-8").strip()
    except (OSError, UnicodeError, ValueError) as exc:
        raise DevWorkflowKeyringBootstrapError(
            f"{description} cannot be trusted"
        ) from exc
    if len(value.encode("utf-8")) < 32:
        raise DevWorkflowKeyringBootstrapError(
            f"{description} is too short"
        )
    if "\x00" in value or any(
        character.isspace() for character in value
    ):
        raise DevWorkflowKeyringBootstrapError(
            f"{description} value is invalid"
        )
    return value


def _worker_specs(
    *,
    alpha_worker_id: str,
    beta_worker_id: str,
) -> tuple[WorkerRegistrationSpec, ...]:
    normalized_ids = tuple(
        str(value or "").strip()
        for value in (alpha_worker_id, beta_worker_id)
    )
    if (
        len(set(normalized_ids)) != 2
        or any(
            not value
            or len(value.encode("utf-8")) > 256
            or "\x00" in value
            or any(character in value for character in "\r\n")
            for value in normalized_ids
        )
    ):
        raise DevWorkflowKeyringBootstrapError(
            "development Worker identifiers are invalid"
        )
    return (
        WorkerRegistrationSpec(
            logical_name="alpha",
            worker_id=normalized_ids[0],
            worker_url="http://ai-agent-alpha:5000",
        ),
        WorkerRegistrationSpec(
            logical_name="beta",
            worker_id=normalized_ids[1],
            worker_url="http://ai-agent-beta:5000",
        ),
    )


def _remove_staging_tree(*, root: Path, staging: Path) -> None:
    normalized_root = Path(os.path.abspath(os.fspath(root)))
    normalized_staging = Path(
        os.path.abspath(os.fspath(staging))
    )
    if (
        normalized_staging.parent != normalized_root
        or not normalized_staging.name.startswith(_STAGING_PREFIX)
        or normalized_staging.is_symlink()
    ):
        raise DevWorkflowKeyringBootstrapError(
            "development workflow staging path is unsafe"
        )
    if not normalized_staging.exists():
        return

    def remove_directory(directory: Path) -> None:
        for entry in directory.iterdir():
            if entry.is_symlink():
                raise DevWorkflowKeyringBootstrapError(
                    "development workflow staging entry is unsafe"
                )
            if entry.is_dir():
                remove_directory(entry)
                entry.rmdir()
            elif entry.is_file():
                entry.unlink()
            else:
                raise DevWorkflowKeyringBootstrapError(
                    "development workflow staging entry is unsafe"
                )

    remove_directory(normalized_staging)
    normalized_staging.rmdir()
    _fsync_directory(normalized_root)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create or validate local-only workflow runtime keyrings.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        required=True,
        help="Absolute directory mounted only into the local Compose stack.",
    )
    parser.add_argument(
        "--alpha-worker-id",
        default="ananta-worker-1",
        help="Registered identity for the local alpha Worker.",
    )
    parser.add_argument(
        "--beta-worker-id",
        default="ananta-worker-2",
        help="Registered identity for the local beta Worker.",
    )
    parser.add_argument(
        "--owner-uid",
        type=int,
        help=(
            "Optional WSL host UID that should own the generated bind-mounted "
            "credentials."
        ),
    )
    parser.add_argument(
        "--owner-gid",
        type=int,
        help=(
            "Optional WSL host GID that should own the generated bind-mounted "
            "credentials."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        if (args.owner_uid is None) != (args.owner_gid is None):
            raise DevWorkflowKeyringBootstrapError(
                "owner UID and GID must be configured together"
            )
        result = bootstrap(
            args.root,
            alpha_worker_id=args.alpha_worker_id,
            beta_worker_id=args.beta_worker_id,
        )
        if args.owner_uid is not None and args.owner_gid is not None:
            _assign_host_ownership(
                _paths(args.root),
                owner_uid=args.owner_uid,
                owner_gid=args.owner_gid,
            )
    except DevWorkflowKeyringBootstrapError as exc:
        print(f"workflow keyring bootstrap failed: {exc}", file=os.sys.stderr)
        return 64
    print(f"development workflow keyrings {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
