from __future__ import annotations

import base64
import time

import pytest
from sqlmodel import Session

from agent.database import engine
from agent.db_models import VoiceResultArtifactDB
from agent.services.voice_governance_domain import VoiceGovernanceError, VoicePrincipal
from agent.services.voice_result_artifact_service import VoiceResultArtifactService


def _result(**extra):
    return {
        "text": "safe transcript",
        "candidates": [{"candidate_id": "candidate-1", "text": "safe transcript"}],
        **extra,
    }


@pytest.mark.parametrize(
    "nested_payload",
    [
        {"provenance": {"raw_audio": "UklGRg=="}},
        {"candidates": [{"candidate_id": "candidate-1", "audio_bytes": [1, 2, 3]}]},
        {"provenance": {"attachment": "data:audio/wav;base64,UklGRg=="}},
        {"provenance": {"attachment": "UklGR" + "A" * 512}},
        {"provenance": {"rawAudio": "hidden by camelCase"}},
        {"provenance": {"attachment": base64.b64encode(b"\x00\xff" * 400).decode("ascii")}},
    ],
)
def test_result_artifact_rejects_nested_or_encoded_audio(app, nested_payload) -> None:
    principal = VoicePrincipal(tenant_id="artifact-security", subject="artifact-security")
    service = VoiceResultArtifactService(retention_resolver=lambda _principal, _profile: 86_400)

    with app.app_context(), pytest.raises(VoiceGovernanceError) as error:
        service.create(
            principal,
            request_hash="a" * 64,
            profile_id="artifact-security-profile",
            result=_result(**nested_payload),
        )

    assert error.value.code == "voice_result.raw_audio_forbidden"


def test_result_artifact_retention_never_exceeds_resolved_consent_policy(app) -> None:
    principal = VoicePrincipal(tenant_id="artifact-retention", subject="artifact-retention")
    service = VoiceResultArtifactService(retention_resolver=lambda _principal, _profile: 3_600)
    started = time.time()

    with app.app_context():
        artifact = service.create(
            principal,
            request_hash="b" * 64,
            profile_id="artifact-retention-profile",
            result=_result(),
            retention_seconds=30 * 86_400,
        )

    assert started + 3_540 <= artifact["expires_at"] <= time.time() + 3_600


def test_result_artifact_recovers_only_live_scope_and_profile_envelope(app) -> None:
    principal = VoicePrincipal(tenant_id="artifact-recovery", subject="artifact-owner")
    other_principal = VoicePrincipal(tenant_id="artifact-recovery", subject="other-owner")
    request_ref = f"voice-request-{'c' * 64}"
    service = VoiceResultArtifactService(retention_resolver=lambda _principal, _profile: 3_600)

    with app.app_context():
        created = service.create(
            principal,
            request_hash=request_ref,
            profile_id="artifact-recovery-profile",
            result=_result(),
        )
        recovered = service.find_live_envelope(
            principal,
            request_ref=request_ref,
            profile_id="artifact-recovery-profile",
        )
        wrong_profile = service.find_live_envelope(
            principal,
            request_ref=request_ref,
            profile_id="another-profile",
        )
        wrong_principal = service.find_live_envelope(
            other_principal,
            request_ref=request_ref,
            profile_id="artifact-recovery-profile",
        )

    assert recovered is not None
    assert recovered["id"] == created["id"]
    assert recovered["result"] == _result()
    assert wrong_profile is None
    assert wrong_principal is None

    with Session(engine) as session:
        envelope = session.get(VoiceResultArtifactDB, created["id"])
        assert envelope is not None
        envelope.expires_at = time.time() - 1
        session.add(envelope)
        session.commit()

    with app.app_context():
        assert (
            service.find_live_envelope(
                principal,
                request_ref=request_ref,
                profile_id="artifact-recovery-profile",
            )
            is None
        )
