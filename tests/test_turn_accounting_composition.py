from flask import Flask
from sqlalchemy import create_engine
from sqlmodel import SQLModel

from agent.bootstrap.turn_accounting import initialize_turn_accounting
from agent.db_models.turn_accounting import (
    TurnAccountingLedgerDB,
    TurnAccountingSourceCursorDB,
)
from agent.repositories.turn_accounting_repository import SqlTurnAccountingRepository


def test_turn_accounting_composition_is_durable_or_fail_closed(tmp_path):
    unavailable = Flask("turn-accounting-unavailable")
    status = initialize_turn_accounting(unavailable)
    assert status.ready is False
    assert "turn_accounting_service" not in unavailable.extensions

    engine = create_engine(f"sqlite:///{tmp_path / 'composition.db'}")
    SQLModel.metadata.create_all(
        engine,
        tables=[
            TurnAccountingLedgerDB.__table__,
            TurnAccountingSourceCursorDB.__table__,
        ],
    )
    app = Flask("turn-accounting-ready")
    app.secret_key = "production-secret"
    status = initialize_turn_accounting(app, db_engine=engine)

    assert status.ready is True
    assert isinstance(
        app.extensions["turn_accounting_repository"],
        SqlTurnAccountingRepository,
    )
    assert app.extensions["turn_accounting_service"] is not None
