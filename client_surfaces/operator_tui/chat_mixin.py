"""ChatMixin — chat/notes/ask methods extracted from InteractiveOperatorTui.

Contains: _chat_send_message, _process_notes_ops, _init_notes_channel,
          _tick_chat, _on_chat_messages_received, _tick_chat_ai_response,
          _poll_tutor_ask_result, _resolve_ask_question, _tutorial_ai_llm_ask,
          _local_knowledge_answer, _fire_tutorial_event, _process_tutorial_event,
          _post_artifact_async

Used as a mixin: class InteractiveOperatorTui(ChatMixin, ...):
All methods use self.* — no back-reference needed beyond normal Python MRO.
"""
from __future__ import annotations

import os
import time
import urllib.request
from typing import TYPE_CHECKING, Any

_TUTORIAL_AI_KNOWLEDGE: tuple[str, ...] = (
    "TUI: Focus [Tab], Command [:], Snake [Ctrl+S], Hilfe [?].",
    "Snake: B frame-mode, X Rahmen, C copy, V replace (nur command line).",
    "Chat: E04 Notes-Kanal, AI-Kanal, :ask Frage stellt AI eine Frage.",
    "Goal: ananta goal create 'Aufgabe' startet autonomen Workflow.",
    "Worker: ananta worker list zeigt aktive Worker.",
    "Hub: ananta hub status zeigt API-Status.",
    "Section: Tab wechselt Sektion, Enter öffnet Detail.",
    "Artifacts: Dateien/Outputs erscheinen im Artifact-Panel.",
)


