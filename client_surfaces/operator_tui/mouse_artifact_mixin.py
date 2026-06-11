"""MouseArtifactMixin — artifact interaction, inline-open, and navigation.

Contains: _apply_artifact_intent, _activate_artifact_chat,
          _append_artifact_chat_ai_message, _open_artifact_target_inline,
          _open_selected_item_inline, _open_inline_path, _run_command,
          _select_region_target, _set_selected_index,
          _deactivate_*_for_section_change, nav helpers, and delegating
          wrappers for mouse event processing extracted to
          ``mouse_event_handler``.

Mouse event handling has been extracted to ``mouse_event_handler.py``.
This module keeps the ``MouseArtifactMixin`` class and re-exports all
public symbols for backward compatibility.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

from client_surfaces.operator_tui.app import load_active_section
from client_surfaces.operator_tui.artifact_intent import ArtifactIntent, IntentConfidence
from client_surfaces.operator_tui.chat_long_message import (
    configure_middle_view_for_history_entry,
    is_showing_chat_long_message,
    long_message_history_rows,
    refresh_rendered_view,
)
from client_surfaces.operator_tui.chat_state import get_chat_state, set_chat_state
from client_surfaces.operator_tui.commands import execute_command
from client_surfaces.operator_tui.mouse_event_handler import (
    clamp_down,
    handle_left_click,
    handle_mouse_selection_event,
    handle_share_content_click,
    handle_visual_viewport_scrollbar_mouse,
    ingest_mouse_event,
    move_focus,
    parse_sgr_mouse_event,
    route_wheel_scroll,
    shortcut_action_at,
    shortcut_action_display_map,
    trigger_shortcut_action,
)
from client_surfaces.operator_tui.models import FocusPane, OperatorMode
from client_surfaces.operator_tui.plugins import resolve_item_reference
from client_surfaces.operator_tui.region_index import RegionTarget, build_region_index
from client_surfaces.operator_tui.sections import SECTIONS, get_section
from client_surfaces.operator_tui.audit_nav import audit_nav_items
from client_surfaces.operator_tui.template_nav import template_nav_items

if TYPE_CHECKING:
    from client_surfaces.operator_tui.chat_state import ChatState


class MouseArtifactMixin:
    """Mixin providing mouse event handling, artifact chat, and navigation."""

    # ── Delegating wrappers (extracted to mouse_event_handler) ────────────────

    def _ingest_mouse_event(self, **kwargs: Any) -> None:
        ingest_mouse_event(self, **kwargs)

    def _parse_sgr_mouse_event(self, raw: str) -> tuple[int, int, str, int, int, bool] | None:
        return parse_sgr_mouse_event(raw)

    def _shortcut_action_at(self, x: int, y: int) -> str | None:
        return shortcut_action_at(self, x, y)

    def _shortcut_action_display_map(self) -> dict[str, str]:
        return shortcut_action_display_map(self)

    def _trigger_shortcut_action(self, action: str) -> None:
        trigger_shortcut_action(self, action)

    def _handle_mouse_selection_event(self, game: dict[str, object], **kwargs: Any) -> bool:
        return handle_mouse_selection_event(self, game, **kwargs)

    def _route_wheel_scroll(self, game: dict, **kwargs: Any) -> None:
        route_wheel_scroll(self, game, **kwargs)

    def _handle_visual_viewport_scrollbar_mouse(self, game: dict[str, object], **kwargs: Any) -> bool:
        return handle_visual_viewport_scrollbar_mouse(self, game, **kwargs)

    def _handle_left_click(self, game: dict[str, object], **kwargs: Any) -> None:
        handle_left_click(self, game, **kwargs)

    def _handle_share_content_click(self, **kwargs: Any) -> bool:
        return handle_share_content_click(self, **kwargs)

    def _clamp_down(self) -> int:
        return clamp_down(self)

    def _move_focus(self, delta: int) -> None:
        move_focus(self, delta)

    # ── Auxiliary view deactivation ──────────────────────────────────────────

    def _deactivate_template_editor_for_section_change(self, state, *, next_section_id: str):
        if str(next_section_id) == "templates":
            return state
        game = state.header_logo_game if isinstance(state.header_logo_game, dict) else {}
        editor = dict(game.get("template_editor") or {})
        if not bool(editor.get("active")):
            return state
        game_out = dict(game)
        game_out["template_editor"] = {"active": False}
        next_mode = OperatorMode.NORMAL if state.mode is OperatorMode.EDIT else state.mode
        return state.with_updates(header_logo_game=game_out, mode=next_mode)

    def _deactivate_audit_viewer_for_section_change(self, state, *, next_section_id: str):
        if str(next_section_id) == "audit":
            return state
        game = state.header_logo_game if isinstance(state.header_logo_game, dict) else {}
        viewer = dict(game.get("audit_viewer") or {})
        if not bool(viewer.get("active")):
            return state
        game_out = dict(game)
        game_out["audit_viewer"] = {"active": False}
        return state.with_updates(header_logo_game=game_out)

    def _deactivate_aux_views_for_section_change(self, state, *, next_section_id: str):
        state = self._deactivate_template_editor_for_section_change(state, next_section_id=next_section_id)
        state = self._deactivate_audit_viewer_for_section_change(state, next_section_id=next_section_id)
        return state

    # ── Region / intent ──────────────────────────────────────────────────────

    def _select_region_target(self, target: RegionTarget) -> None:
        from client_surfaces.operator_tui.sections import SECTIONS

        game = dict(self.state.header_logo_game or {})
        if target.pane in {"header", "nav", "content", "detail"}:
            self._clear_chat_input_focus(game)

        new_section = str(target.section_id or self.state.section_id or "dashboard")
        if target.pane == "header":
            new_focus = FocusPane.HEADER
        elif target.pane == "nav":
            new_focus = FocusPane.NAVIGATION
        elif target.pane == "detail":
            new_focus = FocusPane.DETAIL
        else:
            new_focus = FocusPane.CONTENT
        new_selected = self.state.selected_index

        if isinstance(target.payload.get("selected_index"), int):
            new_selected = max(0, int(target.payload["selected_index"]))
        elif target.pane == "nav":
            section_ids = [s.id for s in SECTIONS]
            try:
                new_selected = section_ids.index(new_section)
            except ValueError:
                new_selected = 0
        elif isinstance(target.payload.get("index"), int):
            new_selected = max(0, int(target.payload["index"]))

        changed = (
            new_section != self.state.section_id
            or new_focus != self.state.focus
            or new_selected != self.state.selected_index
        )
        if not changed:
            return

        next_state = self.state.with_updates(
            header_logo_game=game,
            focus=new_focus,
            section_id=new_section,
            selected_index=new_selected,
        )
        next_state = self._deactivate_aux_views_for_section_change(next_state, next_section_id=new_section)
        if new_section != self.state.section_id:
            next_state = load_active_section(next_state, self._registry)
        if target.pane == "nav" and target.kind in {"section", "pane"}:
            from client_surfaces.operator_tui.tab_manager import open_or_activate_tab, tab_label_for_section
            next_state = open_or_activate_tab(
                next_state, section_id=new_section, kind="section",
                label=tab_label_for_section(new_section),
            )
        self._set_state(next_state)

    def _apply_artifact_intent(
        self,
        game: dict[str, object],
        *,
        intent: ArtifactIntent,
        now: float,
        width: int,
        height: int,
    ) -> None:
        game["artifact_intent_confidence"] = intent.confidence.value
        game["artifact_intent_score"] = round(float(intent.score), 3)
        game["artifact_intent_reason"] = intent.reason
        target = intent.target
        if target is None:
            game["artifact_intent_target"] = None
            return
        payload = dict(target.payload)
        target_payload = {
            "kind": target.kind,
            "section_id": target.section_id,
            "pane": target.pane,
            "label": target.label,
            "payload": payload,
        }
        game["artifact_intent_target"] = target_payload
        target_cell = self._target_cell_for_region_target(target=target, width=width, height=height)
        game["artifact_target_cell"] = target_cell
        if intent.confidence in {IntentConfidence.LIKELY, IntentConfidence.CONFIRMED}:
            game["tutorial_ai_target_mode"] = "fast_target"
            game["tutorial_ai_target_hint"] = target.pane or "content"
            if intent.confidence is IntentConfidence.CONFIRMED:
                self._activate_artifact_chat(game, target=target, now=now)
                self._open_artifact_target_inline(target=target)
        else:
            game["tutorial_ai_target_mode"] = "follow_user"

    def _target_cell_for_region_target(self, *, target: RegionTarget, width: int, height: int) -> tuple[int, int]:
        w = max(72, int(width))
        h = max(18, int(height))
        if target.pane == "nav":
            return (max(1, w // 6), max(2, h // 2))
        if target.pane == "detail":
            return (max(2, w - 10), max(2, h - 5))
        return (max(2, w // 2), max(2, h // 2))

    # ── Artifact chat ────────────────────────────────────────────────────────

    def _activate_artifact_chat(self, game: dict[str, object], *, target: RegionTarget, now: float) -> None:
        chat_raw = game.get("artifact_chat_state")
        chat = dict(chat_raw) if isinstance(chat_raw, dict) else {}
        active_target = {
            "section_id": target.section_id,
            "kind": target.kind,
            "label": target.label,
            "path": str(target.payload.get("path") or ""),
            "id": str(target.payload.get("id") or ""),
        }
        messages_raw = chat.get("messages")
        messages = [dict(msg) for msg in messages_raw if isinstance(msg, dict)] if isinstance(messages_raw, list) else []
        if not messages or messages[-1].get("text") != f"Kontext aktiv: {target.label}":
            messages.append(
                {
                    "at": float(now),
                    "source": "system",
                    "text": f"Kontext aktiv: {target.label}",
                }
            )
        chat.update(
            {
                "active_target": active_target,
                "messages": messages[-8:],
                "pending_request": "",
                "backend_source": self._tutorial_last_source or "local-knowledge",
                "error": "",
            }
        )
        game["artifact_chat_state"] = chat

    def _append_artifact_chat_ai_message(self, *, game: dict[str, object], now: float, text: str) -> None:
        chat_raw = game.get("artifact_chat_state")
        if not isinstance(chat_raw, dict):
            return
        chat = dict(chat_raw)
        messages_raw = chat.get("messages")
        messages = [dict(msg) for msg in messages_raw if isinstance(msg, dict)] if isinstance(messages_raw, list) else []
        normalized = " ".join(str(text or "").split())
        if not normalized:
            return
        if messages and str(messages[-1].get("text") or "") == normalized and str(messages[-1].get("source") or "") == "ai":
            return
        messages.append({"at": float(now), "source": "ai", "text": normalized})
        chat["messages"] = messages[-8:]
        chat["backend_source"] = self._tutorial_last_source or "local-knowledge"
        game["artifact_chat_state"] = chat

    # ── Inline open ──────────────────────────────────────────────────────────

    def _open_artifact_target_inline(self, *, target: RegionTarget) -> None:
        path = str(target.payload.get("path") or "").strip()
        if not path:
            return
        p = Path(path).expanduser()
        if not p.is_absolute():
            p = (Path.cwd() / p).resolve()
        if not p.exists() or not p.is_file():
            return
        self._open_inline_path(path_override=str(p))

    def _open_selected_item_inline(self) -> bool:
        section = get_section(self.state.section_id)
        payload = (self.state.section_payloads or {}).get(section.id, {})
        reference = resolve_item_reference(payload, self.state.selected_index)
        if not reference:
            return False
        return self._open_inline_path(path_override=reference)

    def _open_inline_path(self, *, path_override: str) -> bool:
        reference = str(path_override).strip()
        if not reference:
            return False

        path = Path(reference).expanduser()
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
        if not path.exists():
            self._set_state(self.state.with_updates(status_message=f"inline vim: file not found ({reference})"))
            return True
        if not path.is_file():
            self._set_state(self.state.with_updates(status_message=f"inline vim: not a file ({path.name})"))
            return True

        try:
            raw = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            self._set_state(self.state.with_updates(status_message=f"inline vim: {exc}"))
            return True

        max_lines = 260
        lines = raw.splitlines()
        clipped = lines[:max_lines]
        truncated = len(lines) > max_lines
        numbered = [f"{idx + 1:>4} {line}" for idx, line in enumerate(clipped)]
        language = (path.suffix or "").lstrip(".")
        fenced = "\n".join(numbered)
        if truncated:
            fenced += f"\n... ({len(lines) - max_lines} more lines)"
        markdown = (
            f"# Inline Vim Viewer\n\n"
            f"`{path}`\n\n"
            f"```{language}\n{fenced}\n```"
        )
        self._set_state(
            self.state.with_updates(
                mode=OperatorMode.EDIT,
                focus=FocusPane.CONTENT,
                markdown_source=markdown,
                status_message=f"inline vim: {path.name}",
            )
        )
        return True

    # ── Command runner ───────────────────────────────────────────────────────

    def _run_command(self, command: str) -> None:
        result = execute_command(command, self.state)
        msg = str(result.state.status_message or result.message or "")
        state = result.state.with_updates(status_message=msg)
        if msg:
            import time as _t
            _now = _t.monotonic()
            if hasattr(self, "_last_command_feedback"):
                self._last_command_feedback = msg
                self._last_command_feedback_at = _now
            _game_pre = dict(state.header_logo_game or {})
            _game_pre["_cmd_feedback"] = msg
            _game_pre["_cmd_feedback_at"] = _now
            state = state.with_updates(header_logo_game=_game_pre)
        state = self._deactivate_aux_views_for_section_change(state, next_section_id=state.section_id)
        if state.section_id != self.state.section_id or command.strip().lower() in {":refresh", "refresh", "r", ":next", ":prev"}:
            state = load_active_section(state, self._registry)
        if hasattr(self, "_apply_visual_command_requests"):
            state = self._apply_visual_command_requests(state)
        if hasattr(self, "_tick_external_window"):
            self.state = state
            self._tick_external_window()
            state = self.state
        self._command_buffer = ""
        if hasattr(self, "_command_cursor"):
            self._command_cursor = 0
        if hasattr(self, "_command_history_index"):
            self._command_history_index = None
        if hasattr(self, "_command_saved_draft"):
            self._command_saved_draft = ""
        game = dict(state.header_logo_game or {})
        game["command_input_cursor"] = 0
        mode_after = state.mode if state.mode is not OperatorMode.COMMAND else OperatorMode.NORMAL
        state = state.with_updates(header_logo_game=game, command_line="", mode=mode_after)
        if state.section_id != self.state.section_id:
            from client_surfaces.operator_tui.tab_manager import open_or_activate_tab, tab_label_for_section
            state = open_or_activate_tab(
                state, section_id=state.section_id, kind="section",
                label=tab_label_for_section(state.section_id),
            )
        self._set_state(state)

    # ── Selection / navigation helpers ───────────────────────────────────────

    def _set_selected_index(self, index: int) -> None:
        new_index = max(0, int(index))
        game = dict(self.state.header_logo_game or {})
        if self.state.focus is FocusPane.NAVIGATION:
            self._clear_chat_input_focus(game)
        next_state = self.state.with_updates(header_logo_game=game, selected_index=new_index)
        if self.state.focus is FocusPane.NAVIGATION:
            template_count = self._template_nav_selectable_count()
            audit_count = self._audit_nav_selectable_count()
            if 0 <= new_index < len(SECTIONS):
                section = SECTIONS[new_index]
                next_state = next_state.with_updates(section_id=section.id)
                next_state = self._deactivate_aux_views_for_section_change(next_state, next_section_id=section.id)
                if section.id != self.state.section_id:
                    next_state = load_active_section(next_state, self._registry)
                from client_surfaces.operator_tui.tab_manager import open_or_activate_tab, tab_label_for_section
                next_state = open_or_activate_tab(
                    next_state, section_id=section.id, kind="section",
                    label=tab_label_for_section(section.id),
                )
            elif template_count > 0 and len(SECTIONS) <= new_index < len(SECTIONS) + template_count:
                next_state = next_state.with_updates(section_id="templates")
            elif audit_count > 0 and len(SECTIONS) + template_count <= new_index < len(SECTIONS) + template_count + audit_count:
                next_state = next_state.with_updates(section_id="audit")
            else:
                game = dict(self.state.header_logo_game or self._default_header_snake())
                rows = long_message_history_rows(game)
                history_idx = new_index - len(SECTIONS) - template_count - audit_count
                if 0 <= history_idx < len(rows) and configure_middle_view_for_history_entry(game, rows[history_idx]):
                    next_state = next_state.with_updates(
                        header_logo_game=game,
                        focus=FocusPane.CONTENT,
                        selected_index=0,
                        status_message="Chat-History: Originalausgabe",
                    )
        self._set_state(next_state)

    def _template_nav_selectable_count(self) -> int:
        if self.state.section_id != "templates":
            return 0
        payload = dict((self.state.section_payloads or {}).get("templates") or {})
        return len(template_nav_items(payload))

    def _template_nav_item_for_nav_index(self, nav_index: int) -> tuple[int, dict[str, Any]] | None:
        if self.state.section_id != "templates":
            return None
        payload = dict((self.state.section_payloads or {}).get("templates") or {})
        items = template_nav_items(payload)
        item_pos = int(nav_index) - len(SECTIONS)
        if 0 <= item_pos < len(items):
            return items[item_pos]
        return None

    def _audit_nav_selectable_count(self) -> int:
        if self.state.section_id != "audit":
            return 0
        payload = dict((self.state.section_payloads or {}).get("audit") or {})
        return len(audit_nav_items(payload))

    def _audit_nav_item_for_nav_index(self, nav_index: int) -> tuple[int, dict[str, Any]] | None:
        if self.state.section_id != "audit":
            return None
        payload = dict((self.state.section_payloads or {}).get("audit") or {})
        items = audit_nav_items(payload)
        item_pos = int(nav_index) - len(SECTIONS)
        if 0 <= item_pos < len(items):
            return items[item_pos]
        return None

    def _clear_chat_input_focus(self, game: dict[str, object]) -> None:
        chat = get_chat_state(game)
        chat["chat_focus"] = False
        chat["chat_input_history_index"] = None
        set_chat_state(game, chat)
        game["artifact_chat_focus"] = False
