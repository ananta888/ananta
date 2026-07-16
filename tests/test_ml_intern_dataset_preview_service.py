from __future__ import annotations

import itertools
from contextlib import contextmanager
from pathlib import Path

import pytest

from agent.services.ml_intern_dataset_catalog_service import MlInternDatasetCatalogService
from agent.services.ml_intern_dataset_preview_service import (
    DatasetPreviewError,
    DatasetPreviewPolicy,
    MlInternDatasetPreviewService,
)


def _catalog(tmp_path: Path) -> MlInternDatasetCatalogService:
    ids = (f"ds-{index:032x}" for index in itertools.count(1))
    return MlInternDatasetCatalogService(
        storage_root=tmp_path / "catalog",
        id_factory=lambda: next(ids),
    )


def _records(count: int) -> list[dict]:
    return [
        {"instruction": f"Instruction {index}", "output": f"Output {index}"}
        for index in range(count)
    ]


def test_preview_cursor_pages_records_without_paths_or_cross_tenant_data(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    created = catalog.create_from_records(
        tenant_id="tenant-a", principal_id="alice", records=_records(7), name="Paged"
    )
    catalog.validate_dataset(
        tenant_id="tenant-a", principal_id="alice", dataset_id=created["dataset_id"]
    )
    preview = MlInternDatasetPreviewService(catalog)

    first = preview.get_page(
        tenant_id="tenant-a", principal_id="alice", dataset_id=created["dataset_id"], limit=3
    )
    second = preview.get_page(
        tenant_id="tenant-a",
        principal_id="alice",
        dataset_id=created["dataset_id"],
        limit=3,
        cursor=first["next_cursor"],
    )
    third = preview.get_page(
        tenant_id="tenant-a",
        principal_id="alice",
        dataset_id=created["dataset_id"],
        limit=3,
        cursor=second["next_cursor"],
    )
    assert [row["record_index"] for row in first["records"]] == [0, 1, 2]
    assert [row["record_index"] for row in second["records"]] == [3, 4, 5]
    assert [row["record_index"] for row in third["records"]] == [6]
    assert third["next_cursor"] is None
    assert first["state"] == "ready"
    assert str(tmp_path) not in str(first)

    with pytest.raises(DatasetPreviewError) as exc:
        preview.get_page(
            tenant_id="tenant-b", principal_id="alice", dataset_id=created["dataset_id"]
        )
    assert exc.value.reason_code == "dataset_not_found"


def test_preview_redacts_secrets_pii_paths_and_truncates_text(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    created = catalog.create_from_records(
        tenant_id="t",
        principal_id="p",
        records=[
            {
                "instruction": "Inspect configuration",
                "output": "api_key=abcdefghijklmnop alice@example.test /home/alice/private " + "x" * 200,
                "source_path": "/home/alice/source.jsonl",
            }
        ],
        name="Sensitive",
    )
    preview = MlInternDatasetPreviewService(
        catalog,
        policy=DatasetPreviewPolicy(max_text_chars=64),
    )
    page = preview.get_page(
        tenant_id="t", principal_id="p", dataset_id=created["dataset_id"]
    )
    serialized = str(page)
    assert "abcdefghijklmnop" not in serialized
    assert "alice@example.test" not in serialized
    assert "/home/alice" not in serialized
    assert "[REDACTED]" in serialized
    assert "…" in serialized
    assert page["state"] == "not_validated"


def test_statistics_project_counts_distribution_and_split_sizes(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    created = catalog.create_from_records(
        tenant_id="t", principal_id="p", records=_records(5), name="Stats"
    )
    stats = MlInternDatasetPreviewService(catalog).get_statistics(
        tenant_id="t", principal_id="p", dataset_id=created["dataset_id"]
    )
    assert stats["record_count"] == 5
    assert stats["format_distribution"]["instruction"] == 5
    assert stats["split_sizes"] == {"train": 5}
    assert stats["state"] == "not_validated"


def test_preview_replaces_non_finite_json_numbers() -> None:
    class NonFiniteCatalog:
        def partition_descriptor(self, **_kwargs):
            return {"record_count": 1, "sha256": "a" * 64, "validation_status": "passed"}

        @contextmanager
        def open_partition(self, **_kwargs):
            yield ['{"instruction":"Inspect","output":"Captured","metric":NaN}\n']

    page = MlInternDatasetPreviewService(NonFiniteCatalog()).get_page(
        tenant_id="t", principal_id="p", dataset_id="ds-" + "1" * 32
    )

    assert page["records"][0]["record"]["metric"] == "[INVALID_NUMBER]"


class _BoundedFakeCatalog:
    def __init__(self) -> None:
        self.lines_read = 0

    def partition_descriptor(self, **_kwargs):
        return {"record_count": 10_000, "sha256": "a" * 64, "validation_status": "passed"}

    @contextmanager
    def open_partition(self, **_kwargs):
        owner = self

        class Lines:
            def __iter__(self):
                for index in range(10_000):
                    owner.lines_read += 1
                    if owner.lines_read > 3:
                        raise AssertionError("preview read beyond page plus lookahead")
                    yield '{"instruction":"Prompt %d","output":"Answer"}\n' % index

        yield Lines()


def test_page_streaming_stops_after_limit_plus_one_lookahead() -> None:
    fake = _BoundedFakeCatalog()
    page = MlInternDatasetPreviewService(fake).get_page(
        tenant_id="t", principal_id="p", dataset_id="ds-" + "1" * 32, limit=2
    )
    assert page["returned_count"] == 2
    assert page["next_cursor"]
    assert fake.lines_read == 3
