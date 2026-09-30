"""Value objects, request parsers and ports of Source Control grant admin.

Pure data contracts only: no persistence and no policy evaluation. They are
shared by the grant admin service, its collaborators and the API runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol

from agent.services.context_policy_lifecycle import (
    ContextPolicyActor,
    ContextPolicyPreview,
    ContextPolicyVersion,
)
from ananta_contracts.source_control import (
    DestinationDescriptor,
    GrantOperation,
    GrantTransformation,
)

SCHEMA_GRANT = "ananta.source-control.grant-admin-item.v1"
SCHEMA_GRANT_LIST = "ananta.source-control.grant-admin-list.v1"
SCHEMA_PRESET = "ananta.source-control.grant-preset.v1"


class SourceControlGrantAdminError(ValueError):
    """Stable service-boundary error without persistence detail leakage."""

    def __init__(self, reason_code: str, *, status_code: int) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class GrantAdminActor:
    subject_id: str
    tenant_id: str
    project_id: str
    roles: frozenset[str]


@dataclass(frozen=True)
class GrantCreateRequest:
    source_revision_id: str
    destination_id: str
    policy_id: str
    preset_id: str
    duration_seconds: int

    @classmethod
    def from_mapping(
        cls, payload: Mapping[str, object]
    ) -> "GrantCreateRequest":
        expected = {
            "source_revision_id",
            "destination_id",
            "policy_id",
            "preset_id",
            "duration_seconds",
        }
        if set(payload) != expected:
            raise SourceControlGrantAdminError(
                "grant_create_fields_invalid", status_code=400
            )
        duration = payload["duration_seconds"]
        if isinstance(duration, bool) or not isinstance(duration, int):
            raise SourceControlGrantAdminError(
                "grant_duration_invalid", status_code=400
            )
        return cls(
            source_revision_id=str(payload["source_revision_id"]),
            destination_id=str(payload["destination_id"]),
            policy_id=str(payload["policy_id"]),
            preset_id=str(payload["preset_id"]),
            duration_seconds=duration,
        )


@dataclass(frozen=True)
class GrantRevokeRequest:
    reason_code: str

    @classmethod
    def from_mapping(
        cls, payload: Mapping[str, object]
    ) -> "GrantRevokeRequest":
        if set(payload) != {"reason_code"}:
            raise SourceControlGrantAdminError(
                "grant_revoke_fields_invalid", status_code=400
            )
        return cls(reason_code=str(payload["reason_code"]))


@dataclass(frozen=True)
class GrantPreset:
    preset_id: str
    label: str
    description: str
    operation: GrantOperation
    transformation: GrantTransformation
    purpose: str
    consumption_mode: str
    max_duration_seconds: int

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": SCHEMA_PRESET,
            "preset_id": self.preset_id,
            "label": self.label,
            "description": self.description,
            "operation": self.operation.value,
            "transformation": self.transformation.value,
            "purpose": self.purpose,
            "max_duration_seconds": self.max_duration_seconds,
        }


@dataclass(frozen=True)
class GrantView:
    grant_id: str
    grant_family_id: str
    version: int
    source_revision_id: str
    destination_id: str
    preset_id: str | None
    operation: str
    transformation: str
    purpose: str
    policy_version: str
    state: str
    issued_at: str
    expires_at: str
    expired: bool
    etag: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": SCHEMA_GRANT,
            "grant_id": self.grant_id,
            "grant_family_id": self.grant_family_id,
            "version": self.version,
            "source_revision_id": self.source_revision_id,
            "destination_id": self.destination_id,
            "preset_id": self.preset_id,
            "operation": self.operation,
            "transformation": self.transformation,
            "purpose": self.purpose,
            "policy_version": self.policy_version,
            "state": self.state,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "expired": self.expired,
            "etag": self.etag,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "GrantView":
        if payload.get("schema") != SCHEMA_GRANT:
            raise SourceControlGrantAdminError(
                "grant_idempotency_result_invalid", status_code=500
            )
        try:
            return cls(
                grant_id=str(payload["grant_id"]),
                grant_family_id=str(payload["grant_family_id"]),
                version=int(payload["version"]),
                source_revision_id=str(payload["source_revision_id"]),
                destination_id=str(payload["destination_id"]),
                preset_id=(
                    str(payload["preset_id"])
                    if payload.get("preset_id") is not None
                    else None
                ),
                operation=str(payload["operation"]),
                transformation=str(payload["transformation"]),
                purpose=str(payload["purpose"]),
                policy_version=str(payload["policy_version"]),
                state=str(payload["state"]),
                issued_at=str(payload["issued_at"]),
                expires_at=str(payload["expires_at"]),
                expired=bool(payload["expired"]),
                etag=str(payload["etag"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise SourceControlGrantAdminError(
                "grant_idempotency_result_invalid", status_code=500
            ) from exc


@dataclass(frozen=True)
class GrantListPage:
    items: tuple[GrantView, ...]
    next_cursor: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": SCHEMA_GRANT_LIST,
            "items": [item.to_dict() for item in self.items],
            "next_cursor": self.next_cursor,
        }


class ScopedDestinationCatalogPort(Protocol):
    """Implemented by ScopedWorkerModelDestinationCatalog."""

    def get(
        self,
        *,
        tenant_id: str,
        project_id: str,
        destination_id: str,
    ) -> DestinationDescriptor | None: ...


class ActiveContextPolicyPort(Protocol):
    """Subset implemented by the persistent Context Policy lifecycle."""

    def active(
        self, *, actor: ContextPolicyActor, policy_id: str
    ) -> ContextPolicyVersion: ...

    def preview(
        self,
        *,
        actor: ContextPolicyActor,
        policy_id: str,
        version: int,
        source_revision_id: str,
        destination_id: str,
        operation: GrantOperation,
        transformation: GrantTransformation,
    ) -> ContextPolicyPreview: ...


__all__ = [
    "ActiveContextPolicyPort",
    "GrantAdminActor",
    "GrantCreateRequest",
    "GrantListPage",
    "GrantPreset",
    "GrantRevokeRequest",
    "GrantView",
    "SCHEMA_GRANT",
    "SCHEMA_GRANT_LIST",
    "SCHEMA_PRESET",
    "ScopedDestinationCatalogPort",
    "SourceControlGrantAdminError",
]
