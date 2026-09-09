"""Bounded private live-floor fixture composition and exact synthetic PCM."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent.models.meet_machine_principal import MeetMachinePrincipal
from agent.services.meet_speech_result import validate_speech_binding
from ananta_contracts.meet_speech import speech_profile
from tests import test_meet_speaker_floor as sql_fixture
from tests.meet_multi_worker_media import MultiWorkerMediaScenario
from tests.meet_multi_worker_speaker_floor import FloorTone, MultiWorkerSpeakerFloor, multi_worker_media

store = sql_fixture.store


@pytest.mark.parametrize("interrupt", [False, True])
def test_floor_fixture_emits_exact_finite_audio_and_rejects_a_third_reply(interrupt):
    worker = FloorTone(interrupt)
    turn = {
        "task_id": "synthetic-child",
        "lease_id": "synthetic-lease",
        "speech_profile": speech_profile(max_seconds=20),
    }
    for seconds in ((20 if interrupt else 14), 4):
        result = worker.execute(turn)
        validate_speech_binding(turn, result)
        assert result["speech"]["samples"] == seconds * 22050 and result["duration_seconds"] == seconds
    with pytest.raises(ValueError, match="reply_budget"):
        worker.execute(turn)


@pytest.mark.parametrize("invalid", [0, 1, "fifo", None])
def test_floor_fixture_mode_is_not_implicitly_coerced(invalid):
    with pytest.raises(ValueError, match="mode_invalid"):
        FloorTone(invalid)


def test_floor_selector_preserves_legacy_media_and_non_media_scenarios():
    assert isinstance(multi_worker_media(True), MultiWorkerMediaScenario)
    for mode in (False, "browser", "runtime-stall", "guarded-turn-udp"):
        assert multi_worker_media(mode) is None
    for mode in ("speaker-fifo", "speaker-barge-in"):
        assert isinstance(multi_worker_media(mode), MultiWorkerSpeakerFloor)


@pytest.mark.parametrize("interrupt", [False, True])
def test_live_fixture_installs_native_sql_floor_and_only_explicit_second_role_can_interrupt(store, interrupt):
    fixture = MultiWorkerSpeakerFloor(interrupt)
    options = fixture.service_options(Mock(), SimpleNamespace(engine=store.engine))
    floor = options["speaker_floor"]
    assert floor.admission.store is fixture.store is floor.states
    assert options["replies"].worker is fixture.media.worker
    assert fixture.worker is fixture.media.worker and fixture.worker.calls == []
    assert floor.monotonic() < floor.ready_at
    scope = SimpleNamespace(
        tenant_id="synthetic",
        project_id="synthetic",
        machine_principal=MeetMachinePrincipal(
            "synthetic",
            "synthetic",
            "meet-test-org",
            "meet-test-slot-second",
            "assignment",
            "a" * 64,
        ),
    )
    decision = floor.policy.decide(scope)
    assert decision.priority == (2 if interrupt else 0) and decision.barge_in is interrupt
    assert fixture.initial_options(0) != fixture.initial_options(1)
