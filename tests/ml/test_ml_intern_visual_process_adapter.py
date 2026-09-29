from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping

import pytest

from agent.services.ml_intern_training_repository_port import MlInternTrainingPrincipal
from agent.visual_process.models import VisualProcessStep
from agent.visual_process.step_adapters import (
    MlInternBuildLoraDatasetAdapter,
    MlInternLegacyDatasetImportAdapter,
    MlInternTrainLoraAdapter,
    MlInternVisualProcessAdapterError,
)
from agent.visual_process.task_kind_registry import get_task_kind_info


@dataclass
class _ControlStub:
    response: dict[str, Any]
    replayed: bool = False
    calls: list[tuple[MlInternTrainingPrincipal, dict[str, Any], str]] = field(default_factory=list)

    def create_job(
        self,
        principal: MlInternTrainingPrincipal,
        payload: Mapping[str, Any],
        *,
        idempotency_key: str,
    ) -> tuple[dict[str, Any], bool]:
        self.calls.append((principal, dict(payload), idempotency_key))
        return dict(self.response), self.replayed


@dataclass
class _LegacyImporterStub:
    dataset_id: str
    calls: list[tuple[MlInternTrainingPrincipal, str]] = field(default_factory=list)

    def import_relative_path(self, principal: MlInternTrainingPrincipal, relative_path: str) -> str:
        self.calls.append((principal, relative_path))
        return self.dataset_id


@dataclass
class _DatasetCatalogStub:
    projection: dict[str, Any]
    create_calls: list[dict[str, Any]] = field(default_factory=list)
    get_calls: list[tuple[MlInternTrainingPrincipal, str]] = field(default_factory=list)

    def create_from_records(
        self,
        principal: MlInternTrainingPrincipal,
        records: list[dict[str, Any]],
        **options: Any,
    ) -> Mapping[str, Any]:
        self.create_calls.append({"principal": principal, "records": records, **options})
        return dict(self.projection)

    def get_dataset(
        self,
        principal: MlInternTrainingPrincipal,
        dataset_id: str,
    ) -> Mapping[str, Any]:
        self.get_calls.append((principal, dataset_id))
        return {**self.projection, "id": dataset_id}


def _step(**metadata: Any) -> VisualProcessStep:
    return VisualProcessStep(
        id="vp-train-step",
        label="Train LoRA",
        kind="ml_intern_train_lora",
        gate=True,
        metadata=metadata,
    )


def _build_step(**metadata: Any) -> VisualProcessStep:
    return VisualProcessStep(
        id="vp-build-step",
        label="Dataset kuratieren",
        kind="ml_intern_build_lora_dataset",
        gate=False,
        metadata=metadata,
    )


def _context(tmp_path) -> dict[str, Any]:
    return {
        "visual_process_id": "vp-lora-flow",
        "visual_process_run_id": "vp-run-17",
        "ml_intern_training_principal": {"tenant_id": "tenant-a", "subject": "alice"},
        "ml_intern_training": {
            "enabled": True,
            "mode": "dry_run",
            "backend": "mock",
            "gpu_profile": "generic-safe",
            "dataset_root": str(tmp_path / "trusted-datasets"),
            "artifact_root": str(tmp_path / "trusted-artifacts"),
            "require_dataset_validation": True,
        },
    }


