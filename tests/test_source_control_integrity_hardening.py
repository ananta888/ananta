from __future__ import annotations

import hashlib

import pytest
from sqlmodel import Session, create_engine

from agent.db_models.context_policy_lifecycle import ContextPolicyVersionDB
from agent.db_models.source_control import (
    SourceAccessGrantDB,
    SourceControlBulkTargetCheckpointDB,
    SourceControlJobEventOutboxDB,
    SourceControlOperationDB,
    SourceControlPurgeApprovalDB,
)
from agent.services.effective_source_access_service import (
    EffectiveSourceRevision,
)
from agent.services.source_control_api_runtime import (
    SQLSourceControlJobEventRepository,
    SQLSourceControlOperationStore,
)
from agent.services.source_control_bulk_service import (
    BulkAuthorization,
    BulkMutationPlan,
    BulkPlanItem,
    BulkTarget,
    SourceControlBulkService,
)
from agent.services.source_control_production_adapters import (
    PersistentGrantEffectivePolicy,
    SourceControlProductionAdapterError,
    derive_policy_snapshot_id,
)
from agent.services.source_control_purge_approval import (
    SQLSourceControlPurgeApprovalStore,
)
from agent.services.source_index_lifecycle_service import (
    SourceIndexLifecycleError,
)
from ananta_contracts.source_control import (
    DestinationDescriptor,
    GrantOperation,
    GrantTransformation,
    ProviderLocation,
)


def _engine():
    return create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )


def test_outbox_cursor_survives_timestamp_and_generation_collision() -> None:
    engine = _engine()
    SourceControlJobEventOutboxDB.__table__.create(engine)
    repository = SQLSourceControlJobEventRepository(engine)
    for event_id, job_id in (("event-a", "job-a"), ("event-b", "job-b")):
        repository.append(
            event_id=event_id,
            tenant_id="tenant-example",
            project_id="project-example",
            resource_id="source-example",
            job_id=job_id,
            event_type="index_progress",
            status="running",
            reason_code=None,
            trace_id=event_id,
            occurred_at_epoch=1_800_000_000.0,
        )

    first = repository.read_after(
        tenant_id="tenant-example",
        project_id="project-example",
        after_sequence=0,
        limit=1,
    )
    second = repository.read_after(
        tenant_id="tenant-example",
        project_id="project-example",
        after_sequence=first[-1].sequence,
        limit=1,
    )

    assert [event.event_id for event in first + second] == [
        "event-a",
        "event-b",
    ]
    assert second[0].sequence > first[0].sequence


class _AllowAll:
    def authorize(self, *, target: BulkTarget, **_kwargs):
        return BulkAuthorization(True, "authorized", target.expected_etag)


class _CrashOnceIdempotentMutations:
    def __init__(self) -> None:
        self.results = {}
        self.side_effects = {}
        self.crashed = False

    def execute(self, *, target: BulkTarget, idempotency_key: str, **_kwargs):
        if idempotency_key in self.results:
            return self.results[idempotency_key]
        self.side_effects[idempotency_key] = (
            self.side_effects.get(idempotency_key, 0) + 1
        )
        result = {
            "resource_id": target.resource_id,
            "status": "completed",
        }
        self.results[idempotency_key] = result
        if not self.crashed:
            self.crashed = True
            raise SystemExit("simulated_process_crash")
        return result


def test_bulk_reclaims_expired_lease_and_resumes_executing_target() -> None:
    engine = _engine()
    SourceControlOperationDB.__table__.create(engine)
    SourceControlBulkTargetCheckpointDB.__table__.create(engine)
    now = [100.0]
    store = SQLSourceControlOperationStore(
        engine,
        clock=lambda: now[0],
        lease_seconds=5.0,
    )
    mutations = _CrashOnceIdempotentMutations()
    service = SourceControlBulkService(
        authorization=_AllowAll(),
        mutations=mutations,
        idempotency=store,
    )
    item = BulkPlanItem(
        resource_id="source-example",
        expected_etag="a" * 64,
        current_etag="a" * 64,
        allowed=True,
        reason_code="authorized",
    )
    plan = BulkMutationPlan(
        schema="ananta.source-control.bulk-plan.v1",
        tenant_id="tenant-example",
        project_id="project-example",
        actor_id="actor-example",
        mutation="refresh",
        items=(item,),
        plan_digest="b" * 64,
    )

    with pytest.raises(SystemExit, match="simulated_process_crash"):
        service.execute(
            plan=plan,
            supplied_plan_digest=plan.plan_digest,
            idempotency_key="bulk-crash-example",
        )
    now[0] += 6.0
    result = service.execute(
        plan=plan,
        supplied_plan_digest=plan.plan_digest,
        idempotency_key="bulk-crash-example",
    )

    assert result["results"][0]["status"] == "completed"
    assert tuple(mutations.side_effects.values()) == (1,)


