from __future__ import annotations

from agent.artifacts.goal_artifact_service import GoalArtifactService
from agent.services.imap_mail_artifact_service import get_mail_artifact, register_mail_artifact
from agent.services.imap_mail_context_envelope_service import build_mail_context_envelope
from agent.services.imap_redaction_pipeline_service import redact_mail_for_worker_context


def test_mail_security_flow_enforces_metadata_only_excerpt_grant_and_cloud_denied(tmp_path) -> None:
    message_ref = {
        "account_id": "imap-a",
        "mailbox": "INBOX",
        "uid": 19,
        "message_id": "<m19@example.com>",
    }
    metadata_artifact = register_mail_artifact(
        message_ref=message_ref,
        scope="metadata_only",
        redaction_status="not_required",
        policy_decision_ref="policy:mail:metadata_only",
        repo_root=tmp_path,
    )
    assert metadata_artifact["artifact_kind"] == "metadata_only"
    assert metadata_artifact["excerpt"] == ""

    redacted = redact_mail_for_worker_context(body_text="token=abc123 build failed")
    assert redacted["redaction_status"] == "redacted"
    assert "abc123" not in str(redacted["redacted_body"])

    excerpt_artifact = register_mail_artifact(
        message_ref=message_ref,
        scope="excerpt",
        redaction_status=str(redacted["redaction_status"]),
        policy_decision_ref="policy:mail:excerpt",
        excerpt=str(redacted["redacted_body"]),
        repo_root=tmp_path,
    )
    goal_id = "goal-mail-security-1"
    GoalArtifactService().create_grant(
        goal_id=goal_id,
        grant={
            "schema": "source_artifact_grant.v1",
            "grant_id": "grant-mail-excerpt-1",
            "goal_id": goal_id,
            "artifact_ref": excerpt_artifact["artifact_ref"],
            "granted_by": "test",
            "granted_at": "2026-05-27T00:00:00Z",
            "allowed_usages": ["read", "use_as_context"],
            "data_boundary": "project_private",
            "sensitivity": "internal",
            "policy_decision_ref": "policy:mail:excerpt",
        },
    )
    cloud = build_mail_context_envelope(goal_id=goal_id, worker_target="cloud_worker", repo_root=str(tmp_path))
    local = build_mail_context_envelope(goal_id=goal_id, worker_target="local_worker", repo_root=str(tmp_path))
    assert cloud["allowed"] is False
    assert cloud["reason_code"] == "mail_context_default_denied_cloud"
    assert local["allowed"] is True
    assert local["mail_source_refs"] == [excerpt_artifact["artifact_ref"]]
    assert local["redaction_statuses"] == ["redacted"]


def test_excerpt_artifact_keeps_redacted_content_for_worker_context(tmp_path) -> None:
    message_ref = {
        "account_id": "imap-a",
        "mailbox": "INBOX",
        "uid": 20,
        "message_id": "<m20@example.com>",
    }
    redacted = redact_mail_for_worker_context(body_text="password=hunter2")
    artifact = register_mail_artifact(
        message_ref=message_ref,
        scope="excerpt",
        redaction_status=str(redacted["redaction_status"]),
        policy_decision_ref="policy:mail:excerpt",
        excerpt=str(redacted["redacted_body"]),
        repo_root=tmp_path,
    )
    loaded = get_mail_artifact(artifact_ref=artifact["artifact_ref"], repo_root=tmp_path)
    assert loaded is not None
    assert loaded["artifact_kind"] == "excerpt"
    assert "hunter2" not in str(loaded["excerpt"])
    assert "[REDACTED_SECRET]" in str(loaded["excerpt"])
