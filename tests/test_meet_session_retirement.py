"""Synthetic retirement contract and bounded Hub transport; no rejoin authority."""

import io
import json
from dataclasses import replace

import pytest

from agent.models.meet_session_retirement import validate_retirement
from agent.services.meet_contract import MeetError
from tests.test_meet_observation_transport import transport
from tests.test_meet_session_observation import observation_fixture

pytestmark = pytest.mark.timeout(30)


def retired(value):
    return {
        "schema": "ananta.meet-session-retired.v1",
        "nonce": value["nonce"],
        "sessionId": value["lease"]["sessionId"],
        "binding": value["binding"],
        "retired": True,
    }


def test_closed_retirement_is_not_new_membership_or_a_renewed_lease():
    value, args = observation_fixture()
    receipt = retired(value)
    assert validate_retirement(receipt, *args) is receipt
    for field in receipt:
        with pytest.raises(MeetError, match="retirement_receipt_invalid"):
            validate_retirement({k: v for k, v in receipt.items() if k != field}, *args)
    for extra in ("lease", "grant", "membershipEpoch", "peerId", "grants"):
        with pytest.raises(MeetError):
            validate_retirement(receipt | {extra: value.get(extra)}, *args)


@pytest.mark.parametrize(
    "field",
    [
        "issuer", "subject", "roomId", "taskId", "tenantId", "projectId", "protocolVersion",
        "runtimeId", "hubSessionId", "capabilitySet",
    ],
)
def test_retirement_cannot_substitute_any_immutable_binding(field):
    value, args = observation_fixture()
    receipt = retired(value)
    receipt["binding"] = receipt["binding"] | {field: "foreign"}
    with pytest.raises(MeetError):
        validate_retirement(receipt, *args)


@pytest.mark.parametrize("patch", [
    {"schema": "ananta.meet-authorization.v1"}, {"nonce": "old"}, {"sessionId": "ms_" + "c" * 32},
    {"retired": False}, {"retired": 1}, {"retired": "true"}, {"binding": None},
])
def test_old_nonce_session_or_unconfirmed_receipt_is_rejected(patch):
    value, args = observation_fixture()
    with pytest.raises(MeetError):
        validate_retirement(retired(value) | patch, *args)


def test_retirement_uses_exact_fixed_tls_path_current_grant_nonce_and_no_redirect(monkeypatch):
    client, authority, opener, build, value, args = transport(monkeypatch)
    receipt = retired(value)
    opener.open.return_value = io.BytesIO(json.dumps(receipt).encode())
    assert client.retire("task", "dispatch", "runtime", args[2]) == receipt
    request = opener.open.call_args.args[0]
    assert request.full_url == args[0].origin + "/api/machine/sessions/retire"
    assert json.loads(request.data) == {"roomId": args[0].room_id, "sessionId": args[2], "nonce": args[3]}
    assert opener.open.call_args.kwargs == {"timeout": 3}
    assert build.call_args.args[0].proxies == {}
    assert authority.current.call_count == 2
    with pytest.raises(MeetError, match="redirect_denied"):
        build.call_args.args[1].redirect_request(None)


@pytest.mark.parametrize("field", ["runtime_id", "lease_id", "owner_subject", "session_id", "capabilities", "deadline"])
def test_authority_change_during_retirement_cannot_authorize_a_replacement(monkeypatch, field):
    client, authority, opener, _, value, args = transport(monkeypatch)
    opener.open.return_value = io.BytesIO(json.dumps(retired(value)).encode())
    changed = () if field == "capabilities" else args[0].deadline + 1 if field == "deadline" else "foreign"
    authority.current.side_effect = [args[0], replace(args[0], **{field: changed})]
    with pytest.raises(MeetError, match="authorization_changed"):
        client.retire("task", "dispatch", "runtime", args[2])
    assert opener.open.call_count == 1


@pytest.mark.parametrize("failure", ["transport", "oversize", "old_nonce", "revoked"])
def test_failed_retirement_never_falls_back_or_repeats_the_operation(monkeypatch, failure):
    client, authority, opener, _, value, args = transport(monkeypatch)
    receipt = retired(value)
    if failure == "transport":
        opener.open.side_effect = OSError("synthetic-private-content")
    elif failure == "oversize":
        opener.open.return_value = io.BytesIO(b"x" * 16385)
    else:
        if failure == "old_nonce":
            receipt["nonce"] = "old"
        else:
            authority.current.side_effect = [args[0], MeetError("synthetic_revoked", 403)]
        opener.open.return_value = io.BytesIO(json.dumps(receipt).encode())
    with pytest.raises(MeetError) as error:
        client.retire("task", "dispatch", "runtime", args[2])
    assert "synthetic-private-content" not in str(error.value)
    assert opener.open.call_count == 1