def test_purge_approval_is_digest_bound_expiring_and_one_time() -> None:
    engine = _engine()
    SourceControlPurgeApprovalDB.__table__.create(engine)
    now = [100.0]
    store = SQLSourceControlPurgeApprovalStore(
        engine,
        clock=lambda: now[0],
        claim_lease_seconds=5.0,
    )
    digest = hashlib.sha256(b"bound-purge").hexdigest()
    approval_id = store.issue(
        tenant_id="tenant-example",
        project_id="project-example",
        action="purge",
        object_type="knowledge_index",
        object_id="index-example",
        request_digest=digest,
        approved_by="security-reviewer",
        expires_at_epoch=200.0,
        approval_id="approval-example",
    )

    with pytest.raises(
        SourceIndexLifecycleError, match="binding_mismatch"
    ):
        store.claim(
            approval_id=approval_id,
            tenant_id="tenant-example",
            project_id="project-example",
            action="purge",
            object_type="knowledge_index",
            object_id="index-example",
            request_digest="f" * 64,
            claim_id="operation-example",
        )
    store.claim(
        approval_id=approval_id,
        tenant_id="tenant-example",
        project_id="project-example",
        action="purge",
        object_type="knowledge_index",
        object_id="index-example",
        request_digest=digest,
        claim_id="operation-example",
    )
    store.consume(
        approval_id=approval_id,
        request_digest=digest,
        claim_id="operation-example",
    )
    with pytest.raises(
        SourceIndexLifecycleError, match="already_consumed"
    ):
        store.claim(
            approval_id=approval_id,
            tenant_id="tenant-example",
            project_id="project-example",
            action="purge",
            object_type="knowledge_index",
            object_id="index-example",
            request_digest=digest,
            claim_id="different-operation",
        )


def test_effective_policy_uses_real_snapshot_digest_and_rejects_tamper() -> None:
    engine = _engine()
    ContextPolicyVersionDB.__table__.create(engine)
    SourceAccessGrantDB.__table__.create(engine)
    policy_digest = hashlib.sha256(b"real-policy-document").hexdigest()
    snapshot_id = derive_policy_snapshot_id(
        tenant_id="tenant-example",
        project_id="project-example",
        policy_id="policy-example",
        version=1,
        policy_digest=policy_digest,
    )
    with Session(engine) as db:
        db.add(
            ContextPolicyVersionDB(
                record_id="policy-record-example",
                tenant_id="tenant-example",
                project_id="project-example",
                policy_id="policy-example",
                version=1,
                state="active",
                document_json={},
                policy_digest=policy_digest,
                etag="e" * 64,
                created_by="actor-example",
                created_at="2026-01-01T00:00:00Z",
                updated_by="actor-example",
                updated_at="2026-01-01T00:00:00Z",
            )
        )
        db.add(
            SourceAccessGrantDB(
                grant_id="grant-example",
                grant_family_id="grant-family-example",
                grant_version=1,
                tenant_id="tenant-example",
                project_id="project-example",
                owner_id="actor-example",
                source_revision_id="revision-example",
                destination_id="destination-example",
                operation="index",
                transformation="redacted",
                purpose="knowledge-index",
                policy_version=snapshot_id,
                policy_snapshot_digest=policy_digest,
                state="active",
                issued_at_epoch=100.0,
                expires_at_epoch=1_900_000_000.0,
                lock_version=1,
                updated_at_epoch=100.0,
            )
        )
        db.commit()
    source = EffectiveSourceRevision(
        source_revision_id="revision-example",
        tenant_id="tenant-example",
        project_id="project-example",
        source_type="workspace",
        sensitivity="project_internal",
        revision_digest="a" * 64,
    )
    destination = DestinationDescriptor.create(
        worker_id="worker-example",
        worker_kind="llm",
        runtime_id="runtime-example",
        runtime_kind="remote_api",
        provider_id="provider-example",
        model_id="model-example",
        model_class="model-class-example",
        provider_location=ProviderLocation.EXTERNAL_REGION,
        data_residency="region-example",
    )
    policy = PersistentGrantEffectivePolicy(engine, clock=lambda: 200.0)

    decision = policy.evaluate(
        source_revision=source,
        destination=destination,
        operation=GrantOperation.INDEX,
        transformation=GrantTransformation.REDACTED,
        purpose="knowledge-index",
    )
    assert decision.policy_digest == policy_digest

    with Session(engine) as db:
        row = db.get(SourceAccessGrantDB, "grant-example")
        assert row is not None
        row.policy_snapshot_digest = "f" * 64
        db.add(row)
        db.commit()
    with pytest.raises(
        SourceControlProductionAdapterError,
        match="policy_snapshot_binding_mismatch",
    ):
        policy.evaluate(
            source_revision=source,
            destination=destination,
            operation=GrantOperation.INDEX,
            transformation=GrantTransformation.REDACTED,
            purpose="knowledge-index",
        )
