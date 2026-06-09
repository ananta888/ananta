"""Optionale Integrationstests fuer BrowserCamofoxAdapter (task.008).

Werden nur ausgefuehrt wenn CAMOFOX_TEST_URL gesetzt ist, z.B.:
    CAMOFOX_TEST_URL=http://localhost:9377 pytest tests/test_browser_camofox_integration.py -v
"""
from __future__ import annotations

import os

import pytest

from agent.services.browser_camofox_adapter import build_camofox_adapter
from agent.services.browser_task_contract import BrowserTaskContract

CAMOFOX_TEST_URL = os.environ.get("CAMOFOX_TEST_URL", "")

pytestmark = pytest.mark.skipif(
    not CAMOFOX_TEST_URL,
    reason="CAMOFOX_TEST_URL nicht gesetzt — Camofox-Server nicht verfügbar",
)


@pytest.fixture
def live_adapter():
    return build_camofox_adapter({"camofox_url": CAMOFOX_TEST_URL, "timeout_seconds": 15})


@pytest.fixture
def live_contract():
    return BrowserTaskContract.from_payload(
        {
            "allowed_domains": ["example.com"],
            "max_actions": 5,
            "timeout_seconds": 15,
            "download_policy": "deny",
            "auth_policy": "none",
            "screenshot_policy": "on_error",
        }
    )


def test_live_health_check(live_adapter):
    result = live_adapter.health_check()
    assert result["healthy"] is True, f"Camofox-Server nicht erreichbar: {result}"


def test_live_navigate_and_read(live_adapter, live_contract):
    session_id = live_adapter.create_session(contract=live_contract)
    assert session_id

    nav = live_adapter.navigate(url="https://example.com", session_id=session_id, contract=live_contract)
    assert nav.ok, f"Navigate fehlgeschlagen: {nav.error}"

    page = live_adapter.read_page(session_id=session_id, contract=live_contract)
    assert page.ok

    live_adapter.close_session(session_id=session_id)


def test_live_screenshot(live_adapter, live_contract):
    session_id = live_adapter.create_session(contract=live_contract)
    nav = live_adapter.navigate(url="https://example.com", session_id=session_id, contract=live_contract)
    assert nav.ok

    screenshot = live_adapter.screenshot(session_id=session_id, contract=live_contract)
    assert screenshot.ok
    assert screenshot.data

    live_adapter.close_session(session_id=session_id)


def test_live_session_closed_at_end(live_adapter, live_contract):
    session_id = live_adapter.create_session(contract=live_contract)
    close_result = live_adapter.close_session(session_id=session_id)
    assert close_result.ok
