"""APMCO-005: Deterministic decision engine — answer simple queries without LLM.

Supported answer types:
  file_list, grep_matches, symbol_locations, domain_map_summary,
  graph_neighbors, index_status, cannot_answer_without_context.

No-LLM answers must:
- Contain evidence refs (paths, line numbers, scores).
- Be marked ``deterministic: true``.
- Never make negative claims if no evidence is present.
- Not be generated when ``allow_no_llm_answers=False``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from agent.services.pre_model_context_config import (
    DETERMINISTIC_TASK_KINDS,
    HIGH_EVIDENCE_TASK_KINDS,
    TASK_GENERIC_CHAT,
    TASK_ARCHITECTURE,
    TASK_EXPLANATION,
)

ANSWER_FILE_LIST = "file_list"
ANSWER_GREP_MATCHES = "grep_matches"
ANSWER_SYMBOL_LOCATIONS = "symbol_locations"
ANSWER_DOMAIN_MAP = "domain_map_summary"
ANSWER_GRAPH_NEIGHBORS = "graph_neighbors"
ANSWER_INDEX_STATUS = "index_status"
ANSWER_CANNOT = "cannot_answer_without_context"
ANSWER_NEEDS_LLM = "needs_llm"

_NEEDS_LLM_TASK_KINDS = frozenset({TASK_GENERIC_CHAT, TASK_ARCHITECTURE, TASK_EXPLANATION})


@dataclass
class DeterministicAnswer:
    answer_type: str
    text: str
    evidence: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    deterministic: bool = True
    context_evidence_missing: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "answer_type": self.answer_type,
            "text": self.text,
            "evidence": self.evidence,
            "warnings": self.warnings,
            "deterministic": self.deterministic,
            "context_evidence_missing": self.context_evidence_missing,
        }


class DeterministicDecisionEngine:
    """Attempts to answer a query without invoking an LLM.

    Parameters
    ----------
    allow_no_llm_answers:
        Global switch. When ``False``, ``decide()`` always returns
        ``ANSWER_NEEDS_LLM``.
    deterministic_only:
        When ``True``, never falls back to LLM — returns ``ANSWER_CANNOT``
        instead of ``ANSWER_NEEDS_LLM`` when evidence is insufficient.
    """

    def __init__(
        self,
        *,
        allow_no_llm_answers: bool = True,
        deterministic_only: bool = False,
    ) -> None:
        self._allow = allow_no_llm_answers
        self._det_only = deterministic_only

    def decide(
        self,
        *,
        task_text: str,
        task_kind: str,
        candidates: list[dict[str, Any]],
        index_status: dict[str, Any] | None = None,
    ) -> DeterministicAnswer:
        """Return the best deterministic answer, or signal that LLM is needed."""
        if not self._allow and not self._det_only:
            return DeterministicAnswer(
                answer_type=ANSWER_NEEDS_LLM,
                text="",
                deterministic=False,
            )

        # Tasks that inherently need generative answers
        if task_kind in _NEEDS_LLM_TASK_KINDS and not self._det_only:
            return DeterministicAnswer(
                answer_type=ANSWER_NEEDS_LLM,
                text="",
                deterministic=False,
            )

        # Security / bugfix without evidence → context_evidence_missing warning
        if task_kind in HIGH_EVIDENCE_TASK_KINDS and not candidates:
            return DeterministicAnswer(
                answer_type=ANSWER_CANNOT if self._det_only else ANSWER_NEEDS_LLM,
                text=(
                    "Keine belegbaren Informationen im Index gefunden. "
                    "Eine Antwort ohne Evidence könnte falsche Annahmen erzeugen."
                )
                if self._det_only
                else "",
                warnings=["context_evidence_missing"],
                context_evidence_missing=True,
                deterministic=self._det_only,
            )

        # Navigation / simple lookup with candidates
        if task_kind in DETERMINISTIC_TASK_KINDS:
            return self._answer_navigation(task_text, candidates)

        # Symbol lookup pattern
        if _is_symbol_query(task_text) and candidates:
            return self._answer_symbol(task_text, candidates)

        # grep-style pattern
        if _is_grep_query(task_text) and candidates:
            return self._answer_grep(task_text, candidates)

        if not candidates:
            if self._det_only:
                return DeterministicAnswer(
                    answer_type=ANSWER_CANNOT,
                    text="Keine belegbaren Informationen verfügbar. Nicht belegbar mit aktuellem Kontext.",
                    warnings=["context_evidence_missing"],
                    context_evidence_missing=True,
                )
            return DeterministicAnswer(
                answer_type=ANSWER_NEEDS_LLM,
                text="",
                deterministic=False,
            )

        if self._det_only:
            return self._answer_file_list(candidates)

        return DeterministicAnswer(
            answer_type=ANSWER_NEEDS_LLM,
            text="",
            deterministic=False,
        )

    # ── Answer builders ───────────────────────────────────────────────────────

    def _answer_navigation(
        self, task_text: str, candidates: list[dict[str, Any]]
    ) -> DeterministicAnswer:
        evidence = [
            {"path": c.get("path", ""), "score": c.get("embedding_score", 0.0)}
            for c in candidates[:10]
        ]
        paths = [c.get("path", "") for c in candidates[:10]]
        text = "Gefundene Dateien:\n" + "\n".join(f"- {p}" for p in paths if p)
        return DeterministicAnswer(
            answer_type=ANSWER_FILE_LIST,
            text=text,
            evidence=evidence,
        )

    def _answer_file_list(self, candidates: list[dict[str, Any]]) -> DeterministicAnswer:
        evidence = [
            {"path": c.get("path", ""), "score": c.get("embedding_score", 0.0)}
            for c in candidates[:20]
        ]
        paths = [c.get("path", "") for c in candidates[:20]]
        text = "Relevante Dateien:\n" + "\n".join(f"- {p}" for p in paths if p)
        return DeterministicAnswer(
            answer_type=ANSWER_FILE_LIST,
            text=text,
            evidence=evidence,
        )

    def _answer_symbol(
        self, task_text: str, candidates: list[dict[str, Any]]
    ) -> DeterministicAnswer:
        evidence = []
        for c in candidates[:15]:
            for sym in (c.get("symbols") or []):
                evidence.append({"path": c.get("path", ""), "symbol": sym})
        text = "Symbol-Fundstellen:\n" + "\n".join(
            f"- {e['symbol']} in {e['path']}" for e in evidence[:15]
        )
        return DeterministicAnswer(
            answer_type=ANSWER_SYMBOL_LOCATIONS,
            text=text,
            evidence=evidence[:15],
        )

    def _answer_grep(
        self, task_text: str, candidates: list[dict[str, Any]]
    ) -> DeterministicAnswer:
        evidence = [
            {"path": c.get("path", ""), "excerpt": (c.get("excerpt") or "")[:200]}
            for c in candidates[:10]
        ]
        text = "Treffer:\n" + "\n".join(
            f"- {e['path']}: {e['excerpt'][:80]}" for e in evidence
        )
        return DeterministicAnswer(
            answer_type=ANSWER_GREP_MATCHES,
            text=text,
            evidence=evidence,
        )


def _is_symbol_query(text: str) -> bool:
    low = text.lower()
    return any(kw in low for kw in (
        "wo wird", "where is", "where does", "wo ist die funktion",
        "symbol", "class", "klasse", "function", "funktion", "method", "methode",
        "definiert", "defined", "declared", "deklariert",
    ))


def _is_grep_query(text: str) -> bool:
    low = text.lower()
    return any(kw in low for kw in (
        "grep", "suche nach", "search for", "alle vorkommen", "all occurrences",
        "find all", "finde alle",
    ))
