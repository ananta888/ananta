from __future__ import annotations

from typing import Any

import pytest

from agent.services.mail_task_service import (
    InMemoryMailAccountLeaseStore,
    MailTaskService,
    MailWorkspaceScope,
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
        self.calls: list[dict[str, Any]] = []

    def ingest_task(self, **kwargs: Any) -> None:
        self.calls.append(dict(kwargs))
        self.repository.rows[str(kwargs["task_id"])] = {
            "id": kwargs["task_id"],
            "status": kwargs["status"],
            **dict(kwargs.get("extra_fields") or {}),
            "verification_status": {},
        }


def _service() -> tuple[MailTaskService, _Queue, _Repository]:
    repository = _Repository()
    queue = _Queue(repository)

    def update(task_id: str, status: str, **kwargs: Any) -> dict[str, Any]:
        row = repository.rows[task_id]
        row["status"] = status
        row.update(kwargs)
        return row

    return (
        MailTaskService(
            task_queue=queue,
            task_repository=repository,
            lease_store=InMemoryMailAccountLeaseStore(),
            status_updater=update,
            audit=lambda _event, _payload: None,
            clock=lambda: 100.0,
            role="hub",
        ),
        queue,
        repository,
    )


def _submit(
    service: MailTaskService,
    *,
    operation: str,
    idempotency_key: str,
    operation_refs: dict[str, str] | None = None,
) -> dict[str, Any]:
    return service.submit(
        operation=operation,
        account_ref="mail-account:primary",
        workspace_scope=MailWorkspaceScope("repo"),
        idempotency_key=idempotency_key,
        policy_refs={"execution_policy_ref": "policy:mail:test:v1"},
        operation_refs=operation_refs,
        actor="test",
    )


def test_idempotent_submission_queues_reference_only_envelope_once() -> None:
    service, queue, _repository = _service()

    first = _submit(
        service,
        operation="body",
        idempotency_key="mail-body-primary-1",
        operation_refs={"message_ref": "mailref-one"},
    )
    second = _submit(
        service,
        operation="body",
        idempotency_key="mail-body-primary-1",
        operation_refs={"message_ref": "mailref-one"},
    )

    assert second["job_id"] == first["job_id"]
    assert len(queue.calls) == 1
    envelope = queue.calls[0]["extra_fields"]["worker_execution_context"][
        "mail_task"
    ]
    assert envelope["operation_refs"] == {"message_ref": "mailref-one"}
    assert "credential" not in str(envelope).lower()


def test_content_operations_require_only_opaque_operation_references() -> None:
    service, _queue, _repository = _service()

    with pytest.raises(ValueError, match="mail_task_operation_ref_required"):
        _submit(
            service,
            operation="body",
            idempotency_key="mail-body-primary-missing",
        )
    with pytest.raises(ValueError, match="mail_task_operation_ref_field_invalid"):
        _submit(
            service,
            operation="body",
            idempotency_key="mail-body-primary-content",
            operation_refs={"body": "plaintext"},
        )


def test_account_lease_serializes_jobs_with_fencing() -> None:
    service, _queue, _repository = _service()
    first = _submit(
        service,
        operation="sync",
        idempotency_key="mail-sync-primary-1",
    )
    second = _submit(
        service,
        operation="diagnose",
        idempotency_key="mail-diagnose-primary-1",
    )

    first_lease = service.claim_for_delegation(
        job_id=first["job_id"],
        owner_ref="hub-worker:first",
    )
    assert first_lease is not None
    assert (
        service.claim_for_delegation(
            job_id=second["job_id"],
            owner_ref="hub-worker:second",
        )
        is None
    )
    assert service.release_lease(
        job_id=first["job_id"],
        fencing_token=first_lease["fencing_token"],
        owner_ref="hub-worker:first",
    )
    second_lease = service.claim_for_delegation(
        job_id=second["job_id"],
        owner_ref="hub-worker:second",
    )
    assert second_lease is not None
    assert second_lease["fencing_token"] > first_lease["fencing_token"]


def test_worker_role_cannot_own_mail_queue() -> None:
    service, _queue, _repository = _service()
    service._role = "worker"

    with pytest.raises(PermissionError, match="mail_task_hub_role_required"):
        _submit(
            service,
            operation="sync",
            idempotency_key="mail-sync-worker-forbidden",
        )
