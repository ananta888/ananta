import logging
import time
import json
import os
import concurrent.futures
from typing import Optional
from flask import Blueprint, current_app, g
from agent.common.errors import api_response
from agent.utils import validate_request, _extract_command, _extract_reason, _extract_tool_calls, _log_terminal_entry
from agent.llm_integration import _call_llm
from agent.common.sgpt import run_llm_cli_command, SUPPORTED_CLI_BACKENDS
from agent.auth import check_auth
from agent.repository import task_repo, role_repo, template_repo, team_member_repo
from agent.routes.tasks.utils import _update_local_task_status, _forward_to_worker
from agent.models import (
    TaskStepProposeRequest,
    TaskStepProposeResponse,
    TaskStepExecuteRequest,
    TaskStepExecuteResponse,
)
from agent.metrics import TASK_COMPLETED, TASK_FAILED, RETRIES_TOTAL
from agent.shell import get_shell
from agent.tools import registry as tool_registry
from agent.tool_guardrails import evaluate_tool_call_guardrails, estimate_text_tokens, estimate_tool_calls_tokens
from agent.common.api_envelope import unwrap_api_envelope

execution_bp = Blueprint("tasks_execution", __name__)


def _normalize_task_kind(task_kind: str | None, prompt: str) -> str:
    if task_kind:
        val = str(task_kind).strip().lower()
        if val in {"coding", "analysis", "doc", "ops"}:
            return val
    text = (prompt or "").lower()
    if any(k in text for k in ("refactor", "implement", "fix", "code", "test", "bug")):
        return "coding"
    if any(k in text for k in ("deploy", "docker", "restart", "kubernetes", "ops", "infrastructure")):
        return "ops"
    if any(k in text for k in ("readme", "documentation", "docs", "explain")):
        return "doc"
    return "analysis"


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


def _append_sample(target: dict, now: int, success: bool, quality_passed: bool, latency_ms: int, tokens_total: int) -> None:
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
    provider = str(provider or "").strip().lower()
    model = str(model or "").strip()
    if not provider or not model:
        return
    if task_kind not in {"coding", "analysis", "doc", "ops"}:
        task_kind = "analysis"

    path = _benchmarks_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            db = json.load(fh)
        if not isinstance(db, dict):
            db = {"models": {}, "updated_at": None}
    except Exception:
        db = {"models": {}, "updated_at": None}

    models = db.setdefault("models", {})
    key = f"{provider}:{model}"
    entry = models.setdefault(
        key,
        {"provider": provider, "model": model, "overall": _default_metric_bucket(), "task_kinds": {}},
    )
    task_kinds = entry.setdefault("task_kinds", {})
    bucket = task_kinds.setdefault(task_kind, _default_metric_bucket())
    now = int(time.time())

    def _apply(target: dict) -> None:
        target["total"] = int(target.get("total") or 0) + 1
        if success:
            target["success"] = int(target.get("success") or 0) + 1
        else:
            target["failed"] = int(target.get("failed") or 0) + 1
        if quality_gate_passed:
            target["quality_pass"] = int(target.get("quality_pass") or 0) + 1
        else:
            target["quality_fail"] = int(target.get("quality_fail") or 0) + 1
        target["latency_ms_total"] = int(target.get("latency_ms_total") or 0) + max(0, int(latency_ms or 0))
        target["tokens_total"] = int(target.get("tokens_total") or 0) + max(0, int(tokens_total or 0))
        target["last_seen"] = now
        _append_sample(target, now, success, quality_gate_passed, latency_ms, tokens_total)

    _apply(bucket)
    _apply(entry.setdefault("overall", _default_metric_bucket()))
    entry["provider"] = provider
    entry["model"] = model
    models[key] = entry
    db["models"] = models
    db["updated_at"] = now
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(db, fh, ensure_ascii=False, indent=2)


