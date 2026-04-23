from __future__ import annotations

import json
import re
import uuid

from flask import Blueprint, Response, current_app, g, has_request_context, request

from agent.auth import check_auth
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.llm_integration import _default_model_for_provider, resolve_preferred_local_runtime
from agent.local_llm_backends import resolve_local_openai_backend
from agent.governance_modes import resolve_governance_mode
from agent.runtime_policy import normalize_task_kind
from agent.services.hub_llm_service import generate_text
from agent.services.routing_decision_service import get_routing_decision_service
from agent.services.tool_routing_service import get_tool_routing_service
from agent.tool_capabilities import (
    build_capability_contract,
    describe_capabilities,
    resolve_allowed_tools,
    validate_tool_calls_against_contract,
)
from agent.tool_guardrails import estimate_text_tokens, estimate_tool_calls_tokens, evaluate_tool_call_guardrails
from agent.tools import registry as tool_registry
from agent.utils import log_llm_entry, rate_limit

from . import shared
from .llm_support import build_sse_response, build_system_instruction, extract_json

llm_generate_bp = Blueprint("config_llm_generate", __name__)


def _preflight_with_meta(payload: dict, raw_payload: dict | None = None) -> dict:
    raw_payload = raw_payload if isinstance(raw_payload, dict) else {}
    request_cfg = raw_payload.get("config") if isinstance(raw_payload.get("config"), dict) else {}
    return {
        **payload,
        "routing": {
            "policy_version": "llm-generate-v1",
            "requested": {
                "provider": str(request_cfg.get("provider") or "").strip() or None,
                "model": str(request_cfg.get("model") or "").strip() or None,
                "base_url": str(request_cfg.get("base_url") or "").strip() or None,
            },
            "effective": {"provider": None, "model": None, "base_url": None},
            "fallback": {
                "provider_source": "preflight_validation",
                "model_source": "preflight_validation",
                "base_url_source": "preflight_validation",
            },
        },
    }

def _infer_tool_calls_from_prompt(prompt: str, context: dict | None = None) -> list[dict]:
    p = (prompt or "").strip().lower()
    if not p:
        return []
    wants_templates = any(token in p for token in ["template", "templates", "vorlage", "vorlagen"])
    wants_role_links = any(
        token in p
        for token in ["rolle verkn", "rollen verkn", "role link", "role links", "rollen zuordnen", "roles zuordnen"]
    )
    team_types: list[str] = []
    if "scrum" in p:
        team_types.append("Scrum")
    if "kanban" in p:
        team_types.append("Kanban")
    if wants_templates or wants_role_links:
        return [{"name": "ensure_team_templates", "args": {"team_types": team_types}}] if team_types else []

    wants_create_team = any(token in p for token in ["team erstellen", "team anlegen", "create team", "neues team", "new team"])
    if wants_create_team:
        inferred_type = "Scrum" if "scrum" in p else "Kanban" if "kanban" in p else ""
        if not inferred_type:
            return []
        team_name = ""
        quoted = re.search(r"['\"]([^'\"]{2,80})['\"]", prompt or "")
        if quoted:
            team_name = quoted.group(1).strip()
        if not team_name:
            match = re.search(r"(?:team(?:name)?\s*[:=]\s*)([a-zA-Z0-9 _-]{2,80})", prompt or "", flags=re.IGNORECASE)
            if match:
                team_name = match.group(1).strip(" .,:;")
        if not team_name:
            return []
        return [{"name": "create_team", "args": {"name": team_name, "team_type": inferred_type}}]

    wants_assign_role = any(token in p for token in ["rolle zuweisen", "assign role", "agent zuordnen", "agent zuweisen", "mitglied zuordnen"])
    if not wants_assign_role:
        return []
    team_id_match = re.search(r"team_id\s*[:=]\s*([a-zA-Z0-9._:-]+)", prompt or "", flags=re.IGNORECASE)
    role_id_match = re.search(r"role_id\s*[:=]\s*([a-zA-Z0-9._:-]+)", prompt or "", flags=re.IGNORECASE)
    agent_url_match = re.search(r"agent_url\s*[:=]\s*(https?://\S+)", prompt or "", flags=re.IGNORECASE)
    if not (team_id_match and role_id_match and agent_url_match):
        return []
    return [
        {
            "name": "assign_role",
            "args": {
                "team_id": team_id_match.group(1).strip(),
                "role_id": role_id_match.group(1).strip(),
                "agent_url": agent_url_match.group(1).strip().rstrip(".,;"),
            },
        }
    ]


