from __future__ import annotations

import json
import os
from pathlib import Path

from client_surfaces.operator_tui.windowing.backends.wslg_webview_backend import wslg_available
from client_surfaces.operator_tui.windowing.bridge_server import ExternalWindowBridgeServer
from client_surfaces.operator_tui.windowing.external_window_controller import ExternalWindowController
from client_surfaces.operator_tui.windowing.window_surface import ExternalWindowState, WindowHealth


class _MockSurface:
    backend_name = "mock_window"

    def __init__(self) -> None:
        self._state = ExternalWindowState.INACTIVE

    def open_window(self, *, url: str) -> WindowHealth:
        _ = url
        self._state = ExternalWindowState.ACTIVE
        return WindowHealth(state=self._state, backend=self.backend_name, pid=4242)

    def close_window(self) -> WindowHealth:
        self._state = ExternalWindowState.INACTIVE
        return WindowHealth(state=self._state, backend=self.backend_name)

    def health(self) -> WindowHealth:
        return WindowHealth(state=self._state, backend=self.backend_name, pid=(4242 if self._state == ExternalWindowState.ACTIVE else None))


def test_tui_external_window_ai_snake_e2e_generates_evidence(tmp_path: Path) -> None:
    wslg_ok, wslg_reason = wslg_available()
    controller = ExternalWindowController(surface=_MockSurface(), bridge=ExternalWindowBridgeServer())
    started = controller.open()
    controller.publish_state(
        {
            "state_version": "e2e-001",
            "mode": "normal",
            "section": "dashboard",
            "snake": {"active": True, "paused": False},
        }
    )
    events = [
        {"event_id": "e2e-ev-1", "action_id": "view.doc"},
        {"event_id": "e2e-ev-2", "action_id": "snake.pause"},
        {"event_id": "e2e-ev-3", "action_id": "snake.resume"},
    ]
    evidence = {
        "status": "ok",
        "window_state": started.state.value,
        "bridge_port": started.bridge_port,
        "backend": started.backend,
        "wslg_available": wslg_ok,
        "wslg_reason": wslg_reason,
        "events": events,
        "reason_code": "",
    }
    if not wslg_ok and not os.environ.get("WSL_DISTRO_NAME"):
        evidence["status"] = "degraded"
        evidence["reason_code"] = "window_start_degraded_no_wslg"
    artifact = tmp_path / "external-window-ai-snake-e2e.json"
    artifact.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")

    assert started.state in {ExternalWindowState.ACTIVE, ExternalWindowState.DEGRADED, ExternalWindowState.FAILED}
    assert artifact.exists()
    loaded = json.loads(artifact.read_text(encoding="utf-8"))
    assert loaded["events"][0]["action_id"] == "view.doc"
    assert loaded["events"][1]["action_id"] == "snake.pause"
    assert loaded["events"][2]["action_id"] == "snake.resume"
    _ = controller.close()
