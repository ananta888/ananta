from datetime import UTC, datetime

from agent.services.local_model_runtime_inventory_adapter import LocalRuntimeModelInventoryAdapter
from agent.services.local_model_runtime_status_service import LocalRuntimeStatusService, RuntimeProbeObservation
from agent.services.local_multi_model_runtime import GiB, ResourceSnapshot, rtx3080_local_model_capabilities
from ananta_contracts.local_model_runtime import RuntimeHealth, RuntimeReadiness
from ananta_contracts.model_catalog import ModelAvailability, ModelHealth


class Resources:
    def snapshot(self):
        return ResourceSnapshot(10 * GiB, 2 * GiB, 64 * GiB)


class Probes:
    def probe(self, capability, *, timeout_seconds):
        return RuntimeProbeObservation(
            RuntimeHealth.HEALTHY,
            RuntimeReadiness.READY,
            "runtime_ready",
            (capability.model_id,),
        )


def test_observed_local_runtime_inventory_exposes_readiness_budgets_and_candidate_boundary():
    snapshot = LocalRuntimeStatusService(
        probes=Probes(),
        resources=Resources(),
        clock=lambda: datetime(2026, 8, 27, tzinfo=UTC),
    ).snapshot(rtx3080_local_model_capabilities())

    result = LocalRuntimeModelInventoryAdapter(lambda: snapshot).collect()
    by_model = {item.model_id: item for item in result.models}
    needle = by_model["needle-2-45m"]

    assert len(result.models) == 3
    assert all(item.availability is ModelAvailability.AVAILABLE for item in result.models)
    assert all(item.health is ModelHealth.HEALTHY for item in result.models)
    assert needle.executor_id == "candidate:needle_sidecar"
    facts = {item.fact_id: item.value for item in needle.metadata_facts}
    assert facts["candidate_only"] == "true"
    assert facts["orchestration_authority"] == "false"
