"""Database-backed task admission and exact replay across Hub processes."""

from __future__ import annotations

import json
from typing import Callable

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from agent.db_models.tool_plan_receipt import ToolPlanEventDB, ToolPlanReceiptDB


def _engine():
    from agent.database import engine

    return engine


class ToolPlanAuditStore:
    """Content-free scoped audit append; write failures propagate before dispatch."""

    def __init__(self, engine: Callable = _engine):
        self._engine = engine

    def record(self, event) -> None:
        with Session(self._engine()) as session:
            session.add(
                ToolPlanEventDB(
                    tenant_id=event["tenant_id"],
                    project_id=event["project_id"],
                    task_id=event["task_id"],
                    event_json=json.dumps(dict(event), sort_keys=True, allow_nan=False),
                )
            )
            session.commit()


class ToolPlanReceiptStore:
    def __init__(self, engine: Callable = _engine):
        self._engine = engine

    def claim(self, scope_digest: str, request_digest: str) -> tuple[bool, dict]:
        with Session(self._engine()) as session:
            row = ToolPlanReceiptDB(scope_digest=scope_digest, request_digest=request_digest)
            session.add(row)
            try:
                session.commit()
                return True, {}
            except IntegrityError:
                session.rollback()
                existing = session.get(ToolPlanReceiptDB, scope_digest)
                if existing is None:
                    raise ValueError("plan_receipt_conflict")
                if existing.request_digest != request_digest:
                    return False, {"status": "blocked", "reason": "task_plan_rate_limit"}
                if existing.state == "running":
                    # Interrupted/uncertain execution is never auto-retried.
                    return False, {"status": "blocked", "reason": "plan_execution_in_progress_or_uncertain"}
                return False, json.loads(existing.response_json)

    def finish(self, scope_digest: str, request_digest: str, response: dict) -> None:
        encoded = json.dumps(response, sort_keys=True, allow_nan=False)
        if len(encoded.encode()) > 128 * 1024:
            raise ValueError("plan_receipt_result_too_large")
        with Session(self._engine()) as session:
            row = session.get(ToolPlanReceiptDB, scope_digest)
            if row is None or row.request_digest != request_digest or row.state != "running":
                raise ValueError("plan_receipt_binding_mismatch")
            row.state, row.response_json = "complete", encoded
            session.add(row)
            session.commit()
