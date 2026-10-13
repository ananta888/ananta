"""Project-scoped, content-free quarantine inventory for Hub operators.

Only identifiers, lifecycle state and audit references leave this boundary; no
entry summary, chunk content or metadata payload is ever projected. Rows are
scoped by the owning project through the actual SQL owner (task for result
memory) or the stored memory namespace (tree chunk) and fail closed on any
missing or foreign binding.
"""

from sqlmodel import Session, select

from agent.db_models import MemoryEntryDB, MemoryTreeChunkDB, TaskDB
from agent.repositories.memory import _engine

_BLOCKED_TREE_LIFECYCLES = ("quarantined", "revoked")
MAX_QUARANTINE_LIMIT = 500


class SQLMemoryQuarantineReadRepository:
    def project_quarantine(self, *, tenant_id: str, project_id: str, limit: int = 200) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= MAX_QUARANTINE_LIMIT:
            raise ValueError("memory_quarantine_query_invalid")
        items: list[dict] = []
        with Session(_engine()) as session:
            entries = session.exec(
                select(MemoryEntryDB).join(TaskDB, MemoryEntryDB.task_id == TaskDB.id).where(
                    TaskDB.tenant_id == tenant_id, TaskDB.project_id == project_id
                )
            ).all()
            for entry in entries:
                quarantine = entry.memory_metadata.get("memory_quarantine")
                state = quarantine.get("state", "active") if isinstance(quarantine, dict) else "active"
                if state == "active":
                    continue
                items.append(self._entry_item(entry, quarantine, state))
            chunks = session.exec(
                select(MemoryTreeChunkDB).where(
                    MemoryTreeChunkDB.lifecycle.in_(_BLOCKED_TREE_LIFECYCLES),
                    MemoryTreeChunkDB.chunk_metadata["memory_namespace"]["tenant_id"].as_string() == tenant_id,
                    MemoryTreeChunkDB.chunk_metadata["memory_namespace"]["workspace_id"].as_string() == project_id,
                )
            ).all()
            for chunk in chunks:
                quarantined = chunk.chunk_metadata.get("memory_quarantine")
                items.append(self._chunk_item(chunk, quarantined if isinstance(quarantined, dict) else {}))
        items.sort(key=lambda item: (item["created_at"], item["kind"], item["id"]))
        return items[:limit]

    @staticmethod
    def _entry_item(entry, quarantine, state) -> dict:
        return {
            "kind": "result_memory_entry", "id": entry.id, "channel_id": "hub-result-memory",
            "state": state, "reason_code": quarantine.get("reason_code"),
            "policy_digest": quarantine.get("policy_digest"),
            "decision_audit_ref": quarantine.get("decision_audit_ref"),
            "task_id": entry.task_id, "created_at": entry.created_at,
        }

    @staticmethod
    def _chunk_item(chunk, quarantine) -> dict:
        return {
            "kind": "memory_tree_chunk", "id": chunk.id, "channel_id": "hub-memory-tree",
            "state": chunk.lifecycle, "reason_code": quarantine.get("reason_code"),
            "policy_digest": quarantine.get("policy_digest"),
            "decision_audit_ref": quarantine.get("decision_audit_ref"),
            "task_id": None, "created_at": chunk.created_at,
        }
