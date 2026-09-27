"""Persistence of access roles, role bindings and their revisions (SQLModel)."""

from __future__ import annotations

import time
from typing import Any

from sqlmodel import Session, delete, select

from agent.database import engine
from agent.db_models import AccessPolicyRevisionDB, AccessRoleBindingDB, AccessRoleDB


class AccessRoleRepository:
    def __init__(self, bind: Any = None) -> None:
        self._engine = bind if bind is not None else engine

    # --- roles -----------------------------------------------------------------------------------

    def list_roles(self) -> list[AccessRoleDB]:
        with Session(self._engine) as session:
            return list(session.exec(select(AccessRoleDB).order_by(AccessRoleDB.id)).all())

    def get_role(self, role_id: str) -> AccessRoleDB | None:
        with Session(self._engine) as session:
            return session.get(AccessRoleDB, role_id)

    def save_role(self, role: AccessRoleDB) -> AccessRoleDB:
        role.updated_at = time.time()
        with Session(self._engine) as session:
            merged = session.merge(role)
            session.commit()
            session.refresh(merged)
            return merged

    def delete_role(self, role_id: str) -> bool:
        with Session(self._engine) as session:
            role = session.get(AccessRoleDB, role_id)
            if role is None:
                return False
            session.exec(delete(AccessRoleBindingDB).where(AccessRoleBindingDB.role_id == role_id))
            session.delete(role)
            session.commit()
            return True

    # --- bindings --------------------------------------------------------------------------------

    def list_bindings(self) -> list[AccessRoleBindingDB]:
        with Session(self._engine) as session:
            return list(session.exec(select(AccessRoleBindingDB).order_by(AccessRoleBindingDB.created_at)).all())

    def save_binding(self, binding: AccessRoleBindingDB) -> AccessRoleBindingDB:
        with Session(self._engine) as session:
            merged = session.merge(binding)
            session.commit()
            session.refresh(merged)
            return merged

    def delete_binding(self, binding_id: str) -> bool:
        with Session(self._engine) as session:
            binding = session.get(AccessRoleBindingDB, binding_id)
            if binding is None:
                return False
            session.delete(binding)
            session.commit()
            return True

    # --- revisions -------------------------------------------------------------------------------

    def replace_all(self, roles: list[dict[str, Any]], bindings: list[dict[str, Any]]) -> None:
        """Restore a snapshot: every role and binding is replaced in one transaction."""
        with Session(self._engine) as session:
            session.exec(delete(AccessRoleBindingDB))
            session.exec(delete(AccessRoleDB))
            for row in roles:
                session.add(AccessRoleDB(**row))
            session.flush()
            for row in bindings:
                session.add(AccessRoleBindingDB(**row))
            session.commit()

    def add_revision(self, snapshot: dict[str, Any], *, actor: str, reason: str) -> AccessPolicyRevisionDB:
        with Session(self._engine) as session:
            latest = session.exec(
                select(AccessPolicyRevisionDB.revision).order_by(AccessPolicyRevisionDB.revision.desc()).limit(1)
            ).first()
            revision = AccessPolicyRevisionDB(revision=int(latest or 0) + 1, snapshot=snapshot, actor=actor,
                                              reason=reason)
            session.add(revision)
            session.commit()
            session.refresh(revision)
            return revision

    def list_revisions(self, limit: int = 50) -> list[AccessPolicyRevisionDB]:
        with Session(self._engine) as session:
            return list(session.exec(
                select(AccessPolicyRevisionDB).order_by(AccessPolicyRevisionDB.revision.desc()).limit(limit)
            ).all())

    def get_revision(self, revision: int) -> AccessPolicyRevisionDB | None:
        with Session(self._engine) as session:
            return session.get(AccessPolicyRevisionDB, revision)
