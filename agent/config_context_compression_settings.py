"""Context-compression adapter settings composed into the application settings."""

from pydantic import Field
from pydantic_settings import BaseSettings


class ContextCompressionSettings(BaseSettings):
    """Configuration boundary for the context compression adapter (HCCA-002)."""

    # Main switch — off by default; set to passthrough_with_metrics to collect data without changing output
    context_compression_enabled: bool = Field(
        default=False,
        validation_alias="CONTEXT_COMPRESSION_ENABLED",
    )
    # Mode: off | passthrough_with_metrics | compress | compress_aggressive
    context_compression_mode: str = Field(
        default="passthrough_with_metrics",
        validation_alias="CONTEXT_COMPRESSION_MODE",
    )
    # Adapter: ananta_context_compression | external_headroom
    context_compression_adapter: str = Field(
        default="ananta_context_compression",
        validation_alias="CONTEXT_COMPRESSION_ADAPTER",
    )
    context_compression_target_reduction_percent: float = Field(
        default=35.0,
        validation_alias="CONTEXT_COMPRESSION_TARGET_REDUCTION_PERCENT",
    )
    context_compression_max_input_tokens: int = Field(
        default=1200,
        validation_alias="CONTEXT_COMPRESSION_MAX_INPUT_TOKENS",
    )
    context_compression_min_quality_score: float = Field(
        default=0.7,
        validation_alias="CONTEXT_COMPRESSION_MIN_QUALITY_SCORE",
    )
    context_compression_fallback_on_quality_risk: bool = Field(
        default=True,
        validation_alias="CONTEXT_COMPRESSION_FALLBACK_ON_QUALITY_RISK",
    )
    # CCR Store settings
    context_compression_ccr_enabled: bool = Field(
        default=True,
        validation_alias="CONTEXT_COMPRESSION_CCR_ENABLED",
    )
    context_compression_ccr_path: str = Field(
        default=".ananta/context-compression/ccr",
        validation_alias="CONTEXT_COMPRESSION_CCR_PATH",
    )
    context_compression_ccr_ttl_hours: int = Field(
        default=72,
        validation_alias="CONTEXT_COMPRESSION_CCR_TTL_HOURS",
    )
    # External Headroom adapter (off by default — requires headroom CLI installed)
    context_compression_external_headroom_enabled: bool = Field(
        default=False,
        validation_alias="CONTEXT_COMPRESSION_EXTERNAL_HEADROOM_ENABLED",
    )
    context_compression_external_headroom_transport: str = Field(
        default="cli",
        validation_alias="CONTEXT_COMPRESSION_EXTERNAL_HEADROOM_TRANSPORT",
    )
    # Observability
    context_compression_emit_events: bool = Field(
        default=True,
        validation_alias="CONTEXT_COMPRESSION_EMIT_EVENTS",
    )
