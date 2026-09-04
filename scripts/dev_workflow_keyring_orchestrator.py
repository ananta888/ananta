"""Application service for development workflow keyring bootstrap."""

from __future__ import annotations

from pathlib import Path

from scripts.dev_workflow_keyring_contract import (
    _ALL_DOCUMENTS,
    _AUTHORIZATION_DOCUMENTS,
    _IDENTITY_DOCUMENTS,
    _LEGACY_ALL_DOCUMENTS,
    _SOURCE_ACCESS_DOCUMENTS,
    DevWorkflowKeyringBootstrapError,
    WorkerRegistrationSpec,
)
from scripts.dev_workflow_keyring_ports import (
    KeyringBootstrapDependencies,
    default_keyring_bootstrap_dependencies,
)


def bootstrap(
    root: Path,
    *,
    alpha_worker_id: str = "ananta-worker-1",
    beta_worker_id: str = "ananta-worker-2",
    dependencies: KeyringBootstrapDependencies | None = None,
) -> str:
    ports = dependencies or default_keyring_bootstrap_dependencies()
    worker_specs = ports.material.worker_specs(
        alpha_worker_id=alpha_worker_id,
        beta_worker_id=beta_worker_id,
    )
    paths = ports.filesystem.paths(root)
    ports.filesystem.prepare(paths)
    ports.transaction.recover(
        paths,
        worker_specs=worker_specs,
    )
    ports.filesystem.assert_safe(paths)
    existing = ports.filesystem.existing(paths, _ALL_DOCUMENTS)

    if existing == _ALL_DOCUMENTS:
        if _upgrade_known_worker_registration(
            paths,
            worker_specs=worker_specs,
            allow_missing_source_access=False,
            dependencies=ports,
        ):
            return "upgraded"
        return "reused"
    if existing == _LEGACY_ALL_DOCUMENTS:
        _upgrade_known_worker_registration(
            paths,
            worker_specs=worker_specs,
            allow_missing_source_access=True,
            dependencies=ports,
        )
        ports.transaction.publish(
            paths,
            documents=ports.material.source_access_documents(),
            target_names=_SOURCE_ACCESS_DOCUMENTS,
            mode="source_access_upgrade",
            worker_specs=worker_specs,
        )
        return "upgraded"
    if existing == (_AUTHORIZATION_DOCUMENTS | _SOURCE_ACCESS_DOCUMENTS):
        documents = ports.material.identity_documents(worker_specs=worker_specs)
        ports.transaction.publish(
            paths,
            documents=documents,
            target_names=_IDENTITY_DOCUMENTS,
            mode="legacy_upgrade",
            worker_specs=worker_specs,
        )
        return "upgraded"
    if existing == _AUTHORIZATION_DOCUMENTS:
        documents = {
            **ports.material.identity_documents(worker_specs=worker_specs),
            **ports.material.source_access_documents(),
        }
        ports.transaction.publish(
            paths,
            documents=documents,
            target_names=(_IDENTITY_DOCUMENTS | _SOURCE_ACCESS_DOCUMENTS),
            mode="legacy_full_upgrade",
            worker_specs=worker_specs,
        )
        return "upgraded"
    if existing:
        missing = ", ".join(sorted(_ALL_DOCUMENTS - existing))
        raise DevWorkflowKeyringBootstrapError(f"incomplete development workflow keyring set; missing: {missing}")

    documents = ports.material.all_documents(worker_specs=worker_specs)
    ports.transaction.publish(
        paths,
        documents=documents,
        target_names=_ALL_DOCUMENTS,
        mode="create",
        worker_specs=worker_specs,
    )
    return "created"


def _upgrade_known_worker_registration(
    paths: dict[str, Path],
    *,
    worker_specs: tuple[WorkerRegistrationSpec, ...],
    allow_missing_source_access: bool,
    dependencies: KeyringBootstrapDependencies,
) -> bool:
    upgrade_required = dependencies.validation.validate(
        paths,
        worker_specs=worker_specs,
        allow_legacy_registration=True,
        allow_missing_source_access=allow_missing_source_access,
    )
    if not upgrade_required:
        return False
    secrets_by_name = dependencies.filesystem.read_identity_secrets(paths)
    dependencies.filesystem.write_json(
        paths["registration_keyring"],
        dependencies.material.registration_document(
            secrets_by_name,
            worker_specs=worker_specs,
        ),
        mode=0o600,
    )
    dependencies.validation.validate(
        paths,
        worker_specs=worker_specs,
        allow_missing_source_access=allow_missing_source_access,
    )
    return True
