"""CTA-005: Modul/Task-Resolver mit ranked_candidates.

Signalprioritaet ist als Konstante implementiert (Reihenfolge =
architecture.context_signal_priority im Todo). Raum/Zeit-Hints grenzen
nur ein: ohne Material-Evidence oder expliziten Hint entsteht kein
Kandidat ueber dem Schwellwert.
"""
from __future__ import annotations

from typing import Callable

from agent.services.tools._evidence import EVIDENCE_KIND_RETRIEVAL_CHUNK, build_evidence_entry

SIGNAL_PRIORITY = (
    "explicit_task_id_from_event",
    "explicit_module_id_from_event",
    "student_question_terms_and_error_messages",
    "codecompass_material_matches",
    "codecompass_n8n_workflow_matches",
    "zoom_room_group_hint",
    "day_and_time_schedule_hint",
)

WARNING_WEAK_CONTEXT_HINT_ONLY = "weak_context_hint_only"

DEFAULT_CONFIDENCE_THRESHOLD = 0.55


class ModuleTaskResolverService:
    """search_fn(query: str, filters: dict) -> list[dict] mit Feldern
    module_id, task_id, title, score (0..1), file, excerpt."""

    def __init__(
        self,
        search_fn: Callable[[str, dict], list[dict]] | None = None,
        confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    ) -> None:
        self.search_fn = search_fn
        self.confidence_threshold = confidence_threshold

    def resolve(self, *, event: dict, detection: dict, hints: dict) -> dict:
        candidates: list[dict] = []
        warnings: list[str] = []
        hint_refs = list(hints.get("ranked_context_hints") or [])

        explicit_task = str(event.get("task_id_hint") or "").strip()
        explicit_module = str(event.get("module_id_hint") or "").strip()
        if explicit_task:
            # Expliziter Hint gewinnt gegen alle semantischen Treffer.
            candidates.append({
                "module_id": explicit_module or None,
                "task_id": explicit_task,
                "score": 1.0,
                "signals": ["explicit_task_id_from_event"],
                "evidence_refs": [],
                "context_hint_refs": hint_refs,
            })

        material_matches = self._search_materials(event, detection, hints)
        for match in material_matches:
            evidence, _ = build_evidence_entry(
                kind=EVIDENCE_KIND_RETRIEVAL_CHUNK,
                path=str(match.get("file") or ""),
                excerpt=str(match.get("excerpt") or match.get("title") or ""),
                score=float(match.get("score") or 0.0),
                source="classroom.module_task_resolver",
            )
            score = max(0.0, min(1.0, float(match.get("score") or 0.0)))
            signals = ["codecompass_material_matches", "student_question_terms_and_error_messages"]
            # Raum/Zeit stuetzen einen Materialtreffer nur leicht.
            room_scope = (hints.get("retrieval_filters") or {}).get("module_scope")
            if room_scope and str(match.get("module_id") or "") == str(room_scope):
                score = min(1.0, score + 0.1)
                signals.append("zoom_room_group_hint")
            candidates.append({
                "module_id": match.get("module_id"),
                "task_id": match.get("task_id"),
                "title": match.get("title"),
                "score": round(score, 4),
                "signals": signals,
                "evidence_refs": [evidence],
                "context_hint_refs": hint_refs,
            })

        # Kandidaten ohne Evidence UND ohne expliziten Hint sind verboten.
        candidates = [c for c in candidates if c["evidence_refs"] or "explicit_task_id_from_event" in c["signals"]]
        candidates.sort(key=lambda c: (-c["score"], str(c.get("task_id") or "")))
        candidates = self._deduplicate(candidates)

        confident = [c for c in candidates if c["score"] >= self.confidence_threshold]
        if not confident:
            if hint_refs:
                warnings.append(WARNING_WEAK_CONTEXT_HINT_ONLY)
            return {"ranked_candidates": candidates, "warnings": warnings, "confirmed": None}

        confirmed = confident[0] if (len(confident) == 1 or confident[0]["score"] - confident[1]["score"] >= 0.15) else None
        return {"ranked_candidates": candidates, "warnings": warnings, "confirmed": confirmed}

    # ── intern ───────────────────────────────────────────────────────────

    def _search_materials(self, event: dict, detection: dict, hints: dict) -> list[dict]:
        if self.search_fn is None:
            return []
        terms = " ".join([
            str(event.get("text_segment") or "")[:400],
            " ".join(str(span) for span in (detection.get("evidence_spans") or [])[:3]),
        ]).strip()
        if not terms:
            return []
        filters = dict(hints.get("retrieval_filters") or {})
        try:
            results = self.search_fn(terms, filters)
        except Exception:
            return []
        return [r for r in (results or []) if isinstance(r, dict)]

    @staticmethod
    def _deduplicate(candidates: list[dict]) -> list[dict]:
        seen: set[tuple] = set()
        unique: list[dict] = []
        for candidate in candidates:
            key = (candidate.get("module_id"), candidate.get("task_id"))
            if key in seen:
                continue
            seen.add(key)
            unique.append(candidate)
        return unique
