import concurrent.futures
import json
import os
import time
from typing import Optional

from flask import Blueprint, current_app, g

from agent.auth import check_auth
from agent.common.api_envelope import unwrap_api_envelope
from agent.common.errors import (
    TaskConflictError,
    TaskNotFoundError,
    ToolGuardrailError,
    WorkerForwardingError,
    api_response,
)
from agent.common.sgpt import SUPPORTED_CLI_BACKENDS, run_llm_cli_command
from agent.llm_integration import _call_llm
from agent.llm_benchmarks import estimate_cost_units
from agent.metrics import RETRIES_TOTAL, TASK_COMPLETED, TASK_FAILED
from agent.models import (
    TaskStepExecuteRequest,
    TaskStepExecuteResponse,
    TaskStepProposeRequest,
    TaskStepProposeResponse,
)
from agent.services.repository_registry import get_repository_registry
from agent.pipeline_trace import append_stage, new_pipeline_trace
from agent.research_backend import normalize_research_artifact
from agent.runtime_policy import build_trace_record, normalize_task_kind, resolve_cli_backend, review_policy, runtime_routing_config
from agent.routes.tasks.utils import _forward_to_worker, _update_local_task_status
from agent.services.service_registry import get_core_services
from agent.services.task_execution_policy_service import (
    classify_execution_failure,
    compute_execution_retry_delay,
    resolve_execution_policy,
    should_retry_execution,
)
from agent.shell import get_shell
from agent.tool_guardrails import estimate_text_tokens, estimate_tool_calls_tokens, evaluate_tool_call_guardrails
from agent.tools import registry as tool_registry
from agent.utils import _extract_command, _extract_reason, _extract_tool_calls, _log_terminal_entry, validate_request

execution_bp = Blueprint("tasks_execution", __name__)


def _services():
    return get_core_services()


def _repos():
    return get_repository_registry()


def _log():
    return _services().log_service.bind(__name__)


def _apply_implicit_execution_defaults(execution_policy, request_data: TaskStepExecuteRequest, agent_cfg: dict) -> None:
    explicit_fields = set(getattr(request_data, "model_fields_set", set()) or set())
    if "retries" not in explicit_fields and agent_cfg.get("command_retries") is not None:
        execution_policy.retries = max(0, min(int(agent_cfg.get("command_retries") or 0), 10))
    if "retry_delay" not in explicit_fields and agent_cfg.get("command_retry_delay") is not None:
        execution_policy.retry_delay_seconds = max(0, min(int(agent_cfg.get("command_retry_delay") or 0), 60))
    if request_data.retry_policy_override is None and agent_cfg.get("command_retryable_exit_codes") is not None:
        execution_policy.retryable_exit_codes = [int(code) for code in list(agent_cfg.get("command_retryable_exit_codes") or [])]
    if request_data.retry_policy_override is None and agent_cfg.get("command_retry_on_timeouts") is not None:
        execution_policy.retry_on_timeouts = bool(agent_cfg.get("command_retry_on_timeouts"))


def _execute_shell_command_with_policy(
    *,
    tid: str | None,
    command: str,
    execution_policy,
) -> tuple[str, int | None, int, str, list[dict]]:
    shell = get_shell()
    retries_used = 0
    attempt = 0
    latest_output = ""
    latest_exit_code: int | None = -1
    failure_type = "success"
    retry_history: list[dict] = []

    while True:
        attempt += 1
        latest_output, latest_exit_code = shell.execute(command, timeout=execution_policy.timeout_seconds)
        failure_type = classify_execution_failure(latest_exit_code, latest_output)
        if latest_exit_code == 0:
            return latest_output, latest_exit_code, retries_used, failure_type, retry_history

        should_retry = retries_used < execution_policy.retries and should_retry_execution(
            exit_code=latest_exit_code,
            output=latest_output,
            policy=execution_policy,
        )
        delay = compute_execution_retry_delay(policy=execution_policy, attempt=retries_used + 1) if should_retry else 0.0
        retry_history.append(
            {
                "attempt": attempt,
                "exit_code": latest_exit_code,
                "failure_type": failure_type,
                "retry_scheduled": should_retry,
                "delay_seconds": round(delay, 3),
            }
        )
        if not should_retry:
            return latest_output, latest_exit_code, retries_used, failure_type, retry_history

        retries_used += 1
        RETRIES_TOTAL.inc()
        _log().info(
            "Task %s Shell-Fehler (%s, exit_code %s). Retry in %.2fs... (%s/%s)",
            tid or "<direct>",
            failure_type,
            latest_exit_code,
            delay,
            retries_used,
            execution_policy.retries,
        )
        if delay > 0:
            time.sleep(delay)


def _benchmarks_path() -> str:
    data_dir = current_app.config.get("DATA_DIR") or "data"
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, "llm_model_benchmarks.json")


def _default_metric_bucket() -> dict:
    return {
        "total": 0,
        "success": 0,
        "failed": 0,
        "quality_pass": 0,
        "quality_fail": 0,
        "latency_ms_total": 0,
        "tokens_total": 0,
        "cost_units_total": 0.0,
        "last_seen": None,
    }


def _append_sample(
    target: dict, now: int, success: bool, quality_passed: bool, latency_ms: int, tokens_total: int
) -> None:
    cfg = (current_app.config.get("AGENT_CONFIG", {}) or {}).get("benchmark_retention", {}) or {}
    max_samples = max(50, min(50000, int(cfg.get("max_samples") or 2000)))
    max_days = max(1, min(3650, int(cfg.get("max_days") or 90)))
    min_ts = int(now) - (max_days * 86400)

    samples = target.setdefault("samples", [])
    if not isinstance(samples, list):
        samples = []
        target["samples"] = samples
    else:
        samples[:] = [s for s in samples if int((s or {}).get("ts") or 0) >= min_ts]
    samples.append(
        {
            "ts": int(now),
            "success": bool(success),
            "quality_passed": bool(quality_passed),
            "latency_ms": max(0, int(latency_ms or 0)),
            "tokens_total": max(0, int(tokens_total or 0)),
        }
    )
    if len(samples) > max_samples:
        del samples[: len(samples) - max_samples]


