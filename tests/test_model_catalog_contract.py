from __future__ import annotations

import subprocess
import sys
import warnings

import pytest
from pydantic import ValidationError

from ananta_contracts.model_catalog import (
    MODEL_CATALOG_SCHEMA,
    MODEL_DEFAULT_SELECTION_COMMAND_SCHEMA,
    MODEL_DEFAULT_SELECTION_SCHEMA,
    MODEL_SUMMARY_SCHEMA,
    ModelCatalog,
    ModelDefaultSelection,
    ModelDefaultSelectionCommand,
    ModelSummary,
)


def _summary_payload() -> dict[str, object]:
    return {
        "schema": MODEL_SUMMARY_SCHEMA,
        "provider_id": "openai",
        "runtime": "cloud",
        "model_id": "gpt-safe",
        "display_name": "GPT Safe",
        "availability": "available",
        "loaded": None,
        "context_window": 32768,
        "quantization": None,
        "capabilities": ["chat"],
        "health": "healthy",
        "is_default": True,
    }


def test_schema_alias_round_trips_without_changing_wire_contract() -> None:
    summary = ModelSummary.model_validate(_summary_payload())
    selection = ModelDefaultSelection.model_validate(
        {
            "schema": MODEL_DEFAULT_SELECTION_SCHEMA,
            "provider_id": "openai",
            "model_id": "gpt-safe",
        }
    )
    command = ModelDefaultSelectionCommand.model_validate(
        {
            "schema": MODEL_DEFAULT_SELECTION_COMMAND_SCHEMA,
            "provider_id": "openai",
            "model_id": "gpt-safe",
        }
    )
    catalog = ModelCatalog.model_validate(
        {
            "schema": MODEL_CATALOG_SCHEMA,
            "default_selection": selection.model_dump(
                mode="json",
                by_alias=True,
            ),
            "models": [
                summary.model_dump(mode="json", by_alias=True)
            ],
            "provider_failures": [],
        }
    )

    assert summary.schema_version == MODEL_SUMMARY_SCHEMA
    assert selection.schema_version == MODEL_DEFAULT_SELECTION_SCHEMA
    assert (
        command.schema_version
        == MODEL_DEFAULT_SELECTION_COMMAND_SCHEMA
    )
    assert catalog.schema_version == MODEL_CATALOG_SCHEMA
    assert summary.model_dump(mode="json", by_alias=True) == (
        _summary_payload()
    )
    assert catalog.to_wire()["schema"] == MODEL_CATALOG_SCHEMA
    assert "schema_version" not in catalog.to_wire()
    assert "schema_version" not in catalog.to_wire()["models"][0]


def test_schema_alias_contract_remains_fail_closed_for_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ModelSummary.model_validate(
            {
                **_summary_payload(),
                "api_key": "must-not-be-accepted",
            }
        )

    with pytest.raises(ValidationError):
        ModelDefaultSelectionCommand.model_validate(
            {
                "schema": MODEL_DEFAULT_SELECTION_COMMAND_SCHEMA,
                "provider_id": "openai",
                "model_id": "gpt-safe",
                "base_url": "https://attacker.invalid",
            }
        )


def test_import_and_model_construction_are_warning_free() -> None:
    script = """
import warnings
warnings.simplefilter("error")
from ananta_contracts.model_catalog import ModelSummary
ModelSummary(
    provider_id="openai",
    runtime="cloud",
    model_id="gpt-safe",
    display_name="GPT Safe",
    availability="available",
    health="healthy",
)
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        ModelSummary.model_validate(_summary_payload())
