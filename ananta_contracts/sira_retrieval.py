"""Runtime-neutral configuration contract for corpus-discriminative retrieval."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


class SiraMode(str, Enum):
    OFF = "off"
    SHADOW = "shadow"
    ON_DEMAND = "on_demand"
    PREFERRED = "preferred"
    REQUIRED = "required"


_ALLOWED_FIELDS = {
    "schema",
    "mode",
    "enrichment_model",
    "query_model",
    "rerank_model",
    "prompt_version",
    "temperature",
    "max_generated_terms",
    "max_term_length",
    "query_timeout_ms",
    "rerank_timeout_ms",
    "max_query_tokens",
    "rerank_top_n",
    "reranker_enabled",
    "minimum_term_confidence",
    "maximum_document_frequency_ratio",
    "minimum_baseline_margin",
    "local_models_only",
    "profile_version",
}


@dataclass(frozen=True, slots=True)
class SiraConfig:
    schema: str = "codecompass.sira-config.v1"
    mode: SiraMode = SiraMode.OFF
    enrichment_model: str = ""
    query_model: str = ""
    rerank_model: str = ""
    prompt_version: str = "sira-ananta-v1"
    temperature: float = 0.0
    max_generated_terms: int = 12
    max_term_length: int = 80
    query_timeout_ms: int = 4_000
    rerank_timeout_ms: int = 4_000
    max_query_tokens: int = 512
    rerank_top_n: int = 12
    reranker_enabled: bool = False
    minimum_term_confidence: float = 0.35
    maximum_document_frequency_ratio: float = 0.8
    minimum_baseline_margin: float = 0.2
    local_models_only: bool = True
    profile_version: str = "corpus-discriminative-lexical.v1"

    def __post_init__(self) -> None:
        if self.schema != "codecompass.sira-config.v1":
            raise ValueError("sira_config_schema_invalid")
        if not isinstance(self.mode, SiraMode):
            raise ValueError("sira_mode_invalid")
        if self.temperature != 0.0:
            raise ValueError("sira_temperature_must_be_zero")
        if not 1 <= self.max_generated_terms <= 64:
            raise ValueError("sira_max_generated_terms_invalid")
        if not 2 <= self.max_term_length <= 256:
            raise ValueError("sira_max_term_length_invalid")
        if not 100 <= self.query_timeout_ms <= 120_000:
            raise ValueError("sira_query_timeout_invalid")
        if not 100 <= self.rerank_timeout_ms <= 120_000:
            raise ValueError("sira_rerank_timeout_invalid")
        if not 32 <= self.max_query_tokens <= 8_192:
            raise ValueError("sira_query_token_budget_invalid")
        if not 1 <= self.rerank_top_n <= 100:
            raise ValueError("sira_rerank_top_n_invalid")
        if not 0.0 <= self.minimum_term_confidence <= 1.0:
            raise ValueError("sira_minimum_term_confidence_invalid")
        if not 0.0 < self.maximum_document_frequency_ratio <= 1.0:
            raise ValueError("sira_maximum_df_ratio_invalid")
        if not 0.0 <= self.minimum_baseline_margin <= 1.0:
            raise ValueError("sira_minimum_baseline_margin_invalid")
        for value, reason in (
            (self.prompt_version, "sira_prompt_version_required"),
            (self.profile_version, "sira_profile_version_required"),
        ):
            if not str(value).strip() or len(str(value)) > 256:
                raise ValueError(reason)
        if self.reranker_enabled and not self.rerank_model:
            raise ValueError("sira_rerank_model_required")
        if self.mode in {SiraMode.PREFERRED, SiraMode.REQUIRED, SiraMode.SHADOW} and not self.query_model:
            raise ValueError("sira_query_model_required")

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> "SiraConfig":
        payload: dict[str, Any] = dict(raw or {})
        unknown = sorted(set(payload).difference(_ALLOWED_FIELDS))
        if unknown:
            raise ValueError(f"sira_config_unknown_fields:{','.join(unknown)}")
        if "mode" in payload:
            try:
                payload["mode"] = SiraMode(str(payload["mode"]).strip().lower())
            except ValueError as exc:
                raise ValueError("sira_mode_invalid") from exc
        return cls(**payload)

    def safe_dict(self) -> dict[str, Any]:
        """Return trace-safe configuration without credentials or endpoints."""

        return {
            "schema": self.schema,
            "mode": self.mode.value,
            "enrichment_model": self.enrichment_model,
            "query_model": self.query_model,
            "rerank_model": self.rerank_model,
            "prompt_version": self.prompt_version,
            "temperature": self.temperature,
            "max_generated_terms": self.max_generated_terms,
            "max_term_length": self.max_term_length,
            "query_timeout_ms": self.query_timeout_ms,
            "rerank_timeout_ms": self.rerank_timeout_ms,
            "max_query_tokens": self.max_query_tokens,
            "rerank_top_n": self.rerank_top_n,
            "reranker_enabled": self.reranker_enabled,
            "minimum_term_confidence": self.minimum_term_confidence,
            "maximum_document_frequency_ratio": self.maximum_document_frequency_ratio,
            "minimum_baseline_margin": self.minimum_baseline_margin,
            "local_models_only": self.local_models_only,
            "profile_version": self.profile_version,
        }


__all__ = ["SiraConfig", "SiraMode"]