def _resolve_cli_backend(task_kind: str, requested_backend: str = "auto", agent_cfg: dict | None = None) -> tuple[str, str]:
    backend, reason, _ = resolve_cli_backend(
        task_kind=task_kind,
        requested_backend=requested_backend,
        supported_backends=SUPPORTED_CLI_BACKENDS,
        agent_cfg=agent_cfg if agent_cfg is not None else (current_app.config.get("AGENT_CONFIG", {}) or {}),
        fallback_backend="sgpt",
    )
    return backend, reason


def _build_research_result(raw_res: str, backend_used: str, tid: str | None, rc: int, cli_err: str, latency_ms: int) -> dict:
    artifact = normalize_research_artifact(
        raw_res,
        backend=backend_used,
        task_id=tid,
        cli_result={"returncode": rc, "latency_ms": latency_ms, "stderr_preview": (cli_err or "")[:240]},
    )
    return {
        "reason": artifact.get("summary") or "Research report generated",
        "raw": raw_res,
        "research_artifact": artifact,
        "backend": backend_used,
        "command": None,
        "tool_calls": None,
        "cli_result": {"returncode": rc, "latency_ms": latency_ms, "stderr_preview": (cli_err or "")[:240]},
    }


def _build_review_state(agent_cfg: dict, backend: str, task_kind: str) -> dict:
    policy = review_policy(agent_cfg, backend=backend, task_kind=task_kind)
    return {
        "required": bool(policy.get("required")),
        "status": "pending" if policy.get("required") else "not_required",
        "policy_version": policy.get("policy_version"),
        "reason": policy.get("reason"),
        "reviewed_by": None,
        "reviewed_at": None,
        "comment": None,
    }


def _get_worker_execution_context(task: dict | None) -> dict:
    execution_context = dict((task or {}).get("worker_execution_context") or {})
    if execution_context:
        return execution_context
    bundle_id = str((task or {}).get("context_bundle_id") or "").strip()
    if not bundle_id:
        return {}
    bundle = _repos().context_bundle_repo.get_by_id(bundle_id)
    if bundle is None:
        return {}
    return {
        "context_bundle_id": bundle.id,
        "context": {
            "context_text": bundle.context_text,
            "chunks": list(bundle.chunks or []),
            "token_estimate": int(bundle.token_estimate or 0),
            "bundle_metadata": dict(bundle.bundle_metadata or {}),
        },
    }


def _tool_definitions_for_task(task: dict | None) -> list[dict]:
    execution_context = _get_worker_execution_context(task)
    allowed_tools = list(execution_context.get("allowed_tools") or [])
    if allowed_tools:
        return tool_registry.get_tool_definitions(allowlist=allowed_tools)
    return tool_registry.get_tool_definitions()


def _build_task_propose_prompt(*, tid: str, task: dict, base_prompt: str) -> tuple[str, dict]:
    execution_context = _get_worker_execution_context(task)
    context_payload = dict(execution_context.get("context") or {})
    context_text = str(context_payload.get("context_text") or "").strip()
    allowed_tools = list(execution_context.get("allowed_tools") or [])
    expected_output_schema = dict(execution_context.get("expected_output_schema") or {})
    tools_desc = json.dumps(_tool_definitions_for_task(task), indent=2, ensure_ascii=False)

    prompt_sections: list[str] = []
    system_prompt = _get_system_prompt_for_task(tid)
    if system_prompt:
        prompt_sections.append(system_prompt)

    prompt_sections.append(f"Aktueller Auftrag: {base_prompt}")

    if context_text:
        prompt_sections.append(f"Selektierter Hub-Kontext:\n{context_text}")

    if expected_output_schema:
        prompt_sections.append(
            "Erwartetes Ausgabeschema (JSON Schema oder Strukturhinweis):\n"
            f"{json.dumps(expected_output_schema, indent=2, ensure_ascii=False)}"
        )

    prompt_sections.append(f"Dir stehen folgende Werkzeuge zur Verfügung:\n{tools_desc}")
    prompt_sections.append(
        "Antworte IMMER im JSON-Format mit folgenden Feldern:\n"
        "{\n"
        '  "reason": "Kurze Begründung",\n'
        '  "command": "Shell-Befehl (optional)",\n'
        '  "tool_calls": [ { "name": "tool_name", "args": { "arg1": "val1" } } ] (optional)\n'
        "}"
    )

    return "\n\n".join(section for section in prompt_sections if section), {
        "context_bundle_id": execution_context.get("context_bundle_id") or task.get("context_bundle_id"),
        "allowed_tools": allowed_tools,
        "expected_output_schema": expected_output_schema,
        "context_chunk_count": len(context_payload.get("chunks") or []),
        "has_context_text": bool(context_text),
    }


def _persist_research_artifact(*, tid: str, task: dict | None, research_artifact: dict | None) -> dict | None:
    return _services().task_execution_tracking_service.persist_research_artifact(
        tid=tid,
        task=task,
        research_artifact=research_artifact,
    )


def _sync_worker_result_tracking(
    *,
    tid: str,
    task: dict | None,
    status: str,
    output: str,
    trace: dict,
    artifact_refs: list[dict] | None = None,
) -> dict | None:
    return _services().task_execution_tracking_service.sync_worker_result_tracking(
        tid=tid,
        task=task,
        status=status,
        output=output,
        trace=trace,
        artifact_refs=artifact_refs,
    )


def _get_system_prompt_for_task(tid: str) -> Optional[str]:
    task = _repos().task_repo.get_by_id(tid)
    if not task:
        return None

    role_id = task.assigned_role_id
    template_id = None

    # Falls keine Rolle direkt zugewiesen, versuchen wir sie über den Agenten und das Team zu finden
    if task.team_id and task.assigned_agent_url:
        members = _repos().team_member_repo.get_by_team(task.team_id)
        for m in members:
            if m.agent_url == task.assigned_agent_url:
                if not role_id:
                    role_id = m.role_id
                template_id = getattr(m, "custom_template_id", None)
                break

    if role_id and not template_id:
        role = _repos().role_repo.get_by_id(role_id)
        if role:
            template_id = role.default_template_id

    if template_id:
        template = _repos().template_repo.get_by_id(template_id)
        if template:
            prompt = template.prompt_template

            # Variablen ersetzen
            variables = {
                "agent_name": current_app.config.get("AGENT_NAME", "Unbekannter Agent"),
                "task_title": task.title or "Kein Titel",
                "task_description": task.description or "Keine Beschreibung",
            }

            if task.team_id:
                team = _repos().team_repo.get_by_id(task.team_id)
                if team:
                    variables["team_name"] = team.name

            if role_id:
                role = _repos().role_repo.get_by_id(role_id)
                if role:
                    variables["role_name"] = role.name

            for k, v in variables.items():
                prompt = prompt.replace("{{" + k + "}}", str(v))

            return prompt

    return None