def _resolve_request_runtime(data: dict, user_prompt: str) -> dict:
    agent_cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
    llm_cfg = agent_cfg.get("llm_config", {}) if isinstance(agent_cfg.get("llm_config"), dict) else {}
    provider_urls = current_app.config.get("PROVIDER_URLS", {})
    cfg = data.get("config") or {}
    requested_provider = str(cfg.get("provider") or "").strip()
    requested_model = str(cfg.get("model") or "").strip()
    requested_base_url = str(cfg.get("base_url") or "").strip()
    inferred_task_kind = normalize_task_kind(data.get("task_kind"), user_prompt or "")
    provider = cfg.get("provider") or llm_cfg.get("provider") or agent_cfg.get("default_provider")
    model = cfg.get("model") or llm_cfg.get("model") or agent_cfg.get("default_model")
    api_key = cfg.get("api_key") or llm_cfg.get("api_key")
    timeout_val = cfg.get("timeout")
    temperature_val = cfg.get("temperature") if cfg.get("temperature") is not None else llm_cfg.get("temperature")
    context_limit_val = cfg.get("context_limit") if cfg.get("context_limit") is not None else llm_cfg.get("context_limit")

    try:
        temperature_val = float(temperature_val) if temperature_val is not None else None
    except (TypeError, ValueError):
        temperature_val = None
    if temperature_val is not None:
        temperature_val = max(0.0, min(2.0, temperature_val))

    try:
        context_limit_val = int(context_limit_val) if context_limit_val is not None else None
    except (TypeError, ValueError):
        context_limit_val = None
    if context_limit_val is not None:
        context_limit_val = max(256, min(200000, context_limit_val))

    provider_source = "request.config.provider" if cfg.get("provider") else ("agent_config.llm_config.provider" if llm_cfg.get("provider") else "agent_config.default_provider")
    model_source = "request.config.model" if cfg.get("model") else ("agent_config.llm_config.model" if llm_cfg.get("model") else "agent_config.default_model")
    api_key_profile = cfg.get("api_key_profile") or llm_cfg.get("api_key_profile")
    base_url, base_url_source = shared.resolve_provider_base_url(
        provider=provider,
        requested_base_url=cfg.get("base_url"),
        llm_cfg=llm_cfg,
        agent_cfg=agent_cfg,
        provider_urls=provider_urls,
    )
    api_key = shared.resolve_provider_api_key(
        provider=provider,
        explicit_api_key=api_key,
        api_key_profile=api_key_profile,
        agent_cfg=agent_cfg,
    )

    recommendation = None
    if not requested_provider and not requested_model:
        recommendation = shared.recommend_runtime_selection(
            task_kind=inferred_task_kind,
            current_provider=str(provider or "").strip().lower() or None,
            current_model=str(model or "").strip() or None,
            agent_cfg=agent_cfg,
            provider_urls=provider_urls,
        )
        if recommendation:
            provider = recommendation["provider"]
            model = recommendation["model"]
            provider_source = recommendation["selection_source"]
            model_source = recommendation["selection_source"]
            base_url, base_url_source = shared.resolve_provider_base_url(provider, cfg.get("base_url"), llm_cfg, agent_cfg, provider_urls)
            api_key = shared.resolve_provider_api_key(provider, api_key, api_key_profile, agent_cfg)

    runtime_choice = None
    runtime_probe_timeout = 5
    try:
        if timeout_val is not None:
            runtime_probe_timeout = max(1, int(timeout_val))
    except (TypeError, ValueError):
        runtime_probe_timeout = 5
    if not requested_provider and not requested_base_url and str(provider or "").strip().lower() in {"lmstudio", "ollama"}:
        runtime_choice = resolve_preferred_local_runtime(
            provider=str(provider or "").strip().lower(),
            provider_urls=provider_urls,
            timeout=runtime_probe_timeout,
        )
        selected_provider = str(runtime_choice.get("provider") or provider).strip().lower()
        if selected_provider and selected_provider != str(provider or "").strip().lower():
            provider = selected_provider
            provider_source = runtime_choice.get("selection_source") or provider_source
            base_url, base_url_source = shared.resolve_provider_base_url(provider, None, {}, agent_cfg, provider_urls)
            if not requested_model:
                model = _default_model_for_provider(provider, model) or model
                model_source = provider_source
            api_key = shared.resolve_provider_api_key(provider, None, api_key_profile, agent_cfg)

    local_backend = resolve_local_openai_backend(
        provider,
        agent_cfg=agent_cfg,
        provider_urls=provider_urls,
        default_provider=str(agent_cfg.get("default_provider") or ""),
        default_model=str(agent_cfg.get("default_model") or ""),
    )
    transport_provider = str(local_backend.get("transport_provider") or "openai") if local_backend else provider
    routing = {
        "policy_version": "llm-generate-v1",
        "task_kind": inferred_task_kind,
        "requested": {"provider": requested_provider or None, "model": requested_model or None, "base_url": requested_base_url or None},
        "effective": {
            "provider": str(provider or "").strip() or None,
            "transport_provider": str(transport_provider or "").strip() or None,
            "model": str(model or "").strip() or None,
            "base_url": str(base_url or "").strip() or None,
        },
        "fallback": {"provider_source": provider_source, "model_source": model_source, "base_url_source": base_url_source},
    }
    if recommendation:
        routing["recommendation"] = recommendation
    if runtime_choice:
        routing["runtime_selection"] = runtime_choice
    routing["decision_chain"] = get_routing_decision_service().build_decision_chain(
        cfg=agent_cfg,
        task_kind=inferred_task_kind,
        requested=routing["requested"],
        effective=routing["effective"],
        sources={
            "provider_source": provider_source,
            "model_source": model_source,
            "base_url_source": base_url_source,
        },
        recommendation=recommendation,
        runtime_selection=runtime_choice,
    )
    routing["fallback_policy"] = routing["decision_chain"]["fallback_policy"]
    routing["tool_router"] = get_tool_routing_service().route_execution_backend(
        task_kind=inferred_task_kind,
        requested_backend=str(agent_cfg.get("sgpt_execution_backend") or "").strip().lower() or None,
        required_capabilities=[],
        governance_mode=resolve_governance_mode(agent_cfg),
        agent_cfg=agent_cfg,
    )
    return {
        "agent_cfg": agent_cfg,
        "llm_cfg": llm_cfg,
        "provider": provider,
        "model": model,
        "base_url": base_url,
        "api_key": api_key,
        "timeout_val": timeout_val,
        "temperature_val": temperature_val,
        "context_limit_val": context_limit_val,
        "routing": routing,
        "transport_provider": transport_provider,
    }


