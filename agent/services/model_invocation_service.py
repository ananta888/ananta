"""ModelInvocationService — real LLM HTTP calls for propose strategies. FA-T021."""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

# LM Studio handles one inference at a time. Concurrent requests return empty content
# because the second request is queued/dropped. This lock serializes all LM Studio calls
# across threads (Flask runs with threaded=True, so planning and propose can overlap).
_LMSTUDIO_INFERENCE_LOCK = threading.Lock()

# Module-level resolver cache — loaded lazily, shared across calls.
_PROFILE_RESOLVER_CACHE: Any = None
_PROFILE_RESOLVER_LOCK = threading.Lock()


class LLMUnavailableError(Exception):
    """LLM provider not reachable, timed out, or returned server error."""

    def __init__(self, message: str, *, llm_call_profile: list[dict[str, Any]] | None = None):
        super().__init__(message)
        self.llm_call_profile = list(llm_call_profile or [])


class ModelInvocationService:
    """LLM invocation via OpenAI-compatible chat/completions endpoint."""

    @staticmethod
    def _build_llm_call_profile_entry(
        *,
        name: str,
        backend: str,
        provider: str | None,
        model: str | None,
        success: bool,
        started_at: float | None,
        ended_at: float | None,
        usage: dict[str, Any] | None = None,
        source: str = "model_invocation_service",
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
            "name": name,
            "backend": backend,
            "provider": str(provider or "").strip() or None,
            "model": str(model or "").strip() or None,
            "success": bool(success),
            "latency_ms": latency_ms,
            "prompt_tokens": int(prompt_tokens) if isinstance(prompt_tokens, int) else None,
            "completion_tokens": int(completion_tokens) if isinstance(completion_tokens, int) else None,
            "total_tokens": int(total_tokens) if isinstance(total_tokens, int) else None,
            "source": source,
            "estimated": bool(estimated),
            "error_type": str(error_type or "").strip() or None,
            "error_message": str(error_message or "").strip() or None,
            "started_at": float(started_at) if started_at is not None else None,
            "ended_at": float(ended_at) if ended_at is not None else None,
        }

    @classmethod
    def _raise_llm_error(
        cls,
        *,
        message: str,
        name: str,
        backend: str,
        provider: str | None,
        model: str | None,
        started_at: float | None,
        error_type: str,
    ) -> None:
        ended_at = time.time()
        entry = cls._build_llm_call_profile_entry(
            name=name,
            backend=backend,
            provider=provider,
            model=model,
            success=False,
            started_at=started_at,
            ended_at=ended_at,
            usage=None,
            source="model_invocation_service",
            estimated=False,
            error_type=error_type,
            error_message=message,
        )
        raise LLMUnavailableError(message, llm_call_profile=[entry])

    @classmethod
    def _get_settings(cls):
        from agent.config import settings
        return settings

    @classmethod
    def _get_resolver(cls):
        """Lazily load ModelProfileResolver from the configured profiles path.
        Returns None if no profiles file is configured or parseable."""
        global _PROFILE_RESOLVER_CACHE
        if _PROFILE_RESOLVER_CACHE is not None:
            return _PROFILE_RESOLVER_CACHE
        with _PROFILE_RESOLVER_LOCK:
            if _PROFILE_RESOLVER_CACHE is not None:
                return _PROFILE_RESOLVER_CACHE
            try:
                import os
                from pathlib import Path
                from agent.services.model_profile_loader import ModelProfileLoader
                from agent.services.model_profile_resolver import ModelProfileResolver, SecurityPolicyChecker, RoutingRules
                from agent.services.model_master_default_service import get_global_master_default_service

                profiles_path_env = os.environ.get("MODEL_PROFILES_PATH", "").strip()
                if not profiles_path_env:
                    return None
                path = Path(profiles_path_env)
                if not path.exists():
                    logger.info("model_invocation: MODEL_PROFILES_PATH %s not found", path)
                    return None
                result = ModelProfileLoader().load_file(path)
                if not result.ok or not result.profiles:
                    logger.warning("model_invocation: profile load errors: %s", result.errors)
                    return None

                logger.info("model_invocation: loaded %d profiles from %s", len(result.profiles), path)

                # Load routing rules
                routing_rules = RoutingRules()
                routing_path_str = (
                    os.environ.get("MODEL_ROUTING_PATH", "").strip()
                    or os.environ.get("ANANTA_MODEL_ROUTING_PATH", "").strip()
                )
                if routing_path_str:
                    rp = Path(routing_path_str)
                    if rp.exists():
                        try:
                            raw_routing = json.loads(rp.read_text(encoding="utf-8"))
                            if isinstance(raw_routing, dict):
                                routing_rules = RoutingRules.from_dict(raw_routing)
                                logger.info("model_invocation: loaded routing rules from %s", rp)
                        except Exception as exc:
                            logger.warning("model_invocation: routing parse failed for %s: %s — using empty rules", rp, exc)
                    else:
                        logger.info("model_invocation: MODEL_ROUTING_PATH %s not found — using empty rules", rp)
                else:
                    logger.debug("model_invocation: no MODEL_ROUTING_PATH set — using empty rules")

                # Load global master default
                master_svc = get_global_master_default_service()
                master_profile = master_svc.get_master_profile()

                resolver = ModelProfileResolver(
                    profiles=result.profiles,
                    security_policy=SecurityPolicyChecker(),
                    routing_rules=routing_rules,
                    master_default_profile=master_profile,
                )
                _PROFILE_RESOLVER_CACHE = resolver

                if master_profile:
                    logger.info(
                        "model_invocation: global master default active: provider=%s model=%s",
                        master_profile.provider_id, master_profile.model,
                    )

                # AMR-020: log deprecation warning if legacy env vars are still set
                import os as _os
                if _os.environ.get("DEFAULT_PROVIDER") or _os.environ.get("DEFAULT_MODEL"):
                    logger.warning(
                        "model_invocation: DEFAULT_PROVIDER/DEFAULT_MODEL env vars are set but "
                        "MODEL_PROFILES_PATH is also configured. Profile-based routing takes "
                        "precedence. Remove DEFAULT_PROVIDER/DEFAULT_MODEL to silence this warning."
                    )
                return resolver
            except Exception as exc:
                logger.warning("model_invocation: resolver init failed: %s", exc)
                return None

    @classmethod
    def _provider_info_from_profile(cls, profile) -> tuple[str, str, str | None]:
        """Convert a ModelProfile to (provider_label, url, api_key)."""
        import os
        s = cls._get_settings()
        provider = profile.provider_id.lower()
        base_url = (profile.base_url or "").rstrip("/")
        api_key: str | None = None

        if profile.api_key_env:
            api_key = os.environ.get(profile.api_key_env) or None

        if not base_url:
            if provider in ("lmstudio", "lm_studio"):
                base_url = s.lmstudio_url.rstrip("/")
            elif provider == "ollama":
                base_url = s.ollama_url.rstrip("/")
                if "/api/generate" in base_url:
                    base_url = base_url.replace("/api/generate", "")
                if not base_url.endswith("/v1"):
                    base_url = base_url + "/v1"
            elif provider == "openai":
                base_url = "https://api.openai.com/v1"
                if not api_key:
                    api_key = s.openai_api_key
            elif provider == "openrouter":
                base_url = "https://openrouter.ai/api/v1"
            elif provider == "mock":
                base_url = s.mock_url.rstrip("/") + "/v1"

        if not base_url.endswith("/chat/completions"):
            if not base_url.endswith("/v1"):
                # already has path like /v1/chat/completions — leave as-is if it has /chat
                if "/chat" not in base_url:
                    base_url = base_url + "/chat/completions"
                # else trust the URL
            else:
                base_url = base_url + "/chat/completions"

        return provider, base_url, api_key

    @classmethod
    def _provider_info(cls) -> tuple[str, str, str | None]:
        """Return (provider_label, chat_completions_url, api_key)."""
        s = cls._get_settings()
        provider = (s.default_provider or "lmstudio").strip().lower()

        if provider in ("lmstudio", "lm_studio"):
            base = s.lmstudio_url.rstrip("/")
            # lmstudio_url may point to /v1 base or /v1/chat/completions
            if not base.endswith("/chat/completions"):
                base = base + "/chat/completions"
            return "lmstudio", base, None

        if provider == "ollama":
            base = s.ollama_url.rstrip("/")
            # ollama_url defaults to /api/generate; use OpenAI-compat endpoint
            if "/api/generate" in base:
                base = base.replace("/api/generate", "")
            if not base.endswith("/chat/completions"):
                if not base.endswith("/v1"):
                    base = base + "/v1"
                base = base + "/chat/completions"
            return "ollama", base, None

        if provider == "openai":
            url = s.openai_url.rstrip("/")
            if not url.endswith("/chat/completions"):
                url = url + "/chat/completions"
            return "openai", url, s.openai_api_key

        if provider == "mock":
            return "mock", s.mock_url.rstrip("/") + "/v1/chat/completions", None

        # Generic OpenAI-compatible fallback
        base = s.lmstudio_url.rstrip("/")
        if not base.endswith("/chat/completions"):
            base = base + "/chat/completions"
        return provider, base, None

    @staticmethod
    def _normalize_openai_tools(tools: list | None) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for item in list(tools or []):
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type") or "").strip().lower()
            if item_type == "function" and isinstance(item.get("function"), dict):
                normalized.append(item)
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                fn = item.get("function") if isinstance(item.get("function"), dict) else {}
                name = str(fn.get("name") or "").strip()
            if not name:
                continue
            description = str(item.get("description") or "").strip()
            if not description:
                fn = item.get("function") if isinstance(item.get("function"), dict) else {}
                description = str(fn.get("description") or "").strip()
            parameters = item.get("parameters")
            if not isinstance(parameters, dict):
                fn = item.get("function") if isinstance(item.get("function"), dict) else {}
                parameters = fn.get("parameters")
            if not isinstance(parameters, dict):
                parameters = {"type": "object", "properties": {}}
            normalized.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": description,
                        "parameters": parameters,
                    },
                }
            )
        return normalized

    @staticmethod
    def _tool_calling_mode(profile: Any | None) -> str:
        if profile is None:
            return "native_tools"
        mode = str(getattr(profile, "tool_calling_mode", "") or "").strip()
        if mode:
            return mode
        return "native_tools" if bool(getattr(profile, "supports_tools", False)) else "none"

    @classmethod
    def _messages_for_tool_mode(
        cls,
        messages: list[dict],
        *,
        tools: list | None,
        tool_calling_mode: str,
    ) -> tuple[list[dict], bool]:
        normalized_tools = cls._normalize_openai_tools(tools)
        if not normalized_tools:
            return messages, False
        if tool_calling_mode in {"native_tools", "both"}:
            return messages, True
        if tool_calling_mode != "prompt_json":
            return messages, False
        tool_contract = {
            "response_schema": {
                "type": "object",
                "required": ["tool", "args"],
                "properties": {
                    "tool": {"type": "string"},
                    "args": {"type": "object"},
                    "confidence": {"type": "number"},
                    "reasoning_summary": {"type": "string"},
                },
            },
            "allowed_tools": [
                {
                    "name": item["function"]["name"],
                    "description": item["function"].get("description") or "",
                    "parameters": item["function"].get("parameters") or {"type": "object", "properties": {}},
                }
                for item in normalized_tools
            ],
        }
        system_msg = {
            "role": "system",
            "content": (
                "Return exactly one JSON object selecting a tool. Do not call tools directly. "
                f"Tool contract: {json.dumps(tool_contract, sort_keys=True)}"
            ),
        }
        return [system_msg] + [m for m in messages if isinstance(m, dict)], False

    @staticmethod
    def _blocked_candidates_as_dict(blocked: list[tuple[str, str]] | None) -> list[dict[str, Any]]:
        return [{"profile_id": pid, "reason": reason} for pid, reason in list(blocked or [])]

    @classmethod
    def _fallback_error_type(cls, exc: LLMUnavailableError) -> str:
        profile = list(getattr(exc, "llm_call_profile", []) or [])
        if profile and isinstance(profile[-1], dict):
            return str(profile[-1].get("error_type") or "unknown")
        return "unknown"

    @staticmethod
    def _finalize_trace_error(prompt_trace: Any, trace_svc: Any, error_type: str, error_message: str) -> None:
        if prompt_trace is None or trace_svc is None:
            return
        try:
            finalized = trace_svc.finalize_trace(
                prompt_trace,
                success=False,
                error_type=error_type,
                error_message=error_message,
            )
            trace_svc.store(finalized)
        except Exception:
            pass

    @classmethod
    def _make_single_chat_call(
        cls,
        messages: list[dict],
        *,
        tools: list | None,
        response_format: dict | None,
        attempt: dict[str, Any],
        resolution_info: dict[str, Any],
    ) -> dict:
        provider = attempt["provider"]
        url = attempt["url"]
        api_key = attempt.get("api_key")
        effective_model = attempt["model"]
        timeout = int(attempt.get("timeout") or 120)
        profile = attempt.get("profile")
        tool_mode = cls._tool_calling_mode(profile)
        outgoing_messages, send_native_tools = cls._messages_for_tool_mode(
            messages,
            tools=tools,
            tool_calling_mode=tool_mode,
        )

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        body: dict[str, Any] = {"model": effective_model, "messages": outgoing_messages}
        if tools and send_native_tools:
            body["tools"] = cls._normalize_openai_tools(tools)
            body["tool_choice"] = "auto"
        if response_format:
            body["response_format"] = response_format

        prompt_trace = None
        trace_svc = None
        try:
            from flask import g, has_app_context
            if has_app_context():
                from agent.services.prompt_trace_service import get_prompt_trace_service

                trace_goal_id = str(getattr(g, "llm_goal_id", "") or "").strip() or None
                trace_task_id = str(getattr(g, "llm_task_id", "") or "").strip() or None
                trace_svc = get_prompt_trace_service()
                prompt_trace = trace_svc.create_trace(
                    goal_id=trace_goal_id,
                    task_id=trace_task_id,
                    source_component="model_invocation_service",
                    provider=provider,
                    transport_provider=provider,
                    model=effective_model,
                    endpoint_kind="chat_completions",
                    request_kind="propose",
                    messages=[m for m in list(outgoing_messages or []) if isinstance(m, dict)],
                    tools=cls._normalize_openai_tools(tools) if send_native_tools else [],
                    llm_scope="task",
                    sensitivity_level="internal",
                )
        except Exception:
            prompt_trace = None
            trace_svc = None

        started_at = time.time()
        lock = _LMSTUDIO_INFERENCE_LOCK if provider in ("lmstudio", "lm_studio") else None
        if lock is not None:
            if not lock.acquire(blocking=False):
                logger.debug("LM Studio busy - waiting for inference lock (provider=%s)", provider)
                lock.acquire()
        try:
            try:
                resp = requests.post(url, json=body, headers=headers, timeout=timeout)
            except requests.exceptions.ConnectionError as exc:
                cls._finalize_trace_error(prompt_trace, trace_svc, "connection_error", f"{exc}")
                cls._raise_llm_error(
                    message=f"llm_connection_failed: {exc}",
                    name="chat_completions",
                    backend="llm_api",
                    provider=provider,
                    model=effective_model,
                    started_at=started_at,
                    error_type="connection_error",
                )
            except requests.exceptions.Timeout as exc:
                cls._finalize_trace_error(prompt_trace, trace_svc, "timeout", f"{exc}")
                cls._raise_llm_error(
                    message=f"llm_timeout: {exc}",
                    name="chat_completions",
                    backend="llm_api",
                    provider=provider,
                    model=effective_model,
                    started_at=started_at,
                    error_type="timeout",
                )

            if resp.status_code >= 500:
                cls._finalize_trace_error(prompt_trace, trace_svc, "server_error", f"HTTP {resp.status_code}")
                cls._raise_llm_error(
                    message=f"llm_server_error: HTTP {resp.status_code}",
                    name="chat_completions",
                    backend="llm_api",
                    provider=provider,
                    model=effective_model,
                    started_at=started_at,
                    error_type="server_error",
                )
            if resp.status_code >= 400:
                cls._finalize_trace_error(prompt_trace, trace_svc, "client_error", f"HTTP {resp.status_code} {resp.text[:200]}")
                cls._raise_llm_error(
                    message=f"llm_client_error: HTTP {resp.status_code} {resp.text[:200]}",
                    name="chat_completions",
                    backend="llm_api",
                    provider=provider,
                    model=effective_model,
                    started_at=started_at,
                    error_type="client_error",
                )

            try:
                payload = resp.json()
            except Exception as exc:
                cls._finalize_trace_error(prompt_trace, trace_svc, "invalid_json_response", f"{exc}")
                cls._raise_llm_error(
                    message=f"llm_invalid_json_response: {exc}",
                    name="chat_completions",
                    backend="llm_api",
                    provider=provider,
                    model=effective_model,
                    started_at=started_at,
                    error_type="invalid_json_response",
                )

            ended_at = time.time()
            usage = payload.get("usage") if isinstance(payload, dict) else {}
            if prompt_trace is not None and trace_svc is not None:
                try:
                    msg_content = ""
                    if isinstance(payload, dict):
                        first = ((payload.get("choices") or [{}])[0] or {})
                        msg = first.get("message") if isinstance(first, dict) else {}
                        msg_content = str((msg or {}).get("content") or "")
                    finalized = trace_svc.finalize_trace(
                        prompt_trace,
                        success=True,
                        response_text=msg_content or None,
                        usage=usage if isinstance(usage, dict) else None,
                    )
                    trace_svc.store(finalized)
                except Exception:
                    pass
            call_entry = cls._build_llm_call_profile_entry(
                name="chat_completions",
                backend="llm_api",
                provider=provider,
                model=effective_model,
                success=True,
                started_at=started_at,
                ended_at=ended_at,
                usage=usage if isinstance(usage, dict) else None,
            )
            call_entry["profile_id"] = getattr(profile, "profile_id", None)
            call_entry["tool_calling_mode"] = tool_mode
            if isinstance(payload, dict):
                meta = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
                if prompt_trace is not None:
                    meta["prompt_trace_id"] = str(getattr(prompt_trace, "trace_id", "") or "")
                meta["llm_call_profile"] = list(meta.get("llm_call_profile") or []) + [call_entry]
                if resolution_info:
                    meta["resolution_info"] = dict(resolution_info)
                payload["metadata"] = meta
            return payload
        finally:
            if lock is not None:
                lock.release()

    @classmethod
    def _make_chat_call(
        cls,
        messages: list[dict],
        *,
        tools: list | None = None,
        response_format: dict | None = None,
        model: str | None = None,
        timeout: int | None = None,
        routing_ctx: Any = None,
    ) -> dict:
        resolver = None
        resolution_result = None
        candidate_profiles: list[Any] = []
        resolution_info: dict[str, Any] = {}
        if routing_ctx is not None:
            try:
                resolver = cls._get_resolver()
                if resolver is not None:
                    resolution_result, candidate_profiles = resolver.resolve_candidate_chain(routing_ctx)
                    if resolution_result.ok:
                        resolution_info = {
                            "profile_id": resolution_result.profile.profile_id,
                            "resolution_source": resolution_result.final_source,
                            "resolution_rank": resolution_result.final_rank,
                            "candidate_chain": [p.profile_id for p in candidate_profiles],
                        }
                    else:
                        resolution_info = {
                            "resolution_source": "none",
                            "resolution_fallback_reason": "no_profile_resolved",
                            "blocked_candidates": [r for _, r in resolution_result.blocked_candidates],
                        }
            except Exception as exc:
                resolution_info = {
                    "resolution_source": "error",
                    "resolution_fallback_reason": f"resolver_error:{exc}",
                }
                logger.warning("model_invocation: resolver failed: %s - using legacy path", exc)

        attempts: list[dict[str, Any]] = []
        if candidate_profiles:
            for profile in candidate_profiles:
                provider, url, api_key = cls._provider_info_from_profile(profile)
                effective_model = profile.model if profile.model and profile.model != "auto" else cls._get_settings().default_model
                attempts.append({
                    "profile": profile,
                    "provider": provider,
                    "url": url,
                    "api_key": api_key,
                    "model": effective_model,
                    "timeout": timeout if timeout is not None else profile.timeout_seconds,
                })
        else:
            provider, url, api_key = cls._provider_info()
            settings = cls._get_settings()
            attempts.append({
                "profile": None,
                "provider": provider,
                "url": url,
                "api_key": api_key,
                "model": model if model and model != "auto" else settings.default_model,
                "timeout": timeout if timeout is not None else int(getattr(settings, "llm_invoke_timeout_seconds", None) or 120),
            })
            resolution_info.setdefault("resolution_source", "legacy_provider_info")

        from agent.services.model_fallback_policy_service import ModelFallbackPolicyService

        call_profile: list[dict[str, Any]] = []
        fallback_decisions: list[dict[str, Any]] = []
        blocked = cls._blocked_candidates_as_dict(getattr(resolution_result, "blocked_candidates", []))
        fallback_policy = ModelFallbackPolicyService(getattr(resolver, "health", None) if resolver is not None else None)

        for index, attempt in enumerate(attempts):
            try:
                payload = cls._make_single_chat_call(
                    messages,
                    tools=tools,
                    response_format=response_format,
                    attempt=attempt,
                    resolution_info=resolution_info,
                )
                if isinstance(payload, dict):
                    meta = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
                    meta["llm_call_profile"] = call_profile + list(meta.get("llm_call_profile") or [])
                    meta["fallback_decisions"] = list(fallback_decisions)
                    if resolution_info:
                        meta["resolution_info"] = dict(resolution_info)
                    payload["metadata"] = meta
                return payload
            except LLMUnavailableError as exc:
                call_profile.extend([entry for entry in list(exc.llm_call_profile or []) if isinstance(entry, dict)])
                next_profile = attempts[index + 1]["profile"] if index + 1 < len(attempts) else None
                decision = fallback_policy.should_fallback(
                    error_type=cls._fallback_error_type(exc),
                    previous_profile=attempt.get("profile"),
                    next_profile=next_profile,
                    blocked_candidates=blocked,
                )
                fallback_decisions.append(decision.as_dict())
                if decision.terminal:
                    raise LLMUnavailableError(str(exc), llm_call_profile=call_profile)
                logger.warning(
                    "model_invocation: fallback %s -> %s trigger=%s",
                    decision.previous_profile_id,
                    decision.next_profile_id,
                    decision.trigger,
                )

        raise LLMUnavailableError("llm_unavailable:no_attempts", llm_call_profile=call_profile)

    @classmethod
    def invoke_with_tools(
        cls, prompt: str, tools: list, model: str | None = None, **kwargs
    ) -> dict:
        """Call LLM with tools= parameter. Returns dict with tool_calls list and content."""
        messages = [{"role": "user", "content": prompt}]
        if kwargs.get("system_prompt"):
            messages = [{"role": "system", "content": kwargs["system_prompt"]}] + messages

        response = cls._make_chat_call(
            messages,
            tools=tools,
            model=model,
            timeout=kwargs.get("timeout"),
            routing_ctx=kwargs.get("routing_ctx"),
        )
        choice = (response.get("choices") or [{}])[0]
        msg = choice.get("message") or {}

        tool_calls = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            raw_args = fn.get("arguments", "{}")
            try:
                parsed_args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            except json.JSONDecodeError:
                parsed_args = {"raw": raw_args}
            tool_calls.append({
                "name": fn.get("name", ""),
                "args": parsed_args,
                "id": tc.get("id"),
            })
        if not tool_calls and msg.get("content"):
            allowed_tools = {
                item["function"]["name"]: item["function"].get("parameters") or {"type": "object", "properties": {}}
                for item in cls._normalize_openai_tools(tools)
                if isinstance(item.get("function"), dict)
            }
            try:
                prompt_json_call = json.loads(msg.get("content") or "{}")
            except Exception:
                prompt_json_call = None
            if isinstance(prompt_json_call, dict):
                tool_name = str(prompt_json_call.get("tool") or "").strip()
                args = prompt_json_call.get("args")
                args_valid = False
                if tool_name in allowed_tools and isinstance(args, dict):
                    try:
                        import jsonschema
                        jsonschema.validate(instance=args, schema=allowed_tools[tool_name])
                        args_valid = True
                    except ImportError:
                        args_valid = True
                    except Exception:
                        args_valid = False
                if args_valid:
                    tool_calls.append({
                        "name": tool_name,
                        "args": args,
                        "id": prompt_json_call.get("id") or f"prompt-json-{len(tool_calls) + 1}",
                        "confidence": prompt_json_call.get("confidence"),
                        "reasoning_summary": prompt_json_call.get("reasoning_summary"),
                    })

        metadata = response.get("metadata") if isinstance(response.get("metadata"), dict) else {}
        return {
            "tool_calls": tool_calls,
            "content": msg.get("content") or "",
            "finish_reason": choice.get("finish_reason"),
            "usage": response.get("usage") if isinstance(response.get("usage"), dict) else {},
            "metadata": metadata,
            "provider": cls._provider_info()[0],
            "model": response.get("model") or model,
        }

    @classmethod
    def invoke_with_json_schema(
        cls, prompt: str, json_schema: dict, model: str | None = None, **kwargs
    ) -> str:
        """Call LLM with response_format=json_object. Returns raw content string."""
        messages = [{"role": "user", "content": prompt}]
        if kwargs.get("system_prompt"):
            messages = [{"role": "system", "content": kwargs["system_prompt"]}] + messages
        response = cls._make_chat_call(
            messages, response_format={"type": "json_object"}, model=model,
            routing_ctx=kwargs.get("routing_ctx"),
        )
        choice = (response.get("choices") or [{}])[0]
        return (choice.get("message") or {}).get("content") or ""

    @classmethod
    def invoke_with_json_schema_result(
        cls, prompt: str, json_schema: dict, model: str | None = None, **kwargs
    ) -> dict[str, Any]:
        """Call LLM with response_format=json_object and keep metadata/usage."""
        messages = [{"role": "user", "content": prompt}]
        if kwargs.get("system_prompt"):
            messages = [{"role": "system", "content": kwargs["system_prompt"]}] + messages
        response = cls._make_chat_call(
            messages,
            response_format={"type": "json_object"},
            model=model,
            timeout=kwargs.get("timeout"),
            routing_ctx=kwargs.get("routing_ctx"),
        )
        choice = (response.get("choices") or [{}])[0]
        msg = choice.get("message") if isinstance(choice, dict) else {}
        metadata = response.get("metadata") if isinstance(response.get("metadata"), dict) else {}
        provider = cls._provider_info()[0]
        return {
            "content": (msg.get("content") or "") if isinstance(msg, dict) else "",
            "finish_reason": choice.get("finish_reason") if isinstance(choice, dict) else None,
            "usage": response.get("usage") if isinstance(response.get("usage"), dict) else {},
            "metadata": metadata,
            "provider": provider,
            "model": response.get("model") or model,
        }

    @classmethod
    def invoke(cls, prompt: str, model: str | None = None, **kwargs) -> str:
        """Plain chat completion. Returns content string."""
        messages = [{"role": "user", "content": prompt}]
        if kwargs.get("system_prompt"):
            messages = [{"role": "system", "content": kwargs["system_prompt"]}] + messages
        response = cls._make_chat_call(
            messages, model=model,
            routing_ctx=kwargs.get("routing_ctx"),
        )
        choice = (response.get("choices") or [{}])[0]
        return (choice.get("message") or {}).get("content") or ""

    @classmethod
    def invoke_result(cls, prompt: str, model: str | None = None, **kwargs) -> dict[str, Any]:
        """Plain chat completion with metadata/usage (additive API)."""
        messages = [{"role": "user", "content": prompt}]
        if kwargs.get("system_prompt"):
            messages = [{"role": "system", "content": kwargs["system_prompt"]}] + messages
        response = cls._make_chat_call(
            messages, model=model, timeout=kwargs.get("timeout"),
            routing_ctx=kwargs.get("routing_ctx"),
        )
        choice = (response.get("choices") or [{}])[0]
        msg = choice.get("message") if isinstance(choice, dict) else {}
        metadata = response.get("metadata") if isinstance(response.get("metadata"), dict) else {}
        provider = cls._provider_info()[0]
        return {
            "content": (msg.get("content") or "") if isinstance(msg, dict) else "",
            "finish_reason": choice.get("finish_reason") if isinstance(choice, dict) else None,
            "usage": response.get("usage") if isinstance(response.get("usage"), dict) else {},
            "metadata": metadata,
            "provider": provider,
            "model": response.get("model") or model,
        }
