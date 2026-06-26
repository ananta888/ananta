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


class _RecordingTrace:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def event(self, phase: str, title: str, **kwargs) -> None:
        self.events.append({"phase": phase, "title": title, **kwargs})


def test_tool_read_file_auto_corrects_unique_missing_path(tmp_path):
    source = tmp_path / "worker" / "retrieval" / "codecompass_architecture_query.py"
    source.parent.mkdir(parents=True)
    source.write_text("def run_architecture_query():\n    return 'ok'\n", encoding="utf-8")

    from agent.routes.snakes_rag_tool_loop import _tool_read_file

    result = _tool_read_file(
        "agent/services/tools/codecompass_architecture_query.py",
        tmp_path,
        max_chars=5000,
    )

    assert result.startswith(
        "[Pfad automatisch korrigiert: "
        "agent/services/tools/codecompass_architecture_query.py -> "
        "worker/retrieval/codecompass_architecture_query.py]"
    )
    assert "def run_architecture_query" in result


def test_tool_read_file_keeps_hint_for_ambiguous_missing_path(tmp_path):
    first = tmp_path / "agent" / "same.py"
    second = tmp_path / "worker" / "same.py"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_text("FIRST = True\n", encoding="utf-8")
    second.write_text("SECOND = True\n", encoding="utf-8")

    from agent.routes.snakes_rag_tool_loop import _tool_read_file

    result = _tool_read_file("missing/same.py", tmp_path, max_chars=5000)

    assert result.startswith("[Fehler: Datei nicht gefunden: missing/same.py]")
    assert "Korrekter Pfad" in result
    assert "FIRST = True" not in result
    assert "SECOND = True" not in result


def test_tool_loop_continues_after_ambiguous_path_hint_when_unlimited(tmp_path, monkeypatch):
    first = tmp_path / "agent" / "same.py"
    second = tmp_path / "worker" / "same.py"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_text("FIRST = True\n", encoding="utf-8")
    second.write_text("SECOND = True\n", encoding="utf-8")

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
                                "arguments": '{"path": "missing/same.py"}',
                            },
                        }],
                    },
                }]
            })
        if len(posted_payloads) == 2:
            prompt = "\n".join(
                str(msg.get("content") or "") for msg in posted_payloads[-1]["messages"]
            )
            assert "Datei nicht gefunden: missing/same.py" in prompt
            assert "agent/same.py" in prompt
            assert "worker/same.py" in prompt
            return _FakeResponse({
                "choices": [{
                    "finish_reason": "tool_calls",
                    "message": {
                        "content": "",
                        "tool_calls": [{
                            "id": "call_2",
                            "function": {
                                "name": "read_file",
                                "arguments": '{"path": "worker/same.py"}',
                            },
                        }],
                    },
                }]
            })
        return _FakeResponse({
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": "final answer after choosing worker/same.py"},
            }]
        })

    monkeypatch.setattr("requests.post", _fake_post)

    answer, trace = run_rag_chat_tool_loop(
        messages=[{"role": "user", "content": "Frage: was ist in same.py?"}],
        provider="lmstudio",
        model="test-model",
        repo_root=tmp_path,
        max_tool_calls=0,
        max_chars_per_file=5000,
        question="was ist in same.py?",
    )

    assert answer == "final answer after choosing worker/same.py"
    assert trace["tool_calls_made"] == 2
    assert len(posted_payloads) == 3


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
    first_prompt = "\n".join(str(msg.get("content") or "") for msg in posted_payloads[0]["messages"])
    second_prompt = "\n".join(str(msg.get("content") or "") for msg in posted_payloads[1]["messages"])
    assert "FULL_INITIAL_FILE_BODY" not in first_prompt
    assert "Initial file summary" in first_prompt
    assert "Bereits gelesene CodeCompass-Top-Treffer (kompakt)" in first_prompt
    assert "FULL_INITIAL_FILE_BODY" not in second_prompt
    assert "Bereits gelesene CodeCompass-Top-Treffer (kompakt)" in second_prompt
    assert "Initial file summary" in second_prompt
    assert first_prompt.count("Recherche-Stand fuer die naechste LLM-Aktion") == 1
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
    first_main_payload = next(payload for payload in posted_payloads if payload.get("tools"))
    first_main_prompt = "\n".join(str(msg.get("content") or "") for msg in first_main_payload["messages"])
    assert "RAW_INITIAL_CONTEXT" not in first_main_prompt
    assert "LLM summary for initial evidence" in first_main_prompt
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


