"""Deterministic Hub-to-worker mail contract E2E.

This deliberately does not claim live Stalwart evidence. It verifies the
cross-provider orchestration boundary with injected provider executions.
"""

from __future__ import annotations

from typing import Any

from agent.services.mail_task_service import (
    InMemoryMailAccountLeaseStore,
    MailTaskService,
    MailWorkspaceScope,
)
from worker.mail_task_execution import (
    MailTaskExecutionOutcome,
    MailWorkerTaskHandler,
)


class _Repository:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    def get_by_id(self, task_id: str):
        return self.rows.get(task_id)

    def get_all(self):
        return list(self.rows.values())


class _Queue:
    def __init__(self, repository: _Repository) -> None:
        self.repository = repository

    def ingest_task(self, **kwargs: Any) -> None:
        self.repository.rows[str(kwargs["task_id"])] = {
            "id": kwargs["task_id"],
            "status": kwargs["status"],
            **dict(kwargs["extra_fields"]),
            "verification_status": {},
        }


class _ProviderExecution:
    def __init__(self, provider: str) -> None:
        self.provider = provider
        self.calls = 0

    def execute(self, **kwargs: Any) -> MailTaskExecutionOutcome:
        self.calls += 1
        return MailTaskExecutionOutcome(
            status="completed",
            reason_code="mail_discovery_completed",
            provider=self.provider,
            result_refs=(f"mailref-{self.provider}",),
            counters={"accounts": 1},
        )


def _run(provider: str) -> dict[str, Any]:
    repository = _Repository()
    queue = _Queue(repository)

    def update(task_id: str, status: str, **kwargs: Any):
        repository.rows[task_id].update({"status": status, **kwargs})
        return repository.rows[task_id]

    hub = MailTaskService(
        task_queue=queue,
        task_repository=repository,
        lease_store=InMemoryMailAccountLeaseStore(),
        status_updater=update,
        audit=lambda _event, _payload: None,
        clock=lambda: 100.0,
        role="hub",
    )
    queued = hub.submit(
        operation="discovery",
        account_ref=f"mail-account:{provider}",
        workspace_scope=MailWorkspaceScope("repo"),
        idempotency_key=f"mail-discovery-{provider}-1",
        policy_refs={"discovery_policy_ref": "policy:mail:discovery:v1"},
        actor="e2e",
    )
    lease = hub.claim_for_delegation(
        job_id=queued["job_id"],
        owner_ref=f"hub-worker:{provider}",
    )
    assert lease is not None
    raw = repository.rows[queued["job_id"]]
    raw["worker_execution_context"]["mail_task_control"]["lease"] = lease
    provider_execution = _ProviderExecution(provider)
    worker = MailWorkerTaskHandler(provider_execution, clock=lambda: 100.0)

    result = worker.execute(tid=queued["job_id"], task=raw)
    accepted = hub.validate_worker_result(
        job_id=queued["job_id"],
        result=result,
    )

    assert provider_execution.calls == 1
    assert hub.release_lease(
        job_id=queued["job_id"],
        fencing_token=accepted["lease_fencing_token"],
        owner_ref=f"hub-worker:{provider}",
    )
    return accepted


def test_imap_and_jmap_share_the_same_hub_worker_result_contract() -> None:
    jmap = _run("jmap")
    imap = _run("imap")

    assert set(jmap) == set(imap)
    assert jmap["schema"] == imap["schema"]
    assert jmap["operation"] == imap["operation"] == "discovery"
    assert jmap["status"] == imap["status"] == "completed"
    assert jmap["reason_code"] == imap["reason_code"]
    assert jmap["counters"] == imap["counters"]
