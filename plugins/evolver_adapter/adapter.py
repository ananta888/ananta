from __future__ import annotations

import json
import socket
from dataclasses import dataclass
from typing import Any, Protocol
from urllib import error, request

from agent.services.evolution import (
    EvolutionCapability,
    EvolutionContext,
    EvolutionEngine,
    EvolutionProposal,
    EvolutionProviderDescriptor,
    EvolutionResult,
    UnsupportedEvolutionOperation,
    ApplyResult,
    ValidationResult,
)

from .mapper import map_evolver_result


def _headers_from_config(config: dict[str, Any]) -> dict[str, str]:
    headers: dict[str, str] = {}
    configured = config.get("headers")
    if isinstance(configured, dict):
        headers.update({str(key): str(value) for key, value in configured.items() if str(key).strip()})
    bearer_token = str(config.get("bearer_token") or "").strip()
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    return headers


class EvolverTransportError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        status_code: int | None = None,
        transient: bool = False,
    ):
        self.code = code
        self.status_code = status_code
        self.transient = transient
        super().__init__(message)


class EvolverTimeoutError(EvolverTransportError):
    def __init__(self, message: str = "evolver_transport_timeout"):
        super().__init__(message, code="timeout", transient=True)


class EvolverConnectionError(EvolverTransportError):
    def __init__(self, message: str = "evolver_transport_connection_error"):
        super().__init__(message, code="connection_error", transient=True)


class EvolverHttpError(EvolverTransportError):
    def __init__(self, status_code: int):
        super().__init__(
            f"evolver_transport_http_error:{status_code}",
            code="http_error",
            status_code=status_code,
            transient=status_code in {408, 429, 500, 502, 503, 504},
        )


class EvolverInvalidResponseError(EvolverTransportError):
    def __init__(self, message: str = "evolver_transport_invalid_response"):
        super().__init__(message, code="invalid_response", transient=False)


class EvolverPayloadLimitError(EvolverTransportError):
    def __init__(self, max_response_bytes: int):
        self.max_response_bytes = max_response_bytes
        super().__init__("evolver_response_too_large", code="payload_too_large", transient=False)


@dataclass(frozen=True)
class EvolverHttpLimits:
    connect_timeout_seconds: float = 30.0
    read_timeout_seconds: float = 30.0
    max_response_bytes: int = 1024 * 1024

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "EvolverHttpLimits":
        timeout = float(config.get("timeout_seconds") or 30.0)
        connect_timeout = float(config.get("connect_timeout_seconds") or timeout)
        read_timeout = float(config.get("read_timeout_seconds") or timeout)
        max_response_bytes = int(config.get("max_response_bytes") or 1024 * 1024)
        return cls(
            connect_timeout_seconds=max(0.1, connect_timeout),
            read_timeout_seconds=max(0.1, read_timeout),
            max_response_bytes=max(1024, min(max_response_bytes, 16 * 1024 * 1024)),
        )


