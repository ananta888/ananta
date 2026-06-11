"""ChatMixin — chat/notes/ask methods extracted from InteractiveOperatorTui.

Contains: _capture_snake_ask_trace, _chat_send_message, _process_notes_ops,
          _init_notes_channel, _tick_chat, _on_chat_messages_received

Delegates formatting to ChatMessageFormatterMixin and history management
to ChatHistoryManagerMixin (SPLIT-024).

Used as a mixin: class InteractiveOperatorTui(ChatMixin, ...):
All methods use self.* — no back-reference needed beyond normal Python MRO.
"""
from __future__ import annotations

import os
import time
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any

from client_surfaces.operator_tui.chat_history_manager import ChatHistoryManagerMixin
from client_surfaces.operator_tui.chat_message_formatter import ChatMessageFormatterMixin

if TYPE_CHECKING:
    pass


def _capture_snake_ask_trace(game: dict[str, object], data: dict[str, Any]) -> None:
    """CRPS-007: capture the retrieval profile trace from a /snake/ask response.

    Stored at ``game["last_snake_ask_trace"]`` so the Profile Inspector footer
    (renderer / chat channel status) can display which domain/intent/trigger_mode
    was resolved for the most recent :ask. The trace is a thin subset of what
    the Hub returns — only the fields a user can act on. The full Hub trace is
    dropped because it can include worker internals and is not UI-safe.
    """
    trace = data.get("trace") if isinstance(data, dict) else None
    if not isinstance(trace, dict):
        return
    rag = trace.get("rag")
    if not isinstance(rag, dict):
        return
    profile = rag.get("retrieval_profile")
    if not isinstance(profile, dict):
        return
    inspector = {
        "profile_id": str(profile.get("profile_id") or "?"),
        "domain": str(profile.get("domain") or "?"),
        "intent": str(profile.get("intent") or "?"),
        "analysis_mode": str(profile.get("analysis_mode") or "standard"),
        "trigger_mode": str(profile.get("trigger_mode") or "auto"),
        "feature_flag": str(profile.get("feature_flag") or "auto"),
        "selected_by": str(profile.get("selected_by") or "?"),
        "source_types": list(profile.get("source_types") or []),
        "reasons": list(profile.get("reasons") or [])[:5],
    }
    game["last_snake_ask_trace"] = inspector
    seq = int(game.get("last_snake_ask_trace_seq") or 0) + 1
    game["last_snake_ask_trace_seq"] = seq
    summary = str(rag.get("summary") or "")
    if summary:
        game["last_snake_ask_summary"] = summary


