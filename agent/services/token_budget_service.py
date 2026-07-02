"""TokenBudgetService — T01

Konservative Token-Schätzung und Budget-Gates vor LLM-/Worker-Aufrufen.
Keine LLM-Calls, kein I/O. Pure service.
"""
from __future__ import annotations

import math
import logging
from typing import Any

logger = logging.getLogger(__name__)

# ── Tiktoken helpers ──────────────────────────────────────────────────────────

_TIKTOKEN_AVAILABLE: bool | None = None


def _try_tiktoken_encode(text: str, encoding_name: str = "cl100k_base") -> int | None:
    """Return token count via tiktoken, or None if unavailable."""
    global _TIKTOKEN_AVAILABLE
    if _TIKTOKEN_AVAILABLE is False:
        return None
    try:
        import tiktoken  # type: ignore[import]
        enc = tiktoken.get_encoding(encoding_name)
        _TIKTOKEN_AVAILABLE = True
        return len(enc.encode(text))
    except ImportError:
        _TIKTOKEN_AVAILABLE = False
        return None
    except Exception as exc:
        logger.debug("tiktoken encode failed: %s", exc)
        return None


def _encoding_for_profile(model_profile: Any, provider: str | None, model: str | None) -> str:
    """Select tiktoken encoding name based on profile/provider/model hints."""
    if model_profile is not None:
        strategy = str(getattr(model_profile, "tokenizer_strategy", "") or "")
        if strategy == "tiktoken_llama3":
            return "cl100k_base"  # closest available approximation
        if strategy == "tiktoken_cl100k":
            return "cl100k_base"
    # Model name heuristics
    m = str(model or "").lower()
    p = str(provider or "").lower()
    if "gpt-4" in m or "gpt-3" in m or p == "openai":
        return "cl100k_base"
    return "cl100k_base"  # safe default


# ── Module-level functions ────────────────────────────────────────────────────


def estimate_tokens(
    text: str,
    *,
    model_profile: Any = None,
    provider: str | None = None,
    model: str | None = None,
    chars_per_token: float = 4.0,
    safety_multiplier: float = 1.25,
) -> dict[str, Any]:
    """Estimate token count for `text`.

    Returns a dict with keys:
        tokens, method, safety_multiplier, model, provider, confidence
    """
    if not text:
        return {
            "tokens": 0,
            "method": "empty",
            "safety_multiplier": safety_multiplier,
            "model": model,
            "provider": provider,
            "confidence": "exact",
        }

    encoding_name = _encoding_for_profile(model_profile, provider, model)
    tiktoken_count = _try_tiktoken_encode(text, encoding_name)

    if tiktoken_count is not None:
        # tiktoken gives an accurate base; still apply safety_multiplier
        tokens = math.ceil(tiktoken_count * safety_multiplier)
        return {
            "tokens": tokens,
            "method": "tiktoken",
            "safety_multiplier": safety_multiplier,
            "model": model,
            "provider": provider,
            "confidence": "high",
        }

    # Fallback: chars / chars_per_token * safety_multiplier
    raw = len(text) / max(chars_per_token, 0.1)
    tokens = math.ceil(raw * safety_multiplier)
    return {
        "tokens": tokens,
        "method": "chars_per_token_fallback",
        "safety_multiplier": safety_multiplier,
        "model": model,
        "provider": provider,
        "confidence": "low",
    }