def _normalize_llm_response(response_text: str, *, full_history: list[dict], user_prompt: str, context, runtime: dict) -> tuple[dict | None, str]:
    res_json = extract_json(response_text)
    if res_json is not None:
        return res_json, response_text
    inferred_tool_calls = _infer_tool_calls_from_prompt(user_prompt, context=context if isinstance(context, dict) else None)
    if inferred_tool_calls:
        return {"answer": "Ich habe passende Admin-Aktionen vorbereitet. Bitte bestaetigen.", "tool_calls": inferred_tool_calls, "thought": "Intent fallback"}, response_text
    if response_text and response_text.strip():
        return {"answer": response_text.strip(), "tool_calls": [], "thought": ""}, response_text

    repair_prompt = (
        f"Assistant (invalid JSON): {response_text}\n\n"
        "System: Antworte AUSSCHLIESSLICH mit gueltigem JSON im oben beschriebenen Format. "
        "Beginne mit '{' und ende mit '}'. Kein Freitext, keine Markdown-Bloecke, kein Prefix wie 'Assistant:'."
    )
    repaired = generate_text(
        prompt=repair_prompt,
        provider=runtime["transport_provider"],
        model=runtime["model"],
        base_url=runtime["base_url"],
        api_key=runtime["api_key"],
        history=full_history,
        temperature=runtime["temperature_val"],
        max_context_tokens=runtime["context_limit_val"],
        timeout=runtime["timeout_val"],
    )
    if not repaired or not repaired.strip():
        return None, repaired
    repaired_json = extract_json(repaired)
    if repaired_json is None:
        inferred_tool_calls = _infer_tool_calls_from_prompt(user_prompt, context=context if isinstance(context, dict) else None)
        if inferred_tool_calls:
            repaired_json = {"answer": "Ich habe passende Admin-Aktionen vorbereitet. Bitte bestaetigen.", "tool_calls": inferred_tool_calls, "thought": "Intent fallback"}
    if repaired_json is None and repaired:
        repaired_json = {"answer": repaired.strip(), "tool_calls": [], "thought": ""}
    return repaired_json, repaired


