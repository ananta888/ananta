"""CTA-001: Classroom-Event-Contract + Gateway auf der TriggerEngine.

Webhook-, MCP- und Batch-Pfad liefern denselben normalisierten Event;
Idempotenz/Dedup kommt aus der bestehenden TriggerEngine
(agent/routes/tasks/triggers.py), nicht aus Eigenbau. Der Gateway
orchestriert die Pipeline bis zur TeacherActionCard.
"""

from __future__ import annotations

import time
from typing import Callable

from agent.common.audit import log_audit
from agent.services.classroom import privacy_policy
from agent.services.classroom.answer_composer_service import AnswerComposerService, build_transcript_window
from agent.services.classroom.classroom_material_search_service import ClassroomMaterialSearchService
from agent.services.classroom.module_task_resolver_service import ModuleTaskResolverService
from agent.services.classroom.n8n_partial_workflow_selector import N8nPartialWorkflowSelector, load_fixture_workflows
from agent.services.classroom.n8n_teaching_workflow_verifier_service import (
    STATUS_FAILED,
    known_node_types_from_examples,
    verify_workflow_part,
)
from agent.services.classroom.question_detection_service import (
    ACTIONABLE_INTENTS,
    INTENT_IRONIC,
    StudentQuestionDetectionService,
)
from agent.services.classroom.teacher_action_card_service import (
    WARNING_AMBIGUOUS_INTENT,
    WARNING_LOW_CONFIDENCE,
    WARNING_NO_MATERIAL_EVIDENCE,
    WARNING_PRIVACY_REDACTION_APPLIED,
    WARNING_WEAK_CONTEXT_HINT_ONLY,
    WARNING_WORKFLOW_VERIFICATION_FAILED,
    TeacherActionCardService,
    get_teacher_action_card_service,
)
from agent.services.classroom.zoom_room_schedule_context_hint_service import build_context_hints

SOURCE_ADAPTERS = ("webhook", "mcp", "batch")
SPEAKER_ROLES = ("student", "teacher", "unknown")
TRIGGER_SOURCE = "classroom"

STATUS_ERROR = "error"
STATUS_DUPLICATE = "duplicate"
STATUS_OBSERVED = "observed"
STATUS_CARD_CREATED = "card_created"


def normalize_classroom_event(payload: object, *, source_adapter: str) -> dict:
    """Provider-neutraler Event-Contract. ValueError('reason_code') bei
    Verstoessen; Klarnamen werden hier bereits gehasht."""
    if source_adapter not in SOURCE_ADAPTERS:
        raise ValueError("invalid_source_adapter")
    data = payload if isinstance(payload, dict) else None
    if data is None:
        raise ValueError("invalid_payload")

    event_id = str(data.get("event_id") or "").strip()
    session_id = str(data.get("session_id") or "").strip()
    if not event_id:
        raise ValueError("event_id_required")
    if not session_id:
        raise ValueError("session_id_required")

    # voice_provider.transcribe() liefert 'transcript' — verlustfrei mappen.
    text_segment = str(data.get("text_segment") or data.get("text") or data.get("transcript") or "").strip()
    if not text_segment:
        raise ValueError("text_segment_required")

    speaker_role = str(data.get("speaker_role") or "unknown").strip().lower()
    if speaker_role not in SPEAKER_ROLES:
        speaker_role = "unknown"

    raw_label = str(data.get("speaker_label") or "").strip()
    supplied_hash = str(data.get("speaker_label_hash") or "").strip()
    if raw_label:
        speaker_label_hash = privacy_policy.hash_speaker_label(raw_label)
    elif privacy_policy.is_valid_speaker_hash(supplied_hash):
        speaker_label_hash = supplied_hash
    else:
        speaker_label_hash = privacy_policy.hash_speaker_label("unknown")

    try:
        sequence_no = int(data.get("sequence_no") or 0)
    except (TypeError, ValueError):
        raise ValueError("invalid_sequence_no")

    return {
        "event_id": event_id,
        "session_id": session_id,
        "zoom_room_id": str(data.get("zoom_room_id") or "").strip() or None,
        "room_label": str(data.get("room_label") or "").strip() or None,
        "module_id_hint": str(data.get("module_id_hint") or "").strip() or None,
        "task_id_hint": str(data.get("task_id_hint") or "").strip() or None,
        "timestamp": data.get("timestamp"),
        "sequence_no": sequence_no,
        "speaker_role": speaker_role,
        "speaker_label_hash": speaker_label_hash,
        "text_segment": text_segment,
        "source_adapter": source_adapter,
        "trigger_mode": str(data.get("trigger_mode") or source_adapter),
    }


