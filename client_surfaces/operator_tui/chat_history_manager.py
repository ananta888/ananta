"""Chat history persistence, loading, cleanup for ChatMixin.

Mixin extracted from chat_mixin.py (SPLIT-024). Contains all session
management, chat memory, context building, and RAG helper methods.
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass


class ChatHistoryManagerMixin:
    """Mixin providing session management, history, and context building methods.

    Designed to be used alongside ChatMessageFormatterMixin via ChatMixin.
    """

    def _extract_prior_messages(self, game: dict, *, current_question: str = "") -> list[dict]:
        mem = self._build_chat_memory(game, current_question=current_question)
        return mem.to_prior_messages()

    def _build_chat_memory(self, game: dict, *, current_question: str = "") -> "object":
        from client_surfaces.operator_tui.chat_memory import (
            extract_memory_context,
            resolve_memory_settings,
            build_runtime_status,
        )
        settings = resolve_memory_settings(game)
        ctx = extract_memory_context(
            game,
            current_question=current_question,
            max_turns=settings["history_turns"] if settings["use_history"] else 0,
            max_chars=settings["history_chars"] if settings["use_history"] else 0,
        )
        if not settings["use_summary"]:
            ctx.rolling_summary = ""
        if settings["include_runtime_status"]:
            from client_surfaces.operator_tui.chat_memory import ChatMemoryContext
            ctx = ChatMemoryContext(
                recent_turns=ctx.recent_turns,
                rolling_summary=ctx.rolling_summary,
                active_target_excerpt=ctx.active_target_excerpt,
                codecompass_refs=ctx.codecompass_refs,
                rag_snippets=ctx.rag_snippets,
                runtime_status=build_runtime_status(game),
                metadata=ctx.metadata,
            )
        return ctx

    def _trigger_rolling_summary_update(self, game: dict, *, question: str, answer: str) -> None:
        from client_surfaces.operator_tui.chat_memory import update_rolling_summary, resolve_memory_settings
        settings = resolve_memory_settings(game)
        if not settings["use_summary"]:
            return
        try:
            update_rolling_summary(
                game,
                last_question=question,
                last_answer=answer,
                max_chars=settings["summary_chars"],
                update_every_turns=settings["summary_update_every_turns"],
            )
        except Exception:
            pass

    def _build_active_target_excerpt(self) -> str:
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
            if kind == "terminal_snapshot":
                content = str(game.get("ai_terminal_context") or "").strip()
                if content:
                    return f"Terminal-Kontext ({label or 'aktuelle Ansicht'}):\n{content[:4000]}"
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
                return f"Ausgew\u00e4hlter Kontext: {label} (kind={kind})"
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

    def _chat_codecompass_context_for_question(self, *, question: str) -> list[str]:
        game = dict(self.state.header_logo_game or {})
        enabled_raw = game.get("chat_use_codecompass")
        if isinstance(enabled_raw, bool):
            enabled = enabled_raw
        else:
            enabled = str(os.environ.get("ANANTA_TUI_CHAT_USE_CODECOMPASS", "1")).strip().lower() not in {"0", "false", "no", "off"}
        if not enabled:
            return []
        source_pack_id = str(game.get("chat_source_pack_id") or os.environ.get("ANANTA_TUI_CHAT_SOURCE_PACK") or "ananta-dev-default").strip()
        if not source_pack_id:
            return []
        include_wikipedia_raw = game.get("chat_include_wikipedia")
        if isinstance(include_wikipedia_raw, bool):
            include_wikipedia = include_wikipedia_raw
        else:
            include_wikipedia = str(os.environ.get("ANANTA_TUI_CHAT_INCLUDE_WIKIPEDIA", "0")).strip().lower() in {"1", "true", "yes", "on"}
        include_local_project_raw = game.get("chat_include_local_project")
        if isinstance(include_local_project_raw, bool):
            include_local_project = include_local_project_raw
        else:
            include_local_project = str(os.environ.get("ANANTA_TUI_CHAT_INCLUDE_LOCAL_PROJECT", "1")).strip().lower() not in {"0", "false", "no", "off"}
        try:
            from agent.sources.source_pack_service import SourcePackService
            from agent.sources.source_registry import SourceRegistry
            from agent.sources.source_snapshot_store import SourceSnapshotStore

            service = SourcePackService(registry=SourceRegistry(), snapshots=SourceSnapshotStore())
            result = service.answer_preview(
                source_pack_id=source_pack_id,
                query=question,
                include_wikipedia=include_wikipedia,
                include_local_project=include_local_project,
            )
        except Exception:
            return []
        rows = list(result.get("source_references") or [])
        if not rows:
            return []
        hints: list[str] = []
        bundle_id = str(result.get("codecompass_bundle_id") or "").strip()
        if bundle_id:
            hints.append(f"CodeCompass bundle={bundle_id}")
        for row in rows[:8]:
            if not isinstance(row, dict):
                continue
            source_id = str(row.get("source_id") or "").strip()
            trust = str(row.get("trust_level") or "").strip()
            if not source_id:
                continue
            if source_id == "local-project-context":
                hints.append("CodeCompass local-project-context (Ananta workspace)")
            else:
                hints.append(f"CodeCompass source={source_id}" + (f" trust={trust}" if trust else ""))
        return hints

    # ── Session management commands ──────────────────────────────────────

    def _handle_session_command(
        self,
        buf: str,
        *,
        chat: dict[str, Any],
        game: dict[str, Any],
        ch_id: str | None,
    ) -> bool:
        from client_surfaces.operator_tui.chat_state import (
            get_sessions, get_active_session, set_active_session,
            add_session, delete_session, get_session, make_session,
            ensure_session_channels, switch_channel, make_message,
            append_message, set_chat_state,
            clear_session_messages, clear_all_session_messages,
        )

        def _consume_buffer() -> None:
            chat["chat_input_buffer"] = ""
            chat["chat_input_cursor"] = 0
            chat["chat_input_history_index"] = None
            chat["chat_input_saved_draft"] = ""

        stripped = buf.strip().lower()
        if stripped == "/clear" or stripped == "/clear all" or stripped.startswith("/clear "):
            _consume_buffer()
            arg = buf.strip()[len("/clear"):].strip()
            target_ch_id = ch_id or str(chat.get("active_channel") or "ai:tutor")
            ch_type = "ai"
            if not arg:
                active = get_active_session(chat)
                active_id = str((active or {}).get("id") or "") if isinstance(active, dict) else ""
                cleared = clear_session_messages(chat, active_id) if active_id else False
                if cleared:
                    sys_msg = make_message(
                        channel_id=target_ch_id, channel_type=ch_type,
                        sender_id="system", sender_kind="system",
                        text=f"[TUI] Verlauf der aktiven Session '{active_id}' gel\u00f6scht.",
                        visibility="local_only", delivery_state="received",
                    )
                    append_message(chat, sys_msg)
                    status = f"clear: {active_id}"
                else:
                    status = "clear: keine aktive session"
            elif arg.lower() == "all":
                count = clear_all_session_messages(chat)
                sys_msg = make_message(
                    channel_id=target_ch_id, channel_type=ch_type,
                    sender_id="system", sender_kind="system",
                    text=f"[TUI] Verlauf aller {count} Sessions gel\u00f6scht.",
                    visibility="local_only", delivery_state="received",
                )
                append_message(chat, sys_msg)
                status = f"clear: {count} sessions"
            else:
                if clear_session_messages(chat, arg):
                    sys_msg = make_message(
                        channel_id=target_ch_id, channel_type=ch_type,
                        sender_id="system", sender_kind="system",
                        text=f"[TUI] Verlauf von Session '{arg}' gel\u00f6scht.",
                        visibility="local_only", delivery_state="received",
                    )
                    append_message(chat, sys_msg)
                    status = f"clear: {arg}"
                else:
                    status = f"clear: session '{arg}' nicht gefunden"
            set_chat_state(game, chat)
            self._set_state(self.state.with_updates(header_logo_game=game, status_message=status))
            return True

        if stripped == "/session" or stripped.startswith("/session "):
            _consume_buffer()
            arg = buf.strip()[len("/session"):].strip()
            sessions = get_sessions(chat)
            active = get_active_session(chat)
            active_id = str(active.get("id") or "") if isinstance(active, dict) else ""
            target_ch_id = ch_id or str(chat.get("active_channel") or "ai:tutor")
            ch_type = "ai"
            if not arg:
                lines = ["[TUI] Sessions:"]
                for s in sessions:
                    if not isinstance(s, dict):
                        continue
                    sid = str(s.get("id") or "?")
                    name = str(s.get("name") or sid)
                    icon = str(s.get("icon") or "\U0001f4ac")
                    marker = "*" if sid == active_id else " "
                    backend = str((s.get("settings") or {}).get("chat_backend") or "\u2014")
                    lines.append(f"  {marker} {icon} {sid:14s} {name!r:24s} backend={backend}")
                if not sessions:
                    lines.append("  (keine)")
                list_msg = make_message(
                    channel_id=target_ch_id, channel_type=ch_type,
                    sender_id="system", sender_kind="system",
                    text="\n".join(lines), visibility="local_only",
                    delivery_state="received",
                )
                append_message(chat, list_msg)
                set_chat_state(game, chat)
                self._set_state(self.state.with_updates(
                    header_logo_game=game, status_message=f"sessions: {len(sessions)}",
                ))
                return True
            parts = arg.split(maxsplit=1)
            sub = parts[0].lower()
            rest = parts[1] if len(parts) > 1 else ""
            if sub == "new":
                if not rest:
                    status = "session new: name fehlt"
                else:
                    safe_id = "".join(c if c.isalnum() or c in "-_" else "-" for c in rest.lower()).strip("-")
                    if not safe_id:
                        safe_id = f"session-{int(time.time())}"
                    base_id = safe_id
                    suffix = 2
                    while any(str(s.get("id") or "") == safe_id for s in sessions if isinstance(s, dict)):
                        safe_id = f"{base_id}-{suffix}"
                        suffix += 1
                    new_session = make_session(
                        session_id=safe_id, name=rest,
                        icon="\u2728", system_prompt="", settings={},
                    )
                    add_session(chat, new_session)
                    ensure_session_channels(chat)
                    if set_active_session(chat, safe_id):
                        switch_channel(chat, f"ai:{safe_id}", preserve_input=True)
                    set_chat_state(game, chat)
                    self._set_state(self.state.with_updates(
                        header_logo_game=game, status_message=f"session erstellt: {safe_id}",
                    ))
                    return True
            elif sub == "delete":
                if not rest:
                    status = "session delete: id fehlt"
                else:
                    target = get_session(chat, rest)
                    if not target:
                        status = f"session delete: '{rest}' nicht gefunden"
                    elif len(sessions) <= 1:
                        status = "session delete: letzter session, nicht l\u00f6schbar"
                    else:
                        delete_session(chat, rest)
                        new_active = get_active_session(chat)
                        if new_active is not None:
                            switch_channel(chat, f"ai:{str(new_active.get('id') or 'tutor')}", preserve_input=True)
                        set_chat_state(game, chat)
                        self._set_state(self.state.with_updates(
                            header_logo_game=game, status_message=f"session gel\u00f6scht: {rest}",
                        ))
                        return True
            elif sub == "rename":
                rename_parts = rest.split(maxsplit=1)
                if len(rename_parts) < 2:
                    status = "session rename: usage: /session rename <id> <name>"
                else:
                    target_id, new_name = rename_parts[0], rename_parts[1]
                    target = get_session(chat, target_id)
                    if not target:
                        status = f"session rename: '{target_id}' nicht gefunden"
                    else:
                        target["name"] = new_name
                        target["updated_at"] = time.time()
                        ensure_session_channels(chat)
                        set_chat_state(game, chat)
                        self._set_state(self.state.with_updates(
                            header_logo_game=game, status_message=f"session renamed: {target_id}",
                        ))
                        return True
            else:
                if get_session(chat, sub):
                    if set_active_session(chat, sub):
                        switch_channel(chat, f"ai:{sub}", preserve_input=True)
                        set_chat_state(game, chat)
                        self._set_state(self.state.with_updates(
                            header_logo_game=game, status_message=f"session: {sub}",
                        ))
                        return True
                    status = f"session: '{sub}' nicht gefunden"
                else:
                    status = f"session: '{sub}' nicht gefunden (versuche: list/new/delete/rename)"
            set_chat_state(game, chat)
            self._set_state(self.state.with_updates(header_logo_game=game, status_message=status))
            return True

        return False
