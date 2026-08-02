"""Hub-owned application service for safe knowledge-index access.

The browser selects only a server-published safe option and destination. The
Hub resolves the current admitted revision, owns the policy lifecycle, and
delegates final authorization to the transactional Grant Admin service.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from agent.services.context_policy_lifecycle import (
    ContextPolicyActor,
    ContextPolicyLifecycleError,
    derive_context_policy_digest,
)
from agent.services.source_control_grant_admin import (
    GrantAdminActor,
    GrantCreateRequest,
)
from agent.services.source_control_projection_service import (
    SourceControlPrincipal,
)
from ananta_contracts.source_control import (
    GrantOperation,
    GrantTransformation,
    ProviderLocation,
)


_LOG = logging.getLogger(__name__)

SAFE_INDEX_ACCESS_OPTION_ID = "local-redacted-one-time-index"
SAFE_INDEX_ACCESS_POLICY_ID = "ananta-safe-local-redacted-index"
SAFE_INDEX_ACCESS_PRESET_ID = "worker_index_redacted"
SAFE_INDEX_ACCESS_MIN_DURATION_SECONDS = 60
SAFE_INDEX_ACCESS_MAX_DURATION_SECONDS = 900
SAFE_INDEX_ACCESS_DEFAULT_DURATION_SECONDS = 900

_SAFE_EFFECT: Mapping[str, object] = {
    "provider_location": "local",
    "transformation": "redacted",
    "one_time": True,
}
_SAFE_POLICY_DOCUMENT: Mapping[str, object] = {
    "schema": "ananta.context-access-policy.v1",
    "policy_id": SAFE_INDEX_ACCESS_POLICY_ID,
    "scope": "project",
    "defaults": {
        "send_allowed": False,
        "read_allowed": False,
        "write_allowed": False,
    },
    "rules": [
        {
            "id": "local-redacted-index-only",
            "description": (
                "Allow only redacted reads on a local-container worker."
            ),
            "allowed_provider_locations": [
                ProviderLocation.LOCAL_CONTAINER.value
            ],
            "read_allowed": True,
            "write_allowed": False,
            "send_allowed": False,
            "cloud_allowed": False,
            "external_worker_allowed": False,
            "redaction_required": True,
        }
    ],
    "precedence": 100,
}
_SAFE_POLICY_DIGEST = derive_context_policy_digest(_SAFE_POLICY_DOCUMENT)
_MUTATOR_ROLES = frozenset({"admin", "project_owner", "owner"})


class SourceControlPrepareIndexAccessError(ValueError):
    """Stable, content-free error at the application boundary."""

    def __init__(self, reason_code: str, *, status_code: int = 400) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


class SourceControlProjectionPort(Protocol):
    def get(
        self, *, principal: SourceControlPrincipal, connection_id: str
    ) -> object: ...


class SourceControlBindingPort(Protocol):
    def binding(
        self, *, resource_kind: str, resource_id: str
    ) -> Mapping[str, object] | None: ...


class DestinationCatalogPort(Protocol):
    def list(
        self,
        *,
        tenant_id: str,
        project_id: str,
        cursor: str | None,
        limit: int,
        filters: Mapping[str, object],
    ) -> tuple[Sequence[object], str | None]: ...


class ContextPolicyLifecyclePort(Protocol):
    def active(self, **kwargs: object) -> object: ...

    def versions(
        self, **kwargs: object
    ) -> tuple[Sequence[object], str | None]: ...

    def create_draft(self, **kwargs: object) -> object: ...

    def lint(self, **kwargs: object) -> Sequence[object]: ...

    def preview(self, **kwargs: object) -> object: ...

    def activate(self, **kwargs: object) -> object: ...


class GrantAdminPort(Protocol):
    def create_grant(self, **kwargs: object) -> object: ...


class IdempotencyStorePort(Protocol):
    def claim(self, *, idempotency_key: str, plan_digest: str) -> object: ...

    def complete(self, **kwargs: object) -> None: ...

    def release(self, **kwargs: object) -> None: ...


@dataclass(frozen=True)
class PrepareIndexAccessRequest:
    source_revision_id: str
    destination_id: str
    option_id: str
    duration_seconds: int
    confirmed: bool

    @classmethod
    def from_mapping(
        cls, payload: Mapping[str, object]
    ) -> "PrepareIndexAccessRequest":
        fields = {
            "source_revision_id",
            "destination_id",
            "option_id",
            "duration_seconds",
            "confirmed",
        }
        if set(payload) != fields:
            raise SourceControlPrepareIndexAccessError(
                "index_access_fields_invalid"
            )
        duration = payload.get("duration_seconds")
        if isinstance(duration, bool) or not isinstance(duration, int):
            raise SourceControlPrepareIndexAccessError(
                "index_access_duration_invalid"
            )
        if payload.get("confirmed") is not True:
            raise SourceControlPrepareIndexAccessError(
                "index_access_confirmation_required"
            )
        for field in ("source_revision_id", "destination_id", "option_id"):
            value = payload.get(field)
            if not isinstance(value, str) or not value.strip():
                raise SourceControlPrepareIndexAccessError(
                    f"index_access_{field}_invalid"
                )
        return cls(
            source_revision_id=str(payload["source_revision_id"]).strip(),
            destination_id=str(payload["destination_id"]).strip(),
            option_id=str(payload["option_id"]).strip(),
            duration_seconds=duration,
            confirmed=True,
        )


@dataclass(frozen=True)
class _ReadinessSnapshot:
    etag: str
    source_revision: Mapping[str, object] | None
    destinations: tuple[Mapping[str, object], ...]
    body: Mapping[str, object]


class SourceControlPrepareIndexAccessService:
    """Coordinate a resumable, fail-closed policy-and-grant Hub saga."""

    def __init__(
        self,
        *,
        projections: SourceControlProjectionPort,
        bindings: SourceControlBindingPort,
        destinations: DestinationCatalogPort,
        policies: ContextPolicyLifecyclePort,
        grants: GrantAdminPort,
        idempotency: IdempotencyStorePort,
    ) -> None:
        self._projections = projections
        self._bindings = bindings
        self._destinations = destinations
        self._policies = policies
        self._grants = grants
        self._idempotency = idempotency

    def options(
        self,
        *,
        actor: SourceControlPrincipal,
        connection_id: str,
    ) -> Mapping[str, object]:
        snapshot = self._snapshot(actor=actor, connection_id=connection_id)
        return dict(snapshot.body)

    def prepare(
        self,
        *,
        actor: SourceControlPrincipal,
        connection_id: str,
        payload: Mapping[str, object],
        if_match: str,
        idempotency_key: str,
    ) -> Mapping[str, object]:
        request = PrepareIndexAccessRequest.from_mapping(payload)
        normalized_if_match = _normalize_etag(if_match)
        operation_key = _operation_key(actor.tenant_id, idempotency_key)
        request_digest = _digest(
            {
                "actor_id": actor.subject_id,
                "connection_id": connection_id,
                "if_match": normalized_if_match,
                "payload": {
                    "source_revision_id": request.source_revision_id,
                    "destination_id": request.destination_id,
                    "option_id": request.option_id,
                    "duration_seconds": request.duration_seconds,
                    "confirmed": request.confirmed,
                },
                "project_id": actor.project_id,
                "tenant_id": actor.tenant_id,
            }
        )
        claim = self._idempotency.claim(
            idempotency_key=operation_key,
            plan_digest=request_digest,
        )
        if str(getattr(claim, "state", "")) == "completed":
            result = getattr(claim, "result", None)
            if not isinstance(result, Mapping):
                raise SourceControlPrepareIndexAccessError(
                    "index_access_idempotency_result_invalid",
                    status_code=500,
                )
            return dict(result)
        if str(getattr(claim, "state", "")) != "claimed":
            raise SourceControlPrepareIndexAccessError(
                "index_access_idempotency_in_progress",
                status_code=409,
            )
        claim_token = str(getattr(claim, "claim_token", "") or "")
        if not claim_token:
            raise SourceControlPrepareIndexAccessError(
                "index_access_idempotency_claim_invalid",
                status_code=500,
            )

        try:
            result = self._prepare_claimed(
                actor=actor,
                connection_id=connection_id,
                request=request,
                if_match=normalized_if_match,
                idempotency_key=idempotency_key,
            )
            self._idempotency.complete(
                idempotency_key=operation_key,
                plan_digest=request_digest,
                claim_token=claim_token,
                result=result,
            )
            return result
        except Exception:
            try:
                self._idempotency.release(
                    idempotency_key=operation_key,
                    plan_digest=request_digest,
                    claim_token=claim_token,
                )
            except Exception:
                _LOG.error(
                    "prepare_index_access_idempotency_release_failed",
                    exc_info=True,
                )
            raise

    def _prepare_claimed(
        self,
        *,
        actor: SourceControlPrincipal,
        connection_id: str,
        request: PrepareIndexAccessRequest,
        if_match: str,
        idempotency_key: str,
    ) -> Mapping[str, object]:
        snapshot = self._snapshot(actor=actor, connection_id=connection_id)
        if if_match != snapshot.etag:
            raise SourceControlPrepareIndexAccessError(
                "index_access_version_conflict", status_code=412
            )
        readiness = snapshot.body.get("readiness")
        if (
            not isinstance(readiness, Mapping)
            or readiness.get("ready") is not True
        ):
            raise SourceControlPrepareIndexAccessError(
                "index_access_not_ready", status_code=409
            )
        source_revision = snapshot.source_revision
        if (
            source_revision is None
            or request.source_revision_id
            != source_revision.get("source_revision_id")
        ):
            raise SourceControlPrepareIndexAccessError(
                "index_access_source_revision_stale", status_code=409
            )
        if request.option_id != SAFE_INDEX_ACCESS_OPTION_ID:
            raise SourceControlPrepareIndexAccessError(
                "index_access_option_invalid"
            )
        if not (
            SAFE_INDEX_ACCESS_MIN_DURATION_SECONDS
            <= request.duration_seconds
            <= SAFE_INDEX_ACCESS_MAX_DURATION_SECONDS
        ):
            raise SourceControlPrepareIndexAccessError(
                "index_access_duration_invalid"
            )
        destination = next(
            (
                item
                for item in snapshot.destinations
                if item.get("destination_id") == request.destination_id
            ),
            None,
        )
        if destination is None:
            raise SourceControlPrepareIndexAccessError(
                "index_access_destination_not_safe", status_code=403
            )

        policy_actor = ContextPolicyActor(
            subject_id=actor.subject_id,
            tenant_id=actor.tenant_id,
            project_id=actor.project_id,
            roles=actor.roles,
        )
        policy = self._ensure_policy(
            actor=policy_actor,
            source_revision_id=request.source_revision_id,
            destination_id=request.destination_id,
            idempotency_key=idempotency_key,
        )
        grant = self._grants.create_grant(
            actor=GrantAdminActor(
                subject_id=actor.subject_id,
                tenant_id=actor.tenant_id,
                project_id=actor.project_id,
                roles=actor.roles,
            ),
            request=GrantCreateRequest(
                source_revision_id=request.source_revision_id,
                destination_id=request.destination_id,
                policy_id=SAFE_INDEX_ACCESS_POLICY_ID,
                preset_id=SAFE_INDEX_ACCESS_PRESET_ID,
                duration_seconds=request.duration_seconds,
            ),
            if_match=str(_value(policy, "etag")),
            idempotency_key=_child_key(
                "grant", idempotency_key, request.destination_id
            ),
        )
        return {
            "access_ready": True,
            "connection_id": connection_id,
            "source_revision_id": request.source_revision_id,
            "destination_id": request.destination_id,
            "option_id": request.option_id,
            "effect": dict(_SAFE_EFFECT),
            "policy": {
                "policy_id": str(_value(policy, "policy_id")),
                "version": int(_value(policy, "version")),
                "state": str(_value(policy, "state")),
                "etag": _normalize_etag(str(_value(policy, "etag"))),
            },
            "grant": {
                "grant_id": str(_value(grant, "grant_id")),
                "state": str(_value(grant, "state")),
                "etag": _normalize_etag(str(_value(grant, "etag"))),
                "expires_at": str(_value(grant, "expires_at")),
            },
            "next_actions": ["start_index_run"],
        }

    def _snapshot(
        self,
        *,
        actor: SourceControlPrincipal,
        connection_id: str,
    ) -> _ReadinessSnapshot:
        projection = self._projections.get(
            principal=actor, connection_id=connection_id
        )
        binding = self._bindings.binding(
            resource_kind="source_connection", resource_id=connection_id
        )
        if (
            binding is None
            or binding.get("tenant_id") != actor.tenant_id
            or binding.get("project_id") != actor.project_id
        ):
            raise SourceControlPrepareIndexAccessError(
                "index_access_resource_not_found", status_code=404
            )
        if (
            not (_MUTATOR_ROLES & actor.roles)
            and binding.get("owner_id") != actor.subject_id
        ):
            raise SourceControlPrepareIndexAccessError(
                "index_access_owner_required", status_code=403
            )

        revision = getattr(projection, "revision", None)
        admission = getattr(projection, "admission", None)
        admission_state = str(
            (admission or {}).get("state")
            or (revision or {}).get("admission_state")
            or "pending"
        )
        source_revision = (
            {
                "source_revision_id": revision.get("source_revision_id"),
                "revision_digest": revision.get("revision_digest"),
                "admission_state": admission_state,
                "captured_at": revision.get("captured_at"),
            }
            if isinstance(revision, Mapping)
            else None
        )
        destinations = self._safe_destinations(actor)
        reason_codes: list[str] = []
        connection = getattr(projection, "connection", {})
        if (
            not isinstance(connection, Mapping)
            or connection.get("state") != "active"
        ):
            reason_codes.append("connection_not_active")
        if source_revision is None:
            reason_codes.append("source_revision_missing")
        elif admission_state != "admitted":
            reason_codes.append("source_revision_not_admitted")
        if not destinations:
            reason_codes.append("local_destination_unavailable")

        option = {
            "option_id": SAFE_INDEX_ACCESS_OPTION_ID,
            "preset_id": SAFE_INDEX_ACCESS_PRESET_ID,
            "label": "Local redacted one-time index access",
            "effect": dict(_SAFE_EFFECT),
            "duration_seconds": {
                "minimum": SAFE_INDEX_ACCESS_MIN_DURATION_SECONDS,
                "maximum": SAFE_INDEX_ACCESS_MAX_DURATION_SECONDS,
                "default": SAFE_INDEX_ACCESS_DEFAULT_DURATION_SECONDS,
            },
        }
        projection_etag = _normalize_etag(
            str(getattr(projection, "etag", ""))
        )
        body: dict[str, object] = {
            "connection_id": connection_id,
            "source_revision": source_revision,
            "destinations": [dict(item) for item in destinations],
            "options": [option],
            "readiness": {
                "ready": not reason_codes,
                "reason_codes": reason_codes,
            },
        }
        etag = _digest({"projection_etag": projection_etag, "body": body})
        body["etag"] = etag
        return _ReadinessSnapshot(
            etag=etag,
            source_revision=source_revision,
            destinations=destinations,
            body=body,
        )

    def _safe_destinations(
        self, actor: SourceControlPrincipal
    ) -> tuple[Mapping[str, object], ...]:
        values, _next_cursor = self._destinations.list(
            tenant_id=actor.tenant_id,
            project_id=actor.project_id,
            cursor=None,
            limit=200,
            filters={},
        )
        projected = []
        for value in values:
            if _value(value, "provider_location") != (
                ProviderLocation.LOCAL_CONTAINER
            ):
                continue
            projected.append(
                {
                    "destination_id": str(_value(value, "destination_id")),
                    "worker_id": str(_value(value, "worker_id")),
                    "runtime_kind": str(_value(value, "runtime_kind")),
                    "provider_location": (
                        ProviderLocation.LOCAL_CONTAINER.value
                    ),
                    "data_residency": str(_value(value, "data_residency")),
                }
            )
        return tuple(
            sorted(projected, key=lambda item: item["destination_id"])
        )

    def _ensure_policy(
        self,
        *,
        actor: ContextPolicyActor,
        source_revision_id: str,
        destination_id: str,
        idempotency_key: str,
    ) -> object:
        active = self._active_policy(actor)
        if active is not None and _is_safe_policy(active):
            return active

        versions, _ = self._policies.versions(
            actor=actor,
            policy_id=SAFE_INDEX_ACCESS_POLICY_ID,
            cursor=None,
            limit=1,
        )
        latest = versions[0] if versions else None
        if (
            latest is not None
            and _is_safe_policy(latest)
            and str(_value(latest, "state")) == "draft"
        ):
            draft = latest
        else:
            try:
                draft = self._policies.create_draft(
                    actor=actor,
                    policy_id=SAFE_INDEX_ACCESS_POLICY_ID,
                    document=dict(_SAFE_POLICY_DOCUMENT),
                    expected_latest_version=(
                        int(_value(latest, "version"))
                        if latest is not None
                        else None
                    ),
                    idempotency_key=_child_key(
                        "policy-draft", idempotency_key, destination_id
                    ),
                )
            except ContextPolicyLifecycleError as exc:
                if "version_conflict" not in exc.reason_code:
                    raise
                versions, _ = self._policies.versions(
                    actor=actor,
                    policy_id=SAFE_INDEX_ACCESS_POLICY_ID,
                    cursor=None,
                    limit=1,
                )
                draft = versions[0] if versions else None
                if draft is None or not _is_safe_policy(draft):
                    raise

        diagnostics = self._policies.lint(
            actor=actor,
            policy_id=SAFE_INDEX_ACCESS_POLICY_ID,
            version=int(_value(draft, "version")),
        )
        if any(
            str(_value(item, "severity")) == "error"
            for item in diagnostics
        ):
            raise SourceControlPrepareIndexAccessError(
                "index_access_managed_policy_lint_failed", status_code=409
            )
        preview = self._policies.preview(
            actor=actor,
            policy_id=SAFE_INDEX_ACCESS_POLICY_ID,
            version=int(_value(draft, "version")),
            source_revision_id=source_revision_id,
            destination_id=destination_id,
            operation=GrantOperation.INDEX,
            transformation=GrantTransformation.REDACTED,
        )
        if (
            str(_value(preview, "policy_digest"))
            != str(_value(draft, "policy_digest"))
            or str(_value(preview, "decision"))
            not in {"allow", "allow_redacted"}
        ):
            raise SourceControlPrepareIndexAccessError(
                "index_access_managed_policy_denied", status_code=403
            )
        if str(_value(draft, "state")) == "active":
            return draft
        try:
            return self._policies.activate(
                actor=actor,
                policy_id=SAFE_INDEX_ACCESS_POLICY_ID,
                version=int(_value(draft, "version")),
                if_match=str(_value(draft, "etag")),
                idempotency_key=_child_key(
                    "policy-activate", idempotency_key, destination_id
                ),
            )
        except ContextPolicyLifecycleError as exc:
            if not any(
                marker in exc.reason_code
                for marker in ("version_conflict", "state_conflict")
            ):
                raise
            active = self._active_policy(actor)
            if active is None or not _is_safe_policy(active):
                raise
            return active

    def _active_policy(self, actor: ContextPolicyActor) -> object | None:
        try:
            return self._policies.active(
                actor=actor, policy_id=SAFE_INDEX_ACCESS_POLICY_ID
            )
        except ContextPolicyLifecycleError as exc:
            if not any(
                marker in exc.reason_code
                for marker in ("not_found", "missing")
            ):
                raise
            return None


def _is_safe_policy(value: object) -> bool:
    document = _value(value, "document")
    return (
        isinstance(document, Mapping)
        and _digest(document) == _SAFE_POLICY_DIGEST
    )


def _value(value: object, field: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(field)
    return getattr(value, field, None)


def _normalize_etag(value: str) -> str:
    return str(value or "").strip().removeprefix("W/").strip().strip('"')


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _operation_key(tenant_id: str, idempotency_key: str) -> str:
    return "prepare_" + _digest(
        {"idempotency_key": idempotency_key, "tenant_id": tenant_id}
    )


def _child_key(operation: str, idempotency_key: str, scope: str) -> str:
    return "pia_" + _digest(
        {
            "idempotency_key": idempotency_key,
            "operation": operation,
            "scope": scope,
        }
    )


__all__ = [
    "SAFE_INDEX_ACCESS_DEFAULT_DURATION_SECONDS",
    "SAFE_INDEX_ACCESS_MAX_DURATION_SECONDS",
    "SAFE_INDEX_ACCESS_MIN_DURATION_SECONDS",
    "SAFE_INDEX_ACCESS_OPTION_ID",
    "SourceControlPrepareIndexAccessError",
    "SourceControlPrepareIndexAccessService",
]
