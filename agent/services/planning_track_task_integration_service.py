from __future__ import annotations

import hashlib
import time
from typing import Any

from agent.artifacts.goal_artifact_repository import GoalArtifactRepository
from agent.artifacts.goal_artifact_service import GoalArtifactService
from agent.db_models import TaskDB
from agent.repository import goal_repo, task_repo
from agent.services.planning_track_pipeline_service import compute_tasks_status_summary
from agent.services.task_runtime_service import update_local_task_status


def _task_id_for_plan_task(*, goal_id: str, output_artifact_id: str, plan_task_id: str) -> str:
    digest = hashlib.sha1(f"{goal_id}:{output_artifact_id}:{plan_task_id}".encode("utf-8")).hexdigest()[:14]
    return f"ptask-{digest}"


def _normalize_task_kind(value: str) -> str:
    kind = str(value or "").strip().lower()
    if kind in {"analysis", "schema"}:
        return "analysis"
    if kind in {"coding", "worker", "integration", "artifact"}:
        return "coding"
    if kind in {"test", "validation"}:
        return "testing"
    if kind in {"docs", "doc", "prompt"}:
        return "review"
    return "coding"


def _extract_workflow_step(plan_task: dict[str, Any]) -> dict[str, Any]:
    """WFG-007: pull the workflow-step provenance that the
    BlueprintPlanningAdapter (WFG-006) attaches to a planning track task.

    The adapter writes a flat set of keys onto each subtask
    (blueprint_workflow_step_id, blueprint_role_name, task_kind, gate,
    checks, failure_policy, required_capabilities, produces, consumes).
    This helper normalizes that into a single ``workflow_step`` block so
    the materializer can persist it onto ``worker_execution_context``
    and downstream queue/worker code can consume a stable shape.

    Returns an empty dict when the plan task is not backed by a
    blueprint workflow step (legacy/manual tracks), which keeps
    materialize_tasks() fully backward compatible.
    """
    if not isinstance(plan_task, dict):
        return {}
    step_id = str(plan_task.get("blueprint_workflow_step_id") or "").strip()
    if not step_id:
        return {}
    step_label = str(plan_task.get("blueprint_workflow_step_id_label") or "").strip()
    role = str(plan_task.get("blueprint_role_name") or "").strip()
    task_kind = str(plan_task.get("task_kind") or "").strip()
    failure_policy = plan_task.get("failure_policy")
    return {
        "schema": "workflow_step_provenance.v1",
        "step_id": step_id,
        "step_label": step_label or step_id,
        "role": role,
        "task_kind": task_kind,
        "gate": bool(plan_task.get("gate", False)),
        "checks": dict(plan_task.get("checks") or {}) or None,
        "failure_policy": str(failure_policy) if failure_policy else None,
        "required_capabilities": list(plan_task.get("required_capabilities") or []),
        "produces": list(plan_task.get("produces") or []),
        "consumes": list(plan_task.get("consumes") or []),
    }


