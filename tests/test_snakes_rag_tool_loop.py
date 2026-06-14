from __future__ import annotations


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
        posted_payloads.append(dict(json or {}))
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
    assert any(
        msg.get("role") == "user"
        and "Recherche-Stand fuer die naechste Aktion" in msg.get("content", "")
        and "agent/example.py" in msg.get("content", "")
        for msg in second_messages
    )