def _resolve_benchmark_identity(proposal_meta: dict | None, agent_cfg: dict | None) -> tuple[str, str]:
    proposal_meta = proposal_meta or {}
    agent_cfg = agent_cfg or {}
    routing = proposal_meta.get("routing") or {}
    llm_cfg = agent_cfg.get("llm_config") or {}
    precedence_cfg = agent_cfg.get("benchmark_identity_precedence") or {}
    provider_order = precedence_cfg.get("provider_order")
    model_order = precedence_cfg.get("model_order")
    allowed_provider_sources = {
        "proposal_backend",
        "routing_effective_backend",
        "llm_config_provider",
        "default_provider",
        "provider",
    }
    allowed_model_sources = {
        "proposal_model",
        "llm_config_model",
        "default_model",
        "model",
    }
    default_provider_order = [
        "proposal_backend",
        "routing_effective_backend",
        "llm_config_provider",
        "default_provider",
        "provider",
    ]
    default_model_order = [
        "proposal_model",
        "llm_config_model",
        "default_model",
        "model",
    ]
    provider_order_list = [
        str(x).strip().lower()
        for x in (provider_order if isinstance(provider_order, list) else default_provider_order)
        if str(x).strip().lower() in allowed_provider_sources
    ]
    model_order_list = [
        str(x).strip().lower()
        for x in (model_order if isinstance(model_order, list) else default_model_order)
        if str(x).strip().lower() in allowed_model_sources
    ]
    if not provider_order_list:
        provider_order_list = default_provider_order
    if not model_order_list:
        model_order_list = default_model_order

    provider_sources = {
        "proposal_backend": proposal_meta.get("backend"),
        "routing_effective_backend": routing.get("effective_backend"),
        "llm_config_provider": llm_cfg.get("provider"),
        "default_provider": agent_cfg.get("default_provider"),
        "provider": agent_cfg.get("provider"),
    }
    model_sources = {
        "proposal_model": proposal_meta.get("model"),
        "llm_config_model": llm_cfg.get("model"),
        "default_model": agent_cfg.get("default_model"),
        "model": agent_cfg.get("model"),
    }

    provider = ""
    for source_key in provider_order_list:
        val = str(provider_sources.get(source_key) or "").strip().lower()
        if val:
            provider = val
            break

    model = ""
    for source_key in model_order_list:
        val = str(model_sources.get(source_key) or "").strip()
        if val:
            model = val
            break

    return provider or "unknown", model or "unknown"


def _routing_config() -> dict:
    cfg = (current_app.config.get("AGENT_CONFIG", {}) or {}).get("sgpt_routing", {}) or {}
    return {
        "policy_version": str(cfg.get("policy_version") or "v2"),
        "default_backend": str(cfg.get("default_backend") or "sgpt").strip().lower(),
        "task_kind_backend": cfg.get("task_kind_backend") or {},
    }


