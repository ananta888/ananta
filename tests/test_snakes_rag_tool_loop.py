from __future__ import annotations

import copy
import threading


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def test_tool_loop_adds_evidence_memory_to_followup_llm_call(tmp_path, monkeypatch):
    source = tmp_path / "agent" / "example.py"
    source.parent.mkdir()
    source.write_text("def important():\n    return 'evidence'\n", encoding="utf-8")

    from agent.routes.snakes_rag_tool_loop import run_rag_chat_tool_loop

    monkeypatch.setattr(
        "agent.llm_integration._runtime_provider_urls",
        lambda: {"lmstudio": "http://llm.test/v1"},
    )
    monkeypatch.setattr("agent.llm_integration._runtime_api_key", lambda _provider: "")

    posted_payloads: list[dict] = []

    def _fake_post(_endpoint, *, json=None, **_kwargs):
        posted_payloads.append(copy.deepcopy(dict(json or {})))
        if len(posted_payloads) == 1:
            return _FakeResponse({
                "choices": [{
                    "finish_reason": "tool_calls",
                    "message": {
                        "content": "",
                        "tool_calls": [{
                            "id": "call_1",
                            "function": {
                                "name": "read_file",
                                "arguments": '{"path": "agent/example.py"}',
                            },
                        }],
                    },
                }]
            })
        return _FakeResponse({
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": "final answer"},
            }]
        })

    monkeypatch.setattr("requests.post", _fake_post)

    answer, trace = run_rag_chat_tool_loop(
        messages=[{"role": "user", "content": "Frage: was ist wichtig?"}],
        provider="lmstudio",
        model="test-model",
        repo_root=tmp_path,
        max_tool_calls=2,
        max_chars_per_file=5000,
        question="was ist wichtig?",
        summarize_reads=False,
    )

    assert answer == "final answer"
    assert trace["evidence"][0]["path"] == "agent/example.py"
    second_messages = posted_payloads[1]["messages"]
    evidence_messages = [
        msg for msg in second_messages
        if msg.get("role") == "user"
        and "Recherche-Stand fuer die naechste LLM-Aktion" in msg.get("content", "")
    ]
    assert len(evidence_messages) == 1
    assert any(
        msg.get("role") == "user"
        and "Recherche-Stand fuer die naechste LLM-Aktion" in msg.get("content", "")
        and "agent/example.py" in msg.get("content", "")
        for msg in second_messages
    )


def test_tool_loop_compacts_initial_packed_files_for_followup_llm_call(tmp_path, monkeypatch):
    source = tmp_path / "agent" / "next.py"
    source.parent.mkdir()
    source.write_text("def next_step():\n    return 'ok'\n", encoding="utf-8")

    from agent.routes.snakes_rag_tool_loop import run_rag_chat_tool_loop

    monkeypatch.setattr(
        "agent.llm_integration._runtime_provider_urls",
        lambda: {"lmstudio": "http://llm.test/v1"},
    )
    monkeypatch.setattr("agent.llm_integration._runtime_api_key", lambda _provider: "")

    posted_payloads: list[dict] = []

    def _fake_post(_endpoint, *, json=None, **_kwargs):
        posted_payloads.append(copy.deepcopy(dict(json or {})))
        if len(posted_payloads) == 1:
            return _FakeResponse({
                "choices": [{
                    "finish_reason": "tool_calls",
                    "message": {
                        "content": "",
                        "tool_calls": [{
                            "id": "call_1",
                            "function": {
                                "name": "read_file",
                                "arguments": '{"path": "agent/next.py"}',
                            },
                        }],
                    },
                }]
            })
        return _FakeResponse({
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": "final answer"},
            }]
        })

    monkeypatch.setattr("requests.post", _fake_post)

    large_initial_body = "FULL_INITIAL_FILE_BODY " * 1000
    initial_message = (
        "Frage: erklaere x\n\n"
        "=== Bereits gelesene CodeCompass-Top-Treffer ===\n"
        f"1. agent/initial.py\n```\n{large_initial_body}\n```\n\n"
        "=== Verfügbare Dateien (2 gefunden, nach Relevanz) ===\n"
        "1. agent/initial.py (relevanz: 10.0)\n"
        "2. agent/next.py (relevanz: 9.0)\n"
    )

    answer, trace = run_rag_chat_tool_loop(
        messages=[{"role": "user", "content": initial_message}],
        provider="lmstudio",
        model="test-model",
        repo_root=tmp_path,
        max_tool_calls=2,
        max_chars_per_file=5000,
        question="erklaere x",
        initial_evidence=[{
            "path": "agent/initial.py",
            "summary": "Initial file summary",
            "score": 10.0,
            "source": "initial_context",
        }],
    )

    assert answer == "final answer"
    assert trace["initial_context_compacted_for_followups"] is True
    first_prompt = str(posted_payloads[0]["messages"][0]["content"])
    second_prompt = "\n".join(str(msg.get("content") or "") for msg in posted_payloads[1]["messages"])
    assert "FULL_INITIAL_FILE_BODY" in first_prompt
    assert "FULL_INITIAL_FILE_BODY" not in second_prompt
    assert "Bereits gelesene CodeCompass-Top-Treffer (kompakt)" in second_prompt
    assert "Initial file summary" in second_prompt
    assert second_prompt.count("Recherche-Stand fuer die naechste LLM-Aktion") == 1