def test_tool_loop_rejects_textual_tool_request_as_final_answer(tmp_path, monkeypatch):
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
                    "finish_reason": "stop",
                    "message": {
                        "content": '[TOOL_REQUEST]\n{"name":"search_codebase","arguments":{"query":"x"}}\n[END_TOOL_REQUEST]',
                    },
                }]
            })
        return _FakeResponse({
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": "final normal answer"},
            }]
        })

    monkeypatch.setattr("requests.post", _fake_post)

    answer, trace = run_rag_chat_tool_loop(
        messages=[{"role": "user", "content": "Frage: erklaere x"}],
        provider="lmstudio",
        model="test-model",
        repo_root=tmp_path,
        max_tool_calls=0,
    )

    assert answer == "final normal answer"
    assert trace["rejected_final_tool_request"] is True
    assert "tools" not in posted_payloads[1]
    assert "Tool-Aufrufe sind jetzt nicht mehr erlaubt" in posted_payloads[1]["messages"][-1]["content"]


def test_tool_loop_records_textual_tool_request_trace_event(tmp_path, monkeypatch):
    from agent.routes.snakes_rag_tool_loop import run_rag_chat_tool_loop

    monkeypatch.setattr(
        "agent.llm_integration._runtime_provider_urls",
        lambda: {"lmstudio": "http://llm.test/v1"},
    )
    monkeypatch.setattr("agent.llm_integration._runtime_api_key", lambda _provider: "")

    def _fake_post(_endpoint, *, json=None, **_kwargs):
        messages = list((json or {}).get("messages") or [])
        if len(messages) == 1:
            return _FakeResponse({
                "choices": [{
                    "finish_reason": "stop",
                    "message": {
                        "content": '[TOOL_REQUEST]\n{"name":"search_codebase","arguments":{"query":"agent/x.py"}}\n[END_TOOL_REQUEST]',
                    },
                }]
            })
        return _FakeResponse({
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": "final normal answer"},
            }]
        })

    monkeypatch.setattr("requests.post", _fake_post)
    rec = _RecordingTrace()

    answer, trace = run_rag_chat_tool_loop(
        messages=[{"role": "user", "content": "Frage: erklaere x"}],
        provider="lmstudio",
        model="test-model",
        repo_root=tmp_path,
        max_tool_calls=0,
        rec=rec,
    )

    event = next(item for item in rec.events if item["phase"] == "tool_loop_llm_1_textual_tool_request")
    assert answer == "final normal answer"
    assert trace["rejected_final_tool_request"] is True
    assert event["status"] == "blocked"
    assert "[TOOL_REQUEST]" in event["output_preview"]


def test_tool_loop_llm_done_trace_includes_tool_call_arguments(tmp_path, monkeypatch):
    from agent.routes.snakes_rag_tool_loop import run_rag_chat_tool_loop

    monkeypatch.setattr(
        "agent.llm_integration._runtime_provider_urls",
        lambda: {"lmstudio": "http://llm.test/v1"},
    )
    monkeypatch.setattr("agent.llm_integration._runtime_api_key", lambda _provider: "")
    rec = _RecordingTrace()
    calls = 0

    def _fake_post(_endpoint, *, json=None, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return _FakeResponse({
                "choices": [{
                    "finish_reason": "tool_calls",
                    "message": {
                        "content": "",
                        "tool_calls": [{
                            "id": "call_search_1",
                            "function": {
                                "name": "search_codebase",
                                "arguments": '{"query": "agent/services/agent_registry_service.py", "max_results": 5}',
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
        messages=[{"role": "user", "content": "Frage: suche"}],
        provider="lmstudio",
        model="test-model",
        repo_root=tmp_path,
        max_tool_calls=1,
        rec=rec,
    )

    done = next(item for item in rec.events if item["phase"] == "tool_loop_llm_1_done")
    detail = done["details"]["tool_call_details"][0]
    assert answer == "final answer"
    assert trace["tool_calls_made"] == 1
    assert detail["id"] == "call_search_1"
    assert detail["name"] == "search_codebase"
    assert detail["arguments"]["query"] == "agent/services/agent_registry_service.py"
    assert "search_codebase" in done["output_preview"]


def test_search_codebase_filters_generated_codecompass_outputs(tmp_path, monkeypatch):
    from agent.routes.snakes_rag_tool_loop import _tool_search_codebase

    class _Chunk:
        def __init__(self, source: str, score: float) -> None:
            self.source = source
            self.score = score

    class _FakeRepositoryMapEngine:
        def __init__(self, _repo_root):
            pass

        def build(self):
            return None

        def search(self, *_args, **_kwargs):
            return [
                _Chunk("rag-helper/out/index_by_kind/python_file.jsonl", 100.0),
                _Chunk("agent/real.py", 90.0),
            ]

    monkeypatch.setattr("agent.hybrid_orchestrator.RepositoryMapEngine", _FakeRepositoryMapEngine)

    result = _tool_search_codebase("codecompass", 8, tmp_path)

    assert "agent/real.py" in result
    assert "rag-helper/out/index_by_kind/python_file.jsonl" not in result