def _resolve_cli_backend(task_kind: str, requested_backend: str = "auto") -> tuple[str, str]:
    backend = str(requested_backend or "auto").strip().lower()
    routing_cfg = _routing_config()
    if backend != "auto":
        return backend, f"explicit_backend:{backend}"

    kind_map = routing_cfg.get("task_kind_backend") or {}
    mapped = str(kind_map.get(task_kind) or "").strip().lower()
    if mapped in SUPPORTED_CLI_BACKENDS:
        return mapped, f"task_kind_policy:{task_kind}->{mapped}"

    configured = str(routing_cfg.get("default_backend") or "sgpt").strip().lower()
    if configured in SUPPORTED_CLI_BACKENDS:
        return configured, f"default_policy:{configured}"
    return "sgpt", "default_policy:sgpt"


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
            "estimated_total_tokens": estimate_text_tokens(data.command)
            + estimate_tool_calls_tokens(data.tool_calls),
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
                data={"blocked_tools": decision.blocked_tools, "blocked_reasons": decision.reasons, "guardrails": decision.details},
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
        with concurrent.futures.ThreadPoolExecutor() as executor:
            futures = {
                executor.submit(
                    _call_llm,
                    provider=p.split(":")[0],
                    model=p.split(":")[1] if ":" in p else cfg.get("model", "llama3"),
                    prompt=prompt,
                    urls=current_app.config["PROVIDER_URLS"],
                    api_key=current_app.config["OPENAI_API_KEY"],
                    history=task.get("history", []),
                ): p
                for p in data.providers
            }
            results = {}
            for future in concurrent.futures.as_completed(futures):
                p_name = futures[future]
                try:
                    res = future.result()
                    if res:
                        results[p_name] = {
                            "reason": _extract_reason(res),
                            "command": _extract_command(res),
                            "tool_calls": _extract_tool_calls(res),
                            "raw": res,
                        }
                except Exception as e:
                    logging.error(f"Multi-Provider Call for {p_name} failed: {e}")

            if not results:
                return api_response(status="error", message="all_llm_failed", code=502)

            best_p = list(results.keys())[0]
            main_res = results[best_p]

            proposal = {"reason": main_res["reason"]}
            if main_res["command"] and main_res["command"] != main_res["raw"].strip():
                proposal["command"] = main_res["command"]
            if main_res["tool_calls"]:
                proposal["tool_calls"] = main_res["tool_calls"]
            proposal["comparisons"] = results

            _update_local_task_status(tid, "proposing", last_proposal=proposal)

            return api_response(
                data={
                    "status": "proposing",
                    "reason": main_res["reason"],
                    "command": main_res["command"] if main_res["command"] != main_res["raw"].strip() else None,
                    "tool_calls": main_res["tool_calls"],
                    "raw": main_res["raw"],
                    "comparisons": results,
                }
            )

    task_kind = _normalize_task_kind(None, base_prompt)
    effective_backend, routing_reason = _resolve_cli_backend(task_kind, requested_backend="auto")
    timeout = int((current_app.config.get("AGENT_CONFIG", {}) or {}).get("command_timeout", 60) or 60)
    started_at = time.time()
    rc, cli_out, cli_err, backend_used = run_llm_cli_command(
        prompt=prompt,
        options=["--no-interaction"],
        timeout=timeout,
        backend=effective_backend,
        model=data.model,
        routing_policy={"mode": "adaptive", "task_kind": task_kind, "policy_version": _routing_config()["policy_version"]},
    )
    latency_ms = int((time.time() - started_at) * 1000)
    raw_res = cli_out or ""
    if rc != 0 and not raw_res.strip():
        return api_response(
            status="error",
            message="llm_cli_failed",
            data={"details": cli_err or f"backend '{backend_used}' failed with exit code {rc}", "backend": backend_used},
            code=502,
        )

    if not raw_res:
        return api_response(status="error", message="llm_failed", code=502)

    reason = _extract_reason(raw_res)
    command = _extract_command(raw_res)
    tool_calls = _extract_tool_calls(raw_res)

    routing = {"task_kind": task_kind, "effective_backend": effective_backend, "reason": routing_reason}
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
        command = proposal.get("command")
        tool_calls = proposal.get("tool_calls")
        reason = proposal.get("reason", "Vorschlag ausgeführt")

    exec_started_at = time.time()
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
        if not decision.allowed:
            _append_guardrail_block_history(tid, task, command, tool_calls, decision)
            return api_response(
                status="error",
                message="tool_guardrail_blocked",
                data={"blocked_tools": decision.blocked_tools, "blocked_reasons": decision.reasons, "guardrails": decision.details},
                code=400,
            )
        for tc in tool_calls:
            name = tc.get("name")
            args = tc.get("args", {})
            current_app.logger.info(f"Task {tid} führt Tool aus: {name} mit {args}")
            tool_res = tool_registry.execute(name, args)

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

    output = "\n---\n".join(output_parts)
    exit_code = overall_exit_code
    execution_duration_ms = int((time.time() - exec_started_at) * 1000)
    retries_used = max(0, int((data.retries or 0) - retries_left)) if command else 0

    history = task.get("history", [])
    proposal_meta = task.get("last_proposal", {}) or {}
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
    bench_task_kind = _normalize_task_kind(
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
    return api_response(data=res.model_dump())
