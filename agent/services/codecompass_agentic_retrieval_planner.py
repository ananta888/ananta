"""Choose which CodeCompass retrieval signals a query should fire.

This planner only decides signal mix and depth. Execution stays in
``CodeCompassAgenticRetrievalService`` so backends remain substitutable.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from agent.services.codecompass_agentic_retrieval_contract import (
    EXPLICIT_MODES,
    MODE_AUTO,
    MODE_EXACT,
    MODE_GRAPH,
    MODE_HYBRID,
    MODE_VECTOR,
    SIGNAL_EXACT,
    SIGNAL_GRAPH,
    SIGNAL_VECTOR,
    VALID_SIGNALS,
    AgenticRetrievalContractError,
    REASON_INVALID_SIGNALS,
    REASON_UNKNOWN_MODE,
)

_SYMBOL_RE = re.compile(
    r"(?:[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*)|"
    r"(?:[A-Z][A-Za-z0-9]+[A-Z][A-Za-z0-9]+)|"
    r"(?:[A-Za-z_][A-Za-z0-9_]*\(\))|"
    r"(?:/[A-Za-z0-9_.-]+)+\.[A-Za-z0-9]+|"
    r"(?:[A-Za-z0-9_.-]+\.(?:py|ts|tsx|js|java|rs|go|md))"
)
_ARCHITECTURE_RE = re.compile(
    r"\b(architecture|subsystem|component|dependenc(?:y|ies)|module|"
    r"hierarchy|system design|layer|bounded context)\b",
    re.I,
)


def _ordered_unique(values: Iterable[str]) -> list[str]:
    seen: list[str] = []
    for value in values:
        token = str(value or "").strip().lower()
        if token in VALID_SIGNALS and token not in seen:
            seen.append(token)
    return seen


def looks_like_symbol_or_path(query: str) -> bool:
    text = str(query or "").strip()
    if not text:
        return False
    if "/" in text or "\\" in text:
        return True
    return bool(_SYMBOL_RE.search(text))


def looks_like_architecture_question(query: str) -> bool:
    return bool(_ARCHITECTURE_RE.search(str(query or "")))


class CodeCompassAgenticRetrievalPlanner:
    """Deterministic signal planner for the agentic retrieval contract."""

    def plan(
        self,
        *,
        query: str,
        mode: str = MODE_AUTO,
        requested_signals: list[str] | None = None,
        allowed_signals: Iterable[str] | None = None,
        task_kind: str | None = None,
    ) -> dict[str, Any]:
        resolved_mode = str(mode or MODE_AUTO).strip().lower() or MODE_AUTO
        if resolved_mode not in {MODE_AUTO, *EXPLICIT_MODES}:
            raise AgenticRetrievalContractError(REASON_UNKNOWN_MODE)

        requested = _ordered_unique(requested_signals or [])
        if requested_signals and not requested:
            raise AgenticRetrievalContractError(REASON_INVALID_SIGNALS)

        if allowed_signals is None:
            allowed = [SIGNAL_EXACT, SIGNAL_GRAPH, SIGNAL_VECTOR]
        else:
            allowed = _ordered_unique(allowed_signals)
            if not allowed:
                raise AgenticRetrievalContractError("empty_scope")

        base_mode, base_signals, rationale = self._auto_or_explicit(
            query=query,
            mode=resolved_mode,
            task_kind=task_kind,
        )
        planned = [signal for signal in base_signals if signal in allowed]
        if requested:
            planned = [signal for signal in planned if signal in requested]
            if not planned:
                # Requested signals may only narrow; empty intersection is a
                # typed client error, not a silent widening to other engines.
                raise AgenticRetrievalContractError(REASON_INVALID_SIGNALS)

        if not planned:
            planned = [signal for signal in (SIGNAL_EXACT,) if signal in allowed]
            rationale = "fallback_exact_within_allowed_signals"

        effective_mode = base_mode
        if resolved_mode == MODE_AUTO:
            if planned == [SIGNAL_VECTOR]:
                effective_mode = MODE_VECTOR
            elif planned == [SIGNAL_EXACT]:
                effective_mode = MODE_EXACT
            elif planned == [SIGNAL_GRAPH]:
                effective_mode = MODE_GRAPH
            elif set(planned) == {SIGNAL_EXACT, SIGNAL_GRAPH, SIGNAL_VECTOR}:
                effective_mode = MODE_HYBRID
            else:
                effective_mode = MODE_HYBRID

        return {
            "mode": effective_mode,
            "signals": planned,
            "rationale": rationale,
            "requested_signals": requested,
            "allowed_signals": allowed,
        }

    def _auto_or_explicit(
        self,
        *,
        query: str,
        mode: str,
        task_kind: str | None,
    ) -> tuple[str, list[str], str]:
        if mode == MODE_VECTOR:
            return MODE_VECTOR, [SIGNAL_VECTOR], "explicit_vector_only"
        if mode == MODE_EXACT:
            return MODE_EXACT, [SIGNAL_EXACT], "explicit_exact_only"
        if mode == MODE_GRAPH:
            return MODE_GRAPH, [SIGNAL_GRAPH], "explicit_graph_only"
        if mode == MODE_HYBRID:
            return (
                MODE_HYBRID,
                [SIGNAL_EXACT, SIGNAL_GRAPH, SIGNAL_VECTOR],
                "explicit_hybrid",
            )

        kind = str(task_kind or "").strip().lower()
        if looks_like_symbol_or_path(query):
            return MODE_EXACT, [SIGNAL_EXACT, SIGNAL_GRAPH], "symbol_or_path_query"
        if looks_like_architecture_question(query) or kind == "architecture_question":
            return (
                MODE_HYBRID,
                [SIGNAL_GRAPH, SIGNAL_EXACT, SIGNAL_VECTOR],
                "architecture_query",
            )
        tokens = [part for part in re.split(r"\s+", str(query or "").strip()) if part]
        if len(tokens) <= 2:
            return MODE_HYBRID, [SIGNAL_VECTOR, SIGNAL_EXACT], "short_conceptual_query"
        return (
            MODE_HYBRID,
            [SIGNAL_EXACT, SIGNAL_VECTOR, SIGNAL_GRAPH],
            "default_hybrid",
        )


def plan_from_request(
    request: Mapping[str, Any],
    *,
    allowed_signals: Iterable[str] | None = None,
) -> dict[str, Any]:
    planner = CodeCompassAgenticRetrievalPlanner()
    return planner.plan(
        query=str(request.get("query") or ""),
        mode=str(request.get("mode") or MODE_AUTO),
        requested_signals=list(request.get("requested_signals") or []),
        allowed_signals=allowed_signals,
        task_kind=str(request.get("task_kind") or ""),
    )
