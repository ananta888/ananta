from __future__ import annotations

import json
from pathlib import Path

from ananta_contracts.kanban import (
    KanbanBoard,
    KanbanCard,
    KanbanRevisionConflictResponse,
)
from ananta_contracts.kanban_events import KanbanEvent
from ananta_contracts.model_catalog import ModelSummary


FIXTURE_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "kanban_model_dashboard"
    / "kanban-model-dashboard.v1.json"
)


def _fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_shared_fixture_validates_against_product_pydantic_contracts() -> None:
    fixture = _fixture()
    board = KanbanBoard.model_validate(fixture["board"])
    card = KanbanCard.model_validate(fixture["card"])
    event = KanbanEvent.model_validate(fixture["event"])
    conflict = KanbanRevisionConflictResponse.model_validate(
        fixture["error"]["body"]
    )
    model = ModelSummary.model_validate(fixture["model_summary"])

    assert fixture["fixture_version"] == "kanban-model-dashboard.fixture.v1"
    assert fixture["_meta"]["deterministic"] is True
    assert card.board_id == board.id
    assert event.board_id == board.id
    assert event.task_id == card.id
    assert event.revision == card.revision == 7
    assert event.sequence == 42
    assert fixture["error"]["http_status"] == 409
    assert conflict.error.code == "kanban_revision_conflict"
    assert conflict.error.details.current_revision == 8
    assert model.model_id == "safe-model"


def test_shared_fixture_roundtrips_without_contract_alias_drift() -> None:
    fixture = _fixture()

    assert KanbanBoard.model_validate(fixture["board"]).model_dump(
        mode="json"
    ) == fixture["board"]
    assert KanbanCard.model_validate(fixture["card"]).model_dump(
        mode="json"
    ) == fixture["card"]
    assert KanbanEvent.model_validate(fixture["event"]).model_dump(
        mode="json"
    ) == fixture["event"]
    assert ModelSummary.model_validate(fixture["model_summary"]).model_dump(
        mode="json",
        by_alias=True,
    ) == fixture["model_summary"]


def test_fixture_documents_exact_cross_runtime_consumers() -> None:
    fixture = _fixture()

    assert fixture["_meta"]["dependent_tests"] == [
        "tests/test_kanban_model_dashboard_shared_contract.py",
        (
            "tests/client_surfaces/operator_tui/"
            "test_dashboard_shared_contract_fixture.py"
        ),
        (
            "frontend-angular/src/app/contracts/"
            "kanban-model-dashboard.fixture.spec.ts"
        ),
        (
            "tests/client_surfaces/operator_tui/"
            "test_kanban_cross_surface_e2e.py"
        ),
        "frontend-angular/tests/kanban-cross-surface-live-hub.spec.ts",
    ]
    serialized = FIXTURE_PATH.read_text(encoding="utf-8")
    assert "SRC_" not in serialized
    assert "RUN_" not in serialized