class ChatMixin(ChatHistoryManagerMixin, ChatMessageFormatterMixin):
    """Mixin providing chat, notes, and ask-question functionality.

    Inherits from ChatHistoryManagerMixin and ChatMessageFormatterMixin
    for session management and message formatting respectively.
    """

    def _chat_send_message(self) -> None:
        game = dict(self.state.header_logo_game or {})
        from client_surfaces.operator_tui.chat_state import (
            get_chat_state, set_chat_state, get_active_channel, make_message,
            append_message, sanitize_text, ChannelType, DeliveryState,
        )
        from client_surfaces.operator_tui.chat_policy import check_policy, audit, system_message_for_deny
        from client_surfaces.operator_tui.snake_notes import append_note

        chat = get_chat_state(game)
        buf = sanitize_text(str(chat.get("chat_input_buffer") or ""))
        if not buf:
            return

        if buf.startswith("//"):
            chat["chat_input_buffer"] = ""
            chat["chat_input_cursor"] = 0
            chat["chat_input_history_index"] = None
            chat["chat_input_saved_draft"] = ""
            game["shortcut_help_middle_open"] = not bool(game.get("shortcut_help_middle_open"))
            game["shortcut_help_open"] = False
            set_chat_state(game, chat)
            self._set_state(
                self.state.with_updates(
                    header_logo_game=game,
                    status_message="shortcuts mitte: an" if bool(game.get("shortcut_help_middle_open")) else "shortcuts mitte: aus",
                )
            )
            return

        if self._handle_session_command(buf, chat=chat, game=game, ch_id=None):
            return

        chat["chat_input_buffer"] = ""
        chat["chat_input_cursor"] = 0
        history = [str(item) for item in (chat.get("chat_input_history") or []) if str(item).strip()]
        if not history or history[-1] != buf:
            history.append(buf)
        chat["chat_input_history"] = history[-100:]
        chat["chat_input_history_index"] = None
        chat["chat_input_saved_draft"] = ""
        try:
            if hasattr(self, "_save_chat_to_history"):
                self._save_chat_to_history(buf)
        except Exception:
            pass
        ch = get_active_channel(chat)
        if ch is None:
            set_chat_state(game, chat)
            self._set_state(self.state.with_updates(header_logo_game=game))
            return
        ch_id = str(ch.get("id") or "room:main")
        ch_type_raw = ch.get("channel_type") or "room"
        ch_type = str(getattr(ch_type_raw, "value", ch_type_raw))
        local_id = str(game.get("local_snake_id") or "s1")

        if buf.startswith("/") and not buf.startswith("//"):
            from client_surfaces.operator_tui.chat_control_parser import parse_chat_command
            from client_surfaces.operator_tui.chat_control_config import load_chat_control_config
            from client_surfaces.operator_tui.chat_control_policy import evaluate
            from client_surfaces.operator_tui.tui_action_dispatcher import TuiActionDispatcher, ActionRequest
            from client_surfaces.operator_tui.chat_control_audit import get_default_audit_log

            cc_cfg_raw = game.get("chat_control_config") if isinstance(game.get("chat_control_config"), dict) else {}
            cc_cfg = load_chat_control_config(cc_cfg_raw)

            if cc_cfg.enabled:
                parsed = parse_chat_command(buf, nl_mode_enabled=cc_cfg.nl_mode_enabled)
                decision = evaluate(parsed, config=cc_cfg)
                audit_log = get_default_audit_log()

                if decision.allowed():
                    dispatcher = TuiActionDispatcher()
                    dispatcher.set_tui_state(dict(game))
                    req = ActionRequest(action_id=decision.action_id, args=decision.normalized_args, source="chat")
                    result = dispatcher.dispatch(req)

                    audit_log.record(
                        source_channel=ch_id,
                        sender_kind="user",
                        raw_text=buf,
                        parsed_action_id=parsed.action_id,
                        policy_verdict=decision.verdict,
                        dispatch_status=result.status,
                        mode=cc_cfg.mode,
                        auto_confirmed=decision.auto_confirmed,
                        reason=decision.reason,
                        extra={"control_result_marker": result.control_result_marker},
                    )

                    for k, v in result.changed_state_summary.items():
                        game[k] = v

                    ctrl_msg = make_message(
                        channel_id=ch_id, channel_type=ch_type,
                        sender_id="tui_control", sender_kind="control",
                        text=f"[TUI] {result.message}",
                        visibility="local_only",
                        delivery_state="sent",
                    )
                    append_message(chat, ctrl_msg)
                    set_chat_state(game, chat)
                    self._set_state(self.state.with_updates(
                        header_logo_game=game,
                        status_message=result.message[:60],
                    ))
                    return

                audit_log.record(
                    source_channel=ch_id,
                    sender_kind="user",
                    raw_text=buf,
                    parsed_action_id=parsed.action_id,
                    policy_verdict=decision.verdict,
                    dispatch_status="skipped",
                    mode=cc_cfg.mode,
                    reason=decision.reason,
                )
                deny_msg = make_message(
                    channel_id=ch_id, channel_type=ch_type,
                    sender_id="tui_control", sender_kind="control",
                    text=f"[TUI] Denied: {decision.reason}",
                    visibility="local_only",
                    delivery_state="sent",
                )
                append_message(chat, deny_msg)
                set_chat_state(game, chat)
                self._set_state(self.state.with_updates(
                    header_logo_game=game,
                    status_message=f"command denied: {decision.reason[:40]}",
                ))
                return

        if ch_type == "notes":
            note = append_note(buf)
            if note:
                msg = make_message(
                    channel_id=ch_id, channel_type=ch_type,
                    sender_id=local_id, sender_kind="user",
                    text=buf, visibility="local_only",
                    delivery_state="sent",
                )
                msg["id"] = note["id"]
                append_message(chat, msg)
            set_chat_state(game, chat)
            self._set_state(self.state.with_updates(header_logo_game=game, status_message="note saved"))
            return

        if buf.strip().lower() in {"/cancel", "/abbrechen", "/stop"}:
            chat["ai_typing"] = False
            chat["ai_pending_msg_channel"] = None
            game["tutor_ask_answered"] = True
            set_chat_state(game, chat)
            try:
                hub_url = str(game.get("hub_url") or "")
                snake_id = str(game.get("local_snake_id") or "")
                snake_token = str(game.get("local_snake_token") or "")
                if hub_url and snake_id and snake_token:
                    req = urllib.request.Request(
                        f"{hub_url}/snakes/{snake_id}/chat/cancel",
                        data=b"{}",
                        method="POST",
                    )
                    req.add_header("Content-Type", "application/json")
                    req.add_header("X-Snake-Token", snake_token)
                    urllib.request.urlopen(req, timeout=3)
            except Exception:
                pass
            cancel_msg = make_message(
                channel_id=ch_id, channel_type=ch_type,
                sender_id="system", sender_kind="system",
                text="[TUI] Anfrage abgebrochen.", visibility="local_only",
                delivery_state="received",
            )
            append_message(chat, cancel_msg)
            set_chat_state(game, chat)
            self._set_state(self.state.with_updates(header_logo_game=game, status_message="anfrage abgebrochen"))
            return

        if ch_type == "ai":
            msg = make_message(
                channel_id=ch_id, channel_type=ch_type,
                sender_id=local_id, sender_kind="user",
                text=buf, visibility="ai_context",
                delivery_state="sent",
            )
            append_message(chat, msg)
            game["tutor_ask_question"] = buf
            game["tutor_ask_at"] = time.monotonic()
            game["tutor_ask_section_id"] = self.state.section_id
            timeout_s = self._chat_ask_timeout_seconds()
            game["tutor_ask_timeout_s"] = timeout_s
            game["tutor_ask_deadline_at"] = float(game["tutor_ask_at"]) + timeout_s
            game["tutor_ask_answered"] = False
            game["_ask_submitted"] = False
            game["active"] = True
            game["alive"] = True
            chat["ai_typing"] = True
            chat["ai_pending_msg_channel"] = ch_id
            set_chat_state(game, chat)
            self._set_state(self.state.with_updates(header_logo_game=game, status_message=f"ask: {buf[:40]}"))
            return

        action = "send_hub"
        notes_released = bool(chat.get("notes_context_released"))
        msg = make_message(
            channel_id=ch_id, channel_type=ch_type,
            sender_id=local_id, sender_kind="user",
            text=buf,
            target_ids=[p for p in (ch.get("participants") or []) if p != local_id] if ch_type == "direct" else [],
            delivery_state="queued",
        )
        decision = check_policy(msg, action, notes_context_released=notes_released)
        audit(decision)
        if decision["decision"] == "deny":
            sys_msg = make_message(
                channel_id=ch_id, channel_type=ch_type,
                sender_id="system", sender_kind="system",
                text=system_message_for_deny(decision), visibility="system",
                delivery_state="received",
            )
            append_message(chat, sys_msg)
            msg["delivery_state"] = "blocked"
            msg["policy_decision_ref"] = decision.get("decision_ref")
        append_message(chat, msg)

        if decision["decision"] == "allow" and self._chat_transport is not None:
            self._chat_transport.enqueue(msg)

        set_chat_state(game, chat)
        self._set_state(self.state.with_updates(header_logo_game=game))

    # ── E01: Notes ops from command ───────────────────────────────────────────

    def _process_notes_ops(self, game: dict[str, Any]) -> None:
        from client_surfaces.operator_tui.snake_notes import (
            load_notes, pin_note, unpin_note, delete_note, search_notes, rewrite_notes, visible_notes,
        )
        from client_surfaces.operator_tui.chat_state import get_chat_state, set_chat_state

        changed = False
        notes = load_notes()

        pin_id = str(game.pop("notes_pin_id", "") or "")
        unpin_id = str(game.pop("notes_unpin_id", "") or "")
        delete_id = str(game.pop("notes_delete_id", "") or "")
        search_q = str(game.pop("notes_search_query", "") or "")

        if pin_id:
            if pin_note(notes, pin_id):
                rewrite_notes(notes)
                changed = True
        if unpin_id:
            if unpin_note(notes, unpin_id):
                rewrite_notes(notes)
                changed = True
        if delete_id:
            if delete_note(notes, delete_id):
                rewrite_notes(notes)
                changed = True

        if changed or search_q:
            visible = search_notes(notes, search_q) if search_q else visible_notes(notes)
            chat = get_chat_state(game)
            ch = (chat.get("channels") or {}).get("notes:self")
            if isinstance(ch, dict):
                from client_surfaces.operator_tui.chat_state import make_message
                synced: list[dict[str, Any]] = []
                for n in visible[-200:]:
                    synced.append(make_message(
                        channel_id="notes:self", channel_type="notes",
                        sender_id=str(game.get("local_snake_id") or "s1"),
                        sender_kind="user",
                        text=str(n.get("text") or ""),
                        visibility="local_only",
                        delivery_state="sent",
                    ))
                ch["messages"] = synced
            set_chat_state(game, chat)

    # ── E05: Notes channel init ───────────────────────────────────────────────

    def _init_notes_channel(self) -> None:
        try:
            from client_surfaces.operator_tui.snake_notes import load_notes, visible_notes
            from client_surfaces.operator_tui.chat_state import (
                get_chat_state, set_chat_state, make_message, default_chat_state,
            )
            game = dict(self.state.header_logo_game or {})
            local_id = str(game.get("local_snake_id") or "s1")
            chat = get_chat_state(game)
            if "notes:self" not in (chat.get("channels") or {}):
                chat = default_chat_state(local_id)
            notes = load_notes()
            visible = visible_notes(notes)
            ch = (chat.get("channels") or {}).get("notes:self")
            if isinstance(ch, dict):
                synced = []
                for n in visible[-200:]:
                    synced.append(make_message(
                        channel_id="notes:self", channel_type="notes",
                        sender_id=local_id, sender_kind="user",
                        text=str(n.get("text") or ""), visibility="local_only",
                        delivery_state="sent",
                    ))
                ch["messages"] = synced
            set_chat_state(game, chat)
            self.state = self.state.with_updates(header_logo_game=game)
        except Exception:
            pass

    # ── E03: Chat transport tick ──────────────────────────────────────────────

    def _tick_chat(self, game: dict[str, Any], now: float) -> None:
        if self._chat_transport is not None:
            try:
                self._chat_transport.tick(now)
            except Exception:
                pass
        if game.pop("chat_retry_requested", False) and self._chat_transport is not None:
            try:
                self._chat_transport.retry_failed()
            except Exception:
                pass
        if self._chat_transport is not None:
            try:
                from client_surfaces.operator_tui.chat_state import get_chat_state, set_chat_state
                chat = get_chat_state(game)
                outbox = self._chat_transport.outbox_snapshot()
                outbox_by_id = {m.get("id"): m for m in outbox}
                for ch in (chat.get("channels") or {}).values():
                    for msg in (ch.get("messages") or []):
                        mid = msg.get("id")
                        if mid in outbox_by_id:
                            msg["delivery_state"] = outbox_by_id[mid].get("delivery_state", msg["delivery_state"])
                set_chat_state(game, chat)
            except Exception:
                pass

    def _on_chat_messages_received(self, messages: list[dict[str, Any]]) -> None:
        try:
            game = dict(self.state.header_logo_game or {})
            from client_surfaces.operator_tui.chat_state import (
                get_chat_state, set_chat_state, append_message, make_message, ChannelType,
            )
            chat = get_chat_state(game)
            for raw in messages:
                ch_type = str(raw.get("channel_type") or "room")
                if ch_type not in {"room", "direct", "system"}:
                    continue
                ch_id = str(raw.get("channel_id") or "room:main")
                msg = make_message(
                    channel_id=ch_id, channel_type=ch_type,
                    sender_id=str(raw.get("sender_id") or "?"),
                    sender_kind=str(raw.get("sender_kind") or "user"),
                    text=str(raw.get("text") or ""),
                    delivery_state="received",
                )
                if raw.get("id"):
                    msg["id"] = str(raw["id"])
                append_message(chat, msg)
            set_chat_state(game, chat)
            self.state = self.state.with_updates(header_logo_game=game)
        except Exception:
            pass
