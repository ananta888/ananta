"""SQLAlchemy ORM models and session management for the controller/agent DB.

This module maps to the tables created by src.db.init_db() and provides a
SQLAlchemy session factory for use across the application.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime
from typing import Iterator, Optional

from sqlalchemy import (
    create_engine,
    Integer,
    String,
    Text,
    DateTime,
    Index,
    func,
)
from sqlalchemy.orm import declarative_base, Mapped, mapped_column, scoped_session, sessionmaker, Session
from sqlalchemy.dialects.postgresql import JSONB

# DATABASE_URL is shared with psycopg2 code to keep a single source of truth
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@db:5432/ananta")

# Engine and session configuration with reasonable defaults for performance
_engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    future=True,
)
_SessionFactory = scoped_session(sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False, future=True))

Base = declarative_base()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Provide a transactional scope around a series of operations."""
    session: Session = _SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


class ControllerConfig(Base):
    __tablename__ = "config"
    __table_args__ = {"schema": "controller"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class ControllerTask(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        Index("ix_tasks_agent_created", "agent", "created_at"),
        {"schema": "controller"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task: Mapped[str] = mapped_column(Text, nullable=False)
    agent: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    template: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class ControllerBlacklist(Base):
    __tablename__ = "blacklist"
    __table_args__ = {"schema": "controller"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cmd: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class ControlLog(Base):
    __tablename__ = "control_log"
    __table_args__ = {"schema": "controller"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    received: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    approved: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class AgentLog(Base):
    __tablename__ = "logs"
    __table_args__ = (
        Index("ix_agent_logs_agent_created", "agent", "created_at"),
        {"schema": "agent"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent: Mapped[str] = mapped_column(String(128), nullable=False)
    level: Mapped[str] = mapped_column(Text, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class AgentFlag(Base):
    __tablename__ = "flags"
    __table_args__ = {"schema": "agent"}

    name: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


__all__ = [
    "Base",
    "_engine",
    "_SessionFactory",
    "session_scope",
    "ControllerConfig",
    "ControllerTask",
    "ControllerBlacklist",
    "ControlLog",
    "AgentLog",
    "AgentFlag",
]
