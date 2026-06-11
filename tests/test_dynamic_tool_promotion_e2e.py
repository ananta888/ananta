"""HDE-023: custom tool proposal -> validation -> approval -> active -> execute."""
from __future__ import annotations

from sqlmodel import SQLModel, create_engine

from agent.services.custom_tool_executor import CustomToolExecutor
from agent.services.custom_tool_promotion_service import CustomToolPromotionService
from agent.services.custom_tool_proposal_service import CustomToolProposalService
from agent.services.dynamic_tool_registry_service import DynamicToolRegistryService


def _script_payload():
    return {
        "name": "custom.hello_script",
        "description": "Print a fixed script message",
        "proposed_by": "user:test",
        "source_task_id": "task-1",
        "risk_class": "read",
        "category": "read_only",
        "execution_plane": "worker_runtime",
        "mutation_declaration": "read_only",
        "argument_schema": {"type": "object", "properties": {}},
        "execution_kind": "script",
        "script_body_ref": "tool-scripts/hello.sh",
        "path_arguments": [],
        "allowed_paths": ["**"],
        "denied_paths": [],
        "timeout_seconds": 10,
        "output_max_chars": 2000,
        "tests": [
            {"name": "prints", "kind": "positive", "arguments": {}, "expect_status": "ok", "expect_output_contains": ["hello-e2e"]},
            {"name": "rejects unknown arg", "kind": "negative", "arguments": {"extra": "x"}, "expect_status": "rejected"},
        ],
    }


def test_dynamic_script_tool_promotes_and_executes_with_digest(tmp_path, monkeypatch):
    test_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(test_engine)
    monkeypatch.setattr("agent.services.approval_request_service._engine", lambda: test_engine)
    monkeypatch.setattr(
        "agent.services.approval_request_service.ApprovalRequestService._payload_dir",
        staticmethod(lambda: tmp_path / "payloads"),
    )
    monkeypatch.setattr("agent.common.audit.log_audit", lambda action, details=None: None)

    store = tmp_path / "tool-scripts"
    store.mkdir(parents=True)
    script = store / "hello.sh"
    script.write_text("#!/bin/bash\necho hello-e2e\n", encoding="utf-8")

    proposals = CustomToolProposalService(tmp_path)
    registry = DynamicToolRegistryService(tmp_path)
    promo = CustomToolPromotionService(data_root=tmp_path, proposal_service=proposals, registry=registry)
    from agent.services.approval_request_service import get_approval_request_service

    digest = proposals.create_proposal(_script_payload())["proposal_digest"]
    assert promo.validate(digest)["status"] == "validated"
    proposal = promo.request_approval(digest)
    get_approval_request_service().decide_request(proposal["approval_request_id"], decision="granted", decided_by="operator")
    assert promo.refresh_approval(digest)["status"] == "approved"
    record = promo.activate(digest)

    workspace = tmp_path / "ws"
    workspace.mkdir()
    result = CustomToolExecutor(tmp_path).execute_spec(
        spec=record["spec"],
        arguments={},
        workspace_dir=str(workspace),
        tool_call_id="t-e2e",
        config={},
    )
    assert result["status"] == "ok"
    assert "hello-e2e" in result["evidence"][0]["excerpt"]

    script.write_text("#!/bin/bash\necho tampered\n", encoding="utf-8")
    tampered = CustomToolExecutor(tmp_path).execute_spec(
        spec=record["spec"],
        arguments={},
        workspace_dir=str(workspace),
        tool_call_id="t-e2e-2",
        config={},
    )
    assert tampered["status"] == "rejected"
    assert tampered["error"] == "script_body_digest_mismatch"

