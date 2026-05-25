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


def _require_live_lmstudio() -> str:
    if os.environ.get("ANANTA_E2E_LIVE_LMSTUDIO", "").strip().lower() not in {"1", "true", "yes", "on"}:
        pytest.skip("Set ANANTA_E2E_LIVE_LMSTUDIO=1 to run live LM Studio cast E2E.")
    api_base = str(
        os.environ.get("ANANTA_TUI_LLM_API_BASE")
        or os.environ.get("ANANTA_TUI_SNAKE_AI_API_BASE_URL")
        or os.environ.get("OPENAI_BASE_URL")
        or os.environ.get("OPENAI_API_BASE")
        or "http://127.0.0.1:1234/v1"
    ).rstrip("/")
    try:
        with urllib.request.urlopen(f"{api_base}/models", timeout=2.5):
            pass
    except (urllib.error.URLError, TimeoutError):
        pytest.skip(f"LM Studio API not reachable at {api_base}")
    return api_base


def test_snake_mode_live_e2e_records_cast_with_lmstudio() -> None:
    _api_base = _require_live_lmstudio()
    payload = record_tui_demo(
        run_id="video-enable-snake-mode-live-lmstudio",
        flow_id="tui-snake-mode-live-e2e-video",
        enabled=True,
        scene="snake-mode-live-e2e",
    )

    assert payload["status"] == "recorded"
    video_path = _resolve_ref(payload["video_ref"])
    assert video_path.exists()
    assert video_path.name == "video-tui-snake-mode-live-e2e.cast"

    lines = [line for line in video_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    header = json.loads(lines[0])
    assert header["version"] == 2
    assert header["width"] >= 100
    assert header["height"] >= 28

    frame_text = "\n".join(json.loads(line)[2] for line in lines[1:])
    plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", frame_text)
    assert "ARTIFACTS" in plain
    assert "Tutorial-AI propose flow" in plain
    assert "[user->artifacts]" in plain
    assert "[openai-compatible->" in plain

    synced_targets = list(payload.get("synced_cast_targets") or [])
    assert any(path.endswith("tests/output/operator_tui_splash.cast") for path in synced_targets)
