from __future__ import annotations

import threading
from unittest.mock import patch

import pytest

from agent.services.voice_configuration_service import VoiceConfigurationService
from agent.services.voice_governance_domain import VoicePrincipal


def test_voice_configuration_schema_and_safe_defaults(client, user_auth_header):
    schema = client.get("/v1/voice/configuration/schema", headers=user_auth_header)
    effective = client.get("/v1/voice/configuration", headers=user_auth_header)

    assert schema.status_code == 200
    schema_payload = schema.get_json()["data"]["schema"]
    assert schema_payload["schema_version"] == "ananta.voice-configuration.v1"
    assert schema_payload["properties"]["primary_backend"]["scopes"] == ["global", "profile", "session"]
    assert schema_payload["properties"]["primary_backend"]["capability_reason_source"].endswith(
        "/model_catalog"
    )
    assert schema_payload["administrative_fields"]["session_override_allowed"] is False
    assert schema_payload["properties"]["enhancement_variants"]["items"]["enum"] == [
        "original",
        "bypass",
        "normalized",
        "high_pass",
        "speech_safe",
    ]
    assert schema_payload["properties"]["diarization_backend"]["enum"] == ["none", "pyannote"]
    assert "generative_rewrite" in schema_payload["properties"]["correction_policy"]["enum"]
    assert schema_payload["properties"]["generative_corrector_model"]["default"] == "gemma-2b-it"
    assert schema_payload["properties"]["generative_corrector_max_edit_ratio"]["maximum"] == 1
    configuration = effective.get_json()["data"]["configuration"]
    assert configuration["effective"]["recognition_strategy"] == "single"
    assert not any(configuration["effective"]["feature_flags"].values())


def test_generative_rewrite_requires_its_flag_and_forces_review(client, user_auth_header):
    disabled = client.put(
        "/v1/voice/configuration",
        headers={**user_auth_header, "Idempotency-Key": "corrector-disabled-config"},
        json={
            "scope": "profile",
            "scope_id": "corrector-disabled",
            "delta": {
                "correction_policy": "generative_rewrite",
                "generative_corrector_model": "gemma-2b-it",
            },
        },
    )
    enabled = client.put(
        "/v1/voice/configuration",
        headers={**user_auth_header, "Idempotency-Key": "corrector-enabled-config"},
        json={
            "scope": "profile",
            "scope_id": "corrector-enabled",
            "delta": {
                "correction_policy": "generative_rewrite",
                "generative_corrector_model": "phi-3-mini-instruct",
                "feature_flags": {"generative_corrector": True},
            },
        },
    )

    assert disabled.status_code == 200
    assert enabled.status_code == 200
    disabled_effective = client.get(
        "/v1/voice/configuration?profile_id=corrector-disabled",
        headers=user_auth_header,
    ).get_json()["data"]["configuration"]
    enabled_effective = client.get(
        "/v1/voice/configuration?profile_id=corrector-enabled",
        headers=user_auth_header,
    ).get_json()["data"]["configuration"]
    assert disabled_effective["effective"]["correction_policy"] == "deterministic"
    assert enabled_effective["effective"]["correction_policy"] == "generative_rewrite"
    assert enabled_effective["effective"]["review_policy"] == "always"


def test_profile_and_session_deltas_follow_precedence(client, user_auth_header):
    profile = client.put(
        "/v1/voice/configuration",
        headers={**user_auth_header, "Idempotency-Key": "profile-config-1"},
        json={
            "scope": "profile",
            "scope_id": "profile-a",
            "delta": {"recognition_strategy": "parallel_compare", "feature_flags": {"voice_fusion": True}},
        },
    )
    session = client.put(
        "/v1/voice/configuration",
        headers={**user_auth_header, "Idempotency-Key": "session-config-1"},
        json={
            "scope": "session",
            "scope_id": "session-a",
            "delta": {"recognition_strategy": "classic_then_correct"},
        },
    )
    effective = client.get(
        "/v1/voice/configuration?profile_id=profile-a&session_id=session-a",
        headers=user_auth_header,
    )

    assert profile.status_code == 200
    assert session.status_code == 200
    payload = effective.get_json()["data"]["configuration"]
    assert payload["effective"]["recognition_strategy"] == "classic_then_correct"
    assert payload["effective"]["feature_flags"]["voice_fusion"] is True
    assert [source["scope"] for source in payload["sources"]][-2:] == ["profile", "session"]


