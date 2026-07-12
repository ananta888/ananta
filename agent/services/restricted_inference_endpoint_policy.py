"""Shared exact endpoint policy for the isolated restricted-inference worker."""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from agent.services.private_container_network_policy import (
    AddressResolver,
    PrivateContainerResolutionError,
    pin_private_container_address,
)

__all__ = [
    "AddressResolver",
    "RESTRICTED_INFERENCE_PATH",
    "RestrictedInferenceEndpointResolutionError",
    "normalize_restricted_inference_endpoint",
    "pin_private_container_address",
    "require_allowlisted_restricted_inference_endpoint",
]

RESTRICTED_INFERENCE_PATH = "/internal/v1/restricted-inference"
RestrictedInferenceEndpointResolutionError = PrivateContainerResolutionError


def normalize_restricted_inference_endpoint(value: str) -> str:
    parsed = urlsplit(str(value or "").strip())
    if (
        parsed.scheme != "http"
        or not parsed.hostname
        or parsed.port is None
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path != RESTRICTED_INFERENCE_PATH
    ):
        raise ValueError("restricted inference endpoint must be the explicit internal HTTP endpoint")
    hostname = parsed.hostname.casefold()
    host = f"[{hostname}]" if ":" in hostname else hostname
    return urlunsplit(("http", f"{host}:{parsed.port}", RESTRICTED_INFERENCE_PATH, "", ""))


def require_allowlisted_restricted_inference_endpoint(
    endpoint: str,
    allowed_endpoints: tuple[str, ...],
) -> str:
    normalized = normalize_restricted_inference_endpoint(endpoint)
    allowed = {
        normalize_restricted_inference_endpoint(item) for item in allowed_endpoints
    }
    if normalized not in allowed:
        raise ValueError("restricted inference endpoint is not exactly allowlisted")
    return normalized
