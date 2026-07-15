from __future__ import annotations

import uuid
from io import BytesIO
from unittest.mock import Mock, patch

from agent.services.voice_generative_corrector_service import VoiceGenerativeCorrectorOutcome
from voice_runtime.context import VoiceRecognitionContext


def _result(text: str = "hallo welt") -> dict:
    return {
        "schema_version": "2.0",
        "provider": "voice-runtime",
        "model": "vosk-de",
        "text": text,
        "language": "de",
        "candidates": [
            {
                "candidate_id": "candidate-vosk-1",
                "backend": "vosk",
                "status": "succeeded",
                "text": text,
            }
        ],
        "selected_candidate_id": "candidate-vosk-1",
    }


def _corrected(result: dict, model_id: str) -> dict:
    return {
        **result,
        "original_text": result["text"],
        "text": "Hallo Welt.",
        "generative_corrector": {
            "original_text": result["text"],
            "corrected_text": "Hallo Welt.",
            "model_id": model_id,
            "model_revision": "sha256-fixture",
            "changed": True,
            "review_required": True,
            "edits": [],
        },
    }


def _configure(
    client,
    headers,
    profile_id: str,
    model_id: str,
    *,
    provider_id: str = "embedded",
    expected_version: int | None = None,
):
    body = {
        "scope": "profile",
        "scope_id": profile_id,
        "delta": {
            "correction_policy": "generative_rewrite",
            "generative_corrector_provider": provider_id,
            "generative_corrector_model": model_id,
            "generative_corrector_max_edit_ratio": 0.35,
            "feature_flags": {"generative_corrector": True},
        },
    }
    if expected_version is not None:
        body["expected_version"] = expected_version
    return client.put(
        "/v1/voice/configuration",
        headers={**headers, "Idempotency-Key": f"config-{profile_id}-{model_id}-{uuid.uuid4().hex}"},
        json=body,
    )


def test_batch_projects_hub_only_corrector_policy_away_from_runtime(
    client,
    admin_auth_header,
) -> None:
    profile_id = f"corrector-batch-{uuid.uuid4().hex}"
    assert _configure(client, admin_auth_header, profile_id, "gemma-2b-it").status_code == 200
    provider = Mock()
    provider.transcribe.return_value = _result()
    corrector = Mock()
    corrector.apply.return_value = VoiceGenerativeCorrectorOutcome(
        result=_corrected(_result(), "gemma-2b-it"),
        applied=True,
        reason_code="generative_corrector_corrected",
    )

    with (
        patch("agent.routes.voice.get_voice_provider_service", return_value=provider),
        patch("agent.routes.voice.get_voice_generative_corrector_service", return_value=corrector),
    ):
        response = client.post(
            "/v1/voice/transcribe",
            headers=admin_auth_header,
            data={
                "file": (BytesIO(b"audio"), "sample.webm"),
                "profile_id": profile_id,
                "language": "de",
            },
            content_type="multipart/form-data",
        )

    assert response.status_code == 200
    assert response.get_json()["data"]["text"] == "Hallo Welt."
    runtime_context = provider.transcribe.call_args.kwargs["recognition_context"]
    parsed_runtime_context = VoiceRecognitionContext.from_mapping(runtime_context)
    assert parsed_runtime_context.configuration is not None
    assert parsed_runtime_context.configuration.correction_policy == "deterministic"
    assert "generative_corrector" not in parsed_runtime_context.configuration.feature_flags
    hub_configuration = corrector.apply.call_args.kwargs["effective_configuration"]
    assert hub_configuration["correction_policy"] == "generative_rewrite"
    assert hub_configuration["generative_corrector_model"] == "gemma-2b-it"


