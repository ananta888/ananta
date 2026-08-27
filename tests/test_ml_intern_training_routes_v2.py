from __future__ import annotations

import hashlib
import io
import json
import time
import uuid
import zipfile
from pathlib import Path

import pytest

from agent.db_models import MlInternDatasetDB, MlInternTrainingJobDB
from agent.repositories.ml_intern_training import MlInternTrainingRepository
from agent.services.ml_intern_adapter_import_service import MlInternAdapterImportService
from agent.services.ml_intern_adapter_registry_service import (
    MlInternAdapterRegistryService,
    RegistryError,
)
from agent.services.ml_intern_artifact_security_service import MlInternArtifactSecurityService
from agent.services.ml_intern_dataset_catalog_service import (
    DatasetCatalogError,
    MlInternDatasetCatalogService,
)
from agent.services.ml_intern_training_repository_port import MlInternTrainingPrincipal


def _configure(app, tmp_path: Path) -> Path:
    suffix = uuid.uuid4().hex
    artifact_root = tmp_path / f"artifacts-{suffix}"
    app.config["AGENT_CONFIG"] = {
        **dict(app.config.get("AGENT_CONFIG") or {}),
        "ml_intern_training": {
            "enabled": True,
            "mode": "dry_run",
            "backend": "mock",
            "artifact_root": str(artifact_root),
            "dataset_root": str(tmp_path / f"datasets-{suffix}"),
            "base_models": ["local/base"],
            "require_dataset_validation": True,
            "require_secret_scan": True,
            "max_concurrent_jobs": 4,
        },
        "lora_runtime": {
            "adapter_registry_path": str(artifact_root / "adapter_registry.json"),
        },
    }
    return artifact_root


def _headers(admin_auth_header: dict[str, str], key: str) -> dict[str, str]:
    return {**admin_auth_header, "Idempotency-Key": key}


def _records(count: int = 12, *, prefix: str = "") -> list[dict[str, str]]:
    return [
        {
            "instruction": f"{prefix}Instruction {index}",
            "input": "",
            "output": f"{prefix}Answer {index}",
        }
        for index in range(count)
    ]


def _adapter_bundle() -> bytes:
    value = b"\x00\x00\x00\x00"
    header = json.dumps(
        {"lora.weight": {"dtype": "F32", "shape": [1], "data_offsets": [0, len(value)]}},
        separators=(",", ":"),
    ).encode("utf-8")
    weights = len(header).to_bytes(8, "little") + header + value
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "adapter_config.json",
            json.dumps(
                {
                    "base_model_name_or_path": "local/base",
                    "peft_type": "LORA",
                    "task_type": "CAUSAL_LM",
                    "r": 4,
                    "lora_alpha": 8,
                }
            ),
        )
        archive.writestr("adapter_model.safetensors", weights)
    return output.getvalue()


