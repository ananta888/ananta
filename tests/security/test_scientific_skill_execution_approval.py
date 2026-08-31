from __future__ import annotations

from types import SimpleNamespace

from agent.services.scientific_skill_execution_approval_service import (
    ScientificSkillExecutionApprovalService,
    ScientificSkillExecutionIntent,
)


class _Approvals:
    def __init__(self) -> None:
        self.created = []
        self.grant = None
        self.consume_result = None

    def create_pending_request(self, **kwargs):
        self.created.append(kwargs)
        return SimpleNamespace(id="approval-1")

    def resolve_granted_request(self, **kwargs):
        self.resolved = kwargs
        return self.grant

    def consume_request(self, request_id):
        self.consumed = request_id
        return self.consume_result


def _intent(**changes) -> ScientificSkillExecutionIntent:
    values = {
        "task_id": "task-1",
        "catalog_digest": "a" * 64,
        "entry_id": "skillentry_" + "b" * 64,
        "upstream_pin": "0123456789abcdef",
        "skill_sha256": "c" * 64,
        "action": "network_fetch",
        "target": "papers.example.test",
    }
    values.update(changes)
    return ScientificSkillExecutionIntent(**values)


def test_external_action_request_is_hub_bound_and_never_auto_granted() -> None:
    approvals = _Approvals()
    events = []
    service = ScientificSkillExecutionApprovalService(approvals, lambda action, data: events.append((action, data)))
    assert service.request(_intent()) == "approval-1"
    created = approvals.created[0]
    assert created["tool_name"] == "scientific_skill.controlled_execution"
    assert created["task_id"] == "task-1"
    assert created["target_fingerprint"] == "papers.example.test"
    assert created["arguments"]["upstream_pin"] == "0123456789abcdef"
    assert created["agent_cfg"]["approval_lifecycle"]["human_required_tools"] == [
        "scientific_skill.controlled_execution"
    ]
    assert events[0][0] == "scientific_skill_approval_requested"


def test_missing_mismatched_and_replayed_grants_fail_closed_and_audit() -> None:
    approvals = _Approvals()
    events = []
    service = ScientificSkillExecutionApprovalService(approvals, lambda action, data: events.append((action, data)))
    intent = _intent()
    missing = service.consume(intent)
    assert missing.approved is False
    assert missing.reason_code == "scientific_skill_approval_not_granted"

    approvals.grant = SimpleNamespace(id="approval-1")
    approvals.consume_result = None
    replay = service.consume(intent)
    assert replay.approved is False
    assert replay.reason_code == "scientific_skill_approval_replay_rejected"
    assert approvals.resolved["arguments"] == intent.arguments()
    assert approvals.resolved["target_fingerprint"] == intent.target
    assert [action for action, _ in events] == [
        "scientific_skill_approval_rejected",
        "scientific_skill_approval_rejected",
    ]


def test_consumption_is_one_shot_and_bound_to_every_security_relevant_field() -> None:
    approvals = _Approvals()
    service = ScientificSkillExecutionApprovalService(approvals, lambda *_: None)
    intent = _intent()
    approvals.grant = SimpleNamespace(id="approval-1")
    approvals.consume_result = SimpleNamespace(status="consumed")
    result = service.consume(intent)
    assert result.approved is True
    assert result.request_id == "approval-1"
    assert approvals.resolved["arguments"] == {
        "catalog_digest": "a" * 64,
        "entry_id": "skillentry_" + "b" * 64,
        "upstream_pin": "0123456789abcdef",
        "skill_sha256": "c" * 64,
        "action": "network_fetch",
        "target": "papers.example.test",
    }
