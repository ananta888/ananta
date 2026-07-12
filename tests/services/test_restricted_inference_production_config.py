from __future__ import annotations

import pytest

from agent.services.restricted_inference_config_service import RestrictedInferenceConfigService
from agent.services.restricted_model_inference_service import RestrictedModelInferenceService


def test_production_profile_reports_worker_mock_and_legacy_violations() -> None:
    service = RestrictedInferenceConfigService(
        global_config={
            "restricted_inference": {
                "production_profile": True,
                "execution_mode": "legacy_local",
                "legacy_local_enabled": True,
                "allow_mock_fallback": True,
            }
        }
    )

    codes = {item.reason_code for item in service.diagnostics()}

    assert "production_worker_required" in codes
    assert "legacy_local_forbidden" in codes
    assert "mock_fallback_forbidden" in codes
    assert "mock_model_forbidden" in codes


def test_service_rejects_invalid_production_profile_before_adapter_construction() -> None:
    config = RestrictedInferenceConfigService.from_config(
        {
            "restricted_inference": {
                "production_profile": True,
                "execution_mode": "legacy_local",
                "legacy_local_enabled": True,
                "allow_mock_fallback": True,
            }
        }
    )

    with pytest.raises(ValueError, match="production configuration"):
        RestrictedModelInferenceService(config=config)


def test_valid_worker_profile_is_redacted_and_does_not_enable_legacy_local() -> None:
    config = RestrictedInferenceConfigService.from_config(
        {
            "restricted_inference": {
                "production_profile": True,
                "execution_mode": "worker",
                "worker_url": "http://restricted-inference:8091/internal/v1/restricted-inference",
                "worker_allowed_endpoints": [
                    "http://restricted-inference:8091/internal/v1/restricted-inference"
                ],
                "legacy_local_enabled": False,
                "allow_mock_fallback": False,
                "default_engine": "huggingface-transformers",
                "default_model_id": "manifest-1",
                "allowed_engines": ["huggingface-transformers"],
            }
        }
    )

    payload = config.as_dict()

    assert payload["execution_mode"] == "worker"
    assert payload["legacy_local_enabled"] is False
    assert payload["allow_mock_fallback"] is False


def test_worker_config_requires_exact_internal_endpoint_allowlist() -> None:
    missing = RestrictedInferenceConfigService(
        global_config={
            "restricted_inference": {
                "execution_mode": "worker",
                "worker_url": "http://restricted-inference:8091/internal/v1/restricted-inference",
            }
        }
    )
    mismatched = RestrictedInferenceConfigService(
        global_config={
            "restricted_inference": {
                "execution_mode": "worker",
                "worker_url": "http://restricted-inference:8091/internal/v1/restricted-inference",
                "worker_allowed_endpoints": [
                    "http://other-worker:8091/internal/v1/restricted-inference"
                ],
            }
        }
    )

    assert "worker_endpoint_allowlist_missing" in {
        item.reason_code for item in missing.diagnostics()
    }
    assert "worker_url_not_allowlisted" in {
        item.reason_code for item in mismatched.diagnostics()
    }


def test_status_serialization_hides_absolute_paths_and_remote_urls() -> None:
    raw = {
        "restricted_inference": {
            "models": [
                {
                    "id": "manifest-1",
                    "engine": "huggingface-transformers",
                    "model": "https://models.example/private/revision?token=secret",
                    "local_path": "/srv/restricted-models/private/snapshot",
                }
            ]
        }
    }
    config = RestrictedInferenceConfigService.from_config(raw)

    payload = config.as_dict(redact_secrets=True)["models"][0]

    assert payload["model"] == "<remote-model-source>"
    assert payload["local_path"] == "<local-snapshot>"
    assert "secret" not in str(payload)
    diagnostics = RestrictedInferenceConfigService(global_config=raw).diagnostics()
    assert "/srv/restricted-models" not in str([item.as_dict() for item in diagnostics])
