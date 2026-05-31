from __future__ import annotations

from client_surfaces.operator_tui.windowing.bridge_server import ExternalWindowBridgeServer
from client_surfaces.operator_tui.windowing.external_window_controller import ExternalWindowController
from client_surfaces.operator_tui.windowing.window_surface import ExternalWindowState, WindowHealth


class _FakeSurface:
    backend_name = "fake"

    def __init__(self) -> None:
        self.state = ExternalWindowState.INACTIVE
        self.pid = None

    def open_window(self, *, url: str) -> WindowHealth:
        _ = url
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
