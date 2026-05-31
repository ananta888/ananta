from __future__ import annotations

import os
import shutil

from client_surfaces.operator_tui.windowing.process_runner import run_detached
from client_surfaces.operator_tui.windowing.window_surface import ExternalWindowState, WindowHealth


def wslg_available() -> tuple[bool, str]:
    display = str(os.environ.get("DISPLAY") or "").strip()
    wayland = str(os.environ.get("WAYLAND_DISPLAY") or "").strip()
    if display or wayland:
        return True, ""
    return False, "no DISPLAY/WAYLAND_DISPLAY"


class WslgWebviewBackend:
    backend_name = "wslg_webview"

    def __init__(self) -> None:
        self._state = ExternalWindowState.INACTIVE
        self._pid: int | None = None
        self._reason = ""

    def open_window(self, *, url: str) -> WindowHealth:
        ok, reason = wslg_available()
        if not ok:
            self._state = ExternalWindowState.DEGRADED
            self._reason = reason
            return self.health()

        opener = str(os.environ.get("ANANTA_WINDOW_OPEN_CMD") or "").strip() or "xdg-open"
        if shutil.which(opener) is None:
            self._state = ExternalWindowState.DEGRADED
            self._reason = f"opener missing: {opener}"
            return self.health()

        self._state = ExternalWindowState.STARTING
        started = run_detached([opener, url])
        if not started.ok:
            self._state = ExternalWindowState.FAILED
            self._reason = started.reason
            self._pid = None
            return self.health()

        self._pid = started.pid
        self._state = ExternalWindowState.ACTIVE
        self._reason = ""
        return self.health()

    def close_window(self) -> WindowHealth:
        self._state = ExternalWindowState.INACTIVE
        self._pid = None
        self._reason = ""
        return self.health()

    def health(self) -> WindowHealth:
        return WindowHealth(
            state=self._state,
            backend=self.backend_name,
            pid=self._pid,
            reason=self._reason,
            details={"display": str(os.environ.get("DISPLAY") or ""), "wayland": str(os.environ.get("WAYLAND_DISPLAY") or "")},
        )