class ClassroomEventGateway:
    def __init__(
        self,
        *,
        trigger_engine=None,
        detection_service: StudentQuestionDetectionService | None = None,
        resolver_service: ModuleTaskResolverService | None = None,
        composer_service: AnswerComposerService | None = None,
        workflow_selector: N8nPartialWorkflowSelector | None = None,
        card_service: TeacherActionCardService | None = None,
        audit_fn: Callable[[str, dict], None] = log_audit,
        config_provider: Callable[[], dict] | None = None,
    ) -> None:
        self._trigger_engine = trigger_engine
        self.detection_service = detection_service or StudentQuestionDetectionService()
        self.resolver_service = resolver_service or ModuleTaskResolverService()
        self.composer_service = composer_service or AnswerComposerService()
        self.workflow_selector = workflow_selector or N8nPartialWorkflowSelector()
        self.card_service = card_service or get_teacher_action_card_service()
        self.audit_fn = audit_fn
        self.config_provider = config_provider or (lambda: {})
        self._segments_by_session: dict[str, list[dict]] = {}

    # ── public ───────────────────────────────────────────────────────────

    def process_event(self, payload: object, *, source_adapter: str) -> dict:
        try:
            event = normalize_classroom_event(payload, source_adapter=source_adapter)
        except ValueError as exc:
            return {"status": STATUS_ERROR, "reason_code": str(exc), "card_id": None, "warnings": []}

        dedup = self._check_dedup(event)
        if dedup is not None:
            existing = self.card_service.find_by_event(event["event_id"])
            return {
                "status": STATUS_DUPLICATE,
                "reason_code": dedup,
                "card_id": (existing or {}).get("card_id"),
                "warnings": [],
            }

        cfg = self.config_provider() or {}
        classroom_cfg = cfg.get("classroom") if isinstance(cfg.get("classroom"), dict) else {}
        if not bool(classroom_cfg.get("enabled", False)):
            return {"status": STATUS_ERROR, "reason_code": "classroom_disabled", "card_id": None, "warnings": []}
        redacted_text, redaction_count = privacy_policy.redact_pii(event["text_segment"])
        event["text_segment"] = redacted_text
        self._store_segment(event, cfg)
        self.audit_fn(
            privacy_policy.AUDIT_EVENT_RECEIVED,
            {
                "event_id": event["event_id"],
                "session_id": event["session_id"],
                "zoom_room_id": event["zoom_room_id"],
                "source_adapter": source_adapter,
                "redactions": redaction_count,
            },
        )

        hints = build_context_hints(zoom_room_id=event["zoom_room_id"], timestamp=event["timestamp"], cfg=cfg)
        detection = self.detection_service.detect(
            event["text_segment"],
            context=hints.get("retrieval_filters") or {},
        )

        warnings: list[str] = []
        if redaction_count:
            warnings.append(WARNING_PRIVACY_REDACTION_APPLIED)
        if detection["intent"] == INTENT_IRONIC:
            warnings.append(WARNING_AMBIGUOUS_INTENT)

        threshold = float(
            (classroom_cfg.get("question_confidence_threshold") or self.detection_service.confidence_threshold)
        )
        actionable = detection["intent"] in ACTIONABLE_INTENTS and detection["confidence"] >= threshold
        if not actionable and not detection.get("needs_teacher_attention"):
            # Kein Frage-/Hilfe-Signal: Segment nur beobachten, keine Karte.
            return {"status": STATUS_OBSERVED, "card_id": None, "warnings": warnings, "detection": detection}
        if not actionable:
            warnings.append(WARNING_LOW_CONFIDENCE)

        resolution = self.resolver_service.resolve(event=event, detection=detection, hints=hints)
        warnings.extend(w for w in resolution.get("warnings") or [] if w == WARNING_WEAK_CONTEXT_HINT_ONLY)
        candidates = resolution.get("ranked_candidates") or []
        confirmed = resolution.get("confirmed")
        material_evidence = [ref for candidate in candidates for ref in (candidate.get("evidence_refs") or [])]

        window = build_transcript_window(
            self._segments_by_session.get(event["session_id"], []),
            question_segment=event,
            max_tokens=int((classroom_cfg.get("max_context_tokens") or 2000)),
        )
        answer = None
        if actionable:
            answer = self.composer_service.compose(
                question_text=event["text_segment"],
                window_segments=window,
                candidates=candidates,
                material_evidence=material_evidence,
            )
            if (
                "no_material_evidence" in (answer.get("reason_codes") or [])
                and WARNING_NO_MATERIAL_EVIDENCE not in warnings
            ):
                warnings.append(WARNING_NO_MATERIAL_EVIDENCE)
            self.audit_fn(
                privacy_policy.AUDIT_ANSWER_PROPOSED,
                {
                    "event_id": event["event_id"],
                    "needs_teacher": bool(answer.get("needs_teacher")),
                },
            )

        workflow_part = self._maybe_select_workflow(event, detection, warnings, cfg)

        card = self.card_service.create_card(
            zoom_room=event["zoom_room_id"] or event["room_label"] or "unknown-room",
            student_alias=event["speaker_label_hash"],
            question_summary=event["text_segment"][:300],
            intent=detection["intent"],
            confidence=detection["confidence"],
            module=(confirmed or {}).get("module_id"),
            task=(confirmed or {}).get("task_id"),
            candidates=candidates,
            answer=answer,
            workflow_part=workflow_part,
            evidence_refs=material_evidence,
            context_hints=hints.get("ranked_context_hints") or [],
            warnings=warnings,
            source_event_id=event["event_id"],
        )
        self.audit_fn(
            privacy_policy.AUDIT_CARD_CREATED,
            {
                "card_id": card["card_id"],
                "event_id": event["event_id"],
                "intent": detection["intent"],
            },
        )
        return {"status": STATUS_CARD_CREATED, "card_id": card["card_id"], "warnings": card["warnings"]}

    # ── intern ───────────────────────────────────────────────────────────

    def _check_dedup(self, event: dict) -> str | None:
        engine = self._trigger_engine
        if engine is None:
            from agent.routes.tasks.triggers import trigger_engine as engine  # lazy: vermeidet Import-Zyklen

        result = engine.check_replay_and_dedup(
            TRIGGER_SOURCE,
            {"event_id": event["event_id"], "sequence_no": event["sequence_no"]},
        )
        if result is not None and result.get("status") != "ok":
            return str(result.get("status"))
        return None

    def normalize_event(self, payload: object, *, source_adapter: str) -> dict:
        """Public adapter seam used by webhook, MCP and batch tests."""
        return normalize_classroom_event(payload, source_adapter=source_adapter)

    def _store_segment(self, event: dict, cfg: dict) -> None:
        segments = self._segments_by_session.setdefault(event["session_id"], [])
        segments.append({**event, "received_at": time.time()})
        pruned = privacy_policy.prune_expired_segments(segments, cfg=cfg)
        self._segments_by_session[event["session_id"]] = pruned

    def _maybe_select_workflow(self, event: dict, detection: dict, warnings: list[str], cfg: dict) -> dict | None:
        if "n8n_term" not in set(detection.get("reason_codes") or []):
            from agent.services.classroom.question_detection_service import detect_signals

            if "n8n_term" not in detect_signals(event["text_segment"])["signals"]:
                return None
        examples_dir = str(
            ((cfg.get("classroom") or {}).get("n8n_examples_dir") or self.workflow_selector.examples_dir)
        )
        self.workflow_selector.examples_dir = examples_dir
        proposal = self.workflow_selector.select(question_text=event["text_segment"])
        if proposal is None:
            return None
        known_types = known_node_types_from_examples(load_fixture_workflows(examples_dir))
        verification = verify_workflow_part(
            proposal["part"],
            part_kind=proposal["form"],
            known_node_types=known_types or None,
            task_terms=None,
        )
        if verification["status"] == STATUS_FAILED:
            warnings.append(WARNING_WORKFLOW_VERIFICATION_FAILED)
        self.audit_fn(
            privacy_policy.AUDIT_WORKFLOW_PROPOSED,
            {
                "event_id": event["event_id"],
                "form": proposal["form"],
                "origin": proposal["origin"],
                "verifier_status": verification["status"],
            },
        )
        return {
            "form": proposal["form"],
            "import_hint": proposal["import_hint"],
            "source_ref": proposal["source_ref"],
            "origin": proposal["origin"],
            "verifier_status": verification["status"],
            "verifier_reasons": verification["reasons"],
            "part": verification["verified_part"],
        }


_gateway: ClassroomEventGateway | None = None


def get_classroom_event_gateway() -> ClassroomEventGateway:
    global _gateway
    if _gateway is None:
        material_search = ClassroomMaterialSearchService(_default_config_provider)
        _gateway = ClassroomEventGateway(
            config_provider=_default_config_provider,
            resolver_service=ModuleTaskResolverService(search_fn=material_search.search),
        )
    return _gateway


def _default_config_provider() -> dict:
    try:
        from flask import current_app, has_app_context

        if has_app_context():
            return current_app.config.get("AGENT_CONFIG", {}) or {}
    except Exception:
        pass
    return {}
