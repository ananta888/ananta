from __future__ import annotations

import json

from client_surfaces.operator_tui.chat_memory import ChatMemoryContext
from client_surfaces.operator_tui.chat_message_formatter import ChatMessageFormatterMixin
from client_surfaces.operator_tui.models import FocusPane, OperatorState


class _FakeResp:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return b'{"answer":"hub answer","trace":{"rag":{"source":"hub_rag"}}}'


class _FormatterHarness(ChatMessageFormatterMixin):
    def __init__(self, state: OperatorState) -> None:
        self.state = state

    def _set_state(self, state: OperatorState) -> None:
        self.state = state

    def _rag_context_for_question(self, *args, **kwargs) -> list[str]:
        return ["client rag snippet"]

    def _chat_codecompass_context_for_question(self, **kwargs) -> list[str]:
        return ["client code ref"]

    def _build_active_target_excerpt(self) -> str:
        return ""

    def _chat_answer_char_limit(self) -> int:
        return 4000


def test_worker_v2_payload_leaves_grounding_context_to_hub(monkeypatch) -> None:
    captured: dict[str, object] = {}
    game: dict[str, object] = {
        "chat_backend": "ananta-worker",
        "chat_backend_model": "test-model",
        "chat_retrieval_profile": "repo_first",
        "chat_codecompass_trigger_mode": "force_repo_first",
        "chat_code_questions_repo_first": True,
        "chat_use_codecompass": True,
        "chat_include_local_project": True,
        "chat_include_wikipedia": False,
        "chat_include_task_memory": False,
        "chat_source_pack_id": "local-project",
        "chat_context_chars": 4000,
        "chat_rag_top_k": 12,
        "chat_answer_chars": 1800,
        "chat_max_tokens": 512,
    }
    game["chat_state"] = {
        "active_session_id": "custom",
        "active_channel": "ai:custom",
        "ai_sessions": [
            {
                "id": "custom",
                "name": "Custom",
                "settings": {
                    "chat_backend": "ananta-worker",
                    "chat_backend_model": "test-model",
                    "chat_retrieval_profile": "repo_first",
                    "chat_codecompass_trigger_mode": "force_repo_first",
                    "chat_code_questions_repo_first": True,
                    "chat_include_task_memory": False,
                    "chat_source_pack_id": "local-project",
                },
            }
        ],
        "channels": {"ai:custom": {"messages": []}},
    }
    state = OperatorState(
        endpoint="http://localhost:5000",
        focus=FocusPane.CONTENT,
        header_logo_game=game,
    )
    tui = _FormatterHarness(state)

    monkeypatch.delenv("ANANTA_TUI_CHAT_BACKEND", raising=False)

    def _fake_urlopen(req, timeout=0):
        captured["url"] = req.full_url
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        return _FakeResp()

    monkeypatch.setattr(
        "client_surfaces.operator_tui.chat_message_formatter.urllib.request.urlopen",
        _fake_urlopen,
    )

    answer = tui._resolve_ask_question(
        "Erklaere das Modul alpha-tools",
        depth="expert",
        hints=[],
        rag_context=[],
        memory=ChatMemoryContext(
            recent_turns=[],
            rolling_summary="previous discussion",
            codecompass_refs=["client side code ref"],
            rag_snippets=["client side rag snippet"],
        ),
    )

    assert answer == "hub answer"
    assert captured["url"] == "http://localhost:5000/snake/ask"
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["context"] == ""
    assert payload["question"] == "Erklaere das Modul alpha-tools"
    assert payload["memory_context"]["rolling_summary"] == "previous discussion"
    assert payload["retrieval_config"] == {
        "chat_retrieval_profile": "repo_first",
        "chat_codecompass_trigger_mode": "force_repo_first",
        "chat_code_questions_repo_first": True,
        "chat_use_codecompass": True,
        "chat_include_local_project": True,
        "chat_include_wikipedia": False,
        "chat_include_task_memory": False,
        "chat_source_pack_id": "local-project",
    }
