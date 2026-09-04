"""Narrow injectable ports for the development keyring application service."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from scripts.dev_workflow_keyring_contract import WorkerRegistrationSpec, _registration_document
from scripts.dev_workflow_keyring_filesystem import (
    _assert_expected_entries,
    _assert_expected_file_types,
    _atomic_write_json,
    _paths,
    _prepare_directories,
)
from scripts.dev_workflow_keyring_material import (
    _generate_documents,
    _generate_source_access_documents,
    _generate_worker_identity_documents,
    _worker_specs,
)
from scripts.dev_workflow_keyring_transaction import (
    _recover_interrupted_transaction,
    _stage_validate_and_publish,
)
from scripts.dev_workflow_keyring_validation import _read_identity_secrets, _validate


class KeyringFileSystemPort(Protocol):
    def paths(self, root: Path) -> dict[str, Path]: ...

    def prepare(self, paths: dict[str, Path]) -> None: ...

    def assert_safe(self, paths: dict[str, Path]) -> None: ...

    def existing(self, paths: dict[str, Path], names: frozenset[str]) -> frozenset[str]: ...

    def read_identity_secrets(self, paths: dict[str, Path]) -> dict[str, str]: ...

    def write_json(self, path: Path, document: dict[str, Any], *, mode: int) -> None: ...


class KeyringMaterialPort(Protocol):
    def worker_specs(self, *, alpha_worker_id: str, beta_worker_id: str) -> tuple[WorkerRegistrationSpec, ...]: ...

    def all_documents(self, *, worker_specs: tuple[WorkerRegistrationSpec, ...]) -> dict[str, Any]: ...

    def identity_documents(self, *, worker_specs: tuple[WorkerRegistrationSpec, ...]) -> dict[str, Any]: ...

    def source_access_documents(self) -> dict[str, Any]: ...

    def registration_document(
        self,
        secrets_by_name: dict[str, str],
        *,
        worker_specs: tuple[WorkerRegistrationSpec, ...],
    ) -> dict[str, Any]: ...


class KeyringValidationPort(Protocol):
    def validate(
        self,
        paths: dict[str, Path],
        *,
        worker_specs: tuple[WorkerRegistrationSpec, ...],
        allow_legacy_registration: bool = False,
        allow_missing_source_access: bool = False,
    ) -> bool: ...


class KeyringTransactionPort(Protocol):
    def recover(self, paths: dict[str, Path], *, worker_specs: tuple[WorkerRegistrationSpec, ...]) -> None: ...

    def publish(
        self,
        paths: dict[str, Path],
        *,
        documents: dict[str, Any],
        target_names: frozenset[str],
        mode: str,
        worker_specs: tuple[WorkerRegistrationSpec, ...],
    ) -> None: ...


class LocalKeyringFileSystem:
    paths = staticmethod(_paths)
    prepare = staticmethod(_prepare_directories)
    read_identity_secrets = staticmethod(_read_identity_secrets)
    write_json = staticmethod(_atomic_write_json)

    @staticmethod
    def assert_safe(paths: dict[str, Path]) -> None:
        _assert_expected_entries(paths)
        _assert_expected_file_types(paths)

    @staticmethod
    def existing(paths: dict[str, Path], names: frozenset[str]) -> frozenset[str]:
        return frozenset(name for name in names if paths[name].exists())


class CryptographicKeyringMaterial:
    worker_specs = staticmethod(_worker_specs)
    all_documents = staticmethod(_generate_documents)
    identity_documents = staticmethod(_generate_worker_identity_documents)
    source_access_documents = staticmethod(_generate_source_access_documents)
    registration_document = staticmethod(_registration_document)


class StrictKeyringValidation:
    validate = staticmethod(_validate)


class AtomicKeyringTransaction:
    recover = staticmethod(_recover_interrupted_transaction)
    publish = staticmethod(_stage_validate_and_publish)


@dataclass(frozen=True)
class KeyringBootstrapDependencies:
    filesystem: KeyringFileSystemPort
    material: KeyringMaterialPort
    validation: KeyringValidationPort
    transaction: KeyringTransactionPort


def default_keyring_bootstrap_dependencies() -> KeyringBootstrapDependencies:
    return KeyringBootstrapDependencies(
        filesystem=LocalKeyringFileSystem(),
        material=CryptographicKeyringMaterial(),
        validation=StrictKeyringValidation(),
        transaction=AtomicKeyringTransaction(),
    )


__all__ = [
    "KeyringBootstrapDependencies",
    "KeyringFileSystemPort",
    "KeyringMaterialPort",
    "KeyringTransactionPort",
    "KeyringValidationPort",
    "default_keyring_bootstrap_dependencies",
]
