from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from agent.tui_contract import ShellMode, TuiPaneState
from agent.tui_shell_runtime import TuiShellRuntime, get_tui_shell_runtime

if TYPE_CHECKING:
    from agent.services.embedded_tool_session_service import (
        EmbeddedToolSessionService,
        TuiToolPolicy,
    )

LOGGER = logging.getLogger("agent.tui_main_pane")


def _new_session_id() -> str:
    return f"tui-{uuid.uuid4().hex[:12]}"


class TuiMainPaneController:
    """Coordinates TuiShellRuntime with EmbeddedToolSessionService.

    High-level operations:
    - open_file()    — validate path, resolve editor, launch, switch to embedded_editor mode
    - launch_tool()  — validate tool, launch, switch to embedded_tool mode
    - return_to_dashboard() — suspend active session, return to dashboard
    - status()       — return current pane state as a dict

    This class is NOT a blocking event loop. It is wired into a main loop
    by the TUI app (client_surfaces). All operations are synchronous from
    the caller's perspective; the actual process runs in the terminal session.
    """

    def __init__(
        self,
        *,
        runtime: TuiShellRuntime | None = None,
        embedded_service: "EmbeddedToolSessionService | None" = None,
    ) -> None:
        self._runtime = runtime
        self._embedded_service = embedded_service

    def _get_runtime(self) -> TuiShellRuntime:
        if self._runtime is not None:
            return self._runtime
        return get_tui_shell_runtime()

    def _get_embedded_service(self) -> "EmbeddedToolSessionService":
        if self._embedded_service is not None:
            return self._embedded_service
        from agent.services.embedded_tool_session_service import get_embedded_tool_session_service
        return get_embedded_tool_session_service()

    # ── open_file ──────────────────────────────────────────────────────────────

    def open_file(
        self,
        file_path: str,
        workspace: str,
        *,
        with_editor: str | None = None,
        readonly: bool = False,
        target_type: str = "worker",
        session_id: str | None = None,
        policy: "TuiToolPolicy | None" = None,
    ) -> dict:
        """Open a file in the resolved editor and switch the main pane to embedded_editor.

        Returns a dict with keys: ok, session_id, editor_id, reason, state.
        """
        sid = session_id or _new_session_id()
        svc = self._get_embedded_service()
        result = svc.launch_editor(
            sid,
            file_path,
            workspace=workspace,
            with_editor=with_editor,
            readonly=readonly,
            target_type=target_type,
            policy=policy,
        )
        if not result.ok:
            return {"ok": False, "session_id": sid, "reason": result.reason, "state": self.status()}

        runtime = self._get_runtime()
        try:
            new_state = runtime.switch_to(
                ShellMode.EMBEDDED_EDITOR,
                {
                    "session_id": sid,
                    "file_path": result.meta.file_path if result.meta else file_path,
                    "read_only": readonly,
                    "target_type": target_type,
                },
            )
        except Exception as exc:
            LOGGER.warning("Mode switch failed after editor launch: %s", exc)
            new_state = runtime.current_state()

        LOGGER.info("Pane opened editor: session=%s editor=%s", sid, result.meta.editor_id if result.meta else "?")
        return {
            "ok": True,
            "session_id": sid,
            "editor_id": result.meta.editor_id if result.meta else "",
            "reason": "ok",
            "state": new_state.to_dict(),
        }

    # ── launch_tool ────────────────────────────────────────────────────────────

    def launch_tool(
        self,
        tool_id: str,
        workspace: str,
        *,
        target_type: str = "worker",
        session_id: str | None = None,
        policy: "TuiToolPolicy | None" = None,
    ) -> dict:
        """Launch an embedded TUI tool and switch the main pane to embedded_tool.

        Returns a dict with keys: ok, session_id, tool_id, reason, state.
        """
        sid = session_id or _new_session_id()
        svc = self._get_embedded_service()
        result = svc.launch_tool(sid, tool_id, workspace=workspace, target_type=target_type, policy=policy)
        if not result.ok:
            return {"ok": False, "session_id": sid, "tool_id": tool_id, "reason": result.reason, "state": self.status()}

        runtime = self._get_runtime()
        try:
            new_state = runtime.switch_to(
                ShellMode.EMBEDDED_TOOL,
                {"session_id": sid, "tool_id": tool_id, "target_type": target_type},
            )
        except Exception as exc:
            LOGGER.warning("Mode switch failed after tool launch: %s", exc)
            new_state = runtime.current_state()

        LOGGER.info("Pane launched tool: session=%s tool=%s", sid, tool_id)
        return {
            "ok": True,
            "session_id": sid,
            "tool_id": tool_id,
            "reason": "ok",
            "state": new_state.to_dict(),
        }

    # ── return_to_dashboard ────────────────────────────────────────────────────

    def return_to_dashboard(self) -> dict:
        """Suspend the active session and return the main pane to dashboard mode.

        The underlying terminal session is NOT killed — it can be resumed.
        """
        runtime = self._get_runtime()
        state = runtime.suspend()
        return {"ok": True, "reason": "ok", "state": state.to_dict()}

    # ── status ─────────────────────────────────────────────────────────────────

    def status(self) -> dict:
        """Return the current pane state as a dict."""
        return self._get_runtime().current_state().to_dict()

    # ── current_mode ───────────────────────────────────────────────────────────

    def current_mode(self) -> ShellMode:
        return self._get_runtime().current_state().mode


_controller: TuiMainPaneController | None = None


def get_tui_main_pane_controller() -> TuiMainPaneController:
    global _controller
    if _controller is None:
        _controller = TuiMainPaneController()
    return _controller
