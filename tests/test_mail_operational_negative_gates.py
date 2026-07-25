from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.services.mail_migration_service import (
    MailMigrationCommand,
    MailMigrationService,
)
from agent.services.mail_operation_intent_service import (
    MailOperationIntentService,
)
from agent.services.mail_provider_ports import MailContentAccessRequest
from agent.services.mail_task_service import (
    InMemoryMailAccountLeaseStore,
    MailTaskService,
    MailWorkspaceScope,
)


class _Repository:
    def __init__(self):
        self.rows = {}

    def get_by_id(self, task_id):
        return self.rows.get(task_id)

    def get_all(self):
        return list(self.rows.values())


class _Queue:
    def __init__(self, repository):
        self.repository = repository

    def ingest_task(self, **kwargs):
        row = {
            "id": kwargs["task_id"],
            "status": kwargs["status"],
            **dict(kwargs["extra_fields"]),
        }
        self.repository.rows[row["id"]] = row


def _task_service(now):
    repository = _Repository()

    def update(job_id, status, **fields):
        repository.rows[job_id]["status"] = status
        repository.rows[job_id].update(fields)

    return (
        MailTaskService(
            task_queue=_Queue(repository),
            task_repository=repository,
            lease_store=InMemoryMailAccountLeaseStore(),
            status_updater=update,
            audit=lambda *_: None,
            clock=lambda: now[0],
            role="hub",
        ),
        repository,
    )


def test_cancelled_or_stale_lease_result_is_rejected() -> None:
    now = [100.0]
    service, repository = _task_service(now)
    task = service.submit(
        operation="sync",
        account_ref="mail-account:one",
        workspace_scope=MailWorkspaceScope("repo"),
        idempotency_key="mail-sync-negative-1",
        policy_refs={"sync_policy_ref": "policy:sync:v1"},
        actor="test",
    )
    lease = service.claim_for_delegation(
        job_id=task["job_id"],
        owner_ref="hub-worker:one",
        ttl_seconds=10,
    )
    assert lease is not None
    repository.rows[task["job_id"]]["worker_execution_context"][
        "mail_task_control"
    ]["lease"] = lease
    result = {
        "schema": "ananta.mail_task_result.v1",
        "job_id": task["job_id"],
        "idempotency_key": "mail-sync-negative-1",
        "operation": "sync",
        "status": "completed",
        "reason_code": "mail_sync_completed",
        "retryable": False,
        "retry_after_ms": None,
        "provider": "jmap",
        "result_refs": [],
        "counters": {},
        "lease_fencing_token": lease["fencing_token"],
    }

    now[0] = 111.0
    with pytest.raises(ValueError, match="mail_task_result_lease_stale"):
        service.validate_worker_result(job_id=task["job_id"], result=result)

    now[0] = 112.0
    renewed = service.claim_for_delegation(
        job_id=task["job_id"],
        owner_ref="hub-worker:one",
        ttl_seconds=10,
    )
    assert renewed is not None
    repository.rows[task["job_id"]]["worker_execution_context"][
        "mail_task_control"
    ]["lease"] = renewed
    service.cancel(job_id=task["job_id"], actor="operator")
    result["lease_fencing_token"] = renewed["fencing_token"]
    with pytest.raises(ValueError, match="mail_task_result_terminal_state"):
        service.validate_worker_result(job_id=task["job_id"], result=result)


def test_body_intent_denies_expiry_grant_and_message_mismatch(tmp_path) -> None:
    now = [100.0]
    service = MailOperationIntentService(
        store_path=tmp_path / "intents.json",
        clock=lambda: now[0],
    )
    ref = {
        "schema": "mail_message_ref.v2",
        "mail_ref_id": "mailref-11111111111111111111111111111111",
        "account_id": "one",
        "protocol": "jmap",
        "protocol_locator": {"provider_account_id": "A1", "email_id": "e1"},
        "locator_version": 1,
        "thread_ref_id": "",
    }
    intent = service.create(
        operation="body",
        account_id="one",
        workspace_id="repo",
        grant_ref="grant-one",
        idempotency_key="mail-body-negative-1",
        payload={"message_ref": ref, "release_scope": "full_body"},
        ttl_seconds=30,
    )
    intent = service.bind_job(intent_ref=intent.intent_ref, job_id="job-one")
    request = MailContentAccessRequest(
        account_id="one",
        workspace_id="repo",
        artifact_ref=f"mail://{ref['mail_ref_id']}?scope=full_body",
        mail_ref_id=ref["mail_ref_id"],
        grant_ref="wrong-grant",
        release_scope="full_body",
    )
    assert service.authorize_content(
        intent=intent,
        request=request,
    ).reason_code == "mail_content_access_intent_mismatch"
    now[0] = 131.0
    assert service.resolve(
        intent_ref=intent.intent_ref,
        job_id="job-one",
        operation="body",
        account_id="one",
        workspace_id="repo",
    ).reason_code == "mail_operation_intent_expired"


def test_migration_corrupt_source_fails_without_target_write(tmp_path: Path) -> None:
    legacy_accounts = tmp_path / "legacy-accounts.json"
    legacy_metadata = tmp_path / "legacy-metadata.json"
    legacy_accounts.write_text('{"accounts":[{"password":"plaintext"}]}', encoding="utf-8")
    legacy_metadata.write_text('{"messages":[]}', encoding="utf-8")
    target_accounts = tmp_path / "target" / "accounts.json"
    report = MailMigrationService().execute(
        MailMigrationCommand(
            command_id="negative-migration",
            legacy_accounts_path=legacy_accounts,
            legacy_metadata_path=legacy_metadata,
            target_accounts_path=target_accounts,
            target_metadata_path=tmp_path / "target" / "metadata.json",
            journal_path=tmp_path / "target" / "journal.json",
            dry_run=True,
        )
    )

    assert report.status == "dry_run"
    assert report.failed == 1
    assert report.reason_code == "dry_run_has_failures"
    assert not target_accounts.exists()