def test_voice_configuration_is_idempotent_and_validated(client, user_auth_header):
    headers = {**user_auth_header, "Idempotency-Key": "voice-config-replay"}
    body = {"scope": "profile", "scope_id": "profile-b", "delta": {"confidence_threshold": 0.8}}

    first = client.put("/v1/voice/configuration", headers=headers, json=body)
    replay = client.put("/v1/voice/configuration", headers=headers, json=body)
    invalid = client.put(
        "/v1/voice/configuration",
        headers={**user_auth_header, "Idempotency-Key": "voice-config-invalid"},
        json={"scope": "profile", "scope_id": "profile-b", "delta": {"unknown": True}},
    )

    assert first.get_json()["data"]["configuration"]["version"] == 1
    assert replay.get_json()["data"]["configuration"]["idempotent_replay"] is True
    assert invalid.status_code == 422


def test_configuration_recovers_as_replay_after_post_commit_crash(app) -> None:
    service = VoiceConfigurationService()
    principal = VoicePrincipal(tenant_id="config-atomic-tenant", subject="config-owner")
    original_put = service._repository.put

    def commit_then_crash(*args, **kwargs):
        original_put(*args, **kwargs)
        raise RuntimeError("simulated process crash after atomic commit")

    request = {
        "scope": "profile",
        "scope_id": "config-atomic-profile",
        "delta": {"confidence_threshold": 0.83},
        "expected_version": 0,
        "idempotency_key": "config-atomic-key",
    }
    with app.app_context(), patch.object(service._repository, "put", side_effect=commit_then_crash):
        with pytest.raises(RuntimeError, match="simulated process crash"):
            service.put_delta(principal, **request)

    with app.app_context():
        recovered = service.put_delta(principal, **request)
        record = service._repository.get(
            principal,
            scope="profile",
            scope_id="config-atomic-profile",
        )

    assert recovered["idempotent_replay"] is True
    assert recovered["version"] == 1
    assert record is not None
    assert record.version == 1
    assert record.delta == {"confidence_threshold": 0.83}


def test_configuration_rolls_back_when_idempotency_fence_is_lost(app) -> None:
    service = VoiceConfigurationService()
    principal = VoicePrincipal(tenant_id="config-fence-tenant", subject="config-owner")
    request = {
        "scope": "profile",
        "scope_id": "config-fence-profile",
        "delta": {"confidence_threshold": 0.79},
        "expected_version": 0,
        "idempotency_key": "config-fence-key",
    }

    with app.app_context(), patch.object(
        service._repository,
        "_complete_idempotency_claim",
        side_effect=RuntimeError("simulated stale claim fence"),
    ):
        with pytest.raises(RuntimeError, match="simulated stale claim fence"):
            service.put_delta(principal, **request)

    with app.app_context():
        assert (
            service._repository.get(
                principal,
                scope="profile",
                scope_id="config-fence-profile",
            )
            is None
        )
        retried = service.put_delta(principal, **request)

    assert retried["idempotent_replay"] is False
    assert retried["version"] == 1


def test_concurrent_configuration_retries_share_one_atomic_mutation(app) -> None:
    service = VoiceConfigurationService()
    principal = VoicePrincipal(tenant_id="config-concurrent-tenant", subject="config-owner")
    barrier = threading.Barrier(2)
    results: list[dict] = []
    errors: list[BaseException] = []

    def mutate() -> None:
        try:
            with app.app_context():
                barrier.wait(timeout=2)
                results.append(
                    service.put_delta(
                        principal,
                        scope="profile",
                        scope_id="config-concurrent-profile",
                        delta={"confidence_threshold": 0.77},
                        expected_version=0,
                        idempotency_key="config-concurrent-key",
                    )
                )
        except BaseException as exc:
            errors.append(exc)

    workers = [threading.Thread(target=mutate) for _index in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=5)

    assert errors == []
    assert len(results) == 2
    assert sorted(result["idempotent_replay"] for result in results) == [False, True]
    assert {result["version"] for result in results} == {1}
    with app.app_context():
        record = service._repository.get(
            principal,
            scope="profile",
            scope_id="config-concurrent-profile",
        )
    assert record is not None
    assert record.version == 1


