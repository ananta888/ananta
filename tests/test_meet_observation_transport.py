"""Observation uses bounded TLS transport and current Hub checks on both sides."""

import io
import json
from dataclasses import replace
from unittest.mock import Mock

import pytest

from agent.services.meet_authorization_client import MeetAuthorizationClient
from agent.services.meet_contract import MeetError
from tests.test_meet_session_observation import observation_fixture

pytestmark = pytest.mark.timeout(30)


def transport(monkeypatch):
    value, args = observation_fixture()
    scope, issuer_name, session, nonce, now = args
    authority = Mock()
    authority.current.return_value = scope
    issuer = Mock(issuer=issuer_name)
    issuer.issue_dialog.return_value = {"grant": "synthetic-only"}
    opener = Mock()
    opener.open.return_value = io.BytesIO(json.dumps(value).encode())
    build = Mock(return_value=opener)
    monkeypatch.setattr("urllib.request.build_opener", build)
    monkeypatch.setattr("agent.services.meet_authorization_client.secrets.token_hex", lambda _: nonce)
    return MeetAuthorizationClient(authority, issuer, clock=lambda: now / 1000), authority, opener, build, value, args


def test_observation_posts_only_closed_request_and_does_not_enable_proxies(monkeypatch):
    client, authority, opener, build, value, args = transport(monkeypatch)
    assert client.observe("task", "dispatch", "runtime", args[2]) == value
    request = opener.open.call_args.args[0]
    assert request.full_url == args[0].origin + "/api/machine/sessions/observation"
    assert json.loads(request.data) == {"roomId": args[0].room_id, "sessionId": args[2], "nonce": args[3]}
    assert opener.open.call_args.kwargs == {"timeout": 3}
    assert build.call_args.args[0].proxies == {}
    assert authority.current.call_count == 2
    with pytest.raises(MeetError, match="redirect_denied"):
        build.call_args.args[1].redirect_request(None)


@pytest.mark.parametrize("change", ["owner_subject", "runtime_id", "deadline", "capabilities"])
def test_task_change_during_observation_io_never_returns_obsolete_state(monkeypatch, change):
    client, authority, _, _, _, args = transport(monkeypatch)
    old = args[0]
    changed = replace(
        old, **{change: () if change == "capabilities" else old.deadline + 1 if change == "deadline" else "foreign"}
    )
    authority.current.side_effect = [old, changed]
    with pytest.raises(MeetError, match="authorization_changed"):
        client.observe("task", "dispatch", "runtime", args[2])


@pytest.mark.parametrize("mode", ["unsupported", "oversize", "foreign-field", "revoked"])
def test_unavailable_invalid_and_revoked_observation_never_falls_back(monkeypatch, mode):
    client, authority, opener, _, value, args = transport(monkeypatch)
    if mode == "unsupported":
        opener.open.side_effect = OSError("synthetic-private-error")
    elif mode == "oversize":
        opener.open.return_value = io.BytesIO(b"x" * 16_385)
    elif mode == "foreign-field":
        opener.open.return_value = io.BytesIO(json.dumps(value | {"allowed": True}).encode())
    else:
        authority.current.side_effect = [args[0], MeetError("synthetic_revoked", 403)]
    with pytest.raises(MeetError) as error:
        client.observe("task", "dispatch", "runtime", args[2])
    assert "synthetic-private-error" not in str(error.value)
    assert opener.open.call_count == 1
