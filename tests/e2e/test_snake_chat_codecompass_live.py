"""E2E test: Snake-Chat liefert echten CodeCompass-Kontext aus dem Ananta-Projekt.

Voraussetzung:
    - Hub läuft (ANANTA_HUB_URL oder http://localhost:5000)
    - Ananta-Codebase ist indiziert (setup_codecompass_index.py ausgeführt)

Umgebungsvariablen:
    ANANTA_HUB_URL         Hub-Adresse (default: http://localhost:5000)
    INITIAL_ADMIN_USER     Login (default: admin)
    INITIAL_ADMIN_PASSWORD Passwort (default: test123)
    ANANTA_E2E_SNAKE_LIVE  Auf 1 setzen um diesen Test auszuführen

Dieser Test:
  1. Indexiert eine kleine bekannte Teilmenge des Ananta-Repos
  2. Registriert eine Snake
  3. Sendet eine Frage, die direkt auf indizierten Code verweist
  4. Wartet auf die Antwort
  5. Prüft dass "[Kontext: 0 Treffer]" NICHT vorkommt
  6. Prüft dass mindestens ein Kontext-Treffer erwähnt wird
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

HUB_URL = os.environ.get("ANANTA_HUB_URL", "http://localhost:5000").rstrip("/")
ADMIN_USER = os.environ.get("INITIAL_ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("INITIAL_ADMIN_PASSWORD", "test123")
_LIVE_FLAG = "ANANTA_E2E_SNAKE_LIVE"

PROBE_FILES = [
    "agent/routes/snakes.py",
    "agent/routes/share_sessions.py",
    "agent/routes/pair_groups.py",
    "agent/services/share_session_service.py",
]


def _require_live() -> None:
    if os.environ.get(_LIVE_FLAG, "").strip().lower() not in {"1", "true", "yes"}:
        pytest.skip(f"Set {_LIVE_FLAG}=1 to run the live snake CodeCompass E2E.")


def _api(path: str, method: str = "GET", body: dict | None = None, token: str = "") -> dict:
    url = f"{HUB_URL}/{path.lstrip('/')}"
    data = json.dumps(body).encode() if body else None
    headers: dict[str, str] = {"Accept": "application/json"}
    if data:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")
        raise AssertionError(f"{method} {url} → HTTP {exc.code}: {body_text[:300]}") from exc


def _login() -> str:
    result = _api("/login", "POST", {"username": ADMIN_USER, "password": ADMIN_PASSWORD})
    token = str((result.get("data") or {}).get("access_token") or "")
    assert token, f"Login failed: {result}"
    return token


def _index_probe_files(token: str) -> str:
    """Index a small known subset of Ananta source files and return the source_id."""
    records = []
    for rel in PROBE_FILES:
        path = ROOT / rel
        if not path.exists():
            continue
        content = path.read_text(encoding="utf-8", errors="replace").strip()
        if content:
            records.append({"file": rel, "path": rel, "content": content[:16000]})

    assert records, f"None of the probe files found under {ROOT}"

    source_id = "ananta-e2e-probe"
    result = _api("/knowledge/sources/index-records", "POST", {
        "source_scope": "repo_path",
        "source_id": source_id,
        "records": records,
        "async": False,
        "profile_name": "deep_code",
        "source_metadata": {"context": "e2e_snake_chat_test"},
    }, token=token)

    assert result.get("status") == "success", f"Indexing failed: {result}"
    return source_id


def _register_snake(token: str) -> tuple[str, str]:
    """Register a snake and return (snake_id, snake_token)."""
    result = _api("/snakes", "POST", {"name": "e2e-codecompass-probe", "role": "viewer"}, token=token)
    snake_id = str((result.get("data") or result).get("id") or result.get("id") or "")
    snake_token = str((result.get("data") or result).get("token") or result.get("token") or "")
    assert snake_id and snake_token, f"Snake registration failed: {result}"
    return snake_id, snake_token


def _send_chat_message(snake_id: str, snake_token: str, text: str) -> None:
    msg_id = f"e2e-{int(time.time() * 1000)}"
    _api(
        f"/snakes/{snake_id}/chat/messages",
        "POST",
        {"id": msg_id, "channel_type": "room", "visibility": "room", "text": text},
        token=snake_token,
    )


def _wait_for_ai_response(snake_id: str, user_token: str, timeout: float = 45.0) -> str:
    """Poll for an AI response after our message. Returns the response text."""
    deadline = time.time() + timeout
    last_cursor = "0"
    while time.time() < deadline:
        result = _api(
            f"/snakes/{snake_id}/chat/messages?since={last_cursor}",
            token=user_token,
        )
        messages = result.get("messages") or []
        last_cursor = str(result.get("cursor") or last_cursor)
        for msg in messages:
            if msg.get("sender_kind") == "assistant" and msg.get("sender_id") == "ai-snake":
                return str(msg.get("text") or "")
        time.sleep(2.0)
    raise TimeoutError(f"No AI response within {timeout}s")


def _delete_snake(snake_id: str, user_token: str) -> None:
    try:
        _api(f"/snakes/{snake_id}", "DELETE", token=user_token)
    except Exception:
        pass


# ── Tests ───────────────────────────────────────────────────────────────────


def test_hub_is_reachable() -> None:
    """Smoke: Hub antwortet auf /health."""
    _require_live()
    result = _api("/health")
    assert result.get("status") in {"ok", "success", "healthy"} or result.get("ok"), \
        f"Hub /health returned unexpected: {result}"


def test_knowledge_index_is_populated_after_setup(tmp_path: Path) -> None:
    """Nach setup_codecompass_index.py oder nach _index_probe_files gibt es ≥1 Index."""
    _require_live()
    token = _login()

    # Try to find existing indexes
    result = _api("/knowledge/indexes", token=token)
    items = (result.get("data") or {}).get("items") or result.get("items") or []
    if not items:
        # Index probe files ourselves
        _index_probe_files(token)
        result = _api("/knowledge/indexes", token=token)
        items = (result.get("data") or {}).get("items") or result.get("items") or []

    assert len(items) >= 1, (
        "No knowledge indexes found. Run: python scripts/setup_codecompass_index.py"
    )


def test_snake_chat_codecompass_returns_project_context() -> None:
    """Kerntест: Snake-Chat antwortet mit echtem Kontext aus dem Ananta-Repo."""
    _require_live()
    token = _login()

    # Ensure index exists
    result = _api("/knowledge/indexes", token=token)
    items = (result.get("data") or {}).get("items") or result.get("items") or []
    if not items:
        _index_probe_files(token)

    snake_id, snake_token = _register_snake(token)
    try:
        # Ask something directly about indexed code
        _send_chat_message(
            snake_id, snake_token,
            "Was macht die Funktion _build_grounded_snake_prompt in snakes.py?"
        )
        response = _wait_for_ai_response(snake_id, token)

        assert response, "AI-Snake returned empty response"
        assert "[Kontext: 0 Treffer]" not in response, (
            f"CodeCompass lieferte keine Treffer. Antwort:\n{response[:500]}"
        )
        # Should mention some actual code context
        context_markers = ["Kontext:", "Treffer", "snakes", "snake", "grounded", "prompt", "rag"]
        has_context = any(m.lower() in response.lower() for m in context_markers)
        assert has_context, (
            f"Response doesn't mention any project-relevant terms.\nResponse: {response[:500]}"
        )
    finally:
        _delete_snake(snake_id, token)


def test_snake_chat_codecompass_ananta_folder_structure() -> None:
    """Snake kann Ordnerstruktur aus dem indizierten Codebase beantworten."""
    _require_live()
    token = _login()

    result = _api("/knowledge/indexes", token=token)
    items = (result.get("data") or {}).get("items") or result.get("items") or []
    if not items:
        _index_probe_files(token)

    snake_id, snake_token = _register_snake(token)
    try:
        _send_chat_message(
            snake_id, snake_token,
            "Welche Routes gibt es im agent/routes Ordner? Nenne konkrete Dateinamen."
        )
        response = _wait_for_ai_response(snake_id, token)

        assert response, "AI-Snake returned empty response"
        assert "[Kontext: 0 Treffer]" not in response, (
            f"CodeCompass lieferte keine Treffer. Antwort:\n{response[:500]}"
        )
        # Should mention at least one actual route file
        known_routes = ["snakes", "share_sessions", "pair_groups", "knowledge", "auth"]
        mentioned = [r for r in known_routes if r in response.lower()]
        assert mentioned, (
            f"Response doesn't mention any known routes. Response: {response[:500]}"
        )
    finally:
        _delete_snake(snake_id, token)


def test_snake_chat_codecompass_no_context_fallback() -> None:
    """Snake bleibt funktional wenn eine völlig unbekannte Frage gestellt wird."""
    _require_live()
    token = _login()
    snake_id, snake_token = _register_snake(token)
    try:
        _send_chat_message(
            snake_id, snake_token,
            "Was ist die Hauptstadt von Island?"
        )
        response = _wait_for_ai_response(snake_id, token)
        # Should still respond (even with 0 context), just not crash
        assert response, "AI-Snake returned empty response for off-topic question"
        # For off-topic, 0 Treffer is acceptable
    finally:
        _delete_snake(snake_id, token)
