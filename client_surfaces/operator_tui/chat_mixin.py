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

import json as _json_mod
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
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
            game["_ask_submitted"] = False
            game["paused"] = True
            game["active"] = True
            game["alive"] = True
            game["tutorial_mode"] = True
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
        is_error = answer.startswith("⚠")
        if is_error:
            game["llm_last_error"] = answer
            game["llm_last_error_at"] = time.time()
        else:
            game.pop("llm_last_error", None)
            game.pop("llm_last_error_at", None)
        try:
            from client_surfaces.operator_tui.chat_state import (
                get_chat_state, set_chat_state, append_message, make_message,
            )
            chat = get_chat_state(game)
            chat["ai_typing"] = False
            ai_msg = make_message(
                channel_id=channel_id, channel_type="ai",
                sender_id="system" if is_error else "s-ai",
                sender_kind="system" if is_error else "ai",
                text=answer, visibility="ai_context" if not is_error else "system",
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
        partial = str(getattr(self, "_llm_streaming_partial", "") or "")
        if partial:
            game["llm_streaming_partial"] = partial
        if not question or bool(game.get("tutor_ask_answered")):
            return
        if self._tutor_ask_future is None or self._tutor_ask_future.done():
            if not bool(game.get("_ask_submitted")):
                game["_ask_submitted"] = True
                depth = self._tutor_depth_mode
                hints = self._load_codecompass_hints(now=time.monotonic())
                rag_context = self._load_rag_helper_context(now=time.monotonic())
                # R01: tokenise question itself for targeted RAG retrieval
                question_tokens = [
                    t for t in re.findall(r"[a-z0-9_./-]+", question.lower()) if len(t) >= 2
                ][:32]
                # A01: extract prior ai:tutor messages for multi-turn context
                prior_messages = self._extract_prior_messages(game, current_question=question)
                self._tutor_ask_future = self._tutor_ask_executor.submit(
                    self._resolve_ask_question, question,
                    depth=depth, hints=hints, rag_context=rag_context,
                    question_tokens=question_tokens, prior_messages=prior_messages,
                )
        if self._tutor_ask_future is not None and self._tutor_ask_future.done():
            try:
                answer = self._tutor_ask_future.result(timeout=0.01) or "Keine Antwort erhalten."
            except Exception:
                answer = "Fehler beim Abrufen der Antwort."
            game.pop("llm_streaming_partial", None)
            setattr(self, "_llm_streaming_partial", "")
            game["tutor_ask_answered"] = True
            game["tutor_ask_answer"] = answer
            game["_chat_ai_answer_posted"] = False
            game["paused"] = False
            game["last_move"] = time.monotonic()
            game["_ask_submitted"] = False
            self._tutor_ask_future = None
            self._inject_tutor_tip(game, f"[ask] {answer}", source="ask")
            self._fire_tutorial_event(game, "ask_command_used")

    def _extract_prior_messages(self, game: dict, *, current_question: str = "") -> list[dict]:
        """A01: last 6 ai:tutor messages as prior conversation turns."""
        try:
            from client_surfaces.operator_tui.chat_state import get_chat_state
            chat = get_chat_state(game)
            ai_ch = (chat.get("channels") or {}).get("ai:tutor") or {}
            all_msgs = [m for m in (ai_ch.get("messages") or []) if isinstance(m, dict)]
            result = []
            current_norm = " ".join(str(current_question or "").split())
            for m in all_msgs:
                kind = str(m.get("sender_kind") or "")
                text = str(m.get("text") or "").strip()
                if kind == "user" and current_norm and " ".join(text.split()) == current_norm:
                    continue
                if kind == "ai":
                    result.append({"role": "assistant", "content": text[:300]})
                elif kind == "user":
                    result.append({"role": "user", "content": text[:300]})
            return result[-6:]
        except Exception:
            return []

    def _build_active_target_excerpt(self) -> str:
        """A02: file excerpt or metadata for the currently selected artifact."""
        try:
            game = dict(self.state.header_logo_game or {})
            chat_raw = game.get("artifact_chat_state")
            if not isinstance(chat_raw, dict):
                return ""
            active = chat_raw.get("active_target")
            if not isinstance(active, dict):
                return ""
            label = str(active.get("label") or "")
            path_str = str(active.get("path") or "").strip()
            kind = str(active.get("kind") or "")
            if kind == "goal":
                goal_id = str(active.get("id") or active.get("goal_id") or "").strip()
                goal = self._lookup_active_goal_payload(goal_id=goal_id, label=label)
                if goal:
                    title = str(goal.get("title") or label or goal_id)
                    status = str(goal.get("status") or "")
                    desc = str(goal.get("description") or goal.get("summary") or "")
                    return f"Goal-Kontext: {title}\nStatus: {status}\nBeschreibung: {desc}"[:1500]
            if path_str:
                p = Path(path_str).expanduser()
                if not p.is_absolute():
                    p = (Path.cwd() / p).resolve()
                # Security: must be inside cwd
                try:
                    p.relative_to(Path.cwd())
                except ValueError:
                    return f"Kontext: {label}"
                if p.exists() and p.is_file():
                    try:
                        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()[:40]
                        excerpt = "\n".join(lines)[:1500]
                        return f"Datei-Kontext ({label}):\n{excerpt}"
                    except OSError:
                        pass
            if label:
                return f"Ausgewählter Kontext: {label} (kind={kind})"
        except Exception:
            pass
        return ""

    def _lookup_active_goal_payload(self, *, goal_id: str, label: str) -> dict[str, Any]:
        payloads = self.state.section_payloads or {}
        for payload in payloads.values():
            if not isinstance(payload, dict):
                continue
            for key in ("items", "goals", "rows"):
                rows = payload.get(key)
                if not isinstance(rows, list):
                    continue
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    row_id = str(row.get("id") or row.get("goal_id") or "")
                    row_title = str(row.get("title") or row.get("name") or "")
                    if (goal_id and row_id == goal_id) or (label and row_title == label):
                        return row
        return {}

    def _resolve_ask_question(
        self,
        question: str,
        *,
        depth: str,
        hints: list[str],
        rag_context: list[str],
        question_tokens: list[str] | None = None,
        prior_messages: list[dict] | None = None,
    ) -> str:
        chat_top_k = max(12, int(os.environ.get("ANANTA_TUI_CHAT_RAG_TOP_K", "24")))

        # R01: question-based RAG retrieval (runs in background thread — blocking OK)
        question_rag = self._rag_context_for_question(question, question_tokens=question_tokens, top_k=chat_top_k)

        # Merge question-RAG first (higher relevance), deduplicated with TUI-context rag
        seen: set[str] = set()
        merged: list[str] = []
        for item in question_rag + rag_context:
            key = item[:60]
            if key not in seen:
                seen.add(key)
                merged.append(item)

        # A02: prepend active artifact context
        active_excerpt = self._build_active_target_excerpt()
        context_parts = ([active_excerpt] if active_excerpt else []) + merged[:chat_top_k] + hints[:10]
        context_text = "\n".join(context_parts)

        try:
            endpoint = str(self.state.endpoint or "http://localhost:5000")
            payload = _json_mod.dumps({"question": question, "context": context_text, "depth": depth}).encode()
            req = urllib.request.Request(
                f"{endpoint}/snake/ask",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                data = _json_mod.loads(resp.read().decode())
                answer = str(data.get("answer") or data.get("text") or "")
                if answer:
                    return answer[:600]
        except Exception:
            pass
        return self._tutorial_ai_llm_ask(
            question=question,
            context_text=context_text,
            depth=depth,
            prior_messages=prior_messages or [],
        )

    def _rag_context_for_question(
        self,
        question: str,
        *,
        question_tokens: list[str] | None,
        top_k: int,
    ) -> list[str]:
        from client_surfaces.operator_tui.tutorial_ai_mixin import _load_rag_context_from_dir

        tokens = list(question_tokens or [])
        if not tokens:
            tokens = [t for t in re.findall(r"[a-z0-9_./-]+", str(question).lower()) if len(t) >= 2][:32]
        if not tokens:
            tokens = self._tutorial_relevance_tokens()
        out_dir = self._resolve_codecompass_output_dir()
        if out_dir is None:
            return []
        max_recs = max(80, min(3000, int(os.environ.get("ANANTA_TUI_SNAKE_RAG_MAX_RECORDS_PER_FILE", "800"))))
        return _load_rag_context_from_dir(out_dir, tokens, top_k, max_recs, scope_filter="full")

    def _tutorial_ai_llm_ask(
        self,
        *,
        question: str,
        context_text: str,
        depth: str,
        prior_messages: list[dict] | None = None,
    ) -> str:
        # L04: unified config via _get_llm_api_config
        api_base, model, api_token = self._get_llm_api_config()
        if not api_base:
            return self._local_knowledge_answer(question)

        # R02: configurable context window and max_tokens
        context_chars = max(0, int(os.environ.get("ANANTA_TUI_CHAT_CONTEXT_CHARS", "3000")))
        max_tokens = max(400, int(os.environ.get("ANANTA_TUI_CHAT_MAX_TOKENS", "400")))
        timeout = max(1.0, min(60.0, float(os.environ.get("ANANTA_TUI_SNAKE_AI_TIMEOUT", "8.0"))))

        depth_instruction = {
            "overview": "Antworte in 2-3 Sätzen.",
            "deep": "Antworte in 3-4 Sätzen mit einem konkreten Beispiel oder Codepfad.",
            "expert": "Antworte technisch präzise mit Dateipfaden oder API-Referenzen wenn möglich.",
        }.get(depth, "Antworte präzise und hilfreich.")

        # A03: configurable system prompt
        project_name = Path.cwd().name
        default_system = (
            f"Du bist ein hilfreicher Assistent für das Projekt {project_name}.\n"
            f"Kontext:\n{{context}}\n{{depth_instruction}}"
        )
        system_template = str(os.environ.get("ANANTA_TUI_CHAT_SYSTEM_PROMPT") or default_system).strip()
        if "{context}" not in system_template:
            system_template = default_system
            try:
                game = dict(self.state.header_logo_game or {})
                game["chat_prompt_warning"] = "ANANTA_TUI_CHAT_SYSTEM_PROMPT ohne {context}; nutze Default"
                self.state = self.state.with_updates(header_logo_game=game, status_message="chat prompt fallback: {context} fehlt")
            except Exception:
                pass
        system_content = system_template.replace("{context}", context_text[:context_chars]).replace(
            "{depth_instruction}", depth_instruction
        ).replace("{project_name}", project_name)

        # A01: build message list with prior conversation turns
        messages: list[dict] = [{"role": "system", "content": system_content}]
        # Trim prior_messages so they don't exceed half the context budget
        prior = list(prior_messages or [])
        char_budget = context_chars // 2
        used = 0
        trimmed: list[dict] = []
        for msg in reversed(prior):
            chunk = str(msg.get("content") or "")
            if used + len(chunk) > char_budget:
                break
            trimmed.insert(0, msg)
            used += len(chunk)
        messages.extend(trimmed)
        messages.append({"role": "user", "content": question})

        stream_enabled = str(os.environ.get("ANANTA_TUI_CHAT_STREAMING", "1")).strip().lower() not in {"0", "false", "no", "off"}
        try:
            body = _json_mod.dumps({
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": 0.4,
                **({"stream": True} if stream_enabled else {}),
            }).encode()
            headers: dict[str, str] = {"Content-Type": "application/json"}
            if api_token:
                headers["Authorization"] = f"Bearer {api_token}"
            req = urllib.request.Request(
                f"{api_base.rstrip('/')}/chat/completions",
                data=body,
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                content_type = ""
                try:
                    content_type = str(resp.headers.get("Content-Type") or "")
                except Exception:
                    content_type = ""
                if stream_enabled and "text/event-stream" in content_type.lower():
                    streamed = self._read_chat_stream_response(resp)
                    if streamed:
                        return streamed[:600]
                    return "⚠ LLM Streaming-Fehler: keine Antwort erhalten"
                raw_response = resp.read().decode("utf-8", errors="replace")
                if stream_enabled:
                    streamed = self._read_chat_stream(raw_response)
                    if streamed:
                        return streamed[:600]
                data = _json_mod.loads(raw_response)
                choices = data.get("choices") or []
                if choices:
                    return str(choices[0].get("message", {}).get("content", "")).strip()[:600]
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:80]
            except Exception:
                detail = str(exc.reason or "")[:80]
            return f"⚠ LLM HTTP {exc.code}: {detail}"
        except urllib.error.URLError as exc:
            # L02: surface connection errors as return value (caller posts as system message)
            reason = str(exc.reason) if hasattr(exc, "reason") else str(exc)
            return f"⚠ LMStudio nicht erreichbar: {reason[:80]}. Setze ANANTA_TUI_SNAKE_AI_API_BASE_URL."
        except TimeoutError:
            return f"⚠ LMStudio Timeout ({timeout:.0f}s). Erhöhe ANANTA_TUI_SNAKE_AI_TIMEOUT für große Modelle."
        except Exception as exc:
            return f"⚠ LLM Fehler: {str(exc)[:80]}"

    def _read_chat_stream(self, raw_response: str) -> str:
        chunks: list[str] = []
        try:
            for line in raw_response.splitlines():
                line = line.strip()
                if not line:
                    continue
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    parsed = _json_mod.loads(payload)
                except _json_mod.JSONDecodeError:
                    continue
                choices = parsed.get("choices") if isinstance(parsed, dict) else None
                if not isinstance(choices, list) or not choices:
                    continue
                delta = choices[0].get("delta") if isinstance(choices[0], dict) else None
                piece = ""
                if isinstance(delta, dict):
                    piece = str(delta.get("content") or "")
                if not piece and isinstance(choices[0], dict):
                    message = choices[0].get("message")
                    if isinstance(message, dict):
                        piece = str(message.get("content") or "")
                if piece:
                    chunks.append(piece)
                    setattr(self, "_llm_streaming_partial", "".join(chunks)[-600:])
        except Exception:
            return "".join(chunks)
        finally:
            setattr(self, "_llm_streaming_partial", "")
        return "".join(chunks).strip()

    def _read_chat_stream_response(self, resp: Any) -> str:
        chunks: list[str] = []
        try:
            while True:
                raw_line = resp.readline()
                if not raw_line:
                    break
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line or not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    parsed = _json_mod.loads(payload)
                except _json_mod.JSONDecodeError:
                    continue
                choices = parsed.get("choices") if isinstance(parsed, dict) else None
                if not isinstance(choices, list) or not choices:
                    continue
                delta = choices[0].get("delta") if isinstance(choices[0], dict) else None
                piece = str(delta.get("content") or "") if isinstance(delta, dict) else ""
                if piece:
                    chunks.append(piece)
                    setattr(self, "_llm_streaming_partial", "".join(chunks)[-600:])
        except Exception:
            if chunks:
                chunks.append("\n⚠ Streaming-Fehler")
        finally:
            setattr(self, "_llm_streaming_partial", "")
        return "".join(chunks).strip()

    def _local_knowledge_answer(self, question: str) -> str:
        # A04: score all facts, return top-2 combined
        from client_surfaces.operator_tui.tutorial_ai_mixin import _score_rag_record
        tokens = [t for t in re.findall(r"[a-z0-9_]+", question.lower()) if len(t) > 3]
        scored = sorted(
            ((f, _score_rag_record(f.lower(), tokens)) for f in _TUTORIAL_AI_KNOWLEDGE),
            key=lambda x: x[1],
            reverse=True,
        )
        # Take top-2 facts that scored > 0, or top-1 as generic fallback
        best = [f for f, s in scored[:2] if s > 0]
        if best:
            combined = " — ".join(best)
            return combined[:300]
        return scored[0][0] if scored else "TUI: [Tab] Focus, [:] Command, [Ctrl+S] Snake, [?] Hilfe."

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