def test_admin_json_dataset_to_async_job_preview_sse_and_retention(
    app,
    client,
    admin_auth_header,
    tmp_path: Path,
) -> None:
    _configure(app, tmp_path)
    create = client.post(
        "/api/ml-intern-training/datasets",
        headers=_headers(admin_auth_header, "dataset-json-route-001"),
        json={
            "name": "Curated route dataset",
            "format": "instruction",
            "purpose": "local test training",
            "license": "internal-test",
            "privacy": "private",
            "validation_ratio": 0.25,
            "split_seed": 17,
            "records": _records(),
        },
    )
    assert create.status_code == 201, create.get_data(as_text=True)
    dataset = create.get_json()["data"]
    dataset_id = dataset["id"]
    assert dataset["trainable"] is True
    assert dataset["train_record_count"] + dataset["validation_record_count"] == 12
    assert str(tmp_path) not in json.dumps(dataset)
    validation_report = dataset["validation_report"]
    assert validation_report["reason_codes"] == []
    assert len(validation_report["partitions"]["train"]["sha256"]) == 64
    assert validation_report["partitions"]["train"]["format"] == "instruction"
    stored_report = client.get(
        f"/api/ml-intern-training/datasets/{dataset_id}/validation-report",
        headers=admin_auth_header,
    )
    assert stored_report.status_code == 200
    assert stored_report.get_json()["data"] == validation_report

    listed = client.get("/api/ml-intern-training/datasets", headers=admin_auth_header)
    assert listed.status_code == 200
    assert dataset_id in {item["id"] for item in listed.get_json()["data"]["items"]}

    for partition in ("train", "validation"):
        preview = client.get(
            f"/api/ml-intern-training/datasets/{dataset_id}/records?split={partition}&limit=3",
            headers=admin_auth_header,
        )
        assert preview.status_code == 200
        page = preview.get_json()["data"]
        assert 1 <= len(page["items"]) <= 3
        assert str(tmp_path) not in json.dumps(page)

    submit = client.post(
        "/api/ml-intern-training/jobs",
        headers=_headers(admin_auth_header, "training-route-job-001"),
        json={
            "dataset_id": dataset_id,
            "job_type": "train_lora",
            "mode": "dry_run",
            "backend": "mock",
            "base_model_id": "local/base",
            "method": "lora",
            "output_name": "route-adapter",
            "hyperparameters": {"max_steps": 1, "lora_rank": 8},
        },
    )
    assert submit.status_code == 202, submit.get_data(as_text=True)
    accepted = submit.get_json()["data"]
    assert accepted["poll_url"].endswith(accepted["id"])
    assert accepted["events_url"].endswith(f"{accepted['id']}/events")
    assert "path" not in json.dumps(accepted["configuration"]).casefold()

    deadline = time.monotonic() + 5
    detail = accepted
    while time.monotonic() < deadline and detail["status"] not in {"completed", "failed", "cancelled"}:
        response = client.get(accepted["poll_url"], headers=admin_auth_header)
        assert response.status_code == 200
        detail = response.get_json()["data"]
        time.sleep(0.02)
    assert detail["status"] == "completed", detail

    events = client.get(accepted["events_url"], headers=admin_auth_header)
    assert events.status_code == 200
    rows = events.get_json()["data"]["items"]
    assert [row["sequence"] for row in rows] == sorted({row["sequence"] for row in rows})
    assert str(tmp_path) not in json.dumps(rows)

    stream = client.get(f"{accepted['events_url']}?stream=true", headers=admin_auth_header, buffered=False)
    try:
        assert stream.status_code == 200
        assert stream.mimetype == "text/event-stream"
        first_chunk = next(iter(stream.response)).decode("utf-8")
        assert "data:" in first_chunk
    finally:
        stream.close()

    blocked_delete = client.delete(
        f"/api/ml-intern-training/datasets/{dataset_id}",
        headers=_headers(admin_auth_header, "dataset-delete-route-001"),
    )
    assert blocked_delete.status_code == 409
    assert blocked_delete.get_json()["data"]["error"]["code"] == "dataset_referenced"


def test_dataset_validation_override_is_strict_boolean_and_closed_world(
    app, client, admin_auth_header, tmp_path: Path
) -> None:
    _configure(app, tmp_path)
    created = client.post(
        "/api/ml-intern-training/datasets",
        headers=_headers(admin_auth_header, "strict-validation-dataset"),
        json={"name": "Strict validation", "records": _records()},
    )
    assert created.status_code == 201
    dataset_id = created.get_json()["data"]["id"]

    safe_false = client.post(
        f"/api/ml-intern-training/datasets/{dataset_id}/validate",
        headers=_headers(admin_auth_header, "strict-validation-false"),
        json={"allow_sensitive_override": False},
    )
    assert safe_false.status_code == 200

    for index, value in enumerate(("false", 1, {})):
        rejected = client.post(
            f"/api/ml-intern-training/datasets/{dataset_id}/validate",
            headers=_headers(admin_auth_header, f"strict-validation-typed-{index}"),
            json={"allow_sensitive_override": value},
        )
        assert rejected.status_code == 422
        assert rejected.get_json()["data"]["error"]["code"] == ("allow_sensitive_override_invalid")

    unknown = client.post(
        f"/api/ml-intern-training/datasets/{dataset_id}/validate",
        headers=_headers(admin_auth_header, "strict-validation-unknown"),
        json={"trust_me": True},
    )
    assert unknown.status_code == 422
    assert unknown.get_json()["data"]["error"]["code"] == ("dataset_validation_unknown_fields")


