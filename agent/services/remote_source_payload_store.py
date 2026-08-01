"""Immutable artifact-backed payload store for Hub-owned remote Git sources."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import PurePosixPath
import re
import time
from typing import Any, Callable, Mapping, Protocol

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.db_models.source_control import (
    RemoteSourcePayloadBindingDB,
    RemoteSourcePayloadDB,
)
from agent.services.artifact_store import ArtifactStore
from agent.services.augment.augment_secret_scanner import AugmentSecretScanner
from agent.services.hub_git_authorization_registry import (
    HubGitAuthorizationRegistryPort,
    RegisteredGitAuthorization,
)
from agent.sources.git_source_connector_common import (
    GitConnectorProviderError,
    GitContentRequest,
    GitRepositoryMaterialization,
    GitRepositoryMetrics,
    GitSourceScope,
    GitStoredPayloadQuery,
)


_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SCHEMA = "ananta.remote-source-payload.v1"
_FILENAME = "remote-source-payload.json"


@dataclass(frozen=True)
class RemoteSourcePayloadFile:
    relative_path: str
    mode: str
    content_digest: str
    byte_size: int
    content: str = ""


@dataclass(frozen=True)
class RemoteSourcePayload:
    payload_digest: str
    tenant_id: str
    project_id: str
    owner_id: str
    connector_type: str
    source_id: str
    connection_ref: str
    repository_identifier: str | None
    requested_ref: str
    commit_sha: str
    source_revision_digest: str
    manifest_digest: str
    authorization_binding_digest: str
    files: tuple[RemoteSourcePayloadFile, ...]
    metrics: GitRepositoryMetrics


class RemoteSourcePayloadStorePort(Protocol):
    def persist(
        self,
        *,
        request: GitContentRequest,
        materialization: GitRepositoryMaterialization,
        authorization_binding_digest: str,
    ) -> RemoteSourcePayload: ...

    def inventory(
        self,
        *,
        request: GitContentRequest,
        authorization_binding_digest: str,
    ) -> GitRepositoryMetrics: ...

    def resolve_stored_commit(
        self,
        *,
        query: GitStoredPayloadQuery,
        authorization_binding_digest: str,
    ) -> str: ...


def authorization_binding_digest(record: RegisteredGitAuthorization) -> str:
    return _canonical_digest(
        {
            "authorization_kind": record.authorization_kind,
            "connection_ref": record.connection_ref,
            "credential_ref_digest": _nullable_digest(record.credential_ref),
            "granted_scopes": sorted(record.granted_scopes),
            "remote_url_digest": hashlib.sha256(
                record.remote_url.encode("utf-8")
            ).hexdigest(),
            "repository": record.repository,
        }
    )


def require_active_authorization(
    *,
    registry: HubGitAuthorizationRegistryPort,
    scope: GitSourceScope,
    connection_ref: str,
    repository_identifier: str | None,
) -> tuple[RegisteredGitAuthorization, str]:
    record = registry.resolve_connection(
        scope=scope,
        connection_ref=connection_ref,
        repository_identifier=repository_identifier,
    )
    required_scope = (
        "contents:read"
        if record is not None and record.authorization_kind.startswith("github_")
        else "repository:read"
    )
    if (
        record is None
        or record.authorization_state != "active"
        or required_scope not in record.granted_scopes
    ):
        raise GitConnectorProviderError("authorization_required")
    return record, authorization_binding_digest(record)


class SQLRemoteSourcePayloadStore(RemoteSourcePayloadStorePort):
    def __init__(
        self,
        *,
        session_factory: Callable[[], Session],
        artifact_store: ArtifactStore,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._sessions = session_factory
        self._artifacts = artifact_store
        self._clock = clock
        self._secrets = AugmentSecretScanner()

    def persist(
        self,
        *,
        request: GitContentRequest,
        materialization: GitRepositoryMaterialization,
        authorization_binding_digest: str,
    ) -> RemoteSourcePayload:
        self._validate_request(request, authorization_binding_digest)
        files = self._normalize_files(materialization)
        document = {
            "authority": "hub",
            "commit_sha": request.commit_sha,
            "connection_ref": request.connection_ref,
            "connector_type": request.connector_type,
            "files": [
                {
                    "content": item.content,
                    "mode": item.mode,
                    "relative_path": item.relative_path,
                    "sha256": item.content_digest,
                    "size_bytes": item.byte_size,
                }
                for item in files
            ],
            "git_manifest_digest": materialization.metrics.manifest_digest,
            "owner_id": request.scope.owner_id,
            "project_id": request.scope.project_id,
            "repository_identifier": request.repository_identifier,
            "requested_ref": request.requested_ref,
            "schema": _SCHEMA,
            "source_id": request.source_id,
            "source_revision_digest": request.source_revision_digest,
            "tenant_id": request.scope.tenant_id,
        }
        encoded = _canonical_bytes(document)
        payload_digest = hashlib.sha256(encoded).hexdigest()
        artifact_id = f"remote-source-{payload_digest}"
        try:
            self._artifacts.store_immutable_bytes(
                artifact_id=artifact_id,
                version_number=1,
                filename=_FILENAME,
                content=encoded,
                expected_sha256=payload_digest,
                media_type="application/vnd.ananta.remote-source-payload+json",
            )
        except ValueError as exc:
            raise GitConnectorProviderError(str(exc)) from None
        metrics_json = _canonical_text(
            _metrics_mapping(materialization.metrics, elapsed_seconds=0.0)
        )
        row = RemoteSourcePayloadDB(
            payload_digest=payload_digest,
            tenant_id=request.scope.tenant_id,
            project_id=request.scope.project_id,
            owner_id=request.scope.owner_id,
            connector_type=request.connector_type,
            source_id=request.source_id,
            connection_ref=request.connection_ref,
            repository_key=request.repository_identifier or "",
            requested_ref=request.requested_ref,
            commit_sha=request.commit_sha,
            source_revision_digest=request.source_revision_digest,
            git_manifest_digest=materialization.metrics.manifest_digest,
            authorization_binding_digest=authorization_binding_digest,
            artifact_id=artifact_id,
            artifact_filename=_FILENAME,
            artifact_version=1,
            byte_size=len(encoded),
            file_count=len(files),
            metrics_json=metrics_json,
            created_at_epoch=float(self._clock()),
        )
        with self._sessions() as db:
            existing = self._coordinate_row(db, request)
            if existing is None:
                db.add(row)
                try:
                    db.commit()
                    db.refresh(row)
                except IntegrityError:
                    db.rollback()
                    existing = self._coordinate_row(db, request)
            if existing is not None and not self._same_row(existing, row):
                raise GitConnectorProviderError(
                    "remote_source_payload_identity_conflict"
                )
            selected = existing or row
        return self._load_row(selected)

    def inventory(
        self,
        *,
        request: GitContentRequest,
        authorization_binding_digest: str,
    ) -> GitRepositoryMetrics:
        self._validate_request(request, authorization_binding_digest)
        with self._sessions() as db:
            row = self._coordinate_row(db, request)
        if row is None:
            raise GitConnectorProviderError("remote_source_payload_required")
        self._require_authorization_binding(row, authorization_binding_digest)
        return self._load_row(row).metrics

    def resolve_stored_commit(
        self,
        *,
        query: GitStoredPayloadQuery,
        authorization_binding_digest: str,
    ) -> str:
        with self._sessions() as db:
            rows = db.exec(
                select(RemoteSourcePayloadDB)
                .where(
                    RemoteSourcePayloadDB.tenant_id == query.scope.tenant_id,
                    RemoteSourcePayloadDB.project_id == query.scope.project_id,
                    RemoteSourcePayloadDB.owner_id == query.scope.owner_id,
                    RemoteSourcePayloadDB.connector_type == query.connector_type,
                    RemoteSourcePayloadDB.source_id == query.source_id,
                    RemoteSourcePayloadDB.connection_ref == query.connection_ref,
                    RemoteSourcePayloadDB.repository_key
                    == (query.repository_identifier or ""),
                    RemoteSourcePayloadDB.requested_ref == query.requested_ref,
                )
                .order_by(RemoteSourcePayloadDB.created_at_epoch.desc())
            ).all()
        if not rows:
            raise GitConnectorProviderError("remote_source_payload_required")
        row = rows[0]
        self._require_authorization_binding(row, authorization_binding_digest)
        self._load_row(row)
        return row.commit_sha

    def load_for_revision(
        self,
        *,
        scope: GitSourceScope,
        connector_type: str,
        source_id: str,
        connection_ref: str,
        repository_identifier: str | None,
        commit_sha: str,
        source_revision_digest: str,
        manifest_digest: str,
        authorization_binding_digest: str,
    ) -> RemoteSourcePayload:
        with self._sessions() as db:
            row = db.exec(
                select(RemoteSourcePayloadDB).where(
                    RemoteSourcePayloadDB.tenant_id == scope.tenant_id,
                    RemoteSourcePayloadDB.project_id == scope.project_id,
                    RemoteSourcePayloadDB.owner_id == scope.owner_id,
                    RemoteSourcePayloadDB.connector_type == connector_type,
                    RemoteSourcePayloadDB.source_id == source_id,
                    RemoteSourcePayloadDB.connection_ref == connection_ref,
                    RemoteSourcePayloadDB.repository_key
                    == (repository_identifier or ""),
                    RemoteSourcePayloadDB.commit_sha == commit_sha,
                    RemoteSourcePayloadDB.source_revision_digest
                    == source_revision_digest,
                    RemoteSourcePayloadDB.git_manifest_digest == manifest_digest,
                )
            ).first()
        if row is None:
            raise GitConnectorProviderError("remote_source_payload_required")
        self._require_authorization_binding(row, authorization_binding_digest)
        return self._load_row(row)

    def bind_revision(
        self,
        *,
        payload: RemoteSourcePayload,
        connection_id: str,
        source_revision_id: str,
    ) -> None:
        row = RemoteSourcePayloadBindingDB(
            source_revision_id=source_revision_id,
            connection_id=connection_id,
            payload_digest=payload.payload_digest,
            tenant_id=payload.tenant_id,
            project_id=payload.project_id,
            source_revision_digest=payload.source_revision_digest,
            manifest_digest=payload.manifest_digest,
            bound_at_epoch=float(self._clock()),
        )
        with self._sessions() as db:
            existing = db.get(RemoteSourcePayloadBindingDB, source_revision_id)
            if existing is None:
                db.add(row)
                try:
                    db.commit()
                    return
                except IntegrityError:
                    db.rollback()
                    existing = db.get(
                        RemoteSourcePayloadBindingDB, source_revision_id
                    )
            if existing is None or any(
                getattr(existing, name) != getattr(row, name)
                for name in (
                    "connection_id",
                    "payload_digest",
                    "tenant_id",
                    "project_id",
                    "source_revision_digest",
                    "manifest_digest",
                )
            ):
                raise GitConnectorProviderError(
                    "remote_source_payload_binding_conflict"
                )

    def load_bound(
        self,
        *,
        tenant_id: str,
        project_id: str,
        connection_id: str,
        source_revision_id: str,
    ) -> RemoteSourcePayload:
        with self._sessions() as db:
            binding = db.get(RemoteSourcePayloadBindingDB, source_revision_id)
            row = (
                db.get(RemoteSourcePayloadDB, binding.payload_digest)
                if binding is not None
                else None
            )
        if (
            binding is None
            or row is None
            or binding.tenant_id != tenant_id
            or binding.project_id != project_id
            or binding.connection_id != connection_id
            or binding.source_revision_digest != row.source_revision_digest
            or binding.manifest_digest != row.git_manifest_digest
        ):
            raise GitConnectorProviderError(
                "remote_source_payload_binding_missing"
            )
        return self._load_row(row)

    def _load_row(self, row: RemoteSourcePayloadDB) -> RemoteSourcePayload:
        try:
            encoded = self._artifacts.load_immutable_bytes(
                artifact_id=row.artifact_id,
                version_number=row.artifact_version,
                filename=row.artifact_filename,
                expected_sha256=row.payload_digest,
                expected_size=row.byte_size,
            )
            document = json.loads(encoded.decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            raise GitConnectorProviderError(
                "remote_source_payload_integrity_failed"
            ) from None
        if not isinstance(document, Mapping) or _canonical_bytes(document) != encoded:
            raise GitConnectorProviderError(
                "remote_source_payload_integrity_failed"
            )
        expected = {
            "schema": _SCHEMA,
            "authority": "hub",
            "tenant_id": row.tenant_id,
            "project_id": row.project_id,
            "owner_id": row.owner_id,
            "connector_type": row.connector_type,
            "source_id": row.source_id,
            "connection_ref": row.connection_ref,
            "repository_identifier": row.repository_key or None,
            "requested_ref": row.requested_ref,
            "commit_sha": row.commit_sha,
            "source_revision_digest": row.source_revision_digest,
            "git_manifest_digest": row.git_manifest_digest,
        }
        if any(document.get(key) != value for key, value in expected.items()):
            raise GitConnectorProviderError(
                "remote_source_payload_integrity_failed"
            )
        raw_files = document.get("files")
        if not isinstance(raw_files, list) or len(raw_files) != row.file_count:
            raise GitConnectorProviderError(
                "remote_source_payload_integrity_failed"
            )
        files: list[RemoteSourcePayloadFile] = []
        for raw in raw_files:
            if not isinstance(raw, Mapping) or set(raw) != {
                "content", "mode", "relative_path", "sha256", "size_bytes"
            }:
                raise GitConnectorProviderError(
                    "remote_source_payload_integrity_failed"
                )
            content = raw["content"]
            if not isinstance(content, str):
                raise GitConnectorProviderError(
                    "remote_source_payload_integrity_failed"
                )
            content_bytes = content.encode("utf-8")
            if (
                len(content_bytes) != raw["size_bytes"]
                or hashlib.sha256(content_bytes).hexdigest() != raw["sha256"]
            ):
                raise GitConnectorProviderError(
                    "remote_source_payload_integrity_failed"
                )
            files.append(
                RemoteSourcePayloadFile(
                    relative_path=str(raw["relative_path"]),
                    mode=str(raw["mode"]),
                    content_digest=str(raw["sha256"]),
                    byte_size=int(raw["size_bytes"]),
                    content=content,
                )
            )
        try:
            metrics = GitRepositoryMetrics(**json.loads(row.metrics_json))
        except (TypeError, ValueError, json.JSONDecodeError):
            raise GitConnectorProviderError(
                "remote_source_payload_metrics_invalid"
            ) from None
        if metrics.manifest_digest != row.git_manifest_digest:
            raise GitConnectorProviderError(
                "remote_source_payload_integrity_failed"
            )
        return RemoteSourcePayload(
            payload_digest=row.payload_digest,
            tenant_id=row.tenant_id,
            project_id=row.project_id,
            owner_id=row.owner_id,
            connector_type=row.connector_type,
            source_id=row.source_id,
            connection_ref=row.connection_ref,
            repository_identifier=row.repository_key or None,
            requested_ref=row.requested_ref,
            commit_sha=row.commit_sha,
            source_revision_digest=row.source_revision_digest,
            manifest_digest=row.git_manifest_digest,
            authorization_binding_digest=row.authorization_binding_digest,
            files=tuple(files),
            metrics=metrics,
        )

    def _normalize_files(
        self, materialization: GitRepositoryMaterialization
    ) -> tuple[RemoteSourcePayloadFile, ...]:
        normalized: list[RemoteSourcePayloadFile] = []
        for item in sorted(
            materialization.files, key=lambda value: value.relative_path
        ):
            path = PurePosixPath(item.relative_path)
            if (
                path.is_absolute()
                or str(path) != item.relative_path
                or not path.parts
                or item.mode not in {"100644", "100755"}
                or any(
                    part in {"", ".", ".."} or part.casefold() == ".git"
                    for part in path.parts
                )
            ):
                raise GitConnectorProviderError(
                    "remote_source_payload_path_invalid"
                )
            try:
                text = item.content.decode("utf-8")
            except UnicodeDecodeError:
                raise GitConnectorProviderError(
                    "remote_source_payload_text_required"
                ) from None
            if (
                _CONTROL.search(text)
                or item.byte_size != len(item.content)
                or item.content_digest
                != hashlib.sha256(item.content).hexdigest()
            ):
                raise GitConnectorProviderError(
                    "remote_source_payload_file_invalid"
                )
            if not self._secrets.scan_and_redact_text(text).clean:
                raise GitConnectorProviderError(
                    "remote_source_payload_secret_forbidden"
                )
            normalized.append(
                RemoteSourcePayloadFile(
                    relative_path=item.relative_path,
                    mode=item.mode,
                    content_digest=item.content_digest,
                    byte_size=item.byte_size,
                    content=text,
                )
            )
        if len(normalized) != materialization.metrics.file_count:
            raise GitConnectorProviderError(
                "remote_source_payload_inventory_mismatch"
            )
        return tuple(normalized)

    @staticmethod
    def _validate_request(
        request: GitContentRequest, authorization_digest: str
    ) -> None:
        if (
            request.connector_type not in {"generic_git", "github_repository"}
            or not request.source_id
            or not request.connection_ref
            or not request.requested_ref
            or _COMMIT.fullmatch(request.commit_sha) is None
            or _DIGEST.fullmatch(request.source_revision_digest) is None
            or _DIGEST.fullmatch(authorization_digest) is None
        ):
            raise GitConnectorProviderError(
                "remote_source_payload_request_invalid"
            )

    @staticmethod
    def _coordinate_row(
        db: Session, request: GitContentRequest
    ) -> RemoteSourcePayloadDB | None:
        return db.exec(
            select(RemoteSourcePayloadDB).where(
                RemoteSourcePayloadDB.tenant_id == request.scope.tenant_id,
                RemoteSourcePayloadDB.project_id == request.scope.project_id,
                RemoteSourcePayloadDB.owner_id == request.scope.owner_id,
                RemoteSourcePayloadDB.connector_type == request.connector_type,
                RemoteSourcePayloadDB.source_id == request.source_id,
                RemoteSourcePayloadDB.connection_ref == request.connection_ref,
                RemoteSourcePayloadDB.repository_key
                == (request.repository_identifier or ""),
                RemoteSourcePayloadDB.requested_ref == request.requested_ref,
                RemoteSourcePayloadDB.commit_sha == request.commit_sha,
                RemoteSourcePayloadDB.source_revision_digest
                == request.source_revision_digest,
            )
        ).first()

    @staticmethod
    def _same_row(
        existing: RemoteSourcePayloadDB, candidate: RemoteSourcePayloadDB
    ) -> bool:
        return all(
            getattr(existing, name) == getattr(candidate, name)
            for name in (
                "payload_digest",
                "git_manifest_digest",
                "authorization_binding_digest",
                "artifact_id",
                "artifact_filename",
                "artifact_version",
                "byte_size",
                "file_count",
                "metrics_json",
            )
        )

    @staticmethod
    def _require_authorization_binding(
        row: RemoteSourcePayloadDB, expected: str
    ) -> None:
        if row.authorization_binding_digest != expected:
            raise GitConnectorProviderError(
                "remote_source_payload_authorization_stale"
            )


def _nullable_digest(value: str | None) -> str | None:
    return hashlib.sha256(value.encode("utf-8")).hexdigest() if value else None


def _canonical_digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return _canonical_text(value).encode("utf-8")


def _canonical_text(value: Mapping[str, Any]) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def _metrics_mapping(
    metrics: GitRepositoryMetrics, *, elapsed_seconds: float
) -> dict[str, Any]:
    return {
        "item_count": metrics.item_count,
        "object_count": metrics.object_count,
        "pack_bytes": metrics.pack_bytes,
        "file_count": metrics.file_count,
        "largest_file_bytes": metrics.largest_file_bytes,
        "total_file_bytes": metrics.total_file_bytes,
        "submodule_count": metrics.submodule_count,
        "lfs_object_count": metrics.lfs_object_count,
        "lfs_bytes": metrics.lfs_bytes,
        "elapsed_seconds": elapsed_seconds,
        "egress_bytes": metrics.egress_bytes,
        "manifest_digest": metrics.manifest_digest,
        "exclusions": tuple(dict(item) for item in metrics.exclusions),
    }


__all__ = [
    "RemoteSourcePayload",
    "RemoteSourcePayloadFile",
    "RemoteSourcePayloadStorePort",
    "SQLRemoteSourcePayloadStore",
    "authorization_binding_digest",
    "require_active_authorization",
]
