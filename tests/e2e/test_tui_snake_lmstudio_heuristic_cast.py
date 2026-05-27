"""E2E test: TUI snake mode with LM Studio + heuristic selection rendered cast.

Verifies that:
- The rendered cast shows mouse-follow, heuristic switching, and LM Studio chat.
- All required cast markers are present in the asciinema v2 output.
- Cast has correct header (version 2, width >= 120, height >= 32).

Run with:
    ANANTA_E2E_LIVE_LMSTUDIO=1 pytest tests/e2e/test_tui_snake_lmstudio_heuristic_cast.py -v

To also test live LM Studio connectivity set ANANTA_TUI_LLM_API_BASE to the local
LM Studio endpoint (default: http://127.0.0.1:1234/v1).
"""
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


def _lmstudio_api_base() -> str:
    return str(
        os.environ.get("ANANTA_TUI_LLM_API_BASE")
        or os.environ.get("ANANTA_TUI_SNAKE_AI_API_BASE_URL")
        or os.environ.get("OPENAI_BASE_URL")
        or os.environ.get("OPENAI_API_BASE")
        or "http://127.0.0.1:1234/v1"
    ).rstrip("/")


def _require_live_lmstudio() -> str:
    """Skip unless ANANTA_E2E_LIVE_LMSTUDIO=1 and LM Studio endpoint reachable."""
    if os.environ.get("ANANTA_E2E_LIVE_LMSTUDIO", "").strip().lower() not in {"1", "true", "yes", "on"}:
        pytest.skip("Set ANANTA_E2E_LIVE_LMSTUDIO=1 to run live LM Studio E2E tests.")
    api_base = _lmstudio_api_base()
    try:
        with urllib.request.urlopen(f"{api_base}/models", timeout=2.5):
            pass
    except (urllib.error.URLError, TimeoutError):
        pytest.skip(f"LM Studio API not reachable at {api_base}")
    return api_base


def _probe_lmstudio_chat(api_base: str) -> str:
    """Send a minimal chat request to verify LM Studio is responsive."""
    model = str(os.environ.get("ANANTA_TUI_LLM_MODEL") or "meta-llama_-_llama-3.2-1b-instruct")
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Sag kurz: online"}],
        "temperature": 0.0,
        "max_tokens": 24,
    }
    request = urllib.request.Request(
        url=f"{api_base}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=8.0) as response:
        raw = response.read().decode("utf-8", errors="replace")
    parsed = json.loads(raw)
    choices = parsed.get("choices") if isinstance(parsed, dict) else None
    assert isinstance(choices, list) and choices, f"LM Studio returned no choices: {raw[:400]}"
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    assert isinstance(message, dict), f"No message in LM Studio response: {raw[:400]}"
    content = str(message.get("content") or "").strip()
    assert content, f"LM Studio response content is empty: {raw[:400]}"
    return content


# ── rendered cast test (no live LM Studio required) ───────────────────────────

def test_snake_lmstudio_heuristic_cast_rendered_markers() -> None:
    """Rendered cast must contain heuristic, mouse-follow, and chat markers."""
    payload = record_tui_demo(
        run_id="test-snake-lmstudio-heuristic-rendered",
        flow_id="tui-snake-heuristic-cast",
        enabled=True,
        scene="snake-lmstudio-heuristic",
    )

    assert payload["status"] == "recorded", f"record_tui_demo failed: {payload}"
    video_path = _resolve_ref(payload["video_ref"])
    assert video_path.exists(), f"Cast file not found: {video_path}"
    assert video_path.name == "video-tui-snake-lmstudio-heuristic.cast"

    raw = video_path.read_text(encoding="utf-8")
    lines = [line for line in raw.splitlines() if line.strip()]
    assert len(lines) >= 2, "Cast has fewer than 2 lines (header + at least 1 frame)"

    header = json.loads(lines[0])
    assert header["version"] == 2
    assert header["width"] >= 100, f"Cast width too narrow: {header['width']}"
    assert header["height"] >= 28, f"Cast height too short: {header['height']}"

    frame_text = "\n".join(json.loads(line)[2] for line in lines[1:])
    plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b.", "", frame_text)

    # Heuristic selection markers
    assert "heuristic:snake_tui_follow_distance_default" in plain, (
        "Cast missing follow_distance heuristic marker"
    )
    assert "heuristic:snake_tui_artifact_intent_default" in plain, (
        "Cast missing artifact_intent heuristic marker"
    )

    # Mouse-follow markers
    assert "mouse-follow" in plain, "Cast missing [mouse-follow] marker"

    # LM Studio chat response markers
    assert "lmstudio-chat-active" in plain, "Cast missing [lmstudio-chat-active] marker"

    # Both snakes visible (local + AI tutor)
    assert "local-snake" in plain or "s1" in plain or "mint" in plain, (
        "Local snake (s1/mint) not found in cast"
    )
    assert "tutor-ai" in plain or "s-ai" in plain or "amber" in plain, (
        "AI tutor snake (s-ai/amber) not found in cast"
    )

    # Navigation sections covered
    assert "goals" in plain.lower() or "GOALS" in plain, "Goals section not in cast"
    assert "tasks" in plain.lower() or "TASKS" in plain, "Tasks section not in cast"


# ── live LM Studio E2E test ───────────────────────────────────────────────────

def test_snake_lmstudio_heuristic_cast_live_connectivity() -> None:
    """With live LM Studio: probe chat, record cast, verify full marker set."""
    api_base = _require_live_lmstudio()
    chat_reply = _probe_lmstudio_chat(api_base)
    assert chat_reply, "LM Studio chat probe returned empty reply"

    payload = record_tui_demo(
        run_id="test-snake-lmstudio-heuristic-live",
        flow_id="tui-snake-heuristic-live",
        enabled=True,
        scene="snake-lmstudio-heuristic",
    )

    assert payload["status"] == "recorded"
    video_path = _resolve_ref(payload["video_ref"])
    assert video_path.exists()

    raw = video_path.read_text(encoding="utf-8")
    lines = [line for line in raw.splitlines() if line.strip()]
    header = json.loads(lines[0])
    assert header["version"] == 2

    frame_text = "\n".join(json.loads(line)[2] for line in lines[1:])
    plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b.", "", frame_text)

    # All required markers for live run
    required = [
        "heuristic:snake_tui_follow_distance_default",
        "mouse-follow",
        "lmstudio-chat-active",
    ]
    missing = [m for m in required if m not in plain]
    assert not missing, f"Live cast missing markers: {missing}\n(plain tail: {plain[-1500:]})"

    synced_targets = list(payload.get("synced_cast_targets") or [])
    assert any(
        "operator_tui_splash.cast" in p for p in synced_targets
    ), f"Splash cast not synced. Synced targets: {synced_targets}"
