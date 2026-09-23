"""Knowledge questions: router-forced CodeCompass lookup, Quellenpflicht, German style.

Live regression: "was weisst du ueber den rag-helper wie der funktioniert?" was
answered without any tool call and without a source. Here the transports are
mocked JSON ports, so these tests assert the protocol, the forced lookup and
the deterministic reply rules — never model quality.
"""

import json

import pytest

from worker.meet_media import llm, llm_tools
from worker.meet_media.companion_dialog import (
    PERSONA_SYSTEM,
    PERSONA_SYSTEM_WITH_TOOLS,
    CompanionDialog,
    enforce_sources,
    strip_meta,
)
from worker.meet_media.companion_explanation import RepositorySource
from worker.meet_media.companion_router import ANANTA_CODE_ARCHITECTURE, GENERAL_QUESTION, classify, search_query

pytestmark = pytest.mark.timeout(15)

MODEL = "bonsai-2-27b"
QUESTION = "was weisst du ueber den rag-helper wie der funktioniert?"
RAG_SNIPPETS = [
    {
        "path": "agent/services/rag_helper_index_service.py",
        "line": 31,
        "symbol": "RagHelperIndexService",
        "revision": "",
        "score": 0.9,
        "excerpt": "Indexiert einen Repository-Pfad mit einem Profil und schreibt Gems-Partition-Previews.",
    },
    {"path": "docs/rag-helper.md", "line": None, "symbol": "", "revision": "", "score": 0.5, "excerpt": "RAG-Helper."},
]


def answering(content):
    return {
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 90, "completion_tokens": 8},
    }


class Port:
    """OpenAI JSON port whose model never asks for a tool by itself."""

    def __init__(self, content):
        self._content = content
        self.payloads = []

    def chat(self, payload):
        self.payloads.append(json.loads(json.dumps(payload)))
        return answering(self._content)

    def models(self):
        return {"object": "list", "data": [{"id": MODEL, "object": "model"}]}


class OllamaPort(Port):
    def chat(self, payload):
        self.payloads.append(json.loads(json.dumps(payload)))
        return {
            "message": {"role": "assistant", "content": self._content},
            "done": True,
            "prompt_eval_count": 90,
            "eval_count": 8,
        }

    def models(self):
        return {"models": [{"name": MODEL, "digest": "synthetic-digest"}]}


class Retriever:
    def __init__(self, snippets=None):
        self._snippets = RAG_SNIPPETS if snippets is None else snippets
        self.queries = []

    def __call__(self, query, limit):
        self.queries.append(query)
        return self._snippets


@pytest.fixture(autouse=True)
def backend_env(monkeypatch):
    monkeypatch.setenv("MEET_LLM_BACKEND", "openai")
    monkeypatch.setenv("MEET_LLM_MODEL", MODEL)
    monkeypatch.setenv("MEET_LLM_DIGEST", "synthetic-digest")
    monkeypatch.delenv("MEET_LLM_OPENAI_REASONING_EFFORT", raising=False)
    monkeypatch.delenv("MEET_LLM_OPENAI_CHAT_TEMPLATE_KWARGS", raising=False)
    monkeypatch.delenv("MEET_LLM_TOOL_ROUNDS", raising=False)
    monkeypatch.delenv("MEET_LLM_TOOL_RESULT_CHARS", raising=False)


def companion(port, retriever=None):
    """The production wiring of ``companion.dialog()`` with injected ports."""
    retriever = Retriever() if retriever is None else retriever
    tools = llm_tools.codecompass_toolbox(retriever)
    dialog = CompanionDialog(
        llm=lambda text, context, system: llm.generate(
            text, context=context, system=system, tools=tools, transport=port
        ).text,
        codecompass_enabled=False,
        model_name=MODEL,
        system=PERSONA_SYSTEM_WITH_TOOLS,
        tools=tools,
    )
    return dialog, tools, retriever


# 1) Persona / system prompt


