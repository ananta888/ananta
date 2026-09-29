from __future__ import annotations

from dataclasses import replace

import pytest

from agent.services.source_control_bulk_service import (
    BulkAuthorization,
    BulkIdempotencyClaim,
    BulkTargetCheckpoint,
    BulkTarget,
    SourceControlBulkError,
    SourceControlBulkService,
)
from agent.services.source_control_job_events import (
    SourceControlJobEvent,
    SourceControlJobEventError,
    SourceControlJobEventService,
)


class _Authorization:
    def __init__(self) -> None:
        self.etags = {
            "source-one": "a" * 64,
            "source-two": "b" * 64,
        }

    def authorize(self, *, target, **kwargs):
        return BulkAuthorization(
            allowed=target.resource_id != "source-two",
            reason_code=(
                "authorized"
                if target.resource_id != "source-two"
                else "policy_denied"
            ),
            current_etag=self.etags[target.resource_id],
        )


class _Mutations:
    def __init__(self) -> None:
        self.calls = []

    def execute(self, **kwargs):
        self.calls.append(kwargs)
        return {"status": "accepted"}


class _Idempotency:
    def __init__(self) -> None:
        self.values = {}
        self.claimed = set()
        self.checkpoints = {}

    def claim(self, *, idempotency_key, plan_digest):
        existing = self.values.get(idempotency_key)
        if existing is not None:
            if existing["plan_digest"] != plan_digest:
                raise SourceControlBulkError("idempotency_key_conflict")
            return BulkIdempotencyClaim(
                state="completed",
                result=existing["result"],
            )
        if idempotency_key in self.claimed:
            return BulkIdempotencyClaim(state="in_progress")
        self.claimed.add(idempotency_key)
        return BulkIdempotencyClaim(
            state="claimed",
            claim_token="claim-example",
            checkpoints=tuple(self.checkpoints.values()),
        )

    def begin_target(
        self,
        *,
        idempotency_key,
        plan_digest,
        claim_token,
        target_ordinal,
        resource_id,
        target_digest,
    ):
        checkpoint = BulkTargetCheckpoint(
            target_ordinal=target_ordinal,
            resource_id=resource_id,
            target_digest=target_digest,
            state="executing",
        )
        self.checkpoints[target_ordinal] = checkpoint
        return checkpoint

    def complete_target(
        self,
        *,
        idempotency_key,
        plan_digest,
        claim_token,
        target_ordinal,
        target_digest,
        result,
    ):
        previous = self.checkpoints[target_ordinal]
        checkpoint = replace(
            previous,
            state="completed",
            result=result,
        )
        self.checkpoints[target_ordinal] = checkpoint
        return checkpoint

    def complete(
        self, *, idempotency_key, plan_digest, claim_token, result
    ):
        self.values[idempotency_key] = {
            "plan_digest": plan_digest,
            "result": result,
        }
        self.claimed.discard(idempotency_key)


def test_bulk_requires_dry_run_and_reauthorizes_each_target() -> None:
    mutations = _Mutations()
    service = SourceControlBulkService(
        authorization=_Authorization(),
        mutations=mutations,
        idempotency=_Idempotency(),
    )
    with pytest.raises(SourceControlBulkError, match="dry_run_required"):
        service.plan(
            tenant_id="tenant-example",
            project_id="project-example",
            actor_id="actor-example",
            mutation="refresh",
            targets=[BulkTarget("source-one", "a" * 64)],
            dry_run=False,
        )

    plan = service.plan(
        tenant_id="tenant-example",
        project_id="project-example",
        actor_id="actor-example",
        mutation="refresh",
        targets=[
            BulkTarget("source-one", "a" * 64),
            BulkTarget("source-two", "b" * 64),
        ],
        dry_run=True,
    )
    result = service.execute(
        plan=plan,
        supplied_plan_digest=plan.plan_digest,
        idempotency_key="bulk-example",
    )

    assert len(mutations.calls) == 1
    assert result["results"][0]["status"] == "accepted"
    assert result["results"][1]["reason_code"] == "policy_denied"


def test_bulk_replay_does_not_repeat_mutation() -> None:
    mutations = _Mutations()
    service = SourceControlBulkService(
        authorization=_Authorization(),
        mutations=mutations,
        idempotency=_Idempotency(),
    )
    plan = service.plan(
        tenant_id="tenant-example",
        project_id="project-example",
        actor_id="actor-example",
        mutation="disable",
        targets=[BulkTarget("source-one", "a" * 64)],
        dry_run=True,
    )

    first = service.execute(
        plan=plan,
        supplied_plan_digest=plan.plan_digest,
        idempotency_key="bulk-example",
    )
    second = service.execute(
        plan=plan,
        supplied_plan_digest=plan.plan_digest,
        idempotency_key="bulk-example",
    )

    assert first == second
    assert len(mutations.calls) == 1


def _event(sequence: int = 1) -> SourceControlJobEvent:
    return SourceControlJobEvent(
        event_id=f"event-{sequence}",
        sequence=sequence,
        tenant_id="tenant-example",
        project_id="project-example",
        resource_id="source-example",
        job_id="job-example",
        event_type="index_progress",
        status="running",
        reason_code=None,
        trace_id="trace-example",
        occurred_at="2026-01-01T00:00:00Z",
    )


class _Events:
    def __init__(self, events) -> None:
        self.events = events

    def read_after(self, **kwargs):
        return self.events


def test_event_projection_is_content_free_and_ordered() -> None:
    result = SourceControlJobEventService(_Events([_event(1), _event(2)])).poll(
        tenant_id="tenant-example",
        project_id="project-example",
    )

    assert result["next_sequence"] == 2
    assert "content" not in result["events"][0]
    assert "path" not in result["events"][0]


def test_cross_tenant_or_replayed_event_is_rejected() -> None:
    cross_tenant = replace(_event(), tenant_id="other-tenant")
    with pytest.raises(SourceControlJobEventError):
        SourceControlJobEventService(_Events([cross_tenant])).poll(
            tenant_id="tenant-example",
            project_id="project-example",
        )
    with pytest.raises(SourceControlJobEventError):
        SourceControlJobEventService(_Events([_event(1), _event(1)])).poll(
            tenant_id="tenant-example",
            project_id="project-example",
        )
