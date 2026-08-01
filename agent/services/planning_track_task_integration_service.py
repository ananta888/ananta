from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import Any

from agent.artifacts.goal_artifact_repository import GoalArtifactRepository
from agent.artifacts.goal_artifact_service import GoalArtifactService
from agent.db_models import TaskDB
from agent.repository import goal_repo, task_repo
from agent.services.planning_track_contract_service import planning_contract_hash
from agent.services.planning_track_pipeline_service import compute_tasks_status_summary
from agent.services.task_runtime_service import update_local_task_status

_MATERIALIZATION_LOCK = threading.RLock()


def _stable_hash(payload: Any) -> str:
    rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _canonical_track_file(payload: dict[str, Any]) -> str:
    explicit = str(payload.get("source_track_file") or payload.get("track_file") or "").strip()
    if explicit:
        return explicit
    track = str(payload.get("track") or "").strip()
    return f"todo.{track}.json" if track else ""


def _split_dependency_ref(value: str) -> tuple[str, str] | None:
    text = str(value or "").strip()
    if ":" not in text:
        return None
    file_name, task_id = (part.strip() for part in text.split(":", 1))
    if not file_name.endswith(".json") or not task_id:
        return None
    return file_name, task_id


def _runtime_status_from_plan(value: Any) -> str:
    return {
        "done": "completed",
        "completed": "completed",
        "in_progress": "in_progress",
        "blocked": "blocked",
        "failed": "failed",
        "todo": "todo",
    }.get(str(value or "todo").strip(), "todo")


def _plan_status_from_runtime(value: Any) -> str:
    return {
        "completed": "done",
        "in_progress": "in_progress",
        "failed": "blocked",
        "cancelled": "blocked",
        "verification_failed": "blocked",
        "blocked": "blocked",
        "blocked_by_dependency": "blocked",
        "todo": "todo",
        "created": "todo",
        "assigned": "in_progress",
        "delegated": "in_progress",
    }.get(str(value or "").strip(), "todo")


