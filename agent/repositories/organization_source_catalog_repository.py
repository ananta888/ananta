"""Transactional persistence boundary for Organization Source Catalogs."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any, Protocol, Sequence

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.db_models import (
    ActiveKnowledgeIndexDB,
    KnowledgeIndexDB,
    KnowledgeIndexRunDB,
    SourceAdmissionReceiptDB,
    SourceConnectionDB,
    SourceRevisionDB,
    TaskDB,
)
from agent.db_models.source_control import (
    KnowledgeIndexRunSourceBindingDB,
    KnowledgeIndexSourceBindingDB,
)
from agent.repositories.organizations.instances import (
    SqlOrganizationAdminGrantRepository,
    SqlOrganizationInstanceRepository,
    SqlOrganizationMembershipRepository,
)
from agent.repositories.organizations.operations import (
    SqlOrganizationAuditOutboxRepository,
    SqlOrganizationOperationRepository,
)
from agent.services.codecompass_artifact_manifest import (
    CodeCompassArtifactManifestError,
    CodeCompassArtifactManifestProjector,
)
from agent.services.knowledge_index_retrieval_service import (
    KnowledgeIndexRetrievalService,
)

_READ_CHUNK_BYTES = 1024 * 1024


class OrganizationSourceCatalogPersistenceError(RuntimeError):
    def __init__(self, reason_code: str, *, public_status: int = 409) -> None:
        self.reason_code = str(reason_code)
        self.public_status = int(public_status)
        super().__init__(self.reason_code)


class OrganizationSourceCatalogUniqueRaceError(
    OrganizationSourceCatalogPersistenceError
):
    """A database uniqueness race that must be resolved by scoped replay."""

    def __init__(self) -> None:
        super().__init__(
            "organization_source_catalog_unique_race",
            public_status=409,
        )


@dataclass(frozen=True, slots=True)
class SourceCatalogPublishingAuthority:
    tenant_id: str
    project_id: str
    owner_id: str
    connection_id: str
    connector_type: str
    sensitivity: str
    source_revision_id: str
    revision_digest: str
    source_manifest_digest: str
    admission_receipt_id: str
    admission_digest: str
    knowledge_index_id: str
    index_run_id: str
    index_source_scope: str
    index_manifest_digest: str
    policy_snapshot_digest: str
    active_generation: int


class OrganizationSourceCatalogRepositoryPort(Protocol):
    def resolve_publishing_authority(
        self,
        *,
        tenant_id: str,
        project_id: str,
        connection_id: str,
        expected_knowledge_index_id: str,
        for_update: bool = False,
    ) -> SourceCatalogPublishingAuthority: ...

    def get_task_scoped(
        self,
        *,
        tenant_id: str,
        project_id: str,
        organization_id: str,
        task_id: str,
        for_update: bool = False,
    ) -> TaskDB | None: ...

    def task_id_exists(self, task_id: str) -> bool: ...
    def add_task(self, task: TaskDB) -> TaskDB: ...

    def verify_bound_records(
        self,
        *,
        authority: SourceCatalogPublishingAuthority,
        record_bindings: Sequence[Mapping[str, Any]],
    ) -> None: ...


class OrganizationSourceCatalogUnitOfWorkPort(Protocol):
    instances: Any
    memberships: Any
    admin_grants: Any
    operations: Any
    audit_outbox: Any
    catalogs: OrganizationSourceCatalogRepositoryPort

    def __enter__(self): ...
    def __exit__(self, exc_type, exc_value, traceback) -> None: ...
    def flush(self) -> None: ...


class SqlOrganizationSourceCatalogRepository:
    """Resolve one immutable source/index lineage and persist its Task."""

    def __init__(
        self,
        session: Session,
        *,
        record_reader: KnowledgeIndexRetrievalService | None = None,
        manifest_projector: CodeCompassArtifactManifestProjector | None = None,
    ) -> None:
        self._session = session
        self._records = record_reader or KnowledgeIndexRetrievalService()
        self._manifest_projector = (
            manifest_projector or CodeCompassArtifactManifestProjector()
        )

    def resolve_publishing_authority(
        self,
        *,
        tenant_id: str,
        project_id: str,
        connection_id: str,
        expected_knowledge_index_id: str,
        for_update: bool = False,
    ) -> SourceCatalogPublishingAuthority:
        connection = self._one(
            select(SourceConnectionDB).where(
                SourceConnectionDB.tenant_id == tenant_id,
                SourceConnectionDB.project_id == project_id,
                SourceConnectionDB.connection_id == connection_id,
            ),
            for_update=for_update,
        )
        if connection is None:
            raise self._not_found()
        if str(connection.state or "") != "active":
            raise self._invalid("organization_source_catalog_connection_not_active")

        active = self._one(
            select(ActiveKnowledgeIndexDB).where(
                ActiveKnowledgeIndexDB.tenant_id == tenant_id,
                ActiveKnowledgeIndexDB.project_id == project_id,
                ActiveKnowledgeIndexDB.connection_id == connection_id,
            ),
            for_update=for_update,
        )
        if active is None:
            raise self._invalid("organization_source_catalog_active_index_required")
        if str(active.knowledge_index_id or "") != str(
            expected_knowledge_index_id or ""
        ):
            raise self._invalid("organization_source_catalog_active_index_changed")

        revision = self._one(
            select(SourceRevisionDB).where(
                SourceRevisionDB.tenant_id == tenant_id,
                SourceRevisionDB.project_id == project_id,
                SourceRevisionDB.connection_id == connection_id,
                SourceRevisionDB.source_revision_id == active.source_revision_id,
            ),
            for_update=for_update,
        )
        receipt_statement = (
            select(SourceAdmissionReceiptDB)
            .where(
                SourceAdmissionReceiptDB.tenant_id == tenant_id,
                SourceAdmissionReceiptDB.project_id == project_id,
                SourceAdmissionReceiptDB.source_revision_id
                == active.source_revision_id,
                SourceAdmissionReceiptDB.decision_state == "admitted",
            )
            .order_by(SourceAdmissionReceiptDB.evaluated_at_epoch.desc())
        )
        receipt = self._one(receipt_statement, for_update=for_update)
        binding = self._one(
            select(KnowledgeIndexSourceBindingDB).where(
                KnowledgeIndexSourceBindingDB.tenant_id == tenant_id,
                KnowledgeIndexSourceBindingDB.project_id == project_id,
                KnowledgeIndexSourceBindingDB.connection_id == connection_id,
                KnowledgeIndexSourceBindingDB.knowledge_index_id
                == active.knowledge_index_id,
            ),
            for_update=for_update,
        )
        index = self._one(
            select(KnowledgeIndexDB).where(
                KnowledgeIndexDB.id == active.knowledge_index_id,
            ),
            for_update=for_update,
        )
        if revision is None or receipt is None or binding is None or index is None:
            raise self._invalid("organization_source_catalog_lineage_incomplete")
        run_binding = self._one(
            select(KnowledgeIndexRunSourceBindingDB).where(
                KnowledgeIndexRunSourceBindingDB.index_run_id
                == str(index.latest_run_id or ""),
            ),
            for_update=for_update,
        )
        run = self._one(
            select(KnowledgeIndexRunDB).where(
                KnowledgeIndexRunDB.id == str(index.latest_run_id or ""),
            ),
            for_update=for_update,
        )
        if run_binding is None or run is None:
            raise self._invalid("organization_source_catalog_index_run_incomplete")

        self._validate_lineage(
            connection=connection,
            active=active,
            revision=revision,
            receipt=receipt,
            binding=binding,
            index=index,
            run_binding=run_binding,
            run=run,
        )
        index_manifest_digest = self._verified_manifest_digest(
            index=index,
            expected_digest=str(binding.artifact_manifest_digest or ""),
        )
        return SourceCatalogPublishingAuthority(
            tenant_id=tenant_id,
            project_id=project_id,
            owner_id=str(connection.owner_id),
            connection_id=connection_id,
            connector_type=str(connection.connector_type),
            sensitivity=str(connection.sensitivity),
            source_revision_id=str(revision.source_revision_id),
            revision_digest=self._sha256(
                revision.revision_digest,
                "organization_source_catalog_revision_digest_invalid",
            ),
            source_manifest_digest=self._sha256(
                revision.content_manifest_digest,
                "organization_source_catalog_source_manifest_invalid",
            ),
            admission_receipt_id=str(receipt.receipt_id),
            admission_digest=self._sha256(
                receipt.admission_digest,
                "organization_source_catalog_admission_digest_invalid",
            ),
            knowledge_index_id=str(active.knowledge_index_id),
            index_run_id=str(index.latest_run_id),
            index_source_scope=str(index.source_scope),
            index_manifest_digest=index_manifest_digest,
            policy_snapshot_digest=self._sha256(
                active.policy_snapshot_digest,
                "organization_source_catalog_policy_digest_invalid",
            ),
            active_generation=int(active.generation),
        )

    def get_task_scoped(
        self,
        *,
        tenant_id: str,
        project_id: str,
        organization_id: str,
        task_id: str,
        for_update: bool = False,
    ) -> TaskDB | None:
        return self._one(
            select(TaskDB).where(
                TaskDB.id == task_id,
                TaskDB.tenant_id == tenant_id,
                TaskDB.project_id == project_id,
                TaskDB.organization_id == organization_id,
            ),
            for_update=for_update,
        )

    def task_id_exists(self, task_id: str) -> bool:
        return self._session.get(TaskDB, task_id) is not None

    def add_task(self, task: TaskDB) -> TaskDB:
        self._session.add(task)
        return task

    def verify_bound_records(
        self,
        *,
        authority: SourceCatalogPublishingAuthority,
        record_bindings: Sequence[Mapping[str, Any]],
    ) -> None:
        """Prove exact selected records are hydratable from the locked index.

        Source-control artifact promotion is immutable and lifecycle mutations
        coordinate through the same KnowledgeIndex row.  Holding that row lock,
        re-reading the exact selectors, and then verifying their complete files
        against the admitted public manifest closes the query-to-commit gap.
        Returned content is deliberately discarded at this boundary.
        """

        index = self._one(
            select(KnowledgeIndexDB).where(
                KnowledgeIndexDB.id == authority.knowledge_index_id,
                KnowledgeIndexDB.latest_run_id == authority.index_run_id,
                KnowledgeIndexDB.status == "completed",
            ),
            for_update=True,
        )
        if index is None:
            raise self._invalid(
                "organization_source_catalog_output_snapshot_unavailable"
            )
        bindings = [dict(item) for item in record_bindings]
        try:
            hydrated = self._records.load_bound_records(
                knowledge_index=index,
                bindings=bindings,
            )
        except ValueError as exc:
            raise self._invalid(
                "organization_source_catalog_output_record_mismatch"
            ) from exc
        if [str(row.get("source_id") or "") for row in hydrated] != [
            str(row.get("source_id") or "") for row in bindings
        ]:
            raise self._invalid(
                "organization_source_catalog_output_record_mismatch"
            )
        artifacts = self._verified_public_artifacts(index=index)
        for filename in sorted(
            {str(binding.get("record_file") or "") for binding in bindings}
        ):
            reference = artifacts.get(filename)
            if reference is None:
                raise self._invalid(
                    "organization_source_catalog_output_manifest_mismatch"
                )
            self._verify_output_file(
                index=index,
                filename=filename,
                expected_size=int(reference["size_bytes"]),
                expected_digest=str(reference["sha256"]),
            )

    def _verified_public_artifacts(
        self,
        *,
        index: KnowledgeIndexDB,
    ) -> dict[str, dict[str, Any]]:
        raw = (index.index_metadata or {}).get("artifact_manifest")
        if not isinstance(raw, Mapping):
            raise self._invalid(
                "organization_source_catalog_output_manifest_mismatch"
            )
        payload = dict(raw)
        raw_coverage = payload.get("coverage")
        raw_artifacts = payload.get("artifacts")
        raw_exclusions = payload.get("exclusions")
        if (
            not isinstance(raw_coverage, Mapping)
            or not isinstance(raw_artifacts, list)
            or any(not isinstance(item, Mapping) for item in raw_artifacts)
            or not isinstance(raw_exclusions, list)
            or any(not isinstance(item, Mapping) for item in raw_exclusions)
        ):
            raise self._invalid(
                "organization_source_catalog_output_manifest_mismatch"
            )
        coverage = {
            field: raw_coverage.get(field)
            for field in (
                "symbol_total",
                "symbol_indexed",
                "vector_total",
                "vector_indexed",
            )
        }
        try:
            projected = self._manifest_projector.project(
                knowledge_index_id=str(payload.get("knowledge_index_id") or ""),
                run_id=str(payload.get("run_id") or ""),
                source_revision_id=str(payload.get("source_revision_id") or ""),
                references=[dict(item) for item in raw_artifacts],
                coverage=coverage,
                exclusions=[dict(item) for item in raw_exclusions],
                graph_schema=payload.get("graph_schema"),
                graph_revision=payload.get("graph_revision"),
                status=str(payload.get("status") or ""),
            ).to_dict()
        except (CodeCompassArtifactManifestError, TypeError, ValueError) as exc:
            raise self._invalid(
                "organization_source_catalog_output_manifest_mismatch"
            ) from exc
        if projected != payload:
            raise self._invalid(
                "organization_source_catalog_output_manifest_mismatch"
            )
        artifacts: dict[str, dict[str, Any]] = {}
        expected_roles = {
            "index.jsonl": "index",
            "details.jsonl": "details",
            "relations.jsonl": "relations",
        }
        for raw_reference in raw_artifacts:
            reference = dict(raw_reference)
            filename = str(reference.get("filename") or "")
            if filename in artifacts or (
                filename in expected_roles
                and str(reference.get("role") or "") != expected_roles[filename]
            ):
                raise self._invalid(
                    "organization_source_catalog_output_manifest_mismatch"
                )
            artifacts[filename] = reference
        return artifacts

    @classmethod
    def _verify_output_file(
        cls,
        *,
        index: KnowledgeIndexDB,
        filename: str,
        expected_size: int,
        expected_digest: str,
    ) -> None:
        output = Path(str(index.output_dir or ""))
        candidate = output / filename
        try:
            if output.is_symlink() or not output.is_dir() or candidate.is_symlink():
                raise ValueError
            resolved_output = output.resolve(strict=True)
            resolved_candidate = candidate.resolve(strict=True)
            if (
                resolved_candidate.parent != resolved_output
                or not resolved_candidate.is_file()
                or resolved_candidate.stat().st_size != expected_size
            ):
                raise ValueError
        except (OSError, ValueError) as exc:
            raise cls._invalid(
                "organization_source_catalog_output_manifest_mismatch"
            ) from exc
        actual = cls._file_sha256(resolved_candidate)
        if actual != expected_digest:
            raise cls._invalid(
                "organization_source_catalog_output_manifest_mismatch"
            )

    @staticmethod
    def _file_sha256(path: Path) -> str:
        hasher = hashlib.sha256()
        try:
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(_READ_CHUNK_BYTES), b""):
                    hasher.update(chunk)
        except OSError as exc:
            raise SqlOrganizationSourceCatalogRepository._invalid(
                "organization_source_catalog_output_manifest_mismatch"
            ) from exc
        return hasher.hexdigest()

    def _one(self, statement, *, for_update: bool):
        if for_update and self._supports_row_lock():
            statement = statement.with_for_update()
        return self._session.exec(statement).first()

    def _supports_row_lock(self) -> bool:
        return (
            str(getattr(getattr(self._session.get_bind(), "dialect", None), "name", ""))
            == "postgresql"
        )

    @classmethod
    def _validate_lineage(
        cls,
        *,
        connection: SourceConnectionDB,
        active: ActiveKnowledgeIndexDB,
        revision: SourceRevisionDB,
        receipt: SourceAdmissionReceiptDB,
        binding: KnowledgeIndexSourceBindingDB,
        index: KnowledgeIndexDB,
        run_binding: KnowledgeIndexRunSourceBindingDB,
        run: KnowledgeIndexRunDB,
    ) -> None:
        expected_scope = (
            str(connection.tenant_id),
            str(connection.project_id),
            str(connection.owner_id),
        )
        if (
            (active.tenant_id, active.project_id, active.owner_id) != expected_scope
            or active.source_revision_id != revision.source_revision_id
            or active.policy_snapshot_digest != binding.policy_snapshot_digest
            or (revision.tenant_id, revision.project_id, revision.owner_id)
            != expected_scope
            or revision.connection_id != connection.connection_id
            or revision.connector_type != connection.connector_type
            or revision.sensitivity != connection.sensitivity
            or revision.admission_state != "admitted"
            or (receipt.tenant_id, receipt.project_id)
            != (connection.tenant_id, connection.project_id)
            or receipt.source_revision_id != revision.source_revision_id
            or receipt.revision_digest != revision.revision_digest
            or receipt.manifest_digest != revision.content_manifest_digest
            or (binding.tenant_id, binding.project_id, binding.owner_id)
            != expected_scope
            or binding.source_revision_id != revision.source_revision_id
            or binding.knowledge_index_id != active.knowledge_index_id
            or binding.status != "completed"
            or not binding.artifact_manifest_digest
            or index.id != binding.knowledge_index_id
            or index.status != "completed"
            or not index.latest_run_id
            or run_binding.index_run_id != index.latest_run_id
            or run_binding.knowledge_index_id != index.id
            or (
                run_binding.tenant_id,
                run_binding.project_id,
                run_binding.owner_id,
            )
            != expected_scope
            or run_binding.source_revision_id != revision.source_revision_id
            or run_binding.policy_snapshot_id != binding.policy_snapshot_id
            or run_binding.policy_snapshot_digest != binding.policy_snapshot_digest
            or run_binding.status != "completed"
            or run_binding.artifacts_verified is not True
            or run_binding.artifact_manifest_digest
            != binding.artifact_manifest_digest
            or run.id != index.latest_run_id
            or run.knowledge_index_id != index.id
            or run.status != "completed"
        ):
            raise cls._invalid("organization_source_catalog_lineage_invalid")

        raw_index_manifest = (index.index_metadata or {}).get("artifact_manifest")
        raw_run_manifest = (run.run_metadata or {}).get("artifact_manifest")
        index_manifest = (
            dict(raw_index_manifest)
            if isinstance(raw_index_manifest, Mapping)
            else {}
        )
        run_manifest = (
            dict(raw_run_manifest)
            if isinstance(raw_run_manifest, Mapping)
            else {}
        )
        if (
            not index_manifest
            or index_manifest != run_manifest
            or str(index_manifest.get("schema") or "")
            != "ananta.codecompass.artifact-manifest.v1"
            or str(index_manifest.get("knowledge_index_id") or "") != index.id
            or str(index_manifest.get("run_id") or "") != run.id
            or str(index_manifest.get("source_revision_id") or "")
            != revision.source_revision_id
            or str(index_manifest.get("status") or "") != "completed"
        ):
            raise cls._invalid(
                "organization_source_catalog_public_manifest_invalid"
            )
        cls._sha256(
            index_manifest.get("manifest_digest"),
            "organization_source_catalog_public_manifest_invalid",
        )

    @classmethod
    def _verified_manifest_digest(
        cls,
        *,
        index: KnowledgeIndexDB,
        expected_digest: str,
    ) -> str:
        expected = cls._sha256(
            expected_digest,
            "organization_source_catalog_index_manifest_invalid",
        )
        output = Path(str(index.output_dir or ""))
        manifest = Path(str(index.manifest_path or ""))
        try:
            if (
                output.is_symlink()
                or manifest.is_symlink()
                or not output.is_dir()
                or not manifest.is_file()
            ):
                raise ValueError
            resolved_output = output.resolve(strict=True)
            resolved_manifest = manifest.resolve(strict=True)
            resolved_manifest.relative_to(resolved_output)
        except (OSError, ValueError) as exc:
            raise cls._invalid(
                "organization_source_catalog_index_artifact_unavailable"
            ) from exc
        hasher = hashlib.sha256()
        with resolved_manifest.open("rb") as handle:
            for chunk in iter(lambda: handle.read(_READ_CHUNK_BYTES), b""):
                hasher.update(chunk)
        actual = hasher.hexdigest()
        if actual != expected:
            raise cls._invalid(
                "organization_source_catalog_index_manifest_mismatch"
            )
        return actual

    @staticmethod
    def _sha256(value: object, reason_code: str) -> str:
        normalized = str(value or "").strip().lower()
        if len(normalized) != 64 or any(
            character not in "0123456789abcdef" for character in normalized
        ):
            raise SqlOrganizationSourceCatalogRepository._invalid(reason_code)
        return normalized

    @staticmethod
    def _not_found() -> OrganizationSourceCatalogPersistenceError:
        return OrganizationSourceCatalogPersistenceError(
            "organization_source_catalog_connection_not_found",
            public_status=404,
        )

    @staticmethod
    def _invalid(reason_code: str) -> OrganizationSourceCatalogPersistenceError:
        return OrganizationSourceCatalogPersistenceError(
            reason_code,
            public_status=409,
        )


class OrganizationSourceCatalogUnitOfWork:
    """One commit for authority revalidation, catalog Task and audit receipt."""

    def __init__(self, *, session_factory: Callable[[], Session] | None = None) -> None:
        self._session_factory = session_factory or self._default_session
        self.session: Session | None = None

    @staticmethod
    def _default_session() -> Session:
        from agent.database import engine

        return Session(engine)

    def __enter__(self) -> "OrganizationSourceCatalogUnitOfWork":
        if self.session is not None:
            raise RuntimeError("organization_source_catalog_uow_already_entered")
        self.session = self._session_factory()
        self.instances = SqlOrganizationInstanceRepository(self.session)
        self.memberships = SqlOrganizationMembershipRepository(self.session)
        self.admin_grants = SqlOrganizationAdminGrantRepository(self.session)
        self.operations = SqlOrganizationOperationRepository(self.session)
        self.audit_outbox = SqlOrganizationAuditOutboxRepository(self.session)
        self.catalogs = SqlOrganizationSourceCatalogRepository(self.session)
        return self

    def flush(self) -> None:
        try:
            self._require_session().flush()
        except IntegrityError as exc:
            if self._is_unique_violation(exc):
                raise OrganizationSourceCatalogUniqueRaceError() from exc
            raise

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_value, traceback
        session = self.session
        if session is None:
            return
        try:
            if exc_type is None:
                try:
                    session.commit()
                except IntegrityError as exc:
                    session.rollback()
                    if self._is_unique_violation(exc):
                        raise OrganizationSourceCatalogUniqueRaceError() from exc
                    raise
                except BaseException:
                    session.rollback()
                    raise
            else:
                session.rollback()
        finally:
            session.close()
            self.session = None

    def _require_session(self) -> Session:
        if self.session is None:
            raise RuntimeError("organization_source_catalog_uow_not_entered")
        return self.session

    @staticmethod
    def _is_unique_violation(exc: IntegrityError) -> bool:
        original = getattr(exc, "orig", None)
        sqlstate = str(
            getattr(original, "sqlstate", None)
            or getattr(original, "pgcode", None)
            or ""
        )
        if sqlstate == "23505":
            return True
        if getattr(original, "sqlite_errorcode", None) in {1555, 2067}:
            return True
        message = str(original or exc).lower()
        return "unique constraint" in message or "duplicate key" in message


__all__ = [
    "OrganizationSourceCatalogPersistenceError",
    "OrganizationSourceCatalogRepositoryPort",
    "OrganizationSourceCatalogUniqueRaceError",
    "OrganizationSourceCatalogUnitOfWork",
    "OrganizationSourceCatalogUnitOfWorkPort",
    "SourceCatalogPublishingAuthority",
    "SqlOrganizationSourceCatalogRepository",
]
