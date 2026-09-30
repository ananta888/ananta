"""Durable domain outcomes of granted recovery approvals.

The approval row is the outbox marker for recovery materialization. This
collaborator persists bounded dispatcher outcomes on the row and resumes
interrupted recovery effects; request listing and the database engine are
injected by ``ApprovalRequestService``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy import update as sa_update
from sqlmodel import Session

from agent.db_models import ApprovalRequestDB


class ApprovalDomainOutcomeReconciler:
    """Persist and reconcile recovery-approval domain outcomes."""

    def __init__(
        self,
        *,
        engine_provider: Callable[[], Any],
        request_lister: Callable[..., list[ApprovalRequestDB]],
    ) -> None:
        self._engine_provider = engine_provider
        self._request_lister = request_lister

    @staticmethod
    def bounded_domain_outcome(outcome: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key in (
            "status",
            "reason_code",
            "plan_id",
            "approval_request_id",
            "plan_digest",
        ):
            value = str(outcome.get(key) or "").strip()
            if value:
                result[key] = value[:256]
        node_count = outcome.get("node_count")
        if isinstance(node_count, int) and not isinstance(node_count, bool):
            result["node_count"] = max(0, min(node_count, 10_000))
        created = outcome.get("created_task_ids")
        if isinstance(created, list):
            result["created_task_ids"] = [str(value)[:160] for value in created[:256] if str(value).strip()]
        return result

    def persist_domain_outcome(
        self,
        *,
        request_id: str,
        outcome: dict[str, Any],
        restore_pending: bool,
    ) -> ApprovalRequestDB | None:
        """Persist a bounded handler result and keep failed actions retryable."""
        with Session(self._engine_provider()) as session:
            request = session.get(ApprovalRequestDB, str(request_id or ""))
            if request is None:
                return None
            next_scope = {
                **dict(request.scope or {}),
                "decision_outcome": self.bounded_domain_outcome(outcome),
            }
            session.exec(
                sa_update(ApprovalRequestDB)
                .where(ApprovalRequestDB.id == str(request_id or ""))
                .values(scope=next_scope)
            )
            if restore_pending:
                # Never revive a concurrently consumed or expired grant.
                session.exec(
                    sa_update(ApprovalRequestDB)
                    .where(ApprovalRequestDB.id == str(request_id or ""))
                    .where(ApprovalRequestDB.status == "granted")
                    .values(
                        status="pending",
                        decided_at=None,
                        decided_by=None,
                        decision_reason=None,
                    )
                )
            session.commit()
            request = session.get(
                ApprovalRequestDB,
                str(request_id or ""),
            )
            if request is None:
                return None
            session.refresh(request)
            return request

    def reconcile_granted_domain_actions(
        self,
        *,
        limit: int = 64,
    ) -> dict[str, int]:
        """Resume durable recovery effects after a Hub interruption.

        The approval row is the outbox marker: ``granted`` means the exact
        action still needs dispatch, while a ``consumed`` recovery without a
        persisted domain outcome may still need its paused DAG released.
        """

        from agent.services.approval_decision_dispatcher_service import (
            get_approval_decision_dispatcher_service,
        )
        from agent.services.task_recovery_planning_service import (
            RECOVERY_MATERIALIZE_TOOL,
        )

        bounded_limit = max(1, min(int(limit), 256))
        candidates = self._request_lister(
            status="granted",
            tool_name=RECOVERY_MATERIALIZE_TOOL,
            limit=bounded_limit,
        )
        if len(candidates) < bounded_limit:
            consumed = self._request_lister(
                status="consumed",
                tool_name=RECOVERY_MATERIALIZE_TOOL,
                limit=bounded_limit - len(candidates),
            )
            candidates.extend(
                row
                for row in consumed
                if (
                    not dict(row.scope or {}).get("decision_outcome")
                    or str(dict(dict(row.scope or {}).get("decision_outcome") or {}).get("status") or "") == "failed"
                )
            )
        if len(candidates) < bounded_limit:
            denied = self._request_lister(
                status="denied",
                tool_name=RECOVERY_MATERIALIZE_TOOL,
                limit=bounded_limit - len(candidates),
            )
            candidates.extend(
                row
                for row in denied
                if (
                    not dict(row.scope or {}).get("decision_outcome")
                    or str(dict(dict(row.scope or {}).get("decision_outcome") or {}).get("status") or "") == "failed"
                )
            )

        dispatcher = get_approval_decision_dispatcher_service()
        counts = {
            "examined": 0,
            "completed": 0,
            "failed": 0,
            "in_progress": 0,
        }
        for request in candidates[:bounded_limit]:
            if str(request.tool_name or "") != RECOVERY_MATERIALIZE_TOOL:
                continue
            counts["examined"] += 1
            outcome = dispatcher.dispatch(request) or {}
            status = str(outcome.get("status") or "")
            reason_code = str(outcome.get("reason_code") or "")
            if status == "ignored" and reason_code == ("recovery_action_in_progress"):
                counts["in_progress"] += 1
                continue
            if status == "ignored":
                continue
            self.persist_domain_outcome(
                request_id=request.id,
                outcome=outcome,
                restore_pending=False,
            )
            if status == "failed":
                counts["failed"] += 1
            else:
                counts["completed"] += 1
        return counts
