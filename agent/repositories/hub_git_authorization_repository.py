"""SQL persistence adapter for scoped Hub Git authorizations."""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable
from urllib.parse import urlsplit

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.db_models.hub_git_authorization import (
    HubGitRemoteRegistrationAuditDB,
    HubGitRemoteRegistrationDB,
    HubGitRemoteRegistrationRevisionDB,
)
from agent.services.hub_git_authorization_registry import (
    HubGitAuthorizationRegistryPort,
    RegisteredGitAuthorization,
)
from agent.sources.git_source_connector_common import GitSourceScope

_OPAQUE_IDENTIFIER = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9_.:/@+=-]|%[0-9A-Fa-f]{2}){0,511}$"
)
_MAX_OPAQUE_IDENTIFIER_LENGTH = 512
_SAFE_AUDIT_VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")
_AUTHORIZATION_STATES = frozenset({"active", "revoked", "scope_loss"})


class HubGitAuthorizationPersistenceError(RuntimeError):
    """Stable, content-free persistence failure."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = str(reason_code)
        super().__init__(self.reason_code)


class SQLHubGitAuthorizationRepository(HubGitAuthorizationRegistryPort):
    """CAS repository with an immutable revision trail and redacted audit."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session],
        clock: Callable[[], int] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock or (lambda: int(time.time()))

    def register(
        self,
        record: RegisteredGitAuthorization,
        *,
        actor_id: str,
        reason_code: str,
    ) -> int:
        actor_id, reason_code = _audit_values(actor_id, reason_code)
        _validate_persistable(record)
        registration_id = _registration_id(
            record.scope,
            record.connection_ref,
            record.repository,
        )
        now = self._clock()
        scopes_json = _scopes_json(record.granted_scopes)
        with self._session_factory() as session:
            existing = session.get(HubGitRemoteRegistrationDB, registration_id)
            if existing is not None:
                if _head_matches(existing, record, scopes_json):
                    return existing.current_revision
                raise HubGitAuthorizationPersistenceError(
                    "git_authorization_registration_conflict"
                )
            head = _head_from_record(
                registration_id=registration_id,
                record=record,
                scopes_json=scopes_json,
                now=now,
            )
            digest = _snapshot_digest(record, scopes_json)
            session.add(head)
            session.add(
                _revision_from_record(
                    registration_id=registration_id,
                    revision=1,
                    record=record,
                    scopes_json=scopes_json,
                    snapshot_digest=digest,
                    actor_id=actor_id,
                    reason_code=reason_code,
                    now=now,
                )
            )
            session.add(
                _audit_from_record(
                    registration_id=registration_id,
                    revision=1,
                    record=record,
                    previous_authorization_state=None,
                    action="registered",
                    reason_code=reason_code,
                    actor_id=actor_id,
                    now=now,
                )
            )
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                raise HubGitAuthorizationPersistenceError(
                    "git_authorization_registration_conflict"
                ) from None
        return 1

    def revise(
        self,
        record: RegisteredGitAuthorization,
        *,
        expected_revision: int,
        actor_id: str,
        reason_code: str,
        action: str = "revised",
    ) -> int:
        actor_id, reason_code = _audit_values(actor_id, reason_code)
        if not _SAFE_AUDIT_VALUE.fullmatch(str(action or "")):
            raise HubGitAuthorizationPersistenceError(
                "git_authorization_audit_value_invalid"
            )
        _validate_persistable(record)
        registration_id = _registration_id(
            record.scope,
            record.connection_ref,
            record.repository,
        )
        scopes_json = _scopes_json(record.granted_scopes)
        now = self._clock()
        with self._session_factory() as session:
            current = session.get(HubGitRemoteRegistrationDB, registration_id)
            if current is None:
                raise HubGitAuthorizationPersistenceError(
                    "git_authorization_not_found"
                )
            if current.current_revision != expected_revision:
                raise HubGitAuthorizationPersistenceError(
                    "git_authorization_revision_conflict"
                )
            next_revision = expected_revision + 1
            previous_authorization_state = current.authorization_state
            result = session.execute(
                update(HubGitRemoteRegistrationDB)
                .where(
                    HubGitRemoteRegistrationDB.registration_id
                    == registration_id,
                    HubGitRemoteRegistrationDB.current_revision
                    == expected_revision,
                    HubGitRemoteRegistrationDB.lock_version
                    == current.lock_version,
                )
                .values(
                    authorization_kind=record.authorization_kind,
                    remote_url=record.remote_url,
                    credential_ref=record.credential_ref,
                    credential_username=record.credential_username,
                    authorization_state=record.authorization_state,
                    granted_scopes_json=scopes_json,
                    current_revision=next_revision,
                    lock_version=current.lock_version + 1,
                    updated_at_epoch=now,
                )
            )
            if result.rowcount != 1:
                session.rollback()
                raise HubGitAuthorizationPersistenceError(
                    "git_authorization_revision_conflict"
                )
            digest = _snapshot_digest(record, scopes_json)
            session.add(
                _revision_from_record(
                    registration_id=registration_id,
                    revision=next_revision,
                    record=record,
                    scopes_json=scopes_json,
                    snapshot_digest=digest,
                    actor_id=actor_id,
                    reason_code=reason_code,
                    now=now,
                )
            )
            session.add(
                _audit_from_record(
                    registration_id=registration_id,
                    revision=next_revision,
                    record=record,
                    previous_authorization_state=previous_authorization_state,
                    action=action,
                    reason_code=reason_code,
                    actor_id=actor_id,
                    now=now,
                )
            )
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                raise HubGitAuthorizationPersistenceError(
                    "git_authorization_revision_conflict"
                ) from None
        return next_revision

    def transition_authorization_state(
        self,
        *,
        scope: GitSourceScope,
        connection_ref: str,
        repository: str | None,
        authorization_state: str,
        expected_revision: int,
        actor_id: str,
        reason_code: str,
        granted_scopes: frozenset[str] | None = None,
    ) -> int:
        if authorization_state not in _AUTHORIZATION_STATES:
            raise HubGitAuthorizationPersistenceError(
                "git_authorization_state_invalid"
            )
        current = self.resolve_connection(
            scope=scope,
            connection_ref=connection_ref,
            repository_identifier=repository,
        )
        if current is None:
            raise HubGitAuthorizationPersistenceError(
                "git_authorization_not_found"
            )
        revised = RegisteredGitAuthorization(
            scope=current.scope,
            connection_ref=current.connection_ref,
            authorization_kind=current.authorization_kind,
            remote_url=current.remote_url,
            credential_ref=current.credential_ref,
            credential_username=current.credential_username,
            authorization_state=authorization_state,
            granted_scopes=(
                current.granted_scopes
                if granted_scopes is None
                else granted_scopes
            ),
            repository=current.repository,
        )
        return self.revise(
            revised,
            expected_revision=expected_revision,
            actor_id=actor_id,
            reason_code=reason_code,
            action="authorization_state_changed",
        )

    def list_authorizations(
        self,
        *,
        tenant_id: str,
        project_id: str,
        owner_id: str | None,
    ) -> tuple[RegisteredGitAuthorization, ...]:
        with self._session_factory() as session:
            statement = select(HubGitRemoteRegistrationDB).where(
                HubGitRemoteRegistrationDB.tenant_id == str(tenant_id),
                HubGitRemoteRegistrationDB.project_id == str(project_id),
            )
            if owner_id is not None:
                statement = statement.where(
                    HubGitRemoteRegistrationDB.owner_id == str(owner_id)
                )
            rows = session.exec(
                statement.order_by(
                    HubGitRemoteRegistrationDB.connection_ref,
                    HubGitRemoteRegistrationDB.owner_id,
                    HubGitRemoteRegistrationDB.repository_key,
                )
            ).all()
            return tuple(_record_from_head(row) for row in rows)

    def resolve_registered_remote(
        self,
        *,
        tenant_id: str,
        project_id: str,
        owner_id: str | None,
        remote_id: str,
    ) -> RegisteredGitAuthorization | None:
        with self._session_factory() as session:
            statement = select(HubGitRemoteRegistrationDB).where(
                HubGitRemoteRegistrationDB.tenant_id == str(tenant_id),
                HubGitRemoteRegistrationDB.project_id == str(project_id),
                HubGitRemoteRegistrationDB.connection_ref
                == str(remote_id or "").strip(),
            )
            if owner_id is not None:
                statement = statement.where(
                    HubGitRemoteRegistrationDB.owner_id == str(owner_id)
                )
            rows = session.exec(statement).all()
            return _record_from_head(rows[0]) if len(rows) == 1 else None

    def resolve_connection(
        self,
        *,
        scope: GitSourceScope,
        connection_ref: str,
        repository_identifier: str | None = None,
    ) -> RegisteredGitAuthorization | None:
        repository_key = _repository_key(repository_identifier)
        with self._session_factory() as session:
            statement = select(HubGitRemoteRegistrationDB).where(
                HubGitRemoteRegistrationDB.tenant_id == str(scope.tenant_id),
                HubGitRemoteRegistrationDB.project_id == str(scope.project_id),
                HubGitRemoteRegistrationDB.owner_id == str(scope.owner_id),
                HubGitRemoteRegistrationDB.connection_ref
                == str(connection_ref or "").strip(),
                HubGitRemoteRegistrationDB.repository_key == repository_key,
            )
            head = session.exec(statement).first()
            return _record_from_head(head) if head is not None else None

    def resolve_github(
        self,
        *,
        scope: GitSourceScope,
        authorization_ref: str,
        repository: str,
    ) -> RegisteredGitAuthorization | None:
        record = self.resolve_connection(
            scope=scope,
            connection_ref=authorization_ref,
            repository_identifier=repository,
        )
        if (
            record is None
            or not record.authorization_kind.startswith("github_")
            or record.repository != str(repository or "").strip()
        ):
            return None
        return record

    def resolve_generic(
        self,
        *,
        scope: GitSourceScope,
        remote_id: str,
    ) -> RegisteredGitAuthorization | None:
        record = self.resolve_connection(
            scope=scope,
            connection_ref=remote_id,
            repository_identifier=None,
        )
        if record is None or record.authorization_kind != "generic_git":
            return None
        return record

    def current_revision(
        self,
        *,
        scope: GitSourceScope,
        connection_ref: str,
        repository: str | None,
    ) -> int | None:
        registration_id = _registration_id(scope, connection_ref, repository)
        with self._session_factory() as session:
            head = session.get(HubGitRemoteRegistrationDB, registration_id)
            return head.current_revision if head is not None else None

    def list_revisions(
        self,
        *,
        scope: GitSourceScope,
        connection_ref: str,
        repository: str | None,
    ) -> tuple[HubGitRemoteRegistrationRevisionDB, ...]:
        registration_id = _registration_id(scope, connection_ref, repository)
        with self._session_factory() as session:
            statement = (
                select(HubGitRemoteRegistrationRevisionDB)
                .where(
                    HubGitRemoteRegistrationRevisionDB.registration_id
                    == registration_id
                )
                .order_by(HubGitRemoteRegistrationRevisionDB.revision)
            )
            return tuple(session.exec(statement).all())


