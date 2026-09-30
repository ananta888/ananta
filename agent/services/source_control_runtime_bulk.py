"""Bulk-mutation adapters and request parsing of the Source Control API runtime.

``BulkProjectionAuthorization`` authorizes each target against the scoped
projection's next actions; ``BulkLifecycleMutation`` executes one target via
the lifecycle service or the configured operations port.
"""

from __future__ import annotations

from collections.abc import Mapping

from agent.services.source_control_bulk_service import (
    BulkAuthorization,
    BulkTarget,
)
from agent.services.source_control_projection_service import (
    SourceControlPrincipal,
    SourceControlProjectionService,
)
from agent.services.source_control_read_repository import (
    SQLSourceControlReadRepository,
)
from agent.services.source_control_runtime_support import (
    SourceControlApiRuntimeError,
)
from agent.services.source_index_lifecycle_service import (
    SourceIndexLifecycleScope,
    SourceIndexLifecycleService,
)


class BulkProjectionAuthorization:
    def __init__(
        self,
        projection: SourceControlProjectionService,
        principal: SourceControlPrincipal,
    ) -> None:
        self._projection = projection
        self._principal = principal

    def authorize(
        self,
        *,
        tenant_id: str,
        project_id: str,
        actor_id: str,
        mutation: str,
        target: BulkTarget,
    ) -> BulkAuthorization:
        if (
            tenant_id != self._principal.tenant_id
            or project_id != self._principal.project_id
            or actor_id != self._principal.subject_id
        ):
            return BulkAuthorization(False, "scope_mismatch", "")
        try:
            projection = self._projection.get(
                principal=self._principal,
                connection_id=target.resource_id,
            )
        except Exception:
            return BulkAuthorization(False, "source_control_not_found", "")
        actions = set(getattr(projection, "next_actions", ()))
        allowed = mutation in actions or (
            mutation == "disable" and "disable" in actions
        )
        return BulkAuthorization(
            allowed=allowed,
            reason_code="authorized" if allowed else "policy_denied",
            current_etag=projection.etag,
        )


class BulkLifecycleMutation:
    def __init__(
        self,
        *,
        lifecycle: SourceIndexLifecycleService,
        reads: SQLSourceControlReadRepository,
        principal: SourceControlPrincipal,
        operations: object | None,
    ) -> None:
        self._lifecycle = lifecycle
        self._reads = reads
        self._principal = principal
        self._operations = operations

    def execute(
        self,
        *,
        tenant_id: str,
        project_id: str,
        actor_id: str,
        mutation: str,
        target: BulkTarget,
        idempotency_key: str,
    ) -> Mapping[str, object]:
        if mutation == "disable":
            version = self._reads.connection_version(
                tenant_id=tenant_id,
                project_id=project_id,
                connection_id=target.resource_id,
            )
            updated = self._lifecycle.disable(
                scope=SourceIndexLifecycleScope(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    actor_id=actor_id,
                    roles=self._principal.roles,
                ),
                connection_id=target.resource_id,
                expected_version=version,
            )
            return {"status": "completed", "version": updated}
        execute = getattr(self._operations, "execute", None)
        if callable(execute):
            return dict(
                execute(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    actor_id=actor_id,
                    mutation=mutation,
                    resource_id=target.resource_id,
                    idempotency_key=idempotency_key,
                )
            )
        return {
            "status": "failed",
            "reason_code": "source_control_operation_unavailable",
        }


def parse_bulk_request(
    payload: Mapping[str, object],
) -> tuple[str, tuple[BulkTarget, ...]]:
    if set(payload) - {"mutation", "targets", "dry_run"}:
        raise SourceControlApiRuntimeError("bulk_request_fields_forbidden")
    mutation = payload.get("mutation")
    raw_targets = payload.get("targets")
    if not isinstance(mutation, str) or not isinstance(raw_targets, list):
        raise SourceControlApiRuntimeError("bulk_request_invalid")
    targets: list[BulkTarget] = []
    for raw in raw_targets:
        if not isinstance(raw, Mapping) or set(raw) != {
            "resource_id",
            "expected_etag",
        }:
            raise SourceControlApiRuntimeError("bulk_target_invalid")
        targets.append(
            BulkTarget(
                resource_id=str(raw["resource_id"]),
                expected_etag=str(raw["expected_etag"]),
            )
        )
    return mutation, tuple(targets)


def parse_bulk_plan_replay(
    plan: Mapping[str, object],
) -> tuple[str, tuple[BulkTarget, ...]]:
    mutation = plan.get("mutation")
    items = plan.get("items")
    if not isinstance(mutation, str) or not isinstance(items, list):
        raise SourceControlApiRuntimeError("bulk_plan_invalid")
    targets: list[BulkTarget] = []
    for item in items:
        if not isinstance(item, Mapping):
            raise SourceControlApiRuntimeError("bulk_plan_invalid")
        targets.append(
            BulkTarget(
                resource_id=str(item.get("resource_id", "")),
                expected_etag=str(item.get("expected_etag", "")),
            )
        )
    return mutation, tuple(targets)


__all__ = [
    "BulkLifecycleMutation",
    "BulkProjectionAuthorization",
    "parse_bulk_plan_replay",
    "parse_bulk_request",
]
