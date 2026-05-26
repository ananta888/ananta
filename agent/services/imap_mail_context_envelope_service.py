from __future__ import annotations

from typing import Any

from agent.artifacts.artifact_grants import is_grant_active
from agent.artifacts.goal_artifact_service import GoalArtifactService
from agent.services.imap_mail_artifact_service import get_mail_artifact


def build_mail_context_envelope(
    *,
    goal_id: str,
    worker_target: str,
    repo_root: str | None = None,
) -> dict[str, Any]:
    target = str(worker_target or "").strip() or "local_worker"
    if target == "cloud_worker":
        return {
            "goal_id": goal_id,
            "worker_target": target,
            "allowed": False,
            "reason_code": "mail_context_default_denied_cloud",
            "mail_artifacts": [],
            "mail_source_refs": [],
            "redaction_statuses": [],
        }
    graph = GoalArtifactService().get_goal_graph(goal_id)
    grants = [dict(item) for item in list(graph.get("source_grants") or []) if isinstance(item, dict)]
    mail_rows: list[dict[str, Any]] = []
    refs: list[str] = []
    statuses: list[str] = []
    for grant in grants:
        active, _reason = is_grant_active(grant)
        if not active:
            continue
        artifact_ref = str(grant.get("artifact_ref") or "").strip()
        if not artifact_ref.startswith("mail://"):
            continue
        artifact = get_mail_artifact(artifact_ref=artifact_ref, repo_root=repo_root)
        if artifact is None:
            continue
        refs.append(artifact_ref)
        statuses.append(str(artifact.get("redaction_status") or "not_required"))
        mail_rows.append(
            {
                "artifact_ref": artifact_ref,
                "artifact_kind": str(artifact.get("artifact_kind") or "metadata_only"),
                "redaction_status": str(artifact.get("redaction_status") or "not_required"),
                "policy_decision_ref": str(artifact.get("policy_decision_ref") or ""),
                "message_ref": dict(artifact.get("message_ref") or {}),
            }
        )
    return {
        "goal_id": goal_id,
        "worker_target": target,
        "allowed": True,
        "reason_code": "mail_context_allowed_local",
        "mail_artifacts": mail_rows,
        "mail_source_refs": refs,
        "redaction_statuses": statuses,
    }
