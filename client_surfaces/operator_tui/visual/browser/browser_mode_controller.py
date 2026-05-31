from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

from client_surfaces.operator_tui.visual.browser.browser_document_adapter import BrowserDocumentAdapter
from client_surfaces.operator_tui.visual.browser.browser_security_policy import BrowserSecurityPolicy
from client_surfaces.operator_tui.visual.browser.carbonyl_runner import CarbonylNotAvailableError, CarbonylRunner
from client_surfaces.operator_tui.visual.browser.center_content_snapshot import CenterContentSnapshot


class BrowserModeState(str, Enum):
    INACTIVE = "inactive"
    STARTING = "starting"
    ACTIVE = "active"
    STOPPING = "stopping"


class BrowserModeController:
    """Manage the lifecycle of the Carbonyl browser render mode.

    State machine:
        inactive → starting → active → stopping → inactive

    Typical usage:
        ctrl = BrowserModeController()
        ctrl.enter_browser_mode(snapshot, cols=120, rows=30)
        # In render loop:
        data = ctrl.tick()
        if data:
            <write data to terminal inside CenterViewport>
        # On user exit:
        ctrl.exit_browser_mode()
    """

    def __init__(
        self,
        *,
        carbonyl_binary: str = "carbonyl",
        security_policy: BrowserSecurityPolicy | None = None,
    ) -> None:
        self._binary = carbonyl_binary
        self._policy = security_policy or BrowserSecurityPolicy()
        self._runner: CarbonylRunner | None = None
        self._state: BrowserModeState = BrowserModeState.INACTIVE
        self._error_message: str = ""
        self._current_html_path: Path | None = None

    # ------------------------------------------------------------------
    # State accessors
    # ------------------------------------------------------------------

    @property
    def state(self) -> BrowserModeState:
        return self._state

    @property
    def is_active(self) -> bool:
        return self._state == BrowserModeState.ACTIVE

    @property
    def error_message(self) -> str:
        return self._error_message

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def enter_browser_mode(
        self, snapshot: CenterContentSnapshot, cols: int, rows: int
    ) -> None:
        """Convert snapshot to HTML and start Carbonyl.

        On failure, controller returns to INACTIVE with error_message set.
        Never raises — degrade gracefully.

        Args:
            snapshot: Current center content to render.
            cols: Terminal column count.
            rows: Terminal row count.
        """
        if self._state != BrowserModeState.INACTIVE:
            return

        self._state = BrowserModeState.STARTING
        self._error_message = ""
        try:
            self._enter(snapshot, cols, rows)
        except Exception as exc:
            self._error_message = str(exc)
            self._state = BrowserModeState.INACTIVE
            self._cleanup_resources()

    def exit_browser_mode(self) -> None:
        """Stop Carbonyl subprocess and clean up resources."""
        if self._state in {BrowserModeState.INACTIVE, BrowserModeState.STOPPING}:
            return
        self._state = BrowserModeState.STOPPING
        self._cleanup_resources()
        self._state = BrowserModeState.INACTIVE

    # ------------------------------------------------------------------
    # Render-loop methods
    # ------------------------------------------------------------------

    def tick(self) -> bytes | None:
        """Read available output from the Carbonyl subprocess.

        Returns:
            Raw bytes from Carbonyl PTY output, or None if no data / inactive.
        """
        if self._state != BrowserModeState.ACTIVE:
            return None
        runner = self._runner
        if runner is None or not runner.is_running():
            self._state = BrowserModeState.INACTIVE
            return None
        data = runner.read_output(timeout=0.05)
        return data if data else None

    def handle_input(self, keys: bytes) -> None:
        """Forward keyboard/mouse input to the Carbonyl PTY.

        Input is only forwarded while browser mode is active.
        """
        if self._state != BrowserModeState.ACTIVE:
            return
        if self._runner is not None:
            self._runner.write_input(keys)

    def resize(self, cols: int, rows: int) -> None:
        """Propagate terminal resize to Carbonyl PTY."""
        if self._runner is not None:
            self._runner.resize(cols, rows)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _enter(self, snapshot: CenterContentSnapshot, cols: int, rows: int) -> None:
        adapter = BrowserDocumentAdapter()
        html_doc = adapter.to_html(snapshot)
        html_path = self._policy.create_temp_file(html_doc, suffix=".html")
        self._current_html_path = html_path

        runner = CarbonylRunner(carbonyl_binary=self._binary)
        try:
            runner.start(str(html_path), cols=cols, rows=rows)
        except CarbonylNotAvailableError as exc:
            raise RuntimeError(
                f"Carbonyl unavailable — browser mode requires carbonyl to be installed. "
                f"Details: {exc}"
            ) from exc

        self._runner = runner
        self._state = BrowserModeState.ACTIVE

    def _cleanup_resources(self) -> None:
        if self._runner is not None:
            try:
                self._runner.stop()
            except Exception:
                pass
            self._runner = None
        try:
            self._policy.cleanup()
        except Exception:
            pass
        self._current_html_path = None

    def __del__(self) -> None:
        try:
            self.exit_browser_mode()
        except Exception:
            pass
