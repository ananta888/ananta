from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from concurrent.futures import Future, ThreadPoolExecutor
import urllib.error
import urllib.request
from urllib.parse import urlparse
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import asyncio
import math
from prompt_toolkit.application import Application
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.output.color_depth import ColorDepth

from client_surfaces.operator_tui.adapters import SectionAdapterRegistry
from client_surfaces.operator_tui.ai_snake_context import (
    artifact_ref_from_game,
    build_context_envelope_ref,
    default_ai_context,
    load_codecompass_artifact,
    relevance_refs_for_intent,
    set_ai_context,
)
from client_surfaces.operator_tui.ai_snake_follow import (
    apply_worker_follow_update,
    make_follow_state,
    step_follow_state,
)
from client_surfaces.operator_tui.ai_snake_policy import apply_policy_to_payload
from client_surfaces.operator_tui.ai_snake_observation import ObservationBuffer
from client_surfaces.operator_tui.ai_snake_prediction import PredictionGate, quick_predict
from client_surfaces.operator_tui.ai_snake_prediction_cache import PredictionCache
from client_surfaces.operator_tui.artifact_intent import ArtifactIntent, ArtifactIntentDetector, IntentConfidence
from client_surfaces.operator_tui.app import load_active_section
from client_surfaces.operator_tui.commands import execute_command
from client_surfaces.operator_tui.mouse import (
    MouseEventType as NormalizedMouseEventType,
    MouseState,
    detect_mouse_support,
    normalize_mouse_state,
)
from client_surfaces.operator_tui.models import FocusPane, OperatorMode, OperatorState
from client_surfaces.operator_tui.logo_renderer.snake_motion import PixelPoint, pixel_boost_speed, smooth_follow
from client_surfaces.operator_tui.plugins import PluginRegistry, default_plugin_registry, resolve_item_reference
from client_surfaces.operator_tui.region_index import RegionTarget, build_region_index
from client_surfaces.operator_tui.renderer import render_operator_shell
from client_surfaces.operator_tui.sections import SECTIONS, get_section

if TYPE_CHECKING:
    from agent.cli.splash import SplashMachine, SplashState

