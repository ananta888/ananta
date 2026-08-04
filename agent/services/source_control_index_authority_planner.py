"""Fail-closed planning for immutable, Hub-authorized source index jobs."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol

from agent.services.source_access_enforcement import (
    SourceAccessRequest,
    SourceGrantResolverPort,
    source_access_grant_digest,
)
from agent.services.source_destination_resolution import (
    DestinationSelection,
    SourceDestinationResolutionService,
)
from ananta_contracts.knowledge_index_execution import (
    KNOWLEDGE_INDEX_DISPATCH_TRANSPORT_MARGIN_SECONDS,
    KnowledgeIndexExecutionAssignment,
    KnowledgeIndexFileManifest,
    KnowledgeIndexResourceBudget,
)
from ananta_contracts.source_control import (
    GrantOperation,
    GrantState,
    GrantTransformation,
)

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_CONNECTION_ID = re.compile(r"^conn_[0-9a-f]{64}$")
_REVISION_ID = re.compile(r"^srev_[0-9a-f]{64}$")
_OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,254}$")


class BoundSourceRevisionPlanningError(ValueError):
    """Stable failure raised before a governed index job can be submitted."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class BoundSourceRevisionAuthority:
    """Current Hub revision/admission projection returned by a revision port."""

    tenant_id: str
    project_id: str
    connection_id: str
    source_revision_id: str
    source_revision_digest: str
    content_manifest_digest: str
    connector_type: str
    source_id: str
    admission_state: str
    admission_digest: str

    def __post_init__(self) -> None:
        for name in ("tenant_id", "project_id", "connector_type", "source_id"):
            if not _OPAQUE_ID.fullmatch(str(getattr(self, name) or "")):
                raise BoundSourceRevisionPlanningError(f"{name}_invalid")
        if not _CONNECTION_ID.fullmatch(self.connection_id):
            raise BoundSourceRevisionPlanningError("connection_id_invalid")
        if not _REVISION_ID.fullmatch(self.source_revision_id):
            raise BoundSourceRevisionPlanningError("source_revision_id_invalid")
        for name in (
            "source_revision_digest",
            "content_manifest_digest",
            "admission_digest",
        ):
            if not _DIGEST.fullmatch(str(getattr(self, name) or "")):
                raise BoundSourceRevisionPlanningError(f"{name}_invalid")
        if self.admission_state not in {"admitted", "blocked", "pending"}:
            raise BoundSourceRevisionPlanningError("admission_state_invalid")


@dataclass(frozen=True)
class BoundSourceRevisionPayload:
    """Artifact-first payload projection for one exact immutable revision."""

    source_revision_id: str
    source_revision_digest: str
    content_manifest_digest: str
    files: tuple[Mapping[str, Any], ...]
    records: tuple[Mapping[str, Any], ...]
    payload_digest: str | None = None
    connection_id: str | None = None


@dataclass(frozen=True)
class BoundSourceIndexAuthority:
    """Hub-selected policy, destination, grant, lease, and resource limits."""

    policy_snapshot_id: str
    policy_snapshot_digest: str
    destination_selection: DestinationSelection
    source_access_grant_id: str
    source_access_grant_digest: str
    resources: KnowledgeIndexResourceBudget
    assignment: KnowledgeIndexExecutionAssignment

    def __post_init__(self) -> None:
        if not _OPAQUE_ID.fullmatch(str(self.policy_snapshot_id or "")):
            raise BoundSourceRevisionPlanningError("policy_snapshot_id_invalid")
        for name in (
            "policy_snapshot_digest",
            "source_access_grant_digest",
        ):
            if not _DIGEST.fullmatch(str(getattr(self, name) or "")):
                raise BoundSourceRevisionPlanningError(f"{name}_invalid")
        if not re.fullmatch(
            r"^grant_[0-9a-f]{64}$", str(self.source_access_grant_id or "")
        ):
            raise BoundSourceRevisionPlanningError(
                "source_access_grant_id_invalid"
            )


class BoundSourceRevisionAuthorityPort(Protocol):
    def resolve_bound_revision(
        self,
        *,
        tenant_id: str,
        project_id: str,
        connection_id: str,
        source_revision_id: str,
    ) -> BoundSourceRevisionAuthority | None: ...


class BoundSourceRevisionPayloadPort(Protocol):
    def load_bound_revision_payload(
        self,
        revision: BoundSourceRevisionAuthority,
    ) -> BoundSourceRevisionPayload: ...


class BoundSourceIndexAuthorityPort(Protocol):
    def resolve_bound_index_authority(
        self,
        *,
        revision: BoundSourceRevisionAuthority,
        actor_id: str,
        idempotency_key: str,
    ) -> BoundSourceIndexAuthority: ...


