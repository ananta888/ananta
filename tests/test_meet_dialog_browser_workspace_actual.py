"""Opt-in real sandboxed Meet receiver with a synthetic public document port."""

import os

import pytest


@pytest.mark.timeout(240)
@pytest.mark.skipif(
    os.environ.get("MEET_BROWSER_WORKSPACE_GATE") != "1", reason="opt-in private browser workspace gate"
)
def test_actual_browser_task_pause_privacy_revocation_and_chat_isolation(app, tmp_path, monkeypatch, record_property):
    from tests.meet_dialog_browser_workspace_scenario import BrowserWorkspaceScenario
    from tests.test_meet_dialog_cross_repository import SOAK_SECONDS
    from tests.test_meet_dialog_cross_repository import (
        test_actual_hub_worker_loop_receives_chat_shares_owned_cdp_and_obeys_stop as run_gate,
    )

    assert SOAK_SECONDS == 0, "workspace regression has a fixed short budget"
    monkeypatch.setenv("MEET_CROSS_REPOSITORY_GATE", "1")
    run_gate(
        app,
        tmp_path,
        monkeypatch,
        False,
        False,
        None,
        False,
        False,
        record_property,
        browser_scenario=BrowserWorkspaceScenario(monkeypatch),
    )
