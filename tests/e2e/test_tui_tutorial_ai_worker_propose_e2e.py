from __future__ import annotations

import json
from urllib.error import URLError

from client_surfaces.operator_tui.interactive import InteractiveOperatorTui
from client_surfaces.operator_tui.models import FocusPane, OperatorState


def test_tutorial_ai_e2e_falls_back_from_worker_propose_to_lmstudio_defaults(monkeypatch) -> None:
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.CONTENT, section_id="tasks")
    tui = InteractiveOperatorTui(state)
    monkeypatch.setenv("ANANTA_TUI_SNAKE_AI_BACKEND", "worker-propose")
    monkeypatch.delenv("ANANTA_TUI_SNAKE_AI_MODEL", raising=False)
    monkeypatch.delenv("ANANTA_TUI_SNAKE_AI_API_BASE_URL", raising=False)
    monkeypatch.delenv("ANANTA_TUI_SNAKE_AI_API_TOKEN", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)
    monkeypatch.setattr(tui, "_load_codecompass_hints", lambda now: ["method · render_operator_shell"])
    monkeypatch.setattr(tui, "_load_rag_helper_context", lambda now: ["architecture · Hub owns orchestration"])

    captured: dict[str, object] = {"urls": [], "lmstudio_body": ""}

    class _FakeResp:
        def __init__(self, payload: str) -> None:
            self._payload = payload

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return self._payload.encode("utf-8")

    def _fake_urlopen(req, timeout=0):
        url = req.full_url
        urls = captured["urls"]
        assert isinstance(urls, list)
        urls.append(url)
        if url.endswith("/step/propose"):
            raise URLError("worker unavailable")
        if url in {
            "http://192.168.178.100:1234/v1/chat/completions",
            "http://lmstudio.test/v1/chat/completions",
        }:
            captured["lmstudio_body"] = req.data.decode("utf-8")
            return _FakeResp('{"choices":[{"message":{"content":"Open tasks and inspect failures first."}}]}')
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr("client_surfaces.operator_tui.interactive.urllib.request.urlopen", _fake_urlopen)

    tip = tui._tutorial_ai_tip(now=10.0)

    assert tip == "Open tasks and inspect failures first."
    assert "http://localhost:5000/step/propose" in captured["urls"]
    assert any(url.endswith("/v1/chat/completions") for url in captured["urls"])
    body = json.loads(str(captured["lmstudio_body"]))
    assert body["model"] == "google/gemma-4-e4b"


def test_tutorial_ai_e2e_worker_target_tag_steers_snake_to_detail_zone(monkeypatch) -> None:
    game = {
        "active": True,
        "alive": True,
        "ui_steering": True,
        "free_mode": True,
        "tutorial_mode": True,
        "local_snake_id": "s1",
        "snake": [(8, 8), (7, 8), (6, 8)],
        "trail_path": [(8, 8), (7, 8), (6, 8)],
        "last_move": 0.0,
    }
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.HEADER, header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    monkeypatch.setenv("ANANTA_TUI_SNAKE_AI_BACKEND", "worker-propose")
    monkeypatch.setattr(tui, "_load_codecompass_hints", lambda now: ["artifact view"])
    monkeypatch.setattr(tui, "_load_rag_helper_context", lambda now: ["details panel"])

    class _FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return b'{"status":"success","data":{"reason":"[target=detail] Inspect artifact details now."}}'

    monkeypatch.setattr("client_surfaces.operator_tui.interactive.urllib.request.urlopen", lambda req, timeout=0: _FakeResp())

    snakes = {
        "s1": {
            "id": "s1",
            "snake": [(8, 8), (7, 8), (6, 8)],
            "trail_path": [(8, 8), (7, 8), (6, 8)],
            "message": "local",
            "snake_color": "mint",
        }
    }

    tui._update_tutorial_ai_snake(game, snakes, now=5.0, board_w=120, board_h=30, enabled=True)

    ai = snakes.get("s-ai")
    assert isinstance(ai, dict)
    assert ai.get("message") == "Inspect artifact details now."
    target = ai.get("target_cell")
    assert isinstance(target, tuple)
    assert target[0] >= 90
    assert target[1] >= 20
