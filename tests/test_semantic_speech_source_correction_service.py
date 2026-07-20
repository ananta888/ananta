from __future__ import annotations

import hashlib
import time
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agent.services.semantic_speech_source_correction_service import (
    SemanticSpeechSourceCorrectionError,
    SemanticSpeechSourceCorrectionService,
    semantic_speech_security_contract_digest,
)
from ananta_contracts.speech_evidence_governance import SpeechEvidenceGovernanceError

NOW_MS = 10_000
ROOT = Path(__file__).resolve().parents[1]


def _audio() -> bytes:
    return b"bounded-source-audio"


def _raw(**changes: object) -> dict[str, object]:
    source_digest = hashlib.sha256(_audio()).hexdigest()
    value: dict[str, object] = {
        "session_id": "session-a",
        "epoch": 2,
        "turn_id": "turn-a",
        "final_revision": 3,
        "consent_id": "consent-a",
        "consent_version": 4,
        "consent_digest": "b" * 64,
        "consent_revocation_epoch": 1,
        "contract_digest": semantic_speech_security_contract_digest("session-a", 2),
        "source_digest": source_digest,
        "source_expires_at_ms": NOW_MS + 30_000,
        "deadline_at_ms": NOW_MS + 20_000,
        "final_text": "Wir testen alte Worte",
    }
    value.update(changes)
    return value


def _source_result(text: str = "Wir testen neue klare Worte") -> dict[str, object]:
    return {
        "schema_version": "2.0",
        "text": text,
        "raw_backend": "fixture-source-asr",
        "confidence": 0.9,
        "segments": [],
        "candidates": [],
    }


def test_product_composition_calls_only_canonical_alignment_and_is_duplicate_safe() -> None:
    service = SemanticSpeechSourceCorrectionService()
    command = service.command(
        _raw(), source_audio=_audio(), requested_at_ms=NOW_MS, consent_granted=True
    )

    first = service.correct(command, _source_result())
    second = service.correct(command, _source_result("unreachable duplicate text"))

    assert first == second
    assert first["authority"] == "corrected"
    assert first["text"] == "Wir testen neue klare Worte"
    assert first["correction_attempted"] is True
    assert {item["kind"] for item in first["operations"]} >= {"equal", "replace"}
    assert all(str(item["candidate_id"]).startswith("source-asr-") for item in first["operations"])
    assert not (ROOT / "voice_runtime" / "alignment.py").exists()


@pytest.mark.parametrize(
    ("changes", "audio", "reason"),
    [
        ({"source_digest": "f" * 64}, _audio(), "source_digest_mismatch"),
        ({"contract_digest": "f" * 64}, _audio(), "source_correction_contract_mismatch"),
        ({"deadline_at_ms": NOW_MS}, _audio(), "correction_deadline_elapsed"),
        ({"deadline_at_ms": NOW_MS + 30_001}, _audio(), "source_correction_deadline_invalid"),
        ({}, b"", "source_digest_mismatch"),
    ],
)
def test_product_composition_rejects_unbound_or_unbounded_source(
    changes: dict[str, object], audio: bytes, reason: str
) -> None:
    service = SemanticSpeechSourceCorrectionService()

    with pytest.raises(SemanticSpeechSourceCorrectionError, match=reason) as raised:
        service.command(_raw(**changes), source_audio=audio, requested_at_ms=NOW_MS, consent_granted=True)

    assert raised.value.reason_code == reason


def test_product_composition_requires_explicit_hub_consent_before_source_asr() -> None:
    service = SemanticSpeechSourceCorrectionService()

    with pytest.raises(SemanticSpeechSourceCorrectionError) as raised:
        service.command(_raw(), source_audio=_audio(), requested_at_ms=NOW_MS, consent_granted=False)

    assert raised.value.reason_code == "source_correction_consent_required"
    assert raised.value.status_code == 403


