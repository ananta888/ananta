from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable

from agent.tui_contract import ShellMode, TuiPaneState

LOGGER = logging.getLogger("agent.tui_shell_runtime")

# Default suspend chord: Ctrl+b followed by 'd' (tmux-style detach).
# Callers may override via TuiShellRuntime(suspend_key=...).
DEFAULT_SUSPEND_KEY = "\x02d"

# Valid mode transitions: maps current mode → set of allowed next modes
_VALID_TRANSITIONS: dict[ShellMode, set[ShellMode]] = {
    ShellMode.DASHBOARD: {
        ShellMode.TERMINAL_SESSION,
        ShellMode.EMBEDDED_EDITOR,
        ShellMode.EMBEDDED_TOOL,
    },
    ShellMode.TERMINAL_SESSION: {
        ShellMode.DASHBOARD,
        ShellMode.EMBEDDED_EDITOR,
        ShellMode.EMBEDDED_TOOL,
    },
    ShellMode.EMBEDDED_EDITOR: {
        ShellMode.DASHBOARD,
        ShellMode.TERMINAL_SESSION,
        ShellMode.EMBEDDED_TOOL,
    },
    ShellMode.EMBEDDED_TOOL: {
        ShellMode.DASHBOARD,
        ShellMode.TERMINAL_SESSION,
        ShellMode.EMBEDDED_EDITOR,
    },
}


class ShellModeTransitionError(ValueError):
    pass


@dataclass(frozen=True)
class KeyDispatchResult:
    forwarded: bool
    consumed: bool
    suspend_triggered: bool
    key: str

    @classmethod
    def forward(cls, key: str) -> "KeyDispatchResult":
        return cls(forwarded=True, consumed=False, suspend_triggered=False, key=key)

    @classmethod
    def consume(cls, key: str) -> "KeyDispatchResult":
        return cls(forwarded=False, consumed=True, suspend_triggered=False, key=key)

    @classmethod
    def suspend(cls, key: str) -> "KeyDispatchResult":
        return cls(forwarded=False, consumed=True, suspend_triggered=True, key=key)


class TuiShellRuntime:
    """State machine and keyboard dispatcher for the Ananta TUI main shell.

    Manages:
    - Active ShellMode and TuiPaneState.
    - Mode transitions with validation.
    - Keyboard event routing (forward to embedded session vs. consume for TUI).
    - Suspend shortcut to return to dashboard without killing the session.

    This class is intentionally not a blocking event loop — it is plugged
    into a main loop by TuiMainPaneController (E005). It is fully testable
    without a real TTY.
    """

    def __init__(
        self,
        *,
        suspend_key: str = DEFAULT_SUSPEND_KEY,
        on_state_change: Callable[[TuiPaneState], None] | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._state = TuiPaneState(mode=ShellMode.DASHBOARD)
        self._suspend_key = suspend_key
        self._on_state_change = on_state_change
        # Chord state: some suspend keys are 2-char sequences
        self._chord_buffer: str = ""

    # ── State access ──────────────────────────────────────────────────────────

    def current_state(self) -> TuiPaneState:
        with self._lock:
            return self._state

    # ── Mode transitions ──────────────────────────────────────────────────────

    def switch_to(self, mode: ShellMode, context: dict | None = None) -> TuiPaneState:
        """Transition to a new mode, optionally setting pane context.

        Raises ShellModeTransitionError for invalid transitions.
        Context keys: session_id, tool_id, file_path, read_only, target_type.
        """
        ctx = dict(context or {})
        with self._lock:
            current = self._state.mode
            if mode not in _VALID_TRANSITIONS.get(current, set()):
                raise ShellModeTransitionError(
                    f"Invalid transition: {current.value} → {mode.value}"
                )
            new_state = TuiPaneState(
                mode=mode,
                session_id=str(ctx.get("session_id") or ""),
                tool_id=str(ctx.get("tool_id") or ""),
                file_path=str(ctx.get("file_path") or ""),
                read_only=bool(ctx.get("read_only", False)),
                target_type=str(ctx.get("target_type") or ""),
                entered_at=time.time(),
            )
            self._state = new_state
            self._chord_buffer = ""

        LOGGER.debug("TUI mode: %s → %s session=%s", current.value, mode.value, new_state.session_id)
        if self._on_state_change:
            try:
                self._on_state_change(new_state)
            except Exception:
                LOGGER.debug("on_state_change callback raised", exc_info=True)
        return new_state

    def suspend(self) -> TuiPaneState:
        """Return to dashboard mode without killing the active session."""
        with self._lock:
            if self._state.mode == ShellMode.DASHBOARD:
                return self._state
            current = self._state.mode

        new_state = self.switch_to(ShellMode.DASHBOARD)
        LOGGER.info("TUI suspended from %s → dashboard", current.value)
        return new_state

    # ── Keyboard dispatcher ───────────────────────────────────────────────────

    def dispatch_key(self, key: str) -> KeyDispatchResult:
        """Route a raw key event.

        In embedded modes (editor/tool): all keys are forwarded to the
        underlying terminal session, except the suspend chord.
        In dashboard/terminal mode: keys are consumed by the TUI.

        Returns a KeyDispatchResult describing the outcome.
        """
        with self._lock:
            mode = self._state.mode
            suspend_key = self._suspend_key
            chord = self._chord_buffer

        # Detect suspend chord (may be 1 or 2 chars)
        if len(suspend_key) == 2:
            if chord == suspend_key[0] and key == suspend_key[1]:
                with self._lock:
                    self._chord_buffer = ""
                self.suspend()
                return KeyDispatchResult.suspend(key)
            if key == suspend_key[0]:
                with self._lock:
                    self._chord_buffer = key
                # Not yet complete — consume the first char silently
                return KeyDispatchResult.consume(key)
            # Reset chord buffer if a non-chord key arrives mid-sequence
            if chord:
                with self._lock:
                    self._chord_buffer = ""
        else:
            if key == suspend_key:
                self.suspend()
                return KeyDispatchResult.suspend(key)

        # In embedded modes: forward all other keys to the terminal session
        if mode in (ShellMode.EMBEDDED_EDITOR, ShellMode.EMBEDDED_TOOL):
            return KeyDispatchResult.forward(key)

        # In dashboard/terminal: TUI consumes the key
        return KeyDispatchResult.consume(key)

    # ── Convenience ───────────────────────────────────────────────────────────

    def reset(self) -> None:
        """Reset to dashboard state. Intended for testing."""
        with self._lock:
            self._state = TuiPaneState(mode=ShellMode.DASHBOARD)
            self._chord_buffer = ""


_runtime: TuiShellRuntime | None = None
_runtime_lock = threading.Lock()


def get_tui_shell_runtime() -> TuiShellRuntime:
    global _runtime
    if _runtime is None:
        with _runtime_lock:
            if _runtime is None:
                _runtime = TuiShellRuntime()
    return _runtime
