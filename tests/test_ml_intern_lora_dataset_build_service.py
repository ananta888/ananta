"""Tests fuer MlInternLoraDatasetBuildService."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.services.ml_intern_lora_dataset_build_service import (
    DatasetBuildError,
    MlInternLoraDatasetBuildService,
)


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(item) for item in records) + "\n", encoding="utf-8")


def test_build_instruction_dataset_from_curated_records(tmp_path):
    svc = MlInternLoraDatasetBuildService(dataset_root=tmp_path)
    result = svc.build_dataset({
        "output_path": "train.jsonl",
        "records": [
            {"instruction": "Erzeuge Todo JSON", "output": '{"tasks":[]}', "quality_label": "approved"},
            {"instruction": "Erzeuge Todo JSON", "output": '{"tasks":[]}', "quality_label": "approved"},
            {"instruction": "x", "output": "too short"},
        ],
        "require_secret_scan": True,
    })

    assert result.status == "completed"
    assert result.dataset_path == "train.jsonl"
    assert result.written_records == 1
    assert result.duplicate_count == 1
    assert result.validation_report is not None
    assert result.validation_report["ok"] is True
    data = (tmp_path / "train.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(data) == 1


def test_build_dataset_from_jsonl_source_file(tmp_path):
    source = tmp_path / "curated" / "examples.jsonl"
    _write_jsonl(source, [{"prompt": "Plane zwei Schritte", "response": "1. Pruefen\n2. Ausfuehren"}])
    svc = MlInternLoraDatasetBuildService(dataset_root=tmp_path)

    result = svc.build_dataset({
        "source_paths": ["curated/examples.jsonl"],
        "output_path": "built/train.jsonl",
        "format": "instruction",
        "require_secret_scan": False,
    })

    assert result.status == "completed"
    assert result.dataset_path == "built/train.jsonl"
    payload = json.loads((tmp_path / "built" / "train.jsonl").read_text(encoding="utf-8"))
    assert payload["instruction"] == "Plane zwei Schritte"
    assert payload["output"].startswith("1.")


def test_build_chat_dataset_from_instruction_records(tmp_path):
    svc = MlInternLoraDatasetBuildService(dataset_root=tmp_path)
    result = svc.build_dataset({
        "format": "chat",
        "output_path": "chat.jsonl",
        "records": [{"instruction": "Hallo", "output": "Hallo zurueck"}],
        "require_secret_scan": False,
    })

    assert result.status == "completed"
    record = json.loads((tmp_path / "chat.jsonl").read_text(encoding="utf-8"))
    assert record["messages"][0]["role"] == "user"
    assert record["messages"][1]["role"] == "assistant"


def test_build_dataset_secret_validation_failure(tmp_path):
    svc = MlInternLoraDatasetBuildService(dataset_root=tmp_path)
    result = svc.build_dataset({
        "output_path": "secret.jsonl",
        "records": [{"instruction": "Config", "output": "api_key: sk-abcdefghijklmnop12345678901234567890"}],
        "require_secret_scan": True,
    })

    assert result.status == "validation_failed"
    assert any("potential secret" in error for error in result.errors)


def test_build_dataset_rejects_output_path_escape(tmp_path):
    svc = MlInternLoraDatasetBuildService(dataset_root=tmp_path)
    with pytest.raises(DatasetBuildError, match="inside dataset_root"):
        svc.build_dataset({
            "output_path": "../escape.jsonl",
            "records": [{"instruction": "Hallo", "output": "Welt"}],
        })
