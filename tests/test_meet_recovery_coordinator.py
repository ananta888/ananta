"""Real SQL Hub recovery orchestration with explicit synthetic authority ports."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine, select

from agent.models.meet_recovery_binding import recovery_owner
from agent.repositories.meet_dialog_recovery import SqlDialogRecovery, recoveries
from agent.services.meet_contract import MeetError
from agent.services.meet_dialog_controls import initial_controls
from agent.services.meet_dialog_recovery import MeetDialogRecovery
from ananta_contracts.meet_reconnect import validate_reconnect_response
from tests.test_meet_dialog_authority import fixture

pytestmark = pytest.mark.timeout(30)


@pytest.fixture
def system(tmp_path, request):
    f = fixture()
    f.context["reconnect"] = True
    with_speaker = getattr(request, "param", False)
    if with_speaker:
        f.context["capabilities"].append("speech.publish")
        f.context["speaker_floor"] = True
        f.context["controls"] = initial_controls(f.context["capabilities"], "mention", "off", f.now * 1000)
        f.authority.policies[("tenant", "project")] = frozenset(f.context["capabilities"])
    now = [f.now]
    f.authority.clock = lambda: now[0]
    scope = f.authority.current("task", "dispatch", "runtime")
    engine = create_engine(f"sqlite:///{tmp_path / 'coordinator.sqlite'}")
    states = SqlDialogRecovery(engine)
    states.initialize()
    events = []
    meet = Mock()
    meet.retire.side_effect = lambda *args: events.append("retire")
    phases = Mock()
    phases.begin_recovery.side_effect = lambda *args: events.append("phase")
    issuer = Mock()
    issuer.issue_dialog.return_value = {"origin": scope.origin, "room_id": scope.room_id, "grant": "synthetic-fresh"}
    speaker = Mock() if with_speaker else None
    if speaker is not None:
        speaker.withdraw.side_effect = lambda *args: events.append("speaker-withdraw")
    service = MeetDialogRecovery(f.authority, states, meet, issuer, phases, speaker_floor=speaker, clock=lambda: now[0])
    receipt = {
        "lease": {
            "sessionId": "ms_" + "a" * 32,
            "generation": 1,
            "expiresAt": (f.now + 120) * 1000,
            "absoluteExpiresAt": (f.now + 7200) * 1000,
        },
        "peerId": "a" * 16,
        "membershipEpoch": 1,
    }
    service.observe(scope, receipt)
    payload = {
        "schema": "ananta.meet-dialog-callback.v1",
        "action": "reconnect",
        "task_id": "task",
        "lease_id": "dispatch",
        "runtime_id": "runtime",
        "nonce": "c" * 32,
        "sent_at": f.now,
        "meet_session_id": receipt["lease"]["sessionId"],
        "attempt": 0,
    }
    assignment = {
        "reconnect": True,
        "deadline": scope.deadline,
        "meeting": {"origin": scope.origin, "room_id": scope.room_id},
    }
    yield SimpleNamespace(**locals())
    engine.dispose()


def row(s):
    with s.engine.connect() as connection:
        return dict(connection.execute(select(recoveries)).mappings().one())


def test_hub_orders_retirement_phase_quarantine_and_one_fresh_grant_without_a_new_task(system):
    s = system
    response = s.service.exchange(s.payload)
    assert s.events == ["retire", "phase"]
    assert response["state"] == "waiting" and response["meeting"] is None
    validate_reconnect_response(response, s.payload, s.assignment, s.now[0] * 1000, 0)
    s.issuer.issue_dialog.assert_not_called()
    pending = s.payload | {"attempt": 1}
    s.now[0] += 3.999
    assert s.service.exchange(pending)["meeting"] is None
    s.now[0] = s.f.now + 4
    response = s.service.exchange(pending)
    validate_reconnect_response(response, pending, s.assignment, s.now[0] * 1000, 1)
    assert response["meeting"] == s.issuer.issue_dialog.return_value
    assert s.service.exchange(pending)["meeting"] is None
    s.issuer.issue_dialog.assert_called_once_with(s.f.authority, "task", "dispatch", "runtime", s.now[0])
    s.meet.retire.assert_called_once_with("task", "dispatch", "runtime", s.payload["meet_session_id"])
    assert s.f.context["deadline"] == s.assignment["deadline"]


@pytest.mark.parametrize("stage", ["retire", "phase", "issuer"])
def test_uncertain_side_effect_is_not_retried_or_promoted_to_another_grant(system, stage):
    s = system
    failure = MeetError("synthetic_port_failure", 503)
    if stage == "retire":
        s.meet.retire.side_effect = failure
    elif stage == "phase":
        s.phases.begin_recovery.side_effect = failure
    else:
        s.service.exchange(s.payload)
        s.now[0] += 4
        s.issuer.issue_dialog.side_effect = failure
    payload = s.payload | {"attempt": 1 if stage == "issuer" else 0}
    with pytest.raises(MeetError, match="synthetic_port_failure"):
        s.service.exchange(payload)
    if stage != "issuer":
        assert row(s)["state"] == "retiring"
        with pytest.raises(MeetError, match="retirement_unconfirmed"):
            s.service.exchange(s.payload | {"attempt": 1})
        s.issuer.issue_dialog.assert_not_called()
    else:
        assert row(s)["state"] == "joining"
        assert s.service.exchange(payload)["meeting"] is None
        s.issuer.issue_dialog.assert_called_once()
    assert s.meet.retire.call_count == 1


@pytest.mark.parametrize("stage", ["before", "retire", "phase", "grant"])
def test_policy_withdrawal_never_yields_a_rejoin_grant(system, stage):
    s = system

    def revoke(*args):
        s.f.task.status = "cancelled"

    if stage == "before":
        revoke()
    elif stage == "retire":
        s.meet.retire.side_effect = revoke
    elif stage == "phase":
        s.phases.begin_recovery.side_effect = revoke
    else:
        s.service.exchange(s.payload)
        s.now[0] += 4
        s.issuer.issue_dialog.side_effect = lambda *args: (revoke(), s.issuer.issue_dialog.return_value)[1]
    with pytest.raises(MeetError):
        s.service.exchange(s.payload | {"attempt": 1 if stage == "grant" else 0})
    assert s.issuer.issue_dialog.call_count == int(stage == "grant")


@pytest.mark.parametrize("field,value", [("runtime_id", "foreign"), ("deadline", 1), ("reconnect", False)])
def test_original_binding_change_during_retirement_does_not_reassign_a_task(system, field, value):
    s = system
    s.meet.retire.side_effect = lambda *args: s.f.context.update({field: value})
    with pytest.raises(MeetError):
        s.service.exchange(s.payload)
    s.phases.begin_recovery.assert_not_called()
    s.issuer.issue_dialog.assert_not_called()


def test_late_success_cannot_extend_original_attempt_window(system):
    s = system
    s.phases.begin_recovery.side_effect = lambda *args: s.now.__setitem__(0, s.now[0] + 30)
    with pytest.raises(MeetError, match="recovery_expired"):
        s.service.exchange(s.payload)
    assert row(s)["state"] == "failed"
    s.issuer.issue_dialog.assert_not_called()


def test_actual_new_membership_settles_once_and_old_pending_or_observation_is_rejected(system):
    s = system
    s.service.exchange(s.payload)
    s.now[0] += 4
    s.service.exchange(s.payload | {"attempt": 1})
    next_receipt = s.receipt | {
        "peerId": "b" * 16,
        "membershipEpoch": 3,
        "lease": s.receipt["lease"] | {"sessionId": "ms_" + "b" * 32},
    }
    s.service.observe(s.scope, next_receipt)
    assert row(s)["state"] == "active"
    with pytest.raises(MeetError):
        s.service.observe(s.scope, s.receipt)
    with pytest.raises(MeetError):
        s.service.exchange(s.payload | {"attempt": 1})
    s.issuer.issue_dialog.assert_called_once()


@pytest.mark.parametrize("value", [True, -1, 3, None, "1"])
def test_invalid_attempt_is_rejected_before_any_port_side_effect(system, value):
    s = system
    with pytest.raises(MeetError, match="attempt_invalid"):
        s.service.exchange(s.payload | {"attempt": value})
    assert s.events == [] and row(s)["attempt"] == 0


def test_unknown_session_never_retires_or_issues_a_grant(system):
    s = system
    with pytest.raises(MeetError, match="session_changed"):
        s.service.exchange(s.payload | {"meet_session_id": "ms_" + "b" * 32})
    assert s.events == [] and row(s)["attempt"] == 0


def test_old_scope_observation_cannot_bind_a_new_assignment(system):
    s = system
    with pytest.raises(MeetError, match="authority_changed"):
        s.service.observe(replace(s.scope, owner_subject="foreign"), s.receipt)
    assert row(s)["assignment_digest"] == recovery_owner(s.scope).assignment_digest


@pytest.mark.parametrize("system", [True], indirect=True)
def test_speaker_permit_is_withdrawn_before_retirement_and_phase_reset(system):
    s = system
    s.service.exchange(s.payload)
    assert s.events == ["speaker-withdraw", "retire", "phase"]
    s.speaker.withdraw.assert_called_once_with(s.scope)
    s.issuer.issue_dialog.assert_not_called()


@pytest.mark.parametrize("system", [True], indirect=True)
@pytest.mark.parametrize("missing", [False, True])
def test_missing_or_failed_speaker_withdrawal_cannot_issue_retirement_or_grant(system, missing):
    s = system
    if missing:
        s.service.speaker_floor = None
    else:
        s.speaker.withdraw.side_effect = MeetError("synthetic_speaker_failure", 503)
    with pytest.raises(MeetError):
        s.service.exchange(s.payload)
    s.meet.retire.assert_not_called()
    s.phases.begin_recovery.assert_not_called()
    s.issuer.issue_dialog.assert_not_called()
    assert row(s)["state"] == ("active" if missing else "retiring")
