from __future__ import annotations

from dataclasses import replace

from ananta_contracts.model_intelligence import ArtifactRef
from agent.services.model_intelligence_security_policy import (
    ModelIntelligenceAccessPolicy,
    ModelIntelligenceAction,
    ModelIntelligencePrincipal,
    ModelIntelligenceResourceKind,
    ModelIntelligenceResourceScope,
    ModelIntelligenceRetentionPolicy,
    ModelIntelligenceRetentionRecord,
    ModelIntelligenceRole,
    RetentionCause,
    RetentionClass,
    RetentionState,
    model_intelligence_threat_matrix,
    sanitize_model_intelligence_audit_event,
)


def _artifact() -> ArtifactRef:
    return ArtifactRef(
        artifact_id="artifact-1",
        job_id="job-1",
        kind="analysis.report",
        sha256="a" * 64,
        size_bytes=128,
        media_type="application/json",
    )


def test_tenant_scope_precedes_rbac_even_for_tenant_admin() -> None:
    principal = ModelIntelligencePrincipal(
        "subject-1",
        "tenant-a",
        frozenset({ModelIntelligenceRole.TENANT_ADMIN}),
    )
    foreign = ModelIntelligenceResourceScope(
        "tenant-b",
        ModelIntelligenceResourceKind.ARTIFACT,
        "artifact-1",
    )

    decision = ModelIntelligenceAccessPolicy().decide(
        principal,
        foreign,
        ModelIntelligenceAction.READ_ARTIFACT,
    )

    assert decision.allowed is False
    assert decision.reason_code == "tenant_scope_mismatch"


def test_rbac_allows_only_declared_same_tenant_actions() -> None:
    principal = ModelIntelligencePrincipal(
        "subject-1",
        "tenant-a",
        frozenset({ModelIntelligenceRole.VIEWER}),
    )
    resource = ModelIntelligenceResourceScope(
        "tenant-a",
        ModelIntelligenceResourceKind.ARTIFACT,
        "artifact-1",
    )
    policy = ModelIntelligenceAccessPolicy()

    assert policy.decide(principal, resource, ModelIntelligenceAction.READ_ARTIFACT).allowed is True
    assert policy.decide(principal, resource, ModelIntelligenceAction.DELETE_ARTIFACT).reason_code == "rbac_action_denied"


def test_audit_projection_removes_raw_content_bytes_secrets_and_paths() -> None:
    event = sanitize_model_intelligence_audit_event(
        "job_state_transition",
        {
            "state": "failed",
            "reason_code": "policy_denied",
            "raw_prompt": "reveal this prompt",
            "activation_tensor": [1.0, 2.0],
            "model_bytes": b"weights",
            "secret_token": "secret",
            "local_path": "/models/private/model.safetensors",
        },
    )
    serialized = repr(event)

    assert event["state"] == "failed"
    assert event["redacted_field_count"] == 5
    assert "reveal this prompt" not in serialized
    assert "/models/private" not in serialized
    assert "weights" not in serialized
    assert set(event["redaction_categories"]) == {
        "activation",
        "local_path",
        "model_bytes",
        "raw_prompt",
        "secret",
    }


def test_retention_transitions_are_tenant_bound_and_idempotent() -> None:
    record = ModelIntelligenceRetentionRecord(
        tenant_id="tenant-a",
        artifact_ref=_artifact(),
        retention_class=RetentionClass.STANDARD,
        created_at_epoch_seconds=100,
        retain_until_epoch_seconds=200,
    )
    policy = ModelIntelligenceRetentionPolicy()

    foreign = policy.plan_deletion(
        record,
        requesting_tenant_id="tenant-b",
        idempotency_key="delete-key-0001",
        now_epoch_seconds=300,
        cause=RetentionCause.RETENTION_EXPIRED,
    )
    planned = policy.plan_deletion(
        record,
        requesting_tenant_id="tenant-a",
        idempotency_key="delete-key-0001",
        now_epoch_seconds=300,
        cause=RetentionCause.RETENTION_EXPIRED,
    )
    repeated = policy.plan_deletion(
        replace(record, state=RetentionState.DELETE_PENDING),
        requesting_tenant_id="tenant-a",
        idempotency_key="delete-key-0001",
        now_epoch_seconds=300,
        cause=RetentionCause.RETENTION_EXPIRED,
    )

    assert foreign.reason_code == "tenant_scope_mismatch"
    assert planned.next_state is RetentionState.DELETE_PENDING
    assert repeated.idempotent is True
    assert repeated.reason_code == "delete_already_pending"
    assert planned.audit_event()["artifact_ref_digest"] == "a" * 0 or len(
        planned.audit_event()["artifact_ref_digest"]
    ) == 64


def test_threat_matrix_separates_parser_admission_from_policy_boundaries() -> None:
    matrix = model_intelligence_threat_matrix()
    boundaries = {item["boundary"]: item for item in matrix["boundaries"]}

    assert set(boundaries) == {"api", "hub", "worker", "parser", "artifact_store"}
    assert boundaries["parser"]["admission_owner"] == "OWMA-003"
    assert "tenant_scope" in boundaries["api"]["controls"]
    assert "delegated_job_only" in boundaries["worker"]["controls"]
