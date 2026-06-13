import logging
import time
import uuid
from typing import Any, Optional
from urllib.parse import urlsplit, urlunsplit

from flask import current_app, g, has_app_context, has_request_context, request

from agent.common.errors import PermanentError
from agent.config import settings
from agent.llm_strategies import get_strategy
from agent.metrics import LLM_CALL_DURATION, RETRIES_TOTAL
from agent.utils import _http_get, log_llm_entry, read_json, update_json, write_json, get_data_dir

from agent.llm_integration_lmstudio import (
    _sha256_text,
    _model_identifier_tokens,
    _model_identifier_matches,
    _find_matching_lmstudio_candidate,
    _load_lmstudio_history,
    _save_lmstudio_history,
    _touch_lmstudio_models,
    _record_lmstudio_result,
    _update_lmstudio_history,
    _prepare_lmstudio_history,
    _select_best_lmstudio_model,
    _normalize_lmstudio_base_url,
    _lmstudio_models_url,
    _resolve_lmstudio_model,
    _extract_lmstudio_candidates,
    _list_lmstudio_candidates,
    probe_lmstudio_runtime,
    _extract_lmstudio_text,
    _extract_lmstudio_usage,
    _LMSTUDIO_HISTORY_FILE,
)
from agent.llm_integration_ollama import (
    _find_matching_ollama_candidate,
    _normalize_ollama_base_url,
    _ollama_tags_url,
    _ollama_ps_url,
    resolve_ollama_model,
    probe_ollama_runtime,
    probe_ollama_activity,
)
from agent.llm_resilience import (
    CIRCUIT_BREAKER,
    _CB_DEFAULT_THRESHOLD,
    _CB_DEFAULT_RECOVERY_TIME,
    _RATE_LIMIT_WINDOW,
    _RATE_LIMIT_LOCK,
    _ERR_RATE_LOCK,
    _ERR_SUCCESS_WINDOW,
    _ERR_FAILURE_WINDOW,
    _cb_config,
    _check_circuit_breaker,
    _report_llm_failure,
    _report_llm_success,
    _record_llm_failure_rate,
    _rl_config,
    _check_rate_limit,
    get_provider_error_rate,
    get_rate_limit_state,
    get_circuit_breaker_state,
)

HTTP_TIMEOUT = getattr(settings, "http_timeout", 120)

_LOCAL_RUNTIME_SELECTION_CACHE: dict[str, dict[str, Any]] = {}
_LOCAL_RUNTIME_SELECTION_CACHE_TTL_SECONDS = 30
_LOCAL_RUNTIME_PROBE_TIMEOUT_SECONDS = 2


def _runtime_default_provider() -> str:
    if has_app_context():
        cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
        provider = cfg.get("default_provider")
        if provider:
            return str(provider)
    return str(settings.default_provider)


def _runtime_default_model() -> str:
    if has_app_context():
        cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
        model = cfg.get("default_model")
        if model:
            return str(model)
    return str(settings.default_model)


def _runtime_provider_urls() -> dict[str, str | None]:
    if has_app_context():
        urls = current_app.config.get("PROVIDER_URLS", {}) or {}
        if urls:
            return {
                "ollama": urls.get("ollama"),
                "lmstudio": urls.get("lmstudio"),
                "openai": urls.get("openai"),
                "codex": urls.get("codex") or urls.get("openai"),
                "anthropic": urls.get("anthropic"),
                "mock": getattr(settings, "mock_url", None),
            }
    return {
        "ollama": settings.ollama_url,
        "lmstudio": settings.lmstudio_url,
        "openai": settings.openai_url,
        "codex": settings.openai_url,
        "anthropic": settings.anthropic_url,
        "mock": settings.mock_url,
    }


def _runtime_api_key(provider: str | None) -> str | None:
    provider_name = str(provider or "").strip().lower()
    if provider_name in {"openai", "codex"}:
        if has_app_context() and current_app.config.get("OPENAI_API_KEY"):
            return current_app.config.get("OPENAI_API_KEY")
        return settings.openai_api_key
    if provider_name == "anthropic":
        if has_app_context() and current_app.config.get("ANTHROPIC_API_KEY"):
            return current_app.config.get("ANTHROPIC_API_KEY")
        return settings.anthropic_api_key
    return None


