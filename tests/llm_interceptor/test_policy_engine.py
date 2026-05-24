from __future__ import annotations

from agent.services.llm_interceptor.policy_engine import PolicyEngine


def _env(task_kind: str, model: str = "m-local") -> dict:
    return {"task_metadata": {"task_kind": task_kind}, "model": model}


def test_local_allowed_request():
    out = PolicyEngine({"local_context_default": "allowed_by_context_gate"}).evaluate(
        envelope=_env("coding"),
        upstream_trust_level="local",
    )
    assert out.action == "allow"


def test_cloud_secretish_model_denied():
    out = PolicyEngine({}).evaluate(
        envelope=_env("coding", model="secret-model"),
        upstream_trust_level="cloud",
    )
    assert out.action == "deny"


def test_cloud_request_reduced_context():
    out = PolicyEngine({"cloud_context_default": "redacted_minimal"}).evaluate(
        envelope=_env("coding"),
        upstream_trust_level="cloud",
    )
    assert out.action == "reduce_context"
    assert out.context_mode == "redacted_minimal"

