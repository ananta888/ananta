from __future__ import annotations

import io
import itertools
import json
import multiprocessing
import time
from pathlib import Path

import pytest

from agent.services.ml_intern_artifact_security_service import (
    ArtifactSecurityPolicy,
    MlInternArtifactSecurityService,
)
from agent.services.ml_intern_dataset_catalog_service import (
    DatasetCatalogError,
    MlInternDatasetCatalogService,
)


def _records(count: int = 6) -> list[dict]:
    return [
        {"instruction": f"Prompt number {index}", "output": f"Response number {index}"}
        for index in range(count)
    ]


def _catalog(tmp_path: Path, *, audit=None, max_file_bytes: int = 1024 * 1024) -> MlInternDatasetCatalogService:
    policy = ArtifactSecurityPolicy(
        max_file_bytes=max_file_bytes,
        max_request_bytes=max_file_bytes * 2,
        max_tenant_bytes=max_file_bytes * 8,
        max_archive_uncompressed_bytes=max_file_bytes * 2,
    )
    security = MlInternArtifactSecurityService(storage_root=tmp_path / "catalog", policy=policy)
    ids = (f"ds-{index:032x}" for index in itertools.count(1))
    return MlInternDatasetCatalogService(
        storage_root=tmp_path / "catalog",
        security=security,
        audit_sink=audit,
        id_factory=lambda: next(ids),
        clock=lambda: 1_700_000_000,
    )


def _jsonl(records: list[dict]) -> bytes:
    return ("\n".join(json.dumps(record) for record in records) + "\n").encode("utf-8")


class _SlowIdempotencyCatalog(MlInternDatasetCatalogService):
    def _resolve_idempotency(self, **kwargs):
        existing = super()._resolve_idempotency(**kwargs)
        if existing is None:
            time.sleep(0.15)
        return existing


def _create_dataset_in_process(storage_root, barrier, results):
    try:
        service = _SlowIdempotencyCatalog(storage_root=storage_root)
        payload = _jsonl(_records(4))
        barrier.wait(timeout=10)
        created = service.create_from_upload(
            tenant_id="tenant",
            principal_id="owner",
            stream=io.BytesIO(payload),
            filename="train.jsonl",
            media_type="application/x-ndjson",
            name="Concurrent training",
            idempotency_key="shared-upload-key",
        )
        results.put(created["dataset_id"])
    except Exception as exc:  # pragma: no cover - reported in the parent process
        results.put(f"error:{exc!r}")