def test_delta_is_validated_against_inherited_effective_configuration(client, user_auth_header):
    invalid = client.put(
        "/v1/voice/configuration",
        headers={**user_auth_header, "Idempotency-Key": "voice-config-inherited-duplicate"},
        json={
            "scope": "profile",
            "scope_id": "profile-inherited-duplicate",
            "delta": {"secondary_backends": ["vosk"]},
        },
    )

    assert invalid.status_code == 422
    assert invalid.get_json()["data"]["error"]["code"] == "voice_configuration.duplicate_backend"


def test_global_delta_is_shared_with_subjects_in_the_same_tenant(app) -> None:
    service = VoiceConfigurationService()
    admin = VoicePrincipal(tenant_id="shared-config-tenant", subject="admin-a")
    user = VoicePrincipal(tenant_id="shared-config-tenant", subject="user-b")

    with app.app_context():
        service.put_delta(
            admin,
            scope="global",
            scope_id=None,
            delta={"confidence_threshold": 0.91},
            expected_version=None,
            idempotency_key="shared-global-config",
        )
        effective = service.resolve(user)

    assert effective.effective["confidence_threshold"] == 0.91
    assert effective.sources[-1]["scope"] == "global"


def test_disabled_feature_flags_apply_compatible_effective_fallback_without_losing_delta(
    client, user_auth_header
):
    response = client.put(
        "/v1/voice/configuration",
        headers={**user_auth_header, "Idempotency-Key": "voice-config-safe-fallback"},
        json={
            "scope": "profile",
            "scope_id": "profile-safe-fallback",
            "delta": {
                "recognition_strategy": "parallel_compare",
                "correction_policy": "restricted_choice",
            },
        },
    )
    assert response.status_code == 200

    effective = client.get(
        "/v1/voice/configuration?profile_id=profile-safe-fallback",
        headers=user_auth_header,
    ).get_json()["data"]["configuration"]

    assert effective["effective"]["recognition_strategy"] == "single"
    assert effective["effective"]["correction_policy"] == "deterministic"
    assert {item["reason_code"] for item in effective["adjustments"]} == {
        "voice_fusion_disabled",
        "restricted_worker_disabled",
    }
    profile_source = next(item for item in effective["sources"] if item["scope"] == "profile")
    assert profile_source["delta"]["recognition_strategy"] == "parallel_compare"
    assert profile_source["delta"]["correction_policy"] == "restricted_choice"


def test_legacy_pipeline_fallback_and_flag_aliases_project_to_canonical_configuration(app) -> None:
    service = VoiceConfigurationService()
    principal = VoicePrincipal(tenant_id="legacy-voice-tenant", subject="legacy-voice-user")

    with app.app_context():
        resolved = service.resolve(
            principal,
            legacy_global={
                "voice_runtime": {
                    "transcription_pipeline": "realtime_streaming",
                    "backend_fallback_order": "vosk,whisper_cpp,mock",
                    "voice_fusion_enabled": True,
                }
            },
        )

    assert resolved.effective["transport_mode"] == "streaming"
    assert resolved.effective["recognition_strategy"] == "single"
    assert resolved.effective["primary_backend"] == "vosk"
    assert resolved.effective["secondary_backends"] == ["whisper_cpp"]
    assert resolved.effective["feature_flags"]["voice_fusion"] is True
    assert resolved.sources[-1]["scope"] == "legacy_global"


