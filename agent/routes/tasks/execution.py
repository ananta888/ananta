import concurrent.futures
import json
import logging
import os
import time
from typing import Optional

from flask import Blueprint, current_app, g

from agent.auth import check_auth
from agent.common.api_envelope import unwrap_api_envelope
from agent.common.errors import api_response
from agent.common.sgpt import SUPPORTED_CLI_BACKENDS, run_llm_cli_command
from agent.llm_benchmarks import record_benchmark_sample as persist_benchmark_sample
from agent.llm_benchmarks import resolve_benchmark_identity as shared_resolve_benchmark_identity
from agent.llm_integration import _call_llm
from agent.metrics import RETRIES_TOTAL, TASK_COMPLETED, TASK_FAILED
from agent.models import (
    TaskStepExecuteRequest,
    TaskStepExecuteResponse,
    TaskStepProposeRequest,
    TaskStepProposeResponse,
)
from agent.repository import role_repo, task_repo, team_member_repo, template_repo
from agent.pipeline_trace import append_stage, new_pipeline_trace
from agent.research_backend import normalize_research_artifact
from agent.runtime_policy import build_trace_record, normalize_task_kind, resolve_cli_backend, review_policy, runtime_routing_config
from agent.routes.tasks.utils import _forward_to_worker, _update_local_task_status
from agent.shell import get_shell
from agent.tool_guardrails import estimate_text_tokens, estimate_tool_calls_tokens, evaluate_tool_call_guardrails
from agent.tools import registry as tool_registry
from agent.utils import _extract_command, _extract_reason, _extract_tool_calls, _log_terminal_entry, validate_request

execution_bp = Blueprint("tasks_execution", __name__)


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


def _record_benchmark_sample(
    provider: str,
    model: str,
    task_kind: str,
    success: bool,
    quality_gate_passed: bool,
    latency_ms: int,
    tokens_total: int,
) -> None:
    persist_benchmark_sample(
        data_dir=current_app.config.get("DATA_DIR") or "data",
        agent_cfg=current_app.config.get("AGENT_CONFIG", {}) or {},
        provider=provider,
        model=model,
        task_kind=task_kind,
        success=success,
        quality_gate_passed=quality_gate_passed,
        latency_ms=latency_ms,
        tokens_total=tokens_total,
    )


def _resolve_benchmark_identity(proposal_meta: dict | None, agent_cfg: dict | None) -> tuple[str, str]:
    return shared_resolve_benchmark_identity(proposal_meta, agent_cfg)


