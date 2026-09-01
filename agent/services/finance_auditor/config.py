"""Strict configuration for the Ziegler finance auditor."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from agent.services.finance_auditor.models import AuditTone


@dataclass(frozen=True)
class ZieglerAuditorConfig:
    enabled: bool = False
    use_llm: bool = False
    tone: AuditTone = AuditTone.DIRECT
    read_only: bool = True

    @classmethod
    def from_agent_config(cls, config: Mapping[str, Any] | None) -> "ZieglerAuditorConfig":
        root = dict(config or {})
        finance = root.get("finance_auditor") or {}
        if not isinstance(finance, Mapping):
            raise ValueError("finance_auditor_config_invalid")
        raw = finance.get("ziegler") or {}
        if not isinstance(raw, Mapping):
            raise ValueError("ziegler_auditor_config_invalid")
        allowed = {"enabled", "use_llm", "tone", "read_only"}
        if set(raw) - allowed:
            raise ValueError("ziegler_auditor_config_unknown_field")
        for field in ("enabled", "use_llm", "read_only"):
            if field in raw and not isinstance(raw[field], bool):
                raise ValueError(f"ziegler_auditor_{field}_invalid")
        try:
            tone = AuditTone(str(raw.get("tone", AuditTone.DIRECT.value)))
        except ValueError as exc:
            raise ValueError("ziegler_auditor_tone_invalid") from exc
        if raw.get("read_only", True) is not True:
            raise ValueError("ziegler_auditor_must_be_read_only")
        return cls(
            enabled=raw.get("enabled", False),
            use_llm=raw.get("use_llm", False),
            tone=tone,
            read_only=True,
        )


@dataclass(frozen=True)
class MonetativeAuditorConfig:
    enabled: bool = False

    @classmethod
    def from_agent_config(cls, config: Mapping[str, Any] | None) -> "MonetativeAuditorConfig":
        root = dict(config or {})
        finance = root.get("finance_auditor") or {}
        if not isinstance(finance, Mapping):
            raise ValueError("finance_auditor_config_invalid")
        raw = finance.get("monetative") or {}
        if not isinstance(raw, Mapping):
            raise ValueError("monetative_auditor_config_invalid")
        if set(raw) - {"enabled"}:
            raise ValueError("monetative_auditor_config_unknown_field")
        if "enabled" in raw and not isinstance(raw["enabled"], bool):
            raise ValueError("monetative_auditor_enabled_invalid")
        return cls(enabled=raw.get("enabled", False))


@dataclass(frozen=True)
class PredatoryDerivativesConfig:
    enabled: bool = False

    @classmethod
    def from_agent_config(cls, config: Mapping[str, Any] | None) -> "PredatoryDerivativesConfig":
        root = dict(config or {})
        finance = root.get("finance_auditor") or {}
        if not isinstance(finance, Mapping):
            raise ValueError("finance_auditor_config_invalid")
        raw = finance.get("predatory_derivatives") or {}
        if not isinstance(raw, Mapping):
            raise ValueError("predatory_derivatives_config_invalid")
        if set(raw) - {"enabled"}:
            raise ValueError("predatory_derivatives_config_unknown_field")
        if "enabled" in raw and not isinstance(raw["enabled"], bool):
            raise ValueError("predatory_derivatives_enabled_invalid")
        return cls(enabled=raw.get("enabled", False))
