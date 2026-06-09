"""Runtime cluster (research, review-state, session, prompt-build) for the task-scoped service.

Extracted from ``agent.services.task_scoped_execution_service`` as the
runtime cluster of SPLIT-001 (sub-split 001h). The module owns worker execution
context resolution, tool-definition selection, CLI session preparation
(including live-terminal and native-opencode paths), task-propose prompt
building, system-prompt resolution, routing dimensions, and the terminal
parent-goal guard.

Backwards compatibility is preserved at the service boundary via thin
delegating wrappers in :class:`TaskScopedExecutionService` (12-month
deprecation window, see todos/todo.refactor-large-files-split.json SPLIT-001).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from flask import current_app, has_app_context

from agent.common.sgpt import SUPPORTED_CLI_BACKENDS, resolve_codex_runtime_config
from agent.research_backend import normalize_research_artifact
from agent.runtime_policy import resolve_cli_backend, review_policy
from agent.security_risk import (
    classify_command_risk,
    classify_tool_calls_risk,
    has_file_access_signal,
    has_terminal_signal,
    max_risk_level,
)
from agent.services.cli_session_service import get_cli_session_service
from agent.services.context_manager_service import get_context_manager_service
from agent.services.instruction_layer_service import get_instruction_layer_service
from agent.services.repository_registry import get_repository_registry
from agent.services.task_execution_policy_service import normalize_allowed_tools
from agent.services.task_runtime_service import update_local_task_status
from agent.services.task_template_resolution import resolve_task_role_template
from agent.services.worker_execution_profile_service import (
    normalize_worker_execution_profile,
    resolve_worker_execution_profile,
)
from agent.services.worker_workspace_service import get_worker_workspace_service

if TYPE_CHECKING:
    from agent.services.task_scoped_execution_service import TaskScopedRouteResponse


def build_research_result(
    *,
    raw_res: str,
    backend_used: str,
    tid: str | None,
    rc: int,
    cli_err: str,
    latency_ms: int,
    output_source: str = "stdout",
    research_context: dict | None = None,
) -> dict:
    from agent.services._task_scoped_repair import build_llm_call_profile_entries

    artifact = normalize_research_artifact(
        raw_res,
        backend=backend_used,
        task_id=tid,
        cli_result={
            "returncode": rc,
            "latency_ms": latency_ms,
            "stderr_preview": (cli_err or "")[:240],
            "output_source": output_source,
        },
        research_context=research_context,
    )
    return {
        "reason": artifact.get("summary") or "Research report generated",
        "raw": raw_res,
        "research_artifact": artifact,
        "research_context": research_context,
        "backend": backend_used,
        "command": None,
        "tool_calls": None,
        "cli_result": {
            "returncode": rc,
            "latency_ms": latency_ms,
            "stderr_preview": (cli_err or "")[:240],
            "output_source": output_source,
            "llm_call_profile": build_llm_call_profile_entries(
                backend_used=backend_used,
                model=artifact.get("model"),
                prompt=(research_context or {}).get("prompt_section"),
                raw_output=raw_res,
                latency_ms=latency_ms,
                rc=rc,
                repair_attempted=False,
                repair_backend=None,
                repair_model=None,
            ),
        },
    }


def verify_research_artifact(research_artifact: dict | None) -> dict:
    artifact = dict(research_artifact or {})
    report_markdown = str(artifact.get("report_markdown") or "").strip()
    sources = list(artifact.get("sources") or [])
    citations = list(artifact.get("citations") or [])
    passed = bool(report_markdown and sources)
    verification = {
        "passed": passed,
        "ready": passed,
        "has_report": bool(report_markdown),
        "has_sources": bool(sources),
        "has_citations": bool(citations),
        "source_count": len(sources),
        "citation_count": len(citations),
        "reason": "verified" if passed else "missing_sources_or_report",
    }
    artifact_verification = dict(artifact.get("verification") or {})
    artifact_verification.update(verification)
    artifact["verification"] = artifact_verification
    return artifact_verification


def build_review_state(
    agent_cfg: dict,
    backend: str,
    task_kind: str,
    *,
    command: str | None,
    tool_calls: list[dict] | None,
) -> dict:
    risk_level = max_risk_level(
        classify_command_risk(command),
        classify_tool_calls_risk(tool_calls, guard_cfg=agent_cfg),
    )
    policy = review_policy(
        agent_cfg,
        backend=backend,
        task_kind=task_kind,
        risk_level=risk_level,
        uses_terminal=has_terminal_signal(command),
        uses_file_access=has_file_access_signal(command, tool_calls),
    )
    return {
        "required": bool(policy.get("required")),
        "status": "pending" if policy.get("required") else "not_required",
        "policy_version": policy.get("policy_version"),
        "reason": policy.get("reason"),
        "risk_level": policy.get("risk_level"),
        "uses_terminal": policy.get("uses_terminal"),
        "uses_file_access": policy.get("uses_file_access"),
        "reviewed_by": None,
        "reviewed_at": None,
        "comment": None,
    }


def get_worker_execution_context(
    task: dict | None,
    *,
    tid: str | None = None,
    base_prompt: str | None = None,
) -> dict:
    from agent.services._task_scoped_config_policy import resolve_worker_semantic_output_correction_policy

    agent_cfg = (current_app.config.get("AGENT_CONFIG", {}) or {}) if has_app_context() else {}
    semantic_policy = resolve_worker_semantic_output_correction_policy(agent_cfg)
    execution_context = dict((task or {}).get("worker_execution_context") or {})
    if execution_context:
        execution_context["allowed_tools"] = normalize_allowed_tools(execution_context.get("allowed_tools"))
        if semantic_policy and not isinstance(execution_context.get("semantic_output_correction"), dict):
            execution_context["semantic_output_correction"] = semantic_policy
        profile, profile_source = resolve_worker_execution_profile(
            worker_execution_context=execution_context,
            agent_cfg=agent_cfg,
        )
        execution_context["worker_profile"] = profile
        execution_context["profile_source"] = profile_source
        auto_bundle_cfg = dict(
            (agent_cfg.get("worker_runtime") or {}).get("codecompass_auto_bundle") or {}
        )
        if auto_bundle_cfg.get("enabled") and not list(
            (execution_context.get("context") or {}).get("chunks") or []
        ):
            kind_filter = [
                str(k).strip().lower()
                for k in list(auto_bundle_cfg.get("task_kinds") or [])
                if str(k).strip()
            ]
            routing_kind = str(
                (execution_context.get("routing_hints") or {}).get("task_kind") or ""
            ).strip().lower()
            if not kind_filter or not routing_kind or routing_kind in kind_filter:
                try:
                    resolved = get_context_manager_service().ensure_task_context_bundle(
                        task=dict(task or {}),
                        task_id=tid,
                        query=base_prompt,
                    )
                    bundle = resolved.get("context_bundle")
                    if bundle:
                        ctx = dict(execution_context.get("context") or {})
                        ctx.setdefault("chunks", []).extend(list(bundle.chunks or []))
                        ctx["token_estimate"] = (
                            int(ctx.get("token_estimate") or 0)
                            + int(bundle.token_estimate or 0)
                        )
                        if not ctx.get("context_text") and bundle.context_text:
                            ctx["context_text"] = bundle.context_text
                        execution_context["context"] = ctx
                        execution_context.setdefault("context_bundle_id", bundle.id)
                except Exception:
                    pass
        return execution_context
    bundle_id = str((task or {}).get("context_bundle_id") or "").strip()
    bundle = None
    if bundle_id:
        bundle = get_repository_registry().context_bundle_repo.get_by_id(bundle_id)
    if bundle is None and (tid or (task or {})):
        resolved = get_context_manager_service().ensure_task_context_bundle(
            task=dict(task or {}),
            task_id=tid,
            query=base_prompt,
        )
        resolved_bundle = resolved.get("context_bundle")
        if resolved_bundle is not None:
            bundle = resolved_bundle
    if bundle is None:
        return {}
    profile, profile_source = resolve_worker_execution_profile(
        worker_execution_context={},
        agent_cfg=agent_cfg,
    )
    resolved_context = {
        "context_bundle_id": bundle.id,
        "worker_profile": profile,
        "profile_source": profile_source,
        "context": {
            "context_text": bundle.context_text,
            "chunks": list(bundle.chunks or []),
            "token_estimate": int(bundle.token_estimate or 0),
            "bundle_metadata": dict(bundle.bundle_metadata or {}),
        },
    }
    if semantic_policy:
        resolved_context["semantic_output_correction"] = semantic_policy
    return resolved_context


def tool_definitions_for_task(
    task: dict | None,
    *,
    tool_definitions_resolver: Callable,
    execution_context: dict | None = None,
) -> list[dict]:
    execution_context = dict(execution_context or get_worker_execution_context(task))
    allowed_tools = normalize_allowed_tools(execution_context.get("allowed_tools"))
    if allowed_tools:
        return tool_definitions_resolver(allowlist=allowed_tools)
    return tool_definitions_resolver()


def resolve_task_role_identity(tid: str, task: dict) -> tuple[str | None, str | None]:
    task_record = get_repository_registry().task_repo.get_by_id(tid)
    if not task_record:
        return None, None
    role_id = getattr(task_record, "assigned_role_id", None)
    if task_record.team_id and task_record.assigned_agent_url:
        members = get_repository_registry().team_member_repo.get_by_team(task_record.team_id)
        for member in members:
            if member.agent_url == task_record.assigned_agent_url and not role_id:
                role_id = member.role_id
                break
    role_name = None
    if role_id:
        role = get_repository_registry().role_repo.get_by_id(role_id)
        if role:
            role_name = role.name
    return str(role_id or "").strip() or None, str(role_name or "").strip() or None


def resolve_task_session_scope(*, tid: str, task: dict, policy: dict) -> tuple[str, str, str | None]:
    execution_context = dict((task or {}).get("worker_execution_context") or {})
    workspace = dict(execution_context.get("workspace") or {})
    explicit_scope_key = str(workspace.get("session_scope_key") or "").strip()
    if explicit_scope_key:
        explicit_scope_kind = str(workspace.get("session_scope_kind") or "workspace").strip().lower() or "workspace"
        return explicit_scope_kind, explicit_scope_key, None

    reuse_scope = str(policy.get("reuse_scope") or "task").strip().lower()
    if reuse_scope == "role":
        role_id, role_name = resolve_task_role_identity(tid, task)
        if role_id or role_name:
            role_key = role_id or f"role-name:{role_name}"
            return "role", str(role_key), role_name
    return "task", f"task:{tid}", None


def prepare_task_cli_session(
    *,
    tid: str,
    task: dict,
    backend: str,
    model: str | None,
    agent_cfg: dict | None,
) -> dict | None:
    from agent.services._task_scoped_config_policy import (
        resolve_cli_session_policy,
        resolve_opencode_execution_mode,
        resolve_opencode_interactive_launch_mode,
    )

    policy = resolve_cli_session_policy(agent_cfg)
    backend_name = str(backend or "").strip().lower()
    opencode_execution_mode = resolve_opencode_execution_mode(agent_cfg)
    opencode_interactive_launch_mode = resolve_opencode_interactive_launch_mode(agent_cfg)
    terminal_execution_mode = (
        opencode_execution_mode if backend_name == "opencode" and opencode_execution_mode in {"live_terminal", "interactive_terminal"} else None
    )
    if not terminal_execution_mode and (not policy["enabled"] or not policy["allow_task_scoped_auto_session"]):
        return None
    if not terminal_execution_mode and backend_name not in set(policy["stateful_backends"]):
        return None
    scope_kind, scope_key, role_name = resolve_task_session_scope(tid=tid, task=task, policy=policy)
    workspace_context = get_worker_workspace_service().resolve_workspace_context(task=task)
    workspace_dir = str(workspace_context.workspace_dir)
    verification = dict(task.get("verification_status") or {})
    session_meta = verification.get("cli_session") if isinstance(verification.get("cli_session"), dict) else {}
    existing_id = str(session_meta.get("session_id") or "").strip()
    session = get_cli_session_service().get_session(existing_id, include_history=False) if existing_id else None
    if (
        not session
        or str(session.get("status") or "").strip().lower() != "active"
        or str(session.get("backend") or "").strip().lower() != backend_name
    ):
        session = get_cli_session_service().find_active_session(
            backend=backend_name,
            scope_key=scope_key,
            scope_kind=scope_kind,
        )
    session_reused = False
    if session and str(session.get("status") or "").strip().lower() == "active" and str(session.get("backend") or "").strip().lower() == backend_name:
        session_payload = dict(session)
        session_reused = True
    else:
        session_payload = get_cli_session_service().create_session(
            backend=backend_name,
            model=model,
            metadata={
                "source": "task_propose_auto_session",
                "task_id": tid,
                "scope_kind": scope_kind,
                "scope_key": scope_key,
                "role_name": role_name,
                "opencode_workdir": workspace_dir,
                "opencode_interactive_launch_mode": opencode_interactive_launch_mode,
            },
            task_id=tid,
            conversation_id=scope_key,
        )
        verification["cli_session"] = {
            "session_id": session_payload.get("id"),
            "backend": backend_name,
            "model": model,
            "status": "active",
            "scope_kind": scope_kind,
            "scope_key": scope_key,
            "updated_at": time.time(),
        }
        update_local_task_status(
            tid,
            str(task.get("status") or "assigned"),
            verification_status=verification,
        )
    if backend_name == "opencode" and not terminal_execution_mode:
        session_payload = (
            get_cli_session_service().update_session(
                str(session_payload.get("id") or ""),
                metadata_updates={
                    "opencode_execution_mode": "backend",
                    "opencode_interactive_launch_mode": opencode_interactive_launch_mode,
                    "opencode_live_terminal": {},
                },
            )
            or session_payload
        )
        verification["cli_session"] = {
            **verification.get("cli_session", {}),
            "execution_mode": "backend",
            "terminal_session_id": None,
            "forward_param": None,
            "terminal_status": None,
            "updated_at": time.time(),
        }
        verification["opencode_live_terminal"] = {}
        update_local_task_status(
            tid,
            str(task.get("status") or "assigned"),
            verification_status=verification,
        )
    if terminal_execution_mode:
        import agent.services.task_scoped_execution_service as _svc_mod
        terminal_meta = (
            _svc_mod.get_live_terminal_session_service().ensure_session_for_cli(
                session_payload,
                execution_mode=terminal_execution_mode,
                workdir=workspace_dir,
            )
            or {}
        )
        interactive_terminal_workspace = (
            dict(verification.get("interactive_terminal_workspace") or {})
            if isinstance(verification.get("interactive_terminal_workspace"), dict)
            else {}
        )
        if terminal_execution_mode == "interactive_terminal" and not interactive_terminal_workspace.get("baseline_ready"):
            baseline_meta = get_worker_workspace_service().refresh_interactive_terminal_baseline(workspace_dir=Path(workspace_dir))
            interactive_terminal_workspace = {
                "baseline_ready": True,
                **baseline_meta,
            }
        session_payload = (
            get_cli_session_service().update_session(
                str(session_payload.get("id") or ""),
                metadata_updates={
                    "opencode_execution_mode": terminal_execution_mode,
                    "opencode_interactive_launch_mode": opencode_interactive_launch_mode,
                    "opencode_live_terminal": terminal_meta,
                    "opencode_workdir": workspace_dir,
                },
            )
            or session_payload
        )
        verification["cli_session"] = {
            **verification.get("cli_session", {}),
            "execution_mode": terminal_execution_mode,
            "terminal_session_id": terminal_meta.get("terminal_session_id"),
            "forward_param": terminal_meta.get("forward_param"),
            "agent_url": terminal_meta.get("agent_url"),
            "agent_name": terminal_meta.get("agent_name"),
            "terminal_status": terminal_meta.get("status"),
            "updated_at": time.time(),
        }
        if interactive_terminal_workspace:
            verification["interactive_terminal_workspace"] = interactive_terminal_workspace
        verification["opencode_live_terminal"] = dict(terminal_meta)
        update_local_task_status(
            tid,
            str(task.get("status") or "assigned"),
            verification_status=verification,
        )
    elif backend_name == "opencode" and bool(policy.get("native_opencode_sessions")):
        from agent.services.opencode_runtime_service import get_opencode_runtime_service

        runtime_meta = get_opencode_runtime_service().ensure_session_runtime(session_payload, model=model)
        session_payload = (
            get_cli_session_service().get_session(str(session_payload.get("id") or ""), include_history=False) or session_payload
        )
        verification["cli_session"] = {
            **verification.get("cli_session", {}),
            "native_session_id": runtime_meta.get("native_session_id"),
            "server_key": runtime_meta.get("server_key"),
            "server_url": runtime_meta.get("server_url"),
            "agent": runtime_meta.get("agent"),
            "updated_at": time.time(),
        }
        update_local_task_status(
            tid,
            str(task.get("status") or "assigned"),
            verification_status=verification,
        )
    get_cli_session_service().prune_sessions(max_sessions=policy["max_sessions"])
    session_payload["session_reused"] = bool(session_reused)
    session_payload["max_turns_per_session"] = policy["max_turns_per_session"]
    return session_payload


def build_task_propose_prompt(
    *,
    tid: str,
    task: dict,
    base_prompt: str,
    tool_definitions_resolver: Callable,
    research_context: dict | None = None,
    interactive_terminal: bool = False,
    context_profile: dict | None = None,
) -> tuple[str, dict]:
    from agent.services._task_scoped_citation import extract_retrieval_trace_link
    from agent.services._task_scoped_config_policy import bounded_int

    execution_context = get_worker_execution_context(task, tid=tid, base_prompt=base_prompt)
    context_payload = dict(execution_context.get("context") or {})
    retrieval_trace_link = extract_retrieval_trace_link(context_payload)
    context_text = str(context_payload.get("context_text") or "").strip()
    context_profile_payload = dict(context_profile or {})
    compact_profile = bool(context_profile_payload.get("compact"))
    task_brief_char_limit = (
        bounded_int(context_profile_payload.get("task_brief_char_limit"), default=900, minimum=180, maximum=4000)
        if compact_profile
        else None
    )
    hub_context_char_limit = (
        bounded_int(context_profile_payload.get("hub_context_char_limit"), default=2600, minimum=256, maximum=12000)
        if compact_profile
        else None
    )
    research_prompt_char_limit = (
        bounded_int(context_profile_payload.get("research_prompt_char_limit"), default=1800, minimum=200, maximum=12000)
        if compact_profile
        else None
    )
    if hub_context_char_limit and len(context_text) > hub_context_char_limit:
        context_text = context_text[: max(1, hub_context_char_limit - 14)].rstrip() + "\n\n[gekürzt]"
    workspace_payload = dict(execution_context.get("workspace") or {})
    workspace_context = get_worker_workspace_service().resolve_workspace_context(task=task)
    allowed_tools = normalize_allowed_tools(execution_context.get("allowed_tools"))
    expected_output_schema = dict(execution_context.get("expected_output_schema") or {})
    semantic_output_correction = (
        dict(execution_context.get("semantic_output_correction") or {})
        if isinstance(execution_context.get("semantic_output_correction"), dict)
        else {}
    )
    worker_profile = normalize_worker_execution_profile(execution_context.get("worker_profile"))
    profile_source = str(execution_context.get("profile_source") or "agent_default").strip().lower() or "agent_default"
    tool_definitions = tool_definitions_for_task(
        task,
        tool_definitions_resolver=tool_definitions_resolver,
        execution_context=execution_context,
    )

    prompt_sections: list[str] = []
    system_prompt = get_system_prompt_for_task(tid)
    instruction_stack = get_instruction_layer_service().assemble_for_task(
        task=task,
        base_prompt=base_prompt,
        system_prompt=system_prompt,
        emit_audit=True,
    )
    effective_system_prompt = str(instruction_stack.get("rendered_system_prompt") or "").strip() or None
    stack_diagnostics = dict(instruction_stack.get("diagnostics") or {})
    shell_command_mode = str(execution_context.get("shell_command_mode") or "").strip().lower()
    allow_complex_shell = shell_command_mode == "pipeline"
    _raw_pattern_hints = execution_context.get("pattern_hints_normalized")
    _pattern_hints = dict(_raw_pattern_hints) if isinstance(_raw_pattern_hints, dict) and _raw_pattern_hints else None
    opencode_context_files = get_worker_workspace_service().prepare_opencode_context_files(
        task=task,
        workspace_context=workspace_context,
        base_prompt=base_prompt,
        system_prompt=effective_system_prompt,
        context_text=context_text,
        expected_output_schema=expected_output_schema,
        tool_definitions=tool_definitions,
        research_context=research_context,
        include_response_contract=not interactive_terminal,
        allow_complex_shell=allow_complex_shell,
        task_brief_char_limit=task_brief_char_limit,
        context_text_char_limit=hub_context_char_limit,
        research_prompt_char_limit=research_prompt_char_limit,
        pattern_hints=_pattern_hints,
    )
    prompt_sections.append(f"Aktueller Auftrag: {base_prompt}")
    read_paths = [
        str(opencode_context_files.get("agents_path") or "").strip(),
        str(opencode_context_files.get("context_index_path") or "").strip(),
        str(opencode_context_files.get("task_brief_path") or "").strip(),
    ]
    if context_text:
        read_paths.append(str(opencode_context_files.get("hub_context_path") or ".ananta/hub-context.md"))
    if not interactive_terminal:
        read_paths.append(str(opencode_context_files.get("response_contract_path") or "").strip())
    read_paths = [item for item in read_paths if item]
    if read_paths:
        prompt_sections.append(
            "Lies zuerst die bereitgestellten Workspace-Dateien und verwende diesen Dateikontext "
            "statt lange Inhalte zu wiederholen:\n" + "\n".join(f"- {item}" for item in read_paths)
        )
    if context_text:
        prompt_sections.append(
            "Selektierter Hub-Kontext ist im Hub-Kontext enthalten und wird aus derselben Datei geladen. "
            "Selektierter Research-Kontext wird aus derselben Datei geladen."
        )
        context_preview = " ".join(str(context_text).split()).strip().lower()[:240]
        if context_preview and not compact_profile:
            prompt_sections.append(f"Kurzvorschau Hub-Kontext: {context_preview}")
    research_prompt_section = str((research_context or {}).get("prompt_section") or "").strip()
    if research_prompt_section:
        prompt_sections.append(
            "Selektierter Research-Kontext ist ausgelagert in "
            f"{str(opencode_context_files.get('research_context_prompt_path') or 'rag_helper/research-context.md')}."
        )
        if not compact_profile:
            research_preview = " ".join(research_prompt_section.split()).strip().lower()[:320]
            if research_preview:
                prompt_sections.append(f"Kurzvorschau Research-Kontext: {research_preview}")
    if allowed_tools:
        prompt_sections.append(
            "Tool-Scope fuer diesen Task (nur diese Tools verwenden): "
            + ", ".join(str(item) for item in allowed_tools)
        )
    prompt_sections.append(
        f"Worker-Ausfuehrungsprofil: {worker_profile} (source={profile_source})."
    )
    if expected_output_schema and not compact_profile:
        prompt_sections.append(
            "Erwartetes Output-Schema (Kurzfassung): "
            + json.dumps(expected_output_schema, ensure_ascii=False)[:400]
        )
    if stack_diagnostics and not compact_profile:
        prompt_sections.append(get_instruction_layer_service().render_diagnostics_brief(stack_diagnostics))
    prompt_sections.append(
        "Arbeitsverzeichnis fuer Datei-/Shell-Aktionen:\n"
        f"- workspace: {workspace_context.workspace_dir}\n"
        f"- artifacts: {workspace_context.artifacts_dir}\n"
        f"- rag_helper: {workspace_context.rag_helper_dir}\n"
        "Nutze ausschliesslich diesen Workspace fuer neue oder geaenderte Dateien."
    )
    if interactive_terminal:
        if compact_profile:
            prompt_sections.append(
                "Kompaktmodus aktiv: nutze die Workspace-Dateien als Quelle der Wahrheit und vermeide Kontext-Wiederholung."
            )
        prompt_sections.append(
            "Arbeite direkt im Workspace mit normalem OpenCode-CLI. "
            "Fuehre die gewuenschten Datei- und Verzeichnis-Aenderungen im Workspace aus. "
            "Nutze bei Bedarf `rag_helper/` fuer Hilfsdateien oder ausgelagerten Kontext. "
            "Es ist keine JSON-Antwort erforderlich; Workspace-Aenderungen und Diffs werden nach dem Lauf automatisch erfasst."
        )
    else:
        prompt_sections.append(
            "Antworte ausschliesslich als genau ein JSON-Objekt. "
            "Beachte dafuer die Regeln in "
            f"{str(opencode_context_files.get('response_contract_path') or '.ananta/response-contract.md')} "
            "und setze mindestens eines von 'command' oder 'tool_calls'."
        )
        if allow_complex_shell:
            prompt_sections.append(
                "Priorisiere `tool_calls` fuer Datei-/Verzeichnis- und Code-Aenderungen. "
                "Falls ein Shell-Befehl erforderlich ist, liefere einen `command` — "
                "Pipes (`|`), Redirects (`>`, `<`, `2>&1`) und Chaining (`&&`, `||`, `;`) sind erlaubt."
            )
        else:
            prompt_sections.append(
                "Priorisiere `tool_calls` fuer Datei-/Verzeichnis- und Code-Aenderungen. "
                "Falls ein Shell-Befehl erforderlich ist, liefere genau einen einzelnen `command` "
                "ohne `&&`, `||`, `;`, `>`, `<` oder `|`."
            )
    return "\n\n".join(section for section in prompt_sections if section), {
        "context_bundle_id": execution_context.get("context_bundle_id") or task.get("context_bundle_id"),
        "allowed_tools": allowed_tools,
        "expected_output_schema": expected_output_schema,
        "semantic_output_correction": semantic_output_correction if semantic_output_correction else None,
        "worker_profile": worker_profile,
        "profile_source": profile_source,
        "workspace": {
            "requested": workspace_payload or None,
            "workspace_dir": str(workspace_context.workspace_dir),
            "artifacts_dir": str(workspace_context.artifacts_dir),
            "rag_helper_dir": str(workspace_context.rag_helper_dir),
            "opencode_context_files": opencode_context_files,
        },
        "context_chunk_count": len(context_payload.get("chunks") or []),
        "has_context_text": bool(context_text),
        "retrieval_trace_id": retrieval_trace_link["retrieval_trace_id"],
        "retrieval_context_hash": retrieval_trace_link["retrieval_context_hash"],
        "retrieval_manifest_hash": retrieval_trace_link["retrieval_manifest_hash"],
        "instruction_layers": stack_diagnostics,
        "research_context": {
            "artifact_ids": list((research_context or {}).get("artifact_ids") or []),
            "knowledge_collection_ids": list((research_context or {}).get("knowledge_collection_ids") or []),
            "repo_scope_refs": list((research_context or {}).get("repo_scope_refs") or []),
            "truncated": bool((research_context or {}).get("truncated")),
            "context_char_count": int((research_context or {}).get("context_char_count") or 0),
        }
        if research_context
        else None,
    }


def get_system_prompt_for_task(tid: str) -> str | None:
    task = get_repository_registry().task_repo.get_by_id(tid)
    if not task:
        return None

    repos = get_repository_registry()
    resolved = resolve_task_role_template(task, repos=repos)
    template_id = resolved.get("template_id")
    if not template_id:
        return None
    template = repos.template_repo.get_by_id(template_id)
    if not template:
        return None

    prompt = template.prompt_template
    goal_text = ""
    goal_context = ""
    acceptance_criteria: list[str] = []
    goal_id = str(task.goal_id or "").strip()
    if goal_id:
        goal = repos.goal_repo.get_by_id(goal_id)
        if goal:
            goal_text = str(goal.goal or "").strip()
            goal_context = str(goal.context or "").strip()
            acceptance_criteria = [str(item) for item in (goal.acceptance_criteria or []) if str(item or "").strip()]
    variables = {
        "agent_name": current_app.config.get("AGENT_NAME", "Unbekannter Agent"),
        "task_title": task.title or "Kein Titel",
        "task_description": task.description or "Keine Beschreibung",
        "team_goal": goal_text
        or str(task.title or "").strip()
        or str(task.description or "").strip()
        or str(resolved.get("team_name") or "").strip()
        or "aktuelles Teamziel",
        "goal_context": goal_context,
        "acceptance_criteria": "\n".join(f"- {item}" for item in acceptance_criteria),
    }
    if resolved.get("team_name"):
        variables["team_name"] = resolved["team_name"]
    if resolved.get("role_name"):
        variables["role_name"] = resolved["role_name"]
    for key, value in variables.items():
        prompt = prompt.replace("{{" + key + "}}", str(value))
    return prompt


def routing_dimensions(
    *,
    backend_used: str,
    model: str | None,
    temperature: float | None = None,
    requested_backend: str = "auto",
    agent_cfg: dict | None = None,
    worker_profile: str | None = None,
    profile_source: str | None = None,
) -> dict:
    from agent.services._task_scoped_config_policy import normalize_temperature

    backend = str(backend_used or "").strip().lower()
    requested = str(requested_backend or "auto").strip().lower()
    normalized_profile = normalize_worker_execution_profile(worker_profile)
    normalized_profile_source = str(profile_source or "agent_default").strip().lower() or "agent_default"
    cfg = agent_cfg if isinstance(agent_cfg, dict) else ((current_app.config.get("AGENT_CONFIG", {}) or {}) if has_app_context() else {})
    runtime_cfg = cfg.get("worker_runtime") if isinstance(cfg.get("worker_runtime"), dict) else {}
    native_runtime_cfg = runtime_cfg.get("native_worker_runtime") if isinstance(runtime_cfg.get("native_worker_runtime"), dict) else {}
    runtime_path = None
    if backend == "ananta-worker":
        runtime_path = "native_worker_pipeline" if bool(native_runtime_cfg.get("enabled", False)) else "sgpt_fallback_proxy"
    dimensions = {
        "requested_backend": requested or "auto",
        "execution_backend": backend or requested or "sgpt",
        "inference_provider": None,
        "inference_model": str(model or "").strip() or None,
        "inference_temperature": normalize_temperature(temperature),
        "inference_base_url": None,
        "inference_target_kind": None,
        "inference_target_provider_type": None,
        "remote_hub": False,
        "instance_id": None,
        "max_hops": None,
        "worker_profile": normalized_profile,
        "profile_source": normalized_profile_source,
        "worker_runtime_path": runtime_path,
    }
    if backend == "codex":
        runtime_cfg = resolve_codex_runtime_config() if has_app_context() else {}
        dimensions.update(
            {
                "inference_provider": runtime_cfg.get("target_provider") or str(cfg.get("default_provider") or "").strip().lower() or "openai_compatible",
                "inference_base_url": runtime_cfg.get("base_url"),
                "inference_target_kind": runtime_cfg.get("target_kind"),
                "inference_target_provider_type": runtime_cfg.get("target_provider_type"),
                "remote_hub": bool(runtime_cfg.get("remote_hub")),
                "instance_id": runtime_cfg.get("instance_id"),
                "max_hops": runtime_cfg.get("max_hops"),
            }
        )
        return dimensions
    dimensions["inference_provider"] = str(cfg.get("default_provider") or "").strip().lower() or None
    return dimensions


def terminal_parent_goal_guard(*, tid: str, task: dict, phase: str) -> "TaskScopedRouteResponse | None":
    from agent.services.task_scoped_execution_service import TaskScopedRouteResponse

    goal_id = str((task or {}).get("goal_id") or "").strip()
    if not goal_id:
        return None
    goal = get_repository_registry().goal_repo.get_by_id(goal_id)
    goal_status = str(getattr(goal, "status", "") or "").strip().lower() if goal is not None else ""
    if goal_status not in {"completed", "failed", "cancelled", "aborted", "timeout"}:
        return None
    update_local_task_status(
        tid,
        str((task or {}).get("status") or "todo"),
        event_type="parent_goal_cancelled",
        event_actor="task_scoped_execution_service",
        event_details={"goal_id": goal_id, "goal_status": goal_status, "phase": phase},
    )
    return TaskScopedRouteResponse(
        data={
            "status": "skipped",
            "reason": "parent_goal_cancelled",
            "goal_status": goal_status,
            "task_id": tid,
            "goal_id": goal_id,
            "phase": phase,
        },
        status="skipped",
        message="Parent goal is terminal",
        code=409,
    )


# ======================================================================
# CLI backend resolver (SPLIT-001t)
# ======================================================================
# Pulled out of TaskScopedExecutionService._resolve_cli_backend. The
# wrapper preserves the public method surface (12-month deprecation
# window) so tests that monkeypatch the class method keep working.


def resolve_task_cli_backend(
    *,
    task_kind: str,
    requested_backend: str = "auto",
    agent_cfg: dict | None = None,
    required_capabilities: list[str] | None = None,
) -> tuple[str, str]:
    """Resolve the CLI backend for the current task.

    Thin wrapper over :func:`agent.runtime_policy.resolve_cli_backend`
    that supplies the well-known defaults (``SUPPORTED_CLI_BACKENDS``
    as the support set, ``sgpt`` as the fallback backend, and the
    active app config as the default ``agent_cfg``).

    Returns ``(backend, reason)`` — ``reason`` is the human-readable
    explanation of why this backend was chosen (consumed by the
    telemetry path).
    """
    if agent_cfg is None:
        agent_cfg = (current_app.config.get("AGENT_CONFIG", {}) or {}) if has_app_context() else {}
    backend, reason, _ = resolve_cli_backend(
        task_kind=task_kind,
        requested_backend=requested_backend,
        supported_backends=SUPPORTED_CLI_BACKENDS,
        agent_cfg=agent_cfg,
        fallback_backend="sgpt",
        required_capabilities=required_capabilities,
    )
    return backend, reason


_resolve_cli_backend = resolve_task_cli_backend