def test_new_voice_fields_win_over_legacy_aliases_in_mixed_configuration(app) -> None:
    service = VoiceConfigurationService()
    principal = VoicePrincipal(tenant_id="mixed-voice-tenant", subject="mixed-voice-user")

    with app.app_context():
        resolved = service.resolve(
            principal,
            legacy_global={
                "voice_runtime": {
                    "transcription_pipeline": "realtime_streaming",
                    "backend_fallback_order": ["vosk", "whisper_cpp"],
                    "voice_fusion_enabled": True,
                    "transport_mode": "batch",
                    "primary_backend": "faster_whisper",
                    "secondary_backends": ["voxtral"],
                    "feature_flags": {"voice_fusion": False},
                }
            },
        )

    assert resolved.effective["transport_mode"] == "batch"
    assert resolved.effective["primary_backend"] == "faster_whisper"
    assert resolved.effective["secondary_backends"] == ["voxtral"]
    assert resolved.effective["feature_flags"]["voice_fusion"] is False


def test_feature_flag_rollback_preserves_and_restores_sparse_profile_delta(app) -> None:
    service = VoiceConfigurationService()
    principal = VoicePrincipal(tenant_id="rollback-voice-tenant", subject="rollback-voice-user")
    profile_id = "rollback-profile"

    with app.app_context():
        service.put_delta(
            principal,
            scope="profile",
            scope_id=profile_id,
            delta={"recognition_strategy": "parallel_compare"},
            expected_version=None,
            idempotency_key="rollback-profile-delta",
        )
        enabled = service.resolve(
            principal,
            profile_id=profile_id,
            legacy_global={"voice_runtime": {"voice_fusion_enabled": True}},
        )
        disabled = service.resolve(
            principal,
            profile_id=profile_id,
            legacy_global={"voice_runtime": {"voice_fusion_enabled": False}},
        )
        restored = service.resolve(
            principal,
            profile_id=profile_id,
            legacy_global={"voice_runtime": {"voice_fusion_enabled": True}},
        )

    assert enabled.effective["recognition_strategy"] == "parallel_compare"
    assert disabled.effective["recognition_strategy"] == "single"
    assert disabled.adjustments[0]["reason_code"] == "voice_fusion_disabled"
    assert restored.effective["recognition_strategy"] == "parallel_compare"
    profile_source = next(item for item in restored.sources if item["scope"] == "profile")
    assert profile_source["delta"] == {"recognition_strategy": "parallel_compare"}


def test_enhancement_and_diarization_are_schema_driven_and_feature_bounded(client, user_auth_header):
    response = client.put(
        "/v1/voice/configuration",
        headers={**user_auth_header, "Idempotency-Key": "voice-config-optional-extensions"},
        json={
            "scope": "profile",
            "scope_id": "profile-optional",
            "delta": {
                "recognition_strategy": "parallel_compare",
                "enhancement_variants": ["original", "normalized"],
                "diarization_backend": "pyannote",
                "feature_flags": {
                    "voice_fusion": True,
                    "audio_enhancement": True,
                    "optional_models": True,
                },
            },
        },
    )

    assert response.status_code == 200
    effective = client.get(
        "/v1/voice/configuration?profile_id=profile-optional",
        headers=user_auth_header,
    ).get_json()["data"]["configuration"]
    assert effective["effective"]["enhancement_variants"] == ["original", "normalized"]
    assert effective["effective"]["diarization_backend"] == "pyannote"

    disabled = client.put(
        "/v1/voice/configuration",
        headers={**user_auth_header, "Idempotency-Key": "voice-config-optional-disabled"},
        json={
            "scope": "profile",
            "scope_id": "profile-disabled",
            "delta": {
                "enhancement_variants": ["original", "normalized"],
                "diarization_backend": "pyannote",
            },
        },
    )
    assert disabled.status_code == 200
    disabled_effective = client.get(
        "/v1/voice/configuration?profile_id=profile-disabled",
        headers=user_auth_header,
    ).get_json()["data"]["configuration"]
    assert disabled_effective["effective"]["enhancement_variants"] == ["original"]
    assert disabled_effective["effective"]["diarization_backend"] == "none"
    assert {item["reason_code"] for item in disabled_effective["adjustments"]} == {
        "audio_enhancement_disabled",
        "optional_models_disabled",
    }
