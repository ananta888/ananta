"""Authenticated Hub port for the isolated LoRA inference capability."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from agent.services.ml_intern_lora_inference_contract import (
    CONTRACT_VERSION,
    GENERATION_CAPABILITY,
    MANAGEMENT_CAPABILITY,
    LoraInferenceContractError,
    require_identifier,
)
from agent.services.private_container_network_policy import (
    AddressResolver,
    PrivateContainerResolutionError,
    pin_private_container_address,
)
from ananta_contracts.file_credentials import (
    FileCredentialConfigurationError,
    read_file_managed_token,
)

_WORKER_BASE_PATH = "/internal/v1/lora-training"


class LoraInferenceWorkerTransportError(RuntimeError):
    def __init__(self, reason_code: str, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.retryable = retryable


@runtime_checkable
class LoraInferenceWorkerPort(Protocol):
    @property
    def worker_id(self) -> str: ...

    def capabilities(self) -> Mapping[str, Any]: ...

    def generate(self, envelope: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def unload(self, *, adapter_id: str, adapter_version: str, reason: str) -> Mapping[str, Any]: ...


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        raise LoraInferenceWorkerTransportError(
            "worker_redirect_forbidden",
            "LoRA inference worker refused a redirect",
            retryable=False,
        )


class HttpLoraInferenceWorkerPort:
    """Exact-allowlist HTTP adapter with capability and response binding."""

    def __init__(
        self,
        *,
        endpoint: str,
        allowed_endpoints: tuple[str, ...],
        bearer_token: str,
        timeout_seconds: float = 120.0,
        max_response_bytes: int = 8 * 1024 * 1024,
        resolver: AddressResolver | None = None,
        opener: Any | None = None,
    ) -> None:
        normalized = normalize_lora_inference_worker_endpoint(endpoint)
        allowed = {normalize_lora_inference_worker_endpoint(item) for item in allowed_endpoints}
        if normalized not in allowed:
            raise ValueError("LoRA inference worker endpoint is not exactly allowlisted")
        token = str(bearer_token or "").strip()
        if len(token) < 24 or any(character.isspace() for character in token):
            raise ValueError("LoRA inference worker bearer token must contain at least 24 characters")
        if not 1 <= float(timeout_seconds) <= 300:
            raise ValueError("LoRA inference timeout is outside its bounds")
        if not 1024 <= int(max_response_bytes) <= 64 * 1024 * 1024:
            raise ValueError("LoRA inference response limit is outside its bounds")
        self._parsed = urllib.parse.urlsplit(normalized)
        self._token = token
        self._timeout = float(timeout_seconds)
        self._max_response_bytes = int(max_response_bytes)
        self._resolver = resolver
        self._opener = opener or urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _NoRedirectHandler(),
        )

    @property
    def worker_id(self) -> str:
        return "isolated:lora-training-worker"

    def capabilities(self) -> Mapping[str, Any]:
        payload = self._request("GET", "/inference/capabilities")
        if payload.get("contract_version") != CONTRACT_VERSION:
            raise LoraInferenceWorkerTransportError(
                "worker_contract_mismatch",
                "LoRA inference worker contract version mismatch",
                retryable=False,
            )
        capabilities = payload.get("capabilities")
        if not isinstance(capabilities, list) or any(not isinstance(item, str) for item in capabilities):
            raise LoraInferenceWorkerTransportError(
                "invalid_worker_response",
                "LoRA inference worker capability response is invalid",
                retryable=False,
            )
        return payload

    def generate(self, envelope: Mapping[str, Any]) -> Mapping[str, Any]:
        capabilities = self.capabilities()
        if capabilities.get("available") is not True or GENERATION_CAPABILITY not in set(capabilities["capabilities"]):
            raise LoraInferenceWorkerTransportError(
                str(capabilities.get("reason_code") or "worker_capability_unavailable"),
                "No capability-matched LoRA inference runtime is available",
            )
        payload = self._request("POST", "/inference/generate", body=dict(envelope))
        if (
            payload.get("contract_version") != CONTRACT_VERSION
            or payload.get("request_id") != envelope.get("request_id")
            or payload.get("task_id") != envelope.get("task_id")
            or payload.get("capability") != GENERATION_CAPABILITY
            or payload.get("adapter_id") != (envelope.get("adapter") or {}).get("adapter_id")
            or payload.get("adapter_version") != (envelope.get("adapter") or {}).get("version")
            or payload.get("base_model") != (envelope.get("base_model") or {}).get("model_id")
            or payload.get("status") != "succeeded"
            or not isinstance(payload.get("output"), str)
        ):
            raise LoraInferenceWorkerTransportError(
                "worker_response_binding_mismatch",
                "LoRA inference worker response does not match its request",
                retryable=False,
            )
        return payload

    def unload(self, *, adapter_id: str, adapter_version: str, reason: str) -> Mapping[str, Any]:
        adapter_id = require_identifier(adapter_id, "adapter_id")
        adapter_version = require_identifier(adapter_version, "adapter_version")
        normalized_reason = str(reason or "").strip()
        if not 10 <= len(normalized_reason) <= 512:
            raise LoraInferenceContractError("unload_reason_invalid", "a bounded unload reason is required")
        capabilities = self.capabilities()
        if capabilities.get("available") is not True or MANAGEMENT_CAPABILITY not in set(capabilities["capabilities"]):
            raise LoraInferenceWorkerTransportError(
                str(capabilities.get("reason_code") or "worker_management_capability_unavailable"),
                "No capability-matched LoRA cache management runtime is available",
            )
        payload = self._request(
            "POST",
            f"/inference/adapters/{adapter_id}/{adapter_version}/unload",
            body={"confirmed": True, "reason": normalized_reason},
        )
        if (
            payload.get("contract_version") != CONTRACT_VERSION
            or payload.get("capability") != MANAGEMENT_CAPABILITY
            or payload.get("adapter_id") != adapter_id
            or payload.get("adapter_version") != adapter_version
            or payload.get("status") != "succeeded"
        ):
            raise LoraInferenceWorkerTransportError(
                "worker_response_binding_mismatch",
                "LoRA inference unload response does not match its request",
                retryable=False,
            )
        return payload

    def _request(
        self,
        method: str,
        suffix: str,
        *,
        body: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            address = pin_private_container_address(
                str(self._parsed.hostname or ""),
                int(self._parsed.port or 0),
                resolver=self._resolver,
            )
        except PrivateContainerResolutionError as exc:
            raise LoraInferenceWorkerTransportError(exc.reason_code, str(exc)) from exc
        netloc = f"[{address}]:{self._parsed.port}" if ":" in address else f"{address}:{self._parsed.port}"
        url = urllib.parse.urlunsplit(("http", netloc, f"{_WORKER_BASE_PATH}{suffix}", "", ""))
        encoded = (
            json.dumps(dict(body), separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
            if body is not None
            else None
        )
        request = urllib.request.Request(
            url,
            data=encoded,
            method=method,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Host": self._parsed.netloc,
            },
        )
        try:
            response = self._opener.open(request, timeout=self._timeout)
            payload = self._read_json(response)
        except urllib.error.HTTPError as exc:
            payload = self._read_json(exc)
            error = payload.get("error") if isinstance(payload, dict) else None
            reason_code = str(error.get("code") or "worker_rejected") if isinstance(error, dict) else "worker_rejected"
            retryable = bool(error.get("retryable")) if isinstance(error, dict) else exc.code >= 500
            raise LoraInferenceWorkerTransportError(
                reason_code,
                "LoRA inference worker rejected the request",
                retryable=retryable,
            ) from exc
        except LoraInferenceWorkerTransportError:
            raise
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            raise LoraInferenceWorkerTransportError(
                "worker_unavailable",
                "LoRA inference worker is unavailable",
            ) from exc
        if not isinstance(payload, dict):
            raise LoraInferenceWorkerTransportError(
                "invalid_worker_response",
                "LoRA inference worker response must be an object",
                retryable=False,
            )
        return payload

    def _read_json(self, response: Any) -> Any:
        if "application/json" not in str(response.headers.get("Content-Type") or "").lower():
            raise LoraInferenceWorkerTransportError(
                "invalid_worker_response",
                "LoRA inference worker response must be JSON",
                retryable=False,
            )
        data = response.read(self._max_response_bytes + 1)
        if len(data) > self._max_response_bytes:
            raise LoraInferenceWorkerTransportError(
                "worker_response_too_large",
                "LoRA inference worker response exceeds its limit",
                retryable=False,
            )
        try:
            return json.loads(data.decode("utf-8"))
        except (UnicodeError, ValueError) as exc:
            raise LoraInferenceWorkerTransportError(
                "invalid_worker_response",
                "LoRA inference worker returned invalid JSON",
                retryable=False,
            ) from exc


def normalize_lora_inference_worker_endpoint(value: str) -> str:
    parsed = urllib.parse.urlsplit(str(value or "").strip())
    if (
        parsed.scheme != "http"
        or not parsed.hostname
        or parsed.port is None
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/") != _WORKER_BASE_PATH
    ):
        raise ValueError("LoRA inference worker endpoint must be the explicit internal endpoint")
    hostname = parsed.hostname.casefold()
    host = f"[{hostname}]" if ":" in hostname else hostname
    return urllib.parse.urlunsplit(("http", f"{host}:{parsed.port}", _WORKER_BASE_PATH, "", ""))


def lora_inference_worker_port_from_environment() -> HttpLoraInferenceWorkerPort | None:
    endpoint = str(
        os.getenv("ANANTA_LORA_INFERENCE_WORKER_URL") or os.getenv("ANANTA_LORA_TRAINING_WORKER_URL") or ""
    ).strip()
    if not endpoint:
        return None
    raw_allowed = str(
        os.getenv("ANANTA_LORA_INFERENCE_ALLOWED_ENDPOINTS")
        or os.getenv("ANANTA_LORA_TRAINING_ALLOWED_ENDPOINTS")
        or ""
    )
    allowed = tuple(item.strip() for item in raw_allowed.split(",") if item.strip())
    if not allowed:
        raise RuntimeError("LoRA inference worker is configured without an exact endpoint allowlist")
    return HttpLoraInferenceWorkerPort(
        endpoint=endpoint,
        allowed_endpoints=allowed,
        bearer_token=_worker_token_from_environment(),
        timeout_seconds=float(os.getenv("ANANTA_LORA_INFERENCE_TIMEOUT_SECONDS", "120")),
    )


def _worker_token_from_environment() -> str:
    inline = str(os.getenv("ANANTA_LORA_INFERENCE_TOKEN") or os.getenv("ANANTA_LORA_TRAINING_TOKEN") or "").strip()
    path = str(
        os.getenv("ANANTA_LORA_INFERENCE_TOKEN_FILE") or os.getenv("ANANTA_LORA_TRAINING_TOKEN_FILE") or ""
    ).strip()
    if path:
        try:
            file_token = read_file_managed_token(
                path,
                description="LoRA inference worker token file",
                min_bytes=24,
                max_bytes=16_384,
            )
        except FileCredentialConfigurationError as exc:
            raise RuntimeError(str(exc)) from exc
        if inline and inline != file_token:
            raise RuntimeError("inline and file-managed LoRA inference tokens conflict")
        return file_token
    if len(inline) < 24:
        raise RuntimeError("LoRA inference worker is configured without a valid bearer token")
    return inline


__all__ = [
    "HttpLoraInferenceWorkerPort",
    "LoraInferenceWorkerPort",
    "LoraInferenceWorkerTransportError",
    "lora_inference_worker_port_from_environment",
    "normalize_lora_inference_worker_endpoint",
]
