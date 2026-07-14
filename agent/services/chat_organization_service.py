"""Chat organization domain service.

The service owns structural validation, proposal lifecycle, atomic application,
and revision history.  Flask routes only translate HTTP requests; persistence is
provided through the small ``load``/``save`` manager port.
"""

from __future__ import annotations

import copy
import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Literal, Protocol, TypedDict

from agent.services.chat_session_security import chat_session_mutation_lock


class OrganizationPersistence(Protocol):
    def load(self) -> dict[str, Any]: ...
    def save(self, settings: dict[str, Any]) -> bool: ...


class OrganizationOperation(TypedDict, total=False):
    operation_id: str
    type: str
    target_id: str
    temp_id: str
    before: Any
    after: Any
    rationale: str


class OrganizationSnapshot(TypedDict):
    folders: list[dict[str, Any]]
    conversations: list[dict[str, Any]]
    state_hash: str


class ReorganizationProposal(TypedDict, total=False):
    id: str
    created_at: float
    updated_at: float
    status: Literal["draft", "ready", "invalid", "applied", "discarded", "superseded"]
    source: str
    method: str
    input_policy: str
    base_state_hash: str
    summary: str
    operations: list[OrganizationOperation]
    validation_errors: list[dict[str, Any]]


class OrganizationRevision(TypedDict, total=False):
    id: str
    created_at: float
    source: str
    source_proposal_id: str
    reverts_revision_id: str
    summary: str
    applied_operations: list[OrganizationOperation]
    base_state_hash: str
    result_state_hash: str
    before_snapshot: OrganizationSnapshot
    after_snapshot: OrganizationSnapshot


@dataclass(frozen=True)
class OrganizationError(Exception):
    code: str
    message: str
    status: int = 400
    details: tuple[dict[str, Any], ...] = ()

    def payload(self) -> dict[str, Any]:
        result: dict[str, Any] = {"error": self.message, "error_code": self.code}
        if self.details:
            result["details"] = list(self.details)
        return result


_LOCK = chat_session_mutation_lock
_PROPOSAL_STATUSES = {"draft", "ready", "invalid", "applied", "discarded", "superseded"}
_OPERATION_TYPES = {
    "folder.create",
    "folder.rename",
    "folder.move",
    "folder.update_icon",
    "folder.update_color",
    "folder.delete_if_empty",
    "conversation.move",
    "conversation.rename",
    "folder.reorder",
    "conversation.reorder",
}
_MAX_PROPOSALS = 100
_MAX_REVISIONS = 100


def _records(value: Any) -> list[dict[str, Any]]:
    return copy.deepcopy([item for item in (value or []) if isinstance(item, dict)])


