"""Current floor and completion traverse the ordinary signed Hub control read."""

import pytest

from tests.test_meet_control_read_http import http_hub
from tests.test_meet_speaker_contract import permit


def test_signed_control_reports_exact_completion_without_blocking_local_cleanup(tmp_path, monkeypatch):
    observed = []
    with http_hub(tmp_path, monkeypatch, [200, 200], speaker_floor=True, observe=observed.append) as (hub, calls):
        value = permit()
        hub.report_speech_finished(value)
        value["sequence"] = 99
        assert not calls  # Local handoff performs no HTTP, wait or orchestration.
        for _ in range(2):
            result = hub.call("exchange", meet_session_id="ms_" + "a" * 32)
            assert result["speaker_floor"] is None
        assert [p["speech_finished"] for p in observed] == [permit(), permit()]


@pytest.mark.parametrize("projection", [None, permit()])
def test_signed_projection_is_present_even_when_no_speaker_is_admitted(tmp_path, monkeypatch, projection):
    with http_hub(tmp_path, monkeypatch, [200], speaker_floor=True, floor_projection=projection) as (hub, calls):
        assert hub.call("exchange", meet_session_id="ms_" + "a" * 32)["speaker_floor"] == projection
        assert calls == ["exchange"]


@pytest.mark.parametrize("projection", [True, {}, permit() | {"sequence": True}, permit() | {"extra": 1}])
def test_malformed_signed_floor_fails_without_retry_or_legacy_fallback(tmp_path, monkeypatch, projection):
    with http_hub(tmp_path, monkeypatch, [200], speaker_floor=True, floor_projection=projection) as (hub, calls):
        with pytest.raises(ValueError, match="hub_revoked_or_unavailable"):
            hub.call("exchange", meet_session_id="ms_" + "a" * 32)
        assert calls == ["exchange"]


@pytest.mark.parametrize("server_floor", [False, True])
def test_floor_control_negotiation_mismatch_fails_closed(tmp_path, monkeypatch, server_floor):
    with http_hub(tmp_path, monkeypatch, [200], speaker_floor=server_floor) as (hub, calls):
        hub.speaker_floor = not server_floor
        with pytest.raises(ValueError, match="hub_revoked_or_unavailable"):
            hub.call("exchange", meet_session_id="ms_" + "a" * 32)
        assert calls == ["exchange"]


def test_legacy_completion_is_denied_before_network_access(tmp_path, monkeypatch):
    with http_hub(tmp_path, monkeypatch, []) as (hub, calls):
        with pytest.raises(ValueError, match="not_negotiated"):
            hub.report_speech_finished(permit())
        assert not calls