def normalize_usage(
    raw_usage: dict[str, Any],
    *,
    provider: str | None = None,
    model: str | None = None,
    estimated: dict[str, Any] | None = None,
    trace_ref: str | None = None,
) -> dict[str, Any]:
    """Normalize provider-specific usage dicts to a canonical TokenUsageReport.

    Handles:
        - OpenAI format: prompt_tokens, completion_tokens, total_tokens
        - Ollama format: prompt_eval_count, eval_count
        - Anthropic format: input_tokens, output_tokens
    """
    raw_usage = raw_usage if isinstance(raw_usage, dict) else {}
    estimated = estimated if isinstance(estimated, dict) else {}

    actual_prompt: int | None = None
    actual_completion: int | None = None
    actual_total: int | None = None
    reason_code: str | None = None

    # OpenAI
    if "prompt_tokens" in raw_usage or "completion_tokens" in raw_usage:
        actual_prompt = _safe_int(raw_usage.get("prompt_tokens"))
        actual_completion = _safe_int(raw_usage.get("completion_tokens"))
        actual_total = _safe_int(raw_usage.get("total_tokens"))
        if actual_total is None and actual_prompt is not None and actual_completion is not None:
            actual_total = actual_prompt + actual_completion
    # Ollama
    elif "prompt_eval_count" in raw_usage or "eval_count" in raw_usage:
        actual_prompt = _safe_int(raw_usage.get("prompt_eval_count"))
        actual_completion = _safe_int(raw_usage.get("eval_count"))
        if actual_prompt is not None and actual_completion is not None:
            actual_total = actual_prompt + actual_completion
    # Anthropic
    elif "input_tokens" in raw_usage or "output_tokens" in raw_usage:
        actual_prompt = _safe_int(raw_usage.get("input_tokens"))
        actual_completion = _safe_int(raw_usage.get("output_tokens"))
        if actual_prompt is not None and actual_completion is not None:
            actual_total = actual_prompt + actual_completion
    else:
        reason_code = "no_provider_usage_found"

    usage_source = "provider_reported" if actual_prompt is not None else "estimate_only"

    est_prompt = int(estimated.get("tokens", 0)) if estimated else 0
    est_completion = 0
    est_total = est_prompt + est_completion

    return {
        "estimated_prompt_tokens": est_prompt,
        "estimated_completion_tokens": est_completion,
        "estimated_total_tokens": est_total,
        "actual_prompt_tokens": actual_prompt,
        "actual_completion_tokens": actual_completion,
        "actual_total_tokens": actual_total,
        "provider": provider,
        "model": model,
        "tokenizer_method": str(estimated.get("method", "unknown")),
        "usage_source": usage_source,
        "reason_code": reason_code,
        "trace_ref": trace_ref,
    }


# ── Service class ─────────────────────────────────────────────────────────────


class TokenBudgetService:
    """Stateless helper for token estimation, usage normalization, and budget gates."""

    def __init__(self, chars_per_token: float = 4.0, safety_multiplier: float = 1.25) -> None:
        self.chars_per_token = chars_per_token
        self.safety_multiplier = safety_multiplier

    def estimate(
        self,
        text: str,
        *,
        model_profile: Any = None,
        provider: str | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        return estimate_tokens(
            text,
            model_profile=model_profile,
            provider=provider,
            model=model,
            chars_per_token=self.chars_per_token,
            safety_multiplier=self.safety_multiplier,
        )

    def normalize(
        self,
        raw_usage: dict[str, Any],
        *,
        provider: str | None = None,
        model: str | None = None,
        estimated: dict[str, Any] | None = None,
        trace_ref: str | None = None,
    ) -> dict[str, Any]:
        return normalize_usage(
            raw_usage,
            provider=provider,
            model=model,
            estimated=estimated,
            trace_ref=trace_ref,
        )

    def check_budget(self, estimated_tokens: int, max_tokens: int) -> dict[str, Any]:
        """Gate: returns allowed=True/False with reason code.

        Never raises — always returns a controlled dict.
        """
        try:
            estimated_tokens = int(estimated_tokens)
            max_tokens = int(max_tokens)
        except (TypeError, ValueError):
            return {
                "allowed": False,
                "reason_code": "invalid_input",
                "estimated_tokens": 0,
                "max_tokens": 0,
            }

        if max_tokens <= 0:
            return {
                "allowed": True,
                "reason_code": "no_limit",
                "estimated_tokens": estimated_tokens,
                "max_tokens": max_tokens,
            }
        if estimated_tokens > max_tokens:
            return {
                "allowed": False,
                "reason_code": "token_budget_exceeded",
                "estimated_tokens": estimated_tokens,
                "max_tokens": max_tokens,
            }
        return {
            "allowed": True,
            "reason_code": "within_budget",
            "estimated_tokens": estimated_tokens,
            "max_tokens": max_tokens,
        }


# ── Helpers ───────────────────────────────────────────────────────────────────


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