def test_tool_loop_uses_llm_summary_for_initial_evidence_followup(tmp_path, monkeypatch):
    source = tmp_path / "agent" / "next.py"
    source.parent.mkdir()
    source.write_text("def next_step():\n    return 'ok'\n", encoding="utf-8")

    from agent.routes.snakes_rag_tool_loop import run_rag_chat_tool_loop

    monkeypatch.setattr(
        "agent.llm_integration._runtime_provider_urls",
        lambda: {"lmstudio": "http://llm.test/v1"},
    )
    monkeypatch.setattr("agent.llm_integration._runtime_api_key", lambda _provider: "")

    posted_payloads: list[dict] = []
    main_call_count = 0

    def _fake_post(_endpoint, *, json=None, **_kwargs):
        nonlocal main_call_count
        payload = copy.deepcopy(dict(json or {}))
        posted_payloads.append(payload)
        messages = payload.get("messages") or []
        prompt = str((messages[0] or {}).get("content") or "") if messages else ""
        if "Extrahiere AUSSCHLIESSLICH" in prompt:
            return _FakeResponse({
                "choices": [{
                    "finish_reason": "stop",
                    "message": {"content": "LLM summary for initial evidence"},
                }]
            })
        main_call_count += 1
        if main_call_count == 1:
            return _FakeResponse({
                "choices": [{
                    "finish_reason": "tool_calls",
                    "message": {
                        "content": "",
                        "tool_calls": [{
                            "id": "call_1",
                            "function": {
                                "name": "read_file",
                                "arguments": '{"path": "agent/next.py"}',
                            },
                        }],
                    },
                }]
            })
        return _FakeResponse({
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": "final answer"},
            }]
        })

    monkeypatch.setattr("requests.post", _fake_post)

    raw_initial = "RAW_INITIAL_CONTEXT " * 1000
    initial_message = (
        "Frage: erklaere x\n\n"
        "=== Bereits gelesene CodeCompass-Top-Treffer ===\n"
        f"1. agent/initial.py\n```\n{raw_initial}\n```\n\n"
        "=== Verfügbare Dateien (2 gefunden, nach Relevanz) ===\n"
        "1. agent/initial.py (relevanz: 10.0)\n"
        "2. agent/next.py (relevanz: 9.0)\n"
    )

    answer, trace = run_rag_chat_tool_loop(
        messages=[{"role": "user", "content": initial_message}],
        provider="lmstudio",
        model="test-model",
        repo_root=tmp_path,
        max_tool_calls=2,
        max_chars_per_file=5000,
        question="erklaere x",
        summarize_reads=True,
        max_summary_chars=500,
        initial_evidence=[{
            "path": "agent/initial.py",
            "summary": "fallback summary",
            "content": raw_initial,
            "score": 10.0,
            "source": "initial_context",
        }],
    )

    assert answer == "final answer"
    assert trace["evidence"][0]["summary"] == "[Zusammenfassung von agent/initial.py]\nLLM summary for initial evidence"
    followup_prompt = "\n".join(str(msg.get("content") or "") for msg in posted_payloads[-1]["messages"])
    assert "RAW_INITIAL_CONTEXT" not in followup_prompt
    assert "LLM summary for initial evidence" in followup_prompt


def test_tool_loop_forces_final_after_repeated_search_only_calls(tmp_path, monkeypatch):
    from agent.routes import snakes_rag_tool_loop as mod

    monkeypatch.setattr(
        "agent.llm_integration._runtime_provider_urls",
        lambda: {"lmstudio": "http://llm.test/v1"},
    )
    monkeypatch.setattr("agent.llm_integration._runtime_api_key", lambda _provider: "")
    monkeypatch.setattr(mod, "_dispatch_tool", lambda *_args, **_kwargs: "- agent/example.py (score: 1.0)")

    posted_payloads: list[dict] = []

    def _fake_post(_endpoint, *, json=None, **_kwargs):
        posted_payloads.append(copy.deepcopy(dict(json or {})))
        if len(posted_payloads) <= 3:
            return _FakeResponse({
                "choices": [{
                    "finish_reason": "tool_calls",
                    "message": {
                        "content": "",
                        "tool_calls": [{
                            "id": f"call_{len(posted_payloads)}",
                            "function": {
                                "name": "search_codebase",
                                "arguments": '{"query": "same thing"}',
                            },
                        }],
                    },
                }]
            })
        return _FakeResponse({
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": "final after search loop"},
            }]
        })

    monkeypatch.setattr("requests.post", _fake_post)

    answer, trace = mod.run_rag_chat_tool_loop(
        messages=[{"role": "user", "content": "Frage: suche endlos"}],
        provider="lmstudio",
        model="test-model",
        repo_root=tmp_path,
        max_tool_calls=10,
        max_chars_per_file=5000,
        question="suche endlos",
    )

    assert answer == "final after search loop"
    assert trace["tool_calls_made"] == 3
    assert "tools" not in posted_payloads[3]


def test_tool_loop_cancel_event_stops_before_llm_call(tmp_path, monkeypatch):
    from agent.routes.snakes_rag_tool_loop import run_rag_chat_tool_loop

    monkeypatch.setattr(
        "agent.llm_integration._runtime_provider_urls",
        lambda: {"lmstudio": "http://llm.test/v1"},
    )
    monkeypatch.setattr("agent.llm_integration._runtime_api_key", lambda _provider: "")

    cancel_event = threading.Event()
    cancel_event.set()

    def _fake_post(*_args, **_kwargs):
        raise AssertionError("LLM call should not be started after cancellation")

    monkeypatch.setattr("requests.post", _fake_post)

    answer, trace = run_rag_chat_tool_loop(
        messages=[{"role": "user", "content": "Frage: abbrechen"}],
        provider="lmstudio",
        model="test-model",
        repo_root=tmp_path,
        cancel_event=cancel_event,
    )

    assert answer == ""
    assert trace["cancelled"] is True
