"""Real isolated SQL ledger, immutable terminal Tasks and current owner access."""

from copy import deepcopy
from dataclasses import replace
from unittest.mock import Mock

import pytest
from sqlalchemy import select, update

from agent.models.meet_dialog_diagnostics import record_for
from agent.repositories.meet_dialog_diagnostics import SqlDialogDiagnostics, observations
from agent.services.meet_contract import MeetError
from agent.services.meet_dialog_diagnostics import MeetDialogDiagnostics
from tests.test_meet_dialog_avatar_negotiation import system
from tests.test_meet_dialog_diagnostics_contract import observation, request

pytestmark = pytest.mark.timeout(45)


def setup(terminal="completed"):
    from agent.database import engine

    f = system()
    f.task_id = f.service.start(f.principal, "project", f.payload)["task_id"]
    f.context = f.tasks.get_by_id(f.task_id).worker_execution_context["meet_dialog"]
    f.ids = f.task_id, f.context["lease_id"], f.context["runtime_id"]
    if terminal is not None:
        assert f.tasks.finish_bound(*f.ids, terminal)
    f.ledger = SqlDialogDiagnostics(engine, clock=lambda: f.f.now)
    f.ledger.initialize()
    f.diagnostics = MeetDialogDiagnostics(f.ledger, f.f.authority.binding, clock=lambda: f.f.now)
    f.payload = request() | {
        "task_id": f.task_id,
        "lease_id": f.ids[1],
        "runtime_id": f.ids[2],
        "sent_at": int(f.f.now),
    }
    return f


def inspect(f, principal=None):
    return f.diagnostics.inspect(principal or f.principal, "project", f.task_id)


@pytest.mark.parametrize("terminal", ["completed", "failed", "cancelled"])
def test_exact_terminal_observation_survives_restart_without_any_task_or_history_write(app, terminal):
    with app.app_context():
        f = setup(terminal)
        before = f.tasks.get_by_id(f.task_id).model_dump()
        assert inspect(f)["observation_status"] == "missing"
        assert f.diagnostics.accept(f.payload)["accepted"] is True
        f.diagnostics = MeetDialogDiagnostics(
            SqlDialogDiagnostics(f.ledger.engine), f.f.authority.binding, clock=lambda: f.f.now
        )
        result = inspect(f)
        assert result == {
            "schema": "ananta.meet-dialog-diagnostics.v1",
            "task_id": f.task_id,
            "observation_status": "recorded",
            "classification": "unverified_worker_observation",
            "observation": observation(),
            "recorded_at_ms": int(f.f.now * 1000),
            "hub_task_status": terminal,
            "hub_control_revision_at_receipt": f.context["controls"]["revision"],
        }
        assert f.tasks.get_by_id(f.task_id).model_dump() == before
        f.worker.start_dialog.assert_called_once()


def test_exact_duplicate_is_idempotent_but_different_report_cannot_replace_first(app):
    with app.app_context():
        f = setup()
        f.diagnostics.accept(f.payload)
        first = f.ledger.read_record(f.task_id)
        f.f.now += 1
        assert f.diagnostics.accept(f.payload | {"nonce": "b" * 32})["accepted"] is True
        assert f.ledger.read_record(f.task_id) == first
        changed = deepcopy(f.payload)
        changed["observation"]["stop_reason"] = "runtime_failed"
        with pytest.raises(MeetError, match="conflict"):
            f.diagnostics.accept(changed)
        assert f.ledger.read_record(f.task_id) == first
        with f.ledger.engine.connect() as connection:
            assert len(connection.execute(select(observations).where(observations.c.task_id == f.task_id)).all()) == 1


@pytest.mark.parametrize("field", ["lease_id", "runtime_id", "task_id"])
def test_foreign_assignment_cannot_append(app, field):
    with app.app_context():
        f = setup()
        with pytest.raises(MeetError, match="binding_denied|not_found"):
            f.diagnostics.accept(f.payload | {field: "foreign"})
        assert f.ledger.read_record(f.task_id) is None


def test_active_task_never_accepts_terminal_observation_or_changes_execution(app):
    with app.app_context():
        f = setup(None)
        before = f.tasks.get_by_id(f.task_id).model_dump()
        with pytest.raises(MeetError, match="not_terminal"):
            f.diagnostics.accept(f.payload)
        assert inspect(f)["observation_status"] == "missing"
        assert f.tasks.get_by_id(f.task_id).model_dump() == before


