from __future__ import annotations

import json
from pathlib import Path

import pytest

from worker.training.exports import (
    ExportError,
    ExportFormat,
    ExportRequest,
    UnslothExportExecutor,
)


class FakeModel:
    def save_pretrained(self, path: str) -> None:
        Path(path, "adapter.bin").write_bytes(b"adapter")

    def save_pretrained_merged(
        self,
        path: str,
        tokenizer: object,
        *,
        save_method: str,
    ) -> None:
        Path(path, "model.bin").write_bytes(save_method.encode())

    def save_pretrained_gguf(
        self,
        path: str,
        tokenizer: object,
        *,
        quantization_method: str,
    ) -> None:
        Path(path, "model.gguf").write_bytes(quantization_method.encode())


class FakeTokenizer:
    def save_pretrained(self, path: str) -> None:
        Path(path, "tokenizer.json").write_text("{}", encoding="utf-8")


def request(destination: str, format: ExportFormat) -> ExportRequest:
    return ExportRequest(
        tenant_id="tenant-a",
        job_id="job-a",
        attempt_id="attempt-a",
        dataset_hash="a" * 64,
        base_model_hash="b" * 64,
        destination=destination,
        format=format,
        quantization_method="q4_k_m" if format is ExportFormat.GGUF else None,
    )


@pytest.mark.parametrize(
    "format",
    [ExportFormat.ADAPTER, ExportFormat.MERGED_16BIT, ExportFormat.GGUF],
)
def test_export_formats_publish_manifest_atomically(
    tmp_path: Path,
    format: ExportFormat,
) -> None:
    result = UnslothExportExecutor(artifact_root=tmp_path).execute(
        model=FakeModel(),
        tokenizer=FakeTokenizer(),
        request=request(f"artifact-{format.value}", format),
    )

    manifest_path = tmp_path / result.destination / "ananta-export-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["artifact_sha256"] == result.artifact_sha256
    assert manifest["dataset_hash"] == "a" * 64


def test_export_rejects_artifact_root_escape(tmp_path: Path) -> None:
    with pytest.raises(ExportError) as error:
        UnslothExportExecutor(artifact_root=tmp_path).execute(
            model=FakeModel(),
            tokenizer=FakeTokenizer(),
            request=request("../escape", ExportFormat.ADAPTER),
        )

    assert error.value.code == "export_destination_invalid"
