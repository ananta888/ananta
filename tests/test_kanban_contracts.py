import pytest
from pydantic import ValidationError

from ananta_contracts.kanban import CreateBoardCommand, CreateCardCommand, MoveCardCommand


def test_contracts_are_strict_and_scope_safe() -> None:
    with pytest.raises(ValidationError):
        CreateBoardCommand(scope_type="hub", scope_id="x", idempotency_key="board")
    with pytest.raises(ValidationError):
        CreateCardCommand(title="card", idempotency_key="card", unexpected=True)
    with pytest.raises(ValidationError):
        MoveCardCommand(
            board_id="hub",
            expected_revision=-1,
            idempotency_key="",
            column_id="in_progress",
            position=0,
        )

