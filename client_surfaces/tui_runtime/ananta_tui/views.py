from __future__ import annotations

from typing import Any

from client_surfaces.common.types import ClientProfile, ClientResponse


def _safe_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("items"), list):
        return [item for item in payload["items"] if isinstance(item, dict)]
    return []


def render_health_view(profile: ClientProfile, health: ClientResponse, capabilities: ClientResponse) -> str:
    lines = ["[HEALTH]"]
    lines.append(f"profile={profile.profile_id} endpoint={profile.base_url}")
    lines.append(f"state={health.state} status={health.status_code}")
    capability_items: list[str] = []
    if isinstance(capabilities.data, dict):
        capability_items = [str(item) for item in list(capabilities.data.get("capabilities") or [])]
    lines.append(f"capabilities={','.join(capability_items) if capability_items else 'none'}")
    if health.state != "healthy":
        lines.append(f"degraded_reason={health.error or health.state}")
    return "\n".join(lines)


def render_task_artifact_view(tasks: ClientResponse, artifacts: ClientResponse) -> str:
    lines = ["[TASKS]"]
    task_items = _safe_items(tasks.data)
    if task_items:
        for task in task_items[:10]:
            lines.append(f"- {task.get('id')} [{task.get('status')}] {task.get('title')}")
    else:
        lines.append("- no_tasks_available")
    lines.append("[ARTIFACTS]")
    artifact_items = _safe_items(artifacts.data)
    if artifact_items:
        for artifact in artifact_items[:10]:
            lines.append(f"- {artifact.get('id')} ({artifact.get('type')}) {artifact.get('title')}")
    else:
        lines.append("- no_artifacts_available")
    if tasks.state != "healthy":
        lines.append(f"tasks_degraded={tasks.state}")
    if artifacts.state != "healthy":
        lines.append(f"artifacts_degraded={artifacts.state}")
    return "\n".join(lines)


def render_approval_repair_view(approvals: ClientResponse, repairs: ClientResponse) -> str:
    lines = ["[APPROVALS]"]
    approval_items = _safe_items(approvals.data)
    if approval_items:
        for item in approval_items[:10]:
            lines.append(f"- {item.get('id')} scope={item.get('scope')} state={item.get('state')}")
    else:
        lines.append("- no_approval_items")
    lines.append("[REPAIRS]")
    repair_items = _safe_items(repairs.data)
    if repair_items:
        for repair in repair_items[:10]:
            lines.append(
                (
                    f"- {repair.get('session_id')} diagnosis={repair.get('diagnosis')} "
                    f"verification={repair.get('verification_result')} outcome={repair.get('outcome')}"
                )
            )
    else:
        lines.append("- no_repair_sessions")
    lines.append("note=view_only_no_implicit_execution")
    return "\n".join(lines)
