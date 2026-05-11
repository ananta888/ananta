from __future__ import annotations
from typing import List, Optional
from sqlmodel import Session, select, and_
from agent.database import engine
from agent.db_models import ContextAccessPolicyDB

class ContextAccessPolicyRepository:
    def get_by_id(self, policy_id: str, version: int) -> Optional[ContextAccessPolicyDB]:
        with Session(engine) as session:
            statement = select(ContextAccessPolicyDB).where(
                and_(
                    ContextAccessPolicyDB.policy_id == policy_id,
                    ContextAccessPolicyDB.version == version
                )
            )
            return session.exec(statement).first()

    def get_latest_version(self, policy_id: str) -> Optional[ContextAccessPolicyDB]:
        with Session(engine) as session:
            statement = select(ContextAccessPolicyDB).where(
                ContextAccessPolicyDB.policy_id == policy_id
            ).order_by(ContextAccessPolicyDB.version.desc())
            return session.exec(statement).first()

    def save(self, policy_db: ContextAccessPolicyDB) -> ContextAccessPolicyDB:
        with Session(engine) as session:
            session.add(policy_db)
            session.commit()
            session.refresh(policy_db)
            return policy_db

    def find_by_project(self, project_id: str) -> List[ContextAccessPolicyDB]:
        with Session(engine) as session:
            statement = select(ContextAccessPolicyDB).where(
                ContextAccessPolicyDB.project_id == project_id
            ).order_by(ContextAccessPolicyDB.version.desc())
            return session.exec(statement).all()

    def find_by_scope(self, scope: str) -> List[ContextAccessPolicyDB]:
        with Session(engine) as session:
            statement = select(ContextAccessPolicyDB).where(
                ContextAccessPolicyDB.scope == scope
            ).order_by(ContextAccessPolicyDB.version.desc())
            return session.exec(statement).all()

_instance = ContextAccessPolicyRepository()

def get_context_access_policy_repo() -> ContextAccessPolicyRepository:
    return _instance
