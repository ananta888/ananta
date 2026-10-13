"""Scoped append-only snapshots with optimistic concurrency for Hub search."""

from __future__ import annotations

import json
from collections.abc import Callable

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.db_models.harness_search import HarnessSearchRevisionDB
from ananta_contracts.harness_genome import canonical_json, content_digest


class HarnessSearchConflict(ValueError):
    pass


def _engine():
    from agent.database import engine

    return engine


class HarnessSearchStore:
    def __init__(self, *, engine: Callable | None = None):
        self._engine = engine or _engine

    def get(self, *, tenant_id: str, project_id: str, session_id: str) -> dict:
        with Session(self._engine()) as session:
            row = session.exec(
                select(HarnessSearchRevisionDB)
                .where(
                    HarnessSearchRevisionDB.session_id == session_id,
                    HarnessSearchRevisionDB.tenant_id == tenant_id,
                    HarnessSearchRevisionDB.project_id == project_id,
                )
                .order_by(HarnessSearchRevisionDB.revision.desc())
            ).first()
            if row is None:
                raise ValueError("harness_search_unknown")
            payload = json.loads(row.payload_json)
            if content_digest(payload) != row.content_digest:
                raise ValueError("harness_search_state_mutated")
            return {"revision": row.revision, "state": payload}

    def list(self, *, tenant_id: str, project_id: str, limit: int = 100) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("harness_search_list_limit_invalid")
        with Session(self._engine()) as session:
            ids = session.exec(
                select(HarnessSearchRevisionDB.session_id)
                .where(
                    HarnessSearchRevisionDB.tenant_id == tenant_id,
                    HarnessSearchRevisionDB.project_id == project_id,
                )
                .distinct()
                .order_by(HarnessSearchRevisionDB.session_id)
                .limit(limit)
            ).all()
        return [
            {"session_id": session_id, **self.get(tenant_id=tenant_id, project_id=project_id, session_id=session_id)}
            for session_id in ids
        ]

    def append(self, *, tenant_id: str, project_id: str, session_id: str, state: dict, expected_revision: int) -> dict:
        if (
            any(
                not isinstance(value, str) or not value or len(value) > 200
                for value in (tenant_id, project_id, session_id)
            )
            or type(expected_revision) is not int
        ):
            raise ValueError("harness_search_scope_invalid")
        encoded = canonical_json(state)
        if len(encoded.encode()) > 4 * 1024**2:
            raise ValueError("harness_search_state_too_large")
        if expected_revision >= 0:
            current = self.get(tenant_id=tenant_id, project_id=project_id, session_id=session_id)
            if current["revision"] != expected_revision:
                raise HarnessSearchConflict("harness_search_revision_conflict")
            for field in ("policy", "baseline", "suite_id", "created_at"):
                if current["state"][field] != state[field]:
                    raise ValueError("harness_search_immutable_binding_changed")
        elif expected_revision != -1:
            raise ValueError("harness_search_revision_invalid")
        with Session(self._engine()) as session:
            session.add(
                HarnessSearchRevisionDB(
                    session_id=session_id,
                    revision=expected_revision + 1,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    content_digest=content_digest(state),
                    payload_json=encoded,
                )
            )
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                raise HarnessSearchConflict("harness_search_revision_conflict") from None
        return {"revision": expected_revision + 1, "state": json.loads(encoded)}
