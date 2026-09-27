"""Hub-owned access roles, their bindings to identities, and revisions of both (WCRB-003).

A role is a named bundle of grants (operation groups and operations to allow
or deny, plus constraints such as paths or index ids). A binding attaches a
role to an identity: a local user, or a Keycloak group, realm role or client
role, optionally only within one tenant or project. Every change stores a
full snapshot as a revision, so any earlier state can be restored.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Optional

import sqlalchemy as sa
from sqlmodel import JSON, Column, Field, SQLModel


class AccessRoleDB(SQLModel, table=True):
    __tablename__ = "access_roles"
    id: str = Field(primary_key=True)  # slug, e.g. "developer"
    name: str
    description: str = ""
    builtin: bool = Field(default=False)
    grants: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    updated_at: float = Field(default_factory=time.time)


class AccessRoleBindingDB(SQLModel, table=True):
    __tablename__ = "access_role_bindings"
    __table_args__ = (
        sa.UniqueConstraint(
            "subject_kind", "subject", "role_id", "tenant_id", "project_id", name="uq_access_role_binding"
        ),
    )
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    subject_kind: str = Field(index=True)  # local_user | oidc_group | oidc_realm_role | oidc_client_role
    subject: str = Field(index=True)
    role_id: str = Field(index=True, foreign_key="access_roles.id")
    tenant_id: Optional[str] = Field(default=None, index=True)  # None: every tenant
    project_id: Optional[str] = Field(default=None, index=True)  # None: every project
    created_at: float = Field(default_factory=time.time)
    created_by: str = ""


class AccessPolicyRevisionDB(SQLModel, table=True):
    __tablename__ = "access_policy_revisions"
    revision: int = Field(primary_key=True)
    snapshot: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    actor: str = ""
    reason: str = ""
    created_at: float = Field(default_factory=time.time)
