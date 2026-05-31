from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pytest

from client_surfaces.operator_tui.models import OperatorState, PanelState
from client_surfaces.operator_tui.renderer import render_operator_shell
from client_surfaces.operator_tui.visual.browser.carbonyl_runner import CarbonylRunner
from client_surfaces.operator_tui.visual.runtime.capability_detector import detect_carbonyl_browser

_ANSI_STRIP = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _asciinema_v2_lines(*, title: str, frame: str, width: int = 120, height: int = 32) -> str:
    header = {
        "version": 2,
        "width": width,
        "height": height,
        "title": title,
        "env": {"TERM": "xterm-256color", "COLORTERM": "truecolor"},
    }
    lines = [
        json.dumps(header, ensure_ascii=False),
        json.dumps([0.0, "o", frame], ensure_ascii=False),
    ]
    return "\n".join(lines) + "\n"


def test_center_browser_renders_non_empty_carbonyl_cast(tmp_path: Path) -> None:
    cap = detect_carbonyl_browser()
    if not cap.available:
        pytest.skip(f"Carbonyl unavailable: {cap.unavailable_reason}")

    html_path = tmp_path / "browser-e2e.html"
    html_path.write_text(
        """<!doctype html>
<html>
  <head><meta charset="utf-8"><title>Ananta Browser E2E</title></head>
  <body style="background:#111;color:#f5f5f5;font-family:monospace">
    <h1>ANANTA_BROWSER_E2E_OK</h1>
    <p>Center browser render validation.</p>
  </body>
</html>
""",
        encoding="utf-8",
    )

    runner = CarbonylRunner()
    raw = b""
    try:
        runner.start(str(html_path), cols=88, rows=20)
        deadline = time.monotonic() + 2.5
        while time.monotonic() < deadline:
            raw += runner.read_output(timeout=0.1)
            if raw and len(raw) > 4096:
                break
            time.sleep(0.03)
    finally:
        runner.stop()

    assert raw, "expected carbonyl output bytes"

    game = {
        "center_browser_active": True,
        "center_browser_status": "active",
        "center_browser_url": str(html_path),
        "_browser_frame_bytes": raw,
    }
    state = OperatorState(
        endpoint="http://localhost:8000",
        panel_states={"dashboard": PanelState.HEALTHY},
        section_payloads={"dashboard": {}},
        header_logo_game=game,
        section_id="dashboard",
    )
    frame = render_operator_shell(state, width=120, height=32)
    cast_content = _asciinema_v2_lines(
        title="Ananta Operator TUI – Center Browser Carbonyl E2E",
        frame=frame,
        width=120,
        height=32,
    )
    cast_path = tmp_path / "video-tui-center-browser-carbonyl-e2e.cast"
    cast_path.write_text(cast_content, encoding="utf-8")

    lines = [line for line in cast_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    header = json.loads(lines[0])
    assert header["version"] == 2
    assert "Center Browser Carbonyl E2E" in header["title"]

    frame_text = json.loads(lines[1])[2]
    plain = _ANSI_STRIP.sub("", frame_text)
    assert "[BROWSER]" in plain

    # Analysis guard: browser pane must contain visible non-space payload beyond header chrome.
    block_count = plain.count("▄") + plain.count("█") + plain.count("▀")
    non_space = sum(1 for ch in plain if not ch.isspace())
    assert block_count > 200 or non_space > 1200, (
        "center browser cast looks visually empty; expected substantial rendered payload "
        f"(block_count={block_count}, non_space={non_space})"
    )
