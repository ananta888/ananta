from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from agent.db_models import MlInternTrainingJobDB
from agent.services.ml_intern_adapter_registry_service import (
    MlInternAdapterRegistryService,
    RegistryError,
)
from agent.services.ml_intern_evaluation_store_service import MlInternEvaluationStoreService
from agent.services.ml_intern_training_repository_port import MlInternTrainingPrincipal
from agent.services.ml_intern_training_result_publisher import RegistryTrainingResultPublisher


def _setup(tmp_path: Path, *, adapter_loss: float) -> tuple[
    RegistryTrainingResultPublisher,
    MlInternAdapterRegistryService,
    MlInternEvaluationStoreService,
    MlInternTrainingJobDB,
    dict,
]:
    artifact_root = tmp_path / "artifacts"
    registry_path = artifact_root / "adapter_registry.json"
    registry = MlInternAdapterRegistryService(registry_path)
    registry.register_trained(
        adapter_id="eval-adapter",
        display_name="Eval adapter",
        version="1",
        base_model="local/base",
        method="lora",
        artifact_paths={"adapter_dir": str(artifact_root / "adapter")},
        config_hash="a" * 64,
        artifact_sha256="b" * 64,
        tenant_id="tenant-eval",
        owner_subject="owner-eval",
    )
    job = MlInternTrainingJobDB(
        tenant_id="tenant-eval",
        owner_subject="owner-eval",
        task_id=f"task-{uuid.uuid4()}",
        dataset_id="dataset-eval",
        job_type="evaluate_lora",
        mode="live",
        backend="mock",
        base_model="local/base",
        status="completed",
        phase="completed",
        idempotency_key_digest="c" * 64,
        request_digest="d" * 64,
        request_spec={"adapter_id": "eval-adapter", "base_model": "local/base"},
        adapter_id="eval-adapter",
    )
    metrics = {
        "base": {"eval_loss": 1.0, "perplexity": 2.7},
        "adapter": {"eval_loss": adapter_loss, "perplexity": 2.1 if adapter_loss < 1 else 3.1},
        "wins": {"base": int(adapter_loss > 1), "adapter": int(adapter_loss < 1), "tie": 0},
        "samples": [
            {
                "id": "e" * 64,
                "record_index": 0,
                "base_output": "base result",
                "adapter_output": "adapter result",
                "expected_output": "expected result",
                "base_score": 0.0,
                "adapter_score": 1.0 if adapter_loss < 1 else 0.0,
                "winner": "adapter" if adapter_loss < 1 else "base",
            }
        ],
    }
    result_dir = artifact_root / "jobs" / job.id
    result_dir.mkdir(parents=True)
    encoded = json.dumps(metrics, sort_keys=True)
    (result_dir / "eval_report.json").write_text(encoded, encoding="utf-8")
    (result_dir / "evaluation.json").write_text(encoded, encoding="utf-8")
    (result_dir / "evaluation_manifest.json").write_text(
        json.dumps({"job_id": job.id, "adapter": {"adapter_id": "eval-adapter"}}),
        encoding="utf-8",
    )
    return (
        RegistryTrainingResultPublisher(artifact_root=artifact_root, registry_path=registry_path),
        registry,
        MlInternEvaluationStoreService(artifact_root=artifact_root),
        job,
        {"adapter_id": "eval-adapter", "metrics": metrics},
    )


@pytest.mark.parametrize(("adapter_loss", "passed"), [(0.75, True), (1.25, False)])
def test_evaluation_persists_bounded_samples_and_enforces_registry_gate(
    tmp_path: Path,
    adapter_loss: float,
    passed: bool,
) -> None:
    publisher, registry, store, job, result = _setup(tmp_path, adapter_loss=adapter_loss)

    assert publisher.publish_evaluation(job, result) == "eval-adapter"
    report = store.get(MlInternTrainingPrincipal(job.tenant_id, job.owner_subject), job.id)
    scope = {"tenant_id": job.tenant_id, "owner_subject": job.owner_subject}
    record = registry.get("eval-adapter", **scope)

    assert report["status"] == "completed"
    assert report["samples"][0]["prompt_ref"] == "e" * 64
    assert "prompt" not in report["samples"][0]
    assert len(report["samples"][0]["adapter_output"]) <= 2_000
    assert record is not None and record.status == "evaluated"
    assert (record.eval_score >= 0) is passed
    if passed:
        approved = registry.approve(
            "eval-adapter",
            approved_by="admin",
            reason="bounded integration evaluation passed",
            require_eval_report=True,
            minimum_eval_score=0.0,
            **scope,
        )
        assert approved.status == "approved"
    else:
        with pytest.raises(RegistryError):
            registry.approve(
                "eval-adapter",
                approved_by="admin",
                reason="must reject a regressing adapter",
                require_eval_report=True,
                minimum_eval_score=0.0,
                **scope,
            )


def test_invalid_evaluation_does_not_advance_registry(tmp_path: Path) -> None:
    publisher, registry, _store, job, result = _setup(tmp_path, adapter_loss=0.75)
    result["metrics"] = {"adapter": {"eval_loss": 0.75}}

    with pytest.raises(ValueError, match="base-vs-adapter"):
        publisher.publish_evaluation(job, result)

    record = registry.get(
        "eval-adapter",
        tenant_id=job.tenant_id,
        owner_subject=job.owner_subject,
    )
    assert record is not None and record.status == "trained"


def test_scorer_regression_blocks_store_ui_and_registry_even_when_loss_improves(tmp_path: Path) -> None:
    publisher, registry, store, job, result = _setup(tmp_path, adapter_loss=0.75)
    result["metrics"]["wins"] = {"base": 3, "adapter": 1, "tie": 0}

    publisher.publish_evaluation(job, result)
    report = store.get(MlInternTrainingPrincipal(job.tenant_id, job.owner_subject), job.id)
    scope = {"tenant_id": job.tenant_id, "owner_subject": job.owner_subject}
    record = registry.get("eval-adapter", **scope)

    assert report["passed"] is False
    assert report["aggregate_score"] == -0.5
    assert report["reason_code"] == "evaluation_score_below_threshold"
    assert record is not None and record.eval_score == -0.5
    with pytest.raises(RegistryError):
        registry.approve(
            "eval-adapter",
            approved_by="admin",
            reason="scorer regression must block approval",
            require_eval_report=True,
            minimum_eval_score=0.0,
            **scope,
        )
