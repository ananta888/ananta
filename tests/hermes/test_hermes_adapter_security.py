"""HF-T025: No-network/no-file side-effect regression tests.
HF-T027: Cloud-sensitive context matrix tests.
"""
from __future__ import annotations

import pytest

from worker.core.context_resolver import ContextBlock, ContextSensitivity
from worker.core.execution_envelope import CapabilityGrant, ExecutionEnvelope
from worker.core.hermes_adapter import HermesAdapter
from worker.core.hermes_adapter_config import HermesAdapterConfig
from worker.core.hermes_http_client import HermesClientError


# ── helpers ──────────────────────────────────────────────────────────────────

def _cfg(base_url: str = "http://localhost:1", **kw) -> HermesAdapterConfig:
    return HermesAdapterConfig(enabled=True, feature_flag_enabled=True, base_url=base_url, default_model="m", **kw)


def _env(
    *,
    capabilities: list[str] | None = None,
    cloud_allowed: bool = True,
    allow_all_network: bool = False,
) -> ExecutionEnvelope:
    return ExecutionEnvelope(
        task_id="t-sec",
        actor_ref="test",
        capability_grant=CapabilityGrant(capabilities=capabilities or ["planning"]),
        context_envelope_ref="ctx:sec",
        audit_correlation_id="sec-test",
        model_policy={"cloud_allowed": cloud_allowed},
        network_scope={"allow_all": allow_all_network},
    )


_SAFE_BLOCK = ContextBlock("task", "t", "hub", sensitivity=ContextSensitivity.public, content="analyze this")
_SECRET_BLOCK = ContextBlock("task", "s", "hub", sensitivity=ContextSensitivity.secret, content="API_KEY=sk-proj-abc123")
_CONFIDENTIAL_BLOCK = ContextBlock("task", "c", "hub", sensitivity=ContextSensitivity.customer_confidential, content="confidential data")


class _CapturingClient:
    """Tracks calls; raises if network would actually be attempted."""

    def __init__(self, *, raise_on_call: bool = False) -> None:
        self.calls: list[str] = []
        self.raise_on_call = raise_on_call

    def health(self, *, api_key: str = "") -> dict:
        self.calls.append("health")
        return {"ok": True}

    def chat_completions(self, **kwargs) -> dict:
        self.calls.append("chat_completions")
        if self.raise_on_call:
            raise AssertionError("network call must not be made")
        return {
            "choices": [{
                "message": {
                    "content": '{"status":"success","artifact_type":"plan","summary":"ok","findings":[],"risks":[],"suggested_tests":[],"confidence":0.8,"requires_approval_for_apply":false,"no_side_effects_claimed":true}'
                }
            }]
        }


# ── HF-T025: No-network / no-file side effect regressions ────────────────────

def test_adapter_has_no_apply_patch_method() -> None:
    adapter = HermesAdapter(config=_cfg())
    assert not hasattr(adapter, "apply_patch")
    assert not hasattr(adapter, "execute_patch")


def test_adapter_has_no_write_file_method() -> None:
    adapter = HermesAdapter(config=_cfg())
    assert not hasattr(adapter, "write_file")
    assert not hasattr(adapter, "create_file")


def test_adapter_has_no_shell_execute_method() -> None:
    adapter = HermesAdapter(config=_cfg())
    assert not hasattr(adapter, "shell_execute")
    assert not hasattr(adapter, "run_command")


def test_no_network_call_when_disabled() -> None:
    client = _CapturingClient(raise_on_call=True)
    adapter = HermesAdapter(config=HermesAdapterConfig(enabled=False), client=client)  # type: ignore[arg-type]
    result = adapter.propose(_env())
    assert result.status.value == "degraded"
    assert "chat_completions" not in client.calls


def test_no_network_call_when_feature_flag_off() -> None:
    client = _CapturingClient(raise_on_call=True)
    adapter = HermesAdapter(
        config=HermesAdapterConfig(enabled=True, feature_flag_enabled=False, base_url="http://localhost:1", default_model="m"),
        client=client,  # type: ignore[arg-type]
    )
    result = adapter.plan_only(_env(capabilities=["planning"]), context_blocks=[_SAFE_BLOCK])
    assert result.status.value == "degraded"
    assert "chat_completions" not in client.calls


def test_no_network_call_for_denied_capability() -> None:
    client = _CapturingClient(raise_on_call=True)
    adapter = HermesAdapter(config=_cfg(), client=client)  # type: ignore[arg-type]
    # summarize requires "summarize" capability; without it → denied before any network call
    result = adapter.summarize(_env(capabilities=["planning"]), context_blocks=[_SAFE_BLOCK])
    assert result.status.value == "denied"
    assert "chat_completions" not in client.calls


