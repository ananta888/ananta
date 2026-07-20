from __future__ import annotations

import pytest

from agent.services.speech_evidence_peer_revocation_service import (
    SpeechEvidencePeerRevocationService,
    SpeechEvidenceRevocationError,
)
from ananta_contracts.speech_evidence_sync import (
    SpeechEvidenceMessageVerifier,
    SpeechEvidenceReplayWindow,
)
from tests.speech_evidence_sync_support import NOW_MS, StaticEvidenceKeys, digest, message


class _Fence:
    def __init__(self, events) -> None:
        self.events = events

    def fence(self, **values):
        self.events.append(("fence", values))
        return digest("local-impact")


class _Signer:
    def __init__(self, events) -> None:
        self.events = events

    def sign_revocation(self, payload, *, expires_at_ms):
        self.events.append(("sign", payload["revocation_id"]))
        return {"payload": dict(payload), "expires_at_ms": expires_at_ms}


class _Transport:
    def __init__(self, events, *, online=True) -> None:
        self.events = events
        self.online = online

    def send(self, message):
        self.events.append(("send", message["payload"]["revocation_id"]))
        return self.online


def _service(*, online=True, maximum_attempts=3):
    now = [1_000_000]
    events = []
    transport = _Transport(events, online=online)
    service = SpeechEvidencePeerRevocationService(
        local_fence=_Fence(events),
        signer=_Signer(events),
        transport=transport,
        maximum_attempts=maximum_attempts,
        retry_interval_ms=1_000,
        maximum_duration_ms=5_000,
        clock_ms=lambda: now[0],
    )
    return service, now, events, transport


def _request(service, *, key="revoke-1", groups=("group-a", "group-b")):
    return service.request(
        pair_id="pair-test",
        sender_id="peer-a",
        audience_id="peer-b",
        scope_digest=digest("scope"),
        group_ids=groups,
        reason_code="consent_revoked",
        requested_action="delete",
        revocation_epoch=2,
        idempotency_key=key,
    )


def _ack(record, *, groups=("group-a", "group-b"), decision="complete", impact="impact"):
    raw = message(
        "revocation_ack",
        sequence=7,
        sender_id="peer-b",
        audience_id="peer-a",
        payload_override={
            "revocation_id": record.revocation_id,
            "scope_digest": record.scope_digest,
            "revocation_epoch": record.revocation_epoch,
            "impact_digest": digest(impact),
            "group_results": [
                {"group_id": group, "state": "deleted", "reason_code": "local_cleanup_complete"}
                for group in groups
            ],
            "decision": decision,
        },
    )
    return SpeechEvidenceMessageVerifier(
        StaticEvidenceKeys(), SpeechEvidenceReplayWindow(), clock_ms=lambda: NOW_MS
    ).verify(
        raw,
        expected_session_id="session-test",
        expected_pair_id="pair-test",
        expected_audience_id="peer-a",
        expected_epoch=7,
        expected_consent_version=3,
    )


def test_local_jobs_and_adapters_are_fenced_before_first_remote_send() -> None:
    service, _now, events, _transport = _service()
    record = _request(service)
    assert events[0][0] == "fence"
    assert [event[0] for event in events[1:3]] == ["sign", "send"]
    assert record.state == "pending" and record.attempts == 1
    assert _request(service).revocation_id == record.revocation_id


def test_offline_retry_is_bounded_and_finishes_visible_unresolved_not_deleted() -> None:
    service, now, _events, _transport = _service(online=False, maximum_attempts=2)
    record = _request(service)
    now[0] += 1_000
    record = service.tick(record.revocation_id)
    now[0] += 1_000
    record = service.tick(record.revocation_id)
    assert record.state == "unresolved"
    assert record.unresolved_group_ids == ("group-a", "group-b")
    public = service.status(record.revocation_id)
    assert public["unresolved_count"] == 2
    assert "deleted" not in str(public)


def test_partial_wrong_and_late_ack_are_bound_and_idempotent() -> None:
    service, now, _events, _transport = _service(maximum_attempts=1)
    record = _request(service)
    partial = service.acknowledge(_ack(record, groups=("group-a",), decision="partial"))
    assert partial.state == "partial_ack" and partial.unresolved_group_ids == ("group-b",)

    wrong = _ack(record, groups=("group-foreign",), decision="partial")
    with pytest.raises(SpeechEvidenceRevocationError) as captured:
        service.acknowledge(wrong)
    assert captured.value.reason_code == "speech_evidence_revocation_ack_groups_invalid"

    now[0] += 5_001
    unresolved = service.tick(record.revocation_id)
    assert unresolved.state == "unresolved"
    late = service.acknowledge(_ack(record, decision="complete", impact="late-impact"))
    assert late.state == "resolved_late"
    assert service.acknowledge(_ack(record, decision="complete", impact="late-impact")).state == "resolved_late"


def test_counter_revocation_has_independent_idempotent_identity() -> None:
    service, _now, _events, _transport = _service()
    first = _request(service, key="sender-revoke")
    counter = _request(service, key="recipient-counter", groups=("group-a",))
    assert first.revocation_id != counter.revocation_id
