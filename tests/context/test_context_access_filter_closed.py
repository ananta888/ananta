"""Only completed Hub policy grants may contribute model prompt context."""

from unittest.mock import Mock

import pytest

from agent.services import context_access_policy_service as module
from ananta_contracts.context_access_policy import (
    ContextAccessPolicy,
    ContextBlockAccessDecision,
    Decision,
    RequestedOperation,
    build_destination_context,
)


@pytest.fixture
def service(monkeypatch):
    monkeypatch.setattr(module, "get_context_access_policy_repo", Mock())
    monkeypatch.setattr(module, "get_source_classification_service", Mock())
    return module.ContextAccessPolicyService()


def destination():
    return build_destination_context(
        worker_id="synthetic-worker", worker_kind="native",
        runtime_target_id="synthetic-runtime", runtime_kind="local",
        provider_id="ollama", provider_location="local", model_id="synthetic-model",
        requested_operation=RequestedOperation.send_to_llm,
    )


def test_mismatched_approval_is_not_transported_as_context(service):
    policy = ContextAccessPolicy("synthetic-policy", 1, "task", defaults={"send_allowed": True})
    block = {
        "block_id": "synthetic-block", "source_ref": "synthetic-source",
        "content": "restricted context", "content_hash": "a" * 64,
        "approval_override_id": "synthetic-approval",
        "approval_scope": {"block_hash": "b" * 64},
    }
    assert service.get_decision(policy, block, destination()).decision is Decision.approval_required
    assert service.filter_blocks(policy, [block], destination()) == []


@pytest.mark.parametrize(
    "decision", [Decision.deny, Decision.approval_required, Decision.unavailable, None, "future_decision"],
)
def test_non_grants_never_release_or_transform_content(service, decision):
    service.get_decision = Mock(return_value=ContextBlockAccessDecision("block", "source", [], decision))
    service.redact_content, service.summarize_content = Mock(), Mock()
    block = {"content": "restricted context"}
    assert service.filter_blocks(None, [block], destination()) == []
    assert block == {"content": "restricted context"}
    service.redact_content.assert_not_called()
    service.summarize_content.assert_not_called()


@pytest.mark.parametrize("decision, expected", [
    (Decision.allow, "original"),
    (Decision.allow_redacted, "redacted"),
    (Decision.allow_summary_only, "summary"),
])
def test_completed_grants_preserve_required_transformation_without_mutation(service, decision, expected):
    receipt = ContextBlockAccessDecision("block", "source", [], decision, decision_hash="synthetic-digest")
    service.get_decision = Mock(return_value=receipt)
    service.redact_content, service.summarize_content = Mock(return_value="redacted"), Mock(return_value="summary")
    block = {"content": "original"}
    assert service.filter_blocks(None, [block], destination()) == [{
        "content": expected, "access_decision": receipt, "access_decision_hash": "synthetic-digest",
    }]
    assert block == {"content": "original"}
    assert service.redact_content.call_count == int(decision is Decision.allow_redacted)
    assert service.summarize_content.call_count == int(decision is Decision.allow_summary_only)
