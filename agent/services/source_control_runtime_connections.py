"""Connection creation and content admission commands of the Source Control API runtime.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone

from sqlalchemy.engine import Engine

from agent.repositories.source_control_repository import (
    SQLSourceControlRepository,
)
from agent.services.source_control_operation_store import (
    SQLSourceControlOperationStore,
)
from agent.services.source_control_runtime_support import (
    SourceControlApiRuntimeError,
    operation_key,
    runtime_principal,
    wire_digest,
)
from ananta_contracts.source_control import (
    ConnectionState,
    ConnectorType,
    Sensitivity,
    SourceConnection,
)


class SourceControlConnectionCommands:
    """Resolve connection intents, create connections idempotently, admit content."""

    def __init__(
        self,
        *,
        engine: Engine,
        idempotency: SQLSourceControlOperationStore,
        connection_intents: object | None,
        content_admission: object | None,
    ) -> None:
        self._engine = engine
        self._idempotency = idempotency
        self._connection_intents = connection_intents
        self._admission_port = content_admission

    def validate_connection(
        self, *, principal: object, payload: Mapping[str, object]
    ) -> Mapping[str, object]:
        contract = self._connection_contract(principal, payload)
        return {"valid": True, "connection": contract.to_wire()}

    def create_connection(
        self,
        *,
        principal: object,
        payload: Mapping[str, object],
        idempotency_key: str,
    ) -> Mapping[str, object]:
        actor = runtime_principal(principal)
        contract, resolved = self._resolved_connection(principal, payload)
        request_digest = wire_digest(
            {
                "operation": "create_connection",
                "scope": [actor.tenant_id, actor.project_id, actor.subject_id],
                "connection": contract.to_wire(),
            }
        )
        key = operation_key(
            "create", actor.tenant_id, idempotency_key
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
        record = SQLSourceControlRepository(
            self._engine
        ).save_connection_with_selector(
            contract,
            resolved.binding(
                connection_id=contract.connection_id,
                tenant_id=contract.tenant_id,
                project_id=contract.project_id,
                owner_id=contract.owner_id,
            ),
        )
        result = {
            "connection": record.contract.to_wire(),
            "version": int(record.lock_version),
        }
        self._idempotency.complete(
            idempotency_key=key,
            plan_digest=request_digest,
            result=result,
        )
        return result

    def validate_content_admission(
        self, *, principal: object, payload: Mapping[str, object]
    ) -> Mapping[str, object]:
        actor = runtime_principal(principal)
        return dict(
            self._content_admission().validate(
                tenant_id=actor.tenant_id,
                project_id=actor.project_id,
                actor_id=actor.subject_id,
                payload=payload,
            )
        )

    def create_content_admission(
        self,
        *,
        principal: object,
        payload: Mapping[str, object],
        idempotency_key: str,
    ) -> Mapping[str, object]:
        actor = runtime_principal(principal)
        return dict(
            self._content_admission().admit(
                tenant_id=actor.tenant_id,
                project_id=actor.project_id,
                actor_id=actor.subject_id,
                payload=payload,
                idempotency_key=idempotency_key,
            )
        )

    def _connection_contract(
        self, principal: object, payload: Mapping[str, object]
    ) -> SourceConnection:
        return self._resolved_connection(principal, payload)[0]

    def _resolved_connection(
        self, principal: object, payload: Mapping[str, object]
    ) -> tuple[SourceConnection, object]:
        actor = runtime_principal(principal)
        if self._connection_intents is None:
            raise SourceControlApiRuntimeError(
                "source_control_connection_catalog_unavailable",
                status_code=503,
            )
        try:
            resolved = self._connection_intents.resolve(
                principal=actor,
                payload=payload,
            )
            contract = SourceConnection.create(
                tenant_id=actor.tenant_id,
                project_id=actor.project_id,
                owner_id=actor.subject_id,
                connector_type=ConnectorType(
                    str(resolved.connector_type)
                ),
                connection_identity_digest=str(
                    resolved.connection_identity_digest
                ),
                display_name=str(resolved.display_name),
                sensitivity=Sensitivity(str(resolved.sensitivity)),
                state=ConnectionState.DRAFT,
                created_at=datetime.now(timezone.utc),
            )
            return contract, resolved
        except (KeyError, TypeError, ValueError) as exc:
            reason_code = str(getattr(exc, "reason_code", "") or "")
            if reason_code:
                raise SourceControlApiRuntimeError(
                    reason_code,
                    status_code=int(getattr(exc, "status_code", 400)),
                ) from exc
            raise SourceControlApiRuntimeError(
                "source_connection_invalid"
            ) from exc

    def _content_admission(self):
        if self._admission_port is None:
            raise SourceControlApiRuntimeError(
                "content_admission_unavailable", status_code=503
            )
        return self._admission_port


__all__ = ["SourceControlConnectionCommands"]
