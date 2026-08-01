from __future__ import annotations

import time
import uuid
from collections.abc import Callable

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from agent.database import engine
from agent.db_models.projects import ProjectDB, ProjectMembershipDB
from agent.db_models.teams import TeamDB
from agent.models.project_models import (
    ProjectCreateCommand,
    ProjectMembershipRead,
    ProjectMembershipUpsertCommand,
    ProjectRead,
    ProjectUpdateCommand,
)
from agent.repositories.projects import ProjectRepository
from agent.services.project_access_authority import (
    AuthorizedProjectScope,
    ProjectAccessDeniedError,
    ProjectCapability,
    ProjectNotFoundError,
)

SessionFactory = Callable[[], Session]


def _default_session_factory() -> Session:
    return Session(engine)


class ProjectLifecycleError(RuntimeError):
    def __init__(self, reason_code: str, public_status: int) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.public_status = public_status


class ProjectValidationError(ProjectLifecycleError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code, 422)


class ProjectConflictError(ProjectLifecycleError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code, 409)


class ProjectMemberNotFoundError(ProjectLifecycleError):
    def __init__(self) -> None:
        super().__init__("project_member_not_found", 404)


class ProjectVersionConflictError(ProjectConflictError):
    def __init__(self, *, expected_lock_version: int, actual_lock_version: int) -> None:
        super().__init__("project_version_conflict")
        self.expected_lock_version = expected_lock_version
        self.actual_lock_version = actual_lock_version


def _fields_set(model: object) -> set[str]:
    fields = getattr(model, "model_fields_set", None)
    if fields is None:
        fields = getattr(model, "__fields_set__", set())
    return set(fields)


def _required_text(value: str, *, reason_code: str, max_length: int) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > max_length:
        raise ProjectValidationError(reason_code)
    return normalized


def _project_read(project: ProjectDB) -> ProjectRead:
    return ProjectRead(
        id=project.project_id,
        name=project.name,
        description=project.description,
        status=project.status,
        is_active=project.status == "active",
        origin=project.origin,
        team_id=project.team_id,
        version=project.lock_version,
        created_at=project.created_at_epoch,
        updated_at=project.updated_at_epoch,
        archived_at=project.archived_at_epoch,
    )


def _membership_read(membership: ProjectMembershipDB) -> ProjectMembershipRead:
    return ProjectMembershipRead(
        subject_id=membership.subject_id,
        role=membership.role,
        state=membership.state,
        version=membership.lock_version,
        created_at=membership.created_at_epoch,
        updated_at=membership.updated_at_epoch,
    )


_OPERATION_SCOPE_CAPABILITIES: dict[ProjectCapability, frozenset[ProjectCapability]] = {
    ProjectCapability.READ: frozenset(ProjectCapability),
    ProjectCapability.MANAGE: frozenset({ProjectCapability.MANAGE}),
    ProjectCapability.MANAGE_MEMBERS: frozenset(
        {ProjectCapability.MANAGE, ProjectCapability.MANAGE_MEMBERS}
    ),
    ProjectCapability.ARCHIVE: frozenset(
        {ProjectCapability.MANAGE, ProjectCapability.ARCHIVE}
    ),
    ProjectCapability.WRITE: frozenset(
        {ProjectCapability.WRITE, ProjectCapability.MANAGE}
    ),
}


def _require_scope(scope: AuthorizedProjectScope, required: ProjectCapability) -> None:
    if scope.capability not in _OPERATION_SCOPE_CAPABILITIES[required]:
        raise ProjectAccessDeniedError(
            tenant_id=scope.tenant_id,
            project_id=scope.project_id,
        )


