from __future__ import annotations

from agent.services.mail_task_service import MAIL_TASK_SCHEMA
from worker.mail_task_execution import (
    MailTaskExecutionOutcome,
    MailWorkerTaskHandler,
)


class _Execution:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def execute(self, **kwargs):
        self.calls.append(dict(kwargs))
        return MailTaskExecutionOutcome(
            status="completed",
            reason_code="mail_sync_completed",
            provider="jmap",
            result_refs=("mailref-result",),
            counters={"updated": 1},
        )


def _task(*, lease: bool) -> dict:
    control = {}
    if lease:
        control["lease"] = {
            "job_id": "mail-job-1",
            "account_ref": "mail-account:primary",
            "owner_ref": "hub-worker:test",
            "fencing_token": 7,
            "expires_at": 200.0,
        }
    return {
        "worker_execution_context": {
            "mail_task": {
                "schema": MAIL_TASK_SCHEMA,
                "job_id": "mail-job-1",
                "operation": "sync",
                "account_ref": "mail-account:primary",
                "workspace_scope": {"workspace_id": "repo"},
                "idempotency_key": "mail-sync-primary-1",
                "request_fingerprint": "fingerprint:one",
                "operation_refs": {},
                "policy_refs": {"sync_policy_ref": "policy:mail:sync:v1"},
                "deadline_at": 180.0,
                "max_attempts": 3,
                "created_at": 90.0,
            },
            "mail_task_control": control,
        }
    }


def test_proposal_does_not_claim_or_require_account_lease() -> None:
    execution = _Execution()
    handler = MailWorkerTaskHandler(execution, clock=lambda: 100.0)

    proposal = handler.propose(tid="mail-job-1", task=_task(lease=False))

    assert proposal["status"] == "executable"
    assert execution.calls == []


def test_worker_executes_exactly_one_hub_leased_operation() -> None:
    execution = _Execution()
    handler = MailWorkerTaskHandler(execution, clock=lambda: 100.0)

    result = handler.execute(tid="mail-job-1", task=_task(lease=True))

    assert len(execution.calls) == 1
    assert execution.calls[0]["lease_fencing_token"] == 7
    assert execution.calls[0]["operation_refs"] == {}
    assert result["status"] == "completed"
    assert result["lease_fencing_token"] == 7
    assert result["result_refs"] == ["mailref-result"]