def _run_async_propose(
    app_instance,
    tid: str,
    provider: str,
    model: str,
    prompt: str,
    urls: dict,
    api_key: str,
    history: list,
    agent_name: str,
):
    with app_instance.app_context():
        try:
            raw_res = _call_llm(
                provider=provider, model=model, prompt=prompt, urls=urls, api_key=api_key, history=history
            )

            if not raw_res:
                raise RuntimeError("LLM-Aufruf lieferte kein Ergebnis (Timeout oder Fehler).")

            reason = _extract_reason(raw_res)
            command = _extract_command(raw_res)
            tool_calls = _extract_tool_calls(raw_res)

            proposal = {"reason": reason}
            if command and command != raw_res.strip():
                proposal["command"] = command
            if tool_calls:
                proposal["tool_calls"] = tool_calls

            _update_local_task_status(tid, "proposing", last_proposal=proposal)

            _log_terminal_entry(agent_name, 0, "in", prompt=prompt, task_id=tid)
            _log_terminal_entry(
                agent_name, 0, "out", reason=reason, command=command, tool_calls=tool_calls, task_id=tid
            )

            _log().info("Asynchroner Vorschlag für Task %s abgeschlossen.", tid)
        except Exception as e:
            _log().error("Fehler bei asynchronem Vorschlag für Task %s: %s", tid, e)
            try:
                _update_local_task_status(tid, "failed", error=str(e))
            except Exception as e2:
                _log().error("Fehler beim Setzen des Fehlerstatus für Task %s: %s", tid, e2)


def _append_guardrail_block_history(
    tid: str,
    task: dict | None,
    command: str | None,
    tool_calls: list | None,
    decision,
    reason: str = "tool_guardrail_blocked",
) -> None:
    history = list((task or {}).get("history", []) or [])
    history.append(
        {
            "event_type": "tool_guardrail_blocked",
            "reason": reason,
            "command": command,
            "tool_calls": tool_calls or [],
            "blocked_tools": decision.blocked_tools,
            "blocked_reasons": decision.reasons,
            "guardrails": decision.details,
            "timestamp": time.time(),
        }
    )
    _update_local_task_status(
        tid,
        "failed",
        history=history,
        last_output=f"[tool_guardrail] blocked: {', '.join(decision.reasons)}",
        last_exit_code=1,
    )


@execution_bp.route("/step/propose", methods=["POST"])
@check_auth
@validate_request(TaskStepProposeRequest)
def propose_step():
    """
    Nächsten Schritt vorschlagen (LLM)
    ---
    responses:
      200:
        description: Vorschlag erhalten
    """
    data: TaskStepProposeRequest = g.validated_data
    cfg = current_app.config["AGENT_CONFIG"]

    prompt = data.prompt or "Was soll ich als nächstes tun?"

    if data.providers:
        results = {}

        def _call_single(p_name):
            try:
                p_parts = p_name.split(":", 1)
                p = p_parts[0]
                m = p_parts[1] if len(p_parts) > 1 else (data.model or cfg.get("model", "llama3"))

                res = _call_llm(
                    provider=p,
                    model=m,
                    prompt=prompt,
                    urls=current_app.config["PROVIDER_URLS"],
                    api_key=current_app.config["OPENAI_API_KEY"],
                )
                return p_name, {
                    "raw": res,
                    "reason": _extract_reason(res),
                    "command": _extract_command(res),
                    "tool_calls": _extract_tool_calls(res),
                }
            except Exception as e:
                return p_name, {"error": str(e)}

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(data.providers)) as executor:
            future_to_provider = {executor.submit(_call_single, p): p for p in data.providers}
            for future in concurrent.futures.as_completed(future_to_provider):
                p_name, res = future.result()
                results[p_name] = res

        main_p = data.providers[0]
        main_res = results.get(main_p, {})

        return api_response(
            data=TaskStepProposeResponse(
                reason=main_res.get("reason", "Fehler bei primärem Provider"),
                command=main_res.get("command"),
                tool_calls=main_res.get("tool_calls"),
                raw=main_res.get("raw", ""),
                comparisons=results,
            ).model_dump()
        )

    provider = data.provider or cfg.get("provider", "ollama")
    model = data.model or cfg.get("model", "llama3")

    raw_res = _call_llm(
        provider=provider,
        model=model,
        prompt=prompt,
        urls=current_app.config["PROVIDER_URLS"],
        api_key=current_app.config["OPENAI_API_KEY"],
    )

    reason = _extract_reason(raw_res)
    command = _extract_command(raw_res)
    tool_calls = _extract_tool_calls(raw_res)

    if data.task_id:
        proposal = {"reason": reason}
        if command and command != raw_res.strip():
            proposal["command"] = command
        if tool_calls:
            proposal["tool_calls"] = tool_calls

        _update_local_task_status(data.task_id, "proposing", last_proposal=proposal)
        _log_terminal_entry(current_app.config["AGENT_NAME"], 0, "in", prompt=prompt, task_id=data.task_id)
        _log_terminal_entry(
            current_app.config["AGENT_NAME"],
            0,
            "out",
            reason=reason,
            command=command,
            tool_calls=tool_calls,
            task_id=data.task_id,
        )

    return api_response(
        data=TaskStepProposeResponse(
            reason=reason, command=command if command != raw_res.strip() else None, tool_calls=tool_calls, raw=raw_res
        ).model_dump()
    )


