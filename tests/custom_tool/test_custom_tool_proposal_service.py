"""HDE-011: proposal creation — pending only, digest-bound, deduplicated."""
import json

import pytest

from agent.services.custom_tool_proposal_service import CustomToolProposalService
from agent.services.dynamic_tool_registry_service import DynamicToolRegistryService


def _proposal(**overrides):
    payload = {
        "name": "custom.count_lines",
        "description": "Count lines of a file",
        "proposed_by": "llm:worker-1",
        "source_task_id": "task-1",
        "risk_class": "read",
        "category": "read_only",
        "execution_plane": "worker_runtime",
        "mutation_declaration": "read_only",
        "argument_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
        "execution_kind": "command_template",
        "command_template": ["wc", "-l", "{path}"],
        "path_arguments": ["path"],
        "allowed_paths": ["**"],
        "denied_paths": [],
        "timeout_seconds": 10,
        "output_max_chars": 2000,
        "tests": [
            {"name": "ok", "kind": "positive", "arguments": {"path": "a.txt"}},
            {"name": "fail", "kind": "negative", "arguments": {"path": "missing.txt"}},
        ],
    }
    payload.update(overrides)
    return payload


def test_create_proposal_persists_pending_json(tmp_path):
    service = CustomToolProposalService(tmp_path)
    stored = service.create_proposal(_proposal())
    assert stored["status"] == "pending"
    assert stored["approval_status"] == "pending"
    digest = stored["proposal_digest"]
    path = tmp_path / "tool-proposals" / f"{digest}.json"
    assert path.is_file()
    assert json.loads(path.read_text())["name"] == "custom.count_lines"


def test_invalid_proposal_raises_with_error_codes(tmp_path):
    service = CustomToolProposalService(tmp_path)
    with pytest.raises(ValueError, match="invalid_tool_proposal"):
        service.create_proposal(_proposal(command_template="wc -l"))


def test_duplicate_digest_is_not_recreated(tmp_path):
    service = CustomToolProposalService(tmp_path)
    first = service.create_proposal(_proposal())
    second = service.create_proposal(_proposal())
    assert first["proposal_digest"] == second["proposal_digest"]
    assert len(list((tmp_path / "tool-proposals").glob("*.json"))) == 1


def test_proposal_writes_use_atomic_writer(tmp_path, monkeypatch):
    import agent.services.custom_tool_proposal_service as proposal_module

    writes = []
    original = proposal_module._atomic_write_json

    def _recording_write(path, payload):
        writes.append((path, payload.get("status")))
        original(path, payload)

    monkeypatch.setattr(proposal_module, "_atomic_write_json", _recording_write)
    service = CustomToolProposalService(tmp_path)
    stored = service.create_proposal(_proposal())
    service.update_proposal(stored["proposal_digest"], {"status": "validated", "validated_digest": stored["proposal_digest"]})
    assert [status for _, status in writes] == ["pending", "validated"]


def test_content_change_yields_new_digest(tmp_path):
    service = CustomToolProposalService(tmp_path)
    first = service.create_proposal(_proposal())
    second = service.create_proposal(_proposal(description="Count lines, but different"))
    assert first["proposal_digest"] != second["proposal_digest"]


def test_proposer_cannot_preapprove_or_preactivate(tmp_path):
    service = CustomToolProposalService(tmp_path)
    stored = service.create_proposal(_proposal())
    assert stored["status"] == "pending"
    assert stored["approval_status"] == "pending"

    # And creating a proposal never registers anything executable:
    registry = DynamicToolRegistryService(tmp_path)
    assert registry.get_active_tool("custom.count_lines") is None
    assert registry.list_active_tools() == []


def test_preapproved_payload_fields_are_reset(tmp_path):
    service = CustomToolProposalService(tmp_path)
    sneaky = _proposal()
    sneaky["approval_status"] = "granted"
    sneaky["validated_digest"] = "fake"
    stored = service.create_proposal(sneaky)
    assert stored["approval_status"] == "pending"
    assert "validated_digest" not in stored or not stored["validated_digest"]


def test_update_proposal_only_touches_lifecycle_fields(tmp_path):
    service = CustomToolProposalService(tmp_path)
    stored = service.create_proposal(_proposal())
    digest = stored["proposal_digest"]
    updated = service.update_proposal(digest, {"status": "validated", "command_template": ["rm", "-rf", "/"]})
    assert updated["status"] == "validated"
    assert updated["command_template"] == ["wc", "-l", "{path}"]


def test_list_proposals_filters_by_status(tmp_path):
    service = CustomToolProposalService(tmp_path)
    stored = service.create_proposal(_proposal())
    service.update_proposal(stored["proposal_digest"], {"status": "validated"})
    assert service.list_proposals(status="pending") == []
    assert len(service.list_proposals(status="validated")) == 1
