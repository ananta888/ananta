from __future__ import annotations

from agent.models import WorkerExecutionContextContract, WorkerRoutingDecisionContract
from agent.services.worker_routing_policy_utils import derive_research_specialization
from agent.services.task_execution_policy_service import normalize_allowed_tools
from agent.services.worker_execution_profile_service import normalize_worker_execution_profile


class WorkerContractService:
    """Builds normalized worker routing and execution contracts for hub-owned flows."""

    def build_routing_decision(
        self,
        *,
        agent_url: str | None,
        selected_by_policy: bool,
        task_kind: str | None,
        required_capabilities: list[str] | None,
        selection=None,
        preferred_backend: str | None = None,
    ) -> dict:
        normalized_required = [str(item).strip().lower() for item in (required_capabilities or []) if str(item).strip()]
        normalized_profile = normalize_worker_execution_profile(
            ((selection.metadata if hasattr(selection, "metadata") and isinstance(selection.metadata, dict) else {}) or {}).get(
                "worker_profile"
            )
        )
        reasons = list(getattr(selection, "reasons", None) or (["manual_override"] if agent_url else ["no_worker_available"]))
        return WorkerRoutingDecisionContract(
            worker_url=agent_url,
            selected_by_policy=selected_by_policy,
            strategy=str(getattr(selection, "strategy", None) or ("capability_quality_load_match" if selected_by_policy else "manual_override")),
            reasons=reasons,
            matched_capabilities=list(getattr(selection, "matched_capabilities", None) or []),
            matched_roles=list(getattr(selection, "matched_roles", None) or []),
            task_kind=str(task_kind or "").strip() or None,
            required_capabilities=normalized_required,
            research_specialization=derive_research_specialization(None, task_kind, normalized_required),
            preferred_backend=str(preferred_backend or "").strip() or None,
            worker_profile=normalized_profile,
            profile_source=(
                str(
                    ((selection.metadata if hasattr(selection, "metadata") and isinstance(selection.metadata, dict) else {}) or {}).get(
                        "profile_source"
                    )
                    or ""
                ).strip()
                or None
            ),
        ).model_dump()

    def build_execution_context(
        self,
        *,
        instructions: str,
        context_bundle,
        context_policy: dict | None,
        workspace: dict | None,
        artifact_sync: dict | None,
        allowed_tools: list[str] | None,
        expected_output_schema: dict | None,
        routing_decision: dict | None,
    ) -> dict:
        context_policy_payload = dict(
            context_policy
            or dict(getattr(context_bundle, "bundle_metadata", None) or {}).get("context_policy")
            or {}
        )
        normalized_profile = normalize_worker_execution_profile(
            context_policy_payload.get("worker_profile"),
        )
        profile_source = str(context_policy_payload.get("worker_profile_source") or "agent_default").strip().lower() or "agent_default"
        return WorkerExecutionContextContract(
            instructions=instructions,
            context_bundle_id=getattr(context_bundle, "id", None),
            context={
                "context_text": getattr(context_bundle, "context_text", None),
                "chunks": list(getattr(context_bundle, "chunks", None) or []),
                "token_estimate": int(getattr(context_bundle, "token_estimate", 0) or 0),
                "bundle_metadata": dict(getattr(context_bundle, "bundle_metadata", None) or {}),
            },
            context_policy=context_policy_payload,
            workspace=dict(workspace or {}),
            artifact_sync=dict(artifact_sync or {}),
            allowed_tools=normalize_allowed_tools(allowed_tools),
            expected_output_schema=dict(expected_output_schema or {}),
            worker_profile=normalized_profile,
            profile_source=profile_source,
            routing=dict(routing_decision or {}) or None,
        ).model_dump()

    def build_job_metadata(
        self,
        *,
        routing_decision: dict | None,
        task_kind: str | None,
        required_capabilities: list[str] | None,
        context_policy: dict | None = None,
        extra_metadata: dict | None = None,
    ) -> dict:
        return {
            **dict(extra_metadata or {}),
            "routing_decision": dict(routing_decision or {}),
            "task_kind": str(task_kind or "").strip() or None,
            "required_capabilities": [str(item).strip().lower() for item in (required_capabilities or []) if str(item).strip()],
            "context_policy": dict(context_policy or {}),
            "worker_profile": normalize_worker_execution_profile(dict(context_policy or {}).get("worker_profile")),
            "profile_source": str(dict(context_policy or {}).get("worker_profile_source") or "agent_default").strip().lower()
            or "agent_default",
        }

    def build_standalone_task_contract(
        self,
        *,
        task_id: str,
        goal: str,
        command: str,
        worker_profile: str | None,
        trace_id: str,
        capability_id: str,
        context_hash: str,
        files: list[str] | None = None,
        diffs: list[str] | None = None,
    ) -> dict:
        normalized_profile = normalize_worker_execution_profile(worker_profile)
        return {
            "schema": "standalone_task_contract.v1",
            "task_id": str(task_id or "").strip(),
            "goal": str(goal or "").strip(),
            "command": str(command or "").strip(),
            "worker_profile": normalized_profile,
            "files": [str(item).strip() for item in list(files or []) if str(item).strip()],
            "diffs": [str(item).strip() for item in list(diffs or []) if str(item).strip()],
            "control_manifest": {
                "trace_id": str(trace_id or "").strip(),
                "capability_id": str(capability_id or "").strip(),
                "context_hash": str(context_hash or "").strip(),
            },
        }

    def build_worker_todo_contract(
        self,
        *,
        task_id: str,
        goal_id: str,
        trace_id: str,
        capability_id: str,
        context_hash: str,
        executor_kind: str,
        worker_profile: str | None,
        tasks: list[dict] | None,
        track: str = "worker_subplan",
        todo_version: str = "1.0",
        parent_task_id: str | None = None,
        profile_source: str = "task_context",
        target_worker: str | None = None,
        milestones: list[dict] | None = None,
        status_scale: list[str] | None = None,
        priority_scale: list[str] | None = None,
        risk_scale: list[str] | None = None,
        command: str | None = None,
        runner_prompt: str | None = None,
        mode: str = "assistant_execute",
        workspace_dir: str | None = None,
        allowed_tools: list[str] | None = None,
        enforce_artifacts: bool = True,
        max_steps: int = 20,
    ) -> dict:
        normalized_profile = normalize_worker_execution_profile(worker_profile)
        normalized_executor = str(executor_kind or "").strip().lower() or "custom"
        if normalized_executor not in {"ananta_worker", "opencode", "openai_codex_cli", "custom"}:
            normalized_executor = "custom"
        normalized_profile_source = str(profile_source or "task_context").strip().lower() or "task_context"
        if normalized_profile_source not in {"agent_default", "task_context", "task_override", "runtime_override"}:
            normalized_profile_source = "task_context"
        normalized_tasks: list[dict] = []
        for item in list(tasks or []):
            if not isinstance(item, dict):
                continue
            task_item_id = str(item.get("id") or "").strip()
            title = str(item.get("title") or "").strip()
            instructions = str(item.get("instructions") or item.get("description") or "").strip()
            if not task_item_id or not title or not instructions:
                continue
            normalized_expected_artifacts = []
            for artifact in list(item.get("expected_artifacts") or []):
                if not isinstance(artifact, dict):
                    continue
                kind = str(artifact.get("kind") or "").strip()
                if not kind:
                    continue
                normalized_expected_artifacts.append(
                    {
                        "kind": kind,
                        "required": bool(artifact.get("required", True)),
                        **({"description": str(artifact.get("description")).strip()} if str(artifact.get("description") or "").strip() else {}),
                    }
                )
            normalized_tasks.append(
                {
                    "id": task_item_id,
                    "title": title,
                    "instructions": instructions,
                    "status": str(item.get("status") or "todo").strip().lower() or "todo",
                    **({"priority": str(item.get("priority")).strip()} if str(item.get("priority") or "").strip() else {}),
                    **({"risk": str(item.get("risk")).strip()} if str(item.get("risk") or "").strip() else {}),
                    "depends_on": [str(dep).strip() for dep in list(item.get("depends_on") or item.get("dependencies") or []) if str(dep).strip()],
                    "allowed_tools": normalize_allowed_tools(item.get("allowed_tools") or allowed_tools),
                    "expected_artifacts": normalized_expected_artifacts,
                    "acceptance_criteria": [
                        str(criterion).strip()
                        for criterion in list(item.get("acceptance_criteria") or item.get("acceptance") or [])
                        if str(criterion).strip()
                    ]
                    or ["Task requirements satisfied and expected artifacts returned."],
                    "metadata": dict(item.get("metadata") or {}),
                }
            )
        if not normalized_tasks:
            normalized_tasks = [
                {
                    "id": f"{str(task_id or '').strip()}:execute",
                    "title": "Execute delegated task contract",
                    "instructions": "Execute the delegated task and return required artifacts.",
                    "status": "todo",
                    "depends_on": [],
                    "allowed_tools": normalize_allowed_tools(allowed_tools),
                    "expected_artifacts": [],
                    "acceptance_criteria": ["Execution completed and response schema filled."],
                    "metadata": {},
                }
            ]
        normalized_mode = str(mode or "assistant_execute").strip().lower() or "assistant_execute"
        if normalized_mode not in {"command_execute", "assistant_execute", "plan_only"}:
            normalized_mode = "assistant_execute"
        return {
            "schema": "worker_todo_contract.v1",
            "task_id": str(task_id or "").strip(),
            "goal_id": str(goal_id or "").strip(),
            **({"parent_task_id": str(parent_task_id).strip()} if str(parent_task_id or "").strip() else {}),
            "trace_id": str(trace_id or "").strip(),
            "worker": {
                "executor_kind": normalized_executor,
                "worker_profile": normalized_profile,
                "profile_source": normalized_profile_source,
                **({"target_worker": str(target_worker).strip()} if str(target_worker or "").strip() else {}),
            },
            "todo": {
                "version": str(todo_version or "1.0").strip() or "1.0",
                "track": str(track or "worker_subplan").strip() or "worker_subplan",
                "status_scale": list(status_scale or ["todo", "in_progress", "blocked", "done"]),
                "priority_scale": list(priority_scale or ["critical", "high", "medium", "low"]),
                "risk_scale": list(risk_scale or ["high", "medium", "low"]),
                "milestones": [dict(item) for item in list(milestones or []) if isinstance(item, dict)],
                "tasks": normalized_tasks,
            },
            "execution": {
                "mode": normalized_mode,
                **({"command": str(command).strip()} if str(command or "").strip() else {}),
                **({"runner_prompt": str(runner_prompt).strip()} if str(runner_prompt or "").strip() else {}),
                **({"workspace_dir": str(workspace_dir).strip()} if str(workspace_dir or "").strip() else {}),
                "allowed_tools": normalize_allowed_tools(allowed_tools),
                "enforce_artifacts": bool(enforce_artifacts),
                "max_steps": max(1, min(int(max_steps or 1), 200)),
            },
            "control_manifest": {
                "trace_id": str(trace_id or "").strip(),
                "capability_id": str(capability_id or "").strip(),
                "context_hash": str(context_hash or "").strip(),
            },
            "expected_result_schema": "worker_todo_result.v1",
            "schema_refs": {
                "todo_schema": "https://ananta.local/schemas/todo.schema.json",
                "todo_track_schema": "https://ananta.local/schemas/todo.track.schema.json",
                "todo_result_schema": "https://ananta.local/schemas/worker/worker_todo_result.v1.json",
            },
        }


worker_contract_service = WorkerContractService()


def get_worker_contract_service() -> WorkerContractService:
    return worker_contract_service
