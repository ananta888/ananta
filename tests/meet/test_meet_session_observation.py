"""Synthetic closed control-plane state, never proof of decoded media."""

from copy import deepcopy
from dataclasses import replace

import pytest

from agent.models.meet_session_observation import SCHEMA, validate_observation
from agent.services.meet_contract import MeetError
from tests.test_meet_dialog_authority import fixture

pytestmark = pytest.mark.timeout(30)


def observation_fixture():
    f = fixture()
    scope = replace(
        f.authority.current("task", "dispatch", "runtime"),
        capabilities=("avatar.publish", "screen.publish", "speech.publish"),
    )
    session, nonce, issuer = "ms_" + "a" * 32, "b" * 32, "https://hub.example.test"
    value = {
        "schema": SCHEMA,
        "nonce": nonce,
        "peerId": "c" * 16,
        "roomId": scope.room_id,
        "membershipEpoch": 1,
        "publicationRevision": 4,
        "lease": {
            "schema": "ananta.meet-session-lease.v1",
            "sessionId": session,
            "generation": 1,
            "expiresAt": (f.now + 120) * 1000,
            "absoluteExpiresAt": (f.now + 7200) * 1000,
        },
        "binding": {
            "issuer": issuer,
            "subject": "machine:ananta",
            "roomId": scope.room_id,
            "taskId": "task",
            "tenantId": "tenant",
            "projectId": "project",
            "protocolVersion": "v2",
            "runtimeId": "runtime",
            "hubSessionId": "hub-session",
            "capabilitySet": ",".join(scope.capabilities),
        },
        "publications": [
            {"publicationId": name, "source": source, "publicationEpoch": index}
            for index, (name, source) in enumerate(
                [("avatar", "camera"), ("screen", "screen"), ("voice", "microphone")], 1
            )
        ],
    }
    return value, (scope, issuer, session, nonce, f.now * 1000)


def test_own_sources_and_idle_membership_are_distinct_from_receive_authorization():
    value, args = observation_fixture()
    assert validate_observation(value, *args) is value
    idle = value | {"publicationRevision": 0, "publications": []}
    assert validate_observation(idle, *args) is idle
    for key in value:
        changed = deepcopy(value)
        changed.pop(key)
        with pytest.raises(MeetError):
            validate_observation(changed, *args)
    with pytest.raises(MeetError):
        validate_observation(value | {"grants": []}, *args)


@pytest.mark.parametrize(
    "field",
    [
        "issuer",
        "subject",
        "roomId",
        "taskId",
        "tenantId",
        "projectId",
        "protocolVersion",
        "runtimeId",
        "hubSessionId",
        "capabilitySet",
    ],
)
def test_every_immutable_binding_must_match_current_hub_authority(field):
    value, args = observation_fixture()
    value["binding"][field] = "foreign"
    with pytest.raises(MeetError, match="scope_invalid"):
        validate_observation(value, *args)


@pytest.mark.parametrize(
    "patch",
    [
        {"schema": "ananta.meet-authorization.v1"},
        {"nonce": "wrong"},
        {"peerId": True},
        {"roomId": "foreign"},
        {"membershipEpoch": True},
        {"membershipEpoch": 0},
        {"publicationRevision": True},
        {"publicationRevision": -1},
        {"publicationRevision": 2**53},
        {"publications": None},
        {"publications": [{}, {}, {}, {}]},
    ],
)
def test_malformed_or_unbound_observations_fail_closed(patch):
    value, args = observation_fixture()
    with pytest.raises(MeetError):
        validate_observation(value | patch, *args)


@pytest.mark.parametrize(
    "patch",
    [
        {"sessionId": "foreign"},
        {"generation": True},
        {"generation": 0},
        {"generation": 513},
        {"expiresAt": 0},
        {"absoluteExpiresAt": 0},
        {"expiresAt": 2**53},
        {"absoluteExpiresAt": 2**53},
    ],
)
def test_expired_wrong_session_and_unbounded_lease_cannot_be_observed(patch):
    value, args = observation_fixture()
    value["lease"].update(patch)
    with pytest.raises(MeetError):
        validate_observation(value, *args)


@pytest.mark.parametrize(
    "patch",
    [
        {"source": "human_device_capture"},
        {"source": "__proto__"},
        {"source": "screen-audio"},
        {"source": []},
        {"publicationId": True},
        {"publicationId": "x" * 129},
        {"publicationId": "screen"},
        {"publicationEpoch": True},
        {"publicationEpoch": 0},
        {"publicationEpoch": 5},
        {"publicationEpoch": 2},
        {"source": "screen"},
        {"peerId": "foreign"},
        {"text": "synthetic-private"},
    ],
)
def test_publications_are_closed_unique_owned_and_bounded(patch):
    value, args = observation_fixture()
    value["publications"][0].update(patch)
    with pytest.raises(MeetError, match="publications_invalid"):
        validate_observation(value, *args)


def test_matching_but_nonpublishing_scope_does_not_gain_rights_from_observation():
    value, args = observation_fixture()
    scope = replace(args[0], capabilities=("chat.send",))
    value["binding"]["capabilitySet"] = "chat.send"
    with pytest.raises(MeetError, match="publications_invalid"):
        validate_observation(value, scope, *args[1:])