def test_live_evaluation_requires_confirmation_and_propagates_reason(
    app, client, admin_auth_header, tmp_path: Path, monkeypatch
) -> None:
    _configure(app, tmp_path)
    training = dict(app.config["AGENT_CONFIG"]["ml_intern_training"])
    training.update(mode="live", backend="peft_trl", gpu_profile="none")
    app.config["AGENT_CONFIG"] = {
        **dict(app.config["AGENT_CONFIG"]),
        "ml_intern_training": training,
    }
    created = client.post(
        "/api/ml-intern-training/datasets",
        headers=_headers(admin_auth_header, "live-eval-dataset"),
        json={"name": "Live evaluation", "records": _records()},
    )
    assert created.status_code == 201
    dataset_id = created.get_json()["data"]["id"]
    imported = client.post(
        "/api/ml-intern-training/adapters/import",
        headers=_headers(admin_auth_header, "live-eval-adapter"),
        data={
            "adapter_id": "live-eval-adapter",
            "name": "Live eval adapter",
            "version": "1",
            "base_model_id": "local/base",
            "method": "lora",
            "bundle": (io.BytesIO(_adapter_bundle()), "live-eval-adapter.zip"),
        },
        content_type="multipart/form-data",
    )
    assert imported.status_code == 201

    missing = client.post(
        "/api/ml-intern-training/evaluations",
        headers=_headers(admin_auth_header, "live-eval-missing-confirmation"),
        json={"adapter_id": "live-eval-adapter", "dataset_id": dataset_id},
    )
    assert missing.status_code == 403
    assert missing.get_json()["data"]["error"]["code"] == "live_confirmation_required"

    captured: dict = {}

    def accept_evaluation(_self, _principal, payload, *, idempotency_key):
        captured.update(payload)
        captured["idempotency_key"] = idempotency_key
        return {
            "id": "lora-job-live-evaluation",
            "job_type": "evaluate_lora",
            "status": "queued",
            "dataset_id": dataset_id,
        }, False

    monkeypatch.setattr(
        "agent.services.ml_intern_training_control_service.MlInternTrainingControlService.create_job",
        accept_evaluation,
    )
    accepted = client.post(
        "/api/ml-intern-training/evaluations",
        headers=_headers(admin_auth_header, "live-eval-confirmed"),
        json={
            "adapter_id": "live-eval-adapter",
            "dataset_id": dataset_id,
            "scorer_name": "generic",
            "live_confirmed": True,
            "risk_reason": "controlled local adapter evaluation",
        },
    )
    assert accepted.status_code == 202, accepted.get_data(as_text=True)
    response = accepted.get_json()["data"]
    assert response["passed"] is None
    assert captured["mode"] == "live"
    assert captured["live_confirmed"] is True
    assert captured["risk_reason"] == "controlled local adapter evaluation"


def test_model_training_routes_are_admin_only(app, client, user_auth_header, tmp_path: Path) -> None:
    _configure(app, tmp_path)
    response = client.get("/api/ml-intern-training/capabilities", headers=user_auth_header)
    assert response.status_code == 403


def test_backend_recommendation_is_advisory_and_hub_owned(app, client, admin_auth_header, tmp_path: Path) -> None:
    _configure(app, tmp_path)

    response = client.post(
        "/api/ml-intern-training/backends/recommendation",
        headers=admin_auth_header,
        json={
            "objective": "sft",
            "method": "lora",
            "modality": "text",
            "resource_profile": "cpu",
            "estimated_model_bytes": 0,
            "runtime_budget_seconds": 3600,
            "export_format": "adapter",
        },
    )

    assert response.status_code == 200
    recommendation = response.get_json()["data"]
    assert recommendation["backend"] == "mock"
    assert recommendation["requires_confirmation"] is True
    assert recommendation["fallback_policy"] == "new_visible_attempt_only"


