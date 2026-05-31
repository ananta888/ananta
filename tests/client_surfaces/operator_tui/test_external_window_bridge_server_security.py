from __future__ import annotations

import json
import urllib.error
import urllib.request

from client_surfaces.operator_tui.windowing.bridge_server import ExternalWindowBridgeServer


def test_bridge_state_requires_token() -> None:
    bridge = ExternalWindowBridgeServer()
    bridge.start()
    try:
        url = f"http://127.0.0.1:{bridge.status().port}/state"
        req = urllib.request.Request(url, method="GET")
        try:
            urllib.request.urlopen(req, timeout=2)
            assert False, "expected HTTPError"
        except urllib.error.HTTPError as exc:
            body = json.loads(exc.read().decode("utf-8"))
            assert exc.code == 401
            assert body.get("reason_code") == "window_bridge_unauthorized"
    finally:
        bridge.stop()
