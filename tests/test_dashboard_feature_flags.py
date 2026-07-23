from __future__ import annotations

import pytest

from agent.services.dashboard_feature_flag_service import (
    DashboardFeatureFlagError,
    normalize_feature_flag_update,
    resolve_dashboard_feature_flags,
)


def test_dashboard_feature_flags_default_false_and_invalid_values_fail_closed():
    flags = resolve_dashboard_feature_flags(
        {
            "feature_angular_kanban_enabled": "true",
            "feature_tui_model_menu_enabled": 1,
        }
    )

    assert flags.as_dict()["features"] == {
        "angular_kanban": False,
        "angular_model_dashboard": False,
        "tui_kanban": False,
        "tui_model_menu": False,
    }
    assert flags.model_catalog_enabled is False


def test_dashboard_feature_flags_allow_independent_activation():
    flags = resolve_dashboard_feature_flags(
        {
            "feature_angular_kanban_enabled": True,
            "feature_tui_model_menu_enabled": True,
        }
    )

    assert flags.angular_kanban is True
    assert flags.angular_model_dashboard is False
    assert flags.tui_kanban is False
    assert flags.tui_model_menu is True
    assert flags.model_catalog_enabled is True


def test_feature_flag_update_requires_json_booleans():
    assert normalize_feature_flag_update(
        {"feature_tui_kanban_enabled": True, "unrelated": "kept"}
    ) == {"feature_tui_kanban_enabled": True}

    with pytest.raises(
        DashboardFeatureFlagError,
        match="invalid_feature_tui_kanban_enabled",
    ):
        normalize_feature_flag_update(
            {"feature_tui_kanban_enabled": "true"}
        )
