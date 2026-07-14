"""API-Tests für die KI-gestützten Chat-Endpunkte:

- POST /api/chat/sessions/ai-reorganize   (LLM-Vorschlag, Heuristik-Fallback)
- GET  /api/chat/sessions/<id>/context-overview
- POST /api/chat/sessions/<id>/summarize  (LLM, extraktiver Fallback)
- POST /api/chat/sessions/<id>/prompt-preview (read-only Assembly-Vorschau)

Alle LLM-Aufrufe laufen über
``agent.services.chat_partial_summary_service.call_llm_text`` (das
ai-reorganize-Handling importiert die Funktion erst im Funktions-Body,
der Summary-Service ruft sie als Modul-Global auf) — deshalb wird genau
dort gepatcht, damit die Tests deterministisch sind und nie ein echtes
LLM treffen.
"""
from __future__ import annotations

import copy
import json
import re
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from agent.ai_agent import create_app
from agent.services.user_session_tokens import issue_user_access_token

LLM_PATCH_TARGET = "agent.services.chat_partial_summary_service.call_llm_text"

FOLDER_ID_RE = re.compile(r"^folder-[0-9a-f]{12}$")


# ── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture
def app():
    return create_app(testing=True)


@pytest.fixture
def client(app):
    with app.test_client() as c:
        token = issue_user_access_token(username="admin", role="admin")
        c.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        yield c


# ── Test helpers ──────────────────────────────────────────────────────────

def _make_session(session_id: str, **kwargs) -> dict:
    """Fully populated session dict (via make_session, incl. default settings)."""
    from client_surfaces.operator_tui.chat_state import make_session

    return make_session(session_id=session_id, name=kwargs.pop("name", session_id), **kwargs)


@contextmanager
def store_ctx(sessions: list | None = None, folders: list | None = None, active_id: str = ""):
    """Patch get_manager in chat routes to an in-memory store (never touches
    the real user.json). Same isolation approach as test_chat_api.py."""
    sessions = sessions or []
    store: dict = {
        "chat_sessions": copy.deepcopy(sessions),
        "chat_active_session_id": active_id or (sessions[0]["id"] if sessions else ""),
        "chat_folders": copy.deepcopy(folders or []),
    }

    mock_mgr = MagicMock()
    mock_mgr.load.side_effect = lambda: copy.deepcopy(store)

    def _save(data: dict) -> bool:
        for key, value in data.items():
            store[key] = value
        return True

    mock_mgr.save.side_effect = _save

    with patch("agent.routes.chat.get_manager", return_value=mock_mgr):
        yield store, mock_mgr


def _three_sessions() -> list[dict]:
    return [
        _make_session("s-code", name="Code-Hilfe", group="Code"),
        _make_session("s-write", name="Schreib-Coach", group="Schreiben"),
        _make_session("s-misc", name="Sonstiges"),  # no group/type → "Allgemein"
    ]


# ── POST /api/chat/sessions/ai-reorganize ─────────────────────────────────

def test_ai_reorganize_heuristic_fallback_when_llm_empty(client):
    """Empty LLM response → heuristic grouping by session group/type."""
    with store_ctx(sessions=_three_sessions()):
        with patch(LLM_PATCH_TARGET, return_value=""):
            r = client.post("/api/chat/sessions/ai-reorganize")
    assert r.status_code == 200
    body = r.json
    assert body["method"] == "heuristic"
    assert body["folders"], "heuristic must propose at least one folder"
    folder_ids = {f["id"] for f in body["folders"]}
    # Every assignment must point to a proposed folder and a real session
    session_ids = {"s-code", "s-write", "s-misc"}
    assert set(body["assignments"].keys()) == session_ids
    for fid in body["assignments"].values():
        assert fid in folder_ids
    assert "Heuristik" in body["summary"]


def test_ai_reorganize_heuristic_fallback_when_llm_raises(client):
    with store_ctx(sessions=_three_sessions()):
        with patch(LLM_PATCH_TARGET, side_effect=RuntimeError("backend down")):
            r = client.post("/api/chat/sessions/ai-reorganize")
    assert r.status_code == 200
    assert r.json["method"] == "heuristic"
    assert r.json["folders"]