class ChatMixin:
    """Mixin providing chat, notes, and ask-question functionality."""

    # ── E03: Chat send ────────────────────────────────────────────────────────

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
        chat["chat_input_buffer"] = ""
        ch = get_active_channel(chat)
        if ch is None:
            set_chat_state(game, chat)
            self._set_state(self.state.with_updates(header_logo_game=game))
            return
        ch_id = str(ch.get("id") or "room:main")
        ch_type = str(ch.get("channel_type") or "room")
        local_id = str(game.get("local_snake_id") or "s1")

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
            game["tutor_ask_answered"] = False
            game["paused"] = True
            chat["ai_typing"] = True
            chat["ai_pending_msg_channel"] = ch_id
            set_chat_state(game, chat)
            self._set_state(self.state.with_updates(header_logo_game=game, status_message=f"ask: {buf[:40]}"))
            return

        # Room or direct: policy check then queue
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
        """Process pin/unpin/delete/search commands set by commands.py."""
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
        """Called from transport thread when new messages arrive."""
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

    # ── E04: AI chat response sync ────────────────────────────────────────────

    def _tick_chat_ai_response(self, game: dict[str, Any]) -> None:
        """When tutor_ask is answered, post the reply to the AI chat channel."""
        if not bool(game.get("tutor_ask_answered")):
            return
        answer = str(game.get("tutor_ask_answer") or "")
        channel_id = str((game.get("chat_state") or {}).get("ai_pending_msg_channel") or "ai:tutor")
        if not answer or not channel_id:
            return
        if bool(game.get("_chat_ai_answer_posted")):
            return
        game["_chat_ai_answer_posted"] = True
        try:
            from client_surfaces.operator_tui.chat_state import (
                get_chat_state, set_chat_state, append_message, make_message,
            )
            chat = get_chat_state(game)
            chat["ai_typing"] = False
            ai_msg = make_message(
                channel_id=channel_id, channel_type="ai",
                sender_id="s-ai", sender_kind="ai",
                text=answer, visibility="ai_context",
                delivery_state="received",
            )
            append_message(chat, ai_msg)
            chat.pop("ai_pending_msg_channel", None)
            set_chat_state(game, chat)
        except Exception:
            pass

    # ── T02.03: :ask command processing ──────────────────────────────────────

    def _poll_tutor_ask_result(self, game: dict[str, object]) -> None:
        question = str(game.get("tutor_ask_question") or "")
        if not question or bool(game.get("tutor_ask_answered")):
            return
        if self._tutor_ask_future is None or self._tutor_ask_future.done():
            if not bool(game.get("_ask_submitted")):
                game["_ask_submitted"] = True
                depth = self._tutor_depth_mode
                hints = self._load_codecompass_hints(now=time.monotonic())
                rag_context = self._load_rag_helper_context(now=time.monotonic())
                self._tutor_ask_future = self._tutor_ask_executor.submit(
                    self._resolve_ask_question, question, depth=depth,
                    hints=hints, rag_context=rag_context,
                )
        if self._tutor_ask_future is not None and self._tutor_ask_future.done():
            try:
                answer = self._tutor_ask_future.result(timeout=0.01) or "Keine Antwort erhalten."
            except Exception:
                answer = "Fehler beim Abrufen der Antwort."
            game["tutor_ask_answered"] = True
            game["tutor_ask_answer"] = answer
            game["_chat_ai_answer_posted"] = False
            game["paused"] = False
            game["last_move"] = time.monotonic()
            game["_ask_submitted"] = False
            self._tutor_ask_future = None
            self._inject_tutor_tip(game, f"[ask] {answer}", source="ask")
            self._fire_tutorial_event(game, "ask_command_used")

    def _resolve_ask_question(self, question: str, *, depth: str, hints: list[str], rag_context: list[str]) -> str:
        context_parts = hints[:6] + rag_context[:6]
        context_text = "\n".join(context_parts)
        try:
            endpoint = str(self.state.endpoint or "http://localhost:5000")
            import json as _json
            payload = _json.dumps({"question": question, "context": context_text, "depth": depth}).encode()
            req = urllib.request.Request(
                f"{endpoint}/snake/ask",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                data = _json.loads(resp.read().decode())
                answer = str(data.get("answer") or data.get("text") or "")
                if answer:
                    return answer[:400]
        except Exception:
            pass
        return self._tutorial_ai_llm_ask(question=question, context_text=context_text, depth=depth)

    def _tutorial_ai_llm_ask(self, *, question: str, context_text: str, depth: str) -> str:
        try:
            api_base = os.environ.get("ANANTA_TUI_SNAKE_AI_API_BASE_URL", "")
            if not api_base:
                return self._local_knowledge_answer(question)
            model = os.environ.get("ANANTA_TUI_SNAKE_AI_MODEL", "")
            timeout = float(os.environ.get("ANANTA_TUI_SNAKE_AI_TIMEOUT", "3.0"))
            depth_instruction = {
                "overview": "Antworte in 1-2 kurzen Sätzen (max 80 Zeichen pro Satz).",
                "deep": "Antworte in 2-3 Sätzen mit einem konkreten Beispiel.",
                "expert": "Antworte technisch mit Dateipfaden oder API-Referenzen wenn möglich.",
            }.get(depth, "Antworte in 1-2 kurzen Sätzen.")
            import json as _json
            body = _json.dumps({
                "model": model,
                "messages": [
                    {"role": "system", "content": f"Du bist tutor-ai, ein hilfreicher KI-Assistent für das Ananta Operator TUI. Kontext:\n{context_text[:800]}\n{depth_instruction}"},
                    {"role": "user", "content": question},
                ],
                "max_tokens": 160,
                "temperature": 0.4,
            }).encode()
            req = urllib.request.Request(
                f"{api_base.rstrip('/')}/chat/completions",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = _json.loads(resp.read().decode())
                choices = data.get("choices") or []
                if choices:
                    return str(choices[0].get("message", {}).get("content", "")).strip()[:400]
        except Exception:
            pass
        return self._local_knowledge_answer(question)

    def _local_knowledge_answer(self, question: str) -> str:
        q_lower = question.lower()
        for fact in _TUTORIAL_AI_KNOWLEDGE:
            words = question.lower().split()
            if any(w in fact.lower() for w in words if len(w) > 3):
                return fact
        return "Ich bin offline. Versuche :ask erneut wenn der Hub verbunden ist."

    # ── E04.T04: tutorial event processing ───────────────────────────────────

    def _fire_tutorial_event(self, game: dict[str, object], event: str) -> None:
        self._snake_last_event_fired = event
        self._snake_idle_since = 0.0

    def _process_tutorial_event(self, game: dict[str, object], event: str) -> None:
        if not event:
            return
        ts_raw = game.get("tutorial_state")
        if not isinstance(ts_raw, dict) or not ts_raw.get("active"):
            return
        try:
            from client_surfaces.operator_tui.snake_tutorial import get_current_step, advance_step, check_step_completion, make_step_artifact
            from client_surfaces.operator_tui.snake_persistence import save_tutorial_progress
            step = get_current_step(ts_raw)
            if step is None:
                return
            if check_step_completion(step, event):
                try:
                    artifact = make_step_artifact(ts_raw, step)
                    self._post_artifact_async(artifact)
                except Exception:
                    pass
                ts_new = advance_step(ts_raw)
                game["tutorial_state"] = ts_new
                name = str(ts_raw.get("name") or "")
                if name:
                    save_tutorial_progress(name, int(ts_new.get("current_step") or 0))
                if not ts_new.get("active"):
                    try:
                        from client_surfaces.operator_tui.snake_tutorial import make_completion_artifact
                        comp_art = make_completion_artifact(ts_new)
                        self._post_artifact_async(comp_art)
                    except Exception:
                        pass
        except Exception:
            pass

    def _post_artifact_async(self, artifact: dict[str, Any]) -> None:
        try:
            endpoint = str(self.state.endpoint or "http://localhost:5000")
            import json as _json
            body = _json.dumps(artifact).encode()
            req = urllib.request.Request(
                f"{endpoint}/artifacts",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            self._tutorial_async_tip_executor.submit(
                lambda: urllib.request.urlopen(req, timeout=2.0)
            )
        except Exception:
            pass
