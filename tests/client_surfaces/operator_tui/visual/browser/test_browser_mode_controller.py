from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from client_surfaces.operator_tui.visual.browser.browser_mode_controller import (
    BrowserModeController,
    BrowserModeState,
    build_webrtc_app_html,
)
from client_surfaces.operator_tui.visual.browser.carbonyl_runner import CarbonylNotAvailableError
from client_surfaces.operator_tui.visual.browser.center_content_snapshot import CenterContentSnapshot


def _snap(content_type: str = "plain_text", source_text: str = "hello") -> CenterContentSnapshot:
    return CenterContentSnapshot(
        content_type=content_type,
        title="Test",
        source_text=source_text,
        html_text="",
    )


def _make_mock_runner(running: bool = True) -> MagicMock:
    runner = MagicMock()
    runner.is_running.return_value = running
    runner.read_output.return_value = b"output data"
    return runner


# ---------------------------------------------------------------------------
# Initial state
# ---------------------------------------------------------------------------

class TestInitialState:
    def test_starts_inactive(self) -> None:
        ctrl = BrowserModeController()
        assert ctrl.state == BrowserModeState.INACTIVE
        assert not ctrl.is_active
        assert ctrl.error_message == ""


# ---------------------------------------------------------------------------
# enter_browser_mode
# ---------------------------------------------------------------------------

class TestEnterBrowserMode:
    def test_successful_enter(self) -> None:
        ctrl = BrowserModeController()
        mock_runner = _make_mock_runner()

        with patch(
            "client_surfaces.operator_tui.visual.browser.browser_mode_controller.CarbonylRunner",
            return_value=mock_runner,
        ), patch.object(
            ctrl._policy, "create_temp_file", return_value=Path("/tmp/fake.html")
        ):
            ctrl.enter_browser_mode(_snap(), cols=80, rows=24)

        assert ctrl.state == BrowserModeState.ACTIVE

    def test_enter_url_rejects_remote_by_default(self) -> None:
        ctrl = BrowserModeController()
        ctrl.enter_url("https://issuer.example.com/auth", cols=80, rows=24)
        assert ctrl.state == BrowserModeState.INACTIVE
        assert "remote URLs disabled" in ctrl.error_message

    def test_open_oidc_provider_session_uses_isolated_profile(self, tmp_path: Path) -> None:
        ctrl = BrowserModeController()
        mock_runner = _make_mock_runner()
        from client_surfaces.operator_tui.visual.browser.carbonyl_profile_manager import CarbonylProfileManager

        manager = CarbonylProfileManager(profile_root=tmp_path / "profiles")
        with patch(
            "client_surfaces.operator_tui.visual.browser.browser_mode_controller.CarbonylRunner",
            return_value=mock_runner,
        ):
            profile = ctrl.open_oidc_provider_session(
                "https://issuer.example.com/auth",
                provider_id="keycloak",
                profile_manager=manager,
                cols=80,
                rows=24,
            )

        assert profile is not None
        assert ctrl.state == BrowserModeState.ACTIVE
        args = mock_runner.start.call_args.kwargs["extra_args"]
        assert args == [f"--user-data-dir={profile.profile_dir}"]

    def test_open_webrtc_app_injects_local_config(self) -> None:
        ctrl = BrowserModeController()
        mock_runner = _make_mock_runner()
        with patch(
            "client_surfaces.operator_tui.visual.browser.browser_mode_controller.CarbonylRunner",
            return_value=mock_runner,
        ):
            html_path = ctrl.open_webrtc_app(
                {"signaling_url": "", "session_nonce": "nonce", "access_token": "secret"},
                cols=80,
                rows=24,
            )
        assert html_path is not None
        html = html_path.read_text(encoding="utf-8")
        assert "window.ANANTA_CONFIG" in html
        assert "nonce" in html
        assert "secret" not in html
        assert "file://" in html
        assert ctrl.is_active
        assert ctrl.error_message == ""
        ctrl.exit_browser_mode()

    def test_carbonyl_not_available_degrades_gracefully(self) -> None:
        ctrl = BrowserModeController()
        mock_runner = _make_mock_runner()
        mock_runner.start.side_effect = CarbonylNotAvailableError("not found")

        with patch(
            "client_surfaces.operator_tui.visual.browser.browser_mode_controller.CarbonylRunner",
            return_value=mock_runner,
        ), patch.object(
            ctrl._policy, "create_temp_file", return_value=Path("/tmp/fake.html")
        ):
            ctrl.enter_browser_mode(_snap(), cols=80, rows=24)

        assert ctrl.state == BrowserModeState.INACTIVE
        assert "Carbonyl unavailable" in ctrl.error_message

    def test_reenter_while_active_is_noop(self) -> None:
        ctrl = BrowserModeController()
        mock_runner = _make_mock_runner()

        with patch(
            "client_surfaces.operator_tui.visual.browser.browser_mode_controller.CarbonylRunner",
            return_value=mock_runner,
        ), patch.object(
            ctrl._policy, "create_temp_file", return_value=Path("/tmp/fake.html")
        ):
            ctrl.enter_browser_mode(_snap(), cols=80, rows=24)
            # Second call while active — must not change state
            ctrl.enter_browser_mode(_snap(), cols=80, rows=24)

        assert ctrl.state == BrowserModeState.ACTIVE