def test_dataset_json_body_and_training_admission_are_bounded(
    app,
    client,
    admin_auth_header,
    tmp_path: Path,
) -> None:
    _configure(app, tmp_path)
    invalid_ratio = client.post(
        "/api/ml-intern-training/datasets",
        headers=_headers(admin_auth_header, "dataset-invalid-ratio-001"),
        json={"name": "bad", "validation_ratio": 0.01, "records": _records()},
    )
    assert invalid_ratio.status_code == 422
    assert invalid_ratio.get_json()["data"]["error"]["code"] == "numeric_value_out_of_bounds"

    missing_key = client.post(
        "/api/ml-intern-training/datasets",
        headers=admin_auth_header,
        json={"name": "bad", "records": _records()},
    )
    assert missing_key.status_code == 400
    assert missing_key.get_json()["data"]["error"]["code"] == "idempotency_key_invalid"

    invalid_cursor = client.get(
        "/api/ml-intern-training/datasets?cursor=-1",
        headers=admin_auth_header,
    )
    assert invalid_cursor.status_code == 422
    assert invalid_cursor.get_json()["data"]["error"]["code"] == "cursor_invalid"


def test_dataset_listing_paginates_beyond_first_200_rows(app, client, admin_auth_header, tmp_path: Path) -> None:
    _configure(app, tmp_path)
    repository = MlInternTrainingRepository()
    for index in range(205):
        content = f"pagecase-{index}".encode()
        repository.create_dataset(
            MlInternDatasetDB(
                tenant_id="admin",
                owner_subject="admin",
                name=f"Pagecase {index:03d}",
                status="ready",
                content_sha256=hashlib.sha256(content).hexdigest(),
                size_bytes=len(content),
                record_count=1,
                train_record_count=1,
                storage_ref=str(tmp_path / f"pagecase-{index}.jsonl"),
                train_storage_ref=str(tmp_path / f"pagecase-{index}.jsonl"),
            )
        )

    response = client.get(
        "/api/ml-intern-training/datasets?q=pagecase&cursor=200&limit=10",
        headers=admin_auth_header,
    )

    assert response.status_code == 200
    payload = response.get_json()["data"]
    assert payload["total"] == 205
    assert payload["count"] == 5
    assert payload["next_cursor"] is None


def test_dataset_delete_restores_sql_projection_when_catalog_delete_fails(
    app,
    client,
    admin_auth_header,
    tmp_path: Path,
    monkeypatch,
) -> None:
    _configure(app, tmp_path)
    created = client.post(
        "/api/ml-intern-training/datasets",
        headers=_headers(admin_auth_header, "delete-compensation-create"),
        json={"name": "Delete compensation", "records": _records()},
    )
    dataset_id = created.get_json()["data"]["id"]

    def fail_delete(self, **kwargs):
        del self, kwargs
        raise DatasetCatalogError("catalog_delete_failed", "simulated catalog deletion failure")

    monkeypatch.setattr(MlInternDatasetCatalogService, "delete_dataset", fail_delete)
    deleted = client.delete(
        f"/api/ml-intern-training/datasets/{dataset_id}",
        headers=_headers(admin_auth_header, "delete-compensation-request"),
    )

    assert deleted.status_code == 422
    assert (
        MlInternTrainingRepository().get_dataset(
            MlInternTrainingPrincipal("admin", "admin"),
            dataset_id,
        )
        is not None
    )


