"""Atomic persistence adapter for Hub-owned cognitive-style state."""

from __future__ import annotations

from sqlalchemy import update
from sqlmodel import Session, select

from agent.database import engine
from agent.db_models import ConfigDB
from ananta_contracts.cognitive_style import (
    CognitiveStyleConfiguration,
    CognitiveStylePersistedState,
)

_KEY = "cognitive_style_state_v1"


class SqlCognitiveStyleStateRepository:
    def load(self) -> CognitiveStylePersistedState:
        with Session(engine) as session:
            row = session.exec(select(ConfigDB).where(ConfigDB.key == _KEY)).first()
            if row is None:
                return CognitiveStylePersistedState(
                    configuration=CognitiveStyleConfiguration(revision=0)
                )
            return CognitiveStylePersistedState.model_validate_json(row.value_json)

    def save_if_revision(
        self,
        expected_revision: int,
        state: CognitiveStylePersistedState,
    ) -> bool:
        serialized = state.model_dump_json(by_alias=True)
        with Session(engine) as session:
            row = session.exec(select(ConfigDB).where(ConfigDB.key == _KEY)).first()
            if row is None:
                if expected_revision != 0:
                    return False
                session.add(ConfigDB(key=_KEY, value_json=serialized))
                try:
                    session.commit()
                    return True
                except Exception:
                    session.rollback()
                    return False
            current = CognitiveStylePersistedState.model_validate_json(row.value_json)
            if current.configuration.revision != expected_revision:
                return False
            result = session.exec(
                update(ConfigDB)
                .where(ConfigDB.key == _KEY, ConfigDB.value_json == row.value_json)
                .values(value_json=serialized)
            )
            session.commit()
            return bool(result.rowcount == 1)


__all__ = ["SqlCognitiveStyleStateRepository"]
