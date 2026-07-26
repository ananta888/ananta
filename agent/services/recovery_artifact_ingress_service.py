"""Hub-owned materialization boundary for Recovery Worker artifacts."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from agent.db_models import ArtifactDB, ArtifactVersionDB
from agent.services.recovery_task_mutation_policy import (
    recovery_task_role,
)
from ananta_contracts.recovery_artifact_ingress import (
    MAX_RECOVERY_ARTIFACT_BYTES,
    RECOVERY_ARTIFACT_RECEIPTS_SCHEMA,
    RecoveryArtifactIngressContractError,
    recovery_artifact_assignment_fingerprint,
    recovery_artifact_lease_token_digest,
    validate_recovery_artifact_ingress_manifest,
)


class RecoveryArtifactIngressError(RuntimeError):
    """Stable, fail-closed Hub artifact-ingress denial."""

    def __init__(
        self,
        reason_code: str,
        *,
        status_code: int = 409,
    ) -> None:
        self.reason_code = str(reason_code)
        self.status_code = int(status_code)
        super().__init__(self.reason_code)


class RecoveryArtifactIngressService:
    """Authenticate, validate, copy, and register Recovery artifacts.

    Worker database identifiers are retained only as untrusted provenance.
    Artifact and version identities are derived by the Hub from the exact
    lease-bound transfer, then persisted through Hub repositories.
    """

    def __init__(
        self,
        *,
        repository_provider: Callable[[], Any] | None = None,
        dispatch_gate_provider: Callable[[], Any] | None = None,
        lock_provider: Callable[[], Any] | None = None,
        workspace_service_provider: Callable[[], Any] | None = None,
        workspace_file_reader_provider: Callable[[], Any] | None = None,
        artifact_store_provider: Callable[[], Any] | None = None,
        now_provider: Callable[[], float] = time.time,
    ) -> None:
        self._repository_provider = repository_provider
        self._dispatch_gate_provider = dispatch_gate_provider
        self._lock_provider = lock_provider
        self._workspace_service_provider = workspace_service_provider
        self._workspace_file_reader_provider = (
            workspace_file_reader_provider
        )
        self._artifact_store_provider = artifact_store_provider
        self._now_provider = now_provider

    def materialize(
        self,
        *,
        task_id: str,
        manifest: object,
        lease_token: str,
        worker_id: str,
        worker_url: str,
        worker_token: str,
    ) -> dict[str, Any]:
        """Materialize one exact manifest while its dispatch lease is active."""

        normalized = self._normalize_manifest(
            manifest,
            task_id=task_id,
            executor_url=worker_url,
            lease_token=lease_token,
        )
        request_fingerprint = str(
            normalized["request_fingerprint"]
        )
        decision = self._dispatch_gate().admit_dispatch_lease(
            str(task_id or ""),
            token=lease_token,
            phase="execute",
            worker_url=worker_url,
            request_fingerprint=request_fingerprint,
            worker_token=worker_token,
        )
        if not bool(getattr(decision, "allowed", False)):
            raise RecoveryArtifactIngressError(
                str(
                    getattr(decision, "reason_code", "")
                    or "recovery_artifact_dispatch_denied"
                )
            )

        return self._materialize_normalized(
            task_id=task_id,
            normalized=normalized,
            lease_token=lease_token,
            executor_id=worker_id,
            executor_url=worker_url,
            materialization_channel="registered_worker",
        )

    def materialize_trusted_local(
        self,
        *,
        task_id: str,
        manifest: object,
        lease_token: str,
        request_fingerprint: str,
        executor_id: str,
        executor_url: str,
    ) -> dict[str, Any]:
        """Materialize a Hub-local execution without impersonating a Worker."""

        from agent.config import settings

        configured_executor_id = str(
            settings.agent_name or ""
        ).strip()
        configured_executor_url = str(
            settings.agent_url
            or f"http://localhost:{settings.port}"
        ).strip().rstrip("/")
        normalized_executor_id = str(executor_id or "").strip()
        normalized_executor_url = str(executor_url or "").strip().rstrip(
            "/"
        )
        if (
            str(settings.role or "").strip().lower() != "hub"
            or not bool(settings.hub_can_be_worker)
            or not configured_executor_id
            or not configured_executor_url
            or not hmac.compare_digest(
                normalized_executor_id,
                configured_executor_id,
            )
            or not hmac.compare_digest(
                normalized_executor_url,
                configured_executor_url,
            )
        ):
            raise RecoveryArtifactIngressError(
                "recovery_artifact_trusted_local_authority_denied",
                status_code=403,
            )
        normalized = self._normalize_manifest(
            manifest,
            task_id=task_id,
            executor_url=normalized_executor_url,
            lease_token=lease_token,
            request_fingerprint=request_fingerprint,
        )
        return self._materialize_normalized(
            task_id=task_id,
            normalized=normalized,
            lease_token=lease_token,
            executor_id=normalized_executor_id,
            executor_url=normalized_executor_url,
            materialization_channel="trusted_local",
        )

    @staticmethod
    def _normalize_manifest(
        manifest: object,
        *,
        task_id: str,
        executor_url: str,
        lease_token: str,
        request_fingerprint: str | None = None,
    ) -> dict[str, Any]:
        try:
            return validate_recovery_artifact_ingress_manifest(
                manifest,
                task_id=task_id,
                worker_url=executor_url,
                request_fingerprint=request_fingerprint,
                lease_token=lease_token,
            )
        except RecoveryArtifactIngressContractError as exc:
            raise RecoveryArtifactIngressError(
                exc.reason_code,
                status_code=400,
            ) from exc

    def _materialize_normalized(
        self,
        *,
        task_id: str,
        normalized: Mapping[str, Any],
        lease_token: str,
        executor_id: str,
        executor_url: str,
        materialization_channel: str,
    ) -> dict[str, Any]:
        repos = self._repos()
        normalized_task_id = str(task_id or "")
        task_hint = repos.task_repo.get_by_id(
            normalized_task_id
        )
        if (
            task_hint is None
            or recovery_task_role(task_hint) != "child"
        ):
            raise RecoveryArtifactIngressError(
                "recovery_artifact_task_not_authoritative",
                status_code=404,
            )
        source_task_id = str(
            _value(task_hint, "source_task_id") or ""
        ).strip()
        if not source_task_id:
            raise RecoveryArtifactIngressError(
                "recovery_artifact_source_binding_missing"
            )
        with self._locks().mutation_locks(
            {normalized_task_id, source_task_id}
        ) as acquired:
            if not acquired:
                raise RecoveryArtifactIngressError(
                    "recovery_artifact_task_lock_unavailable",
                    status_code=503,
                )
            task = repos.task_repo.get_by_id(
                normalized_task_id
            )
            locked_source_task_id = str(
                _value(task, "source_task_id") or ""
            ).strip()
            if (
                not locked_source_task_id
                or not hmac.compare_digest(
                    locked_source_task_id,
                    source_task_id,
                )
                or repos.task_repo.get_by_id(
                    locked_source_task_id
                )
                is None
            ):
                raise RecoveryArtifactIngressError(
                    "recovery_artifact_source_fence_changed"
                )
            owner_decision = self._dispatch_gate().evaluate_task(
                task,
                repos=repos,
            )
            if not bool(
                getattr(owner_decision, "allowed", False)
            ):
                raise RecoveryArtifactIngressError(
                    str(
                        getattr(
                            owner_decision,
                            "reason_code",
                            "",
                        )
                        or "recovery_artifact_owner_denied"
                    )
                )
            self._validate_authority(
                task=task,
                manifest=normalized,
                lease_token=lease_token,
                worker_url=executor_url,
            )
            task = self._bind_manifest(
                task=task,
                manifest=normalized,
                task_repository=repos.task_repo,
            )
            workspace_root = (
                self._workspace_service()
                .resolve_workspace_dir_for_read(
                    task=_task_mapping(task),
                    agent_name=executor_id,
                )
            )
            if (
                not workspace_root.exists()
                or not workspace_root.is_dir()
            ):
                raise RecoveryArtifactIngressError(
                    "recovery_artifact_workspace_missing"
                )

            # Read and validate the entire manifest before producing any Hub
            # database row.  A later persistence interruption is repaired by
            # deterministic IDs and the idempotent row checks below.
            candidates = [
                self._read_candidate(
                    workspace_root=workspace_root,
                    descriptor=descriptor,
                    manifest=normalized,
                )
                for descriptor in normalized["artifacts"]
            ]
            persisted_receipts = [
                self._materialize_candidate(
                    repos=repos,
                    task=task,
                    worker_id=executor_id,
                    worker_url=executor_url,
                    manifest=normalized,
                    candidate=candidate,
                    materialization_channel=(
                        materialization_channel
                    ),
                )
                for candidate in candidates
            ]
            receipts = [
                {
                    key: value
                    for key, value in receipt.items()
                    if key != "_replayed"
                }
                for receipt in persisted_receipts
            ]

        return {
            "schema": RECOVERY_ARTIFACT_RECEIPTS_SCHEMA,
            "task_id": str(task_id or ""),
            "manifest_digest": normalized["digest"],
            "artifacts": receipts,
            "replayed": all(
                bool(value.get("_replayed"))
                for value in persisted_receipts
            ),
        }

    @staticmethod
    def _bind_manifest(
        *,
        task: Any,
        manifest: Mapping[str, Any],
        task_repository: Any,
    ) -> Any:
        from agent.services.recovery_artifact_manifest_binding_service import (
            RecoveryArtifactManifestBindingError,
            get_recovery_artifact_manifest_binding_service,
        )

        try:
            result = (
                get_recovery_artifact_manifest_binding_service()
                .bind(
                    task=task,
                    manifest=manifest,
                    task_repository=task_repository,
                )
            )
        except RecoveryArtifactManifestBindingError as exc:
            raise RecoveryArtifactIngressError(
                exc.reason_code
            ) from exc
        return result.task

    def _repos(self) -> Any:
        if self._repository_provider is not None:
            return self._repository_provider()
        from agent.services.repository_registry import (
            get_repository_registry,
        )

        return get_repository_registry()

    def _dispatch_gate(self) -> Any:
        if self._dispatch_gate_provider is not None:
            return self._dispatch_gate_provider()
        from agent.services.recovery_dispatch_gate_service import (
            get_recovery_dispatch_gate_service,
        )

        return get_recovery_dispatch_gate_service()

    def _locks(self) -> Any:
        if self._lock_provider is not None:
            return self._lock_provider()
        from agent.services.task_mutation_lock_service import (
            get_task_mutation_lock_port,
        )

        return get_task_mutation_lock_port()

    def _workspace_service(self) -> Any:
        if self._workspace_service_provider is not None:
            return self._workspace_service_provider()
        from agent.services.worker_workspace_service import (
            get_worker_workspace_service,
        )

        return get_worker_workspace_service()

    def _artifact_store(self) -> Any:
        if self._artifact_store_provider is not None:
            return self._artifact_store_provider()
        from agent.services.artifact_store import (
            get_artifact_store,
        )

        return get_artifact_store()

    def _workspace_file_reader(self) -> Any:
        if self._workspace_file_reader_provider is not None:
            return self._workspace_file_reader_provider()
        from agent.services.recovery_workspace_file_reader import (
            get_recovery_workspace_file_reader,
        )

        return get_recovery_workspace_file_reader()

    def _validate_authority(
        self,
        *,
        task: Any,
        manifest: Mapping[str, Any],
        lease_token: str,
        worker_url: str,
    ) -> None:
        if task is None or recovery_task_role(task) != "child":
            raise RecoveryArtifactIngressError(
                "recovery_artifact_task_not_authoritative",
                status_code=404,
            )
        normalized_worker_url = str(worker_url or "").rstrip("/")
        assigned_worker_url = str(
            _value(task, "assigned_agent_url") or ""
        ).strip().rstrip("/")
        if (
            not assigned_worker_url
            or not hmac.compare_digest(
                assigned_worker_url,
                normalized_worker_url,
            )
        ):
            raise RecoveryArtifactIngressError(
                "recovery_artifact_assignment_denied",
                status_code=403,
            )
        expected_assignment = (
            recovery_artifact_assignment_fingerprint(
                task_id=str(_value(task, "id") or ""),
                worker_url=assigned_worker_url,
            )
        )
        if not hmac.compare_digest(
            str(manifest.get("assignment_fingerprint") or ""),
            expected_assignment,
        ):
            raise RecoveryArtifactIngressError(
                "recovery_artifact_assignment_mismatch"
            )
        lease = _mapping(
            _mapping(
                _value(task, "status_reason_details")
            ).get("recovery_dispatch_lease")
        )
        expected_token_digest = (
            recovery_artifact_lease_token_digest(lease_token)
        )
        valid = bool(
            lease.get("schema")
            == "ananta.recovery_dispatch_lease.v1"
            and lease.get("state") == "worker_admitted"
            and lease.get("phase") == "execute"
            and float(lease.get("expires_at") or 0.0)
            > self._now_provider()
            and hmac.compare_digest(
                str(lease.get("token_digest") or ""),
                expected_token_digest,
            )
            and hmac.compare_digest(
                str(manifest.get("lease_token_digest") or ""),
                expected_token_digest,
            )
            and hmac.compare_digest(
                str(lease.get("request_fingerprint") or ""),
                str(manifest.get("request_fingerprint") or ""),
            )
            and str(lease.get("worker_url") or "").rstrip("/")
            == assigned_worker_url
            and str(
                lease.get("admitted_worker_url") or ""
            ).rstrip("/")
            == assigned_worker_url
        )
        if not valid:
            raise RecoveryArtifactIngressError(
                "recovery_artifact_lease_binding_mismatch"
            )

    def _read_candidate(
        self,
        *,
        workspace_root: Path,
        descriptor: Mapping[str, Any],
        manifest: Mapping[str, Any],
    ) -> dict[str, Any]:
        workspace_path = str(descriptor["workspace_path"])
        from agent.services.recovery_workspace_file_reader import (
            RecoveryWorkspaceFileReadError,
        )

        try:
            snapshot = self._workspace_file_reader().read(
                workspace_root=workspace_root,
                relative_path=workspace_path,
                maximum_bytes=MAX_RECOVERY_ARTIFACT_BYTES,
            )
        except RecoveryWorkspaceFileReadError as exc:
            raise RecoveryArtifactIngressError(
                exc.reason_code
            ) from exc
        content = snapshot.content
        actual_size = len(content)
        actual_hash = hashlib.sha256(content).hexdigest()
        if actual_size != int(descriptor["size_bytes"]):
            raise RecoveryArtifactIngressError(
                "recovery_artifact_size_mismatch"
            )
        if not hmac.compare_digest(
            actual_hash,
            str(descriptor["sha256"]),
        ):
            raise RecoveryArtifactIngressError(
                "recovery_artifact_hash_mismatch"
            )
        identity_payload = {
            "schema": "ananta.recovery_artifact_identity.v1",
            "task_id": manifest["task_id"],
            "worker_url": manifest["worker_url"],
            "request_fingerprint": manifest[
                "request_fingerprint"
            ],
            "lease_token_digest": manifest[
                "lease_token_digest"
            ],
            "manifest_digest": manifest["digest"],
            "source_index": descriptor["source_index"],
            "kind": descriptor["kind"],
            "relative_path": descriptor["relative_path"],
            "sha256": actual_hash,
            "size_bytes": actual_size,
        }
        identity_digest = hashlib.sha256(
            json.dumps(
                identity_payload,
                ensure_ascii=True,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return {
            "descriptor": dict(descriptor),
            "content": content,
            "sha256": actual_hash,
            "size_bytes": actual_size,
            "identity_payload": identity_payload,
            "identity_digest": identity_digest,
            "artifact_id": (
                f"recovery-artifact-{identity_digest[:32]}"
            ),
            "version_id": (
                f"recovery-artifact-version-"
                f"{identity_digest[:32]}"
            ),
        }

    def _materialize_candidate(
        self,
        *,
        repos: Any,
        task: Any,
        worker_id: str,
        worker_url: str,
        manifest: Mapping[str, Any],
        candidate: Mapping[str, Any],
        materialization_channel: str,
    ) -> dict[str, Any]:
        descriptor = dict(candidate["descriptor"])
        artifact_id = str(candidate["artifact_id"])
        version_id = str(candidate["version_id"])
        ingress_binding = {
            **dict(candidate["identity_payload"]),
            "identity_digest": candidate["identity_digest"],
            "assignment_fingerprint": manifest[
                "assignment_fingerprint"
            ],
            "worker_id": str(worker_id or ""),
            "workspace_path": descriptor["workspace_path"],
            "worker_artifact_id": descriptor[
                "worker_artifact_id"
            ],
            "worker_artifact_version_id": descriptor[
                "worker_artifact_version_id"
            ],
        }
        if materialization_channel == "trusted_local":
            ingress_binding["materialization_channel"] = (
                "trusted_local"
            )
        existing_artifact = repos.artifact_repo.get_by_id(
            artifact_id
        )
        existing_version = repos.artifact_version_repo.get_by_id(
            version_id
        )
        replayed = bool(
            existing_artifact is not None
            and existing_version is not None
        )
        if existing_artifact is None and existing_version is not None:
            raise RecoveryArtifactIngressError(
                "recovery_artifact_replay_state_invalid"
            )
        if existing_artifact is not None:
            self._validate_existing_artifact(
                artifact=existing_artifact,
                ingress_binding=ingress_binding,
                version_id=version_id,
            )
        if existing_version is not None:
            self._validate_existing_version(
                version=existing_version,
                artifact_id=artifact_id,
                ingress_binding=ingress_binding,
                candidate=candidate,
                descriptor=descriptor,
            )

        stored = self._artifact_store().store_bytes(
            artifact_id=artifact_id,
            version_number=1,
            filename=descriptor["filename"],
            content=candidate["content"],
            media_type=descriptor["media_type"],
        )
        if (
            _integer(
                stored.get("size_bytes"),
                default=-1,
            )
            != int(candidate["size_bytes"])
            or not hmac.compare_digest(
                str(stored.get("sha256") or ""),
                str(candidate["sha256"]),
            )
        ):
            raise RecoveryArtifactIngressError(
                "recovery_artifact_storage_verification_failed",
                status_code=500,
            )
        now = self._now_provider()
        artifact = existing_artifact
        if artifact is None:
            artifact = repos.artifact_repo.save(
                ArtifactDB(
                    id=artifact_id,
                    status="stored",
                    created_by="hub_recovery_artifact_ingress",
                    artifact_metadata={
                        "ingestion_mode": (
                            "hub_recovery_artifact_ingress"
                        ),
                        "recovery_artifact_ingress": (
                            ingress_binding
                        ),
                    },
                    created_at=now,
                    updated_at=now,
                )
            )
        version = existing_version
        if version is None:
            version = repos.artifact_version_repo.save(
                ArtifactVersionDB(
                    id=version_id,
                    artifact_id=artifact_id,
                    version_number=1,
                    storage_path=str(stored["storage_path"]),
                    original_filename=descriptor["filename"],
                    media_type=descriptor["media_type"],
                    size_bytes=int(candidate["size_bytes"]),
                    sha256=str(candidate["sha256"]),
                    version_metadata={
                        "versioning_ready": True,
                        "recovery_artifact_ingress": (
                            ingress_binding
                        ),
                    },
                    created_at=now,
                )
            )
        artifact.latest_version_id = version_id
        artifact.latest_sha256 = str(candidate["sha256"])
        artifact.latest_media_type = descriptor["media_type"]
        artifact.latest_filename = descriptor["filename"]
        artifact.size_bytes = int(candidate["size_bytes"])
        artifact.status = "stored"
        artifact.updated_at = now
        repos.artifact_repo.save(artifact)
        return {
            "kind": descriptor["kind"],
            "task_id": str(_value(task, "id") or ""),
            "artifact_id": artifact_id,
            "artifact_version_id": version_id,
            "filename": descriptor["filename"],
            "media_type": descriptor["media_type"],
            "workspace_relative_path": descriptor[
                "relative_path"
            ],
            "content_hash": str(candidate["sha256"]),
            "size_bytes": int(candidate["size_bytes"]),
            "provenance_summary": {
                "schema": (
                    "ananta.recovery_artifact_provenance.v1"
                ),
                "authority": "hub",
                "ingress": "workspace",
                "worker_url": str(worker_url or ""),
                "manifest_digest": manifest["digest"],
                "source_index": descriptor["source_index"],
            },
            "_replayed": replayed,
        }

    @staticmethod
    def _validate_existing_artifact(
        *,
        artifact: Any,
        ingress_binding: Mapping[str, Any],
        version_id: str,
    ) -> None:
        metadata = _mapping(
            _value(artifact, "artifact_metadata")
        )
        existing_binding = _mapping(
            metadata.get("recovery_artifact_ingress")
        )
        latest_version_id = str(
            _value(artifact, "latest_version_id") or ""
        )
        if (
            existing_binding != dict(ingress_binding)
            or (
                latest_version_id
                and latest_version_id != version_id
            )
        ):
            raise RecoveryArtifactIngressError(
                "recovery_artifact_replay_conflict"
            )

    @staticmethod
    def _validate_existing_version(
        *,
        version: Any,
        artifact_id: str,
        ingress_binding: Mapping[str, Any],
        candidate: Mapping[str, Any],
        descriptor: Mapping[str, Any],
    ) -> None:
        metadata = _mapping(
            _value(version, "version_metadata")
        )
        valid = bool(
            str(_value(version, "artifact_id") or "")
            == artifact_id
            and _integer(
                _value(version, "version_number"),
                default=0,
            )
            == 1
            and str(_value(version, "sha256") or "")
            == str(candidate["sha256"])
            and _integer(
                _value(version, "size_bytes"),
                default=-1,
            )
            == int(candidate["size_bytes"])
            and str(_value(version, "original_filename") or "")
            == str(descriptor["filename"])
            and str(_value(version, "media_type") or "")
            == str(descriptor["media_type"])
            and _mapping(
                metadata.get("recovery_artifact_ingress")
            )
            == dict(ingress_binding)
        )
        if not valid:
            raise RecoveryArtifactIngressError(
                "recovery_artifact_replay_conflict"
            )


def _task_mapping(task: Any) -> dict[str, Any]:
    if isinstance(task, Mapping):
        return dict(task)
    model_dump = getattr(task, "model_dump", None)
    if callable(model_dump):
        return dict(model_dump())
    return dict(vars(task))


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _value(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _integer(value: Any, *, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return value


_SERVICE = RecoveryArtifactIngressService()


def get_recovery_artifact_ingress_service() -> (
    RecoveryArtifactIngressService
):
    return _SERVICE


__all__ = [
    "RecoveryArtifactIngressError",
    "RecoveryArtifactIngressService",
    "get_recovery_artifact_ingress_service",
]
