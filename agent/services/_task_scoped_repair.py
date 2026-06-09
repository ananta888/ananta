"""Repair cluster (structured-action parse, repair, shell-meta-block) for the task-scoped service.

Extracted from ``agent.services.task_scoped_execution_service`` as the
repair cluster of SPLIT-001 (sub-split 001i). The module owns structured-action
parsing, proposal repair (local heuristic + LLM re-invocation), token estimation,
LLM call profile building, and the repaired-execute-after-meta-block path.

Backwards compatibility is preserved at the service boundary via thin
delegating wrappers in :class:`TaskScopedExecutionService` (12-month
deprecation window, see todos/todo.refactor-large-files-split.json SPLIT-001).
"""

from __future__ import annotations

import json
from typing import Callable

from flask import current_app

from agent.common.sgpt import SUPPORTED_CLI_BACKENDS
from agent.common.utils.structured_action_utils import (
    extract_structured_action_fields,
    locally_repair_structured_action_output,
    parse_structured_action_payload,
)
from agent.llm_integration import build_llm_call_profile_entry
from agent.pipeline_trace import append_stage
from agent.runtime_policy import runtime_routing_config
from agent.services.service_registry import get_core_services
from agent.utils import _extract_reason


def repair_task_proposal(
    *,
    cli_runner: Callable,
    prompt: str,
    bad_output: str,
    validation_error: str,
    timeout: int,
    task_kind: str,
    policy_version: str,
    cfg: dict,
    primary_backend: str,
    primary_model: str | None,
    primary_temperature: float | None = None,
    research_context: dict | None = None,
    session: dict | None = None,
    workdir: str | None = None,
    invoke_cli_runner: Callable,
    coalesce_cli_output: Callable,
    normalize_temperature: Callable,
) -> dict | None:
    locally_repaired = locally_repair_structured_action_output(bad_output)
    if locally_repaired:
        return {
            "raw": locally_repaired,
            "output_source": "local_repair",
            "backend_used": primary_backend,
            "model": primary_model,
            "temperature": normalize_temperature(primary_temperature),
            "stderr": "",
            "rc": 0,
        }
    default_model = str(cfg.get("default_model") or cfg.get("model") or "").strip() or None
    first_backend = str(primary_backend or "opencode").strip().lower()
    if first_backend not in SUPPORTED_CLI_BACKENDS:
        first_backend = "opencode"
    first_model = primary_model or default_model

    repair_backend = str(cfg.get("task_propose_repair_backend") or "opencode").strip().lower()
    if repair_backend not in SUPPORTED_CLI_BACKENDS:
        repair_backend = "opencode"
    repair_model = str(cfg.get("task_propose_repair_model") or "").strip() or default_model
    normalized_temperature = normalize_temperature(primary_temperature)
    timeout_like_failure = validation_error == "empty_or_failed_cli_response" and "timeout" in str(bad_output or "").lower()
    candidates: list[tuple[str, str | None, float | None]] = []
    if not timeout_like_failure or repair_backend == first_backend:
        candidates.append((first_backend, first_model, normalized_temperature))
    candidates.append((repair_backend, repair_model, normalized_temperature))
    deduped: list[tuple[str, str | None, float | None]] = []
    seen: set[tuple[str, str, str]] = set()
    for backend_name, model_name, temperature in candidates:
        key = (backend_name, str(model_name or ""), str(temperature))
        if key in seen:
            continue
        seen.add(key)
        deduped.append((backend_name, model_name, temperature))

    repair_prompt = build_repair_prompt(prompt=prompt, bad_output=bad_output, validation_error=validation_error)
    for backend_name, model_name, temperature in deduped:
        cli_kwargs = {
            "prompt": repair_prompt,
            "options": ["--no-interaction"],
            "timeout": timeout,
            "backend": backend_name,
            "model": model_name,
            "routing_policy": {"mode": "adaptive", "task_kind": task_kind, "policy_version": policy_version},
            "workdir": workdir,
        }
        if temperature is not None:
            cli_kwargs["temperature"] = temperature
        if research_context:
            cli_kwargs["research_context"] = research_context
        if session:
            cli_kwargs["session"] = session
        rc, cli_out, cli_err, backend_used = invoke_cli_runner(cli_runner, **cli_kwargs)
        raw_res, output_source = coalesce_cli_output(cli_out, cli_err)
        if not raw_res.strip():
            continue
        command, tool_calls = extract_structured_action_fields(raw_res)
        if not command and not tool_calls:
            continue
        return {
            "raw": raw_res,
            "output_source": output_source,
            "backend_used": backend_used,
            "model": model_name,
            "temperature": temperature,
            "stderr": cli_err,
            "rc": rc,
        }
    return None


