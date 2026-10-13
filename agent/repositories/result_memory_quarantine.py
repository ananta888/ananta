"""Scoped, atomic Result-Memory lifecycle mutation and content-free audit."""

import re

from sqlmodel import Session

from agent.db_models import MemoryEntryDB, MemoryQuarantineEventDB
from agent.repositories.memory import _engine
from agent.repositories.result_memory_scope import result_memory_scope_query
from agent.services.memory_storage_boundary import MemoryStorageBoundary
from ananta_contracts.memory_access import MemoryOperation, MemoryScope


class ResultMemoryQuarantineRepository:
    def __init__(self):
        self._boundary = MemoryStorageBoundary("hub-result-memory")

    def set_quarantine(self, entry_id, *, action, reason_code, scope=MemoryScope.task):
        if action not in {"quarantine", "release", "revoke"} or (
            not isinstance(reason_code, str) or re.fullmatch(r"[a-z][a-z0-9_.-]{1,100}", reason_code) is None
        ):
            raise ValueError("memory_quarantine_request_invalid")
        decision = self._boundary.authorize(MemoryOperation.update, scope=scope)
        query = result_memory_scope_query(decision.target.namespace, scope, decision.target.security.sensitivity)
        with Session(_engine()) as session:
            row = session.exec(query.where(MemoryEntryDB.id == entry_id).with_for_update()).first()
            if row is None:
                return False
            previous = row.memory_metadata.get("memory_quarantine")
            previous = previous if isinstance(previous, dict) else {}
            state = previous.get("state", "active")
            target = {"quarantine": "quarantined", "release": "active", "revoke": "revoked"}[action]
            if state == target:
                self._boundary.recheck(decision)
                return True
            if state not in {"active", "quarantined"} or action == "release" and state != "quarantined":
                return False
            row.memory_metadata = {**row.memory_metadata, "memory_quarantine": {
                "schema": "ananta.memory-quarantine.v1", "state": target, "reason_code": reason_code,
                "decision_audit_ref": decision.audit_event_id, "policy_digest": decision.policy_digest,
            }}
            session.add(MemoryQuarantineEventDB(
                chunk_id=row.id, channel_id="hub-result-memory", tenant_id=decision.actor.tenant_id,
                action=action, previous_lifecycle=state, next_lifecycle=target, reason_code=reason_code,
                policy_digest=decision.policy_digest, decision_audit_ref=decision.audit_event_id,
            ))
            session.add(row)
            self._boundary.recheck(decision)
            session.commit()
            return True
