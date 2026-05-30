from __future__ import annotations

import json
import os
import re
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from werkzeug.serving import make_server

from scripts.e2e.record_tui_demo import record_tui_demo

ROOT = Path(__file__).resolve().parents[2]


def _resolve_ref(ref: str) -> Path:
    ref_path = Path(ref)
    return ref_path if ref_path.is_absolute() else ROOT / ref_path


def _load_dotenv() -> dict[str, str]:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return {}
    loaded: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, _, value = raw.partition("=")
        loaded[key.strip()] = value.strip().strip('"').strip("'")
    return loaded


def _issue_access_token(endpoint: str, username: str, password: str) -> str:
    body = json.dumps({"username": username, "password": password}).encode("utf-8")
    req = urllib.request.Request(
        f"{endpoint.rstrip('/')}/login",
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=3.0) as response:
        payload = json.loads(response.read().decode("utf-8"))
    token = str(((payload.get("data") or {}).get("access_token") or "")).strip()
    if not token:
        raise RuntimeError("login response did not include access_token")
    return token


def _is_http_unauthorized(exc: BaseException) -> bool:
    return isinstance(exc, urllib.error.HTTPError) and int(getattr(exc, "code", 0)) in {401, 403}


def _token_looks_usable(endpoint: str, token: str) -> bool:
    candidate = str(token or "").strip()
    if not candidate:
        return False
    req = urllib.request.Request(
        f"{endpoint.rstrip('/')}/share-sessions",
        headers={"Authorization": f"Bearer {candidate}", "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=3.0):
            return True
    except urllib.error.HTTPError as exc:
        if int(getattr(exc, "code", 0)) in {401, 403}:
            return False
        return False
    except (urllib.error.URLError, TimeoutError):
        return False


def _extract_titles(payload: object) -> list[str]:
    titles: list[str] = []
    if isinstance(payload, dict):
        title = payload.get("title")
        if isinstance(title, str) and title.strip():
            titles.append(title.strip())
        for value in payload.values():
            titles.extend(_extract_titles(value))
    elif isinstance(payload, list):
        for item in payload:
            titles.extend(_extract_titles(item))
    return titles


def _list_share_titles(endpoint: str, token: str) -> list[str]:
    req = urllib.request.Request(
        f"{endpoint.rstrip('/')}/share-sessions",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=5.0) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return _extract_titles(payload)


def _require_live_share(
    *,
    endpoint_override: str | None = None,
    username_override: str | None = None,
    password_override: str | None = None,
) -> tuple[str, str]:
    if os.environ.get("ANANTA_E2E_LIVE_SHARE", "").strip().lower() not in {"1", "true", "yes", "on"}:
        pytest.skip("Set ANANTA_E2E_LIVE_SHARE=1 to run real PTY share-session E2E.")

    dotenv = _load_dotenv()
    endpoint = str(
        endpoint_override
        or os.environ.get("ANANTA_TUI_E2E_SHARE_ENDPOINT")
        or os.environ.get("ANANTA_ENDPOINT")
        or os.environ.get("ANANTA_HUB_URL")
        or dotenv.get("ANANTA_ENDPOINT")
        or dotenv.get("ANANTA_HUB_URL")
        or "http://localhost:5000"
    ).strip()
    token_candidates = [
        str(os.environ.get("ANANTA_AUTH_TOKEN") or "").strip(),
        str(dotenv.get("ANANTA_AUTH_TOKEN") or "").strip(),
    ]
    for token in token_candidates:
        if _token_looks_usable(endpoint, token):
            return endpoint, token

    username_candidates: list[str] = []
    for value in (
        username_override,
        os.environ.get("ANANTA_USER"),
        os.environ.get("INITIAL_ADMIN_USER"),
        dotenv.get("ANANTA_USER"),
        dotenv.get("INITIAL_ADMIN_USER"),
        "admin",
    ):
        candidate = str(value or "").strip()
        if candidate and candidate not in username_candidates:
            username_candidates.append(candidate)

    password_candidates: list[str] = []
    for value in (
        password_override,
        os.environ.get("ANANTA_PASSWORD"),
        os.environ.get("INITIAL_ADMIN_PASSWORD"),
        dotenv.get("ANANTA_PASSWORD"),
        dotenv.get("INITIAL_ADMIN_PASSWORD"),
        "test123",
        "AnantaLocalDevAdmin123!",
    ):
        candidate = str(value or "").strip()
        if candidate and candidate not in password_candidates:
            password_candidates.append(candidate)
    if not password_candidates:
        pytest.skip(
            "No usable auth found for live :share create/list E2E (set ANANTA_AUTH_TOKEN or ANANTA_PASSWORD/INITIAL_ADMIN_PASSWORD)."
        )
    try:
        with urllib.request.urlopen(f"{endpoint.rstrip('/')}/health", timeout=2.5):
            pass
    except (urllib.error.URLError, TimeoutError):
        pytest.skip(f"Hub is not reachable at {endpoint}")

    last_login_error: BaseException | None = None
    for username in username_candidates:
        for password in password_candidates:
            try:
                return endpoint, _issue_access_token(endpoint, username, password)
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
                last_login_error = exc
                if _is_http_unauthorized(exc):
                    continue
                pytest.skip(f"Live share login failed for {username}@{endpoint}: {exc}")
    pytest.skip(
        f"Live share login failed for users={username_candidates} at {endpoint}: {last_login_error or 'unauthorized'}"
    )


@pytest.fixture
def live_share_hub_endpoint(app):
    server = make_server("127.0.0.1", 0, app)
    host, port = server.server_address[:2]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://{host}:{int(port)}"
    finally:
        server.shutdown()
        thread.join(timeout=2.0)


def test_share_session_live_e2e_scene_uses_pty_capture_backend(tmp_path: Path, monkeypatch) -> None:
    def _fake_live_share_cast(*, run_id: str) -> str:
        assert run_id == "video-enable-share-session-live-e2e"
        return (
            '{"version": 2, "width": 200, "height": 56, "title": "Ananta Operator TUI – Share Session Live E2E"}\n'
            '[0.0, "o", "\\u001b[2J\\u001b[Hready> :share create test\\n"]\n'
            "[1.1, \"o\", \"Session 'test' erstellt. Invite: /share/share-test-001\\n\"]\n"
            "[2.2, \"o\", \"ready> :share list\\n\"]\n"
            "[3.3, \"o\", \"1 Session(s): 'test'[share-te] 1P\\n\"]\n"
        )

    monkeypatch.setattr("scripts.e2e.record_tui_demo._share_session_live_e2e_cast", _fake_live_share_cast)

    payload = record_tui_demo(
        run_id="video-enable-share-session-live-e2e",
        flow_id="tui-share-session-live-e2e-video",
        enabled=True,
        scene="share-session-live-e2e",
        artifact_root=tmp_path / "artifacts",
    )

    assert payload["status"] == "recorded"
    video_path = _resolve_ref(payload["video_ref"])
    assert video_path.exists()
    assert video_path.name == "video-tui-share-session-live-e2e.cast"
    plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b.", "", video_path.read_text(encoding="utf-8"))
    assert ":share create test" in plain
    assert "Session 'test' erstellt." in plain
    assert ":share list" in plain
    assert "1 Session(s):" in plain
    assert "'test'[" in plain


def test_share_session_live_e2e_records_real_pty_flow(tmp_path: Path, live_share_hub_endpoint: str) -> None:
    endpoint, access_token = _require_live_share(
        endpoint_override=live_share_hub_endpoint,
        username_override="admin",
        password_override="admin",
    )
    share_title = "e2e-live-share"
    snapshot_dir = tmp_path / "tui-snapshots"

    original_seconds = os.environ.get("ANANTA_TUI_E2E_CAST_SECONDS")
    original_endpoint = os.environ.get("ANANTA_TUI_E2E_SHARE_ENDPOINT")
    original_title = os.environ.get("ANANTA_TUI_E2E_SHARE_TITLE")
    original_snapshot_dir = os.environ.get("ANANTA_TUI_SNAPSHOT_DIR")
    original_password = os.environ.get("ANANTA_PASSWORD")
    original_access_token = os.environ.get("ANANTA_AUTH_TOKEN")
    original_share_cast_width = os.environ.get("ANANTA_TUI_E2E_SHARE_CAST_WIDTH")
    original_share_cast_height = os.environ.get("ANANTA_TUI_E2E_SHARE_CAST_HEIGHT")
    try:
        os.environ["ANANTA_TUI_E2E_CAST_SECONDS"] = "40"
        os.environ["ANANTA_TUI_E2E_SHARE_CAST_WIDTH"] = "200"
        os.environ["ANANTA_TUI_E2E_SHARE_CAST_HEIGHT"] = "56"
        os.environ["ANANTA_TUI_E2E_SHARE_ENDPOINT"] = endpoint
        os.environ["ANANTA_TUI_E2E_SHARE_TITLE"] = share_title
        os.environ["ANANTA_TUI_SNAPSHOT_DIR"] = str(snapshot_dir)
        os.environ["ANANTA_AUTH_TOKEN"] = access_token
        os.environ.pop("ANANTA_PASSWORD", None)
        payload = record_tui_demo(
            run_id="video-enable-share-session-live-e2e-real",
            flow_id="tui-share-session-live-e2e-video",
            enabled=True,
            scene="share-session-live-e2e",
        )
    finally:
        if original_seconds is None:
            os.environ.pop("ANANTA_TUI_E2E_CAST_SECONDS", None)
        else:
            os.environ["ANANTA_TUI_E2E_CAST_SECONDS"] = original_seconds
        if original_endpoint is None:
            os.environ.pop("ANANTA_TUI_E2E_SHARE_ENDPOINT", None)
        else:
            os.environ["ANANTA_TUI_E2E_SHARE_ENDPOINT"] = original_endpoint
        if original_title is None:
            os.environ.pop("ANANTA_TUI_E2E_SHARE_TITLE", None)
        else:
            os.environ["ANANTA_TUI_E2E_SHARE_TITLE"] = original_title
        if original_snapshot_dir is None:
            os.environ.pop("ANANTA_TUI_SNAPSHOT_DIR", None)
        else:
            os.environ["ANANTA_TUI_SNAPSHOT_DIR"] = original_snapshot_dir
        if original_password is None:
            os.environ.pop("ANANTA_PASSWORD", None)
        else:
            os.environ["ANANTA_PASSWORD"] = original_password
        if original_access_token is None:
            os.environ.pop("ANANTA_AUTH_TOKEN", None)
        else:
            os.environ["ANANTA_AUTH_TOKEN"] = original_access_token
        if original_share_cast_width is None:
            os.environ.pop("ANANTA_TUI_E2E_SHARE_CAST_WIDTH", None)
        else:
            os.environ["ANANTA_TUI_E2E_SHARE_CAST_WIDTH"] = original_share_cast_width
        if original_share_cast_height is None:
            os.environ.pop("ANANTA_TUI_E2E_SHARE_CAST_HEIGHT", None)
        else:
            os.environ["ANANTA_TUI_E2E_SHARE_CAST_HEIGHT"] = original_share_cast_height

    assert payload["status"] == "recorded"
    video_path = _resolve_ref(payload["video_ref"])
    assert video_path.exists()
    assert video_path.name == "video-tui-share-session-live-e2e.cast"

    lines = [line for line in video_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    header = json.loads(lines[0])
    assert header["version"] == 2
    assert header["width"] >= 190
    assert header["height"] >= 52

    snapshot_files = sorted(snapshot_dir.glob("tui-snapshot-*.txt"))
    assert snapshot_files, "No TUI snapshots were captured during live PTY run."
    snapshot_text = snapshot_files[-1].read_text(encoding="utf-8")
    assert "Snake-Modus aktiv" in snapshot_text
    assert "Share / Teilnehmer" in snapshot_text
    assert ". Artifacts" not in snapshot_text and ". Knowledge" not in snapshot_text

    titles = _list_share_titles(endpoint, access_token)
    assert share_title in titles
