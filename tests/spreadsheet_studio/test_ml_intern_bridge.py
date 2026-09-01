from __future__ import annotations

import json

import pytest

from agent.services.spreadsheet_ml_intern_bridge_service import SpreadsheetMlInternBridgeService


def _record() -> dict:
    return {
        "instruction": "Set the governed value",
        "input": json.dumps({"schema": "ananta.spreadsheet-training-context.v1", "cells": []}),
        "output": json.dumps(
            {
                "schema": "ananta.spreadsheet-action-output.v1",
                "actions": [
                    {
                        "action_id": "action-one",
                        "kind": "set_value",
                        "sheet_id": "sheet-one",
                        "cell": "A1",
                        "value": 42,
                        "formula": None,
                    }
                ],
            }
        ),
        "task_kind": "spreadsheet_actions",
        "privacy_class": "consented_masked",
        "quality_label": "accepted",
        "source_document_version": 1,
        "record_digest": "1" * 64,
        "feedback_id": "feedback-one",
        "consent_id": "consent-one",
        "consent_digest": "2" * 64,
        "lineage_root_id": "document-one",
        "split": "train",
        "recipe_version": "recipe-v1",
    }


def test_dry_run_dataset_is_admitted_to_ml_intern_with_spreadsheet_contract(tmp_path) -> None:
    path = tmp_path / "dataset.jsonl"
    path.write_text(json.dumps(_record()) + "\n")

    class Learning:
        def get_dataset(self, **_kwargs):
            return {
                "dataset_id": "dataset-one",
                "dataset_digest": "3" * 64,
                "split_seed": "split-v1",
                "split_percent": {"train": 70, "validation": 10, "eval": 10, "test": 10},
                "readiness": {"dry_run_ready": True, "training_ready": False},
            }

        def dataset_path(self, **_kwargs):
            return path

    class Catalog:
        def create_from_records(self, **kwargs):
            assert list(kwargs["records"])[0]["task_kind"] == "spreadsheet_actions"
            return {"dataset_id": "ds-" + "a" * 32, "record_count": 1, "revision": 1}

    class Split:
        def split(self, **_kwargs):
            raise AssertionError("dry-run must not require a production split")

    class RepositoryBridge:
        def sync(self, principal, summary, **_kwargs):
            assert principal.tenant_id == "tenant-a"
            assert summary["record_count"] == 1
            return {"id": "ml-dataset-one"}

    class Control:
        def create_job(self, principal, command, *, idempotency_key):
            assert principal.subject == "user-a"
            assert command["task_family"] == "spreadsheet_actions"
            assert command["task_kinds"] == ["spreadsheet_actions"]
            assert len(command["output_schema_digest"]) == 64
            assert len(command["serializer_digest"]) == 64
            assert idempotency_key == "training-key-one"
            return {"id": "job-one", "status": "queued"}, False

    result = SpreadsheetMlInternBridgeService(
        learning=Learning(),
        catalog=Catalog(),
        split=Split(),
        repository_bridge=RepositoryBridge(),
        control=Control(),
    ).start_training(
        tenant_id="tenant-a",
        principal_id="user-a",
        payload={
            "schema": "ananta.spreadsheet-training-command.v1",
            "dataset_id": "dataset-one",
            "mode": "dry_run",
            "backend": "mock",
            "base_model": "model-one",
            "method": "qlora",
            "hyperparameters": {"max_steps": 1},
            "live_confirmed": False,
            "risk_reason": "",
        },
        idempotency_key="training-key-one",
    )
    assert result["job"]["id"] == "job-one"
    assert result["human_intervention_required"] is False


def test_live_training_fails_before_ml_intern_when_dataset_is_not_ready(tmp_path) -> None:
    class Learning:
        def get_dataset(self, **_kwargs):
            return {
                "dataset_id": "dataset-one",
                "readiness": {"dry_run_ready": True, "training_ready": False},
            }

    service = SpreadsheetMlInternBridgeService(
        learning=Learning(),
        catalog=object(),
        split=object(),
        repository_bridge=object(),
        control=object(),
    )
    with pytest.raises(PermissionError, match="dataset_not_ready"):
        service.start_training(
            tenant_id="tenant-a",
            principal_id="user-a",
            payload={
                "schema": "ananta.spreadsheet-training-command.v1",
                "dataset_id": "dataset-one",
                "mode": "live",
                "backend": "unsloth",
                "base_model": "model-one",
                "method": "qlora",
                "hyperparameters": {},
                "live_confirmed": True,
                "risk_reason": "automatic governed production training",
            },
            idempotency_key="training-key-one",
        )


def test_live_training_requires_digest_bound_hub_admission_before_catalog_access() -> None:
    class Learning:
        def get_dataset(self, **_kwargs):
            return {
                "dataset_id": "dataset-one",
                "dataset_digest": "3" * 64,
                "readiness": {"dry_run_ready": True, "training_ready": True},
            }

        def dataset_path(self, **_kwargs):
            raise AssertionError("catalog path must not be read before admission")

    service = SpreadsheetMlInternBridgeService(
        learning=Learning(),
        catalog=object(),
        split=object(),
        repository_bridge=object(),
        control=object(),
    )
    with pytest.raises(PermissionError, match="training_admission_required"):
        service.start_training(
            tenant_id="tenant-a",
            principal_id="user-a",
            payload={
                "schema": "ananta.spreadsheet-training-command.v1",
                "dataset_id": "dataset-one",
                "mode": "live",
                "backend": "unsloth",
                "base_model": "model-one",
                "method": "qlora",
                "hyperparameters": {},
                "live_confirmed": True,
                "risk_reason": "automatic governed production training",
            },
            idempotency_key="training-key-live",
        )
