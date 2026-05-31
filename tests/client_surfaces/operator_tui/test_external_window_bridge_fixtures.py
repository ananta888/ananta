from __future__ import annotations

import json
from pathlib import Path

from client_surfaces.operator_tui.windowing.bridge_server import ExternalWindowBridgeServer


def _fixture(name: str) -> dict:
    path = Path("tests/fixtures/scenarios/external_window_ai_snake") / name
    return json.loads(path.read_text(encoding="utf-8"))


def test_external_window_state_fixture_is_deterministic_and_versioned() -> None:
    payload = _fixture("basic_state.json")
    assert payload["schema_version"] == "window.bridge.v1"
    assert payload["state_version"] == "fixture-001"
    assert payload["payload"]["snake"]["active"] is True


def test_bridge_publish_state_keeps_schema_version_v1() -> None:
    bridge = ExternalWindowBridgeServer()
    bridge.publish_state({"state_version": "test-01", "snake": {"active": False}})
    # exercise public /state payload builder through status + publish path
    bridge.start()
    try:
        state = bridge._state_payload  # noqa: SLF001 - white-box protocol test
        assert state["schema_version"] == "window.bridge.v1"
        assert state["state_version"] == "test-01"
    finally:
        bridge.stop()
