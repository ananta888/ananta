from __future__ import annotations

import os
import pathlib
import socket
import subprocess
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlparse

from client_surfaces.operator_tui.windowing.bridge_server import ExternalWindowBridgeServer
from client_surfaces.operator_tui.windowing.window_surface import ExternalWindowState, WindowHealth, WindowSurface

_PROJECT_ROOT = pathlib.Path(__file__).parent.parent.parent.parent.resolve()
_DIST_BROWSER = _PROJECT_ROOT / "frontend-angular" / "dist" / "ananta-angular" / "browser"


def _is_url_reachable(url: str, *, timeout: float = 0.5) -> bool:
    try:
        p = urlparse(url)
        host = p.hostname or "127.0.0.1"
        port = p.port or 80
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@dataclass(frozen=True)
class ExternalWindowStatus:
    state: ExternalWindowState
    backend: str
    pid: int | None
    reason: str
    bridge_running: bool
    bridge_host: str
    bridge_port: int
    dropped_events: int
    rejected_actions: int
    accepted_actions: int


class NoopWindowSurface:
    backend_name = "noop"

    def open_window(self, *, url: str) -> WindowHealth:
        _ = url
        return WindowHealth(
            state=ExternalWindowState.DEGRADED,
            backend=self.backend_name,
            reason="no_window_backend_configured",
        )

    def close_window(self) -> WindowHealth:
        return WindowHealth(state=ExternalWindowState.INACTIVE, backend=self.backend_name)

    def health(self) -> WindowHealth:
        return WindowHealth(state=ExternalWindowState.INACTIVE, backend=self.backend_name)


class ExternalWindowController:
    def __init__(self, *, surface: WindowSurface, bridge: ExternalWindowBridgeServer) -> None:
        self._surface = surface
        self._bridge = bridge
        self._opened_once = False
        self._current_url = ""
        self._fallback_server_proc: subprocess.Popen | None = None  # type: ignore[type-arg]

    def _ensure_frontend_reachable(self, angular_base: str) -> None:
        """Start a static file server for the pre-built Angular dist if the frontend is not reachable."""
        if _is_url_reachable(angular_base):
            return
        dist_dir = pathlib.Path(os.environ.get("ANANTA_FRONTEND_DIST_DIR", str(_DIST_BROWSER)))
        if not (dist_dir / "index.html").exists():
            return
        p = urlparse(angular_base)
        port = p.port or 4200
        if self._fallback_server_proc is not None and self._fallback_server_proc.poll() is None:
            return
        self._fallback_server_proc = subprocess.Popen(
            ["python3", "-m", "http.server", str(port), "--directory", str(dist_dir)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # Give it a moment to bind
        for _ in range(10):
            time.sleep(0.1)
            if _is_url_reachable(angular_base, timeout=0.2):
                break

    def open(self) -> ExternalWindowStatus:
        self._bridge.start()
        angular_base = os.environ.get("ANANTA_ANGULAR_URL", "http://127.0.0.1:4200").strip()
        if angular_base:
            self._ensure_frontend_reachable(angular_base)
            bridge_status = self._bridge.status()
            params = urlencode({"bridge": f"http://127.0.0.1:{bridge_status.port}", "token": self._bridge.session_token})
            self._current_url = f"{angular_base.rstrip('/')}?{params}"
        else:
            self._current_url = self._bridge.window_url()
        health = self._surface.open_window(url=self._current_url)
        self._opened_once = True
        return self.status(health_override=health)

    def close(self) -> ExternalWindowStatus:
        health = self._surface.close_window()
        self._bridge.stop()
        if self._fallback_server_proc is not None:
            try:
                self._fallback_server_proc.terminate()
            except OSError:
                pass
            self._fallback_server_proc = None
        return self.status(health_override=health)

    def restart(self) -> ExternalWindowStatus:
        _ = self.close()
        return self.open()

    def publish_state(self, payload: dict[str, Any]) -> None:
        self._bridge.publish_state(payload)

    def drain_events(self) -> list[Any]:
        return self._bridge.drain_events()

    def status(self, *, health_override: WindowHealth | None = None) -> ExternalWindowStatus:
        health = health_override or self._surface.health()
        bridge = self._bridge.status()
        return ExternalWindowStatus(
            state=health.state,
            backend=health.backend,
            pid=health.pid,
            reason=health.reason,
            bridge_running=bridge.running,
            bridge_host=bridge.host,
            bridge_port=bridge.port,
            dropped_events=bridge.dropped_events,
            rejected_actions=bridge.rejected_actions,
            accepted_actions=bridge.accepted_actions,
        )

    def view_url(self) -> str:
        return self._current_url or self._bridge.window_url()

    @property
    def opened_once(self) -> bool:
        return self._opened_once
