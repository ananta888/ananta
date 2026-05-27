import hashlib
import json
import time
from typing import Any, List, Optional

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

    def query(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        trace_id: str | None = None,
        task_id: str | None = None,
        actor: str | None = None,
        action: str | None = None,
        event_class: str | None = None,
        since: float | None = None,
        until: float | None = None,
    ) -> List[AuditLogDB]:
        with Session(engine) as session:
            statement = select(AuditLogDB).order_by(AuditLogDB.timestamp.desc())
            if trace_id:
                statement = statement.where(AuditLogDB.trace_id == trace_id)
            if task_id:
                statement = statement.where(AuditLogDB.task_id == task_id)
            if actor:
                statement = statement.where(AuditLogDB.username == actor)
            if action:
                statement = statement.where(AuditLogDB.action == action)
            if since is not None:
                statement = statement.where(AuditLogDB.timestamp >= float(since))
            if until is not None:
                statement = statement.where(AuditLogDB.timestamp <= float(until))
            rows = list(session.exec(statement).all())
        if event_class:
            normalized_class = str(event_class or "").strip().lower()
            rows = [
                row
                for row in rows
                if str((row.details or {}).get("operation_type") or row.action or "").strip().lower() == normalized_class
            ]
        safe_offset = max(0, int(offset))
        safe_limit = max(1, min(int(limit), 1000))
        return rows[safe_offset : safe_offset + safe_limit]

    def summarize(self, *, since: float | None = None, limit: int = 1000) -> dict[str, Any]:
        with Session(engine) as session:
            statement = select(AuditLogDB).order_by(AuditLogDB.timestamp.desc()).limit(max(1, min(int(limit), 10000)))
            if since is not None:
                statement = statement.where(AuditLogDB.timestamp >= float(since))
            rows = list(session.exec(statement).all())
        by_action: dict[str, int] = {}
        by_outcome: dict[str, int] = {}
        critical_events = 0
        for row in rows:
            by_action[row.action] = by_action.get(row.action, 0) + 1
            outcome = str((row.details or {}).get("outcome") or "unknown").strip().lower()
            by_outcome[outcome] = by_outcome.get(outcome, 0) + 1
            action_lower = str(row.action or "").lower()
            details = dict(row.details or {})
            if (
                "blocked" in action_lower
                or "failed" in action_lower
                or "violation" in action_lower
                or outcome in {"blocked", "failed", "denied", "violation"}
                or bool(details.get("unexpected_transition"))
            ):
                critical_events += 1
        top_actions = sorted(by_action.items(), key=lambda item: item[1], reverse=True)[:10]
        return {
            "total_events": len(rows),
            "critical_events": critical_events,
            "by_outcome": by_outcome,
            "top_actions": [{"action": name, "count": count} for name, count in top_actions],
            "generated_at": time.time(),
        }

    def integrity_report(self, *, limit: int = 5000) -> dict[str, Any]:
        with Session(engine) as session:
            statement = select(AuditLogDB).order_by(AuditLogDB.id.asc()).limit(max(1, min(int(limit), 20000)))
            rows = list(session.exec(statement).all())
        mismatched_prev_hash_ids: list[int] = []
        invalid_record_hash_ids: list[int] = []
        legacy_unhashed_ids: list[int] = []
        previous_hash = None
        for row in rows:
            if row.prev_hash != previous_hash:
                mismatched_prev_hash_ids.append(int(row.id or 0))
            if row.record_hash:
                hash_payload = {
                    "username": row.username,
                    "ip": row.ip,
                    "action": row.action,
                    "details": dict(row.details or {}),
                    "prev_hash": row.prev_hash,
                }
                expected_hash = hashlib.sha256(
                    json.dumps(hash_payload, sort_keys=True, ensure_ascii=True, default=str).encode("utf-8")
                ).hexdigest()
                if (row.record_hash or "") != expected_hash:
                    invalid_record_hash_ids.append(int(row.id or 0))
            else:
                legacy_unhashed_ids.append(int(row.id or 0))
            previous_hash = row.record_hash
        return {
            "checked_records": len(rows),
            "tamper_evident_ok": not mismatched_prev_hash_ids and not invalid_record_hash_ids,
            "mismatched_prev_hash_ids": mismatched_prev_hash_ids,
            "invalid_record_hash_ids": invalid_record_hash_ids,
            "legacy_unhashed_ids": legacy_unhashed_ids,
        }

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
