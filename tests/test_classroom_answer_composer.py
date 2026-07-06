from agent.services.classroom.answer_composer_service import AnswerComposerService, build_transcript_window


def test_window_prioritizes_same_speaker_and_filters_other_tasks():
    question = {"session_id": "s", "sequence_no": 10, "speaker_label_hash": "me", "task_id_hint": "A"}
    segments = [
        {
            "session_id": "s",
            "sequence_no": 1,
            "speaker_label_hash": "other",
            "task_id_hint": "B",
            "text_segment": "wrong",
        },
        {
            "session_id": "s",
            "sequence_no": 2,
            "speaker_label_hash": "other",
            "task_id_hint": "A",
            "text_segment": "related",
        },
        {"session_id": "s", "sequence_no": 3, "speaker_label_hash": "me", "task_id_hint": "B", "text_segment": "own"},
    ]
    window = build_transcript_window(segments, question_segment=question, max_tokens=20)
    assert [item["text_segment"] for item in window] == ["own", "related"]


def test_no_evidence_never_generates_student_answer():
    answer = AnswerComposerService().compose(
        question_text="Wie?", window_segments=[], candidates=[], material_evidence=[]
    )
    assert answer["answer_for_student"] is None
    assert answer["reason_codes"] == ["no_material_evidence"]
