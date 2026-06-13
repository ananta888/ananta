"""Message formatting, markdown rendering, code highlighting for ChatMixin.

Mixin extracted from chat_mixin.py (SPLIT-024). Contains all AI response
formatting, streaming, local knowledge fallback, and tutorial event methods.
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

from client_surfaces.operator_tui.chat_long_message import configure_middle_view_for_message
from client_surfaces.operator_tui.keybindings_config import display_for_action

if TYPE_CHECKING:
    pass

_TUTORIAL_AI_KNOWLEDGE: tuple[str, ...] = (
    f"TUI: Focus [{display_for_action('cycle_focus_or_channel', 'Ctrl+W')}], Command [:], "
    f"Snake [{display_for_action('toggle_snake_mode', 'Ctrl+S')}], "
    f"Hilfe [{display_for_action('help', 'Ctrl+Y')}].",
    "Snake: B frame-mode, X Rahmen, C copy, V replace (nur command line).",
    "Chat: E04 Notes-Kanal, AI-Kanal, :ask Frage stellt AI eine Frage.",
    "Goal: ananta goal create 'Aufgabe' startet autonomen Workflow.",
    "Worker: ananta worker list zeigt aktive Worker.",
    "Hub: ananta hub status zeigt API-Status.",
    "Section: Tab wechselt Sektion, Enter öffnet Detail.",
    "Artifacts: Dateien/Outputs erscheinen im Artifact-Panel.",
)

_SNAKE_ASK_RETRIEVAL_CONFIG_KEYS: tuple[str, ...] = (
    "chat_retrieval_profile",
    "chat_retrieval_domain_hint",
    "chat_codecompass_trigger_mode",
    "chat_code_questions_repo_first",
    "chat_architecture_analysis_mode",
    "chat_use_codecompass",
    "chat_include_local_project",
    "chat_include_wikipedia",
    "chat_include_task_memory",
    "chat_source_pack_id",
)


class ChatMessageFormatterMixin:
    """Mixin providing message formatting, streaming, and AI response methods.

    Designed to be used alongside ChatHistoryManagerMixin via ChatMixin.
    """

    def _chat_ask_timeout_seconds(self) -> float:
        game = dict(self.state.header_logo_game or {})
        full_scan_timeout = game.get("chat_full_scan_timeout_s")
        arch_mode = str(game.get("chat_architecture_analysis_mode") or "auto")
        if arch_mode == "full_scan" and full_scan_timeout:
            try:
                return max(60.0, float(full_scan_timeout))
            except (TypeError, ValueError):
                pass
        configured = game.get("chat_ask_timeout_s")
        if isinstance(configured, (int, float)):
            return max(3.0, float(configured))
        if isinstance(configured, str) and configured.strip():
            try:
                return max(3.0, float(configured.strip()))
            except ValueError:
                pass
        raw = str(
            os.environ.get("ANANTA_TUI_CHAT_ASK_TIMEOUT")
            or os.environ.get("ANANTA_TUI_SNAKE_AI_TIMEOUT")
            or "45"
        ).strip()
        try:
            value = float(raw)
        except ValueError:
            value = 45.0
        return max(3.0, value)

    # ── E04: AI chat response sync ────────────────────────────────────────────

    def _tick_chat_ai_response(self, game: dict[str, Any]) -> None:
        if not bool(game.get("tutor_ask_answered")):
            return
        answer = str(game.get("tutor_ask_answer") or "")
        channel_id = str((game.get("chat_state") or {}).get("ai_pending_msg_channel") or "ai:tutor")
        if not answer or not channel_id:
            return
        if bool(game.get("_chat_ai_answer_posted")):
            return
        game["_chat_ai_answer_posted"] = True
        is_error = answer.startswith("\u26a0")
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
            configure_middle_view_for_message(
                game,
                ai_msg | {"text": answer},
                channel_id=channel_id,
                streaming=False,
                activate_view=True,
                plain_text=True,
            )
            chat.pop("ai_pending_msg_channel", None)
            set_chat_state(game, chat)
            game.pop("tutor_ask_deadline_at", None)
        except Exception:
            pass

    # ── T02.03: :ask command processing ──────────────────────────────────────

    def _poll_tutor_ask_result(self, game: dict[str, object]) -> None:
        question = str(game.get("tutor_ask_question") or "")
        partial = str(getattr(self, "_llm_streaming_partial", "") or "")
        if partial:
            game["llm_streaming_partial"] = partial
            chat_state = game.get("chat_state") if isinstance(game.get("chat_state"), dict) else {}
            channel_id = str(chat_state.get("ai_pending_msg_channel") or "ai:tutor")
            configure_middle_view_for_message(
                game,
                {"id": "streaming", "sender_id": "s-ai", "sender_kind": "ai", "text": partial},
                channel_id=channel_id,
                streaming=True,
                activate_view=True,
                plain_text=True,
            )
        if not question or bool(game.get("tutor_ask_answered")):
            return
        timeout_s = self._chat_ask_timeout_seconds()
        if not isinstance(game.get("tutor_ask_timeout_s"), (int, float)):
            game["tutor_ask_timeout_s"] = timeout_s
        ask_started_at = float(game.get("tutor_ask_at") or time.monotonic())
        deadline = float(game.get("tutor_ask_deadline_at") or (ask_started_at + float(game.get("tutor_ask_timeout_s") or timeout_s)))
        if self._tutor_ask_future is not None and not self._tutor_ask_future.done() and time.monotonic() >= deadline:
            try:
                self._tutor_ask_future.cancel()
            except Exception:
                pass
            self._tutor_ask_future = None
            game.pop("llm_streaming_partial", None)
            setattr(self, "_llm_streaming_partial", "")
            game["tutor_ask_answered"] = True
            game["tutor_ask_answer"] = "\u26a0 Anfrage-Timeout erreicht (keine Antwort innerhalb der Maximaldauer)."
            game["_chat_ai_answer_posted"] = False
            game["_ask_submitted"] = False
            return
        if self._tutor_ask_future is None or self._tutor_ask_future.done():
            if not bool(game.get("_ask_submitted")):
                game["_ask_submitted"] = True
                depth = self._tutor_depth_mode
                hints = self._load_codecompass_hints(now=time.monotonic())
                rag_context = self._load_rag_helper_context(now=time.monotonic())
                question_tokens = [
                    t for t in re.findall(r"[a-z0-9_./-]+", question.lower()) if len(t) >= 2
                ][:32]
                memory = self._build_chat_memory(game, current_question=question)
                self._tutor_ask_future = self._tutor_ask_executor.submit(
                    self._resolve_ask_question, question,
                    depth=depth, hints=hints, rag_context=rag_context,
                    question_tokens=question_tokens, memory=memory,
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
            self._trigger_rolling_summary_update(game, question=question, answer=answer)

    def _resolve_ask_question(
        self,
        question: str,
        *,
        depth: str,
        hints: list[str],
        rag_context: list[str],
        question_tokens: list[str] | None = None,
        prior_messages: list[dict] | None = None,
        memory: "object | None" = None,
    ) -> str:
        import time as _time_mod
        from client_surfaces.operator_tui.chat_memory import resolve_memory_settings
        from client_surfaces.operator_tui.chat_prompt_builder import ChatPromptBuilder

        t_start = _time_mod.perf_counter()
        game = dict(self.state.header_logo_game or {})
        from client_surfaces.operator_tui.chat_state import (
            get_chat_state, get_effective_chat_settings, active_session_id,
        )
        chat = get_chat_state(game)
        eff_settings = get_effective_chat_settings(chat, game)
        game = dict(game)
        for k, v in eff_settings.items():
            if k.startswith("chat_") and v is not None and v != "":
                game[k] = v
        mem_settings = resolve_memory_settings(game)

        chat_top_k_raw = game.get("chat_rag_top_k")
        try:
            chat_top_k = int(chat_top_k_raw) if chat_top_k_raw is not None else int(os.environ.get("ANANTA_TUI_CHAT_RAG_TOP_K", "24"))
        except (TypeError, ValueError):
            chat_top_k = 24
        chat_top_k = max(8, min(120, chat_top_k))

        question_rag = self._rag_context_for_question(question, question_tokens=question_tokens, top_k=chat_top_k)
        codecompass_refs = self._chat_codecompass_context_for_question(question=question)

        seen: set[str] = set()
        merged: list[str] = []
        for item in question_rag + rag_context:
            key = item[:60]
            if key not in seen:
                seen.add(key)
                merged.append(item)

        active_excerpt = self._build_active_target_excerpt()

        if memory is not None:
            from client_surfaces.operator_tui.chat_memory import ChatMemoryContext
            mem_ctx = memory
        else:
            from client_surfaces.operator_tui.chat_memory import ChatMemoryContext
            mem_ctx = ChatMemoryContext(
                recent_turns=[],
                rolling_summary="",
                active_target_excerpt=active_excerpt,
                codecompass_refs=codecompass_refs[:8],
                rag_snippets=(merged + hints)[:chat_top_k],
            )

        if hasattr(mem_ctx, "codecompass_refs") and not mem_ctx.codecompass_refs:
            object.__setattr__(mem_ctx, "codecompass_refs", codecompass_refs[:8]) if hasattr(mem_ctx, "__dataclass_fields__") else None
        if not mem_ctx.active_target_excerpt and active_excerpt:
            pass

        context_chars_raw = game.get("chat_context_chars")
        try:
            context_budget = int(context_chars_raw) if context_chars_raw is not None else 3000
        except (TypeError, ValueError):
            context_budget = 3000
        context_budget = max(500, min(20000, context_budget))

        sess_prompt = str(eff_settings.get("chat_system_prompt") or "").strip()
        env_prompt = str(os.environ.get("ANANTA_TUI_CHAT_SYSTEM_PROMPT") or "").strip()
        system_prompt = sess_prompt or env_prompt

        builder = ChatPromptBuilder(
            question=question,
            depth=depth,
            memory=mem_ctx,
            context_budget=context_budget,
            max_turns_chars=mem_settings["history_chars"],
            system_template=system_prompt,
        )
        build_result = builder.build()

        backend = str(
            game.get("chat_backend")
            or os.environ.get("ANANTA_TUI_CHAT_BACKEND")
            or "lmstudio"
        ).strip().lower()
        endpoint = str(getattr(self.state, "endpoint", "") or "").strip().lower()
        env_backend = str(os.environ.get("ANANTA_TUI_CHAT_BACKEND") or "").strip().lower()
        if not env_backend and (":1234" in endpoint or "lmstudio" in endpoint):
            backend = "lmstudio"
        fallback_policy = mem_settings["backend_fallback"]
        used_path = backend
        fallback_reason = ""

        def _record_diagnostics(path: str, latency_ms: float, fallback: str = "") -> None:
            try:
                g = dict(self.state.header_logo_game or {})
                g["last_chat_backend_used"] = backend
                g["last_chat_backend_path"] = path
                g["last_chat_latency_ms"] = round(latency_ms, 1)
                g["last_chat_fallback_reason"] = fallback
                g["last_chat_memory_status"] = {
                    "history_used": bool(mem_ctx.recent_turns),
                    "summary_used": bool(mem_ctx.rolling_summary),
                    "codecompass_used": bool(codecompass_refs),
                    "rag_count": len(merged),
                    "sections": build_result.included_sections,
                }
                self._set_state(self.state.with_updates(header_logo_game=g))
            except Exception:
                pass

        if backend in {"lmstudio", "local", "openai"}:
            answer = self._tutorial_ai_llm_ask(
                question=question,
                context_text="",
                depth=depth,
                prior_messages=build_result.messages[1:-1],
                _messages_override=build_result.messages,
            )
            elapsed = (_time_mod.perf_counter() - t_start) * 1000
            _record_diagnostics("llm_direct", elapsed)
            return answer

        if backend in {"opencode", "hermes"}:
            worker_answer = self._tutorial_ai_worker_chat_ask(
                question=question,
                context_text=build_result.prompt_text,
                depth=depth,
                provider=backend,
                prior_messages=build_result.messages[1:-1],
            )
            if worker_answer:
                elapsed = (_time_mod.perf_counter() - t_start) * 1000
                _record_diagnostics(f"propose/{backend}", elapsed)
                return worker_answer
            used_path = f"propose/{backend}"
            fallback_reason = f"{backend} empty response"

        if backend in {"ananta-worker", "worker", "hub", "default", "auto"} or (backend in {"opencode", "hermes"} and not fallback_reason):
            endpoint_norm = str(self.state.endpoint or "http://localhost:5000").rstrip("/")
            if not (endpoint_norm.endswith("/v1") or ":1234" in endpoint_norm):
                answered = False
                ask_timeout = self._chat_ask_timeout_seconds()
                if mem_settings["pass_memory_to_worker"]:
                    try:
                        _configured_model = str(game.get("chat_backend_model") or "").strip()
                        _v2_dict = dict(build_result.worker_v2_payload)
                        # Keep repository grounding in the Hub. Sending the
                        # TUI-built context would make /snake/ask skip Hub RAG
                        # and hide real workspace files from the answer path.
                        _v2_dict["context"] = ""
                        if _configured_model:
                            _v2_dict["model"] = _configured_model
                        retrieval_config: dict[str, object] = {}
                        for _cfg_key in _SNAKE_ASK_RETRIEVAL_CONFIG_KEYS:
                            if _cfg_key in game:
                                _cfg_value = game.get(_cfg_key)
                                if isinstance(_cfg_value, (str, bool)):
                                    retrieval_config[_cfg_key] = _cfg_value
                        if retrieval_config:
                            _v2_dict["retrieval_config"] = retrieval_config
                        try:
                            _rk = int(game.get("chat_rag_top_k") or 0)
                            if _rk > 0:
                                _v2_dict["rag_top_k"] = max(8, min(120, _rk))
                        except (TypeError, ValueError):
                            pass
                        try:
                            _ac = int(game.get("chat_answer_chars") or 0)
                            if _ac > 0:
                                _v2_dict["answer_chars"] = _ac
                        except (TypeError, ValueError):
                            pass
                        _overflow_policy = str(game.get("chat_answer_overflow_policy") or "").strip().lower()
                        if _overflow_policy in {"allow", "summarize", "truncate"}:
                            _v2_dict["answer_overflow_policy"] = _overflow_policy
                        if "chat_never_truncate_answers" in game:
                            _v2_dict["never_truncate_answers"] = bool(game.get("chat_never_truncate_answers"))
                        try:
                            _mt = int(game.get("chat_max_tokens") or 0)
                            if _mt > 0:
                                _v2_dict["max_tokens"] = _mt
                        except (TypeError, ValueError):
                            pass
                        try:
                            _cc = int(game.get("chat_context_chars") or 0)
                            if _cc > 0:
                                _v2_dict["context_chars"] = _cc
                        except (TypeError, ValueError):
                            pass
                        v2_payload = _json_mod.dumps(_v2_dict).encode()
                        req = urllib.request.Request(
                            f"{endpoint_norm}/snake/ask",
                            data=v2_payload,
                            headers={"Content-Type": "application/json"},
                            method="POST",
                        )
                        with urllib.request.urlopen(req, timeout=ask_timeout) as resp:
                            data = _json_mod.loads(resp.read().decode())
                            answer = str(data.get("answer") or data.get("text") or "")
                            # CRPS-007: capture the retrieval profile trace
                            from client_surfaces.operator_tui.chat_mixin import _capture_snake_ask_trace
                            _capture_snake_ask_trace(game, data)
                            if answer:
                                elapsed = (_time_mod.perf_counter() - t_start) * 1000
                                _record_diagnostics("worker_v2", elapsed)
                                return answer[: self._chat_answer_char_limit()]
                            answered = True
                    except urllib.error.HTTPError as exc:
                        if exc.code == 400:
                            fallback_reason = "worker rejected v2 payload"
                        else:
                            fallback_reason = f"worker HTTP {exc.code}"
                    except Exception as exc:
                        fallback_reason = str(exc)[:60]

                if not answered:
                    try:
                        v1_payload = _json_mod.dumps({"question": question, "context": build_result.prompt_text[:3000], "depth": depth}).encode()
                        req = urllib.request.Request(
                            f"{endpoint_norm}/snake/ask",
                            data=v1_payload,
                            headers={"Content-Type": "application/json"},
                            method="POST",
                        )
                        with urllib.request.urlopen(req, timeout=ask_timeout) as resp:
                            data = _json_mod.loads(resp.read().decode())
                            answer = str(data.get("answer") or data.get("text") or "")
                            if answer:
                                elapsed = (_time_mod.perf_counter() - t_start) * 1000
                                _record_diagnostics("worker_v1", elapsed, fallback_reason)
                                return answer[: self._chat_answer_char_limit()]
                    except Exception as exc2:
                        fallback_reason = f"{fallback_reason}; v1: {str(exc2)[:40]}"

        elapsed = (_time_mod.perf_counter() - t_start) * 1000
        if fallback_policy == "none":
            _record_diagnostics(used_path, elapsed, fallback_reason)
            return f"[Chat nicht verf\u00fcgbar: {fallback_reason or 'kein Backend'}]"
        if fallback_policy == "local_knowledge":
            _record_diagnostics("local_knowledge", elapsed, fallback_reason)
            return self._local_knowledge_answer(question)
        answer = self._tutorial_ai_llm_ask(
            question=question,
            context_text="",
            depth=depth,
            prior_messages=[],
            _messages_override=build_result.messages,
        )
        _record_diagnostics("llm_fallback", elapsed, fallback_reason)
        return answer

    def _tutorial_ai_worker_chat_ask(
        self,
        *,
        question: str,
        context_text: str,
        depth: str,
        provider: str,
        prior_messages: list[dict] | None = None,
    ) -> str:
        base_url = str(self.state.endpoint or os.environ.get("ANANTA_BASE_URL") or "http://localhost:5000").strip()
        if not base_url:
            return ""
        timeout_seconds = max(0.8, min(14.0, float(os.environ.get("ANANTA_TUI_SNAKE_AI_TIMEOUT", "3.0"))))
        game = dict(self.state.header_logo_game or {})
        model = str(game.get("chat_backend_model") or os.environ.get("ANANTA_TUI_CHAT_MODEL") or os.environ.get("ANANTA_TUI_SNAKE_AI_MODEL") or "").strip()
        prior_section = ""
        if prior_messages:
            parts = []
            for m in prior_messages[-6:]:
                role = "User" if m.get("role") == "user" else "Assistant"
                content = str(m.get("content") or "")[:300]
                parts.append(f"{role}: {content}")
            if parts:
                prior_section = "[Letzte Nachrichten]\n" + "\n".join(parts) + "\n\n"
        prompt = (
            f"Depth: {depth}\n"
            "You are AI-snake chat assistant for Ananta.\n"
            "Answer the user directly in max 5 short sentences.\n"
            f"{prior_section}"
            f"Context:\n{context_text[:3500]}\n"
            f"User question:\n{question}\n"
        )
        payload: dict[str, object] = {"prompt": prompt, "temperature": 0.3, "provider": provider}
        if model:
            payload["model"] = model
        headers = {"Content-Type": "application/json"}
        token = str(os.environ.get("ANANTA_TUI_SNAKE_AI_WORKER_TOKEN", "")).strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(
            url=base_url.rstrip("/") + "/step/propose",
            data=_json_mod.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                raw = response.read().decode("utf-8", errors="replace")
            parsed = _json_mod.loads(raw)
        except (urllib.error.URLError, TimeoutError, OSError, _json_mod.JSONDecodeError):
            return ""
        data = parsed.get("data") if isinstance(parsed, dict) and isinstance(parsed.get("data"), dict) else parsed
        if not isinstance(data, dict):
            return ""
        text = str(data.get("reason") or data.get("raw") or data.get("answer") or "").strip()
        return " ".join(text.split())[: self._chat_answer_char_limit()]

    def _tutorial_ai_llm_ask(
        self,
        *,
        question: str,
        context_text: str,
        depth: str,
        prior_messages: list[dict] | None = None,
        _messages_override: list[dict] | None = None,
    ) -> str:
        api_base, model, api_token = self._get_llm_api_config()
        if not api_base:
            return self._local_knowledge_answer(question)

        game = dict(self.state.header_logo_game or {})
        context_chars_raw = game.get("chat_context_chars")
        max_tokens_raw = game.get("chat_max_tokens")
        try:
            context_chars = int(context_chars_raw) if context_chars_raw is not None else int(os.environ.get("ANANTA_TUI_CHAT_CONTEXT_CHARS", "3000"))
        except (TypeError, ValueError):
            context_chars = 3000
        context_chars = max(500, min(20000, context_chars))
        try:
            max_tokens = int(max_tokens_raw) if max_tokens_raw is not None else int(os.environ.get("ANANTA_TUI_CHAT_MAX_TOKENS", "400"))
        except (TypeError, ValueError):
            max_tokens = 400
        max_tokens = max(100, min(8000, max_tokens))
        timeout = max(1.0, min(60.0, float(os.environ.get("ANANTA_TUI_SNAKE_AI_TIMEOUT", "8.0"))))

        depth_instruction = {
            "overview": "Antworte in 2-3 S\u00e4tzen.",
            "deep": "Antworte in 3-4 S\u00e4tzen mit einem konkreten Beispiel oder Codepfad.",
            "expert": "Antworte technisch pr\u00e4zise mit Dateipfaden oder API-Referenzen wenn m\u00f6glich.",
        }.get(depth, "Antworte pr\u00e4zise und hilfreich.")

        project_name = Path.cwd().name
        default_system = (
            f"Du bist ein hilfreicher Assistent f\u00fcr das Projekt {project_name}.\n"
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

        try:
            from agent.services.agent_profile_service import get_agent_profile_service
            _snake_profile = get_agent_profile_service().resolve_by_profile_id("ai_snake_chat")
            if _snake_profile.profile_agents_content:
                _profile_header = _snake_profile.profile_agents_content.strip()
                system_content = f"{_profile_header}\n\n---\n\n{system_content}"
        except Exception:
            pass

        if _messages_override:
            messages = list(_messages_override)
        else:
            messages = [{"role": "system", "content": system_content}]
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

        _game_streaming = game.get("chat_streaming")
        if _game_streaming is None:
            stream_enabled = str(os.environ.get("ANANTA_TUI_CHAT_STREAMING", "1")).strip().lower() not in {"0", "false", "no", "off"}
        else:
            stream_enabled = bool(_game_streaming)
        timeout = self._chat_ask_timeout_seconds() if not stream_enabled else timeout
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
                _char_limit = self._chat_answer_char_limit()
                if stream_enabled and "text/event-stream" in content_type.lower():
                    streamed = self._read_chat_stream_response(resp)
                    if streamed:
                        return streamed[:_char_limit]
                    return "\u26a0 LLM Streaming-Fehler: keine Antwort erhalten"
                raw_response = resp.read().decode("utf-8", errors="replace")
                if stream_enabled:
                    streamed = self._read_chat_stream(raw_response)
                    if streamed:
                        return streamed[:_char_limit]
                data = _json_mod.loads(raw_response)
                choices = data.get("choices") or []
                if choices:
                    return str(choices[0].get("message", {}).get("content", "")).strip()[:_char_limit]
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:80]
            except Exception:
                detail = str(exc.reason or "")[:80]
            return f"\u26a0 LLM HTTP {exc.code}: {detail}"
        except urllib.error.URLError as exc:
            reason = str(exc.reason) if hasattr(exc, "reason") else str(exc)
            return f"\u26a0 LMStudio nicht erreichbar: {reason[:80]}. Setze ANANTA_TUI_SNAKE_AI_API_BASE_URL."
        except TimeoutError:
            return f"\u26a0 LMStudio Timeout ({timeout:.0f}s). Erh\u00f6he ANANTA_TUI_SNAKE_AI_TIMEOUT f\u00fcr gro\u00dfe Modelle."
        except Exception as exc:
            return f"\u26a0 LLM Fehler: {str(exc)[:80]}"

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
                    setattr(self, "_llm_streaming_partial", "".join(chunks))
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
                    setattr(self, "_llm_streaming_partial", "".join(chunks))
        except Exception:
            if chunks:
                chunks.append("\n\u26a0 Streaming-Fehler")
        finally:
            setattr(self, "_llm_streaming_partial", "")
        return "".join(chunks).strip()

    def _local_knowledge_answer(self, question: str) -> str:
        from client_surfaces.operator_tui.tutorial_ai_mixin import _score_rag_record
        tokens = [t for t in re.findall(r"[a-z0-9_]+", question.lower()) if len(t) > 3]
        scored = sorted(
            ((f, _score_rag_record(f.lower(), tokens)) for f in _TUTORIAL_AI_KNOWLEDGE),
            key=lambda x: x[1],
            reverse=True,
        )
        best = [f for f, s in scored[:2] if s > 0]
        if best:
            combined = " \u2014 ".join(best)
            return combined[:300]
        return scored[0][0] if scored else (
            f"TUI: [{display_for_action('cycle_focus_or_channel', 'Ctrl+W')}] Focus, [:] Command, "
            f"[{display_for_action('toggle_snake_mode', 'Ctrl+S')}] Snake, "
            f"[{display_for_action('help', 'Ctrl+Y')}] Hilfe."
        )

    # ── E04.T04: tutorial event processing ───────────────────────────────────

    def _fire_tutorial_event(self, game: dict[str, object], event: str) -> None:
        self._snake_last_event_fired = event
        self._snake_idle_since = 0.0
        event_labels = {
            "tutorial_toggled": "Tutorial-AI umgeschaltet",
            "snake_paused": "Snake pausiert",
            "any_key": "Snake fortgesetzt",
            "food_eaten": "Food aufgenommen",
            "collision_wall": "Kollision mit Wand",
            "collision_self": "Selbstkollision",
            "section_visited": "Bereich besucht",
            "ask_command_used": "Ask-Command verwendet",
        }
        self._append_ai_monitor_log(game, event=event, label=event_labels.get(event, event))

    def _append_ai_monitor_log(self, game: dict[str, object], *, event: str, label: str) -> None:
        rows_raw = game.get("ai_snake_monitor_log")
        rows: list[dict[str, object]] = [dict(item) for item in rows_raw if isinstance(item, dict)] if isinstance(rows_raw, list) else []
        now = time.time()
        normalized = str(label or event or "").strip()
        if rows:
            prev = rows[-1]
            prev_label = str(prev.get("label") or prev.get("event") or "").strip()
            prev_ts = prev.get("created_at")
            if prev_label == normalized and isinstance(prev_ts, (int, float)) and (now - float(prev_ts)) < 1.0:
                return
        rows.append({"event": str(event), "label": normalized, "created_at": now})
        game["ai_snake_monitor_log"] = rows[-40:]

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

    def _chat_answer_char_limit(self) -> int:
        game = dict(self.state.header_logo_game or {})
        raw = game.get("chat_answer_chars")
        try:
            value = int(raw) if raw is not None else int(os.environ.get("ANANTA_TUI_CHAT_ANSWER_CHARS", "12000"))
        except (TypeError, ValueError):
            value = 12000
        return max(600, min(50000, value))
