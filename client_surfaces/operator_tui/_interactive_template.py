from __future__ import annotations

import json
import os
import shutil
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any

from client_surfaces.operator_tui.models import FocusPane, OperatorMode

if TYPE_CHECKING:
    from client_surfaces.operator_tui.interactive import InteractiveOperatorTui


def template_editor_active(tui: InteractiveOperatorTui) -> bool:
    game = tui.state.header_logo_game if isinstance(tui.state.header_logo_game, dict) else {}
    editor = dict(game.get("template_editor") or {})
    return bool(editor.get("active")) and tui.state.section_id == "templates"


def selected_template_entry(
    tui: InteractiveOperatorTui,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | None:
    if tui.state.section_id != "templates":
        return None
    payload = dict((tui.state.section_payloads or {}).get("templates") or {})
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        return None
    idx = max(0, min(len(items) - 1, int(tui.state.selected_index)))
    item = items[idx]
    if not isinstance(item, dict):
        return None
    kind = str(item.get("kind") or "")
    if kind not in {"template", "system_prompt", "blueprint"}:
        return None
    raw_id = str(item.get("raw_id") or "")
    raw_list = payload.get("blueprints_raw") if kind == "blueprint" else payload.get("templates_raw")
    if not isinstance(raw_list, list):
        return None
    raw = next((entry for entry in raw_list if isinstance(entry, dict) and str(entry.get("id") or "") == raw_id), {})
    if not isinstance(raw, dict):
        return None
    return payload, item, raw


def template_editor_text_for_item(
    tui: InteractiveOperatorTui, *, kind: str, item: dict[str, Any], raw: dict[str, Any]
) -> str:
    if kind == "blueprint":
        return json.dumps(raw, indent=2, ensure_ascii=False)
    return str(raw.get("prompt_template") or item.get("prompt_preview") or "")


def template_editor_viewport_metrics(tui: InteractiveOperatorTui) -> tuple[int, int]:
    size = shutil.get_terminal_size((120, 32))
    width = max(72, int(size.columns))
    height = max(18, int(size.lines - 1))
    left_width = 22
    detail_width = 34
    middle_width = max(18, width - left_width - detail_width - 6)
    body_height = max(3, height - 5 - 8)
    pane_title_rows = 1
    editor_header_rows = 3
    visible_rows = max(1, body_height - pane_title_rows - editor_header_rows)
    text_prefix_width = 6
    visible_cols = max(8, middle_width - text_prefix_width)
    return visible_rows, visible_cols


def template_editor_ensure_cursor_visible(
    tui: InteractiveOperatorTui, editor: dict[str, Any]
) -> dict[str, Any]:
    source = str(editor.get("text") or "")
    cursor = max(0, min(len(source), int(editor.get("cursor") or 0)))
    before = source[:cursor]
    cursor_line = before.count("\n")
    cursor_col = len(before.rsplit("\n", 1)[-1])
    lines = source.splitlines() or [""]
    max_line = max(0, len(lines) - 1)
    visible_rows, visible_cols = tui._template_editor_viewport_metrics()

    line_offset = max(0, int(editor.get("view_line_offset") or 0))
    max_line_offset = max(0, len(lines) - visible_rows)
    line_offset = min(line_offset, max_line_offset)
    if cursor_line < line_offset:
        line_offset = cursor_line
    elif cursor_line >= line_offset + visible_rows:
        line_offset = max(0, cursor_line - visible_rows + 1)

    col_offset = max(0, int(editor.get("view_col_offset") or 0))
    max_col = max(0, len(lines[min(cursor_line, max_line)]) - 1)
    max_col_offset = max(0, max_col - visible_cols + 1)
    col_offset = min(col_offset, max_col_offset)
    if cursor_col < col_offset:
        col_offset = cursor_col
    elif cursor_col >= col_offset + visible_cols:
        col_offset = max(0, cursor_col - visible_cols + 1)

    editor["view_line_offset"] = max(0, line_offset)
    editor["view_col_offset"] = max(0, col_offset)
    return editor


def template_editor_scroll_vertical(tui: InteractiveOperatorTui, delta_lines: int) -> None:
    game = dict(tui.state.header_logo_game or {})
    editor = dict(game.get("template_editor") or {})
    if not bool(editor.get("active")):
        return
    source = str(editor.get("text") or "")
    lines = source.splitlines() or [""]
    visible_rows, _ = tui._template_editor_viewport_metrics()
    max_offset = max(0, len(lines) - visible_rows)
    current = max(0, int(editor.get("view_line_offset") or 0))
    editor["view_line_offset"] = max(0, min(max_offset, current + int(delta_lines)))
    game["template_editor"] = editor
    tui._set_state(tui.state.with_updates(header_logo_game=game))


def template_editor_set_cursor_from_content_click(
    tui: InteractiveOperatorTui, *, x: int, y: int, width: int, height: int
) -> bool:
    game = dict(tui.state.header_logo_game or {})
    editor = dict(game.get("template_editor") or {})
    if not bool(editor.get("active")):
        return False

    left_width = 22
    detail_width = 34
    middle_width = max(18, int(width) - left_width - detail_width - 6)
    body_start = 10 if len(tui.state.open_tabs) >= 2 else 9
    content_x1 = left_width + 2
    content_x2 = content_x1 + middle_width - 1
    body_y1 = body_start
    body_height = max(3, int(height) - 5 - body_start)
    body_y2 = body_y1 + body_height - 1
    if not (content_x1 <= int(x) <= content_x2 and body_y1 <= int(y) <= body_y2):
        return False

    local_row = int(y) - body_y1
    local_col = int(x) - content_x1
    if local_row < 4:
        tui._set_state(tui.state.with_updates(focus=FocusPane.CONTENT))
        return True

    text = str(editor.get("text") or "")
    lines = text.splitlines() or [""]
    view_line_offset = max(0, int(editor.get("view_line_offset") or 0))
    view_col_offset = max(0, int(editor.get("view_col_offset") or 0))
    text_row = local_row - 4
    target_line = max(0, min(len(lines) - 1, view_line_offset + text_row))
    click_col = max(0, local_col - 6)
    target_col = max(0, min(len(lines[target_line]), view_col_offset + click_col))
    new_cursor = target_col
    for idx in range(target_line):
        new_cursor += len(lines[idx]) + 1
    editor["cursor"] = max(0, min(len(text), int(new_cursor)))
    editor = tui._template_editor_ensure_cursor_visible(editor)
    game["template_editor"] = editor
    tui._set_state(tui.state.with_updates(header_logo_game=game, focus=FocusPane.CONTENT))
    return True


def open_template_editor_for_selected(tui: InteractiveOperatorTui) -> bool:
    selected = tui._selected_template_entry()
    if selected is None:
        return False
    _, item, raw = selected
    kind = str(item.get("kind") or "template")
    text = tui._template_editor_text_for_item(kind=kind, item=item, raw=raw)
    game = dict(tui.state.header_logo_game or {})
    game["template_editor"] = {
        "active": True,
        "template_id": str(raw.get("id") or item.get("raw_id") or ""),
        "kind": kind,
        "title": str(item.get("title") or ""),
        "text": text,
        "cursor": len(text),
        "view_line_offset": 0,
        "view_col_offset": 0,
        "dirty": False,
    }
    game["template_editor"] = tui._template_editor_ensure_cursor_visible(dict(game["template_editor"]))
    tui._set_state(
        tui.state.with_updates(
            mode=OperatorMode.EDIT,
            focus=FocusPane.CONTENT,
            header_logo_game=game,
            markdown_source="",
            status_message=f"template editor: {str(item.get('title') or '')}",
        )
    )
    return True


def template_editor_insert_text(tui: InteractiveOperatorTui, text: str) -> None:
    if not text:
        return
    game = dict(tui.state.header_logo_game or {})
    editor = dict(game.get("template_editor") or {})
    if not bool(editor.get("active")):
        return
    source = str(editor.get("text") or "")
    cursor = max(0, min(len(source), int(editor.get("cursor") or 0)))
    editor["text"] = source[:cursor] + text + source[cursor:]
    editor["cursor"] = cursor + len(text)
    editor["dirty"] = True
    editor = tui._template_editor_ensure_cursor_visible(editor)
    game["template_editor"] = editor
    tui._set_state(tui.state.with_updates(header_logo_game=game))


def template_editor_backspace(tui: InteractiveOperatorTui) -> None:
    game = dict(tui.state.header_logo_game or {})
    editor = dict(game.get("template_editor") or {})
    if not bool(editor.get("active")):
        return
    source = str(editor.get("text") or "")
    cursor = max(0, min(len(source), int(editor.get("cursor") or 0)))
    if cursor <= 0:
        return
    editor["text"] = source[: cursor - 1] + source[cursor:]
    editor["cursor"] = cursor - 1
    editor["dirty"] = True
    editor = tui._template_editor_ensure_cursor_visible(editor)
    game["template_editor"] = editor
    tui._set_state(tui.state.with_updates(header_logo_game=game))


def template_editor_delete(tui: InteractiveOperatorTui) -> None:
    game = dict(tui.state.header_logo_game or {})
    editor = dict(game.get("template_editor") or {})
    if not bool(editor.get("active")):
        return
    source = str(editor.get("text") or "")
    cursor = max(0, min(len(source), int(editor.get("cursor") or 0)))
    if cursor >= len(source):
        return
    editor["text"] = source[:cursor] + source[cursor + 1 :]
    editor["cursor"] = cursor
    editor["dirty"] = True
    editor = tui._template_editor_ensure_cursor_visible(editor)
    game["template_editor"] = editor
    tui._set_state(tui.state.with_updates(header_logo_game=game))


def template_editor_move_cursor(tui: InteractiveOperatorTui, delta: int) -> None:
    game = dict(tui.state.header_logo_game or {})
    editor = dict(game.get("template_editor") or {})
    if not bool(editor.get("active")):
        return
    source = str(editor.get("text") or "")
    cursor = max(0, min(len(source), int(editor.get("cursor") or 0)))
    editor["cursor"] = max(0, min(len(source), cursor + int(delta)))
    editor = tui._template_editor_ensure_cursor_visible(editor)
    game["template_editor"] = editor
    tui._set_state(tui.state.with_updates(header_logo_game=game))


def template_editor_move_cursor_vertical(tui: InteractiveOperatorTui, direction: int) -> None:
    game = dict(tui.state.header_logo_game or {})
    editor = dict(game.get("template_editor") or {})
    if not bool(editor.get("active")):
        return
    source = str(editor.get("text") or "")
    cursor = max(0, min(len(source), int(editor.get("cursor") or 0)))
    before = source[:cursor]
    line_index = before.count("\n")
    col = len(before.rsplit("\n", 1)[-1])
    lines = source.splitlines() or [""]
    target_line = max(0, min(len(lines) - 1, line_index + int(direction)))
    target_col = min(col, len(lines[target_line]))
    new_cursor = 0
    for idx in range(target_line):
        new_cursor += len(lines[idx]) + 1
    new_cursor += target_col
    editor["cursor"] = max(0, min(len(source), new_cursor))
    editor = tui._template_editor_ensure_cursor_visible(editor)
    game["template_editor"] = editor
    tui._set_state(tui.state.with_updates(header_logo_game=game))


def template_editor_save(tui: InteractiveOperatorTui) -> None:
    game = dict(tui.state.header_logo_game or {})
    editor = dict(game.get("template_editor") or {})
    template_id = str(editor.get("template_id") or "").strip()
    editor_kind = str(editor.get("kind") or "template")
    if not template_id:
        tui._set_state(tui.state.with_updates(status_message="template editor: template_id fehlt"))
        return
    token = str(os.environ.get("ANANTA_AUTH_TOKEN") or os.environ.get("ANANTA_PASSWORD") or "").strip()
    if not token:
        tui._set_state(tui.state.with_updates(status_message="template editor: auth token fehlt"))
        return
    if editor_kind == "blueprint":
        try:
            blueprint_payload = json.loads(str(editor.get("text") or "{}"))
        except json.JSONDecodeError:
            tui._set_state(tui.state.with_updates(status_message="blueprint save failed: invalid JSON"))
            return
        if not isinstance(blueprint_payload, dict):
            tui._set_state(tui.state.with_updates(status_message="blueprint save failed: expected JSON object"))
            return
        endpoint = f"{str(tui.state.endpoint).rstrip('/')}/teams/blueprints/{template_id}"
        allowed_keys = {"name", "description", "base_team_type_name", "roles", "artifacts"}
        request_payload = {key: blueprint_payload[key] for key in allowed_keys if key in blueprint_payload}
    else:
        endpoint = f"{str(tui.state.endpoint).rstrip('/')}/templates/{template_id}"
        request_payload = {"prompt_template": str(editor.get("text") or "")}
    request_data = json.dumps(request_payload).encode("utf-8")
    req = urllib.request.Request(endpoint, data=request_data, method="PATCH")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=8.0) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        tui._set_state(tui.state.with_updates(status_message=f"template save failed: HTTP {exc.code}"))
        return
    except urllib.error.URLError as exc:
        tui._set_state(tui.state.with_updates(status_message=f"template save failed: {exc.reason}"))
        return
    try:
        payload = json.loads(body) if body else {}
    except json.JSONDecodeError:
        payload = {}

    section_payloads = dict(tui.state.section_payloads or {})
    templates_payload = dict(section_payloads.get("templates") or {})
    items = [dict(item) if isinstance(item, dict) else item for item in list(templates_payload.get("items") or [])]
    if editor_kind == "blueprint":
        blueprints_raw = [
            dict(item) if isinstance(item, dict) else item
            for item in list(templates_payload.get("blueprints_raw") or [])
        ]
        response_data = payload.get("data") if isinstance(payload, dict) else None
        for idx, entry in enumerate(blueprints_raw):
            if isinstance(entry, dict) and str(entry.get("id") or "") == template_id:
                if isinstance(response_data, dict):
                    blueprints_raw[idx] = dict(response_data)
                else:
                    blueprints_raw[idx] = {**entry, **request_payload}
        for item in items:
            if isinstance(item, dict) and str(item.get("raw_id") or "") == template_id:
                item["title"] = str(request_payload.get("name") or item.get("title") or "")
                item["description"] = str(request_payload.get("description") or item.get("description") or "")[:100]
                if isinstance(request_payload.get("roles"), list):
                    item["roles_count"] = len(request_payload["roles"])
                if isinstance(request_payload.get("artifacts"), list):
                    item["artifacts_count"] = len(request_payload["artifacts"])
        templates_payload["blueprints_raw"] = blueprints_raw
    else:
        templates_raw = [
            dict(item) if isinstance(item, dict) else item
            for item in list(templates_payload.get("templates_raw") or [])
        ]
        for entry in templates_raw:
            if isinstance(entry, dict) and str(entry.get("id") or "") == template_id:
                entry["prompt_template"] = str(editor.get("text") or "")
        for item in items:
            if isinstance(item, dict) and str(item.get("raw_id") or "") == template_id:
                item["prompt_preview"] = str(editor.get("text") or "")[:120].replace("\n", " ")
        templates_payload["templates_raw"] = templates_raw
    templates_payload["items"] = items
    section_payloads["templates"] = templates_payload
    editor["dirty"] = False
    game["template_editor"] = editor
    data = payload.get("data") if isinstance(payload, dict) else None
    warnings = ""
    if isinstance(data, dict) and data.get("warnings"):
        warnings = " (mit Warnungen)"
    tui._set_state(
        tui.state.with_updates(
            header_logo_game=game,
            section_payloads=section_payloads,
            status_message=("blueprint gespeichert" if editor_kind == "blueprint" else f"template gespeichert{warnings}"),
        )
    )
