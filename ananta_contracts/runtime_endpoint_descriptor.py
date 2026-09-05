"""Pure validation for provider-neutral runtime endpoint descriptors."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


def normalize_runtime_endpoint_descriptor(
    *,
    provider_descriptor: Mapping[str, Any],
    endpoint_descriptor: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate a provider identity without importing Hub runtime state."""
    provider_allowed = {
        "provider_id",
        "provider_type",
        "model_id",
        "provider_revision",
        "capabilities",
        "limits",
    }
    endpoint_allowed = {"endpoint_id", "display_name", "routing_key"}
    if (
        not isinstance(provider_descriptor, Mapping)
        or set(provider_descriptor) - provider_allowed
        or not isinstance(endpoint_descriptor, Mapping)
        or set(endpoint_descriptor) - endpoint_allowed
    ):
        raise ValueError("runtime_endpoint_descriptor_unknown_fields")
    opaque = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    provider_id = str(provider_descriptor.get("provider_id") or "").strip()
    provider_type = str(provider_descriptor.get("provider_type") or "").strip()
    model_id = str(provider_descriptor.get("model_id") or "").strip()
    provider_revision = str(provider_descriptor.get("provider_revision") or "").strip()
    endpoint_id = str(endpoint_descriptor.get("endpoint_id") or "").strip()
    routing_key = str(endpoint_descriptor.get("routing_key") or "").strip()
    model_identity = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
    if (
        any(
            opaque.fullmatch(value) is None
            for value in (provider_id, provider_type, provider_revision, endpoint_id, routing_key)
        )
        or model_identity.fullmatch(model_id) is None
        or ".." in model_id
        or model_id.startswith("/")
    ):
        raise ValueError("runtime_endpoint_descriptor_identity_invalid")
    display_name = str(endpoint_descriptor.get("display_name") or endpoint_id).strip()
    if not 1 <= len(display_name) <= 128:
        raise ValueError("runtime_endpoint_display_name_invalid")
    capability_names = {
        "openai_chat",
        "openai_responses",
        "anthropic_messages",
        "streaming",
        "tools",
        "structured_output",
    }
    raw_capabilities = provider_descriptor.get("capabilities")
    if (
        not isinstance(raw_capabilities, Mapping)
        or set(raw_capabilities) != capability_names
        or not all(isinstance(value, bool) for value in raw_capabilities.values())
        or not any(raw_capabilities.values())
    ):
        raise ValueError("runtime_endpoint_capabilities_invalid")
    capabilities = {key: bool(raw_capabilities[key]) for key in sorted(capability_names)}
    primary_api = (
        capabilities["openai_chat"]
        or capabilities["openai_responses"]
        or capabilities["anthropic_messages"]
    )
    if (
        (capabilities["streaming"] and not primary_api)
        or (capabilities["tools"] and not primary_api)
        or (capabilities["structured_output"] and not primary_api)
    ):
        raise ValueError("runtime_endpoint_capability_dependency_invalid")
    raw_limits = provider_descriptor.get("limits")
    expected_limits = {
        "timeout_seconds": (1, 300),
        "context_tokens": (128, 2_097_152),
        "max_output_tokens": (1, 262_144),
        "stream_idle_timeout_seconds": (1, 300),
    }
    if not isinstance(raw_limits, Mapping) or set(raw_limits) != set(expected_limits):
        raise ValueError("runtime_endpoint_limits_invalid")
    limits: dict[str, int] = {}
    for name, (minimum, maximum) in expected_limits.items():
        value = raw_limits.get(name)
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or not minimum <= value <= maximum
        ):
            raise ValueError("runtime_endpoint_limits_invalid")
        limits[name] = value
    return {
        "schema": "ananta.runtime-endpoint-descriptor.v1",
        "provider": {
            "provider_id": provider_id,
            "provider_type": provider_type,
            "model_id": model_id,
            "provider_revision": provider_revision,
        },
        "endpoint": {
            "endpoint_id": endpoint_id,
            "display_name": display_name,
            "routing_key": routing_key,
        },
        "api_capabilities": capabilities,
        "limits": limits,
        "fallback": None,
    }
