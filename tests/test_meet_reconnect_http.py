"""Actual signed control HTTP; one fixed recovery window and one grant acceptance."""

import time

import pytest

from tests.test_meet_control_read_http import http_hub

pytestmark = pytest.mark.timeout(30)
SESSION = "ms_" + "a" * 32


def factory(*, mutate=None):
    now = int(time.time() * 1000)
    calls = []

    def response(payload, assignment):
        calls.append(payload)
        issued = len(calls) > 1
        result = {
            "schema": "ananta.meet-reconnect-state.v1",
            "nonce": payload["nonce"],
            "attempt": 1,
            "state": "joining" if issued else "waiting",
            "deadline_ms": now + 20000,
            "ready_ms": now - 10,
            "meeting": assignment["meeting"] | {"grant": "fresh-synthetic"} if len(calls) == 2 else None,
        }
        return mutate(result, len(calls)) if mutate else result

    return response, calls


def test_signed_reconnect_uses_original_window_and_accepts_only_one_issued_grant(tmp_path, monkeypatch):
    response, observed = factory()
    with http_hub(tmp_path, monkeypatch, [200, 200, 200], reconnect=True, response_factory=response) as (hub, calls):
        first = hub.call("reconnect", meet_session_id=SESSION, attempt=0)
        assert first["meeting"] is None and hub.recovery_receipts.attempt == 1
        second = hub.call("reconnect", meet_session_id=SESSION, attempt=1)
        assert second["meeting"]["grant"] == "fresh-synthetic"
        assert hub.call("reconnect", meet_session_id=SESSION, attempt=1)["meeting"] is None
        assert calls == ["reconnect"] * 3
        assert len({r["nonce"] for r in observed}) == 3
        assert {r["meet_session_id"] for r in observed} == {SESSION}


def test_legacy_client_never_contacts_hub_for_unnegotiated_reconnect(tmp_path, monkeypatch):
    with http_hub(tmp_path, monkeypatch, []) as (hub, calls):
        with pytest.raises(ValueError, match="not_available"):
            hub.call("reconnect", meet_session_id=SESSION, attempt=0)
        assert not calls


@pytest.mark.parametrize("failure", ["deadline", "quarantine", "attempt", "phase", "second-grant"])
def test_later_signed_response_cannot_extend_window_regress_phase_or_reissue_grant(tmp_path, monkeypatch, failure):
    def mutate(result, count):
        if count == 3:
            if failure == "deadline":
                result["deadline_ms"] += 1
            elif failure == "quarantine":
                result["ready_ms"] += 1
            elif failure == "attempt":
                result["attempt"] = 2
            elif failure == "phase":
                result["state"] = "waiting"
            else:
                result["meeting"] = {
                    "origin": "https://meet.example.test",
                    "room_id": "room-" + "a" * 18,
                    "grant": "second-synthetic",
                }
        return result

    response, _ = factory(mutate=mutate)
    with http_hub(tmp_path, monkeypatch, [200, 200, 200], reconnect=True, response_factory=response) as (hub, calls):
        hub.call("reconnect", meet_session_id=SESSION, attempt=0)
        hub.call("reconnect", meet_session_id=SESSION, attempt=1)
        with pytest.raises(ValueError, match="hub_revoked_or_unavailable"):
            hub.call("reconnect", meet_session_id=SESSION, attempt=1)
        with pytest.raises(ValueError, match="not_available"):
            hub.call("reconnect", meet_session_id=SESSION, attempt=1)
        assert len(calls) == 3


@pytest.mark.parametrize("status", [401, 403, 409, 502, 503, 504, "bad-signature"])
def test_uncertain_reconnect_response_is_terminal_without_control_read_retry(tmp_path, monkeypatch, status):
    response, _ = factory()
    with http_hub(tmp_path, monkeypatch, [status], reconnect=True, response_factory=response) as (hub, calls):
        with pytest.raises(ValueError, match="hub_revoked_or_unavailable") as error:
            hub.call("reconnect", meet_session_id=SESSION, attempt=0)
        assert type(error.value) is ValueError
        with pytest.raises(ValueError, match="not_available"):
            hub.call("reconnect", meet_session_id=SESSION, attempt=0)
        assert calls == ["reconnect"] and hub.recovery_receipts.attempt == 0
