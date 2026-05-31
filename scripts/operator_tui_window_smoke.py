#!/usr/bin/env python3
from __future__ import annotations

import json

from client_surfaces.operator_tui.windowing.backends.wslg_webview_backend import WslgWebviewBackend
from client_surfaces.operator_tui.windowing.bridge_server import ExternalWindowBridgeServer
from client_surfaces.operator_tui.windowing.external_window_controller import ExternalWindowController


def main() -> int:
    ctrl = ExternalWindowController(
        surface=WslgWebviewBackend(),
        bridge=ExternalWindowBridgeServer(),
    )
    started = ctrl.open()
    payload = {
        "started": started.state.value in {"active", "starting"},
        "pid": started.pid,
        "backend": started.backend,
        "bridge_port": started.bridge_port,
        "degraded_reason": started.reason,
        "state": started.state.value,
    }
    print(json.dumps(payload, ensure_ascii=False))
    _ = ctrl.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
