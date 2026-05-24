from __future__ import annotations

from agent.services.llm_interceptor.policy_engine import PolicyEngine


def _cfg() -> dict:
    return {
        "active_profile": "cloud_safe",
        "cloud_context_default": "redacted_minimal",
        "local_context_default": "allowed_by_context_gate",
        "profiles": {
            "local_dev": {"cloud_context_default": "redacted_minimal", "local_context_default": "allowed_by_context_gate"},
            "cloud_safe": {"cloud_context_default": "redacted_minimal", "local_context_default": "allowed_by_context_gate"},
            "high_risk": {"cloud_context_default": "none", "local_context_default": "minimal_local"},
        },
    }


def test_cloud_safe_profile_reduces_context():
    engine = PolicyEngine(_cfg())
    out = engine.evaluate(
        envelope={"task_metadata": {"task_kind": "coding", "policy_profile": "cloud_safe"}, "model": "m"},
        upstream_trust_level="cloud",
    )
    assert out.action == "reduce_context"
    assert out.context_mode == "redacted_minimal"


def test_high_risk_profile_changes_context_mode():
    engine = PolicyEngine(_cfg())
    out = engine.evaluate(
        envelope={"task_metadata": {"task_kind": "coding", "policy_profile": "high_risk"}, "model": "m"},
        upstream_trust_level="cloud",
    )
    assert out.context_mode == "none"