def test_build_step_ingests_bounded_upstream_records_via_catalog_and_returns_canonical_link(tmp_path) -> None:
    catalog = _DatasetCatalogStub(
        {
            "id": "dataset-built",
            "name": "VP records",
            "status": "validated",
            "validation_status": "passed",
            "record_count": 6,
            "train_record_count": 5,
            "validation_record_count": 1,
        }
    )
    received_configs: list[Mapping[str, Any]] = []

    def catalog_factory(config: Mapping[str, Any]) -> _DatasetCatalogStub:
        received_configs.append(config)
        return catalog

    adapter = MlInternBuildLoraDatasetAdapter(catalog_factory=catalog_factory)
    records = [
        {"instruction": f"Bounded instruction {index}", "output": f"Answer {index}"}
        for index in range(6)
    ]
    result = adapter.execute(
        _build_step(
            name="VP records",
            format="instruction",
            validation_ratio=0.2,
            split_seed=17,
            dataset_root="/untrusted/step/root",
            output_path="../../outside.jsonl",
        ),
        artifacts={"records": records},
        context=_context(tmp_path),
    )

    assert result.status == "success"
    assert result.outputs["dataset_id"] == "dataset-built"
    assert result.outputs["dataset_url"] == "/model-training?tab=datasets&dataset_id=dataset-built"
    assert result.outputs["links"]["dataset"] == result.outputs["dataset_url"]
    assert not {
        "dataset_path",
        "absolute_dataset_path",
        "validation_report_path",
        "dataset_root",
        "output_path",
    }.intersection(result.outputs)
    assert result.diagnostics["source_mode"] == "bounded_upstream_records"
    assert received_configs[0]["dataset_root"] == str(tmp_path / "trusted-datasets")
    call = catalog.create_calls[0]
    assert call["principal"] == MlInternTrainingPrincipal("tenant-a", "alice")
    assert call["records"] == records
    assert call["validation_ratio"] == 0.2
    assert call["split_seed"] == 17
    assert call["idempotency_key"].startswith("vp-dataset-")
    assert any("dataset_root" in warning and "output_path" in warning for warning in result.warnings)


def test_build_step_resolves_existing_repository_dataset_without_reingress(tmp_path) -> None:
    catalog = _DatasetCatalogStub(
        {
            "id": "dataset-existing",
            "status": "validated",
            "validation_status": "passed",
            "record_count": 10,
            "train_record_count": 8,
            "validation_record_count": 2,
        }
    )
    adapter = MlInternBuildLoraDatasetAdapter(catalog_factory=lambda _config: catalog)

    result = adapter.execute(
        _build_step(dataset_id="dataset-existing"),
        artifacts={},
        context=_context(tmp_path),
    )

    assert result.status == "success"
    assert result.outputs["dataset_id"] == "dataset-existing"
    assert result.diagnostics["source_mode"] == "catalog_reference"
    assert catalog.get_calls == [(MlInternTrainingPrincipal("tenant-a", "alice"), "dataset-existing")]
    assert not catalog.create_calls


def test_build_step_adapts_one_legacy_source_only_through_quarantine(tmp_path) -> None:
    catalog = _DatasetCatalogStub(
        {
            "id": "dataset-quarantined",
            "status": "validated",
            "validation_status": "passed",
            "record_count": 5,
            "train_record_count": 4,
            "validation_record_count": 1,
        }
    )
    importer = _LegacyImporterStub("dataset-quarantined")
    received_configs: list[Mapping[str, Any]] = []

    def importer_factory(config: Mapping[str, Any]) -> _LegacyImporterStub:
        received_configs.append(config)
        return importer

    adapter = MlInternBuildLoraDatasetAdapter(
        catalog_factory=lambda _config: catalog,
        legacy_dataset_import_factory=importer_factory,
    )
    result = adapter.execute(
        _build_step(
            source_paths="curated/legacy.jsonl",
            dataset_root="/untrusted/step/root",
            output_path="legacy-output.jsonl",
        ),
        artifacts={},
        context=_context(tmp_path),
    )

    assert result.status == "success"
    assert result.outputs["dataset_id"] == "dataset-quarantined"
    assert result.diagnostics["source_mode"] == "legacy_quarantine_import"
    assert importer.calls == [(MlInternTrainingPrincipal("tenant-a", "alice"), "curated/legacy.jsonl")]
    assert received_configs[0]["dataset_root"] == str(tmp_path / "trusted-datasets")
    assert catalog.get_calls == [(MlInternTrainingPrincipal("tenant-a", "alice"), "dataset-quarantined")]


