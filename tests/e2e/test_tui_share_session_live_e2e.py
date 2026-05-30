from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

import pytest

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


def _require_live_share() -> tuple[str, str]:
    if os.environ.get("ANANTA_E2E_LIVE_SHARE", "").strip().lower() not in {"1", "true", "yes", "on"}:
        pytest.skip("Set ANANTA_E2E_LIVE_SHARE=1 to run real PTY share-session E2E.")

    dotenv = _load_dotenv()
    endpoint = str(
        os.environ.get("ANANTA_TUI_E2E_SHARE_ENDPOINT")
        or os.environ.get("ANANTA_ENDPOINT")
        or os.environ.get("ANANTA_HUB_URL")
        or dotenv.get("ANANTA_ENDPOINT")
        or dotenv.get("ANANTA_HUB_URL")
        or "http://localhost:5000"
    ).strip()
    password = str(
        os.environ.get("ANANTA_PASSWORD")
        or os.environ.get("INITIAL_ADMIN_PASSWORD")
        or dotenv.get("ANANTA_PASSWORD")
        or dotenv.get("INITIAL_ADMIN_PASSWORD")
        or ""
    ).strip()
    if not password:
        pytest.skip("ANANTA_PASSWORD (or INITIAL_ADMIN_PASSWORD) is required for live :share create/list E2E.")
    try:
        with urllib.request.urlopen(f"{endpoint.rstrip('/')}/health", timeout=2.5):
            pass
    except (urllib.error.URLError, TimeoutError):
        pytest.skip(f"Hub is not reachable at {endpoint}")
    return endpoint, password


def test_share_session_live_e2e_scene_uses_pty_capture_backend(tmp_path: Path, monkeypatch) -> None:
    def _fake_live_share_cast(*, run_id: str) -> str:
        assert run_id == "video-enable-share-session-live-e2e"
        return (
            '{"version": 2, "width": 120, "height": 32, "title": "Ananta Operator TUI – Share Session Live E2E"}\n'
            '[0.0, "o", "\\u001b[2J\\u001b[Hready> :share create test\\n"]\n'
            "[1.1, \"o\", \"Session 'test' erstellt. Invite: /share/share-test-001\\n\"]\n"
            "[2.2, \"o\", \"1 Session(s): 'test'[share-te] 1P\\n\"]\n"
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
    assert "1 Session(s):" in plain


def test_share_session_live_e2e_records_real_pty_flow() -> None:
    endpoint, _password = _require_live_share()

    original_seconds = os.environ.get("ANANTA_TUI_E2E_CAST_SECONDS")
    original_endpoint = os.environ.get("ANANTA_TUI_E2E_SHARE_ENDPOINT")
    try:
        os.environ["ANANTA_TUI_E2E_CAST_SECONDS"] = "28"
        os.environ["ANANTA_TUI_E2E_SHARE_ENDPOINT"] = endpoint
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

    assert payload["status"] == "recorded"
    video_path = _resolve_ref(payload["video_ref"])
    assert video_path.exists()
    assert video_path.name == "video-tui-share-session-live-e2e.cast"

    lines = [line for line in video_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    header = json.loads(lines[0])
    assert header["version"] == 2
    assert header["width"] >= 100
    assert header["height"] >= 28

    frame_text = "\n".join(json.loads(line)[2] for line in lines[1:])
    plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b.", "", frame_text)
    assert ":share create test" in plain
    assert "Session 'test' erstellt." in plain
    assert ":share list" in plain
    assert "Session(s):" in plain