def test_job_history_filters_and_exact_cursor_use_filtered_total(
    app,
    client,
    admin_auth_header,
    tmp_path: Path,
) -> None:
    _configure(app, tmp_path)
    created = client.post(
        "/api/ml-intern-training/datasets",
        headers=_headers(admin_auth_header, "job-filter-dataset-create"),
        json={"name": "Job filter dataset", "records": _records()},
    )
    assert created.status_code == 201
    dataset_id = created.get_json()["data"]["id"]
    repository = MlInternTrainingRepository()
    nonce = uuid.uuid4().hex
    for index, (backend, status) in enumerate((("unsloth", "failed"), ("unsloth", "failed"), ("mock", "completed"))):
        repository.create_job(
            MlInternTrainingJobDB(
                tenant_id="admin",
                owner_subject="admin",
                task_id=f"job-filter-task-{nonce}-{index}",
                dataset_id=dataset_id,
                backend=backend,
                status=status,
                idempotency_key_digest=hashlib.sha256(f"idem-{nonce}-{index}".encode()).hexdigest(),
                request_digest=hashlib.sha256(f"request-{nonce}-{index}".encode()).hexdigest(),
            )
        )

    first = client.get(
        f"/api/ml-intern-training/jobs?backend=unsloth&dataset_id={dataset_id}&status=failed&limit=1",
        headers=admin_auth_header,
    )
    assert first.status_code == 200
    first_page = first.get_json()["data"]
    assert first_page["count"] == 1
    assert first_page["total"] == 2
    assert first_page["next_cursor"] == "1"
    assert first_page["items"][0]["backend"] == "unsloth"
    assert first_page["items"][0]["dataset_id"] == dataset_id

    second = client.get(
        f"/api/ml-intern-training/jobs?backend=unsloth&dataset_id={dataset_id}&status=failed&limit=1&cursor=1",
        headers=admin_auth_header,
    )
    second_page = second.get_json()["data"]
    assert second_page["count"] == 1
    assert second_page["total"] == 2
    assert second_page["next_cursor"] is None

    invalid = client.get(
        "/api/ml-intern-training/jobs?backend=remote-shell",
        headers=admin_auth_header,
    )
    assert invalid.status_code == 422
    assert invalid.get_json()["data"]["error"]["code"] == "job_backend_invalid"


def test_separately_uploaded_validation_dataset_can_be_attached_without_leakage(
    app,
    client,
    admin_auth_header,
    tmp_path: Path,
) -> None:
    _configure(app, tmp_path)

    def upload(name: str, records: list[dict[str, str]], key: str) -> str:
        response = client.post(
            "/api/ml-intern-training/datasets",
            headers=_headers(admin_auth_header, key),
            json={"name": name, "validation_ratio": 0.25, "records": records},
        )
        assert response.status_code == 201, response.get_data(as_text=True)
        return response.get_json()["data"]["id"]

    train_id = upload("Train source", _records(8, prefix="Train "), "external-train-001")
    validation_id = upload(
        "Validation source",
        _records(4, prefix="Validation "),
        "external-validation-001",
    )
    attached = client.post(
        f"/api/ml-intern-training/datasets/{train_id}/validation-dataset",
        headers=_headers(admin_auth_header, "attach-validation-001"),
        json={"validation_dataset_id": validation_id},
    )

    assert attached.status_code == 200, attached.get_data(as_text=True)
    payload = attached.get_json()["data"]
    assert payload["train_record_count"] == 8
    assert payload["validation_record_count"] == 4
    assert payload["trainable"] is True
    assert payload["external_validation"] == {
        "dataset_id": validation_id,
        "semantic_overlap_count": 0,
        "algorithm_version": "external-validation-dataset-v1",
    }
    assert str(tmp_path) not in json.dumps(payload)

    preview = client.get(
        f"/api/ml-intern-training/datasets/{train_id}/records?split=validation&limit=10",
        headers=admin_auth_header,
    )
    assert preview.status_code == 200
    assert len(preview.get_json()["data"]["items"]) == 4

    referenced_delete = client.delete(
        f"/api/ml-intern-training/datasets/{validation_id}",
        headers=_headers(admin_auth_header, "external-validation-delete-001"),
    )
    assert referenced_delete.status_code == 409
    assert referenced_delete.get_json()["data"]["error"]["code"] == "dataset_referenced"