def test_build_step_rejects_multiple_legacy_paths_instead_of_running_a_second_pipeline(tmp_path) -> None:
    catalog = _DatasetCatalogStub({"id": "must-not-run"})
    importer = _LegacyImporterStub("must-not-run")
    adapter = MlInternBuildLoraDatasetAdapter(
        catalog_factory=lambda _config: catalog,
        legacy_dataset_import_factory=lambda _config: importer,
    )

    result = adapter.execute(
        _build_step(source_paths=["one.jsonl", "two.jsonl"]),
        artifacts={},
        context=_context(tmp_path),
    )

    assert result.status == "failed"
    assert result.diagnostics["reason_code"] == "legacy_dataset_sources_ambiguous"
    assert not importer.calls
    assert not catalog.create_calls
    assert not catalog.get_calls


def test_build_step_requires_catalog_id_or_bounded_records(tmp_path) -> None:
    catalog = _DatasetCatalogStub({"id": "must-not-run"})
    adapter = MlInternBuildLoraDatasetAdapter(catalog_factory=lambda _config: catalog)

    result = adapter.execute(_build_step(), artifacts={}, context=_context(tmp_path))

    assert result.status == "failed"
    assert result.diagnostics["reason_code"] == "dataset_input_required"
    assert not catalog.create_calls
    assert not catalog.get_calls


def test_build_step_real_catalog_ingress_splits_validates_and_projects_records(tmp_path) -> None:
    marker = tmp_path.name
    records = [
        {
            "instruction": f"Erklaere Catalog-Ingress {marker} Nummer {index}",
            "output": f"Kanonische Antwort {index}",
        }
        for index in range(6)
    ]
    context = _context(tmp_path)
    context["ml_intern_training_principal"] = {
        "tenant_id": f"tenant-build-{marker}",
        "subject": f"owner-build-{marker}",
    }

    result = MlInternBuildLoraDatasetAdapter().execute(
        _build_step(name="VP Catalog Integration", validation_ratio=0.2, split_seed=17),
        artifacts={"records": records},
        context=context,
    )

    assert result.status == "success"
    assert result.outputs["dataset_id"]
    assert result.outputs["dataset_build_result"]["id"] == result.outputs["dataset_id"]
    assert result.outputs["dataset_build_result"]["train_record_count"] > 0
    assert result.outputs["dataset_build_result"]["validation_record_count"] > 0
    assert result.outputs["dataset_build_result"]["trainable"] is True
    assert result.diagnostics["source_mode"] == "bounded_upstream_records"
    assert "dataset_path" not in result.outputs
    assert "absolute_dataset_path" not in result.outputs


def test_v2_vp_step_materializes_canonical_hub_job_and_control_center_links(tmp_path) -> None:
    control = _ControlStub(
        {
            "id": "job-vp-001",
            "job_type": "train_lora",
            "status": "queued",
            "phase": "queued",
            "poll_url": "/api/ml-intern-training/jobs/job-vp-001",
            "events_url": "/api/ml-intern-training/jobs/job-vp-001/events",
        }
    )
    adapter = MlInternTrainLoraAdapter(control_factory=lambda _config: control)
    step = _step(
        dataset_id="dataset-001",
        training_profile_id="generic-safe",
        base_model="qwen2.5-coder-7b",
        max_steps=12,
        output_name="vp-adapter",
    )

    result = adapter.execute(step, artifacts={}, context=_context(tmp_path))

    assert result.status == "success"
    assert result.backend_service == "MlInternTrainingControlService.create_job"
    assert result.outputs["job_id"] == "job-vp-001"
    assert result.outputs["training_phase"] == "queued"
    assert result.outputs["terminal"] is False
    assert result.outputs["model_training_url"] == "/model-training"
    assert result.outputs["job_url"] == "/model-training?tab=jobs&job_id=job-vp-001"
    assert result.outputs["dataset_url"] == "/model-training?tab=datasets&dataset_id=dataset-001"

    principal, payload, idempotency_key = control.calls[0]
    assert principal == MlInternTrainingPrincipal("tenant-a", "alice")
    assert payload == {
        "dataset_id": "dataset-001",
        "job_type": "train_lora",
        "mode": "dry_run",
        "backend": "mock",
        "base_model": "qwen2.5-coder-7b",
        "method": "qlora",
        "gpu_profile": "generic-safe",
        "output_name": "vp-adapter",
        "hyperparameters": {"max_steps": 12},
        "require_dataset_validation": True,
        "require_secret_scan": True,
    }
    assert idempotency_key.startswith("vp-lora-")
    assert "dataset_path" not in payload
    assert "dataset_root" not in payload
    assert "artifact_root" not in payload


