import pytest

from agent.services.classroom.teacher_action_card_service import TeacherActionCardService


def _create(service, warnings=None):
    return service.create_card(
        zoom_room="r",
        student_alias="spk-123456789abc",
        question_summary="q",
        intent="question",
        confidence=0.7,
        module=None,
        task=None,
        candidates=[],
        answer=None,
        workflow_part=None,
        evidence_refs=[],
        context_hints=[],
        warnings=warnings or [],
        source_event_id="e",
    )


def test_card_schema_status_and_warning_vocabulary():
    service = TeacherActionCardService()
    card = _create(service)
    assert card["schema"] == "teacher_action_card.v1"
    assert card["evidence_refs"] is not card["context_hints"]
    assert service.update_status(card["card_id"], "answered")["status"] == "answered"
    with pytest.raises(ValueError, match="card_unknown_warning"):
        _create(service, ["invented"])
