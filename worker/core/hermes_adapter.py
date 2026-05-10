from __future__ import annotations

from typing import Any

from agent.services.hermes_worker_profile import get_default_hermes_profile
from worker.core.execution_envelope import ExecutionEnvelope, WorkerResult, WorkerResultStatus, make_trace
from worker.core.hermes_adapter_config import HermesAdapterConfig
from worker.core.hermes_http_client import HermesClientConfig, HermesHttpClient


class HermesAdapter:
    """Governed Hermes adapter skeleton.

    Phase 1 methods return explicit degraded/not-implemented WorkerResults.
    """

    id = "hermes"

    def __init__(
        self,
        *,
        config: HermesAdapterConfig | None = None,
        client: HermesHttpClient | None = None,
    ) -> None:
        self.config = config or HermesAdapterConfig()
        self.profile = get_default_hermes_profile()
        self.client = client or HermesHttpClient(
            config=HermesClientConfig(
                base_url=self.config.base_url or "http://127.0.0.1:0",
                timeout_seconds=self.config.timeout_seconds,
                default_model=self.config.default_model or "hermes-default",
            )
        )

    def health(self) -> dict[str, Any]:
        if not self.config.enabled:
            return {"status": "disabled", "reason": "disabled_config", "adapter_id": self.id}
        return {"status": "ready", "reason": "configured", "adapter_id": self.id}

    def propose(self, envelope: ExecutionEnvelope) -> WorkerResult:
        return self._not_implemented(envelope, "propose_not_implemented")

    def review(self, envelope: ExecutionEnvelope) -> WorkerResult:
        return self._not_implemented(envelope, "review_not_implemented")

    def summarize(self, envelope: ExecutionEnvelope) -> WorkerResult:
        return self._not_implemented(envelope, "summarize_not_implemented")

    def patch_propose(self, envelope: ExecutionEnvelope) -> WorkerResult:
        return self._not_implemented(envelope, "patch_propose_not_implemented")

    def _not_implemented(self, envelope: ExecutionEnvelope, reason_code: str) -> WorkerResult:
        trace = make_trace(envelope)
        trace.append("hermes_degraded", reason_code=reason_code, adapter_id=self.id)
        return WorkerResult(
            task_id=envelope.task_id,
            status=WorkerResultStatus.degraded,
            summary=f"Hermes adapter degraded: {reason_code}",
            trace_bundle=trace,
            policy_observations=[reason_code],
            warnings=["hermes_not_implemented_phase1"],
            no_side_effects_confirmed=True,
        )