def _resolve_cli_backend(task_kind: str, requested_backend: str = "auto") -> tuple[str, str]:
    backend, reason, _ = resolve_cli_backend(
        task_kind=task_kind,
        requested_backend=requested_backend,
        supported_backends=SUPPORTED_CLI_BACKENDS,
        agent_cfg=current_app.config.get("AGENT_CONFIG", {}) or {},
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


def _get_system_prompt_for_task(tid: str) -> Optional[str]:
    from agent.repository import team_repo

    task = task_repo.get_by_id(tid)
    if not task:
        return None

    role_id = task.assigned_role_id
    template_id = None

    # Falls keine Rolle direkt zugewiesen, versuchen wir sie über den Agenten und das Team zu finden
    if task.team_id and task.assigned_agent_url:
        members = team_member_repo.get_by_team(task.team_id)
        for m in members:
            if m.agent_url == task.assigned_agent_url:
                if not role_id:
                    role_id = m.role_id
                template_id = getattr(m, "custom_template_id", None)
                break

    if role_id and not template_id:
        role = role_repo.get_by_id(role_id)
        if role:
            template_id = role.default_template_id

    if template_id:
        template = template_repo.get_by_id(template_id)
        if template:
            prompt = template.prompt_template

            # Variablen ersetzen
            variables = {
                "agent_name": current_app.config.get("AGENT_NAME", "Unbekannter Agent"),
                "task_title": task.title or "Kein Titel",
                "task_description": task.description or "Keine Beschreibung",
            }

            if task.team_id:
                team = team_repo.get_by_id(task.team_id)
                if team:
                    variables["team_name"] = team.name

            if role_id:
                role = role_repo.get_by_id(role_id)
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

            logging.info(f"Asynchroner Vorschlag für Task {tid} abgeschlossen.")
        except Exception as e:
            logging.error(f"Fehler bei asynchronem Vorschlag für Task {tid}: {e}")
            try:
                _update_local_task_status(tid, "failed", error=str(e))
            except Exception as e2:
                logging.error(f"Fehler beim Setzen des Fehlerstatus für Task {tid}: {e2}")


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

    output_parts = []
    overall_exit_code = 0

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
            return api_response(
                status="error",
                message="tool_guardrail_blocked",
                data={
                    "blocked_tools": decision.blocked_tools,
                    "blocked_reasons": decision.reasons,
                    "guardrails": decision.details,
                },
                code=400,
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
        shell = get_shell()
        output, exit_code = shell.execute(data.command, timeout=data.timeout or 60)
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

    return api_response(
        data=TaskStepExecuteResponse(
            output=final_output,
            exit_code=final_exit_code,
            task_id=data.task_id,
            status="completed" if final_exit_code == 0 else "failed",
        ).model_dump()
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
        return api_response(status="error", message="not_found", code=404)

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
                logging.error(f"Forwarding an {worker_url} fehlgeschlagen: {e}")
                return api_response(status="error", message="forwarding_failed", data={"details": str(e)}, code=502)

    cfg = current_app.config["AGENT_CONFIG"]
    base_prompt = data.prompt or task.get("description") or task.get("prompt") or "Bearbeite Task " + tid
    tools_desc = json.dumps(tool_registry.get_tool_definitions(), indent=2, ensure_ascii=False)

    system_prompt = _get_system_prompt_for_task(tid)
    if system_prompt:
        prompt = (
            f"{system_prompt}\n\n"
            f"Aktueller Auftrag: {base_prompt}\n\n"
            f"Dir stehen folgende Werkzeuge zur Verfügung:\n{tools_desc}\n\n"
            "Antworte IMMER im JSON-Format mit folgenden Feldern:\n"
            "{\n"
            '  "reason": "Kurze Begründung",\n'
            '  "command": "Shell-Befehl (optional)",\n'
            '  "tool_calls": [ { "name": "tool_name", "args": { "arg1": "val1" } } ] (optional)\n'
            "}"
        )
    else:
        prompt = (
            f"{base_prompt}\n\n"
            f"Dir stehen folgende Werkzeuge zur Verfügung:\n{tools_desc}\n\n"
            "Antworte IMMER im JSON-Format mit folgenden Feldern:\n"
            "{\n"
            '  "reason": "Kurze Begründung",\n'
            '  "command": "Shell-Befehl (optional)",\n'
            '  "tool_calls": [ { "name": "tool_name", "args": { "arg1": "val1" } } ] (optional)\n'
            "}"
        )

    if data.providers:
        task_kind = normalize_task_kind(None, base_prompt)
        timeout = int((current_app.config.get("AGENT_CONFIG", {}) or {}).get("command_timeout", 60) or 60)
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

            effective_backend, routing_reason = _resolve_cli_backend(task_kind, requested_backend=requested_backend)
            started_at = time.time()
            rc, cli_out, cli_err, backend_used = run_llm_cli_command(
                prompt=prompt,
                options=["--no-interaction"],
                timeout=timeout,
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
                    logging.error(f"Multi-Provider CLI Call for {requested} failed: {e}")
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
            metadata={"source": "task_propose_multi", "comparison_count": len(results)},
        )
        proposal["trace"] = trace
        proposal["provenance"] = {"source": "cli_backend", "backend": main_res.get("backend"), "trace_id": trace["trace_id"]}
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

        _update_local_task_status(tid, "proposing", last_proposal=proposal)

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
        metadata={"task_id": tid, "requested_backend": "auto"},
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
            metadata={"source": "task_propose", "artifact_kind": "research_report"},
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
            "review": _build_review_state(current_app.config.get("AGENT_CONFIG", {}) or {}, backend_used, task_kind),
        }
        history = list(task.get("history", []) or [])
        history.append(
            {
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
                "timestamp": time.time(),
            }
        )
        _update_local_task_status(tid, "proposing", last_proposal=proposal, history=history)
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
        metadata={"source": "task_propose"},
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
        "review": _build_review_state(current_app.config.get("AGENT_CONFIG", {}) or {}, backend_used, task_kind),
    }
    if command and command != raw_res.strip():
        proposal["command"] = command
    if tool_calls:
        proposal["tool_calls"] = tool_calls

    history = list(task.get("history", []) or [])
    history.append(
        {
            "event_type": "proposal_result",
            "reason": reason,
            "backend": backend_used,
            "routing_reason": routing_reason,
            "latency_ms": latency_ms,
            "returncode": rc,
            "pipeline": proposal.get("pipeline"),
            "trace": trace,
            "timestamp": time.time(),
        }
    )
    _update_local_task_status(tid, "proposing", last_proposal=proposal, history=history)

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
    from agent.routes.tasks.utils import _get_local_task_status

    task = _get_local_task_status(tid)
    if not task:
        return api_response(status="error", message="not_found", code=404)

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
                logging.error(f"Forwarding (Execute) an {worker_url} fehlgeschlagen: {e}")
                return api_response(status="error", message="forwarding_failed", data={"details": str(e)}, code=502)

    command = data.command
    tool_calls = data.tool_calls
    reason = "Direkte Ausführung"

    if not command and not tool_calls:
        proposal = task.get("last_proposal")
        if not proposal:
            return api_response(status="error", message="no_proposal", code=400)
        research_artifact = proposal.get("research_artifact") if isinstance(proposal, dict) else None
        if isinstance(research_artifact, dict):
            review = (proposal.get("review") or {}) if isinstance(proposal, dict) else {}
            if review.get("required") and review.get("status") != "approved":
                return api_response(
                    status="error",
                    message="research_review_required",
                    data={"review": review, "task_id": tid},
                    code=409,
                )
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
            history = list(task.get("history", []) or [])
            history.append(
                {
                    "event_type": "execution_result",
                    "prompt": task.get("description"),
                    "reason": proposal.get("reason", "Research report persisted"),
                    "command": None,
                    "tool_calls": None,
                    "output": output,
                    "exit_code": 0,
                    "backend": proposal.get("backend"),
                    "routing_reason": ((proposal.get("routing") or {}).get("reason")),
                    "artifact_kind": research_artifact.get("kind"),
                    "source_count": len(research_artifact.get("sources") or []),
                    "pipeline": {**pipeline, "trace_id": trace["trace_id"]},
                    "trace": trace,
                    "timestamp": time.time(),
                }
            )
            _update_local_task_status(
                tid,
                "completed",
                history=history,
                last_output=output,
                last_exit_code=0,
            )
            TASK_COMPLETED.inc()
            res = TaskStepExecuteResponse(output=output, exit_code=0, task_id=tid, status="completed")
            return api_response(
                data={**res.model_dump(), "trace": trace, "pipeline": {**pipeline, "trace_id": trace["trace_id"]}, "review": review}
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
            return api_response(
                status="error",
                message="tool_guardrail_blocked",
                data={
                    "blocked_tools": decision.blocked_tools,
                    "blocked_reasons": decision.reasons,
                    "guardrails": decision.details,
                },
                code=400,
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
        shell = get_shell()
        retries_left = data.retries or 0
        cmd_output, cmd_exit_code = "", -1

        while True:
            cmd_output, cmd_exit_code = shell.execute(command, timeout=data.timeout or 60)
            if cmd_exit_code == 0 or retries_left <= 0:
                break

            retries_left -= 1
            RETRIES_TOTAL.inc()
            logging.info(f"Task {tid} Shell-Fehler (exit_code {cmd_exit_code}). Wiederholung... ({retries_left} übrig)")
            time.sleep(data.retry_delay or 1)

        output_parts.append(cmd_output)
        if cmd_exit_code != 0:
            overall_exit_code = cmd_exit_code
        append_stage(
            pipeline,
            name="shell_execute",
            status="ok" if cmd_exit_code == 0 else "error",
            metadata={"exit_code": cmd_exit_code},
            started_at=exec_started_at,
        )

    output = "\n---\n".join(output_parts)
    exit_code = overall_exit_code
    execution_duration_ms = int((time.time() - exec_started_at) * 1000)
    retries_used = max(0, int((data.retries or 0) - retries_left)) if command else 0

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
        metadata={"retries_used": retries_used, "duration_ms": execution_duration_ms},
    )
    history.append(
        {
            "event_type": "execution_result",
            "prompt": task.get("description"),
            "reason": reason,
            "command": command,
            "tool_calls": tool_calls,
            "output": output,
            "exit_code": exit_code,
            "backend": proposal_meta.get("backend"),
            "routing_reason": ((proposal_meta.get("routing") or {}).get("reason")),
            "retries_used": retries_used,
            "duration_ms": execution_duration_ms,
            "pipeline": {**pipeline, "trace_id": trace["trace_id"]},
            "trace": trace,
            "timestamp": time.time(),
        }
    )

    status = "completed" if exit_code == 0 else "failed"
    if status == "completed":
        TASK_COMPLETED.inc()
    else:
        TASK_FAILED.inc()

    bench_provider, bench_model = _resolve_benchmark_identity(
        proposal_meta,
        current_app.config.get("AGENT_CONFIG", {}) or {},
    )
    bench_task_kind = normalize_task_kind(
        ((proposal_meta.get("routing") or {}).get("task_kind")),
        task.get("description") or command or "",
    )
    quality_passed = status == "completed" and "[quality_gate] failed:" not in (output or "")
    estimated_tokens = estimate_text_tokens(task.get("description") or "") + estimate_text_tokens(output or "")
    if tool_calls:
        estimated_tokens += estimate_tool_calls_tokens(tool_calls)
    try:
        _record_benchmark_sample(
            provider=bench_provider,
            model=bench_model,
            task_kind=bench_task_kind,
            success=(status == "completed"),
            quality_gate_passed=quality_passed,
            latency_ms=execution_duration_ms,
            tokens_total=estimated_tokens,
        )
    except Exception as e:
        logging.warning(f"Benchmark ingestion failed for task {tid}: {e}")

    _update_local_task_status(tid, status, history=history)

    _log_terminal_entry(current_app.config["AGENT_NAME"], len(history), "out", command=command, task_id=tid)
    _log_terminal_entry(
        current_app.config["AGENT_NAME"], len(history), "in", output=output, exit_code=exit_code, task_id=tid
    )

    res = TaskStepExecuteResponse(output=output, exit_code=exit_code, task_id=tid, status=status)
    return api_response(data={**res.model_dump(), "trace": trace, "pipeline": {**pipeline, "trace_id": trace["trace_id"]}})
