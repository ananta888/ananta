"""Idempotency receipts and audit rows for grant admin mutations.

``GrantMutationReceipts`` derives the operation key/request digest of a
mutation and replays a completed receipt; the row builders create the
append-only audit and operation receipt rows. The grant admin service only
sequences them (SRP).
"""

from __future__ import annotations

import json
from typing import Mapping

from sqlalchemy.engine import Engine
from sqlmodel import Session

from agent.db_models.source_control import (
    SourceAccessGrantAuditDB,
    SourceAccessGrantDB,
    SourceControlOperationDB,
)
from agent.services.source_control_grant_admin_contracts import (
    GrantAdminActor,
    GrantView,
    SourceControlGrantAdminError,
)
from agent.services.source_control_grant_admin_rules import digest


class GrantMutationReceipts:
    """Derives mutation identities and replays completed receipts."""

    def __init__(self, *, engine: Engine) -> None:
        self._engine = engine

    @staticmethod
    def mutation_identity(
        *,
        actor: GrantAdminActor,
        operation: str,
        idempotency_key: str,
        payload: Mapping[str, object],
    ) -> tuple[str, str]:
        key = str(idempotency_key or "").strip()
        if not key or len(key) > 255:
            raise SourceControlGrantAdminError(
                "grant_idempotency_key_required", status_code=428
            )
        request_digest = digest(
            {
                "schema": "ananta.source-control.grant-mutation.v1",
                "operation": operation,
                "actor_id": actor.subject_id,
                "tenant_id": actor.tenant_id,
                "project_id": actor.project_id,
                "payload": dict(payload),
            }
        )
        operation_key = "gadm_" + digest(
            {
                "operation": operation,
                "tenant_id": actor.tenant_id,
                "project_id": actor.project_id,
                "idempotency_key": key,
            }
        )
        return operation_key, request_digest

    def replay(
        self, *, operation_key: str, request_digest: str
    ) -> GrantView | None:
        with Session(self._engine) as db:
            receipt = db.get(SourceControlOperationDB, operation_key)
        if receipt is None:
            return None
        if receipt.request_digest != request_digest:
            raise SourceControlGrantAdminError(
                "grant_idempotency_key_conflict", status_code=409
            )
        if receipt.state != "completed" or not receipt.result_json:
            raise SourceControlGrantAdminError(
                "grant_mutation_in_progress", status_code=409
            )
        try:
            payload = json.loads(receipt.result_json)
        except (TypeError, ValueError) as exc:
            raise SourceControlGrantAdminError(
                "grant_idempotency_result_invalid", status_code=500
            ) from exc
        if not isinstance(payload, Mapping):
            raise SourceControlGrantAdminError(
                "grant_idempotency_result_invalid", status_code=500
            )
        return GrantView.from_dict(payload)


def grant_audit_row(
    *,
    row: SourceAccessGrantDB,
    actor: GrantAdminActor,
    action: str,
    from_state: str | None,
    to_state: str,
    reason_code: str,
    lock_version: int,
    occurred_at: float,
) -> SourceAccessGrantAuditDB:
    audit_id = "audit_" + digest(
        {
            "grant_id": row.grant_id,
            "action": action,
            "lock_version": lock_version,
            "actor_id": actor.subject_id,
            "occurred_at_epoch": occurred_at,
        }
    )
    return SourceAccessGrantAuditDB(
        audit_id=audit_id,
        grant_id=row.grant_id,
        tenant_id=actor.tenant_id,
        project_id=actor.project_id,
        owner_id=actor.subject_id,
        action=action,
        from_state=from_state,
        to_state=to_state,
        reason_code=reason_code,
        grant_lock_version=lock_version,
        occurred_at_epoch=occurred_at,
    )


def grant_operation_row(
    *,
    operation_key: str,
    request_digest: str,
    operation: str,
    result: GrantView,
    occurred_at: float,
) -> SourceControlOperationDB:
    return SourceControlOperationDB(
        idempotency_key=operation_key,
        request_digest=request_digest,
        operation=operation,
        state="completed",
        result_json=json.dumps(
            result.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ),
        created_at_epoch=occurred_at,
        updated_at_epoch=occurred_at,
    )
