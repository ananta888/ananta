"""Hub-owned provisioning of scoped Git authorization registrations.

Browser clients select only a server-issued authorization handle and, for
GitHub, a repository identifier.  Provider endpoints and credential
references are resolved behind ``HubGitAuthorizationProvisioningPort`` and
never cross the public API boundary.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Callable, Literal, Mapping, Protocol

from agent.services.git_remote_policy_service import (
    GitRemoteAccessPolicyPort,
    GitRemotePolicyRequest,
)
from agent.services.hub_git_authorization_registry import (
    RegisteredGitAuthorization,
)
from agent.sources.git_source_connector_common import GitSourceScope

_OPAQUE_REFERENCE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$"
)
_REPOSITORY = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})/"
    r"[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})$"
)
_REASON_CODE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_KINDS = frozenset({"github_app", "github_oauth", "generic_git"})
_REQUIRED_SCOPES = {
    "github_app": frozenset({"contents:read"}),
    "github_oauth": frozenset({"contents:read"}),
    "generic_git": frozenset({"repository:read"}),
}
_SELECTION_FIELDS = frozenset(
    {"authorization_handle", "authorization_kind", "repository"}
)


class HubGitAuthorizationProvisioningError(ValueError):
    """Stable, content-free provisioning failure."""

    def __init__(
        self,
        reason_code: str,
        *,
        status_code: int = 400,
    ) -> None:
        self.reason_code = str(reason_code)
        self.status_code = int(status_code)
        super().__init__(self.reason_code)


@dataclass(frozen=True)
class GitAuthorizationSelection:
    """The complete and deliberately narrow browser request DTO."""

    authorization_handle: str
    authorization_kind: str
    repository: str | None

    @classmethod
    def from_mapping(
        cls, payload: Mapping[str, object]
    ) -> "GitAuthorizationSelection":
        if set(payload) != _SELECTION_FIELDS:
            raise HubGitAuthorizationProvisioningError(
                "git_authorization_selection_fields_invalid"
            )
        handle = str(payload.get("authorization_handle") or "").strip()
        kind = str(payload.get("authorization_kind") or "").strip()
        raw_repository = payload.get("repository")
        repository = (
            str(raw_repository).strip()
            if raw_repository is not None
            else None
        )
        if _OPAQUE_REFERENCE.fullmatch(handle) is None:
            raise HubGitAuthorizationProvisioningError(
                "git_authorization_handle_invalid"
            )
        if kind not in _KINDS:
            raise HubGitAuthorizationProvisioningError(
                "git_authorization_kind_invalid"
            )
        if kind.startswith("github_"):
            if repository is None or _REPOSITORY.fullmatch(repository) is None:
                raise HubGitAuthorizationProvisioningError(
                    "git_authorization_repository_invalid"
                )
        elif repository is not None:
            raise HubGitAuthorizationProvisioningError(
                "git_authorization_repository_forbidden"
            )
        return cls(
            authorization_handle=handle,
            authorization_kind=kind,
            repository=repository,
        )

    def digest_payload(self) -> Mapping[str, object]:
        return {
            "authorization_handle": self.authorization_handle,
            "authorization_kind": self.authorization_kind,
            "repository": self.repository,
        }


@dataclass(frozen=True)
class GitAuthorizationProvisioningRequest:
    scope: GitSourceScope
    selection: GitAuthorizationSelection


@dataclass(frozen=True, repr=False)
class ProvisionedGitAuthorization:
    """Secret-free provider result containing only an opaque secret reference."""

    connection_ref: str
    authorization_kind: str
    remote_url: str
    credential_ref: str
    credential_username: str | None
    authorization_state: str
    granted_scopes: frozenset[str]
    repository: str | None

    def __repr__(self) -> str:
        return (
            "ProvisionedGitAuthorization("
            f"connection_ref={self.connection_ref!r}, "
            f"authorization_kind={self.authorization_kind!r}, "
            f"authorization_state={self.authorization_state!r}, "
            "remote_url=<redacted>, credential_ref=<opaque>)"
        )


@dataclass(frozen=True)
class GitAuthorizationProviderHealth:
    status: Literal["healthy", "degraded", "unavailable"]
    reason_code: str | None = None

    def __post_init__(self) -> None:
        if self.status not in {"healthy", "degraded", "unavailable"}:
            raise HubGitAuthorizationProvisioningError(
                "git_authorization_provider_health_invalid",
                status_code=503,
            )
        if (
            self.reason_code is not None
            and _REASON_CODE.fullmatch(str(self.reason_code)) is None
        ):
            raise HubGitAuthorizationProvisioningError(
                "git_authorization_provider_health_invalid",
                status_code=503,
            )


class HubGitAuthorizationProvisioningPort(Protocol):
    """External GitHub/OAuth/remote callback integration boundary."""

    def resolve_authorization(
        self, request: GitAuthorizationProvisioningRequest
    ) -> ProvisionedGitAuthorization: ...

    def health(
        self, *, scope: GitSourceScope
    ) -> GitAuthorizationProviderHealth: ...


class HubGitAuthorizationRepositoryPort(Protocol):
    def register(
        self,
        record: RegisteredGitAuthorization,
        *,
        actor_id: str,
        reason_code: str,
    ) -> int: ...

    def list_authorizations(
        self,
        *,
        tenant_id: str,
        project_id: str,
        owner_id: str | None,
    ) -> tuple[RegisteredGitAuthorization, ...]: ...

    def resolve_connection(
        self,
        *,
        scope: GitSourceScope,
        connection_ref: str,
        repository_identifier: str | None = None,
    ) -> RegisteredGitAuthorization | None: ...

    def current_revision(
        self,
        *,
        scope: GitSourceScope,
        connection_ref: str,
        repository: str | None,
    ) -> int | None: ...

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
    ) -> int: ...


class HubGitProvisioningIdempotencyPort(Protocol):
    def claim(
        self, *, idempotency_key: str, plan_digest: str
    ) -> object: ...

    def complete(
        self,
        *,
        idempotency_key: str,
        plan_digest: str,
        claim_token: str | None,
        result: Mapping[str, object],
    ) -> None: ...


class UnavailableHubGitAuthorizationProvisioner:
    """Fail-closed default until an external provider adapter is installed."""

    def resolve_authorization(
        self, request: GitAuthorizationProvisioningRequest
    ) -> ProvisionedGitAuthorization:
        del request
        raise HubGitAuthorizationProvisioningError(
            "git_authorization_provider_unavailable",
            status_code=503,
        )

    def health(
        self, *, scope: GitSourceScope
    ) -> GitAuthorizationProviderHealth:
        del scope
        return GitAuthorizationProviderHealth(
            status="unavailable",
            reason_code="git_authorization_provider_unavailable",
        )


class UnavailableHubGitSecretResolver:
    """Fail closed without inspecting or logging an opaque reference."""

    def resolve(self, reference: str) -> str:
        del reference
        raise HubGitAuthorizationProvisioningError(
            "git_secret_resolver_unavailable",
            status_code=503,
        )


class HubGitAuthorizationProvisioningService:
    """Application service for provider resolution, policy and persistence."""

    def __init__(
        self,
        *,
        repository: HubGitAuthorizationRepositoryPort,
        provider: HubGitAuthorizationProvisioningPort,
        remote_policy: GitRemoteAccessPolicyPort,
        idempotency: HubGitProvisioningIdempotencyPort,
        connector_types: Callable[[], tuple[str, ...]],
        secret_resolver_ready: Callable[[], bool],
    ) -> None:
        self._repository = repository
        self._provider = provider
        self._remote_policy = remote_policy
        self._idempotency = idempotency
        self._connector_types = connector_types
        self._secret_resolver_ready = secret_resolver_ready

    def validate(
        self,
        *,
        principal: object,
        payload: Mapping[str, object],
    ) -> Mapping[str, object]:
        scope = self._scope(principal)
        selection = GitAuthorizationSelection.from_mapping(payload)
        record = self._resolve(scope=scope, selection=selection)
        return self._view(record, revision=0, persisted=False)

    def provision(
        self,
        *,
        principal: object,
        payload: Mapping[str, object],
        idempotency_key: str,
    ) -> Mapping[str, object]:
        scope = self._scope(principal)
        selection = GitAuthorizationSelection.from_mapping(payload)
        plan_digest = _digest(
            {
                "operation": "git_authorization_provision",
                "scope": _scope_wire(scope),
                "selection": selection.digest_payload(),
            }
        )
        completed, claim_token = self._claim(
            idempotency_key=idempotency_key,
            plan_digest=plan_digest,
        )
        if completed is not None:
            return completed
        record = self._resolve(scope=scope, selection=selection)
        revision = self._repository.register(
            record,
            actor_id=str(scope.owner_id),
            reason_code="authorization_provisioned",
        )
        result = self._view(record, revision=revision, persisted=True)
        self._idempotency.complete(
            idempotency_key=idempotency_key,
            plan_digest=plan_digest,
            claim_token=claim_token,
            result=result,
        )
        return result

    def list_authorizations(
        self,
        *,
        principal: object,
        cursor: str | None,
        limit: int,
        authorization_kind: str | None,
        authorization_state: str | None,
    ) -> Mapping[str, object]:
        scope = self._scope(principal)
        if not 1 <= int(limit) <= 200:
            raise HubGitAuthorizationProvisioningError(
                "git_authorization_limit_invalid"
            )
        after = _decode_cursor(cursor)
        rows = list(
            self._repository.list_authorizations(
                tenant_id=str(scope.tenant_id),
                project_id=str(scope.project_id),
                owner_id=str(scope.owner_id),
            )
        )
        if authorization_kind is not None:
            if authorization_kind not in _KINDS:
                raise HubGitAuthorizationProvisioningError(
                    "git_authorization_kind_invalid"
                )
            rows = [
                row
                for row in rows
                if row.authorization_kind == authorization_kind
            ]
        if authorization_state is not None:
            if authorization_state not in {"active", "revoked", "scope_loss"}:
                raise HubGitAuthorizationProvisioningError(
                    "git_authorization_state_invalid"
                )
            rows = [
                row
                for row in rows
                if row.authorization_state == authorization_state
            ]
        rows.sort(key=_record_key)
        if after is not None:
            rows = [row for row in rows if _record_key(row) > after]
        selected = rows[:limit]
        return {
            "items": [
                self._view(
                    row,
                    revision=self._revision(scope=scope, record=row),
                    persisted=True,
                )
                for row in selected
            ],
            "next_cursor": (
                _encode_cursor(_record_key(selected[-1]))
                if len(rows) > limit and selected
                else None
            ),
        }

    def detail(
        self,
        *,
        principal: object,
        authorization_ref: str,
        repository: str | None,
    ) -> Mapping[str, object]:
        scope = self._scope(principal)
        record = self._record(
            scope=scope,
            authorization_ref=authorization_ref,
            repository=repository,
        )
        return self._view(
            record,
            revision=self._revision(scope=scope, record=record),
            persisted=True,
        )

    def revoke(
        self,
        *,
        principal: object,
        authorization_ref: str,
        repository: str | None,
        expected_revision: int,
        idempotency_key: str,
    ) -> Mapping[str, object]:
        return self._transition(
            principal=principal,
            authorization_ref=authorization_ref,
            repository=repository,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            state="revoked",
            reason_code="authorization_revoked",
        )

    def record_scope_loss(
        self,
        *,
        principal: object,
        authorization_ref: str,
        repository: str | None,
        expected_revision: int,
        idempotency_key: str,
    ) -> Mapping[str, object]:
        return self._transition(
            principal=principal,
            authorization_ref=authorization_ref,
            repository=repository,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            state="scope_loss",
            reason_code="authorization_scope_loss",
        )

    def health(self, *, principal: object) -> Mapping[str, object]:
        scope = self._scope(principal)
        try:
            provider = self._provider.health(scope=scope)
        except Exception:
            provider = GitAuthorizationProviderHealth(
                status="unavailable",
                reason_code="git_authorization_provider_health_failed",
            )
        connector_types = frozenset(self._connector_types())
        connector_ready = {
            "github_repository": "github_repository" in connector_types,
            "generic_git": "generic_git" in connector_types,
        }
        rows = self._repository.list_authorizations(
            tenant_id=str(scope.tenant_id),
            project_id=str(scope.project_id),
            owner_id=str(scope.owner_id),
        )
        active = sum(
            row.authorization_state == "active" for row in rows
        )
        status = provider.status
        reason_code = provider.reason_code
        if not all(connector_ready.values()):
            status = "unavailable"
            reason_code = "git_connector_composition_unavailable"
        elif not self._secret_resolver_ready():
            status = "unavailable"
            reason_code = "git_secret_resolver_unavailable"
        elif status == "healthy" and active == 0:
            status = "degraded"
            reason_code = "git_authorization_not_configured"
        return {
            "status": status,
            "reason_code": reason_code,
            "provider_status": provider.status,
            "connector_ready": connector_ready,
            "registration_count": len(rows),
            "active_registration_count": active,
        }

    def _resolve(
        self,
        *,
        scope: GitSourceScope,
        selection: GitAuthorizationSelection,
    ) -> RegisteredGitAuthorization:
        try:
            resolved = self._provider.resolve_authorization(
                GitAuthorizationProvisioningRequest(
                    scope=scope,
                    selection=selection,
                )
            )
        except HubGitAuthorizationProvisioningError:
            raise
        except Exception:
            raise HubGitAuthorizationProvisioningError(
                "git_authorization_provider_resolution_failed",
                status_code=503,
            ) from None
        if (
            _OPAQUE_REFERENCE.fullmatch(
                str(resolved.connection_ref or "").strip()
            )
            is None
            or resolved.authorization_kind != selection.authorization_kind
            or resolved.authorization_state != "active"
            or resolved.repository != selection.repository
            or not str(resolved.credential_ref or "").strip()
            or not str(resolved.remote_url or "").strip()
        ):
            raise HubGitAuthorizationProvisioningError(
                "git_authorization_provider_result_invalid",
                status_code=503,
            )
        required_scopes = _REQUIRED_SCOPES[selection.authorization_kind]
        if not required_scopes.issubset(resolved.granted_scopes):
            raise HubGitAuthorizationProvisioningError(
                "git_authorization_required_scope_missing",
                status_code=403,
            )
        try:
            self._remote_policy.authorize(
                GitRemotePolicyRequest(
                    remote_url=resolved.remote_url,
                    operation="clone",
                    credential_ref=resolved.credential_ref,
                    allow_redirects=False,
                    proxy_url=None,
                    recurse_submodules=False,
                    lfs_mode="pointer_only",
                )
            )
        except HubGitAuthorizationProvisioningError:
            raise
        except Exception as exc:
            reason_code = getattr(
                exc, "reason_code", "git_remote_policy_rejected"
            )
            raise HubGitAuthorizationProvisioningError(
                str(reason_code),
                status_code=403,
            ) from None
        return RegisteredGitAuthorization(
            scope=scope,
            connection_ref=resolved.connection_ref,
            authorization_kind=resolved.authorization_kind,
            remote_url=resolved.remote_url,
            credential_ref=resolved.credential_ref,
            credential_username=resolved.credential_username,
            authorization_state=resolved.authorization_state,
            granted_scopes=frozenset(resolved.granted_scopes),
            repository=resolved.repository,
        )

    def _transition(
        self,
        *,
        principal: object,
        authorization_ref: str,
        repository: str | None,
        expected_revision: int,
        idempotency_key: str,
        state: Literal["revoked", "scope_loss"],
        reason_code: str,
    ) -> Mapping[str, object]:
        scope = self._scope(principal)
        normalized_ref = _require_ref(authorization_ref)
        normalized_repository = _normalize_repository(repository)
        plan_digest = _digest(
            {
                "operation": reason_code,
                "scope": _scope_wire(scope),
                "authorization_ref": normalized_ref,
                "repository": normalized_repository,
                "expected_revision": expected_revision,
            }
        )
        completed, claim_token = self._claim(
            idempotency_key=idempotency_key,
            plan_digest=plan_digest,
        )
        if completed is not None:
            return completed
        current = self._record(
            scope=scope,
            authorization_ref=normalized_ref,
            repository=normalized_repository,
        )
        current_revision = self._revision(scope=scope, record=current)
        if (
            current.authorization_state == state
            and current_revision == expected_revision + 1
        ):
            result = self._view(
                current,
                revision=current_revision,
                persisted=True,
            )
        else:
            next_revision = self._repository.transition_authorization_state(
                scope=scope,
                connection_ref=normalized_ref,
                repository=normalized_repository,
                authorization_state=state,
                expected_revision=expected_revision,
                actor_id=str(scope.owner_id),
                reason_code=reason_code,
                granted_scopes=frozenset(),
            )
            transitioned = self._record(
                scope=scope,
                authorization_ref=normalized_ref,
                repository=normalized_repository,
            )
            result = self._view(
                transitioned,
                revision=next_revision,
                persisted=True,
            )
        self._idempotency.complete(
            idempotency_key=idempotency_key,
            plan_digest=plan_digest,
            claim_token=claim_token,
            result=result,
        )
        return result

    def _record(
        self,
        *,
        scope: GitSourceScope,
        authorization_ref: str,
        repository: str | None,
    ) -> RegisteredGitAuthorization:
        normalized_ref = _require_ref(authorization_ref)
        normalized_repository = _normalize_repository(repository)
        record = self._repository.resolve_connection(
            scope=scope,
            connection_ref=normalized_ref,
            repository_identifier=normalized_repository,
        )
        if record is None:
            raise HubGitAuthorizationProvisioningError(
                "git_authorization_not_found",
                status_code=404,
            )
        return record

    def _revision(
        self,
        *,
        scope: GitSourceScope,
        record: RegisteredGitAuthorization,
    ) -> int:
        revision = self._repository.current_revision(
            scope=scope,
            connection_ref=record.connection_ref,
            repository=record.repository,
        )
        if revision is None:
            raise HubGitAuthorizationProvisioningError(
                "git_authorization_not_found",
                status_code=404,
            )
        return int(revision)

    def _claim(
        self, *, idempotency_key: str, plan_digest: str
    ) -> tuple[Mapping[str, object] | None, str | None]:
        claim = self._idempotency.claim(
            idempotency_key=idempotency_key,
            plan_digest=plan_digest,
        )
        state = str(getattr(claim, "state", "") or "")
        result = getattr(claim, "result", None)
        if state == "completed" and isinstance(result, Mapping):
            return dict(result), None
        if state == "in_progress":
            raise HubGitAuthorizationProvisioningError(
                "git_authorization_operation_in_progress",
                status_code=409,
            )
        token = getattr(claim, "claim_token", None)
        if state != "claimed" or not isinstance(token, str) or not token:
            raise HubGitAuthorizationProvisioningError(
                "git_authorization_idempotency_claim_failed",
                status_code=409,
            )
        return None, token

    @staticmethod
    def _scope(principal: object) -> GitSourceScope:
        roles = frozenset(getattr(principal, "roles", frozenset()) or ())
        if roles.isdisjoint({"admin", "project_owner"}):
            raise HubGitAuthorizationProvisioningError(
                "git_authorization_role_required",
                status_code=403,
            )
        tenant_id = str(getattr(principal, "tenant_id", "") or "").strip()
        project_id = str(
            getattr(principal, "project_id", "") or ""
        ).strip()
        owner_id = str(
            getattr(principal, "subject_id", "") or ""
        ).strip()
        if not all(
            _OPAQUE_REFERENCE.fullmatch(value)
            for value in (tenant_id, project_id, owner_id)
        ):
            raise HubGitAuthorizationProvisioningError(
                "source_control_principal_scope_required",
                status_code=403,
            )
        return GitSourceScope(
            tenant_id=tenant_id,
            project_id=project_id,
            owner_id=owner_id,
        )

    @staticmethod
    def _view(
        record: RegisteredGitAuthorization,
        *,
        revision: int,
        persisted: bool,
    ) -> Mapping[str, object]:
        return {
            "authorization_ref": record.connection_ref,
            "authorization_kind": record.authorization_kind,
            "repository": record.repository,
            "authorization_state": record.authorization_state,
            "granted_scopes": sorted(record.granted_scopes),
            "credential_configured": bool(record.credential_ref),
            "persisted": bool(persisted),
            "current_revision": int(revision),
            "etag": (
                f'"git-auth-v1:{int(revision)}"' if persisted else None
            ),
            "next_actions": (
                ["revoke", "record_scope_loss"]
                if record.authorization_state == "active"
                else []
            ),
        }


def _record_key(record: RegisteredGitAuthorization) -> str:
    return "\x00".join(
        (
            record.authorization_kind,
            record.connection_ref,
            str(record.repository or ""),
        )
    )


def _encode_cursor(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode(
        "ascii"
    ).rstrip("=")


def _decode_cursor(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized or len(normalized) > 1024:
        raise HubGitAuthorizationProvisioningError(
            "git_authorization_cursor_invalid"
        )
    try:
        return base64.urlsafe_b64decode(
            normalized + "=" * (-len(normalized) % 4)
        ).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        raise HubGitAuthorizationProvisioningError(
            "git_authorization_cursor_invalid"
        ) from None


def _require_ref(value: str) -> str:
    normalized = str(value or "").strip()
    if _OPAQUE_REFERENCE.fullmatch(normalized) is None:
        raise HubGitAuthorizationProvisioningError(
            "git_authorization_ref_invalid"
        )
    return normalized


def _normalize_repository(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if _REPOSITORY.fullmatch(normalized) is None:
        raise HubGitAuthorizationProvisioningError(
            "git_authorization_repository_invalid"
        )
    return normalized


def _scope_wire(scope: GitSourceScope) -> Mapping[str, str]:
    return {
        "tenant_id": str(scope.tenant_id),
        "project_id": str(scope.project_id),
        "owner_id": str(scope.owner_id),
    }


def _digest(value: Mapping[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()


__all__ = [
    "GitAuthorizationProviderHealth",
    "GitAuthorizationProvisioningRequest",
    "GitAuthorizationSelection",
    "HubGitAuthorizationProvisioningError",
    "HubGitAuthorizationProvisioningPort",
    "HubGitAuthorizationProvisioningService",
    "ProvisionedGitAuthorization",
    "UnavailableHubGitAuthorizationProvisioner",
    "UnavailableHubGitSecretResolver",
]
