"""Atomic persistence for the Hub-owned model routing configuration."""

from __future__ import annotations

from sqlalchemy import update
from sqlmodel import Session, select

from agent.database import engine
from agent.db_models import ConfigDB
from ananta_contracts.model_selection import ModelRoutingConfiguration

_KEY = "model_routing_configuration_v1"


class SqlModelRoutingConfigurationRepository:
    def load(self) -> ModelRoutingConfiguration:
        with Session(engine) as session:
            row = session.exec(select(ConfigDB).where(ConfigDB.key == _KEY)).first()
            if row is None:
                return ModelRoutingConfiguration(revision=0)
            return ModelRoutingConfiguration.model_validate_json(row.value_json)

    def save_if_revision(
        self, expected_revision: int, value: ModelRoutingConfiguration
    ) -> bool:
        serialized = value.model_dump_json(by_alias=True)
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
            try:
                current = ModelRoutingConfiguration.model_validate_json(row.value_json)
            except Exception:
                return False
            if current.revision != expected_revision:
                return False
            result = session.exec(
                update(ConfigDB)
                .where(ConfigDB.key == _KEY, ConfigDB.value_json == row.value_json)
                .values(value_json=serialized)
            )
            session.commit()
            return bool(result.rowcount == 1)


__all__ = ["SqlModelRoutingConfigurationRepository"]