def test_ai_reorganize_llm_success_remaps_folder_ids(client):
    """Valid strict LLM JSON → method 'llm'; proposed ids (f1, f2) are
    remapped to fresh folder-<uuid12> ids, assignments consistently."""
    with store_ctx(sessions=_three_sessions()):
        # Build the fake LLM answer dynamically from the real session ids
        listing = client.get("/api/chat/sessions")
        assert listing.status_code == 200
        sids = [s["id"] for s in listing.json]
        assert len(sids) >= 2
        fake_answer = json.dumps({
            "folders": [
                {"id": "f1", "name": "Code", "icon": "💻", "parent_id": ""},
                {"id": "f2", "name": "Rest", "icon": "📁", "parent_id": ""},
            ],
            "assignments": {sids[0]: "f1", **{sid: "f2" for sid in sids[1:]}},
        })
        with patch(LLM_PATCH_TARGET, return_value=fake_answer):
            r = client.post("/api/chat/sessions/ai-reorganize")

    assert r.status_code == 200
    body = r.json
    assert body["method"] == "llm"
    assert len(body["folders"]) == 2
    for f in body["folders"]:
        assert FOLDER_ID_RE.match(f["id"]), f["id"]  # uuid-style, not "f1"/"f2"
    # Assignments remapped consistently: sids[0] → "Code" folder, rest → "Rest"
    code_folder = next(f for f in body["folders"] if f["name"] == "Code")
    rest_folder = next(f for f in body["folders"] if f["name"] == "Rest")
    assert body["assignments"][sids[0]] == code_folder["id"]
    for sid in sids[1:]:
        assert body["assignments"][sid] == rest_folder["id"]
    assert set(body["assignments"].keys()) == set(sids)
    assert "KI" in body["summary"]


def test_ai_reorganize_llm_garbage_falls_back_to_heuristic(client):
    with store_ctx(sessions=_three_sessions()):
        with patch(LLM_PATCH_TARGET, return_value="Klar! Hier dein Vorschlag: keine JSON."):
            r = client.post("/api/chat/sessions/ai-reorganize")
    assert r.status_code == 200
    assert r.json["method"] == "heuristic"
    assert r.json["folders"]
    assert "Heuristik" in r.json["summary"]


# ── GET /api/chat/sessions/<id>/context-overview ──────────────────────────

def test_context_overview_known_session(client):
    long_prompt = "Du bist ein sehr hilfreicher Assistent. " * 10  # > 200 chars
    session = _make_session("ctx-sess", system_prompt=long_prompt)
    with store_ctx(sessions=[session]):
        r = client.get("/api/chat/sessions/ctx-sess/context-overview")
    assert r.status_code == 200
    body = r.json
    assert body["session_id"] == "ctx-sess"
    # system_prompt: full length reported, preview truncated to 200 chars + ellipsis
    assert body["system_prompt"]["enabled"] is True
    assert body["system_prompt"]["chars"] == len(long_prompt)
    assert body["system_prompt"]["text"].endswith("…")
    assert len(body["system_prompt"]["text"]) == 201
    # history/summary/rag reflect the default session settings
    assert body["history"] == {"enabled": True, "max_turns": 6, "max_chars": 1800}
    assert body["summary"] == {"enabled": True, "max_chars": 600}
    assert body["rag"]["enabled"] is True
    assert body["rag"]["profile"] == "auto"
    assert body["rag"]["top_k"] == 12
    assert body["rag"]["max_chars"] == 4000


def test_context_overview_unknown_session_returns_404(client):
    with store_ctx(sessions=[_make_session("only")]):
        r = client.get("/api/chat/sessions/nope/context-overview")
    assert r.status_code == 404
    assert "not found" in r.json["error"]


# ── POST /api/chat/sessions/<id>/summarize ────────────────────────────────

_SUMMARIZE_MESSAGES = [
    {"sender": "user", "text": "Wir haben den Bug in parser.py gefunden. Er betrifft alle Läufe."},
    {"sender": "ai", "text": "Der Fix ist in Zeile 42. Ich schlage einen Regressionstest vor."},
]


def test_summarize_llm_success(client):
    fixed = "Bug in parser.py gefunden, Fix in Zeile 42."
    with store_ctx(sessions=[_make_session("s1")]):
        with patch(LLM_PATCH_TARGET, return_value=fixed):
            r = client.post("/api/chat/sessions/s1/summarize",
                            json={"messages": _SUMMARIZE_MESSAGES})
    assert r.status_code == 200
    body = r.json
    assert body["method"] == "llm"
    assert body["summary"] == fixed  # short enough — no truncation
    assert body["source_count"] == len(_SUMMARIZE_MESSAGES)
    assert body["chars"] == len(fixed)


def test_summarize_extractive_fallback_when_llm_empty(client):
    with store_ctx(sessions=[_make_session("s1")]):
        with patch(LLM_PATCH_TARGET, return_value=""):
            r = client.post("/api/chat/sessions/s1/summarize",
                            json={"messages": _SUMMARIZE_MESSAGES, "target_chars": 200})
    assert r.status_code == 200
    body = r.json
    assert body["method"] == "extractive"
    assert body["summary"], "extractive fallback must produce a non-empty summary"
    assert len(body["summary"]) <= 200
    assert body["source_count"] == len(_SUMMARIZE_MESSAGES)
    # Extractive summary keeps sender prefixes
    assert "user:" in body["summary"]


