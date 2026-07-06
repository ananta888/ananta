"""CTA-010: TeacherActionCard (teacher_action_card.v1) + In-Memory-Store.

Die Karte ist die einzige Ausgabeflaeche der Pipeline. Evidence
(Material/Transkript) und Context-Hints (Raum/Zeit) sind strukturell
getrennt; warnings kommen aus einem festen Vokabular.
"""

from __future__ import annotations

import threading
import time
import uuid

CARD_SCHEMA = "teacher_action_card.v1"

WARNING_AMBIGUOUS_INTENT = "ambiguous_intent"
WARNING_LOW_CONFIDENCE = "low_confidence"
WARNING_WEAK_CONTEXT_HINT_ONLY = "weak_context_hint_only"
WARNING_WORKFLOW_VERIFICATION_FAILED = "workflow_verification_failed"
WARNING_PRIVACY_REDACTION_APPLIED = "privacy_redaction_applied"
WARNING_NO_MATERIAL_EVIDENCE = "no_material_evidence"

WARNING_VOCABULARY = frozenset(
    {
        WARNING_AMBIGUOUS_INTENT,
        WARNING_LOW_CONFIDENCE,
        WARNING_WEAK_CONTEXT_HINT_ONLY,
        WARNING_WORKFLOW_VERIFICATION_FAILED,
        WARNING_PRIVACY_REDACTION_APPLIED,
        WARNING_NO_MATERIAL_EVIDENCE,
    }
)

CARD_STATUSES = ("open", "answered", "dismissed")


def validate_card(card: dict) -> None:
    """Schema-Validierung; ValueError mit reason_code bei Verstoss."""
    if card.get("schema") != CARD_SCHEMA:
        raise ValueError("card_schema_mismatch")
    for field in ("card_id", "created_at", "zoom_room", "student_alias", "question_summary", "intent"):
        if not str(card.get(field) or "").strip():
            raise ValueError(f"card_field_missing:{field}")
    unknown = set(card.get("warnings") or []) - WARNING_VOCABULARY
    if unknown:
        raise ValueError(f"card_unknown_warning:{sorted(unknown)[0]}")
    if card.get("status") not in CARD_STATUSES:
        raise ValueError("card_invalid_status")
    if not isinstance(card.get("evidence_refs"), list) or not isinstance(card.get("context_hints"), list):
        raise ValueError("card_refs_must_be_lists")


class TeacherActionCardService:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cards: dict[str, dict] = {}

    def create_card(
        self,
        *,
        zoom_room: str,
        student_alias: str,
        question_summary: str,
        intent: str,
        confidence: float,
        module: str | None,
        task: str | None,
        candidates: list[dict],
        answer: dict | None,
        workflow_part: dict | None,
        evidence_refs: list[dict],
        context_hints: list[dict],
        warnings: list[str],
        source_event_id: str,
    ) -> dict:
        card = {
            "schema": CARD_SCHEMA,
            "card_id": f"card-{uuid.uuid4().hex[:12]}",
            "created_at": time.time(),
            "source_event_id": source_event_id,
            "zoom_room": zoom_room,
            "student_alias": student_alias,
            "question_summary": str(question_summary)[:500],
            "intent": intent,
            "confidence": round(float(confidence), 4),
            "module": module,
            "task": task,
            "candidates": candidates,
            "answer": answer,
            "workflow_part": workflow_part,
            "evidence_refs": list(evidence_refs),
            "context_hints": list(context_hints),
            "warnings": sorted(set(warnings)),
            "status": "open",
        }
        validate_card(card)
        with self._lock:
            self._cards[card["card_id"]] = card
        return card

    def get_card(self, card_id: str) -> dict | None:
        return self._cards.get(str(card_id))

    def find_by_event(self, event_id: str) -> dict | None:
        for card in self._cards.values():
            if card.get("source_event_id") == event_id:
                return card
        return None

    def list_cards(
        self, *, zoom_room: str | None = None, module: str | None = None, status: str | None = None
    ) -> list[dict]:
        cards = list(self._cards.values())
        if zoom_room:
            cards = [c for c in cards if c.get("zoom_room") == zoom_room]
        if module:
            cards = [c for c in cards if c.get("module") == module]
        if status:
            cards = [c for c in cards if c.get("status") == status]
        return sorted(cards, key=lambda c: -float(c.get("created_at") or 0))

    def update_status(self, card_id: str, status: str) -> dict:
        if status not in CARD_STATUSES:
            raise ValueError("card_invalid_status")
        with self._lock:
            card = self._cards.get(str(card_id))
            if card is None:
                raise KeyError("card_not_found")
            card["status"] = status
            return card


_card_service = TeacherActionCardService()


def get_teacher_action_card_service() -> TeacherActionCardService:
    return _card_service
