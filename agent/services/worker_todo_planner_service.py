from __future__ import annotations

import json
import re
from typing import Any

from flask import current_app, has_app_context

from agent.llm_integration import generate_text
from agent.services.planning_utils import extract_json_payload
from worker.core.verification import validate_worker_schema_payload

_DEFAULT_CONFIG = {
    "enabled": True,
    "planner_llm_enabled": True,
    "planner_llm_timeout_seconds": 12,
    "planner_llm_retry_attempts": 1,
    "max_tasks": 6,
    "max_steps": 30,
    "enforce_artifacts": True,
    "default_executor_kind": "ananta_worker",
    "execution_mode": "assistant_execute",
    "provider": None,
    "model": None,
    "base_url": None,
    "api_key": None,
}


def _bounded_int(value: object, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _normalize_executor_kind(value: object) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"ananta_worker", "opencode", "openai_codex_cli", "custom"}:
        return normalized
    return "ananta_worker"


def _normalize_execution_mode(value: object) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"command_execute", "assistant_execute", "plan_only"}:
        return normalized
    return "assistant_execute"


def _normalize_config(agent_cfg: dict | None) -> dict[str, Any]:
    runtime_cfg = (agent_cfg or {}).get("worker_runtime")
    runtime_cfg = runtime_cfg if isinstance(runtime_cfg, dict) else {}
    raw = runtime_cfg.get("todo_contract")
    raw = raw if isinstance(raw, dict) else {}
    normalized = {
        **dict(_DEFAULT_CONFIG),
        "enabled": bool(raw.get("enabled", _DEFAULT_CONFIG["enabled"])),
        "planner_llm_enabled": bool(raw.get("planner_llm_enabled", _DEFAULT_CONFIG["planner_llm_enabled"])),
        "planner_llm_timeout_seconds": _bounded_int(
            raw.get("planner_llm_timeout_seconds"),
            default=int(_DEFAULT_CONFIG["planner_llm_timeout_seconds"]),
            minimum=2,
            maximum=120,
        ),
        "planner_llm_retry_attempts": _bounded_int(
            raw.get("planner_llm_retry_attempts"),
            default=int(_DEFAULT_CONFIG["planner_llm_retry_attempts"]),
            minimum=1,
            maximum=5,
        ),
        "max_tasks": _bounded_int(raw.get("max_tasks"), default=int(_DEFAULT_CONFIG["max_tasks"]), minimum=1, maximum=20),
        "max_steps": _bounded_int(raw.get("max_steps"), default=int(_DEFAULT_CONFIG["max_steps"]), minimum=1, maximum=200),
        "enforce_artifacts": bool(raw.get("enforce_artifacts", _DEFAULT_CONFIG["enforce_artifacts"])),
        "default_executor_kind": _normalize_executor_kind(raw.get("default_executor_kind")),
        "execution_mode": _normalize_execution_mode(raw.get("execution_mode")),
        "provider": str(raw.get("provider") or "").strip() or None,
        "model": str(raw.get("model") or "").strip() or None,
        "base_url": str(raw.get("base_url") or "").strip() or None,
        "api_key": str(raw.get("api_key") or "").strip() or None,
    }
    return normalized


