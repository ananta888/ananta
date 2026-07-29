from __future__ import annotations

from dataclasses import dataclass, field
import json

import pytest

from agent.services.ml_intern_training_repository_port import MlInternTrainingPrincipal
from agent.services.unsloth_data_recipe_adapter import (
    DataRecipeRequest,
    DatasetSnapshot,
    UnslothDataRecipeAdapter,
)
from agent.services.unsloth_data_recipe_composition_service import (
    UnslothDataRecipeSubmissionService,
)
from agent.services.unsloth_evidence import ProvidedEvidenceRegistry
from agent.services.unsloth_model_catalog_service import (
    SqliteUnslothModelCatalogRegistry,
    UnslothModelImportResultHandler,
)
from agent.services.unsloth_model_source_adapter import (
    ModelSourceRequest,
    ModelSourceValidationError,
    UnslothModelSourceAdapter,
)
from agent.services.unsloth_task_port import HubUnslothTaskSubmissionAdapter
from ananta_contracts.model_catalog import ModelCatalog


SOURCE_ID = "SRC_supplied-model"
RUN_ID = "RUN_supplied-evaluation"
HASH_A = "a" * 64
HASH_B = "b" * 64


@dataclass
class _Queue:
    calls: list[dict] = field(default_factory=list)

    def ingest_task(self, **kwargs):
        self.calls.append(kwargs)


@dataclass
class _Repository:
    values: dict[str, object] = field(default_factory=dict)

    def get_by_id(self, task_id: str):
        return self.values.get(task_id)


@dataclass
class _Audit:
    calls: list[dict] = field(default_factory=list)

    def record(self, **kwargs):
        self.calls.append(kwargs)


class _Datasets:
    def get_snapshot(self, *, tenant_id: str, dataset_id: str):
        return DatasetSnapshot(
            dataset_id=dataset_id,
            tenant_id=tenant_id,
            dataset_hash=HASH_A,
            dataset_ref="tenants/tenant-a/train.jsonl",
            dataset_partition_sha256=HASH_B,
            state="approved",
            secret_scan_state="passed",
            pii_state="clear",
            license_state="approved",
            row_count=4,
        )


def _evidence() -> ProvidedEvidenceRegistry:
    return ProvidedEvidenceRegistry(source_ids=[SOURCE_ID], run_ids=[RUN_ID])


def test_hub_task_adapter_queues_without_executing_or_orchestrating_workers() -> None:
    queue = _Queue()
    adapter = HubUnslothTaskSubmissionAdapter(
        task_queue=queue,
        task_repository=_Repository(),
    )

    task_id = adapter.submit(
        task_type="ml.model.import",
        tenant_id="tenant-a",
        payload={"schema_version": 2},
        idempotency_key=HASH_A,
    )

    assert task_id.startswith("unsloth-")
    assert queue.calls[0]["extra_fields"]["task_kind"] == "ml.model.import"
    context = queue.calls[0]["extra_fields"]["worker_execution_context"]["unsloth_task"]
    assert context["followup_task_creation_allowed"] is False


def test_remote_import_requires_both_license_and_network_opt_in() -> None:
    adapter = UnslothModelSourceAdapter(
        tasks=HubUnslothTaskSubmissionAdapter(
            task_queue=_Queue(),
            task_repository=_Repository(),
        ),
        audit=_Audit(),
        evidence=_evidence(),
    )
    base = dict(
        tenant_id="tenant-a",
        project_id="project-a",
        source_id=SOURCE_ID,
        kind="huggingface_snapshot",
        expected_sha256=HASH_A,
        model_id="org/model",
        revision="c" * 40,
        architecture="llama",
    )

    with pytest.raises(ModelSourceValidationError) as license_error:
        adapter.plan(ModelSourceRequest(**base, network_authorized=True))
    assert license_error.value.code == "model_import_license_not_approved"

    with pytest.raises(ModelSourceValidationError) as network_error:
        adapter.plan(ModelSourceRequest(**base, license_status="approved"))
    assert network_error.value.code == "model_import_snapshot_descriptor_invalid"


def test_import_completion_appends_immutable_catalog_versions(tmp_path) -> None:
    registry = SqliteUnslothModelCatalogRegistry(tmp_path / "models.sqlite3")
    handler = UnslothModelImportResultHandler(registry)
    payload = {
        "schema_version": 2,
        "tenant_id": "tenant-a",
        "project_id": "project-a",
        "source_id": SOURCE_ID,
        "kind": "huggingface_snapshot",
        "expected_sha256": HASH_A,
        "artifact_id": None,
        "model_id": "org/model",
        "revision": "c" * 40,
        "max_bytes": 1024,
        "allow_patterns": ["*.safetensors"],
        "trust_remote_code": False,
        "network_authorized": True,
        "license_status": "approved",
        "format": "safetensors",
        "architecture": "llama",
        "quantization": None,
        "capability_facets": ["training.text"],
    }
    result = {
        "schema": "ananta.unsloth-model-import-result.v1",
        "cache_key": HASH_A,
        "relative_path": HASH_A,
        "content_sha256": HASH_A,
        "file_count": 1,
        "total_bytes": 10,
    }

    first = handler.handle(task_id="unsloth-task-a", task_payload=payload, worker_result=result)
    replay = handler.handle(task_id="unsloth-task-a", task_payload=payload, worker_result=result)
    second = handler.handle(
        task_id="unsloth-task-b",
        task_payload={**payload, "expected_sha256": HASH_B},
        worker_result={**result, "content_sha256": HASH_B},
    )

    assert replay == first
    assert (first.version, second.version) == (1, 2)
    assert registry.list_versions(tenant_id="tenant-a") == (first, second)
    wire = ModelCatalog(
        catalog_revision=second.catalog_revision,
        imported_models=(first, second),
    ).to_wire()
    assert wire["imported_models"][0]["source_id"] == SOURCE_ID


def test_recipe_hash_binds_source_and_run_before_queueing() -> None:
    tasks = HubUnslothTaskSubmissionAdapter(
        task_queue=_Queue(),
        task_repository=_Repository(),
    )
    service = UnslothDataRecipeSubmissionService(
        adapter=UnslothDataRecipeAdapter(datasets=_Datasets(), evidence=_evidence()),
        tasks=tasks,
    )
    request = DataRecipeRequest(
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        source_id=SOURCE_ID,
        run_id=RUN_ID,
        objective="causal_lm",
        prompt_field="prompt",
        response_field="answer",
    )

    submission = service.submit(request)
    manifest = json.loads(submission.manifest.canonical_json())

    assert manifest["source_id"] == SOURCE_ID
    assert manifest["run_id"] == RUN_ID
    assert manifest["normalization_version"] == "unsloth-recipe-v2"
