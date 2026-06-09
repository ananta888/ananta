"""Internal sub-module of the Operator TUI renderer.

Extracted from the monolithic client_surfaces.operator_tui/renderer.py to
keep the main module small. This module owns: Content pane renderers: dashboard, browser, system, terminal, templates, audit, detail, planning, helpcenter, mail, diff3, goal-artifacts, visual viewport.

Public re-exports: the public ``client_surfaces.operator_tui.renderer``
module continues to expose every function via thin delegating wrappers
so existing imports keep working.
"""

from __future__ import annotations

import os
import re
import time
from textwrap import shorten
from typing import TYPE_CHECKING

_ANSI_STRIP = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")

from client_surfaces.operator_tui.diagrams import detect_diagram_blocks, render_diagram_fallback
from client_surfaces.operator_tui.goal_artifact_filters import filter_goal_artifact_view
from client_surfaces.operator_tui.keymap import bindings_for_mode, hints_for_mode
from client_surfaces.operator_tui.keybindings_config import display_for_action, shortcut_tokens_for_area
from client_surfaces.operator_tui.ai_snake_config_view import ai_snake_config_filter_options, ai_snake_config_items, chat_model_option_label
from client_surfaces.operator_tui.chat_long_message import (
    compact_chat_message_text,
    get_render_mode,
    is_showing_chat_long_message,
    long_message_history_rows,
    should_use_middle_view_for_message,
)
from client_surfaces.operator_tui.chat_state import get_active_channel
from client_surfaces.operator_tui.markdown_renderer import render_markdown_lines
from client_surfaces.operator_tui.models import FocusPane, OperatorMode, OperatorState, PanelState
from client_surfaces.operator_tui.read_models import build_goal_rows, build_inspection_detail, build_task_rows
from client_surfaces.operator_tui.sections import SECTIONS, get_section
from client_surfaces.operator_tui.audit_nav import grouped_audit_items, audit_nav_items
from client_surfaces.operator_tui.template_nav import grouped_template_items, template_nav_items
from client_surfaces.operator_tui.theme import DEFAULT_THEME, state_label, state_prefix
from client_surfaces.operator_tui.scroll.scrollbar_renderer import minimal_scroll_indicator, render_scrollbar_column

if TYPE_CHECKING:
    from agent.cli.splash import SplashMachine, SplashState





# === Cross-module imports (resolve symbols from other sub-modules) ===
from client_surfaces.operator_tui import _renderer_utils as _ru_x
from client_surfaces.operator_tui._renderer_utils import (_clip, _clip_with_scroll, _overlay_at_visible_col, _render_hscrollbar_row, _render_vscrollbar_char, _rule, _splice_inspector_into_chrome, _truncate_to_height, _chat_channel_label, _inline_input_with_cursor, _pane_title)
from client_surfaces.operator_tui import _renderer_chat_ai as _rca_x
from client_surfaces.operator_tui._renderer_chat_ai import (_chat_timeout_progress_text, _is_chat_ask_mode, _participant_color, _participant_label, _plain_channel_selector, _wrap_plain)
from client_surfaces.operator_tui import _renderer_snake_overlay as _rso_x
from client_surfaces.operator_tui._renderer_snake_overlay import (_ansi_color, _latest_ai_message_text)
from client_surfaces.operator_tui import _renderer_layout as _rl_x
from client_surfaces.operator_tui._renderer_layout import (_runtime_detail_lines)
# === Module-level state (constants) ===
_TPL_THEME = {
    "blueprint": ("\x1b[38;2;130;200;255m", "\x1b[0m"),
    "template":  ("\x1b[38;2;180;230;150m", "\x1b[0m"),
    "seed":      "\x1b[38;2;255;205;100m★\x1b[0m",
    "header":    ("\x1b[38;2;100;120;150m", "\x1b[0m"),
}
_TPL_VAR_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_\.\[\]-]*$")
_TPL_VAR_COLOR = "\x1b[38;2;130;210;255m"
_TPL_WARN_COLOR = "\x1b[38;2;255;195;90m"
_TPL_ERR_COLOR = "\x1b[38;2;255;120;120m"
_TPL_RESET = "\x1b[0m"



# === Functions extracted from the original renderer.py ===