def test_no_network_call_for_blocked_task_kind() -> None:
    client = _CapturingClient(raise_on_call=True)
    adapter = HermesAdapter(config=_cfg(), client=client)  # type: ignore[arg-type]
    # patch_apply is in blocked_task_kinds — adapter must deny and never call network
    # We call _execute_mode directly to test the block
    env = _env(capabilities=["planning"])
    result = adapter._execute_mode("patch_apply", env, [_SAFE_BLOCK])
    assert result.status.value == "denied"
    assert "chat_completions" not in client.calls


def test_worker_result_no_side_effects_confirmed_is_true_on_success() -> None:
    client = _CapturingClient()
    adapter = HermesAdapter(config=_cfg(), client=client)  # type: ignore[arg-type]
    result = adapter.plan_only(_env(capabilities=["planning"]), context_blocks=[_SAFE_BLOCK])
    assert result.status.value == "success"
    assert result.no_side_effects_confirmed is True


def test_worker_result_no_side_effects_confirmed_is_true_on_failure() -> None:
    class _BadClient:
        def chat_completions(self, **_) -> dict:
            raise HermesClientError(code="hermes_server_error", detail="x")
        def health(self, **_) -> dict:
            return {"ok": True}

    adapter = HermesAdapter(config=_cfg(max_retries=0), client=_BadClient())  # type: ignore[arg-type]
    result = adapter.plan_only(_env(capabilities=["planning"]), context_blocks=[_SAFE_BLOCK])
    assert result.no_side_effects_confirmed is True


# ── HF-T027: Cloud × sensitive context × cloud_allowed matrix ────────────────

@pytest.mark.parametrize(
    "base_url, cloud_allowed_env, cloud_allowed_pol, context_blocks, expected",
    [
        # Local endpoint — sensitive ok, cloud_allowed irrelevant
        ("http://localhost:1", False, True, [_SECRET_BLOCK], "success"),
        ("http://localhost:1", False, False, [_SECRET_BLOCK], "success"),
        ("http://localhost:1", True, True, [_SECRET_BLOCK], "success"),
        # Cloud endpoint — cloud_allowed=False on config → denied
        ("https://api.hermes.example", False, True, [_SAFE_BLOCK], "denied"),
        # Cloud endpoint — cloud_allowed=True on config but False on envelope → denied
        ("https://api.hermes.example", True, False, [_SAFE_BLOCK], "denied"),
        # Cloud endpoint — both allowed, safe context → success
        ("https://api.hermes.example", True, True, [_SAFE_BLOCK], "success"),
        # Cloud endpoint — both allowed, but secret context → denied
        ("https://api.hermes.example", True, True, [_SECRET_BLOCK], "denied"),
        # Cloud endpoint — both allowed, but confidential context → denied
        ("https://api.hermes.example", True, True, [_CONFIDENTIAL_BLOCK], "denied"),
    ],
)
def test_cloud_sensitive_context_matrix(
    base_url: str,
    cloud_allowed_env: bool,
    cloud_allowed_pol: bool,
    context_blocks: list[ContextBlock],
    expected: str,
) -> None:
    client = _CapturingClient()
    cfg = HermesAdapterConfig(
        enabled=True,
        feature_flag_enabled=True,
        base_url=base_url,
        default_model="m",
        cloud_allowed=cloud_allowed_env,
    )
    adapter = HermesAdapter(config=cfg, client=client)  # type: ignore[arg-type]
    env = _env(capabilities=["planning"], cloud_allowed=cloud_allowed_pol)
    result = adapter.plan_only(env, context_blocks=context_blocks)
    assert result.status.value == expected, (
        f"base_url={base_url!r} cfg.cloud_allowed={cloud_allowed_env} "
        f"pol.cloud_allowed={cloud_allowed_pol} ctx={[b.sensitivity for b in context_blocks]} "
        f"→ got {result.status.value!r}, want {expected!r}"
    )


def test_mixed_safe_and_sensitive_blocks_on_cloud_denied() -> None:
    """Even one secret block denies cloud dispatch, even with safe blocks present."""
    client = _CapturingClient()
    cfg = HermesAdapterConfig(
        enabled=True, feature_flag_enabled=True,
        base_url="https://api.hermes.example", default_model="m", cloud_allowed=True,
    )
    adapter = HermesAdapter(config=cfg, client=client)  # type: ignore[arg-type]
    env = _env(capabilities=["planning"], cloud_allowed=True)
    result = adapter.plan_only(env, context_blocks=[_SAFE_BLOCK, _SECRET_BLOCK])
    assert result.status.value == "denied"
    assert "chat_completions" not in client.calls
