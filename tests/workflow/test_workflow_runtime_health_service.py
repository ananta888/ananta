from __future__ import annotations

from types import SimpleNamespace

from agent.services.workflow_runtime_health_service import (
    AgentDirectoryRuntimeHealthSource,
    ConfiguredEndpointRuntimeHealthSource,
    WorkflowRuntimeObservedHealthService,
)


class _Http:
    def __init__(self, *, status: int = 200, payload: dict | None = None) -> None:
        self.status = status
        self.payload = payload or {"status": "ready", "ready": True}

    def get_json(self, url: str, *, timeout_seconds: float):
        assert url == "http://temporal-worker:8088/ready"
        assert timeout_seconds <= 10
        return self.status, self.payload


def _agent(**overrides):
    value = {
        "role": "worker",
        "url": "http://worker-1:5000",
        "name": "worker-1",
        "capabilities": ["workflow.adapter.native"],
        "runtime_targets": [
            {
                "runtime_id": "ananta-native",
                "adapter_id": "native",
                "runtime_version": "1.0.0",
            }
        ],
        "registration_validated": True,
        "last_seen": 990.0,
        "status": "online",
    }
    value.update(overrides)
    return SimpleNamespace(**value)


def test_worker_directory_reports_only_fresh_validated_advertised_runtime() -> None:
    source = AgentDirectoryRuntimeHealthSource(
        load_agents=lambda: (_agent(),),
        stale_after_seconds=30,
        clock=lambda: 1000.0,
    )

    observation = source.observations("ananta-native")[0]

    assert observation.status == "ready"
    assert observation.reason_code == "runtime_health_worker_ready"
    assert observation.runtime_version == "1.0.0"
    assert source.observations("langgraph") == ()


def test_stale_invalid_and_degraded_workers_never_report_ready() -> None:
    source = AgentDirectoryRuntimeHealthSource(
        load_agents=lambda: (
            _agent(url="http://stale", last_seen=900.0),
            _agent(url="http://invalid", registration_validated=False),
            _agent(url="http://degraded", status="degraded"),
        ),
        stale_after_seconds=30,
        clock=lambda: 1000.0,
    )

    observations = source.observations("native")

    assert {item.status for item in observations} == {"unavailable", "degraded"}
    assert all(item.status != "ready" for item in observations)


def test_configured_temporal_endpoint_is_observed_without_framework_import() -> None:
    source = ConfiguredEndpointRuntimeHealthSource(
        endpoints={"temporal": "http://temporal-worker:8088/ready"},
        client=_Http(),
        clock=lambda: 1000.0,
    )
    service = WorkflowRuntimeObservedHealthService(
        sources=(source,),
        expected_versions={"temporal": "1.0.0"},
        clock=lambda: 1000.0,
    )

    health = service.get_health("temporal")

    assert health.status == "ready"
    assert health.reason_code == "runtime_health_endpoint_ready"


def test_missing_observation_and_version_mismatch_fail_closed() -> None:
    directory = AgentDirectoryRuntimeHealthSource(
        load_agents=lambda: (_agent(),),
        stale_after_seconds=30,
        clock=lambda: 1000.0,
    )
    service = WorkflowRuntimeObservedHealthService(
        sources=(directory,),
        expected_versions={"ananta-native": "2.0.0"},
        clock=lambda: 1000.0,
    )

    assert service.get_health("langgraph").status == "unavailable"
    mismatch = service.get_health("native")
    assert mismatch.status == "degraded"
    assert mismatch.reason_code == "runtime_health_version_mismatch"
