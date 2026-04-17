from __future__ import annotations

import json
from typing import Any, Protocol
from urllib import request

from agent.services.evolution import (
    EvolutionCapability,
    EvolutionContext,
    EvolutionEngine,
    EvolutionProviderDescriptor,
    EvolutionResult,
)

from .mapper import map_evolver_result


class EvolverTransport(Protocol):
    def analyze(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError


class HttpEvolverTransport:
    def __init__(self, *, base_url: str, analyze_path: str = "/evolution/analyze", timeout_seconds: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.analyze_path = analyze_path if analyze_path.startswith("/") else f"/{analyze_path}"
        self.timeout_seconds = timeout_seconds

    @property
    def endpoint(self) -> str:
        return f"{self.base_url}{self.analyze_path}"

    def analyze(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            self.endpoint,
            data=body,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        with request.urlopen(req, timeout=self.timeout_seconds) as response:
            raw = response.read().decode("utf-8")
        decoded = json.loads(raw or "{}")
        if not isinstance(decoded, dict):
            raise ValueError("evolver_response_must_be_object")
        return decoded


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
        )
        return cls(
            transport=transport,
            provider_name=str(config.get("provider_name") or "evolver"),
            version=str(config.get("version") or "unknown"),
            provider_metadata={
                "transport": "http",
                "base_url": base_url,
                "analyze_path": transport.analyze_path,
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
            EvolutionCapability.PROPOSE,
            EvolutionCapability.RISK_SCORING,
            EvolutionCapability.REVIEW_HINTS,
        ]

    def describe(self) -> EvolutionProviderDescriptor:
        return EvolutionProviderDescriptor(
            provider_name=self.provider_name,
            version=self.version,
            status="available",
            capabilities=self.normalized_capabilities(),
            provider_metadata=self._provider_metadata,
        )

    def analyze(self, context: EvolutionContext) -> EvolutionResult:
        payload = {"context": context.model_dump(mode="json")}
        raw_result = self._transport.analyze(payload)
        return map_evolver_result(raw_result, provider_name=self.provider_name)
