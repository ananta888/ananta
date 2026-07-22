from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent.services.sfu_receiver_quality_ingestion_service import (
    SfuReceiverQualityAuthority,
    SfuReceiverQualityCommand,
    SfuReceiverQualityError,
    SfuReceiverQualityIngestionService,
    SfuReceiverQualityPolicy,
    build_sfu_receiver_quality_validator,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads(
    (ROOT / "tests/fixtures/webrtc/receiver_quality_observation/valid_privacy_bounded.v1.json").read_text(
        encoding="utf-8"
    )
)
NOW = datetime.fromisoformat(FIXTURE["validation_context"]["now"].replace("Z", "+00:00")).timestamp()


class _Authority:
    def __init__(self):
        scope = FIXTURE["validation_context"]["active_scope"]
        self.value = SfuReceiverQualityAuthority(
            tenant_ref=scope["tenant_ref"],
            room_ref=scope["room_ref"],
            subscriber_ref=scope["subscriber_ref"],
            subscription_ref="subscription-fixture-01",
            publication_ref=scope["publication_ref"],
            browser_instance_pseudonym=scope["browser_instance_pseudonym"],
            membership_epoch=7,
            route_epoch=scope["route_epoch"],
            allowed_layer=scope["allowed_layer"],
        )
        self.calls = []

    def resolve(self, command):
        self.calls.append(command)
        return self.value


def _document(policy: SfuReceiverQualityPolicy | None = None):
    document = copy.deepcopy(FIXTURE["instance"])
    if policy is not None:
        document["limits"] = policy.declared_limits()
    return document


def _raw(document):
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode()


def _command(document=None):
    return SfuReceiverQualityCommand(
        raw_document=_raw(document or _document()),
        actor_id="subscriber-fixture-01",
        tenant_id="tenant-fixture",
        session_id="session-fixture",
        membership_epoch=7,
        subscription_ref="subscription-fixture-01",
    )


def _service(*, authority=None, clock=lambda: NOW, policy=None):
    return SfuReceiverQualityIngestionService(
        authority=authority or _Authority(),
        validator=build_sfu_receiver_quality_validator(clock=clock),
        policy=policy,
        clock=clock,
    )


def test_accepts_only_current_authenticated_subscription_and_keeps_report_non_authoritative():
    authority = _Authority()
    service = _service(authority=authority)
    result = service.ingest(_command())
    assert result.payload() == {
        "ok": True,
        "status": "accepted",
        "reason_code": "ok",
        "retained_report_count": 1,
        "sequence": 41,
        "gap_count": 0,
        "authoritative": False,
        "authorization_effect": "none",
    }
    window = service.read_window(_command())
    assert len(window) == 1
    assert window[0]["advisory_only"] is True
    assert authority.calls


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("tenant_ref", "tenant-other", "cross_tenant_observation"),
        ("room_ref", "sfu-fedcba9876543210fedcba9876543210", "cross_room_observation"),
        ("subscriber_ref", "subscriber-other-01", "cross_subscriber_observation"),
        ("publication_ref", "publication-other-01", "cross_publication_observation"),
        ("browser_instance_pseudonym", "room-bip_BBBBBBBBBBBBBBBBBBBBBB", "browser_pseudonym_scope_mismatch"),
        ("route_epoch", 8, "stale_route_epoch"),
        ("route_epoch", 10, "route_epoch_mismatch"),
    ],
)
def test_scope_publication_route_and_browser_bindings_are_fail_closed(field, value, expected):
    document = _document()
    document[field] = value
    with pytest.raises(SfuReceiverQualityError, match=expected):
        _service().ingest(_command(document))