def test_vp_generated_idempotency_key_is_stable_for_same_run_and_payload(tmp_path) -> None:
    control = _ControlStub({"id": "job-stable", "status": "queued", "phase": "queued"})
    adapter = MlInternTrainLoraAdapter(control_factory=lambda _config: control)
    step = _step(
        dataset_id="dataset-stable",
        training_profile_id="none",
        base_model="local-model",
    )

    first = adapter.execute(step, artifacts={}, context=_context(tmp_path))
    second = adapter.execute(step, artifacts={}, context=_context(tmp_path))

    assert first.status == second.status == "success"
    assert control.calls[0][2] == control.calls[1][2]


def test_legacy_dataset_path_is_imported_but_never_forwarded_to_job(tmp_path) -> None:
    control = _ControlStub({"job_id": "job-legacy", "status": "running", "phase": "training"})
    importer = _LegacyImporterStub("dataset-imported")
    received_configs: list[Mapping[str, Any]] = []

    def importer_factory(config: Mapping[str, Any]) -> _LegacyImporterStub:
        received_configs.append(config)
        return importer

    adapter = MlInternTrainLoraAdapter(
        control_factory=lambda _config: control,
        legacy_dataset_import_factory=importer_factory,
    )
    step = _step(
        dataset_path="curated/train.jsonl",
        dataset_root="/untrusted/step/root",
        artifact_root="/untrusted/artifact/root",
        output_dir="legacy-adapter",
        base_model="local-model",
    )

    result = adapter.execute(step, artifacts={}, context=_context(tmp_path))

    assert result.status == "success"
    assert result.outputs["dataset_id"] == "dataset-imported"
    assert result.outputs["training_profile_id"] == "generic-safe"
    assert importer.calls == [(MlInternTrainingPrincipal("tenant-a", "alice"), "curated/train.jsonl")]
    assert received_configs[0]["dataset_root"] == str(tmp_path / "trusted-datasets")
    payload = control.calls[0][1]
    assert payload["dataset_id"] == "dataset-imported"
    assert payload["output_name"] == "legacy-adapter"
    assert not {"dataset_path", "dataset_root", "artifact_root"}.intersection(payload)
    assert any("Deprecated VP path fields" in warning for warning in result.warnings)
    assert any("output_dir" in warning for warning in result.warnings)


def test_legacy_dataset_path_cannot_materialize_a_live_training_job(tmp_path) -> None:
    control = _ControlStub({"job_id": "must-not-exist", "status": "queued"})
    importer = _LegacyImporterStub("dataset-quarantined")
    adapter = MlInternTrainLoraAdapter(
        control_factory=lambda _config: control,
        legacy_dataset_import_factory=lambda _config: importer,
    )

    result = adapter.execute(
        _step(
            dataset_path="legacy/train.jsonl",
            training_profile_id="generic-safe",
            base_model="local-model",
            mode="live",
            output_name="legacy-live",
        ),
        artifacts={"approval_id": "approval-1", "live_confirmed": True},
        context=_context(tmp_path),
    )

    assert result.status == "failed"
    assert result.diagnostics["reason_code"] == "legacy_dataset_live_training_forbidden"
    assert importer.calls == []
    assert control.calls == []


@pytest.mark.parametrize("value", ["../outside.jsonl", "/absolute/train.jsonl", "nested\\train.jsonl"])
def test_legacy_dataset_path_rejects_escape_and_platform_ambiguity(tmp_path, value: str) -> None:
    importer = MlInternLegacyDatasetImportAdapter(
        {
            "dataset_root": str(tmp_path / "datasets"),
            "dataset_catalog_root": str(tmp_path / "catalog"),
        }
    )

    with pytest.raises(MlInternVisualProcessAdapterError) as exc_info:
        importer.import_relative_path(MlInternTrainingPrincipal("tenant-a", "alice"), value)

    assert exc_info.value.reason_code == "legacy_dataset_path_invalid"


