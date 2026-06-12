"""Internal sub-module of the Operator TUI renderer.

Extracted from the monolithic client_surfaces.operator_tui/renderer.py to
keep the main module small. This module owns: Content pane renderers: dashboard, browser, system, terminal, templates, audit, detail, visual viewport. Artifact sub-mode renderers (planning, helpcenter, mail, goal-artifacts, diff3) live in _renderer_content_artifact.py.

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
from client_surfaces.operator_tui import _renderer_content_artifact as _rc_art_x
from client_surfaces.operator_tui import _renderer_content_templates as _rc_tpl_x
from client_surfaces.operator_tui._renderer_content_templates import (  # noqa: F401
    _TPL_THEME,
    _TPL_VAR_NAME_RE,
    _TPL_VAR_COLOR,
    _TPL_WARN_COLOR,
    _TPL_ERR_COLOR,
    _TPL_RESET,
)
from client_surfaces.operator_tui import _renderer_content_detail as _rc_det_x



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
    return _rc_tpl_x._templates_content_lines(payload, state, width)

def _audit_viewer_content_lines(state: OperatorState, width: int, *, viewport_height: int | None = None) -> list[str]:
    return _rc_tpl_x._audit_viewer_content_lines(state, width, viewport_height=viewport_height)

def _highlight_template_line(line: str) -> tuple[str, int]:
    return _rc_tpl_x._highlight_template_line(line)

def _templates_editor_content_lines(state: OperatorState, width: int, *, viewport_height: int | None = None) -> list[str]:
    return _rc_tpl_x._templates_editor_content_lines(state, width, viewport_height=viewport_height)


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
    return _rc_det_x._detail_lines(state, width, height=height)

def _standard_detail_lines(state: OperatorState, width: int) -> list[str]:
    return _rc_det_x._standard_detail_lines(state, width)

def _context_shortcut_lines(state: OperatorState, width: int) -> list[str]:
    return _rc_det_x._context_shortcut_lines(state, width)

def _chat_detail_lines(
    state: OperatorState,
    width: int,
    *,
    max_height: int | None = None,
    bottom_align: bool = False,
) -> list[str]:
    return _rc_det_x._chat_detail_lines(state, width, max_height=max_height, bottom_align=bottom_align)

def _snake_ai_chat_detail_lines(state: OperatorState, width: int, *, height: int) -> list[str]:
    return _rc_det_x._snake_ai_chat_detail_lines(state, width, height=height)

def _snake_ai_detail_lines(state: OperatorState, width: int) -> list[str]:
    return _rc_det_x._snake_ai_detail_lines(state, width)


def _planning_track_content_lines(payload: dict, *, width: int, compact: bool) -> list[str]:
    return _rc_art_x._planning_track_content_lines(payload, width=width, compact=compact)

def _helpcenter_content_lines(payload: dict, *, width: int, compact: bool) -> list[str]:
    return _rc_art_x._helpcenter_content_lines(payload, width=width, compact=compact)

def _mail_content_lines(payload: dict, *, width: int, compact: bool) -> list[str]:
    return _rc_art_x._mail_content_lines(payload, width=width, compact=compact)

def _goal_artifacts_content_lines(payload: dict, *, width: int, compact: bool) -> list[str]:
    return _rc_art_x._goal_artifacts_content_lines(payload, width=width, compact=compact)

def _diff3_content_lines(payload: dict, *, width: int) -> list[str]:
    return _rc_art_x._diff3_content_lines(payload, width=width)

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



