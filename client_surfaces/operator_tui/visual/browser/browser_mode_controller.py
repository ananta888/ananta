from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from urllib.parse import urlparse

from client_surfaces.operator_tui.visual.browser.browser_document_adapter import BrowserDocumentAdapter
from client_surfaces.operator_tui.visual.browser.browser_security_policy import BrowserSecurityPolicy
from client_surfaces.operator_tui.visual.browser.carbonyl_profile_manager import CarbonylProfile, CarbonylProfileManager
from client_surfaces.operator_tui.visual.browser.carbonyl_runner import CarbonylNotAvailableError, CarbonylRunner
from client_surfaces.operator_tui.visual.browser.center_content_snapshot import CenterContentSnapshot

_WEBRTC_APP_DIR = Path(__file__).resolve().parent / "webrtc_app"


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
        self._profile_manager: CarbonylProfileManager | None = None
        self._active_profile: CarbonylProfile | None = None

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

    def is_running(self) -> bool:
        """Return True while controller is active and Carbonyl subprocess is alive."""
        if self._state != BrowserModeState.ACTIVE:
            return False
        runner = self._runner
        return bool(runner is not None and runner.is_running())

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

    def enter_url(
        self,
        url: str,
        cols: int,
        rows: int,
        *,
        profile_dir: Path | None = None,
        allow_remote: bool = False,
    ) -> None:
        """Start Carbonyl against a local file/data URL or explicitly allowed remote URL."""
        if self._state != BrowserModeState.INACTIVE:
            return

        self._state = BrowserModeState.STARTING
        self._error_message = ""
        try:
            allowed, reason = self._policy.validate_url(url)
            if not allowed and allow_remote:
                scheme = (urlparse(url).scheme or "").lower()
                allowed = scheme == "https"
                reason = "remote https URL explicitly allowed" if allowed else reason
            if not allowed:
                raise ValueError(reason)
            self._start_target(url, cols=cols, rows=rows, profile_dir=profile_dir)
        except Exception as exc:
            self._error_message = str(exc)
            self._state = BrowserModeState.INACTIVE
            self._cleanup_resources()

    def open_oidc_provider_session(
        self,
        authorization_url: str,
        *,
        provider_id: str,
        profile_manager: CarbonylProfileManager | None = None,
        cols: int,
        rows: int,
        ephemeral_profile: bool = True,
    ) -> CarbonylProfile | None:
        """Open an OIDC authorization URL in an isolated Carbonyl profile."""
        parsed = urlparse(authorization_url)
        if parsed.scheme != "https":
            self._error_message = "OIDC provider session requires an https authorization URL."
            return None
        manager = profile_manager or CarbonylProfileManager()
        profile = manager.create_profile(provider_id=provider_id, ephemeral=ephemeral_profile)
        self._profile_manager = manager
        self._active_profile = profile
        self.enter_url(authorization_url, cols=cols, rows=rows, profile_dir=profile.profile_dir, allow_remote=True)
        if self._state != BrowserModeState.ACTIVE:
            manager.cleanup_profile(profile)
            self._active_profile = None
            return None
        return profile

    def open_webrtc_app(
        self,
        config: dict[str, object],
        *,
        cols: int,
        rows: int,
    ) -> Path | None:
        """Load the local WebRTC app with non-secret ANANTA_CONFIG injected."""
        if self._state != BrowserModeState.INACTIVE:
            return None
        self._state = BrowserModeState.STARTING
        self._error_message = ""
        try:
            html = build_webrtc_app_html(config)
            html_path = self._policy.create_temp_file(html, suffix=".html")
            self._current_html_path = html_path
            self._start_target(str(html_path), cols=cols, rows=rows)
            return html_path
        except Exception as exc:
            self._error_message = str(exc)
            self._state = BrowserModeState.INACTIVE
            self._cleanup_resources()
            return None

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
        self._start_target(str(html_path), cols=cols, rows=rows)

    def _start_target(self, target: str, *, cols: int, rows: int, profile_dir: Path | None = None) -> None:
        runner = CarbonylRunner(carbonyl_binary=self._binary)
        extra_args = [f"--user-data-dir={profile_dir}"] if profile_dir else []
        try:
            runner.start(target, cols=cols, rows=rows, extra_args=extra_args)
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
        if self._profile_manager is not None and self._active_profile is not None:
            try:
                self._profile_manager.cleanup_profile(self._active_profile)
            except Exception:
                pass
        self._profile_manager = None
        self._active_profile = None
        self._current_html_path = None

    def __del__(self) -> None:
        try:
            self.exit_browser_mode()
        except Exception:
            pass


def build_webrtc_app_html(config: dict[str, object]) -> str:
    """Return local-only WebRTC app HTML with host-provided config injected."""
    safe_config = _non_secret_config(config)
    index = (_WEBRTC_APP_DIR / "index.html").read_text(encoding="utf-8")
    base_href = _WEBRTC_APP_DIR.as_uri() + "/"
    config_script = (
        "<script>"
        "window.ANANTA_CONFIG = "
        + json.dumps(safe_config, sort_keys=True)
        + ";"
        "</script>"
    )
    index = index.replace("<head>", f"<head>\n  <base href=\"{base_href}\" />", 1)
    return index.replace(
        "  <script type=\"module\" src=\"app.js\"></script>",
        f"  {config_script}\n  <script type=\"module\" src=\"app.js\"></script>",
        1,
    )


def _non_secret_config(config: dict[str, object]) -> dict[str, object]:
    blocked_fragments = ("token", "secret", "credential", "cookie", "password", "refresh")
    safe: dict[str, object] = {}
    for key, value in config.items():
        key_str = str(key)
        if any(fragment in key_str.lower() for fragment in blocked_fragments):
            continue
        if isinstance(value, dict):
            safe[key_str] = _non_secret_config(value)
        elif isinstance(value, list):
            safe[key_str] = [_non_secret_config(item) if isinstance(item, dict) else item for item in value]
        else:
            safe[key_str] = value
    return safe
