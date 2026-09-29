from __future__ import annotations

from dataclasses import dataclass

from agent.services.mail_contract_service import MailAccountV2
from agent.services.mail_polling_scheduler import (
    MailPollingScheduler,
    MailPollingSchedulerConfig,
    PersistentMailPollingLease,
)


def _account(
    account_id: str,
    *,
    enabled: bool = True,
    sync_policy: str = "headers_only",
) -> MailAccountV2:
    return MailAccountV2(
        account_id=account_id,
        display_name=account_id,
        requested_protocol="jmap",
        resolved_protocol="jmap",
        username_ref=f"env://{account_id.upper()}_USER",
        credential_ref=f"env://{account_id.upper()}_PASSWORD",
        sync_policy=sync_policy,
        enabled=enabled,
        provider_config={"session_url": "https://mail.example.test/jmap"},
    )


class _Accounts:
    def __init__(self, rows):
        self.rows = rows

    def list_accounts(self):
        return list(self.rows)


class _Tasks:
    def __init__(self):
        self.poll_calls = []
        self.cancel_calls = []

    def poll_accounts(self, **kwargs):
        self.poll_calls.append(kwargs)
        return [{"job_id": f"job-{item}"} for item in kwargs["account_refs"]]

    def cancel_account(self, **kwargs):
        self.cancel_calls.append(kwargs)
        return 1


@dataclass
class _RuntimeSnapshot:
    network_enabled: bool


class _Runtime:
    def __init__(self, enabled):
        self.enabled = enabled

    def snapshot(self):
        return _RuntimeSnapshot(self.enabled)


class _Health:
    def __init__(self):
        self.rows = []

    def observe(self, component, **kwargs):
        self.rows.append((component, kwargs))


class _Circuit:
    def __init__(self):
        self.closed = []

    def close_account(self, account_id):
        self.closed.append(account_id)


class _Metrics:
    def record_call(self, **kwargs):
        pass


def _scheduler(tmp_path, *, owner, now, network=True):
    tasks = _Tasks()
    health = _Health()
    circuit = _Circuit()
    scheduler = MailPollingScheduler(
        accounts=_Accounts(
            [
                _account("enabled"),
                _account("manual", sync_policy="manual"),
                _account("disabled", enabled=False),
            ]
        ),
        tasks=tasks,
        runtime=_Runtime(network),
        health=health,
        lease=PersistentMailPollingLease(path=tmp_path / "poller.json"),
        config=MailPollingSchedulerConfig(
            enabled=True,
            interval_seconds=30,
            lease_ttl_seconds=60,
            max_accounts_per_tick=3,
        ),
        circuit_breaker=circuit,
        metrics=_Metrics(),
        owner_id=owner,
        clock=lambda: now,
    )
    return scheduler, tasks, health, circuit


def test_scheduler_is_bounded_coalescing_queue_producer_and_cancels_disabled(
    tmp_path,
) -> None:
    scheduler, tasks, health, circuit = _scheduler(
        tmp_path,
        owner="hub-a",
        now=100.0,
    )

    result = scheduler.run_once()

    assert result == {
        "status": "scheduled",
        "claimed": 1,
        "eligible": 1,
        "queued": 1,
        "cancelled": 1,
    }
    assert tasks.poll_calls[0]["account_refs"] == ["mail-account:enabled"]
    assert tasks.poll_calls[0]["max_tasks"] == 3
    assert tasks.cancel_calls[0]["account_ref"] == "mail-account:disabled"
    assert circuit.closed == ["disabled"]
    assert health.rows[-1][1]["reason_code"] == "mail_polling_scheduled"


def test_scheduler_persistent_leader_lease_and_offline_cancellation(tmp_path) -> None:
    leader, leader_tasks, _, _ = _scheduler(
        tmp_path,
        owner="hub-a",
        now=100.0,
        network=False,
    )
    standby, standby_tasks, _, _ = _scheduler(
        tmp_path,
        owner="hub-b",
        now=101.0,
        network=True,
    )

    assert leader.run_once()["status"] == "offline"
    assert all(call["operation"] == "sync" for call in leader_tasks.cancel_calls)
    assert standby.run_once()["status"] == "standby"
    assert standby_tasks.poll_calls == []

    recovered, recovered_tasks, _, _ = _scheduler(
        tmp_path,
        owner="hub-b",
        now=161.0,
        network=True,
    )
    assert recovered.run_once()["status"] == "scheduled"
    assert len(recovered_tasks.poll_calls) == 1