def is_timeout_like_repair_failure(*, validation_error: str, bad_output: str) -> bool:
    error_marker = str(validation_error or "").strip().lower()
    output_marker = str(bad_output or "").strip().lower()
    if error_marker in {"empty_or_failed_cli_response", "empty_cli_response"}:
        if not output_marker:
            return True
        return "timeout" in output_marker or "timed out" in output_marker
    return "timeout" in output_marker or "timed out" in output_marker


def is_shell_meta_blocked_failure(output: str | None, failure_type: str | None) -> bool:
    if str(failure_type or "").strip().lower() != "command_runtime_error":
        return False
    text = str(output or "")
    markers = (
        "Befehlskettung (&&/||)",
        "Semikolons (;)",
        "Input/Output-Redirection",
        "Background-Execution (&)",
        "Unsupported shell operators in command",
    )
    return any(marker in text for marker in markers)


def is_command_not_found_failure(output: str | None, failure_type: str | None) -> bool:
    normalized = str(failure_type or "").strip().lower()
    if normalized in {"command_not_found", "command_runtime_error"}:
        text = str(output or "").lower()
        if "command not found" in text or "not recognized as an internal or external command" in text:
            return True
    return False


def estimate_tokens(value: str | None) -> int:
    text = str(value or "")
    if not text:
        return 0
    return max(1, int(len(text) / 4))


def build_llm_call_profile_entries(
    *,
    backend_used: str,
    model: str | None,
    prompt: str,
    raw_output: str,
    latency_ms: int,
    rc: int,
    repair_attempted: bool,
    repair_backend: str | None,
    repair_model: str | None,
) -> list[dict]:
    entries = [
        build_llm_call_profile_entry(
            name="propose_primary",
            backend=str(backend_used or ""),
            provider=None,
            model=str(model or "") or None,
            success=bool(rc == 0),
            started_at=None,
            ended_at=None,
            usage={
                "prompt_tokens": estimate_tokens(prompt),
                "completion_tokens": estimate_tokens(raw_output),
            },
            source="cli_backend",
            estimated=True,
        )
    ]
    # Override latency_ms post-hoc since we have the real value but not started_at/ended_at.
    entries[0]["latency_ms"] = int(latency_ms or 0)
    if repair_attempted:
        entries.append(
            build_llm_call_profile_entry(
                name="propose_repair",
                backend=str(repair_backend or ""),
                provider=None,
                model=str(repair_model or "") or None,
                success=bool(rc == 0),
                started_at=None,
                ended_at=None,
                source="cli_backend",
                estimated=True,
            )
        )
    return entries


def build_repair_prompt(*, prompt: str, bad_output: str, validation_error: str) -> str:
    preview = str(bad_output or "").strip()
    if len(preview) > 2000:
        preview = preview[:2000]
    return (
        "Der vorherige Modell-Output war leer/ungueltig oder nicht ausfuehrbar.\n"
        "Repariere die Antwort und gib NUR ein valides JSON-Objekt zurueck.\n\n"
        f"Validator/Fehlergrund: {validation_error}\n\n"
        "Anforderungen:\n"
        "- Genau ein JSON-Objekt, kein Markdown.\n"
        "- Felder: reason (string), command (string optional), tool_calls (array optional).\n"
        "- Mindestens eines von command oder tool_calls muss befuellt sein.\n\n"
        f"Original-Prompt:\n{prompt}\n\n"
        f"Fehlerhafter Output (Ausschnitt):\n{preview}\n"
    )