@execution_bp.route("/step/execute", methods=["POST"])
@check_auth
@validate_request(TaskStepExecuteRequest)
def execute_step():
    """
    Vorgeschlagenen Schritt ausführen
    ---
    responses:
      200:
        description: Schritt ausgeführt
    """
    data: TaskStepExecuteRequest = g.validated_data
    agent_cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
    execution_policy = resolve_execution_policy(
        data,
        agent_cfg=agent_cfg,
        source="execute_step",
    )
    _apply_implicit_execution_defaults(execution_policy, data, agent_cfg)

    output_parts = []
    overall_exit_code = 0
    retries_used = 0
    failure_type = "success"
    retry_history: list[dict] = []

    guard_cfg = current_app.config.get("AGENT_CONFIG", {})
    if data.tool_calls:
        token_usage = {
            "prompt_tokens": estimate_text_tokens(data.command),
            "tool_calls_tokens": estimate_tool_calls_tokens(data.tool_calls),
            "estimated_total_tokens": estimate_text_tokens(data.command) + estimate_tool_calls_tokens(data.tool_calls),
        }
        decision = evaluate_tool_call_guardrails(data.tool_calls, guard_cfg, token_usage=token_usage)
        if not decision.allowed:
            if data.task_id:
                from agent.routes.tasks.utils import _get_local_task_status

                _append_guardrail_block_history(
                    data.task_id,
                    _get_local_task_status(data.task_id),
                    data.command,
                    data.tool_calls,
                    decision,
                )
            raise ToolGuardrailError(
                details={
                    "blocked_tools": decision.blocked_tools,
                    "blocked_reasons": decision.reasons,
                    "guardrails": decision.details,
                }
            )
        for tc in data.tool_calls:
            name = tc.get("name")
            args = tc.get("args", {})
            tool_res = tool_registry.execute(name, args)
            res_str = f"Tool '{name}': {'Erfolg' if tool_res.success else 'Fehler'}"
            if tool_res.output:
                res_str += f"\nOutput: {tool_res.output}"
            if tool_res.error:
                res_str += f"\nError: {tool_res.error}"
                overall_exit_code = 1
            output_parts.append(res_str)

    if data.command:
        output, exit_code, retries_used, failure_type, retry_history = _execute_shell_command_with_policy(
            tid=data.task_id,
            command=data.command,
            execution_policy=execution_policy,
        )
        output_parts.append(output)
        if exit_code != 0:
            overall_exit_code = exit_code

    final_output = "\n---\n".join(output_parts)
    final_exit_code = overall_exit_code

    if data.task_id:
        status = "completed" if final_exit_code == 0 else "failed"
        _update_local_task_status(data.task_id, status, last_output=final_output, last_exit_code=final_exit_code)
        if status == "completed":
            TASK_COMPLETED.inc()
        else:
            TASK_FAILED.inc()

    _log_terminal_entry(
        current_app.config["AGENT_NAME"],
        0,
        "out",
        command=data.command,
        tool_calls=data.tool_calls,
        task_id=data.task_id,
    )
    _log_terminal_entry(
        current_app.config["AGENT_NAME"], 0, "in", output=final_output, exit_code=final_exit_code, task_id=data.task_id
    )

    estimated_tokens = max(
        0,
        estimate_text_tokens(data.command) + estimate_text_tokens(final_output) + estimate_tool_calls_tokens(data.tool_calls),
    )
    cost_units, pricing_source = estimate_cost_units(
        current_app.config.get("AGENT_CONFIG", {}) or {},
        "",
        "",
        estimated_tokens,
    )

    return api_response(
        data={
            **TaskStepExecuteResponse(
                output=final_output,
                exit_code=final_exit_code,
                task_id=data.task_id,
                status="completed" if final_exit_code == 0 else "failed",
                retry_history=retry_history if data.command else [],
                cost_summary={
                    "provider": None,
                    "model": None,
                    "task_kind": data.task_kind,
                    "tokens_total": estimated_tokens,
                    "cost_units": cost_units,
                    "latency_ms": None,
                    "pricing_source": pricing_source,
                },
            ).model_dump(),
            "retries_used": retries_used,
            "failure_type": failure_type if data.command else ("success" if final_exit_code == 0 else "tool_failure"),
            "execution_policy": execution_policy.model_dump(),
        }
    )