def _normalize_llm_usage(usage: Any) -> dict[str, int]:
    if not isinstance(usage, dict):
        return {}
    prompt_tokens = usage.get("prompt_tokens", usage.get("input_tokens", usage.get("prompt_eval_count", 0)))
    completion_tokens = usage.get("completion_tokens", usage.get("output_tokens", usage.get("eval_count", 0)))
    total_tokens = usage.get("total_tokens")
    try:
        p = max(0, int(prompt_tokens or 0))
        c = max(0, int(completion_tokens or 0))
        if total_tokens is None:
            t = p + c
        else:
            t = max(0, int(total_tokens or 0))
        return {"prompt_tokens": p, "completion_tokens": c, "total_tokens": t}
    except Exception:
        return {}


LLM_CALL_PROFILE_FIELDS: tuple[str, ...] = (
    "name",
    "backend",
    "provider",
    "model",
    "success",
    "latency_ms",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "source",
    "estimated",
    "error_type",
    "error_message",
    "started_at",
    "ended_at",
)


def build_llm_call_profile_entry(
    *,
    name: str,
    backend: str,
    provider: str | None,
    model: str | None,
    success: bool,
    started_at: float | None,
    ended_at: float | None,
    usage: dict[str, Any] | None = None,
    source: str = "llm_integration",
    estimated: bool = False,
    error_type: str | None = None,
    error_message: str | None = None,
) -> dict[str, Any]:
    usage = usage if isinstance(usage, dict) else {}
    prompt_tokens = usage.get("prompt_tokens")
    completion_tokens = usage.get("completion_tokens")
    total_tokens = usage.get("total_tokens")
    latency_ms = None
    if started_at is not None and ended_at is not None:
        latency_ms = max(0, int((ended_at - started_at) * 1000))
    return {
        "name": str(name or "").strip() or "generate_text",
        "backend": str(backend or "").strip() or "llm_integration",
        "provider": str(provider or "").strip() or None,
        "model": str(model or "").strip() or None,
        "success": bool(success),
        "latency_ms": latency_ms,
        "prompt_tokens": int(prompt_tokens) if isinstance(prompt_tokens, int) else None,
        "completion_tokens": int(completion_tokens) if isinstance(completion_tokens, int) else None,
        "total_tokens": int(total_tokens) if isinstance(total_tokens, int) else None,
        "source": str(source or "").strip() or "llm_integration",
        "estimated": bool(estimated),
        "error_type": str(error_type or "").strip() or None,
        "error_message": str(error_message or "").strip() or None,
        "started_at": float(started_at) if started_at is not None else None,
        "ended_at": float(ended_at) if ended_at is not None else None,
    }