def test_legacy_dataset_path_is_quarantined_split_validated_and_projected(tmp_path) -> None:
    dataset_root = tmp_path / "datasets"
    dataset_root.mkdir()
    source = dataset_root / "legacy.jsonl"
    marker = tmp_path.name
    source.write_text(
        "".join(
            json.dumps(
                {
                    "instruction": f"Erklaere sicheren Datensatz {marker} Nummer {index}",
                    "output": f"Validierte Antwort {index}",
                },
                sort_keys=True,
            )
            + "\n"
            for index in range(6)
        ),
        encoding="utf-8",
    )
    importer = MlInternLegacyDatasetImportAdapter(
        {
            "dataset_root": str(dataset_root),
            "dataset_catalog_root": str(tmp_path / "catalog"),
            "validation_ratio": 0.2,
            "split_seed": 17,
        }
    )
    principal = MlInternTrainingPrincipal(f"tenant-{marker}", f"owner-{marker}")

    dataset_id = importer.import_relative_path(principal, "legacy.jsonl")

    from agent.repositories.ml_intern_training import get_ml_intern_training_repository

    projected = get_ml_intern_training_repository().get_dataset(principal, dataset_id)
    assert projected is not None
    assert projected.train_record_count > 0
    assert projected.validation_record_count > 0
    assert projected.validation_report["ok"] is True
    assert projected.train_storage_ref != str(source)
    assert projected.validation_storage_ref


def test_v2_dataset_id_requires_explicit_bounded_training_profile(tmp_path) -> None:
    control = _ControlStub({"id": "must-not-run", "status": "queued", "phase": "queued"})
    adapter = MlInternTrainLoraAdapter(control_factory=lambda _config: control)

    result = adapter.execute(
        _step(dataset_id="dataset-001", base_model="local-model"),
        artifacts={},
        context=_context(tmp_path),
    )

    assert result.status == "failed"
    assert result.diagnostics["reason_code"] == "training_profile_required"
    assert not control.calls


def test_legacy_output_directory_cannot_reintroduce_a_server_path(tmp_path) -> None:
    control = _ControlStub({"id": "must-not-run", "status": "queued", "phase": "queued"})
    adapter = MlInternTrainLoraAdapter(control_factory=lambda _config: control)

    result = adapter.execute(
        _step(
            dataset_id="dataset-safe-output",
            training_profile_id="generic-safe",
            base_model="local-model",
            output_dir="../../outside",
        ),
        artifacts={},
        context=_context(tmp_path),
    )

    assert result.status == "failed"
    assert result.diagnostics["reason_code"] == "output_name_invalid"
    assert not control.calls


def test_terminal_hub_result_is_projected_into_vp_runtime_overlay(tmp_path) -> None:
    control = _ControlStub(
        {
            "id": "job-completed",
            "job_type": "train_lora",
            "status": "completed",
            "phase": "completed",
            "result": {"metrics": {"eval_loss": 0.42}},
        },
        replayed=True,
    )
    adapter = MlInternTrainLoraAdapter(control_factory=lambda _config: control)

    result = adapter.execute(
        _step(dataset_id="dataset-terminal", training_profile_id="none", base_model="local-model"),
        artifacts={},
        context=_context(tmp_path),
    )

    assert result.status == "success"
    assert result.outputs["terminal"] is True
    assert result.outputs["terminal_result"] == {"metrics": {"eval_loss": 0.42}}
    assert result.diagnostics["idempotent_replay"] is True


def test_runtime_truth_points_to_hub_control_service() -> None:
    info = get_task_kind_info("ml_intern_train_lora")

    assert info is not None
    assert info["backend_service"] == "MlInternTrainingControlService.create_job"
    assert "write_database" in info["side_effects"]
    assert "shell_execution" not in info["side_effects"]


def test_dataset_build_runtime_truth_points_to_catalog_and_repository() -> None:
    info = get_task_kind_info("ml_intern_build_lora_dataset")

    assert info is not None
    assert info["backend_service"] == "MlInternDatasetCatalogService + MlInternDatasetRepositoryBridgeService"
    assert "write_database" in info["side_effects"]
    assert "shell_execution" not in info["side_effects"]
