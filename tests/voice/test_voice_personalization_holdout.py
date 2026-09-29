from __future__ import annotations

import io
import wave

from sqlmodel import Session, select

from agent.database import engine
from agent.db_models import TaskDB
from agent.services.voice_governance_domain import VoicePrincipal
from agent.services.voice_result_artifact_service import get_voice_result_artifact_service
from voice_runtime.backends.base import ChatResult, TranscriptionResult, TranscriptionSegment
from voice_runtime.config import VoiceRuntimeConfig
from voice_runtime.context import VoiceRecognitionContext
from voice_runtime.pipeline import TranscriptionPipeline


class _HoldoutBackend:
    def name(self) -> str:
        return "mock"

    def transcribe(self, **_kwargs) -> TranscriptionResult:
        return TranscriptionResult(
            text="Anantha baut",
            duration_ms=100,
            raw_backend="mock",
            segments=(TranscriptionSegment(0, 100, "Anantha baut", backend="mock"),),
        )

    def audio_chat(self, **_kwargs) -> ChatResult:
        return ChatResult(text="unused")

    def list_models(self) -> list[dict]:
        return []

    def context_capabilities(self) -> frozenset[str]:
        return frozenset()


def _wav() -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16_000)
        handle.writeframes(b"\x00\x00" * 1_600)
    return output.getvalue()


def _headers(auth: dict[str, str], key: str) -> dict[str, str]:
    return {**auth, "Idempotency-Key": key}


def _consent(client, auth, profile_id: str) -> None:
    response = client.put(
        f"/v1/voice/consents/{profile_id}",
        headers=_headers(auth, f"holdout-consent-{profile_id}"),
        json={
            "granted": True,
            "categories": ["preferences", "text_corrections", "vocabulary"],
            "retention_days": 30,
        },
    )
    assert response.status_code == 200


def _confirmed_review(client, auth, profile_id: str, suffix: str) -> str:
    candidate_id = f"holdout-candidate-{suffix}"
    with client.application.app_context():
        artifact = get_voice_result_artifact_service().create(
            VoicePrincipal(tenant_id="testuser", subject="testuser"),
            request_hash=f"holdout-request-{suffix}",
            profile_id=profile_id,
            result={
                "text": "Ananta",
                "selected_candidate_id": candidate_id,
                "candidates": [
                    {
                        "candidate_id": candidate_id,
                        "text": "Ananta",
                        "status": "succeeded",
                    }
                ],
            },
        )
    created = client.post(
        "/v1/voice/reviews",
        headers=_headers(auth, f"holdout-review-{suffix}"),
        json={
            "profile_id": profile_id,
            "result_ref": artifact["id"],
            "candidate_ids": [candidate_id],
        },
    )
    review = created.get_json()["data"]["review"]
    decided = client.post(
        f"/v1/voice/reviews/{review['id']}/decision",
        headers=_headers(auth, f"holdout-decision-{suffix}"),
        json={
            "decision": "accept",
            "expected_version": review["version"],
            "selected_candidate_id": candidate_id,
        },
    )
    assert decided.status_code == 200
    return review["id"]


def _feedback(client, auth, *, profile_id: str, review_id: str, kind: str, suffix: str) -> None:
    response = client.post(
        "/v1/voice/personalization/feedback",
        headers=_headers(auth, f"holdout-feedback-{suffix}"),
        json={
            "profile_id": profile_id,
            "review_id": review_id,
            "kind": kind,
            "source_text": "Anantha",
            "target_text": "Ananta" if kind != "negative" else None,
            "metadata": {"language": "de"},
        },
    )
    assert response.status_code == 201


def _snapshot(client, auth, profile_id: str) -> dict:
    response = client.get(f"/v1/voice/personalization/{profile_id}/snapshot", headers=auth)
    assert response.status_code == 200
    return response.get_json()["data"]["snapshot"]


def _recognize(snapshot: dict) -> str:
    context = VoiceRecognitionContext.from_mapping({"personalization": snapshot})
    pipeline = TranscriptionPipeline(
        config=VoiceRuntimeConfig(
            backend_fallback_order=("mock",),
            postprocess_backend="rules",
        ),
        backend=_HoldoutBackend(),
    )
    return pipeline.transcribe(filename="holdout.wav", content=_wav(), context=context).text


def test_confirmed_profile_feedback_improves_only_its_holdout_and_negative_retracts_rule(
    client,
    user_auth_header,
) -> None:
    profile_a = "holdout-profile-a"
    profile_b = "holdout-profile-b"
    _consent(client, user_auth_header, profile_a)
    _consent(client, user_auth_header, profile_b)
    positive_review = _confirmed_review(client, user_auth_header, profile_a, "positive")
    _feedback(
        client,
        user_auth_header,
        profile_id=profile_a,
        review_id=positive_review,
        kind="substitution",
        suffix="positive",
    )

    assert _recognize(_snapshot(client, user_auth_header, profile_a)) == "Ananta baut."
    assert _recognize(_snapshot(client, user_auth_header, profile_b)) == "Anantha baut."

    negative_review = _confirmed_review(client, user_auth_header, profile_a, "negative")
    _feedback(
        client,
        user_auth_header,
        profile_id=profile_a,
        review_id=negative_review,
        kind="negative",
        suffix="negative",
    )

    assert _recognize(_snapshot(client, user_auth_header, profile_a)) == "Anantha baut."
    assert _recognize(_snapshot(client, user_auth_header, profile_b)) == "Anantha baut."
    with Session(engine) as session:
        automatic_training = session.exec(
            select(TaskDB).where(TaskDB.task_kind == "voice_training_export")
        ).all()
    assert automatic_training == []
