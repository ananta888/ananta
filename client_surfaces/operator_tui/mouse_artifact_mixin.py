"""MouseArtifactMixin — mouse event handling, artifact intent, and inline-open methods.

Contains: _ingest_mouse_event, _parse_sgr_mouse_event, _apply_artifact_intent,
          _target_cell_for_region_target, _activate_artifact_chat,
          _append_artifact_chat_ai_message, _open_artifact_target_inline,
          _open_selected_item_inline, _open_inline_path,
          _run_command, _clamp_down, _move_focus
"""
from __future__ import annotations

import re
import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from client_surfaces.operator_tui.app import load_active_section
from client_surfaces.operator_tui.artifact_intent import ArtifactIntent, IntentConfidence
from client_surfaces.operator_tui.commands import execute_command
from client_surfaces.operator_tui.models import FocusPane, OperatorMode
from client_surfaces.operator_tui.ai_snake_config_view import ai_snake_config_items, apply_ai_snake_config_change
from client_surfaces.operator_tui.mouse import (
    MouseEventType as NormalizedMouseEventType,
    normalize_mouse_state,
)
from client_surfaces.operator_tui.plugins import resolve_item_reference
from client_surfaces.operator_tui.region_index import RegionTarget, build_region_index
from client_surfaces.operator_tui.sections import SECTIONS, get_section


