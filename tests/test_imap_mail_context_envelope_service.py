from __future__ import annotations

from agent.artifacts.goal_artifact_service import GoalArtifactService
from agent.services.imap_mail_artifact_service import register_mail_artifact
from agent.services.imap_mail_context_envelope_service import build_mail_context_envelope


def test_mail_context_envelope_denies_cloud_by_default() -> None:
    envelope = build_mail_context_envelope(goal_id="goal-cloud-denied", worker_target="cloud_worker")
    assert envelope["allowed"] is False
    assert envelope["reason_code"] == "mail_context_default_denied_cloud"
    assert envelope["mail_artifacts"] == []


def test_mail_context_envelope_allows_local_with_explicit_grants(tmp_path) -> None:
    artifact = register_mail_artifact(
        message_ref={"account_id": "imap-a", "mailbox": "INBOX", "uid": 8, "message_id": "<m8@example.com>"},
        scope="metadata_only",
        redaction_status="not_required",
        policy_decision_ref="policy:mail:metadata_only",
        repo_root=tmp_path,
    )
    goal_id = "goal-mail-local"
    service = GoalArtifactService()
    service.create_grant(
        goal_id=goal_id,
        grant={
            "schema": "source_artifact_grant.v1",
            "grant_id": "grant-mail-1",
            "goal_id": goal_id,
            "artifact_ref": artifact["artifact_ref"],
            "granted_by": "test",
            "granted_at": "2026-05-27T00:00:00Z",
            "allowed_usages": ["read", "use_as_context"],
            "data_boundary": "project_private",
            "sensitivity": "internal",
            "policy_decision_ref": "policy:mail:metadata_only",
        },
    )
    envelope = build_mail_context_envelope(goal_id=goal_id, worker_target="local_worker", repo_root=str(tmp_path))
    assert envelope["allowed"] is True
    assert envelope["mail_source_refs"] == [artifact["artifact_ref"]]
    assert envelope["redaction_statuses"] == ["not_required"]
