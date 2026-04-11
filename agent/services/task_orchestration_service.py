from __future__ import annotations

import json
import re
import uuid
from typing import Any

from flask import current_app, has_app_context

from agent.common.api_envelope import unwrap_api_envelope
from agent.config import settings
from agent.research_backend import resolve_research_backend_config
from agent.routes.tasks.orchestration_policy import (
    derive_required_capabilities,
    evaluate_worker_routing_policy,
    persist_policy_decision,
)
from agent.routes.tasks.orchestration_policy.read_model import build_orchestration_read_model
from agent.services.context_bundle_service import get_context_bundle_service
from agent.services.hub_llm_service import get_hub_llm_service
from agent.services.task_execution_policy_service import normalize_allowed_tools
from agent.services.repository_registry import get_repository_registry
from agent.services.task_execution_tracking_service import get_task_execution_tracking_service
from agent.services.task_runtime_service import forward_to_worker, get_local_task_status, update_local_task_status


def build_copilot_routing_prompt(
    *,
    task: dict[str, Any],
    task_kind: str | None,
    required_capabilities: list[str] | None,
    workers: list[dict[str, Any]],
) -> str:
    worker_rows = []
    for worker in workers:
        execution_limits = dict(worker.get("execution_limits") or {})
        worker_rows.append(
            {
                "url": worker.get("url"),
                "status": worker.get("status"),
                "worker_roles": list(worker.get("worker_roles") or []),
                "capabilities": list(worker.get("capabilities") or []),
                "current_load": worker.get("current_load"),
                "max_parallel_tasks": execution_limits.get("max_parallel_tasks"),
                "success_rate": dict(worker.get("routing_signals") or {}).get(
                    "success_rate",
                    worker.get("success_rate", dict(worker.get("metrics") or {}).get("success_rate")),
                ),
                "quality_rate": dict(worker.get("routing_signals") or {}).get(
                    "quality_rate",
                    worker.get("quality_rate", dict(worker.get("metrics") or {}).get("quality_rate")),
                ),
                "security_level": worker.get("security_level") or worker.get("security_tier"),
                "registration_validated": worker.get("registration_validated"),
                "available_for_routing": worker.get("available_for_routing"),
            }
        )
    prompt_payload = {
        "task": {
            "id": task.get("id"),
            "title": task.get("title"),
            "description": task.get("description"),
            "task_kind": task_kind or task.get("task_kind"),
            "required_capabilities": list(required_capabilities or []),
        },
        "workers": worker_rows,
        "instructions": {
            "goal": "Gib nur einen strategischen Routing-Hinweis fuer den Hub. Du delegierst keine Ausfuehrung selbst.",
            "constraints": [
                "Antworte ausschliesslich als JSON.",
                "Waehle suggested_worker_url nur aus der uebergebenen Worker-Liste.",
                "Wenn kein klarer Hinweis moeglich ist, setze suggested_worker_url auf null.",
                "Der Hinweis dient nur als Advisor fuer Routing/Governance und ersetzt keine Policy-Entscheidung.",
            ],
            "response_schema": {
                "suggested_worker_url": "string|null",
                "reasoning": "string",
                "confidence": "number_between_0_and_1",
            },
        },
    }
    return json.dumps(prompt_payload, ensure_ascii=True, indent=2)


def extract_copilot_routing_hint(raw_text: str, worker_urls: list[str]) -> dict[str, Any] | None:
    text = str(raw_text or "").strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None

    suggested_worker_url = payload.get("suggested_worker_url")
    if suggested_worker_url is not None:
        suggested_worker_url = str(suggested_worker_url).strip() or None
    known_urls = {str(url).strip() for url in worker_urls if str(url).strip()}
    if suggested_worker_url and suggested_worker_url not in known_urls:
        suggested_worker_url = None

    reasoning = str(payload.get("reasoning") or "").strip() or None
    try:
        confidence = float(payload.get("confidence")) if payload.get("confidence") is not None else None
    except (TypeError, ValueError):
        confidence = None
    if confidence is not None:
        confidence = max(0.0, min(1.0, confidence))

    return {
        "suggested_worker_url": suggested_worker_url,
        "reasoning": reasoning,
        "confidence": confidence,
        "raw_response": text,
    }


