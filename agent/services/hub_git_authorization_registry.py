"""Scoped registrations used by Hub-side Git source connector providers."""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, replace
from typing import Iterable, Protocol

from agent.sources.git_source_connector_common import GitSourceScope


_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")
_REPOSITORY = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})/"
    r"[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})$"
)
_KINDS = frozenset({"github_app", "github_oauth", "generic_git"})
_STATES = frozenset({"active", "revoked", "scope_loss"})


class HubGitAuthorizationRegistryError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = str(reason_code)
        super().__init__(self.reason_code)


@dataclass(frozen=True, repr=False)
class RegisteredGitAuthorization:
    scope: GitSourceScope
    connection_ref: str
    authorization_kind: str
    remote_url: str
    credential_ref: str | None
    credential_username: str | None
    authorization_state: str
    granted_scopes: frozenset[str]
    repository: str | None = None

    def __post_init__(self) -> None:
        connection_ref = str(self.connection_ref or "").strip()
        authorization_kind = str(self.authorization_kind or "").strip()
        state = str(self.authorization_state or "").strip().lower()
        repository = (
            str(self.repository).strip() if self.repository is not None else None
        )
        if (
            _REFERENCE.fullmatch(connection_ref) is None
            or authorization_kind not in _KINDS
            or state not in _STATES
            or not str(self.remote_url or "").strip()
            or (
                authorization_kind.startswith("github_")
                and (
                    repository is None
                    or _REPOSITORY.fullmatch(repository) is None
                )
            )
            or (
                authorization_kind == "generic_git"
                and repository is not None
            )
        ):
            raise HubGitAuthorizationRegistryError(
                "git_authorization_registration_invalid"
            )
        object.__setattr__(self, "connection_ref", connection_ref)
        object.__setattr__(self, "authorization_kind", authorization_kind)
        object.__setattr__(self, "authorization_state", state)
        object.__setattr__(self, "repository", repository)
        object.__setattr__(
            self,
            "granted_scopes",
            frozenset(
                str(item or "").strip().lower()
                for item in self.granted_scopes
                if str(item or "").strip()
            ),
        )

    def __repr__(self) -> str:
        return (
            "RegisteredGitAuthorization("
            f"connection_ref={self.connection_ref!r}, "
            f"authorization_kind={self.authorization_kind!r}, "
            f"authorization_state={self.authorization_state!r}, "
            "remote_url=<redacted>, credential_ref=<opaque>)"
        )


class HubGitAuthorizationRegistryPort(Protocol):
    def list_authorizations(
        self,
        *,
        tenant_id: str,
        project_id: str,
        owner_id: str | None,
    ) -> tuple[RegisteredGitAuthorization, ...]: ...

    def resolve_registered_remote(
        self,
        *,
        tenant_id: str,
        project_id: str,
        owner_id: str | None,
        remote_id: str,
    ) -> RegisteredGitAuthorization | None: ...

    def resolve_connection(
        self,
        *,
        scope: GitSourceScope,
        connection_ref: str,
        repository_identifier: str | None = None,
    ) -> RegisteredGitAuthorization | None: ...

    def resolve_github(
        self,
        *,
        scope: GitSourceScope,
        authorization_ref: str,
        repository: str,
    ) -> RegisteredGitAuthorization | None: ...

    def resolve_generic(
        self,
        *,
        scope: GitSourceScope,
        remote_id: str,
    ) -> RegisteredGitAuthorization | None: ...


def _scope_key(scope: GitSourceScope) -> tuple[str, str, str]:
    return (
        str(scope.tenant_id),
        str(scope.project_id),
        str(scope.owner_id),
    )


def _repository_key(repository: str | None) -> str:
    return str(repository or "").strip()


class ScopedGitAuthorizationRegistry(HubGitAuthorizationRegistryPort):
    """Thread-safe server-side registry; persistence may implement the same port."""

    def __init__(
        self,
        records: Iterable[RegisteredGitAuthorization] = (),
    ) -> None:
        self._lock = threading.RLock()
        self._records: dict[
            tuple[str, str, str, str, str],
            RegisteredGitAuthorization,
        ] = {}
        for record in records:
            self.upsert(record)

    def upsert(self, record: RegisteredGitAuthorization) -> None:
        key = (
            *_scope_key(record.scope),
            record.connection_ref,
            _repository_key(record.repository),
        )
        with self._lock:
            self._records[key] = record

    def set_authorization_state(
        self,
        *,
        scope: GitSourceScope,
        connection_ref: str,
        repository: str | None = None,
        authorization_state: str,
    ) -> None:
        key = (
            *_scope_key(scope),
            str(connection_ref or "").strip(),
            _repository_key(repository),
        )
        with self._lock:
            current = self._records.get(key)
            if current is None:
                raise HubGitAuthorizationRegistryError(
                    "git_authorization_not_found"
                )
            self._records[key] = replace(
                current,
                authorization_state=authorization_state,
            )

    def list_authorizations(
        self,
        *,
        tenant_id: str,
        project_id: str,
        owner_id: str | None,
    ) -> tuple[RegisteredGitAuthorization, ...]:
        """Return scoped server registrations without exposing secret fields."""

        with self._lock:
            return tuple(
                sorted(
                    (
                        value
                        for (tenant, project, owner, _, _), value
                        in self._records.items()
                        if tenant == tenant_id
                        and project == project_id
                        and (owner_id is None or owner == owner_id)
                    ),
                    key=lambda value: value.connection_ref,
                )
            )

    def resolve_registered_remote(
        self,
        *,
        tenant_id: str,
        project_id: str,
        owner_id: str | None,
        remote_id: str,
    ) -> RegisteredGitAuthorization | None:
        """Resolve one opaque registration without accepting a browser URL."""

        with self._lock:
            matches = tuple(
                value
                for (
                    tenant,
                    project,
                    owner,
                    connection_ref,
                    _,
                ), value in self._records.items()
                if tenant == tenant_id
                and project == project_id
                and connection_ref == str(remote_id or "").strip()
                and (owner_id is None or owner == owner_id)
            )
        return matches[0] if len(matches) == 1 else None

    def resolve_github(
        self,
        *,
        scope: GitSourceScope,
        authorization_ref: str,
        repository: str,
    ) -> RegisteredGitAuthorization | None:
        record = self._resolve(
            scope,
            authorization_ref,
            repository=repository,
        )
        if (
            record is None
            or not record.authorization_kind.startswith("github_")
            or record.repository != str(repository or "").strip()
        ):
            return None
        return record

    def resolve_connection(
        self,
        *,
        scope: GitSourceScope,
        connection_ref: str,
        repository_identifier: str | None = None,
    ) -> RegisteredGitAuthorization | None:
        return self._resolve(
            scope,
            connection_ref,
            repository=repository_identifier,
        )

    def resolve_generic(
        self,
        *,
        scope: GitSourceScope,
        remote_id: str,
    ) -> RegisteredGitAuthorization | None:
        record = self._resolve(scope, remote_id, repository=None)
        if record is None or record.authorization_kind != "generic_git":
            return None
        return record

    def _resolve(
        self,
        scope: GitSourceScope,
        connection_ref: str,
        *,
        repository: str | None,
    ) -> RegisteredGitAuthorization | None:
        key = (
            *_scope_key(scope),
            str(connection_ref or "").strip(),
            _repository_key(repository),
        )
        with self._lock:
            return self._records.get(key)


__all__ = [
    "HubGitAuthorizationRegistryError",
    "HubGitAuthorizationRegistryPort",
    "RegisteredGitAuthorization",
    "ScopedGitAuthorizationRegistry",
]
