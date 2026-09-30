"""Delegating TUI methods of the audit viewer, template editor and AI-Snake config panel.

``InteractiveOperatorTui`` exposes these operations as methods because the key
bindings, mouse handlers and mixins call them on the TUI instance. The
behavior lives in ``_interactive_audit``, ``_interactive_template`` and
``_interactive_ai_config``; these mixins only bind it to ``self`` (split out
of ``interactive.py`` to keep that module focused on wiring and lifecycle).
They are listed first in the TUI's bases, so they keep the precedence the
methods had when they were defined on the class itself.
"""
from __future__ import annotations

from typing import Any

from client_surfaces.operator_tui import _interactive_ai_config as _iconfig
from client_surfaces.operator_tui import _interactive_audit as _ia
from client_surfaces.operator_tui import _interactive_template as _it


class AuditTemplateEditorMixin:
    """Audit viewer / cleanup and template editor operations."""

    def _audit_viewer_active(self) -> bool:
        return _ia.audit_viewer_active(self)

    def _audit_cleanup_confirm_mode_active(self) -> bool:
        return _ia.audit_cleanup_confirm_mode_active(self)

    def _audit_cleanup_result_mode_active(self) -> bool:
        return _ia.audit_cleanup_result_mode_active(self)

    def _audit_cleanup_set_choice(self, choice: str) -> None:
        _ia.audit_cleanup_set_choice(self, choice)

    def _audit_cleanup_close_viewer(self, *, status_message: str) -> None:
        _ia.audit_cleanup_close_viewer(self, status_message=status_message)

    def _audit_cleanup_show_result(self, *, title: str, summary: str) -> None:
        _ia.audit_cleanup_show_result(self, title=title, summary=summary)

    def _audit_cleanup_button_choice_from_click(self, *, x: int, y: int, width: int, height: int) -> str | None:
        return _ia.audit_cleanup_button_choice_from_click(self, x=x, y=y, width=width, height=height)

    def _audit_cleanup_handle_mouse_click(self, *, x: int, y: int, width: int, height: int) -> bool:
        return _ia.audit_cleanup_handle_mouse_click(self, x=x, y=y, width=width, height=height)

    def _selected_audit_entry(self) -> tuple[dict[str, Any], dict[str, Any]] | None:
        return _ia.selected_audit_entry(self)

    def _audit_viewer_viewport_metrics(self) -> tuple[int, int]:
        return _ia.audit_viewer_viewport_metrics(self)

    def _audit_viewer_scroll_vertical(self, delta_lines: int) -> None:
        _ia.audit_viewer_scroll_vertical(self, delta_lines)

    def _audit_viewer_scroll_horizontal(self, delta_cols: int) -> None:
        _ia.audit_viewer_scroll_horizontal(self, delta_cols)

    def _open_audit_viewer_for_selected(self) -> bool:
        return _ia.open_audit_viewer_for_selected(self)

    def _clear_runtime_chat_history(self, game: dict[str, Any]) -> None:
        _ia.clear_runtime_chat_history(self, game)

    def _clear_persisted_chat_history(self) -> None:
        _ia.clear_persisted_chat_history(self)

    def _confirm_audit_cleanup_action(self) -> bool:
        return _ia.confirm_audit_cleanup_action(self)

    def _template_editor_active(self) -> bool:
        return _it.template_editor_active(self)

    def _selected_template_entry(self) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | None:
        return _it.selected_template_entry(self)

    def _template_editor_text_for_item(self, *, kind: str, item: dict[str, Any], raw: dict[str, Any]) -> str:
        return _it.template_editor_text_for_item(self, kind=kind, item=item, raw=raw)

    def _template_editor_viewport_metrics(self) -> tuple[int, int]:
        return _it.template_editor_viewport_metrics(self)

    def _template_editor_ensure_cursor_visible(self, editor: dict[str, Any]) -> dict[str, Any]:
        return _it.template_editor_ensure_cursor_visible(self, editor)

    def _template_editor_scroll_vertical(self, delta_lines: int) -> None:
        _it.template_editor_scroll_vertical(self, delta_lines)

    def _template_editor_set_cursor_from_content_click(self, *, x: int, y: int, width: int, height: int) -> bool:
        return _it.template_editor_set_cursor_from_content_click(self, x=x, y=y, width=width, height=height)

    def _open_template_editor_for_selected(self) -> bool:
        return _it.open_template_editor_for_selected(self)

    def _template_editor_insert_text(self, text: str) -> None:
        _it.template_editor_insert_text(self, text)

    def _template_editor_backspace(self) -> None:
        _it.template_editor_backspace(self)

    def _template_editor_delete(self) -> None:
        _it.template_editor_delete(self)

    def _template_editor_move_cursor(self, delta: int) -> None:
        _it.template_editor_move_cursor(self, delta)

    def _template_editor_move_cursor_vertical(self, direction: int) -> None:
        _it.template_editor_move_cursor_vertical(self, direction)

    def _template_editor_save(self) -> None:
        _it.template_editor_save(self)


class AiSnakeConfigPanelMixin:
    """AI-Snake config panel and its combo box."""

    def _toggle_ai_snake_config_panel(self) -> None:
        _iconfig.toggle_ai_snake_config_panel(self)

    def _toggle_ai_snake_config_selected(self) -> None:
        _iconfig.toggle_ai_snake_config_selected(self)

    def _ai_snake_config_combo_active(self, game: dict[str, object] | None = None) -> bool:
        return _iconfig.ai_snake_config_combo_active(self, game)

    def _ai_snake_config_next_index(self, delta: int, game: dict[str, object] | None = None) -> int:
        return _iconfig.ai_snake_config_next_index(self, delta, game)

    def _open_ai_snake_config_combo(self, game: dict[str, object], *, key: str, idx: int) -> None:
        _iconfig.open_ai_snake_config_combo(self, game, key=key, idx=idx)

    def _ai_snake_config_combo_close(self, *, status: str = "ai config: auswahl geschlossen") -> None:
        _iconfig.ai_snake_config_combo_close(self, status=status)

    def _ai_snake_config_combo_filter_text(self, combo: dict[str, object]) -> str:
        return _iconfig.ai_snake_config_combo_filter_text(self, combo)

    def _ai_snake_config_combo_apply(self, game: dict[str, object], *, value: str) -> None:
        _iconfig.ai_snake_config_combo_apply(self, game, value=value)

    def _ai_snake_config_combo_commit(self) -> None:
        _iconfig.ai_snake_config_combo_commit(self)

    def _ai_snake_config_combo_move(self, delta: int) -> None:
        _iconfig.ai_snake_config_combo_move(self, delta)

    def _ai_snake_config_combo_append_filter(self, ch: str) -> None:
        _iconfig.ai_snake_config_combo_append_filter(self, ch)

    def _ai_snake_config_combo_backspace(self) -> None:
        _iconfig.ai_snake_config_combo_backspace(self)

    def _ai_snake_config_combo_delete(self) -> None:
        _iconfig.ai_snake_config_combo_delete(self)

    def _ai_snake_config_combo_move_cursor(self, delta: int) -> None:
        _iconfig.ai_snake_config_combo_move_cursor(self, delta)

    def _ai_snake_config_combo_select_value(self, *, value: str) -> None:
        _iconfig.ai_snake_config_combo_select_value(self, value=value)