def test_voice_capabilities_advertise_source_correction_only_when_runtime_is_enabled(
    client, admin_auth_header
) -> None:
    provider = MagicMock()
    provider.health.return_value = {"ok": True, "status": "ok"}
    provider.models.return_value = []
    provider.capability_catalog.return_value = []
    flags = client.application.extensions.get("semantic_media_feature_flags")
    try:
        with patch("agent.routes.voice.get_voice_provider_service", return_value=provider):
            client.application.extensions["semantic_media_feature_flags"] = {
                "semantic_speech_runtime": True
            }
            enabled = client.get("/v1/voice/capabilities", headers=admin_auth_header)
            client.application.extensions["semantic_media_feature_flags"] = {
                "semantic_speech_runtime": False
            }
            disabled = client.get("/v1/voice/capabilities", headers=admin_auth_header)
    finally:
        client.application.extensions["semantic_media_feature_flags"] = flags

    assert "semantic_source_correction" in enabled.get_json()["data"]["capabilities"]
    assert "semantic_source_correction" not in disabled.get_json()["data"]["capabilities"]


def test_hub_route_delegates_source_asr_then_returns_canonical_correction(
    client, admin_auth_header
) -> None:
    audio = _audio()
    now_ms = time.time_ns() // 1_000_000
    raw = _raw(source_expires_at_ms=now_ms + 25_000, deadline_at_ms=now_ms + 20_000)
    consent = SimpleNamespace(
        consent_version=4,
        revocation_epoch=1,
        consent_digest="b" * 64,
        owner_subject="admin",
        speaker_id="admin",
        recipient_id="peer-b",
        direction="sender_to_receiver",
        purpose="live_correction",
        allows=MagicMock(),
    )
    share_service = MagicMock()
    share_service.get_session.return_value = {
        "id": "session-a",
        "owner_user_id": "admin",
        "tenant_id": "admin",
        "security_epoch": 2,
        "security_mode": "strict_e2ee",
        "revoked_at": None,
        "expires_at": None,
    }
    consent_service = MagicMock()
    consent_service.get.return_value = consent

    client.application.extensions["semantic_media_feature_flags"] = {"semantic_speech_runtime": True}
    with (
        patch("agent.routes.voice.get_share_session_service", return_value=share_service),
        patch("agent.routes.voice.get_speech_evidence_consent_service", return_value=consent_service),
        patch("agent.routes.voice.get_voice_provider_service") as provider_factory,
        patch("agent.routes.voice.log_audit") as audit,
    ):
        provider_factory.return_value.transcribe.return_value = _source_result()
        response = client.post(
            "/v1/voice/source-corrections",
            headers={**admin_auth_header, "Idempotency-Key": "semantic-source-route"},
            data={
                **{key: str(value) for key, value in raw.items() if key != "final_text"},
                "final_text": raw["final_text"],
                "deadline_seconds": "20",
                "file": (BytesIO(audio), "turn-a.webm"),
            },
            content_type="multipart/form-data",
        )
        replay = client.post(
            "/v1/voice/source-corrections",
            headers={**admin_auth_header, "Idempotency-Key": "semantic-source-route"},
            data={
                **{key: str(value) for key, value in raw.items() if key != "final_text"},
                "final_text": raw["final_text"],
                "deadline_seconds": "20",
                "file": (BytesIO(audio), "turn-a.webm"),
            },
            content_type="multipart/form-data",
        )

    assert response.status_code == 200
    data = response.get_json()["data"]
    assert data["authority"] == "corrected"
    assert data["text"] == "Wir testen neue klare Worte"
    assert str(data["task_id"]).startswith("voice-transcription-")
    assert provider_factory.return_value.transcribe.call_count == 1
    assert replay.status_code == 200
    assert replay.get_json()["data"]["idempotent_replay"] is True
    assert replay.get_json()["data"]["task_id"] == data["task_id"]
    assert consent.allows.call_count >= 8
    assert {call.args[0] for call in consent.allows.call_args_list} == {
        "capture",
        "raw_audio_share",
        "transcript_share",
    }
    assert consent_service.get.call_count > 2
    assert audit.call_args.args[1]["raw_audio_stored"] is False
    assert "Wir testen" not in str(audit.call_args.args[1])


