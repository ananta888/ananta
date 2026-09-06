"""Real temporary SQL/filesystem cleanup, synthetic policy, no production data."""

import errno
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import select

from agent.repositories.persona_retention import SqlPersonaRetention, events
from agent.services.persona_retention_runner import PersonaRetentionRunner
from agent.services.persona_retention_service import PersonaRetentionService
from tests.test_persona_asset_erasure import paths
from tests.test_persona_asset_erasure import retired as retired
from tests.test_persona_assets import setup as setup

pytestmark = pytest.mark.timeout(40)


@pytest.fixture
def scheduled(request):
    service, erasure, principal, asset = request.getfixturevalue("retired")
    store = SqlPersonaRetention(service.catalog.engine)
    store.initialize()
    now = [1000.0]
    administration = PersonaRetentionService(
        policy=service.policy, catalog=service.catalog, store=store, clock=lambda: now[0]
    )
    tasks = Mock()
    tasks.finish.return_value = True
    runner = PersonaRetentionRunner(
        policy=service.policy, catalog=service.catalog, store=store, erasure=erasure, tasks=tasks, clock=lambda: now[0]
    )
    result = SimpleNamespace(
        service=service,
        erasure=erasure,
        principal=principal,
        asset=asset,
        store=store,
        now=now,
        admin=administration,
        runner=runner,
        tasks=tasks,
    )
    schedule(result)
    return result


def schedule(case, **changes):
    return case.admin.schedule(
        case.principal,
        "project",
        case.asset.image.artifact_id,
        **(dict(asset_revision=3, expected_revision=0, delete_after_seconds=60) | changes),
    )


def status(case):
    return case.admin.status(case.principal, "project", case.asset.image.artifact_id)


def due(case):
    case.now[0] = 1060
    return case.store.due(1_060_000, limit=5)[0]


def test_only_explicit_due_retired_bundle_is_erased(scheduled):
    case = scheduled
    assert case.runner.run_once()["claimed"] == 0
    assert all(path.is_file() for path in paths(case.service, case.asset))
    case.now[0] = 1060
    assert case.runner.run_once()["completed"] == 1
    assert status(case)["state"] == "completed"
    assert all(not path.exists() for path in paths(case.service, case.asset))
    assert case.runner.run_once()["claimed"] == 0
    case.tasks.start.assert_called_once()
    with case.store.engine.connect() as connection:
        assert [row.state for row in connection.execute(select(events))] == ["scheduled", "running", "completed"]


def test_admin_privilege_is_not_persisted_for_future_automation(scheduled):
    calls = scheduled.service.policy.require_revoke.call_args_list
    assert calls[-1].args[0].roles == frozenset({"user"})
    assert calls[-1].args[0].is_admin is False
    assert calls[-1].args[0].project_id == "project"


@pytest.mark.parametrize(
    "change",
    [
        {"asset_revision": 2},
        {"asset_revision": True},
        {"expected_revision": True},
        {"expected_revision": 7},
        {"delete_after_seconds": 0},
        {"delete_after_seconds": 59},
        {"delete_after_seconds": "60"},
        {"delete_after_seconds": 365 * 86400 + 1},
    ],
)
def test_strict_schedule_and_asset_revision_guards(scheduled, change):
    with pytest.raises(ValueError):
        schedule(scheduled, expected_revision=1, **change) if "expected_revision" not in change else schedule(
            scheduled, **change
        )


def test_cancelled_grant_cannot_be_claimed_or_delete_files(scheduled):
    case = scheduled
    observed = due(case)
    assert case.admin.cancel(case.principal, "project", case.asset.image.artifact_id, expected_revision=1) == 2
    assert case.store.claim(observed, 1_060_000) is None
    assert case.runner.run_once()["claimed"] == 0
    assert all(path.is_file() for path in paths(case.service, case.asset))


def test_concurrent_hubs_get_exactly_one_attempt(scheduled):
    case = scheduled
    observed = due(case)
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: case.store.claim(observed, 1_060_000), range(2)))
    assert sum(claim is not None for claim in claims) == 1
    assert case.store.due(1_060_000, limit=5) == ()


def test_cancel_during_attempt_invalidates_old_checkpoint_and_terminal_write(scheduled):
    case = scheduled
    record = case.store.claim(due(case), 1_060_000)
    case.admin.cancel(case.principal, "project", case.asset.image.artifact_id, expected_revision=1)
    with pytest.raises(PermissionError, match="claim_changed"):
        case.store.require_claim(record, 1_060_000)
    assert not case.store.finish(record, "completed", 1_060_000)
    assert status(case)["state"] == "cancelled"


