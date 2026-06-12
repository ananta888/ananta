"""Internal sub-module of the Operator TUI renderer.

Extracted from _renderer_content.py to keep the main module small.
This module owns: Detail pane renderers (standard detail, context
shortcuts, chat detail, snake AI detail).

Public re-exports: the parent _renderer_content module re-exports every
function so the public chain (renderer -> _renderer_content -> sub-module)
keeps working transparently.
"""

from __future__ import annotations

from client_surfaces.operator_tui.chat_long_message import (
    compact_chat_message_text,
    should_use_middle_view_for_message,
)
from client_surfaces.operator_tui.keybindings_config import display_for_action, shortcut_tokens_for_area
from client_surfaces.operator_tui.models import FocusPane, OperatorState
from client_surfaces.operator_tui.read_models import build_inspection_detail
from client_surfaces.operator_tui.sections import get_section
from client_surfaces.operator_tui._renderer_utils import (
    _chat_channel_label,
    _clip,
    _inline_input_with_cursor,
    _pane_title,
)
from client_surfaces.operator_tui._renderer_chat_ai import (
    _chat_timeout_progress_text,
    _participant_color,
    _participant_label,
    _plain_channel_selector,
    _wrap_plain,
)
from client_surfaces.operator_tui._renderer_snake_overlay import _ansi_color
from client_surfaces.operator_tui._renderer_layout import _runtime_detail_lines


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



