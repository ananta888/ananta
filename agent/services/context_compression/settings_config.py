"""Compression adapter config from the ``CONTEXT_COMPRESSION_*`` settings (LCTX-003).

The hybrid orchestrator used to read ``settings.global_config["context_compression"]``,
a field that never existed, so the documented environment variables had no
effect. This maps them onto the keys ``ContextCompressionAdapter.from_config``
reads. Disabled by default (``CONTEXT_COMPRESSION_ENABLED=0``).
"""

from __future__ import annotations

from typing import Any


def compression_config_from_settings(settings: Any) -> dict[str, Any]:
    def value(name: str, default: Any = None) -> Any:
        return getattr(settings, f"context_compression_{name}", default)

    config = {
        "enabled": bool(value("enabled", False)),
        "mode": value("mode", "passthrough_with_metrics"),
        "adapter": value("adapter", "ananta_context_compression"),
        "target_reduction_percent": value("target_reduction_percent"),
        "max_input_tokens": value("max_input_tokens"),
        "min_quality_score": value("min_quality_score"),
        "fallback_on_quality_risk": value("fallback_on_quality_risk", True),
        "ccr_enabled": bool(value("ccr_enabled", False)),
        "ccr_store_path": value("ccr_path"),
        "ccr_ttl_hours": value("ccr_ttl_hours", 72),
        "external_headroom_enabled": bool(value("external_headroom_enabled", False)),
        "emit_events": bool(value("emit_events", True)),
    }
    return {key: item for key, item in config.items() if item is not None}  # unset: the adapter's own defaults
