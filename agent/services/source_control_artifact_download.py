"""Authorized, lineage-bound streaming of admitted source-control artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Protocol

from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from agent.db_models.knowledge import KnowledgeIndexDB
from agent.db_models.knowledge_index_execution import (
    KnowledgeIndexExecutionBindingDB,
)
from agent.db_models.source_control import (
    ActiveKnowledgeIndexDB,
    KnowledgeIndexRunSourceBindingDB,
    KnowledgeIndexSourceBindingDB,
    SourceAccessGrantDB,
    SourceConnectionDB,
    SourceRevisionDB,
)
from agent.services.source_access_enforcement import (
    source_access_grant_digest,
)
from agent.services.source_control_observability import (
    SourceControlAuditEvent,
    SourceControlAuditOperation,
    SourceControlDecision,
    emit_source_control_audit,
)
from agent.services.source_destination_resolution import (
    DestinationSelection,
    SourceDestinationResolutionService,
)
from ananta_contracts.knowledge_index_execution import (
    KnowledgeIndexExecutionJob,
)
from ananta_contracts.source_control import (
    GrantOperation,
    GrantTransformation,
    SourceAccessGrant,
)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,254}$")
_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")
_MAX_ARTIFACT_BYTES = 128 * 1024 * 1024
_CHUNK_BYTES = 1024 * 1024


class SourceControlArtifactDownloadError(ValueError):
    def __init__(self, reason_code: str, *, status_code: int = 400) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


@dataclass
class SourceControlArtifactStream:
    body: Iterator[bytes] = field(repr=False)
    status_code: int
    media_type: str
    filename: str
    content_length: int
    sha256: str
    etag: str
    content_range: str | None
    _close: Callable[[], None] = field(repr=False)

    def close(self) -> None:
        self._close()


class SourceControlArtifactDownloadAuditPort(Protocol):
    def record(
        self,
        *,
        actor_id: str,
        tenant_id: str,
        project_id: str,
        connection_id: str,
        artifact_id: str,
        decision: str,
        reason_code: str,
        revision_digest: str | None,
        manifest_digest: str | None,
        policy_digest: str | None,
    ) -> None: ...


class ContentFreeArtifactDownloadAudit:
    def record(self, **event: object) -> None:
        reason = (
            str(event.get("reason_code") or "artifact_download_denied")
            .strip()
            .lower()
            .replace("-", "_")
        )
        if re.fullmatch(r"[a-z0-9][a-z0-9._:-]{0,63}", reason) is None:
            reason = "artifact_download_denied"
        actor_id = str(event.get("actor_id") or "")
        artifact_id = str(event.get("artifact_id") or "")
        trace = hashlib.sha256(
            f"{actor_id}\0{artifact_id}\0{reason}".encode("utf-8")
        ).hexdigest()[:24]
        emit_source_control_audit(
            SourceControlAuditEvent(
                operation=SourceControlAuditOperation.download,
                actor_id=actor_id,
                tenant_id=str(event.get("tenant_id") or ""),
                project_id=str(event.get("project_id") or ""),
                resource_kind="knowledge_index_artifact",
                resource_id=artifact_id,
                trace_id=f"download-{trace}",
                decision=(
                    SourceControlDecision.allow
                    if event.get("decision") == "allow"
                    else SourceControlDecision.deny
                ),
                reason_code=reason,
                revision_digest=_digest_or_none(
                    event.get("revision_digest")
                ),
                manifest_digest=_digest_or_none(
                    event.get("manifest_digest")
                ),
                policy_digest=_digest_or_none(
                    event.get("policy_digest")
                ),
            )
        )


@dataclass(frozen=True)
class _ArtifactLineage:
    filename: str
    media_type: str
    size_bytes: int
    sha256: str
    output_dir: Path
    revision_digest: str
    manifest_digest: str
    policy_digest: str


class SourceControlArtifactDownloadService:
    """Fail closed before exposing a bounded immutable byte snapshot."""

    def __init__(
        self,
        *,
        engine: Engine,
        artifact_root: str | Path,
        destinations: object,
        effective_access: object,
        audit: SourceControlArtifactDownloadAuditPort | None = None,
        max_artifact_bytes: int = _MAX_ARTIFACT_BYTES,
        clock=time.time,
    ) -> None:
        self._engine = engine
        self._root = Path(artifact_root).expanduser().resolve()
        self._destinations = destinations
        self._destination_resolver = SourceDestinationResolutionService(
            destinations
        )
        self._effective_access = effective_access
        self._audit = audit or ContentFreeArtifactDownloadAudit()
        self._max_bytes = max(1, min(int(max_artifact_bytes), _MAX_ARTIFACT_BYTES))
        self._clock = clock

    def open(
        self,
        *,
        principal: object,
        connection_id: str,
        artifact_id: str,
        range_header: str | None,
    ) -> SourceControlArtifactStream:
        actor_id = str(getattr(principal, "subject_id", "") or "")
        tenant_id = str(getattr(principal, "tenant_id", "") or "")
        project_id = str(getattr(principal, "project_id", "") or "")
        for value in (actor_id, tenant_id, project_id, connection_id, artifact_id):
            if _ID.fullmatch(value) is None:
                raise SourceControlArtifactDownloadError(
                    "artifact_download_scope_invalid", status_code=403
                )
        lineage: _ArtifactLineage | None = None
        spool: BinaryIO | None = None
        try:
            lineage = self._resolve_lineage(
                actor_id=actor_id,
                tenant_id=tenant_id,
                project_id=project_id,
                connection_id=connection_id,
                artifact_id=artifact_id,
            )
            target = self._contained_file(
                lineage.output_dir, lineage.filename
            )
            spool = self._verified_snapshot(
                target,
                expected_size=lineage.size_bytes,
                expected_digest=lineage.sha256,
            )
            start, end, status_code, content_range = _parse_range(
                range_header, lineage.size_bytes
            )
            length = 0 if end < start else end - start + 1
            spool.seek(start)
            body = _bounded_stream(spool, length)
            self._audit.record(
                actor_id=actor_id,
                tenant_id=tenant_id,
                project_id=project_id,
                connection_id=connection_id,
                artifact_id=artifact_id,
                decision="allow",
                reason_code="artifact_download_authorized",
                revision_digest=lineage.revision_digest,
                manifest_digest=lineage.manifest_digest,
                policy_digest=lineage.policy_digest,
            )
            return SourceControlArtifactStream(
                body=body,
                status_code=status_code,
                media_type=lineage.media_type,
                filename=lineage.filename,
                content_length=length,
                sha256=lineage.sha256,
                etag=f'"{lineage.sha256}"',
                content_range=content_range,
                _close=spool.close,
            )
        except Exception as exc:
            if spool is not None:
                spool.close()
            self._audit.record(
                actor_id=actor_id,
                tenant_id=tenant_id,
                project_id=project_id,
                connection_id=connection_id,
                artifact_id=artifact_id,
                decision="deny",
                reason_code=str(
                    getattr(exc, "reason_code", "")
                    or "artifact_download_denied"
                ),
                revision_digest=(
                    lineage.revision_digest if lineage is not None else None
                ),
                manifest_digest=(
                    lineage.manifest_digest if lineage is not None else None
                ),
                policy_digest=(
                    lineage.policy_digest if lineage is not None else None
                ),
            )
            raise

    def _resolve_lineage(
        self,
        *,
        actor_id: str,
        tenant_id: str,
        project_id: str,
        connection_id: str,
        artifact_id: str,
    ) -> _ArtifactLineage:
        now = float(self._clock())
        with Session(self._engine) as db:
            connection = db.exec(
                select(SourceConnectionDB).where(
                    SourceConnectionDB.connection_id == connection_id,
                    SourceConnectionDB.tenant_id == tenant_id,
                    SourceConnectionDB.project_id == project_id,
                )
            ).first()
            active = db.exec(
                select(ActiveKnowledgeIndexDB).where(
                    ActiveKnowledgeIndexDB.connection_id == connection_id,
                    ActiveKnowledgeIndexDB.tenant_id == tenant_id,
                    ActiveKnowledgeIndexDB.project_id == project_id,
                )
            ).first()
            if connection is None or active is None:
                raise SourceControlArtifactDownloadError(
                    "artifact_not_found", status_code=404
                )
            binding = db.get(
                KnowledgeIndexSourceBindingDB, active.knowledge_index_id
            )
            index = db.get(KnowledgeIndexDB, active.knowledge_index_id)
            if (
                binding is None
                or index is None
                or binding.connection_id != connection_id
                or binding.tenant_id != tenant_id
                or binding.project_id != project_id
                or binding.owner_id != connection.owner_id
                or binding.source_revision_id != active.source_revision_id
                or binding.policy_snapshot_digest
                != active.policy_snapshot_digest
                or binding.status != "completed"
                or index.status != "completed"
                or not index.latest_run_id
            ):
                raise SourceControlArtifactDownloadError(
                    "artifact_lineage_invalid", status_code=409
                )
            run = db.get(
                KnowledgeIndexRunSourceBindingDB, index.latest_run_id
            )
            revision = db.get(SourceRevisionDB, binding.source_revision_id)
            if (
                run is None
                or revision is None
                or run.knowledge_index_id != binding.knowledge_index_id
                or run.tenant_id != tenant_id
                or run.project_id != project_id
                or run.owner_id != binding.owner_id
                or run.source_revision_id != binding.source_revision_id
                or run.policy_snapshot_digest
                != binding.policy_snapshot_digest
                or run.status != "completed"
                or not run.artifacts_verified
                or not run.artifact_manifest_digest
                or run.artifact_manifest_digest
                != binding.artifact_manifest_digest
                or revision.connection_id != connection_id
                or revision.tenant_id != tenant_id
                or revision.project_id != project_id
                or revision.owner_id != binding.owner_id
                or revision.admission_state != "admitted"
            ):
                raise SourceControlArtifactDownloadError(
                    "artifact_lineage_invalid", status_code=409
                )
            manifest = self._public_manifest(
                index=index,
                binding=binding,
                run=run,
            )
            reference = self._artifact_reference(
                manifest, artifact_id=artifact_id
            )
            manifest_reference = self._artifact_reference(
                manifest, artifact_id="manifest"
            )
            if manifest_reference["sha256"] != run.artifact_manifest_digest:
                raise SourceControlArtifactDownloadError(
                    "artifact_manifest_digest_mismatch", status_code=409
                )
            output_dir = self._contained_output(index)
            manifest_path = self._contained_file(
                output_dir, str(manifest_reference["filename"])
            )
            self._verified_file_digest(
                manifest_path,
                expected_size=int(manifest_reference["size_bytes"]),
                expected_digest=str(manifest_reference["sha256"]),
            )
            execution, grant = self._authorized_execution(
                db=db,
                actor_id=actor_id,
                owner_id=binding.owner_id,
                tenant_id=tenant_id,
                project_id=project_id,
                revision=revision,
                binding=binding,
                now=now,
            )
            self._verify_policy(
                tenant_id=tenant_id,
                project_id=project_id,
                revision=revision,
                execution=execution,
                grant=grant,
            )
            return _ArtifactLineage(
                filename=str(reference["filename"]),
                media_type=str(reference["media_type"]),
                size_bytes=int(reference["size_bytes"]),
                sha256=str(reference["sha256"]),
                output_dir=output_dir,
                revision_digest=revision.revision_digest,
                manifest_digest=str(manifest["manifest_digest"]),
                policy_digest=binding.policy_snapshot_digest,
            )

    def _authorized_execution(
        self,
        *,
        db: Session,
        actor_id: str,
        owner_id: str,
        tenant_id: str,
        project_id: str,
        revision: SourceRevisionDB,
        binding: KnowledgeIndexSourceBindingDB,
        now: float,
    ) -> tuple[KnowledgeIndexExecutionBindingDB, SourceAccessGrantDB]:
        grants = db.exec(
            select(SourceAccessGrantDB).where(
                SourceAccessGrantDB.tenant_id == tenant_id,
                SourceAccessGrantDB.project_id == project_id,
                SourceAccessGrantDB.owner_id == owner_id,
                SourceAccessGrantDB.source_revision_id
                == revision.source_revision_id,
                SourceAccessGrantDB.policy_version
                == binding.policy_snapshot_id,
                SourceAccessGrantDB.policy_snapshot_digest
                == binding.policy_snapshot_digest,
                SourceAccessGrantDB.state == "active",
                SourceAccessGrantDB.expires_at_epoch > now,
            )
        ).all()
        matches: list[
            tuple[KnowledgeIndexExecutionBindingDB, SourceAccessGrantDB]
        ] = []
        for grant in grants:
            digest = source_access_grant_digest(_grant_contract(grant))
            executions = db.exec(
                select(KnowledgeIndexExecutionBindingDB).where(
                    KnowledgeIndexExecutionBindingDB.tenant_id
                    == tenant_id,
                    KnowledgeIndexExecutionBindingDB.project_id
                    == project_id,
                    KnowledgeIndexExecutionBindingDB.owner_id == owner_id,
                    KnowledgeIndexExecutionBindingDB.source_revision_id
                    == revision.source_revision_id,
                    KnowledgeIndexExecutionBindingDB.source_revision_digest
                    == revision.revision_digest,
                    KnowledgeIndexExecutionBindingDB.policy_snapshot_id
                    == binding.policy_snapshot_id,
                    KnowledgeIndexExecutionBindingDB.policy_snapshot_digest
                    == binding.policy_snapshot_digest,
                    KnowledgeIndexExecutionBindingDB.source_access_grant_id
                    == grant.grant_id,
                    KnowledgeIndexExecutionBindingDB.source_access_grant_digest
                    == digest,
                    KnowledgeIndexExecutionBindingDB.state == "completed",
                )
            ).all()
            for execution in executions:
                if (
                    execution.completed_at_epoch_ms is None
                    or execution.completed_at_epoch_ms
                    > execution.lease_expires_epoch_ms
                ):
                    continue
                try:
                    job = KnowledgeIndexExecutionJob.model_validate(
                        dict(execution.envelope_json or {})
                    )
                except (TypeError, ValueError):
                    continue
                if (
                    job.assignment.assignment_id
                    != execution.assignment_id
                    or job.assignment.lease_id != execution.lease_id
                    or job.authority_binding.source_access_grant_id
                    != grant.grant_id
                    or job.authority_binding.policy_snapshot_digest
                    != binding.policy_snapshot_digest
                ):
                    continue
                matches.append((execution, grant))
        if len(matches) != 1:
            raise SourceControlArtifactDownloadError(
                "artifact_access_binding_ambiguous"
                if matches
                else "artifact_access_grant_missing",
                status_code=403,
            )
        if actor_id != owner_id:
            raise SourceControlArtifactDownloadError(
                "artifact_owner_scope_mismatch", status_code=403
            )
        return matches[0]

    def _verify_policy(
        self,
        *,
        tenant_id: str,
        project_id: str,
        revision: SourceRevisionDB,
        execution: KnowledgeIndexExecutionBindingDB,
        grant: SourceAccessGrantDB,
    ) -> None:
        get = getattr(self._destinations, "get", None)
        descriptor = (
            get(
                tenant_id=tenant_id,
                project_id=project_id,
                destination_id=execution.destination_id,
            )
            if callable(get)
            else None
        )
        if descriptor is None:
            raise SourceControlArtifactDownloadError(
                "artifact_destination_unavailable", status_code=503
            )
        resolved = self._destination_resolver.resolve(
            DestinationSelection(
                worker_id=descriptor.worker_id,
                runtime_id=descriptor.runtime_id,
                provider_id=descriptor.provider_id,
                model_id=descriptor.model_id,
            )
        )
        if (
            resolved.descriptor.destination_id != execution.destination_id
            or resolved.destination_digest != execution.destination_digest
        ):
            raise SourceControlArtifactDownloadError(
                "artifact_destination_binding_mismatch", status_code=409
            )
        source = self._effective_access
        service = (
            source(tenant_id=tenant_id, project_id=project_id)
            if callable(source)
            else source
        )
        verify = getattr(service, "verify_dispatch", None)
        if not callable(verify):
            raise SourceControlArtifactDownloadError(
                "effective_source_access_unavailable", status_code=503
            )
        verify(
            tenant_id=tenant_id,
            project_id=project_id,
            source_revision_id=revision.source_revision_id,
            destination_id=execution.destination_id,
            operation=GrantOperation(grant.operation),
            transformation=GrantTransformation(grant.transformation),
            purpose=grant.purpose,
            expected_revision_digest=revision.revision_digest,
            expected_policy_digest=execution.policy_snapshot_digest,
        )

    @staticmethod
    def _public_manifest(
        *,
        index: KnowledgeIndexDB,
        binding: KnowledgeIndexSourceBindingDB,
        run: KnowledgeIndexRunSourceBindingDB,
    ) -> dict[str, object]:
        metadata = dict(index.index_metadata or {})
        raw = metadata.get("artifact_manifest")
        if not isinstance(raw, Mapping):
            raise SourceControlArtifactDownloadError(
                "artifact_manifest_missing", status_code=409
            )
        manifest = dict(raw)
        supplied_digest = str(manifest.get("manifest_digest") or "")
        canonical = dict(manifest)
        canonical.pop("manifest_digest", None)
        if (
            manifest.get("schema")
            != "ananta.codecompass.artifact-manifest.v1"
            or manifest.get("status") != "completed"
            or manifest.get("knowledge_index_id")
            != binding.knowledge_index_id
            or manifest.get("run_id") != run.index_run_id
            or manifest.get("source_revision_id")
            != binding.source_revision_id
            or supplied_digest != _digest(canonical)
        ):
            raise SourceControlArtifactDownloadError(
                "artifact_manifest_binding_mismatch", status_code=409
            )
        return manifest

    def _artifact_reference(
        self, manifest: Mapping[str, object], *, artifact_id: str
    ) -> dict[str, object]:
        values = manifest.get("artifacts")
        if not isinstance(values, list):
            raise SourceControlArtifactDownloadError(
                "artifact_manifest_invalid", status_code=409
            )
        matches = [
            dict(item)
            for item in values
            if isinstance(item, Mapping) and item.get("role") == artifact_id
        ]
        if len(matches) != 1:
            raise SourceControlArtifactDownloadError(
                "artifact_not_found", status_code=404
            )
        value = matches[0]
        filename = str(value.get("filename") or "")
        media_type = str(value.get("media_type") or "")
        size = value.get("size_bytes")
        digest = str(value.get("sha256") or "")
        path = PurePosixPath(filename)
        if (
            _FILENAME.fullmatch(filename) is None
            or path.is_absolute()
            or len(path.parts) != 1
            or not re.fullmatch(
                r"[a-z0-9.+-]+/[a-z0-9.+-]+", media_type
            )
            or isinstance(size, bool)
            or not isinstance(size, int)
            or size < 0
            or size > self._max_bytes
            or _SHA256.fullmatch(digest) is None
        ):
            raise SourceControlArtifactDownloadError(
                "artifact_reference_invalid", status_code=409
            )
        return {
            "filename": filename,
            "media_type": media_type,
            "size_bytes": size,
            "sha256": digest,
        }

    def _contained_output(self, index: KnowledgeIndexDB) -> Path:
        raw = str(index.output_dir or "")
        candidate = Path(raw)
        if (
            not candidate.is_absolute()
            or self._root.is_symlink()
            or candidate.is_symlink()
        ):
            raise SourceControlArtifactDownloadError(
                "artifact_outside_root", status_code=409
            )
        try:
            root = self._root.resolve(strict=True)
            output = candidate.resolve(strict=True)
            output.relative_to(root)
        except (OSError, ValueError) as exc:
            raise SourceControlArtifactDownloadError(
                "artifact_outside_root", status_code=409
            ) from exc
        if not output.is_dir():
            raise SourceControlArtifactDownloadError(
                "artifact_not_materialized", status_code=409
            )
        return output

    def _contained_file(self, output: Path, filename: str) -> Path:
        candidate = output / filename
        if candidate.is_symlink():
            raise SourceControlArtifactDownloadError(
                "artifact_symlink_forbidden", status_code=409
            )
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(self._root.resolve(strict=True))
        except (OSError, ValueError) as exc:
            raise SourceControlArtifactDownloadError(
                "artifact_outside_root", status_code=409
            ) from exc
        if not resolved.is_file():
            raise SourceControlArtifactDownloadError(
                "artifact_not_materialized", status_code=409
            )
        return resolved

    def _verified_file_digest(
        self, path: Path, *, expected_size: int, expected_digest: str
    ) -> None:
        snapshot = self._verified_snapshot(
            path,
            expected_size=expected_size,
            expected_digest=expected_digest,
        )
        snapshot.close()

    def _verified_snapshot(
        self, path: Path, *, expected_size: int, expected_digest: str
    ) -> BinaryIO:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = os.open(path, flags)
        except OSError as exc:
            raise SourceControlArtifactDownloadError(
                "artifact_not_materialized", status_code=409
            ) from exc
        spool = tempfile.SpooledTemporaryFile(
            max_size=_CHUNK_BYTES, mode="w+b"
        )
        try:
            with os.fdopen(descriptor, "rb", closefd=True) as source:
                before = os.fstat(source.fileno())
                if (
                    not stat.S_ISREG(before.st_mode)
                    or before.st_size != expected_size
                    or before.st_size > self._max_bytes
                ):
                    raise SourceControlArtifactDownloadError(
                        "artifact_size_mismatch", status_code=409
                    )
                digest = hashlib.sha256()
                copied = 0
                while True:
                    chunk = source.read(_CHUNK_BYTES)
                    if not chunk:
                        break
                    copied += len(chunk)
                    if copied > self._max_bytes:
                        raise SourceControlArtifactDownloadError(
                            "artifact_too_large", status_code=413
                        )
                    digest.update(chunk)
                    spool.write(chunk)
                after = os.fstat(source.fileno())
                if (
                    copied != expected_size
                    or before.st_dev != after.st_dev
                    or before.st_ino != after.st_ino
                    or before.st_size != after.st_size
                    or before.st_mtime_ns != after.st_mtime_ns
                    or digest.hexdigest() != expected_digest
                ):
                    raise SourceControlArtifactDownloadError(
                        "artifact_hash_drift", status_code=409
                    )
            spool.seek(0)
            return spool
        except Exception:
            spool.close()
            raise


def _parse_range(
    value: str | None, size: int
) -> tuple[int, int, int, str | None]:
    if value is None or not value.strip():
        return 0, size - 1, 200, None
    raw = value.strip()
    if len(raw) > 128 or "," in raw or size <= 0:
        raise SourceControlArtifactDownloadError(
            "artifact_range_not_satisfiable", status_code=416
        )
    match = _RANGE.fullmatch(raw)
    if match is None or not any(match.groups()):
        raise SourceControlArtifactDownloadError(
            "artifact_range_not_satisfiable", status_code=416
        )
    start_raw, end_raw = match.groups()
    if not start_raw:
        suffix = int(end_raw)
        if suffix <= 0:
            raise SourceControlArtifactDownloadError(
                "artifact_range_not_satisfiable", status_code=416
            )
        start = max(size - suffix, 0)
        end = size - 1
    else:
        start = int(start_raw)
        end = int(end_raw) if end_raw else size - 1
        if start >= size or end < start:
            raise SourceControlArtifactDownloadError(
                "artifact_range_not_satisfiable", status_code=416
            )
        end = min(end, size - 1)
    return start, end, 206, f"bytes {start}-{end}/{size}"


def _bounded_stream(source: BinaryIO, length: int) -> Iterator[bytes]:
    remaining = length
    while remaining > 0:
        chunk = source.read(min(_CHUNK_BYTES, remaining))
        if not chunk:
            raise SourceControlArtifactDownloadError(
                "artifact_stream_truncated", status_code=409
            )
        remaining -= len(chunk)
        yield chunk


def _grant_contract(row: SourceAccessGrantDB) -> SourceAccessGrant:
    return SourceAccessGrant(
        schema="ananta.source-control.source-access-grant.v1",
        authority="hub",
        grant_id=row.grant_id,
        version=row.grant_version,
        tenant_id=row.tenant_id,
        project_id=row.project_id,
        source_revision_id=row.source_revision_id,
        destination_id=row.destination_id,
        operation=row.operation,
        transformation=row.transformation,
        purpose=row.purpose,
        policy_version=row.policy_version,
        state=row.state,
        issued_at=datetime.fromtimestamp(
            row.issued_at_epoch, tz=timezone.utc
        ),
        expires_at=datetime.fromtimestamp(
            row.expires_at_epoch, tz=timezone.utc
        ),
    )


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()


def _digest_or_none(value: object) -> str | None:
    text = str(value or "")
    return text if _SHA256.fullmatch(text) else None


__all__ = [
    "ContentFreeArtifactDownloadAudit",
    "SourceControlArtifactDownloadError",
    "SourceControlArtifactDownloadService",
    "SourceControlArtifactStream",
]
