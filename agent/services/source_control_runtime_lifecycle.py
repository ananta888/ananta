"""Index lifecycle commands of the Source Control API runtime.

Run history, index comparison, activate/rollback/disable/tombstone/purge
mutations with idempotency receipts, and bulk plan/execute.
"""

from __future__ import annotations

from collections.abc import Mapping

from agent.services.source_control_bulk_service import SourceControlBulkService
from agent.services.source_control_operation_store import (
    SQLSourceControlOperationStore,
)
from agent.services.source_control_projection_service import (
    SourceControlPrincipal,
    SourceControlProjectionService,
)
from agent.services.source_control_read_repository import (
    SQLSourceControlReadRepository,
)
from agent.services.source_control_runtime_bulk import (
    BulkLifecycleMutation,
    BulkProjectionAuthorization,
    parse_bulk_plan_replay,
    parse_bulk_request,
)
from agent.services.source_control_runtime_support import (
    SourceControlApiRuntimeError,
    lifecycle_scope,
    operation_key,
    runtime_principal,
    wire,
    wire_digest,
)
from agent.services.source_index_lifecycle_service import (
    SourceIndexLifecycleService,
)


class SourceControlIndexLifecycleCommands:
    """Scoped index lifecycle reads and idempotent lifecycle mutations."""

    def __init__(
        self,
        *,
        reads: SQLSourceControlReadRepository,
        projection: SourceControlProjectionService,
        lifecycle: SourceIndexLifecycleService,
        idempotency: SQLSourceControlOperationStore,
        operations: object | None,
    ) -> None:
        self._reads = reads
        self._projection = projection
        self._lifecycle = lifecycle
        self._idempotency = idempotency
        self._operations = operations

    def run_history(
        self,
        *,
        principal: object,
        connection_id: str,
        cursor: str | None,
        limit: int,
    ) -> Mapping[str, object]:
        actor = runtime_principal(principal)
        value = wire(
            self._lifecycle.history(
                scope=lifecycle_scope(principal),
                connection_id=connection_id,
                cursor=cursor,
                limit=limit,
            )
        )
        for item in value.get("items", []):
            version = self._reads.index_version(
                tenant_id=actor.tenant_id,
                project_id=actor.project_id,
                knowledge_index_id=str(item["knowledge_index_id"]),
            )
            item["etag"] = self._reads.index_etag(version)
        return value

    def compare_indices(
        self,
        *,
        principal: object,
        left_index_id: str,
        right_index_id: str,
    ) -> Mapping[str, object]:
        return self._lifecycle.compare(
            scope=lifecycle_scope(principal),
            left_index_id=left_index_id,
            right_index_id=right_index_id,
        )

    def mutate(
        self,
        *,
        principal: object,
        operation: str,
        resource_id: str,
        if_match: str,
        idempotency_key: str,
        payload: Mapping[str, object],
    ) -> Mapping[str, object]:
        actor = runtime_principal(principal)
        request_digest = wire_digest(
            {
                "operation": operation,
                "resource_id": resource_id,
                "if_match": if_match,
                "payload": payload,
                "scope": [actor.tenant_id, actor.project_id, actor.subject_id],
            }
        )
        key = operation_key(
            "lifecycle", actor.tenant_id, idempotency_key
        )
        claim = self._idempotency.claim(
            idempotency_key=key, plan_digest=request_digest
        )
        if claim.state == "completed":
            return dict(claim.result or {})
        if claim.state == "in_progress":
            raise SourceControlApiRuntimeError(
                "idempotency_in_progress", status_code=409
            )
        scope = lifecycle_scope(principal)
        if operation in {"activate", "rollback"}:
            generation = _etag_number(if_match, "active")
            pointer = (
                self._lifecycle.activate(
                    scope=scope,
                    knowledge_index_id=resource_id,
                    expected_generation=generation,
                )
                if operation == "activate"
                else self._lifecycle.rollback(
                    scope=scope,
                    target_index_id=resource_id,
                    expected_generation=generation,
                )
            )
            result: dict[str, object] = {
                "operation": operation,
                "resource_id": resource_id,
                "result": wire(pointer),
            }
        elif operation == "disable":
            projection = self._projection.get(
                principal=actor, connection_id=resource_id
            )
            self._projection.assert_if_match(projection, if_match)
            version = self._reads.connection_version(
                tenant_id=actor.tenant_id,
                project_id=actor.project_id,
                connection_id=resource_id,
            )
            updated = self._lifecycle.disable(
                scope=scope,
                connection_id=resource_id,
                expected_version=version,
            )
            result = {
                "operation": operation,
                "resource_id": resource_id,
                "result": {"version": updated},
            }
        elif operation == "tombstone":
            version = self._assert_index_etag(actor, resource_id, if_match)
            updated = self._lifecycle.tombstone(
                scope=scope,
                knowledge_index_id=resource_id,
                expected_version=version,
            )
            result = {
                "operation": operation,
                "resource_id": resource_id,
                "result": {
                    "version": updated,
                    "etag": self._reads.index_etag(updated),
                },
            }
        elif operation == "purge":
            version = self._assert_index_etag(actor, resource_id, if_match)
            approval_id = payload.get("approval_id")
            if approval_id is not None and not isinstance(approval_id, str):
                raise SourceControlApiRuntimeError("approval_id_invalid")
            self._lifecycle.purge(
                scope=scope,
                knowledge_index_id=resource_id,
                expected_version=version,
                approval_id=approval_id,
                approval_claim_id=key,
            )
            result = {
                "operation": operation,
                "resource_id": resource_id,
                "result": {"purged": True},
            }
        else:
            raise SourceControlApiRuntimeError(
                "source_control_operation_invalid"
            )
        self._idempotency.complete(
            idempotency_key=key,
            plan_digest=request_digest,
            result=result,
        )
        return result

    def bulk_plan(
        self, *, principal: object, payload: Mapping[str, object]
    ) -> Mapping[str, object]:
        mutation, targets = parse_bulk_request(payload)
        actor = runtime_principal(principal)
        return wire(
            self._bulk(actor).plan(
                tenant_id=actor.tenant_id,
                project_id=actor.project_id,
                actor_id=actor.subject_id,
                mutation=mutation,
                targets=targets,
                dry_run=True,
            )
        )

    def bulk_execute(
        self,
        *,
        principal: object,
        payload: Mapping[str, object],
        idempotency_key: str,
    ) -> Mapping[str, object]:
        plan_payload = payload.get("plan")
        if not isinstance(plan_payload, Mapping):
            raise SourceControlApiRuntimeError("bulk_plan_invalid")
        mutation, targets = parse_bulk_plan_replay(plan_payload)
        actor = runtime_principal(principal)
        service = self._bulk(actor)
        plan = service.plan(
            tenant_id=actor.tenant_id,
            project_id=actor.project_id,
            actor_id=actor.subject_id,
            mutation=mutation,
            targets=targets,
            dry_run=True,
        )
        supplied = payload.get("supplied_plan_digest")
        if not isinstance(supplied, str):
            raise SourceControlApiRuntimeError("bulk_plan_digest_invalid")
        result = dict(
            service.execute(
                plan=plan,
                supplied_plan_digest=supplied,
                idempotency_key=operation_key(
                    "bulk", actor.tenant_id, idempotency_key
                ),
            )
        )
        result.pop("schema", None)
        return result

    def _bulk(
        self, actor: SourceControlPrincipal
    ) -> SourceControlBulkService:
        return SourceControlBulkService(
            authorization=BulkProjectionAuthorization(self._projection, actor),
            mutations=BulkLifecycleMutation(
                lifecycle=self._lifecycle,
                reads=self._reads,
                principal=actor,
                operations=self._operations,
            ),
            idempotency=self._idempotency,
        )

    def _assert_index_etag(
        self,
        actor: SourceControlPrincipal,
        knowledge_index_id: str,
        if_match: str,
    ) -> int:
        version = self._reads.index_version(
            tenant_id=actor.tenant_id,
            project_id=actor.project_id,
            knowledge_index_id=knowledge_index_id,
        )
        if if_match != self._reads.index_etag(version):
            raise SourceControlApiRuntimeError(
                "index_version_conflict", status_code=412
            )
        return version


def _etag_number(value: str, namespace: str) -> int:
    normalized = value.strip()
    if normalized.startswith("W/"):
        normalized = normalized[2:]
    normalized = normalized.strip('"')
    prefix = f"{namespace}:"
    if normalized.startswith(prefix):
        normalized = normalized[len(prefix) :]
    try:
        parsed = int(normalized)
    except ValueError as exc:
        raise SourceControlApiRuntimeError(
            "if_match_invalid", status_code=412
        ) from exc
    if parsed < 0:
        raise SourceControlApiRuntimeError(
            "if_match_invalid", status_code=412
        )
    return parsed


