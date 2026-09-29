"""CCRDS-013: cross-domain write approval lifecycle (deny/pending/granted)."""
from __future__ import annotations

import pytest
from sqlmodel import SQLModel, create_engine

from agent.codecompass.domain_scope import (
    DECISION_ALLOW,
    DECISION_APPROVAL_REQUIRED,
    DECISION_BLOCKED,
    DomainScopeViolation,
    VIOLATION_WRITE_OUT_OF_SCOPE,
)
from agent.codecompass.domain_scope_approval import (
    APPROVAL_CLASS_CROSS_DOMAIN_WRITE,
    request_cross_domain_write_approval,
)


@pytest.fixture
def approval_engine(monkeypatch, tmp_path):
    test_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(test_engine)
    monkeypatch.setattr("agent.services.approval_request_service._engine", lambda: test_engine)
    monkeypatch.setenv("ANANTA_DATA_DIR", str(tmp_path))
    return test_engine


def _violation(path: str = "catalog/service.py") -> DomainScopeViolation:
    return DomainScopeViolation(
        kind=VIOLATION_WRITE_OUT_OF_SCOPE,
        message="write outside selected domain(s) ['orders']",
        requested_path=path,
        matched_domain="orders",
        allowed_paths=("orders",),
        severity="critical",
    )


def test_strict_mode_blocks_without_touching_lifecycle(approval_engine) -> None:
    decision, details = request_cross_domain_write_approval(_violation(), mode="strict")
    assert decision.decision == DECISION_BLOCKED
    assert details == {}


def test_approval_mode_creates_pending_request(approval_engine) -> None:
    decision, details = request_cross_domain_write_approval(
        _violation(), mode="approval", task_id="task-1"
    )
    assert decision.decision == DECISION_APPROVAL_REQUIRED
    assert details["status"] == "pending"
    assert details["approval_request_id"]

    from agent.services.approval_request_service import get_approval_request_service

    request = get_approval_request_service().get_request(details["approval_request_id"])
    assert request is not None
    assert request.scope["approval_class"] == APPROVAL_CLASS_CROSS_DOMAIN_WRITE
    assert request.scope["requested_path"] == "catalog/service.py"


def test_approval_mode_is_idempotent_for_same_call(approval_engine) -> None:
    _, first = request_cross_domain_write_approval(_violation(), mode="approval", task_id="task-1")
    _, second = request_cross_domain_write_approval(_violation(), mode="approval", task_id="task-1")
    assert first["approval_request_id"] == second["approval_request_id"]


def test_granted_request_turns_into_allow(approval_engine) -> None:
    from agent.services.approval_request_service import get_approval_request_service

    decision, details = request_cross_domain_write_approval(
        _violation(), mode="approval", task_id="task-1"
    )
    assert decision.decision == DECISION_APPROVAL_REQUIRED

    get_approval_request_service().decide_request(
        details["approval_request_id"], decision="granted", decided_by="operator"
    )

    decision2, details2 = request_cross_domain_write_approval(
        _violation(), mode="approval", task_id="task-1"
    )
    assert decision2.decision == DECISION_ALLOW
    assert decision2.reason == "approval_granted_by_request"
    assert details2["approval_request_id"] == details["approval_request_id"]


def test_grant_is_path_bound_not_tool_bound(approval_engine) -> None:
    from agent.services.approval_request_service import get_approval_request_service

    _, details = request_cross_domain_write_approval(
        _violation("catalog/a.py"), mode="approval", task_id="task-1"
    )
    get_approval_request_service().decide_request(
        details["approval_request_id"], decision="granted", decided_by="operator"
    )

    # Same tool, different path → no grant, new pending request.
    decision_other, details_other = request_cross_domain_write_approval(
        _violation("catalog/b.py"), mode="approval", task_id="task-1"
    )
    assert decision_other.decision == DECISION_APPROVAL_REQUIRED
    assert details_other["approval_request_id"] != details["approval_request_id"]


def test_denied_request_stays_approval_required(approval_engine) -> None:
    from agent.services.approval_request_service import get_approval_request_service

    _, details = request_cross_domain_write_approval(
        _violation(), mode="approval", task_id="task-1"
    )
    get_approval_request_service().decide_request(
        details["approval_request_id"], decision="denied", decided_by="operator"
    )

    decision, details2 = request_cross_domain_write_approval(
        _violation(), mode="approval", task_id="task-1"
    )
    assert decision.decision == DECISION_APPROVAL_REQUIRED
    assert details2["status"] == "pending"
