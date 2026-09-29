"""LLM call-profile telemetry entries and provider result text/usage extraction."""

from typing import Any


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