def _slug(value: str, *, fallback: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    return normalized or fallback


def _default_expected_artifacts(*, task_kind: str | None, expected_output_schema: dict[str, Any] | None) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = [{"kind": "task_output", "required": True, "description": "Primary task output artifact."}]
    kind = str(task_kind or "").strip().lower()
    if kind in {"coding", "testing", "verification"}:
        artifacts.append({"kind": "patch_artifact", "required": True, "description": "Patch or code delta produced by execution."})
        artifacts.append({"kind": "verification_artifact", "required": True, "description": "Verification evidence for the change."})
    if expected_output_schema:
        artifacts.append({"kind": "structured_output", "required": True, "description": "Structured output matching expected_output_schema."})
    return artifacts


def _default_acceptance_criteria(*, expected_output_schema: dict[str, Any] | None) -> list[str]:
    criteria = [
        "Task instructions are fulfilled without violating workspace or policy constraints.",
        "All required artifacts are returned and traceable.",
    ]
    required = list((expected_output_schema or {}).get("required") or [])
    if required:
        criteria.append(
            f"Structured output includes required fields: {', '.join(str(item) for item in required if str(item).strip())}."
        )
    return criteria


def _safe_priority(raw_priority: object) -> str:
    value = str(raw_priority or "").strip().lower()
    if value in {"critical", "high", "medium", "low"}:
        return value
    return "medium"


def _safe_risk(raw_risk: object) -> str:
    value = str(raw_risk or "").strip().lower()
    if value in {"critical", "high", "medium", "low"}:
        return value
    return "medium"


class WorkerTodoPlannerService:
    def build_delegation_todo_contract(
        self,
        *,
        worker_contract_service,
        subtask_id: str,
        parent_task: dict[str, Any],
        subtask_description: str,
        task_kind: str | None,
        required_capabilities: list[str] | None,
        worker_profile: str,
        profile_source: str,
        allowed_tools: list[str] | None,
        expected_output_schema: dict[str, Any] | None,
        target_worker: str | None,
        context_bundle_id: str | None,
        workspace_dir: str | None,
    ) -> dict[str, Any] | None:
        agent_cfg = (current_app.config.get("AGENT_CONFIG", {}) or {}) if has_app_context() else {}
        planner_cfg = _normalize_config(agent_cfg)
        if not planner_cfg["enabled"]:
            return None

        capabilities = [str(item).strip().lower() for item in list(required_capabilities or []) if str(item).strip()]
        capability_id = capabilities[0] if capabilities else "worker.command.execute"
        base_task = {
            "id": "todo-1",
            "title": str(subtask_description or "Execute delegated worker task").strip()[:120],
            "instructions": str(subtask_description or "Execute delegated worker task.").strip(),
            "status": "todo",
            "priority": _safe_priority(parent_task.get("priority")),
            "risk": _safe_risk("medium"),
            "depends_on": [],
            "allowed_tools": list(allowed_tools or []),
            "expected_artifacts": _default_expected_artifacts(
                task_kind=task_kind,
                expected_output_schema=dict(expected_output_schema or {}),
            ),
            "acceptance_criteria": _default_acceptance_criteria(expected_output_schema=dict(expected_output_schema or {})),
            "metadata": {
                "source": "hub_deterministic_seed",
                "task_kind": str(task_kind or "").strip().lower() or None,
                "context_bundle_id": str(context_bundle_id or "").strip() or None,
            },
        }
        todo_contract = worker_contract_service.build_worker_todo_contract(
            task_id=str(subtask_id or "").strip(),
            goal_id=str(parent_task.get("goal_id") or "").strip(),
            trace_id=str(parent_task.get("goal_trace_id") or f"tr-{subtask_id}").strip(),
            capability_id=capability_id,
            context_hash=str(context_bundle_id or f"ctx-{subtask_id}").strip(),
            executor_kind=planner_cfg["default_executor_kind"],
            worker_profile=worker_profile,
            profile_source=profile_source,
            tasks=[base_task],
            track=f"{_slug(str(task_kind or 'general'), fallback='general')}-worker-subplan",
            parent_task_id=str(parent_task.get("id") or "").strip() or None,
            target_worker=str(target_worker or "").strip() or None,
            runner_prompt=str(subtask_description or "").strip() or None,
            mode=planner_cfg["execution_mode"],
            workspace_dir=workspace_dir,
            allowed_tools=list(allowed_tools or []),
            enforce_artifacts=planner_cfg["enforce_artifacts"],
            max_steps=int(planner_cfg["max_steps"]),
        )
        generation = {
            "enabled": True,
            "planner_llm_enabled": bool(planner_cfg["planner_llm_enabled"]),
            "llm_attempted": False,
            "llm_applied": False,
            "mode": "deterministic_only",
            "errors": [],
        }
        try:
            validate_worker_schema_payload(schema_name="worker_todo_contract.v1", payload=todo_contract)
        except ValueError as exc:
            generation["errors"].append(f"deterministic_schema_invalid:{exc}")
            generation["mode"] = "deterministic_schema_invalid"
            return {"contract": todo_contract, "generation": generation}

        llm_available = self._planner_llm_available(planner_cfg=planner_cfg, agent_cfg=agent_cfg)
        allow_llm = bool(
            planner_cfg["planner_llm_enabled"]
            and llm_available
            and has_app_context()
            and not bool(getattr(current_app, "testing", False))
        )
        if not allow_llm:
            return {"contract": todo_contract, "generation": generation}

        generation["llm_attempted"] = True
        llm_tasks, llm_error = self._refine_tasks_with_llm(
            planner_cfg=planner_cfg,
            agent_cfg=agent_cfg,
            todo_contract=todo_contract,
            task_kind=task_kind,
            subtask_description=subtask_description,
            max_tasks=int(planner_cfg["max_tasks"]),
            default_allowed_tools=list(allowed_tools or []),
            fallback_expected_artifacts=list(base_task["expected_artifacts"]),
        )
        if llm_error:
            generation["errors"].append(llm_error)
            generation["mode"] = "deterministic_fallback"
            return {"contract": todo_contract, "generation": generation}
        if not llm_tasks:
            generation["errors"].append("planner_llm_returned_no_tasks")
            generation["mode"] = "deterministic_fallback"
            return {"contract": todo_contract, "generation": generation}

        todo_contract["todo"]["tasks"] = llm_tasks
        try:
            validate_worker_schema_payload(schema_name="worker_todo_contract.v1", payload=todo_contract)
        except ValueError as exc:
            generation["errors"].append(f"planner_llm_schema_invalid:{exc}")
            generation["mode"] = "deterministic_fallback"
            todo_contract["todo"]["tasks"] = [base_task]
            return {"contract": todo_contract, "generation": generation}

        generation["llm_applied"] = True
        generation["mode"] = "deterministic_plus_llm"
        return {"contract": todo_contract, "generation": generation}

    @staticmethod
    def _planner_llm_available(*, planner_cfg: dict[str, Any], agent_cfg: dict | None) -> bool:
        if planner_cfg.get("provider") and planner_cfg.get("model"):
            return True
        llm_cfg = (agent_cfg or {}).get("llm_config")
        llm_cfg = llm_cfg if isinstance(llm_cfg, dict) else {}
        provider = str(llm_cfg.get("provider") or (agent_cfg or {}).get("default_provider") or "").strip().lower()
        model = str(llm_cfg.get("model") or (agent_cfg or {}).get("default_model") or "").strip()
        return bool(provider and model)

    def _resolve_llm_config(self, *, planner_cfg: dict[str, Any], agent_cfg: dict | None) -> dict[str, Any]:
        llm_cfg = (agent_cfg or {}).get("llm_config")
        llm_cfg = llm_cfg if isinstance(llm_cfg, dict) else {}
        provider = str(
            planner_cfg.get("provider")
            or llm_cfg.get("provider")
            or (agent_cfg or {}).get("default_provider")
            or ""
        ).strip().lower()
        model = str(
            planner_cfg.get("model")
            or llm_cfg.get("model")
            or (agent_cfg or {}).get("default_model")
            or ""
        ).strip()
        base_url = str(planner_cfg.get("base_url") or llm_cfg.get("base_url") or "").strip() or None
        api_key = str(planner_cfg.get("api_key") or llm_cfg.get("api_key") or "").strip() or None
        return {
            "provider": provider,
            "model": model,
            "base_url": base_url,
            "api_key": api_key,
        }

    def _refine_tasks_with_llm(
        self,
        *,
        planner_cfg: dict[str, Any],
        agent_cfg: dict | None,
        todo_contract: dict[str, Any],
        task_kind: str | None,
        subtask_description: str,
        max_tasks: int,
        default_allowed_tools: list[str],
        fallback_expected_artifacts: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]] | None, str | None]:
        llm = self._resolve_llm_config(planner_cfg=planner_cfg, agent_cfg=agent_cfg)
        if not llm.get("provider") or not llm.get("model"):
            return None, "planner_llm_not_configured"
        prompt = self._build_planner_prompt(
            todo_contract=todo_contract,
            task_kind=task_kind,
            subtask_description=subtask_description,
            max_tasks=max_tasks,
        )
        retries = max(1, int(planner_cfg.get("planner_llm_retry_attempts") or 1))
        timeout = int(planner_cfg.get("planner_llm_timeout_seconds") or 12)
        last_error: str | None = None
        for _ in range(retries):
            try:
                raw = generate_text(
                    prompt=prompt,
                    provider=str(llm["provider"]),
                    model=str(llm["model"]),
                    base_url=llm.get("base_url"),
                    api_key=llm.get("api_key"),
                    timeout=timeout,
                )
                payload_text = extract_json_payload(str(raw or "")) or str(raw or "").strip()
                parsed = json.loads(payload_text)
                raw_tasks = parsed if isinstance(parsed, list) else parsed.get("tasks")
                if not isinstance(raw_tasks, list):
                    return None, "planner_llm_invalid_shape"
                normalized = self._normalize_llm_tasks(
                    items=raw_tasks,
                    max_tasks=max_tasks,
                    default_allowed_tools=default_allowed_tools,
                    fallback_expected_artifacts=fallback_expected_artifacts,
                )
                if not normalized:
                    return None, "planner_llm_empty_after_normalization"
                return normalized, None
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                last_error = f"planner_llm_parse_failed:{exc}"
            except Exception as exc:  # noqa: BLE001 - planner path is best-effort with deterministic fallback.
                last_error = f"planner_llm_call_failed:{exc}"
        return None, last_error or "planner_llm_failed"

    @staticmethod
    def _build_planner_prompt(
        *,
        todo_contract: dict[str, Any],
        task_kind: str | None,
        subtask_description: str,
        max_tasks: int,
    ) -> str:
        contract_json = json.dumps(todo_contract, ensure_ascii=False, indent=2)
        return (
            "You are a strict worker todo planner.\n"
            "Expand the deterministic todo contract into a concise, executable task list.\n"
            "Return JSON only. No markdown.\n\n"
            f"Task kind: {str(task_kind or '').strip().lower() or 'general'}\n"
            f"Subtask description: {str(subtask_description or '').strip()}\n"
            f"Maximum tasks: {max_tasks}\n\n"
            "Output JSON schema:\n"
            "{\n"
            '  "tasks": [\n'
            "    {\n"
            '      "id": "todo-1",\n'
            '      "title": "short title",\n'
            '      "instructions": "clear execution instructions",\n'
            '      "status": "todo",\n'
            '      "depends_on": [],\n'
            '      "priority": "high|medium|low|critical",\n'
            '      "risk": "high|medium|low|critical",\n'
            '      "acceptance_criteria": ["..."],\n'
            '      "expected_artifacts": [{"kind":"task_output","required":true,"description":"..."}],\n'
            '      "allowed_tools": ["bash"]\n'
            "    }\n"
            "  ]\n"
            "}\n\n"
            "Existing deterministic contract:\n"
            f"{contract_json}\n"
        )

    @staticmethod
    def _normalize_llm_tasks(
        *,
        items: list[dict[str, Any]],
        max_tasks: int,
        default_allowed_tools: list[str],
        fallback_expected_artifacts: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for idx, item in enumerate(list(items or [])[: max(1, max_tasks)], start=1):
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or item.get("name") or "").strip()
            instructions = str(item.get("instructions") or item.get("description") or "").strip()
            if not title and instructions:
                title = instructions[:120].strip()
            if not title or not instructions:
                continue
            task_id = str(item.get("id") or "").strip()
            if not task_id:
                task_id = f"todo-{idx}"
            depends_on = [str(dep).strip() for dep in list(item.get("depends_on") or []) if str(dep).strip()]
            status = str(item.get("status") or "todo").strip().lower() or "todo"
            if status not in {"todo", "open", "planned", "in_progress", "blocked", "done"}:
                status = "todo"
            acceptance = [
                str(entry).strip()
                for entry in list(item.get("acceptance_criteria") or item.get("acceptance") or [])
                if str(entry).strip()
            ]
            if not acceptance:
                acceptance = ["Task requirements satisfied."]
            expected_artifacts: list[dict[str, Any]] = []
            for artifact in list(item.get("expected_artifacts") or []):
                if not isinstance(artifact, dict):
                    continue
                kind = str(artifact.get("kind") or "").strip()
                if not kind:
                    continue
                expected_artifacts.append(
                    {
                        "kind": kind,
                        "required": bool(artifact.get("required", True)),
                        **({"description": str(artifact.get("description")).strip()} if str(artifact.get("description") or "").strip() else {}),
                    }
                )
            if not expected_artifacts:
                expected_artifacts = [dict(entry) for entry in list(fallback_expected_artifacts or []) if isinstance(entry, dict)]
            normalized.append(
                {
                    "id": _slug(task_id, fallback=f"todo-{idx}"),
                    "title": title[:120],
                    "instructions": instructions[:2000],
                    "status": status,
                    "priority": _safe_priority(item.get("priority")),
                    "risk": _safe_risk(item.get("risk")),
                    "depends_on": depends_on[:10],
                    "allowed_tools": [str(tool).strip() for tool in list(item.get("allowed_tools") or default_allowed_tools) if str(tool).strip()],
                    "expected_artifacts": expected_artifacts,
                    "acceptance_criteria": acceptance[:10],
                    "metadata": {
                        "source": "planner_llm",
                    },
                }
            )
        if not normalized:
            return []
        known_ids = {str(entry.get("id") or "").strip() for entry in normalized}
        for entry in normalized:
            depends_on = [dep for dep in list(entry.get("depends_on") or []) if dep in known_ids]
            entry["depends_on"] = depends_on
        return normalized


worker_todo_planner_service = WorkerTodoPlannerService()


def get_worker_todo_planner_service() -> WorkerTodoPlannerService:
    return worker_todo_planner_service