@execution_bp.route("/tasks/<tid>/step/propose", methods=["POST"])
@check_auth
@validate_request(TaskStepProposeRequest)
def task_propose(tid):
    """
    Vorschlag für einen spezifischen Task (v2)
    ---
    parameters:
      - name: tid
        in: path
        type: string
        required: true
    responses:
      200:
        description: Vorschlag erhalten
    """
    from agent.config import settings

    data: TaskStepProposeRequest = g.validated_data
    from agent.routes.tasks.utils import _get_local_task_status

    task = _get_local_task_status(tid)
    if not task:
        raise TaskNotFoundError()

    worker_url = task.get("assigned_agent_url")
    if worker_url:
        my_url = settings.agent_url or f"http://localhost:{settings.port}"
        if worker_url.rstrip("/") != my_url.rstrip("/"):
            try:
                res = _forward_to_worker(
                    worker_url, f"/tasks/{tid}/step/propose", data.model_dump(), token=task.get("assigned_agent_token")
                )
                res = unwrap_api_envelope(res)
                if isinstance(res, dict) and "command" in res:
                    _update_local_task_status(tid, "proposing", last_proposal=res)
                return api_response(data=res)
            except Exception as e:
                _log().error("Forwarding an %s fehlgeschlagen: %s", worker_url, e)
                raise WorkerForwardingError(details={"details": str(e), "worker_url": worker_url})

    cfg = current_app.config["AGENT_CONFIG"]
    base_prompt = data.prompt or task.get("description") or task.get("prompt") or "Bearbeite Task " + tid
    prompt, worker_context_meta = _build_task_propose_prompt(tid=tid, task=task, base_prompt=base_prompt)

    if data.providers:
        task_kind = normalize_task_kind(None, base_prompt)
        timeout = int((current_app.config.get("AGENT_CONFIG", {}) or {}).get("command_timeout", 60) or 60)
        compare_policy = resolve_execution_policy(
            TaskStepExecuteRequest(timeout=timeout),
            agent_cfg=current_app.config.get("AGENT_CONFIG", {}) or {},
            source="task_propose_compare",
        )
        routing_policy_version = runtime_routing_config(current_app.config.get("AGENT_CONFIG", {}) or {})["policy_version"]

        def _run_single_provider(provider_entry: str) -> tuple[str, dict]:
            entry = str(provider_entry or "").strip()
            if not entry:
                return provider_entry, {"error": "invalid_provider_entry"}

            parts = entry.split(":", 1)
            requested_backend = str(parts[0] or "").strip().lower()
            selected_model = (
                (parts[1].strip() if len(parts) > 1 else "")
                or data.model
                or cfg.get("default_model")
                or cfg.get("model")
            )
            if requested_backend not in SUPPORTED_CLI_BACKENDS:
                return entry, {"error": f"unsupported_backend:{requested_backend}", "backend": requested_backend}

            effective_backend, routing_reason = _resolve_cli_backend(
                task_kind,
                requested_backend=requested_backend,
                agent_cfg=cfg,
            )
            started_at = time.time()
            rc, cli_out, cli_err, backend_used = run_llm_cli_command(
                prompt=prompt,
                options=["--no-interaction"],
                timeout=compare_policy.timeout_seconds,
                backend=effective_backend,
                model=selected_model,
                routing_policy={"mode": "adaptive", "task_kind": task_kind, "policy_version": routing_policy_version},
            )
            latency_ms = int((time.time() - started_at) * 1000)
            raw_res = cli_out or ""
            if rc != 0 and not raw_res.strip():
                return (
                    entry,
                    {
                        "error": cli_err or f"backend '{backend_used}' failed with exit code {rc}",
                        "backend": backend_used,
                        "routing": {
                            "task_kind": task_kind,
                            "effective_backend": effective_backend,
                            "reason": routing_reason,
                        },
                        "cli_result": {
                            "returncode": rc,
                            "latency_ms": latency_ms,
                            "stderr_preview": (cli_err or "")[:240],
                        },
                    },
                )
            if not raw_res:
                return (
                    entry,
                    {
                        "error": "empty_response",
                        "backend": backend_used,
                        "routing": {
                            "task_kind": task_kind,
                            "effective_backend": effective_backend,
                            "reason": routing_reason,
                        },
                        "cli_result": {
                            "returncode": rc,
                            "latency_ms": latency_ms,
                            "stderr_preview": (cli_err or "")[:240],
                        },
                    },
                )

            if backend_used == "deerflow":
                deerflow_res = _build_research_result(raw_res, backend_used, tid, rc, cli_err, latency_ms)
                deerflow_res["model"] = selected_model
                deerflow_res["routing"] = {
                    "task_kind": task_kind,
                    "effective_backend": effective_backend,
                    "reason": routing_reason,
                }
                return (entry, deerflow_res)

            return (
                entry,
                {
                    "reason": _extract_reason(raw_res),
                    "command": _extract_command(raw_res),
                    "tool_calls": _extract_tool_calls(raw_res),
                    "raw": raw_res,
                    "backend": backend_used,
                    "model": selected_model,
                    "routing": {
                        "task_kind": task_kind,
                        "effective_backend": effective_backend,
                        "reason": routing_reason,
                    },
                    "cli_result": {"returncode": rc, "latency_ms": latency_ms, "stderr_preview": (cli_err or "")[:240]},
                },
            )

        results = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(data.providers))) as executor:
            futures = {executor.submit(_run_single_provider, p): p for p in data.providers}
            for future in concurrent.futures.as_completed(futures):
                requested = futures[future]
                try:
                    provider_key, provider_result = future.result()
                    results[provider_key or requested] = provider_result
                except Exception as e:
                    _log().error("Multi-Provider CLI Call for %s failed: %s", requested, e)
                    results[requested] = {"error": str(e)}

        successful_results = [
            results.get(p)
            for p in data.providers
            if isinstance(results.get(p), dict) and not results.get(p).get("error")
        ]
        if not successful_results:
            return api_response(status="error", message="all_llm_failed", data={"comparisons": results}, code=502)

        main_res = results.get(data.providers[0])
        if not isinstance(main_res, dict) or main_res.get("error"):
            main_res = successful_results[0]

        proposal = {
            "reason": main_res.get("reason"),
            "backend": main_res.get("backend"),
            "model": main_res.get("model"),
            "routing": main_res.get("routing"),
            "cli_result": main_res.get("cli_result"),
            "comparisons": results,
        }
        trace = build_trace_record(
            task_id=tid,
            event_type="proposal_result",
            task_kind=(main_res.get("routing") or {}).get("task_kind"),
            backend=main_res.get("backend"),
            requested_backend=data.providers[0] if data.providers else "auto",
            routing_reason=((main_res.get("routing") or {}).get("reason")),
            policy_version=routing_policy_version,
            metadata={**worker_context_meta, "source": "task_propose_multi", "comparison_count": len(results)},
        )
        proposal["trace"] = trace
        proposal["provenance"] = {"source": "cli_backend", "backend": main_res.get("backend"), "trace_id": trace["trace_id"]}
        proposal["worker_context"] = worker_context_meta
        proposal["review"] = _build_review_state(
            current_app.config.get("AGENT_CONFIG", {}) or {},
            backend=str(main_res.get("backend") or ""),
            task_kind=str(((main_res.get("routing") or {}).get("task_kind") or "")),
        )
        if main_res.get("research_artifact"):
            proposal["research_artifact"] = main_res.get("research_artifact")
        if main_res.get("command") and main_res.get("command") != str(main_res.get("raw") or "").strip():
            proposal["command"] = main_res.get("command")
        if main_res.get("tool_calls"):
            proposal["tool_calls"] = main_res.get("tool_calls")

        _services().task_execution_tracking_service.persist_proposal_result(
            tid=tid,
            task=task,
            proposal=proposal,
            history_event={
                "event_type": "proposal_result",
                "reason": main_res.get("reason"),
                "backend": main_res.get("backend"),
                "routing_reason": ((main_res.get("routing") or {}).get("reason")),
                "latency_ms": int((main_res.get("cli_result") or {}).get("latency_ms") or 0),
                "returncode": int((main_res.get("cli_result") or {}).get("returncode") or 0),
                "comparison_count": len(results),
                "pipeline": None,
                "trace": trace,
            },
        )

        return api_response(
            data={
                "status": "proposing",
                "reason": main_res.get("reason"),
                "command": main_res.get("command")
                if main_res.get("command") != str(main_res.get("raw") or "").strip()
                else None,
                "tool_calls": main_res.get("tool_calls"),
                "raw": main_res.get("raw"),
                "backend": main_res.get("backend"),
                "model": main_res.get("model"),
                "routing": main_res.get("routing"),
                "cli_result": main_res.get("cli_result"),
                "comparisons": results,
                "research_artifact": main_res.get("research_artifact"),
                "worker_context": worker_context_meta,
                "trace": trace,
                "review": proposal.get("review"),
            }
        )

    task_kind = normalize_task_kind(None, base_prompt)
    effective_backend, routing_reason = _resolve_cli_backend(task_kind, requested_backend="auto")
    timeout = int((current_app.config.get("AGENT_CONFIG", {}) or {}).get("command_timeout", 60) or 60)
    pipeline = new_pipeline_trace(
        pipeline="task_propose",
        task_kind=task_kind,
        policy_version=runtime_routing_config(current_app.config.get("AGENT_CONFIG", {}) or {})["policy_version"],
        metadata={"task_id": tid, "requested_backend": "auto", **worker_context_meta},
    )
    append_stage(
        pipeline,
        name="route",
        status="ok",
        metadata={"effective_backend": effective_backend, "reason": routing_reason},
    )
    started_at = time.time()
    rc, cli_out, cli_err, backend_used = run_llm_cli_command(
        prompt=prompt,
        options=["--no-interaction"],
        timeout=timeout,
        backend=effective_backend,
        model=data.model,
        routing_policy={
            "mode": "adaptive",
            "task_kind": task_kind,
            "policy_version": runtime_routing_config(current_app.config.get("AGENT_CONFIG", {}) or {})["policy_version"],
        },
    )
    latency_ms = int((time.time() - started_at) * 1000)
    append_stage(
        pipeline,
        name="execute",
        status="ok" if rc == 0 or bool(cli_out) else "error",
        metadata={"backend_used": backend_used, "returncode": rc, "latency_ms": latency_ms},
        started_at=started_at,
    )
    raw_res = cli_out or ""
    if rc != 0 and not raw_res.strip():
        return api_response(
            status="error",
            message="llm_cli_failed",
            data={
                "details": cli_err or f"backend '{backend_used}' failed with exit code {rc}",
                "backend": backend_used,
            },
            code=502,
        )

    if not raw_res:
        return api_response(status="error", message="llm_failed", code=502)

    if backend_used == "deerflow":
        research_res = _build_research_result(raw_res, backend_used, tid, rc, cli_err, latency_ms)
        routing = {"task_kind": task_kind, "effective_backend": effective_backend, "reason": routing_reason}
        trace = build_trace_record(
            task_id=tid,
            event_type="proposal_result",
            task_kind=task_kind,
            backend=backend_used,
            requested_backend="auto",
            routing_reason=routing_reason,
            policy_version=runtime_routing_config(current_app.config.get("AGENT_CONFIG", {}) or {})["policy_version"],
            metadata={**worker_context_meta, "source": "task_propose", "artifact_kind": "research_report"},
        )
        proposal = {
            "reason": research_res.get("reason"),
            "backend": backend_used,
            "model": data.model or cfg.get("default_model") or cfg.get("model"),
            "routing": routing,
            "cli_result": research_res.get("cli_result"),
            "research_artifact": research_res.get("research_artifact"),
            "trace": trace,
            "pipeline": {**pipeline, "trace_id": trace["trace_id"]},
            "provenance": {"source": "cli_backend", "backend": backend_used, "trace_id": trace["trace_id"]},
            "worker_context": worker_context_meta,
            "review": _build_review_state(current_app.config.get("AGENT_CONFIG", {}) or {}, backend_used, task_kind),
        }
        _services().task_execution_tracking_service.persist_proposal_result(
            tid=tid,
            task=task,
            proposal=proposal,
            history_event={
                "event_type": "proposal_result",
                "reason": research_res.get("reason"),
                "backend": backend_used,
                "routing_reason": routing_reason,
                "latency_ms": latency_ms,
                "returncode": rc,
                "artifact_kind": "research_report",
                "source_count": len((research_res.get("research_artifact") or {}).get("sources") or []),
                "pipeline": proposal.get("pipeline"),
                "trace": trace,
            },
        )
        return api_response(
            data={
                "status": "proposing",
                "reason": research_res.get("reason"),
                "raw": raw_res,
                "backend": backend_used,
                "routing": routing,
                "cli_result": research_res.get("cli_result"),
                "research_artifact": research_res.get("research_artifact"),
                "pipeline": proposal.get("pipeline"),
                "worker_context": worker_context_meta,
                "trace": trace,
                "review": proposal.get("review"),
            }
        )

    reason = _extract_reason(raw_res)
    command = _extract_command(raw_res)
    tool_calls = _extract_tool_calls(raw_res)
    append_stage(
        pipeline,
        name="parse",
        status="ok",
        metadata={"has_command": bool(command), "tool_call_count": len(tool_calls or [])},
    )

    routing = {"task_kind": task_kind, "effective_backend": effective_backend, "reason": routing_reason}
    trace = build_trace_record(
        task_id=tid,
        event_type="proposal_result",
        task_kind=task_kind,
        backend=backend_used,
        requested_backend="auto",
        routing_reason=routing_reason,
        policy_version=runtime_routing_config(current_app.config.get("AGENT_CONFIG", {}) or {})["policy_version"],
        metadata={**worker_context_meta, "source": "task_propose"},
    )
    proposal = {
        "reason": reason,
        "backend": backend_used,
        "model": data.model or cfg.get("default_model") or cfg.get("model"),
        "routing": routing,
        "cli_result": {
            "returncode": rc,
            "latency_ms": latency_ms,
            "stderr_preview": (cli_err or "")[:240],
        },
        "trace": trace,
        "pipeline": {**pipeline, "trace_id": trace["trace_id"]},
        "provenance": {"source": "cli_backend", "backend": backend_used, "trace_id": trace["trace_id"]},
        "worker_context": worker_context_meta,
        "review": _build_review_state(current_app.config.get("AGENT_CONFIG", {}) or {}, backend_used, task_kind),
    }
    if command and command != raw_res.strip():
        proposal["command"] = command
    if tool_calls:
        proposal["tool_calls"] = tool_calls

    _services().task_execution_tracking_service.persist_proposal_result(
        tid=tid,
        task=task,
        proposal=proposal,
        history_event={
            "event_type": "proposal_result",
            "reason": reason,
            "backend": backend_used,
            "routing_reason": routing_reason,
            "latency_ms": latency_ms,
            "returncode": rc,
            "pipeline": proposal.get("pipeline"),
            "trace": trace,
        },
    )

    _log_terminal_entry(current_app.config["AGENT_NAME"], 0, "in", prompt=prompt, task_id=tid)
    _log_terminal_entry(
        current_app.config["AGENT_NAME"], 0, "out", reason=reason, command=command, tool_calls=tool_calls, task_id=tid
    )

    return api_response(
        data={
            "status": "proposing",
            "reason": reason,
            "command": command if command != raw_res.strip() else None,
            "tool_calls": tool_calls,
            "raw": raw_res,
            "backend": backend_used,
            "routing": routing,
            "cli_result": proposal.get("cli_result"),
            "pipeline": proposal.get("pipeline"),
            "worker_context": worker_context_meta,
            "trace": trace,
            "review": proposal.get("review"),
        }
    )


