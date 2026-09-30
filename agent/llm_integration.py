# Compatibility helpers imported below are intentionally re-exported for old
# callers while their implementations live in focused modules.
# ruff: noqa: F401

import logging
import time
import uuid
from typing import Any, Optional
from urllib.parse import urlsplit, urlunsplit

from flask import current_app, g, has_app_context, has_request_context, request

from agent.common.errors import PermanentError
from agent.config import settings
from agent.llm_call_profile import (
    LLM_CALL_PROFILE_FIELDS,
    _attach_llm_call_profile,
    _build_llm_call_profile_entry,
    _normalize_llm_usage,
    build_llm_call_profile_entry,
    extract_llm_call_metadata,
    extract_llm_text_and_usage,
    normalize_llm_call_profile_entry,
)
from agent.llm_integration_lmstudio import (
    _LMSTUDIO_HISTORY_FILE,
    _extract_lmstudio_candidates,
    _extract_lmstudio_text,
    _extract_lmstudio_usage,
    _find_matching_lmstudio_candidate,
    _list_lmstudio_candidates,
    _lmstudio_models_url,
    _load_lmstudio_history,
    _model_identifier_matches,
    _model_identifier_tokens,
    _normalize_lmstudio_base_url,
    _prepare_lmstudio_history,
    _record_lmstudio_result,
    _resolve_lmstudio_model,
    _save_lmstudio_history,
    _select_best_lmstudio_model,
    _sha256_text,
    _touch_lmstudio_models,
    _update_lmstudio_history,
    probe_lmstudio_runtime,
)
from agent.llm_integration_ollama import (
    _find_matching_ollama_candidate,
    _normalize_ollama_base_url,
    _ollama_ps_url,
    _ollama_tags_url,
    probe_ollama_activity,
    probe_ollama_runtime,
    resolve_ollama_model,
)
from agent.llm_prompt_messages import (
    _build_chat_messages,
    _build_history_prompt,
    _estimate_tokens,
    _trim_messages,
    _truncate_text,
)
from agent.llm_resilience import (
    _CB_DEFAULT_RECOVERY_TIME,
    _CB_DEFAULT_THRESHOLD,
    _ERR_FAILURE_WINDOW,
    _ERR_RATE_LOCK,
    _ERR_SUCCESS_WINDOW,
    _RATE_LIMIT_LOCK,
    _RATE_LIMIT_WINDOW,
    CIRCUIT_BREAKER,
    _cb_config,
    _check_circuit_breaker,
    _check_rate_limit,
    _record_llm_failure_rate,
    _report_llm_failure,
    _report_llm_success,
    _rl_config,
    get_circuit_breaker_state,
    get_provider_error_rate,
    get_rate_limit_state,
)
from agent.llm_strategies import get_strategy
from agent.metrics import LLM_CALL_DURATION, RETRIES_TOTAL
from agent.utils import _http_get, get_data_dir, log_llm_entry, read_json, update_json, write_json

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


def _runtime_context_limit(provider: str, model: str | None = None, requested: int | None = None) -> int | None:
    """The window of this call. Local runtimes: the effective window (profile capped by ``llm_config.context_limit``,
    the model map and what the runtime serves); a requested limit only narrows it. Cloud/subscription providers keep
    their own window: only a requested or declared limit applies (``agent.context_profile.window_for_provider``)."""
    from agent.context_profile import window_for_provider

    try:
        requested_tokens = int(requested) if requested else None
    except (TypeError, ValueError):
        requested_tokens = None
    return window_for_provider(str(provider or "").strip().lower() or None, model=model or None,
                               requested=requested_tokens)


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
    primary_probe = (
        primary_probe_fn(primary_url, timeout=timeout)
        if primary_url and primary_probe_fn
        else {"ok": False}
    )
    if primary_probe.get("ok"):
        result = {
            "provider": provider_name,
            "base_url": primary_url,
            "selection_source": f"runtime.{provider_name}_available",
        }
    else:
        fallback_url, fallback_probe_fn = probes.get(fallback_provider, ("", None))
        fallback_probe = (
            fallback_probe_fn(fallback_url, timeout=timeout)
            if fallback_url and fallback_probe_fn
            else {"ok": False}
        )
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
    provider_context: Any = None,
    seed: Optional[int] = None,
    max_retries: Optional[int] = None,
    backoff_factor: Optional[float] = None,
) -> Any:
    p = provider or _runtime_default_provider()
    m = model or _runtime_default_model()
    urls = _runtime_provider_urls()

    actual_timeout = timeout if timeout is not None else HTTP_TIMEOUT

    effective_base_url = str(base_url or "").strip() or None
    provider_uses_runtime_url = not effective_base_url or _is_same_provider_url(p, effective_base_url, urls.get(p))

    if p in {"lmstudio", "ollama"} and provider_uses_runtime_url:
        probe_timeout = max(1, min(int(actual_timeout), _LOCAL_RUNTIME_PROBE_TIMEOUT_SECONDS))
        runtime_choice = resolve_preferred_local_runtime(p, urls, timeout=probe_timeout)
        selected_provider = str(runtime_choice.get("provider") or p).strip().lower() or p
        if selected_provider != p:
            p = selected_provider
            if selected_provider == "ollama":
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
    max_context_tokens = _runtime_context_limit(p, m, max_context_tokens)

    idempotency_key = str(uuid.uuid4())

    from agent.context_window import check_fit, record_expected_overflow, truncation_scope

    if max_context_tokens:
        # LCTX-002: does the assembled request fit? (the strategy trims below; every trim is recorded)
        record_expected_overflow("llm.generate_text", check_fit(
            prompt=prompt, messages=history if isinstance(history, list) else None,
            window_tokens=int(max_context_tokens), output_reserve_tokens=max_output_tokens))
    with truncation_scope() as truncations:
        result = _call_generate(p, m, prompt, urls, key, actual_timeout, history, temperature, max_context_tokens,
                                max_output_tokens, tools, tool_choice, trace_goal_id, trace_task_id,
                                idempotency_key, provider_context, seed, max_retries, backoff_factor)
    return _attach_context_truncation(result, truncations)