def test_revoked_membership_blocks_before_filesystem_access(scheduled):
    case = scheduled
    due(case)
    case.service.policy.require_revoke.side_effect = PermissionError("revoked")
    assert case.runner.run_once()["blocked"] == 1
    assert all(path.is_file() for path in paths(case.service, case.asset))
    case.tasks.finish.assert_called_once()
    assert case.tasks.finish.call_args.args[1] == "failed"


def test_transient_failure_resumes_exact_files_after_backoff(scheduled):
    case = scheduled
    due(case)
    original = case.erasure.eraser
    calls = [0]

    def fail_second(reference, expected_size, *, checkpoint):
        calls[0] += 1
        if calls[0] == 2:
            raise OSError("synthetic interrupted storage")
        original.erase(reference, expected_size, checkpoint=checkpoint)

    case.erasure.eraser = Mock(erase=fail_second)
    assert case.runner.run_once()["retrying"] == 1
    first, second = paths(case.service, case.asset)
    assert not first.exists() and second.exists()
    assert case.runner.run_once()["claimed"] == 0
    case.erasure.eraser = original
    case.now[0] += 61
    assert case.runner.run_once()["completed"] == 1
    assert not second.exists()


def test_crashed_attempt_is_fenced_after_lease_expiry_and_recovered(scheduled):
    case = scheduled
    first = case.store.claim(due(case), 1_060_000)
    case.now[0] += 61
    assert case.runner.run_once()["completed"] == 1
    with pytest.raises(PermissionError):
        case.store.require_claim(first, int(case.now[0] * 1000))
    assert not case.store.finish(first, "blocked", int(case.now[0] * 1000))
    assert case.tasks.finish.call_args_list[0].args == (first, "failed")


def test_repeated_process_crashes_consume_the_finite_attempt_budget(scheduled):
    case = scheduled
    case.now[0] = 1060
    for _ in range(5):
        now_ms = int(case.now[0] * 1000)
        case.store.claim(case.store.due(now_ms, limit=1)[0], now_ms)
        case.now[0] += 61
    assert case.runner.run_once()["blocked"] == 1
    assert status(case)["state"] == "blocked" and status(case)["attempts"] == 5
    assert all(path.is_file() for path in paths(case.service, case.asset))


def test_task_cancellation_and_shutdown_stop_before_deletion(scheduled):
    case = scheduled
    due(case)
    assert case.runner.run_once(stopped=lambda: True)["claimed"] == 0
    case.tasks.require.side_effect = PermissionError("task_cancelled")
    assert case.runner.run_once()["blocked"] == 1
    assert all(path.is_file() for path in paths(case.service, case.asset))


def test_cross_scope_cannot_resolve_grants(scheduled):
    record = due(scheduled)
    for change in ({"tenant_id": "other"}, {"project_id": "other"}, {"artifact_id": "other"}):
        with pytest.raises(ValueError, match="unavailable"):
            scheduled.store.get(record | change)


def test_actual_fsync_failure_is_retried_and_missing_file_directory_is_synced(scheduled, monkeypatch):
    from agent.services import persona_image_erasure_store

    case = scheduled
    due(case)
    original = persona_image_erasure_store.os.fsync
    calls = [0]

    def interrupted(descriptor):
        calls[0] += 1
        if calls[0] == 1:
            raise OSError(errno.EIO, "synthetic interrupted fsync")
        original(descriptor)

    monkeypatch.setattr(persona_image_erasure_store.os, "fsync", interrupted)
    assert case.runner.run_once()["retrying"] == 1
    first, second = paths(case.service, case.asset)
    assert not first.exists() and second.exists()
    case.now[0] += 61
    assert case.runner.run_once()["completed"] == 1
    assert calls[0] == 3  # Failed sync, retried missing-file directory, second file.
    assert not second.exists()


def test_expired_attempt_cannot_commit_a_success_receipt(scheduled):
    record = scheduled.store.claim(due(scheduled), 1_060_000)
    assert not scheduled.store.finish(record, "completed", 1_120_000)


def test_replacement_retains_immutable_old_grant_terms_in_audit(scheduled):
    schedule(scheduled, expected_revision=1, delete_after_seconds=120)
    with scheduled.store.engine.connect() as connection:
        history = list(connection.execute(select(events).order_by(events.c.revision)).mappings())
    assert [(row["revision"], row["due_at_ms"]) for row in history] == [(1, 1_060_000), (2, 1_120_000)]
    assert history[0]["asset_digest"] == history[1]["asset_digest"]
    assert all(row["grant_actor"] == scheduled.principal.subject_id for row in history)