class PlanningTrackTaskIntegrationService:
    def __init__(
        self,
        *,
        goal_artifact_service: GoalArtifactService | None = None,
        goal_artifact_repository: GoalArtifactRepository | None = None,
    ) -> None:
        self._artifact_service = goal_artifact_service or GoalArtifactService()
        self._artifact_repo = goal_artifact_repository or GoalArtifactRepository()

    def _load_output(self, *, goal_id: str, output_artifact_id: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        graph = self._artifact_service.get_goal_graph(goal_id)
        output = next(
            (
                dict(item)
                for item in list(graph.get("output_artifacts") or [])
                if isinstance(item, dict) and str(item.get("output_artifact_id") or "") == str(output_artifact_id)
            ),
            None,
        )
        if output is None:
            raise ValueError("planning_track_output_not_found")
        ext = dict(output.get("extensions") or {})
        payload = dict(ext.get("payload") or {})
        if str(output.get("artifact_type") or "") != "planning_track" or not payload:
            raise ValueError("planning_track_payload_missing")
        return graph, output, payload

    def materialize_tasks(self, *, goal_id: str, output_artifact_id: str) -> dict[str, Any]:
        graph, output, payload = self._load_output(goal_id=goal_id, output_artifact_id=output_artifact_id)
        tasks = [dict(item) for item in list(payload.get("tasks") or []) if isinstance(item, dict)]
        mapping: dict[str, str] = {}
        created_ids: list[str] = []

        for plan_task in tasks:
            plan_task_id = str(plan_task.get("id") or "").strip()
            if not plan_task_id:
                continue
            internal_task_id = _task_id_for_plan_task(
                goal_id=goal_id,
                output_artifact_id=output_artifact_id,
                plan_task_id=plan_task_id,
            )
            mapping[plan_task_id] = internal_task_id
            existing = task_repo.get_by_id(internal_task_id)
            title = str(plan_task.get("title") or f"Plan Task {plan_task_id}")[:200]
            acceptance = [str(item).strip() for item in list(plan_task.get("acceptance_criteria") or []) if str(item).strip()]
            description = str(plan_task.get("description") or "").strip()
            if acceptance:
                description = (description + "\n\nAcceptance:\n- " + "\n- ".join(acceptance)).strip()
            milestone_id = str(plan_task.get("milestone_id") or "").strip()
            status = str(plan_task.get("status") or "todo").strip()
            depends_plan_ids = [str(item).strip() for item in list(plan_task.get("depends_on") or []) if str(item).strip()]
            depends_internal_ids = [
                _task_id_for_plan_task(goal_id=goal_id, output_artifact_id=output_artifact_id, plan_task_id=dep)
                for dep in depends_plan_ids
            ]
            workflow_step = _extract_workflow_step(plan_task)
            required_capabilities = list(workflow_step.get("required_capabilities") or [])
            task_payload = existing or TaskDB(id=internal_task_id, created_at=time.time())
            task_payload.title = title
            task_payload.description = description
            task_payload.status = status if status in {"todo", "in_progress", "blocked", "completed", "failed"} else "todo"
            # WFG-007: gate tasks remain blocked until their depends_on are
            # satisfied; otherwise the queue lets them run too early.
            if bool(workflow_step.get("gate", False)) and task_payload.status == "todo":
                task_payload.status = "blocked"
            task_payload.priority = str(plan_task.get("priority") or "Medium")
            task_payload.goal_id = goal_id
            task_payload.plan_id = output_artifact_id
            task_payload.plan_node_id = plan_task_id
            workflow_task_kind = str(workflow_step.get("task_kind") or "").strip()
            task_payload.task_kind = (
                workflow_task_kind or _normalize_task_kind(str(plan_task.get("type") or ""))
            )
            task_payload.depends_on = depends_internal_ids
            if required_capabilities:
                task_payload.required_capabilities = required_capabilities
            # Preserve existing planning_track context; only refresh the
            # workflow_step block so repeated materialization is idempotent.
            existing_ctx = dict(task_payload.worker_execution_context or {})
            existing_ctx["planning_track"] = {
                "output_artifact_id": output_artifact_id,
                "plan_task_id": plan_task_id,
                "milestone_id": milestone_id,
            }
            if workflow_step:
                existing_ctx["workflow_step"] = workflow_step
            task_payload.worker_execution_context = existing_ctx
            if bool(workflow_step.get("gate", False)) and workflow_step.get("checks"):
                # Surface gate check expectations on the task itself so
                # downstream queue/worker code can verify without
                # re-reading the workflow definition.
                task_payload.verification_spec = {
                    **dict(task_payload.verification_spec or {}),
                    "schema": "workflow_gate_checks.v1",
                    "checks": dict(workflow_step.get("checks") or {}),
                    "failure_policy": str(workflow_step.get("failure_policy") or "block"),
                }
            task_payload.updated_at = time.time()
            task_repo.save(task_payload)
            created_ids.append(internal_task_id)

        ext = dict(output.get("extensions") or {})
        ext["task_mapping"] = dict(mapping)
        ext["materialized_task_ids"] = list(created_ids)
        ext["materialized_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        output["extensions"] = ext
        outputs = list(graph.get("output_artifacts") or [])
        for idx, row in enumerate(outputs):
            if isinstance(row, dict) and str(row.get("output_artifact_id") or "") == output_artifact_id:
                outputs[idx] = output
                break
        graph["output_artifacts"] = outputs
        graph["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self._artifact_repo.save_graph(graph)

        return {
            "goal_id": goal_id,
            "output_artifact_id": output_artifact_id,
            "materialized_task_ids": created_ids,
            "plan_task_to_internal_task": mapping,
        }

    def adopt_track(self, *, goal_id: str, output_artifact_id: str) -> dict[str, Any]:
        _graph, output, payload = self._load_output(goal_id=goal_id, output_artifact_id=output_artifact_id)
        ext = dict(output.get("extensions") or {})
        if not bool(ext.get("active_plan_candidate", False)):
            raise ValueError("planning_track_not_active_candidate")
        if str(output.get("status") or "") != "created" or str(output.get("verification_status") or "") != "valid":
            raise ValueError("planning_track_not_valid")

        materialized = self.materialize_tasks(goal_id=goal_id, output_artifact_id=output_artifact_id)

        goal = goal_repo.get_by_id(goal_id)
        if goal is not None:
            prefs = dict(goal.execution_preferences or {})
            prefs["active_planning_track_output_id"] = output_artifact_id
            prefs["planning_track_task_map"] = dict(materialized.get("plan_task_to_internal_task") or {})
            goal.execution_preferences = prefs
            goal.updated_at = time.time()
            goal_repo.save(goal)

        return materialized

    def reject_track(self, *, goal_id: str, output_artifact_id: str) -> dict[str, Any]:
        goal = goal_repo.get_by_id(goal_id)
        if goal is None:
            return {"goal_id": goal_id, "rejected_output_ids": [output_artifact_id]}
        prefs = dict(goal.execution_preferences or {})
        rejected = [str(item).strip() for item in list(prefs.get("rejected_planning_track_output_ids") or []) if str(item).strip()]
        if output_artifact_id not in rejected:
            rejected.append(output_artifact_id)
        prefs["rejected_planning_track_output_ids"] = rejected
        if str(prefs.get("active_planning_track_output_id") or "") == output_artifact_id:
            prefs["active_planning_track_output_id"] = ""
        goal.execution_preferences = prefs
        goal.updated_at = time.time()
        goal_repo.save(goal)
        return {"goal_id": goal_id, "rejected_output_ids": rejected}

    def execute_next_plan_task(self, *, goal_id: str, output_artifact_id: str, worker_id: str = "operator_tui") -> dict[str, Any]:
        graph, output, payload = self._load_output(goal_id=goal_id, output_artifact_id=output_artifact_id)
        ext = dict(output.get("extensions") or {})
        mapped = dict(ext.get("task_mapping") or {})
        if not mapped:
            materialized = self.materialize_tasks(goal_id=goal_id, output_artifact_id=output_artifact_id)
            mapped = dict(materialized.get("plan_task_to_internal_task") or {})

        tasks = [dict(item) for item in list(payload.get("tasks") or []) if isinstance(item, dict)]
        next_task = next((item for item in tasks if str(item.get("status") or "") in {"todo", "in_progress"}), None)
        if next_task is None:
            raise ValueError("no_executable_plan_task")
        plan_task_id = str(next_task.get("id") or "").strip()
        internal_task_id = str(mapped.get(plan_task_id) or "")
        if not internal_task_id:
            raise ValueError("plan_task_not_materialized")

        update_local_task_status(
            internal_task_id,
            "in_progress",
            event_type="planning_track_execution_started",
            event_actor="operator_tui",
            event_details={"goal_id": goal_id, "plan_task_id": plan_task_id, "output_artifact_id": output_artifact_id},
            force=True,
        )
        self._artifact_service.upsert_execution_provenance(
            goal_id=goal_id,
            provenance={
                "schema": "execution_provenance.v1",
                "provenance_id": f"prov-{hashlib.sha1(f'{goal_id}:{output_artifact_id}:{plan_task_id}:exec'.encode('utf-8')).hexdigest()[:16]}",
                "goal_id": goal_id,
                "task_id": internal_task_id,
                "execution_id": f"exec-{hashlib.sha1(f'{internal_task_id}:exec'.encode('utf-8')).hexdigest()[:14]}",
                "worker_id": worker_id,
                "worker_kind": "planner_execution",
                "runtime_target_ref": {"runtime_type": "ananta-worker", "location": "local"},
                "model_ref": {"provider_id": "none", "model_id": "manual"},
                "config_refs": {
                    "worker_config_ref": "cfg:planning-track-execution",
                    "runtime_config_ref": "cfg:planning-track-execution",
                    "model_config_ref": "cfg:none",
                    "policy_config_ref": "cfg:planning-track-execution",
                },
                "prompt_refs": {"no_prompt_reason": "plan_task_execution_handoff"},
                "input_usage_refs": list(output.get("input_usage_refs") or []),
                "output_artifact_refs": [output_artifact_id],
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "extensions": {
                    "plan_task_id": plan_task_id,
                    "planning_output_id": output_artifact_id,
                },
            },
        )
        return {
            "goal_id": goal_id,
            "output_artifact_id": output_artifact_id,
            "plan_task_id": plan_task_id,
            "internal_task_id": internal_task_id,
            "status": "in_progress",
        }

    def sync_plan_status_from_internal_task(
        self,
        *,
        goal_id: str,
        output_artifact_id: str,
        plan_task_id: str,
        internal_status: str,
    ) -> dict[str, Any]:
        graph, output, payload = self._load_output(goal_id=goal_id, output_artifact_id=output_artifact_id)
        ext = dict(output.get("extensions") or {})
        tasks = [dict(item) for item in list(payload.get("tasks") or []) if isinstance(item, dict)]
        updated = False
        mapped_status = {
            "completed": "done",
            "in_progress": "in_progress",
            "failed": "blocked",
            "blocked": "blocked",
            "todo": "todo",
        }.get(str(internal_status or "").strip(), "todo")
        for task in tasks:
            if str(task.get("id") or "").strip() != plan_task_id:
                continue
            task["status"] = mapped_status
            updated = True
            break
        if not updated:
            raise ValueError("plan_task_not_found")

        payload["tasks"] = tasks
        payload["tasks_status_summary"] = compute_tasks_status_summary(payload)
        ext["payload"] = payload
        output["extensions"] = ext

        outputs = list(graph.get("output_artifacts") or [])
        for idx, row in enumerate(outputs):
            if isinstance(row, dict) and str(row.get("output_artifact_id") or "") == output_artifact_id:
                outputs[idx] = output
                break
        graph["output_artifacts"] = outputs
        graph["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self._artifact_repo.save_graph(graph)
        return {"goal_id": goal_id, "output_artifact_id": output_artifact_id, "plan_task_id": plan_task_id, "status": mapped_status}
