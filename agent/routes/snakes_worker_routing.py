"""Worker routing helpers for snake ask — token auth, worker selection, worker proposal."""

from __future__ import annotations

import logging
import json
import os
import secrets
from typing import Any

from flask import request

from agent.config import settings

from .snakes_chat_helpers import SnakeAskLimits, _fit_answer_to_chars


_HEAVY_SNAKE_TERMS = frozenset(
    {
        "architecture", "architektur", "bug", "code", "coding", "debug",
        "datei", "file", "funktion", "implement", "klasse", "migration",
        "repo", "repository", "refactor", "stacktrace", "test",
    }
)


def resolve_snake_routing_task_kind(prompt: str) -> str:
    """Hub-owned, deterministic classification for delegated Snake inference."""
    normalized = str(prompt or "").lower()
    tokens = {token.strip(".,:;!?()[]{}\"'") for token in normalized.split()}
    if tokens & _HEAVY_SNAKE_TERMS:
        if tokens & {"bug", "debug", "stacktrace"}:
            return "debugging"
        return "repo_analysis"
    return "classification"


def snake_profile_routing_enabled() -> bool:
    return str(os.environ.get("ANANTA_AI_SNAKE_PROFILE_ROUTING") or "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def _get_snakes() -> dict:
    from agent.routes.snakes_state import _snakes
    return _snakes


def _auth_token(snake_id: str) -> str | None:
    """Extract Bearer token from Authorization header. Returns None if missing."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return None


def _verify_token(snake_id: str) -> bool:
    snake = _get_snakes().get(snake_id)
    if not snake or not snake.get("active"):
        return False
    token = _auth_token(snake_id)
    return token is not None and secrets.compare_digest(str(snake.get("token") or ""), token)


def _pick_worker_for_ask(*, exclude_urls: set[str] | None = None) -> tuple[str, str | None]:
    """Return (worker_url, token) for the first online worker, or ("", None)."""
    try:
        from agent.services.agent_registry_service import get_agent_registry_service
        from agent.services.repository_registry import get_repository_registry

        agents = get_agent_registry_service().get_online_agents()
        if not agents:
            return "", None
        excluded = set(exclude_urls or ())
        for agent in agents:
            worker_url = str(getattr(agent, "url", "") or "").strip()
            if not worker_url or worker_url in excluded:
                continue
            token: str | None = None
            try:
                db_agent = get_repository_registry().agent_repo.get_by_url(worker_url)
                token = str(getattr(db_agent, "token", "") or "").strip() or None
            except Exception:
                pass
            return worker_url, token
        return "", None
    except Exception:
        return "", None


def _resolve_lmstudio_model_for_worker(configured: str | None) -> str | None:
    """Resolve an actual LMStudio model ID, bypassing smoke/placeholder names."""
    try:
        from agent.llm_integration import _list_lmstudio_candidates, _select_best_lmstudio_model, _prepare_lmstudio_history
        from agent.config import settings as _s

        base_url = str(getattr(_s, "lmstudio_url", "") or "").rstrip("/")
        if not base_url:
            return configured
        candidates = _list_lmstudio_candidates(base_url, timeout=5)
        if not candidates:
            return configured
        if configured and "smoke" not in configured.lower() and "ananta" not in configured.lower():
            from agent.llm_integration import _find_matching_lmstudio_candidate
            matched = _find_matching_lmstudio_candidate(configured, candidates)
            if matched:
                return str(matched.get("id") or configured)
        history = _prepare_lmstudio_history(candidates)
        best = _select_best_lmstudio_model(candidates, history)
        return str((best or candidates[0]).get("id") or "")
    except Exception:
        return configured


def _worker_propose(
    grounded_prompt: str,
    model: str | None,
    *,
    provider: str = "lmstudio",
    limits: SnakeAskLimits | None = None,
    retrieval_profile_trace: dict[str, Any] | None = None,
    allow_profile_routing: bool = True,
    worker_picker: Any = None,
    model_resolver: Any = None,
) -> tuple[str, dict[str, Any]]:
    """Forward prompt to worker /step/propose. Returns (answer, trace)."""
    from agent.services.task_runtime_service import forward_to_worker

    effective_limits = limits or SnakeAskLimits()
    trace: dict[str, Any] = {}
    worker_url, token = (worker_picker or _pick_worker_for_ask)()
    trace["worker_url"] = worker_url
    if not worker_url:
        trace["error"] = "no_online_worker"
        return "", trace

    use_profile_routing = snake_profile_routing_enabled() and allow_profile_routing
    resolved_model = (
        None
        if use_profile_routing
        else (model_resolver or _resolve_lmstudio_model_for_worker)(model)
    )
    trace["model_requested"] = model
    trace["model_resolved"] = resolved_model
    payload: dict[str, Any] = {
        "prompt": grounded_prompt,
        "provider": provider,
        "temperature": 0.3,
        "max_context_chars": effective_limits.context_chars,
        "answer_chars": effective_limits.answer_chars,
        "answer_overflow_policy": effective_limits.answer_overflow_policy,
        "never_truncate_answers": effective_limits.never_truncate_answers,
    }
    if use_profile_routing:
        payload["provider"] = "ananta_profile"
        payload["routing_task_kind"] = resolve_snake_routing_task_kind(grounded_prompt)
        trace["routing_task_kind"] = payload["routing_task_kind"]
        trace["routing_source"] = "hub_snake_profile_policy"
    elif resolved_model:
        payload["model"] = resolved_model
    if effective_limits.max_tokens is not None:
        payload["max_tokens"] = effective_limits.max_tokens
    trace["prompt_chars"] = len(grounded_prompt)
    trace["prompt_preview"] = grounded_prompt[:300]
    trace["limits"] = {
        "context_chars": effective_limits.context_chars,
        "answer_chars": effective_limits.answer_chars,
        "max_tokens": effective_limits.max_tokens,
        "rag_top_k": effective_limits.rag_top_k,
        "answer_overflow_policy": effective_limits.answer_overflow_policy,
        "never_truncate_answers": effective_limits.never_truncate_answers,
    }
    if retrieval_profile_trace:
        analysis_mode = str(retrieval_profile_trace.get("analysis_mode") or "standard")
        trace["full_scan"] = {
            "status": "delegated_to_worker" if analysis_mode == "architecture_full_scan" else "not_requested",
            "analysis_mode": analysis_mode,
            "profile_id": retrieval_profile_trace.get("profile_id"),
            "output_intent": retrieval_profile_trace.get("output_intent"),
            "coverage_policy": retrieval_profile_trace.get("coverage_policy"),
            "plan_id": None,
            "artifact_paths": {},
        }

    try:
        result = forward_to_worker(worker_url, "/step/propose", payload, token=token)
        auth_failed = (
            isinstance(result, dict)
            and int(result.get("http_status") or 0) in {401, 403}
        )
        if auth_failed and (worker_picker is None or worker_picker is _pick_worker_for_ask):
            fallback_url, fallback_token = _pick_worker_for_ask(exclude_urls={worker_url})
            if fallback_url:
                trace["worker_failover"] = {
                    "from": worker_url,
                    "to": fallback_url,
                    "reason": "worker_auth_rejected",
                }
                worker_url, token = fallback_url, fallback_token
                trace["worker_url"] = worker_url
                result = forward_to_worker(worker_url, "/step/propose", payload, token=token)
    except Exception as exc:
        logging.getLogger(__name__).debug("snake-ask worker forward failed: %s", exc)
        trace["error"] = str(exc)[:120]
        return "", trace

    trace["worker_raw_response"] = str(result)[:500] if result else None
    if not isinstance(result, dict):
        trace["error"] = "non_dict_response"
        return "", trace
    data = result.get("data") if isinstance(result.get("data"), dict) else result
    if not isinstance(data, dict):
        trace["error"] = "no_data_field"
        return "", trace
    if str(data.get("status") or "").lower() == "error":
        trace["error"] = str(data.get("message") or "worker_error")[:200]
        trace["http_status"] = data.get("http_status")
        return "", trace
    text = str(data.get("reason") or data.get("raw") or data.get("answer") or "").strip()
    text = _fit_answer_to_chars(
        text,
        limit=effective_limits.answer_chars,
        provider=provider,
        model=resolved_model,
        timeout=min(int(getattr(settings, "http_timeout", 120) or 120), 180),
        overflow_policy=effective_limits.answer_overflow_policy,
        never_truncate=effective_limits.never_truncate_answers,
    )
    trace["answer_chars"] = len(text)
    return text, trace


def _worker_profile_chat(
    messages: list[dict[str, Any]],
    *,
    task_kind: str,
    tools: list[dict[str, Any]] | None = None,
    worker_picker: Any = None,
    timeout_seconds: int | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Delegate a profile-routed chat/tool decision and preserve tool calls."""
    from agent.services.task_runtime_service import forward_to_worker

    trace: dict[str, Any] = {
        "routing_task_kind": task_kind,
        "routing_source": "hub_snake_profile_policy",
    }
    worker_url, token = (worker_picker or _pick_worker_for_ask)()
    trace["worker_url"] = worker_url
    trace["timeout_seconds"] = timeout_seconds
    if not worker_url:
        trace["error"] = "no_online_worker"
        return None, trace
    prompt = "\n\n".join(
        f"[{str(item.get('role') or 'user').upper()}]\n{str(item.get('content') or '')}"
        for item in messages
        if isinstance(item, dict) and item.get("content") is not None
    )
    payload: dict[str, Any] = {
        "prompt": prompt,
        "provider": "ananta_profile",
        "routing_task_kind": task_kind,
    }
    if tools:
        payload["routing_tools"] = tools
    try:
        result = forward_to_worker(
            worker_url,
            "/step/propose",
            payload,
            token=token,
            timeout=timeout_seconds,
        )
        if result is None and token:
            result = forward_to_worker(
                worker_url,
                "/step/propose",
                payload,
                token=None,
                timeout=timeout_seconds,
            )
    except Exception as exc:
        trace["error"] = str(exc)[:160]
        return None, trace
    data = result.get("data") if isinstance(result, dict) and isinstance(result.get("data"), dict) else result
    if not isinstance(data, dict):
        trace["error"] = "invalid_worker_response"
        return None, trace
    inference = data.get("inference") if isinstance(data.get("inference"), dict) else {}
    if inference:
        trace["inference"] = dict(inference)
        trace["effective_provider"] = inference.get("provider")
        trace["effective_model"] = inference.get("model")
        trace["effective_profile_id"] = inference.get("profile_id")
    normalized_calls = []
    for index, call in enumerate(data.get("tool_calls") or []):
        if not isinstance(call, dict):
            continue
        normalized_calls.append({
            "id": call.get("id") or f"snake-tool-{index + 1}",
            "type": "function",
            "function": {
                "name": str(call.get("name") or ""),
                "arguments": json.dumps(call.get("args") or {}),
            },
        })
    worker_error = str(data.get("error") or "").strip()
    if worker_error:
        trace["error"] = worker_error[:200]
        return None, trace
    if not str(data.get("reason") or data.get("raw") or data.get("answer") or "").strip() and not normalized_calls:
        trace["error"] = "empty_worker_response"
        return None, trace
    return {
        "choices": [{
            "message": {
                "content": str(data.get("reason") or data.get("raw") or ""),
                "tool_calls": normalized_calls,
            },
            "finish_reason": "tool_calls" if normalized_calls else "stop",
        }]
    }, trace
