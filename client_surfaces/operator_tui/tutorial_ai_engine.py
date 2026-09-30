"""Tutorial AI engine — LLM interaction and hint generation for tutorials.

Contains: data constants and TutorialAiEngineMixin with AI/LLM methods. The
          retrieval helpers (_score_rag_record, _cosine_similarity,
          _embedding_vector_for_text, _load_codecompass_hints_from_dir,
          _load_rag_context_from_dir, _name_lookup_from_details) live in
          ``tutorial_ai_retrieval`` and are re-exported here.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from client_surfaces.operator_tui.keybindings_config import display_for_action

if TYPE_CHECKING:
    from concurrent.futures import Future, ThreadPoolExecutor

from client_surfaces.operator_tui.tutorial_ai_retrieval import (  # noqa: F401 - re-exported
    _cosine_similarity,
    _embedding_vector_for_text,
    _load_codecompass_hints_from_dir,
    _load_rag_context_from_dir,
    _name_lookup_from_details,
    _score_rag_record,
    _score_rag_record_with_embedding,
)

# ── Data constants ────────────────────────────────────────────────────────


_TUTORIAL_AI_KNOWLEDGE: tuple[str, ...] = (
    f"TUI: Focus [{display_for_action('cycle_focus_or_channel', 'Ctrl+W')}], Command [:], "
    f"Snake [{display_for_action('toggle_snake_mode', 'Ctrl+S')}], "
    f"Hilfe [{display_for_action('help', 'Ctrl+Y')}].",
    "Snake: B frame-mode, X Rahmen, C copy, V replace (nur command line).",
    "Architektur: Hub orchestriert, Worker fuehren aus; keine worker-zu-worker orchestration.",
    "Taskfluss: User -> Hub -> Task Queue -> Worker; Hub bleibt Control Plane.",
    "Betrieb: Hub/Worker getrennte Container, reproduzierbare Umgebungen.",
    "API evolution: additive, rueckwaertskompatibel, keine Big-Bang Refactors.",
)
_TUTORIAL_AI_PROMPT_TEMPLATE_DEFAULT = (
    "You are tutorial-snake guidance.\n"
    "Priority: {priority}\n"
    "User feed: {user_feed}\n"
    "Contact zone: {contact_zone}\n"
    "Respond with one immediate actionable hint (max 180 chars)."
)


# ── TutorialAiEngineMixin ────────────────────────────────────────────────


class TutorialAiEngineMixin:
    """Mixin providing AI/LLM tutorial methods."""

    def _tutorial_ai_tip(self, *, now: float) -> str:
        status = self._tutorial_status_delta_summary()
        game = self.state.header_logo_game if isinstance(getattr(self.state, "header_logo_game", None), dict) else {}
        cfg_override = game.get("ai_visual_use_codecompass")
        include_external_context = (
            bool(cfg_override)
            if isinstance(cfg_override, bool)
            else str(os.environ.get("ANANTA_TUI_VISUAL_AI_USE_CODECOMPASS", "0")).strip().lower() in {"1", "true", "yes", "on"}
        )
        hints = self._load_codecompass_hints(now=now) if include_external_context else []
        rag_context = self._load_rag_helper_context(now=now) if include_external_context else []
        if not self._tutorial_async_enabled():
            result = self._tutorial_ai_tip_sync(now=now, status=status, hints=hints, rag_context=rag_context)
            if result:
                self._tutorial_last_source = result.get("source", self._tutorial_last_source)
                self._tutorial_last_target = result.get("target", self._tutorial_last_target)
                self._tutorial_last_tip_text = result.get("text", self._tutorial_last_tip_text)
            return self._tutorial_last_tip_text

        refresh_seconds = max(2.0, min(60.0, float(os.environ.get("ANANTA_TUI_SNAKE_AI_REFRESH", "8.0"))))
        self._poll_tutorial_async_tip_result()
        if self._tutorial_async_tip_future is None and now >= self._tutorial_async_next_refresh_at:
            self._tutorial_async_next_refresh_at = now + refresh_seconds
            self._tutorial_async_tip_future = self._tutorial_async_tip_executor.submit(
                self._tutorial_ai_tip_sync,
                now=now,
                status=status,
                hints=list(hints),
                rag_context=list(rag_context),
            )
        if self._tutorial_last_tip_text:
            return self._tutorial_last_tip_text
        return "KI-Schlange analysiert UI-Delta…"

    def _tutorial_async_enabled(self) -> bool:
        enabled = str(os.environ.get("ANANTA_TUI_SNAKE_AI_ASYNC", "1")).strip().lower() in {"1", "true", "yes", "on"}
        return bool(enabled and getattr(self._app, "is_running", False))

    def _poll_tutorial_async_tip_result(self) -> None:
        future = self._tutorial_async_tip_future
        if future is None or not future.done():
            return
        result = future.result()
        self._tutorial_async_tip_future = None
        if not isinstance(result, dict):
            return
        text = str(result.get("text") or "").strip()
        if not text:
            return
        self._tutorial_last_tip_text = text
        self._tutorial_last_source = str(result.get("source") or self._tutorial_last_source)
        self._tutorial_last_target = str(result.get("target") or self._tutorial_last_target)

    def _tutorial_status_delta_summary(self) -> str:
        mode = self.state.mode.value
        focus = self.state.focus.value
        section = self.state.section_id
        selected = self.state.selected_index
        snapshot = {
            "mode": str(mode),
            "focus": str(focus),
            "section": str(section),
            "idx": str(selected),
            "state": str((self.state.panel_states or {}).get(section, "")),
        }
        previous = dict(self._tutorial_status_snapshot)
        changed = [f"{key}={value}" for key, value in snapshot.items() if previous.get(key) != value]
        self._tutorial_status_snapshot = snapshot
        if not previous:
            return f"TUI state mode={mode} focus={focus} section={section} idx={selected}."
        if not changed:
            return "TUI delta: unchanged."
        return "TUI delta: " + ", ".join(changed)

    def _tutorial_ai_tip_sync(
        self,
        *,
        now: float,
        status: str,
        hints: list[str],
        rag_context: list[str],
    ) -> dict[str, str]:
        game = dict(self.state.header_logo_game or {})
        user_feed = str(game.get("tutorial_user_feed") or game.get("message") or "").strip()
        contact_zone = str(game.get("tutorial_ai_contact_zone") or "").strip()
        artifact_overlay = self._artifact_chat_prompt_overlay(game=game)
        priority = "explain-current-position" if bool(game.get("tutorial_ai_local_contact")) else "navigation-guidance"
        template = self._resolve_tutorial_prompt_template(game)
        overlay = self._render_tutorial_prompt_overlay(
            template=template,
            priority=priority,
            user_feed=user_feed or "(none)",
            contact_zone=contact_zone or "(none)",
        )
        effective_status = f"{status}\n{overlay}\n{artifact_overlay}"
        worker_tip = self._tutorial_ai_worker_propose_message(now=now, status=effective_status, hints=hints, rag_context=rag_context)
        if worker_tip:
            self._append_artifact_chat_ai_message(game=game, now=now, text=worker_tip)
            self.state = self.state.with_updates(header_logo_game=game)
            return {
                "source": "worker-propose",
                "target": self._tutorial_worker_target_hint or "follow",
                "text": worker_tip,
            }
        llm_hints = [*hints[:12], *[f"RAG {entry}" for entry in rag_context[:8]]]
        llm_tip = self._tutorial_ai_llm_message(now=now, status=effective_status, hints=llm_hints)
        if llm_tip:
            self._append_artifact_chat_ai_message(game=game, now=now, text=llm_tip)
            self.state = self.state.with_updates(header_logo_game=game)
            return {
                "source": "openai-compatible",
                "target": self._tutorial_worker_target_hint or "content",
                "text": llm_tip,
            }
        if not hints and not rag_context:
            base = _TUTORIAL_AI_KNOWLEDGE[int(now * 0.5) % len(_TUTORIAL_AI_KNOWLEDGE)]
            return {
                "source": "local-knowledge",
                "target": "follow",
                "text": f"{status} {base}",
            }
        cc = hints[int(now * 0.7) % len(hints)] if hints else ""
        rag = rag_context[int(now * 0.9) % len(rag_context)] if rag_context else ""
        parts = [status]
        if cc:
            parts.append(f"CodeCompass: {cc}")
        if rag:
            parts.append(f"RAG: {rag}")
        return {
            "source": "codecompass-rag",
            "target": "content",
            "text": " ".join(parts),
        }

    def _artifact_chat_prompt_overlay(self, *, game: dict[str, object]) -> str:
        target = game.get("artifact_intent_target")
        if not isinstance(target, dict):
            return "artifact_context=none"
        label = str(target.get("label") or "(unnamed)")
        payload = target.get("payload")
        path = ""
        if isinstance(payload, dict):
            path = str(payload.get("path") or "")
        excerpt = ""
        if path:
            p = Path(path).expanduser()
            if not p.is_absolute():
                p = (Path.cwd() / p).resolve()
            if p.exists() and p.is_file():
                try:
                    lines = p.read_text(encoding="utf-8").splitlines()[:8]
                    excerpt = " | ".join(" ".join(line.split()) for line in lines if line.strip())[:420]
                except OSError:
                    excerpt = ""
                except UnicodeDecodeError:
                    excerpt = ""
        if excerpt:
            return f"artifact_context={label} path={path} excerpt={excerpt}"
        return f"artifact_context={label} path={path or '(none)'}"

    def _resolve_tutorial_prompt_template(self, game: dict[str, object]) -> str:
        env_template = str(os.environ.get("ANANTA_TUI_SNAKE_AI_PROMPT_TEMPLATE") or "").strip()
        game_template = str(game.get("tutorial_prompt_template") or "").strip()
        template = game_template or env_template or _TUTORIAL_AI_PROMPT_TEMPLATE_DEFAULT
        return template[:1200]

    def _render_tutorial_prompt_overlay(
        self,
        *,
        template: str,
        priority: str,
        user_feed: str,
        contact_zone: str,
    ) -> str:
        values = {
            "priority": str(priority or ""),
            "user_feed": str(user_feed or ""),
            "contact_zone": str(contact_zone or ""),
        }
        class _SafeTemplateDict(dict[str, str]):
            def __missing__(self, key: str) -> str:
                return "{" + key + "}"
        try:
            rendered = str(template).format_map(_SafeTemplateDict(values))
        except Exception:
            rendered = _TUTORIAL_AI_PROMPT_TEMPLATE_DEFAULT.format_map(_SafeTemplateDict(values))
        return " ".join(rendered.split())[:1200]

    def _tutorial_ai_worker_propose_message(
        self,
        *,
        now: float,
        status: str,
        hints: list[str],
        rag_context: list[str],
    ) -> str | None:
        backend, implicit = self._tutorial_worker_backend()
        if backend not in {"worker-propose", "worker", "opencode", "hermes"}:
            return None
        # the implicit Hub route shares the local model with tasks and Meet: ask it rarely
        refresh_default = "60.0" if implicit else "8.0"
        refresh_seconds = max(2.0, min(60.0, float(os.environ.get("ANANTA_TUI_SNAKE_AI_REFRESH", refresh_default))))
        cached_at, cached_msg = self._tutorial_worker_cache
        if cached_msg and (now - cached_at) < refresh_seconds:
            self._tutorial_last_source = "worker-propose"
            if self._tutorial_worker_target_hint:
                self._tutorial_last_target = self._tutorial_worker_target_hint
            return cached_msg

        base_url = str(self.state.endpoint or os.environ.get("ANANTA_BASE_URL") or "http://localhost:5000").strip()
        if not base_url:
            return None
        # a background tip may wait for a real model call; a synchronous one must not stall the UI
        timeout_default = "12.0" if self._tutorial_async_enabled() else "1.6"
        timeout_seconds = max(0.3, min(12.0, float(os.environ.get("ANANTA_TUI_SNAKE_AI_TIMEOUT", timeout_default))))
        model = str(os.environ.get("ANANTA_TUI_SNAKE_AI_MODEL", "")).strip()
        provider = str(os.environ.get("ANANTA_TUI_SNAKE_AI_WORKER_PROVIDER", "")).strip()
        if not provider and backend in {"opencode", "hermes"}:
            provider = backend

        hint_block = "\n".join(f"- {h}" for h in hints[:8]) if hints else "- no codecompass hints"
        rag_block = "\n".join(f"- {h}" for h in rag_context[:8]) if rag_context else "- no rag_helper context"
        prompt = (
            f"{status}\n"
            "You are the tutorial snake controller for Ananta TUI.\n"
            "Use provided context hints when present.\n"
            "Return exactly one line <=180 chars with immediate guidance.\n"
            "Prefix the line with one steering tag in this format: [target=header|nav|content|detail|follow].\n"
            f"CodeCompass hints:\n{hint_block}\n"
            f"rag_helper context:\n{rag_block}\n"
        )
        payload: dict[str, object] = {"prompt": prompt, "temperature": 0.2}
        if model:
            payload["model"] = model
        if provider:
            payload["provider"] = provider
        strategy_mode = str(os.environ.get("ANANTA_TUI_SNAKE_AI_WORKER_STRATEGY", "")).strip()
        if strategy_mode:
            payload["strategy_mode"] = strategy_mode
        token = str(os.environ.get("ANANTA_TUI_SNAKE_AI_WORKER_TOKEN", "")).strip()
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        else:  # the operator's Hub login, as the chat's Hub path uses it
            from client_surfaces.operator_tui.chat_message_formatter import hub_user_auth_headers

            headers.update(hub_user_auth_headers(base_url))
        request = urllib.request.Request(
            url=base_url.rstrip("/") + "/step/propose",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                raw = response.read().decode("utf-8", errors="replace")
            parsed = json.loads(raw)
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
            return None
        data = parsed.get("data") if isinstance(parsed, dict) and isinstance(parsed.get("data"), dict) else parsed
        if not isinstance(data, dict):
            return None
        text = str(data.get("reason") or data.get("raw") or "").strip()
        if not text:
            return None
        single_line = " ".join(text.split())
        if not single_line:
            return None
        target_hint = ""
        match = re.search(r"\[target=(header|nav|content|detail|follow)\]", single_line, flags=re.IGNORECASE)
        if match:
            target_hint = match.group(1).lower()
            single_line = re.sub(r"\[target=(header|nav|content|detail|follow)\]\s*", "", single_line, flags=re.IGNORECASE)
        self._tutorial_worker_target_hint = target_hint
        self._tutorial_last_source = "worker-propose"
        self._tutorial_last_target = target_hint or "follow"
        clipped = single_line[:180].strip()
        if not clipped:
            return None
        self._tutorial_worker_cache = (now, clipped)
        return clipped

    def _endpoint_as_llm_base(self) -> str:
        """The TUI endpoint itself when the operator pointed it at an OpenAI-compatible runtime (``.../v1``)."""
        endpoint = str(getattr(self.state, "endpoint", "") or "").strip().rstrip("/")
        return endpoint if endpoint.endswith("/v1") else ""

    def _tutorial_worker_backend(self) -> tuple[str, bool]:
        """The tutorial AI's Hub route and whether it was chosen implicitly: an explicit
        ``ANANTA_TUI_SNAKE_AI_BACKEND`` wins; without one, the Hub (``worker-propose``) answers unless a
        direct OpenAI-compatible endpoint is configured."""
        explicit = str(os.environ.get("ANANTA_TUI_SNAKE_AI_BACKEND", "")).strip().lower()
        if explicit:
            return explicit, False
        api_base, _model, _token = self._get_llm_api_config()
        return ("", False) if api_base else ("worker-propose", True)

    def _tutorial_ai_llm_message(self, *, now: float, status: str, hints: list[str]) -> str | None:
        api_base, model, api_token = self._get_llm_api_config()
        if not model:
            model = "ananta-smoke"
        if not api_base:
            return None
        if not (model and api_base):
            return None
        parsed_api_base = urlparse(api_base)
        if (not parsed_api_base.path or parsed_api_base.path == "/") and parsed_api_base.netloc.endswith(":1234"):
            api_base = api_base.rstrip("/") + "/v1"

        refresh_seconds = max(2.0, min(60.0, float(os.environ.get("ANANTA_TUI_SNAKE_AI_REFRESH", "8.0"))))
        cached_at, cached_msg = self._tutorial_llm_cache
        if cached_msg and (now - cached_at) < refresh_seconds:
            self._tutorial_last_source = "openai-compatible"
            self._tutorial_last_target = "content"
            return cached_msg

        timeout_seconds = max(0.3, min(10.0, float(os.environ.get("ANANTA_TUI_SNAKE_AI_TIMEOUT", "1.6"))))
        profile = self._resolve_tutorial_llm_profile(
            now=now,
            model=model,
            api_base=api_base,
            api_token=api_token,
            timeout_seconds=timeout_seconds,
        )
        hint_block = "\n".join(f"- {h}" for h in hints[:8]) if hints else "- no codecompass hints available"
        prompt = (
            f"{status}\n"
            f"{str(profile.get('user_prompt') or '')}\n"
            f"CodeCompass + rag_helper hints:\n{hint_block}\n"
            "Max 180 chars."
        )
        content = self._tutorial_llm_chat_completion(
            model=model,
            api_base=api_base,
            api_token=api_token,
            timeout_seconds=timeout_seconds,
            system_prompt=str(profile.get("system_prompt") or "You are a concise in-product tutorial assistant."),
            user_prompt=prompt,
            temperature=float(profile.get("temperature") or 0.15),
            max_tokens=int(profile.get("max_tokens") or 72),
        )
        if not content:
            return None
        parsed = self._parse_tutorial_ai_llm_content(content)
        if not parsed:
            return None
        clipped, target_hint = parsed
        self._tutorial_worker_target_hint = target_hint
        self._tutorial_last_source = "openai-compatible"
        self._tutorial_last_target = target_hint or "content"
        self._tutorial_llm_cache = (now, clipped)
        return clipped

    def _resolve_tutorial_llm_profile(
        self,
        *,
        now: float,
        model: str,
        api_base: str,
        api_token: str,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        profile_key = f"{model}@{api_base}"
        if self._tutorial_llm_profile_cache and self._tutorial_llm_profile_key == profile_key:
            return dict(self._tutorial_llm_profile_cache)

        default_profile: dict[str, Any] = {
            "id": "compact-plain",
            "system_prompt": "You are a concise in-product tutorial assistant.",
            "user_prompt": "Provide one concise tutorial line for a snake assistant in this TUI. Focus on the immediate next action.",
            "temperature": 0.15,
            "max_tokens": 72,
        }
        training_enabled = str(os.environ.get("ANANTA_TUI_SNAKE_AI_TRAINING", "0")).strip().lower() in {"1", "true", "yes", "on"}
        if not training_enabled:
            self._tutorial_llm_profile_key = profile_key
            self._tutorial_llm_profile_cache = dict(default_profile)
            return dict(default_profile)

        candidates: list[dict[str, Any]] = [
            dict(default_profile),
            {
                "id": "compact-tagged",
                "system_prompt": "You are a concise in-product tutorial assistant.",
                "user_prompt": (
                    "Return exactly one short line with one steering prefix "
                    "[target=header|nav|content|detail|follow] and immediate next action."
                ),
                "temperature": 0.1,
                "max_tokens": 64,
            },
        ]

        best_profile: dict[str, Any] = dict(default_profile)
        best_score: tuple[int, float] = (-1, 999.0)
        for candidate in candidates:
            probe_prompt = (
                "TUI mode=normal focus=content section=dashboard idx=0.\n"
                f"{str(candidate.get('user_prompt') or '')}\n"
                "CodeCompass + rag_helper hints:\n- queue depth\n- tasks pending\n"
                "Max 180 chars."
            )
            started = time.monotonic()
            content = self._tutorial_llm_chat_completion(
                model=model,
                api_base=api_base,
                api_token=api_token,
                timeout_seconds=min(1.8, timeout_seconds),
                system_prompt=str(candidate.get("system_prompt") or ""),
                user_prompt=probe_prompt,
                temperature=float(candidate.get("temperature") or 0.15),
                max_tokens=int(candidate.get("max_tokens") or 72),
            )
            elapsed = time.monotonic() - started
            parsed = self._parse_tutorial_ai_llm_content(content or "")
            if not parsed:
                continue
            _, target_hint = parsed
            structure_bonus = 1 if target_hint else 0
            score = (1 + structure_bonus, elapsed)
            if score[0] > best_score[0] or (score[0] == best_score[0] and score[1] < best_score[1]):
                best_score = score
                best_profile = dict(candidate)

        self._tutorial_llm_profile_key = profile_key
        self._tutorial_llm_profile_cache = dict(best_profile)
        return dict(best_profile)

    def _tutorial_llm_chat_completion(
        self,
        *,
        model: str,
        api_base: str,
        api_token: str,
        timeout_seconds: float,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
    ) -> str | None:
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": max(0.0, min(1.0, float(temperature))),
            "max_tokens": max(24, min(160, int(max_tokens))),
        }
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if api_token:
            headers["Authorization"] = f"Bearer {api_token}"
        request = urllib.request.Request(
            url=api_base.rstrip("/") + "/chat/completions",
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                raw = response.read().decode("utf-8", errors="replace")
            parsed = json.loads(raw)
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
            return None
        choices = parsed.get("choices") if isinstance(parsed, dict) else None
        if not isinstance(choices, list) or not choices:
            return None
        first = choices[0]
        if not isinstance(first, dict):
            return None
        message = first.get("message")
        if not isinstance(message, dict):
            return None
        content = str(message.get("content") or "").strip()
        return content or None

    def _parse_tutorial_ai_llm_content(self, content: str) -> tuple[str, str] | None:
        single_line = " ".join(str(content or "").split())
        if not single_line:
            return None
        target_hint = ""
        match = re.search(r"\[target=(header|nav|content|detail|follow)\]", single_line, flags=re.IGNORECASE)
        if match:
            target_hint = match.group(1).lower()
            single_line = re.sub(r"\[target=(header|nav|content|detail|follow)\]\s*", "", single_line, flags=re.IGNORECASE)
        elif single_line.startswith("{") and single_line.endswith("}"):
            try:
                payload = json.loads(single_line)
            except json.JSONDecodeError:
                payload = {}
            if isinstance(payload, dict):
                text = str(payload.get("text") or payload.get("message") or "").strip()
                target = str(payload.get("target") or "").strip().lower()
                if target in {"header", "nav", "content", "detail", "follow"}:
                    target_hint = target
                if text:
                    single_line = " ".join(text.split())
        clipped = single_line[:180].strip()
        if not clipped:
            return None
        return clipped, target_hint

    def _get_llm_api_config(self) -> tuple[str, str, str]:
        game = self.state.header_logo_game if isinstance(getattr(self.state, "header_logo_game", None), dict) else {}
        backend_hint = str(
            os.environ.get("ANANTA_TUI_SNAKE_AI_BACKEND")
            or game.get("chat_backend")
            or ""
        ).strip().lower()
        # A direct OpenAI-compatible endpoint only when one is configured; without it the TUI's AI goes
        # through the Hub (see _tutorial_worker_backend) instead of a guessed LAN address.
        api_base = str(
            game.get("chat_backend_api_base")
            or os.environ.get("ANANTA_TUI_CHAT_API_BASE_URL")
            or os.environ.get("ANANTA_TUI_SNAKE_AI_API_BASE_URL")
            or os.environ.get("OPENAI_BASE_URL")
            or os.environ.get("OPENAI_API_BASE")
            or self._endpoint_as_llm_base()
            or ""
        ).strip()
        if backend_hint == "worker-propose":
            model = str(
                os.environ.get("ANANTA_TUI_CHAT_MODEL")
                or os.environ.get("ANANTA_TUI_SNAKE_AI_MODEL")
                or "google/gemma-4-e4b"
            ).strip()
        else:
            model = str(
                game.get("chat_backend_model")
                or os.environ.get("ANANTA_TUI_CHAT_MODEL")
                or os.environ.get("ANANTA_TUI_SNAKE_AI_MODEL")
                or "google/gemma-4-e4b"
            ).strip()
        api_token = str(
            os.environ.get("ANANTA_TUI_SNAKE_AI_API_TOKEN")
            or os.environ.get("OPENAI_API_KEY")
            or ""
        ).strip()
        return api_base, model, api_token

    def _llm_health_check_sync(self) -> dict:
        checked_at = time.time()
        api_base, model, api_token = self._get_llm_api_config()
        if not api_base:
            return {"reachable": False, "model": model, "last_check_at": checked_at, "error": "ANANTA_TUI_SNAKE_AI_API_BASE_URL nicht gesetzt"}
        try:
            url = f"{api_base.rstrip('/')}/models"
            headers: dict[str, str] = {"Content-Type": "application/json"}
            if api_token:
                headers["Authorization"] = f"Bearer {api_token}"
            req = urllib.request.Request(url, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                data = json.loads(resp.read().decode())
                models_list = data.get("data") or []
                loaded = models_list[0].get("id", model) if models_list else model
                return {"reachable": True, "model": str(loaded), "last_check_at": checked_at, "error": ""}
        except TimeoutError:
            return {"reachable": False, "model": model, "last_check_at": checked_at, "error": "timeout"}
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            err = "timeout" if isinstance(reason, TimeoutError) else str(reason)[:80]
            return {"reachable": False, "model": model, "last_check_at": checked_at, "error": err}
        except Exception as exc:
            return {"reachable": False, "model": model, "last_check_at": checked_at, "error": str(exc)[:80]}

    def _maybe_tick_llm_health(self, game: dict, now: float) -> None:
        raw_interval = str(os.environ.get("ANANTA_TUI_LLM_HEALTH_INTERVAL_SECS", "30")).strip()
        interval = max(0.0, float(raw_interval)) if raw_interval.replace(".", "").isdigit() else 30.0
        if interval == 0:
            return
        last_at = float(game.get("llm_health_last_at") or 0)
        future = getattr(self, "_llm_health_future", None)
        if future is not None and future.done():
            try:
                result = future.result()
                game["llm_status"] = dict(result)
            except Exception:
                pass
            self._llm_health_future = None
            future = None
        if future is None and (now - last_at) >= interval:
            game["llm_health_last_at"] = now
            self._llm_health_future = self._get_snake_bg_executor().submit(
                self._llm_health_check_sync
            )