class MouseArtifactMixin:
    """Mixin providing mouse event handling, artifact chat, and navigation."""

    def _ingest_mouse_event(
        self,
        *,
        x: int,
        y: int,
        event_type: str,
        buttons: int = 0,
        scroll_delta: int = 0,
        now: float | None = None,
    ) -> None:
        game = dict(self.state.header_logo_game or self._default_header_snake())
        size = shutil.get_terminal_size((120, 32))
        width = max(72, int(size.columns))
        height = max(18, int(size.lines - 1))
        ts = float(now if now is not None else time.monotonic())
        self._mouse_state = normalize_mouse_state(
            self._mouse_state,
            x=x,
            y=y,
            width=width,
            height=height,
            event_type=cast(NormalizedMouseEventType, str(event_type)),
            buttons=buttons,
            scroll_delta=scroll_delta,
            now=ts,
        )
        game["mouse_state"] = {
            "x": self._mouse_state.x,
            "y": self._mouse_state.y,
            "event": self._mouse_state.last_event_type,
            "buttons": self._mouse_state.buttons,
            "scroll_delta": self._mouse_state.scroll_delta,
            "last_seen_at": self._mouse_state.last_seen_at,
            "active": self._mouse_state.active,
            "hover_started_at": self._mouse_state.hover_started_at,
        }

        region_index = build_region_index(self.state, width=width, height=height)
        target = region_index.get_target_at(self._mouse_state.x, self._mouse_state.y)
        if target is not None:
            game["mouse_target"] = {
                "kind": target.kind,
                "section_id": target.section_id,
                "pane": target.pane,
                "label": target.label,
                "payload": dict(target.payload),
            }
        else:
            game["mouse_target"] = None

        intent = self._intent_detector.evaluate(
            now=ts,
            mouse=self._mouse_state,
            target=target,
            selected_index=self.state.selected_index,
            current_section_id=self.state.section_id,
            user_feed=str(game.get("tutorial_user_feed") or ""),
        )
        self._apply_artifact_intent(game, intent=intent, now=ts, width=width, height=height)

        mouse_selection_handled = self._handle_mouse_selection_event(
            game,
            x=self._mouse_state.x,
            y=self._mouse_state.y,
            event_type=event_type,
            buttons=buttons,
        )

        # Left-click: select item + AI snake jumps there + open chat + trigger explanation.
        # Drag is handled as visual multi-select and does not repeatedly open targets.
        if not mouse_selection_handled and event_type == "down" and buttons == 1 and target is not None:
            self._handle_left_click(game, target=target, now=ts, width=width, height=height)

        status = str(game.pop("_copy_status_message", "") or f"mouse {self._mouse_state.x},{self._mouse_state.y}")
        self._set_state(self.state.with_updates(header_logo_game=game, status_message=status))

    def _parse_sgr_mouse_event(self, raw: str) -> tuple[int, int, str, int, int] | None:
        # Typical xterm SGR mouse: ESC [ < Cb ; Cx ; Cy M|m
        text = str(raw or "")
        match = re.search(r"\x1b\[<(\d+);(\d+);(\d+)([Mm])", text)
        if not match:
            return None
        cb = int(match.group(1))
        cx = max(0, int(match.group(2)) - 1)
        cy = max(0, int(match.group(3)) - 1)
        release = match.group(4) == "m"
        event_type = "move"
        buttons = 0
        scroll_delta = 0
        if cb & 64:
            event_type = "scroll_down" if (cb & 1) else "scroll_up"
            scroll_delta = 1 if event_type == "scroll_down" else -1
        elif release:
            event_type = "up"
        elif cb & 32:
            event_type = "move"
            button_code = cb & 3
            buttons = {0: 1, 1: 2, 2: 3}.get(button_code, 0)
        else:
            event_type = "down"
            button_code = cb & 3
            buttons = {0: 1, 1: 2, 2: 3}.get(button_code, 0)
        return cx, cy, event_type, buttons, scroll_delta

    def _handle_mouse_selection_event(
        self,
        game: dict[str, object],
        *,
        x: int,
        y: int,
        event_type: str,
        buttons: int,
    ) -> bool:
        if not self._snake_mode_active(game):
            return False
        if event_type == "down" and buttons == 3:
            self._snake_copy_selection_to_game(game)
            return True
        if event_type == "down" and buttons == 1:
            game["mouse_selection_active"] = True
            game["mouse_selection_anchor"] = (int(x), int(y))
            game["mouse_selection_dragged"] = False
            self._set_mouse_selection_rect(game, anchor=(int(x), int(y)), current=(int(x), int(y)), additive=False)
            return False
        if event_type == "move" and buttons == 1 and bool(game.get("mouse_selection_active")):
            anchor_raw = game.get("mouse_selection_anchor")
            if isinstance(anchor_raw, (list, tuple)) and len(anchor_raw) == 2:
                anchor = (int(anchor_raw[0]), int(anchor_raw[1]))
                game["mouse_selection_dragged"] = True
                self._set_mouse_selection_rect(game, anchor=anchor, current=(int(x), int(y)), additive=True)
                return True
        if event_type == "up" and bool(game.get("mouse_selection_active")):
            game["mouse_selection_active"] = False
            return bool(game.get("mouse_selection_dragged"))
        return False

    def _set_mouse_selection_rect(
        self,
        game: dict[str, object],
        *,
        anchor: tuple[int, int],
        current: tuple[int, int],
        additive: bool,
    ) -> None:
        ax, ay = anchor
        cx, cy = current
        min_x, max_x = sorted((int(ax), int(cx)))
        min_y, max_y = sorted((int(ay), int(cy)))
        rect = [(x, y) for y in range(min_y, max_y + 1) for x in range(min_x, max_x + 1)]
        existing_raw = game.get("selection_cells") or []
        existing = {
            (int(item[0]), int(item[1]))
            for item in existing_raw
            if isinstance(item, (list, tuple)) and len(item) == 2
        } if additive else set()
        existing.update(rect)
        game["selection_cells"] = sorted(existing)
        game["selection_anchor"] = anchor
        game["selection_regions"] = [(min_x, min_y, max_x, max_y)]

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

    # ── Left-click: select + AI snake + chat + explanation ───────────────────

    def _handle_left_click(
        self,
        game: dict[str, object],
        *,
        target: RegionTarget,
        now: float,
        width: int,
        height: int,
    ) -> None:
        """On left click: select the item, direct AI snake there, open chat, trigger explanation."""
        if bool(game.get("ai_snake_config_open")) and target.pane == "content":
            cfg_key = str(target.payload.get("ai_snake_config_key") or "")
            idx = int(target.payload.get("selected_index") or 0)
            if not cfg_key:
                items = ai_snake_config_items(game)
                if 0 <= idx < len(items):
                    cfg_key = str(items[idx].get("key") or "")
            if cfg_key:
                self.state = self.state.with_updates(selected_index=max(0, idx), focus=FocusPane.CONTENT)
                status = apply_ai_snake_config_change(game, key=cfg_key)
                if cfg_key == "visual_enabled" and not bool(game.get("tutorial_mode")):
                    self._disable_visual_ai_snake_runtime(game)
                self._set_state(self.state.with_updates(header_logo_game=game, status_message=status))
                return

        # 1. Select the clicked item in the UI (section switch, item index, focus)
        self._select_region_target(target)

        # 2. Direct AI snake to the exact click position
        game["artifact_target_cell"] = (self._mouse_state.x, self._mouse_state.y)
        game["tutorial_ai_target_mode"] = "fast_target"
        game["tutorial_ai_target_hint"] = target.pane or "content"
        game["artifact_intent_confidence"] = "confirmed"
        game["artifact_intent_target"] = {
            "kind": target.kind,
            "section_id": target.section_id,
            "pane": target.pane,
            "label": target.label,
            "payload": dict(target.payload),
        }

        # 3. Open AI chat panel with "Kontext aktiv" message
        self._activate_artifact_chat(game, target=target, now=now)

        # Trigger AI explanation when the game is active (tutorial_mode controls the amber snake,
        # but explanations should fire regardless — the tick runs whenever active=True)
        if not bool(game.get("active")):
            return

        # 4. Set context for the AI explanation: what was clicked
        label = str(target.label or target.section_id or "diesen Bereich")
        section = str(target.section_id or self.state.section_id or "")
        game["tutorial_user_feed"] = f"Erkläre {label} im Abschnitt {section}."
        game["tutorial_ai_local_contact"] = True
        game["tutorial_ai_contact_zone"] = target.pane or "content"

        # 5. Reset the async tip timer so a new explanation fires immediately on the next tick.
        #    _tutorial_ai_tip() checks _tutorial_async_next_refresh_at — setting it to 0 forces
        #    it to submit a new future on the very next tick, bypassing the normal refresh interval.
        self._tutorial_async_next_refresh_at = 0.0
        self._tutorial_async_tip_future = None   # cancel any pending tip for the old context

    def _select_region_target(self, target: RegionTarget) -> None:
        """Switch the TUI to the section/item the user clicked."""
        from client_surfaces.operator_tui.sections import SECTIONS

        new_section = str(target.section_id or self.state.section_id or "dashboard")
        new_focus = FocusPane.NAVIGATION if target.pane == "nav" else (
            FocusPane.DETAIL if target.pane == "detail" else FocusPane.CONTENT
        )
        new_selected = self.state.selected_index

        if target.pane == "nav":
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
            focus=new_focus,
            section_id=new_section,
            selected_index=new_selected,
        )
        if new_section != self.state.section_id:
            next_state = load_active_section(next_state, self._registry)
        self._set_state(next_state)

    def _run_command(self, command: str) -> None:
        result = execute_command(command, self.state)
        state = result.state.with_updates(status_message=str(result.state.status_message or result.message))
        if state.section_id != self.state.section_id or command.strip().lower() in {":refresh", "refresh", "r", ":next", ":prev"}:
            state = load_active_section(state, self._registry)
        self._command_buffer = ""
        if hasattr(self, "_command_cursor"):
            self._command_cursor = 0
        if hasattr(self, "_command_history_index"):
            self._command_history_index = None
        if hasattr(self, "_command_saved_draft"):
            self._command_saved_draft = ""
        game = dict(state.header_logo_game or {})
        game["command_input_cursor"] = 0
        self._set_state(state.with_updates(header_logo_game=game, command_line=""))

    def _clamp_down(self) -> int:
        cur = self.state.selected_index
        if self.state.focus is FocusPane.NAVIGATION:
            return min(cur + 1, len(SECTIONS) - 1)
        if self.state.focus is FocusPane.HEADER:
            from client_surfaces.operator_tui.header_config import CONFIG_ITEMS
            return min(cur + 1, len(CONFIG_ITEMS) - 1)
        return cur + 1

    def _move_focus(self, delta: int) -> None:
        panes = (FocusPane.HEADER, FocusPane.NAVIGATION, FocusPane.CONTENT, FocusPane.DETAIL)
        cur = panes.index(self.state.focus)
        new_focus = panes[(cur + delta) % len(panes)]
        if new_focus is FocusPane.NAVIGATION:
            section_ids = [s.id for s in SECTIONS]
            try:
                new_selected = section_ids.index(self.state.section_id)
            except ValueError:
                new_selected = 0
        elif new_focus is FocusPane.HEADER or self.state.focus in (FocusPane.NAVIGATION, FocusPane.HEADER):
            new_selected = 0
        else:
            new_selected = self.state.selected_index
        next_state = self.state.with_updates(focus=new_focus, selected_index=new_selected)
        self._set_state(next_state)
