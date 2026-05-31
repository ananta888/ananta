from __future__ import annotations

from client_surfaces.operator_tui.windowing.bridge_server import ExternalWindowBridgeServer
from client_surfaces.operator_tui.windowing.external_window_controller import ExternalWindowController
from client_surfaces.operator_tui.windowing.window_surface import ExternalWindowState, WindowHealth


class _FlakySurface:
    backend_name = "flaky"

    def __init__(self) -> None:
        self.opens = 0
        self._state = ExternalWindowState.INACTIVE

    def open_window(self, *, url: str) -> WindowHealth:
        _ = url
        self.opens += 1
        if self.opens == 1:
            self._state = ExternalWindowState.FAILED
            return WindowHealth(state=self._state, backend=self.backend_name, reason="first_open_failed")
        self._state = ExternalWindowState.ACTIVE
        return WindowHealth(state=self._state, backend=self.backend_name, pid=777)

    def close_window(self) -> WindowHealth:
        self._state = ExternalWindowState.INACTIVE
        return WindowHealth(state=self._state, backend=self.backend_name)

    def health(self) -> WindowHealth:
        return WindowHealth(state=self._state, backend=self.backend_name, pid=(777 if self._state == ExternalWindowState.ACTIVE else None))


def test_external_window_recovery_restart_after_failed_open() -> None:
    controller = ExternalWindowController(surface=_FlakySurface(), bridge=ExternalWindowBridgeServer())
    first = controller.open()
    assert first.state == ExternalWindowState.FAILED
    assert first.bridge_running is True

    second = controller.restart()
    assert second.state == ExternalWindowState.ACTIVE
    assert second.bridge_running is True

    closed = controller.close()
    assert closed.state == ExternalWindowState.INACTIVE
