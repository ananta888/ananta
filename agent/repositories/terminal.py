from __future__ import annotations

import time
from typing import Optional

from sqlmodel import Session, select

from agent.database import engine
from agent.db_models import TerminalEventDB, TerminalSessionDB


class TerminalSessionRepository:
    _ALLOWED_STATUS = {"created", "running", "attached", "detached", "expired", "killed", "failed"}

    def get_by_id(self, session_id: str) -> Optional[TerminalSessionDB]:
        with Session(engine) as session:
            return session.get(TerminalSessionDB, session_id)

    def list_all(self) -> list[TerminalSessionDB]:
        with Session(engine) as session:
            return list(session.exec(select(TerminalSessionDB).order_by(TerminalSessionDB.created_at.desc())).all())

    def list_by_user_id(self, user_id: str) -> list[TerminalSessionDB]:
        with Session(engine) as session:
            stmt = select(TerminalSessionDB).where(TerminalSessionDB.created_by_user_id == str(user_id)).order_by(
                TerminalSessionDB.created_at.desc()
            )
            return list(session.exec(stmt).all())

    def save(self, entry: TerminalSessionDB) -> TerminalSessionDB:
        if not str(entry.target_type or "").strip():
            raise ValueError("terminal_session_missing_target_type")
        if not str(entry.target_id or "").strip():
            raise ValueError("terminal_session_missing_target_id")
        if not str(entry.created_by_user_id or "").strip():
            raise ValueError("terminal_session_missing_user_id")
        if not str(entry.policy_decision_id or "").strip():
            raise ValueError("terminal_session_missing_policy_decision_id")
        if entry.status not in self._ALLOWED_STATUS:
            raise ValueError("terminal_session_invalid_status")
        now = time.time()
        entry.updated_at = now
        if not entry.created_at:
            entry.created_at = now
        with Session(engine) as session:
            merged = session.merge(entry)
            session.commit()
            session.refresh(merged)
            return merged

    def transition_status(self, session_id: str, status: str) -> Optional[TerminalSessionDB]:
        if status not in self._ALLOWED_STATUS:
            raise ValueError("terminal_session_invalid_status")
        with Session(engine) as session:
            item = session.get(TerminalSessionDB, session_id)
            if item is None:
                return None
            item.status = status
            item.updated_at = time.time()
            session.add(item)
            session.commit()
            session.refresh(item)
            return item


class TerminalEventRepository:
    def append(self, event: TerminalEventDB) -> TerminalEventDB:
        with Session(engine) as session:
            session.add(event)
            session.commit()
            session.refresh(event)
            return event

    def list_by_session(self, session_id: str) -> list[TerminalEventDB]:
        with Session(engine) as session:
            stmt = select(TerminalEventDB).where(TerminalEventDB.session_id == str(session_id)).order_by(TerminalEventDB.timestamp.asc())
            return list(session.exec(stmt).all())
