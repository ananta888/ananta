import json
from pathlib import Path

from agent.services.classroom.classroom_event_gateway import (
    STATUS_CARD_CREATED,
    STATUS_DUPLICATE,
    ClassroomEventGateway,
    normalize_classroom_event,
)
from agent.services.classroom.teacher_action_card_service import TeacherActionCardService


class _DedupEngine:
    def __init__(self):
        self.seen = set()

    def _check_replay_and_dedup(self, source, payload, headers=None):
        key = (source, payload["event_id"], payload["sequence_no"])
        if key in self.seen:
            return {"status": "replay_blocked", "reason": "duplicate_event"}
        self.seen.add(key)
        return {"status": "ok"}

    def check_replay_and_dedup(self, source, payload, headers=None):
        return self._check_replay_and_dedup(source, payload, headers=headers)


def _payload():
    line = Path("tests/fixtures/classroom/transcripts/basic.jsonl").read_text().splitlines()[0]
    return json.loads(line)


def test_event_normalization_hashes_names_and_adapters_are_equivalent():
    payload = _payload()
    normalized = [normalize_classroom_event(payload, source_adapter=adapter) for adapter in ("webhook", "mcp", "batch")]
    for item in normalized:
        item.pop("source_adapter")
        item["trigger_mode"] = "normalized"
    assert normalized[0] == normalized[1] == normalized[2]
    assert "Max Mustermann" not in json.dumps(normalized)
    assert normalized[0]["module_id_hint"] is None


def test_invalid_prefixed_clear_name_is_rehashed():
    event = normalize_classroom_event(
        {**_payload(), "speaker_label": "", "speaker_label_hash": "spk-Max Mustermann"},
        source_adapter="webhook",
    )
    assert event["speaker_label_hash"] != "spk-Max Mustermann"


def test_duplicate_event_creates_exactly_one_card():
    cards = TeacherActionCardService()
    gateway = ClassroomEventGateway(
        trigger_engine=_DedupEngine(),
        card_service=cards,
        config_provider=lambda: {"classroom": {"enabled": True}},
        audit_fn=lambda *_: None,
    )
    first = gateway.process_event(_payload(), source_adapter="batch")
    second = gateway.process_event(_payload(), source_adapter="batch")
    assert first["status"] == STATUS_CARD_CREATED
    assert second["status"] == STATUS_DUPLICATE
    assert len(cards.list_cards()) == 1
    assert "Max Mustermann" not in json.dumps(cards.list_cards())