@pytest.mark.parametrize("when", ["request", "cleanup", "during-transaction"])
def test_expiry_rechecked_at_ingress_and_locked_insert(app, when):
    with app.app_context():
        f = setup()
        if when == "request":
            f.payload["sent_at"] -= 11
        elif when == "cleanup":
            f.f.now = f.context["deadline"] + 30
            f.payload["sent_at"] = int(f.f.now)
        else:
            f.ledger.clock = lambda: f.context["deadline"] + 31
        with pytest.raises(MeetError, match="expired|request_invalid"):
            f.diagnostics.accept(f.payload)
        assert f.ledger.read_record(f.task_id) is None


@pytest.mark.parametrize("clock", [float("nan"), float("inf"), True, -1])
def test_invalid_repository_clock_fails_closed(app, clock):
    with app.app_context():
        f = setup()
        f.ledger.clock = lambda: clock
        with pytest.raises(MeetError, match="record_invalid"):
            f.diagnostics.accept(f.payload)
        assert f.ledger.read_record(f.task_id) is None


@pytest.mark.parametrize("field", ["tenant_id", "project_id", "subject_id"])
def test_foreign_owner_scope_cannot_read(app, field):
    with app.app_context():
        f = setup()
        f.diagnostics.accept(f.payload)
        # A forged project_id on the principal cannot confer foreign project access;
        # actual access authority is consulted independently on every read.
        principal = replace(f.principal, **{field: "foreign"})
        if field == "project_id":
            f.f.authority.binding.require_write_access.side_effect = PermissionError("revoked")
        with pytest.raises((MeetError, PermissionError)):
            inspect(f, principal)


def test_current_access_and_parent_scope_are_rechecked_after_record_read(app):
    with app.app_context():
        f = setup()
        f.diagnostics.accept(f.payload)
        access = f.f.authority.binding.require_write_access
        access.reset_mock()
        access.side_effect = [None, PermissionError("revoked")]
        with pytest.raises(PermissionError):
            inspect(f)
        assert access.call_args.args == (f.principal, "project", f.context.get("binding_task_id", ""))


@pytest.mark.parametrize("race", ["status", "scope", "runtime", "controls"])
def test_stale_snapshot_cannot_be_inserted_even_with_matching_observation(app, race):
    with app.app_context():
        f = setup()
        snapshot = f.ledger.read_task(f.task_id)
        if race == "status":
            snapshot["status"] = "failed"
        elif race == "scope":
            snapshot["tenant_id"] = "foreign"
        else:
            dialog = snapshot["worker_execution_context"]["meet_dialog"]
            if race == "runtime":
                dialog["runtime_id"] = "foreign"
            else:
                dialog["controls"]["revision"] += 1
        record = record_for(snapshot, observation(), int(f.f.now * 1000))
        with pytest.raises(MeetError, match="conflict"):
            f.ledger.append(snapshot, record, int((f.f.now + 10) * 1000))
        assert f.ledger.read_record(f.task_id) is None


@pytest.mark.parametrize("mutation", ["digest", "authority", "reason", "status"])
def test_corrupt_stored_row_is_never_presented_as_valid(app, mutation):
    with app.app_context():
        f = setup()
        f.diagnostics.accept(f.payload)
        record = f.ledger.read_record(f.task_id)
        if mutation == "digest":
            record["binding_digest"] = "0" * 64
        elif mutation == "authority":
            record["classification"] = "grounded"
        elif mutation == "status":
            record["hub_task_status"] = "failed"
        else:
            record["observation"]["stop_reason"] = "private text"
        with f.ledger.engine.begin() as connection:
            connection.execute(update(observations).where(observations.c.task_id == f.task_id).values(record=record))
        with pytest.raises(MeetError, match="record_invalid"):
            inspect(f)


def test_storage_failure_is_bounded_and_does_not_reopen_or_retry_task(app):
    with app.app_context():
        f = setup()
        before = f.tasks.get_by_id(f.task_id).model_dump()
        f.ledger.append = Mock(side_effect=MeetError("meet_dialog_diagnostics_storage_unavailable", 503))
        with pytest.raises(MeetError, match="storage_unavailable"):
            f.diagnostics.accept(f.payload)
        f.ledger.append.assert_called_once()
        assert f.tasks.get_by_id(f.task_id).model_dump() == before
