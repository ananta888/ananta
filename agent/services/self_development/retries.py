"""Bounded Hub retries always use a fresh Task, workspace and reserved run."""

from __future__ import annotations

import time
import uuid

from agent.services.self_development.budget import (
    attempt_budget_exhausted,
    session_active,
    task_budget_exhausted,
)


def prepare_retry(item, policy, state, queue):
    attempts = item.setdefault("attempts", [])
    review_failed = str(item.get("reason") or "").startswith("acceptance_review_failed:")
    if (
        not item.get("contract")
        or (item.get("outcome", {}).get("passed") and not review_failed)
        or attempt_budget_exhausted(policy.value, attempts)
        or (policy.root / "stop").exists()
        or not session_active(policy.value, state.get("session"), time.time())
        or task_budget_exhausted(policy.value, state.get("session") or {})
    ):
        return False
    policy.assert_current()
    attempts.append({k: item.get(k) for k in ("task_id", "reason", "outcome", "revision")})
    item.update(task_id=str(uuid.uuid4()), status="ready", reason="retry_pending")
    _cancel_superseded_review(item)
    for key in (
        "assignment",
        "outcome",
        "hashes",
        "revision",
        "worker_url",
        "commit_sha",
        "acceptance_review",
        "acceptance_review_request",
        "source_context",
        "review_reservation",
        "review_task_id",
        "review_assignment",
        "review_verification_command",
    ):
        item.pop(key, None)
    queue.ingest_task(
        task_id=item["task_id"],
        status="todo",
        title=item["title"],
        description=item["description"],
        created_by="self-development-hub",
        source="system",
        team_id=policy.value["team_id"],
        tags=["self-development", item["todo_id"]],
        event_details={"retry_of": attempts[-1]["task_id"], "attempt": len(attempts) + 1},
        extra_fields={
            "task_kind": "coding",
            "required_capabilities": ["coding", "testing", "workflow.adapter.native", "codecompass"],
        },
    )
    return True


def _cancel_superseded_review(item):
    """A retried implementation attempt must never keep a stale review Task alive."""
    review_task_id = item.get("review_task_id")
    if not review_task_id:
        return
    from agent.services.task_runtime_service import update_local_task_status

    update_local_task_status(
        review_task_id,
        "cancelled",
        event_type="self_development_review_superseded",
        event_details={"superseded_by_attempt": item["task_id"]},
    )
