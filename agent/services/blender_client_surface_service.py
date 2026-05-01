from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from agent.db_models import GoalDB
from agent.services.blender_action_plan_service import build_blender_action_plan
from agent.services.blender_export_plan_service import build_export_plan
from agent.services.blender_redaction_service import redact_blender_payload
from agent.services.repository_registry import get_repository_registry
from agent.services.service_registry import get_core_services

ROOT = Path(__file__).resolve().parents[2]
CAPABILITIES_PATH = ROOT / "domains" / "blender" / "capabilities.json"
POLICY_PACK_PATH = ROOT / "domains" / "blender" / "policies" / "policy.v1.json"


class BlenderClientSurfaceService:
    """Thin Blender API adapter. Hub-owned services keep policy, task and artifact authority."""

    def __init__(self) -> None:
        self._capabilities_payload = self._load_json(CAPABILITIES_PATH)
        self._policy_pack_payload = self._load_json(POLICY_PACK_PATH)
        self._approval_decisions: list[dict[str, Any]] = []

    def health(self) -> dict[str, Any]:
        readiness = get_core_services().goal_service.goal_readiness()
        return {
            "status": "connected",
            "surface": "blender",
            "readiness": readiness,
            "capability_count": len(list(self._capabilities_payload.get("capabilities") or [])),
            "contract_version": "blender_client_surface.v1",
        }

    def capabilities(self) -> dict[str, Any]:
        decisions = {
            str(item.get("capability_id") or ""): str(item.get("decision") or self._policy_pack_payload.get("default_decision") or "default_deny")
            for item in list(self._policy_pack_payload.get("rules") or [])
        }
        capabilities: list[dict[str, Any]] = []
        for item in list(self._capabilities_payload.get("capabilities") or []):
            capability_id = str(item.get("capability_id") or "")
            capabilities.append(
                {
                    "capability_id": capability_id,
                    "display_name": item.get("display_name"),
                    "risk": item.get("risk"),
                    "category": item.get("category"),
                    "read_only": bool(item.get("read_only")),
                    "mutating": bool(item.get("mutating")),
                    "approval_required": bool(item.get("approval_required")),
                    "default_policy_state": str(item.get("default_policy_state") or "default_deny"),
                    "effective_decision": decisions.get(capability_id, str(item.get("default_policy_state") or "default_deny")),
                }
            )
        return {"status": "ok", "surface": "blender", "capabilities": capabilities}

    def submit_goal(self, *, goal: str, context: dict[str, Any], capability_id: str, requested_by: str) -> dict[str, Any]:
        goal_text = str(goal or "").strip()
        if not goal_text:
            return {"status": "degraded", "reason": "goal_missing"}
        services = get_core_services()
        repos = get_repository_registry()
        readiness = services.goal_service.goal_readiness()
        redacted_context = redact_blender_payload(context)
        goal_record = GoalDB(
            goal=goal_text,
            summary=goal_text[:200],
            status="planning",
            source="blender_client_surface",
            requested_by=requested_by,
            context=json.dumps(
                {
                    "source_surface": "blender",
                    "capability_id": capability_id,
                    "blender_context": redacted_context,
                }
            ),
            constraints=[],
            acceptance_criteria=[
                "Goal remains bounded to the submitted Blender context.",
                "No Blender mutation executes without approval-bound request.",
            ],
            execution_preferences={"source_surface": "blender", "capability_id": capability_id},
            visibility={"surface": "blender"},
            workflow_defaults=services.goal_service.default_workflow_config(),
            workflow_overrides={},
            workflow_effective=services.goal_service.default_workflow_config(),
            workflow_provenance={},
            readiness=readiness,
            mode="generic",
            mode_data={"surface": "blender"},
        )
        goal_record = repos.goal_repo.save(goal_record)
        goal_record = services.goal_lifecycle_service.transition_goal(
            goal_record,
            target_status="planning",
            reason="blender_client_surface_goal_submit",
            readiness=readiness,
        )
        return {
            "status": "accepted",
            "goal_id": goal_record.id,
            "task_id": goal_record.id,
            "trace_id": goal_record.trace_id,
            "goal": services.goal_service.serialize_goal(goal_record),
        }

    def list_tasks(self) -> dict[str, Any]:
        items = get_core_services().task_query_service.list_tasks(
            status_filter="",
            agent_filter=None,
            since_filter=None,
            until_filter=None,
            limit=100,
            offset=0,
        )
        if isinstance(items, dict) and isinstance(items.get("items"), list):
            return {"status": "ok", **items}
        return {"status": "ok", "items": list(items or [])}

    def get_task(self, *, task_id: str) -> tuple[dict[str, Any], int]:
        task = get_core_services().task_runtime_service.get_local_task_status(task_id)
        if not task:
            return {"status": "degraded", "reason": "not_found"}, 404
        return {"status": "ok", "task": dict(task)}, 200

    def list_artifacts(self) -> dict[str, Any]:
        return {"status": "ok", "items": [item.model_dump() for item in get_repository_registry().artifact_repo.get_all()]}

    def get_artifact(self, *, artifact_id: str) -> tuple[dict[str, Any], int]:
        artifact = get_repository_registry().artifact_repo.get_by_id(artifact_id)
        if artifact is None:
            return {"status": "degraded", "reason": "not_found"}, 404
        return {"status": "ok", "artifact": artifact.model_dump()}, 200

    def list_approvals(self) -> dict[str, Any]:
        tasks = self.list_tasks().get("items") or []
        approvals = []
        for task in tasks:
            if str((task or {}).get("status") or "").lower() != "blocked":
                continue
            reason = str((task or {}).get("status_reason_code") or (task or {}).get("failure_type") or "")
            if "approval" not in reason:
                continue
            approvals.append(
                {
                    "id": f"approval:{task.get('id') or task.get('task_id')}",
                    "task_id": task.get("id") or task.get("task_id"),
                    "state": "pending",
                    "risk": "high",
                    "action_text": "Review Blender approval-gated action",
                    "surface": "blender",
                }
            )
        return {"status": "ok", "items": approvals, "decisions": list(self._approval_decisions[-20:])}

    def approval_decision(self, *, approval_id: str, decision: str, requested_by: str) -> tuple[dict[str, Any], int]:
        approval_value = str(approval_id or "").strip()
        decision_value = str(decision or "").strip().lower()
        if decision_value == "deny":
            decision_value = "reject"
        if not approval_value or decision_value not in {"approve", "reject"}:
            return {"status": "degraded", "reason": "invalid_approval_decision"}, 400
        record = {
            "status": "accepted",
            "approval_id": approval_value,
            "decision": decision_value,
            "requested_by": requested_by,
            "decided_at": time.time(),
        }
        self._approval_decisions.append(record)
        return record, 200

    def export_plan(self, *, fmt: str, target_path: str, selection_only: bool) -> dict[str, Any]:
        return {"status": "accepted", "plan": build_export_plan(fmt=fmt, target_path=target_path, selection_only=selection_only)}

    def render_plan(self, *, payload: dict[str, Any]) -> dict[str, Any]:
        plan = {
            "mode": "plan_only",
            "kind": str(payload.get("kind") or "preview_render"),
            "camera": payload.get("camera"),
            "output_path": payload.get("output_path"),
            "width": max(64, min(int(payload.get("width") or 512), 8192)),
            "height": max(64, min(int(payload.get("height") or 512), 8192)),
            "samples": max(1, min(int(payload.get("samples") or 16), 64)),
            "approval_required": str(payload.get("kind") or "preview_render") != "preview_render",
        }
        return {"status": "accepted", "plan": plan}

    def mutation_plan(self, *, payload: dict[str, Any]) -> dict[str, Any]:
        operations = list(payload.get("operations") or [])
        if not operations and payload.get("action"):
            operations = [{"action": payload.get("action"), "targets": list(payload.get("targets") or [])}]
        return {
            "status": "accepted",
            "plan": build_blender_action_plan(
                capability=str(payload.get("capability_id") or "blender.scene.mutate"),
                operations=[dict(item or {}) for item in operations],
                provenance={"surface": "blender", "mode": "plan_only"},
            ),
        }

    def execute(self, *, payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
        approval_id = str(payload.get("approval_id") or "").strip()
        if not approval_id:
            return {"status": "approval_required", "reason": "approval_required"}, 409
        return {"status": "accepted", "execution": dict(payload)}, 200

    def events(self) -> dict[str, Any]:
        audit_items = get_repository_registry().audit_repo.get_all(limit=25)
        return {"status": "ok", "items": [item.model_dump() for item in audit_items]}

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))


_SERVICE: BlenderClientSurfaceService | None = None


def get_blender_client_surface_service() -> BlenderClientSurfaceService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = BlenderClientSurfaceService()
    return _SERVICE
