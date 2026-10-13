"""OHA-008: MemoryTreeStoreService — CRUD layer for Memory Tree entities.

Provides content-addressed chunk storage, lifecycle management and
basic node (source/topic/global) tree operations. No LLM dependencies.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time

from sqlmodel import Session, select

import agent.database as _db_module
from agent.db_models import MemoryTreeChunkDB, MemoryTreeJobDB, MemoryTreeNodeDB
from agent.db_models.memory_quarantine import MemoryQuarantineEventDB
from agent.services.memory_tree_visibility import BLOCKED_LIFECYCLES, READABLE_LIFECYCLES

logger = logging.getLogger(__name__)

_LIFECYCLE_ORDER = [
    "pending_extraction",
    "admitted",
    "buffered",
    "sealed",
    "dropped",
]


# ---------------------------------------------------------------------------
# ID helpers
# ---------------------------------------------------------------------------

def chunk_id(source_id: str, label: str, content: str) -> str:
    """Deterministic, content-addressed chunk ID (SHA-256 hex, first 32 chars)."""
    raw = f"{source_id}\x00{label}\x00{content}"
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:32]


def node_id(node_type: str, label: str) -> str:
    """Deterministic node ID."""
    raw = f"{node_type}\x00{label}"
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:32]


# ---------------------------------------------------------------------------
# MemoryTreeStoreService
# ---------------------------------------------------------------------------

class MemoryTreeStoreService:
    """Namespace-scoped SQL adapter; every operation requires current Hub grants."""

    def __init__(self):
        from agent.services.memory_storage_boundary import MemoryStorageBoundary

        self._boundary = MemoryStorageBoundary("hub-memory-tree")

    def _decision(self, operation, *, scope=None, sensitivity="internal"):
        from ananta_contracts.memory_access import MemoryOperation, MemoryScope

        return self._boundary.authorize(MemoryOperation(operation), scope=scope or MemoryScope.task,
                                        sensitivity=sensitivity)

    def _query(self, model, metadata, decision):
        statement = self._boundary.constrain(select(model), metadata, decision)
        if model is MemoryTreeChunkDB:
            statement = statement.where(model.sensitivity == metadata["memory_security"]["sensitivity"].as_string())
        return statement

    def ingest_chunk(self, *, source_id, source_type, label, content, scope="source", kind="leaf",
                     sensitivity="internal", provenance_ref=None, original_ref=None, created_by=None,
                     metadata=None):
        decision = self._decision("append", sensitivity=sensitivity)
        cid = self._boundary.scoped_id(decision, chunk_id(source_id, label, content))
        protected = self._boundary.metadata(decision, metadata)
        protected.pop("memory_quarantine", None)
        with Session(_db_module.engine) as session:
            query = self._query(MemoryTreeChunkDB, MemoryTreeChunkDB.chunk_metadata, decision).where(
                MemoryTreeChunkDB.id == cid)
            existing_id = session.exec(query.with_only_columns(MemoryTreeChunkDB.id)).first()
            if existing_id is not None:
                read = self._decision("read", sensitivity=sensitivity)
                self._boundary.recheck(decision)
                existing = session.exec(query).first()
                self._boundary.recheck(read)
                return existing, False
            chunk = MemoryTreeChunkDB(
                id=cid, source_id=source_id, source_type=source_type, label=label, content=content,
                scope=scope, kind=kind, sensitivity=sensitivity, lifecycle="admitted",
                provenance_ref=provenance_ref, original_ref=original_ref, created_by=created_by,
                chunk_metadata=protected,
            )
            session.add(chunk)
            self._boundary.recheck(decision)
            session.commit()
            session.refresh(chunk)
            return chunk, True

    def get_chunks_by_source(self, source_id, *, lifecycle=None, limit=500, sensitivity_ceiling="internal"):
        if type(limit) is not int or not 1 <= limit <= 2000:
            raise ValueError("memory_tree_limit_invalid")
        decision = self._decision("read", sensitivity=sensitivity_ceiling)
        with Session(_db_module.engine) as session:
            statement = self._query(MemoryTreeChunkDB, MemoryTreeChunkDB.chunk_metadata, decision).where(
                MemoryTreeChunkDB.source_id == source_id,
                MemoryTreeChunkDB.lifecycle.in_(READABLE_LIFECYCLES),
            )
            if lifecycle:
                statement = statement.where(MemoryTreeChunkDB.lifecycle == lifecycle)
            rows = list(session.exec(statement.order_by(MemoryTreeChunkDB.id).limit(limit)).all())
        self._boundary.recheck(decision)
        return rows

    def update_lifecycle(self, chunk_id_val, lifecycle):
        if lifecycle not in _LIFECYCLE_ORDER:
            return False
        decision = self._decision("delete" if lifecycle == "dropped" else "update")
        with Session(_db_module.engine) as session:
            chunk = session.exec(self._query(MemoryTreeChunkDB, MemoryTreeChunkDB.chunk_metadata, decision)
                                 .where(MemoryTreeChunkDB.id == chunk_id_val).with_for_update()).first()
            if chunk is None:
                return False
            if chunk.lifecycle in BLOCKED_LIFECYCLES and lifecycle != chunk.lifecycle:
                return False
            chunk.lifecycle = lifecycle
            if lifecycle == "sealed":
                chunk.sealed_at = time.time()
            elif lifecycle == "dropped":
                chunk.dropped_at = time.time()
            session.add(chunk)
            self._boundary.recheck(decision)
            session.commit()
            return True

    def set_chunk_quarantine(self, chunk_id_val, *, action, reason_code):
        """Audited, scope-bound quarantine/release; generic lifecycle cannot release."""
        if action not in {"quarantine", "release", "revoke"} or (
            not isinstance(reason_code, str) or re.fullmatch(r"[a-z][a-z0-9_.-]{1,100}", reason_code) is None
        ):
            raise ValueError("memory_quarantine_request_invalid")
        decision = self._decision("update")
        with Session(_db_module.engine) as session:
            row = session.exec(self._query(MemoryTreeChunkDB, MemoryTreeChunkDB.chunk_metadata, decision)
                               .where(MemoryTreeChunkDB.id == chunk_id_val).with_for_update()).first()
            if row is None:
                return False
            previous = dict(row.chunk_metadata.get("memory_quarantine") or {})
            if action == "release":
                if (row.lifecycle != "quarantined" or previous.get("prior_lifecycle") not in READABLE_LIFECYCLES
                        or previous.get("schema") != "ananta.memory-quarantine.v1"):
                    return False
                target = previous["prior_lifecycle"]
            else:
                target = "quarantined" if action == "quarantine" else "revoked"
                if row.lifecycle == target:
                    self._boundary.recheck(decision)
                    return True
                if row.lifecycle in {"dropped", "revoked"}:
                    return False
            session.add(MemoryQuarantineEventDB(
                chunk_id=row.id, tenant_id=decision.actor.tenant_id, action=action,
                previous_lifecycle=row.lifecycle, next_lifecycle=target, reason_code=reason_code,
                policy_digest=decision.policy_digest, decision_audit_ref=decision.audit_event_id,
            ))
            row.chunk_metadata = {**row.chunk_metadata, "memory_quarantine": {
                "schema": "ananta.memory-quarantine.v1", "action": action,
                "prior_lifecycle": row.lifecycle if action != "release" else previous["prior_lifecycle"],
                "reason_code": reason_code, "decision_audit_ref": decision.audit_event_id,
                "policy_digest": decision.policy_digest,
            }}
            row.lifecycle = target
            session.add(row)
            self._boundary.recheck(decision)
            session.commit()
            return True

    def seal_source(self, source_id):
        return sum(self.update_lifecycle(chunk.id, "sealed")
                   for chunk in self.get_chunks_by_source(source_id, lifecycle="buffered"))

    def count_chunks(self, source_id, lifecycle=None):
        return len(self.get_chunks_by_source(source_id, lifecycle=lifecycle))

    def _node_parent_chunks(self, session, child_ids, write_decision):
        """Read the complete bounded parent set under one current namespace grant."""
        if not child_ids:
            return []
        read = self._decision("read")
        # Keep pending node mutations unflushed while authorization audits use
        # their own transaction; otherwise SQLite can block that audit writer.
        with session.no_autoflush:
            parents = list(session.exec(
                self._query(MemoryTreeChunkDB, MemoryTreeChunkDB.chunk_metadata, read)
                .where(MemoryTreeChunkDB.id.in_(child_ids)).with_for_update()
            ).all())
        if {chunk.id for chunk in parents} != set(child_ids):
            from agent.services.memory_policy_gateway import MemoryAccessError

            raise MemoryAccessError("memory_tree_child_namespace_mismatch")
        if any(chunk.lifecycle not in READABLE_LIFECYCLES for chunk in parents):
            from agent.services.memory_policy_gateway import MemoryAccessError

            raise MemoryAccessError("memory_tree_parent_unavailable")
        self._boundary.recheck(read)
        for sensitivity in sorted({chunk.sensitivity for chunk in parents}):
            self._boundary.promote_to(write_decision, sensitivity=sensitivity)
        return parents

    def upsert_node(self, *, node_type, label, summary=None, provenance_refs=None, child_chunk_ids=None,
                    metadata=None):
        if child_chunk_ids is not None and (
                not isinstance(child_chunk_ids, list) or len(child_chunk_ids) > 2000
                or any(not isinstance(value, str) or len(value) != 32
                       or any(char not in "0123456789abcdef" for char in value) for value in child_chunk_ids)):
            raise ValueError("memory_tree_children_invalid")
        scope = self._boundary.node_scope(node_type)
        decision = self._decision("append", scope=scope)
        nid = self._boundary.scoped_id(decision, node_id(node_type, label))
        with Session(_db_module.engine) as session:
            if node_type != "source":
                from ananta_contracts.memory_access import MemoryScope

                self._boundary.promote_to(decision, source_scope=(
                    MemoryScope.task if provenance_refs else MemoryScope.workspace
                ))
            query = self._query(MemoryTreeNodeDB, MemoryTreeNodeDB.node_metadata, decision).where(
                MemoryTreeNodeDB.id == nid)
            existing_id = session.exec(query.with_only_columns(MemoryTreeNodeDB.id)).first()
            if existing_id is None:
                write_decision = decision
                node = MemoryTreeNodeDB(id=nid, node_type=node_type, label=label,
                                        node_metadata=self._boundary.metadata(decision, metadata))
            else:
                # Do not hydrate existing content under an append-only grant.
                write_decision = self._decision("update", scope=scope)
                read = self._decision("read", scope=scope)
                self._boundary.recheck(decision)
                node = session.exec(query.with_for_update()).first()
                self._boundary.recheck(read)
                if node is None:
                    from agent.services.memory_policy_gateway import MemoryAccessError

                    raise MemoryAccessError("memory_tree_node_changed")
            if summary is not None:
                node.summary = summary
            if provenance_refs is not None:
                node.provenance_refs = list(dict.fromkeys([*(node.provenance_refs or []), *provenance_refs]))
            if child_chunk_ids is not None:
                node.child_chunk_ids = list(dict.fromkeys([*(node.child_chunk_ids or []), *child_chunk_ids]))
            if len(node.child_chunk_ids or []) > 2000:
                raise ValueError("memory_tree_children_invalid")
            # Revalidate the complete effective parent set, including old links.
            # Only chunk parents are permitted, so this graph cannot form cycles.
            parents = self._node_parent_chunks(session, node.child_chunk_ids or [], write_decision)
            from agent.services.memory_provenance_service import memory_tree_derivation

            updated_metadata = {**(node.node_metadata or {}), **(metadata or {})}
            node.node_metadata = self._boundary.metadata(write_decision, updated_metadata)
            node.node_metadata["memory_provenance"]["derivation"] = memory_tree_derivation(
                summary=node.summary, chunks=parents,
            )
            session.add(node)
            self._boundary.recheck(decision)
            if write_decision is not decision:
                self._boundary.recheck(write_decision)
            session.commit()
            session.refresh(node)
            return node

    def get_node(self, node_type, label):
        decision = self._decision("read", scope=self._boundary.node_scope(node_type))
        nid = self._boundary.scoped_id(decision, node_id(node_type, label))
        with Session(_db_module.engine) as session:
            row = session.exec(self._query(MemoryTreeNodeDB, MemoryTreeNodeDB.node_metadata, decision)
                               .where(MemoryTreeNodeDB.id == nid)).first()
            if row is not None and row.child_chunk_ids:
                # Observe only parent identity/lifecycle, never expose child content
                # through a wider summary grant. Missing/foreign parents fail closed.
                if len(row.child_chunk_ids) > 2000:
                    row = None
                else:
                    parents = session.exec(select(MemoryTreeChunkDB.id, MemoryTreeChunkDB.lifecycle).where(
                        MemoryTreeChunkDB.id.in_(row.child_chunk_ids),
                        MemoryTreeChunkDB.chunk_metadata["memory_namespace"]["tenant_id"].as_string()
                        == decision.actor.tenant_id,
                    )).all()
                    if {item[0] for item in parents} != set(row.child_chunk_ids) or any(
                        item[1] not in READABLE_LIFECYCLES for item in parents
                    ):
                        row = None
        self._boundary.recheck(decision)
        return row

    def enqueue_job(self, *, kind, payload, dedupe_key=None):
        decision = self._decision("append")
        scoped_payload = self._boundary.metadata(decision, payload)
        with Session(_db_module.engine) as session:
            if dedupe_key:
                query = self._query(MemoryTreeJobDB, MemoryTreeJobDB.payload, decision).where(
                    MemoryTreeJobDB.dedupe_key == dedupe_key, MemoryTreeJobDB.status == "pending",
                )
                existing_id = session.exec(query.with_only_columns(MemoryTreeJobDB.id)).first()
                if existing_id is not None:
                    read = self._decision("read")
                    self._boundary.recheck(decision)
                    existing = session.exec(query.where(MemoryTreeJobDB.id == existing_id)).first()
                    self._boundary.recheck(read)
                    return existing
            job = MemoryTreeJobDB(kind=kind, payload=scoped_payload, dedupe_key=dedupe_key)
            session.add(job)
            self._boundary.recheck(decision)
            session.commit()
            session.refresh(job)
            return job

    def complete_job(self, job_id, *, status="done"):
        decision = self._decision("update")
        with Session(_db_module.engine) as session:
            job = session.exec(self._query(MemoryTreeJobDB, MemoryTreeJobDB.payload, decision)
                               .where(MemoryTreeJobDB.id == job_id).with_for_update()).first()
            if job is None:
                return False
            job.status, job.completed_at = status, time.time()
            session.add(job)
            self._boundary.recheck(decision)
            session.commit()
            return True

    def lease_next_job(self, *, kinds=None, lease_seconds=60):
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 3600:
            raise ValueError("memory_tree_lease_invalid")
        decision = self._decision("update")
        self._decision("read")
        with Session(_db_module.engine) as session:
            statement = self._query(MemoryTreeJobDB, MemoryTreeJobDB.payload, decision).where(
                MemoryTreeJobDB.status == "pending",
            )
            if kinds:
                statement = statement.where(MemoryTreeJobDB.kind.in_(list(kinds)))
            job = session.exec(statement.order_by(MemoryTreeJobDB.created_at, MemoryTreeJobDB.id)
                               .limit(1).with_for_update()).first()
            if job is None:
                return None
            job.status, job.lease_until = "leased", min(time.time() + lease_seconds, decision.expires_at)
            job.retry_count += 1
            session.add(job)
            self._boundary.recheck(decision)
            session.commit()
            session.refresh(job)
            return job

    def release_expired_leases(self):
        decision = self._decision("update")
        with Session(_db_module.engine) as session:
            rows = session.exec(self._query(MemoryTreeJobDB, MemoryTreeJobDB.payload, decision).where(
                MemoryTreeJobDB.status == "leased", MemoryTreeJobDB.lease_until < time.time(),
            ).with_for_update()).all()
            for job in rows:
                job.status, job.lease_until = "pending", None
                session.add(job)
            self._boundary.recheck(decision)
            session.commit()
            return len(rows)

    def list_jobs(self, *, status=None, limit=200):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("memory_tree_limit_invalid")
        decision = self._decision("read")
        with Session(_db_module.engine) as session:
            statement = self._query(MemoryTreeJobDB, MemoryTreeJobDB.payload, decision)
            if status:
                statement = statement.where(MemoryTreeJobDB.status == status)
            rows = list(session.exec(statement.order_by(MemoryTreeJobDB.created_at, MemoryTreeJobDB.id)
                                     .limit(limit)).all())
        self._boundary.recheck(decision)
        return rows


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_memory_tree_store_service: MemoryTreeStoreService | None = None


def get_memory_tree_store_service() -> MemoryTreeStoreService:
    global _memory_tree_store_service
    if _memory_tree_store_service is None:
        _memory_tree_store_service = MemoryTreeStoreService()
    return _memory_tree_store_service
