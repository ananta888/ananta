from __future__ import annotations

from agent.providers.provenance import build_provider_provenance


def test_build_provider_provenance_is_deterministic_for_same_input() -> None:
    first = build_provider_provenance(
        provider_id="mock-provider",
        provider_family="workflow",
        provider_version="1.2.3",
        external_ref="run-123",
        source_ref="workflow://notify",
        run_id="run-123",
        trace_id="trace-1",
    )
    second = build_provider_provenance(
        provider_id="mock-provider",
        provider_family="workflow",
        provider_version="1.2.3",
        external_ref="run-123",
        source_ref="workflow://notify",
        run_id="run-123",
        trace_id="trace-1",
    )
    assert first == second


def test_build_provider_provenance_handles_missing_optional_fields() -> None:
    payload = build_provider_provenance(provider_id="provider-x", provider_family="worker_execution")
    assert payload == {
        "provider_id": "provider-x",
        "provider_family": "worker_execution",
    }
