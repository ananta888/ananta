from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.unsloth_dataset_admission import (
    UnslothDatasetAdmissionError,
    materialize_admitted_dolly_recipe,
)


def test_non_synthetic_dataset_is_scanned_and_recipe_bound(tmp_path: Path) -> None:
    source = tmp_path / "dolly.jsonl"
    rows = [
        {
            "instruction": f"Question {index}",
            "context": "",
            "response": f"Answer {index}",
            "category": "open_qa",
        }
        for index in range(24)
    ]
    source.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    contract = tmp_path / "contract.json"
    contract.write_text(
        json.dumps(
            {
                "schema": "ananta.unsloth-dataset-admission.v1",
                "dataset_id": "dolly-test",
                "origin": "https://example.invalid/dolly.jsonl",
                "upstream_revision": "a" * 40,
                "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "source_record_count": len(rows),
                "license": {
                    "spdx": "CC-BY-SA-3.0",
                    "approved_scope": "local_nonproduction_evaluation",
                },
                "selection": {
                    "candidate_limit": 20,
                    "record_count": 16,
                    "allowed_categories": ["open_qa"],
                    "require_empty_context": True,
                },
            }
        ),
        encoding="utf-8",
    )

    result = materialize_admitted_dolly_recipe(
        source_path=source,
        contract_path=contract,
        output_root=tmp_path / "output",
        source_id="SRC_dataset",
        run_id="RUN_training",
        attempt_id="unsloth-" + "1" * 32,
    )

    assert result["validation"] == {
        "ok": True,
        "reason_codes": [],
        "pii_finding_count": 0,
        "secret_finding_count": 0,
    }
    assert result["manifest"]["dataset_hash"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert result["result"]["source_id"] == "SRC_dataset"
    assert result["result"]["run_id"] == "RUN_training"
    assert result["result"]["train_rows"] + result["result"]["validation_rows"] == 16


def test_dataset_admission_rejects_source_symlink(tmp_path: Path) -> None:
    source = tmp_path / "source.jsonl"
    source.write_text("{}\n", encoding="utf-8")
    linked_source = tmp_path / "linked.jsonl"
    linked_source.symlink_to(source)
    contract = tmp_path / "contract.json"
    contract.write_text(
        json.dumps(
            {
                "schema": "ananta.unsloth-dataset-admission.v1",
                "dataset_id": "dolly-test",
                "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "source_record_count": 1,
                "license": {"approved_scope": "local_nonproduction_evaluation"},
                "selection": {"candidate_limit": 1, "record_count": 1},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(UnslothDatasetAdmissionError, match="dataset_source_binding_invalid"):
        materialize_admitted_dolly_recipe(
            source_path=linked_source,
            contract_path=contract,
            output_root=tmp_path / "output",
            source_id="SRC_dataset",
            run_id="RUN_training",
            attempt_id="unsloth-" + "1" * 32,
        )