def test_adapter_import_approval_export_and_rollback_are_hash_bound(
    app,
    client,
    admin_auth_header,
    tmp_path: Path,
    monkeypatch,
) -> None:
    from agent.routes import ml_intern_training as training_routes

    audit_events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        training_routes,
        "log_audit",
        lambda action, details=None: audit_events.append((action, dict(details or {}))),
    )
    artifact_root = _configure(app, tmp_path)
    imported = client.post(
        "/api/ml-intern-training/adapters/import",
        headers=_headers(admin_auth_header, "adapter-import-route-001"),
        data={
            "adapter_id": "route-adapter-v1",
            "name": "Route adapter",
            "version": "1",
            "base_model_id": "local/base",
            "method": "lora",
            "bundle": (io.BytesIO(_adapter_bundle()), "route-adapter.zip"),
        },
        content_type="multipart/form-data",
    )
    assert imported.status_code == 201, imported.get_data(as_text=True)
    adapter = imported.get_json()["data"]
    assert adapter["status"] == "trained"
    assert adapter["hash_verified"] is True
    assert len(adapter["sha256"]) == 64
    assert str(tmp_path) not in json.dumps(adapter)

    premature = client.post(
        "/api/ml-intern-training/adapters/route-adapter-v1/approve",
        headers=_headers(admin_auth_header, "adapter-premature-approve-001"),
        json={"confirmed": True, "reason": "approve only after evaluation"},
    )
    assert premature.status_code == 409
    assert premature.get_json()["data"]["error"]["code"] == ("adapter_evaluation_binding_mismatch")

    registry = MlInternAdapterRegistryService(artifact_root / "adapter_registry.json")
    evaluation_job, replayed = MlInternTrainingRepository().create_job(
        MlInternTrainingJobDB(
            tenant_id="admin",
            owner_subject="admin",
            task_id=f"task-eval-{uuid.uuid4().hex}",
            dataset_id=None,
            job_type="evaluate_lora",
            mode="dry_run",
            backend="mock",
            base_model="local/base",
            status="completed",
            phase="completed",
            idempotency_key_digest=uuid.uuid4().hex * 2,
            request_digest=uuid.uuid4().hex * 2,
            request_spec={"adapter_id": "route-adapter-v1", "base_model": "local/base"},
            adapter_id="route-adapter-v1",
        )
    )
    assert replayed is False
    registry.set_eval_report(
        "route-adapter-v1",
        eval_report_ref=evaluation_job.id,
        eval_score=0.25,
        tenant_id="admin",
        owner_subject="admin",
    )
    approved = client.post(
        "/api/ml-intern-training/adapters/route-adapter-v1/approve",
        headers=_headers(admin_auth_header, "adapter-approve-route-001"),
        json={
            "confirmed": True,
            "reason": "evaluation score exceeds threshold",
            "expected_version": 2,
        },
    )
    assert approved.status_code == 200, approved.get_data(as_text=True)
    assert approved.get_json()["data"]["status"] == "approved"
    assert approved.get_json()["data"]["version"] == 1
    assert approved.get_json()["data"]["registry_version"] == 3

    stale = client.post(
        "/api/ml-intern-training/adapters/route-adapter-v1/deprecate",
        headers=_headers(admin_auth_header, "adapter-stale-deprecate-001"),
        json={
            "confirmed": True,
            "reason": "stale operator view must be rejected",
            "expected_version": 2,
        },
    )
    assert stale.status_code == 409
    assert stale.get_json()["data"]["error"]["code"] == "adapter_version_conflict"
    assert (
        registry.get(
            "route-adapter-v1",
            tenant_id="admin",
            owner_subject="admin",
        ).status
        == "approved"
    )

    exported = client.post(
        "/api/ml-intern-training/adapters/route-adapter-v1/export",
        headers=_headers(admin_auth_header, "adapter-export-route-001"),
        json={},
    )
    assert exported.status_code == 201, exported.get_data(as_text=True)
    export = exported.get_json()["data"]
    downloaded = client.get(export["download_url"], headers=admin_auth_header)
    assert downloaded.status_code == 200
    assert downloaded.headers["X-Artifact-SHA256"] == export["sha256"]
    with zipfile.ZipFile(io.BytesIO(downloaded.data)) as archive:
        assert "adapter_model.safetensors" in archive.namelist()
        assert "ananta_export_manifest.json" in archive.namelist()

    rolled_back = client.post(
        "/api/ml-intern-training/adapters/route-adapter-v1/rollback",
        headers=_headers(admin_auth_header, "adapter-rollback-route-001"),
        json={
            "confirmed": True,
            "reason": "operator rollback verification",
            "expected_version": 3,
        },
    )
    assert rolled_back.status_code == 200
    rollback = rolled_back.get_json()["data"]
    assert rollback["status"] == "deprecated"
    assert rollback["rollback_target"] == {
        "type": "base_model_only",
        "base_model_id": "local/base",
    }
    assert {action for action, _details in audit_events} >= {
        "ml_intern_adapter_imported",
        "ml_intern_adapter_decision",
    }


