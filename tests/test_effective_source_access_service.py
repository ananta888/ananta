from __future__ import annotations

import pytest

from agent.services.effective_source_access_service import (
    EffectivePolicyEvaluation,
    EffectiveSourceAccessError,
    EffectiveSourceAccessService,
    EffectiveSourceRevision,
)
from ananta_contracts.source_control import (
    DestinationDescriptor,
    GrantOperation,
    GrantTransformation,
    ProviderLocation,
)


SOURCE = EffectiveSourceRevision(
    source_revision_id="revision-example",
    tenant_id="tenant-example",
    project_id="project-example",
    source_type="workspace",
    sensitivity="project_internal",
    revision_digest="a" * 64,
)
DESTINATION = DestinationDescriptor.create(
    worker_id="worker-example",
    worker_kind="llm",
    runtime_id="runtime-example",
    runtime_kind="remote_api",
    provider_id="anthropic",
    model_id="claude-model-example",
    model_class="anthropic_claude",
    provider_location=ProviderLocation.EXTERNAL_REGION,
    data_residency="region-example",
)


class _Sources:
    def get_revision(self, **kwargs):
        return SOURCE if kwargs["source_revision_id"] == SOURCE.source_revision_id else None

    def list_revisions(self, **kwargs):
        return [SOURCE], None


class _Destinations:
    def get_destination(self, **kwargs):
        return (
            DESTINATION
            if kwargs["destination_id"] == DESTINATION.destination_id
            else None
        )

    def list_destinations(self, **kwargs):
        return [DESTINATION], None


class _Policy:
    def __init__(self, decision: str = "deny") -> None:
        self.decision = decision
        self.calls = []

    def evaluate(self, **kwargs):
        self.calls.append(kwargs)
        return EffectivePolicyEvaluation(
            decision=self.decision,
            reason_codes=("cloud_blocked",)
            if self.decision == "deny"
            else ("explicit_allow",),
            matched_rule_path=("project-rule",),
            default_applied=False,
            approval_requirement=None,
            policy_digest="b" * 64,
        )


def _service(policy: _Policy) -> EffectiveSourceAccessService:
    return EffectiveSourceAccessService(
        sources=_Sources(),
        destinations=_Destinations(),
        policy=policy,
    )


def _preview(service: EffectiveSourceAccessService):
    return service.preview(
        tenant_id="tenant-example",
        project_id="project-example",
        source_revision_id=SOURCE.source_revision_id,
        destination_id=DESTINATION.destination_id,
        operation=GrantOperation.CHAT_CONTEXT,
        transformation=GrantTransformation.REDACTED,
        purpose="project-chat",
    )


def test_preview_explains_server_resolved_destination() -> None:
    policy = _Policy("deny")
    decision = _preview(_service(policy))

    assert decision.decision == "deny"
    assert decision.reason_codes == ("cloud_blocked",)
    assert policy.calls[0]["destination"].model_id == "claude-model-example"
    assert policy.calls[0]["destination"].provider_id == "anthropic"


def test_dispatch_uses_same_evaluator_and_exact_digests() -> None:
    policy = _Policy("allow")
    service = _service(policy)
    preview = _preview(service)

    dispatch = service.verify_dispatch(
        tenant_id="tenant-example",
        project_id="project-example",
        source_revision_id=SOURCE.source_revision_id,
        destination_id=DESTINATION.destination_id,
        operation=GrantOperation.CHAT_CONTEXT,
        transformation=GrantTransformation.REDACTED,
        purpose="project-chat",
        expected_revision_digest=preview.revision_digest,
        expected_policy_digest=preview.policy_digest,
    )

    assert dispatch == preview
    assert len(policy.calls) == 2


def test_policy_or_revision_change_after_preview_blocks_dispatch() -> None:
    service = _service(_Policy("allow"))

    with pytest.raises(EffectiveSourceAccessError, match="revision_changed"):
        service.verify_dispatch(
            tenant_id="tenant-example",
            project_id="project-example",
            source_revision_id=SOURCE.source_revision_id,
            destination_id=DESTINATION.destination_id,
            operation=GrantOperation.INDEX,
            transformation=GrantTransformation.RAW,
            purpose="project-index",
            expected_revision_digest="c" * 64,
            expected_policy_digest="b" * 64,
        )
    with pytest.raises(EffectiveSourceAccessError, match="policy_changed"):
        service.verify_dispatch(
            tenant_id="tenant-example",
            project_id="project-example",
            source_revision_id=SOURCE.source_revision_id,
            destination_id=DESTINATION.destination_id,
            operation=GrantOperation.INDEX,
            transformation=GrantTransformation.RAW,
            purpose="project-index",
            expected_revision_digest="a" * 64,
            expected_policy_digest="c" * 64,
        )


def test_matrix_is_bounded_and_contains_only_server_records() -> None:
    service = _service(_Policy("deny"))
    matrix = service.matrix(
        tenant_id="tenant-example",
        project_id="project-example",
        operation=GrantOperation.EXPORT,
        transformation=GrantTransformation.SUMMARY,
        purpose="project-review",
        source_filters={"sensitivity": "project_internal"},
        destination_filters={"provider_id": "anthropic"},
    )

    assert len(matrix["rows"]) == 1
    assert matrix["rows"][0]["destination_id"] == DESTINATION.destination_id
    with pytest.raises(EffectiveSourceAccessError, match="limit"):
        service.matrix(
            tenant_id="tenant-example",
            project_id="project-example",
            operation=GrantOperation.EXPORT,
            transformation=GrantTransformation.SUMMARY,
            purpose="project-review",
            source_limit=50,
            destination_limit=50,
        )
