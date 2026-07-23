"""Atomic SQLModel persistence adapter for the legacy default model keys."""

from __future__ import annotations

import json

from sqlmodel import Session

from agent.database import engine
from agent.db_models import ConfigDB


class SqlModelDefaultSelectionRepository:
    def save(self, *, provider_id: str, model_id: str) -> None:
        with Session(engine) as session:
            session.merge(
                ConfigDB(
                    key="default_provider",
                    value_json=json.dumps(provider_id),
                )
            )
            session.merge(
                ConfigDB(
                    key="default_model",
                    value_json=json.dumps(model_id),
                )
            )
            session.commit()


__all__ = ["SqlModelDefaultSelectionRepository"]