def _execute_tool_calls(*, tool_calls: list[dict], user_prompt: str, full_history: list[dict], runtime: dict, response_text: str, res_json: dict, is_admin: bool):
    if not is_admin:
        return api_response(status="error", message="forbidden", data={"details": "Admin privileges required"}, code=403)
    capability_contract = build_capability_contract(runtime["agent_cfg"])
    allowed_tools = resolve_allowed_tools(runtime["agent_cfg"], is_admin=is_admin, contract=capability_contract)
    blocked_tools, blocked_reasons_by_tool = validate_tool_calls_against_contract(
        tool_calls, allowed_tools=allowed_tools, contract=capability_contract, is_admin=is_admin
    )
    if blocked_tools:
        log_audit("tool_calls_blocked", {"tools": blocked_tools, "reasons_by_tool": blocked_reasons_by_tool})
        blocked_results = [{"tool": name, "success": False, "output": None, "error": blocked_reasons_by_tool.get(name, "tool_not_allowed")} for name in blocked_tools]
        return api_response(data={"response": f"Tool calls blocked: {', '.join(blocked_tools)}", "tool_results": blocked_results, "blocked_tools": blocked_tools, "blocked_reasons_by_tool": blocked_reasons_by_tool})

    token_usage = {
        "prompt_tokens": estimate_text_tokens(user_prompt),
        "history_tokens": estimate_text_tokens(json.dumps(full_history, ensure_ascii=False)),
        "completion_tokens": estimate_text_tokens(response_text or json.dumps(res_json or {}, ensure_ascii=False)),
        "tool_calls_tokens": estimate_tool_calls_tokens(tool_calls),
    }
    provider_usage = getattr(g, "llm_last_usage", {}) if has_request_context() else {}
    if isinstance(provider_usage, dict) and provider_usage:
        if provider_usage.get("prompt_tokens") is not None:
            token_usage["prompt_tokens"] = int(provider_usage.get("prompt_tokens") or 0)
        if provider_usage.get("completion_tokens") is not None:
            token_usage["completion_tokens"] = int(provider_usage.get("completion_tokens") or 0)
        if provider_usage.get("total_tokens") is not None:
            token_usage["estimated_total_tokens"] = int(provider_usage.get("total_tokens") or 0)
        token_usage["provider_usage"] = {
            "prompt_tokens": int(provider_usage.get("prompt_tokens") or 0),
            "completion_tokens": int(provider_usage.get("completion_tokens") or 0),
            "total_tokens": int(provider_usage.get("total_tokens") or 0),
        }
        token_usage["token_source"] = "provider_usage"
    else:
        token_usage["token_source"] = "estimated"
    if token_usage.get("estimated_total_tokens") is None:
        token_usage["estimated_total_tokens"] = int(token_usage.get("prompt_tokens") or 0) + int(token_usage.get("history_tokens") or 0) + int(token_usage.get("completion_tokens") or 0) + int(token_usage.get("tool_calls_tokens") or 0)
    guardrail_decision = evaluate_tool_call_guardrails(tool_calls, runtime["agent_cfg"], token_usage=token_usage)
    if not guardrail_decision.allowed:
        blocked_results = [{"tool": name, "success": False, "output": None, "error": "tool_guardrail_blocked"} for name in (guardrail_decision.blocked_tools or [tc.get("name", "<missing>") for tc in tool_calls])]
        log_audit("tool_calls_guardrail_blocked", {"tools": guardrail_decision.blocked_tools, "reasons": guardrail_decision.reasons, **guardrail_decision.details})
        return api_response(data={"response": "Tool calls blocked by guardrails.", "tool_results": blocked_results, "blocked_tools": guardrail_decision.blocked_tools, "blocked_reasons": guardrail_decision.reasons, "guardrails": guardrail_decision.details})

    results = []
    for tool_call in tool_calls:
        name = tool_call.get("name")
        args = tool_call.get("args", {})
        current_app.logger.info(f"KI ruft Tool auf: {name} mit {args}")
        tool_res = tool_registry.execute(name, args)
        results.append({"tool": name, "success": tool_res.success, "output": tool_res.output, "error": tool_res.error})
    final_response = generate_text(
        prompt="Bitte gib eine finale Antwort an den Nutzer basierend auf diesen Ergebnissen.",
        provider=runtime["transport_provider"],
        model=runtime["model"],
        base_url=runtime["base_url"],
        api_key=runtime["api_key"],
        history=full_history + [{"role": "user", "content": user_prompt}, {"role": "assistant", "content": json.dumps({"tool_calls": tool_calls})}, {"role": "system", "content": f"Tool Results: {json.dumps(results)}"}],
        temperature=runtime["temperature_val"],
        max_context_tokens=runtime["context_limit_val"],
        timeout=runtime["timeout_val"],
    )
    if not final_response or not final_response.strip():
        return api_response(status="error", message="llm_failed", data={"details": "LLM returned empty response"}, code=502)
    return build_sse_response(final_response) if runtime["stream"] else api_response(data={"response": final_response, "tool_results": results})