def test_stream_final_uses_creation_snapshot_when_profile_changes_mid_stream(
    client,
    admin_auth_header,
) -> None:
    profile_id = f"corrector-stream-{uuid.uuid4().hex}"
    assert _configure(client, admin_auth_header, profile_id, "gemma-2b-it").status_code == 200
    provider = Mock()
    provider.create_stream.side_effect = lambda **kwargs: {
        "session_id": kwargs["requested_session_id"],
        "state": "created",
        "max_audio_seconds": kwargs["max_audio_seconds"],
    }
    provider.finalize_stream.return_value = {
        "event": {"event_type": "final", "payload": {"result": _result()}}
    }
    corrector = Mock()
    corrector.apply.return_value = VoiceGenerativeCorrectorOutcome(
        result=_corrected(_result(), "gemma-2b-it"),
        applied=True,
        reason_code="generative_corrector_corrected",
    )

    with (
        patch("agent.routes.voice.get_voice_provider_service", return_value=provider),
        patch("agent.routes.voice.get_voice_generative_corrector_service", return_value=corrector),
    ):
        created = client.post(
            "/v1/voice/streams",
            headers={**admin_auth_header, "Idempotency-Key": f"stream-{uuid.uuid4().hex}"},
            json={
                "filename": "stream.pcm",
                "media_type": "audio/pcm;rate=16000;channels=1",
                "profile_id": profile_id,
                "language": "de",
            },
        )
        assert created.status_code == 201
        assert _configure(
            client,
            admin_auth_header,
            profile_id,
            "phi-3-mini-instruct",
            expected_version=1,
        ).status_code == 200
        session_id = created.get_json()["data"]["stream"]["session_id"]
        final = client.post(
            f"/v1/voice/streams/{session_id}/finalize",
            headers=admin_auth_header,
        )

    assert final.status_code == 200
    assert final.get_json()["data"]["result"]["text"] == "Hallo Welt."
    configuration = corrector.apply.call_args.kwargs["effective_configuration"]
    assert configuration["generative_corrector_model"] == "gemma-2b-it"
    assert corrector.apply.call_args.kwargs["language"] == "de"
    runtime_context = provider.create_stream.call_args.kwargs["recognition_context"]
    VoiceRecognitionContext.from_mapping(runtime_context)


def test_capability_is_not_advertised_when_configured_model_is_not_worker_ready(
    client,
    admin_auth_header,
) -> None:
    provider = Mock()
    provider.health.return_value = {"ok": True, "status": "ready"}
    provider.models.return_value = []
    provider.capability_catalog.return_value = []
    missing_model = {
        "id": "gemma-2b-it",
        "role": "generative_corrector",
        "available": False,
        "status": "model_missing",
        "reason_code": "generative_corrector_model_missing",
    }
    with (
        patch("agent.routes.voice.get_voice_provider_service", return_value=provider),
        patch(
            "agent.routes.voice.generative_corrector_capability_bundle",
            return_value={
                "correction_models": [missing_model],
                "correction_providers": [],
                "correction_default": {
                    "provider": "lmstudio",
                    "model": "auto",
                    "source": "settings.default",
                    "available": False,
                },
            },
        ),
    ):
        unavailable = client.get("/v1/voice/capabilities", headers=admin_auth_header)

    assert unavailable.status_code == 200
    data = unavailable.get_json()["data"]
    assert data["correction_models"] == [missing_model]
    assert "generative_transcript_correction" not in data["capabilities"]


def test_batch_resolves_inherit_from_general_llm_configuration_at_the_hub_boundary(
    client,
    admin_auth_header,
    app,
) -> None:
    profile_id = f"corrector-inherit-{uuid.uuid4().hex}"
    app.config["AGENT_CONFIG"] = {
        **dict(app.config.get("AGENT_CONFIG") or {}),
        "llm_config": {"provider": "ollama", "model": "qwen2.5:7b"},
    }
    assert _configure(
        client,
        admin_auth_header,
        profile_id,
        "",
        provider_id="inherit",
    ).status_code == 200
    provider = Mock()
    provider.transcribe.return_value = _result()
    corrector = Mock()
    corrector.apply.return_value = VoiceGenerativeCorrectorOutcome(
        result=_corrected(_result(), "ollama:qwen2.5:7b"),
        applied=True,
        reason_code="generative_corrector_corrected",
    )

    with (
        patch("agent.routes.voice.get_voice_provider_service", return_value=provider),
        patch("agent.routes.voice.get_voice_generative_corrector_service", return_value=corrector),
    ):
        response = client.post(
            "/v1/voice/transcribe",
            headers=admin_auth_header,
            data={
                "file": (BytesIO(b"audio"), "sample.webm"),
                "profile_id": profile_id,
                "language": "de",
            },
            content_type="multipart/form-data",
        )

    assert response.status_code == 200
    effective = corrector.apply.call_args.kwargs["effective_configuration"]
    assert effective["generative_corrector_provider"] == "ollama"
    assert effective["generative_corrector_model"] == "qwen2.5:7b"
    assert effective["generative_corrector_inherited_source"] == "agent_config.llm_config"
    runtime_context = provider.transcribe.call_args.kwargs["recognition_context"]
    assert "generative_corrector_provider" not in runtime_context["configuration"]
