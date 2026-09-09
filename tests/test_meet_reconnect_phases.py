"""Hub-only phase replacement never relaxes ordinary transitions or old-ID fencing."""

from copy import deepcopy

import pytest

from agent.models.meet_dialog_phase import reconnecting, transition, validate_record
from agent.services.meet_contract import MeetError
from tests.test_meet_dialog_phase_model import BINDING, MEMBERSHIP, joined, observed
from tests.test_meet_dialog_phases import inspect, setup

pytestmark = pytest.mark.timeout(45)
NEXT = {"session_id": "ms_" + "b" * 32, "peer_id": "b" * 16}


def test_only_recovery_transition_clears_old_observation_and_binds_retired_membership():
    original = transition(joined(), "publishing", 100010, observation=observed(now=100010))
    with pytest.raises(MeetError, match="transition_invalid"):
        transition(original, "connecting", 100011)
    ready = reconnecting(original, MEMBERSHIP["session_id"], 1, 100011)
    assert ready["phase"] == "connecting" and ready["membership"] is ready["observation"] is None
    assert ready["retired_memberships"] == [MEMBERSHIP] and ready["revision"] == original["revision"] + 1
    assert original["membership"] == MEMBERSHIP and original["observation"] is not None
    with pytest.raises(MeetError):
        transition(ready, "joined", 100012, membership=MEMBERSHIP)
    active = transition(ready, "joined", 100012, membership=NEXT)
    with pytest.raises(MeetError):
        reconnecting(active, MEMBERSHIP["session_id"], 2, 100013)
    second = reconnecting(active, NEXT["session_id"], 2, 100013)
    for old in (MEMBERSHIP, NEXT):
        with pytest.raises(MeetError):
            transition(second, "joined", 100014, membership=old)
    final = transition(second, "joined", 100014, membership={"session_id": "ms_" + "c" * 32, "peer_id": "c" * 16})
    with pytest.raises(MeetError):
        reconnecting(final, final["membership"]["session_id"], 3, 100015)


@pytest.mark.parametrize("attempt", [True, 1.0, 0, 2, 3, None])
def test_wrong_recovery_attempt_never_rewinds_phase(attempt):
    with pytest.raises(MeetError, match="recovery_invalid"):
        reconnecting(joined(), MEMBERSHIP["session_id"], attempt, 100004)


@pytest.mark.parametrize(
    "patch",
    [
        {"retired_memberships": None},
        {"retired_memberships": {}},
        {"retired_memberships": [MEMBERSHIP]},
        {"retired_memberships": [NEXT, NEXT]},
        {"retired_memberships": [NEXT | {"grant": "forbidden"}]},
    ],
)
def test_corrupt_or_current_duplicate_membership_history_is_not_accepted(patch):
    with pytest.raises(MeetError):
        validate_record(joined() | patch, BINDING)


def test_stop_expiry_clock_rollback_and_old_peer_reuse_remain_terminal():
    for original, now in (
        (transition(joined(), "stopping", 100004), 100005),
        (joined(), 100002),
        (joined() | {"revision": 2**53 - 2}, 100004),
    ):
        with pytest.raises(MeetError):
            reconnecting(original, MEMBERSHIP["session_id"], 1, now)
    ready = reconnecting(joined(), MEMBERSHIP["session_id"], 1, 100004)
    with pytest.raises(MeetError):
        transition(ready, "joined", 100005, membership=NEXT | {"peer_id": MEMBERSHIP["peer_id"]})


def test_native_task_recovery_keeps_original_dispatch_and_audits_new_phase_without_redispatch(app):
    with app.app_context():
        f = setup(reconnect=True)
        assignment = deepcopy(f.worker.start_dialog.call_args.args[0])
        assert assignment["reconnect"] is True and f.scope.reconnect
        f.phases.advance(f.scope, "joined", f.state)
        inspect(f, True)
        before = deepcopy(f.tasks.get_by_id(f.task_id).worker_execution_context["meet_dialog"])
        f.phases.begin_recovery(f.scope, f.state["lease"]["sessionId"], 1)
        assert inspect(f)["phase"] == "connecting" and not inspect(f)["observation_fresh"]
        with pytest.raises(MeetError):
            f.phases.advance(f.scope, "joined", f.state)
        new_state = f.state | {"lease": f.state["lease"] | {"sessionId": NEXT["session_id"]}, "peerId": NEXT["peer_id"]}
        f.phases.advance(f.scope, "joined", new_state)
        task = f.tasks.get_by_id(f.task_id)
        assert task.worker_execution_context["meet_dialog"] == before
        assert f.worker.start_dialog.call_count == 1
        assert inspect(f)["phase"] == "joined"
        events = [e for e in task.history if e.get("event_type") == "meet_dialog_phase_observed"]
        assert [e["details"].get("reconnect_attempt") for e in events[-2:]] == [1, 1]


def test_legacy_native_phase_cannot_infer_permission_for_reconnect(app):
    with app.app_context():
        f = setup()
        f.phases.advance(f.scope, "joined", f.state)
        with pytest.raises(MeetError, match="recovery_invalid"):
            f.phases.begin_recovery(f.scope, f.state["lease"]["sessionId"], 1)
        assert inspect(f)["phase"] == "joined"
