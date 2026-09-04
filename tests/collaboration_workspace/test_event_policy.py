from __future__ import annotations

from pathlib import Path

import pytest

from agent.services.collaboration_event_policy import CollaborationEventPolicy
from ananta_contracts.collaboration_workspace import CollaborationContractError
from tests.collaboration_workspace.helpers import actor, build_event, service


def test_event_classes_are_explicit_and_unknown_types_fail_closed() -> None:
    policy = CollaborationEventPolicy()
    assert policy.classify("typing.changed").traffic_class == "ephemeral"
    assert policy.classify("typing.changed").durable is False
    assert policy.classify("decision.recorded").traffic_class == "durable_collaboration"
    assert policy.classify("artifact.linked").traffic_class == "bulk_reference"
    assert policy.classify("command.proposed").traffic_class == "command_intent"
    unknown = policy.classify("looks.like.chat")
    assert (unknown.admitted, unknown.reason_code) == (False, "event_type_unknown")


@pytest.mark.parametrize(
    "payload",
    [
        {"password": "not-allowed"},
        {"nested": {"access_token": "not-allowed"}},
        {"items": [{"private_reasoning": "not-allowed"}]},
        {"raw_tool_output": "unbounded"},
        {"text": "Authorization: Bearer abcdefghijklmnop"},
        {"message": "api_key=abcdefghijklmnop"},
    ],
)
def test_sensitive_content_is_rejected_before_durable_storage(payload: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="sensitive_content_rejected"):
        CollaborationEventPolicy().require_durable("message.posted", payload)


def test_ephemeral_and_unknown_events_cannot_enter_durable_workspace_store(tmp_path: Path) -> None:
    workspaces = service(tmp_path / "collaboration.sqlite3")
    workspaces.create_workspace(
        tenant_id="tenant-a",
        principal_id="user-a",
        title="Policy",
        owner=actor(),
        workspace_id="workspace-a",
    )
    ephemeral = build_event(
        workspace_id="workspace-a",
        actor_binding_id="human-user-a",
        event_type="message.posted",
        payload={"text": "typing"},
        idempotency_key="ephemeral",
    )
    ephemeral["event_type"] = "typing.changed"
    with pytest.raises(CollaborationContractError, match="event_invalid"):
        workspaces.append_event(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            principal_actor_id="human-user-a",
            event=ephemeral,
        )
    unknown = {**ephemeral, "event_type": "looks.like.chat", "idempotency_key": "unknown"}
    with pytest.raises(CollaborationContractError, match="event_invalid"):
        workspaces.append_event(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            principal_actor_id="human-user-a",
            event=unknown,
        )


def test_sensitive_payload_is_rejected_by_workspace_admission(tmp_path: Path) -> None:
    workspaces = service(tmp_path / "collaboration.sqlite3")
    workspaces.create_workspace(
        tenant_id="tenant-a",
        principal_id="user-a",
        title="Policy",
        owner=actor(),
        workspace_id="workspace-a",
    )
    event = build_event(
        workspace_id="workspace-a",
        actor_binding_id="human-user-a",
        event_type="message.posted",
        payload={"nested": {"api_token": "not-allowed"}},
        idempotency_key="sensitive",
    )
    with pytest.raises(ValueError, match="sensitive_content_rejected"):
        workspaces.append_event(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            principal_actor_id="human-user-a",
            event=event,
        )


def test_artifacts_must_be_clean_bounded_digest_references() -> None:
    policy = CollaborationEventPolicy()
    reference = {
        "artifact_id": "artifact-a",
        "digest": "a" * 64,
        "size_bytes": 1024,
        "media_type": "text/plain",
        "scan_status": "clean",
        "export_allowed": False,
    }
    assert policy.require_durable("artifact.linked", reference).traffic_class == "bulk_reference"
    with pytest.raises(ValueError, match="artifact_reference_invalid"):
        policy.require_durable("artifact.linked", {**reference, "content": "inline secret"})
    with pytest.raises(ValueError, match="artifact_reference_invalid"):
        policy.require_durable("artifact.linked", {**reference, "scan_status": "unknown"})
