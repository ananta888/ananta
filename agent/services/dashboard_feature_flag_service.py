"""Fail-closed feature flags shared by dashboard and operator surfaces."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

DASHBOARD_FEATURE_FLAGS_SCHEMA = "ananta.dashboard-feature-flags.v1"
FEATURE_FLAG_KEYS = (
    "feature_angular_kanban_enabled",
    "feature_angular_model_dashboard_enabled",
    "feature_tui_kanban_enabled",
    "feature_tui_model_menu_enabled",
)


class DashboardFeatureFlagError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class DashboardFeatureFlags:
    angular_kanban: bool = False
    angular_model_dashboard: bool = False
    tui_kanban: bool = False
    tui_model_menu: bool = False

    @property
    def model_catalog_enabled(self) -> bool:
        return self.angular_model_dashboard or self.tui_model_menu

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": DASHBOARD_FEATURE_FLAGS_SCHEMA,
            "features": {
                "angular_kanban": self.angular_kanban,
                "angular_model_dashboard": self.angular_model_dashboard,
                "tui_kanban": self.tui_kanban,
                "tui_model_menu": self.tui_model_menu,
            },
        }


def resolve_dashboard_feature_flags(
    agent_config: Mapping[str, Any] | None,
    *,
    defaults: Mapping[str, Any] | None = None,
) -> DashboardFeatureFlags:
    config = agent_config if isinstance(agent_config, Mapping) else {}
    fallback = defaults if isinstance(defaults, Mapping) else {}

    def enabled(key: str) -> bool:
        value = config[key] if key in config else fallback.get(key, False)
        return value if type(value) is bool else False

    return DashboardFeatureFlags(
        angular_kanban=enabled("feature_angular_kanban_enabled"),
        angular_model_dashboard=enabled(
            "feature_angular_model_dashboard_enabled"
        ),
        tui_kanban=enabled("feature_tui_kanban_enabled"),
        tui_model_menu=enabled("feature_tui_model_menu_enabled"),
    )


def normalize_feature_flag_update(
    payload: Mapping[str, Any],
) -> dict[str, bool]:
    normalized: dict[str, bool] = {}
    for key in FEATURE_FLAG_KEYS:
        if key not in payload:
            continue
        value = payload[key]
        if type(value) is not bool:
            raise DashboardFeatureFlagError(f"invalid_{key}")
        normalized[key] = value
    return normalized


__all__ = [
    "DASHBOARD_FEATURE_FLAGS_SCHEMA",
    "FEATURE_FLAG_KEYS",
    "DashboardFeatureFlagError",
    "DashboardFeatureFlags",
    "normalize_feature_flag_update",
    "resolve_dashboard_feature_flags",
]