class BoundSourceRevisionAuthorityPlanner:
    """Compose closed job plans exclusively from current Hub authorities."""

    def __init__(
        self,
        *,
        revisions: BoundSourceRevisionAuthorityPort,
        payloads: BoundSourceRevisionPayloadPort,
        authority: BoundSourceIndexAuthorityPort,
        destinations: SourceDestinationResolutionService,
        grants: SourceGrantResolverPort,
        clock=lambda: datetime.now(timezone.utc),
    ) -> None:
        self._revisions = revisions
        self._payloads = payloads
        self._authority = authority
        self._destinations = destinations
        self._grants = grants
        self._clock = clock

    def plan_bound_source_revision(
        self,
        *,
        tenant_id: str,
        project_id: str,
        actor_id: str,
        connection_id: str,
        source_revision_id: str,
        source_revision_digest: str,
        content_manifest_digest: str,
        descriptor: Mapping[str, object],
        idempotency_key: str,
    ) -> Mapping[str, object]:
        """Return the exact closed plan consumed by the Hub submission adapter."""

        self._require_request_values(
            tenant_id=tenant_id,
            project_id=project_id,
            actor_id=actor_id,
            connection_id=connection_id,
            source_revision_id=source_revision_id,
            source_revision_digest=source_revision_digest,
            content_manifest_digest=content_manifest_digest,
            idempotency_key=idempotency_key,
        )
        source_id = str(descriptor.get("source_id") or "").strip()
        if not _OPAQUE_ID.fullmatch(source_id):
            raise BoundSourceRevisionPlanningError("source_id_invalid")
        for field_name, expected in (
            ("tenant_id", tenant_id),
            ("project_id", project_id),
            ("connection_id", connection_id),
        ):
            supplied = str(descriptor.get(field_name) or "").strip()
            if supplied and supplied != expected:
                raise BoundSourceRevisionPlanningError(
                    "source_descriptor_scope_mismatch"
                )

        revision = self._revisions.resolve_bound_revision(
            tenant_id=tenant_id,
            project_id=project_id,
            connection_id=connection_id,
            source_revision_id=source_revision_id,
        )
        if revision is None:
            raise BoundSourceRevisionPlanningError("source_revision_not_found")
        if (
            revision.tenant_id != tenant_id
            or revision.project_id != project_id
            or revision.connection_id != connection_id
            or revision.source_revision_id != source_revision_id
            or revision.source_revision_digest != source_revision_digest
            or revision.content_manifest_digest != content_manifest_digest
            or revision.source_id != source_id
        ):
            raise BoundSourceRevisionPlanningError("source_revision_stale")
        if revision.admission_state != "admitted":
            raise BoundSourceRevisionPlanningError(
                "source_revision_admission_required"
            )

        payload = self._payloads.load_bound_revision_payload(revision)
        if (
            payload.source_revision_id != revision.source_revision_id
            or payload.source_revision_digest != revision.source_revision_digest
            or payload.content_manifest_digest
            != revision.content_manifest_digest
        ):
            raise BoundSourceRevisionPlanningError(
                "source_revision_payload_binding_mismatch"
            )
        if payload.connection_id is not None and (
            payload.connection_id != revision.connection_id
        ):
            raise BoundSourceRevisionPlanningError(
                "source_revision_payload_connection_mismatch"
            )
        if payload.payload_digest is not None and not _DIGEST.fullmatch(
            payload.payload_digest
        ):
            raise BoundSourceRevisionPlanningError(
                "source_revision_payload_digest_invalid"
            )
        if not payload.records:
            raise BoundSourceRevisionPlanningError(
                "source_revision_payload_records_required"
            )
        try:
            file_manifest = KnowledgeIndexFileManifest.create(
                [dict(item) for item in payload.files]
            )
        except (TypeError, ValueError) as exc:
            raise BoundSourceRevisionPlanningError(
                "source_revision_file_manifest_invalid"
            ) from exc

        planned = self._authority.resolve_bound_index_authority(
            revision=revision,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
        )
        destination = self._destinations.resolve(
            planned.destination_selection
        )
        if destination.descriptor.worker_id != planned.assignment.worker_id:
            raise BoundSourceRevisionPlanningError(
                "source_index_destination_assignment_mismatch"
            )
        now = self._aware_now()
        now_ms = int(now.timestamp() * 1000)
        if not (
            planned.assignment.lease_issued_epoch_ms
            <= now_ms
            < planned.assignment.lease_expires_epoch_ms
        ):
            raise BoundSourceRevisionPlanningError(
                "source_index_assignment_lease_inactive"
            )
        # The authority issuer adds the pre-dispatch reserve to the lease and
        # grant budget so proposal, persistence, and network latency may
        # consume it.  At this boundary the fail-closed invariant is the same
        # one enforced at dispatch: the complete Worker runtime plus result
        # transfer margin must still remain.
        required_dispatch_window_ms = (
            planned.resources.max_runtime_seconds
            + KNOWLEDGE_INDEX_DISPATCH_TRANSPORT_MARGIN_SECONDS
        ) * 1000
        if (
            planned.assignment.lease_expires_epoch_ms - now_ms
            < required_dispatch_window_ms
        ):
            raise BoundSourceRevisionPlanningError(
                "source_index_assignment_runtime_window_insufficient"
            )

        access_request = SourceAccessRequest(
            tenant_id=tenant_id,
            project_id=project_id,
            source_revision_id=source_revision_id,
            source_revision_digest=source_revision_digest,
            destination_id=destination.descriptor.destination_id,
            destination_digest=destination.destination_digest,
            source_access_grant_id=planned.source_access_grant_id,
            source_access_grant_digest=planned.source_access_grant_digest,
            operation=GrantOperation.INDEX,
            transformation=GrantTransformation.REDACTED,
            purpose="knowledge-index",
            policy_version=planned.policy_snapshot_id,
            policy_digest=planned.policy_snapshot_digest,
            manifest_id=file_manifest.manifest_id,
            manifest_digest=file_manifest.manifest_digest,
            assignment_id=planned.assignment.assignment_id,
            lease_id=planned.assignment.lease_id,
        )
        resolved_grant = self._grants.resolve_active(access_request)
        if resolved_grant is None:
            raise BoundSourceRevisionPlanningError(
                "source_index_active_grant_required"
            )
        grant = resolved_grant.grant
        if (
            grant.grant_id != planned.source_access_grant_id
            or source_access_grant_digest(grant)
            != planned.source_access_grant_digest
            or grant.tenant_id != tenant_id
            or grant.project_id != project_id
            or grant.source_revision_id != source_revision_id
            or grant.destination_id != destination.descriptor.destination_id
            or grant.operation != GrantOperation.INDEX
            or grant.transformation != GrantTransformation.REDACTED
            or grant.purpose != "knowledge-index"
            or grant.policy_version != planned.policy_snapshot_id
            or grant.policy_snapshot_digest
            != planned.policy_snapshot_digest
            or grant.state != GrantState.ACTIVE
            or grant.expires_at <= now
        ):
            raise BoundSourceRevisionPlanningError(
                "source_index_grant_binding_mismatch"
            )
        if int((grant.expires_at - now).total_seconds() * 1000) < (
            required_dispatch_window_ms
        ):
            raise BoundSourceRevisionPlanningError(
                "source_index_grant_runtime_window_insufficient"
            )

        return {
            "hub_task_id": self._hub_task_id(
                tenant_id=tenant_id,
                project_id=project_id,
                source_revision_id=source_revision_id,
                actor_id=actor_id,
                idempotency_key=idempotency_key,
            ),
            "source_revision_id": source_revision_id,
            "source_revision_digest": source_revision_digest,
            "admission_digest": revision.admission_digest,
            "policy_snapshot_id": planned.policy_snapshot_id,
            "policy_snapshot_digest": planned.policy_snapshot_digest,
            "destination_id": destination.descriptor.destination_id,
            "destination_digest": destination.destination_digest,
            "source_access_grant_id": grant.grant_id,
            "source_access_grant_digest": planned.source_access_grant_digest,
            "files": [item.to_wire() for item in file_manifest.files],
            "resource_budget": planned.resources.to_wire(),
            "assignment": planned.assignment.to_wire(),
            "destination_selection": {
                "worker_id": planned.destination_selection.worker_id,
                "runtime_id": planned.destination_selection.runtime_id,
                "provider_id": planned.destination_selection.provider_id,
                "model_id": planned.destination_selection.model_id,
            },
            "source_scope": revision.connector_type,
            "source_id": revision.source_id,
            **(
                {
                    "source_payload_digest": payload.payload_digest,
                    "source_payload_connection_id": payload.connection_id,
                }
                if payload.payload_digest is not None
                else {}
            ),
            "records": [dict(item) for item in payload.records],
        }

    @staticmethod
    def _require_request_values(**values: str) -> None:
        for name in ("tenant_id", "project_id"):
            if not _OPAQUE_ID.fullmatch(str(values[name] or "")):
                raise BoundSourceRevisionPlanningError(f"{name}_invalid")
        if not _CONNECTION_ID.fullmatch(str(values["connection_id"] or "")):
            raise BoundSourceRevisionPlanningError("connection_id_invalid")
        if not _REVISION_ID.fullmatch(
            str(values["source_revision_id"] or "")
        ):
            raise BoundSourceRevisionPlanningError(
                "source_revision_id_invalid"
            )
        for name in ("source_revision_digest", "content_manifest_digest"):
            if not _DIGEST.fullmatch(str(values[name] or "")):
                raise BoundSourceRevisionPlanningError(f"{name}_invalid")
        for name in ("actor_id", "idempotency_key"):
            value = str(values[name] or "")
            if not value or len(value) > 512 or "\x00" in value:
                raise BoundSourceRevisionPlanningError(f"{name}_invalid")

    def _aware_now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise BoundSourceRevisionPlanningError(
                "source_index_planner_clock_invalid"
            )
        return value.astimezone(timezone.utc)

    @staticmethod
    def _hub_task_id(**coordinates: str) -> str:
        digest = hashlib.sha256(
            json.dumps(
                coordinates,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("ascii")
        ).hexdigest()
        return f"hub_index_{digest}"


__all__ = [
    "BoundSourceIndexAuthority",
    "BoundSourceIndexAuthorityPort",
    "BoundSourceRevisionAuthority",
    "BoundSourceRevisionAuthorityPlanner",
    "BoundSourceRevisionAuthorityPort",
    "BoundSourceRevisionPayload",
    "BoundSourceRevisionPayloadPort",
    "BoundSourceRevisionPlanningError",
]
