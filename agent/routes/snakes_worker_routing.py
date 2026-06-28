"""Worker routing helpers for snake ask — token auth, worker selection, worker proposal."""

from __future__ import annotations

import logging
import secrets
from typing import Any

from flask import request

from agent.config import settings

from .snakes_chat_helpers import SnakeAskLimits, _fit_answer_to_chars


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


def _pick_worker_for_ask() -> tuple[str, str | None]:
    """Return (worker_url, token) for the first online worker, or ("", None)."""
    try:
        from agent.services.agent_registry_service import get_agent_registry_service
        from agent.services.repository_registry import get_repository_registry

        agents = get_agent_registry_service().get_online_agents()
        if not agents:
            return "", None
        agent = agents[0]
        worker_url = str(getattr(agent, "url", "") or "").strip()
        if not worker_url:
            return "", None
        token: str | None = None
        try:
            db_agent = get_repository_registry().agent_repo.get_by_url(worker_url)
            token = str(getattr(db_agent, "token", "") or "").strip() or None
        except Exception:
            pass
        return worker_url, token
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

    resolved_model = (model_resolver or _resolve_lmstudio_model_for_worker)(model)
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
    if resolved_model:
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
        if result is None and token:
            result = forward_to_worker(worker_url, "/step/propose", payload, token=None)
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
