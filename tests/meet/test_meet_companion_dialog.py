"""Companion dialog: routing, CodeCompass grounding, traced self-explanation, flags."""

import pytest

from worker.meet_media.assist import _snippet
from worker.meet_media.companion_dialog import PERSONA_SYSTEM, CompanionDialog, context_block
from worker.meet_media.companion_explanation import AnswerTrace, explain
from worker.meet_media.companion_flags import (
    AVATAR_FLAG,
    CODECOMPASS_FLAG,
    avatar_enabled,
    codecompass_enabled,
)
from worker.meet_media.companion_router import (
    ANANTA_CODE_ARCHITECTURE,
    GENERAL_QUESTION,
    MEET_RUNTIME_CURRENT_DIALOG,
    SELF_EXPLANATION,
    classify,
)

pytestmark = pytest.mark.timeout(30)

SNIPPETS = [
    {"path": "worker/meet_media/companion_media.py", "symbol": "SpeechAvatarPublisher", "revision": "26aa8f8763891e8190b2a13ec18a2a27d73ddd8a", "score": 0.9, "excerpt": "Publishes TTS audio and the rendered snake avatar from one timeline."},
    {"path": "worker/meet_media/snake_avatar_timeline.py", "symbol": "", "revision": "", "score": 0.5, "excerpt": "One monotonic media timeline."},
    {"path": "", "symbol": "", "revision": "", "score": 0.1, "excerpt": "orphan excerpt without a locator"},
]


@pytest.mark.parametrize(
    "text, route, codecompass",
    [
        ("Was ist Ananta?", ANANTA_CODE_ARCHITECTURE, True),
        ("Wie funktioniert Ananta Meet?", MEET_RUNTIME_CURRENT_DIALOG, True),
        ("Wie funktioniert deine Sprachausgabe?", ANANTA_CODE_ARCHITECTURE, True),
        ("Welche Datei macht das?", ANANTA_CODE_ARCHITECTURE, True),
        ("Wie hast du diese Antwort erzeugt?", SELF_EXPLANATION, False),
        ("How did you generate that answer?", SELF_EXPLANATION, False),
        ("Wer ist gerade im Raum?", MEET_RUNTIME_CURRENT_DIALOG, False),
        ("Hallo! Wie geht es dir heute?", GENERAL_QUESTION, False),
        ("Erzähl mir einen Witz", GENERAL_QUESTION, False),
        ("", GENERAL_QUESTION, False),
    ],
)
def test_router_grounds_repository_questions_only(text, route, codecompass):
    decision = classify(text)
    assert (decision.route, decision.codecompass) == (route, codecompass)


def test_router_never_uses_codecompass_when_the_flag_is_off():
    decision = classify("Welche Datei implementiert den Hub?", codecompass_enabled=False)
    assert decision.route == ANANTA_CODE_ARCHITECTURE and decision.codecompass is False


@pytest.mark.parametrize("value, expected", [("1", True), ("true", True), ("0", False), ("off", False), ("FALSE", False)])
def test_feature_flags_default_on_and_disable_explicitly(value, expected):
    assert avatar_enabled({}) is True and codecompass_enabled({}) is True
    assert avatar_enabled({AVATAR_FLAG: value}) is expected
    assert codecompass_enabled({CODECOMPASS_FLAG: value}) is expected


def test_context_block_prefixes_path_and_symbol_and_stays_bounded():
    block = context_block(SNIPPETS, max_chars=120)
    assert block.startswith("[worker/meet_media/companion_media.py#SpeechAvatarPublisher] Publishes")
    assert len(block) <= 120 + 1
    assert context_block([]) == ""


def test_assist_snippet_projection_keeps_only_bounded_locator_fields():
    projected = _snippet({"path": "a.py", "symbol": "f", "revision": "abc", "score": 0.3, "excerpt": "x" * 2000, "content": "private"})
    assert set(projected) == {"path", "line", "line_end", "symbol", "revision", "score", "excerpt"}
    assert projected["line"] is None and _snippet({"line": 12})["line"] == 12
    assert _snippet({"line": True})["line"] is None
    assert _snippet({"line": 12, "line_end": 40})["line_end"] == 40
    assert _snippet({"line": 12, "line_end": 3})["line_end"] is None
    assert _snippet({"line_end": 40})["line_end"] is None
    assert len(projected["excerpt"]) == 1200 and projected["score"] == 0.3
    assert _snippet({"score": "high"})["score"] is None


