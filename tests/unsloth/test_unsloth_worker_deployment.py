from __future__ import annotations

import hashlib
import json
from pathlib import Path

from agent.services.unsloth_model_catalog_service import (
    SqliteUnslothModelCatalogRegistry,
    UnslothModelImportResultHandler,
)
from agent.services.unsloth_worker_result_service import (
    HubUnslothWorkerResultProjector,
)
from worker.training.data_recipe_materializer import (
    FilesystemDatasetRecipeMaterializer,
)
from worker.training.model_imports import ModelImportResult
from worker.training.unsloth_task_handlers import (
    UnslothModelImportTaskHandler,
)
from worker.training.unsloth_worker_runtime import (
    build_unsloth_worker_runtime,
)


SOURCE_ID = "SRC_supplied-model"
RUN_ID = "RUN_supplied-evaluation"


def _payload_hash(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()


def _model_payload() -> dict:
    return {
        "schema_version": 2,
        "tenant_id": "tenant-a",
        "project_id": "project-a",
        "source_id": SOURCE_ID,
        "kind": "huggingface_snapshot",
        "expected_sha256": "a" * 64,
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


def _task(
    task_id: str,
    task_type: str,
    payload: dict,
) -> dict:
    return {
        "id": task_id,
        "worker_execution_context": {
            "schema": (
                "ananta.unsloth-worker-task-context.v1"
            ),
            "unsloth_task": {
                "task_type": task_type,
                "tenant_id": "tenant-a",
                "payload": payload,
                "payload_sha256": _payload_hash(payload),
                "result_handler": (
                    "unsloth_model_import_v1"
                    if task_type == "ml.model.import"
                    else "unsloth_data_recipe_v1"
                ),
                "followup_task_creation_allowed": False,
            },
        },
    }


def test_model_handler_returns_hash_bound_queue_result() -> None:
    class Executor:
        def execute(self, _command):
            return ModelImportResult(
                cache_key="b" * 64,
                relative_path="b" * 64,
                content_sha256="a" * 64,
                file_count=1,
                total_bytes=10,
            )

    payload = _model_payload()
    task_id = "unsloth-" + "1" * 32
    result = UnslothModelImportTaskHandler(
        Executor()
    ).execute(
        task=_task(
            task_id,
            "ml.model.import",
            payload,
        )
    )

    assert result["schema"] == (
        "ananta.unsloth-worker-task-result.v1"
    )
    assert result["task_id"] == task_id
    assert result["payload_sha256"] == _payload_hash(
        payload
    )
    assert result["status"] == "completed"


def test_recipe_materializer_is_attempt_scoped_and_offline(
    tmp_path: Path,
) -> None:
    dataset_root = tmp_path / "datasets"
    output_root = tmp_path / "attempt-output"
    dataset_root.mkdir()
    output_root.mkdir()
    content = (
        b'{"answer":"A","prompt":"P"}\n'
        b'{"answer":"B","prompt":"Q"}\n'
    )
    source = dataset_root / "train.jsonl"
    source.write_bytes(content)
    unsigned = {
        "tenant_id": "tenant-a",
        "dataset_id": "dataset-a",
        "dataset_hash": "d" * 64,
        "dataset_ref": "train.jsonl",
        "dataset_partition_sha256": (
            hashlib.sha256(content).hexdigest()
        ),
        "source_id": SOURCE_ID,
        "run_id": RUN_ID,
        "objective": "causal_lm",
        "prompt_field": "prompt",
        "response_field": "answer",
        "media_field": None,
        "validation_fraction": 0.2,
        "seed": 3407,
        "row_count": 2,
        "normalization_version": "unsloth-recipe-v2",
    }
    recipe_id = hashlib.sha256(
        json.dumps(
            unsigned,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    manifest = {"recipe_id": recipe_id, **unsigned}
    attempt_id = "unsloth-" + "2" * 32

    result = FilesystemDatasetRecipeMaterializer(
        dataset_root=dataset_root,
        attempt_output_root=output_root,
        expected_attempt_id=attempt_id,
    ).materialize(
        manifest,
        attempt_id=attempt_id,
    )

    assert result["attempt_id"] == attempt_id
    assert result["total_rows"] == 2
    assert result["output_ref"] == recipe_id
    assert (
        output_root / recipe_id / "result.json"
    ).is_file()


def test_hub_completion_projects_model_result(
    tmp_path: Path,
) -> None:
    payload = _model_payload()
    task_id = "unsloth-" + "3" * 32
    worker_result = {
        "schema": "ananta.unsloth-model-import-result.v1",
        "cache_key": "b" * 64,
        "relative_path": "b" * 64,
        "content_sha256": "a" * 64,
        "file_count": 1,
        "total_bytes": 10,
    }
    response = {
        "schema": "ananta.unsloth-worker-task-result.v1",
        "task_id": task_id,
        "task_type": "ml.model.import",
        "tenant_id": "tenant-a",
        "payload_sha256": _payload_hash(payload),
        "status": "completed",
        "reason_code": None,
        "result": worker_result,
    }
    projector = HubUnslothWorkerResultProjector(
        UnslothModelImportResultHandler(
            SqliteUnslothModelCatalogRegistry(
                tmp_path / "catalog.sqlite3"
            )
        )
    )

    projected = projector.project(
        task_id=task_id,
        task=_task(
            task_id,
            "ml.model.import",
            payload,
        ),
        response=response,
    )

    assert projected is not None
    assert projected["unsloth_model_import"][
        "import_task_id"
    ] == task_id
    assert projected["unsloth_worker_result"][
        "status"
    ] == "completed"


def test_recipe_runtime_advertises_queue_capability(
    tmp_path: Path,
) -> None:
    datasets = tmp_path / "datasets"
    output = tmp_path / "output"
    datasets.mkdir()
    output.mkdir()
    runtime = build_unsloth_worker_runtime(
        {
            "ANANTA_UNSLOTH_WORKER_MODE": "data_recipe",
            "ANANTA_UNSLOTH_RECIPE_DATASET_ROOT": str(
                datasets
            ),
            (
                "ANANTA_UNSLOTH_RECIPE_"
                "ATTEMPT_OUTPUT_ROOT"
            ): str(output),
            "HF_DATASETS_OFFLINE": "1",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
        }
    )

    health = runtime.health_snapshot()
    assert health["ready"] is True
    assert health["network_access"] == "none"
    assert health["capabilities"] == [
        "unsloth_dataset_materialization",
    ]


def test_compose_keeps_recipe_off_egress_network() -> None:
    compose = Path(
        "docker/compose-next/compose.unsloth.yml"
    ).read_text(encoding="utf-8")

    assert "unsloth-model-import-network" in compose
    assert "unsloth-model-download-egress" in compose
    recipe_section = compose.split(
        "unsloth-data-recipe-worker:",
        1,
    )[1]
    recipe_section = recipe_section.split(
        "unsloth-model-import-worker:",
        1,
    )[0]
    assert (
        "unsloth-model-download-egress"
        not in recipe_section
    )
    assert "<<: *unsloth-hardening" in recipe_section
    assert "read_only: true" in compose
    assert (
        "ANANTA_UNSLOTH_RECIPE_ATTEMPT_OUTPUT_ROOT: "
        "/attempt-output"
    ) in recipe_section