def _conversation_view(session: dict[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(session.get(key, "" if key != "sort_order" else 0))
        for key in (
            "id",
            "name",
            "folder_id",
            "profile_id",
            "session_type",
            "session_subtype",
            "group",
            "sort_order",
            "updated_at",
            "last_message_preview",
            "message_count",
        )
    }


def organization_snapshot(settings: dict[str, Any]) -> dict[str, Any]:
    folders = sorted(_records(settings.get("chat_folders")), key=lambda item: str(item.get("id") or ""))
    sessions = sorted(_records(settings.get("chat_sessions")), key=lambda item: str(item.get("id") or ""))
    snapshot = {
        "folders": folders,
        "conversations": [_conversation_view(item) for item in sessions],
    }
    canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    snapshot["state_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return snapshot


def validate_folder_parent(folders: list[dict[str, Any]], folder_id: str, parent_id: str) -> None:
    if not parent_id:
        return
    by_id = {str(item.get("id") or ""): item for item in folders}
    if parent_id not in by_id:
        raise OrganizationError("folder_parent_invalid", f"Folder parent '{parent_id}' not found")
    current = parent_id
    visited: set[str] = set()
    while current:
        if current == folder_id:
            raise OrganizationError("folder_cycle", "Folder move would create a cycle")
        if current in visited:
            raise OrganizationError("folder_cycle", "Existing folder hierarchy contains a cycle")
        visited.add(current)
        current = str(by_id.get(current, {}).get("parent_id") or "")


def validate_classification(
    types: list[dict[str, Any]],
    type_id: str,
    subtype: str,
) -> None:
    if not type_id:
        if subtype:
            raise OrganizationError("subtype_not_allowed", "Subtype requires a conversation type")
        return
    item = next((entry for entry in types if str(entry.get("id") or "") == type_id), None)
    if item is None:
        raise OrganizationError("type_not_found", f"Conversation type '{type_id}' not found")
    allowed = {str(value) for value in item.get("subtypes") or []}
    if subtype and subtype not in allowed:
        raise OrganizationError("subtype_not_allowed", f"Subtype '{subtype}' is not allowed for '{type_id}'")


class ChatOrganizationService:
    """Application service for the chat organization aggregate."""

    def __init__(self, persistence: OrganizationPersistence) -> None:
        self._persistence = persistence

    def snapshot(self) -> dict[str, Any]:
        return organization_snapshot(self._persistence.load())

    def list_proposals(self) -> list[dict[str, Any]]:
        return sorted(
            _records(self._persistence.load().get("chat_organization_proposals")),
            key=lambda item: float(item.get("created_at") or 0),
            reverse=True,
        )

    def get_proposal(self, proposal_id: str) -> dict[str, Any]:
        proposal = next((item for item in self.list_proposals() if item.get("id") == proposal_id), None)
        if proposal is None:
            raise OrganizationError("proposal_not_found", f"Proposal '{proposal_id}' not found", 404)
        return proposal

    def create_proposal(self, payload: dict[str, Any]) -> dict[str, Any]:
        with _LOCK:
            settings = self._persistence.load()
            now = time.time()
            proposal = {
                "id": str(payload.get("id") or f"proposal-{uuid.uuid4().hex}"),
                "created_at": now,
                "updated_at": now,
                "status": "draft",
                "source": str(payload.get("source") or "user"),
                "method": str(payload.get("method") or "manual"),
                "input_policy": str(payload.get("input_policy") or "metadata_only"),
                "base_state_hash": str(payload.get("base_state_hash") or organization_snapshot(settings)["state_hash"]),
                "summary": str(payload.get("summary") or "Organization proposal"),
                "operations": _records(payload.get("operations")),
                "validation_errors": [],
            }
            proposals = _records(settings.get("chat_organization_proposals"))
            if any(item.get("id") == proposal["id"] for item in proposals):
                raise OrganizationError("proposal_exists", f"Proposal '{proposal['id']}' already exists", 409)
            proposals.append(proposal)
            self._persist(settings, proposals=proposals)
            return proposal

    def update_proposal(self, proposal_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        with _LOCK:
            settings = self._persistence.load()
            proposals = _records(settings.get("chat_organization_proposals"))
            proposal = next((item for item in proposals if item.get("id") == proposal_id), None)
            if proposal is None:
                raise OrganizationError("proposal_not_found", f"Proposal '{proposal_id}' not found", 404)
            if proposal.get("status") == "applied":
                raise OrganizationError("proposal_already_applied", "Applied proposals are immutable", 409)
            if "operations" in payload:
                proposal["operations"] = _records(payload.get("operations"))
            for key in ("summary", "input_policy"):
                if key in payload:
                    proposal[key] = str(payload.get(key) or "")
            if payload.get("status") in {"discarded", "draft"}:
                proposal["status"] = payload["status"]
            proposal["updated_at"] = time.time()
            proposal["validation_errors"] = []
            self._persist(settings, proposals=proposals)
            return proposal

    def discard_proposal(self, proposal_id: str) -> None:
        self.update_proposal(proposal_id, {"status": "discarded"})

    def validate_proposal(self, proposal_id: str) -> dict[str, Any]:
        with _LOCK:
            settings = self._persistence.load()
            proposals = _records(settings.get("chat_organization_proposals"))
            proposal = next((item for item in proposals if item.get("id") == proposal_id), None)
            if proposal is None:
                raise OrganizationError("proposal_not_found", f"Proposal '{proposal_id}' not found", 404)
            errors: list[dict[str, Any]] = []
            try:
                self._simulate(settings, proposal.get("operations") or [])
            except OrganizationError as exc:
                errors = list(exc.details) or [{"error_code": exc.code, "message": exc.message}]
            proposal["validation_errors"] = errors
            proposal["status"] = "invalid" if errors else "ready"
            proposal["updated_at"] = time.time()
            self._persist(settings, proposals=proposals)
            return proposal

    def apply_proposal(self, proposal_id: str) -> dict[str, Any]:
        with _LOCK:
            settings = self._persistence.load()
            proposals = _records(settings.get("chat_organization_proposals"))
            revisions = _records(settings.get("chat_organization_revisions"))
            proposal = next((item for item in proposals if item.get("id") == proposal_id), None)
            if proposal is None:
                raise OrganizationError("proposal_not_found", f"Proposal '{proposal_id}' not found", 404)
            if proposal.get("status") == "applied":
                existing = next((item for item in revisions if item.get("source_proposal_id") == proposal_id), None)
                if existing is not None:
                    return existing
                raise OrganizationError("proposal_already_applied", "Proposal was already applied", 409)
            before = organization_snapshot(settings)
            if proposal.get("base_state_hash") != before["state_hash"]:
                raise OrganizationError("proposal_stale", "Organization state changed since proposal creation", 409)
            folders, sessions, applied = self._simulate(settings, proposal.get("operations") or [])
            after_settings = {**settings, "chat_folders": folders, "chat_sessions": sessions}
            after = organization_snapshot(after_settings)
            revision = {
                "id": f"revision-{uuid.uuid4().hex}",
                "created_at": time.time(),
                "source": str(proposal.get("source") or "user"),
                "source_proposal_id": proposal_id,
                "summary": str(proposal.get("summary") or "Organization updated"),
                "applied_operations": applied,
                "base_state_hash": before["state_hash"],
                "result_state_hash": after["state_hash"],
                "before_snapshot": before,
                "after_snapshot": after,
            }
            revisions.append(revision)
            proposal["status"] = "applied"
            proposal["applied_revision_id"] = revision["id"]
            proposal["updated_at"] = time.time()
            self._persist(settings, folders=folders, sessions=sessions, proposals=proposals, revisions=revisions)
            return revision

    def apply_manual(self, summary: str, operations: list[dict[str, Any]]) -> dict[str, Any]:
        """Apply one user action as one atomic append-only revision."""
        with _LOCK:
            settings = self._persistence.load()
            revisions = _records(settings.get("chat_organization_revisions"))
            before = organization_snapshot(settings)
            trusted_operations = copy.deepcopy(operations)
            for operation in trusted_operations:
                if operation.get("type") == "folder.create" and operation.get("target_id"):
                    operation["_manual_real_id"] = str(operation["target_id"])
            folders, sessions, applied = self._simulate(settings, trusted_operations)
            after = organization_snapshot({**settings, "chat_folders": folders, "chat_sessions": sessions})
            revision = {
                "id": f"revision-{uuid.uuid4().hex}",
                "created_at": time.time(),
                "source": "user",
                "summary": summary,
                "applied_operations": applied,
                "base_state_hash": before["state_hash"],
                "result_state_hash": after["state_hash"],
                "before_snapshot": before,
                "after_snapshot": after,
            }
            revisions.append(revision)
            self._persist(settings, folders=folders, sessions=sessions, revisions=revisions)
            return revision

    def list_revisions(self) -> list[dict[str, Any]]:
        return sorted(
            _records(self._persistence.load().get("chat_organization_revisions")),
            key=lambda item: float(item.get("created_at") or 0),
            reverse=True,
        )

    def get_revision(self, revision_id: str) -> dict[str, Any]:
        revision = next((item for item in self.list_revisions() if item.get("id") == revision_id), None)
        if revision is None:
            raise OrganizationError("revision_not_found", f"Revision '{revision_id}' not found", 404)
        return revision

    def revert_revision(self, revision_id: str) -> dict[str, Any]:
        with _LOCK:
            settings = self._persistence.load()
            revisions = _records(settings.get("chat_organization_revisions"))
            original = next((item for item in revisions if item.get("id") == revision_id), None)
            if original is None:
                raise OrganizationError("revision_not_found", f"Revision '{revision_id}' not found", 404)
            existing = next((item for item in revisions if item.get("reverts_revision_id") == revision_id), None)
            if existing is not None:
                return existing
            current = organization_snapshot(settings)
            if current["state_hash"] != original.get("result_state_hash"):
                raise OrganizationError("revision_conflict", "Revision cannot be reverted from the current state", 409)
            before = original.get("before_snapshot") or {}
            folders = _records(before.get("folders"))
            before_conversations = {str(item.get("id") or ""): item for item in _records(before.get("conversations"))}
            sessions = _records(settings.get("chat_sessions"))
            if {str(item.get("id") or "") for item in sessions} != set(before_conversations):
                raise OrganizationError("revision_conflict", "Conversation set changed since the revision", 409)
            for session in sessions:
                old = before_conversations[str(session.get("id") or "")]
                for key in ("name", "folder_id", "sort_order"):
                    session[key] = old.get(key, "" if key != "sort_order" else 0)
            after_settings = {**settings, "chat_folders": folders, "chat_sessions": sessions}
            reverted = organization_snapshot(after_settings)
            revision = {
                "id": f"revision-{uuid.uuid4().hex}",
                "created_at": time.time(),
                "source": "user",
                "summary": f"Revert: {original.get('summary') or revision_id}",
                "reverts_revision_id": revision_id,
                "applied_operations": [],
                "base_state_hash": current["state_hash"],
                "result_state_hash": reverted["state_hash"],
                "before_snapshot": current,
                "after_snapshot": reverted,
            }
            revisions.append(revision)
            self._persist(settings, folders=folders, sessions=sessions, revisions=revisions)
            return revision

    def _persist(self, settings: dict[str, Any], **collections: Any) -> None:
        mapping = {
            "folders": "chat_folders",
            "sessions": "chat_sessions",
            "proposals": "chat_organization_proposals",
            "revisions": "chat_organization_revisions",
        }
        if "proposals" in collections:
            collections["proposals"] = list(collections["proposals"])[-_MAX_PROPOSALS:]
        if "revisions" in collections:
            collections["revisions"] = list(collections["revisions"])[-_MAX_REVISIONS:]
        patch = {mapping[key]: value for key, value in collections.items()}
        if patch and not self._persistence.save(patch):
            raise OrganizationError("organization_persistence_failed", "Could not persist organization state", 500)

    def _simulate(
        self,
        settings: dict[str, Any],
        operations: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        folders = _records(settings.get("chat_folders"))
        sessions = _records(settings.get("chat_sessions"))
        folder_by_id = {str(item.get("id") or ""): item for item in folders}
        session_by_id = {str(item.get("id") or ""): item for item in sessions}
        temp_ids: dict[str, str] = {}
        seen_ops: set[str] = set()
        applied: list[dict[str, Any]] = []
        for index, raw in enumerate(operations):
            operation = copy.deepcopy(raw)
            op_id = str(operation.get("operation_id") or f"operation-{index}")
            if op_id in seen_ops:
                raise self._operation_error(op_id, "duplicate_operation", "Duplicate operation_id")
            seen_ops.add(op_id)
            kind = str(operation.get("type") or "")
            if kind not in _OPERATION_TYPES:
                raise self._operation_error(op_id, "unknown_operation", f"Unknown operation type '{kind}'")
            target = str(operation.get("target_id") or "")
            if target in temp_ids:
                target = temp_ids[target]
            value = operation.get("after") if "after" in operation else operation.get("value")
            if kind == "folder.create":
                temp = str(operation.get("temp_id") or target)
                if not temp or temp in temp_ids or temp in folder_by_id:
                    raise self._operation_error(op_id, "duplicate_temp_id", "Folder create requires a unique temp_id")
                real_id = str(operation.pop("_manual_real_id", "") or f"folder-{uuid.uuid4().hex[:12]}")
                if real_id in folder_by_id:
                    raise self._operation_error(op_id, "folder_exists", f"Folder '{real_id}' already exists")
                parent = str((value or {}).get("parent_id") or "")
                parent = temp_ids.get(parent, parent)
                validate_folder_parent(folders, real_id, parent)
                folder = {
                    "id": real_id,
                    "name": str((value or {}).get("name") or "").strip(),
                    "icon": str((value or {}).get("icon") or "📁"),
                    "color": str((value or {}).get("color") or ""),
                    "parent_id": parent,
                    "sort_order": int((value or {}).get("sort_order") or 0),
                    "created_at": time.time(),
                    "updated_at": time.time(),
                }
                if not folder["name"]:
                    raise self._operation_error(op_id, "folder_name_required", "Folder name is required")
                folders.append(folder)
                folder_by_id[real_id] = folder
                temp_ids[temp] = real_id
            elif kind.startswith("folder."):
                folder = folder_by_id.get(target)
                if folder is None:
                    raise self._operation_error(op_id, "folder_not_found", f"Folder '{target}' not found")
                if kind == "folder.rename":
                    folder["name"] = str(value or "").strip() or folder.get("name")
                elif kind == "folder.move":
                    parent = temp_ids.get(str(value or ""), str(value or ""))
                    validate_folder_parent(folders, target, parent)
                    folder["parent_id"] = parent
                elif kind == "folder.update_icon":
                    folder["icon"] = str(value or "📁")
                elif kind == "folder.update_color":
                    folder["color"] = str(value or "")
                elif kind == "folder.reorder":
                    folder["sort_order"] = int(value or 0)
                elif kind == "folder.delete_if_empty":
                    if any(str(item.get("parent_id") or "") == target for item in folders) or any(
                        str(item.get("folder_id") or "") == target for item in sessions
                    ):
                        raise self._operation_error(op_id, "folder_not_empty", f"Folder '{target}' is not empty")
                    folders.remove(folder)
                    del folder_by_id[target]
                folder["updated_at"] = time.time()
            else:
                session = session_by_id.get(target)
                if session is None:
                    raise self._operation_error(op_id, "conversation_not_found", f"Conversation '{target}' not found")
                if kind == "conversation.move":
                    folder_id = temp_ids.get(str(value or ""), str(value or ""))
                    if folder_id and folder_id not in folder_by_id:
                        raise self._operation_error(op_id, "folder_not_found", f"Folder '{folder_id}' not found")
                    session["folder_id"] = folder_id
                elif kind == "conversation.rename":
                    session["name"] = str(value or "").strip() or session.get("name")
                elif kind == "conversation.reorder":
                    session["sort_order"] = int(value or 0)
                session["updated_at"] = time.time()
            operation["operation_id"] = op_id
            operation["resolved_target_id"] = target or temp_ids.get(str(operation.get("temp_id") or ""), "")
            applied.append(operation)
        return folders, sessions, applied

    @staticmethod
    def _operation_error(operation_id: str, code: str, message: str) -> OrganizationError:
        return OrganizationError(
            code, message, 400, ({"operation_id": operation_id, "error_code": code, "message": message},)
        )
