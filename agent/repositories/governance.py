from typing import List, Optional

from sqlalchemy import or_
from sqlmodel import Session, select

from agent.database import engine
from agent.db_models import ActionPackDB, AuditLogDB, PolicyDecisionDB, VerificationRecordDB


class PolicyDecisionRepository:
    def get_all(self, limit: int = 200):
        with Session(engine) as session:
            statement = select(PolicyDecisionDB).order_by(PolicyDecisionDB.created_at.desc()).limit(limit)
            return session.exec(statement).all()

    def get_by_task_id(self, task_id: str) -> List[PolicyDecisionDB]:
        with Session(engine) as session:
            statement = (
                select(PolicyDecisionDB)
                .where(PolicyDecisionDB.task_id == task_id)
                .order_by(PolicyDecisionDB.created_at.desc())
            )
            return session.exec(statement).all()

    def get_by_goal_or_task_ids(self, goal_id: str, task_ids: list[str] | None = None, limit: int = 500) -> List[PolicyDecisionDB]:
        with Session(engine) as session:
            statement = select(PolicyDecisionDB).order_by(PolicyDecisionDB.created_at.desc())
            filters = [PolicyDecisionDB.goal_id == goal_id]
            safe_task_ids = [str(task_id).strip() for task_id in (task_ids or []) if str(task_id).strip()]
            if safe_task_ids:
                filters.append(PolicyDecisionDB.task_id.in_(safe_task_ids))
            statement = statement.where(or_(*filters)).limit(max(1, min(int(limit), 5000)))
            return session.exec(statement).all()

    def save(self, decision: PolicyDecisionDB):
        with Session(engine) as session:
            session.add(decision)
            session.commit()
            session.refresh(decision)
            return decision


class VerificationRecordRepository:
    def get_by_id(self, record_id: str) -> Optional[VerificationRecordDB]:
        with Session(engine) as session:
            return session.get(VerificationRecordDB, record_id)

    def get_by_task_id(self, task_id: str) -> List[VerificationRecordDB]:
        with Session(engine) as session:
            statement = (
                select(VerificationRecordDB)
                .where(VerificationRecordDB.task_id == task_id)
                .order_by(VerificationRecordDB.created_at.desc())
            )
            return session.exec(statement).all()

    def get_by_goal_id(self, goal_id: str) -> List[VerificationRecordDB]:
        with Session(engine) as session:
            statement = (
                select(VerificationRecordDB)
                .where(VerificationRecordDB.goal_id == goal_id)
                .order_by(VerificationRecordDB.created_at.desc())
            )
            return session.exec(statement).all()

    def get_by_goal_or_task_ids(
        self, goal_id: str, task_ids: list[str] | None = None, limit: int = 500
    ) -> List[VerificationRecordDB]:
        with Session(engine) as session:
            statement = select(VerificationRecordDB).order_by(VerificationRecordDB.created_at.desc())
            filters = [VerificationRecordDB.goal_id == goal_id]
            safe_task_ids = [str(task_id).strip() for task_id in (task_ids or []) if str(task_id).strip()]
            if safe_task_ids:
                filters.append(VerificationRecordDB.task_id.in_(safe_task_ids))
            statement = statement.where(or_(*filters)).limit(max(1, min(int(limit), 5000)))
            return session.exec(statement).all()

    def save(self, record: VerificationRecordDB):
        with Session(engine) as session:
            merged = session.merge(record)
            session.commit()
            session.refresh(merged)
            return merged


class AuditLogRepository:
    def get_all(self, limit: int = 100, offset: int = 0):
        with Session(engine) as session:
            statement = select(AuditLogDB).order_by(AuditLogDB.timestamp.desc()).limit(limit).offset(offset)
            return session.exec(statement).all()

    def save(self, log_entry: AuditLogDB):
        with Session(engine) as session:
            session.add(log_entry)
            session.commit()
            session.refresh(log_entry)
            return log_entry


class ActionPackRepository:
    def get_by_id(self, action_pack_id: str) -> Optional[ActionPackDB]:
        with Session(engine) as session:
            return session.get(ActionPackDB, action_pack_id)

    def get_by_name(self, name: str) -> Optional[ActionPackDB]:
        with Session(engine) as session:
            statement = select(ActionPackDB).where(ActionPackDB.name == name)
            return session.exec(statement).first()

    def get_all(self, enabled_only: bool = False) -> List[ActionPackDB]:
        with Session(engine) as session:
            statement = select(ActionPackDB)
            if enabled_only:
                statement = statement.where(ActionPackDB.enabled == True)
            return session.exec(statement).all()

    def save(self, action_pack: ActionPackDB):
        with Session(engine) as session:
            merged = session.merge(action_pack)
            session.commit()
            session.refresh(merged)
            return merged

    def delete(self, action_pack_id: str):
        with Session(engine) as session:
            action_pack = session.get(ActionPackDB, action_pack_id)
            if action_pack:
                session.delete(action_pack)
                session.commit()
                return True
            return False