@execution_bp.route("/tasks/<tid>/step/execute", methods=["POST"])
@check_auth
@validate_request(TaskStepExecuteRequest)
def task_execute(tid):
    """
    Ausführung für einen spezifischen Task (v2)
    ---
    parameters:
      - name: tid
        in: path
        type: string
        required: true
    responses:
      200:
        description: Schritt ausgeführt
    """
    from agent.config import settings

    data: TaskStepExecuteRequest = g.validated_data
    agent_cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
    execution_policy = resolve_execution_policy(
        data,
        agent_cfg=agent_cfg,
        source="task_execute",
    )
    _apply_implicit_execution_defaults(execution_policy, data, agent_cfg)
    from agent.routes.tasks.utils import _get_local_task_status

    task = _get_local_task_status(tid)
    if not task:
        raise TaskNotFoundError()

    worker_url = task.get("assigned_agent_url")
    if worker_url:
        my_url = settings.agent_url or f"http://localhost:{settings.port}"
        if worker_url.rstrip("/") != my_url.rstrip("/"):
            try:
                res = _forward_to_worker(
                    worker_url, f"/tasks/{tid}/step/execute", data.model_dump(), token=task.get("assigned_agent_token")
                )
                res = unwrap_api_envelope(res)

                if isinstance(res, dict) and "status" in res:
                    history = task.get("history", [])
                    proposal_meta = task.get("last_proposal", {}) or {}
                    history.append(
                        {
                            "event_type": "execution_result",
                            "prompt": task.get("description"),
                            "reason": "Forwarded to " + worker_url,
                            "command": data.command or task.get("last_proposal", {}).get("command"),
                            "output": res.get("output"),
                            "exit_code": res.get("exit_code"),
                            "backend": proposal_meta.get("backend"),
                            "routing_reason": ((proposal_meta.get("routing") or {}).get("reason")),
                            "forwarded": True,
                            "timestamp": time.time(),
                        }
                    )
                    _update_local_task_status(tid, res["status"], history=history)

                return api_response(data=res)
            except Exception as e:
                _log().error("Forwarding (Execute) an %s fehlgeschlagen: %s", worker_url, e)
                raise WorkerForwardingError(details={"details": str(e), "worker_url": worker_url})

    command = data.command
    tool_calls = data.tool_calls
    reason = "Direkte Ausführung"

    if not command and not tool_calls:
        proposal = task.get("last_proposal")
        if not proposal:
            raise TaskConflictError("no_proposal")
        research_artifact = proposal.get("research_artifact") if isinstance(proposal, dict) else None
        if isinstance(research_artifact, dict):
            review = (proposal.get("review") or {}) if isinstance(proposal, dict) else {}
            if review.get("required") and review.get("status") != "approved":
                raise TaskConflictError("research_review_required", details={"review": review, "task_id": tid})
            output = str(research_artifact.get("report_markdown") or "")
            pipeline = new_pipeline_trace(
                pipeline="task_execute",
                task_kind=((proposal.get("routing") or {}).get("task_kind")),
                policy_version=((proposal.get("trace") or {}).get("policy_version")),
                metadata={"task_id": tid, "artifact_execute": True},
            )
            append_stage(
                pipeline,
                name="artifact_finalize",
                status="ok",
                metadata={"artifact_kind": research_artifact.get("kind")},
            )
            trace = build_trace_record(
                task_id=tid,
                event_type="execution_result",
                task_kind=((proposal.get("routing") or {}).get("task_kind")),
                backend=proposal.get("backend"),
                requested_backend=proposal.get("backend"),
                routing_reason=((proposal.get("routing") or {}).get("reason")),
                policy_version=((proposal.get("trace") or {}).get("policy_version")),
                metadata={"source": "research_artifact_execute", "artifact_kind": research_artifact.get("kind")},
            )
            artifact_ref = _persist_research_artifact(tid=tid, task=task, research_artifact=research_artifact)
            TASK_COMPLETED.inc()
            tracking = _services().task_execution_tracking_service.finalize_execution_result(
                tid=tid,
                task=task,
                status="completed",
                reason=proposal.get("reason", "Research report persisted"),
                command=None,
                tool_calls=None,
                output=output,
                exit_code=0,
                retries_used=0,
                retry_history=[],
                failure_type="success",
                execution_duration_ms=0,
                trace=trace,
                pipeline={**pipeline, "trace_id": trace["trace_id"]},
                artifact_refs=[artifact_ref] if artifact_ref else None,
                extra_history={
                    "artifact_kind": research_artifact.get("kind"),
                    "artifact_ref": artifact_ref,
                    "source_count": len(research_artifact.get("sources") or []),
                },
            )
            res = TaskStepExecuteResponse(
                output=output,
                exit_code=0,
                task_id=tid,
                status="completed",
                retry_history=[],
                cost_summary=tracking["cost_summary"],
            )
            return api_response(
                data={
                    **res.model_dump(),
                    "execution_policy": execution_policy.model_dump(),
                    "trace": trace,
                    "pipeline": {**pipeline, "trace_id": trace["trace_id"]},
                    "review": review,
                    "artifacts": [artifact_ref] if artifact_ref else [],
                    "memory_entry_id": tracking["memory_entry"].id if tracking.get("memory_entry") else None,
                }
            )
        command = proposal.get("command")
        tool_calls = proposal.get("tool_calls")
        reason = proposal.get("reason", "Vorschlag ausgeführt")

    exec_started_at = time.time()
    pipeline = new_pipeline_trace(
        pipeline="task_execute",
        task_kind=((task.get("last_proposal", {}) or {}).get("routing") or {}).get("task_kind"),
        policy_version=((task.get("last_proposal", {}) or {}).get("trace") or {}).get("policy_version"),
        metadata={"task_id": tid},
    )
    output_parts = []
    overall_exit_code = 0
    retries_used = 0
    failure_type = "success"

    guard_cfg = current_app.config.get("AGENT_CONFIG", {})
    if tool_calls:
        token_usage = {
            "prompt_tokens": estimate_text_tokens(command or task.get("description")),
            "history_tokens": estimate_text_tokens(json.dumps(task.get("history", []), ensure_ascii=False)),
            "tool_calls_tokens": estimate_tool_calls_tokens(tool_calls),
        }
        token_usage["estimated_total_tokens"] = sum(int(token_usage.get(k) or 0) for k in token_usage)
        decision = evaluate_tool_call_guardrails(tool_calls, guard_cfg, token_usage=token_usage)
        append_stage(
            pipeline,
            name="guardrails",
            status="ok" if decision.allowed else "blocked",
            metadata={"tool_call_count": len(tool_calls or []), "blocked_tools": decision.blocked_tools},
        )
        if not decision.allowed:
            _append_guardrail_block_history(tid, task, command, tool_calls, decision)
            raise ToolGuardrailError(
                details={
                    "blocked_tools": decision.blocked_tools,
                    "blocked_reasons": decision.reasons,
                    "guardrails": decision.details,
                }
            )
        for tc in tool_calls:
            name = tc.get("name")
            args = tc.get("args", {})
            current_app.logger.info(f"Task {tid} führt Tool aus: {name} mit {args}")
            tool_res = tool_registry.execute(name, args)
            append_stage(
                pipeline,
                name="tool_call",
                status="ok" if tool_res.success else "error",
                metadata={"tool": name},
            )

            res_str = f"Tool '{name}': {'Erfolg' if tool_res.success else 'Fehler'}"
            if tool_res.output:
                res_str += f"\nOutput: {tool_res.output}"
            if tool_res.error:
                res_str += f"\nError: {tool_res.error}"
                overall_exit_code = 1

            output_parts.append(res_str)

    if command:
        cmd_output, cmd_exit_code, retries_used, failure_type, retry_history = _execute_shell_command_with_policy(
            tid=tid,
            command=command,
            execution_policy=execution_policy,
        )

        output_parts.append(cmd_output)
        if cmd_exit_code != 0:
            overall_exit_code = cmd_exit_code
        append_stage(
            pipeline,
            name="shell_execute",
            status="ok" if cmd_exit_code == 0 else "error",
            metadata={"exit_code": cmd_exit_code, "failure_type": failure_type, "retries_used": retries_used},
            started_at=exec_started_at,
        )

    output = "\n---\n".join(output_parts)
    exit_code = overall_exit_code
    execution_duration_ms = int((time.time() - exec_started_at) * 1000)
    retries_used = retries_used if command else 0
    failure_type = failure_type if command else ("success" if exit_code == 0 else "tool_failure")

    history = task.get("history", [])
    proposal_meta = task.get("last_proposal", {}) or {}
    trace = build_trace_record(
        task_id=tid,
        event_type="execution_result",
        task_kind=((proposal_meta.get("routing") or {}).get("task_kind")),
        backend=proposal_meta.get("backend"),
        requested_backend=proposal_meta.get("backend"),
        routing_reason=((proposal_meta.get("routing") or {}).get("reason")),
        policy_version=((proposal_meta.get("trace") or {}).get("policy_version")),
        metadata={"retries_used": retries_used, "duration_ms": execution_duration_ms, "failure_type": failure_type},
    )
    status = "completed" if exit_code == 0 else "failed"
    if status == "completed":
        TASK_COMPLETED.inc()
    else:
        TASK_FAILED.inc()

    tracking = _services().task_execution_tracking_service.finalize_execution_result(
        tid=tid,
        task=task,
        status=status,
        reason=reason,
        command=command,
        tool_calls=tool_calls,
        output=output,
        exit_code=exit_code,
        retries_used=retries_used,
        retry_history=retry_history,
        failure_type=failure_type,
        execution_duration_ms=execution_duration_ms,
        trace=trace,
        pipeline={**pipeline, "trace_id": trace["trace_id"]},
    )

    _log_terminal_entry(current_app.config["AGENT_NAME"], len(history), "out", command=command, task_id=tid)
    _log_terminal_entry(
        current_app.config["AGENT_NAME"], len(history), "in", output=output, exit_code=exit_code, task_id=tid
    )

    res = TaskStepExecuteResponse(
        output=output,
        exit_code=exit_code,
        task_id=tid,
        status=status,
        retry_history=retry_history,
        cost_summary=tracking["cost_summary"],
    )
    return api_response(
        data={
            **res.model_dump(),
            "trace": trace,
            "pipeline": {**pipeline, "trace_id": trace["trace_id"]},
            "memory_entry_id": tracking["memory_entry"].id if tracking.get("memory_entry") else None,
            "retries_used": retries_used,
            "failure_type": failure_type,
            "execution_policy": execution_policy.model_dump(),
        }
    )
