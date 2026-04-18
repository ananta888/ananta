from __future__ import annotations

import json
import socket
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
    ):
        self.base_url = base_url.rstrip("/")
        self.analyze_path = analyze_path if analyze_path.startswith("/") else f"/{analyze_path}"
        self.timeout_seconds = timeout_seconds
        self.health_path = self._normalize_path(health_path) if health_path else None

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
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        raw = self._open(req)
        decoded = self._decode_json(raw)
        if not isinstance(decoded, dict):
            raise EvolverInvalidResponseError("evolver_response_must_be_object")
        return decoded

    def health(self) -> dict[str, Any]:
        req = request.Request(
            self.health_endpoint,
            headers={"Accept": "application/json"},
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

    def _open(self, req: request.Request) -> str:
        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as response:
                status_code = int(getattr(response, "status", 200) or 200)
                if status_code < 200 or status_code >= 300:
                    raise EvolverHttpError(status_code)
                return response.read().decode("utf-8")
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
        timeout_seconds = float(config.get("timeout_seconds") or 30.0)
        transport = HttpEvolverTransport(
            base_url=base_url,
            analyze_path=str(config.get("analyze_path") or "/evolution/analyze"),
            timeout_seconds=timeout_seconds,
            health_path=config.get("health_path"),
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
                "timeout_seconds": timeout_seconds,
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
                metadata["health_checked"] = True
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
