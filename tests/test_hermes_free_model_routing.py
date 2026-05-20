from __future__ import annotations

from worker.core.context_resolver import ContextBlock
from worker.core.execution_envelope import CapabilityGrant, ExecutionEnvelope
from worker.core.hermes_adapter import HermesAdapter
from worker.core.hermes_adapter_config import HermesAdapterConfig


def _env(capabilities: list[str], preferred_model: str | None = None) -> ExecutionEnvelope:
    return ExecutionEnvelope(
        task_id="t-routing",
        actor_ref="hub:test",
        capability_grant=CapabilityGrant(capabilities=capabilities),
        context_envelope_ref="ctx:route",
        audit_correlation_id="audit:route",
        model_policy={"preferred_model": preferred_model} if preferred_model else {},
    )


class _Client:
    def health(self, *, api_key: str = "") -> dict[str, object]:
        return {"ok": True}

    def chat_completions(self, **_: object) -> dict[str, object]:
        return {
            "choices": [{
                "message": {
                    "content": '{"status":"success","artifact_type":"plan","summary":"ok","findings":[],"risks":[],"suggested_tests":[],"confidence":0.8,"requires_approval_for_apply":false,"no_side_effects_claimed":true}'
                }
            }]
        }


def test_adapter_uses_task_kind_specific_model_and_emits_metadata() -> None:
    cfg = HermesAdapterConfig(
        enabled=True,
        feature_flag_enabled=True,
        base_url="http://localhost:1",
        default_model="z-ai/glm-4.5-air:free",
        task_kind_models={"plan_only": "z-ai/glm-4.5-air:free", "review": "qwen/qwen3-coder:free"},
        fallback_free_models={"default": ["z-ai/glm-4.5-air:free"]},
        model_selection_policy={"require_free_model_suffix": True},
    )
    adapter = HermesAdapter(config=cfg, client=_Client())  # type: ignore[arg-type]
    result = adapter.plan_only(
        _env(["planning"]),
        context_blocks=[ContextBlock("task", "1", "hub", content="plan")],
    )
    assert result.status.value == "success"
    metadata = result.artifacts[0].metadata
    assert metadata["model"] == "z-ai/glm-4.5-air:free"
    assert metadata["model_selection_source"] in {"task_kind_models", "default_model"}
    assert metadata["read_only_enforced"] is True


def test_adapter_blocks_command_execute_for_hermes() -> None:
    cfg = HermesAdapterConfig(
        enabled=True,
        feature_flag_enabled=True,
        base_url="http://localhost:1",
        default_model="z-ai/glm-4.5-air:free",
    )
    adapter = HermesAdapter(config=cfg, client=_Client())  # type: ignore[arg-type]
    denied = adapter._execute_mode("command_execute", _env(["planning"]), [ContextBlock("task", "1", "hub", content="do shell")])
    assert denied.status.value == "denied"
