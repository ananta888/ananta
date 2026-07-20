from __future__ import annotations

import pytest

from tests.speech_evidence_support import digest
from voice_runtime.evidence_identity import SpeechEvidenceIdentityService


def _identity(
    service,
    *,
    pair="pair-a",
    session="session-a",
    segment="segment-a",
    source="same-source",
    revision=1,
):
    return service.identify(
        pair_id=pair,
        session_id=session,
        session_epoch=1,
        speaker_scope="speaker-a",
        capture_segment_id=segment,
        start_ms=0,
        end_ms=1000,
        source_digest=digest(source),
        revision=revision,
        revision_digest=digest(f"revision-{revision}"),
    )


def test_revisions_share_family_but_other_pair_session_or_reconnect_do_not() -> None:
    service = SpeechEvidenceIdentityService(b"i" * 32)
    first = _identity(service, revision=1)
    revision = _identity(service, revision=2)
    other_pair = _identity(service, pair="pair-b")
    reconnect = _identity(service, segment="segment-reconnect")

    assert first.utterance_family_id == revision.utterance_family_id
    assert first.evidence_revision_id != revision.evidence_revision_id
    assert len({first.utterance_family_id, other_pair.utterance_family_id, reconnect.utterance_family_id}) == 3
    assert first.source_scope_digest != other_pair.source_scope_digest


def test_identity_is_deterministic_and_algorithm_versioned() -> None:
    service = SpeechEvidenceIdentityService(b"i" * 32)
    value = _identity(service)
    assert value == _identity(service)
    assert value.algorithm_version == "speech-evidence-commitment-hmac-sha256-v1"
    assert value.utterance_family_id == (
        "utterance-v1:6d68a2eebcd8948ed4c40428deb20a1a0ed94f1f10137539867b8dbb7f62e242"
    )
    assert value.evidence_revision_id == (
        "evidence-revision-v1:176c35c9b25c5d0b51adf5ec74ce92ad104f270e636ba7a9d12bf98c3a37d8dc"
    )


@pytest.mark.parametrize("fixture", ["silence", "repeated-phrase", "clipped-waveform"])
def test_audio_edge_fixture_commitments_are_deterministic_and_collision_separated(fixture: str) -> None:
    service = SpeechEvidenceIdentityService(b"i" * 32)
    current = _identity(service, source=fixture)
    replay = _identity(service, source=fixture)
    baseline = _identity(service, source="same-source")

    assert current == replay
    assert current.utterance_family_id != baseline.utterance_family_id
