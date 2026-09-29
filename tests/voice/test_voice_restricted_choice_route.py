from __future__ import annotations

from io import BytesIO
from typing import Any, Mapping
from unittest.mock import Mock, patch

from agent.services.restricted_inference_contract import (
    CONTRACT_VERSION,
    RestrictedInferenceRequest,
)
from agent.services.restricted_inference_port import ContractRestrictedInferencePort
from agent.services.voice_generative_judge_service import VoiceGenerativeJudgeOutcome
from agent.services.voice_restricted_choice_service import VoiceRestrictedChoiceService


class _ChoiceTransport:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.request: RestrictedInferenceRequest | None = None

    def dispatch(self, envelope: Mapping[str, Any]) -> Mapping[str, Any]:
        if self.fail:
            raise RuntimeError("restricted worker unavailable")
        request = RestrictedInferenceRequest.from_dict(envelope)
        self.request = request
        return {
            "contract_version": CONTRACT_VERSION,
            "request_id": request.request_id,
            "task_id": request.task_id,
            "operation": "score_choices",
            "status": "succeeded",
            "result": {
                "items": [
                    {"choice": choice, "score": 1.0 if choice == "candidate-b" else 0.0}
                    for choice in request.payload["choices"]
                ],
                "engine": "huggingface-transformers",
                "model_id": "fixture/voice-choice",
                "manifest_digest": "d" * 64,
                "latency_ms": 1.0,
            },
            "error": None,
            "no_generation": True,
        }


def _base_result() -> dict[str, Any]:
    return {
        "schema_version": "2.0",
        "provider": "voice-runtime",
        "model": "fixture",
        "text": "deterministic baseline",
        "selected_candidate_id": "candidate-a",
        "warnings": [],
        "decision_trace": {"runtime": "voice"},
        "candidates": [
            {
                "candidate_id": "candidate-a",
                "text": "deterministic baseline",
                "status": "succeeded",
            },
            {
                "candidate_id": "candidate-b",
                "text": "known alternate",
                "status": "succeeded",
            },
        ],
    }


def _recognition_context() -> dict[str, Any]:
    return {
        "schema_version": "ananta.voice-recognition-context.v1",
        "configuration": {
            "correction_policy": "restricted_choice",
            "feature_flags": {"restricted_worker": True},
        },
    }


def _service(transport: _ChoiceTransport) -> VoiceRestrictedChoiceService:
    return VoiceRestrictedChoiceService(
        inference_port=ContractRestrictedInferencePort(transport),
        manifest_resolver=lambda: "voice-choice-manifest-v1",
    )


def _post_transcription(client, headers: Mapping[str, str]):
    return client.post(
        "/v1/voice/transcribe",
        headers=dict(headers),
        data={"file": (BytesIO(b"audio"), "sample.webm")},
        content_type="multipart/form-data",
    )


def test_transcribe_route_applies_hub_choice_to_a_known_candidate(client, admin_auth_header) -> None:
    base = _base_result()
    transport = _ChoiceTransport()
    provider = Mock()
    provider.transcribe.return_value = base
    artifacts = Mock()
    artifacts.create.return_value = {"id": "voice-result-1", "payload_digest": "a" * 64}

    with (
        patch("agent.routes.voice.get_voice_provider_service", return_value=provider),
        patch("agent.routes.voice._recognition_context", return_value=_recognition_context()),
        patch("agent.routes.voice.get_voice_restricted_choice_service", return_value=_service(transport)),
        patch("agent.routes.voice.get_voice_result_artifact_service", return_value=artifacts),
    ):
        response = _post_transcription(client, admin_auth_header)

    assert response.status_code == 200
    result = response.get_json()["data"]
    assert result["text"] == "known alternate"
    assert result["selected_candidate_id"] == "candidate-b"
    assert result["decision_trace"]["restricted_choice"]["no_generation"] is True
    assert transport.request is not None
    assert list(transport.request.payload["choices"]) == ["candidate-a", "candidate-b"]
    persisted = artifacts.create.call_args.kwargs["result"]
    assert persisted["text"] == "known alternate"
    assert base["text"] == "deterministic baseline"


def test_transcribe_route_worker_failure_persists_exact_baseline_object(client, admin_auth_header) -> None:
    base = _base_result()
    provider = Mock()
    provider.transcribe.return_value = base
    artifacts = Mock()
    artifacts.create.return_value = {"id": "voice-result-2", "payload_digest": "b" * 64}

    with (
        patch("agent.routes.voice.get_voice_provider_service", return_value=provider),
        patch("agent.routes.voice._recognition_context", return_value=_recognition_context()),
        patch(
            "agent.routes.voice.get_voice_restricted_choice_service",
            return_value=_service(_ChoiceTransport(fail=True)),
        ),
        patch("agent.routes.voice.get_voice_result_artifact_service", return_value=artifacts),
    ):
        response = _post_transcription(client, admin_auth_header)

    assert response.status_code == 200
    assert response.get_json()["data"]["text"] == "deterministic baseline"
    assert artifacts.create.call_args.kwargs["result"] is base
    assert provider.transcribe.call_count == 1


def test_transcribe_route_delegates_generative_policy_only_from_the_hub(client, admin_auth_header) -> None:
    base = _base_result()
    corrected = {**base, "text": "known alternate"}
    provider = Mock()
    provider.transcribe.return_value = base
    artifacts = Mock()
    artifacts.create.return_value = {"id": "voice-result-3", "payload_digest": "c" * 64}
    judge = Mock()
    judge.apply.return_value = VoiceGenerativeJudgeOutcome(
        result=corrected,
        applied=True,
        reason_code="generative_judge_selected",
    )
    context = {
        "schema_version": "ananta.voice-recognition-context.v1",
        "configuration": {
            "correction_policy": "generative_local",
            "feature_flags": {"generative_judge": True},
        },
    }

    with (
        patch("agent.routes.voice.get_voice_provider_service", return_value=provider),
        patch("agent.routes.voice._recognition_context", return_value=context),
        patch("agent.routes.voice.get_voice_generative_judge_service", return_value=judge),
        patch("agent.routes.voice.get_voice_result_artifact_service", return_value=artifacts),
    ):
        response = _post_transcription(client, admin_auth_header)

    assert response.status_code == 200
    assert response.get_json()["data"]["text"] == "known alternate"
    call = judge.apply.call_args
    assert call.args == (base,)
    assert call.kwargs["effective_configuration"] == context["configuration"]
    assert call.kwargs["parent_task_id"].startswith("voice-transcription-")
    assert call.kwargs["tenant_id"]
    assert call.kwargs["request_id"].startswith("audit-voice-")
    assert artifacts.create.call_args.kwargs["result"] == corrected
