from __future__ import annotations

from agent.services.sfu_broadcast_feature_policy import (
    SFB_BROADCAST_FEATURE_DEFINITIONS,
    resolve_sfu_broadcast_feature_flags,
)
from agent.services.semantic_media_feature_flags import SEMANTIC_MEDIA_FEATURE_CATALOG


def test_broadcast_catalog_and_semantic_catalog_agree() -> None:
    catalog_keys = [item.key for item in SEMANTIC_MEDIA_FEATURE_CATALOG]
    for definition in SFB_BROADCAST_FEATURE_DEFINITIONS:
        assert definition.key in catalog_keys
        assert definition.owner == "hub"
        assert definition.default is False


def test_resolve_broadcast_feature_flags_is_fail_closed() -> None:
    raw = {"semantic_media_broadcast": "enabled", "semantic_media_receiver_groups": "1"}
    resolved = resolve_sfu_broadcast_feature_flags(raw)
    assert resolved["semantic_media_broadcast"] is False
    assert resolved["semantic_media_receiver_groups"] is False


def test_resolve_broadcast_feature_flags_applies_dependencies() -> None:
    resolved = resolve_sfu_broadcast_feature_flags(
        {
            "semantic_media_broadcast": "true",
            "semantic_media_receiver_groups": "true",
            "semantic_media_fleet_admission": "true",
            "semantic_media_turn_cost_controls": "true",
        },
    )
    assert resolved["semantic_media_broadcast"]
    assert resolved["semantic_media_receiver_groups"]
    assert resolved["semantic_media_fleet_admission"]
    assert resolved["semantic_media_turn_cost_controls"]
