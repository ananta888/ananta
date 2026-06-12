"""Internal sub-module of the Operator TUI renderer.

Extracted from _renderer_content.py to keep the main module small.
This module owns: Templates tree view, audit viewer and template editor
renderers (incl. template syntax highlighting).

Public re-exports: the parent _renderer_content module re-exports every
function so the public chain (renderer -> _renderer_content -> sub-module)
keeps working transparently.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from client_surfaces.operator_tui.theme import DEFAULT_THEME
from client_surfaces.operator_tui._renderer_utils import _clip, _overlay_at_visible_col

if TYPE_CHECKING:
    from client_surfaces.operator_tui.models import OperatorState


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