def _validate_persistable(record: RegisteredGitAuthorization) -> None:
    credential_ref = str(record.credential_ref or "").strip()
    if credential_ref and (
        len(credential_ref) > _MAX_OPAQUE_IDENTIFIER_LENGTH
        or _OPAQUE_IDENTIFIER.fullmatch(credential_ref) is None
    ):
        raise HubGitAuthorizationPersistenceError(
            "git_credential_reference_invalid"
        )
    parsed_remote = urlsplit(str(record.remote_url or "").strip())
    if (
        parsed_remote.password is not None
        or parsed_remote.query
        or parsed_remote.fragment
        or (
            parsed_remote.scheme.lower() in {"http", "https"}
            and parsed_remote.username is not None
        )
    ):
        raise HubGitAuthorizationPersistenceError(
            "git_remote_embedded_credential_forbidden"
        )


def _audit_values(actor_id: str, reason_code: str) -> tuple[str, str]:
    actor = str(actor_id or "").strip()
    reason = str(reason_code or "").strip()
    if (
        _SAFE_AUDIT_VALUE.fullmatch(actor) is None
        or _SAFE_AUDIT_VALUE.fullmatch(reason) is None
    ):
        raise HubGitAuthorizationPersistenceError(
            "git_authorization_audit_value_invalid"
        )
    return actor, reason