def _attach_context_truncation(result: Any, truncations: list) -> Any:
    """Make shortened context visible on the result and the request (LCTX-002: never silent)."""
    from agent.context_window import truncation_summary

    summary = truncation_summary(truncations)
    if summary is None:
        return result
    if has_app_context():
        try:
            from flask import g

            g.llm_context_truncations = list(getattr(g, "llm_context_truncations", []) or []) + summary["events"]
        except RuntimeError:
            pass
    if isinstance(result, dict):
        meta = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
        meta["context_truncation"] = summary
        result["metadata"] = meta
    return result


def _call_generate(p, m, prompt, urls, key, actual_timeout, history, temperature, max_context_tokens,
                   max_output_tokens, tools, tool_choice, trace_goal_id, trace_task_id, idempotency_key,
                   provider_context, seed, max_retries, backoff_factor) -> Any:
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
        provider_context=provider_context,
        seed=seed,
        max_retries=max_retries,
        backoff_factor=backoff_factor,
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
    provider_context: Any = None,
    seed: int | None = None,
) -> Any:
    from agent.providers.redaction import redact_provider_payload
    from agent.services.provider_invocation_middleware import (
        ProviderInvocationBlocked,
        ProviderInvocationContext,
        get_provider_invocation_middleware,
    )

    if not _check_circuit_breaker(provider):
        logging.warning(f"Abbruch: Circuit Breaker für {provider} ist offen.")
        return ""
    if not _check_rate_limit(provider):
        logging.warning("Abbruch: Rate-Limit für provider=%s überschritten.", provider)
        return ""

    max_retries, backoff_factor = _resolve_retry_policy(max_retries, backoff_factor)
    if not idempotency_key:
        idempotency_key = str(uuid.uuid4())

    try:
        resolved_provider_context = ProviderInvocationContext.from_value(provider_context)
        resolved_provider_context.assert_valid()
    except ProviderInvocationBlocked as exc:
        logging.error("Provider-Kontext blockiert LLM-Aufruf: %s", exc.reason_code)
        return ""
    safe_observability_payload = redact_provider_payload(
        {"prompt": prompt, "history": list(history or [])},
        secret_refs=resolved_provider_context.secret_refs,
    )
    prompt = str(safe_observability_payload.get("prompt") or "")
    history = list(safe_observability_payload.get("history") or [])

    request_id, request_path, request_method = _llm_request_log_context()
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
    _prompt_trace = _create_llm_prompt_trace(
        provider=provider,
        model=model,
        prompt=prompt,
        history=history,
        tools=tools,
        provider_url=urls.get(provider),
        request_id=request_id,
        idempotency_key=idempotency_key,
        trace_goal_id=trace_goal_id,
        trace_task_id=trace_task_id,
    )

    provider_middleware = get_provider_invocation_middleware()
    # Kept across attempts (as before the extraction): a failure before the call reuses the last start time.
    started_at: float | None = None
    for attempt in range(max_retries + 1):
        if attempt > 0:
            logging.info(f"LLM Retry Versuch {attempt}/{max_retries} für Provider {provider} (Key: {idempotency_key})")
            RETRIES_TOTAL.inc()
            time.sleep(backoff_factor**attempt)

        prepared = None
        try:
            prepared = provider_middleware.prepare(
                context=resolved_provider_context.for_attempt(
                    attempt,
                    retry_id=f"{idempotency_key}:provider:{attempt}",
                ),
                provider=provider,
                model=model,
                endpoint_url=str(urls.get(provider) or ""),
                payload={
                    "prompt": prompt,
                    "history": list(history or []),
                    "temperature": temperature,
                    "max_context_tokens": max_context_tokens,
                    "max_output_tokens": max_output_tokens,
                    "tools": list(tools or []),
                    "tool_choice": tool_choice,
                    "seed": seed,
                },
            )
            cached_text = _cached_provider_text(prepared)
            if cached_text:
                _report_llm_success(provider)
                return cached_text
            safe_payload = prepared.payload
            started_at = time.time()
            res = _execute_llm_call(
                provider=provider,
                model=model,
                prompt=str(safe_payload.get("prompt") or ""),
                urls=urls,
                api_key=api_key,
                timeout=timeout,
                history=list(safe_payload.get("history") or []),
                temperature=safe_payload.get("temperature"),
                max_context_tokens=safe_payload.get("max_context_tokens"),
                max_output_tokens=safe_payload.get("max_output_tokens"),
                tools=list(safe_payload.get("tools") or []),
                tool_choice=safe_payload.get("tool_choice"),
                seed=safe_payload.get("seed"),
                idempotency_key=idempotency_key,
            )
            text_out = _complete_llm_attempt(
                res,
                provider=provider,
                model=model,
                started_at=started_at,
                prepared=prepared,
                provider_middleware=provider_middleware,
                request_id=request_id,
                attempt=attempt,
                prompt_trace=_prompt_trace,
            )
            if text_out is not None:
                return text_out
        except ProviderInvocationBlocked as e:
            logging.error("Provider-Middleware blockiert LLM-Aufruf: %s", e.reason_code)
            break
        except PermanentError as e:
            _record_failed_llm_attempt(e, prepared, provider_middleware, provider, model, started_at)
            logging.error(f"Permanenter Fehler bei LLM-Aufruf (Versuch {attempt + 1}): {e}")
            if _prompt_trace is not None and _finalize_llm_prompt_trace(
                _prompt_trace, success=False, error_type=type(e).__name__, error_message=str(e)
            ):
                _prompt_trace = None
            break
        except Exception as e:
            _record_failed_llm_attempt(e, prepared, provider_middleware, provider, model, started_at)
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
        _finalize_llm_prompt_trace(_prompt_trace, success=False, error_type="max_retries_exceeded")
    return ""