@llm_generate_bp.route("/llm/generate", methods=["POST"])
@check_auth
@rate_limit(limit=30, window=60, namespace="config_llm_generate")
def llm_generate():
    request_id = str(uuid.uuid4())
    g.llm_request_id = request_id

    def _log(event: str, **kwargs):
        try:
            log_llm_entry(event=event, request_id=request_id, **kwargs)
        except Exception:
            pass

    raw_data = request.get_json()
    data = {} if raw_data is None else raw_data
    if not isinstance(data, dict):
        _log("llm_error", error="invalid_json")
        return api_response(status="error", message="invalid_json", data=_preflight_with_meta({}, raw_data), code=400)

    user_prompt = data.get("prompt") or ""
    tool_calls_input = data.get("tool_calls")
    confirm_tool_calls = bool(data.get("confirm_tool_calls") or data.get("confirmed"))
    stream = bool(data.get("stream"))
    if not user_prompt and not tool_calls_input:
        _log("llm_error", error="missing_prompt")
        return api_response(status="error", message="missing_prompt", data=_preflight_with_meta({}, data), code=400)

    runtime = _resolve_request_runtime(data, user_prompt)
    runtime["stream"] = stream
    is_admin = bool(getattr(g, "is_admin", False))
    denylist_cfg = runtime["agent_cfg"].get("llm_tool_denylist", [])
    capability_contract = build_capability_contract(runtime["agent_cfg"])
    allowed_tools = resolve_allowed_tools(runtime["agent_cfg"], is_admin=is_admin, contract=capability_contract)
    capability_meta = describe_capabilities(capability_contract, allowed_tools=allowed_tools, is_admin=is_admin)
    tools_desc = json.dumps(
        tool_registry.get_tool_definitions(allowlist=allowed_tools, denylist=denylist_cfg),
        indent=2,
        ensure_ascii=False,
    )
    full_history = [{"role": "system", "content": build_system_instruction(tools_desc=tools_desc, context=data.get("context"), stream=stream)}] + (data.get("history") if isinstance(data.get("history"), list) else [])

    def _with_meta(payload: dict) -> dict:
        return {**payload, "routing": runtime["routing"], "assistant_capabilities": capability_meta}

    if not runtime["provider"]:
        _log("llm_error", error="llm_not_configured", reason="missing_provider")
        return api_response(status="error", message="llm_not_configured", data=_with_meta({"details": "LLM provider is not configured"}), code=400)
    if runtime["transport_provider"] in {"openai", "codex", "anthropic"} and not runtime["api_key"]:
        _log("llm_error", error="llm_api_key_missing", provider=runtime["provider"])
        return api_response(status="error", message="llm_api_key_missing", data=_with_meta({"details": f"API key missing for {runtime['provider']}"}), code=400)
    if not runtime["base_url"]:
        _log("llm_error", error="llm_base_url_missing", provider=runtime["provider"])
        return api_response(status="error", message="llm_base_url_missing", data=_with_meta({"details": f"Base URL missing for {runtime['provider']}"}), code=400)

    _log(
        "llm_request",
        prompt=user_prompt,
        stream=stream,
        confirm_tool_calls=confirm_tool_calls,
        tool_calls_input=tool_calls_input,
        history_len=len(full_history) - 1,
        provider=runtime["provider"],
        transport_provider=runtime["transport_provider"],
        model=runtime["model"],
        base_url=runtime["base_url"],
        is_admin=is_admin,
    )

    response_text = ""
    if tool_calls_input and confirm_tool_calls:
        if not isinstance(tool_calls_input, list):
            _log("llm_error", error="invalid_tool_calls")
            return api_response(status="error", message="invalid_tool_calls", code=400)
        tool_calls = tool_calls_input
        res_json = {"answer": ""}
    else:
        response_text = generate_text(
            prompt=user_prompt,
            provider=runtime["transport_provider"],
            model=runtime["model"],
            base_url=runtime["base_url"],
            api_key=runtime["api_key"],
            history=full_history,
            temperature=runtime["temperature_val"],
            max_context_tokens=runtime["context_limit_val"],
            timeout=runtime["timeout_val"],
        )
        if not response_text or not response_text.strip():
            _log("llm_error", error="llm_empty_response")
            return api_response(data=_with_meta({"response": "LLM returned empty response. Please try again."}), status="ok")
        if stream:
            _log("llm_response", response=response_text, tool_calls=[], status="stream")
            return build_sse_response(response_text)
        res_json, response_text = _normalize_llm_response(response_text, full_history=full_history, user_prompt=user_prompt, context=data.get("context"), runtime=runtime)
        if res_json is None:
            _log("llm_response", response=response_text, tool_calls=[], status="no_json")
            return api_response(data=_with_meta({"response": response_text}))
        tool_calls = res_json.get("tool_calls", [])
        if not tool_calls:
            inferred_tool_calls = _infer_tool_calls_from_prompt(user_prompt, context=data.get("context") if isinstance(data.get("context"), dict) else None)
            if inferred_tool_calls:
                tool_calls = inferred_tool_calls
                res_json["tool_calls"] = inferred_tool_calls
                if not res_json.get("answer"):
                    res_json["answer"] = "Ich habe passende Admin-Aktionen vorbereitet. Bitte bestaetigen."
        if tool_calls and not confirm_tool_calls:
            if not is_admin:
                _log("llm_blocked", tool_calls=tool_calls, reason="admin_required")
                return api_response(data=_with_meta({"response": res_json.get("answer") or "Tool calls require admin privileges.", "tool_calls": tool_calls, "blocked": True}))
            _log("llm_requires_confirmation", tool_calls=tool_calls)
            return api_response(data=_with_meta({"response": res_json.get("answer"), "requires_confirmation": True, "thought": res_json.get("thought"), "tool_calls": tool_calls}))

    if tool_calls_input and confirm_tool_calls and not tool_calls:
        _log("llm_no_tool_calls")
        return api_response(data=_with_meta({"response": "No tool calls to execute."}))
    if tool_calls and not confirm_tool_calls:
        _log("llm_requires_confirmation", tool_calls=tool_calls)
        return api_response(data=_with_meta({"response": "Pending actions require confirmation.", "requires_confirmation": True, "tool_calls": tool_calls}))
    if tool_calls:
        result = _execute_tool_calls(tool_calls=tool_calls, user_prompt=user_prompt, full_history=full_history, runtime=runtime, response_text=response_text, res_json=res_json, is_admin=is_admin)
        if isinstance(result, Response):
            _log("llm_response", response="stream", tool_calls=tool_calls, status="tool_results")
            return result
        payload = result.get_json(silent=True) if hasattr(result, "get_json") else None
        if payload and isinstance(payload, dict) and "data" in payload:
            payload["data"] = _with_meta(payload.get("data") or {})
            result.set_data(json.dumps(payload))
            result.mimetype = "application/json"
        return result

    final_text = res_json.get("answer", response_text)
    _log("llm_response", response=final_text, tool_calls=tool_calls, status="ok")
    return api_response(data=_with_meta({"response": final_text}))