_ANSI_STRIP = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_TUTORIAL_AI_KNOWLEDGE: tuple[str, ...] = (
    "TUI: Focus [Tab], Command [:], Snake [Ctrl+S], Hilfe [?].",
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


class InteractiveOperatorTui:
    def __init__(
        self,
        state: OperatorState,
        registry: SectionAdapterRegistry | None = None,
        splash: SplashMachine | None = None,
    ) -> None:
        self._registry = registry or SectionAdapterRegistry()
        self._splash = splash
        self._plugins: PluginRegistry = default_plugin_registry()
        self._mouse_capabilities = detect_mouse_support()
        self._mouse_state = MouseState()
        self._intent_detector = ArtifactIntentDetector(
            dwell_seconds=float(os.environ.get("ANANTA_TUI_SNAKE_MOUSE_DWELL", "0.35"))
        )
        self.state = load_active_section(state, self._registry)
        term_graphics = dict(self.state.terminal_graphics or {})
        term_graphics["mouse_support"] = dict(self._mouse_capabilities)
        self.state = self.state.with_updates(terminal_graphics=term_graphics)
        self._tutorial_codecompass_cache: tuple[float, list[str]] = (0.0, [])
        self._tutorial_rag_cache: tuple[float, list[str]] = (0.0, [])
        self._tutorial_worker_cache: tuple[float, str] = (0.0, "")
        self._tutorial_worker_target_hint: str = ""
        self._tutorial_llm_cache: tuple[float, str] = (0.0, "")
        self._tutorial_llm_profile_cache: dict[str, Any] | None = None
        self._tutorial_llm_profile_key: str = ""
        self._tutorial_last_tip_text: str = ""
        self._tutorial_async_tip_future: Future[dict[str, str] | None] | None = None
        self._tutorial_async_tip_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tui-tutorial-ai")
        self._tutorial_async_next_refresh_at: float = 0.0
        self._tutorial_status_snapshot: dict[str, str] = {}
        self._codecompass_build_future: Future[Path | None] | None = None
        self._codecompass_build_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tui-codecompass-build")
        self._codecompass_build_output_dir: Path | None = None
        self._tutorial_last_source: str = "local-knowledge"
        self._tutorial_last_target: str = "follow"
        self._ai_observation = ObservationBuffer(max_events=100)
        self._ai_prediction_gate = PredictionGate(min_interval_seconds=3.0, min_confidence=0.35, stable_ms=500)
        self._ai_prediction_cache = PredictionCache(ttl_seconds=30)
        # E01/E02: new runtime state
        self._snake_idle_since: float = 0.0
        self._snake_last_event_fired: str = ""
        self._tutor_event_session_used: set[str] = set()
        self._tutor_ask_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tui-tutor-ask")
        self._tutor_ask_future: Future[str | None] | None = None
        self._section_first_visit_pending: str = ""
        # E01: load highscore cache once
        self._scores_cache: dict[str, Any] = {}
        try:
            from client_surfaces.operator_tui.snake_persistence import load_snake_scores
            self._scores_cache = load_snake_scores()
        except Exception:
            pass
        # E02: tutor depth mode from persistence
        self._tutor_depth_mode: str = "overview"
        try:
            from client_surfaces.operator_tui.snake_persistence import get_tutor_mode
            self._tutor_depth_mode = get_tutor_mode()
        except Exception:
            pass
        self._command_buffer = ""
        # E03: Chat transport (initialized lazily when snake registers with Hub)
        self._chat_transport: Any = None
        # E05: Load initial notes into notes:self channel
        self._init_notes_channel()
        self._rendered_text = self._render()
        self._control = FormattedTextControl(text=lambda: ANSI(self._rendered_text))
        self._output = Window(content=self._control, wrap_lines=False)
        self._app = Application(
            layout=Layout(self._output),
            key_bindings=self._build_keybindings(),
            full_screen=True,
            mouse_support=bool(self._mouse_capabilities.get("enabled")),
            color_depth=ColorDepth.TRUE_COLOR,
        )

    def run(self) -> int:
        self._app.run(pre_run=self._on_app_start)
        return 0

    def _on_app_start(self) -> None:
        if self._splash is not None:
            self._app.create_background_task(self._splash_loop())
        if self._header_3d_active():
            self._app.create_background_task(self._header_logo_loop())

    def _header_3d_active(self) -> bool:
        enabled = os.environ.get("ANANTA_TUI_HEADER_3D", "1").strip().lower() not in {"0", "false", "no", "off"}
        no_3d = (self.state.terminal_graphics or {}).get("no_3d", False)
        return enabled and not no_3d

    async def _header_logo_loop(self) -> None:
        fps = max(1, min(60, int(os.environ.get("ANANTA_TUI_HEADER_3D_FPS", "24"))))
        delay = 1.0 / fps
        while True:
            self._tick_header_snake()
            self._rendered_text = self._render()
            self._app.invalidate()
            await asyncio.sleep(delay)

    async def _splash_loop(self) -> None:
        while self._splash is not None:
            ctx = self._splash.context
            from agent.cli.splash import SplashState
            if ctx.state in (SplashState.DISABLED, SplashState.SKIPPED, SplashState.COMPACT_HEADER):
                break
            self._splash.tick()
            self._rendered_text = self._render()
            self._app.invalidate()
            await asyncio.sleep(0.1)

    def _build_keybindings(self) -> KeyBindings:
        bindings = KeyBindings()

        @bindings.add("q")
        def _(event) -> None:
            if self._snake_message_mode_active():
                self._snake_message_append("q")
                return
            if self.state.mode is OperatorMode.COMMAND:
                self._append_command("q")
                return
            event.app.exit()

        @bindings.add(":")
        def _(event) -> None:
            if self._snake_message_mode_active():
                self._snake_message_append(":")
                return
            if self._snake_mode_active():
                return
            if self.state.mode is OperatorMode.COMMAND:
                self._append_command(":")
                return
            self._command_buffer = ""
            self._set_state(self.state.with_updates(mode=OperatorMode.COMMAND, command_line=""))

        @bindings.add("enter")
        def _(event) -> None:
            if self._snake_message_mode_active():
                self._snake_commit_message()
                return
            if self._chat_focus_active():
                self._chat_send_message()
                return
            if self._snake_mode_active():
                # T04.04: Enter advances guided tour immediately
                game = self.state.header_logo_game or {}
                ts_raw = game.get("tutorial_state")
                if isinstance(ts_raw, dict) and ts_raw.get("guided"):
                    self._advance_guided_tour_now()
                return
            if self.state.mode is OperatorMode.COMMAND:
                self._run_command(self._command_buffer)
                return
            if self.state.focus is FocusPane.HEADER:
                from client_surfaces.operator_tui.header_config import CONFIG_ITEMS, cycle_value
                if 0 <= self.state.selected_index < len(CONFIG_ITEMS):
                    self._set_state(cycle_value(self.state, CONFIG_ITEMS[self.state.selected_index]))
                return
            if self.state.focus is FocusPane.NAVIGATION:
                if 0 <= self.state.selected_index < len(SECTIONS):
                    section = SECTIONS[self.state.selected_index]
                    self._run_command(f":section {section.id}")
                    self._set_state(self.state.with_updates(focus=FocusPane.CONTENT, selected_index=0))
                return
            self._run_command(":inspect")

        @bindings.add("escape")
        def _(event) -> None:
            if self._snake_message_mode_active():
                self._snake_cancel_message()
                return
            if self._chat_focus_active():
                self._chat_focus_leave()
                return
            if self._snake_mode_active():
                return
            self._command_buffer = ""
            self._run_command(":cancel")

        @bindings.add("backspace")
        def _(event) -> None:
            if self._snake_message_mode_active():
                self._snake_message_backspace()
                return
            if self._chat_focus_active():
                self._chat_backspace()
                return
            if self._snake_mode_active():
                return
            if self.state.mode is OperatorMode.COMMAND:
                self._command_buffer = self._command_buffer[:-1]
                self._set_state(self.state.with_updates(command_line=self._command_buffer))

        @bindings.add("j")
        def _(event) -> None:
            def _j():
                self._set_state(self.state.with_updates(selected_index=self._clamp_down()))
            self._normal_or_text("j", _j)

        @bindings.add("k")
        def _(event) -> None:
            self._normal_or_text("k", lambda: self._set_state(self.state.with_updates(selected_index=max(0, self.state.selected_index - 1))))

        @bindings.add("e")
        def _(event) -> None:
            def _e():
                if self._open_selected_item_inline():
                    return
                section = get_section(self.state.section_id)
                payload = (self.state.section_payloads or {}).get(section.id, {})
                plugin = self._plugins.launcher_for(payload, self.state.selected_index)
                if plugin is None:
                    return
                async def _run():
                    await event.app.run_in_terminal(
                        lambda: plugin.launch(payload, self.state.selected_index)
                    )
                event.app.create_background_task(_run())
            self._normal_or_text("e", _e)

        @bindings.add("h")
        def _(event) -> None:
            self._normal_or_text("h", lambda: self._move_focus(-1))

        @bindings.add("l")
        def _(event) -> None:
            self._normal_or_text("l", lambda: self._move_focus(1))

        @bindings.add("r")
        def _(event) -> None:
            self._normal_or_text("r", lambda: self._run_command(":refresh"))

        @bindings.add("?")
        def _(event) -> None:
            self._normal_or_text("?", lambda: self._run_command(":help"))

        @bindings.add("tab")
        def _(event) -> None:
            if self._snake_mode_active():
                return
            if self.state.mode is OperatorMode.COMMAND:
                self._append_command(" ")
                return
            self._move_focus(1)

        @bindings.add("space")
        def _(event) -> None:
            if self.state.mode is OperatorMode.COMMAND:
                self._append_command(" ")
                return
            if not self._snake_mode_active():
                return
            self._toggle_snake_pause()  # T01.02: Space togglet Pause statt Stopp

        @bindings.add("c-s")
        def _(event) -> None:
            if self.state.mode is OperatorMode.COMMAND:
                return
            self._toggle_snake_mode()

        @bindings.add("m")
        def _(event) -> None:
            if self.state.mode is OperatorMode.COMMAND:
                self._append_command("m")
                return
            if self._snake_message_mode_active():
                self._snake_message_append("m")
                return
            if not self._snake_mode_active():
                return
            self._toggle_snake_message_mode()

        @bindings.add("x")
        def _(event) -> None:
            if self.state.mode is OperatorMode.COMMAND:
                self._append_command("x")
                return
            if self._snake_message_mode_active():
                self._snake_message_append("x")
                return
            if not self._snake_mode_active():
                return
            self._snake_toggle_selection()

        @bindings.add("b")
        def _(event) -> None:
            if self.state.mode is OperatorMode.COMMAND:
                self._append_command("b")
                return
            if self._snake_message_mode_active():
                self._snake_message_append("b")
                return
            if not self._snake_mode_active():
                return
            self._snake_toggle_frame_mode()

        @bindings.add("c")
        def _(event) -> None:
            if self.state.mode is OperatorMode.COMMAND:
                self._append_command("c")
                return
            if self._snake_message_mode_active():
                self._snake_message_append("c")
                return
            if not self._snake_mode_active():
                return
            if self._chat_focus_active():
                self._chat_append("c")
                return
            self._chat_focus_enter()

        @bindings.add("v")
        def _(event) -> None:
            if self.state.mode is OperatorMode.COMMAND:
                self._append_command("v")
                return
            if self._snake_message_mode_active():
                self._snake_message_append("v")
                return
            if not self._snake_mode_active():
                return
            self._snake_replace_selection()

        @bindings.add("t")
        def _(event) -> None:
            if self.state.mode is OperatorMode.COMMAND:
                self._append_command("t")
                return
            if self._snake_message_mode_active():
                self._snake_message_append("t")
                return
            if not self._snake_mode_active():
                return
            self._snake_cycle_message_style()

        @bindings.add("y")
        def _(event) -> None:
            if self.state.mode is OperatorMode.COMMAND:
                self._append_command("y")
                return
            if self._snake_message_mode_active():
                self._snake_message_append("y")
                return
            if not self._snake_mode_active():
                return
            self._snake_cycle_color()

        @bindings.add("z")
        def _(event) -> None:
            if self.state.mode is OperatorMode.COMMAND:
                self._append_command("z")
                return
            if self._snake_message_mode_active():
                self._snake_message_append("z")
                return
            if not self._snake_mode_active():
                return
            self._snake_clear_visual_marks()

        @bindings.add("u")
        def _(event) -> None:
            if self.state.mode is OperatorMode.COMMAND:
                self._append_command("u")
                return
            if self._snake_message_mode_active():
                self._snake_message_append("u")
                return
            if not self._snake_mode_active():
                return
            self._toggle_tutorial_ai_mode()

        @bindings.add("o")
        def _(event) -> None:
            if self.state.mode is OperatorMode.COMMAND:
                self._append_command("o")
                return
            if self._snake_message_mode_active():
                self._snake_message_append("o")
                return
            if not self._snake_mode_active():
                return
            self._toggle_snake_mouse_follow()

        @bindings.add("left")
        def _(event) -> None:
            if self._try_header_snake_direction((-1, 0)):
                return
            self._set_state(self.state.with_updates(selected_index=max(0, self.state.selected_index - 1)))

        @bindings.add("right")
        def _(event) -> None:
            if self._try_header_snake_direction((1, 0)):
                return
            self._set_state(self.state.with_updates(selected_index=self._clamp_down()))

        @bindings.add("up")
        def _(event) -> None:
            if self._try_header_snake_direction((0, -1)):
                return
            self._set_state(self.state.with_updates(selected_index=max(0, self.state.selected_index - 1)))

        @bindings.add("down")
        def _(event) -> None:
            if self._try_header_snake_direction((0, 1)):
                return
            self._set_state(self.state.with_updates(selected_index=self._clamp_down()))

        @bindings.add("n")
        def _(event) -> None:
            self._normal_or_text("n", lambda: self._run_command(":next"))

        @bindings.add("p")
        def _(event) -> None:
            self._normal_or_text("p", lambda: self._run_command(":prev"))

        @bindings.add("g")
        def _(event) -> None:
            self._normal_or_text("g", lambda: self._set_state(self.state.with_updates(selected_index=0)))

        @bindings.add("G")
        def _(event) -> None:
            self._normal_or_text("G", lambda: self._set_state(self.state.with_updates(selected_index=999999)))

        @bindings.add("<any>")
        def _(event) -> None:
            if self._snake_message_mode_active():
                data = event.key_sequence[0].data
                if data and data.isprintable():
                    self._snake_message_append(data)
                return
            if self._chat_focus_active():
                data = event.key_sequence[0].data
                if data and data.isprintable():
                    self._chat_append(data)
                return
            if self.state.mode is OperatorMode.COMMAND:
                data = event.key_sequence[0].data
                if data and data.isprintable():
                    self._append_command(data)

        @bindings.add("pageup")
        def _(event) -> None:
            if self._chat_focus_active():
                self._chat_scroll(-5)

        @bindings.add("pagedown")
        def _(event) -> None:
            if self._chat_focus_active():
                self._chat_scroll(5)

        @bindings.add("escape", "up")  # Alt+Up
        def _(event) -> None:
            if self._chat_focus_active():
                self._chat_scroll(-1)

        @bindings.add("escape", "down")  # Alt+Down
        def _(event) -> None:
            if self._chat_focus_active():
                self._chat_scroll(1)

        @bindings.add(Keys.Vt100MouseEvent)
        def _(event) -> None:
            game = dict(self.state.header_logo_game or {})
            if not self._snake_mode_active(game):
                return
            data = event.key_sequence[0].data or ""
            parsed = self._parse_sgr_mouse_event(data)
            if parsed is None:
                return
            self._ingest_mouse_event(
                x=parsed[0],
                y=parsed[1],
                event_type=parsed[2],
                buttons=parsed[3],
                scroll_delta=parsed[4],
            )

        return bindings

    # ── Chat focus helpers (E01.04) ───────────────────────────────────────────

    def _chat_focus_active(self) -> bool:
        game = self.state.header_logo_game or {}
        chat_raw = game.get("chat_state")
        return isinstance(chat_raw, dict) and bool(chat_raw.get("chat_focus")) and self._snake_mode_active()

    def _chat_focus_enter(self) -> None:
        game = dict(self.state.header_logo_game or {})
        from client_surfaces.operator_tui.chat_state import get_chat_state, set_chat_state
        chat = get_chat_state(game)
        chat["chat_focus"] = True
        chat["chat_input_buffer"] = ""
        set_chat_state(game, chat)
        self._set_state(self.state.with_updates(header_logo_game=game, status_message="chat: focus"))

    def _chat_focus_leave(self) -> None:
        game = dict(self.state.header_logo_game or {})
        from client_surfaces.operator_tui.chat_state import get_chat_state, set_chat_state
        chat = get_chat_state(game)
        chat["chat_focus"] = False
        chat["chat_input_buffer"] = ""
        set_chat_state(game, chat)
        self._set_state(self.state.with_updates(header_logo_game=game, status_message="chat: game focus"))

    def _chat_append(self, ch: str) -> None:
        game = dict(self.state.header_logo_game or {})
        from client_surfaces.operator_tui.chat_state import get_chat_state, set_chat_state
        chat = get_chat_state(game)
        buf = str(chat.get("chat_input_buffer") or "")
        chat["chat_input_buffer"] = (buf + ch)[:200]
        set_chat_state(game, chat)
        self._set_state(self.state.with_updates(header_logo_game=game))

    def _chat_backspace(self) -> None:
        game = dict(self.state.header_logo_game or {})
        from client_surfaces.operator_tui.chat_state import get_chat_state, set_chat_state
        chat = get_chat_state(game)
        buf = str(chat.get("chat_input_buffer") or "")
        chat["chat_input_buffer"] = buf[:-1]
        set_chat_state(game, chat)
        self._set_state(self.state.with_updates(header_logo_game=game))

    def _chat_scroll(self, delta: int) -> None:
        game = dict(self.state.header_logo_game or {})
        from client_surfaces.operator_tui.chat_state import get_chat_state, set_chat_state
        chat = get_chat_state(game)
        current = int(chat.get("scroll_offset") or 0)
        chat["scroll_offset"] = max(0, current + delta)
        set_chat_state(game, chat)
        self._set_state(self.state.with_updates(header_logo_game=game))

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
            # Local notes: persist and show
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
            # AI chat: trigger ask flow
            msg = make_message(
                channel_id=ch_id, channel_type=ch_type,
                sender_id=local_id, sender_kind="user",
                text=buf, visibility="ai_context",
                delivery_state="sent",
            )
            append_message(chat, msg)
            # Reuse tutor ask mechanism
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

    # ── Notes ops from command (E05.03) ───────────────────────────────────────

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

    def _normal_or_text(self, text: str, normal_action) -> None:
        if self._snake_message_mode_active():
            self._snake_message_append(text)
            return
        if self.state.mode is OperatorMode.COMMAND:
            self._append_command(text)
            return
        if self._snake_mode_active():
            return
        normal_action()

    def _append_command(self, text: str) -> None:
        self._command_buffer += text
        self._set_state(self.state.with_updates(command_line=self._command_buffer))

    def _toggle_snake_mouse_follow(self) -> None:
        game = dict(self.state.header_logo_game or self._default_header_snake())
        enabled = bool(game.get("mouse_follow_enabled"))
        game["mouse_follow_enabled"] = not enabled
        game["movement_mode"] = "mouse_follow" if not enabled else "keyboard"
        status = "an" if not enabled else "aus"
        self._set_state(self.state.with_updates(header_logo_game=game, status_message=f"snake mouse-follow: {status}"))

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
        self._set_state(self.state.with_updates(header_logo_game=game, status_message=f"mouse {self._mouse_state.x},{self._mouse_state.y}"))

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
        else:
            event_type = "down"
            buttons = 1
        return cx, cy, event_type, buttons, scroll_delta

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

    def _run_command(self, command: str) -> None:
        result = execute_command(command, self.state)
        state = result.state.with_updates(status_message=result.message)
        if state.section_id != self.state.section_id or command.strip().lower() in {":refresh", "refresh", "r", ":next", ":prev"}:
            state = load_active_section(state, self._registry)
        self._command_buffer = ""
        self._set_state(state.with_updates(command_line=""))

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

    def _header_snake_enabled(self) -> bool:
        return os.environ.get("ANANTA_TUI_HEADER_SNAKE", "1").strip().lower() not in {"0", "false", "no", "off"}

    def _default_header_snake(self) -> dict[str, object]:
        cfg = self._load_snake_message_config()
        board_w, board_h = 18, 6
        snake = [(6, 3), (5, 3), (4, 3), (3, 3), (2, 3)]
        gaps = self._compute_snake_escape_gaps(board_w, board_h, seed=int(time.time() * 1000))
        return {
            "active": False,
            "alive": True,
            "ui_steering": False,
            "free_mode": False,
            "local_snake_id": "s1",
            "pseudonym": os.environ.get("ANANTA_TUI_SNAKE_PSEUDONYM", "local-snake"),
            "oidc_provider": os.environ.get("ANANTA_TUI_SNAKE_OIDC_PROVIDER", "local"),
            "board_w": board_w,
            "board_h": board_h,
            "snake": snake,
            "trail_path": list(snake),
            "mark_cells": [],
            "selection_anchor": None,
            "selection_cells": [],
            "selection_regions": [],
            "selection_frame_mode": False,
            "selection_frame_anchor": None,
            "clipboard": "",
            "message": str(cfg.get("snake_message") or ""),
            "tutorial_user_feed": str(cfg.get("tutorial_user_feed") or ""),
            "tutorial_prompt_template": str(
                cfg.get("tutorial_prompt_template")
                or os.environ.get("ANANTA_TUI_SNAKE_AI_PROMPT_TEMPLATE")
                or _TUTORIAL_AI_PROMPT_TEMPLATE_DEFAULT
            ),
            "message_style": "trail",
            "snake_color": "mint",
            "movement_mode": "mouse_follow" if bool(self._mouse_capabilities.get("enabled")) else "keyboard",
            "mouse_follow_enabled": bool(self._mouse_capabilities.get("enabled")),
            "mouse_state": {},
            "mouse_target": None,
            "artifact_intent_confidence": "none",
            "artifact_intent_score": 0.0,
            "artifact_intent_reason": "",
            "artifact_intent_target": None,
            "ai_snake_mode": "lurking_follow",
            "ai_snake_prediction": {},
            "ai_snake_debug": {},
            "ai_snake_runtime_status": "idle",
            "ai_snake_follow_state": make_follow_state(ai_position=(3, 3), mode="lurking_follow"),
            "artifact_target_cell": None,
            "tutorial_ai_target_mode": "follow_user",
            "tutorial_ai_target_hint": "follow",
            "artifact_chat_state": {
                "active_target": None,
                "messages": [],
                "pending_request": "",
                "backend_source": "",
                "error": "",
            },
            "trail_window": max(1, min(120, int(os.environ.get("ANANTA_TUI_SNAKE_TRAIL_WINDOW", "10")))),
            "trail_speed": max(0.2, min(60.0, float(os.environ.get("ANANTA_TUI_SNAKE_TRAIL_SPEED", "8.0")))),
            "tutorial_mode": os.environ.get("ANANTA_TUI_SNAKE_TUTORIAL_AI", "0").strip().lower() in {"1", "true", "yes", "on"},
            "snakes": {},
            "direction": (1, 0),
            "next_direction": (1, 0),
            "vel_x": 10.0,
            "vel_y": 0.0,
            "accum_x": 0.0,
            "accum_y": 0.0,
            "food": (12, 3),
            "gaps": gaps,
            "score": 0,
            "moves": 0,
            "last_move": time.monotonic(),
        }

    def _activate_header_snake(self, state: OperatorState) -> OperatorState:
        if not self._header_snake_enabled():
            return state
        game = dict(state.header_logo_game or self._default_header_snake())
        board_w = max(6, int(game.get("board_w", 18)))
        board_h = max(4, int(game.get("board_h", 6)))
        game["gaps"] = self._ensure_snake_escape_gaps(
            game.get("gaps"),
            board_w=board_w,
            board_h=board_h,
            seed=int(time.time() * 1000),
        )
        game["active"] = True
        game["ui_steering"] = True
        if not game.get("alive", True):
            game = self._default_header_snake()
        game["last_move"] = time.monotonic()
        return state.with_updates(header_logo_game=game)

    def _deactivate_header_snake(self, state: OperatorState) -> OperatorState:
        game = dict(state.header_logo_game or {})
        if not game:
            return state
        if game.get("ui_steering"):
            return state.with_updates(header_logo_game=game)
        game["active"] = False
        return state.with_updates(header_logo_game=game)

    def _try_header_snake_direction(self, direction: tuple[int, int]) -> bool:
        game = dict(self.state.header_logo_game or {})
        if self.state.mode is OperatorMode.COMMAND and not game.get("ui_steering"):
            return False
        if not self._header_snake_enabled():
            return False
        steering = self._snake_mode_active(game)
        if not steering:
            return False
        if not game.get("active", False):
            game["active"] = True
        if not game.get("alive", True):
            game = self._default_header_snake()
        accel = max(1.0, min(20.0, float(os.environ.get("ANANTA_TUI_HEADER_SNAKE_ACCEL", "3.0"))))
        max_speed = max(6.0, min(120.0, float(os.environ.get("ANANTA_TUI_HEADER_SNAKE_MAX_SPEED", "70"))))
        vx = float(game.get("vel_x", 10.0))
        vy = float(game.get("vel_y", 0.0))
        dx, dy = direction
        if dx:
            vx += accel * dx
            vy *= 0.15
            if abs(vx) < 4.0:
                vx = 4.0 * dx
        if dy:
            vy += accel * dy
            vx *= 0.15
            if abs(vy) < 4.0:
                vy = 4.0 * dy
        if abs(vx) < 0.1 and abs(vy) < 0.1:
            vx = 4.0 * dx
            vy = 4.0 * dy
        vx = max(-max_speed, min(max_speed, vx))
        vy = max(-max_speed, min(max_speed, vy))
        game["vel_x"] = vx
        game["vel_y"] = vy
        game["next_direction"] = direction
        self._set_state(self.state.with_updates(header_logo_game=game))
        return True

    def _tick_header_snake(self) -> None:
        game = dict(self.state.header_logo_game or {})
        if not self._header_snake_enabled():
            return
        if self.state.focus is not FocusPane.HEADER and not game.get("ui_steering"):
            return
        if not game or not game.get("active", False) or not game.get("alive", True):
            return
        # T01.02: skip tick when paused
        if bool(game.get("paused")):
            self._poll_tutor_ask_result(game)
            self._set_state(self.state.with_updates(header_logo_game=game))
            return
        # T01.04: speed override via :speed command
        tps_override = game.get("tps_override")
        tps = max(2, min(60, int(tps_override if tps_override else os.environ.get("ANANTA_TUI_HEADER_SNAKE_TPS", "18"))))
        step = 1.0 / tps
        now = time.monotonic()
        last_move = float(game.get("last_move", now))
        if (now - last_move) < step:
            return
        dt = max(step, now - last_move)

        free_mode = bool(game.get("free_mode"))
        if free_mode:
            size = shutil.get_terminal_size((120, 32))
            board_w = max(24, int(size.columns))
            board_h = max(12, int(size.lines - 1))
        else:
            board_w = max(18, int(game.get("board_w", 18)))
            board_h = max(6, int(game.get("board_h", 6)))
        game["board_w"] = board_w
        game["board_h"] = board_h
        # T01.03: clamp food to new board boundaries after resize
        food_raw = game.get("food")
        if isinstance(food_raw, (list, tuple)) and len(food_raw) == 2:
            fx, fy = int(food_raw[0]), int(food_raw[1])
            if fx >= board_w or fy >= board_h:
                game["food"] = (fx % board_w, fy % board_h)
        snake_raw = game.get("snake") or []
        snake = [(int(p[0]), int(p[1])) for p in snake_raw if isinstance(p, (list, tuple)) and len(p) == 2]
        if not snake:
            snake = [(6, 3), (5, 3), (4, 3), (3, 3), (2, 3)]
        snake = [((x % board_w), (y % board_h)) for x, y in snake]
        trail_raw = game.get("trail_path") or []
        trail_path = [
            (int(p[0]) % board_w, int(p[1]) % board_h)
            for p in trail_raw
            if isinstance(p, (list, tuple)) and len(p) == 2
        ]
        if not trail_path:
            trail_path = list(snake)
        marks_raw = game.get("mark_cells") or []
        marks: list[tuple[int, int, int]] = []
        for item in marks_raw:
            if not isinstance(item, (list, tuple)) or len(item) != 3:
                continue
            mx, my, ttl = int(item[0]), int(item[1]), int(item[2])
            if ttl > 0:
                marks.append((mx % board_w, my % board_h, ttl))
        vx = float(game.get("vel_x", 10.0))
        vy = float(game.get("vel_y", 0.0))
        if bool(game.get("mouse_follow_enabled")) and str(game.get("movement_mode") or "") == "mouse_follow":
            mouse = game.get("mouse_state")
            if isinstance(mouse, dict) and bool(mouse.get("active")):
                hx, hy = snake[0]
                mx = max(0, min(board_w - 1, int(mouse.get("x", hx))))
                my = max(0, min(board_h - 1, int(mouse.get("y", hy))))
                dx = mx - hx
                dy = my - hy
                smoothing = max(0.05, min(0.9, float(os.environ.get("ANANTA_TUI_MOUSE_FOLLOW_SMOOTHING", "0.28"))))
                limit = max(6.0, min(100.0, float(os.environ.get("ANANTA_TUI_MOUSE_FOLLOW_MAX_SPEED", "56.0"))))
                vx = (vx * (1.0 - smoothing)) + (dx * smoothing * 8.0)
                vy = (vy * (1.0 - smoothing)) + (dy * smoothing * 8.0)
                vx = max(-limit, min(limit, vx))
                vy = max(-limit, min(limit, vy))
        ax = float(game.get("accum_x", 0.0)) + vx * dt
        ay = float(game.get("accum_y", 0.0)) + vy * dt

        moved = 0
        safety = 120
        while safety > 0 and (abs(ax) >= 1.0 or abs(ay) >= 1.0):
            safety -= 1
            if abs(ax) >= abs(ay):
                sx = 1 if ax > 0 else -1
                sy = 0
                ax -= sx
            else:
                sx = 0
                sy = 1 if ay > 0 else -1
                ay -= sy
            hx, hy = snake[0]
            new_head = ((hx + sx) % board_w, (hy + sy) % board_h)
            snake = [new_head, *snake]
            while len(snake) > 12:
                snake.pop()
            trail_path = [new_head, *trail_path]
            mark_ttl = max(4, min(24, int(os.environ.get("ANANTA_TUI_SNAKE_MARK_TTL", "12"))))
            marks = [(mx, my, ttl - 1) for (mx, my, ttl) in marks if ttl > 1]
            marks.insert(0, (new_head[0], new_head[1], mark_ttl))
            moved += 1

        msg = str(game.get("message") or "")
        trail_max = max(96, min(800, max(len(msg) * 8, 256)))
        trail_path = trail_path[:trail_max]
        marks = marks[:trail_max]
        game["snake"] = snake
        game["trail_path"] = trail_path
        game["mark_cells"] = marks
        if abs(vx) >= abs(vy):
            game["direction"] = (1 if vx > 0 else (-1 if vx < 0 else 0), 0)
        else:
            game["direction"] = (0, 1 if vy > 0 else (-1 if vy < 0 else 0))
        game["next_direction"] = game["direction"]
        game["accum_x"] = ax
        game["accum_y"] = ay
        moves = int(game.get("moves", 0)) + max(1, moved)
        game["moves"] = moves
        game["last_move"] = now
        game["free_mode"] = free_mode

        # T01.01: in split-view, restrict board to left portion
        if free_mode and board_w >= 100:
            game["board_w"] = max(24, board_w - 42)

        # T01.05: score = moves // 20, cache highscore
        score = moves // 20
        game["score"] = score
        game["_scores_cache"] = self._scores_cache

        # T02.01: fire milestone events into tutor event queue
        self._fire_score_events(game, score=score)
        # T02.06: idle comment
        self._maybe_fire_idle_comment(game, now=now)
        # T02.03: process pending :ask question
        self._poll_tutor_ask_result(game)
        # T02.04: advance pointer blink frame
        self._tick_tutor_pointer(game, now=now)
        # E04.T04: advance tutorial step if event matches
        self._process_tutorial_event(game, event=self._snake_last_event_fired)
        self._snake_last_event_fired = ""
        # T04.04: guided tour auto-advance
        self._tick_guided_tour(game, now=now)

        # E01: process pending notes ops (pin/unpin/delete/search)
        if game.get("notes_pin_id") or game.get("notes_unpin_id") or game.get("notes_delete_id") or game.get("notes_search_query"):
            self._process_notes_ops(game)

        # E03: poll chat transport and handle incoming messages
        self._tick_chat(game, now=now)

        # E04: sync AI ask result back to AI chat channel
        self._tick_chat_ai_response(game)

        # T01.05: record section visit for first-visit explanation
        current_section = str(self.state.section_id or "dashboard")
        if current_section != getattr(self, "_last_tracked_section", ""):
            self._last_tracked_section = current_section
            self._section_first_visit_pending = current_section

        if self._section_first_visit_pending:
            self._maybe_fire_section_visit_explanation(game, section_id=self._section_first_visit_pending)
            self._section_first_visit_pending = ""

        self._tick_ai_snake_prediction(game, now=now)

        # sync tutor depth mode into game state
        game["tutor_depth_mode"] = self._tutor_depth_mode

        self._update_multi_snake_state(game, now=now, board_w=board_w, board_h=board_h)
        mode_label = "fullscreen" if free_mode else "framed"
        speed_level = int(game.get("speed_level") or 3)
        next_state = self.state.with_updates(
            header_logo_game=game,
            status_message=f"snake:{mode_label} speed:{speed_level}/5 vx={vx:.1f} vy={vy:.1f}",
        )
        self.state = self._apply_snake_hover_selection_delay(next_state, head=snake[0], now=now)

    def _tick_ai_snake_prediction(self, game: dict[str, object], *, now: float) -> None:
        section = str(self.state.section_id or "dashboard")
        self._ai_observation.add_event(kind="section", value=section, timestamp=now)
        if bool(game.get("tutorial_mode")):
            self._ai_observation.add_event(kind="chat_channel", value="ai:tutor", timestamp=now)
        artifact_ref = artifact_ref_from_game(game)
        if isinstance(artifact_ref, dict):
            self._ai_observation.add_event(
                kind="artifact",
                value=str(artifact_ref.get("path") or artifact_ref.get("label") or "artifact"),
                ref_id=str(artifact_ref.get("path") or ""),
                timestamp=now,
            )
        vx = float(game.get("vel_x") or 0.0)
        vy = float(game.get("vel_y") or 0.0)
        if abs(vx) >= abs(vy):
            movement = "right" if vx > 0.25 else ("left" if vx < -0.25 else "idle")
        else:
            movement = "down" if vy > 0.25 else ("up" if vy < -0.25 else "idle")
        self._ai_observation.add_event(kind="movement", value=movement, timestamp=now)
        self._ai_observation.add_event(
            kind="notes_active",
            value=bool((game.get("chat_state") or {}).get("notes_context_released")) if isinstance(game.get("chat_state"), dict) else False,
            timestamp=now,
        )
        summary = self._ai_observation.compact_summary(max_facts=20)
        quick = quick_predict(self._ai_observation.events(), now=now)
        prediction = quick.as_dict()
        codecompass = load_codecompass_artifact()
        ai_ctx = default_ai_context()
        set_ai_context(game, ai_ctx)
        envelope = build_context_envelope_ref(ai_ctx, codecompass_artifact=codecompass, selected_artifact_ref=artifact_ref)
        envelope["retrieval_refs"] = relevance_refs_for_intent(
            intent=str(prediction.get("predicted_intent") or "unknown"),
            codecompass_artifact=codecompass,
            max_refs=12,
        )
        signature = f"{prediction.get('predicted_intent')}|{prediction.get('target_ref')}|{section}"
        cache_key = self._ai_prediction_cache.make_key(
            section=section,
            target_ref=str(prediction.get("target_ref") or ""),
            intent_kind=str(prediction.get("predicted_intent") or "unknown"),
            context_hash=str(envelope.get("context_hash") or "missing"),
        )
        cached = self._ai_prediction_cache.get(cache_key, now=now)
        cache_hit = cached is not None
        if not cache_hit:
            self._ai_prediction_cache.set(cache_key, prediction, now=now)
        gate_decision = self._ai_prediction_gate.evaluate(
            prediction=quick,
            signature=signature,
            now=now,
            selected_artifact=isinstance(artifact_ref, dict),
        )
        selected_allowed = isinstance(artifact_ref, dict) or str(prediction.get("target_ref") or "").startswith("section:")
        notes_released = bool((game.get("chat_state") or {}).get("notes_context_released")) if isinstance(game.get("chat_state"), dict) else False
        worker_payload, worker_policy = apply_policy_to_payload(
            {
                "mode": str(game.get("ai_snake_mode") or "lurking_follow"),
                "quick_prediction": prediction,
                "context_envelope_ref": envelope,
                "observation_summary": summary,
                "notes_context": (game.get("chat_state") or {}).get("notes_context"),
            },
            boundary="worker_request",
            notes_released=notes_released,
            selected_artifact_allowed=selected_allowed,
            external_provider=False,
        )
        prompt_payload, prompt_policy = apply_policy_to_payload(
            {
                "quick_prediction": prediction,
                "observation_summary": summary,
                "notes_context": (game.get("chat_state") or {}).get("notes_context"),
            },
            boundary="lmstudio_prompt",
            notes_released=notes_released,
            selected_artifact_allowed=selected_allowed,
            external_provider=False,
        )
        ai_mode = str(game.get("ai_snake_mode") or "lurking_follow")
        follow_state_raw = game.get("ai_snake_follow_state")
        follow_state = dict(follow_state_raw) if isinstance(follow_state_raw, dict) else make_follow_state(mode=ai_mode)
        local_snake = game.get("snake")
        if isinstance(local_snake, list) and local_snake:
            head = local_snake[0]
            if isinstance(head, (list, tuple)) and len(head) == 2:
                follow_state["mode"] = ai_mode
                follow_state = step_follow_state(
                    follow_state,
                    user_position=(int(head[0]), int(head[1])),
                    board_w=max(1, int(game.get("board_w") or 18)),
                    board_h=max(1, int(game.get("board_h") or 6)),
                )
        response = game.get("ai_snake_worker_response")
        if isinstance(response, dict):
            follow_state = apply_worker_follow_update(
                follow_state,
                follow_mode_update=str(response.get("follow_mode_update") or ""),
                prediction_target=str(response.get("target_ref") or ""),
                confidence=float(response.get("confidence") or 0.0),
            )

        runtime_status = "idle"
        if ai_mode == "off":
            runtime_status = "off"
        elif ai_mode == "quiet":
            runtime_status = "quiet"
        elif ai_mode == "point_to_target":
            runtime_status = "pointing"
        elif str(follow_state.get("mode") or "") == "follow":
            runtime_status = "following"
        elif str(follow_state.get("mode") or "") == "lurking":
            runtime_status = "lurking"
        allow_proactive_comment = (
            gate_decision.allow_worker_request
            and float(prediction.get("confidence") or 0.0) >= 0.65
            and ai_mode != "off"
            and prompt_policy.allowed
        )
        if isinstance(game.get("chat_state"), dict):
            from client_surfaces.operator_tui.chat_state import maybe_add_prediction_comment

            forced = bool(game.pop("ai_force_question", False))
            maybe_add_prediction_comment(
                cast(dict[str, Any], game["chat_state"]),
                prediction=prediction,
                now=now,
                quiet=(ai_mode == "quiet"),
                forced=forced,
                cooldown_seconds=20,
            ) if (allow_proactive_comment or forced) else None

        game["ai_snake_prediction"] = prediction
        game["ai_snake_context_envelope"] = envelope
        game["ai_snake_follow_state"] = follow_state
        game["ai_snake_runtime_status"] = runtime_status
        game["ai_snake_debug"] = {
            "observation_summary": summary,
            "cache_hit": cache_hit,
            "gate_reason": gate_decision.reason,
            "skipped_worker_requests": gate_decision.skipped_worker_requests,
            "allow_worker_request": gate_decision.allow_worker_request,
            "policy": {
                "worker_request": worker_policy.as_dict(),
                "lmstudio_prompt": prompt_policy.as_dict(),
            },
            "policy_payload_preview": {
                "worker_request": worker_payload,
                "lmstudio_prompt": prompt_payload,
            },
            "allow_proactive_comment": allow_proactive_comment,
        }

    def _update_multi_snake_state(
        self,
        game: dict[str, object],
        *,
        now: float,
        board_w: int,
        board_h: int,
    ) -> None:
        snakes_raw = game.get("snakes")
        snakes: dict[str, dict[str, object]]
        if isinstance(snakes_raw, dict):
            snakes = {str(k): dict(v) for k, v in snakes_raw.items() if isinstance(v, dict)}
        else:
            snakes = {}
        local_id = str(game.get("local_snake_id") or "s1")
        local_pseudonym = str(game.get("pseudonym") or os.environ.get("ANANTA_TUI_SNAKE_PSEUDONYM", "local-snake"))
        local_provider = str(game.get("oidc_provider") or os.environ.get("ANANTA_TUI_SNAKE_OIDC_PROVIDER", "local"))
        local_snapshot = {
            "id": local_id,
            "pseudonym": local_pseudonym,
            "oidc_provider": local_provider,
            "snake": list(game.get("snake") or []),
            "trail_path": list(game.get("trail_path") or []),
            "mark_cells": list(game.get("mark_cells") or []),
            "selection_cells": list(game.get("selection_cells") or []),
            "selection_regions": list(game.get("selection_regions") or []),
            "message": str(game.get("message") or ""),
            "message_style": str(game.get("message_style") or "trail"),
            "snake_color": str(game.get("snake_color") or "mint"),
            "trail_window": int(game.get("trail_window") or 10),
            "trail_speed": float(game.get("trail_speed") or 8.0),
            "active": True,
            "updated_at": now,
            "local": True,
            "access_level": "full",
        }
        snakes[local_id] = local_snapshot
        self._update_demo_remote_snakes(snakes, now=now, board_w=board_w, board_h=board_h)
        self._update_tutorial_ai_snake(game, snakes, now=now, board_w=board_w, board_h=board_h, enabled=bool(game.get("tutorial_mode")))
        game["snakes"] = snakes
        game["local_snake_id"] = local_id

    def _update_tutorial_ai_snake(
        self,
        game: dict[str, object],
        snakes: dict[str, dict[str, object]],
        *,
        now: float,
        board_w: int,
        board_h: int,
        enabled: bool,
    ) -> None:
        sid = "s-ai"
        if not enabled:
            snakes.pop(sid, None)
            return
        hints = self._load_codecompass_hints(now=now)
        rag_context = self._load_rag_helper_context(now=now)
        context_tokens = [*hints[:10], *rag_context[:10]]
        intent_confidence = str(game.get("artifact_intent_confidence") or "none")
        artifact_target = game.get("artifact_intent_target")
        target_mode = "follow_user"
        if intent_confidence in {"likely", "confirmed"}:
            target_mode = "fast_target"
            if isinstance(artifact_target, dict):
                context_tokens.insert(0, f"artifact:{artifact_target.get('label')}")
                context_tokens.insert(0, f"target:{artifact_target.get('pane') or 'content'}")
        if self._tutorial_worker_target_hint:
            context_tokens.insert(0, f"target:{self._tutorial_worker_target_hint}")
        local = snakes.get(str(self.state.header_logo_game.get("local_snake_id", "s1"))) if isinstance(self.state.header_logo_game, dict) else None
        local_head = None
        if isinstance(local, dict):
            local_snake = local.get("snake")
            if isinstance(local_snake, list) and local_snake:
                head = local_snake[0]
                if isinstance(head, (list, tuple)) and len(head) == 2:
                    local_head = (int(head[0]) % max(1, board_w), int(head[1]) % max(1, board_h))
        target = self._tutorial_ai_target_cell(
            board_w=board_w,
            board_h=board_h,
            context_tokens=context_tokens,
            local_head=local_head,
        )
        artifact_cell = game.get("artifact_target_cell")
        if target_mode == "fast_target" and isinstance(artifact_cell, (list, tuple)) and len(artifact_cell) == 2:
            target = (int(artifact_cell[0]) % max(1, board_w), int(artifact_cell[1]) % max(1, board_h))
        existing = snakes.get(sid, {})
        existing_snake_raw = existing.get("snake") if isinstance(existing, dict) else []
        existing_snake = [
            (int(p[0]) % max(1, board_w), int(p[1]) % max(1, board_h))
            for p in (existing_snake_raw or [])
            if isinstance(p, (list, tuple)) and len(p) == 2
        ]
        if existing_snake:
            start_head = existing_snake[0]
        else:
            start_head = ((target[0] - 1) % max(1, board_w), target[1] % max(1, board_h))
            existing_snake = [start_head]
        new_head = self._step_toward_cell(
            current=start_head,
            target=target,
            board_w=board_w,
            board_h=board_h,
        )
        if target_mode == "fast_target":
            new_head = self._step_toward_cell(
                current=new_head,
                target=target,
                board_w=board_w,
                board_h=board_h,
            )
        body = [new_head, *existing_snake]
        while len(body) < 10:
            tx = (body[-1][0] - 1) % max(1, board_w)
            body.append((tx, body[-1][1]))
        body = body[:10]
        trail = list(body)
        ai_local_contact = False
        contact_zone = ""
        if local_head is not None:
            ai_local_contact = abs(new_head[0] - local_head[0]) + abs(new_head[1] - local_head[1]) <= 1
            if ai_local_contact:
                contact_zone = self._tutorial_target_label(board_w=board_w, board_h=board_h, target=local_head)
        game["tutorial_ai_local_contact"] = ai_local_contact
        game["tutorial_ai_contact_zone"] = contact_zone
        game["tutorial_ai_contact_at"] = float(now) if ai_local_contact else 0.0
        game["tutorial_ai_target_mode"] = target_mode
        if target_mode == "fast_target":
            dist = abs(new_head[0] - target[0]) + abs(new_head[1] - target[1])
            if dist <= 1:
                game["tutorial_ai_target_mode"] = "explain_target"
                self._append_artifact_chat_ai_message(game=game, now=now, text="Ziel erreicht. Ich erkläre dieses Artefakt im Kontext.")

        tip = self._tutorial_ai_tip(now=now)
        target_label = self._tutorial_last_target or self._tutorial_target_label(board_w=board_w, board_h=board_h, target=target)
        source_label = self._tutorial_last_source or "codecompass-rag"
        self._record_tutorial_propose_event(
            game,
            now=now,
            source=source_label,
            target=target_label,
            text=tip,
        )
        existing_access = str(existing.get("access_level") or "view") if isinstance(existing, dict) else "view"
        snakes[sid] = {
            "id": sid,
            "pseudonym": "tutor-ai",
            "oidc_provider": "codecompass-ai",
            "snake": body,
            "trail_path": trail,
            "selection_cells": [],
            "message": tip,
            "message_style": "ticker",
            "snake_color": "amber",
            "trail_window": 28,
            "trail_speed": 8.0,
            "active": True,
            "updated_at": now,
            "local": False,
            "knowledge_scope": ("tui", "architecture", "workflow"),
            "target_cell": target,
            "mode": game.get("tutorial_ai_target_mode") or "follow_user",
            "access_level": existing_access,
        }

    def _tutorial_target_label(
        self,
        *,
        board_w: int,
        board_h: int,
        target: tuple[int, int],
    ) -> str:
        tx, ty = int(target[0]), int(target[1])
        if ty <= max(2, board_h // 6):
            return "header"
        if tx <= max(2, board_w // 4):
            return "nav"
        if tx >= max(2, board_w - max(8, board_w // 4)) and ty >= max(2, board_h - max(6, board_h // 4)):
            return "detail"
        return "content"

    def _record_tutorial_propose_event(
        self,
        game: dict[str, object],
        *,
        now: float,
        source: str,
        target: str,
        text: str,
    ) -> None:
        history_raw = game.get("tutorial_propose_history")
        history: list[dict[str, object]]
        if isinstance(history_raw, list):
            history = [dict(entry) for entry in history_raw if isinstance(entry, dict)]
        else:
            history = []
        entry = {
            "at": float(now),
            "source": str(source or "unknown"),
            "target": str(target or "content"),
            "text": str(text or "").strip(),
        }
        if not entry["text"]:
            return
        last = history[-1] if history else None
        if isinstance(last, dict):
            if (
                str(last.get("source") or "") == str(entry["source"])
                and str(last.get("target") or "") == str(entry["target"])
                and str(last.get("text") or "") == str(entry["text"])
            ):
                return
        history.append(entry)
        game["tutorial_propose_history"] = history[-8:]

    def _tutorial_ai_target_cell(
        self,
        *,
        board_w: int,
        board_h: int,
        context_tokens: list[str],
        local_head: tuple[int, int] | None,
    ) -> tuple[int, int]:
        text = " ".join(context_tokens).lower()
        if "target:header" in text:
            return (max(0, board_w - max(4, board_w // 6)), max(1, board_h // 6))
        if "target:nav" in text:
            return (max(1, board_w // 5), max(2, board_h // 2))
        if "target:content" in text:
            return (max(2, board_w // 2), max(2, board_h // 2))
        if "target:detail" in text:
            return (max(2, board_w - max(8, board_w // 4)), max(2, board_h - max(4, board_h // 4)))
        if "target:follow" in text and local_head is not None:
            return ((local_head[0] + 3) % max(1, board_w), local_head[1] % max(1, board_h))
        if any(token in text for token in ("endpoint", "auth", "header", "config", "oidc")):
            return (max(0, board_w - max(4, board_w // 6)), max(1, board_h // 6))
        if any(token in text for token in ("task", "goal", "section", "navigation", "queue")):
            return (max(1, board_w // 5), max(2, board_h // 2))
        if any(token in text for token in ("detail", "inspect", "artifact", "context", "result")):
            return (max(2, board_w - max(8, board_w // 4)), max(2, board_h - max(4, board_h // 4)))
        if local_head is not None:
            return ((local_head[0] + 3) % max(1, board_w), local_head[1] % max(1, board_h))
        return (max(2, board_w // 2), max(2, board_h // 2))

    def _step_toward_cell(
        self,
        *,
        current: tuple[int, int],
        target: tuple[int, int],
        board_w: int,
        board_h: int,
    ) -> tuple[int, int]:
        cx, cy = int(current[0]), int(current[1])
        tx, ty = int(target[0]), int(target[1])
        dx = tx - cx
        dy = ty - cy
        if abs(dx) >= abs(dy) and dx != 0:
            step_x = 1 if dx > 0 else -1
            return ((cx + step_x) % max(1, board_w), cy % max(1, board_h))
        if dy != 0:
            step_y = 1 if dy > 0 else -1
            return (cx % max(1, board_w), (cy + step_y) % max(1, board_h))
        return (cx % max(1, board_w), cy % max(1, board_h))

    def _tutorial_ai_tip(self, *, now: float) -> str:
        status = self._tutorial_status_delta_summary()
        hints = self._load_codecompass_hints(now=now)
        rag_context = self._load_rag_helper_context(now=now)
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
        backend = str(os.environ.get("ANANTA_TUI_SNAKE_AI_BACKEND", "")).strip().lower()
        if backend not in {"worker-propose", "worker", "opencode", "hermes"}:
            return None
        refresh_seconds = max(2.0, min(60.0, float(os.environ.get("ANANTA_TUI_SNAKE_AI_REFRESH", "8.0"))))
        cached_at, cached_msg = self._tutorial_worker_cache
        if cached_msg and (now - cached_at) < refresh_seconds:
            self._tutorial_last_source = "worker-propose"
            if self._tutorial_worker_target_hint:
                self._tutorial_last_target = self._tutorial_worker_target_hint
            return cached_msg

        base_url = str(self.state.endpoint or os.environ.get("ANANTA_BASE_URL") or "http://localhost:5000").strip()
        if not base_url:
            return None
        timeout_seconds = max(0.3, min(12.0, float(os.environ.get("ANANTA_TUI_SNAKE_AI_TIMEOUT", "1.6"))))
        model = str(os.environ.get("ANANTA_TUI_SNAKE_AI_MODEL", "")).strip()
        provider = str(os.environ.get("ANANTA_TUI_SNAKE_AI_WORKER_PROVIDER", "")).strip()
        if not provider and backend in {"opencode", "hermes"}:
            provider = backend

        hint_block = "\n".join(f"- {h}" for h in hints[:8]) if hints else "- no codecompass hints"
        rag_block = "\n".join(f"- {h}" for h in rag_context[:8]) if rag_context else "- no rag_helper context"
        prompt = (
            f"{status}\n"
            "You are the tutorial snake controller for Ananta TUI.\n"
            "Use CodeCompass and rag_helper context.\n"
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

    def _tutorial_ai_llm_message(self, *, now: float, status: str, hints: list[str]) -> str | None:
        model = str(os.environ.get("ANANTA_TUI_SNAKE_AI_MODEL") or "google/gemma-4-e4b").strip()
        api_base = str(
            os.environ.get("ANANTA_TUI_SNAKE_AI_API_BASE_URL")
            or os.environ.get("OPENAI_BASE_URL")
            or os.environ.get("OPENAI_API_BASE")
            or "http://192.168.178.100:1234"
        ).strip()
        api_token = str(
            os.environ.get("ANANTA_TUI_SNAKE_AI_API_TOKEN")
            or os.environ.get("OPENAI_API_KEY")
            or ""
        ).strip()
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
            "max_tokens": max(24, min(120, int(max_tokens))),
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

    def _load_codecompass_hints(self, *, now: float) -> list[str]:
        cached_at, cached = self._tutorial_codecompass_cache
        if cached and (now - cached_at) < 6.0:
            return cached

        out_dir = self._resolve_codecompass_output_dir()
        if out_dir is None:
            self._tutorial_codecompass_cache = (now, [])
            return []

        try:
            from worker.retrieval.codecompass_output_reader import CodeCompassOutputReader
        except Exception:
            self._tutorial_codecompass_cache = (now, [])
            return []

        try:
            payload = CodeCompassOutputReader().load_from_output_dir(output_dir=out_dir)
            records = payload.get("records") if isinstance(payload, dict) else []
            if not isinstance(records, list):
                records = []
            hints: list[str] = []
            for record in records:
                if not isinstance(record, dict):
                    continue
                kind = str(record.get("kind") or record.get("type") or "").strip()
                file_path = str(record.get("file") or record.get("path") or "").strip()
                name = str(record.get("name") or record.get("id") or "").strip()
                if not (kind or file_path or name):
                    continue
                parts = []
                if kind:
                    parts.append(kind)
                if name:
                    parts.append(name)
                if file_path:
                    parts.append(file_path)
                hint = " · ".join(parts)
                if hint:
                    hints.append(hint)
                if len(hints) >= 64:
                    break
            self._tutorial_codecompass_cache = (now, hints)
            return hints
        except Exception:
            self._tutorial_codecompass_cache = (now, [])
            return []

    def _load_rag_helper_context(self, *, now: float) -> list[str]:
        cached_at, cached = self._tutorial_rag_cache
        if cached and (now - cached_at) < 6.0:
            return cached

        out_dir = self._resolve_codecompass_output_dir()
        if out_dir is None:
            self._tutorial_rag_cache = (now, [])
            return []

        manifest_path = out_dir / "manifest.json"
        files: list[str] = []
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                manifest = {}
            partitioned = manifest.get("partitioned_outputs") if isinstance(manifest, dict) else None
            if isinstance(partitioned, dict):
                for values in partitioned.values():
                    if not isinstance(values, list):
                        continue
                    for item in values:
                        rel = str(item or "").strip()
                        if rel:
                            files.append(rel)
        files.extend(
            [
                "context.jsonl",
                "details.jsonl",
                "index.jsonl",
                "xml_overview.jsonl",
                "embedding.jsonl",
                "graph_nodes.jsonl",
                "graph_edges.jsonl",
                "relations.jsonl",
            ]
        )
        deduped_files: list[str] = []
        seen_files: set[str] = set()
        for rel in files:
            normalized = rel.strip().lstrip("/")
            if not normalized or normalized in seen_files:
                continue
            seen_files.add(normalized)
            deduped_files.append(normalized)

        query_tokens = self._tutorial_relevance_tokens()
        top_k = max(12, min(96, int(os.environ.get("ANANTA_TUI_SNAKE_RAG_TOP_K", "48"))))
        candidates: list[tuple[float, str]] = []
        max_records_per_file = max(80, min(3000, int(os.environ.get("ANANTA_TUI_SNAKE_RAG_MAX_RECORDS_PER_FILE", "800"))))
        for rel in deduped_files[:24]:
            path = out_dir / rel
            if not path.exists() or not path.is_file() or path.suffix.lower() != ".jsonl":
                continue
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except Exception:
                continue
            source_kind = path.name.lower().replace(".jsonl", "")
            for idx, line in enumerate(lines):
                if idx >= max_records_per_file:
                    break
                payload = line.strip()
                if not payload:
                    continue
                try:
                    parsed = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if not isinstance(parsed, dict):
                    continue
                source_file = str(
                    parsed.get("file")
                    or parsed.get("path")
                    or parsed.get("source_file")
                    or parsed.get("source_path")
                    or parsed.get("target_file")
                    or parsed.get("target_path")
                    or ""
                ).strip()
                if source_kind in {"graph_nodes", "graph_edges"} and source_file:
                    source_lower = source_file.lower()
                    if "client_surfaces/operator_tui" not in source_lower and "/operator_tui/" not in source_lower:
                        target_file = str(parsed.get("target_file") or parsed.get("target_path") or "").lower()
                        if "client_surfaces/operator_tui" not in target_file and "/operator_tui/" not in target_file:
                            continue

                tokens = [
                    str(parsed.get("domain") or "").strip(),
                    str(parsed.get("kind") or "").strip(),
                    str(parsed.get("title") or "").strip(),
                    str(parsed.get("section_title") or "").strip(),
                    str(parsed.get("name") or "").strip(),
                    source_file,
                    str(parsed.get("source_id") or parsed.get("from") or "").strip(),
                    str(parsed.get("target_id") or parsed.get("to") or "").strip(),
                    str(parsed.get("relation") or parsed.get("type") or "").strip(),
                    str(parsed.get("embedding_text") or "").strip(),
                    str(parsed.get("summary") or "").strip(),
                    str(parsed.get("content") or parsed.get("text") or "").strip(),
                ]
                text = " · ".join(part for part in tokens if part)
                compact = " ".join(text.split())
                if not compact:
                    continue
                compact = f"{source_kind} · {compact}"
                score = self._tutorial_context_relevance_score(compact, query_tokens=query_tokens)
                if source_kind in {"embedding", "graph_nodes", "graph_edges"}:
                    score += 0.8
                if "client_surfaces/operator_tui" in compact.lower():
                    score += 1.2
                if score <= 0:
                    continue
                candidates.append((score, compact[:240]))
        ranked = sorted(candidates, key=lambda item: item[0], reverse=True)
        context = [item[1] for item in ranked[:top_k]]
        self._tutorial_rag_cache = (now, context)
        return context

    def _tutorial_relevance_tokens(self) -> list[str]:
        game = dict(self.state.header_logo_game or {})
        raw_parts = [
            str(self.state.section_id or ""),
            str(self.state.focus.value or ""),
            str(self.state.mode.value or ""),
            str(game.get("tutorial_user_feed") or ""),
            str(game.get("tutorial_ai_contact_zone") or ""),
            "ananta tui snake operator_tui interactive renderer prompt propose",
        ]
        text = " ".join(raw_parts).lower()
        return [token for token in re.findall(r"[a-z0-9_./-]+", text) if len(token) >= 2][:64]

    def _tutorial_context_relevance_score(self, text: str, *, query_tokens: list[str]) -> float:
        haystack = str(text or "").lower()
        if not haystack:
            return 0.0
        if not query_tokens:
            return 0.5
        score = 0.0
        for token in query_tokens:
            count = haystack.count(token)
            if count <= 0:
                continue
            score += 1.0 + min(0.6, (count - 1) * 0.15)
        if "embedding_text" in haystack or "embedding" in haystack:
            score += 0.3
        if "graph_edges" in haystack or "graph_nodes" in haystack:
            score += 0.3
        return score

    def _resolve_codecompass_output_dir(self) -> Path | None:
        candidates = [
            os.environ.get("ANANTA_TUI_CODECOMPASS_OUTPUT_DIR"),
            os.environ.get("CODECOMPASS_OUTPUT_DIR"),
            os.environ.get("ANANTA_CODECOMPASS_OUTPUT_DIR"),
            "rag-helper/out",
            "rag-helper/output",
            "codecompass-out",
        ]
        if self._codecompass_build_output_dir is not None:
            candidates.insert(0, str(self._codecompass_build_output_dir))
        for raw in candidates:
            if not raw:
                continue
            path = Path(raw).expanduser()
            if not path.is_absolute():
                path = (Path.cwd() / path).resolve()
            if path.exists() and path.is_dir() and (path / "index.jsonl").exists():
                return path
        built_dir = self._poll_codecompass_output_build()
        if built_dir is not None and built_dir.exists() and (built_dir / "index.jsonl").exists():
            return built_dir
        self._ensure_codecompass_output_build_started()
        return None

    def _ensure_codecompass_output_build_started(self) -> None:
        auto_enabled = str(os.environ.get("ANANTA_TUI_AUTO_BUILD_CODECOMPASS", "1")).strip().lower() in {"1", "true", "yes", "on"}
        if not auto_enabled:
            return
        if self._codecompass_build_future is not None and not self._codecompass_build_future.done():
            return
        self._codecompass_build_future = self._codecompass_build_executor.submit(self._build_codecompass_outputs_sync)

    def _poll_codecompass_output_build(self) -> Path | None:
        future = self._codecompass_build_future
        if future is None or not future.done():
            return None
        self._codecompass_build_future = None
        built = future.result()
        if built is None:
            return None
        self._codecompass_build_output_dir = built
        return built

    def _build_codecompass_outputs_sync(self) -> Path | None:
        root_dir = Path.cwd()
        candidate_scripts = [
            root_dir / "rag-helper" / "codecompass_rag.py",
            root_dir / "codecompass_rag.py",
        ]
        script_path = next((path for path in candidate_scripts if path.exists() and path.is_file()), None)
        if script_path is None:
            return None
        output_dir = (root_dir / "rag-helper" / "out").resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        command = [
            "python3",
            str(script_path),
            str(root_dir),
            "-o",
            str(output_dir),
            "--retrieval-output-mode",
            "both",
            "--graph-export-mode",
            "jsonl",
            "--relation-output-mode",
            "both",
            "--output-partition-mode",
            "by-kind",
        ]
        timeout_seconds = max(20, min(900, int(os.environ.get("ANANTA_TUI_CODECOMPASS_BUILD_TIMEOUT", "240"))))
        try:
            completed = subprocess.run(
                command,
                cwd=str(root_dir),
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if completed.returncode != 0:
            return None
        return output_dir if (output_dir / "index.jsonl").exists() else None

    def _update_demo_remote_snakes(
        self,
        snakes: dict[str, dict[str, object]],
        *,
        now: float,
        board_w: int,
        board_h: int,
    ) -> None:
        demo_peers = max(0, min(3, int(os.environ.get("ANANTA_TUI_SNAKE_DEMO_PEERS", "0"))))
        if demo_peers <= 0:
            return
        radius_x = max(3, board_w // 7)
        radius_y = max(2, board_h // 5)
        center_x = board_w // 2
        center_y = board_h // 2
        for i in range(demo_peers):
            sid = f"s{i + 2}"
            existing = snakes.get(sid, {})
            access_level = str(existing.get("access_level") or "cancel")
            phase = now * (0.9 + i * 0.3)
            hx = int(center_x + radius_x * math.sin(phase + i * 1.7)) % max(1, board_w)
            hy = int(center_y + radius_y * math.cos(phase + i * 1.3)) % max(1, board_h)
            target_pixel = PixelPoint(float(hx * 8), float(hy * 16))
            prev_px = float(existing.get("pixel_x") or target_pixel.x)
            prev_py = float(existing.get("pixel_y") or target_pixel.y)
            intent_level = str((self.state.header_logo_game or {}).get("artifact_intent_confidence") or "none")
            speed = pixel_boost_speed(base_speed=2.2 + i * 0.4, artifact_intent=intent_level)
            smoothed = smooth_follow(
                current=PixelPoint(prev_px, prev_py),
                target=target_pixel,
                speed=speed,
                dt=max(0.01, min(0.25, 0.08 + (i * 0.02))),
            )
            body = []
            for j in range(8):
                bx = (hx - (j % 4)) % max(1, board_w)
                by = (hy - (j // 4)) % max(1, board_h)
                body.append((bx, by))
            trail = list(body)
            min_x = max(0, hx - 1)
            max_x = min(max(0, board_w - 1), hx + 1)
            min_y = max(0, hy - 1)
            max_y = min(max(0, board_h - 1), hy + 1)
            selection_cells = [(x, y) for y in range(min_y, max_y + 1) for x in (min_x, max_x)]
            selection_cells += [(x, y) for x in range(min_x, max_x + 1) for y in (min_y, max_y)]
            snakes[sid] = {
                "id": sid,
                "pseudonym": f"peer-{i + 2}",
                "oidc_provider": "demo-oidc",
                "snake": body,
                "trail_path": trail,
                "selection_cells": selection_cells,
                "message": f"peer-{i + 2}",
                "message_style": ("orbit" if i % 2 == 0 else "trail"),
                "snake_color": ("cyan" if i % 2 == 0 else "violet"),
                "trail_window": 10,
                "trail_speed": 8.0,
                "active": True,
                "updated_at": now,
                "local": False,
                "access_level": access_level,
                "pixel_x": round(smoothed.x, 3),
                "pixel_y": round(smoothed.y, 3),
            }

    def _apply_snake_hover_selection_delay(
        self,
        state: OperatorState,
        *,
        head: tuple[int, int],
        now: float,
    ) -> OperatorState:
        """Only apply selectable-option focus after a short hover delay."""
        game = dict(state.header_logo_game or {})
        if not game.get("active"):
            return state
        size = shutil.get_terminal_size((120, 32))
        width = max(72, int(size.columns))
        x, y = head
        x = max(0, min(width - 1, int(x)))
        y = max(0, int(y))

        # Approximate body start from renderer layout (header + rule).
        body_start = 9
        left_width = 22
        candidate: tuple[str, int] | None = None
        if y >= body_start + 1 and x < left_width:
            row = y - (body_start + 1)
            if 0 <= row < len(SECTIONS):
                candidate = ("nav", row)

        if candidate is None:
            game.pop("pending_select_target", None)
            game.pop("pending_select_since", None)
            return state.with_updates(header_logo_game=game)

        delay = max(0.10, min(2.0, float(os.environ.get("ANANTA_TUI_SNAKE_SELECT_DELAY", "0.45"))))
        pending = game.get("pending_select_target")
        since = float(game.get("pending_select_since", now))
        if pending != candidate:
            game["pending_select_target"] = candidate
            game["pending_select_since"] = now
            return state.with_updates(header_logo_game=game, status_message="snake: option anvisiert…")
        if (now - since) < delay:
            return state.with_updates(header_logo_game=game)

        pane, idx = candidate
        game.pop("pending_select_target", None)
        game.pop("pending_select_since", None)
        if pane == "nav":
            return state.with_updates(
                focus=FocusPane.NAVIGATION,
                selected_index=max(0, min(len(SECTIONS) - 1, idx)),
                header_logo_game=game,
                status_message="snake: option gewählt",
            )
        return state.with_updates(header_logo_game=game)

    def _snake_mode_active(self, game: dict[str, object] | None = None) -> bool:
        g = game if game is not None else dict(self.state.header_logo_game or {})
        return bool(g.get("active") and g.get("ui_steering"))

    def _toggle_snake_mode(self) -> None:
        game = dict(self.state.header_logo_game or self._default_header_snake())
        if self._snake_mode_active(game):
            game["active"] = False
            game["ui_steering"] = False
            game["free_mode"] = False
            game["message_mode"] = False
            game["message_draft"] = ""
            game["selection_anchor"] = None
            game["selection_cells"] = []
            game["selection_regions"] = []
            game["selection_frame_mode"] = False
            game["selection_frame_anchor"] = None
            self._set_state(self.state.with_updates(header_logo_game=game, status_message="snake mode: aus"))
            return
        game["active"] = True
        game["ui_steering"] = True
        game["free_mode"] = True
        game["mouse_follow_enabled"] = bool(game.get("mouse_follow_enabled", self._mouse_capabilities.get("enabled")))
        game["movement_mode"] = "mouse_follow" if bool(game.get("mouse_follow_enabled")) else "keyboard"
        game["message_mode"] = False
        game["message_draft"] = ""
        game["message_style"] = str(game.get("message_style") or "trail")
        game["snake_color"] = str(game.get("snake_color") or "mint")
        game["selection_anchor"] = None
        game["selection_cells"] = []
        game["selection_regions"] = []
        game["selection_frame_mode"] = False
        game["selection_frame_anchor"] = None
        game["last_move"] = time.monotonic()
        self._set_state(self.state.with_updates(header_logo_game=game, status_message="snake mode: an"))

    def _toggle_tutorial_ai_mode(self) -> None:
        game = dict(self.state.header_logo_game or self._default_header_snake())
        enabled = bool(game.get("tutorial_mode"))
        game["tutorial_mode"] = not enabled
        label = "an" if not enabled else "aus"
        self._fire_tutorial_event(game, "tutorial_toggled")
        self._set_state(self.state.with_updates(header_logo_game=game, status_message=f"snake tutorial-ai: {label}"))

    # ── T01.02: pause/resume ──────────────────────────────────────────────────

    def _toggle_snake_pause(self) -> None:
        game = dict(self.state.header_logo_game or {})
        if not game:
            return
        paused = bool(game.get("paused"))
        game["paused"] = not paused
        if not paused:
            # entering pause: zero velocity but keep position
            game["vel_x"] = 0.0
            game["vel_y"] = 0.0
            self._snake_idle_since = time.monotonic()
            status = "snake: pausiert [ Space zum Fortsetzen ]"
        else:
            # resuming
            game["last_move"] = time.monotonic()
            self._snake_idle_since = 0.0
            status = "snake: fortgesetzt"
        self._fire_tutorial_event(game, "snake_paused" if not paused else "any_key")
        self._set_state(self.state.with_updates(header_logo_game=game, status_message=status))

    # ── T01.03: terminal size warning exposed via tick (already in renderer) ──

    # ── T02.01: event-driven tutor explanations ───────────────────────────────

    def _fire_score_events(self, game: dict[str, object], *, score: int) -> None:
        prev_score = int(game.get("_prev_score") or 0)
        game["_prev_score"] = score
        milestones = {5: "level_up_5", 10: "level_up_10", 20: "level_up_20"}
        for threshold, event in milestones.items():
            if prev_score < threshold <= score and event not in self._tutor_event_session_used:
                self._queue_tutor_event(game, event)

    def _queue_tutor_event(self, game: dict[str, object], event_key: str) -> None:
        if event_key in self._tutor_event_session_used:
            return
        self._tutor_event_session_used.add(event_key)
        queue: list[dict[str, object]] = list(game.get("tutor_event_queue") or [])
        priority = {"collision_wall": 5, "collision_self": 5, "level_up_20": 4,
                    "level_up_10": 3, "level_up_5": 3, "zone_header": 2,
                    "zone_nav": 2, "zone_content": 2, "zone_detail": 2,
                    "food_eaten": 1}.get(event_key, 1)
        queue.append({"event": event_key, "priority": priority, "at": time.monotonic()})
        # Keep at most 5 entries, drop lowest priority if full
        queue.sort(key=lambda e: (-int(e.get("priority") or 0), float(e.get("at") or 0)))
        game["tutor_event_queue"] = queue[:5]

    def _dequeue_tutor_event(self, game: dict[str, object]) -> str:
        queue: list[dict[str, object]] = list(game.get("tutor_event_queue") or [])
        if not queue:
            return ""
        queue.sort(key=lambda e: (-int(e.get("priority") or 0), float(e.get("at") or 0)))
        event_key = str(queue[0].get("event") or "")
        game["tutor_event_queue"] = queue[1:]
        return event_key

    def _get_tutor_text(self, event_key: str) -> str:
        depth = self._tutor_depth_mode
        try:
            from pathlib import Path
            import yaml as _yaml
            yaml_path = Path(__file__).parent / "snake_tutor_texts.yaml"
            data = _yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
            # try events first, then sections, then idle
            for category in ("events", "sections"):
                bucket = data.get(category, {})
                if event_key in bucket:
                    texts = bucket[event_key]
                    if isinstance(texts, dict):
                        text = str(texts.get(depth) or texts.get("overview") or "")
                        return text.strip().replace("\n", " ").replace("  ", " ")
            return ""
        except Exception:
            return ""

    def _get_idle_tutor_text(self) -> str:
        depth = self._tutor_depth_mode
        try:
            from pathlib import Path
            import yaml as _yaml
            import random
            yaml_path = Path(__file__).parent / "snake_tutor_texts.yaml"
            data = _yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
            idle_list = data.get("idle", [])
            if not idle_list:
                return ""
            entry = random.choice(idle_list)
            if isinstance(entry, dict):
                return str(entry.get(depth) or entry.get("overview") or "").strip().replace("\n", " ").replace("  ", " ")
            return ""
        except Exception:
            return ""

    # ── T02.06: idle comments ─────────────────────────────────────────────────

    def _maybe_fire_idle_comment(self, game: dict[str, object], *, now: float) -> None:
        if bool(game.get("tutor_silent")):
            return
        if not bool(game.get("tutorial_mode")):
            return
        idle_threshold = 8.0
        if self._snake_idle_since == 0.0:
            self._snake_idle_since = now
        idle_duration = now - self._snake_idle_since
        last_idle_at = float(game.get("_last_idle_comment_at") or 0.0)
        if idle_duration >= idle_threshold and (now - last_idle_at) >= 60.0:
            tip = self._get_idle_tutor_text()
            if tip:
                game["_last_idle_comment_at"] = now
                self._inject_tutor_tip(game, tip, source="idle")

    def _inject_tutor_tip(self, game: dict[str, object], tip: str, *, source: str = "event") -> None:
        history: list[dict[str, object]] = list(game.get("tutorial_propose_history") or [])
        history.append({"at": time.monotonic(), "source": source, "target": "content", "text": tip})
        game["tutorial_propose_history"] = history[-10:]
        # T02.04: detect section references and set tutor pointer
        self._maybe_set_tutor_pointer(game, tip)
        # also update s-ai message if it exists
        snakes_raw = game.get("snakes")
        if isinstance(snakes_raw, dict):
            snakes = dict(snakes_raw)
            ai = dict(snakes.get("s-ai") or {})
            if ai:
                ai["message"] = tip
                snakes["s-ai"] = ai
                game["snakes"] = snakes

    def _maybe_set_tutor_pointer(self, game: dict[str, object], tip: str) -> None:
        """T02.04: wenn ein Sektionsname im Tip vorkommt, Pointer darauf setzen."""
        from client_surfaces.operator_tui.sections import SECTIONS
        tip_lower = tip.lower()
        for section in SECTIONS:
            if section.id in tip_lower or section.title.lower() in tip_lower:
                game["tutor_pointer"] = {
                    "target": section.id,
                    "expires": time.monotonic() + 2.0,
                    "blink_frame": 0,
                }
                return

    def _tick_tutor_pointer(self, game: dict[str, object], now: float) -> None:
        """T02.04: Pointer-Blink-Frame erhöhen und nach Ablauf löschen."""
        ptr = game.get("tutor_pointer")
        if not isinstance(ptr, dict):
            return
        if now >= float(ptr.get("expires", 0)):
            game.pop("tutor_pointer", None)
            return
        ptr = dict(ptr)
        ptr["blink_frame"] = (int(ptr.get("blink_frame", 0)) + 1) % 6
        game["tutor_pointer"] = ptr

    # ── T02.07: section first-visit explanations ──────────────────────────────

    def _maybe_fire_section_visit_explanation(self, game: dict[str, object], *, section_id: str) -> None:
        if not bool(game.get("tutorial_mode")):
            return
        try:
            from client_surfaces.operator_tui.snake_persistence import mark_section_visited
            is_first = mark_section_visited(section_id)
        except Exception:
            is_first = True
        if not is_first:
            return
        tip = self._get_tutor_text(section_id)
        if not tip:
            return
        self._inject_tutor_tip(game, tip, source=f"section:{section_id}")
        self._fire_tutorial_event(game, "section_visited")

    # ── E05: Notes init ───────────────────────────────────────────────────────

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
        # Poll transport for incoming messages
        if self._chat_transport is not None:
            try:
                self._chat_transport.tick(now)
            except Exception:
                pass
        # Handle retry request from :chat retry command
        if game.pop("chat_retry_requested", False) and self._chat_transport is not None:
            try:
                self._chat_transport.retry_failed()
            except Exception:
                pass
        # Sync outbox delivery states back to chat state
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
        # Only post once (check if already posted)
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
        # Submit to executor if not already running
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
        # Check if done
        if self._tutor_ask_future is not None and self._tutor_ask_future.done():
            try:
                answer = self._tutor_ask_future.result(timeout=0.01) or "Keine Antwort erhalten."
            except Exception:
                answer = "Fehler beim Abrufen der Antwort."
            game["tutor_ask_answered"] = True
            game["tutor_ask_answer"] = answer
            game["_chat_ai_answer_posted"] = False  # allow _tick_chat_ai_response to post
            game["paused"] = False  # resume after answer
            game["last_move"] = time.monotonic()
            game["_ask_submitted"] = False
            self._tutor_ask_future = None
            self._inject_tutor_tip(game, f"[ask] {answer}", source="ask")
            self._fire_tutorial_event(game, "ask_command_used")

    def _resolve_ask_question(self, question: str, *, depth: str, hints: list[str], rag_context: list[str]) -> str:
        context_parts = hints[:6] + rag_context[:6]
        context_text = "\n".join(context_parts)
        # Try worker-propose backend first
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
        # Try LLM backend
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
        self._snake_idle_since = 0.0  # reset idle on any event

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
                # try to post artifact to hub
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
                    # tutorial complete
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
            # fire-and-forget in executor
            self._tutorial_async_tip_executor.submit(
                lambda: urllib.request.urlopen(req, timeout=2.0)
            )
        except Exception:
            pass

    # ── T04.04: Guided Tour auto-advance ─────────────────────────────────────

    def _tick_guided_tour(self, game: dict[str, object], *, now: float) -> None:
        """T04.04: Guided Mode – navigiert automatisch alle 15s zur nächsten Sektion."""
        ts_raw = game.get("tutorial_state")
        if not isinstance(ts_raw, dict) or not ts_raw.get("guided"):
            return
        ts = dict(ts_raw)
        from client_surfaces.operator_tui.sections import SECTIONS
        section_ids = [s.id for s in SECTIONS]
        guided_idx = int(ts.get("guided_section_idx") or 0)
        guided_next_at = float(ts.get("guided_next_at") or 0.0)

        # initialise on first call
        if guided_next_at == 0.0:
            ts["guided_section_idx"] = guided_idx
            ts["guided_next_at"] = now + 15.0
            ts["guided_visited"] = []
            game["tutorial_state"] = ts
            # navigate immediately to first section and explain
            section_id = section_ids[guided_idx % len(section_ids)]
            self._apply_snake_section_target(game, section_id=section_id, now=now)
            tip = self._get_tutor_text(section_id)
            if tip:
                self._inject_tutor_tip(game, tip, source=f"guided:{section_id}")
            return

        if now < guided_next_at:
            return

        guided_visited = list(ts.get("guided_visited") or [])
        current_id = section_ids[guided_idx % len(section_ids)]
        if current_id not in guided_visited:
            guided_visited.append(current_id)

        guided_idx += 1
        if guided_idx >= len(section_ids):
            # tour complete – show summary and disable guided
            ts["guided"] = False
            visited_names = ", ".join(guided_visited)
            summary = f"Tour abgeschlossen! Besuchte Sektionen: {visited_names}. Starte ':tutorial start snake_mode' für den Snake-Modus."
            self._inject_tutor_tip(game, summary, source="guided:summary")
            game["tutorial_state"] = ts
            return

        next_id = section_ids[guided_idx]
        ts["guided_section_idx"] = guided_idx
        ts["guided_next_at"] = now + 15.0
        ts["guided_visited"] = guided_visited
        game["tutorial_state"] = ts
        self._apply_snake_section_target(game, section_id=next_id, now=now)
        tip = self._get_tutor_text(next_id)
        if tip:
            self._inject_tutor_tip(game, tip, source=f"guided:{next_id}")

    def _advance_guided_tour_now(self) -> None:
        """T04.04: Enter-Taste übernimmt – Guided Tour sofort weiterschalten."""
        game = dict(self.state.header_logo_game or {})
        ts_raw = game.get("tutorial_state")
        if not isinstance(ts_raw, dict) or not ts_raw.get("guided"):
            return
        ts = dict(ts_raw)
        ts["guided_next_at"] = 0.0
        game["tutorial_state"] = ts
        self._tick_guided_tour(game, now=time.monotonic())
        self._set_state(self.state.with_updates(header_logo_game=game))

    # ── E03.T03: snake role handling ──────────────────────────────────────────

    def _snake_role_for(self, snake_id: str, snapshot: dict[str, object]) -> str:
        if snapshot.get("local"):
            return str(snapshot.get("role") or "player")
        if snake_id == "s-ai":
            return "tutor"
        return str(snapshot.get("role") or "viewer")

    def _snake_cycle_message_style(self) -> None:
        game = dict(self.state.header_logo_game or self._default_header_snake())
        styles = ("trail", "orbit", "ticker")
        current = str(game.get("message_style") or styles[0])
        try:
            idx = styles.index(current)
        except ValueError:
            idx = 0
        next_style = styles[(idx + 1) % len(styles)]
        game["message_style"] = next_style
        self._set_state(
            self.state.with_updates(
                header_logo_game=game,
                status_message=f"snake text-style: {next_style}",
            )
        )

    def _snake_cycle_color(self) -> None:
        game = dict(self.state.header_logo_game or self._default_header_snake())
        palette = ("mint", "cyan", "violet", "amber", "rose")
        current = str(game.get("snake_color") or palette[0])
        try:
            idx = palette.index(current)
        except ValueError:
            idx = 0
        next_color = palette[(idx + 1) % len(palette)]
        game["snake_color"] = next_color
        self._set_state(
            self.state.with_updates(
                header_logo_game=game,
                status_message=f"snake farbe: {next_color}",
            )
        )

    def _snake_head(self, game: dict[str, object]) -> tuple[int, int] | None:
        snake = game.get("snake") or []
        if not isinstance(snake, list) or not snake:
            return None
        head = snake[0]
        if not isinstance(head, (list, tuple)) or len(head) != 2:
            return None
        return int(head[0]), int(head[1])

    def _snake_toggle_selection(self) -> None:
        game = dict(self.state.header_logo_game or {})
        if not self._snake_mode_active(game):
            return
        head = self._snake_head(game)
        if head is None:
            return
        if bool(game.get("selection_frame_mode")):
            self._snake_commit_frame_selection(game, head=head)
            return
        anchor_raw = game.get("selection_anchor")
        if not isinstance(anchor_raw, (list, tuple)) or len(anchor_raw) != 2:
            game["selection_anchor"] = head
            game["selection_cells"] = []
            self._set_state(self.state.with_updates(header_logo_game=game, status_message="snake select: start"))
            return
        ax, ay = int(anchor_raw[0]), int(anchor_raw[1])
        hx, hy = head
        min_x, max_x = sorted((ax, hx))
        min_y, max_y = sorted((ay, hy))
        cells = [(x, y) for y in range(min_y, max_y + 1) for x in range(min_x, max_x + 1)]
        game["selection_anchor"] = None
        game["selection_cells"] = cells
        self._set_state(
            self.state.with_updates(
                header_logo_game=game,
                status_message=f"snake select: {len(cells)} zellen markiert",
            )
        )

    def _snake_toggle_frame_mode(self) -> None:
        game = dict(self.state.header_logo_game or {})
        if not self._snake_mode_active(game):
            return
        head = self._snake_head(game)
        if head is None:
            return
        enabled = bool(game.get("selection_frame_mode"))
        if enabled:
            game["selection_frame_mode"] = False
            game["selection_frame_anchor"] = None
            self._set_state(self.state.with_updates(header_logo_game=game, status_message="snake frame: aus"))
            return
        game["selection_frame_mode"] = True
        game["selection_frame_anchor"] = head
        if not isinstance(game.get("selection_regions"), list):
            game["selection_regions"] = []
        self._set_state(self.state.with_updates(header_logo_game=game, status_message="snake frame: an (X setzt Rahmen)"))

    def _snake_clear_visual_marks(self) -> None:
        game = dict(self.state.header_logo_game or {})
        if not self._snake_mode_active(game):
            return
        game["mark_cells"] = []
        game["selection_anchor"] = None
        game["selection_cells"] = []
        game["selection_regions"] = []
        game["selection_frame_mode"] = False
        game["selection_frame_anchor"] = None
        snakes_raw = game.get("snakes")
        if isinstance(snakes_raw, dict):
            cleaned: dict[str, dict[str, object]] = {}
            for sid, snap in snakes_raw.items():
                if not isinstance(snap, dict):
                    continue
                s = dict(snap)
                s["mark_cells"] = []
                s["selection_cells"] = []
                s["selection_regions"] = []
                cleaned[str(sid)] = s
            game["snakes"] = cleaned
        self._set_state(self.state.with_updates(header_logo_game=game, status_message="snake marks: ausgeblendet"))

    def _snake_commit_frame_selection(self, game: dict[str, object], *, head: tuple[int, int]) -> None:
        anchor_raw = game.get("selection_frame_anchor")
        if not isinstance(anchor_raw, (list, tuple)) or len(anchor_raw) != 2:
            game["selection_frame_anchor"] = head
            self._set_state(self.state.with_updates(header_logo_game=game, status_message="snake frame: anchor gesetzt"))
            return
        ax, ay = int(anchor_raw[0]), int(anchor_raw[1])
        hx, hy = head
        min_x, max_x = sorted((ax, hx))
        min_y, max_y = sorted((ay, hy))
        region_cells = [(x, y) for y in range(min_y, max_y + 1) for x in range(min_x, max_x + 1)]
        existing_raw = game.get("selection_cells") or []
        existing = {
            (int(c[0]), int(c[1]))
            for c in existing_raw
            if isinstance(c, (list, tuple)) and len(c) == 2
        }
        existing.update(region_cells)
        game["selection_cells"] = sorted(existing)
        regions_raw = game.get("selection_regions")
        regions = list(regions_raw) if isinstance(regions_raw, list) else []
        regions.append((min_x, min_y, max_x, max_y))
        game["selection_regions"] = regions
        game["selection_frame_anchor"] = head
        self._set_state(
            self.state.with_updates(
                header_logo_game=game,
                status_message=f"snake frame: +{len(region_cells)} zellen ({len(regions)} rahmen)",
            )
        )

    def _snake_render_plain_lines(self) -> list[str]:
        game = dict(self.state.header_logo_game or {})
        if game.get("free_mode"):
            game["free_mode"] = False
        temp_state = self.state.with_updates(header_logo_game=game)
        size = shutil.get_terminal_size((120, 32))
        rendered = render_operator_shell(temp_state, width=size.columns, height=max(18, size.lines - 1), splash=self._splash)
        return [_ANSI_STRIP.sub("", line) for line in rendered.splitlines()]

    def _snake_copy_selection(self) -> None:
        game = dict(self.state.header_logo_game or {})
        cells_raw = game.get("selection_cells") or []
        cells = [
            (int(c[0]), int(c[1]))
            for c in cells_raw
            if isinstance(c, (list, tuple)) and len(c) == 2
        ]
        if not cells:
            self._set_state(self.state.with_updates(header_logo_game=game, status_message="snake copy: keine auswahl"))
            return
        lines = self._snake_render_plain_lines()
        by_row: dict[int, list[int]] = {}
        for x, y in cells:
            by_row.setdefault(y, []).append(x)
        chunks: list[str] = []
        for y in sorted(by_row.keys()):
            if y < 0 or y >= len(lines):
                continue
            row = lines[y]
            xs = by_row[y]
            if not xs:
                continue
            x_sorted = sorted(set(xs))
            parts: list[str] = []
            seg_start = x_sorted[0]
            seg_end = x_sorted[0]
            for x in x_sorted[1:]:
                if x == seg_end + 1:
                    seg_end = x
                    continue
                if seg_start < len(row):
                    parts.append(row[seg_start : min(len(row), seg_end + 1)])
                seg_start = x
                seg_end = x
            if seg_start < len(row):
                parts.append(row[seg_start : min(len(row), seg_end + 1)])
            if parts:
                chunks.append(" | ".join(parts))
        copied = "\n".join(chunks).rstrip("\n")
        game["clipboard"] = copied
        if copied:
            game["message"] = copied
        self._set_state(
            self.state.with_updates(
                header_logo_game=game,
                status_message="snake copy: in clipboard + message",
            )
        )

    def _snake_replace_selection(self) -> None:
        game = dict(self.state.header_logo_game or {})
        cells_raw = game.get("selection_cells") or []
        cells = [
            (int(c[0]), int(c[1]))
            for c in cells_raw
            if isinstance(c, (list, tuple)) and len(c) == 2
        ]
        if not cells:
            self._set_state(self.state.with_updates(header_logo_game=game, status_message="snake replace: keine auswahl"))
            return
        if self.state.mode is not OperatorMode.COMMAND:
            self._set_state(
                self.state.with_updates(
                    header_logo_game=game,
                    status_message="snake replace: nur im editierbaren command-feld",
                )
            )
            return
        lines = self._snake_render_plain_lines()
        if not lines:
            return
        command_row = len(lines) - 2
        ys = {y for _, y in cells}
        if ys != {command_row}:
            self._set_state(
                self.state.with_updates(
                    header_logo_game=game,
                    status_message="snake replace: auswahl muss in der command-zeile liegen",
                )
            )
            return
        replacement = str(game.get("message") or game.get("clipboard") or "")
        if not replacement:
            self._set_state(
                self.state.with_updates(
                    header_logo_game=game,
                    status_message="snake replace: keine message/clipboard vorhanden",
                )
            )
            return
        xs = sorted(x for x, _ in cells)
        min_x = min(xs)
        max_x = max(xs)
        # command line has one visible prefix char (":" in command mode)
        start = max(0, min_x - 1)
        end = max(start, max_x - 1)
        cmd = self.state.command_line
        if start > len(cmd):
            start = len(cmd)
        end = min(len(cmd) - 1, end) if cmd else -1
        if end >= start:
            new_cmd = cmd[:start] + replacement + cmd[end + 1 :]
        else:
            new_cmd = cmd[:start] + replacement + cmd[start:]
        self._command_buffer = new_cmd
        game["selection_anchor"] = None
        game["selection_cells"] = []
        self._set_state(
            self.state.with_updates(
                command_line=new_cmd,
                header_logo_game=game,
                status_message="snake replace: command-feld ersetzt",
            )
        )

    def _snake_message_mode_active(self) -> bool:
        game = dict(self.state.header_logo_game or {})
        return bool(game.get("message_mode"))

    def _toggle_snake_message_mode(self) -> None:
        game = dict(self.state.header_logo_game or self._default_header_snake())
        game["active"] = True
        game["ui_steering"] = True
        if game.get("message_mode"):
            game["message_mode"] = False
            game["message_draft"] = ""
            self._set_state(self.state.with_updates(header_logo_game=game, status_message="snake message: abgebrochen"))
            return
        game["message_mode"] = True
        game["message_draft"] = str(game.get("message", ""))
        self._set_state(self.state.with_updates(header_logo_game=game, status_message="snake message: eingeben + Enter speichern"))

    def _snake_message_append(self, text: str) -> None:
        game = dict(self.state.header_logo_game or {})
        if not game.get("message_mode"):
            return
        draft = str(game.get("message_draft", ""))
        game["message_draft"] = (draft + text)[:200]
        self._set_state(self.state.with_updates(header_logo_game=game, status_message="snake message: tippen..."))

    def _snake_message_backspace(self) -> None:
        game = dict(self.state.header_logo_game or {})
        if not game.get("message_mode"):
            return
        draft = str(game.get("message_draft", ""))
        game["message_draft"] = draft[:-1]
        self._set_state(self.state.with_updates(header_logo_game=game, status_message="snake message: tippen..."))

    def _snake_cancel_message(self) -> None:
        game = dict(self.state.header_logo_game or {})
        if not game.get("message_mode"):
            return
        game["message_mode"] = False
        game["message_draft"] = ""
        self._set_state(self.state.with_updates(header_logo_game=game, status_message="snake message: abgebrochen"))

    def _snake_commit_message(self) -> None:
        game = dict(self.state.header_logo_game or {})
        if not game.get("message_mode"):
            return
        message = str(game.get("message_draft", "")).strip()
        if message.lower().startswith("/template "):
            template = message[10:].strip()
            if template:
                game["tutorial_prompt_template"] = template
                game["message"] = f"template set ({len(template)} chars)"
                status_message = "snake template: gespeichert"
            else:
                status_message = "snake template: leer, ignoriert"
        else:
            game["message"] = message
            game["tutorial_user_feed"] = message
            if message:
                target = str(game.get("tutorial_ai_contact_zone") or self.state.section_id or "content")
                history_raw = game.get("tutorial_propose_history")
                history = [dict(item) for item in history_raw if isinstance(item, dict)] if isinstance(history_raw, list) else []
                history.append(
                    {
                        "at": float(time.monotonic()),
                        "source": "user",
                        "target": target,
                        "text": message,
                    }
                )
                game["tutorial_propose_history"] = history[-8:]
                # Force a fresh AI response after a new user question.
                self._tutorial_worker_cache = (0.0, "")
                self._tutorial_llm_cache = (0.0, "")
            status_message = "snake message/feed: gespeichert"
        game["message_mode"] = False
        game["message_draft"] = ""
        self._save_snake_message_config(message)
        self._set_state(self.state.with_updates(header_logo_game=game, status_message=status_message))

    def _save_snake_message_config(self, message: str) -> None:
        cfg_dir = Path.home() / ".config" / "ananta"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        cfg_file = cfg_dir / "snake-config.json"
        game = dict(self.state.header_logo_game or {})
        payload = {
            "snake_message": message,
            "tutorial_user_feed": str(game.get("tutorial_user_feed") or message),
            "tutorial_prompt_template": str(game.get("tutorial_prompt_template") or ""),
            "updated_at": int(time.time()),
        }
        cfg_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _load_snake_message_config(self) -> dict[str, object]:
        cfg_file = Path.home() / ".config" / "ananta" / "snake-config.json"
        if not cfg_file.exists():
            return {}
        try:
            payload = json.loads(cfg_file.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _snake_immediate_brake(self) -> None:
        game = dict(self.state.header_logo_game or {})
        if not game:
            return
        game["vel_x"] = 0.0
        game["vel_y"] = 0.0
        game["accum_x"] = 0.0
        game["accum_y"] = 0.0
        game["next_direction"] = (0, 0)
        self._set_state(self.state.with_updates(header_logo_game=game, status_message="snake: sofortstopp"))

    def _snake_escape_target(
        self,
        *,
        nx: int,
        ny: int,
        hx: int,
        hy: int,
        board_w: int,
        board_h: int,
        gaps: object,
    ) -> FocusPane | None:
        g = self._ensure_snake_escape_gaps(gaps, board_w=board_w, board_h=board_h, seed=0)
        right_gap = int(g.get("right", 1))
        if nx >= board_w and abs(hy - right_gap) <= 1:
            return FocusPane.NAVIGATION
        if ny >= board_h:
            bottom_nav = int(g.get("bottom_nav", board_w // 5))
            bottom_content = int(g.get("bottom_content", board_w // 2))
            bottom_detail = int(g.get("bottom_detail", (board_w * 4) // 5))
            if abs(hx - bottom_nav) <= 1:
                return FocusPane.NAVIGATION
            if abs(hx - bottom_content) <= 1:
                return FocusPane.CONTENT
            if abs(hx - bottom_detail) <= 1:
                return FocusPane.DETAIL
        return None

    def _apply_snake_escape(
        self,
        game: dict[str, object],
        *,
        target: FocusPane,
        now: float,
        board_h: int,
    ) -> None:
        game["active"] = True
        game["ui_steering"] = True
        game["escaped_to"] = target.value
        game["last_move"] = now
        if target is FocusPane.NAVIGATION:
            nav_idx = min(len(SECTIONS) - 1, max(0, int((int(game.get("moves", 0)) + board_h) % max(1, len(SECTIONS)))))
            self.state = self.state.with_updates(
                focus=FocusPane.NAVIGATION,
                selected_index=nav_idx,
                header_logo_game=game,
                status_message="snake: ausgebrochen nach NAV",
            )
            return
        selected = max(0, min(999999, int(self.state.selected_index)))
        self.state = self.state.with_updates(
            focus=target,
            selected_index=selected,
            header_logo_game=game,
            status_message=f"snake: ausgebrochen nach {target.value}",
        )

    def _apply_snake_section_target(self, game: dict[str, object], *, section_id: str, now: float) -> None:
        section_ids = [s.id for s in SECTIONS]
        if section_id not in section_ids:
            return
        idx = section_ids.index(section_id)
        game["active"] = True
        game["ui_steering"] = True
        game["escaped_to"] = "navigation"
        game["last_move"] = now
        next_state = self.state.with_updates(
            focus=FocusPane.NAVIGATION,
            selected_index=idx,
            section_id=section_id,
            header_logo_game=game,
            status_message=f"snake: section {section_id}",
        )
        self.state = load_active_section(next_state, self._registry)

    def _apply_snake_ui_controls(
        self,
        state: OperatorState,
        *,
        head: tuple[int, int],
        board_w: int,
        board_h: int,
    ) -> OperatorState:
        game = dict(state.header_logo_game or {})
        if not game.get("ui_steering"):
            return state
        x, y = head
        x = max(0, min(board_w - 1, x))
        y = max(0, min(board_h - 1, y))
        third = max(1, board_w // 3)

        # Top-center zone acts as "input field focus" (command line mode).
        if y <= 1 and third <= x < (third * 2):
            return state.with_updates(mode=OperatorMode.COMMAND, command_line=state.command_line)

        if x < third:
            nav_idx = min(len(SECTIONS) - 1, max(0, round((y / max(1, board_h - 1)) * max(0, len(SECTIONS) - 1))))
            return state.with_updates(focus=FocusPane.NAVIGATION, selected_index=nav_idx, mode=OperatorMode.NORMAL)
        if x < (third * 2):
            content_idx = max(0, round((y / max(1, board_h - 1)) * 8))
            return state.with_updates(focus=FocusPane.CONTENT, selected_index=content_idx, mode=OperatorMode.NORMAL)
        detail_idx = max(0, round((y / max(1, board_h - 1)) * 8))
        return state.with_updates(focus=FocusPane.DETAIL, selected_index=detail_idx, mode=OperatorMode.NORMAL)

    def _compute_control_boxes(
        self,
        board_w: int,
        board_h: int,
    ) -> list[dict[str, object]]:
        return []

    def _box_hit_target(
        self,
        head: tuple[int, int],
        boxes: list[dict[str, object]],
    ) -> FocusPane | str | None:
        _ = (head, boxes)
        return None

    def _ensure_snake_escape_gaps(
        self,
        gaps: object,
        *,
        board_w: int,
        board_h: int,
        seed: int,
    ) -> dict[str, int]:
        if isinstance(gaps, dict):
            try:
                keys = ("right", "bottom_nav", "bottom_content", "bottom_detail")
                parsed = {k: int(gaps[k]) for k in keys}
                if 1 <= parsed["right"] <= board_h - 2:
                    for key in ("bottom_nav", "bottom_content", "bottom_detail"):
                        parsed[key] = max(1, min(board_w - 2, parsed[key]))
                    return parsed
            except Exception:
                pass
        return self._compute_snake_escape_gaps(board_w, board_h, seed=seed)

    def _compute_snake_escape_gaps(self, board_w: int, board_h: int, *, seed: int) -> dict[str, int]:
        usable_w = max(3, board_w - 2)
        right_gap = 1 + ((seed // 3) % max(1, board_h - 2))
        nav = 1 + ((seed // 5) % max(1, usable_w // 3))
        content = max(2, min(board_w - 2, board_w // 2 + ((seed // 7) % 3) - 1))
        detail = max(2, min(board_w - 2, board_w - 2 - ((seed // 11) % max(1, usable_w // 3))))
        if detail <= content:
            detail = min(board_w - 2, content + 2)
        if content <= nav:
            content = min(board_w - 2, nav + 2)
        return {
            "right": right_gap,
            "bottom_nav": nav,
            "bottom_content": content,
            "bottom_detail": detail,
        }

    def _spawn_snake_food(
        self,
        board_w: int,
        board_h: int,
        snake: list[tuple[int, int]],
        seed: int,
    ) -> tuple[int, int]:
        occupied = set(snake)
        free = [(x, y) for y in range(board_h) for x in range(board_w) if (x, y) not in occupied]
        if not free:
            return snake[-1] if snake else (0, 0)
        idx = (seed * 17 + board_w * 13 + board_h * 7) % len(free)
        return free[idx]

    def _set_state(self, state: OperatorState) -> None:
        if self._splash is not None:
            from agent.cli.splash import SplashState
            ctx = self._splash.context
            if ctx.state in (SplashState.FULLSCREEN, SplashState.TRANSITION):
                self._splash.transition_to(SplashState.COMPACT_HEADER)
        self.state = state
        self._rendered_text = self._render()
        self._app.invalidate()

    def _render(self) -> str:
        if self._splash is not None:
            self._splash.tick()
        size = shutil.get_terminal_size((120, 32))
        return render_operator_shell(self.state, width=size.columns, height=max(18, size.lines - 1), splash=self._splash)