def _resolve_retry_policy(max_retries: int | None, backoff_factor: float | None) -> tuple[int, float]:
    """Explicit retry arguments win over the ``retry_count`` / ``retry_backoff`` settings."""
    resolved_retries = int(getattr(settings, "retry_count", 3)) if max_retries is None else int(max_retries)
    resolved_backoff = (
        float(getattr(settings, "retry_backoff", 1.5)) if backoff_factor is None else float(backoff_factor)
    )
    return resolved_retries, resolved_backoff


def _llm_request_log_context() -> tuple[Any, Any, Any]:
    """(request_id, path, method) of the current HTTP request; resets the request's last usage."""
    if not has_request_context():
        return None, None, None
    request_id = getattr(g, "llm_request_id", None)
    request_path, request_method = request.path, request.method
    g.llm_last_usage = {}
    return request_id, request_path, request_method


def _prompt_context_sources(prompt: str, history: list) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    if history:
        sources.append(
            {"kind": "history", "included": True, "count": len(list(history or [])), "hash": _sha256_text(str(history))}
        )
    if prompt:
        sources.append(
            {"kind": "prompt", "included": True, "chars": len(str(prompt)), "hash": _sha256_text(str(prompt))}
        )
    return sources


def _create_llm_prompt_trace(
    *,
    provider: str,
    model: str,
    prompt: str,
    history: list,
    tools: list | None,
    provider_url: Any,
    request_id: Any,
    idempotency_key: str,
    trace_goal_id: Optional[str],
    trace_task_id: Optional[str],
) -> Any:
    """PTI: open a prompt trace for this call; best effort (None when tracing is unavailable)."""
    try:
        from agent.services.context_file_selector import provider_to_llm_scope
        from agent.services.prompt_trace_service import get_prompt_trace_service

        _trace_svc = get_prompt_trace_service()
        _goal_id = str(trace_goal_id or "").strip() or (getattr(g, "llm_goal_id", None) if has_app_context() else None)
        _task_id = str(trace_task_id or "").strip() or (getattr(g, "llm_task_id", None) if has_app_context() else None)
        _llm_scope = provider_to_llm_scope(provider, provider_url)
        trace = _trace_svc.create_trace(
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
            context_sources=_prompt_context_sources(prompt, history),
            llm_scope=_llm_scope,
            sensitivity_level="internal",
        )
        if has_app_context():
            existing = list(getattr(g, "llm_prompt_trace_ids", []) or [])
            existing.append(trace.trace_id)
            g.llm_prompt_trace_ids = existing
        return trace
    except Exception as _pti_exc:
        logging.debug("PTI trace creation skipped: %s", _pti_exc)
        return None


