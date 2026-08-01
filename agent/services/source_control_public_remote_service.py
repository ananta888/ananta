"""Hub-owned validate-then-create application service for public remotes."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
from collections.abc import Callable, Mapping
from typing import Protocol

from agent.repositories.source_control_public_remote_repository import (
    SourceControlPublicRemotePersistenceError,
)
from agent.services.git_remote_policy_service import (
    GitRemoteAccessPolicyPort,
    GitRemotePolicyError,
    GitRemotePolicyRequest,
    GitTransportAuthorization,
)
from agent.services.source_control_public_remote_contracts import (
    PublicRemoteCreateSelection,
    PublicRemoteRecord,
    PublicRemoteSelection,
    PublicRemoteValidationBinding,
    SourceControlPublicRemoteContractError,
    audit_binding_digest,
)
from agent.services.project_access_authority import (
    ProjectAccessError,
    ProjectAccessPort,
    ProjectCapability,
)
from agent.sources.git_source_connector_common import (
    GitConnectorProviderError,
    GitSourceScope,
)

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,190}$")
_IDEMPOTENCY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,127}$")


class SourceControlPublicRemoteError(ValueError):
    def __init__(self, reason_code: str, *, status_code: int = 400) -> None:
        self.reason_code = str(reason_code)
        self.status_code = int(status_code)
        super().__init__(self.reason_code)


class PublicRemoteRepositoryPort(Protocol):
    def store_validation(self, **kwargs) -> None: ...
    def consume_validation(self, **kwargs) -> PublicRemoteRecord: ...
    def get_consumed_validation(self, **kwargs) -> PublicRemoteRecord: ...
    def record_denial(self, **kwargs) -> None: ...


class PublicRemoteIdempotencyPort(Protocol):
    def claim(self, *, idempotency_key: str, plan_digest: str): ...
    def complete(
        self,
        *,
        idempotency_key: str,
        plan_digest: str,
        claim_token: str | None,
        result: Mapping[str, object],
    ) -> None: ...


class PublicRemoteTransportPort(Protocol):
    def supports_authorization(
        self,
        authorization: GitTransportAuthorization,
    ) -> bool: ...

    def resolve_commit(
        self,
        *,
        authorization: GitTransportAuthorization,
        credential_username: str | None,
        requested_ref: str,
    ) -> str: ...


class SourceControlPublicRemoteService:
    def __init__(
        self,
        *,
        repository: PublicRemoteRepositoryPort,
        remote_policy: GitRemoteAccessPolicyPort,
        transport: PublicRemoteTransportPort | None,
        idempotency: PublicRemoteIdempotencyPort,
        project_access: ProjectAccessPort,
        enabled: bool,
        connector_registry_ready: bool,
        ttl_seconds: int = 300,
        clock: Callable[[], float] = time.time,
        token_factory: Callable[[], str] = (
            lambda: secrets.token_urlsafe(32)
        ),
    ) -> None:
        if not 30 <= int(ttl_seconds) <= 900:
            raise ValueError("public_remote_ttl_invalid")
        self._repository = repository
        self._remote_policy = remote_policy
        self._transport = transport
        self._idempotency = idempotency
        self._project_access = project_access
        self._enabled = bool(enabled)
        self._connector_registry_ready = bool(connector_registry_ready)
        self._ttl_seconds = int(ttl_seconds)
        self._clock = clock
        self._token_factory = token_factory

    def validate(
        self,
        *,
        principal: object,
        payload: Mapping[str, object],
    ) -> Mapping[str, object]:
        scope = self._scope(principal)
        self._require_available()
        try:
            selection = PublicRemoteSelection.from_mapping(payload)
        except SourceControlPublicRemoteContractError as exc:
            raise SourceControlPublicRemoteError(
                exc.reason_code,
                status_code=exc.status_code,
            ) from None
        audit_digest = audit_binding_digest(
            scope=scope,
            selection=selection,
        )
        try:
            policy_request = GitRemotePolicyRequest(
                remote_url=selection.internal_remote_url,
                operation="fetch",
                credential_ref=None,
                allow_redirects=False,
                redirect_url=None,
                proxy_url=None,
                recurse_submodules=False,
                lfs_mode="disabled",
            )
            authorized = self._remote_policy.authorize(policy_request)
            if (
                authorized.scheme != "https"
                or authorized.host != selection.host
                or authorized.port != 443
                or authorized.credential_ref is not None
                or authorized.canonical_url
                != selection.internal_remote_url
            ):
                raise SourceControlPublicRemoteError(
                    "public_remote_policy_projection_invalid"
                )
            transport_authorization = GitTransportAuthorization.create(
                authorized=authorized,
                request=policy_request,
            )
            if (
                self._transport is None
                or not self._transport.supports_authorization(
                    transport_authorization
                )
            ):
                raise SourceControlPublicRemoteError(
                    "public_remote_transport_unavailable",
                    status_code=503,
                )
            commit_sha = self._transport.resolve_commit(
                authorization=transport_authorization,
                credential_username=None,
                requested_ref=selection.requested_ref,
            )
            binding = PublicRemoteValidationBinding(
                scope=scope,
                selection=selection,
                commit_sha=commit_sha,
                policy_digest=(
                    transport_authorization.authorization_digest
                ),
            )
        except (GitRemotePolicyError, GitConnectorProviderError) as exc:
            self._record_denial(
                scope=scope,
                event_type="validate",
                reason_code=exc.reason_code,
                binding_digest=audit_digest,
            )
            raise SourceControlPublicRemoteError(
                exc.reason_code,
                status_code=422,
            ) from None
        except SourceControlPublicRemoteError as exc:
            self._record_denial(
                scope=scope,
                event_type="validate",
                reason_code=exc.reason_code,
                binding_digest=audit_digest,
            )
            raise

        expires_at = float(self._clock()) + self._ttl_seconds
        for _attempt in range(3):
            handle = f"prv1_{self._token_factory()}"
            handle_digest = hashlib.sha256(
                handle.encode("ascii")
            ).hexdigest()
            try:
                self._repository.store_validation(
                    handle_digest=handle_digest,
                    binding=binding,
                    expires_at_epoch=expires_at,
                )
                return self._validation_view(
                    handle=handle,
                    binding=binding,
                    expires_at_epoch=expires_at,
                )
            except SourceControlPublicRemotePersistenceError as exc:
                if (
                    exc.reason_code
                    == "public_remote_validation_handle_collision"
                ):
                    continue
                raise
        raise SourceControlPublicRemoteError(
            "public_remote_validation_handle_generation_failed",
            status_code=503,
        )

    def create(
        self,
        *,
        principal: object,
        payload: Mapping[str, object],
        idempotency_key: str,
    ) -> Mapping[str, object]:
        scope = self._scope(principal)
        self._require_available()
        try:
            selection = PublicRemoteCreateSelection.from_mapping(payload)
        except SourceControlPublicRemoteContractError as exc:
            raise SourceControlPublicRemoteError(
                exc.reason_code,
                status_code=exc.status_code,
            ) from None
        normalized_key = str(idempotency_key or "").strip()
        if _IDEMPOTENCY.fullmatch(normalized_key) is None:
            raise SourceControlPublicRemoteError(
                "idempotency_key_required"
            )
        operation_key = "public-remote:" + hashlib.sha256(
            (
                f"{scope.tenant_id}\0{scope.project_id}\0"
                f"{scope.owner_id}\0{normalized_key}"
            ).encode("utf-8")
        ).hexdigest()
        plan_digest = _digest(
            {
                "operation": "public_remote_create",
                "tenant_id": scope.tenant_id,
                "project_id": scope.project_id,
                "owner_id": scope.owner_id,
                "handle_digest": selection.handle_digest,
            }
        )
        claim = self._idempotency.claim(
            idempotency_key=operation_key,
            plan_digest=plan_digest,
        )
        state = str(getattr(claim, "state", "") or "")
        if state == "completed":
            record = self._repository.get_consumed_validation(
                handle_digest=selection.handle_digest,
                scope=scope,
            )
            return self._created_view(record)
        if state == "in_progress":
            raise SourceControlPublicRemoteError(
                "public_remote_operation_in_progress",
                status_code=409,
            )
        claim_token = getattr(claim, "claim_token", None)
        if (
            state != "claimed"
            or not isinstance(claim_token, str)
            or not claim_token
        ):
            raise SourceControlPublicRemoteError(
                "public_remote_idempotency_claim_failed",
                status_code=409,
            )
        try:
            record = self._consume(
                handle_digest=selection.handle_digest,
                scope=scope,
            )
        except SourceControlPublicRemotePersistenceError as exc:
            self._record_denial(
                scope=scope,
                event_type="create",
                reason_code=exc.reason_code,
                binding_digest=selection.handle_digest,
            )
            raise SourceControlPublicRemoteError(
                exc.reason_code,
                status_code=exc.status_code,
            ) from None
        result = self._created_view(record)
        self._idempotency.complete(
            idempotency_key=operation_key,
            plan_digest=plan_digest,
            claim_token=claim_token,
            result=result,
        )
        return result

    def _consume(
        self,
        *,
        handle_digest: str,
        scope: GitSourceScope,
    ) -> PublicRemoteRecord:
        for _attempt in range(3):
            try:
                return self._repository.consume_validation(
                    handle_digest=handle_digest,
                    scope=scope,
                    remote_id=f"pub_{self._token_factory()}",
                )
            except SourceControlPublicRemotePersistenceError as exc:
                if exc.reason_code == "public_remote_id_collision":
                    continue
                raise
        raise SourceControlPublicRemotePersistenceError(
            "public_remote_id_generation_failed",
            status_code=503,
        )

    def _require_available(self) -> None:
        if not self._enabled:
            raise SourceControlPublicRemoteError(
                "public_remote_feature_disabled",
                status_code=404,
            )
        if not self._connector_registry_ready or self._transport is None:
            raise SourceControlPublicRemoteError(
                "public_remote_composition_unavailable",
                status_code=503,
            )

    def _scope(self, principal: object) -> GitSourceScope:
        roles = frozenset(getattr(principal, "roles", frozenset()) or ())
        values = (
            str(getattr(principal, "tenant_id", "") or "").strip(),
            str(getattr(principal, "project_id", "") or "").strip(),
            str(getattr(principal, "subject_id", "") or "").strip(),
        )
        if not all(_ID.fullmatch(value) for value in values):
            raise SourceControlPublicRemoteError(
                "source_control_principal_scope_required",
                status_code=403,
            )
        try:
            authorized = self._project_access.require(
                tenant_id=values[0],
                project_id=values[1],
                subject_id=values[2],
                capability=ProjectCapability.WRITE,
                tenant_admin="admin" in roles,
            )
        except ProjectAccessError as exc:
            raise SourceControlPublicRemoteError(
                exc.reason_code,
                status_code=exc.public_status,
            ) from None
        return GitSourceScope(
            tenant_id=authorized.tenant_id,
            project_id=authorized.project_id,
            owner_id=authorized.subject_id,
        )

    def _record_denial(
        self,
        *,
        scope: GitSourceScope,
        event_type: str,
        reason_code: str,
        binding_digest: str,
    ) -> None:
        try:
            self._repository.record_denial(
                scope=scope,
                event_type=event_type,
                reason_code=reason_code,
                binding_digest=binding_digest,
            )
        except Exception:
            pass

    @staticmethod
    def _validation_view(
        *,
        handle: str,
        binding: PublicRemoteValidationBinding,
        expires_at_epoch: float,
    ) -> Mapping[str, object]:
        selection = binding.selection
        return {
            "validation_handle": handle,
            "provider": selection.provider,
            "requested_ref": selection.requested_ref,
            "commit_sha": binding.commit_sha,
            "expires_at_epoch": int(expires_at_epoch),
            "capabilities": {
                "connector_type": selection.public_connector_type,
                "credential_mode": "none",
                "remote_url_exposed": False,
                "immutable_validation": True,
            },
        }

    @staticmethod
    def _created_view(
        record: PublicRemoteRecord,
    ) -> Mapping[str, object]:
        selection = record.binding.selection
        return {
            "remote_id": record.remote_id,
            "provider": selection.provider,
            "commit_sha": record.binding.commit_sha,
            "state": "active",
            "capabilities": {
                "connector_type": selection.public_connector_type,
                "credential_mode": "none",
                "remote_url_exposed": False,
            },
        }


def _digest(value: Mapping[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    ).hexdigest()


__all__ = [
    "SourceControlPublicRemoteError",
    "SourceControlPublicRemoteService",
]
