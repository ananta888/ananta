from __future__ import annotations

import copy

import pytest

from agent.services.chat_organization_service import (
    ChatOrganizationService,
    OrganizationError,
    organization_snapshot,
    validate_classification,
    validate_folder_parent,
)


class MemoryPersistence:
    def __init__(self, data: dict | None = None) -> None:
        self.data = copy.deepcopy(data or {})
        self.save_calls: list[dict] = []

    def load(self) -> dict:
        return copy.deepcopy(self.data)

    def save(self, settings: dict) -> bool:
        self.save_calls.append(copy.deepcopy(settings))
        self.data.update(copy.deepcopy(settings))
        return True


def _store() -> MemoryPersistence:
    return MemoryPersistence(
        {
            "chat_folders": [],
            "chat_sessions": [
                {"id": "chat-1", "name": "First", "folder_id": "", "settings": {}},
                {"id": "chat-2", "name": "Second", "folder_id": "", "settings": {}},
            ],
            "chat_organization_proposals": [],
            "chat_organization_revisions": [],
        }
    )


def test_snapshot_hash_is_stable_for_record_order() -> None:
    first = organization_snapshot(
        {"chat_folders": [{"id": "b"}, {"id": "a"}], "chat_sessions": [{"id": "2"}, {"id": "1"}]}
    )
    second = organization_snapshot(
        {"chat_folders": [{"id": "a"}, {"id": "b"}], "chat_sessions": [{"id": "1"}, {"id": "2"}]}
    )
    assert first["state_hash"] == second["state_hash"]


def test_folder_parent_validation_rejects_missing_parent_and_cycles() -> None:
    folders = [{"id": "parent", "parent_id": ""}, {"id": "child", "parent_id": "parent"}]
    with pytest.raises(OrganizationError, match="not found") as missing:
        validate_folder_parent(folders, "child", "missing")
    assert missing.value.code == "folder_parent_invalid"
    with pytest.raises(OrganizationError) as cycle:
        validate_folder_parent(folders, "parent", "child")
    assert cycle.value.code == "folder_cycle"


def test_classification_requires_known_type_and_allowed_subtype() -> None:
    types = [{"id": "work", "subtypes": ["review"]}]
    validate_classification(types, "work", "review")
    with pytest.raises(OrganizationError) as unknown:
        validate_classification(types, "missing", "")
    assert unknown.value.code == "type_not_found"
    with pytest.raises(OrganizationError) as subtype:
        validate_classification(types, "work", "write")
    assert subtype.value.code == "subtype_not_allowed"


def test_proposal_roundtrip_validate_apply_and_idempotency() -> None:
    persistence = _store()
    service = ChatOrganizationService(persistence)
    proposal = service.create_proposal(
        {
            "summary": "Group first chat",
            "operations": [
                {
                    "operation_id": "create-work",
                    "type": "folder.create",
                    "temp_id": "work",
                    "after": {"name": "Work", "parent_id": ""},
                },
                {
                    "operation_id": "move-first",
                    "type": "conversation.move",
                    "target_id": "chat-1",
                    "after": "work",
                },
            ],
        }
    )
    assert service.get_proposal(proposal["id"])["status"] == "draft"
    assert service.validate_proposal(proposal["id"])["status"] == "ready"

    revision = service.apply_proposal(proposal["id"])
    repeated = service.apply_proposal(proposal["id"])

    assert repeated["id"] == revision["id"]
    assert persistence.data["chat_organization_proposals"][0]["status"] == "applied"
    folder_id = persistence.data["chat_folders"][0]["id"]
    assert persistence.data["chat_sessions"][0]["folder_id"] == folder_id
    assert len(persistence.data["chat_organization_revisions"]) == 1
    atomic_patch = persistence.save_calls[-1]
    assert set(atomic_patch) == {
        "chat_folders",
        "chat_sessions",
        "chat_organization_proposals",
        "chat_organization_revisions",
    }


def test_invalid_multistep_proposal_never_changes_structure() -> None:
    persistence = _store()
    service = ChatOrganizationService(persistence)
    proposal = service.create_proposal(
        {
            "operations": [
                {
                    "operation_id": "valid-create",
                    "type": "folder.create",
                    "temp_id": "created",
                    "after": {"name": "Created"},
                },
                {
                    "operation_id": "bad-move",
                    "type": "conversation.move",
                    "target_id": "missing-chat",
                    "after": "created",
                },
            ]
        }
    )
    validated = service.validate_proposal(proposal["id"])
    assert validated["status"] == "invalid"
    assert validated["validation_errors"][0]["operation_id"] == "bad-move"
    assert persistence.data["chat_folders"] == []
    with pytest.raises(OrganizationError):
        service.apply_proposal(proposal["id"])
    assert persistence.data["chat_folders"] == []


def test_stale_proposal_is_rejected() -> None:
    persistence = _store()
    service = ChatOrganizationService(persistence)
    proposal = service.create_proposal({"operations": []})
    persistence.data["chat_folders"].append({"id": "external", "name": "External", "parent_id": ""})
    with pytest.raises(OrganizationError) as stale:
        service.apply_proposal(proposal["id"])
    assert stale.value.code == "proposal_stale"


def test_revert_is_append_only_idempotent_and_conflict_aware() -> None:
    persistence = _store()
    service = ChatOrganizationService(persistence)
    proposal = service.create_proposal(
        {
            "summary": "Rename",
            "operations": [
                {"operation_id": "rename", "type": "conversation.rename", "target_id": "chat-1", "after": "Renamed"}
            ],
        }
    )
    revision = service.apply_proposal(proposal["id"])
    reverted = service.revert_revision(revision["id"])
    repeated = service.revert_revision(revision["id"])
    assert reverted["id"] == repeated["id"]
    assert persistence.data["chat_sessions"][0]["name"] == "First"
    assert len(persistence.data["chat_organization_revisions"]) == 2

    other = service.create_proposal(
        {
            "operations": [
                {"operation_id": "rename-again", "type": "conversation.rename", "target_id": "chat-1", "after": "Again"}
            ]
        }
    )
    later = service.apply_proposal(other["id"])
    persistence.data["chat_sessions"][0]["name"] = "External edit"
    with pytest.raises(OrganizationError) as conflict:
        service.revert_revision(later["id"])
    assert conflict.value.code == "revision_conflict"