class TaskOrchestrationService:
    """Hub-owned orchestration use-cases for delegation, completion, and orchestration read models."""

    @staticmethod
    def _safe_scope_segment(value: str | None, *, fallback: str) -> str:
        raw = str(value or "").strip().lower()
        if not raw:
            return fallback
        normalized = re.sub(r"[^a-z0-9._-]+", "-", raw).strip("-")
        return normalized or fallback

    def _resolve_workspace_reuse_mode(self) -> str:
        agent_cfg = current_app.config.get("AGENT_CONFIG", {}) if has_app_context() else {}
        worker_runtime = (agent_cfg or {}).get("worker_runtime")
        worker_runtime = worker_runtime if isinstance(worker_runtime, dict) else {}
        mode = str(worker_runtime.get("workspace_reuse_mode") or "goal_worker").strip().lower()
        return mode if mode in {"task", "goal_worker"} else "goal_worker"

    def _derive_workspace_scope(
        self,
        *,
        parent_task: dict[str, Any],
        subtask_id: str,
        worker_job_id: str,
        agent_url: str | None,
    ) -> dict[str, str]:
        mode = self._resolve_workspace_reuse_mode()
        worker_key = self._safe_scope_segment(agent_url, fallback="worker")
        if mode == "task":
            scope_key = f"{subtask_id}:{worker_job_id}"
            return {
                "mode": "task",
                "scope_key": scope_key,
                "session_scope_kind": "task",
                "session_scope_key": f"task:{subtask_id}",
            }

        goal_key = self._safe_scope_segment(str(parent_task.get("goal_id") or "").strip(), fallback="")
        team_key = self._safe_scope_segment(str(parent_task.get("team_id") or "").strip(), fallback="")
        task_key = self._safe_scope_segment(str(parent_task.get("id") or "").strip(), fallback="task")
        continuity_key = goal_key or team_key or task_key
        scope_key = f"{worker_key}:{continuity_key}"
        return {
            "mode": "goal_worker",
            "scope_key": scope_key,
            "session_scope_kind": "workspace",
            "session_scope_key": f"workspace:{scope_key}",
        }

    def _resolve_context_bundle_policy(self) -> dict[str, Any]:
        agent_cfg = current_app.config.get("AGENT_CONFIG", {}) if has_app_context() else {}
        return get_context_bundle_service().resolve_context_bundle_policy((agent_cfg or {}).get("context_bundle_policy"))

    def _default_retrieval_hints_for_task_kind(self, task_kind: str | None) -> dict[str, str]:
        normalized = str(task_kind or "").strip().lower()
        if normalized in {"bugfix", "testing", "test"}:
            return {
                "retrieval_intent": "localize_failure_and_fix",
                "required_context_scope": "local_code_and_failure_neighbors",
                "preferred_bundle_mode": "standard",
            }
        if normalized in {"refactor", "implement", "coding"}:
            return {
                "retrieval_intent": "symbol_and_dependency_neighborhood",
                "required_context_scope": "module_and_related_symbols",
                "preferred_bundle_mode": "standard",
            }
        if normalized in {"architecture", "analysis", "doc", "research"}:
            return {
                "retrieval_intent": "architecture_and_decision_context",
                "required_context_scope": "cross_module_docs_and_contracts",
                "preferred_bundle_mode": "full",
            }
        if normalized in {"config", "xml", "ops"}:
            return {
                "retrieval_intent": "configuration_contracts_and_runtime_edges",
                "required_context_scope": "config_and_integration_points",
                "preferred_bundle_mode": "standard",
            }
        return {
            "retrieval_intent": "execution_focused_context",
            "required_context_scope": "task_and_direct_neighbors",
            "preferred_bundle_mode": "standard",
        }

    def _derive_retrieval_hints(self, *, parent_task: dict[str, Any], data: Any, effective_task_kind: str | None) -> dict[str, str]:
        defaults = self._default_retrieval_hints_for_task_kind(effective_task_kind)
        retrieval_intent = (
            str(getattr(data, "retrieval_intent", None) or "").strip()
            or str(parent_task.get("retrieval_intent") or "").strip()
            or defaults["retrieval_intent"]
        )
        required_context_scope = (
            str(getattr(data, "required_context_scope", None) or "").strip()
            or str(parent_task.get("required_context_scope") or "").strip()
            or defaults["required_context_scope"]
        )
        preferred_bundle_mode = (
            str(getattr(data, "preferred_bundle_mode", None) or "").strip().lower()
            or str(parent_task.get("preferred_bundle_mode") or "").strip().lower()
            or defaults["preferred_bundle_mode"]
        )
        if preferred_bundle_mode not in {"compact", "standard", "full"}:
            preferred_bundle_mode = defaults["preferred_bundle_mode"]
        return {
            "retrieval_intent": retrieval_intent,
            "required_context_scope": required_context_scope,
            "preferred_bundle_mode": preferred_bundle_mode,
        }

    def _derive_task_neighborhood(self, *, parent_task: dict[str, Any]) -> dict[str, list[str]]:
        parent_task_id = str(parent_task.get("id") or "").strip()
        goal_id = str(parent_task.get("goal_id") or "").strip()
        parent_parent_id = str(parent_task.get("parent_task_id") or "").strip()
        depends_on = [str(item).strip() for item in list(parent_task.get("depends_on") or []) if str(item).strip()]

        repos = get_repository_registry()
        task_rows = repos.task_repo.get_all()
        sibling_ids: list[str] = []
        completed_neighbor_ids: list[str] = []
        dependent_task_ids: list[str] = []
        for row in task_rows:
            item = row.model_dump()
            item_id = str(item.get("id") or "").strip()
            if not item_id or item_id == parent_task_id:
                continue
            item_depends_on = [str(dep).strip() for dep in list(item.get("depends_on") or []) if str(dep).strip()]
            if parent_task_id and parent_task_id in item_depends_on:
                dependent_task_ids.append(item_id)
            if parent_parent_id and str(item.get("parent_task_id") or "").strip() == parent_parent_id:
                sibling_ids.append(item_id)
            if goal_id and str(item.get("goal_id") or "").strip() == goal_id:
                status = str(item.get("status") or "").strip().lower()
                if status in {"completed", "failed"}:
                    completed_neighbor_ids.append(item_id)

        ordered_neighbors: list[str] = []
        for task_id in [*depends_on, *dependent_task_ids, *sibling_ids, *completed_neighbor_ids]:
            if task_id and task_id != parent_task_id and task_id not in ordered_neighbors:
                ordered_neighbors.append(task_id)
            if len(ordered_neighbors) >= 12:
                break
        return {
            "depends_on_task_ids": depends_on,
            "dependent_task_ids": dependent_task_ids[:12],
            "sibling_task_ids": sibling_ids[:12],
            "completed_neighbor_task_ids": completed_neighbor_ids[:12],
            "neighbor_task_ids": ordered_neighbors[:12],
        }

    def _resolve_copilot_routing_hint(
        self,
        *,
        task: dict[str, Any],
        workers: list[dict[str, Any]],
        task_kind: str | None,
        required_capabilities: list[str] | None,
    ) -> dict[str, Any] | None:
        hub_llm = get_hub_llm_service()
        copilot_config = hub_llm.resolve_copilot_config()
        if not copilot_config.get("active") or not copilot_config.get("supports_routing"):
            return None
        prompt = build_copilot_routing_prompt(
            task=task,
            task_kind=task_kind,
            required_capabilities=required_capabilities,
            workers=workers,
        )
        try:
            result = hub_llm.route_with_copilot(prompt=prompt)
        except Exception:
            return None
        hint = extract_copilot_routing_hint(
            str(result.get("text") or ""),
            worker_urls=[str(worker.get("url") or "") for worker in workers],
        )
        if not hint:
            return None
        return {
            **hint,
            "strategy_mode": copilot_config.get("strategy_mode"),
            "effective_provider": dict(copilot_config.get("effective") or {}).get("provider"),
            "effective_model": dict(copilot_config.get("effective") or {}).get("model"),
        }

    def delegate_task(
        self,
        *,
        task_id: str,
        data: Any,
        worker_job_service,
        worker_contract_service,
        agent_registry_service,
        result_memory_service,
        verification_service,
    ) -> dict[str, Any]:
        parent_task = get_local_task_status(task_id)
        if not parent_task:
            return {"error": "parent_task_not_found", "code": 404}

        agent_url = data.agent_url
        selected_by_policy = False
        selection = None
        policy_decision = None
        routing_hint = None
        effective_task_kind = data.task_kind or parent_task.get("task_kind")
        effective_required_capabilities = data.required_capabilities or parent_task.get("required_capabilities") or derive_required_capabilities(
            parent_task,
            effective_task_kind,
        )
        preferred_backend = (
            resolve_research_backend_config(agent_cfg=current_app.config.get("AGENT_CONFIG", {}) or {}).get("provider")
            if str(effective_task_kind or "").strip().lower() == "research"
            else None
        )
        if not agent_url:
            repos = get_repository_registry()
            available_workers = [
                agent_registry_service.build_directory_entry(agent=worker, timeout=300)
                for worker in repos.agent_repo.get_all()
            ]
            routing_hint = self._resolve_copilot_routing_hint(
                task=parent_task,
                workers=available_workers,
                task_kind=effective_task_kind,
                required_capabilities=effective_required_capabilities,
            )
            selection, _decision = evaluate_worker_routing_policy(
                task=parent_task,
                workers=available_workers,
                decision_type="delegation",
                task_kind=effective_task_kind,
                required_capabilities=effective_required_capabilities,
                task_id=task_id,
                extra_details={"copilot_routing_hint": routing_hint} if routing_hint else None,
            )
            policy_decision = _decision
            agent_url = selection.worker_url
            selected_by_policy = True
            if not agent_url:
                return {
                    "error": "no_worker_available",
                    "code": 409,
                    "data": {"reasons": selection.reasons},
                }

        subtask_id = f"sub-{uuid.uuid4()}"
        my_url = settings.agent_url or f"http://localhost:{settings.port}"
        callback_url = f"{my_url.rstrip('/')}/tasks/{task_id}/subtask-callback"
        context_query = str(data.context_query or "").strip() or " ".join(
            item
            for item in [
                str(parent_task.get("title") or "").strip(),
                str(parent_task.get("description") or "").strip(),
                str(data.subtask_description or "").strip(),
            ]
            if item
        )
        retrieval_hints = self._derive_retrieval_hints(
            parent_task=parent_task,
            data=data,
            effective_task_kind=effective_task_kind,
        )
        task_neighborhood = self._derive_task_neighborhood(parent_task=parent_task)
        context_policy = {
            **self._resolve_context_bundle_policy(),
            "task_kind": effective_task_kind,
            "retrieval_intent": retrieval_hints["retrieval_intent"],
            "required_context_scope": retrieval_hints["required_context_scope"],
            "preferred_bundle_mode": retrieval_hints["preferred_bundle_mode"],
            **task_neighborhood,
        }
        context_bundle = worker_job_service.create_context_bundle(
            query=context_query,
            parent_task_id=task_id,
            goal_id=parent_task.get("goal_id"),
            context_policy=context_policy,
        )
        expected_output_schema = dict(data.expected_output_schema or {})
        allowed_tools = normalize_allowed_tools(data.allowed_tools)
        routing_decision = worker_contract_service.build_routing_decision(
            agent_url=agent_url,
            selected_by_policy=selected_by_policy,
            task_kind=effective_task_kind,
            required_capabilities=effective_required_capabilities,
            selection=selection,
            preferred_backend=preferred_backend,
        )
        if routing_hint:
            routing_decision["copilot_hint"] = dict(routing_hint)
        worker_job = worker_job_service.create_worker_job(
            parent_task_id=task_id,
            subtask_id=subtask_id,
            worker_url=agent_url,
            context_bundle_id=context_bundle.id,
            allowed_tools=allowed_tools,
            expected_output_schema=expected_output_schema,
            metadata=worker_contract_service.build_job_metadata(
                routing_decision=routing_decision,
                task_kind=effective_task_kind,
                required_capabilities=effective_required_capabilities,
                context_policy=context_policy,
                extra_metadata={"selected_by_policy": selected_by_policy},
            ),
        )
        workspace_scope = self._derive_workspace_scope(
            parent_task=parent_task,
            subtask_id=subtask_id,
            worker_job_id=worker_job.id,
            agent_url=agent_url,
        )
        worker_workspace = {
            "mode": "task_scoped_workspace",
            "scope_mode": workspace_scope["mode"],
            "task_id": subtask_id,
            "parent_task_id": task_id,
            "worker_job_id": worker_job.id,
            "agent_url": agent_url,
            "agent_name": str(agent_url or "worker").rstrip("/").split("/")[-1] or "worker",
            "scope_key": workspace_scope["scope_key"],
            "session_scope_kind": workspace_scope["session_scope_kind"],
            "session_scope_key": workspace_scope["session_scope_key"],
        }
        artifact_sync = {
            "enabled": True,
            "sync_to_hub": True,
            "collection_name": "task-execution-results",
            "max_changed_files": 30,
            "max_file_size_bytes": 2 * 1024 * 1024,
        }
        worker_execution_context = worker_contract_service.build_execution_context(
            instructions=data.subtask_description,
            context_bundle=context_bundle,
            context_policy=context_policy,
            workspace=worker_workspace,
            artifact_sync=artifact_sync,
            allowed_tools=allowed_tools,
            expected_output_schema=expected_output_schema,
            routing_decision=routing_decision,
        )

        delegation_payload = {
            "id": subtask_id,
            "title": data.subtask_description[:200],
            "description": data.subtask_description,
            "parent_task_id": task_id,
            "priority": data.priority,
            "team_id": parent_task.get("team_id"),
            "goal_id": parent_task.get("goal_id"),
            "goal_trace_id": parent_task.get("goal_trace_id"),
            "task_kind": effective_task_kind,
            "retrieval_intent": retrieval_hints["retrieval_intent"],
            "required_context_scope": retrieval_hints["required_context_scope"],
            "preferred_bundle_mode": retrieval_hints["preferred_bundle_mode"],
            "required_capabilities": effective_required_capabilities,
            "context_bundle_id": context_bundle.id,
            "worker_execution_context": worker_execution_context,
            "callback_url": callback_url,
            "callback_token": settings.agent_token or "",
            "source": "agent",
            "created_by": settings.agent_name or "hub",
            "context_bundle_policy": dict(context_policy),
        }
        try:
            if not selected_by_policy:
                policy_decision = persist_policy_decision(
                    decision_type="delegation",
                    status="approved",
                    policy_name="worker_capability_routing",
                    policy_version="worker-routing-v2",
                    reasons=(selection.reasons if selection else ["manual_override"]),
                    details={
                        "task_kind": data.task_kind,
                        "required_capabilities": effective_required_capabilities,
                        "manual_override": True,
                        "copilot_routing_hint": routing_hint,
                        "context_bundle_policy": context_policy,
                        "retrieval_hints": retrieval_hints,
                        "task_neighborhood": task_neighborhood,
                    },
                    task_id=task_id,
                    worker_url=agent_url,
                )
            response = unwrap_api_envelope(
                forward_to_worker(agent_url, "/tasks", delegation_payload, token=data.agent_token or "")
            )
        except Exception as exc:
            return {"error": "delegation_failed", "code": 502, "data": {"details": str(exc)}}

        subtasks = list(parent_task.get("subtasks") or [])
        subtasks.append(
            {
                "id": subtask_id,
                "agent_url": agent_url,
                "description": data.subtask_description,
                "status": "created",
            }
        )
        update_local_task_status(
            task_id,
            parent_task.get("status", "in_progress"),
            context_bundle_id=context_bundle.id,
            current_worker_job_id=worker_job.id,
            worker_execution_context=worker_execution_context,
            subtasks=subtasks,
            event_type="task_delegated",
            event_actor="hub",
            event_details={
                "delegated_to": agent_url,
                "subtask_id": subtask_id,
                "context_bundle_id": context_bundle.id,
                "worker_job_id": worker_job.id,
                "policy": "hub_central_queue",
                "selected_by_policy": selected_by_policy,
                "copilot_routing_hint": routing_hint,
                "context_bundle_policy": context_policy,
                "retrieval_hints": retrieval_hints,
                "task_neighborhood": task_neighborhood,
                "workspace_scope": workspace_scope,
                "policy_decision_id": getattr(policy_decision, "id", None),
            },
        )
        return {
            "data": {
                "status": "delegated",
                "subtask_id": subtask_id,
                "agent_url": agent_url,
                "response": response,
                "selected_by_policy": selected_by_policy,
                "selection_reasons": selection.reasons if selection else ["manual_override"],
                "worker_selection": routing_decision,
                "copilot_routing_hint": routing_hint,
                "policy_decision_id": getattr(policy_decision, "id", None),
                "context_bundle_id": context_bundle.id,
                "worker_job_id": worker_job.id,
                "context_bundle_policy": context_policy,
                "retrieval_hints": retrieval_hints,
                "task_neighborhood": task_neighborhood,
                "workspace_scope": workspace_scope,
            }
        }

    def complete_task(
        self,
        *,
        task_id: str,
        payload: dict[str, Any],
        verification_service,
        worker_job_service,
        result_memory_service,
    ) -> dict[str, Any]:
        task = get_local_task_status(task_id)
        if not task:
            return {"error": "not_found", "code": 404}

        gate = payload.get("gate_results") or {}
        all_passed = bool(gate.get("passed", False))
        final_status = "completed" if all_passed else "failed"
        record = verification_service.create_or_update_record(
            task_id,
            trace_id=payload.get("trace_id"),
            output=str(payload.get("output") or ""),
            exit_code=0 if all_passed else 1,
            gate_results=gate,
        )
        worker_job_id = str(payload.get("worker_job_id") or task.get("current_worker_job_id") or "").strip() or None
        actor = str(payload.get("actor") or "system")
        payload_artifacts = payload.get("artifacts") if isinstance(payload.get("artifacts"), list) else None
        if worker_job_id:
            worker_job_service.record_worker_result(
                worker_job_id=worker_job_id,
                task_id=task_id,
                worker_url=actor,
                status=final_status,
                output=str(payload.get("output") or ""),
                metadata={"gate_results": gate, "trace_id": payload.get("trace_id")},
            )
        memory_entry = result_memory_service.record_worker_result_memory(
            task_id=task_id,
            goal_id=task.get("goal_id"),
            trace_id=payload.get("trace_id") or task.get("goal_trace_id"),
            worker_job_id=worker_job_id,
            title=task.get("title"),
            output=str(payload.get("output") or ""),
            artifact_refs=list(payload_artifacts or [{"kind": "task_output", "task_id": task_id, "worker_job_id": worker_job_id}]),
            retrieval_tags=[
                value
                for value in [
                    str(task.get("task_kind") or "").strip(),
                    str(task.get("goal_id") or "").strip(),
                    str(final_status).strip(),
                ]
                if value
            ],
            metadata={"gate_results": gate, "actor": actor},
        )
        verification_status = {
            "record_id": record.id if record else None,
            "status": record.status if record else ("passed" if all_passed else "failed"),
            "retry_count": record.retry_count if record else 0,
            "repair_attempts": record.repair_attempts if record else 0,
            "escalation_reason": record.escalation_reason if record else None,
            "memory_entry_id": memory_entry.id if memory_entry else None,
        }
        update_local_task_status(
            task_id,
            final_status,
            last_output=str(payload.get("output") or ""),
            last_exit_code=0 if all_passed else 1,
            verification_status=verification_status,
            event_type="task_completed_with_gates",
            event_actor=actor,
            event_details={"gate_results": gate, "trace_id": payload.get("trace_id"), "worker_job_id": worker_job_id},
        )
        return {
            "data": {
                "task_id": task_id,
                "status": final_status,
                "gates_passed": all_passed,
                "verification_status": verification_status,
            }
        }

    def orchestration_read_model(self) -> dict[str, Any]:
        repos = get_repository_registry()
        model = build_orchestration_read_model([task.model_dump() for task in repos.task_repo.get_all()])
        model["recent_policy_decisions"] = [item.model_dump() for item in repos.policy_decision_repo.get_all(limit=50)]
        model["worker_execution_reconciliation"] = get_task_execution_tracking_service().build_execution_reconciliation_snapshot()
        return model


task_orchestration_service = TaskOrchestrationService()


def get_task_orchestration_service() -> TaskOrchestrationService:
    return task_orchestration_service
