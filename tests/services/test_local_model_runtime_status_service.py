import json
import subprocess
from datetime import UTC, datetime

import pytest

from agent.services.local_model_runtime_status_service import (
    HttpLocalResourceSnapshot,
    HttpLocalRuntimeProbe,
    LocalRuntimeStatusService,
    RuntimeProbeObservation,
    SystemLocalResourceSnapshot,
)
from agent.services.local_multi_model_runtime import (
    GiB,
    ResourceSnapshot,
    RuntimeResourceMeasurement,
    rtx3080_local_model_capabilities,
)
from ananta_contracts.local_model_runtime import RuntimeHealth, RuntimeReadiness


class _Response:
    status = 200

    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode()


def test_http_probe_separates_health_readiness_and_never_invokes_generation():
    requested = []

    def open_request(request, *, timeout):
        requested.append((request.full_url, timeout, request.headers))
        return _Response({"data": [{"id": "kat-coder-v2.5-dev"}]})

    capability = rtx3080_local_model_capabilities()[0]
    result = HttpLocalRuntimeProbe(
        token_resolver=lambda _runtime: "x" * 24,
        opener=open_request,
    ).probe(capability, timeout_seconds=0.2)

    assert result.health is RuntimeHealth.HEALTHY
    assert result.readiness is RuntimeReadiness.READY
    assert requested[0][0].endswith("/v1/models")
    assert "chat/completions" not in requested[0][0]


def test_status_service_projects_three_content_free_runtime_rows():
    class Probes:
        def probe(self, capability, *, timeout_seconds):
            assert timeout_seconds == 0.5
            return RuntimeProbeObservation(
                RuntimeHealth.HEALTHY,
                RuntimeReadiness.READY,
                "runtime_ready",
                (capability.model_id,),
            )

    class Resources:
        def snapshot(self):
            return ResourceSnapshot(
                10 * GiB,
                2 * GiB,
                48 * GiB,
                runtime_usage={
                    "kat": RuntimeResourceMeasurement(4 * GiB, 2 * GiB),
                    "needle": RuntimeResourceMeasurement(0, 64 * 1024 * 1024),
                },
                active_contexts={"kat": 32768, "lfm": 16384, "needle": 256},
            )

    snapshot = LocalRuntimeStatusService(
        probes=Probes(),
        resources=Resources(),
        timeout_seconds=0.5,
        clock=lambda: datetime(2026, 8, 27, tzinfo=UTC),
    ).snapshot(rtx3080_local_model_capabilities(), revision=3)

    wire = snapshot.to_wire()
    assert [item["runtime_id"] for item in wire["runtimes"]] == ["kat", "lfm", "needle"]
    assert next(item for item in wire["runtimes"] if item["runtime_id"] == "lfm")["effective_context"] == 16384
    assert next(item for item in wire["runtimes"] if item["runtime_id"] == "kat")["available_models"] == [
        "kat-coder-v2.5-dev"
    ]
    assert next(item for item in wire["runtimes"] if item["runtime_id"] == "needle")["candidate_only"] is True
    assert all(item["timeout_supported"] is True for item in wire["runtimes"])
    assert all(item["cancellation_supported"] is True for item in wire["runtimes"])
    kat_resources = next(item for item in wire["runtimes"] if item["runtime_id"] == "kat")["resources"]
    assert kat_resources["vram_used_bytes"] == 4 * GiB
    assert kat_resources["budget_status"] == "within_budget"
    assert (
        next(item for item in wire["runtimes"] if item["runtime_id"] == "lfm")["resources"]["budget_status"]
        == "unmeasured"
    )
    assert "prompt" not in json.dumps(wire)


def test_system_resource_adapter_uses_bounded_argument_vector():
    captured = {}

    def run(command, **kwargs):
        captured.update(command=command, kwargs=kwargs)
        return subprocess.CompletedProcess(command, 0, stdout="10240, 2048\n", stderr="")

    adapter = SystemLocalResourceSnapshot(command_runner=run)
    snapshot = adapter.snapshot()

    assert snapshot.total_vram_bytes == 10 * GiB
    assert snapshot.free_vram_bytes == 2 * GiB
    assert captured["command"][0] == "nvidia-smi"
    assert captured["kwargs"]["timeout"] == 3


def test_http_resource_adapter_requires_internal_url_and_closed_numeric_payload():
    def open_request(request, *, timeout):
        assert request.full_url == "http://host.docker.internal:8093/v1/resources"
        assert timeout == 3
        return _Response(
            {
                "total_vram_bytes": 10 * GiB,
                "free_vram_bytes": 2 * GiB,
                "available_ram_bytes": 48 * GiB,
                "runtime_usage": {
                    "kat": {"vram_used_bytes": 4 * GiB, "ram_used_bytes": 2 * GiB},
                },
                "effective_contexts": {"kat": 32768, "lfm": 16384, "needle": 256},
            }
        )

    snapshot = HttpLocalResourceSnapshot(
        "http://host.docker.internal:8093",
        token="x" * 24,
        opener=open_request,
    ).snapshot()

    assert snapshot == ResourceSnapshot(
        10 * GiB,
        2 * GiB,
        48 * GiB,
        runtime_usage={"kat": RuntimeResourceMeasurement(4 * GiB, 2 * GiB)},
        active_contexts={"kat": 32768, "lfm": 16384, "needle": 256},
    )
    with pytest.raises(ValueError, match="url_invalid"):
        HttpLocalResourceSnapshot("https://example.com", token="x" * 24)

    assert HttpLocalResourceSnapshot(
        "http://172.17.0.1:8093",
        token="x" * 24,
        opener=open_request,
    )
