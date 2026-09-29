"""Private actual Hub/Worker/Meet continuation through one explicit test-only 503."""

import os
import threading

import pytest


@pytest.mark.timeout(240)
@pytest.mark.skipif(os.environ.get("MEET_CONTROL_RECOVERY_GATE") != "1", reason="opt-in private control recovery gate")
def test_actual_dialog_continues_through_transient_control_read_without_replaying_media(
    app, tmp_path, monkeypatch, record_property
):
    from agent.services.meet_contract import MeetError
    from tests.test_meet_dialog_cross_repository import SOAK_SECONDS
    from tests.test_meet_dialog_cross_repository import (
        test_actual_hub_worker_loop_receives_chat_shares_owned_cdp_and_obeys_stop as run_gate,
    )

    assert SOAK_SECONDS == 0, "read-recovery regression has a fixed short budget"
    monkeypatch.setenv("MEET_CROSS_REPOSITORY_GATE", "1")
    lock = threading.Lock()
    observed = {"reads": 0, "injected": 0, "recovered": False}

    def start(service, *args, **kwargs):
        native_exchange = service.exchange

        def exchange(payload):
            with lock:
                observed["reads"] += 1
                if observed["reads"] == 2:
                    observed["injected"] += 1
                    raise MeetError("meet_authorization_unavailable", 503)
            result = native_exchange(payload)
            with lock:
                if observed["reads"] > 2:
                    observed["recovered"] = True
            return result

        monkeypatch.setattr(service, "exchange", exchange)
        return service.start(*args, **kwargs)

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
        start_scenario=start,
    )
    assert observed["injected"] == 1 and observed["recovered"] and observed["reads"] >= 3
    record_property(
        "actual_control_read_recovery",
        {
            "synthetic_policy_and_model": True,
            "injected_http_status": 503,
            "injections": 1,
            "new_signed_state_after_failure": True,
            "unchanged_control_freshness_ms": 2500,
            "actual_chat_and_screen_continuation": True,
            "production_release_evidence": False,
        },
    )
