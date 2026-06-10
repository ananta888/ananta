from __future__ import annotations

import json
import shutil
from typing import TYPE_CHECKING, Any

from client_surfaces.operator_tui.audit_cleanup import run_audit_cleanup_action
from client_surfaces.operator_tui.chat_long_message import (
    configure_middle_view_for_history_entry,
    long_message_history_rows,
)
from client_surfaces.operator_tui.models import FocusPane, OperatorMode
from client_surfaces.operator_tui.sections import SECTIONS

if TYPE_CHECKING:
    from client_surfaces.operator_tui.interactive import InteractiveOperatorTui


def audit_viewer_active(tui: InteractiveOperatorTui) -> bool:
    game = tui.state.header_logo_game if isinstance(tui.state.header_logo_game, dict) else {}
    viewer = dict(game.get("audit_viewer") or {})
    return bool(viewer.get("active")) and tui.state.section_id == "audit"


def audit_cleanup_confirm_mode_active(tui: InteractiveOperatorTui) -> bool:
    if not tui._audit_viewer_active():
        return False
    game = tui.state.header_logo_game if isinstance(tui.state.header_logo_game, dict) else {}
    viewer = dict(game.get("audit_viewer") or {})
    return str(viewer.get("mode") or "") == "confirm_cleanup"


def audit_cleanup_result_mode_active(tui: InteractiveOperatorTui) -> bool:
    if not tui._audit_viewer_active():
        return False
    game = tui.state.header_logo_game if isinstance(tui.state.header_logo_game, dict) else {}
    viewer = dict(game.get("audit_viewer") or {})
    return str(viewer.get("mode") or "") == "cleanup_result"


def audit_cleanup_set_choice(tui: InteractiveOperatorTui, choice: str) -> None:
    game = dict(tui.state.header_logo_game or {})
    viewer = dict(game.get("audit_viewer") or {})
    if str(viewer.get("mode") or "") != "confirm_cleanup":
        return
    normalized = "delete" if str(choice).strip().lower() == "delete" else "cancel"
    viewer["confirm_choice"] = normalized
    game["audit_viewer"] = viewer
    tui._set_state(
        tui.state.with_updates(
            header_logo_game=game,
            status_message=(
                "cleanup auswahl: Loeschen" if normalized == "delete" else "cleanup auswahl: Abbrechen"
            ),
        )
    )


def audit_cleanup_close_viewer(tui: InteractiveOperatorTui, *, status_message: str) -> None:
    game = dict(tui.state.header_logo_game or {})
    game["audit_viewer"] = {"active": False}
    tui._set_state(
        tui.state.with_updates(
            header_logo_game=game,
            mode=OperatorMode.NORMAL,
            focus=FocusPane.CONTENT,
            status_message=status_message,
        )
    )


def audit_cleanup_show_result(tui: InteractiveOperatorTui, *, title: str, summary: str) -> None:
    game = dict(tui.state.header_logo_game or {})
    game["audit_viewer"] = {
        "active": True,
        "mode": "cleanup_result",
        "title": title,
        "group": "Data Cleanup",
        "text": summary,
        "view_line_offset": 0,
        "view_col_offset": 0,
    }
    tui._set_state(
        tui.state.with_updates(
            header_logo_game=game,
            mode=OperatorMode.NORMAL,
            focus=FocusPane.CONTENT,
            status_message=summary,
        )
    )


