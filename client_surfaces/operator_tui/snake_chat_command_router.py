"""SCTR-004: SnakeChatCommandRouter — intent classification and route dispatch.

Classifies a user question into a routing decision without blocking the UI thread.
Primary classification uses a fast keyword heuristic; can optionally use a small
LLM call for ambiguous cases when hub is reachable.

Routes:
  "filesystem_read"  → FilesystemReadTool
  "git_read"         → GitReadTool (SCTR-005)
  "todo_read"        → TodoReadTool (SCTR-006)
  "llm_answer"       → /snake/ask (v1/v2/v3)
  "direct_answer"    → inline answer without LLM
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from client_surfaces.operator_tui.snake_chat_routing_memory import SnakeChatRoutingMemory
from client_surfaces.operator_tui.snake_chat_security_policy import (
    SnakeChatSecurityPolicy,
    check_tool_dispatch_allowed,
)


# ── Keyword heuristics ────────────────────────────────────────────────────────

_FS_KEYWORDS = re.compile(
    r'\b(list|zeige?|show|ls|dir|dateien?|files?|ordner|directory|was ist in|'
    r'inhalt von|open|lese?|read|cat|such[e]? datei)\b',
    re.I,
)
_GIT_KEYWORDS = re.compile(
    r'\b(git|commit|branch|diff|log|status|änderungen?|changes?|history|push|pull|merge)\b',
    re.I,
)
_TODO_KEYWORDS = re.compile(
    r'\b(todo|task|aufgabe|ticket|backlog|track|cwfh|amr|epc|sctr|mpm|roadmap|'
    r'offen|pending|done|erledigt|nächste[r]? schritt)\b',
    re.I,
)
_GREETING_KEYWORDS = re.compile(
    r'^(hallo|hi|hey|hello|moin|guten morgen|good morning|wie gehts?|was geht)\??$',
    re.I,
)

# Threshold above which we consider a keyword match confident
_CONFIDENCE_THRESHOLD = 0.6


@dataclass
class RoutingDecision:
    route: str                           # one of the routes above
    confidence: float = 1.0             # 0.0–1.0
    method: str = "keyword"             # "keyword" or "llm_classifier"
    tool_args: dict[str, Any] = field(default_factory=dict)
    latency_ms: float = 0.0
    blocked: bool = False
    block_reason: str | None = None
    output_mode: str = "structured"
    route_source: str = "keyword"
    policy_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "route": self.route,
            "confidence": self.confidence,
            "method": self.method,
            "tool_args": self.tool_args,
            "latency_ms": self.latency_ms,
            "blocked": self.blocked,
            "block_reason": self.block_reason,
            "output_mode": self.output_mode,
            "route_source": self.route_source,
            "policy_reason": self.policy_reason,
        }


class SnakeChatCommandRouter:
    """
    Classifies user messages and returns a RoutingDecision.

    Usage:
        router = SnakeChatCommandRouter(policy=policy)
        decision = router.route("Zeige mir alle Python-Dateien im src/ Ordner")
        # decision.route == "filesystem_read"
    """

    def __init__(
        self,
        policy: SnakeChatSecurityPolicy | None = None,
        enable_llm_classifier: bool = False,
        llm_classifier_timeout: float = 2.0,
        routing_memory: SnakeChatRoutingMemory | None = None,
    ) -> None:
        self._policy = policy or SnakeChatSecurityPolicy()
        self._use_llm = enable_llm_classifier
        self._llm_timeout = llm_classifier_timeout
        self._memory = routing_memory
        self._error_count = 0
        self._last_error_at: float = 0.0

    def route(self, question: str) -> RoutingDecision:
        """Classify the question and return a RoutingDecision."""
        t0 = time.monotonic()
        q = question.strip()

        # Slash-command passthrough — caller handles these before routing
        if q.startswith("/"):
            return RoutingDecision(route="slash_command", confidence=1.0, method="exact")

        # Greeting — answer inline
        if _GREETING_KEYWORDS.match(q):
            return RoutingDecision(
                route="direct_answer",
                confidence=0.95,
                method="keyword",
                output_mode="raw",
                route_source="keyword",
            )

        memory_entry = self._memory.match(q) if self._memory is not None else None
        if memory_entry is not None:
            decision = RoutingDecision(
                route=memory_entry.route,
                confidence=memory_entry.confidence,
                method="memory_pattern",
                tool_args=dict(memory_entry.tool_args),
                output_mode="structured",
                route_source="memory_pattern",
            )
            return self._finalize_decision(decision, t0)

        # Keyword classification
        decision = self._keyword_classify(q)

        # If confidence is low and LLM classifier is enabled and healthy, refine
        if decision.confidence < _CONFIDENCE_THRESHOLD and self._use_llm and self._is_llm_healthy():
            llm_decision = self._llm_classify(q)
            if llm_decision is not None:
                decision = llm_decision

        return self._finalize_decision(decision, t0)

    def learn_safe_pattern(
        self,
        *,
        question: str,
        route: str,
        tool_args: dict[str, Any] | None = None,
    ) -> bool:
        """Store a safe read-only routing pattern when memory is enabled."""
        if self._memory is None:
            return False
        return self._memory.learn(
            question=question,
            route=route,
            tool_args=tool_args,
            policy=self._policy,
        )

    def _finalize_decision(self, decision: RoutingDecision, started_at: float) -> RoutingDecision:
        """Apply policy and telemetry fields to a routing decision."""
        allowed, reason = check_tool_dispatch_allowed(decision.route, policy=self._policy)
        if not allowed:
            decision = RoutingDecision(
                route="llm_answer", confidence=0.5, method="security_fallback",
                blocked=True, block_reason=reason, route_source="blocked_by_policy",
                policy_reason=reason,
            )
        else:
            decision.policy_reason = reason

        decision.latency_ms = round((time.monotonic() - started_at) * 1000, 1)
        return decision

    def _keyword_classify(self, question: str) -> RoutingDecision:
        fs_score = len(_FS_KEYWORDS.findall(question)) * 0.4
        git_score = len(_GIT_KEYWORDS.findall(question)) * 0.4
        todo_score = len(_TODO_KEYWORDS.findall(question)) * 0.35

        scores = {
            "filesystem_read": min(fs_score, 1.0),
            "git_read": min(git_score, 1.0),
            "todo_read": min(todo_score, 1.0),
        }

        best_route = max(scores, key=lambda r: scores[r])
        best_score = scores[best_route]

        if best_score >= _CONFIDENCE_THRESHOLD:
            tool_args = self._extract_tool_args(question, best_route)
            return RoutingDecision(
                route=best_route,
                confidence=best_score,
                method="keyword",
                tool_args=tool_args,
                output_mode="structured",
                route_source="direct_tool",
            )

        return RoutingDecision(
            route="llm_answer",
            confidence=0.5,
            method="keyword_fallback",
            output_mode="summary",
            route_source="rag_or_llm",
        )

    def _extract_tool_args(self, question: str, route: str) -> dict[str, Any]:
        if route == "filesystem_read":
            # Extract a path hint if present
            path_match = re.search(r'(?:in|von|from|unter|at)\s+([^\s,!?]+)', question, re.I)
            if path_match:
                return {"path_hint": path_match.group(1)}
        elif route == "git_read":
            sub_match = re.search(r'\b(log|status|diff|show|blame)\b', question, re.I)
            if sub_match:
                return {"git_subcommand": sub_match.group(1).lower()}
        elif route == "todo_read":
            track_match = re.search(
                r'\b(cwfh|amr|epc|sctr|mpm|aprl|rts|rcfg|roadmap)\b', question, re.I
            )
            if track_match:
                return {"track": track_match.group(1).upper()}
        return {}

    def _is_llm_healthy(self) -> bool:
        if self._error_count > 3 and (time.monotonic() - self._last_error_at) < 60.0:
            return False
        return True

    def _llm_classify(self, question: str) -> RoutingDecision | None:
        """Optional LLM-based intent classification. Returns None on failure."""
        # Stub — real implementation would call the hub /config/llm-generate endpoint
        # with a small prompt and parse the JSON response
        return None

    def record_llm_error(self) -> None:
        self._error_count += 1
        self._last_error_at = time.monotonic()

    def record_llm_recovery(self) -> None:
        self._error_count = 0
