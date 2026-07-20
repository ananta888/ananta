from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from ananta_contracts.speech_evidence_sync import (
    OFFER_PROTOCOL_VERSION,
    SpeechEvidenceMessageVerifier,
    SpeechEvidenceProtocolError,
    SpeechEvidenceReplayWindow,
    canonical_json,
    canonical_sha256,
    validate_payload,
)
from tests.speech_evidence_sync_support import NOW_MS, StaticEvidenceKeys, message


def _verifier(*, keys=None, replay=None):
    return SpeechEvidenceMessageVerifier(
        keys or StaticEvidenceKeys(),
        replay or SpeechEvidenceReplayWindow(width=32, maximum_contexts=4),
        clock_ms=lambda: NOW_MS,
    )


def _verify(verifier, raw):
    return verifier.verify(
        raw,
        expected_session_id="session-test",
        expected_pair_id="pair-test",
        expected_audience_id="peer-b",
        expected_epoch=7,
        expected_consent_version=3,
    )


def test_canonical_utf8_fixture_matches_browser_contract() -> None:
    fixture_path = (
        Path(__file__).parents[1]
        / "frontend-angular/src/app/services/fixtures/speech-evidence-protocol.v1.json"
    )
    golden = json.loads(fixture_path.read_text(encoding="utf-8"))["canonical_utf8"]
    assert canonical_json(golden["value"]).decode("utf-8") == golden["json"]
    assert canonical_sha256(golden["value"]) == golden["sha256"]


def test_signature_payload_and_replay_are_bound_statefully() -> None:
    verifier = _verifier()
    raw = message("inventory")
    verified = _verify(verifier, raw)
    assert verified.header.message_type == "inventory"
    with pytest.raises(SpeechEvidenceProtocolError) as captured:
        _verify(verifier, raw)
    assert captured.value.reason_code == "speech_evidence_replayed"

    tampered = message("inventory", sequence=2)
    tampered["payload"]["leaf_count"] = 3
    with pytest.raises(SpeechEvidenceProtocolError) as captured:
        _verify(verifier, tampered)
    assert captured.value.reason_code == "speech_evidence_payload_digest_mismatch"


@pytest.mark.parametrize(
    ("raw", "keys", "reason"),
    [
        (message("inventory", expires_at_ms=NOW_MS - 1), StaticEvidenceKeys(), "speech_evidence_expired"),
        (message("inventory", audience_id="peer-c"), StaticEvidenceKeys(), "speech_evidence_wrong_audience"),
        (message("inventory"), StaticEvidenceKeys(available=False), "speech_evidence_key_unknown"),
    ],
)
def test_stale_wrong_peer_and_revoked_key_fail_before_payload_use(raw, keys, reason) -> None:
    with pytest.raises(SpeechEvidenceProtocolError) as captured:
        _verify(_verifier(keys=keys), raw)
    assert captured.value.reason_code == reason


def test_replay_window_is_bounded_restartable_and_epoch_scoped() -> None:
    class State:
        value = None

        def load(self):
            return self.value

        def save(self, value):
            self.value = copy.deepcopy(value)

    state = State()
    first = SpeechEvidenceReplayWindow(width=32, maximum_contexts=2, state_port=state)
    _verify(_verifier(replay=first), message("inventory", sequence=5))
    restored = SpeechEvidenceReplayWindow(width=32, maximum_contexts=2, state_port=state)
    with pytest.raises(SpeechEvidenceProtocolError) as captured:
        _verify(_verifier(replay=restored), message("inventory", sequence=5))
    assert captured.value.reason_code == "speech_evidence_replayed"
    restored.advance_epoch(session_id="session-test", pair_id="pair-test", minimum_epoch=8)
    assert not restored.snapshot()["entries"]


def test_replay_sequences_are_isolated_by_traffic_class_not_message_type() -> None:
    verifier = _verifier()
    _verify(verifier, message("inventory", sequence=9))
    with pytest.raises(SpeechEvidenceProtocolError) as captured:
        _verify(verifier, message("offer", sequence=9))
    assert captured.value.reason_code == "speech_evidence_replayed"

    # The bulk lane has its own sequence space, so the same sequence is valid there.
    assert _verify(verifier, message("chunk", sequence=9)).header.message_type == "chunk"


def test_unknown_fields_nonfinite_private_paths_and_chunk_limits_fail_closed() -> None:
    raw = message("inventory")
    raw["unknown_security"] = True
    with pytest.raises(SpeechEvidenceProtocolError) as captured:
        _verify(_verifier(), raw)
    assert captured.value.reason_code == "speech_evidence_unknown_field"

    with pytest.raises(SpeechEvidenceProtocolError) as captured:
        validate_payload("inventory", {**message("inventory")["payload"], "leaf_count": float("nan")})
    assert captured.value.reason_code == "speech_evidence_leaf_count_invalid"

    private = message("offer")["payload"]
    private["group_ids"] = ["../private"]
    with pytest.raises(SpeechEvidenceProtocolError) as captured:
        validate_payload("offer", private, protocol_version=OFFER_PROTOCOL_VERSION)
    assert captured.value.reason_code == "speech_evidence_private_path_forbidden"

    oversized = message("chunk")["payload"]
    oversized["plaintext_bytes"] = 65_537
    with pytest.raises(SpeechEvidenceProtocolError) as captured:
        validate_payload("chunk", oversized)
    assert captured.value.reason_code == "speech_evidence_chunk_oversized"


def test_offer_v2_preview_is_closed_and_signature_bound() -> None:
    signed = message("offer")
    forged = copy.deepcopy(signed)
    forged["payload"]["group_previews"][0]["speaker_scope_digest"] = "f" * 64
    with pytest.raises(SpeechEvidenceProtocolError) as captured:
        _verify(_verifier(), forged)
    assert captured.value.reason_code == "speech_evidence_payload_digest_mismatch"

    missing = copy.deepcopy(signed["payload"])
    missing.pop("group_previews")
    with pytest.raises(SpeechEvidenceProtocolError) as captured:
        validate_payload("offer", missing, protocol_version=OFFER_PROTOCOL_VERSION)
    assert captured.value.reason_code == "speech_evidence_required_field_missing"

    content_leak = copy.deepcopy(signed["payload"])
    content_leak["group_previews"][0]["transcript"] = "must never be previewed"
    with pytest.raises(SpeechEvidenceProtocolError) as captured:
        validate_payload("offer", content_leak, protocol_version=OFFER_PROTOCOL_VERSION)
    assert captured.value.reason_code == "speech_evidence_unknown_field"


@pytest.mark.parametrize(
    ("field", "reason_code"),
    [
        ("group_id", "speech_evidence_source_group_mismatch"),
        ("resolution_digest", "speech_evidence_resolution_digest_mismatch"),
    ],
)
def test_offer_preview_rejects_wrong_source_group_and_resolution_digest(field: str, reason_code: str) -> None:
    body = copy.deepcopy(message("offer")["payload"])
    body["group_previews"][0][field] = "f" * 64
    if field == "group_id":
        body["group_ids"][0] = "f" * 64
    with pytest.raises(SpeechEvidenceProtocolError) as captured:
        validate_payload("offer", body, protocol_version=OFFER_PROTOCOL_VERSION)
    assert captured.value.reason_code == reason_code