def attempt_repaired_execute_after_meta_block(
    *,
    tid: str,
    task: dict,
    task_kind: str,
    command: str | None,
    execution_output: str | None,
    execution_policy,
    agent_cfg: dict,
    cli_runner: Callable,
    tool_definitions_resolver: Callable | None,
    pipeline: dict,
    workspace_dir: str,
    exec_started_at: float | None,
    build_task_propose_prompt: Callable,
    resolve_task_propose_timeout: Callable,
    resolve_requested_model: Callable,
    normalize_temperature: Callable,
    prepare_task_cli_session: Callable,
    invoke_cli_runner: Callable,
    coalesce_cli_output: Callable,
) -> dict | None:
    proposal_meta = dict(task.get("last_proposal") or {})
    research_context = proposal_meta.get("research_context") if isinstance(proposal_meta.get("research_context"), dict) else None
    prompt, _ = build_task_propose_prompt(
        tid=tid,
        task=task,
        base_prompt=str(task.get("description") or task.get("prompt") or f"Bearbeite Task {tid}"),
        tool_definitions_resolver=tool_definitions_resolver or (lambda *_args, **_kwargs: []),
        research_context=research_context,
    )
    timeout = resolve_task_propose_timeout(agent_cfg, task_kind)
    routing_policy_version = runtime_routing_config(current_app.config.get("AGENT_CONFIG", {}) or {})["policy_version"]
    primary_backend = str(
        proposal_meta.get("backend")
        or ((proposal_meta.get("routing") or {}).get("execution_backend"))
        or ((proposal_meta.get("routing") or {}).get("effective_backend"))
        or "opencode"
    ).strip().lower()
    primary_model = resolve_requested_model(
        agent_cfg=agent_cfg,
        requested_model=str(proposal_meta.get("model") or "").strip() or None,
    )
    bad_output = json.dumps(
        {
            "blocked_command": command,
            "execution_error": execution_output,
            "raw_proposal_preview": str(proposal_meta.get("raw") or "")[:1200],
        },
        ensure_ascii=False,
    )
    repaired = repair_task_proposal(
        cli_runner=cli_runner,
        prompt=prompt,
        bad_output=bad_output,
        validation_error="shell_meta_character_blocked",
        timeout=timeout,
        task_kind=task_kind,
        policy_version=routing_policy_version,
        cfg=agent_cfg,
        primary_backend=primary_backend,
        primary_model=primary_model,
        primary_temperature=normalize_temperature(((proposal_meta.get("routing") or {}).get("inference_temperature"))),
        research_context=research_context,
        session=prepare_task_cli_session(
            tid=tid,
            task=task,
            backend=primary_backend,
            model=primary_model,
            agent_cfg=agent_cfg,
        ),
        workdir=workspace_dir,
        invoke_cli_runner=invoke_cli_runner,
        coalesce_cli_output=coalesce_cli_output,
        normalize_temperature=normalize_temperature,
    )
    if not repaired:
        return None
    repaired_command, repaired_tool_calls = extract_structured_action_fields(str(repaired.get("raw") or ""))
    if not repaired_command and not repaired_tool_calls:
        return None
    if repaired_command and repaired_command.strip() == str(command or "").strip() and not repaired_tool_calls:
        return None
    append_stage(
        pipeline,
        name="proposal_repair",
        status="ok",
        metadata={
            "reason": "shell_meta_character_blocked",
            "repair_backend": repaired.get("backend_used"),
            "repair_model": repaired.get("model"),
        },
    )
    repaired_run = get_core_services().task_execution_service.execute_local_step(
        tid=tid,
        task=task,
        command=repaired_command,
        tool_calls=repaired_tool_calls,
        execution_policy=execution_policy,
        guard_cfg=agent_cfg,
        working_directory=workspace_dir,
        pipeline=pipeline,
        exec_started_at=exec_started_at,
    )
    return {
        "reason": _extract_reason(str(repaired.get("raw") or "")) or "Repaired proposal after shell policy block.",
        "command": repaired_command,
        "tool_calls": repaired_tool_calls,
        "execution_run": repaired_run,
        "repair_meta": {
            "attempted": True,
            "trigger": "shell_meta_character_blocked",
            "repair_backend": repaired.get("backend_used"),
            "repair_model": repaired.get("model"),
            "output_source": repaired.get("output_source"),
        },
    }
