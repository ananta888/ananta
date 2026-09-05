from __future__ import annotations

import hashlib

import pytest

from agent.services.integration_registry_service import IntegrationRegistryService
from agent.services.model_invocation_errors import ModelRoutingConfigurationError
from agent.services.model_invocation_service import ModelInvocationService
from agent.services.model_profile_loader import ModelProfile
from agent.services.runtime_handoff_invocation_service import (
    RuntimeHandoffInvocationService,
)
from agent.services.unsloth_runtime_endpoint_registry_service import (
    RuntimeEndpointRegistryError,
    SqliteRuntimeEndpointRegistry,
)

HASH_A = "a" * 64
HASH_B = "b" * 64


def _descriptor(endpoint_id: str = "endpoint-a"):
    return IntegrationRegistryService().normalize_runtime_endpoint_descriptor(
        provider_descriptor={
            "provider_id": "local-provider",
            "provider_type": "local-openai-compatible",
            "model_id": "model-a",
            "provider_revision": "revision-a",
            "capabilities": {
                "openai_chat": True,
                "openai_responses": False,
                "anthropic_messages": False,
                "streaming": True,
                "tools": True,
                "structured_output": True,
            },
            "limits": {
                "timeout_seconds": 60,
                "context_tokens": 8192,
                "max_output_tokens": 2048,
                "stream_idle_timeout_seconds": 30,
            },
        },
        endpoint_descriptor={
            "endpoint_id": endpoint_id,
            "display_name": "Local promoted adapter",
            "routing_key": "promoted-adapter",
        },
    )


def _manifest(endpoint_id: str, expected_revision: int, artifact_sha: str):
    descriptor = _descriptor(endpoint_id)
    return {
        "schema_version": 2,
        "tenant_id": "tenant-a",
        "endpoint_id": endpoint_id,
        "provider": "local-provider",
        "artifact": {
            "artifact_id": "lora-export-artifact-a",
            "artifact_sha256": artifact_sha,
            "format": "adapter",
            "promotion_id": "promotion-a",
            "adapter_id": "adapter-a",
            "adapter_sha256": HASH_A,
            "base_model_id": "model-a",
            "base_model_sha256": HASH_B,
        },
        "provider_descriptor": descriptor["provider"],
        "endpoint_descriptor": descriptor["endpoint"],
        "api_capabilities": descriptor["api_capabilities"],
        "limits": descriptor["limits"],
        "source_ids": ["SRC_supplied-model"],
        "run_ids": ["RUN_supplied-evaluation"],
        "job_id": "job-a",
        "attempt_id": "attempt-a",
        "fencing_token_digest": HASH_A,
        "reason_sha256": HASH_B,
        "expected_endpoint_revision": expected_revision,
        "fallback": None,
    }


def test_endpoint_handoff_is_revision_fenced_and_capability_gated(tmp_path) -> None:
    registry = SqliteRuntimeEndpointRegistry(tmp_path / "endpoints.sqlite3")
    first = registry.apply_handoff(
        tenant_id="tenant-a",
        endpoint_id="endpoint-a",
        expected_revision=0,
        task_id="task-a",
        idempotency_key="idempotency-a",
        manifest=_manifest("endpoint-a", 0, HASH_A),
    )

    resolved = ModelInvocationService.resolve_runtime_handoff_endpoint(
        tenant_id="tenant-a",
        endpoint_id="endpoint-a",
        required_capability="openai_chat",
        expected_endpoint_revision=1,
        endpoint_registry=registry,
    )

    assert first.revision == 1
    assert resolved["fallback"] is None
    with pytest.raises(RuntimeEndpointRegistryError) as blocked:
        ModelInvocationService.resolve_runtime_handoff_endpoint(
            tenant_id="tenant-a",
            endpoint_id="endpoint-a",
            required_capability="anthropic_messages",
            endpoint_registry=registry,
        )
    assert blocked.value.reason_code == "runtime_endpoint_capability_unavailable"


def test_resolved_runtime_endpoint_invokes_exact_profile_without_fallback(tmp_path) -> None:
    registry = SqliteRuntimeEndpointRegistry(tmp_path / "endpoints.sqlite3")
    registry.apply_handoff(
        tenant_id="tenant-a",
        endpoint_id="endpoint-a",
        expected_revision=0,
        task_id="task-a",
        idempotency_key="idempotency-a",
        manifest=_manifest("endpoint-a", 0, HASH_A),
    )
    endpoint = ModelInvocationService.resolve_runtime_handoff_endpoint(
        tenant_id="tenant-a",
        endpoint_id="endpoint-a",
        required_capability="openai_chat",
        endpoint_registry=registry,
    )
    profile = ModelProfile(
        profile_id="runtime-handoff",
        provider_id="local-provider",
        model="model-a",
        base_url="http://localhost:11434/v1",
        context_tokens=8192,
        max_output_tokens=2048,
    )
    captured = {}

    class Provider:
        def invoke(self, **kwargs):  # noqa: ANN003
            captured.update(kwargs)
            return {"choices": [{"message": {"content": "ok"}}]}

    service = RuntimeHandoffInvocationService(Provider())

    result = service.invoke_result(
        "hello",
        endpoint=endpoint,
        profile=profile,
    )

    assert result["choices"][0]["message"]["content"] == "ok"
    assert captured["model_id"] == "model-a"
    assert captured["resolution_info"]["endpoint_revision"] == 1

    with pytest.raises(ModelRoutingConfigurationError, match="runtime_handoff_invocation_binding_invalid"):
        service.invoke_result(
            "hello",
            endpoint={**endpoint, "fallback": {"provider_id": "other"}},
            profile=profile,
        )


def test_rollback_appends_previous_immutable_revision(tmp_path) -> None:
    registry = SqliteRuntimeEndpointRegistry(tmp_path / "endpoints.sqlite3")
    registry.apply_handoff(
        tenant_id="tenant-a",
        endpoint_id="endpoint-a",
        expected_revision=0,
        task_id="task-a",
        idempotency_key="idempotency-a",
        manifest=_manifest("endpoint-a", 0, HASH_A),
    )
    registry.apply_handoff(
        tenant_id="tenant-a",
        endpoint_id="endpoint-a",
        expected_revision=1,
        task_id="task-b",
        idempotency_key="idempotency-b",
        manifest=_manifest("endpoint-a", 1, HASH_B),
    )

    rolled_back = registry.rollback(
        tenant_id="tenant-a",
        endpoint_id="endpoint-a",
        expected_revision=2,
        reason_sha256=hashlib.sha256(b"bounded operator reason").hexdigest(),
        actor_id="admin-a",
    )

    assert rolled_back.revision == 3
    assert rolled_back.restored_from_revision == 1
    assert rolled_back.manifest["artifact"]["artifact_sha256"] == HASH_A
    assert [item.operation for item in registry.history(
        tenant_id="tenant-a",
        endpoint_id="endpoint-a",
    )] == ["handoff", "handoff", "rollback"]


def test_descriptors_reject_direct_provider_targets() -> None:
    provider = {
        **_descriptor()["provider"],
        "capabilities": _descriptor()["api_capabilities"],
        "limits": _descriptor()["limits"],
        "base_url": "http://provider.internal",
    }

    with pytest.raises(ValueError):
        IntegrationRegistryService().normalize_runtime_endpoint_descriptor(
            provider_descriptor=provider,
            endpoint_descriptor=_descriptor()["endpoint"],
        )
