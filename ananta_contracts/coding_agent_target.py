"""Pure Hub-selected inference target shared with delegated coding Workers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CodingAgentInferenceTarget:
    """Selected endpoint data, not a model resolver or execution authorization."""

    client_id: str
    provider_id: str | None
    model: str | None
    cli_model: str | None
    base_url: str | None
    target_kind: str | None
    provider_type: str | None = None
    api_key: str | None = None
    api_key_source: str | None = None

    def public_metadata(self) -> dict[str, object]:
        return {
            "client_id": self.client_id,
            "target_provider": self.provider_id,
            "target_model": self.model,
            "cli_model": self.cli_model,
            "target_base_url": self.base_url,
            "target_kind": self.target_kind,
            "target_provider_type": self.provider_type,
            "api_key_configured": bool(self.api_key),
            "api_key_source": self.api_key_source,
        }

    def process_environment(self) -> dict[str, str]:
        environment: dict[str, str] = {}
        if self.base_url:
            environment["OPENAI_API_BASE"] = self.base_url
            environment["OPENAI_BASE_URL"] = self.base_url
        if self.api_key:
            environment["OPENAI_API_KEY"] = self.api_key
        return environment