class FakeLlm:
    def __init__(self):
        self.calls = []

    def __call__(self, text, context, system):
        self.calls.append((text, context, system))
        return "Klar! Ich bin die Ananta-Schlange."


def dialog(retriever=lambda query: SNIPPETS, **kwargs):
    llm = FakeLlm()
    return CompanionDialog(llm=llm, retriever=retriever, model_name="local-test-model", **kwargs), llm


def test_repository_question_is_grounded_and_sources_are_cited_with_file_symbol_and_sha():
    companion, llm = dialog()
    reply, trace = companion.answer("Wie funktioniert deine Sprachausgabe?")
    # The fake reply names no source, so the best one is appended (Quellenpflicht).
    assert reply == (
        "Klar! Ich bin die Ananta-Schlange. Quelle: worker/meet_media/companion_media.py, SpeechAvatarPublisher."
    )
    assert llm.calls[0][2] == PERSONA_SYSTEM and "[worker/meet_media/companion_media.py#SpeechAvatarPublisher]" in llm.calls[0][1]
    assert trace.route == ANANTA_CODE_ARCHITECTURE and trace.codecompass_used
    assert trace.source_labels() == [
        "worker/meet_media/companion_media.py · SpeechAvatarPublisher @ 26aa8f876389",
        "worker/meet_media/snake_avatar_timeline.py",
    ]
    assert trace.generated_by == "local-test-model" and trace.generated_text == reply
    assert any("retrieval returned 3" in step for step in trace.observed)


def test_smalltalk_skips_codecompass_and_meet_questions_attach_runtime_context():
    calls = []
    companion, llm = dialog(retriever=lambda query: calls.append(query) or SNIPPETS, runtime_context=lambda: "peers=2 e2ee=on")
    _reply, trace = companion.answer("Hallo, wie geht es dir?")
    assert calls == [] and trace.codecompass_used is False and llm.calls[-1][1] == ""
    _reply, trace = companion.answer("Wer ist gerade im Raum?")
    assert calls == [] and trace.route == MEET_RUNTIME_CURRENT_DIALOG
    assert llm.calls[-1][1] == "[meet-runtime] peers=2 e2ee=on"
    assert "meet runtime context attached" in trace.observed


def test_self_explanation_separates_observed_repository_and_generated_without_a_model_call():
    companion, llm = dialog()
    companion.answer("Welche Datei macht die Sprachausgabe?")
    reply, trace = companion.answer("Wie hast du diese Antwort erzeugt?")
    assert len(llm.calls) == 1
    assert reply.startswith("Beobachtet: chat message received, route=ananta_code_architecture")
    assert "Aus dem Repository: worker/meet_media/companion_media.py · SpeechAvatarPublisher @ 26aa8f876389" in reply
    assert "Formuliert wurde der Antworttext von local-test-model" in reply
    assert trace.route == SELF_EXPLANATION and trace.generated_by.startswith("keinem Modell")
    assert len(reply) <= 450


def test_missing_retrieval_evidence_never_names_files():
    companion, _llm = dialog(retriever=lambda query: [])
    _reply, trace = companion.answer("Welche Datei macht das?")
    assert trace.repository == [] and trace.codecompass_used
    explanation = explain(trace)
    assert "CodeCompass hat keine passende Quelle geliefert, daher nenne ich keine Datei" in explanation
    assert ".py" not in explanation

    def broken(query):
        raise ConnectionError("hub unreachable")

    companion, _llm = dialog(retriever=broken)
    _reply, trace = companion.answer("Welche Datei macht das?")
    assert trace.repository == [] and any("retrieval failed (ConnectionError)" in step for step in trace.observed)


def test_explanation_without_a_previous_answer_and_unknown_snippets_are_bounded():
    assert explain(None).startswith("Ich habe in diesem Gespräch noch keine Antwort erzeugt")
    trace = AnswerTrace(question="q", route=GENERAL_QUESTION)
    trace.add_sources([{"excerpt": "no locator"}, "junk", {"path": "a.py", "score": "x"}])
    assert trace.source_labels() == ["a.py"]
    for index in range(40):
        trace.observe(f"step {index}")
    assert len(trace.observed) == 32
    assert "Aus dem Repository: a.py." in explain(trace)
    assert "Aus dem Repository: nichts, diese Frage lief ohne CodeCompass." in explain(AnswerTrace("q", GENERAL_QUESTION))
