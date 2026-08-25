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
        "model_catalog_v2": False,
        "model_routing_editor": False,
        "legacy_model_picker_deprecation": False,
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


def test_model_rollout_flags_fall_back_compatibly_but_can_be_split():
    compatible = resolve_dashboard_feature_flags({
        "feature_angular_model_dashboard_enabled": True,
    })
    assert compatible.model_catalog_v2 is True
    assert compatible.model_routing_editor is True
    assert compatible.legacy_model_picker_deprecation is False

    staged = resolve_dashboard_feature_flags({
        "feature_angular_model_dashboard_enabled": True,
        "feature_model_catalog_v2_enabled": True,
        "feature_model_routing_editor_enabled": False,
        "feature_legacy_model_picker_deprecation_enabled": True,
    })
    assert staged.model_catalog_v2 is True
    assert staged.model_routing_editor is False
    assert staged.legacy_model_picker_deprecation is True


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

    with pytest.raises(
        DashboardFeatureFlagError,
        match="invalid_feature_model_routing_editor_enabled",
    ):
        normalize_feature_flag_update(
            {"feature_model_routing_editor_enabled": 1}
        )
