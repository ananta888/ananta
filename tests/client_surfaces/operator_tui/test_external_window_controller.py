from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from client_surfaces.operator_tui.windowing.bridge_server import ExternalWindowBridgeServer
from client_surfaces.operator_tui.windowing.external_window_controller import ExternalWindowController
from client_surfaces.operator_tui.windowing.window_surface import ExternalWindowState, WindowHealth


class _FakeSurface:
    backend_name = "fake"

    def __init__(self) -> None:
        self.state = ExternalWindowState.INACTIVE
        self.pid = None
        self.last_url = ""

    def open_window(self, *, url: str) -> WindowHealth:
        self.last_url = url
        self.state = ExternalWindowState.ACTIVE
        self.pid = 123
        return self.health()

    def close_window(self) -> WindowHealth:
        self.state = ExternalWindowState.INACTIVE
        self.pid = None
        return self.health()

    def health(self) -> WindowHealth:
        return WindowHealth(state=self.state, backend=self.backend_name, pid=self.pid)


def test_external_window_controller_open_close_restart() -> None:
    bridge = ExternalWindowBridgeServer()
    controller = ExternalWindowController(surface=_FakeSurface(), bridge=bridge)

    started = controller.open()
    assert started.state == ExternalWindowState.ACTIVE
    assert started.bridge_running is True
    assert started.bridge_port > 0

    restarted = controller.restart()
    assert restarted.state == ExternalWindowState.ACTIVE
    assert restarted.bridge_running is True

    closed = controller.close()
    assert closed.state == ExternalWindowState.INACTIVE
    assert closed.bridge_running is False


def test_external_window_controller_status_exposes_bridge_counters() -> None:
    bridge = ExternalWindowBridgeServer()
    controller = ExternalWindowController(surface=_FakeSurface(), bridge=bridge)
    _ = controller.open()
    status = controller.status()
    assert status.accepted_actions >= 0
    assert status.rejected_actions >= 0
    assert status.dropped_events >= 0
    _ = controller.close()


def test_external_window_controller_keeps_bearers_out_of_the_url() -> None:
    bridge = ExternalWindowBridgeServer()
    surface = _FakeSurface()
    controller = ExternalWindowController(surface=surface, bridge=bridge)

    try:
        controller.open(
            auth_context={
                "hub_url": "https://hub.example.test",
                "hub_token": "hub-secret",
                "oidc_token": "oidc-secret",
            }
        )
        parsed = urlparse(surface.last_url)
        query = parse_qs(parsed.query)
        fragment = parse_qs(parsed.fragment)

        assert set(query) == {"bridge"}
        assert set(fragment) == {"ananta_window_token"}
        assert fragment["ananta_window_token"] == [bridge.session_token]
        assert "hub-secret" not in surface.last_url
        assert "oidc-secret" not in surface.last_url
        assert "hub_token" not in surface.last_url
        assert "oidc_token" not in surface.last_url
        assert bridge.session_token not in controller.view_url()
    finally:
        controller.close()


def test_external_window_controller_reloads_auth_context_and_rotates_token_on_restart() -> None:
    bridge = ExternalWindowBridgeServer()
    surface = _FakeSurface()
    calls = 0

    def auth_context_provider() -> dict[str, str]:
        nonlocal calls
        calls += 1
        return {
            "hub_url": f"https://hub-{calls}.example.test",
            "hub_token": f"hub-{calls}",
            "oidc_token": "",
        }

    controller = ExternalWindowController(
        surface=surface,
        bridge=bridge,
        auth_context_provider=auth_context_provider,
    )
    try:
        controller.open()
        first_token = bridge.session_token
        assert bridge.consume_auth_context(first_token) == (
            "ok",
            {
                "hub_url": "https://hub-1.example.test",
                "hub_token": "hub-1",
                "oidc_token": "",
            },
        )

        controller.restart()

        assert bridge.session_token != first_token
        assert bridge.consume_auth_context(bridge.session_token) == (
            "ok",
            {
                "hub_url": "https://hub-2.example.test",
                "hub_token": "hub-2",
                "oidc_token": "",
            },
        )
        assert calls == 2
    finally:
        controller.close()