def audit_cleanup_button_choice_from_click(
    tui: InteractiveOperatorTui, *, x: int, y: int, width: int, height: int
) -> str | None:
    if not tui._audit_cleanup_confirm_mode_active():
        return None
    game = dict(tui.state.header_logo_game or {})
    viewer = dict(game.get("audit_viewer") or {})
    text = str(viewer.get("text") or "")
    text_lines = text.splitlines() or [""]
    body_start = 10 if len(tui.state.open_tabs) >= 2 else 9
    body_y1 = body_start
    body_height = max(3, int(height) - 5 - body_start)
    y_rel = int(y) - body_y1
    if y_rel < 0:
        return None
    pane_title_rows = 1
    viewer_header_rows = 3
    visible_rows = max(1, body_height - pane_title_rows - viewer_header_rows)
    view_line_offset = max(0, int(viewer.get("view_line_offset") or 0))
    max_line_offset = max(0, len(text_lines) - visible_rows)
    view_line_offset = min(view_line_offset, max_line_offset)
    end_line = min(len(text_lines), view_line_offset + visible_rows)
    button_row = pane_title_rows + viewer_header_rows + max(0, end_line - view_line_offset)
    if end_line < len(text_lines):
        button_row += 1
    button_row += 1
    if y_rel != button_row:
        return None
    left_width = 22
    detail_width = 34
    middle_width = max(12, int(width) - left_width - detail_width - 6)
    content_x1 = left_width + 2
    rel_x = int(x) - content_x1
    if rel_x < 0 or rel_x >= middle_width:
        return None
    button_line_mid = len("  [ Loeschen ]   [ Abbrechen ] ") // 2
    return "delete" if rel_x < button_line_mid else "cancel"


def audit_cleanup_handle_mouse_click(
    tui: InteractiveOperatorTui, *, x: int, y: int, width: int, height: int
) -> bool:
    choice = tui._audit_cleanup_button_choice_from_click(x=x, y=y, width=width, height=height)
    if choice is None:
        return False
    tui._audit_cleanup_set_choice(choice)
    return tui._confirm_audit_cleanup_action()


def selected_audit_entry(tui: InteractiveOperatorTui) -> tuple[dict[str, Any], dict[str, Any]] | None:
    if tui.state.section_id != "audit":
        return None
    payload = dict((tui.state.section_payloads or {}).get("audit") or {})
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        return None
    idx = max(0, min(len(items) - 1, int(tui.state.selected_index)))
    entry = items[idx]
    if not isinstance(entry, dict):
        return None
    return payload, entry


def audit_viewer_viewport_metrics(tui: InteractiveOperatorTui) -> tuple[int, int]:
    size = shutil.get_terminal_size((120, 32))
    width = max(72, int(size.columns))
    height = max(18, int(size.lines - 1))
    left_width = 22
    detail_width = 34
    middle_width = max(18, width - left_width - detail_width - 6)
    body_height = max(3, height - 5 - 8)
    pane_title_rows = 1
    viewer_header_rows = 3
    visible_rows = max(1, body_height - pane_title_rows - viewer_header_rows)
    visible_cols = max(8, middle_width - 8)
    return visible_rows, visible_cols


def audit_viewer_scroll_vertical(tui: InteractiveOperatorTui, delta_lines: int) -> None:
    game = dict(tui.state.header_logo_game or {})
    viewer = dict(game.get("audit_viewer") or {})
    if not bool(viewer.get("active")):
        return
    lines = str(viewer.get("text") or "").splitlines() or [""]
    visible_rows, _ = tui._audit_viewer_viewport_metrics()
    max_offset = max(0, len(lines) - visible_rows)
    current = max(0, int(viewer.get("view_line_offset") or 0))
    viewer["view_line_offset"] = max(0, min(max_offset, current + int(delta_lines)))
    game["audit_viewer"] = viewer
    tui._set_state(tui.state.with_updates(header_logo_game=game))


def audit_viewer_scroll_horizontal(tui: InteractiveOperatorTui, delta_cols: int) -> None:
    game = dict(tui.state.header_logo_game or {})
    viewer = dict(game.get("audit_viewer") or {})
    if not bool(viewer.get("active")):
        return
    lines = str(viewer.get("text") or "").splitlines() or [""]
    _, visible_cols = tui._audit_viewer_viewport_metrics()
    max_width = max((len(line) for line in lines), default=0)
    max_offset = max(0, max_width - visible_cols)
    current = max(0, int(viewer.get("view_col_offset") or 0))
    viewer["view_col_offset"] = max(0, min(max_offset, current + int(delta_cols)))
    game["audit_viewer"] = viewer
    tui._set_state(tui.state.with_updates(header_logo_game=game))


