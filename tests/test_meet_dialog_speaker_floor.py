"""Native dialog authority, real SQL chat dispatch and bounded speaker handoff."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent.models.meet_preauthorization_binding import assignment_projection
from agent.models.meet_speaker_floor import SpeakerOwner
from agent.repositories.meet_speaker_floor import SqlMeetSpeakerFloor
from agent.services.meet_contract import MeetError
from agent.services.meet_dialog_service import MeetDialogService
from agent.services.meet_dialog_speaker_floor import MeetDialogSpeakerFloor
from agent.services.meet_speaker_floor import MeetSpeakerFloor
from ananta_contracts.meet_spoken_reply import decode_spoken_response
from tests import test_meet_dialog_spoken_reply as spoken_tests

spoken = spoken_tests.spoken


def floor(spoken):
    f, service, payload, receipt, worker, tasks, generate = spoken
    f.context["speaker_floor"] = True
    repository = SqlMeetSpeakerFloor(service.reservations.engine)
    repository.initialize()
    elapsed = [0]
    admission = MeetSpeakerFloor(repository, clock=lambda: f.now, monotonic=lambda: elapsed[0])
    coordinator = MeetDialogSpeakerFloor(admission, repository, clock=lambda: f.now, monotonic=lambda: elapsed[0])
    service.speaker_floor = coordinator
    elapsed[0] = 5
    scope = f.authority.current(payload["task_id"], payload["lease_id"], payload["runtime_id"])
    return SimpleNamespace(**locals())


def test_native_spoken_reply_keeps_floor_until_exact_completed_control(spoken):
    s = floor(spoken)
    response = s.service.execute(s.payload)
    binding = response["reply"]["binding"]
    decoded = decode_spoken_response(response, s.payload, binding, s.f.now * 1000, floor_required=True)
    permit = decoded.speaker_floor
    assert s.coordinator.exchange(s.scope) == permit
    assert s.service.execute(s.payload)["reply"] is None
    s.worker.execute.assert_called_once()
    assert s.coordinator.exchange(s.scope, permit) is None
    assert s.coordinator.exchange(s.scope, permit) is None


def test_native_exchange_projects_only_own_floor_and_accepts_exact_completion(spoken):
    s = floor(spoken)
    reply = s.service.execute(s.payload)["reply"]
    service = MeetDialogService(
        s.f.authority,
        s.f.tasks,
        s.service.meet,
        Mock(),
        Mock(),
        s.worker,
        s.service.reservations,
        Mock(),
        clock=lambda: s.f.now,
        replies=s.service.replies,
        speaker_floor=s.coordinator,
    )
    payload = {k: v for k, v in s.payload.items() if k != "event"}
    state = service.exchange(payload)
    assert state["speaker_floor"] == reply["speaker_floor"]
    assert state["controls"]["speech"]["enabled"] is True
    state = service.exchange(payload | {"speech_finished": reply["speaker_floor"]})
    assert state["speaker_floor"] is None
    assert state["controls"]["speech"]["enabled"] is True  # Resource state does not rewrite user intent.


@pytest.mark.parametrize("mismatch", ["missing_controller", "unnegotiated_task"])
def test_negotiation_mismatch_fails_before_generation_without_unmanaged_fallback(spoken, mismatch):
    s = floor(spoken)
    if mismatch == "missing_controller":
        s.service.speaker_floor = None
    else:
        del s.f.context["speaker_floor"]
    with pytest.raises(MeetError, match="negotiation_required"):
        s.service.execute(s.payload)
    s.worker.execute.assert_not_called()


def test_headless_startup_quarantine_cannot_immediately_admit_old_cached_speakers(spoken):
    s = floor(spoken)
    s.elapsed[0] = 3.9
    with pytest.raises(MeetError, match="startup_quarantine"):
        s.service.execute(s.payload)
    assert s.repository.projection(SpeakerOwner.from_scope(s.scope), s.f.now * 1000) is None
    s.worker.execute.assert_not_called()


def test_negotiation_withdrawn_during_generation_cannot_issue_speaker_permit(spoken):
    s = floor(spoken)

    def withdraw(turn):
        result = s.generate(turn)
        del s.f.context["speaker_floor"]
        return result

    s.worker.execute.side_effect = withdraw
    with pytest.raises(MeetError):
        s.service.execute(s.payload)
    assert s.repository.projection(SpeakerOwner.from_scope(s.scope), s.f.now * 1000) is None


def test_current_speech_pause_withdraws_floor_but_not_another_owner(spoken):
    s = floor(spoken)
    permit = s.service.execute(s.payload)["reply"]["speaker_floor"]
    foreign = replace(s.scope, task_id="foreign")
    assert s.coordinator.exchange(foreign, permit) is None
    assert s.coordinator.exchange(s.scope) == permit
    paused = replace(
        s.scope, controls=replace(s.scope.controls, speech=replace(s.scope.controls.speech, enabled=False))
    )
    assert s.coordinator.exchange(paused) is None
    assert s.coordinator.exchange(s.scope) is None


def test_speaker_negotiation_is_part_of_immutable_pre_authorization(spoken):
    s = floor(spoken)
    context = s.f.context | {"binding_task_id": "parent"}
    args = (s.scope.task_id, s.scope.tenant_id, s.scope.project_id, s.scope.origin)
    projected = assignment_projection(*args, context)
    assert projected["speaker_floor"] is True
    legacy = dict(context)
    del legacy["speaker_floor"]
    assert "speaker_floor" not in assignment_projection(*args, legacy)
    for flag in (False, 1, "true"):
        with pytest.raises(MeetError, match="binding_invalid"):
            assignment_projection(*args, context | {"speaker_floor": flag})


@pytest.mark.parametrize("flag", [False, 1, "true", None])
def test_current_hub_authority_rejects_malformed_stored_negotiation(spoken, flag):
    s = floor(spoken)
    s.f.context["speaker_floor"] = flag
    with pytest.raises(MeetError, match="negotiation_invalid"):
        s.f.authority.current(s.scope.task_id, s.scope.lease_id, s.scope.runtime_id)