def _finalize_llm_prompt_trace(prompt_trace: Any, **outcome: Any) -> bool:
    """PTI: finalize and store a prompt trace; best effort, True when stored."""
    try:
        from agent.services.prompt_trace_service import get_prompt_trace_service

        _trace_svc = get_prompt_trace_service()
        _trace_svc.store(_trace_svc.finalize_trace(prompt_trace, **outcome))
        return True
    except Exception as _pti_exc:
        logging.debug("PTI finalize trace skipped: %s", _pti_exc)
        return False


def _cached_provider_text(prepared: Any) -> str:
    if prepared.cached_response is None:
        return ""
    return str(prepared.cached_response.get("content") or "")


def _append_llm_call_profile(entry: dict) -> None:
    if has_request_context():
        g.llm_last_call_profile = list(getattr(g, "llm_last_call_profile", []) or []) + [entry]


def _describe_empty_response(entry: dict, call_metadata: dict) -> None:
    """Explain an empty provider answer on the call profile entry."""
    entry["error_type"] = str(call_metadata.get("empty_reason") or "empty_response")
    msg_parts = [f"empty_reason={call_metadata.get('empty_reason')}"]
    if call_metadata.get("context_limit"):
        msg_parts.append(f"context_limit={call_metadata.get('context_limit')}")
    if call_metadata.get("model_id"):
        msg_parts.append(f"model={call_metadata.get('model_id')}")
    entry["error_message"] = ", ".join(msg_parts)


def _complete_llm_attempt(
    res: Any,
    *,
    provider: str,
    model: str,
    started_at: float,
    prepared: Any,
    provider_middleware: Any,
    request_id: Any,
    attempt: int,
    prompt_trace: Any,
) -> str | None:
    """Record one provider answer; returns the text on success, None for an empty answer (retry)."""
    ended_at = time.time()
    text_out, usage = extract_llm_text_and_usage(res)
    has_text = bool(text_out and text_out.strip())
    normalized_usage = _normalize_llm_usage(usage)
    success_entry = _build_llm_call_profile_entry(
        name="generate_text",
        backend="llm_integration",
        provider=provider,
        model=model,
        success=has_text,
        started_at=started_at,
        ended_at=ended_at,
        usage=normalized_usage if normalized_usage else None,
        source="llm_integration",
        estimated=False,
    )
    call_metadata = extract_llm_call_metadata(res)
    if not has_text and call_metadata:
        _describe_empty_response(success_entry, call_metadata)
    _append_llm_call_profile(success_entry)
    _attach_llm_call_profile(res, success_entry)
    if not has_text:
        provider_middleware.fail(prepared, provider=provider, model=model, reason_code="provider_empty_response")
        return None
    provider_middleware.complete(
        prepared,
        provider=provider,
        model=model,
        response={"content": text_out, "usage": normalized_usage},
    )
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
    if prompt_trace is not None:
        _finalize_llm_prompt_trace(prompt_trace, success=True, response_text=text_out, usage=normalized_usage or {})
    return text_out


def _record_failed_llm_attempt(
    exc: Exception,
    prepared: Any,
    provider_middleware: Any,
    provider: str,
    model: str,
    started_at: float | None,
) -> None:
    if prepared is not None:
        provider_middleware.fail(prepared, provider=provider, model=model, reason_code=type(exc).__name__)
    _append_llm_call_profile(
        _build_llm_call_profile_entry(
            name="generate_text",
            backend="llm_integration",
            provider=provider,
            model=model,
            success=False,
            started_at=started_at,
            ended_at=time.time(),
            usage=None,
            source="llm_integration",
            estimated=False,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
    )


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
    seed: int | None = None,
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
            seed=seed,
        )