def _content_browser_lines(game: dict, width: int, *, height: int | None = None) -> list[str]:
    """Render Carbonyl browser output via pyte virtual terminal."""
    h = max(3, int(height or 20))
    status = str(game.get("center_browser_status") or "")
    url = str(game.get("center_browser_url") or "")
    error = str(game.get("center_browser_error") or "")
    render_mode = str(
        game.get("center_browser_render_mode")
        or os.environ.get("ANANTA_TUI_BROWSER_RENDER_MODE")
        or "pyte_auto"
    ).strip().lower()
    header = f"\x1b[38;2;80;140;220m[BROWSER]\x1b[0m {url[:max(4, width - 12)]}"

    if status in ("requested", "starting"):
        return [header, "  \x1b[2mstarte carbonyl…\x1b[0m"]
    if status == "error" or error:
        return [header,
                f"  \x1b[38;2;220;80;80m✗ {error[:width - 4]}\x1b[0m",
                "  \x1b[2mCtrl+2 zum Schliessen\x1b[0m"]

    raw_bytes: bytes = bytes(game.get("_browser_frame_bytes") or b"")
    if not raw_bytes:
        return [header, "  \x1b[2mwarte auf Carbonyl-Output…\x1b[0m"]

    def _raw_ansi_lines() -> list[str]:
        try:
            text = raw_bytes[-65536:].decode("utf-8", errors="replace")
        except Exception:
            text = ""
        ansi_lines = text.split("\n")
        visible = ansi_lines[-h:]
        result = [header] + [_clip(line, width) for line in visible]
        while len(result) < h:
            result.append("")
        return result[:h]

    def _raw_fallback_lines() -> list[str]:
        try:
            text = raw_bytes[-32768:].decode("utf-8", errors="replace")
        except Exception:
            text = ""
        plain_lines = _ANSI_STRIP.sub("", text).split("\n")
        visible = [l for l in plain_lines if l.strip()][-h:]
        result = [header] + [_clip(l, width) for l in visible]
        while len(result) < h:
            result.append("")
        return result[:h]

    if render_mode == "raw_ansi":
        return _raw_ansi_lines()

    try:
        import pyte
        screen = pyte.Screen(width, h)
        stream = pyte.ByteStream(screen)
        # Feed the last 128 KB to the virtual terminal
        stream.feed(raw_bytes[-131072:])
        result = [header]
        non_space_chars = 0
        for row_idx in range(h):
            row = screen.buffer.get(row_idx, {})
            cells: list[str] = []
            for col in range(width):
                cell = row.get(col)
                if not cell:
                    cells.append(" ")
                    continue
                ch = str(getattr(cell, "data", " ") or " ")
                if ch == " ":
                    # Carbonyl often paints with colored background spaces.
                    # Preserve visibility by showing a block for styled spaces.
                    bg = str(getattr(cell, "bg", "default") or "default").lower()
                    fg = str(getattr(cell, "fg", "default") or "default").lower()
                    reverse = bool(getattr(cell, "reverse", False))
                    styled_space = (
                        bg not in {"default", "black", "000000", "#000000"}
                        or fg not in {"default"}
                        or reverse
                    )
                    if styled_space:
                        ch = "█"
                cells.append(ch)
            line = "".join(cells).rstrip()
            non_space_chars += sum(1 for ch in line if not ch.isspace())
            result.append(line)
        # Some Carbonyl/frame combinations render as nearly-empty in pyte; prefer raw fallback then.
        if non_space_chars <= max(8, width // 6):
            return _raw_fallback_lines()
        return result[:h + 1]
    except Exception:
        # pyte not available or parse error — fall back to last raw lines
        return _raw_fallback_lines()



def _content_lines(state: OperatorState, width: int, *, height: int | None = None) -> list[str]:
    section = get_section(state.section_id)
    panel_state = (state.panel_states or {}).get(section.id, PanelState.LOADING)
    payload = (state.section_payloads or {}).get(section.id, {})
    game = state.header_logo_game if isinstance(state.header_logo_game, dict) else {}
    lines = [_pane_title(section.title.upper(), state.focus == FocusPane.CONTENT)]

    if bool(game.get("shortcut_help_middle_open")):
        return _content_shortcut_lines(state, width)
    if bool(game.get("ai_snake_config_open")):
        return _content_ai_snake_config_lines(state, width)
    if bool(game.get("center_browser_active")):
        return _content_browser_lines(game, width, height=height)
    if _is_chat_ask_mode(game) and not bool(game.get("chat_long_message_streaming")):
        # Only occupy this section's middle pane — navigate away → normal content.
        _ask_section = str(game.get("tutor_ask_section_id") or "")
        if not _ask_section or _ask_section == state.section_id:
            ask_lines = _content_chat_plain_ask_lines(state, width, height=height)
            if ask_lines is not None:
                return ask_lines
    if bool(dict(game.get("visual_viewport") or {}).get("enabled")):
        return _content_visual_viewport_lines(state, width)

    if panel_state == PanelState.LOADING:
        lines.append("  loading...")
        return lines
    if panel_state == PanelState.UNAUTHORIZED:
        lines.append("  ! access denied")
        lines.append("    export ANANTA_USER=admin")
        lines.append("    export ANANTA_PASSWORD=...")
        return lines
    if panel_state == PanelState.DEGRADED:
        lines.append(f"  ! degraded — {state.status_message or 'check system logs'}")
        lines.append("    press r to retry")
        return lines

    if section.id == "dashboard":
        lines.extend(_dashboard_content_lines(payload, state=state, width=width))
    elif section.id == "goals":
        items = payload.get("items") or []
        if not items:
            lines.append('  no goals — try: ananta plan "..."')
        else:
            for i, item in enumerate(items):
                marker = DEFAULT_THEME.selected_prefix if i == state.selected_index else " "
                lines.append(f"{marker} {item.get('id','?')}  [{item.get('status','?')}]  {item.get('title','')}")
    elif section.id == "tasks":
        items = payload.get("items") or []
        if not items:
            lines.append("  no tasks yet")
        else:
            for i, item in enumerate(items):
                marker = DEFAULT_THEME.selected_prefix if i == state.selected_index else " "
                lines.append(f"{marker} {item.get('id','?')}  [{item.get('status','?')}]  agent={item.get('agent','?')}  {item.get('title','')}")
        timeline = payload.get("timeline") or []
        if timeline:
            lines.append("")
            lines.append("  Timeline:")
            for entry in timeline[:3]:
                lines.append(f"    {entry.get('id','?')}  {entry.get('summary','')}")
    elif section.id == "templates":
        editor = dict(game.get("template_editor") or {})
        if state.mode is OperatorMode.EDIT and bool(editor.get("active")):
            lines.extend(_templates_editor_content_lines(state, width, viewport_height=height))
        else:
            lines.extend(_templates_content_lines(payload, state, width))
    elif section.id == "audit":
        viewer = dict(game.get("audit_viewer") or {})
        if bool(viewer.get("active")):
            lines.extend(_audit_viewer_content_lines(state, width, viewport_height=height))
        else:
            items = payload.get("items") or []
            if not items:
                lines.append("  (empty)")
                lines.append("  press r to refresh")
            else:
                lines.append("  Audit-Datasets (read-only)")
                for i, item in enumerate(items[:20]):
                    marker = DEFAULT_THEME.selected_prefix if i == state.selected_index else " "
                    title = str(item.get("title") or item.get("id") or "dataset")
                    group = str(item.get("group") or "")
                    summary = str(item.get("summary") or "")
                    status = str(item.get("status") or "")
                    warn = " ⚠" if status and status != "ok" else ""
                    parts = [p for p in (group, summary) if p]
                    lines.append(f"{marker} {title}{warn}" + (f" — {' · '.join(parts)}" if parts else ""))
    elif section.id == "system":
        lines.extend(_system_content_lines(payload))
    elif section.id == "terminal":
        lines.extend(_terminal_content_lines(payload, state, width))
    elif section.id == "share":
        lines.extend(_share_section_content_lines(payload, state, width))
    elif section.id == "help":
        lines.append("")
        lines.extend(_binding_lines(state, width))
    elif section.id == "artifacts" and bool(payload.get("diff3_mode")):
        lines.extend(_diff3_content_lines(payload, width=width))
    elif section.id == "artifacts" and bool(payload.get("planning_track_mode")):
        lines.extend(_planning_track_content_lines(payload, width=width, compact=width < 74))
    elif section.id == "artifacts" and bool(payload.get("mail_mode")):
        lines.extend(_mail_content_lines(payload, width=width, compact=width < 74))
    elif section.id == "artifacts" and bool(payload.get("helpcenter_mode")):
        lines.extend(_helpcenter_content_lines(payload, width=width, compact=width < 74))
    elif section.id == "artifacts" and bool(payload.get("goal_artifacts_mode")):
        lines.extend(_goal_artifacts_content_lines(payload, width=width, compact=width < 74))
    else:
        items = payload.get("items") or []
        if panel_state == PanelState.EMPTY or not items:
            lines.append("  (empty)")
            lines.append("  press r to refresh")
        else:
            for i, item in enumerate(items[:20]):
                marker = DEFAULT_THEME.selected_prefix if i == state.selected_index else " "
                label = item.get("title") or item.get("id") or str(item)
                lines.append(f"{marker} {label}")

    if state.markdown_source:
        lines.append("")
        for block in detect_diagram_blocks(state.markdown_source):
            lines.extend(render_diagram_fallback(block, width=width))
            lines.append("")
        max_lines = 24 if state.mode.value == "edit" else 8
        lines.append("markdown:")
        lines.extend(render_markdown_lines(state.markdown_source, width=width, max_lines=max_lines))

    return lines



def _templates_content_lines(payload: dict, state: OperatorState, width: int) -> list[str]:
    items: list[dict] = payload.get("items") or []
    bp_count  = int(payload.get("blueprints_count") or 0)
    tpl_count = int(payload.get("templates_count") or 0)
    sys_count = int(payload.get("system_prompts_count") or 0)

    lines: list[str] = []
    summary = f"  {bp_count} blueprints · {tpl_count} templates"
    if sys_count:
        summary += f" · {sys_count} system"
    lines.append(summary)

    sel = state.selected_index
    item_idx = 0

    bp_items  = [it for it in items if it.get("kind") == "blueprint"]
    tpl_items = [it for it in items if it.get("kind") == "template"]
    sys_items = [it for it in items if it.get("kind") == "system_prompt"]

    tree_groups: list[tuple[str, list[dict]]] = []
    if bp_items:
        tree_groups.append(("Blueprints", bp_items))
    if tpl_items:
        tree_groups.append(("Prompt-Templates", tpl_items))
    if sys_items:
        tree_groups.append(("System-Prompts", sys_items))

    if tree_groups:
        lines.append("  Tree:")
    for group_index, (group_name, group_items) in enumerate(tree_groups):
        group_branch = "└" if group_index == len(tree_groups) - 1 else "├"
        lines.append(f"  {group_branch}─ {group_name} ({len(group_items)})")
        for leaf_index, item in enumerate(group_items):
            marker = DEFAULT_THEME.selected_prefix if item_idx == sel else " "
            leaf_branch = "└" if leaf_index == len(group_items) - 1 else "├"
            child_prefix = "     " if group_index == len(tree_groups) - 1 else "  │  "
            title = str(item.get("title") or "")
            if str(item.get("kind") or "") == "blueprint":
                roles = int(item.get("roles_count") or 0)
                arts = int(item.get("artifacts_count") or 0)
                base = str(item.get("base_team_type") or "")
                meta_parts = [f"{roles}r"]
                if arts:
                    meta_parts.append(f"{arts}a")
                if base:
                    meta_parts.append(base)
                if item.get("is_seed"):
                    meta_parts.append("seed")
                meta = f" [{', '.join(meta_parts)}]" if meta_parts else ""
            elif str(item.get("kind") or "") == "system_prompt":
                svc = str(item.get("service") or "")
                meta = f" [{svc}]" if svc else ""
            else:
                preview = str(item.get("prompt_preview") or "").strip()
                meta = f" :: {preview[: max(0, width // 3)]}" if preview else ""
            lines.append(f"{marker}{child_prefix}{leaf_branch}─ {title}{meta}")
            item_idx += 1

    if not items:
        lines.append("  (keine Templates, Blueprints oder System-Prompts)")
        lines.append("  press r to refresh")

    return [_clip(line, width) for line in lines]



def _audit_viewer_content_lines(state: OperatorState, width: int, *, viewport_height: int | None = None) -> list[str]:
    game = state.header_logo_game if isinstance(state.header_logo_game, dict) else {}
    viewer = dict(game.get("audit_viewer") or {})
    title = str(viewer.get("title") or "dataset")
    group = str(viewer.get("group") or "")
    mode = str(viewer.get("mode") or "read_only")
    text = str(viewer.get("text") or "")
    text_lines = text.splitlines() or [""]
    if mode == "confirm_cleanup":
        header = f"  Audit Cleanup · {title}" + (f" ({group})" if group else "")
        hint = "  Links/Rechts waehlt Button · Enter ausfuehren · Esc abbrechen"
    elif mode == "cleanup_result":
        header = f"  Audit Cleanup Ergebnis · {title}" + (f" ({group})" if group else "")
        hint = "  Enter/Esc schliesst diese Meldung"
    else:
        header = f"  Audit Viewer · {title}" + (f" ({group})" if group else "")
        hint = "  read-only · Esc schließen · ↑/↓ scroll · ←/→ horizontal scroll"
    lines = [
        header,
        hint,
        "",
    ]
    pane_title_rows = 1
    viewer_header_rows = 3
    visible_rows = max(1, int(viewport_height or 24) - pane_title_rows - viewer_header_rows)
    view_line_offset = max(0, int(viewer.get("view_line_offset") or 0))
    view_col_offset = max(0, int(viewer.get("view_col_offset") or 0))
    max_line_width = max((len(line) for line in text_lines), default=0)
    visible_cols = max(8, width - 8)
    max_col_offset = max(0, max_line_width - visible_cols)
    view_col_offset = min(view_col_offset, max_col_offset)
    max_line_offset = max(0, len(text_lines) - visible_rows)
    view_line_offset = min(view_line_offset, max_line_offset)
    end_line = min(len(text_lines), view_line_offset + visible_rows)
    for row_index in range(view_line_offset, end_line):
        line = text_lines[row_index]
        snippet = line[view_col_offset : view_col_offset + visible_cols]
        lines.append(f"  {row_index + 1:>4} {snippet}")
    if end_line < len(text_lines):
        lines.append(f"  ... ({len(text_lines) - end_line} weitere Zeilen)")
    if mode == "confirm_cleanup":
        choice = str(viewer.get("confirm_choice") or "cancel").strip().lower()
        delete_selected = choice == "delete"
        delete_button = ">[ Loeschen ]<" if delete_selected else " [ Loeschen ] "
        cancel_button = ">[ Abbrechen ]<" if not delete_selected else " [ Abbrechen ] "
        lines.append("")
        lines.append(f"  {delete_button}   {cancel_button}")
    return [_clip(line, width) for line in lines]



def _highlight_template_line(line: str) -> tuple[str, int]:
    if not line:
        return "", 0
    out: list[str] = []
    issues = 0
    i = 0
    n = len(line)
    while i < n:
        if line.startswith("{{", i):
            end = line.find("}}", i + 2)
            if end < 0:
                issues += 1
                out.append(f"{_TPL_ERR_COLOR}{line[i:]}{_TPL_RESET}")
                break
            token = line[i : end + 2]
            expr = line[i + 2 : end].strip()
            if expr and _TPL_VAR_NAME_RE.match(expr):
                out.append(f"{_TPL_VAR_COLOR}{token}{_TPL_RESET}")
            else:
                issues += 1
                out.append(f"{_TPL_WARN_COLOR}{token}{_TPL_RESET}")
            i = end + 2
            continue
        if line.startswith("}}", i):
            issues += 1
            out.append(f"{_TPL_ERR_COLOR}}}}}{_TPL_RESET}")
            i += 2
            continue
        out.append(line[i])
        i += 1
    return "".join(out), issues



def _templates_editor_content_lines(state: OperatorState, width: int, *, viewport_height: int | None = None) -> list[str]:
    game = state.header_logo_game if isinstance(state.header_logo_game, dict) else {}
    editor = dict(game.get("template_editor") or {})
    title = str(editor.get("title") or "template")
    kind = str(editor.get("kind") or "template")
    text = str(editor.get("text") or "")
    cursor = max(0, min(len(text), int(editor.get("cursor") or 0)))
    dirty = bool(editor.get("dirty"))
    text_lines = text.splitlines() or [""]
    lint_total = 0
    lint_by_line: dict[int, int] = {}
    for idx, source in enumerate(text_lines):
        _, issues = _highlight_template_line(source)
        if issues > 0:
            lint_by_line[idx] = issues
            lint_total += issues
    lines = [
        f"  Template Editor · {title} ({kind}){' *' if dirty else ''}",
        (
            "  Ctrl+S speichern · Esc schließen · "
            + (
                f"{_TPL_ERR_COLOR}Lint: {lint_total} issue(s){_TPL_RESET}"
                if lint_total
                else "Lint: ok"
            )
        ),
        "",
    ]

    before = text[:cursor]
    cursor_line = before.count("\n")
    cursor_col = len(before.rsplit("\n", 1)[-1])
    # Use the full visible middle-pane height instead of a fixed 20-line window.
    pane_title_rows = 1  # added by _content_lines
    editor_header_rows = 3
    visible_rows = max(1, int(viewport_height or 24) - pane_title_rows - editor_header_rows)
    start_line = max(0, int(editor.get("view_line_offset") or 0))
    if start_line + visible_rows > len(text_lines):
        start_line = max(0, len(text_lines) - visible_rows)
    end_line = min(len(text_lines), start_line + visible_rows)
    visible_cols = max(8, width - 6)
    max_line_width = max((len(line) for line in text_lines), default=0)
    col_offset = max(0, int(editor.get("view_col_offset") or 0))
    max_col_offset = max(0, max_line_width - visible_cols)
    col_offset = min(col_offset, max_col_offset)
    for row_index in range(start_line, end_line):
        source_line = text_lines[row_index]
        issues_on_line = int(lint_by_line.get(row_index) or 0)
        if row_index == cursor_line:
            line_prefix = ">"
        elif issues_on_line > 0:
            line_prefix = f"{_TPL_ERR_COLOR}!{_TPL_RESET}"
        else:
            line_prefix = " "
        line_view = source_line[col_offset : col_offset + visible_cols]
        rendered_line, _ = _highlight_template_line(line_view)
        if row_index == cursor_line:
            local_col = max(0, min(visible_cols, cursor_col - col_offset))
            rendered_line = _overlay_at_visible_col(
                rendered_line,
                local_col,
                f"{_TPL_WARN_COLOR}|{_TPL_RESET}",
            )
        lines.append(f"{line_prefix} {row_index + 1:>3} {rendered_line}")
    if end_line < len(text_lines):
        lines.append(f"  ... ({len(text_lines) - end_line} weitere Zeilen)")
    return [_clip(line, width) for line in lines]



def _content_chat_plain_ask_lines(
    state: OperatorState, width: int, *, height: int | None = None
) -> list[str] | None:
    """Render the :ask answer as plain text in the middle pane (no visual
    viewport, no tree compression, no markdown rendering). Returns None if
    there is no AI answer to show yet — caller falls back to the visual
    viewport.
    """
    game = state.header_logo_game if isinstance(state.header_logo_game, dict) else {}
    question = str(game.get("tutor_ask_question") or "").strip()
    latest = _latest_ai_message_text(game)
    if latest is None and not question:
        return None
    sender_label, answer_text = latest if latest is not None else ("AI-Snake", "")

    title = _pane_title("AI-SNAKE ANTWORT", state.focus == FocusPane.CONTENT)
    lines: list[str] = [title]

    # Frage als Header (Echo der User-Eingabe)
    if question:
        q_header = f"  ? {question[: max(4, width - 6)]}"
        lines.append(_clip(q_header, width))
        lines.append("  " + "─" * max(4, width - 4))

    if not answer_text:
        # Noch keine Antwort — Wartezustand
        lines.append("  \x1b[2m…warte auf Antwort\x1b[0m")
        out = [_clip(l, width) for l in lines]
        return _truncate_to_height(out, height)

    # Plain-Text-Antwort, hart an width gewrappt (kein visuelles Viewport-Wrapping).
    body_width = max(8, width - 2)
    lines.append(f"  \x1b[38;2;120;180;255m{sender_label}:\x1b[0m")
    body: list[str] = []
    for raw_line in answer_text.splitlines() or [""]:
        if not raw_line:
            body.append("")
            continue
        body.extend(_wrap_plain(raw_line, body_width) or [""])
    lines.extend("  " + row for row in body)
    lines.append(f"  \x1b[2mStrg+C: Antwort kopieren  PgUp/PgDn: scrollen\x1b[0m")

    out = [_clip(l, width) for l in lines]

    # Wenn der Renderer eine Höhe vorgibt, MUSS die Ausgabe exakt diese Höhe haben.
    # Lange Antworten werden gescrollt; der Scroll-Offset wird im game-state gehalten.
    out = _clip_with_scroll(out, game=game, height=height, width=width)

    # CRPS-007: Profile Inspector footer — re-insert AFTER scroll clipping
    # so it stays visible regardless of scroll offset. We splice it into the
    # first available chrome slot (just below the cyan sender line). This
    # works for both short (no scroll) and long (scrolled) answers because
    # we always shrink the visible body by the inspector's height first.
    inspector_lines = _profile_inspector_lines(game, width)
    if inspector_lines and out:
        out = _splice_inspector_into_chrome(out, inspector_lines, height)

    return out



def _profile_inspector_lines(game: dict, width: int) -> list[str]:
    """CRPS-007: build the Profile Inspector footer lines for the AI-Snake
    answer pane.

    Returns 0-2 lines. The first line is the compact one-liner that the user
    sees in 99% of cases. The second line is the reasons trace (only shown
    when the env-flag ANANTA_TUI_PROFILE_INSPECTOR_VERBOSE=1 is set).

    The inspector is fed by ``game['last_snake_ask_trace']`` (set by
    ``_capture_snake_ask_trace`` in chat_mixin). It is intentionally compact
    so it does not crowd the response pane:

      [Profil] ananta-codecompass • domain=codecompass • intent=code_explanation
              • trigger=auto • flag=auto • Kontext: 7 Treffer (...) [ananta-codecompass]
    """
    trace = game.get("last_snake_ask_trace")
    if not isinstance(trace, dict):
        return []
    # Match the seq bump set by _capture_snake_ask_trace. If the trace was
    # never refreshed (e.g. a previous :ask from an older session), still
    # render it — it is informative even when stale. We do NOT add a "stale"
    # marker because the user can see the timestamp implicitly through the
    # question they asked.
    profile_id = str(trace.get("profile_id") or "?").strip() or "?"
    domain = str(trace.get("domain") or "?").strip() or "?"
    intent = str(trace.get("intent") or "?").strip() or "?"
    trigger_mode = str(trace.get("trigger_mode") or "auto").strip() or "auto"
    feature_flag = str(trace.get("feature_flag") or "auto").strip() or "auto"
    src_types = trace.get("source_types") or []
    if isinstance(src_types, list):
        src_str = ",".join(str(s) for s in src_types if str(s).strip())
    else:
        src_str = ""
    src_str = src_str or "-"

    summary = str(game.get("last_snake_ask_summary") or "").strip()
    compact = (
        f"  \x1b[2m[Profil]\x1b[0m \x1b[38;2;120;180;255m{profile_id}\x1b[0m"
        f"  \x1b[2m•\x1b[0m  d={domain}"
        f"  \x1b[2m•\x1b[0m  i={intent}"
        f"  \x1b[2m•\x1b[0m  trig={trigger_mode}"
        f"  \x1b[2m•\x1b[0m  flag={feature_flag}"
    )
    if summary:
        compact += f"  \x1b[2m•\x1b[0m  {summary[: max(0, width - 2)]}"
    lines: list[str] = [_clip(compact, width)]

    # Optional second line: reasons trace, only in verbose mode. The flag
    # is read on every render so a developer can toggle verbosity without
    # restarting the TUI. Default: off (single-line inspector is enough for
    # 99% of the users).
    verbose = str(os.environ.get("ANANTA_TUI_PROFILE_INSPECTOR_VERBOSE") or "").strip().lower() in {
        "1", "true", "yes", "on"
    }
    if verbose:
        reasons = trace.get("reasons") or []
        if isinstance(reasons, list) and reasons:
            reason_text = "  ".join(str(r) for r in reasons[:5])
            lines.append(_clip(f"  \x1b[2mreasons: {reason_text}\x1b[0m", width))
        lines.append(_clip(f"  \x1b[2msources: {src_str}\x1b[0m", width))
    return lines



def _content_visual_viewport_lines(state: OperatorState, width: int) -> list[str]:
    game = state.header_logo_game if isinstance(state.header_logo_game, dict) else {}
    runtime = dict(game.get("visual_runtime_status") or {})
    title = _pane_title("VISUAL VIEWPORT", state.focus == FocusPane.CONTENT)
    show_doc_diag = str(os.environ.get("ANANTA_TUI_DOC_DIAGNOSTICS") or "").strip().lower() in {"1", "true", "yes", "on"}

    frame_lines = [str(row) for row in (game.get("visual_viewport_frame_lines") or []) if isinstance(row, str)]
    if not frame_lines:
        lines = [title, "  visual runtime aktiv, warte auf frame ..."]
        view = str(runtime.get("active_view") or game.get("visual_viewport_active_view") or "-")
        lines.append(f"  view={view}")
        return [_clip(l, width) for l in lines]

    # Scroll metadata from scene (populated by _sync_visual_viewport_state)
    vp_meta = dict(game.get("visual_viewport_scene_meta") or {})
    content_lines = int(vp_meta.get("content_lines") or 0)
    max_line_width = int(vp_meta.get("max_line_width") or 0)
    v_offset = int(vp_meta.get("scroll_offset") or 0)
    h_offset = int(vp_meta.get("h_offset") or 0)
    n_frame = len(frame_lines)

    # V-scrollbar: content must exceed viewport
    needs_v = content_lines > n_frame and n_frame > 0
    # H-scrollbar: content wider than full panel (frame renders at region.columns ≈ width)
    # Use a tolerance of 2 to avoid triggering due to v-scrollbar char
    needs_h = max_line_width > width + 2 and n_frame > 0

    # H-scrollbar occupies the last frame line
    h_bar_rows = 1 if needs_h else 0
    body_rows = max(0, n_frame - h_bar_rows)

    # Build vertical scrollbar chars for body rows
    v_bar: list[str] = []
    if needs_v:
        v_bar = render_scrollbar_column(
            content_height=content_lines,
            viewport_height=body_rows if body_rows > 0 else n_frame,
            offset=v_offset,
            height=body_rows if body_rows > 0 else n_frame,
        )

    # Compose output lines
    lines: list[str] = [title]
    if show_doc_diag and str(runtime.get("active_view") or game.get("visual_viewport_active_view") or "") == "markdown_mermaid_document":
        diag = dict(game.get("visual_viewport_scene_meta") or {})
        profile = str(diag.get("docs_graphics_profile") or "-")
        backend = str(diag.get("mermaid_renderer_used") or "-")
        fallback_count = int(diag.get("mermaid_fallback_count") or 0)
        cache_hits = int(diag.get("mermaid_cache_hits") or 0)
        cache_misses = int(diag.get("mermaid_cache_misses") or 0)
        cache_ratio = (cache_hits / max(1, cache_hits + cache_misses)) * 100.0
        lines.append(
            _clip(
                f"  docdiag profile={profile} backend={backend} fallback={fallback_count} cache={cache_hits}/{cache_misses} ({cache_ratio:.0f}%)",
                width,
            )
        )

    # Body lines: v-scrollbar overlaid as LAST character of each line (no width change)
    for i in range(body_rows):
        raw = frame_lines[i] if i < len(frame_lines) else ""
        if needs_v:
            bar_ch = v_bar[i] if i < len(v_bar) else " "
            sb = _render_vscrollbar_char(bar_ch)
            # Clip to width-1, then append scrollbar char → total = width chars
            lines.append(_clip(raw, width - 1) + sb)
        else:
            lines.append(_clip(raw, width))

    # Last row: h-scrollbar or remaining frame line
    if needs_h:
        track_w = max(4, width - 4)
        h_row = _render_hscrollbar_row(
            content_width=max_line_width,
            viewport_width=width,
            offset=h_offset,
            track_width=track_w,
        )
        lines.append(_clip(h_row, width))
    elif n_frame > body_rows:
        raw = frame_lines[body_rows] if body_rows < len(frame_lines) else ""
        if needs_v and len(v_bar) > body_rows:
            sb = _render_vscrollbar_char(v_bar[body_rows])
            lines.append(_clip(raw, width - 1) + sb)
        else:
            lines.append(_clip(raw, width))

    # Mode hint (Ctrl+Space toggle): replace last body line, not touching the h-scrollbar
    if is_showing_chat_long_message(game) and body_rows > 0 and len(lines) >= body_rows + 1:
        mode = get_render_mode(game)
        other = "Plain-Text" if mode == "rendered" else "Markdown/Mermaid"
        shortcut = display_for_action("open_long_chat_message", "Ctrl+Space")
        hint_col = "\x1b[38;2;80;120;80m" if mode == "rendered" else "\x1b[38;2;100;100;140m"
        mode_label = "Markdown/Mermaid" if mode == "rendered" else "Plain-Text"
        hint = f"{hint_col}  ▸ {mode_label}  {shortcut}→{other}\x1b[0m"
        # Replace the last body line (index: title=0, body=1..body_rows)
        lines[body_rows] = _clip(hint, width)

    # Status footer
    view = str(runtime.get("active_view") or game.get("visual_viewport_active_view") or "-")
    renderer_name = str(runtime.get("active_renderer") or "-")
    adapter = str(runtime.get("active_adapter") or "-")
    fallback = str(runtime.get("fallback_reason") or "").strip()
    lines.append("")
    lines.append(f"  {view} · {renderer_name} · {adapter}")
    if state.focus == FocusPane.CONTENT:
        up = display_for_action("scroll_line_up", "Shift+Up")
        down = display_for_action("scroll_line_down", "Shift+Down")
        page_up = display_for_action("scroll_page_up", "PgUp")
        page_down = display_for_action("scroll_page_down", "PgDn")
        left = display_for_action("scroll_left", "Shift+Left")
        right = display_for_action("scroll_right", "Shift+Right")
        lines.append(_clip(f"  Scroll {up}/{down} {page_up}/{page_down}  H {left}/{right}  Ctrl+Cursor  Maus/Rad", width))
        if content_lines > 0:
            max_v = max(0, content_lines - max(1, body_rows))
            max_h = max(0, max_line_width - max(1, width))
            lines.append(_clip(f"  pos v:{v_offset}/{max_v} h:{h_offset}/{max_h}", width))
    if fallback:
        lines.append(f"  fallback: {fallback}")
    return [_clip(l, width) for l in lines]



def _content_shortcut_lines(state: OperatorState, width: int) -> list[str]:
    lines = [_pane_title("SHORTCUTS", state.focus == FocusPane.CONTENT)]
    for combo, label in shortcut_tokens_for_area("shortcuts"):
        lines.append(f"  {combo} {label}")
    lines.append("")
    lines.append("  Chat:")
    lines.append("    // im Chat-Input: Shortcut-Ansicht ein/aus")
    lines.append("    /  im Chat-Input: Command-Modus")
    lines.append("    :  normal: Command-Modus")
    return [_clip(line, width) for line in lines]



def _content_ai_snake_config_lines(state: OperatorState, width: int) -> list[str]:
    game = state.header_logo_game if isinstance(state.header_logo_game, dict) else {}
    combo = dict(game.get("ai_snake_config_combo") or {})
    combo_open = bool(combo.get("open"))
    lines = [_pane_title("AI-SNAKE CONFIG", state.focus == FocusPane.CONTENT)]
    lines.append("  Visual + Chat getrennt konfigurieren")
    lines.append("  Enter/Click oeffnet Auswahl | Auswahl mit Ctrl+J/K")
    lines.append("")
    items = ai_snake_config_items(dict(game))
    selected = max(0, int(state.selected_index))
    for idx, item in enumerate(items):
        marker = DEFAULT_THEME.selected_prefix if idx == selected else " "
        label = str(item.get("label") or item.get("key") or f"item-{idx}")
        key = str(item.get("key") or "")
        value = item.get("value")
        value_text = ("AN" if value else "AUS") if isinstance(value, bool) else str(value or "-")
        if key == "chat_model":
            value_text = chat_model_option_label(dict(game), value_text)
        lines.append(f"{marker} {label:<22} {value_text}")
    if combo_open:
        key = str(combo.get("key") or "")
        item = next((row for row in items if str(row.get("key") or "") == key), {})
        label = str(item.get("label") or key or "-")
        filter_text = str(combo.get("filter") or "")
        cursor = max(0, min(len(filter_text), int(combo.get("filter_cursor") or len(filter_text))))
        filtered, filter_error = ai_snake_config_filter_options(dict(game), key=key, regex_filter=filter_text)
        selected_option = max(0, min(max(0, len(filtered) - 1), int(combo.get("selected_option") or 0)))
        lines.append("")
        lines.append(f"  COMBOBOX: {label}")
        lines.append("  Regex-Filter (Enter uebernimmt Eingabe):")
        lines.append("  > " + _inline_input_with_cursor(filter_text, cursor, max(8, width - 6)))
        if filter_error:
            lines.append(f"  ! {filter_error}")
        if not filtered:
            lines.append("    (keine Treffer)")
        else:
            for idx, option in enumerate(filtered[:10]):
                marker = ">" if idx == selected_option else " "
                option_text = chat_model_option_label(dict(game), option) if key == "chat_model" else option
                lines.append(f"  {marker} {option_text}")
        lines.append("  Up/Down oder Click waehlt Option | Esc schliesst Auswahl")
    lines.append("")
    lines.append(f"  {display_for_action('toggle_ai_snake_config', 'Ctrl+A')} Config ein/aus")
    lines.append(f"  {display_for_action('toggle_tutorial_ai', 'Ctrl+U')} Visual AI hart an/aus")
    return [_clip(line, width) for line in lines]



def _dashboard_content_lines(payload: dict, *, state: OperatorState | None = None, width: int = 72) -> list[str]:
    lines = []
    agents = payload.get("agents") or {}
    llm = payload.get("llm_providers") or {}
    queue = payload.get("queue") or {}
    goal_summary = payload.get("goal_summary") or payload.get("goals") or {}
    task_summary = payload.get("task_summary") or payload.get("tasks") or {}

    lines.append("  System")
    if agents:
        online = agents.get("online", "?")
        total = agents.get("total", "?")
        lines.append(f"    Agents  {online}/{total} online")
    if llm:
        for provider, status in llm.items():
            lines.append(f"    {provider:<10} {status}")
    if queue:
        depth = queue.get("depth", 0)
        lines.append(f"    Queue   {depth} tasks pending")
    if not agents and not llm and not queue:
        lines.append("    go to System for health info")

    if goal_summary or task_summary:
        lines.append("")
        lines.append("  Overview")
        if goal_summary:
            lines.append(f"    Goals:  {goal_summary}")
        if task_summary:
            lines.append(f"    Tasks:  {task_summary}")
    else:
        lines.append("")
        lines.append("  go to Goals or Tasks for details")

    return lines



def _system_content_lines(payload: dict) -> list[str]:
    lines = []
    agents = payload.get("agents") or {}
    llm = payload.get("llm_providers") or {}
    queue = payload.get("queue") or {}
    contracts = payload.get("contracts") or []

    if agents:
        online = agents.get("online", "?")
        total = agents.get("total", "?")
        lines.append(f"  Agents:    {online}/{total} online")
    if llm:
        for provider, status in llm.items():
            lines.append(f"  {provider:<12} {status}")
    if queue:
        depth = queue.get("depth", 0)
        counts = queue.get("counts") or {}
        lines.append(f"  Queue:     {depth} pending")
        if counts:
            parts = [f"{k}={v}" for k, v in counts.items() if v]
            if parts:
                lines.append(f"             {' '.join(parts)}")
    if contracts:
        lines.append("")
        lines.append("  Contracts:")
        for c in contracts[:5]:
            lines.append(f"    {c}")
    if not lines:
        lines.append("  press r to load system data")

    return lines



def _share_section_content_lines(payload: dict, state: OperatorState, width: int) -> list[str]:
    from client_surfaces.operator_tui.share_menu import share_section_lines
    # Live-Status aus game state in payload einbauen: Share-Aktionen laufen im
    # Hintergrund und sollen sofort im Menü sichtbar werden.
    game = state.header_logo_game if isinstance(state.header_logo_game, dict) else {}
    merged = dict(payload)
    if game.get("oidc_device_flow"):
        merged["oidc_device_flow"] = dict(game["oidc_device_flow"])
    if game.get("share_status_message"):
        merged["share_status_message"] = str(game["share_status_message"])
    if game.get("share_audit_items"):
        merged["share_audit_items"] = list(game.get("share_audit_items") or [])
    active_session = dict(game.get("share_active_session") or {})
    if active_session:
        active_id = str(active_session.get("id") or "")
        # sessions (gesamt)
        sessions = [dict(s) for s in list(merged.get("sessions") or []) if isinstance(s, dict)]
        if active_id:
            sessions = [s for s in sessions if str(s.get("id") or "") != active_id]
        merged["sessions"] = [active_session, *sessions]
        # sessions_mine: aktive Session an erster Stelle
        mine = [dict(s) for s in list(merged.get("sessions_mine") or []) if isinstance(s, dict)]
        if active_id:
            mine = [s for s in mine if str(s.get("id") or "") != active_id]
        merged["sessions_mine"] = [active_session, *mine]
        merged["selected_session"] = active_session
        if active_session.get("participants") and not merged.get("participants"):
            merged["participants"] = list(active_session.get("participants") or [])
    return share_section_lines(merged, width=width, selected_index=state.selected_index)



def _terminal_content_lines(payload: dict, state: OperatorState, width: int) -> list[str]:
    lines: list[str] = []
    targets = payload.get("targets") or []
    sessions = payload.get("sessions") or []

    lines.append("  Targets:")
    if not targets:
        lines.append("    no targets available (terminal feature disabled?)")
    else:
        for i, t in enumerate(targets):
            marker = DEFAULT_THEME.selected_prefix if i == state.selected_index else " "
            ttype = t.get("target_type", "?")
            tid = t.get("target_id", "?")
            risk = " [HIGH RISK]" if ttype in {"hub", "hub_as_worker"} else ""
            lines.append(f"{marker} {ttype:<16} {tid}{risk}")

    lines.append("")
    lines.append("  Sessions:")
    if not sessions:
        lines.append("    no active sessions")
    else:
        for s in sessions:
            sid = (s.get("id") or "?")[:16] + "…"
            stype = s.get("target_type", "?")
            status = s.get("status", "?")
            ro = " [ro]" if s.get("read_only") else ""
            lines.append(f"  {sid} {stype:<14} {status}{ro}")

    lines.append("")
    lines.append("  Commands:")
    lines.append("    :tmux targets    list targets")
    lines.append("    :tmux start      create session")
    lines.append("    :tmux attach <id> attach")
    lines.append("    :tmux kill <id>  kill session")
    return lines



def _detail_lines(state: OperatorState, width: int, *, height: int | None = None) -> list[str]:
    game = state.header_logo_game if isinstance(state.header_logo_game, dict) else {}
    if bool(game.get("shortcut_help_open")):
        return _context_shortcut_lines(state, width)
    if bool(game.get("active")) and bool(game.get("free_mode")):
        return _snake_ai_chat_detail_lines(state, width, height=max(3, int(height or 18)))
    if height is None:
        return [*_standard_detail_lines(state, width), "", *_chat_detail_lines(state, width)]
    chat_height = max(7, min(int(height), int(height * 0.45)))
    detail_height = max(0, int(height) - chat_height)
    detail_lines = _standard_detail_lines(state, width)[:detail_height]
    if len(detail_lines) < detail_height:
        detail_lines.extend([""] * (detail_height - len(detail_lines)))
    chat_lines = _chat_detail_lines(state, width, max_height=chat_height, bottom_align=True)
    return [_clip(line, width) for line in [*detail_lines, *chat_lines]][: int(height)]



def _standard_detail_lines(state: OperatorState, width: int) -> list[str]:
    section = get_section(state.section_id)
    lines = [_pane_title("DETAIL", state.focus == FocusPane.DETAIL)]
    runtime_lines = _runtime_detail_lines(state, width)
    if runtime_lines:
        lines.extend(runtime_lines)

    if state.mode.value == "inspect":
        lines.append("")
        lines.append("  inspect:")
        lines.extend(
            f"    {l}"
            for l in build_inspection_detail(
                section.id, (state.section_payloads or {}).get(section.id, {}), state.selected_index
            )
        )

    if state.pending_action:
        lines.append("")
        lines.append("  ! Pending action:")
        lines.append(f"    {state.pending_action.get('name')}")
        lines.append(f"    risk={state.pending_action.get('risk')}")
        lines.append("    :confirm  to execute")
        lines.append("    :cancel   to abort")

    if state.audit_context:
        lines.append("")
        lines.append("  Audit:")
        lines.append(f"    intent={state.audit_context.get('intent')}")
        lines.append(f"    action={state.audit_context.get('action')}")

    if state.browser_fallback_url:
        lines.append("")
        lines.append(f"browser={state.browser_fallback_url}")

    lines.append("")
    lines.append("  Commands:")
    lines.append("    :section <id>   switch section")
    lines.append("    :refresh        reload data")
    lines.append("    :focus <pane>   nav/content/detail")
    lines.append("    :help           keybindings")
    if section.id in {"goals", "tasks"}:
        lines.append("    :inspect        show selected")
        lines.append("    :action <n> <r> dispatch action")
    if section.id == "templates":
        lines.append("    :inspect        detail ansicht")
        lines.append("    :tpl new        neues template")
        lines.append("    :bp new         neues blueprint")
        lines.append("    :bp instantiate team erstellen")
    if section.id == "artifacts":
        payload = (state.section_payloads or {}).get(section.id, {})
        if bool(payload.get("diff3_mode")):
            lines.append("    :diff3")
            lines.append("    :diff3 panel <A|B|C> current [--mode <mode>]")
            lines.append("    :diff3 panel <A|B|C> output <output-id>")
            lines.append("    :diff3 panel <A|B|C> ai <review|explain|risk|tests|patch|chat>")
            lines.append("    :diff3 panel <A|B|C> mode <render-mode>")
            lines.append("    :diff3 panel <A|B|C> filter key=value ...")
            lines.append("    :diff3 focus <A|B|C> | :diff3 scroll ...")
            lines.append("    :diff3 sync on|off | :diff3 ai <mode> | :diff3 ai run [mode]")
        if bool(payload.get("goal_artifacts_mode")):
            lines.append("    :goal artifacts [filter ...|clear-filter]")
            lines.append("    :goal sources candidates")
            lines.append("    :goal source grant/revoke/detail ...")
            lines.append("    :artifact provenance <output-id>")
            lines.append("    :artifact prompt <output-id>")
            lines.append("    :artifact config <output-id>")
        if bool(payload.get("planning_track_mode")):
            lines.append("    :plan track [--from-goal <goal-id>]")
            lines.append("    :plan track filter status=... priority=... risk=... type=...")
            lines.append("    :plan track clear-filter")
            lines.append("    :plan track adopt <output-id> | reject <output-id>")
            lines.append("    :plan track execute-next | sync-status <plan-task-id> <status>")
            lines.append("    :plan track diff <left-output-id> <right-output-id>")
            lines.append("    :plan summary doctor <file> | fix <file> | recompute")
        if bool(payload.get("helpcenter_mode")):
            lines.append("    :helpcenter")
            lines.append("    :helpcenter ingest github-failures [--repo owner/repo] [--limit N] [--dry-run]")
            lines.append("    :helpcenter open <analysis-id>")
            lines.append("    :helpcenter suggest-followup [analysis-id]")
        if bool(payload.get("mail_mode")):
            lines.append("    :mail")
            lines.append("    :mail account list|status|create|use|disable|delete")
            lines.append("    :mail mailbox <name> | :mail open <message-id|uid> | :mail load-body [id]")
            lines.append("    :mail search from:... to:... subject:... mailbox:... date:YYYY..YYYY unread:true")
            lines.append("    :mail filter key=value ... | :mail scroll <delta>")
            lines.append("    :mail note add <text> | :mail link-current-to-goal <goal-id>")
            lines.append("    :mail artifact register-current [--scope metadata_only|excerpt|full_body]")
            lines.append("    :mail attachment list|download <filename>|register <filename>")
            lines.append("    :mail export current --format json|text|eml [--include-body --confirm-body] [--goal <goal-id>]")
            lines.append("    :mail grant-current-to-goal <goal-id> [--scope ...] [--confirm-full-body]")
            lines.append("    :mail revoke-grant <goal-id> <grant-id> | :mail context-envelope <goal-id> [--target ...]")
            lines.append("    :mail snake-explain")
    return [_clip(line, width) for line in lines]



def _context_shortcut_lines(state: OperatorState, width: int) -> list[str]:
    game = state.header_logo_game if isinstance(state.header_logo_game, dict) else {}
    lines = [_pane_title("SHORTCUTS", state.focus == FocusPane.DETAIL)]
    for combo, label in shortcut_tokens_for_area("shortcuts"):
        lines.append(f"  {combo} {label}")
    lines.append("  : Command-Modus (vim-ähnlich)")
    if bool(game.get("free_mode")) or bool(game.get("ui_steering")):
        lines.append("")
        lines.append("  Snake:")
        lines.append("    Left drag: mark area")
        lines.append("    Left click: select/explain")
        lines.append("    Right click: copy mark")
        lines.append(f"    {display_for_action('snake_toggle_selection', 'Ctrl+X')}: select/frame")
        lines.append(f"    {display_for_action('snake_replace_selection', 'Ctrl+V')}: replace command text")
        lines.append(f"    {display_for_action('snake_clear_marks', 'Ctrl+Z')}: clear marks")
        lines.append(f"    {display_for_action('toggle_mouse_follow', 'Ctrl+O')}: mouse follow")
    if bool(game.get("chat_panel_open")) or bool(game.get("artifact_chat_focus")):
        lines.append("")
        lines.append("  Chat:")
        lines.append(f"    {display_for_action('cycle_focus_or_channel', 'Ctrl+W')}: channel")
        lines.append("    Enter: send")
        lines.append("    Esc: leave input")
        lines.append(f"    {display_for_action('clear_chat_input', 'Ctrl+L')}: clear input")
        lines.append("")
        lines.append("  Sessions:")
        lines.append("    /session             liste alle sessions")
        lines.append("    /session <id>        wechsle session")
        lines.append("    /session new <name>  neue session")
        lines.append("    /session delete <id> session löschen")
        lines.append("    /session rename <id> <name>")
        lines.append("    /clear               verlauf löschen")
    lines.append("")
    lines.append("  Commands:")
    lines.append("    :help full help")
    lines.append("    :section <id>")
    lines.append("    :refresh")
    return [_clip(line, width) for line in lines]



def _chat_detail_lines(
    state: OperatorState,
    width: int,
    *,
    max_height: int | None = None,
    bottom_align: bool = False,
) -> list[str]:
    game = state.header_logo_game if isinstance(state.header_logo_game, dict) else {}
    try:
        from client_surfaces.operator_tui.chat_state import get_chat_state, sanitize_text
        chat = get_chat_state(dict(game))
    except Exception:
        chat = {}
        sanitize_text = lambda value: str(value)

    active_ch_id = str(chat.get("active_channel") or "room:main")
    channels = chat.get("channels") if isinstance(chat.get("channels"), dict) else {}
    ch = channels.get(active_ch_id) if isinstance(channels, dict) else {}
    if not isinstance(ch, dict):
        ch = {}
    ch_type_raw = ch.get("channel_type") or "room"
    ch_type = str(getattr(ch_type_raw, "value", ch_type_raw))
    chat_focus = bool(chat.get("chat_focus")) or bool(game.get("artifact_chat_focus"))
    active_label = _chat_channel_label(active_ch_id)
    unread_total = 0
    if isinstance(channels, dict):
        unread_total = sum(int(c.get("unread") or 0) for c in channels.values() if isinstance(c, dict))

    header_lines = [_pane_title("CHAT", state.focus == FocusPane.DETAIL)]
    focus_note = " INPUT" if chat_focus else ""
    header_lines.append(f"  ACTIVE: {active_label}{focus_note}")
    if unread_total:
        header_lines.append(f"  unread: {unread_total}")
    selector = _plain_channel_selector(active_ch_id)
    header_lines.append(f"  {selector}")
    # Session indicator
    try:
        from client_surfaces.operator_tui.chat_state import get_sessions, get_active_session
        _active_sess = get_active_session(chat)
        _all_sessions = get_sessions(chat)
        if _active_sess:
            _sicon = str(_active_sess.get("icon") or "💬")
            _sname = str(_active_sess.get("name") or _active_sess.get("id") or "?")
            _sback = str((_active_sess.get("settings") or {}).get("chat_backend") or "")
            _scc = bool((_active_sess.get("settings") or {}).get("chat_use_codecompass"))
            _cc_tag = " CC" if _scc else ""
            _back_tag = f" [{_sback}]" if _sback else ""
            _n = len(_all_sessions)
            header_lines.append(f"  {_sicon} {_sname}{_back_tag}{_cc_tag}  ({_n} sessions)")
    except Exception:
        pass
    header_lines.append("  " + "-" * max(8, width - 4))

    messages: list[dict] = []
    for msg in list(ch.get("messages") or [])[-10:]:
        if isinstance(msg, dict):
            messages.append(msg)
    partial = str(game.get("llm_streaming_partial") or "").strip()
    if partial and active_ch_id == "ai:tutor":
        messages.append({"sender_kind": "ai", "sender_id": "s-ai", "text": partial, "delivery_state": "streaming"})
    message_lines: list[str] = []
    if not messages:
        message_lines.append("  keine Nachrichten")
    for msg in messages:
        sender_kind = str(msg.get("sender_kind") or "user")
        sender = str(msg.get("sender_id") or "?")
        text = sanitize_text(str(msg.get("text") or ""), max_len=6000)
        if should_use_middle_view_for_message(msg | {"text": text}):
            text = compact_chat_message_text(text)
        line_col = _participant_color(game, sender_id=sender, sender_kind=sender_kind)
        if sender_kind == "system":
            prefix = "* "
        elif sender_kind == "ai":
            prefix = _participant_label(game, sender, fallback="AI") + ": "
        else:
            prefix = _participant_label(game, sender, fallback="Du" if active_ch_id == "ai:tutor" else sender[:8]) + ": "
        for row in _wrap_plain(prefix + text, max(8, width - 2)):
            message_lines.append("  " + _ansi_color(row, line_col))
    if bool(chat.get("ai_typing")) and active_ch_id == "ai:tutor":
        message_lines.append("  " + _chat_timeout_progress_text(game))

    footer_lines = ["  " + "-" * max(8, width - 4)]
    if chat_focus:
        if bool(game.get("artifact_chat_focus")):
            buf = str(game.get("artifact_chat_input") or "")
            cursor = int(game.get("artifact_chat_cursor") or len(buf))
        else:
            buf = str(chat.get("chat_input_buffer") or "")
            cursor = int(chat.get("chat_input_cursor") or len(buf))
        prompt_map = {"room": "#room>", "direct": "@>", "ai": "AI>", "notes": "notes>", "system": ">"}
        prompt = prompt_map.get(ch_type, ">")
        if ch_type == "ai":
            try:
                from client_surfaces.operator_tui.chat_state import get_active_session as _gas3
                _ps = _gas3(chat)
                if _ps:
                    _pname = str(_ps.get("name") or "AI")
                    prompt = f"{_pname}>"
            except Exception:
                pass
        # History indicator: show ▲N if history available (↑/↓ to navigate)
        history = [str(h) for h in (chat.get("chat_input_history") or []) if str(h).strip()]
        hist_note = f" \x1b[2m▲{len(history)}\x1b[0m" if history else ""
        visible = _inline_input_with_cursor(buf, cursor, max(1, width - len(prompt) - 3 - len(hist_note.replace("\x1b[2m","").replace("\x1b[0m",""))))
        footer_lines.append(f"  {prompt}{hist_note} {visible}")
    else:
        footer_lines.append(
            f"  {display_for_action('chat_focus', 'Ctrl+E')} Eingabe  "
            f"{display_for_action('cycle_focus_or_channel', 'Ctrl+W')} Kanal"
        )
    if max_height is not None:
        available = max(0, int(max_height) - len(header_lines) - len(footer_lines))
        min_messages = 2 if bool(game.get("chat_panel_open")) else 1
        if available < min_messages and len(header_lines) > 2:
            trim = min(len(header_lines) - 2, min_messages - available)
            header_lines = header_lines[:-trim]
            available = max(0, int(max_height) - len(header_lines) - len(footer_lines))
        message_lines = message_lines[-available:] if available else []
    lines = [*header_lines, *message_lines, *footer_lines]
    if max_height is not None and bottom_align and len(lines) < int(max_height):
        lines = [""] * (int(max_height) - len(lines)) + lines
    return [_clip(line, width) for line in lines]



def _snake_ai_chat_detail_lines(state: OperatorState, width: int, *, height: int) -> list[str]:
    chat_height = max(8, min(height, int(height * 0.58)))
    ai_height = max(0, height - chat_height)
    ai_lines = _snake_ai_detail_lines(state, width)[:ai_height]
    if len(ai_lines) < ai_height:
        ai_lines.extend([""] * (ai_height - len(ai_lines)))
    chat_lines = _chat_detail_lines(state, width, max_height=chat_height, bottom_align=True)
    return [_clip(line, width) for line in [*ai_lines, *chat_lines]][:height]



def _snake_ai_detail_lines(state: OperatorState, width: int) -> list[str]:
    game = state.header_logo_game if isinstance(state.header_logo_game, dict) else {}
    depth = str(game.get("tutor_depth_mode") or "overview")
    score = int(game.get("score") or 0)
    scores_raw = game.get("_scores_cache")
    high = int(scores_raw.get("high") or 0) if isinstance(scores_raw, dict) else 0
    speed_level = int(game.get("speed_level") or 3)
    tutorial_enabled = bool(game.get("tutorial_mode"))
    paused = bool(game.get("paused"))
    llm_status = game.get("llm_status") if isinstance(game.get("llm_status"), dict) else {}
    model = str(llm_status.get("model") or "LM")[:12] if llm_status.get("reachable") else "lokal"
    lines = [_pane_title("AI-SNAKE", state.focus == FocusPane.DETAIL)]
    lines.append(f"  tutor-ai {depth}")
    lines.append(f"  score:{score} best:{max(score, high)} spd:{speed_level}/5")
    lines.append(f"  llm:{model}" + (" paused" if paused else ""))
    question = str(game.get("tutor_ask_question") or "")
    if question and not bool(game.get("tutor_ask_answered")):
        lines.append(_clip(f"  ask: loading {question}", width))
    elif question:
        lines.append(_clip(f"  ask: ready {question}", width))
    lines.append("  " + "-" * max(8, width - 4))
    lines.append(f"  {display_for_action('toggle_tutorial_ai', 'Ctrl+U')} Heuristik:{'AN' if tutorial_enabled else 'AUS'}")
    lines.append(f"  {display_for_action('chat_focus', 'Ctrl+E')} Chat-Fokus")
    lines.append(f"  {display_for_action('copy_ai_status', 'Ctrl+I')} Status kopieren")
    lines.append(f"  {display_for_action('toggle_mouse_follow', 'Ctrl+O')} MouseFollow")
    lines.append("  " + "-" * max(8, width - 4))
    runtime_status = str(game.get("ai_snake_runtime_status") or "idle")
    ai_mode = str(game.get("ai_snake_mode") or "lurking_follow")
    lines.append(_clip(f"  mode={ai_mode}", width))
    lines.append(_clip(f"  runtime={runtime_status}", width))
    monitor_log = game.get("ai_snake_monitor_log")
    rows = [dict(item) for item in monitor_log if isinstance(item, dict)] if isinstance(monitor_log, list) else []
    if rows:
        for item in rows[-3:]:
            label = str(item.get("label") or item.get("event") or "event")
            lines.append(_clip(f"  {label}", width))
    return [_clip(line, width) for line in lines]



def _planning_track_content_lines(payload: dict, *, width: int, compact: bool) -> list[str]:
    goal_id = str(payload.get("goal_id") or "unknown")
    status = str(payload.get("planning_status") or "idle")
    lifecycle = [str(item) for item in list(payload.get("planning_lifecycle") or []) if str(item).strip()]
    selected_track = dict(payload.get("selected_track") or {})
    selected_output = str(payload.get("selected_output_id") or "")
    active_output = str(payload.get("active_output_id") or "")
    filters = dict(payload.get("task_filters") or {})
    warnings = list(selected_track.get("quality_gate_warnings") or [])
    rows = list(payload.get("track_rows") or [])

    lines = [
        f"  Planning Track: {goal_id}",
        f"  Status: {status}  lifecycle={' -> '.join(lifecycle) if lifecycle else '-'}",
        f"  Selected output: {selected_output or '-'}  active={active_output or '-'}",
        f"  Filters: {', '.join([f'{k}={v}' for k, v in filters.items()]) if filters else 'none'}",
    ]
    if compact:
        lines.append("  --- compact view ---")
    if not selected_track:
        if rows:
            lines.append("  planning outputs available, but selected track payload missing")
        else:
            lines.append("  no planning track outputs")
        return lines

    owner = str(selected_track.get("owner") or "-")
    track = str(selected_track.get("track") or "-")
    goal = str(selected_track.get("goal") or goal_id)
    progress = dict(selected_track.get("progress_summary") or {})
    summary = dict(selected_track.get("tasks_status_summary") or {})
    weighted = dict(selected_track.get("weighted_progress_summary") or {})
    metadata = dict(selected_track.get("derived_summary_metadata") or {})
    type_summary = dict(selected_track.get("tasks_type_summary") or {})
    provenance = dict(selected_track.get("provenance") or {})
    mapping = dict(selected_track.get("task_mapping") or {})
    source_refs = [str(item) for item in list(selected_track.get("source_references") or []) if str(item).strip()]
    context_refs = [str(item) for item in list(selected_track.get("context_references") or []) if str(item).strip()]
    raw_summary_status = str(selected_track.get("summary_recalculation_status") or "not_needed")
    summary_status = (
        "repaired"
        if raw_summary_status == "repaired"
        else ("invalid" if raw_summary_status == "failed" else "fresh")
    )
    repaired_fields = [str(item) for item in list(selected_track.get("repaired_fields") or []) if str(item).strip()]
    lines.append(f"  Header: owner={owner} track={track} goal={goal}")
    lines.append(
        _clip(
            "  Summary: "
            f"state={progress.get('state') or '-'} done={summary.get('by_status', {}).get('done', 0)} "
            f"todo={summary.get('by_status', {}).get('todo', 0)} total={summary.get('total', 0)}",
            width,
        )
    )
    lines.append(
        _clip(
            "  Progress: "
            f"count_based={progress.get('count_based_percent', '-')}% "
            f"weighted={progress.get('weighted_percent', '-')}% "
            f"blocked_count={summary.get('by_status', {}).get('blocked', 0)} "
            f"blocked_weight={weighted.get('blocked_weight', 0)}",
            width,
        )
    )
    lines.append(
        _clip(
            "  Critical path: "
            f"done={summary.get('critical_path', {}).get('done', 0)}/"
            f"{summary.get('critical_path', {}).get('total', 0)} "
            f"remaining={summary.get('critical_path', {}).get('remaining', 0)}",
            width,
        )
    )
    lines.append(
        _clip(
            "  Derived summary: "
            f"status={summary_status} "
            f"source_hash={str(metadata.get('source_hash') or '-')[:12]} "
            f"repaired_fields={','.join(repaired_fields) if repaired_fields else '-'}",
            width,
        )
    )

    milestones = [dict(item) for item in list(selected_track.get("milestones") or []) if isinstance(item, dict)]
    lines.append("  [Milestones]")
    if not milestones:
        lines.append("    - none")
    for milestone in milestones[:8]:
        lines.append(
            _clip(
                f"    {milestone.get('id')} [{milestone.get('status')}] "
                f"{milestone.get('title')} tasks={','.join([str(x) for x in list(milestone.get('task_ids') or [])])}",
                width,
            )
        )

    tasks = [dict(item) for item in list(selected_track.get("tasks_filtered") or []) if isinstance(item, dict)]
    lines.append("  [Tasks]")
    if not tasks:
        lines.append("    - none (filtered)")
    for task in tasks[:16]:
        lines.append(
            _clip(
                f"    {task.get('id')} [{task.get('status')}] {task.get('priority')}/{task.get('risk')} "
                f"type={task.get('type') or '-'} {task.get('title')}",
                width,
            )
        )

    critical = [str(item) for item in list(selected_track.get("critical_path_tasks") or []) if str(item).strip()]
    lines.append(f"  Critical path tasks: {', '.join(critical) if critical else 'none'}")
    by_priority = dict(summary.get("by_priority") or {})
    by_risk = dict(summary.get("by_risk") or {})
    if by_priority:
        lines.append(_clip(f"  Priority breakdown: {', '.join([f'{k}={v}' for k, v in by_priority.items()])}", width))
    if by_risk:
        lines.append(_clip(f"  Risk breakdown: {', '.join([f'{k}={v}' for k, v in by_risk.items()])}", width))
    by_type = dict(type_summary.get("by_type") or {})
    if by_type:
        lines.append("  [Type progress]")
        for key in sorted(by_type.keys())[:8]:
            bucket = dict(by_type.get(key) or {})
            lines.append(
                _clip(
                    f"    {key}: total={bucket.get('total', 0)} done={bucket.get('done', 0)} "
                    f"partial={bucket.get('partial', 0)} blocked={bucket.get('blocked', 0)} "
                    f"progress={bucket.get('progress_percent', 0)}%",
                    width,
                )
            )
    if provenance:
        lines.append(
            _clip(
                f"  Provenance: {provenance.get('provenance_id') or '-'} model={dict(provenance.get('model_ref') or {}).get('model_id') or '-'}",
                width,
            )
        )
    lines.append(_clip(f"  Plan mapping: {len(mapping)} task refs", width))
    lines.append(_clip(f"  Sources: {len(source_refs)} refs  Context: {len(context_refs)} refs", width))

    if warnings:
        lines.append("  [Quality warnings]")
        for warning in warnings[:5]:
            if not isinstance(warning, dict):
                continue
            lines.append(_clip(f"    {warning.get('path')}: {warning.get('reason_code')}", width))

    status_issues = [dict(item) for item in list(payload.get("status_issues") or []) if isinstance(item, dict)]
    if status_issues:
        lines.append("  [Validation issues]")
        for issue in status_issues[:5]:
            lines.append(_clip(f"    {issue.get('path')}: {issue.get('reason_code')}", width))

    diff = dict(payload.get("plan_diff") or {})
    if diff:
        lines.append("  [Plan diff]")
        lines.append(
            f"    {diff.get('left_output_id')} -> {diff.get('right_output_id')} "
            f"new={len(list(diff.get('new_tasks') or []))} "
            f"changed={len(list(diff.get('changed_tasks') or []))} "
            f"removed={len(list(diff.get('removed_tasks') or []))}"
        )
    return lines



def _helpcenter_content_lines(payload: dict, *, width: int, compact: bool) -> list[str]:
    rows = [dict(item) for item in list(payload.get("reports") or []) if isinstance(item, dict)]
    selected_id = str(payload.get("selected_analysis_id") or "")
    selected_report = dict(payload.get("selected_report") or {})
    selected_analysis = dict(payload.get("selected_analysis") or {})
    last_ingest = dict(payload.get("last_ingest") or {})
    lines = [
        "  Helpcenter",
        f"  Reports: {len(rows)} selected={selected_id or '-'}",
    ]
    if last_ingest:
        lines.append(
            _clip(
                f"  Last ingest: repo={last_ingest.get('repo') or '-'} found={last_ingest.get('found', 0)} "
                f"written={last_ingest.get('written', 0)} dry_run={bool(last_ingest.get('dry_run'))}",
                width,
            )
        )
    if not rows:
        lines.append("  no helpcenter reports")
        return lines
    lines.append("  [Reports]")
    preview_rows = rows[:12] if not compact else rows[:6]
    for row in preview_rows:
        marker = "*" if str(row.get("analysis_id") or "") == selected_id else "-"
        lines.append(
            _clip(
                f"  {marker} {row.get('analysis_id')} [{row.get('status')}] "
                f"{row.get('severity')} {row.get('source_kind')} at {row.get('created_at')}",
                width,
            )
        )
    if not selected_report:
        return lines
    lines.append("  [Detail]")
    lines.append(
        _clip(
            f"  Source: kind={selected_report.get('source_kind') or '-'} "
            f"ref={selected_analysis.get('source_refs', ['-'])[0] if isinstance(selected_analysis.get('source_refs'), list) and selected_analysis.get('source_refs') else '-'}",
            width,
        )
    )
    lines.append(_clip(f"  Summary: {selected_analysis.get('failure_summary') or '-'}", width))
    lines.append(
        _clip(
            f"  no_auto_fix={bool(selected_analysis.get('no_auto_fix'))} "
            f"md={selected_report.get('report_ref') or '-'} json={selected_report.get('json_ref') or '-'}",
            width,
        )
    )
    causes = [str(item) for item in list(selected_analysis.get("likely_causes") or []) if str(item).strip()]
    if causes:
        lines.append("  Likely causes:")
        for item in causes[:4]:
            lines.append(_clip(f"    - {item}", width))
    next_steps = [str(item) for item in list(selected_analysis.get("next_steps") or []) if str(item).strip()]
    if next_steps:
        lines.append("  Next steps:")
        for item in next_steps[:4]:
            lines.append(_clip(f"    - {item}", width))
    followup = str(payload.get("followup_suggestion") or "").strip()
    lines.append(_clip(f"  Follow-up suggestion: {followup or '-'}", width))
    return lines



def _mail_content_lines(payload: dict, *, width: int, compact: bool) -> list[str]:
    accounts = [dict(item) for item in list(payload.get("accounts") or []) if isinstance(item, dict)]
    selected_account_id = str(payload.get("selected_account_id") or "")
    mailboxes = [str(item) for item in list(payload.get("mailboxes") or []) if str(item).strip()]
    selected_mailbox = str(payload.get("selected_mailbox") or "")
    filters = dict(payload.get("filters") or {})
    rows = [dict(item) for item in list(payload.get("messages") or []) if isinstance(item, dict)]
    total_messages = int(payload.get("total_messages") or 0)
    selected_key = str(payload.get("selected_message_key") or "")
    detail = dict(payload.get("selected_detail") or {})
    last_search_query = str(payload.get("last_search_query") or "")
    search_refs = [str(item) for item in list(payload.get("search_result_refs") or []) if str(item).strip()]
    notes = [dict(item) for item in list(payload.get("notes") or []) if isinstance(item, dict)]
    linked_goal_refs = [str(item) for item in list(payload.get("linked_goal_refs") or []) if str(item).strip()]
    current_artifact_ref = str(payload.get("current_artifact_ref") or "")
    lines = [
        "  Mail",
        f"  Accounts: {len(accounts)} selected={selected_account_id or '-'} mailbox={selected_mailbox or '-'}",
        f"  Mailboxes: {', '.join(mailboxes) if mailboxes else '-'}",
        f"  Filters: {', '.join([f'{k}={v}' for k, v in filters.items()]) if filters else 'none'}",
        f"  Messages: showing={len(rows)} total={total_messages} offset={int(payload.get('list_offset') or 0)}",
        f"  Search: query={last_search_query or '-'} refs={len(search_refs)}",
        f"  Notes={len(notes)} linked-goals={len(linked_goal_refs)} artifacts={int(payload.get('artifact_count') or 0)}",
    ]
    if accounts:
        lines.append("  [Accounts]")
        for row in accounts[:6]:
            marker = "*" if str(row.get("account_id") or "") == selected_account_id else "-"
            lines.append(
                _clip(
                    f"  {marker} {row.get('display_name') or row.get('account_id')} "
                    f"state={row.get('state')} enabled={bool(row.get('enabled'))}",
                    width,
                )
            )
    if not rows:
        lines.append("  no mail messages")
        return lines
    lines.append("  [Mailbox list]")
    preview = rows[:8] if compact else rows[:14]
    for row in preview:
        ref = dict(row.get("message_ref") or {})
        header = dict(row.get("header_meta") or {})
        marker = "*" if str(ref.get("message_id") or "") == selected_key else "-"
        flags = []
        if bool(header.get("unread")):
            flags.append("unread")
        if bool(header.get("starred")):
            flags.append("starred")
        flags_text = ",".join(flags) or "-"
        lines.append(
            _clip(
                f"  {marker} uid={ref.get('uid')} date={ref.get('date')} from={ref.get('from')} "
                f"subject={header.get('subject') or '-'} flags={flags_text} "
                f"policy={row.get('body_scope') or 'metadata_only'} thread={row.get('thread_count') or 1}",
                width,
            )
        )
    if not detail:
        return lines
    lines.append("  [Detail]")
    detail_ref = dict(detail.get("message_ref") or {})
    detail_header = dict(detail.get("header_meta") or {})
    lines.append(_clip(f"  Message: id={detail_ref.get('message_id') or '-'} uid={detail_ref.get('uid') or '-'}", width))
    lines.append(_clip(f"  Subject: {detail_header.get('subject') or '-'}", width))
    lines.append(
        _clip(
            f"  Body loaded={bool(detail.get('body_loaded'))} "
            f"scope={detail.get('body_scope') or 'metadata_only'} "
            f"redaction={detail.get('redaction_status') or '-'}",
            width,
        )
    )
    lines.append(_clip(f"  Artifact: {current_artifact_ref or '-'}", width))
    body_text = str(detail.get("body_text") or "").strip()
    lines.append(_clip(f"  Body preview: {body_text[:200] if body_text else '(not loaded)'}", width))
    attachments = [dict(item) for item in list(detail.get("attachments") or []) if isinstance(item, dict)]
    lines.append(f"  Attachments: {len(attachments)}")
    for attachment in attachments[:4]:
        lines.append(
            _clip(
                f"    - {attachment.get('filename') or '-'} "
                f"type={attachment.get('content_type') or '-'} "
                f"size={attachment.get('size') or 0} "
                f"danger={bool(attachment.get('danger'))}",
                width,
            )
        )
    downloaded = dict(detail.get("attachment_downloaded") or {})
    if downloaded:
        lines.append(
            _clip(
                f"  Last download: {downloaded.get('filename') or '-'} "
                f"sha256={str(downloaded.get('sha256') or '')[:16]}... "
                f"danger={bool(downloaded.get('dangerous'))}",
                width,
            )
        )
    return lines



def _goal_artifacts_content_lines(payload: dict, *, width: int, compact: bool) -> list[str]:
    def _safe(value: object) -> str:
        text = str(value or "")
        text = _ANSI_STRIP.sub("", text)
        return text.replace("\r", " ").replace("\n", " ")

    goal_id = str(payload.get("goal_id") or "unknown")
    filters = dict(payload.get("filters") or {})
    filtered = filter_goal_artifact_view(
        source_grants=list(payload.get("source_grants") or []),
        source_usages=list(payload.get("source_usages") or []),
        output_artifacts=list(payload.get("output_artifacts") or []),
        filters=filters,
    )
    grants = list(filtered.get("source_grants") or [])
    usages = list(filtered.get("source_usages") or [])
    outputs = list(filtered.get("output_artifacts") or [])
    usage_grant_ids = {_safe(item.get("grant_id") or "") for item in usages}
    lines = [
        f"  Goal Artifacts: {goal_id}",
        f"  Filters: {', '.join([f'{k}={v}' for k, v in filters.items()]) if filters else 'none'}",
    ]
    if compact:
        lines.append("  --- compact view ---")
        for grant in grants[:5]:
            grant_id = str(grant.get("grant_id") or "?")
            marker = "✓" if grant_id in usage_grant_ids else "~"
            lines.append(
                _clip(
                    f"  {marker} grant {grant_id} source={_safe(grant.get('artifact_ref') or '-')}",
                    width,
                )
            )
        for usage in usages[:5]:
            lines.append(_clip(f"  • usage {_safe(usage.get('usage_id'))} -> {_safe(usage.get('artifact_ref'))}", width))
        for output in outputs[:6]:
            provenance_note = " provenance-missing" if not _safe(output.get("provenance_id")) else ""
            lines.append(
                _clip(
                    "  ◦ output "
                    f"{_safe(output.get('output_artifact_id'))} type={_safe(output.get('artifact_type'))} "
                    f"status={_safe(output.get('status'))}{provenance_note} "
                    f"exec={_safe(output.get('execution_summary') or '')}",
                    width,
                )
            )
        if not grants and not usages and not outputs:
            lines.append("  (empty goal artifact graph)")
        return lines


    lines.append("  [Freigegeben]")
    if not grants:
        lines.append("    - none")
    for grant in grants[:8]:
        grant_id = _safe(grant.get("grant_id") or "?")
        used = grant_id in usage_grant_ids
        marker = "used" if used else "granted-not-used"
        lines.append(
            _clip(
                f"    {grant_id} [{marker}] sensitivity={_safe(grant.get('sensitivity'))} "
                f"boundary={_safe(grant.get('data_boundary'))} ref={_safe(grant.get('artifact_ref'))}",
                width,
            )
        )

    lines.append("  [Genutzt]")
    if not usages:
        lines.append("    - none")
    for usage in usages[:8]:
        lines.append(
            _clip(
                f"    {_safe(usage.get('usage_id'))} grant={_safe(usage.get('grant_id'))} "
                f"task={_safe(usage.get('task_id'))} worker={_safe(usage.get('worker_id'))} "
                f"ref={_safe(usage.get('artifact_ref'))}",
                width,
            )
        )

    lines.append("  [Erzeugt]")
    if not outputs:
        lines.append("    - none")
    for output in outputs[:10]:
        provenance_note = "provenance missing" if not _safe(output.get("provenance_id")) else f"prov={_safe(output.get('provenance_id'))}"
        lines.append(
            _clip(
                f"    {_safe(output.get('output_artifact_id'))} type={_safe(output.get('artifact_type'))} "
                f"status={_safe(output.get('status'))} task={_safe(output.get('task_id'))} "
                f"worker={_safe(output.get('worker_id'))} {provenance_note} created_at={_safe(output.get('created_at'))}",
                width,
            )
        )
        summary = _safe(output.get("execution_summary"))
        if summary:
            lines.append(_clip(f"      exec: {summary}", width))
    return lines



def _diff3_content_lines(payload: dict, *, width: int) -> list[str]:
    rows = list(payload.get("panel_summaries") or [])
    active_panel = str(payload.get("active_panel") or "A")
    sync = bool(payload.get("sync_scroll"))
    lines = [
        f"  DIFF3: active panel={active_panel} sync={'on' if sync else 'off'}",
    ]
    ai_state = dict(payload.get("ai_panel_state") or {})
    if ai_state:
        lines.append(
            _clip(
                f"  AI: mode={ai_state.get('mode')} status={ai_state.get('status')} "
                f"prompt={ai_state.get('prompt_template_ref')} last={ai_state.get('last_response_ref') or '-'}",
                width,
            )
        )
        findings = list(payload.get("raw_state", {}).get("extensions", {}).get("ai_last_findings") or [])
        if findings:
            lines.append(_clip(f"  AI findings: {findings[0]}", width))
    if not rows:
        lines.append("  (empty diff3 session)")
        return lines

    if width < 58:
        lines.append("  --- tabbed mode (<120 terminal width) ---")
        active = next((row for row in rows if str(row.get("panel_id") or "") == active_panel), rows[0])
        filters = dict(active.get("filters") or {})
        lines.append(
            _clip(
                f"  [{active.get('panel_id')}] {active.get('source_label')} "
                f"mode={active.get('render_mode')} status={active.get('status')}",
                width,
            )
        )
        if filters:
            lines.append(_clip(f"  filters: {', '.join(f'{k}={v}' for k, v in filters.items())}", width))
        stats = dict(active.get("stats") or {})
        if stats:
            lines.append(
                _clip(
                    f"  stats: files={stats.get('files',0)} hunks={stats.get('hunks',0)} truncated={stats.get('truncated',False)}",
                    width,
                )
            )
        return lines

    if width >= 84:
        cols = max(18, (width - 4) // 3)

        def _cell(text: str) -> str:
            return _clip(text, cols).ljust(cols)

        headers: list[str] = []
        details: list[str] = []
        filters_line: list[str] = []
        for row in rows[:3]:
            headers.append(_cell(f"[{row.get('panel_id')}] {row.get('source_label')}"))
            details.append(_cell(f"{row.get('render_mode')} | {row.get('status')}"))
            filters = dict(row.get("filters") or {})
            if filters:
                filters_line.append(_cell(",".join(f"{k}={v}" for k, v in filters.items())))
            else:
                filters_line.append(_cell("filters:none"))
        lines.append("  " + " | ".join(headers))
        lines.append("  " + " | ".join(details))
        lines.append("  " + " | ".join(filters_line))
        return lines

    lines.append("  --- compact diff3 view ---")
    for row in rows:
        filters = dict(row.get("filters") or {})
        filter_label = ",".join(f"{k}={v}" for k, v in filters.items()) if filters else "none"
        lines.append(
            _clip(
                f"  [{row.get('panel_id')}] {row.get('source_label')} "
                f"mode={row.get('render_mode')} status={row.get('status')} filters={filter_label}",
                width,
            )
        )
    return lines



def _help_overlay(state: OperatorState, width: int) -> list[str]:
    lines = [_rule(width), "HELP"]
    lines.extend(_binding_lines(state, width))
    return lines



def _binding_lines(state: OperatorState, width: int) -> list[str]:
    lines = []
    for binding in bindings_for_mode(state.mode):
        lines.append(shorten(f"{binding.key:<7} {binding.action:<18} {binding.description}", width=width, placeholder="..."))
    return lines



def _cell(lines: list[str], index: int, width: int) -> str:
    value = lines[index] if index < len(lines) else ""
    clipped = _clip(value, width)
    visible_len = len(_ANSI_STRIP.sub("", clipped))
    return clipped + " " * max(0, width - visible_len)



def _tutorial_propose_dock_lines(state: OperatorState, width: int) -> list[str]:
    game = state.header_logo_game if isinstance(state.header_logo_game, dict) else {}
    history = game.get("tutorial_propose_history") if isinstance(game.get("tutorial_propose_history"), list) else []
    chat = game.get("artifact_chat_state") if isinstance(game.get("artifact_chat_state"), dict) else {}
    show_tutorial_flow = (bool(game.get("tutorial_mode")) or bool(history) or bool(chat.get("active_target"))) and not bool(game.get("chat_panel_open"))
    if not show_tutorial_flow:
        return []

    inner_width = max(24, int(width) - 4)
    top = f"+-{'-' * inner_width}-+"
    title = f"| {_clip('Tutorial-AI propose flow', inner_width).ljust(inner_width)} |"

    rows: list[str] = []
    marker_bits: list[str] = []
    if bool(game.get("mouse_follow_enabled")) and bool(game.get("mouse_state")):
        marker_bits.append("mouse-follow")
    confidence = str(game.get("artifact_intent_confidence") or "none")
    if confidence in {"likely", "confirmed"}:
        marker_bits.append("artifact-intent")
    if str(game.get("tutorial_ai_target_mode") or "") in {"fast_target", "explain_target"}:
        marker_bits.append("ai-fast-target")
    if isinstance(chat, dict) and chat.get("active_target"):
        marker_bits.append("artifact-chat-active")
    if marker_bits:
        marker_line = " ".join(f"[{bit}]" for bit in marker_bits)
        rows.append(f"| {shorten(marker_line, width=inner_width, placeholder='...').ljust(inner_width)} |")
    if isinstance(chat, dict) and isinstance(chat.get("active_target"), dict):
        active = chat.get("active_target") or {}
        label = str(active.get("label") or active.get("path") or active.get("id") or "(none)")
        rows.append(f"| {shorten(f'context: {label}', width=inner_width, placeholder='...').ljust(inner_width)} |")
    if history:
        for entry in history[-2:]:
            if not isinstance(entry, dict):
                continue
            source = str(entry.get("source") or "unknown")
            target = str(entry.get("target") or "content")
            text = str(entry.get("text") or "").strip()
            if not text:
                continue
            label = f"[{source}->{target}] {text}"
            rows.append(f"| {shorten(label, width=inner_width, placeholder='...').ljust(inner_width)} |")
    if not rows:
        rows.append(f"| {_clip('waiting for first propose...', inner_width).ljust(inner_width)} |")

    return [top, title, *rows, top]