def test_upload_returns_opaque_summary_and_reuses_existing_builder(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    payload = _jsonl(_records())
    summary = catalog.create_from_upload(
        tenant_id="tenant-a",
        principal_id="alice",
        stream=io.BytesIO(payload),
        filename="training.jsonl",
        media_type="application/x-ndjson",
        name="Training examples",
        declared_size=len(payload),
    )

    assert summary["dataset_id"].startswith("ds-")
    assert summary["record_count"] == 6
    assert summary["validation"]["status"] == "pending"
    assert summary["validation"]["trainable"] is False
    assert summary["sha256"] and summary["size_bytes"] > 0
    serialized = json.dumps(summary)
    assert str(tmp_path) not in serialized
    assert "relative_path" not in serialized

    report = catalog.validate_dataset(
        tenant_id="tenant-a", principal_id="alice", dataset_id=summary["dataset_id"]
    )
    assert report["ok"] is True
    assert "dataset_path" not in json.dumps(report)
    assert catalog.get_dataset(
        tenant_id="tenant-a", principal_id="alice", dataset_id=summary["dataset_id"]
    )["validation"]["trainable"] is True


def test_curated_records_are_stream_bounded_and_empty_is_rejected(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    summary = catalog.create_from_records(
        tenant_id="t", principal_id="owner", records=iter(_records(4)), name="Curated"
    )
    assert summary["input_record_count"] == 4

    with pytest.raises(DatasetCatalogError) as exc:
        catalog.create_from_records(
            tenant_id="t", principal_id="owner", records=[], name="Empty"
        )
    assert exc.value.reason_code == "empty_dataset"


def test_idempotency_same_digest_returns_existing_and_conflict_is_rejected(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    first_payload = _jsonl(_records(4))
    kwargs = {
        "tenant_id": "tenant",
        "principal_id": "owner",
        "filename": "train.jsonl",
        "media_type": "application/x-ndjson",
        "name": "Training",
        "idempotency_key": "upload-1",
    }
    first = catalog.create_from_upload(stream=io.BytesIO(first_payload), **kwargs)
    same = catalog.create_from_upload(stream=io.BytesIO(first_payload), **kwargs)
    assert same["dataset_id"] == first["dataset_id"]
    assert len(catalog.list_datasets(tenant_id="tenant", principal_id="owner")) == 1

    with pytest.raises(DatasetCatalogError) as exc:
        catalog.create_from_upload(stream=io.BytesIO(_jsonl(_records(5))), **kwargs)
    assert exc.value.reason_code == "idempotency_conflict"


def test_dataset_idempotency_is_atomic_across_hub_processes(tmp_path: Path) -> None:
    context = multiprocessing.get_context("fork")
    storage_root = tmp_path / "catalog"
    barrier = context.Barrier(3)
    results = context.Queue()
    processes = [
        context.Process(
            target=_create_dataset_in_process,
            args=(storage_root, barrier, results),
        )
        for _index in range(2)
    ]
    for process in processes:
        process.start()
    barrier.wait(timeout=10)
    for process in processes:
        process.join(timeout=30)
        assert process.exitcode == 0

    dataset_ids = [results.get(timeout=2) for _process in processes]
    assert not any(dataset_id.startswith("error:") for dataset_id in dataset_ids)
    assert len(set(dataset_ids)) == 1
    persisted = MlInternDatasetCatalogService(storage_root=storage_root).list_datasets(
        tenant_id="tenant",
        principal_id="owner",
    )
    assert [row["dataset_id"] for row in persisted] == dataset_ids[:1]


def test_tenant_and_principal_isolation_fail_as_not_found(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    created = catalog.create_from_records(
        tenant_id="tenant-a", principal_id="alice", records=_records(4), name="Private"
    )
    assert catalog.list_datasets(tenant_id="tenant-a", principal_id="bob") == []
    assert catalog.list_datasets(tenant_id="tenant-b", principal_id="alice") == []
    for tenant, principal in (("tenant-a", "bob"), ("tenant-b", "alice")):
        with pytest.raises(DatasetCatalogError) as exc:
            catalog.get_dataset(tenant_id=tenant, principal_id=principal, dataset_id=created["dataset_id"])
        assert exc.value.reason_code == "dataset_not_found"


def test_file_quota_and_referenced_delete_are_enforced(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path, max_file_bytes=256)
    with pytest.raises(DatasetCatalogError) as exc:
        catalog.create_from_upload(
            tenant_id="t",
            principal_id="p",
            stream=io.BytesIO(b"{" + b"x" * 300),
            filename="large.json",
            media_type="application/json",
        )
    assert exc.value.reason_code == "file_quota_exceeded"

    created = catalog.create_from_records(
        tenant_id="t", principal_id="p", records=_records(2), name="Referenced"
    )
    catalog.mark_referenced(
        tenant_id="t",
        principal_id="p",
        dataset_id=created["dataset_id"],
        reference_id="job-1",
    )
    with pytest.raises(DatasetCatalogError) as exc:
        catalog.delete_dataset(
            tenant_id="t", principal_id="p", dataset_id=created["dataset_id"]
        )
    assert exc.value.reason_code == "dataset_referenced"


def test_pii_is_default_deny_and_admin_override_is_audited_without_raw_value(tmp_path: Path) -> None:
    audits: list[tuple[str, dict]] = []
    catalog = _catalog(tmp_path, audit=lambda action, details: audits.append((action, details)))
    created = catalog.create_from_records(
        tenant_id="t",
        principal_id="admin",
        records=[{"instruction": "Contact alice@example.test please", "output": "Request accepted"}],
        name="PII",
    )
    blocked = catalog.validate_dataset(
        tenant_id="t", principal_id="admin", dataset_id=created["dataset_id"]
    )
    assert blocked["ok"] is False
    assert "pii_detected" in blocked["reason_codes"]
    assert "alice@example.test" not in json.dumps(blocked)

    with pytest.raises(DatasetCatalogError) as exc:
        catalog.validate_dataset(
            tenant_id="t",
            principal_id="admin",
            dataset_id=created["dataset_id"],
            allow_sensitive_override=True,
            is_admin=False,
            override_reason="approved synthetic contact fixture",
        )
    assert exc.value.reason_code == "sensitive_override_denied"

    allowed = catalog.validate_dataset(
        tenant_id="t",
        principal_id="admin",
        dataset_id=created["dataset_id"],
        allow_sensitive_override=True,
        is_admin=True,
        override_reason="approved synthetic contact fixture",
    )
    assert allowed["ok"] is True
    assert audits == [
        (
            "ml_intern_dataset_sensitive_override",
            {
                "dataset_id": created["dataset_id"],
                "reason": "approved synthetic contact fixture",
                "pii_finding_count": 1,
            },
        )
    ]