def test_the_tool_persona_demands_the_lookup_first_and_keeps_smalltalk_out():
    assert "rufst du es zuerst auf und antwortest erst danach" in PERSONA_SYSTEM_WITH_TOOLS
    assert "„was weißt du über X“" in PERSONA_SYSTEM_WITH_TOOLS
    assert "Für Smalltalk und allgemeine Fragen rufst du es nicht auf" in PERSONA_SYSTEM_WITH_TOOLS
    for prompt in (PERSONA_SYSTEM, PERSONA_SYSTEM_WITH_TOOLS):
        assert "Datei:Zeile" in prompt and "nicht belegt" in prompt and "rate nie" in prompt
        assert "nie über deine Antwort" in prompt and "knapp und direkt" in prompt


# 2) Router: deterministic knowledge decision


@pytest.mark.parametrize(
    "text, query",
    [
        (QUESTION, "rag-helper"),
        ("Was macht RagHelperIndexService?", "RagHelperIndexService"),
        ("erklär mir docs/rag-helper.md", "docs/rag-helper.md"),
        ("Was weißt du über den RAG helper?", "RAG helper"),
        ("Wie funktioniert deine Sprachausgabe?", "Sprachausgabe"),
        ("Welche Datei macht das?", "Datei"),
    ],
)
def test_knowledge_questions_are_marked_for_a_forced_lookup_with_a_focused_query(text, query):
    decision = classify(text, codecompass_enabled=False)
    assert decision.knowledge is True and decision.query == query
    # The prefix flag stays off; forcing does not depend on it.
    assert decision.codecompass is False


@pytest.mark.parametrize(
    "text",
    [
        "Hallo! Wie geht es dir heute?",
        "Erzähl mir einen Witz",
        "Was ist die Hauptstadt von Frankreich?",
        "Wie funktioniert ein Kühlschrank?",
        "Was ist Covid-19?",
        "Wer ist gerade im Raum?",
    ],
)
def test_smalltalk_and_general_questions_are_never_knowledge_questions(text):
    decision = classify(text)
    assert decision.knowledge is False and decision.query == ""


def test_search_query_is_bounded_and_never_empty():
    assert len(search_query("was ist " + "a_b " * 200)) <= 200
    assert search_query("was ist das?") == "was ist das?"


def test_the_live_question_routes_to_code_architecture():
    assert classify(QUESTION).route == ANANTA_CODE_ARCHITECTURE
    assert classify("Wie geht es dir?").route == GENERAL_QUESTION


# 2) Forced tool call through the real backends


def test_the_router_forces_codecompass_even_when_the_model_would_not_call_it():
    port = Port("Der RAG-Helper indexiert Repository-Pfade mit einem Profil.")
    dialog, tools, retriever = companion(port)

    reply, trace = dialog.answer(QUESTION)

    assert retriever.queries == ["rag-helper"]
    assert [call["forced"] for call in tools.calls] == [True]
    first = port.payloads[0]["messages"]
    assert [message["role"] for message in first] == ["system", "user", "assistant", "tool"]
    call = first[2]["tool_calls"][0]
    assert call["id"] == "forced-0" and call["function"]["name"] == llm_tools.NAME
    assert json.loads(call["function"]["arguments"]) == {"query": "rag-helper"}
    assert first[3]["tool_call_id"] == "forced-0"
    assert "[agent/services/rag_helper_index_service.py:31#RagHelperIndexService]" in first[3]["content"]
    # The model may still refine: the tools stay offered on the first request.
    assert port.payloads[0]["tools"][0]["function"]["name"] == llm_tools.NAME
    assert "codecompass_search forced by the router before the model call" in trace.observed
    # 3) Quellenpflicht: the uncited reply gets datei:zeile + symbol appended.
    assert reply.endswith("Quelle: agent/services/rag_helper_index_service.py:31, RagHelperIndexService.")
    assert trace.source_labels()[0] == "agent/services/rag_helper_index_service.py:31 · RagHelperIndexService"