# ---------------------------------------------------------------------------
# exit_browser_mode
# ---------------------------------------------------------------------------

class TestExitBrowserMode:
    def test_exit_transitions_to_inactive(self) -> None:
        ctrl = BrowserModeController()
        mock_runner = _make_mock_runner()

        with patch(
            "client_surfaces.operator_tui.visual.browser.browser_mode_controller.CarbonylRunner",
            return_value=mock_runner,
        ), patch.object(
            ctrl._policy, "create_temp_file", return_value=Path("/tmp/fake.html")
        ):
            ctrl.enter_browser_mode(_snap(), cols=80, rows=24)

        ctrl.exit_browser_mode()
        assert ctrl.state == BrowserModeState.INACTIVE
        assert not ctrl.is_active

    def test_exit_when_inactive_is_safe(self) -> None:
        ctrl = BrowserModeController()
        ctrl.exit_browser_mode()  # should not raise
        assert ctrl.state == BrowserModeState.INACTIVE


# ---------------------------------------------------------------------------
# tick
# ---------------------------------------------------------------------------

class TestTick:
    def test_tick_returns_none_when_inactive(self) -> None:
        ctrl = BrowserModeController()
        assert ctrl.tick() is None

    def test_tick_returns_output_when_active(self) -> None:
        ctrl = BrowserModeController()
        mock_runner = _make_mock_runner()
        mock_runner.read_output.return_value = b"frame data"

        with patch(
            "client_surfaces.operator_tui.visual.browser.browser_mode_controller.CarbonylRunner",
            return_value=mock_runner,
        ), patch.object(
            ctrl._policy, "create_temp_file", return_value=Path("/tmp/fake.html")
        ):
            ctrl.enter_browser_mode(_snap(), cols=80, rows=24)

        result = ctrl.tick()
        assert result == b"frame data"

    def test_tick_returns_none_on_empty_output(self) -> None:
        ctrl = BrowserModeController()
        mock_runner = _make_mock_runner()
        mock_runner.read_output.return_value = b""

        with patch(
            "client_surfaces.operator_tui.visual.browser.browser_mode_controller.CarbonylRunner",
            return_value=mock_runner,
        ), patch.object(
            ctrl._policy, "create_temp_file", return_value=Path("/tmp/fake.html")
        ):
            ctrl.enter_browser_mode(_snap(), cols=80, rows=24)

        result = ctrl.tick()
        assert result is None

    def test_tick_detects_stopped_subprocess(self) -> None:
        ctrl = BrowserModeController()
        mock_runner = _make_mock_runner()

        with patch(
            "client_surfaces.operator_tui.visual.browser.browser_mode_controller.CarbonylRunner",
            return_value=mock_runner,
        ), patch.object(
            ctrl._policy, "create_temp_file", return_value=Path("/tmp/fake.html")
        ):
            ctrl.enter_browser_mode(_snap(), cols=80, rows=24)

        mock_runner.is_running.return_value = False
        ctrl.tick()
        assert ctrl.state == BrowserModeState.INACTIVE


# ---------------------------------------------------------------------------
# handle_input and resize
# ---------------------------------------------------------------------------

class TestHandleInputAndResize:
    def _active_ctrl(self) -> tuple[BrowserModeController, MagicMock]:
        ctrl = BrowserModeController()
        mock_runner = _make_mock_runner()
        with patch(
            "client_surfaces.operator_tui.visual.browser.browser_mode_controller.CarbonylRunner",
            return_value=mock_runner,
        ), patch.object(
            ctrl._policy, "create_temp_file", return_value=Path("/tmp/fake.html")
        ):
            ctrl.enter_browser_mode(_snap(), cols=80, rows=24)
        return ctrl, mock_runner

    def test_handle_input_forwarded(self) -> None:
        ctrl, mock_runner = self._active_ctrl()
        ctrl.handle_input(b"\x1b[A")
        mock_runner.write_input.assert_called_once_with(b"\x1b[A")

    def test_handle_input_ignored_when_inactive(self) -> None:
        ctrl = BrowserModeController()
        ctrl.handle_input(b"keys")  # should not raise

    def test_resize_forwarded(self) -> None:
        ctrl, mock_runner = self._active_ctrl()
        ctrl.resize(100, 40)
        mock_runner.resize.assert_called_once_with(100, 40)

    def test_resize_when_inactive_does_not_raise(self) -> None:
        ctrl = BrowserModeController()
        ctrl.resize(80, 24)  # should not raise


class TestWebRtcAppHtml:
    def test_build_webrtc_app_html_uses_local_assets_and_scrubs_tokens(self) -> None:
        html = build_webrtc_app_html({
            "signaling_url": "",
            "session_nonce": "nonce",
            "oidc_subject_hash": "hash",
            "refresh_token": "secret-refresh",
            "turn_servers": [{"urls": "turn:example", "credential": "secret-cred"}],
        })
        assert "<base href=\"file://" in html
        assert "window.ANANTA_CONFIG" in html
        assert "secret-refresh" not in html
        assert "secret-cred" not in html
        assert "media_probe.js" in html