def normalize_llm_call_profile_entry(entry: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(entry, dict):
        return build_llm_call_profile_entry(
            name="unknown",
            backend="unknown",
            provider=None,
            model=None,
            success=False,
            started_at=None,
            ended_at=None,
            source="normalized",
            estimated=True,
        )
    name = str(entry.get("name") or entry.get("phase") or "").strip() or "unknown"
    latency_raw = entry.get("latency_ms")
    try:
        latency_ms: int | None = max(0, int(latency_raw)) if latency_raw is not None else None
    except (TypeError, ValueError):
        latency_ms = None

    def _int_or_none(v: Any) -> int | None:
        try:
            return max(0, int(v)) if v is not None else None
        except (TypeError, ValueError):
            return None

    started_at = entry.get("started_at")
    ended_at = entry.get("ended_at")
    if latency_ms is None and started_at is not None and ended_at is not None:
        try:
            latency_ms = max(0, int((float(ended_at) - float(started_at)) * 1000))
        except (TypeError, ValueError):
            pass
    return {
        "name": name,
        "backend": str(entry.get("backend") or "").strip() or "unknown",
        "provider": str(entry.get("provider") or "").strip() or None,
        "model": str(entry.get("model") or "").strip() or None,
        "success": bool(entry.get("success", False)),
        "latency_ms": latency_ms,
        "prompt_tokens": _int_or_none(entry.get("prompt_tokens")),
        "completion_tokens": _int_or_none(entry.get("completion_tokens")),
        "total_tokens": _int_or_none(entry.get("total_tokens")),
        "source": str(entry.get("source") or "").strip() or "unknown",
        "estimated": bool(entry.get("estimated", False)),
        "error_type": str(entry.get("error_type") or "").strip() or None,
        "error_message": str(entry.get("error_message") or "").strip() or None,
        "started_at": float(started_at) if started_at is not None else None,
        "ended_at": float(ended_at) if ended_at is not None else None,
    }


_build_llm_call_profile_entry = build_llm_call_profile_entry


def _attach_llm_call_profile(result: Any, entry: dict[str, Any]) -> Any:
    if not isinstance(entry, dict):
        return result
    if isinstance(result, dict):
        meta = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
        meta["llm_call_profile"] = list(meta.get("llm_call_profile") or []) + [entry]
        result["metadata"] = meta
        return result
    return result


def extract_llm_text_and_usage(result: Any) -> tuple[str, dict[str, int]]:
    if isinstance(result, str):
        return result, {}
    if not isinstance(result, dict):
        return "", {}

    if isinstance(result.get("text"), str):
        return result.get("text", ""), _normalize_llm_usage(result.get("usage"))

    usage = _normalize_llm_usage(result.get("usage"))
    if isinstance(result.get("response"), str):
        return result.get("response", ""), usage
    choices = result.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0] if isinstance(choices[0], dict) else {}
        if isinstance(first.get("text"), str):
            return first.get("text", ""), usage
        msg = first.get("message")
        if isinstance(msg, dict) and isinstance(msg.get("content"), str):
            return msg.get("content", ""), usage
    content = result.get("content")
    if isinstance(content, list) and content and isinstance(content[0], dict):
        text = content[0].get("text")
        if isinstance(text, str):
            return text, usage
    return "", usage


