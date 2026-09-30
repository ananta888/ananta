"""Tenant/project/owner-scoped row lookups shared by source-control stores.

Every function runs inside the caller's session so it participates in the
caller's transaction; a missing row fails closed with a stable reason code.
"""

from __future__ import annotations

from sqlmodel import Session, select

from agent.db_models.source_control import (
    KnowledgeIndexSourceBindingDB,
    SourceAccessGrantDB,
    SourceRevisionDB,
)
from agent.models.source_control_persistence import (
    SourceControlPersistenceError,
)


def require_scoped_revision(
    db: Session,
    *,
    tenant_id: str,
    project_id: str,
    owner_id: str,
    source_revision_id: str,
) -> SourceRevisionDB:
    row = db.exec(
        select(SourceRevisionDB).where(
            SourceRevisionDB.source_revision_id == source_revision_id,
            SourceRevisionDB.tenant_id == tenant_id,
            SourceRevisionDB.project_id == project_id,
            SourceRevisionDB.owner_id == owner_id,
        )
    ).first()
    if row is None:
        raise SourceControlPersistenceError(
            "source_control_revision_not_found"
        )
    return row


def require_scoped_grant(
    db: Session,
    *,
    tenant_id: str,
    project_id: str,
    owner_id: str,
    grant_id: str,
) -> SourceAccessGrantDB:
    row = db.exec(
        select(SourceAccessGrantDB).where(
            SourceAccessGrantDB.grant_id == grant_id,
            SourceAccessGrantDB.tenant_id == tenant_id,
            SourceAccessGrantDB.project_id == project_id,
            SourceAccessGrantDB.owner_id == owner_id,
        )
    ).first()
    if row is None:
        raise SourceControlPersistenceError(
            "source_control_grant_not_found"
        )
    return row


def require_scoped_index(
    db: Session,
    *,
    tenant_id: str,
    project_id: str,
    owner_id: str,
    knowledge_index_id: str,
) -> KnowledgeIndexSourceBindingDB:
    row = db.exec(
        select(KnowledgeIndexSourceBindingDB).where(
            KnowledgeIndexSourceBindingDB.knowledge_index_id
            == knowledge_index_id,
            KnowledgeIndexSourceBindingDB.tenant_id == tenant_id,
            KnowledgeIndexSourceBindingDB.project_id == project_id,
            KnowledgeIndexSourceBindingDB.owner_id == owner_id,
        )
    ).first()
    if row is None:
        raise SourceControlPersistenceError(
            "source_control_index_binding_not_found"
        )
    return row
