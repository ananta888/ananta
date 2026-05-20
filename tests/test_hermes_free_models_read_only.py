from __future__ import annotations

from worker.core.context_resolver import ContextBlock
from worker.core.execution_envelope import CapabilityGrant, ExecutionEnvelope
from worker.core.hermes_adapter import HermesAdapter
from worker.core.hermes_adapter_config import HermesAdapterConfig


def _env() -> ExecutionEnvelope:
    return ExecutionEnvelope(
        task_id="t-ro",
        actor_ref="hub:test",
        capability_grant=CapabilityGrant(capabilities=["patch_propose"]),
        context_envelope_ref="ctx:ro",
        audit_correlation_id="audit:ro",
    )


class _PatchProposalClient:
    def health(self, *, api_key: str = "") -> dict[str, object]:
        return {"ok": True}

    def chat_completions(self, **_: object) -> dict[str, object]:
        return {
            "choices": [{
                "message": {
                    "content": '{"status":"success","artifact_type":"patch","summary":"proposal only","findings":[],"risks":[],"suggested_tests":[],"confidence":0.7,"requires_approval_for_apply":true,"no_side_effects_claimed":true,"touched_files":["mini_slugify.py"],"patch_unified_diff":"--- a/mini_slugify.py\\n+++ b/mini_slugify.py\\n@@ -1 +1 @@\\n-a\\n+b","shell_commands":["rm -rf /"]}'
                }
            }]
        }


def test_patch_propose_is_read_only_artifact_only() -> None:
    adapter = HermesAdapter(
        config=HermesAdapterConfig(
            enabled=True,
            feature_flag_enabled=True,
            base_url="http://localhost:1",
            default_model="qwen/qwen3-coder:free",
        ),
        client=_PatchProposalClient(),  # type: ignore[arg-type]
    )
    result = adapter.patch_propose(_env(), context_blocks=[ContextBlock("task", "1", "hub", content="propose patch")])
    assert result.status.value == "success"
    assert result.no_side_effects_confirmed is True
    assert result.artifacts[0].metadata["requires_approval_for_apply"] is True
    assert not hasattr(adapter, "apply_patch")
