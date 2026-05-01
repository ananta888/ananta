from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent.db_models import GoalDB
from agent.services.freecad_export_plan_service import build_export_plan
from agent.services.freecad_macro_execution_service import execute_macro_if_approved
from agent.services.freecad_macro_planning_service import build_macro_plan
from agent.services.freecad_model_inspection_service import inspect_model_context
from agent.services.repository_registry import get_repository_registry
from agent.services.service_registry import get_core_services

ROOT = Path(__file__).resolve().parents[2]
CAPABILITIES_PATH = ROOT / "domains" / "freecad" / "capabilities.json"
POLICY_PACK_PATH = ROOT / "domains" / "freecad" / "policies" / "policy.v1.json"


class FreecadClientSurfaceService:
    def __init__(self) -> None:
        self._capabilities_payload = self._load_json(CAPABILITIES_PATH)
        self._policy_pack_payload = self._load_json(POLICY_PACK_PATH)

    def health(self) -> dict[str, Any]:
        readiness = get_core_services().goal_service.goal_readiness()
        return {
            "status": "connected",
            "surface": "freecad",
            "readiness": readiness,
            "capability_count": len(list(self._capabilities_payload.get("capabilities") or [])),
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
                    "approval_required": bool(item.get("approval_required")),
                    "default_policy_state": str(item.get("default_policy_state") or "default_deny"),
                    "effective_decision": decisions.get(capability_id, str(item.get("default_policy_state") or "default_deny")),
                }
            )
        return {"status": "ok", "capabilities": capabilities}

    def submit_goal(
        self,
        *,
        goal: str,
        context: dict[str, Any],
        capability_id: str,
        requested_by: str,
    ) -> dict[str, Any]:
        goal_text = str(goal or "").strip()
        if not goal_text:
            return {"status": "degraded", "reason": "goal_missing"}

        services = get_core_services()
        repos = get_repository_registry()
        readiness = services.goal_service.goal_readiness()
        inspected = inspect_model_context(context)
        goal_record = GoalDB(
            goal=goal_text,
            summary=goal_text[:200],
            status="planning",
            source="freecad_client_surface",
            requested_by=requested_by,
            context=json.dumps(
                {
                    "source_surface": "freecad",
                    "capability_id": capability_id,
                    "freecad_context": dict(context or {}),
                    "inspection": inspected,
                }
            ),
            constraints=[],
            acceptance_criteria=[
                "Goal remains bounded to the submitted FreeCAD context.",
                "No client-side macro execution without approval-bound request.",
            ],
            execution_preferences={"source_surface": "freecad", "capability_id": capability_id},
            visibility={"surface": "freecad"},
            workflow_defaults=services.goal_service.default_workflow_config(),
            workflow_overrides={},
            workflow_effective=services.goal_service.default_workflow_config(),
            workflow_provenance={},
            readiness=readiness,
            mode="generic",
            mode_data={"surface": "freecad"},
        )
        goal_record = repos.goal_repo.save(goal_record)
        goal_record = services.goal_lifecycle_service.transition_goal(
            goal_record,
            target_status="planning",
            reason="freecad_client_surface_goal_submit",
            readiness=readiness,
        )
        return {
            "status": "accepted",
            "goal_id": goal_record.id,
            "task_id": goal_record.id,
            "trace_id": goal_record.trace_id,
            "goal": services.goal_service.serialize_goal(goal_record),
            "inspection": inspected,
        }

    def approval_decision(self, *, approval_id: str, decision: str, requested_by: str) -> tuple[dict[str, Any], int]:
        approval_value = str(approval_id or "").strip()
        decision_value = str(decision or "").strip().lower()
        if not approval_value or decision_value not in {"approve", "reject"}:
            return {"status": "degraded", "reason": "invalid_approval_decision"}, 400
        return {
            "status": "accepted",
            "approval_id": approval_value,
            "decision": decision_value,
            "requested_by": requested_by,
        }, 200

    def export_plan(self, *, fmt: str, target_path: str, selection_only: bool) -> dict[str, Any]:
        return {"status": "accepted", "plan": build_export_plan(fmt=fmt, target_path=target_path, selection_only=selection_only)}

    def macro_plan(self, *, objective: str, context_summary: dict[str, Any] | None = None) -> dict[str, Any]:
        objective_value = str(objective or "").strip()
        if not objective_value:
            return {"status": "degraded", "reason": "objective_missing"}
        return {"status": "accepted", "plan": build_macro_plan(objective=objective_value, context_summary=context_summary)}

    def macro_execute(
        self,
        *,
        macro_text: str,
        approval_id: str | None,
        correlation_id: str,
    ) -> tuple[dict[str, Any], int]:
        approved = bool(str(approval_id or "").strip())
        result = execute_macro_if_approved(
            macro_text=macro_text,
            approved=approved,
            approval_id=str(approval_id or "").strip() or None,
            correlation_id=correlation_id,
        )
        if result.get("status") == "blocked":
            return {"status": "approval_required", **result}, 409
        return {"status": "accepted", "execution": result}, 200

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))


_SERVICE: FreecadClientSurfaceService | None = None


def get_freecad_client_surface_service() -> FreecadClientSurfaceService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = FreecadClientSurfaceService()
    return _SERVICE