class EvolverTransport(Protocol):
    def analyze(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError


class HttpEvolverTransport:
    def __init__(
        self,
        *,
        base_url: str,
        analyze_path: str = "/evolution/analyze",
        timeout_seconds: float = 30.0,
        health_path: str | None = None,
        headers: dict[str, str] | None = None,
        limits: EvolverHttpLimits | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.analyze_path = analyze_path if analyze_path.startswith("/") else f"/{analyze_path}"
        self.limits = limits or EvolverHttpLimits.from_config({"timeout_seconds": timeout_seconds})
        self.timeout_seconds = self.limits.connect_timeout_seconds
        self.health_path = self._normalize_path(health_path) if health_path else None
        self.headers = dict(headers or {})

    @property
    def endpoint(self) -> str:
        return f"{self.base_url}{self.analyze_path}"

    @property
    def health_endpoint(self) -> str:
        return f"{self.base_url}{self.health_path}" if self.health_path else self.base_url

    def analyze(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            self.endpoint,
            data=body,
            headers=self._headers({"Content-Type": "application/json", "Accept": "application/json"}),
            method="POST",
        )
        raw = self._open(req)
        decoded = self._decode_json(raw)
        if not isinstance(decoded, dict):
            raise EvolverInvalidResponseError("evolver_response_must_be_object")
        return decoded

    def health(self) -> dict[str, Any]:
        if not self.health_path:
            return {"status": "unknown", "checked": False, "fallback": "health_endpoint_not_configured"}
        req = request.Request(
            self.health_endpoint,
            headers=self._headers({"Accept": "application/json"}),
            method="GET",
        )
        raw = self._open(req)
        if not raw.strip():
            return {"status": "available"}
        decoded = self._decode_json(raw)
        if not isinstance(decoded, dict):
            raise EvolverInvalidResponseError("evolver_health_response_must_be_object")
        return decoded

    @staticmethod
    def _normalize_path(path: str | None) -> str:
        value = str(path or "").strip()
        return value if value.startswith("/") else f"/{value}"

    def _headers(self, base: dict[str, str]) -> dict[str, str]:
        headers = dict(base)
        headers.update(self.headers)
        return headers

    def _open(self, req: request.Request) -> str:
        try:
            with request.urlopen(req, timeout=self.limits.connect_timeout_seconds) as response:
                status_code = int(getattr(response, "status", 200) or 200)
                if status_code < 200 or status_code >= 300:
                    raise EvolverHttpError(status_code)
                self._apply_read_timeout(response)
                return self._read_limited(response)
        except EvolverTransportError:
            raise
        except error.HTTPError as exc:
            raise EvolverHttpError(int(exc.code)) from exc
        except TimeoutError as exc:
            raise EvolverTimeoutError() from exc
        except socket.timeout as exc:
            raise EvolverTimeoutError() from exc
        except error.URLError as exc:
            reason = getattr(exc, "reason", None)
            if isinstance(reason, TimeoutError | socket.timeout):
                raise EvolverTimeoutError() from exc
            raise EvolverConnectionError() from exc

    @staticmethod
    def _decode_json(raw: str) -> Any:
        try:
            return json.loads(raw or "{}")
        except json.JSONDecodeError as exc:
            raise EvolverInvalidResponseError("evolver_response_invalid_json") from exc

    def _read_limited(self, response) -> str:
        chunks: list[bytes] = []
        total = 0
        while True:
            try:
                chunk = response.read(min(65536, self.limits.max_response_bytes + 1 - total))
            except TimeoutError as exc:
                raise EvolverTimeoutError("evolver_transport_read_timeout") from exc
            except socket.timeout as exc:
                raise EvolverTimeoutError("evolver_transport_read_timeout") from exc
            if not chunk:
                break
            total += len(chunk)
            if total > self.limits.max_response_bytes:
                raise EvolverPayloadLimitError(self.limits.max_response_bytes)
            chunks.append(chunk)
        return b"".join(chunks).decode("utf-8")

    def _apply_read_timeout(self, response) -> None:
        try:
            response.fp.raw._sock.settimeout(self.limits.read_timeout_seconds)
        except Exception:
            return


class EvolverAdapter(EvolutionEngine):
    def __init__(
        self,
        *,
        transport: EvolverTransport,
        provider_name: str = "evolver",
        version: str = "unknown",
        provider_metadata: dict[str, Any] | None = None,
    ):
        self._transport = transport
        self._provider_name = provider_name
        self._version = version
        self._provider_metadata = provider_metadata or {}

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "EvolverAdapter":
        base_url = str(config.get("base_url") or "").strip()
        if not base_url:
            raise ValueError("evolver_base_url_required")
        limits = EvolverHttpLimits.from_config(config)
        headers = _headers_from_config(config)
        transport = HttpEvolverTransport(
            base_url=base_url,
            analyze_path=str(config.get("analyze_path") or "/evolution/analyze"),
            timeout_seconds=limits.connect_timeout_seconds,
            health_path=config.get("health_path"),
            headers=headers,
            limits=limits,
        )
        return cls(
            transport=transport,
            provider_name=str(config.get("provider_name") or "evolver"),
            version=str(config.get("version") or "unknown"),
            provider_metadata={
                "transport": "http",
                "base_url": base_url,
                "analyze_path": transport.analyze_path,
                "health_path": transport.health_path,
                "connect_timeout_seconds": limits.connect_timeout_seconds,
                "read_timeout_seconds": limits.read_timeout_seconds,
                "max_response_bytes": limits.max_response_bytes,
                "configured_header_names": sorted(headers.keys()),
            },
        )

    @property
    def provider_name(self) -> str:
        return self._provider_name

    @property
    def version(self) -> str:
        return self._version

    @property
    def capabilities(self):
        return [
            EvolutionCapability.ANALYZE,
            EvolutionCapability.RISK_SCORING,
            EvolutionCapability.REVIEW_HINTS,
        ]

    def describe(self) -> EvolutionProviderDescriptor:
        status = "available"
        metadata = dict(self._provider_metadata)
        health_fn = getattr(self._transport, "health", None)
        if callable(health_fn):
            try:
                health = health_fn()
                status = str(health.get("status") or status)
                metadata["health_checked"] = bool(health.get("checked", True))
                if health.get("fallback"):
                    metadata["health_fallback"] = health.get("fallback")
            except EvolverTransportError as exc:
                status = "degraded" if exc.transient else "unavailable"
                metadata["health_checked"] = True
                metadata["last_error"] = {
                    "code": exc.code,
                    "message": str(exc),
                    "transient": exc.transient,
                    "status_code": exc.status_code,
                }
            except Exception as exc:
                status = "unavailable"
                metadata["health_checked"] = True
                metadata["last_error"] = {"code": "health_error", "message": str(exc)}
        else:
            metadata["health_checked"] = False
        return EvolutionProviderDescriptor(
            provider_name=self.provider_name,
            version=self.version,
            status=status,
            capabilities=self.normalized_capabilities(),
            provider_metadata=metadata,
        )

    def analyze(self, context: EvolutionContext) -> EvolutionResult:
        payload = {"context": self._external_context_payload(context)}
        raw_result = self._transport.analyze(payload)
        return map_evolver_result(raw_result, provider_name=self.provider_name)

    def validate(self, context: EvolutionContext, proposal: EvolutionProposal) -> ValidationResult:
        raise UnsupportedEvolutionOperation(self.provider_name, EvolutionCapability.VALIDATE.value)

    def apply(self, context: EvolutionContext, proposal: EvolutionProposal) -> ApplyResult:
        raise UnsupportedEvolutionOperation(self.provider_name, EvolutionCapability.APPLY.value)

    @staticmethod
    def _external_context_payload(context: EvolutionContext) -> dict[str, Any]:
        raw = context.model_dump(mode="json")
        signals = raw.get("signals") if isinstance(raw.get("signals"), dict) else {}
        task = signals.get("task") if isinstance(signals.get("task"), dict) else {}
        verification = signals.get("verification") if isinstance(signals.get("verification"), dict) else {}
        audit = signals.get("audit") if isinstance(signals.get("audit"), dict) else {}
        artifacts = signals.get("artifacts") if isinstance(signals.get("artifacts"), list) else []
        constraints = raw.get("constraints") if isinstance(raw.get("constraints"), dict) else {}
        return {
            "objective": raw.get("objective"),
            "task": {
                "title": task.get("title"),
                "description": task.get("description"),
                "status": task.get("status"),
                "priority": task.get("priority"),
                "task_kind": task.get("task_kind"),
                "last_exit_code": task.get("last_exit_code"),
                "last_output_present": bool(task.get("last_output_present")),
            },
            "verification": {
                "latest_status": verification.get("latest_status"),
                "record_count": verification.get("record_count"),
            },
            "audit": {
                "event_count": audit.get("event_count"),
            },
            "artifacts": {
                "count": len(artifacts),
                "media_types": sorted(
                    {
                        str(item.get("media_type"))
                        for item in artifacts
                        if isinstance(item, dict) and item.get("media_type")
                    }
                ),
            },
            "constraints": {
                "required_capabilities": constraints.get("required_capabilities")
                if isinstance(constraints.get("required_capabilities"), list)
                else [],
                "review_required": bool(constraints.get("review_required")),
            },
        }