@pytest.mark.parametrize(
    ("key", "value", "expected"),
    [
        ("ip_address", "192.0.2.10", "privacy_ip_forbidden"),
        ("device_label", "Built-in Display", "privacy_device_forbidden"),
        ("fingerprint", "stable-device", "privacy_fingerprint_forbidden"),
        ("sdp", "v=0", "privacy_sdp_forbidden"),
        ("raw_stats", {"packets": 2}, "privacy_raw_stats_forbidden"),
        ("media_payload_base64", "AAECAw==", "privacy_media_forbidden"),
        ("transcript", "private words", "privacy_transcript_forbidden"),
        ("embedding", [0.1, 0.2], "privacy_embedding_forbidden"),
    ],
)
def test_privacy_payloads_are_rejected_before_storage(key, value, expected):
    document = _document()
    document["samples"][0][key] = value
    service = _service()
    with pytest.raises(SfuReceiverQualityError, match=expected):
        service.ingest(_command(document))
    assert service.read_window(_command()) == ()


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_json_tokens_are_rejected(token):
    raw = _raw(_document()).replace(b'"rtt_ms":48', f'"rtt_ms":{token}'.encode())
    command = _command()
    command = SfuReceiverQualityCommand(
        raw, command.actor_id, command.tenant_id, command.session_id,
        command.membership_epoch, command.subscription_ref,
    )
    with pytest.raises(SfuReceiverQualityError, match="invalid_json_non_finite"):
        _service().ingest(command)


def test_duplicate_reorder_gap_and_stale_sequence_have_stable_outcomes():
    now = [NOW]
    service = _service(clock=lambda: now[0])
    assert service.ingest(_command()).reason_code == "ok"
    assert service.ingest(_command()).reason_code == "observation_duplicate"

    now[0] += 5
    reordered = _document()
    reordered["sequence"] = 40
    assert service.ingest(_command(reordered)).reason_code == "observation_reordered"

    now[0] += 5
    gap = _document()
    gap["sequence"] = 44
    gap["issued_at"] = datetime.fromtimestamp(
        now[0], tz=timezone.utc
    ).isoformat().replace("+00:00", "Z")
    for sample in gap["samples"]:
        sample["observed_at"] = gap["issued_at"]
    accepted = service.ingest(_command(gap))
    assert accepted.reason_code == "observation_sequence_gap"
    assert accepted.gap_count == 2

    now[0] += 5
    stale = _document()
    stale["sequence"] = 1
    with pytest.raises(SfuReceiverQualityError, match="observation_sequence_stale"):
        service.ingest(_command(stale))


def test_interval_rate_history_retention_and_cleanup_are_bounded():
    now = [NOW]
    policy = SfuReceiverQualityPolicy(
        quality_reports_per_window_max=3,
        quality_report_window_seconds=60,
        quality_report_interval_ms_min=1_000,
        history_reports_max=3,
        retention_seconds=2,
    )
    service = _service(clock=lambda: now[0], policy=policy)
    service.ingest(_command(_document(policy)))
    second = _document(policy)
    second["sequence"] = 42
    with pytest.raises(SfuReceiverQualityError, match="report_interval_too_short"):
        service.ingest(_command(second))
    for sequence in (42, 43):
        now[0] += 1
        document = _document(policy)
        document["sequence"] = sequence
        document["issued_at"] = datetime.fromtimestamp(
            now[0], tz=timezone.utc
        ).isoformat().replace("+00:00", "Z")
        for sample in document["samples"]:
            sample["observed_at"] = document["issued_at"]
        service.ingest(_command(document))
    now[0] += 1
    fourth = _document(policy)
    fourth["sequence"] = 44
    with pytest.raises(SfuReceiverQualityError, match="report_rate_exceeded"):
        service.ingest(_command(fourth))
    now[0] += 2
    assert service.read_window(_command()) == ()
    assert service.purge_subscription(
        tenant_id="tenant-fixture", session_id="session-fixture",
        subscriber_ref="subscriber-fixture-01", subscription_ref="subscription-fixture-01",
    ) == 0


def test_leave_and_revoke_cleanup_cannot_mutate_authority_or_topology():
    authority = _Authority()
    service = _service(authority=authority)
    service.ingest(_command())
    before = authority.value
    assert service.purge_participant(
        tenant_id="tenant-fixture",
        session_id="session-fixture",
        subscriber_ref="subscriber-fixture-01",
    ) == 1
    assert authority.value == before
    assert service.read_window(_command()) == ()
