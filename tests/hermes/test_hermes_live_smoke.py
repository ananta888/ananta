from __future__ import annotations

import os

import pytest

from worker.core.context_resolver import ContextBlock
from worker.core.execution_envelope import CapabilityGrant, ExecutionEnvelope
from worker.core.hermes_adapter import HermesAdapter
from worker.core.hermes_adapter_config import HermesAdapterConfig


@pytest.mark.skipif(
    os.getenv("HERMES_LIVE_SMOKE", "").lower() not in {"1", "true", "yes"},
    reason="set HERMES_LIVE_SMOKE=1 to run live smoke",
)
def test_live_hermes_smoke_plan_only() -> None:
    base_url = os.getenv("HERMES_BASE_URL", "").strip()
    model = os.getenv("HERMES_MODEL", "").strip()
    if not base_url or not model:
        pytest.fail("missing HERMES_BASE_URL or HERMES_MODEL for live smoke")

    adapter = HermesAdapter(
        config=HermesAdapterConfig(
            enabled=True,
            feature_flag_enabled=True,
            base_url=base_url,
            api_key_env=os.getenv("HERMES_API_KEY_ENV", "HERMES_API_KEY"),
            default_model=model,
            max_context_chars=1200,
            timeout_seconds=10,
            max_retries=0,
            strict_json_required=True,
        )
    )
    health = adapter.health()
    if health.get("status") not in {"ready", "unauthorized"}:
        pytest.fail(f"live health failed: {health}")

    env = ExecutionEnvelope(
        task_id="live-smoke-plan",
        actor_ref="hub:live-smoke",
        capability_grant=CapabilityGrant(capabilities=["planning"]),
        context_envelope_ref="ctx:live",
        audit_correlation_id="audit:live",
    )
    result = adapter.plan_only(
        env,
        context_blocks=[ContextBlock("task", "ctx1", "hub", content="Return a tiny three-step plan as strict JSON.")],
    )
    assert result.status.value in {"success", "failed", "denied", "degraded"}