class ProjectLifecycleService:
    """Own project aggregate mutations and their transaction boundaries."""

    def __init__(self, *, session_factory: SessionFactory | None = None) -> None:
        self._session_factory = session_factory or _default_session_factory

    def create_project(self, command: ProjectCreateCommand) -> ProjectRead:
        tenant_id = _required_text(
            command.tenant_id,
            reason_code="invalid_project_tenant",
            max_length=191,
        )
        owner_subject_id = _required_text(
            command.owner_subject_id,
            reason_code="invalid_project_owner",
            max_length=191,
        )
        name = _required_text(
            command.name,
            reason_code="invalid_project_name",
            max_length=255,
        )
        requested_project_id = (
            _required_text(
                command.project_id,
                reason_code="invalid_project_id",
                max_length=191,
            )
            if command.project_id is not None
            else None
        )
        requested_team_id = (
            _required_text(
                command.team_id,
                reason_code="invalid_project_team",
                max_length=191,
            )
            if command.team_id is not None
            else None
        )
        if (
            requested_project_id is not None
            and requested_team_id is not None
            and requested_project_id != requested_team_id
        ):
            raise ProjectValidationError("project_team_mismatch")

        project_id = requested_team_id or requested_project_id or str(uuid.uuid4())
        description = command.description.strip() if command.description else None
        now = time.time()

        with self._session_factory() as session:
            repository = ProjectRepository(session)
            if repository.get(tenant_id, project_id) is not None:
                raise ProjectConflictError("project_already_exists")
            if repository.get_by_team_id(project_id) is not None:
                raise ProjectConflictError("project_team_already_bound")

            backing_team = session.get(TeamDB, project_id)
            if requested_team_id is not None:
                if backing_team is None:
                    raise ProjectLifecycleError("project_team_not_found", 404)
            elif backing_team is not None:
                raise ProjectConflictError("project_team_id_conflict")
            else:
                session.add(
                    TeamDB(
                        id=project_id,
                        name=name,
                        description=description,
                        is_active=True,
                        role_templates={},
                        blueprint_snapshot={},
                    )
                )
                # ProjectDB intentionally does not expose an ORM relationship to
                # TeamDB. Flush the backing team explicitly so SQLAlchemy cannot
                # order the project INSERT ahead of its required foreign key.
                session.flush()

            project = ProjectDB(
                tenant_id=tenant_id,
                project_id=project_id,
                name=name,
                description=description,
                status="active",
                origin="native",
                team_id=project_id,
                created_by_subject_id=owner_subject_id,
                lock_version=1,
                created_at_epoch=now,
                updated_at_epoch=now,
            )
            owner = ProjectMembershipDB(
                tenant_id=tenant_id,
                project_id=project_id,
                subject_id=owner_subject_id,
                role="owner",
                state="active",
                lock_version=1,
                created_at_epoch=now,
                updated_at_epoch=now,
            )
            repository.add(project)
            repository.add_membership(owner)
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise ProjectConflictError("project_create_conflict") from exc
            session.refresh(project)
            return _project_read(project)

    def get_project(self, scope: AuthorizedProjectScope) -> ProjectRead:
        _require_scope(scope, ProjectCapability.READ)
        with self._session_factory() as session:
            project = ProjectRepository(session).get(scope.tenant_id, scope.project_id)
            if project is None:
                raise ProjectNotFoundError(
                    tenant_id=scope.tenant_id,
                    project_id=scope.project_id,
                )
            return _project_read(project)

    def list_projects(
        self,
        *,
        tenant_id: str,
        subject_id: str,
        tenant_admin: bool = False,
        include_archived: bool = False,
    ) -> list[ProjectRead]:
        with self._session_factory() as session:
            projects = ProjectRepository(session).list_visible(
                tenant_id=tenant_id,
                subject_id=subject_id,
                tenant_admin=tenant_admin,
                include_archived=include_archived,
            )
            return [_project_read(project) for project in projects]

    def update_project(
        self,
        scope: AuthorizedProjectScope,
        command: ProjectUpdateCommand,
    ) -> ProjectRead:
        _require_scope(scope, ProjectCapability.MANAGE)
        fields_set = _fields_set(command)
        values: dict[str, object] = {}
        if "name" in fields_set:
            values["name"] = _required_text(
                command.name or "",
                reason_code="invalid_project_name",
                max_length=255,
            )
        if "description" in fields_set:
            values["description"] = (
                command.description.strip() if command.description else None
            )

        with self._session_factory() as session:
            repository = ProjectRepository(session)
            project = repository.get(scope.tenant_id, scope.project_id)
            if project is None:
                raise ProjectNotFoundError(
                    tenant_id=scope.tenant_id,
                    project_id=scope.project_id,
                )
            if not values:
                return _project_read(project)
            expected = command.expected_lock_version or scope.lock_version
            if project.lock_version != expected:
                raise ProjectVersionConflictError(
                    expected_lock_version=expected,
                    actual_lock_version=project.lock_version,
                )
            values.update(updated_at_epoch=time.time(), lock_version=expected + 1)
            if not repository.update_project_if_version(
                tenant_id=scope.tenant_id,
                project_id=scope.project_id,
                expected_lock_version=expected,
                values=values,
            ):
                raise ProjectVersionConflictError(
                    expected_lock_version=expected,
                    actual_lock_version=project.lock_version,
                )
            session.commit()
            session.expire_all()
            updated = repository.get(scope.tenant_id, scope.project_id)
            if updated is None:
                raise ProjectNotFoundError(
                    tenant_id=scope.tenant_id,
                    project_id=scope.project_id,
                )
            return _project_read(updated)

    def archive_project(
        self,
        scope: AuthorizedProjectScope,
        *,
        expected_lock_version: int | None = None,
    ) -> ProjectRead:
        _require_scope(scope, ProjectCapability.ARCHIVE)
        with self._session_factory() as session:
            repository = ProjectRepository(session)
            project = repository.get(scope.tenant_id, scope.project_id)
            if project is None:
                raise ProjectNotFoundError(
                    tenant_id=scope.tenant_id,
                    project_id=scope.project_id,
                )
            if project.status == "archived":
                return _project_read(project)
            expected = expected_lock_version or scope.lock_version
            if project.lock_version != expected:
                raise ProjectVersionConflictError(
                    expected_lock_version=expected,
                    actual_lock_version=project.lock_version,
                )
            now = time.time()
            if not repository.update_project_if_version(
                tenant_id=scope.tenant_id,
                project_id=scope.project_id,
                expected_lock_version=expected,
                values={
                    "status": "archived",
                    "archived_at_epoch": now,
                    "updated_at_epoch": now,
                    "lock_version": expected + 1,
                },
            ):
                raise ProjectVersionConflictError(
                    expected_lock_version=expected,
                    actual_lock_version=project.lock_version,
                )
            session.commit()
            session.expire_all()
            archived = repository.get(scope.tenant_id, scope.project_id)
            if archived is None:
                raise ProjectNotFoundError(
                    tenant_id=scope.tenant_id,
                    project_id=scope.project_id,
                )
            return _project_read(archived)

    def list_members(self, scope: AuthorizedProjectScope) -> list[ProjectMembershipRead]:
        _require_scope(scope, ProjectCapability.MANAGE_MEMBERS)
        with self._session_factory() as session:
            memberships = ProjectRepository(session).list_members(
                scope.tenant_id,
                scope.project_id,
            )
            return [_membership_read(membership) for membership in memberships]

    def upsert_member(
        self,
        scope: AuthorizedProjectScope,
        command: ProjectMembershipUpsertCommand,
    ) -> ProjectMembershipRead:
        _require_scope(scope, ProjectCapability.MANAGE_MEMBERS)
        subject_id = _required_text(
            command.subject_id,
            reason_code="invalid_project_member",
            max_length=191,
        )
        role = str(command.role)
        now = time.time()

        with self._session_factory() as session:
            repository = ProjectRepository(session)
            existing = repository.get_membership(
                scope.tenant_id,
                scope.project_id,
                subject_id,
            )
            if existing is None:
                if command.expected_lock_version is not None:
                    raise ProjectVersionConflictError(
                        expected_lock_version=command.expected_lock_version,
                        actual_lock_version=0,
                    )
                membership = ProjectMembershipDB(
                    tenant_id=scope.tenant_id,
                    project_id=scope.project_id,
                    subject_id=subject_id,
                    role=role,
                    state="active",
                    lock_version=1,
                    created_at_epoch=now,
                    updated_at_epoch=now,
                )
                repository.add_membership(membership)
                try:
                    session.commit()
                except IntegrityError as exc:
                    session.rollback()
                    raise ProjectConflictError("project_member_create_conflict") from exc
                session.refresh(membership)
                return _membership_read(membership)

            if (
                existing.role == "owner"
                and existing.state == "active"
                and role != "owner"
                and repository.count_active_owners(
                    scope.tenant_id,
                    scope.project_id,
                ) <= 1
            ):
                raise ProjectConflictError("project_requires_owner")
            expected = command.expected_lock_version or existing.lock_version
            if existing.lock_version != expected:
                raise ProjectVersionConflictError(
                    expected_lock_version=expected,
                    actual_lock_version=existing.lock_version,
                )
            if not repository.update_membership_if_version(
                tenant_id=scope.tenant_id,
                project_id=scope.project_id,
                subject_id=subject_id,
                expected_lock_version=expected,
                values={
                    "role": role,
                    "state": "active",
                    "updated_at_epoch": now,
                    "lock_version": expected + 1,
                },
            ):
                raise ProjectVersionConflictError(
                    expected_lock_version=expected,
                    actual_lock_version=existing.lock_version,
                )
            session.commit()
            session.expire_all()
            updated = repository.get_membership(
                scope.tenant_id,
                scope.project_id,
                subject_id,
            )
            if updated is None:
                raise ProjectMemberNotFoundError()
            return _membership_read(updated)

    def revoke_member(
        self,
        scope: AuthorizedProjectScope,
        *,
        subject_id: str,
        expected_lock_version: int | None = None,
    ) -> ProjectMembershipRead:
        _require_scope(scope, ProjectCapability.MANAGE_MEMBERS)
        member_id = _required_text(
            subject_id,
            reason_code="invalid_project_member",
            max_length=191,
        )
        with self._session_factory() as session:
            repository = ProjectRepository(session)
            membership = repository.get_membership(
                scope.tenant_id,
                scope.project_id,
                member_id,
            )
            if membership is None:
                raise ProjectMemberNotFoundError()
            if membership.state == "revoked":
                return _membership_read(membership)
            if (
                membership.role == "owner"
                and repository.count_active_owners(
                    scope.tenant_id,
                    scope.project_id,
                ) <= 1
            ):
                raise ProjectConflictError("project_requires_owner")
            expected = expected_lock_version or membership.lock_version
            if membership.lock_version != expected:
                raise ProjectVersionConflictError(
                    expected_lock_version=expected,
                    actual_lock_version=membership.lock_version,
                )
            if not repository.update_membership_if_version(
                tenant_id=scope.tenant_id,
                project_id=scope.project_id,
                subject_id=member_id,
                expected_lock_version=expected,
                values={
                    "state": "revoked",
                    "updated_at_epoch": time.time(),
                    "lock_version": expected + 1,
                },
            ):
                raise ProjectVersionConflictError(
                    expected_lock_version=expected,
                    actual_lock_version=membership.lock_version,
                )
            session.commit()
            session.expire_all()
            revoked = repository.get_membership(
                scope.tenant_id,
                scope.project_id,
                member_id,
            )
            if revoked is None:
                raise ProjectMemberNotFoundError()
            return _membership_read(revoked)