def open_audit_viewer_for_selected(tui: InteractiveOperatorTui) -> bool:
    game = dict(tui.state.header_logo_game or {})
    if tui.state.section_id == "templates" and tui.state.focus is FocusPane.CONTENT:
        return tui._open_template_editor_for_selected()
    if tui.state.focus is FocusPane.NAVIGATION:
        history_idx = (
            tui.state.selected_index
            - len(SECTIONS)
            - tui._template_nav_selectable_count()
            - tui._audit_nav_selectable_count()
        )
        rows = long_message_history_rows(game)
        if 0 <= history_idx < len(rows) and configure_middle_view_for_history_entry(game, rows[history_idx]):
            tui._set_state(
                tui.state.with_updates(
                    header_logo_game=game,
                    focus=FocusPane.CONTENT,
                    selected_index=0,
                    status_message="Chat-History: Originalausgabe",
                )
            )
            return True
    if bool(game.get("ai_snake_config_open")):
        if tui.state.focus is not FocusPane.CONTENT:
            tui._set_state(tui.state.with_updates(focus=FocusPane.CONTENT))
        if not tui._ai_snake_config_combo_active(game):
            tui._toggle_ai_snake_config_selected()
        return True
    selected = tui._selected_audit_entry()
    if selected is None:
        return False
    payload, entry = selected
    dataset_id = str(entry.get("dataset_id") or entry.get("id") or "")
    datasets = payload.get("datasets")
    raw = datasets.get(dataset_id) if isinstance(datasets, dict) else None
    raw_dict = dict(raw) if isinstance(raw, dict) else {}
    if dataset_id.startswith("llm.requests.chat_prompt.") and isinstance(raw_dict.get("final_prompt_redacted"), str):
        text = str(raw_dict.get("final_prompt_redacted") or "").strip() or "{}"
        game = dict(tui.state.header_logo_game or {})
        game["audit_viewer"] = {
            "active": True,
            "mode": "read_only",
            "dataset_id": dataset_id,
            "title": str(entry.get("title") or dataset_id or "dataset"),
            "group": str(entry.get("group") or ""),
            "text": text,
            "view_line_offset": 0,
            "view_col_offset": 0,
        }
        tui._set_state(
            tui.state.with_updates(
                mode=OperatorMode.NORMAL,
                focus=FocusPane.CONTENT,
                header_logo_game=game,
                status_message=f"audit viewer: {str(entry.get('title') or dataset_id)}",
            )
        )
        return True
    raw_kind = str(raw_dict.get("kind") or "")
    if raw_kind in {"cleanup_action", "cleanup_overview"}:
        details = [str(line) for line in list(raw_dict.get("details") or []) if str(line).strip()]
        if raw_kind == "cleanup_action":
            text_lines = [
                "Bitte Loeschung bestaetigen.",
                "",
                *details,
                "",
                "Diese Aktion kann nicht rueckgaengig gemacht werden.",
            ]
            mode = "confirm_cleanup"
        else:
            text_lines = details
            mode = "read_only"
        text = "\n".join(text_lines).strip() or "{}"
        game = dict(tui.state.header_logo_game or {})
        game["audit_viewer"] = {
            "active": True,
            "mode": mode,
            "dataset_id": dataset_id,
            "cleanup_action_id": str(raw_dict.get("cleanup_action_id") or ""),
            "confirm_choice": "cancel",
            "clear_runtime_chat": bool(raw_dict.get("clear_runtime_chat")),
            "clear_persisted_chat_history": bool(raw_dict.get("clear_persisted_chat_history")),
            "title": str(entry.get("title") or dataset_id or "dataset"),
            "group": str(entry.get("group") or ""),
            "text": text,
            "view_line_offset": 0,
            "view_col_offset": 0,
        }
        tui._set_state(
            tui.state.with_updates(
                mode=OperatorMode.NORMAL,
                focus=FocusPane.CONTENT,
                header_logo_game=game,
                status_message=(
                    f"cleanup bereit: {str(entry.get('title') or dataset_id)}"
                    if raw_kind == "cleanup_action"
                    else f"audit viewer: {str(entry.get('title') or dataset_id)}"
                ),
            )
        )
        return True
    text: str
    if isinstance(raw, str):
        text = raw
    elif raw is None:
        text = "{}"
    else:
        text = json.dumps(raw, indent=2, ensure_ascii=False)
    game = dict(tui.state.header_logo_game or {})
    game["audit_viewer"] = {
        "active": True,
        "dataset_id": dataset_id,
        "title": str(entry.get("title") or dataset_id or "dataset"),
        "group": str(entry.get("group") or ""),
        "text": text,
        "view_line_offset": 0,
        "view_col_offset": 0,
    }
    tui._set_state(
        tui.state.with_updates(
            mode=OperatorMode.NORMAL,
            focus=FocusPane.CONTENT,
            header_logo_game=game,
            status_message=f"audit viewer: {str(entry.get('title') or dataset_id)}",
        )
    )
    return True


