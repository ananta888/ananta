from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from agent.services.restricted_inference_endpoint_policy import (
    AddressResolver,
    RestrictedInferenceEndpointResolutionError,
    pin_private_container_address,
    require_allowlisted_restricted_inference_endpoint,
)
from agent.services.restricted_inference_management_circuit_breaker import (
    RestrictedInferenceManagementCircuitBreaker,
    get_restricted_inference_management_circuit_breaker,
)

_MANIFEST_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_MANIFEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,191}$")
_INFERENCE_PATH = "/internal/v1/restricted-inference"
_FORBIDDEN_RESPONSE_KEYS = frozenset(
    {
        "authorization",
        "bearer_token",
        "endpoint",
        "manifest_root",
        "model_path",
        "password",
        "secret",
        "snapshot_path",
        "snapshot_root",
        "token",
        "tokenizer_path",
        "url",
        "worker_url",
    }
)


class RestrictedInferenceManagementError(RuntimeError):
    def __init__(self, reason_code: str, message: str, *, status_code: int = 502) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.status_code = status_code


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        raise RestrictedInferenceManagementError("worker_redirect_forbidden", "worker redirect refused")


class RestrictedInferenceManagementService:
    """Hub-only client for the isolated worker's bounded management surface."""

    def __init__(
        self,
        *,
        inference_endpoint: str | None = None,
        allowed_endpoints: tuple[str, ...] | None = None,
        bearer_token: str | None = None,
        timeout_seconds: float = 5.0,
        max_response_bytes: int = 4 * 1024 * 1024,
        resolver: AddressResolver | None = None,
        opener: Any | None = None,
        circuit_breaker: RestrictedInferenceManagementCircuitBreaker | None = None,
    ) -> None:
        endpoint = str(inference_endpoint or os.getenv("ANANTA_RESTRICTED_INFERENCE_URL", "")).strip()
        configured_allowlist = allowed_endpoints
        if configured_allowlist is None:
            configured_allowlist = tuple(
                item.strip()
                for item in str(
                    os.getenv("ANANTA_RESTRICTED_INFERENCE_ALLOWED_ENDPOINTS", "")
                ).split(",")
                if item.strip()
            )
        token = str(bearer_token or os.getenv("ANANTA_RESTRICTED_INFERENCE_TOKEN", "")).strip()
        try:
            normalized_endpoint = require_allowlisted_restricted_inference_endpoint(
                endpoint,
                configured_allowlist,
            )
        except ValueError as exc:
            raise RestrictedInferenceManagementError(
                "worker_not_allowlisted",
                "restricted inference management endpoint is not exactly allowlisted",
                status_code=503,
            ) from exc
        parsed = urlsplit(normalized_endpoint)
        if len(token) < 24:
            raise RestrictedInferenceManagementError(
                "worker_auth_not_configured",
                "restricted inference management authentication requires at least 24 characters",
                status_code=503,
            )
        if timeout_seconds <= 0 or not 1024 <= max_response_bytes <= 16 * 1024 * 1024:
            raise ValueError("restricted inference management transport limits are invalid")
        self._parsed = parsed
        self._endpoint_key = normalized_endpoint
        self._token = token
        self._timeout = float(timeout_seconds)
        self._max_response_bytes = int(max_response_bytes)
        self._resolver = resolver
        self._circuit_breaker = (
            circuit_breaker or get_restricted_inference_management_circuit_breaker()
        )
        self._opener = opener or urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _NoRedirectHandler(),
        )

    def status(self) -> dict[str, Any]:
        return self._request("GET", f"{_INFERENCE_PATH}/status")

    def unload(self, manifest_digest: str) -> dict[str, Any]:
        normalized = str(manifest_digest or "").strip().lower()
        if not _MANIFEST_DIGEST_RE.fullmatch(normalized):
            raise RestrictedInferenceManagementError(
                "invalid_manifest_digest",
                "manifest digest must be a SHA-256",
                status_code=422,
            )
        return self._request("POST", f"{_INFERENCE_PATH}/models/{normalized}/unload")

    def load(self, manifest_id: str, *, deadline_epoch_ms: int) -> dict[str, Any]:
        normalized = str(manifest_id or "").strip()
        if not _MANIFEST_ID_RE.fullmatch(normalized):
            raise RestrictedInferenceManagementError(
                "invalid_manifest_id",
                "manifest ID is invalid",
                status_code=422,
            )
        return self._request(
            "POST",
            f"{_INFERENCE_PATH}/models/load",
            body={"manifest_id": normalized, "deadline_epoch_ms": int(deadline_epoch_ms)},
        )

    def configuration(self) -> dict[str, Any]:
        return self._request("GET", f"{_INFERENCE_PATH}/configuration")

    def update_configuration(
        self,
        delta: dict[str, Any],
        *,
        expected_version: int,
    ) -> dict[str, Any]:
        if set(delta) != {"allow_cpu_fallback"} or not isinstance(delta.get("allow_cpu_fallback"), bool):
            raise RestrictedInferenceManagementError(
                "invalid_runtime_configuration",
                "runtime configuration delta is invalid",
                status_code=422,
            )
        if isinstance(expected_version, bool) or not isinstance(expected_version, int) or expected_version < 1:
            raise RestrictedInferenceManagementError(
                "invalid_runtime_configuration",
                "runtime configuration version is invalid",
                status_code=422,
            )
        return self._request(
            "PATCH",
            f"{_INFERENCE_PATH}/configuration",
            body={"delta": delta, "expected_version": expected_version},
        )

    def cache_gc(self) -> dict[str, Any]:
        return self._request("POST", f"{_INFERENCE_PATH}/cache/gc")

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        circuit_decision = self._circuit_breaker.before_request(self._endpoint_key)
        if not circuit_decision.allowed:
            raise RestrictedInferenceManagementError(
                "worker_circuit_open",
                "restricted inference worker is temporarily unavailable",
                status_code=503,
            )
        encoded_body = (
            json.dumps(body or {}, sort_keys=True, separators=(",", ":")).encode("utf-8")
            if method in {"PATCH", "POST"}
            else None
        )
        try:
            address = pin_private_container_address(
                str(self._parsed.hostname or ""),
                int(self._parsed.port or 0),
                resolver=self._resolver,
            )
        except RestrictedInferenceEndpointResolutionError as exc:
            if exc.reason_code == "worker_unavailable":
                self._circuit_breaker.record_unavailable(self._endpoint_key)
            else:
                self._circuit_breaker.record_reachable(self._endpoint_key)
            raise RestrictedInferenceManagementError(
                exc.reason_code,
                str(exc),
                status_code=503,
            ) from exc
        netloc = f"[{address}]:{self._parsed.port}" if ":" in address else f"{address}:{self._parsed.port}"
        request = urllib.request.Request(
            urlunsplit(("http", netloc, path, "", "")),
            data=encoded_body,
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
            payload = self._read_payload(response)
        except urllib.error.HTTPError as exc:
            self._circuit_breaker.record_reachable(self._endpoint_key)
            payload = self._read_payload(exc)
            error = payload.get("error") if isinstance(payload, dict) else None
            reason = (
                str(error.get("code") or "worker_management_failed")
                if isinstance(error, dict)
                else "worker_management_failed"
            )
            raise RestrictedInferenceManagementError(
                reason,
                "restricted inference worker rejected the management operation",
                status_code=exc.code,
            ) from exc
        except RestrictedInferenceManagementError:
            self._circuit_breaker.record_reachable(self._endpoint_key)
            raise
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            self._circuit_breaker.record_unavailable(self._endpoint_key)
            raise RestrictedInferenceManagementError(
                "worker_unavailable",
                "restricted inference worker is unavailable",
                status_code=503,
            ) from exc
        self._circuit_breaker.record_reachable(self._endpoint_key)
        if not isinstance(payload, dict):
            raise RestrictedInferenceManagementError("invalid_worker_response", "worker response must be an object")
        sanitized = _sanitize_management_payload(payload)
        if not isinstance(sanitized, dict):
            raise RestrictedInferenceManagementError("invalid_worker_response", "worker response must be an object")
        return sanitized

    def _read_payload(self, response: Any) -> Any:
        if "application/json" not in str(response.headers.get("Content-Type") or "").lower():
            raise RestrictedInferenceManagementError("invalid_worker_response", "worker response must be JSON")
        data = response.read(self._max_response_bytes + 1)
        if len(data) > self._max_response_bytes:
            raise RestrictedInferenceManagementError("worker_response_too_large", "worker response exceeds limit")
        try:
            return json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise RestrictedInferenceManagementError("invalid_worker_response", "worker returned invalid JSON") from exc


def get_restricted_inference_management_service() -> RestrictedInferenceManagementService:
    return RestrictedInferenceManagementService()


def _sanitize_management_payload(value: Any, *, depth: int = 0) -> Any:
    if depth > 8:
        return None
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for raw_key, child in list(value.items())[:512]:
            key = str(raw_key)[:128]
            normalized_key = key.casefold()
            if normalized_key in _FORBIDDEN_RESPONSE_KEYS or any(
                normalized_key.endswith(suffix)
                for suffix in ("_endpoint", "_password", "_path", "_root", "_secret", "_token", "_url")
            ):
                continue
            sanitized[key] = _sanitize_management_payload(child, depth=depth + 1)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_management_payload(item, depth=depth + 1) for item in value[:512]]
    if isinstance(value, str):
        bounded = value[:1024]
        if "://" in bounded or bounded.startswith(("/", "\\\\")) or re.match(r"^[A-Za-z]:[\\/]", bounded):
            return "[redacted]"
        return bounded
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    return str(value)[:256]
