"""CTA-006: Antwort-Komposition inkl. Transcript-Fenster und Tokenbudget.

Enthaelt das fruehere CTA-003 (Windowing) als Baustein. Ohne
Material-Evidence entsteht keine Schueler-Antwort, sondern eine
Dozent-Rueckfrage (reason_code no_material_evidence).
"""

from __future__ import annotations

import json
from typing import Callable

REASON_NO_MATERIAL_EVIDENCE = "no_material_evidence"

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "problem_summary": {"type": "string"},
        "student_position": {"type": "string"},
        "next_action_for_teacher": {"type": "string"},
        "answer_for_student": {"type": ["string", "null"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["problem_summary", "student_position", "next_action_for_teacher", "answer_for_student", "confidence"],
    "additionalProperties": False,
}


def _estimate_tokens(text: str) -> int:
    return max(1, len(str(text or "")) // 4)


def build_transcript_window(
    segments: list[dict],
    *,
    question_segment: dict,
    max_tokens: int,
) -> list[dict]:
    """Kontextfenster: eigene fruehere Beitraege zuerst, fremde nur bei
    gleichem Task-Kontext, hartes Tokenbudget."""
    speaker = str(question_segment.get("speaker_label_hash") or "")
    task_hint = str(question_segment.get("task_id_hint") or "")
    question_seq = int(question_segment.get("sequence_no") or 0)

    prior = [
        seg
        for seg in segments
        if int(seg.get("sequence_no") or 0) < question_seq
        and str(seg.get("session_id")) == str(question_segment.get("session_id"))
    ]
    own = [seg for seg in prior if str(seg.get("speaker_label_hash") or "") == speaker]
    others = [
        seg
        for seg in prior
        if str(seg.get("speaker_label_hash") or "") != speaker
        and task_hint
        and str(seg.get("task_id_hint") or "") == task_hint
    ]
    # Eigene Fehlversuche zuerst (juengste zuerst), dann fremde mit
    # gleichem Task-Kontext.
    ordered = sorted(own, key=lambda s: -int(s.get("sequence_no") or 0)) + sorted(
        others, key=lambda s: -int(s.get("sequence_no") or 0)
    )

    window: list[dict] = []
    budget = max(1, int(max_tokens))
    for segment in ordered:
        cost = _estimate_tokens(segment.get("text_segment") or "")
        if cost > budget:
            continue
        window.append(segment)
        budget -= cost
        if budget <= 0:
            break
    return window


class AnswerComposerService:
    def __init__(
        self,
        invoke_json: Callable[..., dict] | None = None,
        max_context_tokens: int = 2000,
    ) -> None:
        self.invoke_json = invoke_json
        self.max_context_tokens = max_context_tokens

    def compose(
        self,
        *,
        question_text: str,
        window_segments: list[dict],
        candidates: list[dict],
        material_evidence: list[dict],
    ) -> dict:
        if not material_evidence:
            return {
                "problem_summary": str(question_text)[:300],
                "student_position": "unbekannt — keine Materialtreffer",
                "next_action_for_teacher": "Rueckfrage an den Schueler stellen; kein passendes Material gefunden.",
                "answer_for_student": None,
                "confidence": 0.0,
                "needs_teacher": True,
                "reason_codes": [REASON_NO_MATERIAL_EVIDENCE],
                "evidence_refs": [],
            }

        top = candidates[0] if candidates else {}
        if self.invoke_json is not None:
            payload = self.invoke_json(
                schema=ANSWER_SCHEMA,
                prompt=self._build_prompt(question_text, window_segments, top, material_evidence),
            )
            if isinstance(payload, dict) and isinstance(payload.get("content"), str):
                try:
                    payload = json.loads(payload["content"])
                except (TypeError, ValueError):
                    payload = {}
            result = payload if isinstance(payload, dict) else {}
        else:
            excerpt = str((material_evidence[0] or {}).get("excerpt") or "")[:200]
            task_title = top.get("title") or top.get("task_id") or "der Aufgabe"
            result = {
                "problem_summary": str(question_text)[:200],
                "student_position": f"Vermutlich bei Aufgabe {top.get('task_id') or 'unbekannt'}",
                "next_action_for_teacher": f"Materialstelle zeigen: {excerpt or 'siehe Quelle'}",
                "answer_for_student": f"Schau in das Material zu {task_title}: {excerpt}",
                "confidence": min(0.75, float(top.get("score") or 0.5)),
            }

        result["needs_teacher"] = False
        result.setdefault("reason_codes", [])
        # Antworten referenzieren ausschliesslich uebergebene Evidence.
        result["evidence_refs"] = list(material_evidence)
        return result

    def _build_prompt(
        self, question_text: str, window_segments: list[dict], top_candidate: dict, evidence: list[dict]
    ) -> str:
        # Das Fenster ist bereits budgetiert (build_transcript_window im
        # Gateway); hier nur noch Formatierung.
        window_text = "\n".join(f"- {seg.get('text_segment')}" for seg in window_segments[:10])
        evidence_text = "\n".join(f"- {e.get('path')}: {str(e.get('excerpt'))[:200]}" for e in evidence[:5])
        return (
            "Erzeuge eine kurze, dozententaugliche Hilfe. Nutze NUR die angegebenen Quellen.\n"
            f"Frage: {str(question_text)[:500]}\n"
            f"Aufgabe: {top_candidate.get('module_id')}/{top_candidate.get('task_id')}\n"
            f"Verlauf:\n{window_text}\n"
            f"Quellen:\n{evidence_text}"
        )
