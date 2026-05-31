from __future__ import annotations

import json
import re
from pathlib import Path

from client_surfaces.operator_tui.models import OperatorState, PanelState
from client_surfaces.operator_tui.renderer import render_operator_shell

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


def test_tui_markdown_mermaid_quality_cast_contains_readable_content(tmp_path: Path) -> None:
    markdown = (
        "# Architektur\n\n"
        "- Punkt A\n"
        "  - Punkt B\n\n"
        "```mermaid\n"
        "flowchart TD\n"
        "A-->B\n"
        "B-->C\n"
        "```\n"
    )

    game = {
        "visual_viewport": {"enabled": True},
        "visual_viewport_enabled": True,
        "visual_viewport_active_view": "markdown_mermaid_document",
        "visual_viewport_active_renderer": "ansi_blocks",
        "visual_viewport_active_adapter": "ansi",
        "visual_viewport_frame_lines": [
            "# Architektur",
            "",
            "- Punkt A",
            "  - Punkt B",
            "",
            "[Mermaid: Mermaid image renderer unavailable]",
            "flowchart TD",
            "A-->B",
            "B-->C",
        ],
        "visual_viewport_scene_meta": {
            "content_lines": 9,
            "max_line_width": 44,
            "scroll_offset": 0,
            "h_offset": 0,
            "mermaid_renderer_used": "fallback_codeblock",
            "mermaid_fallback_count": 1,
            "docs_graphics_profile": "wsl2",
        },
        "visual_runtime_status": {
            "active_view": "markdown_mermaid_document",
            "active_renderer": "ansi_blocks",
            "active_adapter": "ansi",
            "rendered_frames": 1,
            "skipped_frames": 0,
            "dropped_frames": 0,
            "fallback_reason": "",
            "runtime_error": "",
        },
        "markdown_text": markdown,
        "markdown_mermaid_render_requested": True,
        "document_source": {"kind": "inline", "content_or_ref": markdown, "title": "fixture"},
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
        title="Ananta Operator TUI – Markdown Mermaid Quality E2E",
        frame=frame,
        width=120,
        height=32,
    )
    cast_path = tmp_path / "video-tui-markdown-mermaid-quality-e2e.cast"
    cast_path.write_text(cast_content, encoding="utf-8")

    lines = [line for line in cast_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    header = json.loads(lines[0])
    assert header["version"] == 2
    assert "Markdown Mermaid Quality E2E" in header["title"]

    frame_text = json.loads(lines[1])[2]
    plain = _ANSI_STRIP.sub("", frame_text)

    assert "# Architektur" in plain
    assert "Punkt A" in plain
    assert "flowchart TD" in plain
    non_space = sum(1 for ch in plain if not ch.isspace())
    assert non_space > 300