def test_the_ollama_backend_replays_the_forced_round_with_object_arguments(monkeypatch):
    monkeypatch.setenv("MEET_LLM_BACKEND", "ollama")
    port = OllamaPort("Laut docs/rag-helper.md indexiert der RAG-Helper Repository-Pfade.")
    dialog, _tools, _retriever = companion(port)

    reply, _trace = dialog.answer(QUESTION)

    assistant, result = port.payloads[0]["messages"][2:4]
    assert assistant["tool_calls"][0]["function"]["arguments"] == {"query": "rag-helper"}
    assert "id" not in assistant["tool_calls"][0] and "tool_call_id" not in result
    # Already cites a source, so nothing is appended.
    assert reply == "Laut docs/rag-helper.md indexiert der RAG-Helper Repository-Pfade."


def test_smalltalk_runs_no_lookup_and_the_request_carries_no_tool_round():
    port = Port("Mir geht es prima, danke!")
    dialog, tools, retriever = companion(port)

    reply, trace = dialog.answer("Hallo! Wie geht es dir heute?")

    assert reply == "Mir geht es prima, danke!"
    assert retriever.queries == [] and tools.calls == [] and tools.forced == []
    assert [message["role"] for message in port.payloads[0]["messages"]] == ["system", "user"]
    assert trace.codecompass_used is False


def test_the_forced_round_is_forgotten_per_reply():
    port = Port("Der RAG-Helper indexiert Repository-Pfade.")
    dialog, tools, _retriever = companion(port)
    dialog.answer(QUESTION)
    dialog.answer("Erzähl mir einen Witz")
    assert tools.forced == [] and tools.calls == []
    assert [message["role"] for message in port.payloads[-1]["messages"]] == ["system", "user"]


# 3) No evidence: say so and offer to search, never invent


def test_without_evidence_the_reply_says_it_is_not_evidenced_and_offers_a_search():
    port = Port("Der RAG-Helper ist eine der vier CodeCompass-Pipelines.")
    dialog, _tools, _retriever = companion(port, Retriever(snippets=[]))

    reply, trace = dialog.answer(QUESTION)

    assert reply == (
        "Der RAG-Helper ist eine der vier CodeCompass-Pipelines. "
        "Im Projektindex ist das nicht belegt; soll ich gezielt nach „rag-helper“ suchen?"
    )
    assert trace.repository == [] and ".py" not in reply


def test_a_reply_that_already_admits_missing_evidence_is_left_alone():
    reply = "Dazu finde ich im Projekt keinen Beleg. Soll ich gezielt nachsehen?"
    assert enforce_sources(reply, [], knowledge=True, query="x") == reply
    assert enforce_sources("Hallo!", [], knowledge=False) == "Hallo!"


def test_the_appended_source_keeps_the_reply_within_the_budget():
    source = RepositorySource("agent/services/rag_helper_index_service.py", "RagHelperIndexService", line=31)
    reply = enforce_sources("x" * 600, [source], knowledge=True)
    assert len(reply) <= 450
    assert reply.endswith("Quelle: agent/services/rag_helper_index_service.py:31, RagHelperIndexService.")


# 4) German style: no meta talk about the reply itself


def test_meta_sentences_about_the_reply_are_dropped():
    reply = (
        "Ich habe keine Details zum rag-helper geliefert. "
        "Der RAG-Helper indexiert Repository-Pfade mit einem Profil."
    )
    assert strip_meta(reply) == "Der RAG-Helper indexiert Repository-Pfade mit einem Profil."
    assert strip_meta("Meine Antwort basiert auf dem Index. Klar!") == "Klar!"
    assert strip_meta("Ich habe heute gute Laune!") == "Ich habe heute gute Laune!"


def test_the_live_meta_reply_becomes_a_plain_statement_with_an_offer():
    port = Port("Ich habe keine belegten Details zum rag-helper geliefert.")
    dialog, _tools, _retriever = companion(port, Retriever(snippets=[]))
    reply, _trace = dialog.answer(QUESTION)
    assert "geliefert" not in reply
    assert reply == "Im Projektindex ist das nicht belegt; soll ich gezielt nach „rag-helper“ suchen?"
