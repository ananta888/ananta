"""Crash-safe publication transaction for development workflow keyrings."""

from __future__ import annotations

import os
import secrets
import uuid
from pathlib import Path
from typing import Any

from scripts.dev_workflow_keyring_contract import (
    _ALL_DOCUMENTS,
    _IDENTITY_DOCUMENTS,
    _LEGACY_ALL_DOCUMENTS,
    _SOURCE_ACCESS_DOCUMENTS,
    _STAGING_PREFIX,
    _TRANSACTION_SCHEMA,
    DevWorkflowKeyringBootstrapError,
    WorkerRegistrationSpec,
)
from scripts.dev_workflow_keyring_filesystem import (
    _atomic_write_bytes,
    _atomic_write_json,
    _fsync_directory,
    _paths,
    _prepare_directories,
    _read_json,
    _remove_staging_tree,
    _sha256_path,
)
from scripts.dev_workflow_keyring_validation import _validate


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
        "source_access_keyring",
    }
    for name in sorted(target_names):
        if name not in documents:
            raise DevWorkflowKeyringBootstrapError(f"development workflow document missing: {name}")
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
    targets_by_mode = {
        "create": _ALL_DOCUMENTS,
        "legacy_upgrade": _IDENTITY_DOCUMENTS,
        "legacy_full_upgrade": (_IDENTITY_DOCUMENTS | _SOURCE_ACCESS_DOCUMENTS),
        "source_access_upgrade": _SOURCE_ACCESS_DOCUMENTS,
    }
    if mode not in targets_by_mode:
        raise DevWorkflowKeyringBootstrapError("development workflow transaction mode is invalid")
    expected_targets = targets_by_mode[mode]
    if target_names != expected_targets:
        raise DevWorkflowKeyringBootstrapError("development workflow transaction target set is invalid")
    for name in target_names:
        if os.path.lexists(paths[name]):
            raise DevWorkflowKeyringBootstrapError("development workflow transaction refuses to overwrite credentials")

    staging_root = paths["root"] / f"{_STAGING_PREFIX}{uuid.uuid4().hex}"
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

        target_hashes = {name: _sha256_path(staging_paths[name]) for name in sorted(target_names)}
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
        raise DevWorkflowKeyringBootstrapError("development workflow transaction could not be published") from exc
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
    target_hashes = journal.get("target_hashes")
    declared_targets = frozenset(target_hashes) if isinstance(target_hashes, dict) else frozenset()
    expected_targets = {
        "create": _ALL_DOCUMENTS,
        "legacy_upgrade": _IDENTITY_DOCUMENTS,
        "legacy_full_upgrade": (_IDENTITY_DOCUMENTS | _SOURCE_ACCESS_DOCUMENTS),
        "source_access_upgrade": _SOURCE_ACCESS_DOCUMENTS,
    }.get(mode, frozenset())
    if mode == "create" and declared_targets == _LEGACY_ALL_DOCUMENTS:
        expected_targets = _LEGACY_ALL_DOCUMENTS
    staging_name = str(journal.get("staging_name") or "")
    if (
        journal.get("schema") != _TRANSACTION_SCHEMA
        or not expected_targets
        or not isinstance(target_hashes, dict)
        or set(target_hashes) != expected_targets
        or not staging_name.startswith(_STAGING_PREFIX)
        or Path(staging_name).name != staging_name
    ):
        raise DevWorkflowKeyringBootstrapError("development workflow transaction journal cannot be trusted")
    for value in target_hashes.values():
        digest = str(value or "")
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise DevWorkflowKeyringBootstrapError("development workflow transaction journal cannot be trusted")

    published: list[str] = []
    for name in sorted(expected_targets):
        target = paths[name]
        if not os.path.lexists(target):
            continue
        if target.is_symlink() or not target.is_file():
            raise DevWorkflowKeyringBootstrapError("development workflow transaction target is unsafe")
        if not secrets.compare_digest(
            _sha256_path(target),
            str(target_hashes[name]),
        ):
            raise DevWorkflowKeyringBootstrapError("development workflow transaction target was modified")
        published.append(name)

    staging_root = paths["root"] / staging_name
    if len(published) == len(expected_targets):
        legacy_create = mode == "create" and expected_targets == _LEGACY_ALL_DOCUMENTS
        _validate(
            paths,
            worker_specs=worker_specs,
            allow_legacy_registration=(mode in {"create", "legacy_upgrade"}),
            allow_missing_source_access=(mode == "legacy_upgrade" or legacy_create),
        )
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
