from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from agent.common import audit as audit_module
from agent.common.audit import log_audit
from agent.database import engine
from agent.db_models import AuditLogDB


def _all_audit_entries() -> list[AuditLogDB]:
    with Session(engine) as session:
        statement = select(AuditLogDB).order_by(AuditLogDB.id.asc())
        return list(session.exec(statement).all())


def test_audit_minimum_appends_required_security_events() -> None:
    before = _all_audit_entries()

    log_audit(
        "policy_denied",
        {"task_id": "task-a", "trace_id": "trace-a", "token": "secret-token"},
    )
    log_audit("approval_requested", {"task_id": "task-a", "trace_id": "trace-a", "approval_id": "apr-a"})
    log_audit(
        "approval_decided",
        {"task_id": "task-a", "trace_id": "trace-a", "approval_id": "apr-a", "decision": "approved"},
    )
    log_audit("execution_result", {"task_id": "task-a", "trace_id": "trace-a", "status": "finished"})

    after = _all_audit_entries()
    assert len(after) >= len(before) + 4

    added = after[len(before) :]
    assert [entry.action for entry in added] == [
        "policy_denied",
        "approval_requested",
        "approval_decided",
        "execution_result",
    ]

    for idx in range(1, len(added)):
        assert added[idx].prev_hash == added[idx - 1].record_hash


def test_audit_minimum_redacts_sensitive_values_and_keeps_event_envelope() -> None:
    log_audit(
        "policy_denied",
        {
            "task_id": "task-secret",
            "trace_id": "trace-secret",
            "token": "raw-token-value",
            "api_key": "raw-api-key",
            "password": "raw-password",
        },
    )

    latest = _all_audit_entries()[-1]
    details = dict(latest.details or {})
    details_text = json.dumps(details, sort_keys=True)

    assert "raw-token-value" not in details_text
    assert "raw-api-key" not in details_text
    assert "raw-password" not in details_text
    assert details.get("_event", {}).get("channel") == "audit"
    assert details.get("_event", {}).get("event_type") == "policy_denied"


def test_audit_minimum_strips_raw_prompt_and_messages_payloads() -> None:
    log_audit(
        "execution_audit_event",
        {
            "task_id": "task-raw",
            "trace_id": "trace-raw",
            "raw_prompt": "do not store this prompt text",
            "messages": [{"role": "user", "content": "secret content"}],
        },
    )

    latest = _all_audit_entries()[-1]
    details = dict(latest.details or {})
    assert details.get("raw_prompt") == "***REDACTED_AUDIT_PAYLOAD***"
    assert details.get("messages") == "***REDACTED_AUDIT_PAYLOAD***"


def test_audit_hash_chain_is_serialized_before_pool_checkout(monkeypatch) -> None:
    isolated_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(isolated_engine)
    monkeypatch.setattr(audit_module, "_engine", lambda: isolated_engine)

    def _append(index: int) -> None:
        audit_module.log_audit(
            "concurrent_integrity_probe",
            {"task_id": f"task-{index}", "trace_id": f"trace-{index}"},
        )

    with ThreadPoolExecutor(max_workers=16) as executor:
        list(executor.map(_append, range(32)))

    with Session(isolated_engine) as session:
        entries = list(
            session.exec(select(AuditLogDB).order_by(AuditLogDB.id.asc())).all()
        )

    assert len(entries) == 32
    assert entries[0].prev_hash is None
    for previous, current in zip(entries, entries[1:]):
        assert current.prev_hash == previous.record_hash
