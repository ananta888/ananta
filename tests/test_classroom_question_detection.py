from agent.services.classroom.question_detection_service import (
    INTENT_HELP_REQUEST,
    INTENT_IRONIC,
    INTENT_OFFTOPIC,
    INTENT_ORGANIZATIONAL,
    INTENT_QUESTION,
    INTENT_SMALLTALK,
    StudentQuestionDetectionService,
)


def test_deterministic_intents():
    service = StudentQuestionDetectionService()
    assert service.detect("Wie verbinde ich den Webhook?")["intent"] == INTENT_QUESTION
    assert service.detect("Der Node funktioniert nicht")["intent"] == INTENT_HELP_REQUEST
    assert service.detect("Super, wieder alles kaputt?")["intent"] == INTENT_IRONIC
    assert service.detect("Wie war das Fußballspiel?")["intent"] == INTENT_OFFTOPIC
    assert service.detect("Hallo zusammen")["intent"] == INTENT_SMALLTALK
    assert service.detect("Wann ist Pause?")["intent"] == INTENT_ORGANIZATIONAL


def test_no_candidate_does_not_invoke_llm_and_schema_is_closed():
    calls = []
    service = StudentQuestionDetectionService(invoke_json=lambda **kw: calls.append(kw))
    service.detect("Hallo")
    assert calls == []

    service.detect("Wie geht das?")
    assert calls[0]["schema"]["additionalProperties"] is False