class PlanningDependencyResolver:
    """Resolve plan references to materialized Hub tasks without phantoms."""

    def __init__(self, *, goal_id: str, graph: dict[str, Any], current_output: dict[str, Any]) -> None:
        self._goal_id = goal_id
        self._graph = graph
        self._current_output = current_output

    def resolve(
        self,
        *,
        dependency_ref: str,
        local_mapping: dict[str, str],
        readiness_gates: dict[str, str],
        prior_bindings: dict[str, Any],
    ) -> tuple[str, dict[str, str] | None]:
        parsed = _split_dependency_ref(dependency_ref)
        if parsed is None:
            internal_id = str(local_mapping.get(dependency_ref) or "")
            if not internal_id:
                raise ValueError(f"local_dependency_unresolved:{dependency_ref}")
            return internal_id, None

        file_name, plan_task_id = parsed
        gate_plan_id = str(readiness_gates.get(dependency_ref) or "").strip()
        if gate_plan_id:
            gate_internal_id = str(local_mapping.get(gate_plan_id) or "")
            if not gate_internal_id:
                raise ValueError(f"cross_track_readiness_gate_unresolved:{dependency_ref}")
            return gate_internal_id, {
                "resolution": "readiness_gate",
                "dependency_ref": dependency_ref,
                "internal_task_id": gate_internal_id,
            }

        pinned = prior_bindings.get(dependency_ref)
        pinned_output_id = str(dict(pinned).get("output_artifact_id") or "") if isinstance(pinned, dict) else ""
        candidate_output_id = pinned_output_id or self._active_output_id(file_name)
        if not candidate_output_id:
            raise ValueError(f"cross_track_parent_not_active:{dependency_ref}")
        output = self._output_by_id(candidate_output_id)
        if output is None:
            raise ValueError(f"cross_track_parent_missing:{dependency_ref}")
        self._verify_parent(output=output, expected_file=file_name, allow_pinned=bool(pinned_output_id))
        ext = dict(output.get("extensions") or {})
        mapping = dict(ext.get("task_mapping") or {})
        internal_id = str(mapping.get(plan_task_id) or "")
        if not internal_id or task_repo.get_by_id(internal_id) is None:
            raise ValueError(f"cross_track_dependency_not_materialized:{dependency_ref}")
        return internal_id, {
            "resolution": "active_parent_output",
            "dependency_ref": dependency_ref,
            "output_artifact_id": candidate_output_id,
            "content_hash": str(output.get("content_hash") or ""),
            "internal_task_id": internal_id,
        }

    def _active_output_id(self, file_name: str) -> str:
        goal = goal_repo.get_by_id(self._goal_id)
        if goal is None:
            return ""
        preferences = dict(goal.execution_preferences or {})
        by_track = dict(preferences.get("active_planning_track_outputs") or {})
        if str(by_track.get(file_name) or ""):
            return str(by_track[file_name])
        legacy_id = str(preferences.get("active_planning_track_output_id") or "")
        legacy = self._output_by_id(legacy_id)
        if legacy is not None:
            payload = dict(dict(legacy.get("extensions") or {}).get("payload") or {})
            if _canonical_track_file(payload) == file_name:
                return legacy_id
        return ""

    def _output_by_id(self, output_id: str) -> dict[str, Any] | None:
        return next(
            (
                dict(item)
                for item in list(self._graph.get("output_artifacts") or [])
                if isinstance(item, dict) and str(item.get("output_artifact_id") or "") == output_id
            ),
            None,
        )

    def _verify_parent(self, *, output: dict[str, Any], expected_file: str, allow_pinned: bool) -> None:
        ext = dict(output.get("extensions") or {})
        payload = dict(ext.get("payload") or {})
        if _canonical_track_file(payload) != expected_file:
            raise ValueError(f"cross_track_parent_track_mismatch:{expected_file}")
        if (
            str(output.get("artifact_type") or "") != "planning_track"
            or str(output.get("status") or "") != "created"
            or str(output.get("verification_status") or "") != "valid"
            or not bool(ext.get("active_plan_candidate", False))
        ):
            raise ValueError(f"cross_track_parent_failed:{expected_file}")
        if str(ext.get("schema_hash") or "") != planning_contract_hash():
            raise ValueError(f"cross_track_parent_stale_schema:{expected_file}")
        if str(output.get("content_hash") or "") != _stable_hash(payload):
            raise ValueError(f"cross_track_parent_integrity_failed:{expected_file}")
        provenance_id = str(output.get("provenance_id") or "")
        provenances = list(dict(self._graph.get("extensions") or {}).get("execution_provenance") or [])
        provenance_valid = any(
            isinstance(item, dict)
            and str(item.get("provenance_id") or "") == provenance_id
            and str(output.get("output_artifact_id") or "") in list(item.get("output_artifact_refs") or [])
            for item in provenances
        )
        if not provenance_valid:
            raise ValueError(f"cross_track_parent_provenance_invalid:{expected_file}")
        if not allow_pinned and self._active_output_id(expected_file) != str(output.get("output_artifact_id") or ""):
            raise ValueError(f"cross_track_parent_not_active:{expected_file}")


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

    def _append_handoff_events_for_step(
        self,
        *,
        task_payload: TaskDB,
        goal_id: str,
        output_artifact_id: str,
        plan_task_id: str,
        workflow_step: dict[str, Any],
        depends_internal_ids: list[str],
    ) -> None:
        """WFG-015: emit a handoff event per dependency edge for one
        materialised step. Idempotent: re-running materialization for
        the same task does not double-emit, because the workflow
        event service dedupes by deterministic event id.
        """
        from agent.services.workflow_event_service import (
            append_handoff_to_task,
            build_handoff_event,
            record_handoff_to_audit_log,
        )

        step_id = str(workflow_step.get("step_id") or plan_task_id).strip()
        step_role = str(workflow_step.get("role") or "").strip()
        produces = list(workflow_step.get("produces") or [])
        is_gate = bool(workflow_step.get("gate", False))
        existing_ctx = dict(task_payload.worker_execution_context or {})
        emitted: list[Any] = []
        for dep_task_id in depends_internal_ids:
            dep_step_id = str(dep_task_id)
            event = build_handoff_event(
                goal_id=goal_id,
                plan_id=output_artifact_id,
                workflow_id=str(existing_ctx.get("workflow_step", {}).get("workflow_id") or output_artifact_id),
                from_step=dep_step_id,
                to_step=step_id,
                from_role="",
                to_role=step_role,
                task_ids=[str(task_payload.id)],
                artifact_refs=produces,
                gate_required=is_gate,
                gate_task_id=str(task_payload.id) if is_gate else None,
                status="created",
                reason_code="materialized",
                actor="system:planning_track_integration",
                blueprint_id=str(existing_ctx.get("workflow_step", {}).get("blueprint_id") or ""),
                blueprint_version=str(existing_ctx.get("workflow_step", {}).get("blueprint_version") or ""),
            )
            new_ctx = append_handoff_to_task(
                task={"worker_execution_context": existing_ctx},
                event=event,
            )
            existing_ctx = new_ctx
            emitted.append(event)
        if emitted:
            task_payload.worker_execution_context = existing_ctx
            for event in emitted:
                record_handoff_to_audit_log(event=event)

    def _load_output(
        self, *, goal_id: str, output_artifact_id: str
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
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
        with _MATERIALIZATION_LOCK:
            return self._materialize_tasks_locked(goal_id=goal_id, output_artifact_id=output_artifact_id)

    def _materialize_tasks_locked(self, *, goal_id: str, output_artifact_id: str) -> dict[str, Any]:
        graph, output, payload = self._load_output(goal_id=goal_id, output_artifact_id=output_artifact_id)
        from agent.repository import goal_repo

        goal_scope = goal_repo.get_by_id(goal_id)
        tasks = [dict(item) for item in list(payload.get("tasks") or []) if isinstance(item, dict)]
        mapping: dict[str, str] = {
            plan_task_id: _task_id_for_plan_task(
                goal_id=goal_id,
                output_artifact_id=output_artifact_id,
                plan_task_id=plan_task_id,
            )
            for item in tasks
            if (plan_task_id := str(item.get("id") or "").strip())
        }
        created_ids: list[str] = []
        ext = dict(output.get("extensions") or {})
        prior_bindings = dict(ext.get("cross_track_dependency_bindings") or {})
        readiness_gates = {
            str(ref).strip(): str(gate).strip()
            for ref, gate in dict(payload.get("cross_track_readiness_gates") or {}).items()
            if str(ref).strip() and str(gate).strip()
        }
        resolver = PlanningDependencyResolver(goal_id=goal_id, graph=graph, current_output=output)
        resolved_bindings: dict[str, dict[str, str]] = {}

        for plan_task in tasks:
            plan_task_id = str(plan_task.get("id") or "").strip()
            if not plan_task_id:
                continue
            internal_task_id = mapping[plan_task_id]
            existing = task_repo.get_by_id(internal_task_id)
            title = str(plan_task.get("title") or f"Plan Task {plan_task_id}")[:200]
            acceptance = [
                str(item).strip() for item in list(plan_task.get("acceptance_criteria") or []) if str(item).strip()
            ]
            description = str(plan_task.get("description") or "").strip()
            if acceptance:
                description = (description + "\n\nAcceptance:\n- " + "\n- ".join(acceptance)).strip()
            milestone_id = str(plan_task.get("milestone_id") or "").strip()
            depends_plan_ids = [
                str(item).strip() for item in list(plan_task.get("depends_on") or []) if str(item).strip()
            ]
            depends_internal_ids: list[str] = []
            for dependency_ref in depends_plan_ids:
                internal_dependency_id, binding = resolver.resolve(
                    dependency_ref=dependency_ref,
                    local_mapping=mapping,
                    readiness_gates=readiness_gates,
                    prior_bindings=prior_bindings,
                )
                if internal_dependency_id not in depends_internal_ids:
                    depends_internal_ids.append(internal_dependency_id)
                if binding is not None:
                    resolved_bindings[dependency_ref] = binding
            workflow_step = _extract_workflow_step(plan_task)
            required_capabilities = list(workflow_step.get("required_capabilities") or [])
            task_payload = existing or TaskDB(id=internal_task_id, created_at=time.time())
            task_payload.title = title
            task_payload.description = description
            if existing is None:
                task_payload.status = _runtime_status_from_plan(plan_task.get("status"))
            # WFG-007: gate tasks remain blocked until their depends_on are
            # satisfied; otherwise the queue lets them run too early.
            if bool(workflow_step.get("gate", False)) and task_payload.status == "todo":
                task_payload.status = "blocked"
            task_payload.priority = str(plan_task.get("priority") or "Medium")
            task_payload.goal_id = goal_id
            if goal_scope is not None:
                task_payload.tenant_id = getattr(goal_scope, "tenant_id", None)
                task_payload.project_id = getattr(goal_scope, "project_id", None)
                task_payload.team_id = getattr(goal_scope, "team_id", None)
            task_payload.plan_id = output_artifact_id
            task_payload.plan_node_id = plan_task_id
            workflow_task_kind = str(workflow_step.get("task_kind") or "").strip()
            task_payload.task_kind = workflow_task_kind or _normalize_task_kind(str(plan_task.get("type") or ""))
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
            raw_pattern_hints = plan_task.get("pattern_hints_normalized")
            if isinstance(raw_pattern_hints, dict) and raw_pattern_hints:
                existing_ctx["pattern_hints_normalized"] = dict(raw_pattern_hints)
            # WFG-015: persist a handoff event per dependency edge so the
            # audit query (WFG-017) and the UI views (WFG-022/023) can
            # render the chain from a single source.
            if workflow_step and depends_internal_ids:
                self._append_handoff_events_for_step(
                    task_payload=task_payload,
                    goal_id=goal_id,
                    output_artifact_id=output_artifact_id,
                    plan_task_id=plan_task_id,
                    workflow_step=workflow_step,
                    depends_internal_ids=depends_internal_ids,
                )
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

        ext["task_mapping"] = dict(mapping)
        ext["materialized_task_ids"] = list(created_ids)
        ext["cross_track_dependency_bindings"] = resolved_bindings
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
            active_outputs = dict(prefs.get("active_planning_track_outputs") or {})
            active_outputs[_canonical_track_file(payload)] = output_artifact_id
            prefs["active_planning_track_outputs"] = active_outputs
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
        rejected = [
            str(item).strip()
            for item in list(prefs.get("rejected_planning_track_output_ids") or [])
            if str(item).strip()
        ]
        if output_artifact_id not in rejected:
            rejected.append(output_artifact_id)
        prefs["rejected_planning_track_output_ids"] = rejected
        if str(prefs.get("active_planning_track_output_id") or "") == output_artifact_id:
            prefs["active_planning_track_output_id"] = ""
        active_outputs = {
            file_name: active_output_id
            for file_name, active_output_id in dict(prefs.get("active_planning_track_outputs") or {}).items()
            if str(active_output_id) != output_artifact_id
        }
        prefs["active_planning_track_outputs"] = active_outputs
        goal.execution_preferences = prefs
        goal.updated_at = time.time()
        goal_repo.save(goal)
        return {"goal_id": goal_id, "rejected_output_ids": rejected}

    def _refresh_payload_statuses(
        self,
        *,
        graph: dict[str, Any],
        output: dict[str, Any],
        payload: dict[str, Any],
        mapping: dict[str, str],
    ) -> tuple[dict[str, Any], bool]:
        tasks = [dict(item) for item in list(payload.get("tasks") or []) if isinstance(item, dict)]
        changed = False
        for task in tasks:
            plan_task_id = str(task.get("id") or "").strip()
            internal_task = task_repo.get_by_id(str(mapping.get(plan_task_id) or ""))
            if internal_task is None:
                continue
            refreshed = _plan_status_from_runtime(internal_task.status)
            if str(task.get("status") or "") != refreshed:
                task["status"] = refreshed
                changed = True
        if not changed:
            return payload, False
        payload["tasks"] = tasks
        payload["tasks_status_summary"] = compute_tasks_status_summary(payload)
        ext = dict(output.get("extensions") or {})
        ext["payload"] = payload
        output["extensions"] = ext
        outputs = list(graph.get("output_artifacts") or [])
        for index, item in enumerate(outputs):
            if isinstance(item, dict) and str(item.get("output_artifact_id") or "") == str(
                output.get("output_artifact_id") or ""
            ):
                outputs[index] = output
                break
        graph["output_artifacts"] = outputs
        graph["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self._artifact_repo.save_graph(graph)
        return payload, True

    def execute_next_plan_task(
        self, *, goal_id: str, output_artifact_id: str, worker_id: str = "operator_tui"
    ) -> dict[str, Any]:
        graph, output, payload = self._load_output(goal_id=goal_id, output_artifact_id=output_artifact_id)
        ext = dict(output.get("extensions") or {})
        mapped = dict(ext.get("task_mapping") or {})
        if not mapped:
            materialized = self.materialize_tasks(goal_id=goal_id, output_artifact_id=output_artifact_id)
            mapped = dict(materialized.get("plan_task_to_internal_task") or {})
            graph, output, payload = self._load_output(goal_id=goal_id, output_artifact_id=output_artifact_id)

        payload, _ = self._refresh_payload_statuses(
            graph=graph,
            output=output,
            payload=payload,
            mapping=mapped,
        )
        tasks = [dict(item) for item in list(payload.get("tasks") or []) if isinstance(item, dict)]
        next_task: dict[str, Any] | None = None
        selected_runtime_task: TaskDB | None = None
        for candidate in tasks:
            candidate_id = str(candidate.get("id") or "").strip()
            runtime_task = task_repo.get_by_id(str(mapped.get(candidate_id) or ""))
            if runtime_task is None:
                continue
            dependencies = [task_repo.get_by_id(str(dep)) for dep in list(runtime_task.depends_on or [])]
            if any(dependency is None or str(dependency.status) != "completed" for dependency in dependencies):
                continue
            runtime_status = str(runtime_task.status or "")
            if runtime_status == "in_progress":
                next_task = candidate
                selected_runtime_task = runtime_task
                break
            if runtime_status not in {
                "todo",
                "created",
                "assigned",
                "proposing",
                "updated",
                "blocked",
                "blocked_by_dependency",
            }:
                continue
            if runtime_status in {"blocked", "blocked_by_dependency"}:
                update_local_task_status(
                    str(runtime_task.id),
                    "todo",
                    event_type="planning_track_dependencies_resolved",
                    event_actor="hub:planning_track_integration",
                    event_details={"goal_id": goal_id, "output_artifact_id": output_artifact_id},
                    force=False,
                )
                runtime_task = task_repo.get_by_id(str(runtime_task.id))
                if runtime_task is None or str(runtime_task.status) != "todo":
                    continue
            next_task = candidate
            selected_runtime_task = runtime_task
            break
        if next_task is None or selected_runtime_task is None:
            raise ValueError("no_executable_plan_task")
        plan_task_id = str(next_task.get("id") or "").strip()
        internal_task_id = str(mapped.get(plan_task_id) or "")
        if not internal_task_id:
            raise ValueError("plan_task_not_materialized")

        if str(selected_runtime_task.status) != "in_progress":
            update_local_task_status(
                internal_task_id,
                "in_progress",
                event_type="planning_track_execution_started",
                event_actor=worker_id,
                event_details={
                    "goal_id": goal_id,
                    "plan_task_id": plan_task_id,
                    "output_artifact_id": output_artifact_id,
                },
                force=False,
            )
            updated_task = task_repo.get_by_id(internal_task_id)
            if updated_task is None or str(updated_task.status) != "in_progress":
                raise ValueError("no_executable_plan_task")
        provenance_seed = f"{goal_id}:{output_artifact_id}:{plan_task_id}:exec"
        provenance_id = f"prov-{hashlib.sha1(provenance_seed.encode('utf-8')).hexdigest()[:16]}"
        self._artifact_service.upsert_execution_provenance(
            goal_id=goal_id,
            provenance={
                "schema": "execution_provenance.v1",
                "provenance_id": provenance_id,
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
        return {
            "goal_id": goal_id,
            "output_artifact_id": output_artifact_id,
            "plan_task_id": plan_task_id,
            "status": mapped_status,
        }