def clear_runtime_chat_history(tui: InteractiveOperatorTui, game: dict[str, Any]) -> None:
    from client_surfaces.operator_tui.chat_state import get_chat_state, set_chat_state

    chat = get_chat_state(game)
    channels = chat.get("channels")
    if isinstance(channels, dict):
        for channel in channels.values():
            if not isinstance(channel, dict):
                continue
            channel["messages"] = []
            channel["unread"] = 0
    chat["chat_input_buffer"] = ""
    chat["chat_input_cursor"] = 0
    chat["chat_input_history_index"] = None
    chat["chat_input_saved_draft"] = ""
    chat["ai_typing"] = False
    set_chat_state(game, chat)

    game["artifact_chat_input"] = ""
    game["artifact_chat_cursor"] = 0
    game["artifact_chat_history"] = []
    game["artifact_chat_history_index"] = None
    game["artifact_chat_saved_draft"] = ""
    game["chat_long_message_history"] = []
    game["chat_long_message_markdown"] = ""
    game["chat_long_message_plain_text"] = ""
    game["chat_long_message_id"] = ""
    game["chat_memory_summary"] = ""
    game["chat_memory_summary_turn_count"] = 0


def clear_persisted_chat_history(tui: InteractiveOperatorTui) -> None:
    from client_surfaces.operator_tui.config.user_config_manager import save_user_config

    save_user_config({"chat_input_history": []})


def confirm_audit_cleanup_action(tui: InteractiveOperatorTui) -> bool:
    game = dict(tui.state.header_logo_game or {})
    viewer = dict(game.get("audit_viewer") or {})
    if str(viewer.get("mode") or "") != "confirm_cleanup":
        return False
    choice = str(viewer.get("confirm_choice") or "cancel").strip().lower()
    title = str(viewer.get("title") or "Cleanup")
    if choice != "delete":
        tui._audit_cleanup_show_result(title=title, summary="cleanup abgebrochen")
        return True
    action_id = str(viewer.get("cleanup_action_id") or "").strip()
    if not action_id:
        return False
    try:
        result = run_audit_cleanup_action(action_id)
    except Exception as exc:
        tui._audit_cleanup_show_result(title=title, summary=f"cleanup fehlgeschlagen: {exc}")
        return True
    if bool(result.get("clear_runtime_chat")):
        tui._clear_runtime_chat_history(game)
    if bool(result.get("clear_persisted_chat_history")):
        tui._clear_persisted_chat_history()
    counts = dict(result.get("counts") or {})
    summary_parts = [f"{key}={value}" for key, value in counts.items() if int(value) > 0]
    summary = ", ".join(summary_parts) if summary_parts else "keine gespeicherten Einträge gefunden"
    tui._audit_cleanup_show_result(title=title, summary=f"cleanup ausgefuehrt: {action_id} ({summary})")
    return True