def test_summarize_empty_messages_returns_400(client):
    with store_ctx(sessions=[_make_session("s1")]):
        r = client.post("/api/chat/sessions/s1/summarize", json={"messages": []})
        assert r.status_code == 400
        assert "messages" in r.json["error"]
        # Messages with empty text are rejected as well
        r2 = client.post("/api/chat/sessions/s1/summarize",
                         json={"messages": [{"sender": "user", "text": "   "}]})
    assert r2.status_code == 400


def test_summarize_unknown_session_returns_404(client):
    with store_ctx(sessions=[_make_session("only")]):
        r = client.post("/api/chat/sessions/nope/summarize",
                        json={"messages": _SUMMARIZE_MESSAGES})
    assert r.status_code == 404
    assert "not found" in r.json["error"]


# ── POST /api/chat/sessions/<id>/prompt-preview ───────────────────────────

_SECTION_ORDER = ["system_prompt", "summary", "history", "rag", "user_message"]


def test_prompt_preview_basic(client):
    session = _make_session("pp", system_prompt="Du bist ein Test-Assistent.")
    history = [
        {"sender": "user", "text": "Frage 1"},
        {"sender": "ai", "text": "Antwort 1"},
        {"sender": "user", "text": "Frage 2"},
    ]
    with store_ctx(sessions=[session]):
        r = client.post("/api/chat/sessions/pp/prompt-preview",
                        json={"message": "Was ist X?", "history": history})
    assert r.status_code == 200
    body = r.json
    assert body["session_id"] == "pp"
    assert [s["name"] for s in body["sections"]] == _SECTION_ORDER
    user_section = body["sections"][-1]
    assert user_section["text"] == "Was ist X?"
    assert user_section["enabled"] is True
    assert user_section["chars"] == len("Was ist X?")
    # 3 history entries fit into the default 6-turn window — no truncation
    history_section = next(s for s in body["sections"] if s["name"] == "history")
    assert history_section["truncated"] is False
    assert history_section["text"] == "user: Frage 1\nai: Antwort 1\nuser: Frage 2"
    assert body["total_chars"] == len(body["assembled_prompt"])
    assert "## Neue Nachricht" in body["assembled_prompt"]


def test_prompt_preview_history_truncated_by_turn_limit(client):
    session = _make_session("pp-trunc", settings={"chat_history_turns": 2})
    history = [{"sender": "user", "text": f"Nachricht {i}"} for i in range(5)]
    with store_ctx(sessions=[session]):
        r = client.post("/api/chat/sessions/pp-trunc/prompt-preview",
                        json={"message": "Weiter", "history": history})
    assert r.status_code == 200
    history_section = next(s for s in r.json["sections"] if s["name"] == "history")
    assert history_section["truncated"] is True
    # Only the last 2 turns are kept
    assert history_section["text"] == "user: Nachricht 3\nuser: Nachricht 4"
    assert "Nachricht 0" not in history_section["text"]


def test_prompt_preview_summary_section(client):
    session = _make_session("pp-sum")  # chat_use_summary defaults to True
    with store_ctx(sessions=[session]):
        r = client.post("/api/chat/sessions/pp-sum/prompt-preview",
                        json={"message": "Weiter", "summary": "Bisher: alles gut."})
    assert r.status_code == 200
    summary_section = next(s for s in r.json["sections"] if s["name"] == "summary")
    assert summary_section["enabled"] is True
    assert summary_section["text"] == "Bisher: alles gut."
    assert summary_section["truncated"] is False
    assert "## Zusammenfassung\nBisher: alles gut." in r.json["assembled_prompt"]


def test_prompt_preview_missing_message_returns_400(client):
    with store_ctx(sessions=[_make_session("pp")]):
        r = client.post("/api/chat/sessions/pp/prompt-preview", json={"summary": "x"})
        assert r.status_code == 400
        # Whitespace-only message is also rejected
        r2 = client.post("/api/chat/sessions/pp/prompt-preview", json={"message": "   "})
    assert r2.status_code == 400


def test_prompt_preview_unknown_session_returns_404(client):
    with store_ctx(sessions=[_make_session("only")]):
        r = client.post("/api/chat/sessions/nope/prompt-preview", json={"message": "Hi"})
    assert r.status_code == 404
    assert "not found" in r.json["error"]


def test_prompt_preview_is_read_only(client):
    """The preview must not modify any persisted session state."""
    with store_ctx(sessions=[_make_session("pp", system_prompt="SP")]):
        before = client.get("/api/chat/sessions").json
        r = client.post("/api/chat/sessions/pp/prompt-preview",
                        json={"message": "Hi", "summary": "S",
                              "history": [{"sender": "user", "text": "alt"}]})
        assert r.status_code == 200
        after = client.get("/api/chat/sessions").json
    assert before == after
