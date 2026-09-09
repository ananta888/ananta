"""Backchannel source capabilities remain separate from sending and local analysis."""

from copy import deepcopy
from dataclasses import replace

import pytest

from agent.services.meet_authorization_client import validate_authorization
from agent.services.meet_contract import MeetError
from ananta_contracts.meet_receive_capability import receive_capability
from tests.test_meet_dialog_audio import runtime
from tests.test_meet_dialog_authority import fixture


def receipt(scope, now, publications):
    peer = "a" * 16
    own = "b" * 16
    session, nonce, issuer = "ms_" + "c" * 32, "d" * 32, "https://hub.example.test"
    value = {
        "schema": "ananta.meet-authorization.v1",
        "nonce": nonce,
        "lease": {
            "schema": "ananta.meet-session-lease.v1",
            "sessionId": session,
            "generation": 1,
            "expiresAt": (now + 120) * 1000,
            "absoluteExpiresAt": (now + 7200) * 1000,
        },
        "binding": {
            "issuer": issuer,
            "subject": "machine:ananta",
            "roomId": scope.room_id,
            "taskId": scope.task_id,
            "tenantId": scope.tenant_id,
            "projectId": scope.project_id,
            "protocolVersion": "v2",
            "runtimeId": scope.runtime_id,
            "hubSessionId": scope.session_id,
            "capabilitySet": ",".join(scope.capabilities),
        },
        "peerId": own,
        "roomId": scope.room_id,
        "membershipEpoch": 1,
        "receiveRevision": 1,
        "grants": [
            {
                "publisherPeerId": peer,
                "machinePeerId": own,
                "publicationIds": publications,
                "chatRead": False,
                "expiresAt": (now + 120) * 1000,
            }
        ],
        "publications": [
            {"peerId": peer, "publicationId": source, "source": source, "publicationEpoch": 1}
            for source in publications
        ],
    }
    return value, (scope, issuer, session, nonce, now * 1000)


@pytest.mark.parametrize(
    "source,required",
    [
        ("camera", "video.receive"),
        ("screen", "video.receive"),
        ("microphone", "audio.receive"),
        ("screen-audio", "audio.receive"),
    ],
)
@pytest.mark.parametrize(
    "capability", ["audio.receive", "video.receive", "avatar.publish", "screen.publish", "speech.publish"]
)
def test_backchannel_needs_the_exact_receive_capability_for_each_source(source, required, capability):
    f = fixture()
    scope = replace(f.authority.current("task", "dispatch", "runtime"), capabilities=(capability,))
    value, args = receipt(scope, f.now, [source])
    assert receive_capability(source) == required
    if capability == required:
        assert validate_authorization(value, *args) == value
    else:
        with pytest.raises(MeetError, match="publications_invalid"):
            validate_authorization(value, *args)


def test_four_granted_sources_are_bounded_and_cannot_be_substituted_or_inferred():
    f = fixture()
    scope = replace(f.authority.current("task", "dispatch", "runtime"), capabilities=("audio.receive", "video.receive"))
    value, args = receipt(scope, f.now, ["microphone", "screen-audio", "camera", "screen"])
    validate_authorization(value, *args)
    for mutation in ("extra_source", "wrong_publisher", "ungranted", "fifth"):
        changed = deepcopy(value)
        if mutation == "extra_source":
            changed["publications"][0]["source"] = "room-mix"
        if mutation == "wrong_publisher":
            changed["publications"][0]["peerId"] = "e" * 16
        if mutation == "ungranted":
            changed["grants"][0]["publicationIds"].pop()
        if mutation == "fifth":
            changed["grants"][0]["publicationIds"].append("fifth")
        with pytest.raises(MeetError):
            validate_authorization(changed, *args)


@pytest.mark.parametrize("source", ["camera", "screen"])
def test_audio_child_cannot_consume_a_visual_grant_even_with_both_capabilities(source):
    f, value, service, payload = runtime()
    f.context["capabilities"] += ["video.receive"]
    f.authority.policies[("tenant", "project")] = frozenset(f.context["capabilities"])
    value["publications"][0]["source"] = source
    with pytest.raises(MeetError, match="source_denied"):
        service.start(payload)
    f.tasks.claim_audio.assert_not_called()
