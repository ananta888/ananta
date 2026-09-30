"""Configuration readers of the Source Control composition root.

Feature flags, rollout stage/policy and the server-side model catalog are
read from app config or environment only, never from request payloads.
"""

from __future__ import annotations

import os

from agent.services.model_catalog_service import CatalogQuery
from agent.services.source_control_rollout_policy import (
    SourceControlRolloutConfiguration,
    SourceControlRolloutPolicy,
    SourceControlRolloutStage,
)


def public_remote_feature_enabled(app) -> bool:
    value = app.config.get(
        "SOURCE_CONTROL_PUBLIC_REMOTES_ENABLED",
        os.environ.get(
            "ANANTA_SOURCE_CONTROL_PUBLIC_REMOTES_ENABLED",
            "false",
        ),
    )
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def server_models(app):
    """Use the existing Hub model-catalog composition, never request payloads."""

    with app.app_context():
        from agent.routes.config.providers import _model_catalog_service

        config = dict(app.config.get("AGENT_CONFIG", {}) or {})
        return _model_catalog_service().versioned_catalog(
            CatalogQuery(
                default_provider=str(
                    config.get("default_provider") or ""
                ),
                default_model=str(config.get("default_model") or ""),
                task_kind="code_review",
                timeout_seconds=3,
                cache_ttl_seconds=30,
            )
        ).models


def source_control_rollout_policy(app) -> SourceControlRolloutPolicy:
    raw_stage = str(
        app.config.get("SOURCE_CONTROL_ROLLOUT_STAGE")
        or os.environ.get("SOURCE_CONTROL_ROLLOUT_STAGE")
        or "GITHUB"
    ).strip()
    try:
        stage = (
            SourceControlRolloutStage(int(raw_stage))
            if raw_stage.isdigit()
            else SourceControlRolloutStage[raw_stage.upper()]
        )
    except (KeyError, ValueError) as exc:
        raise RuntimeError("source_control_rollout_stage_invalid") from exc
    shadow = configured_bool(
        app,
        "SOURCE_CONTROL_SHADOW_COMPARE_ENABLED",
        default=stage is SourceControlRolloutStage.SHADOW_READ_MODEL,
    )
    aliases = configured_bool(
        app,
        "SOURCE_CONTROL_LEGACY_ALIASES_ENABLED",
        default=stage is not SourceControlRolloutStage.LEGACY_DISABLED,
    )
    release_report = app.extensions.get(
        "source_control_release_gate_report"
    )
    production_release_allowed = bool(
        getattr(release_report, "release_allowed", False)
    )
    return SourceControlRolloutPolicy(
        SourceControlRolloutConfiguration(
            stage=stage,
            shadow_compare_enabled=shadow,
            legacy_aliases_enabled=aliases,
            production_release_allowed=production_release_allowed,
        )
    )


def configured_bool(app, name: str, *, default: bool) -> bool:
    value = app.config.get(name)
    if value is None:
        value = os.environ.get(name)
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name.lower()}_invalid")


__all__ = [
    "configured_bool",
    "public_remote_feature_enabled",
    "server_models",
    "source_control_rollout_policy",
]
