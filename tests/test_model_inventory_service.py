from __future__ import annotations

import threading
import time

from agent.services.model_inventory_service import (
    ModelInventoryService,
    ModelInventorySnapshot,
)
from agent.services.provider_catalog_inventory_adapter import (
    ProviderCatalogModelInventoryAdapter,
)
from ananta_contracts.model_catalog import (
    ModelAvailability,
    ModelCapabilityClaim,
    ModelHealth,
    ModelInventoryDescriptor,
    ModelMetadataEvidence,
    ModelRuntime,
    ModelSourceKind,
    ModelCatalog,
    ModelSummary,
    ModelMetadataFact,
)


def _model(
    model_id: str,
    source_id: str,
    *,
    claim: str = "supported",
) -> ModelInventoryDescriptor:
    return ModelInventoryDescriptor(
        provider_id="lmstudio",
        model_id=model_id,
        executor_id="api:lmstudio",
        display_name=model_id,
        runtime=ModelRuntime.LOCAL,
        source_ids=(source_id,),
        source_kinds=(ModelSourceKind.DISCOVERED,),
        availability=ModelAvailability.AVAILABLE,
        health=ModelHealth.HEALTHY,
        listing_supported=True,
        capabilities=(ModelCapabilityClaim(
            capability_id="tools",
            value=claim,
            evidence=ModelMetadataEvidence.DETECTED,
        ),),
    )


class _Adapter:
    source_kind = ModelSourceKind.DISCOVERED
    cache_ttl_seconds = 60.0
    stale_after_seconds = 600.0

    def __init__(self, source_id: str, models=()) -> None:
        self.source_id = source_id
        self.models = tuple(models)
        self.calls = 0
        self.fail = False
        self.failure_message = "provider_unavailable"
        self.wait: threading.Event | None = None

    def collect(self, *, force_refresh: bool = False):
        self.calls += 1
        if self.wait is not None:
            self.wait.wait(timeout=2)
        if self.fail:
            raise RuntimeError(self.failure_message)
        return ModelInventorySnapshot(models=self.models)


def test_source_failure_preserves_stale_snapshot_and_other_sources():
    first = _Adapter("source:first", (_model("first", "source:first"),))
    second = _Adapter("source:second", (_model("second", "source:second"),))
    service = ModelInventoryService((first, second))
    initial = service.catalog()
    first.fail = True
    degraded = service.catalog(force_refresh=True)

    assert [item.model_id for item in initial.models] == ["first", "second"]
    assert [item.model_id for item in degraded.models] == ["first", "second"]
    first_status = next(
        item for item in degraded.sources if item.source_id == "source:first"
    )
    assert first_status.status == "degraded"
    assert first_status.from_cache is True
    assert first_status.reason_code == "provider_unavailable"
    assert degraded.partial is True


def test_parallel_force_refreshes_are_coalesced():
    adapter = _Adapter("source:slow", (_model("slow", "source:slow"),))
    service = ModelInventoryService((adapter,))
    service.catalog()
    adapter.wait = threading.Event()
    barrier = threading.Barrier(6)
    results = []

    def run():
        barrier.wait()
        results.append(service.catalog(force_refresh=True))

    threads = [threading.Thread(target=run) for _ in range(5)]
    for thread in threads:
        thread.start()
    barrier.wait()
    time.sleep(0.05)
    adapter.wait.set()
    for thread in threads:
        thread.join(timeout=2)

    assert len(results) == 5
    assert adapter.calls == 2


def test_deduplication_reports_conflicting_capability_evidence():
    detected = _Adapter("source:detected", (
        _model("same", "source:detected", claim="supported"),
    ))
    declared = _Adapter("source:declared", (
        _model("same", "source:declared", claim="unsupported"),
    ))
    catalog = ModelInventoryService((detected, declared)).catalog()

    assert len(catalog.models) == 1
    assert catalog.models[0].source_ids == (
        "source:declared", "source:detected"
    )
    assert catalog.models[0].capabilities[0].value == "unknown"
    assert catalog.models[0].conflicts == ("capability:tools",)


def test_deduplication_preserves_sources_and_surfaces_scalar_conflicts():
    first = _model("same", "source:first").model_copy(update={
        "context_window": 8192,
        "metadata_facts": (ModelMetadataFact(
            fact_id="model_role", value="coder",
            evidence=ModelMetadataEvidence.DECLARED, source_id="source:first",
        ),),
    })
    second = _model("same", "source:second").model_copy(update={
        "context_window": 32768,
        "metadata_facts": (ModelMetadataFact(
            fact_id="model_role", value="chat",
            evidence=ModelMetadataEvidence.DETECTED, source_id="source:second",
        ),),
    })

    merged = ModelInventoryService((
        _Adapter("source:first", (first,)),
        _Adapter("source:second", (second,)),
    )).catalog().models[0]

    assert merged.context_window is None
    assert "context_window" in merged.conflicts
    assert "metadata:model_role" in merged.conflicts
    assert merged.metadata_facts == ()


def test_source_exception_text_is_redacted_unless_it_is_a_reason_code():
    adapter = _Adapter("source:secret")
    adapter.fail = True
    adapter.failure_message = "supersecrettoken"
    catalog = ModelInventoryService((adapter,)).catalog()

    assert catalog.sources[0].reason_code == (
        "model_inventory_source_error:RuntimeError"
    )
    assert "supersecrettoken" not in catalog.model_dump_json()


def test_inventory_handles_five_thousand_models_with_stable_revision():
    models = tuple(
        _model(f"model-{index}", "source:large") for index in range(5000)
    )
    adapter = _Adapter("source:large", models)
    service = ModelInventoryService((adapter,))
    first = service.catalog()
    second = service.catalog()

    assert len(first.models) == 5000
    assert second.catalog_revision == first.catalog_revision
    assert adapter.calls == 1


def test_remote_hub_inventory_marks_declared_trust_and_hop_limit_without_url():
    adapter = ProviderCatalogModelInventoryAdapter(
        lambda _force: ModelCatalog(models=(ModelSummary(
            provider_id="remote-hub-a",
            runtime=ModelRuntime.REMOTE,
            model_id="remote-model",
            display_name="Remote Model",
            availability=ModelAvailability.AVAILABLE,
            health=ModelHealth.HEALTHY,
        ),)),
        lambda: {
            "remote-hub-a": {"trust_level": "private", "max_hops": 2}
        },
    )

    model = adapter.collect().models[0]
    facts = {item.fact_id: item.value for item in model.metadata_facts}

    assert model.source_kinds == (ModelSourceKind.REMOTE,)
    assert facts == {"remote_trust_level": "private", "remote_hop_limit": "2"}
    assert "url" not in model.model_dump_json().lower()