def _repository_key(repository: str | None) -> str:
    return str(repository or "").strip()


def _registration_id(
    scope: GitSourceScope,
    connection_ref: str,
    repository: str | None,
) -> str:
    material = json.dumps(
        [
            str(scope.tenant_id),
            str(scope.project_id),
            str(scope.owner_id),
            str(connection_ref or "").strip(),
            _repository_key(repository),
        ],
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _scopes_json(scopes: frozenset[str]) -> str:
    return json.dumps(
        sorted(str(scope).strip().lower() for scope in scopes),
        separators=(",", ":"),
        ensure_ascii=True,
    )


def _snapshot_digest(
    record: RegisteredGitAuthorization,
    scopes_json: str,
) -> str:
    material = json.dumps(
        {
            "authorization_kind": record.authorization_kind,
            "authorization_state": record.authorization_state,
            "connection_ref": record.connection_ref,
            "credential_ref": record.credential_ref,
            "credential_username": record.credential_username,
            "granted_scopes": json.loads(scopes_json),
            "owner_id": str(record.scope.owner_id),
            "project_id": str(record.scope.project_id),
            "remote_url": record.remote_url,
            "repository": _repository_key(record.repository),
            "tenant_id": str(record.scope.tenant_id),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _head_from_record(
    *,
    registration_id: str,
    record: RegisteredGitAuthorization,
    scopes_json: str,
    now: int,
) -> HubGitRemoteRegistrationDB:
    return HubGitRemoteRegistrationDB(
        registration_id=registration_id,
        tenant_id=str(record.scope.tenant_id),
        project_id=str(record.scope.project_id),
        owner_id=str(record.scope.owner_id),
        connection_ref=record.connection_ref,
        repository_key=_repository_key(record.repository),
        authorization_kind=record.authorization_kind,
        remote_url=record.remote_url,
        credential_ref=record.credential_ref,
        credential_username=record.credential_username,
        authorization_state=record.authorization_state,
        granted_scopes_json=scopes_json,
        current_revision=1,
        lock_version=1,
        created_at_epoch=now,
        updated_at_epoch=now,
    )


def _revision_from_record(
    *,
    registration_id: str,
    revision: int,
    record: RegisteredGitAuthorization,
    scopes_json: str,
    snapshot_digest: str,
    actor_id: str,
    reason_code: str,
    now: int,
) -> HubGitRemoteRegistrationRevisionDB:
    revision_id = hashlib.sha256(
        f"{registration_id}:{revision}".encode("ascii")
    ).hexdigest()
    return HubGitRemoteRegistrationRevisionDB(
        revision_id=revision_id,
        registration_id=registration_id,
        revision=revision,
        tenant_id=str(record.scope.tenant_id),
        project_id=str(record.scope.project_id),
        owner_id=str(record.scope.owner_id),
        connection_ref=record.connection_ref,
        repository_key=_repository_key(record.repository),
        authorization_kind=record.authorization_kind,
        remote_url=record.remote_url,
        credential_ref=record.credential_ref,
        credential_username=record.credential_username,
        authorization_state=record.authorization_state,
        granted_scopes_json=scopes_json,
        snapshot_digest=snapshot_digest,
        actor_id=actor_id,
        reason_code=reason_code,
        created_at_epoch=now,
    )


def _audit_from_record(
    *,
    registration_id: str,
    revision: int,
    record: RegisteredGitAuthorization,
    previous_authorization_state: str | None,
    action: str,
    reason_code: str,
    actor_id: str,
    now: int,
) -> HubGitRemoteRegistrationAuditDB:
    audit_id = hashlib.sha256(
        f"{registration_id}:{revision}:{action}".encode("ascii")
    ).hexdigest()
    return HubGitRemoteRegistrationAuditDB(
        audit_id=audit_id,
        registration_id=registration_id,
        tenant_id=str(record.scope.tenant_id),
        project_id=str(record.scope.project_id),
        owner_id=str(record.scope.owner_id),
        revision=revision,
        action=action,
        previous_authorization_state=previous_authorization_state,
        authorization_state=record.authorization_state,
        reason_code=reason_code,
        actor_id=actor_id,
        registration_digest=_audit_registration_digest(
            registration_id=registration_id,
            revision=revision,
            authorization_state=record.authorization_state,
        ),
        occurred_at_epoch=now,
    )


def _audit_registration_digest(
    *,
    registration_id: str,
    revision: int,
    authorization_state: str,
) -> str:
    material = (
        f"{registration_id}:{revision}:{authorization_state}"
    ).encode("ascii")
    return hashlib.sha256(material).hexdigest()


def _head_matches(
    head: HubGitRemoteRegistrationDB,
    record: RegisteredGitAuthorization,
    scopes_json: str,
) -> bool:
    return (
        head.tenant_id == str(record.scope.tenant_id)
        and head.project_id == str(record.scope.project_id)
        and head.owner_id == str(record.scope.owner_id)
        and head.connection_ref == record.connection_ref
        and head.repository_key == _repository_key(record.repository)
        and head.authorization_kind == record.authorization_kind
        and head.remote_url == record.remote_url
        and head.credential_ref == record.credential_ref
        and head.credential_username == record.credential_username
        and head.authorization_state == record.authorization_state
        and head.granted_scopes_json == scopes_json
    )


def _record_from_head(
    head: HubGitRemoteRegistrationDB,
) -> RegisteredGitAuthorization:
    return RegisteredGitAuthorization(
        scope=GitSourceScope(
            tenant_id=head.tenant_id,
            project_id=head.project_id,
            owner_id=head.owner_id,
        ),
        connection_ref=head.connection_ref,
        authorization_kind=head.authorization_kind,
        remote_url=head.remote_url,
        credential_ref=head.credential_ref,
        credential_username=head.credential_username,
        authorization_state=head.authorization_state,
        granted_scopes=frozenset(json.loads(head.granted_scopes_json)),
        repository=head.repository_key or None,
    )


__all__ = [
    "HubGitAuthorizationPersistenceError",
    "SQLHubGitAuthorizationRepository",
]
