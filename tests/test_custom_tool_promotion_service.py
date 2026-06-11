"""HDE-015: promotion pipeline pending -> validated -> approved -> active."""
from __future__ import annotations

import pytest
import hashlib
from sqlmodel import SQLModel, create_engine

from agent.services.custom_tool_promotion_service import (
    CustomToolPromotionError,
    CustomToolPromotionService,
)
from agent.services.custom_tool_proposal_service import CustomToolProposalService
from agent.services.dynamic_tool_registry_service import DynamicToolRegistryService


@pytest.fixture
def world(monkeypatch, tmp_path):
    test_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(test_engine)
    monkeypatch.setattr("agent.services.approval_request_service._engine", lambda: test_engine)
    monkeypatch.setattr(
        "agent.services.approval_request_service.ApprovalRequestService._payload_dir",
        staticmethod(lambda: tmp_path / "payloads"),
    )
    monkeypatch.setattr("agent.common.audit.log_audit", lambda action, details=None: None)
    from agent.services.approval_request_service import get_approval_request_service

    proposals = CustomToolProposalService(tmp_path)
    registry = DynamicToolRegistryService(tmp_path)
    promo = CustomToolPromotionService(data_root=tmp_path, proposal_service=proposals, registry=registry)
    return {"promo": promo, "proposals": proposals, "registry": registry, "approvals": get_approval_request_service()}


def _proposal(tests=None, **overrides):
    payload = {
        "name": "custom.count_lines",
        "description": "Count lines of a file",
        "proposed_by": "user:test",
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
        "tests": tests
        or [
            {
                "name": "counts",
                "kind": "positive",
                "setup_files": {"a.txt": "eins\nzwei\n"},
                "arguments": {"path": "a.txt"},
                "expect_status": "ok",
                "expect_output_contains": ["2"],
            },
            {"name": "fails", "kind": "negative", "arguments": {"path": "missing.txt"}, "expect_status": "error"},
        ],
    }
    payload.update(overrides)
    return payload


def test_happy_path_to_active(world):
    digest = world["proposals"].create_proposal(_proposal())["proposal_digest"]
    assert world["promo"].validate(digest)["status"] == "validated"
    proposal = world["promo"].request_approval(digest)
    assert proposal["status"] == "approval_required"

    world["approvals"].decide_request(proposal["approval_request_id"], decision="granted", decided_by="operator")
    assert world["promo"].refresh_approval(digest)["status"] == "approved"

    record = world["promo"].activate(digest)
    assert record["status"] == "active"
    assert world["registry"].get_active_tool("custom.count_lines") is not None


def test_validation_failure_blocks_approval(world):
    failing_tests = [
        {
            "name": "wrong",
            "kind": "positive",
            "setup_files": {"a.txt": "eins\n"},
            "arguments": {"path": "a.txt"},
            "expect_output_contains": ["drei"],
        },
        {"name": "neg", "kind": "negative", "arguments": {"path": "missing.txt"}, "expect_status": "error"},
    ]
    digest = world["proposals"].create_proposal(_proposal(tests=failing_tests))["proposal_digest"]
    assert world["promo"].validate(digest)["status"] == "validation_failed"
    with pytest.raises(CustomToolPromotionError, match="approval_requires_validated_proposal"):
        world["promo"].request_approval(digest)
    with pytest.raises(CustomToolPromotionError):
        world["promo"].activate(digest)


def test_approval_denial_rejects_proposal(world):
    digest = world["proposals"].create_proposal(_proposal())["proposal_digest"]
    world["promo"].validate(digest)
    proposal = world["promo"].request_approval(digest)
    world["approvals"].decide_request(proposal["approval_request_id"], decision="denied", decided_by="operator")
    assert world["promo"].refresh_approval(digest)["status"] == "rejected"
    with pytest.raises(CustomToolPromotionError):
        world["promo"].activate(digest)


def test_digest_change_invalidates_validation_and_grants(world):
    digest = world["proposals"].create_proposal(_proposal())["proposal_digest"]
    world["promo"].validate(digest)
    proposal = world["promo"].request_approval(digest)
    world["approvals"].decide_request(proposal["approval_request_id"], decision="granted", decided_by="operator")
    world["promo"].refresh_approval(digest)

    # Simulate drift: validated digest no longer matches the proposal.
    world["proposals"].update_proposal(digest, {"validated_digest": "anderer-digest"})
    with pytest.raises(CustomToolPromotionError, match="validated_digest_mismatch"):
        world["promo"].activate(digest)


def test_no_activation_without_approval_lifecycle(world):
    digest = world["proposals"].create_proposal(_proposal())["proposal_digest"]
    world["promo"].validate(digest)
    with pytest.raises(CustomToolPromotionError, match="activate_requires_approved_proposal"):
        world["promo"].activate(digest)


def test_admin_override_still_requires_validation(world):
    digest = world["proposals"].create_proposal(_proposal())["proposal_digest"]
    with pytest.raises(CustomToolPromotionError, match="admin_override_requires_validation"):
        world["promo"].activate(digest, admin_override=True)
    world["promo"].validate(digest)
    record = world["promo"].activate(digest, actor="admin:test", admin_override=True)
    assert record["status"] == "active"


def test_script_digest_is_bound_into_promoted_spec(world, tmp_path):
    store = tmp_path / "tool-scripts"
    store.mkdir(parents=True)
    script = store / "hello.sh"
    script.write_text("#!/bin/bash\necho script-ok\n", encoding="utf-8")
    expected_digest = hashlib.sha256(script.read_bytes()).hexdigest()
    payload = _proposal(
        name="custom.hello_script",
        execution_kind="script",
        command_template=None,
        script_body_ref="tool-scripts/hello.sh",
        argument_schema={"type": "object", "properties": {}},
        path_arguments=[],
        tests=[
            {"name": "ok", "kind": "positive", "arguments": {}, "expect_status": "ok", "expect_output_contains": ["script-ok"]},
            {"name": "neg", "kind": "negative", "arguments": {"extra": "x"}, "expect_status": "rejected"},
        ],
    )
    digest = world["proposals"].create_proposal(payload)["proposal_digest"]
    proposal = world["proposals"].get_proposal(digest)
    assert proposal["script_body_digest"] == expected_digest
    world["promo"].validate(digest)
    proposal = world["promo"].request_approval(digest)
    world["approvals"].decide_request(proposal["approval_request_id"], decision="granted", decided_by="operator")
    world["promo"].refresh_approval(digest)
    record = world["promo"].activate(digest)
    assert record["spec"]["script_body_digest"] == expected_digest