def test_adapter_routes_hide_foreign_tenant_and_owner_records(
    app,
    client,
    admin_auth_header,
    tmp_path: Path,
) -> None:
    artifact_root = _configure(app, tmp_path)
    registry = MlInternAdapterRegistryService(artifact_root / "adapter_registry.json")
    registry.register(
        adapter_id="foreign-adapter",
        display_name="Foreign adapter",
        version="artifact-1",
        base_model="local/base",
        tenant_id="tenant-foreign",
        owner_subject="alice",
    )

    listed = client.get(
        "/api/ml-intern-training/adapters",
        headers=admin_auth_header,
    )
    assert listed.status_code == 200
    assert listed.get_json()["data"]["items"] == []

    decision = client.post(
        "/api/ml-intern-training/adapters/foreign-adapter/deprecate",
        headers=_headers(admin_auth_header, "foreign-adapter-decision-001"),
        json={
            "confirmed": True,
            "reason": "foreign lifecycle action must be hidden",
            "expected_version": 1,
        },
    )
    assert decision.status_code == 404
    assert decision.get_json()["data"]["error"]["code"] == "adapter_not_found"
    assert (
        registry.get(
            "foreign-adapter",
            tenant_id="tenant-foreign",
            owner_subject="alice",
        ).status
        == "created"
    )

    exported = client.post(
        "/api/ml-intern-training/adapters/foreign-adapter/export",
        headers=_headers(admin_auth_header, "foreign-adapter-export-001"),
        json={},
    )
    assert exported.status_code == 404


def test_generic_approval_cannot_bypass_local_release_gates(
    app,
    client,
    admin_auth_header,
    tmp_path: Path,
) -> None:
    artifact_root = _configure(app, tmp_path)
    registry = MlInternAdapterRegistryService(artifact_root / "adapter_registry.json")
    registry.register_trained(
        adapter_id="needle-candidate",
        display_name="Needle candidate",
        version="1",
        base_model="needle-base-pinned",
        method="lora",
        artifact_paths={"adapter_dir": str(artifact_root / "needle-candidate")},
        config_hash="a" * 64,
        artifact_sha256="b" * 64,
        release_target="needle2",
        tenant_id="admin",
        owner_subject="admin",
    )

    response = client.post(
        "/api/ml-intern-training/adapters/needle-candidate/approve",
        headers=_headers(admin_auth_header, "needle-generic-approval-blocked"),
        json={
            "confirmed": True,
            "reason": "must pass governed local release gates",
            "expected_version": 1,
        },
    )

    assert response.status_code == 409
    assert response.get_json()["data"]["error"]["code"] == ("local_adapter_governed_release_required")
    assert (
        registry.get(
            "needle-candidate",
            tenant_id="admin",
            owner_subject="admin",
        ).status
        == "trained"
    )