def extract_llm_call_metadata(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    raw = result.get("metadata")
    return dict(raw) if isinstance(raw, dict) else {}


def _build_chat_messages(prompt: str, history: list | None) -> list:
    messages = []
    if history:
        for h in history:
            if isinstance(h, dict) and "role" in h and "content" in h:
                messages.append({"role": h["role"], "content": h["content"]})
            elif isinstance(h, dict):
                messages.append({"role": "user", "content": h.get("prompt") or ""})
                assistant_msg = f"REASON: {h.get('reason')}\nCOMMAND: {h.get('command')}"
                messages.append({"role": "assistant", "content": assistant_msg})
                if "output" in h:
                    messages.append({"role": "system", "content": f"Befehlsausgabe: {h.get('output')}"})
    messages.append({"role": "user", "content": prompt})
    return messages


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _truncate_text(text: str, max_tokens: int, keep: str = "end") -> str:
    max_chars = max_tokens * 4
    if len(text) <= max_chars:
        return text
    if keep == "start":
        return text[:max_chars]
    return text[-max_chars:]


def _trim_messages(messages: list, max_context_tokens: int, max_output_tokens: int) -> list:
    budget = max(max_context_tokens - max_output_tokens - 256, 256)
    if not messages:
        return messages

    system_msg = None
    if messages and messages[0].get("role") == "system":
        system_msg = dict(messages[0])
        messages = messages[1:]

    total_tokens = 0
    for msg in messages:
        total_tokens += _estimate_tokens(str(msg.get("content", "")))
    if system_msg:
        total_tokens += _estimate_tokens(str(system_msg.get("content", "")))
    if total_tokens <= budget:
        return [system_msg] + messages if system_msg else messages

    trimmed_messages = []
    remaining = budget
    if system_msg:
        system_tokens = _estimate_tokens(str(system_msg.get("content", "")))
        system_budget = min(system_tokens, max(64, budget // 2))
        system_msg["content"] = _truncate_text(str(system_msg.get("content", "")), system_budget, keep="start")
        remaining -= _estimate_tokens(system_msg["content"])
        trimmed_messages.append(system_msg)

    for msg in reversed(messages):
        content = str(msg.get("content", ""))
        tokens = _estimate_tokens(content)
        if tokens <= remaining:
            trimmed_messages.append(msg)
            remaining -= tokens
            continue
        if remaining <= 0:
            break
        msg = dict(msg)
        msg["content"] = _truncate_text(content, remaining, keep="end")
        trimmed_messages.append(msg)
        break

    trimmed_messages_tail = list(reversed(trimmed_messages[1:] if system_msg else trimmed_messages))
    if system_msg:
        return [trimmed_messages[0]] + trimmed_messages_tail
    return trimmed_messages_tail


def _is_same_provider_url(provider: str, left: str | None, right: str | None) -> bool:
    left_value = str(left or "").strip()
    right_value = str(right or "").strip()
    if not left_value or not right_value:
        return False
    if provider == "lmstudio":
        return _normalize_lmstudio_base_url(left_value) == _normalize_lmstudio_base_url(right_value)
    if provider == "ollama":
        return _normalize_ollama_base_url(left_value) == _normalize_ollama_base_url(right_value)
    return left_value.rstrip("/") == right_value.rstrip("/")


def _default_model_for_provider(provider: str, current_model: str | None = None) -> str | None:
    provider_name = str(provider or "").strip().lower()
    model_name = str(current_model or "").strip()
    if provider_name == "ollama":
        if model_name.lower() in {"llama3", "mistral", "ananta-default", "ananta-default:latest"}:
            return model_name
        return "ananta-default:latest"
    return model_name or None


def resolve_preferred_local_runtime(
    provider: str | None,
    provider_urls: dict[str, str | None] | None,
    timeout: int,
) -> dict[str, Any]:
    provider_name = str(provider or "").strip().lower()
    urls = provider_urls or {}
    if provider_name not in {"lmstudio", "ollama"}:
        return {
            "provider": provider_name,
            "base_url": urls.get(provider_name),
            "selection_source": "provider_config",
        }

    lmstudio_url = str(urls.get("lmstudio") or "").strip()
    ollama_url = str(urls.get("ollama") or "").strip()
    cache_key = f"lmstudio={lmstudio_url}|ollama={ollama_url}"
    now = time.time()
    cached = _LOCAL_RUNTIME_SELECTION_CACHE.get(cache_key)
    if cached and now - float(cached.get("checked_at") or 0.0) < _LOCAL_RUNTIME_SELECTION_CACHE_TTL_SECONDS:
        return {k: v for k, v in cached.items() if k != "checked_at"}

    probes: dict[str, tuple[str, Any]] = {
        "lmstudio": (lmstudio_url, probe_lmstudio_runtime),
        "ollama": (ollama_url, probe_ollama_runtime),
    }
    fallback_provider = "ollama" if provider_name == "lmstudio" else "lmstudio"

    primary_url, primary_probe_fn = probes.get(provider_name, ("", None))
    primary_probe = primary_probe_fn(primary_url, timeout=timeout) if primary_url and primary_probe_fn else {"ok": False}
    if primary_probe.get("ok"):
        result = {
            "provider": provider_name,
            "base_url": primary_url,
            "selection_source": f"runtime.{provider_name}_available",
        }
    else:
        fallback_url, fallback_probe_fn = probes.get(fallback_provider, ("", None))
        fallback_probe = fallback_probe_fn(fallback_url, timeout=timeout) if fallback_url and fallback_probe_fn else {"ok": False}
        if fallback_probe.get("ok"):
            result = {
                "provider": fallback_provider,
                "base_url": fallback_url,
                "selection_source": f"runtime.{fallback_provider}_fallback",
            }
        else:
            result = {
                "provider": provider_name,
                "base_url": urls.get(provider_name),
                "selection_source": "provider_config",
            }

    _LOCAL_RUNTIME_SELECTION_CACHE[cache_key] = {**result, "checked_at": now}
    return result


def _build_history_prompt(prompt: str, history: list | None) -> str:
    full_prompt = prompt
    if history:
        history_str = "\n\nHistorie bisheriger Interaktionen:\n"
        for h in history:
            if isinstance(h, dict) and "role" in h and "content" in h:
                role_map = {"user": "User", "assistant": "Assistant", "system": "System"}
                role = role_map.get(h["role"], h["role"])
                history_str += f"{role}: {h['content']}\n"
            elif isinstance(h, dict):
                history_str += f"- Prompt: {h.get('prompt')}\n"
                history_str += f"  Reasoning: {h.get('reason')}\n"
                history_str += f"  Befehl: {h.get('command')}\n"
                if "output" in h:
                    out = h.get("output", "")
                    if len(out) > 500:
                        out = out[:500] + "..."
                    history_str += f"  Ergebnis: {out}\n"
        full_prompt = history_str + "\nAktueller Auftrag:\n" + prompt
    return full_prompt


def generate_text(
    prompt: str,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    history: Optional[list] = None,
    temperature: Optional[float] = None,
    max_context_tokens: Optional[int] = None,
    max_output_tokens: Optional[int] = None,
    tools: Optional[list] = None,
    tool_choice: Optional[Any] = None,
    timeout: Optional[int] = None,
    trace_goal_id: Optional[str] = None,
    trace_task_id: Optional[str] = None,
) -> Any:
    p = provider or _runtime_default_provider()
    m = model or _runtime_default_model()
    urls = _runtime_provider_urls()

    actual_timeout = timeout if timeout is not None else HTTP_TIMEOUT

    runtime_default_provider = _runtime_default_provider()
    effective_base_url = str(base_url or "").strip() or None
    provider_was_explicit = bool(str(provider or "").strip())
    provider_uses_runtime_url = not effective_base_url or _is_same_provider_url(p, effective_base_url, urls.get(p))

    if p in {"lmstudio", "ollama"} and provider_uses_runtime_url and not provider_was_explicit:
        probe_timeout = max(1, min(int(actual_timeout), _LOCAL_RUNTIME_PROBE_TIMEOUT_SECONDS))
        runtime_choice = resolve_preferred_local_runtime(p, urls, timeout=probe_timeout)
        selected_provider = str(runtime_choice.get("provider") or p).strip().lower() or p
        if selected_provider != p:
            p = selected_provider
            if not model or (provider_was_explicit is False and selected_provider == "ollama"):
                m = _default_model_for_provider(selected_provider, m) or m
            effective_base_url = None

    if effective_base_url:
        urls[p] = effective_base_url

    if p == "ollama":
        ollama_base_url = str(urls.get("ollama") or "").strip()
        if ollama_base_url:
            probe_timeout = max(1, min(int(actual_timeout), _LOCAL_RUNTIME_PROBE_TIMEOUT_SECONDS))
            resolved_model = resolve_ollama_model(m, ollama_base_url, probe_timeout)
            if resolved_model:
                m = resolved_model

    key = api_key
    if not key:
        key = _runtime_api_key(p)

    idempotency_key = str(uuid.uuid4())

    return _call_llm(
        p,
        m,
        prompt,
        urls,
        key,
        timeout=actual_timeout,
        history=history,
        temperature=temperature,
        max_context_tokens=max_context_tokens,
        max_output_tokens=max_output_tokens,
        tools=tools,
        tool_choice=tool_choice,
        trace_goal_id=trace_goal_id,
        trace_task_id=trace_task_id,
        idempotency_key=idempotency_key,
    )


def _call_llm(
    provider: str,
    model: str,
    prompt: str,
    urls: dict,
    api_key: str | None,
    timeout: int = HTTP_TIMEOUT,
    history: list | None = None,
    temperature: float | None = None,
    max_context_tokens: int | None = None,
    max_output_tokens: int | None = None,
    tools: list | None = None,
    tool_choice: Any | None = None,
    max_retries: int | None = None,
    backoff_factor: float | None = None,
    trace_goal_id: Optional[str] = None,
    trace_task_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> Any:
    if not _check_circuit_breaker(provider):
        logging.warning(f"Abbruch: Circuit Breaker für {provider} ist offen.")
        return ""
    if not _check_rate_limit(provider):
        logging.warning("Abbruch: Rate-Limit für provider=%s überschritten.", provider)
        return ""

    if max_retries is None:
        max_retries = int(getattr(settings, "retry_count", 3))
    else:
        max_retries = int(max_retries)
    if backoff_factor is None:
        backoff_factor = float(getattr(settings, "retry_backoff", 1.5))
    else:
        backoff_factor = float(backoff_factor)

    if not idempotency_key:
        idempotency_key = str(uuid.uuid4())

    request_id = None
    request_path = None
    request_method = None
    if has_request_context():
        request_id = getattr(g, "llm_request_id", None)
        request_path = request.path
        request_method = request.method
        g.llm_last_usage = {}

    log_llm_entry(
        event="llm_call_start",
        request_id=request_id,
        idempotency_key=idempotency_key,
        provider=provider,
        model=model,
        prompt=prompt,
        history_len=len(history) if history else 0,
        request_path=request_path,
        request_method=request_method,
    )

    _prompt_trace = None
    try:
        from agent.services.prompt_trace_service import get_prompt_trace_service
        from agent.services.context_file_selector import provider_to_llm_scope
        _trace_svc = get_prompt_trace_service()
        _goal_id = str(trace_goal_id or "").strip() or (getattr(g, "llm_goal_id", None) if has_app_context() else None)
        _task_id = str(trace_task_id or "").strip() or (getattr(g, "llm_task_id", None) if has_app_context() else None)
        _llm_scope = provider_to_llm_scope(provider, urls.get(provider))
        _context_sources = []
        if history:
            _context_sources.append(
                {
                    "kind": "history",
                    "included": True,
                    "count": len(list(history or [])),
                    "hash": _sha256_text(str(history)),
                }
            )
        if prompt:
            _context_sources.append(
                {
                    "kind": "prompt",
                    "included": True,
                    "chars": len(str(prompt)),
                    "hash": _sha256_text(str(prompt)),
                }
            )
        _prompt_trace = _trace_svc.create_trace(
            request_id=request_id,
            idempotency_key=idempotency_key,
            goal_id=_goal_id,
            task_id=_task_id,
            source_component="llm_integration",
            provider=provider,
            model=model,
            request_kind="generate",
            prompt=prompt,
            messages=history,
            tools=tools,
            context_sources=_context_sources,
            llm_scope=_llm_scope,
            sensitivity_level="internal",
        )
        if has_app_context():
            existing = list(getattr(g, "llm_prompt_trace_ids", []) or [])
            existing.append(_prompt_trace.trace_id)
            g.llm_prompt_trace_ids = existing
    except Exception as _pti_exc:
        logging.debug("PTI trace creation skipped: %s", _pti_exc)

    for attempt in range(max_retries + 1):
        if attempt > 0:
            logging.info(f"LLM Retry Versuch {attempt}/{max_retries} für Provider {provider} (Key: {idempotency_key})")
            RETRIES_TOTAL.inc()
            time.sleep(backoff_factor**attempt)

        try:
            started_at = time.time()
            res = _execute_llm_call(
                provider=provider,
                model=model,
                prompt=prompt,
                urls=urls,
                api_key=api_key,
                timeout=timeout,
                history=history,
                temperature=temperature,
                max_context_tokens=max_context_tokens,
                max_output_tokens=max_output_tokens,
                tools=tools,
                tool_choice=tool_choice,
                idempotency_key=idempotency_key,
            )
            ended_at = time.time()

            text_out, usage = extract_llm_text_and_usage(res)
            normalized_usage = _normalize_llm_usage(usage)
            success_entry = _build_llm_call_profile_entry(
                name="generate_text",
                backend="llm_integration",
                provider=provider,
                model=model,
                success=bool(text_out and text_out.strip()),
                started_at=started_at,
                ended_at=ended_at,
                usage=normalized_usage if normalized_usage else None,
                source="llm_integration",
                estimated=False,
            )
            call_metadata = extract_llm_call_metadata(res)
            if not (text_out and text_out.strip()) and call_metadata:
                success_entry["error_type"] = str(
                    call_metadata.get("empty_reason") or "empty_response"
                )
                ctx_limit = call_metadata.get("context_limit")
                model_id_meta = call_metadata.get("model_id")
                msg_parts = [f"empty_reason={call_metadata.get('empty_reason')}"]
                if ctx_limit:
                    msg_parts.append(f"context_limit={ctx_limit}")
                if model_id_meta:
                    msg_parts.append(f"model={model_id_meta}")
                success_entry["error_message"] = ", ".join(msg_parts)
            if has_request_context():
                g.llm_last_call_profile = list(getattr(g, "llm_last_call_profile", []) or []) + [success_entry]
            res = _attach_llm_call_profile(res, success_entry)
            if text_out and text_out.strip():
                _report_llm_success(provider)
                if has_request_context():
                    g.llm_last_usage = usage
                log_llm_entry(
                    event="llm_call_end",
                    request_id=request_id,
                    provider=provider,
                    model=model,
                    success=True,
                    attempts=attempt + 1,
                    response=text_out,
                )
                if _prompt_trace is not None:
                    try:
                        _trace_svc = get_prompt_trace_service()
                        _finalized = _trace_svc.finalize_trace(
                            _prompt_trace, success=True, response_text=text_out,
                            usage=normalized_usage or {},
                        )
                        _trace_svc.store(_finalized)
                    except Exception as _pti_exc:
                        logging.debug("PTI finalize trace skipped: %s", _pti_exc)
                return text_out
        except PermanentError as e:
            ended_at = time.time()
            error_entry = _build_llm_call_profile_entry(
                name="generate_text",
                backend="llm_integration",
                provider=provider,
                model=model,
                success=False,
                started_at=started_at if "started_at" in locals() else None,
                ended_at=ended_at,
                usage=None,
                source="llm_integration",
                estimated=False,
                error_type=type(e).__name__,
                error_message=str(e),
            )
            if has_request_context():
                g.llm_last_call_profile = list(getattr(g, "llm_last_call_profile", []) or []) + [error_entry]
            logging.error(f"Permanenter Fehler bei LLM-Aufruf (Versuch {attempt + 1}): {e}")
            if _prompt_trace is not None:
                try:
                    _trace_svc = get_prompt_trace_service()
                    _finalized = _trace_svc.finalize_trace(
                        _prompt_trace, success=False,
                        error_type=type(e).__name__, error_message=str(e),
                    )
                    _trace_svc.store(_finalized)
                    _prompt_trace = None
                except Exception:
                    pass
            break
        except Exception as e:
            ended_at = time.time()
            error_entry = _build_llm_call_profile_entry(
                name="generate_text",
                backend="llm_integration",
                provider=provider,
                model=model,
                success=False,
                started_at=started_at if "started_at" in locals() else None,
                ended_at=ended_at,
                usage=None,
                source="llm_integration",
                estimated=False,
                error_type=type(e).__name__,
                error_message=str(e),
            )
            if has_request_context():
                g.llm_last_call_profile = list(getattr(g, "llm_last_call_profile", []) or []) + [error_entry]
            logging.warning(f"Fehler bei LLM-Aufruf (Versuch {attempt + 1}): {e}")

        logging.warning(f"LLM Aufruf lieferte kein Ergebnis oder schlug fehl (Versuch {attempt + 1}/{max_retries + 1})")

    _report_llm_failure(provider)
    logging.error(f"LLM Aufruf nach {max_retries} Retries endgültig fehlgeschlagen.")
    log_llm_entry(
        event="llm_call_end",
        request_id=request_id,
        provider=provider,
        model=model,
        success=False,
        attempts=max_retries + 1,
        response="",
    )
    if _prompt_trace is not None:
        try:
            _trace_svc = get_prompt_trace_service()
            _finalized = _trace_svc.finalize_trace(
                _prompt_trace, success=False, error_type="max_retries_exceeded",
            )
            _trace_svc.store(_finalized)
        except Exception:
            pass
    return ""


def _execute_llm_call(
    provider: str,
    model: str,
    prompt: str,
    urls: dict,
    api_key: str | None,
    timeout: int = HTTP_TIMEOUT,
    history: list | None = None,
    temperature: float | None = None,
    max_context_tokens: int | None = None,
    max_output_tokens: int | None = None,
    tools: list | None = None,
    tool_choice: Any | None = None,
    idempotency_key: Optional[str] = None,
) -> Any:
    with LLM_CALL_DURATION.time():
        strategy = get_strategy(provider)
        if not strategy:
            logging.error(f"Unbekannter Provider: {provider}")
            return ""

        url = urls.get(provider)
        if not url:
            logging.error(f"Keine URL für Provider {provider} konfiguriert.")
            return ""

        return strategy.execute(
            model=model,
            prompt=prompt,
            url=url,
            api_key=api_key,
            history=history,
            timeout=timeout,
            temperature=temperature,
            max_context_tokens=max_context_tokens,
            max_output_tokens=max_output_tokens,
            tools=tools,
            tool_choice=tool_choice,
            idempotency_key=idempotency_key,
            provider=provider,
        )
