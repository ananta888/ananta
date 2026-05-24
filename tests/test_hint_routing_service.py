from __future__ import annotations

from agent.services.hint_routing_service import get_hint_routing_service


def _cfg() -> dict:
    return {
        "hint_routing": {
            "enabled": True,
            "cloud_allowed_hints": ["hint:planning", "hint:code", "hint:summarize"],
            "local_only_hints": ["hint:context_compaction", "hint:cheap_classify", "hint:local_embedding"],
            "unknown_hint_action": "mark_unavailable",
        },
        "planning_policy": {
            "accepted_output_formats": ["json", "markdown"],
            "runtime_profiles": {
                "context_compaction": {"preferred_output_format": "markdown_sections"},
                "planning": {"preferred_output_format": "strict_json"},
            },
        },
    }


def test_context_compaction_hint_is_local_only():
    out = get_hint_routing_service().resolve(
        hint="hint:context_compaction",
        cfg=_cfg(),
        cloud_allowed=True,
    )
    assert out["available"] is True
    assert out["llm_scope"] == "local_only"


def test_planning_hint_can_use_cloud_when_allowed():
    out = get_hint_routing_service().resolve(
        hint="hint:planning",
        cfg=_cfg(),
        cloud_allowed=True,
        provider="openai",
        model="gpt-4.1-mini",
    )
    assert out["available"] is True
    assert out["llm_scope"] == "external_cloud_allowed"
    assert out["chain"]["policy_version"] == "routing-decision-v1"


def test_local_only_policy_blocks_remote_provider():
    out = get_hint_routing_service().resolve(
        hint="hint:local_embedding",
        cfg=_cfg(),
        cloud_allowed=False,
        provider="openai",
        model="gpt-4.1-mini",
    )
    assert out["available"] is False
    assert out["reason"] == "provider_blocked_by_local_only_policy"


def test_unknown_hint_marks_unavailable():
    out = get_hint_routing_service().resolve(
        hint="hint:does_not_exist",
        cfg=_cfg(),
    )
    assert out["available"] is False
    assert out["reason"].startswith("unknown_hint:")


def test_context_compaction_returns_output_format_profile():
    out = get_hint_routing_service().resolve(
        hint="hint:context_compaction",
        cfg=_cfg(),
        provider="lmstudio",
    )
    profile = out["output_format_profile"]
    assert isinstance(profile, dict)
    assert profile.get("preferred_output_format") in {"markdown", "json", "yaml"}