@pytest.mark.parametrize(
    ("failure", "expected_status", "expected_code"),
    [
        pytest.param(
            RegistryError("simulated register conflict"),
            409,
            "adapter_registry_conflict",
            id="register-conflict",
        ),
        pytest.param(
            OSError("simulated registry I/O failure"),
            500,
            "adapter_registry_write_failed",
            id="registry-write-failure",
        ),
    ],
)
def test_adapter_import_compensates_new_files_when_domain_publication_fails(
    app,
    client,
    admin_auth_header,
    tmp_path: Path,
    monkeypatch,
    failure: Exception,
    expected_status: int,
    expected_code: str,
) -> None:
    artifact_root = _configure(app, tmp_path)

    def fail_publication(_self, *_args, **_kwargs):
        raise failure

    monkeypatch.setattr(MlInternAdapterRegistryService, "register_trained", fail_publication)
    response = client.post(
        "/api/ml-intern-training/adapters/import",
        headers=_headers(admin_auth_header, f"adapter-compensation-{expected_status}"),
        data={
            "adapter_id": "compensated-adapter",
            "name": "Compensated adapter",
            "version": "1",
            "base_model_id": "local/base",
            "method": "lora",
            "bundle": (io.BytesIO(_adapter_bundle()), "compensated-adapter.zip"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == expected_status, response.get_data(as_text=True)
    assert response.get_json()["data"]["error"]["code"] == expected_code
    import_service = MlInternAdapterImportService(storage_root=artifact_root / "adapter-imports")
    assert import_service.list_imports(tenant_id="admin", principal_id="admin") == []
    assert list((artifact_root / "adapter-imports").rglob("adapter_model.safetensors")) == []
    assert MlInternAdapterRegistryService(artifact_root / "adapter_registry.json").list_adapters() == []


def test_adapter_import_conflict_never_deletes_a_preexisting_identical_import(
    app,
    client,
    admin_auth_header,
    tmp_path: Path,
    monkeypatch,
) -> None:
    artifact_root = _configure(app, tmp_path)
    import_service = MlInternAdapterImportService(storage_root=artifact_root / "adapter-imports")
    bundle = _adapter_bundle()
    existing = import_service.import_archive(
        tenant_id="admin",
        principal_id="admin",
        stream=io.BytesIO(bundle),
        filename="preexisting.zip",
        media_type="application/zip",
        adapter_id="preexisting-adapter",
        version="1",
        expected_base_model="local/base",
        declared_size=len(bundle),
    )
    existing_path = import_service.resolve_artifact_path(
        tenant_id="admin",
        principal_id="admin",
        adapter_id="preexisting-adapter",
        version="1",
    )

    def fail_register(_self, *_args, **_kwargs):
        raise RegistryError("simulated domain conflict")

    monkeypatch.setattr(MlInternAdapterRegistryService, "register_trained", fail_register)
    response = client.post(
        "/api/ml-intern-training/adapters/import",
        headers=_headers(admin_auth_header, "preexisting-import-conflict-001"),
        data={
            "adapter_id": "preexisting-adapter",
            "version": "1",
            "base_model_id": "local/base",
            "method": "lora",
            "bundle": (io.BytesIO(bundle), "preexisting.zip"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 409
    assert response.get_json()["data"]["error"]["code"] == "adapter_registry_conflict"
    assert existing_path.exists()
    assert import_service.list_imports(tenant_id="admin", principal_id="admin") == [existing]


def test_atomic_import_publication_resumes_hash_bound_legacy_partial_record(
    app,
    client,
    admin_auth_header,
    tmp_path: Path,
) -> None:
    artifact_root = _configure(app, tmp_path)
    bundle = _adapter_bundle()
    import_service = MlInternAdapterImportService(storage_root=artifact_root / "adapter-imports")
    imported = import_service.import_archive(
        tenant_id="admin",
        principal_id="admin",
        stream=io.BytesIO(bundle),
        filename="transition-recovery.zip",
        media_type="application/zip",
        adapter_id="transition-recovery-adapter",
        version="1",
        expected_base_model="local/base",
        declared_size=len(bundle),
    )
    artifact_path = import_service.resolve_artifact_path(
        tenant_id="admin",
        principal_id="admin",
        adapter_id="transition-recovery-adapter",
        version="1",
    )
    artifact_sha256 = MlInternArtifactSecurityService(storage_root=artifact_root).validate_adapter_tree(artifact_path)[
        "tree_sha256"
    ]
    MlInternAdapterRegistryService(artifact_root / "adapter_registry.json").register(
        adapter_id="transition-recovery-adapter",
        display_name="Interrupted adapter",
        version="1",
        base_model="local/base",
        method="lora",
        artifact_paths={"adapter_dir": str(artifact_path)},
        config_hash=imported["content_sha256"],
        artifact_sha256=artifact_sha256,
    )

    retried = client.post(
        "/api/ml-intern-training/adapters/import",
        headers=_headers(admin_auth_header, "transition-recovery-retry-001"),
        data={
            "adapter_id": "transition-recovery-adapter",
            "version": "1",
            "base_model_id": "local/base",
            "method": "lora",
            "bundle": (io.BytesIO(bundle), "transition-recovery.zip"),
        },
        content_type="multipart/form-data",
    )

    assert retried.status_code == 201, retried.get_data(as_text=True)
    assert retried.get_json()["data"]["status"] == "trained"
    imports = MlInternAdapterImportService(storage_root=artifact_root / "adapter-imports").list_imports(
        tenant_id="admin",
        principal_id="admin",
    )
    assert len(imports) == 1
    assert imports[0]["adapter_id"] == "transition-recovery-adapter"