def test_hub_route_rejects_missing_raw_audio_grant_before_delegation(
    client, admin_auth_header
) -> None:
    now_ms = time.time_ns() // 1_000_000
    raw = _raw(source_expires_at_ms=now_ms + 25_000, deadline_at_ms=now_ms + 20_000)
    consent = SimpleNamespace(
        consent_version=4,
        revocation_epoch=1,
        consent_digest="b" * 64,
        owner_subject="admin",
        speaker_id="admin",
        recipient_id="peer-b",
        direction="sender_to_receiver",
        purpose="live_correction",
        allows=MagicMock(
            side_effect=[
                None,
                SpeechEvidenceGovernanceError(
                    "speech_consent_grant_missing", "raw audio is not granted", status_code=403
                ),
            ]
        ),
    )
    share_service = MagicMock()
    share_service.get_session.return_value = {
        "id": "session-a",
        "owner_user_id": "admin",
        "tenant_id": "admin",
        "security_epoch": 2,
        "security_mode": "strict_e2ee",
        "revoked_at": None,
        "expires_at": None,
    }
    consent_service = MagicMock()
    consent_service.get.return_value = consent
    client.application.extensions["semantic_media_feature_flags"] = {"semantic_speech_runtime": True}
    with (
        patch("agent.routes.voice.get_share_session_service", return_value=share_service),
        patch("agent.routes.voice.get_speech_evidence_consent_service", return_value=consent_service),
        patch("agent.routes.voice._execute_hub_voice_request") as delegated,
    ):
        response = client.post(
            "/v1/voice/source-corrections",
            headers={**admin_auth_header, "Idempotency-Key": "semantic-source-denied"},
            data={
                **{key: str(value) for key, value in raw.items() if key != "final_text"},
                "final_text": raw["final_text"],
                "deadline_seconds": "20",
                "file": (BytesIO(_audio()), "turn-a.webm"),
            },
            content_type="multipart/form-data",
        )

    assert response.status_code == 403
    assert response.get_json()["data"]["error"]["code"] == "speech_consent_grant_missing"
    delegated.assert_not_called()


def test_hub_route_completion_fence_rejects_consent_revoked_during_source_asr(
    client, admin_auth_header
) -> None:
    now_ms = time.time_ns() // 1_000_000
    raw = _raw(source_expires_at_ms=now_ms + 25_000, deadline_at_ms=now_ms + 20_000)
    active = SimpleNamespace(
        consent_version=4,
        revocation_epoch=1,
        consent_digest="b" * 64,
        owner_subject="admin",
        speaker_id="admin",
        recipient_id="peer-b",
        direction="sender_to_receiver",
        purpose="live_correction",
        allows=MagicMock(),
    )
    revoked = SimpleNamespace(
        **{
            **active.__dict__,
            "consent_version": 5,
            "revocation_epoch": 2,
            "allows": MagicMock(),
        }
    )
    share_service = MagicMock()
    share_service.get_session.return_value = {
        "id": "session-a",
        "owner_user_id": "admin",
        "tenant_id": "admin",
        "security_epoch": 2,
        "security_mode": "strict_e2ee",
        "revoked_at": None,
        "expires_at": None,
    }
    consent_service = MagicMock()
    consent_service.get.return_value = active
    provider = MagicMock()

    def transcribe_after_revocation(**_kwargs):
        consent_service.get.return_value = revoked
        return _source_result()

    provider.transcribe.side_effect = transcribe_after_revocation
    client.application.extensions["semantic_media_feature_flags"] = {"semantic_speech_runtime": True}
    with (
        patch("agent.routes.voice.get_share_session_service", return_value=share_service),
        patch("agent.routes.voice.get_speech_evidence_consent_service", return_value=consent_service),
        patch("agent.routes.voice.get_voice_provider_service", return_value=provider),
        patch("agent.routes.voice.log_audit") as audit,
    ):
        response = client.post(
            "/v1/voice/source-corrections",
            headers={**admin_auth_header, "Idempotency-Key": "semantic-source-revoked-midflight"},
            data={
                **{key: str(value) for key, value in raw.items() if key != "final_text"},
                "final_text": raw["final_text"],
                "deadline_seconds": "20",
                "file": (BytesIO(_audio()), "turn-a.webm"),
            },
            content_type="multipart/form-data",
        )

    assert response.status_code == 409
    assert response.get_json()["data"]["error"]["code"] == "speech_consent_stale_claim"
    provider.transcribe.assert_called_once()
    audit.assert_not_called()
